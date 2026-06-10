"""Gateway-scoped health and cluster inference configuration routes."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from shoreguard.api.auth import require_role
from shoreguard.api.deps import _get_gateway_service, get_client
from shoreguard.api.schemas import InferenceBundleResponse, InferenceConfigResponse
from shoreguard.client import ShoreGuardClient
from shoreguard.exceptions import GatewayNotConnectedError

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=None)
async def gw_health(gw: str) -> dict[str, Any] | JSONResponse:
    """Return gateway health status.

    Args:
        gw: The gateway name.

    Returns:
        dict[str, Any] | JSONResponse: Health info or 503 if disconnected.
    """
    try:
        client = _get_gateway_service().get_client(name=gw)
        return await asyncio.to_thread(client.health)
    except GatewayNotConnectedError:
        return JSONResponse(
            status_code=503,
            content={"status": "disconnected", "detail": f"Gateway '{gw}' not connected"},
        )


class SetInferenceRequest(BaseModel):
    """Request body for setting cluster inference configuration.

    Attributes:
        provider_name: Name of the inference provider.
        model_id: Identifier of the model to use.
        verify: Whether to verify the configuration before applying.
        timeout_secs: Per-route request timeout in seconds (0 = default 60s).
        route_name: Named inference route (empty for default cluster route).
    """

    provider_name: str = Field(min_length=1, max_length=253)
    model_id: str = Field(min_length=1, max_length=253)
    verify: bool = True
    timeout_secs: int = Field(default=0, ge=0, le=3600)
    route_name: str = Field(default="", max_length=253)


@router.get("/inference", response_model=InferenceConfigResponse)
async def get_inference(
    gw: str,
    route_name: str = "",
    client: ShoreGuardClient = Depends(get_client),
) -> dict[str, Any]:
    """Return current cluster inference configuration.

    Args:
        gw: The gateway name.
        route_name: Named inference route to query. Empty string returns
            the default cluster route. ``sandbox-system`` returns the
            route used for sandbox system-level model calls (OpenShell
            v0.0.25+).
        client: The ShoreGuardClient for this gateway.

    Returns:
        dict[str, Any]: Current inference provider and model settings.
    """
    return await asyncio.to_thread(client.get_cluster_inference, route_name=route_name)


@router.get("/inference/bundle", response_model=InferenceBundleResponse)
async def get_inference_bundle(
    gw: str,
    request: Request,
    client: ShoreGuardClient = Depends(get_client),
) -> dict[str, Any]:
    """Return the resolved inference bundle for this gateway.

    The bundle exposes the routes the gateway is currently serving
    after policy overlay — the cluster default plus every named
    route. API keys are redacted at the client-wrapper boundary:
    each route carries ``has_api_key`` (bool) instead of the secret
    value, so this endpoint can be read by non-admin operators
    without exposing credentials.

    Args:
        gw: The gateway name.
        request: The incoming HTTP request (for audit logging).
        client: The ShoreGuardClient for this gateway.

    Returns:
        dict[str, Any]: Bundle with revision, timestamp, and routes.
    """
    result = await asyncio.to_thread(client.get_inference_bundle)
    from shoreguard.services.audit import audit_log

    await audit_log(
        request,
        "gateway.inference_bundle.viewed",
        "inference_bundle",
        gw,
        gateway=gw,
    )
    return result


@router.put(
    "/inference",
    response_model=InferenceConfigResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def set_inference(
    gw: str,
    body: SetInferenceRequest,
    request: Request,
    client: ShoreGuardClient = Depends(get_client),
) -> dict[str, Any]:
    """Update cluster inference configuration.

    Args:
        gw: The gateway name.
        body: The inference configuration to apply.
        request: The incoming HTTP request (for audit logging).
        client: The ShoreGuardClient for this gateway.

    Returns:
        dict[str, Any]: Updated inference configuration.
    """
    actor = getattr(request.state, "user_id", "unknown")
    logger.info(
        "Inference config updated (gateway=%s, provider=%s, model=%s, actor=%s)",
        gw,
        body.provider_name,
        body.model_id,
        actor,
    )
    result = await asyncio.to_thread(
        client.set_cluster_inference,
        provider_name=body.provider_name,
        model_id=body.model_id,
        verify=body.verify,
        timeout_secs=body.timeout_secs,
        route_name=body.route_name,
    )
    from shoreguard.services.audit import audit_log
    from shoreguard.services.webhooks import fire_webhook

    await audit_log(
        request,
        "inference.update",
        "inference",
        gw,
        gateway=gw,
        detail={"provider": body.provider_name, "model": body.model_id},
    )
    await fire_webhook(
        "inference.updated",
        {
            "gateway": gw,
            "provider": body.provider_name,
            "model": body.model_id,
            "actor": actor,
        },
    )
    return result
