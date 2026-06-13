"""REST endpoints for tenant management.

Admin-only CRUD over ShoreGuard's tenant grouping (a visibility boundary
of gateways + users), plus a per-tenant spend/health rollup readable by
the tenant's own members. Tenant events are cross-cutting (not tied to a
single gateway), so every ``audit_log`` call here omits ``gateway=`` and
lands in the global/unattributed audit bucket.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_services
from shoreguard.config import VALID_GATEWAY_NAME_RE
from shoreguard.services.audit import audit_log

logger = logging.getLogger(__name__)

router = APIRouter()


class TenantRequest(BaseModel):
    """Create/update payload for a tenant.

    Attributes:
        name: Unique tenant name.
        description: Optional human-readable description.
    """

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)


@router.get("", dependencies=[Depends(require_role("admin"))])
async def list_tenants() -> dict[str, Any]:
    """List all tenants with gateway and user counts.

    Returns:
        dict[str, Any]: ``{"items": [...]}``.
    """
    return {"items": await get_services().tenant.list_tenants()}


@router.post("", dependencies=[Depends(require_role("admin"))], status_code=201)
async def create_tenant(request: Request, body: TenantRequest) -> dict[str, Any]:
    """Create a tenant.

    Args:
        request: The incoming HTTP request.
        body: Tenant parameters.

    Returns:
        dict[str, Any]: The created tenant record.

    Raises:
        HTTPException: 409 if a tenant with this name already exists.
    """
    try:
        tenant = await get_services().tenant.create_tenant(body.name.strip(), body.description)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await audit_log(
        request, "tenant.create", "tenant", str(tenant["id"]), detail={"name": tenant["name"]}
    )
    return tenant


@router.get("/{tenant_id}", dependencies=[Depends(require_role("admin"))])
async def get_tenant(tenant_id: int) -> dict[str, Any]:
    """Return a tenant with its gateway and user membership.

    Args:
        tenant_id: Tenant primary key.

    Returns:
        dict[str, Any]: The tenant record with members.

    Raises:
        HTTPException: 404 if the tenant does not exist.
    """
    tenant = await get_services().tenant.get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(404, "Tenant not found")
    return tenant


@router.put("/{tenant_id}", dependencies=[Depends(require_role("admin"))])
async def update_tenant(request: Request, tenant_id: int, body: TenantRequest) -> dict[str, Any]:
    """Update a tenant's name/description.

    Args:
        request: The incoming HTTP request.
        tenant_id: Tenant primary key.
        body: New tenant parameters.

    Returns:
        dict[str, Any]: The updated record.

    Raises:
        HTTPException: 404 if not found, 409 on a name collision.
    """
    try:
        tenant = await get_services().tenant.update_tenant(
            tenant_id, name=body.name.strip(), description=body.description
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if tenant is None:
        raise HTTPException(404, "Tenant not found")
    await audit_log(
        request, "tenant.update", "tenant", str(tenant_id), detail={"name": tenant["name"]}
    )
    return tenant


@router.delete("/{tenant_id}", dependencies=[Depends(require_role("admin"))])
async def delete_tenant(request: Request, tenant_id: int) -> dict[str, Any]:
    """Delete a tenant (cascades to its memberships).

    Args:
        request: The incoming HTTP request.
        tenant_id: Tenant primary key.

    Returns:
        dict[str, Any]: ``{"deleted": bool}``.

    Raises:
        HTTPException: 404 if the tenant does not exist.
    """
    deleted = await get_services().tenant.delete_tenant(tenant_id)
    if not deleted:
        raise HTTPException(404, "Tenant not found")
    await audit_log(request, "tenant.delete", "tenant", str(tenant_id))
    return {"deleted": True}


@router.put("/{tenant_id}/gateways/{gateway_name}", dependencies=[Depends(require_role("admin"))])
async def add_tenant_gateway(request: Request, tenant_id: int, gateway_name: str) -> dict[str, Any]:
    """Assign a gateway to a tenant.

    Args:
        request: The incoming HTTP request.
        tenant_id: Tenant primary key.
        gateway_name: Gateway name to add.

    Returns:
        dict[str, Any]: ``{"added": True}``.

    Raises:
        HTTPException: 400 on an invalid name, 404 if the tenant or gateway
            is unknown.
    """
    if not VALID_GATEWAY_NAME_RE.match(gateway_name):
        raise HTTPException(400, "Invalid gateway name")
    ok = await get_services().tenant.add_gateway(tenant_id, gateway_name)
    if not ok:
        raise HTTPException(404, "Tenant or gateway not found")
    await audit_log(
        request, "tenant.gateway.add", "tenant", str(tenant_id), detail={"gateway": gateway_name}
    )
    return {"added": True}


@router.delete(
    "/{tenant_id}/gateways/{gateway_name}", dependencies=[Depends(require_role("admin"))]
)
async def remove_tenant_gateway(
    request: Request, tenant_id: int, gateway_name: str
) -> dict[str, Any]:
    """Remove a gateway from a tenant.

    Args:
        request: The incoming HTTP request.
        tenant_id: Tenant primary key.
        gateway_name: Gateway name to remove.

    Returns:
        dict[str, Any]: ``{"removed": bool}``.

    Raises:
        HTTPException: 400 on an invalid gateway name.
    """
    if not VALID_GATEWAY_NAME_RE.match(gateway_name):
        raise HTTPException(400, "Invalid gateway name")
    removed = await get_services().tenant.remove_gateway(tenant_id, gateway_name)
    if removed:
        await audit_log(
            request,
            "tenant.gateway.remove",
            "tenant",
            str(tenant_id),
            detail={"gateway": gateway_name},
        )
    return {"removed": removed}


@router.put("/{tenant_id}/users/{user_id}", dependencies=[Depends(require_role("admin"))])
async def add_tenant_user(request: Request, tenant_id: int, user_id: int) -> dict[str, Any]:
    """Assign a user to a tenant.

    Args:
        request: The incoming HTTP request.
        tenant_id: Tenant primary key.
        user_id: User primary key to add.

    Returns:
        dict[str, Any]: ``{"added": True}``.

    Raises:
        HTTPException: 404 if the tenant or user is unknown.
    """
    ok = await get_services().tenant.add_user(tenant_id, user_id)
    if not ok:
        raise HTTPException(404, "Tenant or user not found")
    await audit_log(
        request, "tenant.user.add", "tenant", str(tenant_id), detail={"user_id": user_id}
    )
    return {"added": True}


@router.delete("/{tenant_id}/users/{user_id}", dependencies=[Depends(require_role("admin"))])
async def remove_tenant_user(request: Request, tenant_id: int, user_id: int) -> dict[str, Any]:
    """Remove a user from a tenant.

    Args:
        request: The incoming HTTP request.
        tenant_id: Tenant primary key.
        user_id: User primary key to remove.

    Returns:
        dict[str, Any]: ``{"removed": bool}``.
    """
    removed = await get_services().tenant.remove_user(tenant_id, user_id)
    if removed:
        await audit_log(
            request, "tenant.user.remove", "tenant", str(tenant_id), detail={"user_id": user_id}
        )
    return {"removed": removed}


@router.get("/{tenant_id}/rollup")
async def tenant_rollup(request: Request, tenant_id: int) -> dict[str, Any]:
    """Return a tenant's spend and gateway-health rollup.

    Readable by an admin or by a member of the tenant. Non-members get 403.

    Args:
        request: The incoming HTTP request.
        tenant_id: Tenant primary key.

    Returns:
        dict[str, Any]: The rollup payload.

    Raises:
        HTTPException: 404 if the tenant is unknown, 403 if the caller is a
            non-admin who is not a member of the tenant.
    """
    from shoreguard.settings import get_settings

    services = get_services()
    detail = await services.tenant.get_tenant(tenant_id)
    if detail is None:
        raise HTTPException(404, "Tenant not found")
    role = getattr(request.state, "role", None)
    if role != "admin":
        user_db_id = getattr(request.state, "user_db_id", None)
        member_ids = {u["id"] for u in detail["users"]}
        if user_db_id is None or user_db_id not in member_ids:
            raise HTTPException(403, "Not a member of this tenant")
    days = get_settings().tenant.rollup_window_days
    return await services.tenant.rollup(tenant_id, services.budget, days=days)
