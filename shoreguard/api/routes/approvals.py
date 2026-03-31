"""REST endpoints for draft policy approval flow."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from shoreguard.api.auth import require_role
from shoreguard.api.deps import _current_gateway, get_actor, get_client
from shoreguard.client import ShoreGuardClient
from shoreguard.services.approvals import ApprovalService
from shoreguard.services.audit import audit_log

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_approval_service(client: ShoreGuardClient = Depends(get_client)) -> ApprovalService:
    return ApprovalService(client)


class RejectRequest(BaseModel):
    """Body for rejecting a draft policy chunk."""

    reason: str = ""


class ApproveAllRequest(BaseModel):
    """Body for bulk-approving all pending draft chunks."""

    include_security_flagged: bool = False


class EditChunkRequest(BaseModel):
    """Body for editing a draft policy chunk's proposed rule."""

    proposed_rule: dict


@router.get("/{name}/approvals")
async def get_approvals(
    name: str,
    status_filter: str = "",
    svc: ApprovalService = Depends(_get_approval_service),
) -> dict[str, Any]:
    """Get draft policy recommendations for a sandbox."""
    return await asyncio.to_thread(svc.get_draft, name, status_filter=status_filter)


@router.get("/{name}/approvals/pending")
async def get_pending_approvals(
    name: str,
    svc: ApprovalService = Depends(_get_approval_service),
) -> list[dict[str, Any]]:
    """Get only pending (unapproved) draft chunks."""
    return await asyncio.to_thread(svc.get_pending, name)


@router.post(
    "/{name}/approvals/{chunk_id}/approve", dependencies=[Depends(require_role("operator"))]
)
async def approve_chunk(
    request: Request,
    name: str,
    chunk_id: str,
    svc: ApprovalService = Depends(_get_approval_service),
) -> dict[str, Any]:
    """Approve a single draft policy chunk."""
    actor = get_actor(request)
    logger.info("Chunk approved (sandbox=%s, chunk_id=%s, actor=%s)", name, chunk_id, actor)
    result = await asyncio.to_thread(svc.approve, name, chunk_id)
    await audit_log(
        request,
        "approval.approve",
        "approval",
        chunk_id,
        gateway=_current_gateway.get(),
        detail={"sandbox": name},
    )
    return result


@router.post(
    "/{name}/approvals/{chunk_id}/reject", dependencies=[Depends(require_role("operator"))]
)
async def reject_chunk(
    request: Request,
    name: str,
    chunk_id: str,
    body: RejectRequest | None = None,
    svc: ApprovalService = Depends(_get_approval_service),
) -> dict:
    """Reject a single draft policy chunk."""
    reason = body.reason if body else ""
    actor = get_actor(request)
    logger.info("Chunk rejected (sandbox=%s, chunk_id=%s, actor=%s)", name, chunk_id, actor)
    await asyncio.to_thread(svc.reject, name, chunk_id, reason=reason)
    await audit_log(
        request,
        "approval.reject",
        "approval",
        chunk_id,
        gateway=_current_gateway.get(),
        detail={"sandbox": name, "reason": reason},
    )
    return {"status": "rejected"}


@router.post("/{name}/approvals/approve-all", dependencies=[Depends(require_role("operator"))])
async def approve_all(
    request: Request,
    name: str,
    body: ApproveAllRequest | None = None,
    svc: ApprovalService = Depends(_get_approval_service),
) -> dict[str, Any]:
    """Approve all pending draft chunks for a sandbox."""
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
        gateway=_current_gateway.get(),
        detail={"include_security_flagged": include_flagged},
    )
    return result


@router.post("/{name}/approvals/{chunk_id}/edit", dependencies=[Depends(require_role("operator"))])
async def edit_chunk(
    request: Request,
    name: str,
    chunk_id: str,
    body: EditChunkRequest,
    svc: ApprovalService = Depends(_get_approval_service),
) -> dict:
    """Edit a pending draft chunk's proposed rule."""
    actor = get_actor(request)
    logger.info("Chunk edited (sandbox=%s, chunk_id=%s, actor=%s)", name, chunk_id, actor)
    await asyncio.to_thread(svc.edit, name, chunk_id, body.proposed_rule)
    await audit_log(
        request,
        "approval.edit",
        "approval",
        chunk_id,
        gateway=_current_gateway.get(),
        detail={"sandbox": name},
    )
    return {"status": "edited"}


@router.post("/{name}/approvals/{chunk_id}/undo", dependencies=[Depends(require_role("operator"))])
async def undo_chunk(
    request: Request,
    name: str,
    chunk_id: str,
    svc: ApprovalService = Depends(_get_approval_service),
) -> dict[str, Any]:
    """Reverse an approval decision."""
    actor = get_actor(request)
    logger.info("Chunk undone (sandbox=%s, chunk_id=%s, actor=%s)", name, chunk_id, actor)
    result = await asyncio.to_thread(svc.undo, name, chunk_id)
    await audit_log(
        request,
        "approval.undo",
        "approval",
        chunk_id,
        gateway=_current_gateway.get(),
        detail={"sandbox": name},
    )
    return result


@router.post("/{name}/approvals/clear", dependencies=[Depends(require_role("operator"))])
async def clear_approvals(
    request: Request,
    name: str,
    svc: ApprovalService = Depends(_get_approval_service),
) -> dict[str, int]:
    """Clear all pending draft chunks for a sandbox."""
    actor = get_actor(request)
    logger.info("Chunks cleared (sandbox=%s, actor=%s)", name, actor)
    result = await asyncio.to_thread(svc.clear, name)
    await audit_log(request, "approval.clear", "approval", name, gateway=_current_gateway.get())
    return result


@router.get("/{name}/approvals/history")
async def get_approval_history(
    name: str,
    svc: ApprovalService = Depends(_get_approval_service),
) -> list[dict[str, Any]]:
    """Get decision history for a sandbox's draft policy."""
    return await asyncio.to_thread(svc.get_history, name)
