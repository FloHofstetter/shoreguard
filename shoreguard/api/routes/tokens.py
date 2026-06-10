"""REST endpoints for gateway sandbox-token issuance (diagnostic).

Wraps the OpenShell IssueSandboxToken / RefreshSandboxToken RPCs (upstream
PR #1404, v0.0.57). These RPCs take an empty request and bind the minted JWT to
the *calling* mTLS identity, so when ShoreGuard calls them the token is bound to
ShoreGuard's own gateway identity — a diagnostic to verify token issuance, not a
way to mint a token scoped to a specific sandbox. Admin-only; the token value is
returned to the operator but never written to the audit log.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request

from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_actor, get_client, get_gateway_name
from shoreguard.api.schemas import SandboxTokenResponse
from shoreguard.api.validation import check_write_rate_limit
from shoreguard.client import ShoreGuardClient
from shoreguard.services.audit import audit_log

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/issue",
    response_model=SandboxTokenResponse,
    dependencies=[Depends(require_role("admin"))],
)
async def issue_token(
    request: Request,
    client: ShoreGuardClient = Depends(get_client),
) -> dict[str, Any]:
    """Mint a gateway JWT bound to ShoreGuard's identity (diagnostic).

    Args:
        request: Incoming HTTP request.
        client: gRPC client for the active gateway.

    Returns:
        dict[str, Any]: ``{"token": str, "expires_at_ms": int}``.
    """
    check_write_rate_limit(request)
    result = await client.sandboxes.issue_token()
    logger.info("Gateway token issued (actor=%s)", get_actor(request))
    # Token value deliberately excluded from the audit detail.
    await audit_log(
        request,
        "gateway.token.issue",
        "gateway",
        get_gateway_name(request),
        gateway=get_gateway_name(request),
    )
    return result


@router.post(
    "/refresh",
    response_model=SandboxTokenResponse,
    dependencies=[Depends(require_role("admin"))],
)
async def refresh_token(
    request: Request,
    client: ShoreGuardClient = Depends(get_client),
) -> dict[str, Any]:
    """Mint a fresh gateway JWT for ShoreGuard's identity (diagnostic).

    Args:
        request: Incoming HTTP request.
        client: gRPC client for the active gateway.

    Returns:
        dict[str, Any]: ``{"token": str, "expires_at_ms": int}``.
    """
    check_write_rate_limit(request)
    result = await client.sandboxes.refresh_token()
    logger.info("Gateway token refreshed (actor=%s)", get_actor(request))
    await audit_log(
        request,
        "gateway.token.refresh",
        "gateway",
        get_gateway_name(request),
        gateway=get_gateway_name(request),
    )
    return result
