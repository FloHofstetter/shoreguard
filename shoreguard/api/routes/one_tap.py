"""One-tap approval voting via signed links (no session required).

The token in the request body is the credential: it was minted by
:mod:`shoreguard.services.approval_links` when an approval webhook
fired, it is HMAC-signed with the session secret, expires after
``SHOREGUARD_WEBHOOK_ONE_TAP_TTL`` seconds, and encodes exactly one
``(gateway, sandbox, chunk, decision)`` vote. The whole feature is
inert unless ``SHOREGUARD_WEBHOOK_ONE_TAP_APPROVALS`` is enabled.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from shoreguard.api.deps import get_services
from shoreguard.services.approval_links import verify_one_tap_token
from shoreguard.services.approvals import ApprovalService
from shoreguard.services.audit import audit_log
from shoreguard.services.webhooks import fire_webhook
from shoreguard.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()

ONE_TAP_ACTOR = "one-tap-link"


class OneTapRequest(BaseModel):
    """Request body carrying the signed one-tap token.

    Attributes:
        token: The signed token from the notification link.
    """

    token: str


@router.post("/api/approvals/one-tap", response_model=None)
async def cast_one_tap_vote(request: Request, body: OneTapRequest) -> dict[str, Any] | JSONResponse:
    """Cast the vote encoded in a signed one-tap token.

    Args:
        request: Incoming HTTP request (used for audit logging).
        body: Request body with the signed token.

    Returns:
        dict[str, Any] | JSONResponse: Vote outcome — ``approved`` /
            ``rejected``, or a 202 receipt when quorum is not yet met.

    Raises:
        HTTPException: 404 if the feature is disabled, 400 if the token is
            invalid or expired, 403/409 if the workflow refuses the vote,
            502 if the gateway is unreachable.
    """
    if not get_settings().webhooks.one_tap_approvals:
        raise HTTPException(status_code=404, detail="One-tap approvals are disabled")
    data = verify_one_tap_token(body.token)
    if data is None:
        raise HTTPException(status_code=400, detail="Invalid or expired link")

    gateway = data["gateway"]
    sandbox = data["sandbox"]
    chunk_id = data["chunk_id"]
    decision = data["decision"]
    request.state.user_id = ONE_TAP_ACTOR

    services = get_services()
    try:
        client = await services.gateway.get_client(gateway)
    except Exception as exc:  # noqa: BLE001 — surface as gateway error
        raise HTTPException(status_code=502, detail=f"Gateway unavailable: {exc}") from exc
    svc = ApprovalService(client)

    workflow = await services.approval_workflow.get_workflow(gateway, sandbox)
    if workflow is not None:
        try:
            vote = await services.approval_workflow.record_decision(
                gateway,
                sandbox,
                chunk_id,
                actor=ONE_TAP_ACTOR,
                role="operator",
                decision=decision,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if decision == "approve" and not vote.quorum_met:
            await audit_log(
                request,
                "approval.vote_cast",
                "approval",
                chunk_id,
                gateway=gateway,
                detail={"sandbox": sandbox, "decision": decision, "via": "one-tap"},
            )
            approve_votes = sum(1 for d in vote.decisions if d["decision"] == "approve")
            return JSONResponse(
                status_code=202,
                content={
                    "status": "pending",
                    "votes": approve_votes,
                    "needed": vote.votes_needed,
                },
            )

    if decision == "approve":
        await svc.approve(sandbox, chunk_id)
        action, event, status = "approval.approve", "approval.approved", "approved"
    else:
        await svc.reject(sandbox, chunk_id, reason="one-tap link")
        action, event, status = "approval.reject", "approval.rejected", "rejected"

    logger.info("One-tap %s (gateway=%s, sandbox=%s, chunk=%s)", status, gateway, sandbox, chunk_id)
    await audit_log(
        request,
        action,
        "approval",
        chunk_id,
        gateway=gateway,
        detail={"sandbox": sandbox, "via": "one-tap"},
    )
    await fire_webhook(
        event,
        {"sandbox": sandbox, "chunk_id": chunk_id, "actor": ONE_TAP_ACTOR, "gateway": gateway},
    )
    return {"status": status, "sandbox": sandbox, "chunk_id": chunk_id}
