"""REST endpoints for draft policy approval flow."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_client
from shoreguard.client import ShoreGuardClient
from shoreguard.services.approvals import ApprovalService

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
    name: str,
    chunk_id: str,
    svc: ApprovalService = Depends(_get_approval_service),
) -> dict[str, Any]:
    """Approve a single draft policy chunk."""
    return await asyncio.to_thread(svc.approve, name, chunk_id)


@router.post(
    "/{name}/approvals/{chunk_id}/reject", dependencies=[Depends(require_role("operator"))]
)
async def reject_chunk(
    name: str,
    chunk_id: str,
    body: RejectRequest | None = None,
    svc: ApprovalService = Depends(_get_approval_service),
) -> dict:
    """Reject a single draft policy chunk."""
    reason = body.reason if body else ""
    await asyncio.to_thread(svc.reject, name, chunk_id, reason=reason)
    return {"status": "rejected"}


@router.post("/{name}/approvals/approve-all", dependencies=[Depends(require_role("operator"))])
async def approve_all(
    name: str,
    body: ApproveAllRequest | None = None,
    svc: ApprovalService = Depends(_get_approval_service),
) -> dict[str, Any]:
    """Approve all pending draft chunks for a sandbox."""
    include_flagged = body.include_security_flagged if body else False
    return await asyncio.to_thread(svc.approve_all, name, include_security_flagged=include_flagged)


@router.post("/{name}/approvals/{chunk_id}/edit", dependencies=[Depends(require_role("operator"))])
async def edit_chunk(
    name: str,
    chunk_id: str,
    body: EditChunkRequest,
    svc: ApprovalService = Depends(_get_approval_service),
) -> dict:
    """Edit a pending draft chunk's proposed rule."""
    await asyncio.to_thread(svc.edit, name, chunk_id, body.proposed_rule)
    return {"status": "edited"}


@router.post("/{name}/approvals/{chunk_id}/undo", dependencies=[Depends(require_role("operator"))])
async def undo_chunk(
    name: str,
    chunk_id: str,
    svc: ApprovalService = Depends(_get_approval_service),
) -> dict[str, Any]:
    """Reverse an approval decision."""
    return await asyncio.to_thread(svc.undo, name, chunk_id)


@router.post("/{name}/approvals/clear", dependencies=[Depends(require_role("operator"))])
async def clear_approvals(
    name: str,
    svc: ApprovalService = Depends(_get_approval_service),
) -> dict[str, int]:
    """Clear all pending draft chunks for a sandbox."""
    return await asyncio.to_thread(svc.clear, name)


@router.get("/{name}/approvals/history")
async def get_approval_history(
    name: str,
    svc: ApprovalService = Depends(_get_approval_service),
) -> list[dict[str, Any]]:
    """Get decision history for a sandbox's draft policy."""
    return await asyncio.to_thread(svc.get_history, name)
