"""REST endpoints for the cross-gateway fleet view.

Read endpoints aggregate per-gateway state (status, OpenShell version,
sandbox policy hashes) and compute policy drift between same-named
sandboxes; the one write endpoint pushes a sandbox's policy from a
source gateway to its namesakes elsewhere.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from shoreguard.api.auth import require_gateway_role, require_role
from shoreguard.api.deps import get_services
from shoreguard.services.audit import audit_log

logger = logging.getLogger(__name__)

router = APIRouter()


class PolicySyncRequest(BaseModel):
    """Request body for a cross-gateway policy sync.

    Attributes:
        source_gateway: Gateway whose policy is the source of truth.
        sandbox: Sandbox name (same on source and targets).
        targets: Gateways to push the policy to.
    """

    source_gateway: str = Field(max_length=253)
    sandbox: str = Field(max_length=253)
    targets: list[str] = Field(min_length=1, max_length=50)


@router.get("/overview")
async def fleet_overview() -> dict[str, Any]:
    """Return per-gateway status, version, and sandbox policy hashes.

    Returns:
        dict[str, Any]: ``{"gateways": [...]}``.
    """
    return {"gateways": await get_services().fleet.overview()}


@router.get("/policy-drift")
async def fleet_policy_drift() -> dict[str, Any]:
    """Return policy drift between same-named sandboxes across gateways.

    Returns:
        dict[str, Any]: ``{"items": [...]}`` — one entry per sandbox
        name present on two or more reachable gateways.
    """
    return {"items": await get_services().fleet.policy_drift()}


@router.post("/policy-sync", dependencies=[Depends(require_role("operator"))])
async def fleet_policy_sync(body: PolicySyncRequest, request: Request) -> dict[str, Any]:
    """Push a sandbox's policy from one gateway to others.

    Args:
        body: Source gateway, sandbox name, and target gateways.
        request: Incoming HTTP request.

    Returns:
        dict[str, Any]: Per-target sync results and errors.

    Raises:
        HTTPException: 400 on an invalid request (unknown source policy,
            source among targets), 403 when a per-gateway role override
            denies one of the touched gateways, 502 when the source is
            unreachable.
    """
    # The route-level operator check only sees the global role; enforce
    # per-gateway overrides for every gateway this sync touches.
    for gateway_name in [body.source_gateway, *body.targets]:
        await require_gateway_role(request, gateway_name, "operator")
    try:
        result = await get_services().fleet.sync_policy(
            source_gateway=body.source_gateway,
            sandbox=body.sandbox,
            targets=body.targets,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — source gateway unreachable
        raise HTTPException(status_code=502, detail=f"Source gateway unavailable: {exc}") from exc
    await audit_log(
        request,
        "fleet.policy_sync",
        "policy",
        body.sandbox,
        gateway=body.source_gateway,
        detail={
            "targets": body.targets,
            "synced": result["synced"],
            "errors": list(result["errors"]),
        },
    )
    return result
