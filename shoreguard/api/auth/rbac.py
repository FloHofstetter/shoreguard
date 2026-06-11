"""Role-based access control: role lookups and FastAPI dependencies."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from fastapi import Cookie, HTTPException, Query, Request, WebSocket, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    pass

from shoreguard.api.auth.core import (
    _ROLE_RANK,
    _lookup_sp_identity,
    _lookup_user,
    check_request_auth,
    is_setup_complete,
    state,
    verify_session_token,
)

logger = logging.getLogger(__name__)


# ─── Gateway-scoped role lookup ───────────────────────────────────────────


class _GatewayRoleLookupError(Exception):
    """Raised when the gateway role DB lookup fails — triggers a 503."""


async def _lookup_gateway_role(
    *, user_id: int | None = None, sp_id: int | None = None, gateway: str
) -> str | None:
    """Return the gateway-scoped role override, or None if no override exists.

    Raises ``_GatewayRoleLookupError`` on DB failure so the caller does NOT
    silently fall back to the (possibly higher) global role (fail-closed).

    Args:
        user_id: Database ID of the user, or ``None``.
        sp_id: Database ID of the service principal, or ``None``.
        gateway: Gateway name to look up the scoped role for.

    Returns:
        str | None: Scoped role string or ``None`` if no override.

    Raises:
        _GatewayRoleLookupError: If the DB query fails.
    """
    if state.session_factory is None:
        return None
    from shoreguard.models import (
        Gateway,
        GroupGatewayRole,
        GroupMember,
        SPGatewayRole,
        UserGatewayRole,
    )

    async with state.session_factory() as session:
        try:
            if user_id is not None:
                # Priority 1: individual gateway role
                row = (
                    (
                        await session.execute(
                            select(UserGatewayRole)
                            .join(Gateway, UserGatewayRole.gateway_id == Gateway.id)
                            .where(
                                UserGatewayRole.user_id == user_id,
                                Gateway.name == gateway,
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if row:
                    return row.role
                # Priority 2: group gateway role (highest rank wins)
                group_rows = (
                    await session.execute(
                        select(GroupGatewayRole.role)
                        .join(GroupMember, GroupMember.group_id == GroupGatewayRole.group_id)
                        .join(Gateway, Gateway.id == GroupGatewayRole.gateway_id)
                        .where(GroupMember.user_id == user_id, Gateway.name == gateway)
                    )
                ).all()
                if group_rows:
                    return max((r[0] for r in group_rows), key=lambda r: _ROLE_RANK.get(r, -1))
                return None
            elif sp_id is not None:
                row = (
                    (
                        await session.execute(
                            select(SPGatewayRole)
                            .join(Gateway, SPGatewayRole.gateway_id == Gateway.id)
                            .where(SPGatewayRole.sp_id == sp_id, Gateway.name == gateway)
                        )
                    )
                    .scalars()
                    .first()
                )
                return row.role if row else None
            else:
                return None
        except SQLAlchemyError:
            logger.exception("Gateway role lookup failed (gateway=%s)", gateway)
            raise _GatewayRoleLookupError(f"Gateway role lookup failed for gateway={gateway}")


async def _lookup_group_global_role(user_id: int) -> str | None:
    """Return the highest global role from all groups a user belongs to.

    Args:
        user_id: Database ID of the user.

    Returns:
        str | None: Highest group global role, or ``None`` if not in any group.

    Raises:
        _GatewayRoleLookupError: If the DB query fails.
    """
    if state.session_factory is None:
        return None
    from shoreguard.models import Group, GroupMember

    async with state.session_factory() as session:
        try:
            rows = (
                await session.execute(
                    select(Group.role)
                    .join(GroupMember, GroupMember.group_id == Group.id)
                    .where(GroupMember.user_id == user_id)
                )
            ).all()
            if not rows:
                return None
            return max((r[0] for r in rows), key=lambda r: _ROLE_RANK.get(r, -1))
        except SQLAlchemyError:
            logger.exception("Group global role lookup failed (user_id=%d)", user_id)
            raise _GatewayRoleLookupError(f"Group global role lookup failed for user_id={user_id}")


# ─── FastAPI dependencies ──────────────────────────────────────────────────


async def require_auth(request: Request) -> None:
    """Reject unauthenticated requests (401).

    Args:
        request: The incoming HTTP request.

    Raises:
        HTTPException: 401 if credentials are missing or invalid.
    """
    role = await check_request_auth(request)
    if role is not None:
        request.state.role = role
        return

    client_ip = request.client.host if request.client else "unknown"
    logger.warning(
        "Auth rejected: missing or invalid credentials (path=%s, client=%s)",
        request.url.path,
        client_ip,
    )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_role(minimum: str) -> Callable[..., Coroutine[Any, Any, None]]:
    """Return a FastAPI dependency that enforces a minimum role level.

    When inside a gateway-scoped route (gateway name on ``request.state``),
    a per-gateway role override takes precedence over the global role.

    Args:
        minimum: The minimum required role (``admin``, ``operator``, ``viewer``).

    Returns:
        Callable[..., Coroutine[Any, Any, None]]: An async FastAPI dependency
            callable.
    """
    from shoreguard.api.deps import get_gateway_name

    async def _dependency(request: Request) -> None:
        """Check that the caller has at least the required role.

        Args:
            request: The incoming HTTP request.

        Raises:
            HTTPException: 401 if unauthenticated, 403 if insufficient role.
        """
        role = getattr(request.state, "role", None)
        if role is None:
            role = await check_request_auth(request)
            if role is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or missing credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            request.state.role = role

        # Check for a gateway-scoped role override
        gateway = get_gateway_name(request)
        if gateway:
            user_db_id = getattr(request.state, "user_db_id", None)
            sp_db_id = getattr(request.state, "sp_db_id", None)
            try:
                gw_role = await _lookup_gateway_role(
                    user_id=user_db_id, sp_id=sp_db_id, gateway=gateway
                )
            except _GatewayRoleLookupError:
                raise HTTPException(
                    status_code=503,
                    detail="Gateway role lookup failed — try again later",
                )
            if gw_role:
                role = gw_role
                request.state.role = role

        # Group global role fallback (only for users, elevates if higher)
        user_db_id = getattr(request.state, "user_db_id", None)
        if user_db_id is not None:
            try:
                group_global = await _lookup_group_global_role(user_db_id)
            except _GatewayRoleLookupError:
                raise HTTPException(
                    status_code=503,
                    detail="Group role lookup failed — try again later",
                )
            if group_global and _ROLE_RANK.get(group_global, -1) > _ROLE_RANK.get(role, -1):
                role = group_global
                request.state.role = role

        if _ROLE_RANK.get(role, -1) < _ROLE_RANK[minimum]:
            actor = getattr(request.state, "user_id", "unknown")
            logger.warning(
                "Role check failed: %s < %s (path=%s, method=%s, actor=%s)",
                role,
                minimum,
                request.url.path,
                request.method,
                actor,
            )
            from shoreguard.services.audit import audit_log

            await audit_log(
                request,
                "auth.forbidden",
                "role",
                minimum,
                detail={"actor_role": role, "required_role": minimum},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {minimum} role",
            )

    return _dependency


async def require_auth_ws(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    sg_session: str | None = Cookie(default=None),
) -> None:
    """FastAPI dependency for WebSocket auth.

    Accepts SP key via ``?token=`` or session cookie.

    Args:
        websocket: The WebSocket connection.
        token: Optional SP key from ``?token=`` query parameter.
        sg_session: Optional session cookie value.

    Raises:
        HTTPException: 403 if authentication fails.
    """
    if state.no_auth:
        return
    if not await is_setup_complete():
        return

    # 1. Query-param token → service principal
    if token:
        sp = await _lookup_sp_identity(token)
        if sp:
            logger.debug(
                "WebSocket auth via SP token (path=%s, role=%s)", websocket.url.path, sp["role"]
            )
            return

    # 2. Session cookie → user
    if sg_session:
        result = verify_session_token(sg_session)
        if result:
            user_id, _ = result
            if await _lookup_user(user_id) is not None:
                logger.debug("WebSocket auth via session cookie (path=%s)", websocket.url.path)
                return
            logger.warning("WebSocket session for inactive/deleted user_id=%d", user_id)

    client_ip = websocket.client.host if websocket.client else "unknown"
    logger.warning("WebSocket auth rejected (path=%s, client=%s)", websocket.url.path, client_ip)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="WebSocket authentication failed",
    )


def require_role_ws(minimum: str) -> Callable[..., Coroutine[Any, Any, None]]:
    """Return a WebSocket dependency enforcing a minimum global role.

    Mirrors :func:`require_auth_ws` identity resolution (SP token or session
    cookie) but additionally rejects callers whose global role is below
    *minimum*. Used for mutating WebSocket endpoints such as interactive exec
    and TCP forwarding. Gateway-scoped overrides are not applied here — the
    WebSocket path carries the gateway in the URL but the role check is global,
    matching the conservative default for these data-plane channels.

    Args:
        minimum: Minimum required role (``admin``, ``operator``, ``viewer``).

    Returns:
        Callable[..., Coroutine[Any, Any, None]]: An async FastAPI WebSocket
            dependency callable.
    """

    async def _dep(
        websocket: WebSocket,
        token: str | None = Query(default=None),
        sg_session: str | None = Cookie(default=None),
    ) -> None:
        """Authenticate the WebSocket and enforce the minimum role.

        Args:
            websocket: The WebSocket connection.
            token: Optional SP key from ``?token=``.
            sg_session: Optional session cookie value.

        Raises:
            HTTPException: 403 if unauthenticated or the role is insufficient.
        """
        if state.no_auth or not await is_setup_complete():
            return
        role: str | None = None
        if token:
            sp = await _lookup_sp_identity(token)
            if sp:
                role = sp["role"]
        if role is None and sg_session:
            result = verify_session_token(sg_session)
            if result:
                user_id, sess_role = result
                if await _lookup_user(user_id) is not None:
                    role = sess_role
        if role is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="WebSocket authentication failed",
            )
        if _ROLE_RANK.get(role, -1) < _ROLE_RANK[minimum]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"WebSocket requires {minimum} role",
            )

    return _dep
