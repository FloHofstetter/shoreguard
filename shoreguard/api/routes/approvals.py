"""REST endpoints for draft policy approval flow."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import shoreguard.services.approval_workflow as _wf_mod
import shoreguard.services.policy_pin as _pin_mod
from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_actor, get_client, get_gateway_name
from shoreguard.api.schemas import (
    ApprovalBulkResponse,
    ApprovalChunkResponse,
    ApprovalClearResponse,
    ApprovalDraftResponse,
    ApprovalVoteResponse,
    ApprovalWorkflowConfig,
    ApprovalWorkflowResponse,
    MessageResponse,
)
from shoreguard.client import ShoreGuardClient
from shoreguard.exceptions import PolicyLockedError
from shoreguard.services.approval_workflow import VoteResult
from shoreguard.services.approvals import ApprovalService
from shoreguard.services.audit import audit_log
from shoreguard.services.policy_status import policy_status_broker
from shoreguard.services.webhooks import fire_webhook

logger = logging.getLogger(__name__)

router = APIRouter()


def _check_policy_pin(request: Request, sandbox_name: str) -> None:
    """Raise HTTP 423 if the sandbox's policy is pinned.

    Args:
        request: Incoming HTTP request (for gateway name).
        sandbox_name: Sandbox to check.

    Raises:
        HTTPException: 423 Locked if an active pin exists.
    """
    svc = _pin_mod.policy_pin_service
    if svc is None:
        return
    try:
        svc.check_pin(get_gateway_name(request), sandbox_name)
    except PolicyLockedError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc


_POLICY_LOAD_TIMEOUT = 30.0


def _get_actor_role(request: Request) -> str:
    """Return the effective role of the caller (set by ``require_role``).

    Args:
        request: Incoming HTTP request.

    Returns:
        str: ``admin``, ``operator`` or ``viewer`` — defaults to ``viewer``.
    """
    return getattr(request.state, "role", None) or "viewer"


def _fire_vote_webhooks(
    event_base: str,
    request: Request,
    *,
    sandbox: str,
    chunk_id: str,
    actor: str,
    result: VoteResult,
) -> list[Any]:
    """Build the list of webhook coroutines to fire after a vote.

    Args:
        event_base: Either ``approval.vote_cast`` or the terminal event.
        request: Incoming HTTP request.
        sandbox: Sandbox name.
        chunk_id: Chunk the vote applied to.
        actor: Voting user identity.
        result: VoteResult from the service layer.

    Returns:
        list[Any]: Coroutines ready to be awaited.
    """
    gateway = get_gateway_name(request)
    coros: list[Any] = [
        fire_webhook(
            event_base,
            {
                "sandbox": sandbox,
                "chunk_id": chunk_id,
                "actor": actor,
                "gateway": gateway,
                "votes": sum(1 for d in result.decisions if d["decision"] == "approve"),
                "needed": result.votes_needed,
            },
        )
    ]
    if result.escalated:
        coros.append(
            fire_webhook(
                "approval.escalated",
                {
                    "sandbox": sandbox,
                    "chunk_id": chunk_id,
                    "gateway": gateway,
                    "timeout_minutes": result.workflow.get("escalation_timeout_minutes"),
                },
            )
        )
    return coros


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
    wait_loaded: bool = Query(default=False),
    svc: ApprovalService = Depends(_get_approval_service),
    client: ShoreGuardClient = Depends(get_client),
) -> dict[str, Any] | JSONResponse:
    """Approve a single draft policy chunk.

    Args:
        request: Incoming HTTP request.
        name: Sandbox name.
        chunk_id: Chunk identifier.
        wait_loaded: When true, block until the proxy has loaded the new
            policy version (up to 30 s).  Eliminates the client-side
            polling that would otherwise be needed before retrying a
            request under the new policy.
        svc: Injected approval service.
        client: gRPC client for the active gateway.

    Returns:
        dict[str, Any] | JSONResponse: Updated chunk status, or a 202 vote
            receipt under an active workflow when quorum is not yet met.

    Raises:
        HTTPException: 403 if the actor is not allowed to vote, 409 if the
            workflow rejects the vote, 423 if the policy is pinned, or 504
            if ``wait_loaded`` times out.
    """
    _check_policy_pin(request, name)
    actor = get_actor(request)
    role = _get_actor_role(request)
    gateway = get_gateway_name(request)

    wf_svc = _wf_mod.approval_workflow_service
    workflow = wf_svc.get_workflow(gateway, name) if wf_svc is not None else None
    if wf_svc is not None and workflow is not None:
        try:
            vote = await asyncio.to_thread(
                wf_svc.record_decision,
                gateway,
                name,
                chunk_id,
                actor=actor,
                role=role,
                decision="approve",
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        if not vote.quorum_met:
            await audit_log(
                request,
                "approval.vote_cast",
                "approval",
                chunk_id,
                gateway=gateway,
                detail={"sandbox": name, "decision": "approve"},
            )
            for coro in _fire_vote_webhooks(
                "approval.vote_cast",
                request,
                sandbox=name,
                chunk_id=chunk_id,
                actor=actor,
                result=vote,
            ):
                await coro
            approve_votes = sum(1 for d in vote.decisions if d["decision"] == "approve")
            return JSONResponse(
                status_code=202,
                content={
                    "status": "pending",
                    "votes": approve_votes,
                    "needed": vote.votes_needed,
                    "decisions": vote.decisions,
                },
            )

        logger.info("Quorum met, firing upstream approve (sandbox=%s, chunk=%s)", name, chunk_id)

    logger.info("Chunk approved (sandbox=%s, chunk_id=%s, actor=%s)", name, chunk_id, actor)
    result = await asyncio.to_thread(svc.approve, name, chunk_id)
    await audit_log(
        request,
        "approval.approve",
        "approval",
        chunk_id,
        gateway=gateway,
        detail={"sandbox": name, "workflow": workflow is not None},
    )
    await fire_webhook(
        "approval.approved",
        {
            "sandbox": name,
            "chunk_id": chunk_id,
            "actor": actor,
            "gateway": gateway,
        },
    )
    if workflow is not None:
        await fire_webhook(
            "approval.quorum_met",
            {
                "sandbox": name,
                "chunk_id": chunk_id,
                "gateway": gateway,
                "votes_needed": workflow["required_approvals"],
            },
        )
    if wait_loaded and (target := result.get("policy_version")):
        await policy_status_broker.wait_for_loaded(
            client, name, target, timeout=_POLICY_LOAD_TIMEOUT
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

    Raises:
        HTTPException: 403 if the actor is not allowed to vote, 409 if the
            workflow rejects the vote.
    """
    reason = body.reason if body else ""
    actor = get_actor(request)
    role = _get_actor_role(request)
    gateway = get_gateway_name(request)

    wf_svc = _wf_mod.approval_workflow_service
    workflow = wf_svc.get_workflow(gateway, name) if wf_svc is not None else None
    if wf_svc is not None and workflow is not None:
        try:
            await asyncio.to_thread(
                wf_svc.record_decision,
                gateway,
                name,
                chunk_id,
                actor=actor,
                role=role,
                decision="reject",
                comment=reason or None,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

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
    wait_loaded: bool = Query(default=False),
    svc: ApprovalService = Depends(_get_approval_service),
    client: ShoreGuardClient = Depends(get_client),
) -> dict[str, Any]:
    """Approve all pending draft chunks for a sandbox.

    Args:
        request: Incoming HTTP request.
        name: Sandbox name.
        body: Optional payload controlling security-flagged inclusion.
        wait_loaded: When true, block until the proxy has loaded the new
            policy version (up to 30 s).
        svc: Injected approval service.
        client: gRPC client for the active gateway.

    Returns:
        dict[str, Any]: Bulk approval result with counts.

    Raises:
        HTTPException: 409 when an approval workflow is active and the
            actor is not admin, 423 if the policy is pinned, or 504 if
            ``wait_loaded`` times out.
    """
    _check_policy_pin(request, name)
    actor = get_actor(request)
    role = _get_actor_role(request)
    gateway = get_gateway_name(request)

    wf_svc = _wf_mod.approval_workflow_service
    workflow = wf_svc.get_workflow(gateway, name) if wf_svc is not None else None
    if workflow is not None and role != "admin":
        raise HTTPException(
            status_code=409,
            detail=(
                "Bulk approve-all is disabled under an active approval workflow; "
                "admin role required to override."
            ),
        )

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
    if wait_loaded and (target := result.get("policy_version")):
        await policy_status_broker.wait_for_loaded(
            client, name, target, timeout=_POLICY_LOAD_TIMEOUT
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


# ─── Multi-stage approval workflows ──────────────────────────────────


@router.get(
    "/{name}/approval-workflow",
    response_model=ApprovalWorkflowResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def get_approval_workflow(request: Request, name: str) -> dict[str, Any]:
    """Read the multi-stage approval workflow for a sandbox.

    Args:
        request: Incoming HTTP request.
        name: Sandbox name.

    Returns:
        dict[str, Any]: Workflow config, or ``{}`` if unconfigured.
    """
    wf_svc = _wf_mod.approval_workflow_service
    if wf_svc is None:
        return {}
    workflow = wf_svc.get_workflow(get_gateway_name(request), name)
    return workflow or {}


@router.put(
    "/{name}/approval-workflow",
    response_model=ApprovalWorkflowResponse,
    dependencies=[Depends(require_role("admin"))],
)
async def upsert_approval_workflow(
    request: Request,
    name: str,
    body: ApprovalWorkflowConfig,
) -> dict[str, Any]:
    """Create or replace the approval workflow for a sandbox.

    Args:
        request: Incoming HTTP request.
        name: Sandbox name.
        body: Workflow configuration.

    Returns:
        dict[str, Any]: Stored workflow config.

    Raises:
        HTTPException: 400 on invalid config, 503 if the workflow service is
            not initialised.
    """
    wf_svc = _wf_mod.approval_workflow_service
    if wf_svc is None:
        raise HTTPException(status_code=503, detail="Approval workflow service unavailable")
    gateway = get_gateway_name(request)
    actor = get_actor(request)
    try:
        workflow = await asyncio.to_thread(
            wf_svc.upsert_workflow,
            gateway,
            name,
            required_approvals=body.required_approvals,
            required_roles=body.required_roles,
            distinct_actors=body.distinct_actors,
            escalation_timeout_minutes=body.escalation_timeout_minutes,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_log(
        request,
        "approval.workflow.upsert",
        "approval_workflow",
        name,
        gateway=gateway,
        detail={"required_approvals": body.required_approvals},
    )
    return workflow


@router.delete(
    "/{name}/approval-workflow",
    response_model=MessageResponse,
    dependencies=[Depends(require_role("admin"))],
)
async def delete_approval_workflow(request: Request, name: str) -> dict[str, str]:
    """Delete the approval workflow for a sandbox.

    Args:
        request: Incoming HTTP request.
        name: Sandbox name.

    Returns:
        dict[str, str]: Deletion status.

    Raises:
        HTTPException: 404 if no workflow exists, 503 if the workflow
            service is not initialised.
    """
    wf_svc = _wf_mod.approval_workflow_service
    if wf_svc is None:
        raise HTTPException(status_code=503, detail="Approval workflow service unavailable")
    gateway = get_gateway_name(request)
    removed = await asyncio.to_thread(wf_svc.delete_workflow, gateway, name)
    if not removed:
        raise HTTPException(status_code=404, detail="No workflow configured")
    await audit_log(
        request,
        "approval.workflow.delete",
        "approval_workflow",
        name,
        gateway=gateway,
    )
    return {"status": "deleted"}


@router.get(
    "/{name}/approvals/{chunk_id}/decisions",
    response_model=ApprovalVoteResponse,
)
async def get_chunk_decisions(request: Request, name: str, chunk_id: str) -> dict[str, Any]:
    """Return current vote state for a chunk under the active workflow.

    Args:
        request: Incoming HTTP request.
        name: Sandbox name.
        chunk_id: Chunk identifier.

    Returns:
        dict[str, Any]: Vote state with status/votes/needed/decisions.
    """
    wf_svc = _wf_mod.approval_workflow_service
    if wf_svc is None:
        return {"status": "no_workflow", "votes": 0, "needed": 0, "decisions": []}
    gateway = get_gateway_name(request)
    workflow = wf_svc.get_workflow(gateway, name)
    if workflow is None:
        return {"status": "no_workflow", "votes": 0, "needed": 0, "decisions": []}
    decisions = wf_svc.list_decisions(gateway, name, chunk_id)
    approve_votes = sum(1 for d in decisions if d["decision"] == "approve")
    return {
        "status": "pending",
        "votes": approve_votes,
        "needed": workflow["required_approvals"],
        "decisions": decisions,
    }
