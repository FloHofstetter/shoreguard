"""User, service-principal, group, and role management API (admin-only)."""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    pass

from shoreguard.api.auth import (
    COOKIE_NAME,
    ROLES,
    add_group_member,
    create_group,
    create_service_principal,
    create_user,
    delete_group,
    delete_service_principal,
    delete_user,
    get_group,
    list_gateway_roles_for_sp,
    list_gateway_roles_for_user,
    list_group_gateway_roles,
    list_groups,
    list_service_principals,
    list_users,
    remove_gateway_role,
    remove_group_gateway_role,
    remove_group_member,
    require_role,
    rotate_service_principal,
    set_gateway_role,
    set_group_gateway_role,
    update_group,
    verify_session_token,
)
from shoreguard.api.schemas import (
    GatewayRoleResponse,
    GroupDetailResponse,
    GroupMemberResponse,
    GroupResponse,
    OkResponse,
    ServicePrincipalCreateResponse,
    ServicePrincipalResponse,
    UserCreateResponse,
    UserResponse,
)
from shoreguard.api.validation import valid_email
from shoreguard.config import VALID_GATEWAY_NAME_RE
from shoreguard.services.audit import audit_log

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


def _get_actor(request: Request) -> str:
    """Extract acting user identity from request state.

    Args:
        request: Incoming HTTP request.

    Returns:
        str: User identifier or ``"unknown"``.
    """
    user_id = getattr(request.state, "user_id", None)
    return str(user_id) if user_id else "unknown"


router = APIRouter()


# ─── User management (admin-only) ───────────────────────────────────────────


class CreateUserRequest(BaseModel):
    """Request body for inviting a user.

    Attributes:
        email: Email address of the user to invite.
        role: Role to assign (default ``"viewer"``).
    """

    email: str
    role: str = "viewer"


@router.get(
    "/api/auth/users",
    dependencies=[Depends(require_role("admin"))],
    response_model=list[UserResponse],
)
async def get_users(request: Request) -> list[dict[str, Any]]:
    """List all users (admin only).

    Args:
        request: Incoming HTTP request.

    Returns:
        list[dict[str, Any]]: All registered users.
    """
    return list_users()


@router.post(
    "/api/auth/users",
    status_code=201,
    dependencies=[Depends(require_role("admin"))],
    response_model=UserCreateResponse,
)
async def create_user_endpoint(
    request: Request, body: CreateUserRequest
) -> dict[str, Any] | JSONResponse:
    """Invite a new user (admin only). Returns an invite token.

    Args:
        request: Incoming HTTP request.
        body: User email and role.

    Returns:
        dict[str, Any] | JSONResponse: Created user info including invite token.

    Raises:
        HTTPException: If the role or email is invalid, the email already
            exists, or user creation fails.
    """
    if body.role not in ROLES:
        raise HTTPException(400, f"Invalid role: {body.role!r} (must be one of {ROLES})")
    if not body.email.strip():
        raise HTTPException(400, "Email is required")
    if not valid_email(body.email):
        raise HTTPException(400, "Invalid email format")
    try:
        info = create_user(body.email.strip(), None, body.role)
    except IntegrityError:
        logger.warning(
            "Duplicate user creation attempt (email=%s, actor=%s)",
            body.email.strip(),
            _get_actor(request),
        )
        raise HTTPException(409, f"A user with email '{body.email.strip()}' already exists")
    except Exception:
        logger.exception("Failed to create user")
        raise HTTPException(500, "Failed to create user")
    logger.info(
        "User invited (email=%s, role=%s, actor=%s)", info["email"], body.role, _get_actor(request)
    )
    await audit_log(request, "user.invite", "user", info["email"], detail={"role": body.role})
    return info


@router.delete(
    "/api/auth/users/{user_id}",
    dependencies=[Depends(require_role("admin"))],
    response_model=OkResponse,
)
async def delete_user_endpoint(request: Request, user_id: int) -> dict[str, Any] | JSONResponse:
    """Delete a user (admin only).

    Args:
        request: Incoming HTTP request.
        user_id: Database ID of the user to delete.

    Returns:
        dict[str, Any] | JSONResponse: Confirmation or error response.

    Raises:
        HTTPException: If attempting self-deletion, deleting the last admin,
            or the user does not exist.
    """
    # Prevent self-deletion
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        result = verify_session_token(cookie)
        if result and result[0] == user_id:
            raise HTTPException(400, "Cannot delete your own account")
    # Prevent deleting the last admin
    users = list_users()
    active_admins = [u for u in users if u.get("role") == "admin" and u.get("is_active")]
    target_is_admin = any(u["id"] == user_id and u.get("role") == "admin" for u in users)
    if target_is_admin and len(active_admins) <= 1:
        raise HTTPException(400, "Cannot delete the last admin user")
    if delete_user(user_id):
        logger.info("User deleted (user_id=%s, actor=%s)", user_id, _get_actor(request))
        await audit_log(request, "user.delete", "user", str(user_id))
        return {"ok": True}
    raise HTTPException(404, "User not found")


# ─── Gateway-scoped role management (admin-only) ──────────────────────────


class SetGatewayRoleRequest(BaseModel):
    """Request body for setting a per-gateway role override.

    Attributes:
        role: Role to assign for the gateway scope.
    """

    role: str


@router.get(
    "/api/auth/users/{user_id}/gateway-roles",
    dependencies=[Depends(require_role("admin"))],
    response_model=list[GatewayRoleResponse],
)
async def get_user_gateway_roles(user_id: int) -> list[dict[str, Any]]:
    """List all gateway-scoped role overrides for a user.

    Args:
        user_id: Database ID of the user.

    Returns:
        list[dict[str, Any]]: Gateway role overrides for the user.
    """
    return await asyncio.to_thread(list_gateway_roles_for_user, user_id)


@router.put(
    "/api/auth/users/{user_id}/gateway-roles/{gw}",
    dependencies=[Depends(require_role("admin"))],
    response_model=GatewayRoleResponse,
)
async def set_user_gateway_role(
    request: Request, user_id: int, gw: str, body: SetGatewayRoleRequest
) -> dict[str, Any] | JSONResponse:
    """Set or update a per-gateway role for a user.

    Args:
        request: Incoming HTTP request.
        user_id: Database ID of the user.
        gw: Gateway name.
        body: Role to assign.

    Returns:
        dict[str, Any] | JSONResponse: Updated gateway role mapping.

    Raises:
        HTTPException: If the gateway name or role is invalid, or a gateway
            role conflict occurs.
    """
    if not VALID_GATEWAY_NAME_RE.match(gw):
        logger.warning(
            "Invalid gateway name rejected (gateway=%s, actor=%s)", gw, _get_actor(request)
        )
        raise HTTPException(400, "Invalid gateway name")
    if body.role not in ROLES:
        logger.warning("Invalid role rejected (role=%s, actor=%s)", body.role, _get_actor(request))
        raise HTTPException(400, f"Invalid role: {body.role!r} (must be one of {ROLES})")
    try:
        result = await asyncio.to_thread(
            set_gateway_role, user_id=user_id, gateway_name=gw, role=body.role
        )
    except IntegrityError:
        logger.warning(
            "Gateway role conflict (user_id=%s, gateway=%s, role=%s, actor=%s)",
            user_id,
            gw,
            body.role,
            _get_actor(request),
        )
        raise HTTPException(409, "Gateway role conflict")
    logger.info(
        "User gateway role set (user_id=%s, gateway=%s, role=%s, actor=%s)",
        user_id,
        gw,
        body.role,
        _get_actor(request),
    )
    await audit_log(
        request,
        "user.gateway_role.set",
        "user",
        str(user_id),
        detail={"gateway": gw, "role": body.role},
    )
    return result


@router.delete(
    "/api/auth/users/{user_id}/gateway-roles/{gw}",
    dependencies=[Depends(require_role("admin"))],
    response_model=OkResponse,
)
async def delete_user_gateway_role(
    request: Request, user_id: int, gw: str
) -> dict[str, Any] | JSONResponse:
    """Remove a per-gateway role override for a user (falls back to global role).

    Args:
        request: Incoming HTTP request.
        user_id: Database ID of the user.
        gw: Gateway name.

    Returns:
        dict[str, Any] | JSONResponse: Confirmation or error response.

    Raises:
        HTTPException: If the gateway name is invalid or the override does
            not exist.
    """
    if not VALID_GATEWAY_NAME_RE.match(gw):
        logger.warning(
            "Invalid gateway name rejected (gateway=%s, actor=%s)", gw, _get_actor(request)
        )
        raise HTTPException(400, "Invalid gateway name")
    if await asyncio.to_thread(remove_gateway_role, user_id=user_id, gateway_name=gw):
        logger.info(
            "User gateway role removed (user_id=%s, gateway=%s, actor=%s)",
            user_id,
            gw,
            _get_actor(request),
        )
        await audit_log(
            request,
            "user.gateway_role.remove",
            "user",
            str(user_id),
            detail={"gateway": gw},
        )
        return {"ok": True}
    logger.warning(
        "Gateway role not found for deletion (user_id=%s, gateway=%s, actor=%s)",
        user_id,
        gw,
        _get_actor(request),
    )
    raise HTTPException(404, "Gateway role not found")


@router.get(
    "/api/auth/service-principals/{sp_id}/gateway-roles",
    dependencies=[Depends(require_role("admin"))],
    response_model=list[GatewayRoleResponse],
)
async def get_sp_gateway_roles(sp_id: int) -> list[dict[str, Any]]:
    """List all gateway-scoped role overrides for a service principal.

    Args:
        sp_id: Database ID of the service principal.

    Returns:
        list[dict[str, Any]]: Gateway role overrides for the service principal.
    """
    return await asyncio.to_thread(list_gateway_roles_for_sp, sp_id)


@router.put(
    "/api/auth/service-principals/{sp_id}/gateway-roles/{gw}",
    dependencies=[Depends(require_role("admin"))],
    response_model=GatewayRoleResponse,
)
async def set_sp_gateway_role_endpoint(
    request: Request, sp_id: int, gw: str, body: SetGatewayRoleRequest
) -> dict[str, Any] | JSONResponse:
    """Set or update a per-gateway role for a service principal.

    Args:
        request: Incoming HTTP request.
        sp_id: Database ID of the service principal.
        gw: Gateway name.
        body: Role to assign.

    Returns:
        dict[str, Any] | JSONResponse: Updated gateway role mapping.

    Raises:
        HTTPException: If the gateway name or role is invalid, or a gateway
            role conflict occurs.
    """
    if not VALID_GATEWAY_NAME_RE.match(gw):
        logger.warning(
            "Invalid gateway name rejected (gateway=%s, actor=%s)", gw, _get_actor(request)
        )
        raise HTTPException(400, "Invalid gateway name")
    if body.role not in ROLES:
        logger.warning("Invalid role rejected (role=%s, actor=%s)", body.role, _get_actor(request))
        raise HTTPException(400, f"Invalid role: {body.role!r} (must be one of {ROLES})")
    try:
        result = await asyncio.to_thread(
            set_gateway_role, sp_id=sp_id, gateway_name=gw, role=body.role
        )
    except IntegrityError:
        logger.warning(
            "Gateway role conflict (sp_id=%s, gateway=%s, role=%s, actor=%s)",
            sp_id,
            gw,
            body.role,
            _get_actor(request),
        )
        raise HTTPException(409, "Gateway role conflict")
    logger.info(
        "SP gateway role set (sp_id=%s, gateway=%s, role=%s, actor=%s)",
        sp_id,
        gw,
        body.role,
        _get_actor(request),
    )
    await audit_log(
        request,
        "sp.gateway_role.set",
        "service_principal",
        str(sp_id),
        detail={"gateway": gw, "role": body.role},
    )
    return result


@router.delete(
    "/api/auth/service-principals/{sp_id}/gateway-roles/{gw}",
    dependencies=[Depends(require_role("admin"))],
    response_model=OkResponse,
)
async def delete_sp_gateway_role(
    request: Request, sp_id: int, gw: str
) -> dict[str, Any] | JSONResponse:
    """Remove a per-gateway role override for a service principal.

    Args:
        request: Incoming HTTP request.
        sp_id: Database ID of the service principal.
        gw: Gateway name.

    Returns:
        dict[str, Any] | JSONResponse: Confirmation or error response.

    Raises:
        HTTPException: If the gateway name is invalid or the override does
            not exist.
    """
    if not VALID_GATEWAY_NAME_RE.match(gw):
        logger.warning(
            "Invalid gateway name rejected (gateway=%s, actor=%s)", gw, _get_actor(request)
        )
        raise HTTPException(400, "Invalid gateway name")
    if await asyncio.to_thread(remove_gateway_role, sp_id=sp_id, gateway_name=gw):
        logger.info(
            "SP gateway role removed (sp_id=%s, gateway=%s, actor=%s)",
            sp_id,
            gw,
            _get_actor(request),
        )
        await audit_log(
            request,
            "sp.gateway_role.remove",
            "service_principal",
            str(sp_id),
            detail={"gateway": gw},
        )
        return {"ok": True}
    logger.warning(
        "Gateway role not found for deletion (sp_id=%s, gateway=%s, actor=%s)",
        sp_id,
        gw,
        _get_actor(request),
    )
    raise HTTPException(404, "Gateway role not found")


# ─── Service principal management (admin-only) ─────────────────────────────


class CreateSPRequest(BaseModel):
    """Request body for creating a service principal.

    Attributes:
        name: Display name for the service principal.
        role: Role to assign (default ``"viewer"``).
        expires_at: Optional expiry timestamp (ISO-8601).
    """

    name: str
    role: str = "viewer"
    expires_at: datetime.datetime | None = None


@router.get(
    "/api/auth/service-principals",
    dependencies=[Depends(require_role("admin"))],
    response_model=list[ServicePrincipalResponse],
)
async def get_sps(request: Request) -> list[dict[str, Any]]:
    """List all service principals (admin only).

    Args:
        request: Incoming HTTP request.

    Returns:
        list[dict[str, Any]]: All registered service principals.
    """
    return list_service_principals()


@router.post(
    "/api/auth/service-principals",
    status_code=201,
    dependencies=[Depends(require_role("admin"))],
    response_model=ServicePrincipalCreateResponse,
)
async def create_sp_endpoint(
    request: Request, body: CreateSPRequest
) -> dict[str, Any] | JSONResponse:
    """Create a new service principal (admin only).

    Args:
        request: Incoming HTTP request.
        body: Service principal name and role.

    Returns:
        dict[str, Any] | JSONResponse: Created service principal info including API key.

    Raises:
        HTTPException: If the role or name is invalid, the name already
            exists, or creation fails.
    """
    if body.role not in ROLES:
        raise HTTPException(400, f"Invalid role: {body.role!r} (must be one of {ROLES})")
    if not body.name.strip():
        raise HTTPException(400, "Name is required")
    try:
        plaintext, info = create_service_principal(
            body.name.strip(), body.role, expires_at=body.expires_at
        )
    except IntegrityError:
        logger.warning(
            "Duplicate service principal creation attempt (name=%s, actor=%s)",
            body.name.strip(),
            _get_actor(request),
        )
        raise HTTPException(409, f"A service principal named '{body.name.strip()}' already exists")
    except Exception:
        logger.exception("Failed to create service principal")
        raise HTTPException(500, "Failed to create service principal")
    logger.info(
        "Service principal created (name=%s, role=%s, actor=%s)",
        body.name.strip(),
        body.role,
        _get_actor(request),
    )
    await audit_log(
        request,
        "sp.create",
        "service_principal",
        body.name.strip(),
        detail={"role": body.role},
    )
    return {"key": plaintext, **info}


@router.delete(
    "/api/auth/service-principals/{sp_id}",
    dependencies=[Depends(require_role("admin"))],
    response_model=OkResponse,
)
async def delete_sp_endpoint(request: Request, sp_id: int) -> dict[str, Any] | JSONResponse:
    """Delete a service principal (admin only).

    Args:
        request: Incoming HTTP request.
        sp_id: Database ID of the service principal to delete.

    Returns:
        dict[str, Any] | JSONResponse: Confirmation or error response.

    Raises:
        HTTPException: If the service principal does not exist.
    """
    if delete_service_principal(sp_id):
        logger.info("Service principal deleted (sp_id=%s, actor=%s)", sp_id, _get_actor(request))
        await audit_log(request, "sp.delete", "service_principal", str(sp_id))
        return {"ok": True}
    raise HTTPException(404, "Service principal not found")


@router.post(
    "/api/auth/service-principals/{sp_id}/rotate",
    dependencies=[Depends(require_role("admin"))],
    response_model=ServicePrincipalCreateResponse,
)
async def rotate_sp_endpoint(request: Request, sp_id: int) -> dict[str, Any] | JSONResponse:
    """Rotate the API key for a service principal (admin only).

    Generates a new key and immediately invalidates the old one.

    Args:
        request: Incoming HTTP request.
        sp_id: Database ID of the service principal.

    Returns:
        dict[str, Any] | JSONResponse: New key info or error response.

    Raises:
        HTTPException: If the service principal does not exist.
    """
    result = rotate_service_principal(sp_id)
    if result is None:
        raise HTTPException(404, "Service principal not found")
    plaintext, info = result
    logger.info("Service principal key rotated (sp_id=%s, actor=%s)", sp_id, _get_actor(request))
    await audit_log(request, "sp.rotate", "service_principal", str(sp_id))
    return {"key": plaintext, **info}


# ─── Group management (admin-only) ──────────────────────────────────────────


class CreateGroupRequest(BaseModel):
    """Request body for creating a group.

    Attributes:
        name: Group name.
        role: Default role for members of the group.
        description: Optional free-form description.
    """

    name: str
    role: str = "viewer"
    description: str | None = None


class UpdateGroupRequest(BaseModel):
    """Request body for updating a group.

    Attributes:
        name: New group name, if changing.
        role: New default role, if changing.
        description: New description, if changing.
    """

    name: str | None = None
    role: str | None = None
    description: str | None = None


class AddGroupMemberRequest(BaseModel):
    """Request body for adding a member to a group.

    Attributes:
        user_id: Database ID of the user to add.
    """

    user_id: int


@router.get(
    "/api/auth/groups",
    dependencies=[Depends(require_role("admin"))],
    response_model=list[GroupResponse],
)
async def get_groups(request: Request) -> list[dict[str, Any]]:
    """List all groups with member counts.

    Args:
        request: The incoming HTTP request.

    Returns:
        list[dict[str, Any]]: Group info dicts.
    """
    return await asyncio.to_thread(list_groups)


@router.post(
    "/api/auth/groups",
    dependencies=[Depends(require_role("admin"))],
    status_code=201,
    response_model=GroupResponse,
)
async def create_group_endpoint(
    request: Request, body: CreateGroupRequest
) -> dict[str, Any] | JSONResponse:
    """Create a new group.

    Args:
        request: The incoming HTTP request.
        body: Group creation payload.

    Returns:
        dict[str, Any] | JSONResponse: Created group or error response.

    Raises:
        HTTPException: If the role is invalid or the group name conflicts.
    """
    if body.role not in ROLES:
        raise HTTPException(400, f"Invalid role: {body.role!r}")
    try:
        result = await asyncio.to_thread(create_group, body.name, body.role, body.description)
    except IntegrityError:
        raise HTTPException(409, "Group name already exists")
    await audit_log(request, "group.create", "group", body.name, detail={"role": body.role})
    return result


@router.get(
    "/api/auth/groups/{group_id}",
    dependencies=[Depends(require_role("admin"))],
    response_model=GroupDetailResponse,
)
async def get_group_endpoint(request: Request, group_id: int) -> dict[str, Any] | JSONResponse:
    """Get a group with its member list.

    Args:
        request: The incoming HTTP request.
        group_id: Database ID of the group.

    Returns:
        dict[str, Any] | JSONResponse: Group info or 404.

    Raises:
        HTTPException: If the group does not exist.
    """
    result = await asyncio.to_thread(get_group, group_id)
    if result is None:
        raise HTTPException(404, "Group not found")
    return result


@router.put(
    "/api/auth/groups/{group_id}",
    dependencies=[Depends(require_role("admin"))],
    response_model=GroupResponse,
)
async def update_group_endpoint(
    request: Request, group_id: int, body: UpdateGroupRequest
) -> dict[str, Any] | JSONResponse:
    """Update a group.

    Args:
        request: The incoming HTTP request.
        group_id: Database ID of the group.
        body: Update payload.

    Returns:
        dict[str, Any] | JSONResponse: Updated group or error.

    Raises:
        HTTPException: If the role is invalid or the new name conflicts.
    """
    if body.role is not None and body.role not in ROLES:
        raise HTTPException(400, f"Invalid role: {body.role!r}")
    try:
        result = await asyncio.to_thread(
            update_group, group_id, name=body.name, role=body.role, description=body.description
        )
    except IntegrityError:
        raise HTTPException(409, "Group name already exists")
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    await audit_log(request, "group.update", "group", result["name"], detail=changes)
    return result


@router.delete(
    "/api/auth/groups/{group_id}",
    dependencies=[Depends(require_role("admin"))],
    response_model=OkResponse,
)
async def delete_group_endpoint(request: Request, group_id: int) -> dict[str, Any] | JSONResponse:
    """Delete a group.

    Args:
        request: The incoming HTTP request.
        group_id: Database ID of the group.

    Returns:
        dict[str, Any] | JSONResponse: Success or 404.

    Raises:
        HTTPException: If the group does not exist.
    """
    # Fetch group name for audit before deleting
    info = await asyncio.to_thread(get_group, group_id)
    if info is None:
        raise HTTPException(404, "Group not found")
    await asyncio.to_thread(delete_group, group_id)
    await audit_log(request, "group.delete", "group", info["name"])
    return {"ok": True}


# ─── Group membership (admin-only) ──────────────────────────────────────────


@router.post(
    "/api/auth/groups/{group_id}/members",
    dependencies=[Depends(require_role("admin"))],
    status_code=201,
    response_model=GroupMemberResponse,
)
async def add_group_member_endpoint(
    request: Request, group_id: int, body: AddGroupMemberRequest
) -> dict[str, Any] | JSONResponse:
    """Add a user to a group.

    Args:
        request: The incoming HTTP request.
        group_id: Database ID of the group.
        body: Member payload with user_id.

    Returns:
        dict[str, Any] | JSONResponse: Membership info or error.

    Raises:
        HTTPException: If the user is already a member of the group.
    """
    try:
        result = await asyncio.to_thread(add_group_member, group_id, body.user_id)
    except IntegrityError:
        raise HTTPException(409, "User is already a member")
    await audit_log(
        request,
        "group.member.add",
        "group",
        result["group_name"],
        detail={"user_id": body.user_id, "user_email": result["user_email"]},
    )
    return result


@router.delete(
    "/api/auth/groups/{group_id}/members/{user_id}",
    dependencies=[Depends(require_role("admin"))],
    response_model=OkResponse,
)
async def remove_group_member_endpoint(
    request: Request, group_id: int, user_id: int
) -> dict[str, Any] | JSONResponse:
    """Remove a user from a group.

    Args:
        request: The incoming HTTP request.
        group_id: Database ID of the group.
        user_id: Database ID of the user to remove.

    Returns:
        dict[str, Any] | JSONResponse: Success or 404.

    Raises:
        HTTPException: If the group or membership does not exist.
    """
    # Fetch group name for audit
    info = await asyncio.to_thread(get_group, group_id)
    if info is None:
        raise HTTPException(404, "Group not found")
    removed = await asyncio.to_thread(remove_group_member, group_id, user_id)
    if not removed:
        raise HTTPException(404, "Membership not found")
    await audit_log(
        request,
        "group.member.remove",
        "group",
        info["name"],
        detail={"user_id": user_id},
    )
    return {"ok": True}


# ─── Group gateway roles (admin-only) ───────────────────────────────────────


@router.get(
    "/api/auth/groups/{group_id}/gateway-roles",
    dependencies=[Depends(require_role("admin"))],
    response_model=list[GatewayRoleResponse],
)
async def get_group_gateway_roles_endpoint(request: Request, group_id: int) -> list[dict[str, Any]]:
    """List gateway-scoped roles for a group.

    Args:
        request: The incoming HTTP request.
        group_id: Database ID of the group.

    Returns:
        list[dict[str, Any]]: Gateway role dicts.
    """
    return await asyncio.to_thread(list_group_gateway_roles, group_id)


@router.put(
    "/api/auth/groups/{group_id}/gateway-roles/{gw}",
    dependencies=[Depends(require_role("admin"))],
    response_model=GatewayRoleResponse,
)
async def set_group_gateway_role_endpoint(
    request: Request, group_id: int, gw: str, body: SetGatewayRoleRequest
) -> dict[str, Any] | JSONResponse:
    """Set a per-gateway role for a group.

    Args:
        request: The incoming HTTP request.
        group_id: Database ID of the group.
        gw: Gateway name.
        body: Role payload.

    Returns:
        dict[str, Any] | JSONResponse: Saved role or error.

    Raises:
        HTTPException: If the role is invalid.
    """
    if body.role not in ROLES:
        raise HTTPException(400, f"Invalid role: {body.role!r}")
    result = await asyncio.to_thread(set_group_gateway_role, group_id, gw, body.role)
    await audit_log(
        request,
        "group.gateway_role.set",
        "group",
        str(group_id),
        detail={"gateway": gw, "role": body.role},
    )
    return result


@router.delete(
    "/api/auth/groups/{group_id}/gateway-roles/{gw}",
    dependencies=[Depends(require_role("admin"))],
    response_model=OkResponse,
)
async def delete_group_gateway_role_endpoint(
    request: Request, group_id: int, gw: str
) -> dict[str, Any] | JSONResponse:
    """Remove a per-gateway role for a group.

    Args:
        request: The incoming HTTP request.
        group_id: Database ID of the group.
        gw: Gateway name.

    Returns:
        dict[str, Any] | JSONResponse: Success or 404.

    Raises:
        HTTPException: If the gateway role does not exist.
    """
    removed = await asyncio.to_thread(remove_group_gateway_role, group_id, gw)
    if not removed:
        raise HTTPException(404, "Gateway role not found")
    await audit_log(
        request,
        "group.gateway_role.remove",
        "group",
        str(group_id),
        detail={"gateway": gw},
    )
    return {"ok": True}
