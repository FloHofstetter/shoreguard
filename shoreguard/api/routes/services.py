"""REST endpoints for sandbox service routing.

Wraps :class:`~shoreguard.services.services_routing.ServiceRoutingService` with
auth, validation, and audit. A *service* exposes a loopback port inside a
sandbox on a gateway-routed (optionally browser-facing) endpoint. The gateway
owns the endpoint records — these routes are a passthrough surface.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_actor, get_client, get_gateway_name
from shoreguard.api.schemas import (
    ExposeServiceRequest,
    ServiceDeleteResponse,
    ServiceEndpointListResponse,
    ServiceEndpointResponse,
)
from shoreguard.api.validation import check_write_rate_limit
from shoreguard.client import ShoreGuardClient
from shoreguard.services.audit import audit_log
from shoreguard.services.services_routing import ServiceRoutingService

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_service_routing(
    client: ShoreGuardClient = Depends(get_client),
) -> ServiceRoutingService:
    """Build a ServiceRoutingService from the injected client.

    Args:
        client: gRPC client for the active gateway.

    Returns:
        ServiceRoutingService: Service instance bound to the client.
    """
    return ServiceRoutingService(client)


@router.get("", response_model=ServiceEndpointListResponse)
async def list_services(
    sandbox: str = Query(default="", max_length=253),
    svc: ServiceRoutingService = Depends(_get_service_routing),
) -> dict[str, Any]:
    """List exposed service endpoints.

    Args:
        sandbox: Optional sandbox name filter; empty lists all sandboxes.
        svc: Injected service-routing service.

    Returns:
        dict[str, Any]: ``{"services": [...]}`` endpoint records.
    """
    services = await asyncio.to_thread(svc.list, sandbox=sandbox)
    return {"services": services}


@router.post(
    "",
    status_code=201,
    response_model=ServiceEndpointResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def expose_service(
    body: ExposeServiceRequest,
    request: Request,
    svc: ServiceRoutingService = Depends(_get_service_routing),
) -> dict[str, Any]:
    """Expose a loopback port inside a sandbox as a routed service.

    Args:
        body: Expose payload (sandbox, service, target_port, domain).
        request: Incoming HTTP request.
        svc: Injected service-routing service.

    Returns:
        dict[str, Any]: The created service endpoint record.
    """
    check_write_rate_limit(request)
    result = await asyncio.to_thread(
        svc.expose,
        sandbox=body.sandbox,
        service=body.service,
        target_port=body.target_port,
        domain=body.domain,
    )
    logger.info(
        "Service exposed (sandbox=%s, service=%s, port=%d, actor=%s)",
        body.sandbox,
        body.service,
        body.target_port,
        get_actor(request),
    )
    await audit_log(
        request,
        "service.expose",
        "service",
        f"{body.sandbox}/{body.service}",
        gateway=get_gateway_name(request),
        detail={"target_port": body.target_port, "domain": body.domain},
    )
    return result


@router.get("/{sandbox}/{service}", response_model=ServiceEndpointResponse)
async def get_service(
    sandbox: str,
    service: str,
    svc: ServiceRoutingService = Depends(_get_service_routing),
) -> dict[str, Any]:
    """Get a single exposed service endpoint.

    Args:
        sandbox: Sandbox name.
        service: Service name within the sandbox.
        svc: Injected service-routing service.

    Returns:
        dict[str, Any]: The service endpoint record.
    """
    return await asyncio.to_thread(svc.get, sandbox=sandbox, service=service)


@router.delete(
    "/{sandbox}/{service}",
    response_model=ServiceDeleteResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def delete_service(
    sandbox: str,
    service: str,
    request: Request,
    svc: ServiceRoutingService = Depends(_get_service_routing),
) -> dict[str, bool]:
    """Delete an exposed service endpoint.

    Args:
        sandbox: Sandbox name.
        service: Service name within the sandbox.
        request: Incoming HTTP request.
        svc: Injected service-routing service.

    Returns:
        dict[str, bool]: ``{"deleted": bool}``.
    """
    check_write_rate_limit(request)
    deleted = await asyncio.to_thread(svc.delete, sandbox=sandbox, service=service)
    if deleted:
        logger.info(
            "Service deleted (sandbox=%s, service=%s, actor=%s)",
            sandbox,
            service,
            get_actor(request),
        )
        await audit_log(
            request,
            "service.delete",
            "service",
            f"{sandbox}/{service}",
            gateway=get_gateway_name(request),
        )
    return {"deleted": deleted}
