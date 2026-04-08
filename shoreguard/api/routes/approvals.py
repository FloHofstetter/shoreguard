"""REST endpoints for draft policy approval flow."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_actor, get_client, get_gateway_name
from shoreguard.api.schemas import (
    ApprovalBulkResponse,
    ApprovalChunkResponse,
    ApprovalClearResponse,
    ApprovalDraftResponse,
    MessageResponse,
)
from shoreguard.client import ShoreGuardClient
from shoreguard.services.approvals import ApprovalService
from shoreguard.services.audit import audit_log
from shoreguard.services.webhooks import fire_webhook

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_approval_service(client: ShoreGuardClient = Depends(get_client)) -> ApprovalService:
    """Build an ApprovalService from the injected client.

    Args:
        client: gRPC client for the active gateway.

    Returns:
        ApprovalService: Service instance bound to the client.
    """
    return ApprovalService(client)


class RejectRequest(BaseModel):
    """Body for rejecting a draft policy chunk.

    Attributes:
        reason: Optional rejection reason.
    """

    reason: str = Field(default="", max_length=1000)


class ApproveAllRequest(BaseModel):
    """Body for bulk-approving all pending draft chunks.

    Attributes:
        include_security_flagged: Whether to also approve security-flagged chunks.
    """

    include_security_flagged: bool = False


class EditChunkRequest(BaseModel):
    """Body for editing a draft policy chunk's proposed rule.

    Attributes:
        proposed_rule: New rule definition to replace the existing one.
    """

    proposed_rule: dict


@router.get("/{name}/approvals", response_model=ApprovalDraftResponse)
async def get_approvals(
    name: str,
    status_filter: str = "",
    svc: ApprovalService = Depends(_get_approval_service),
) -> dict[str, Any]:
    """Get draft policy recommendations for a sandbox.

    Args:
        name: Sandbox name.
        status_filter: Optional filter by approval status.
        svc: Injected approval service.

    Returns:
        dict[str, Any]: Draft policy with approval metadata.
    """
    return await asyncio.to_thread(svc.get_draft, name, status_filter=status_filter)


@router.get("/{name}/approvals/pending", response_model=list[ApprovalChunkResponse])
async def get_pending_approvals(
    name: str,
    svc: ApprovalService = Depends(_get_approval_service),
) -> list[dict[str, Any]]:
    """Get only pending (unapproved) draft chunks.

    Args:
        name: Sandbox name.
        svc: Injected approval service.

    Returns:
        list[dict[str, Any]]: Pending draft chunks.
    """
    return await asyncio.to_thread(svc.get_pending, name)


@router.post(
    "/{name}/approvals/{chunk_id}/approve",
    response_model=ApprovalChunkResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def approve_chunk(
    request: Request,
    name: str,
    chunk_id: str,
    svc: ApprovalService = Depends(_get_approval_service),
) -> dict[str, Any]:
    """Approve a single draft policy chunk.

    Args:
        request: Incoming HTTP request.
        name: Sandbox name.
        chunk_id: Chunk identifier.
        svc: Injected approval service.

    Returns:
        dict[str, Any]: Updated chunk status.
    """
    actor = get_actor(request)
    logger.info("Chunk approved (sandbox=%s, chunk_id=%s, actor=%s)", name, chunk_id, actor)
    result = await asyncio.to_thread(svc.approve, name, chunk_id)
    await audit_log(
        request,
        "approval.approve",
        "approval",
        chunk_id,
        gateway=get_gateway_name(request),
        detail={"sandbox": name},
    )
    await fire_webhook(
        "approval.approved",
        {
            "sandbox": name,
            "chunk_id": chunk_id,
            "actor": actor,
            "gateway": get_gateway_name(request),
        },
    )
    return result


@router.post(
    "/{name}/approvals/{chunk_id}/reject",
    response_model=MessageResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def reject_chunk(
    request: Request,
    name: str,
    chunk_id: str,
    body: RejectRequest | None = None,
    svc: ApprovalService = Depends(_get_approval_service),
) -> dict[str, str]:
    """Reject a single draft policy chunk.

    Args:
        request: Incoming HTTP request.
        name: Sandbox name.
        chunk_id: Chunk identifier.
        body: Optional rejection payload with reason.
        svc: Injected approval service.

    Returns:
        dict[str, str]: Rejection confirmation status.
    """
    reason = body.reason if body else ""
    actor = get_actor(request)
    logger.info("Chunk rejected (sandbox=%s, chunk_id=%s, actor=%s)", name, chunk_id, actor)
    await asyncio.to_thread(svc.reject, name, chunk_id, reason=reason)
    await audit_log(
        request,
        "approval.reject",
        "approval",
        chunk_id,
        gateway=get_gateway_name(request),
        detail={"sandbox": name, "reason": reason},
    )
    await fire_webhook(
        "approval.rejected",
        {
            "sandbox": name,
            "chunk_id": chunk_id,
            "reason": reason,
            "actor": actor,
            "gateway": get_gateway_name(request),
        },
    )
    return {"status": "rejected"}


@router.post(
    "/{name}/approvals/approve-all",
    response_model=ApprovalBulkResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def approve_all(
    request: Request,
    name: str,
    body: ApproveAllRequest | None = None,
    svc: ApprovalService = Depends(_get_approval_service),
) -> dict[str, Any]:
    """Approve all pending draft chunks for a sandbox.

    Args:
        request: Incoming HTTP request.
        name: Sandbox name.
        body: Optional payload controlling security-flagged inclusion.
        svc: Injected approval service.

    Returns:
        dict[str, Any]: Bulk approval result with counts.
    """
    actor = get_actor(request)
    logger.info("All chunks approved (sandbox=%s, actor=%s)", name, actor)
    include_flagged = body.include_security_flagged if body else False
    result = await asyncio.to_thread(
        svc.approve_all, name, include_security_flagged=include_flagged
    )
    await audit_log(
        request,
        "approval.approve_all",
        "approval",
        name,
        gateway=get_gateway_name(request),
        detail={"include_security_flagged": include_flagged},
    )
    await fire_webhook(
        "approval.approved",
        {"sandbox": name, "bulk": True, "actor": actor, "gateway": get_gateway_name(request)},
    )
    return result


@router.post(
    "/{name}/approvals/{chunk_id}/edit",
    response_model=MessageResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def edit_chunk(
    request: Request,
    name: str,
    chunk_id: str,
    body: EditChunkRequest,
    svc: ApprovalService = Depends(_get_approval_service),
) -> dict[str, str]:
    """Edit a pending draft chunk's proposed rule.

    Args:
        request: Incoming HTTP request.
        name: Sandbox name.
        chunk_id: Chunk identifier.
        body: Payload with the new proposed rule.
        svc: Injected approval service.

    Returns:
        dict[str, str]: Edit confirmation status.
    """
    actor = get_actor(request)
    logger.info("Chunk edited (sandbox=%s, chunk_id=%s, actor=%s)", name, chunk_id, actor)
    await asyncio.to_thread(svc.edit, name, chunk_id, body.proposed_rule)
    await audit_log(
        request,
        "approval.edit",
        "approval",
        chunk_id,
        gateway=get_gateway_name(request),
        detail={"sandbox": name},
    )
    return {"status": "edited"}


@router.post(
    "/{name}/approvals/{chunk_id}/undo",
    response_model=ApprovalChunkResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def undo_chunk(
    request: Request,
    name: str,
    chunk_id: str,
    svc: ApprovalService = Depends(_get_approval_service),
) -> dict[str, Any]:
    """Reverse an approval decision.

    Args:
        request: Incoming HTTP request.
        name: Sandbox name.
        chunk_id: Chunk identifier.
        svc: Injected approval service.

    Returns:
        dict[str, Any]: Updated chunk status after undo.
    """
    actor = get_actor(request)
    logger.info("Chunk undone (sandbox=%s, chunk_id=%s, actor=%s)", name, chunk_id, actor)
    result = await asyncio.to_thread(svc.undo, name, chunk_id)
    await audit_log(
        request,
        "approval.undo",
        "approval",
        chunk_id,
        gateway=get_gateway_name(request),
        detail={"sandbox": name},
    )
    return result


@router.post(
    "/{name}/approvals/clear",
    response_model=ApprovalClearResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def clear_approvals(
    request: Request,
    name: str,
    svc: ApprovalService = Depends(_get_approval_service),
) -> dict[str, int]:
    """Clear all pending draft chunks for a sandbox.

    Args:
        request: Incoming HTTP request.
        name: Sandbox name.
        svc: Injected approval service.

    Returns:
        dict[str, int]: Number of cleared chunks.
    """
    actor = get_actor(request)
    logger.info("Chunks cleared (sandbox=%s, actor=%s)", name, actor)
    result = await asyncio.to_thread(svc.clear, name)
    await audit_log(request, "approval.clear", "approval", name, gateway=get_gateway_name(request))
    return result


@router.get("/{name}/approvals/history", response_model=list[ApprovalChunkResponse])
async def get_approval_history(
    name: str,
    svc: ApprovalService = Depends(_get_approval_service),
) -> list[dict[str, Any]]:
    """Get decision history for a sandbox's draft policy.

    Args:
        name: Sandbox name.
        svc: Injected approval service.

    Returns:
        list[dict[str, Any]]: Approval decision history records.
    """
    return await asyncio.to_thread(svc.get_history, name)
