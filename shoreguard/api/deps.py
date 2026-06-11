"""Shared dependencies for API routes.

The ``resolve_gateway`` dependency validates the ``{gw}`` path segment
and stores a typed :class:`GatewayContext` on ``request.state`` — the
single way gateway-scoped routes learn which gateway a request targets.

.. note::

   Starlette ≥ 1.0 runs each ``Depends()`` callable in its own
   ``contextvars.copy_context()``, so a ``ContextVar`` set in one
   dependency is invisible to siblings.  ``request.state`` is the
   supported way to share per-request data. WebSocket handlers do not
   go through this dependency at all — they receive the gateway name
   as an explicit path parameter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

from shoreguard.client import ShoreGuardClient
from shoreguard.config import VALID_GATEWAY_NAME_RE

if TYPE_CHECKING:
    from shoreguard.container import ServiceContainer
    from shoreguard.services.gateway import GatewayService

logger = logging.getLogger(__name__)

_VALID_GW_RE = VALID_GATEWAY_NAME_RE


@dataclass(frozen=True)
class GatewayContext:
    """Per-request gateway scope, set by :func:`resolve_gateway`.

    Attributes:
        name: Validated gateway name from the URL path.
    """

    name: str


def get_services() -> ServiceContainer:
    """Return the installed service container for use in route handlers.

    Raises:
        HTTPException: If no container is installed (lifespan not started).

    Returns:
        ServiceContainer: The process-wide service container.
    """
    from shoreguard.container import try_get_container

    container = try_get_container()
    if container is None:
        raise HTTPException(503, "Services not initialised — app lifespan has not started")
    return container


def _get_gateway_service() -> GatewayService:
    """Return the gateway service from the container.

    Returns:
        GatewayService: The active gateway service instance.
    """
    return get_services().gateway


def get_actor(request: Request) -> str:
    """Extract the acting user identity from the request state.

    Args:
        request: The incoming HTTP request.

    Returns:
        str: The user identity string, or ``"unknown"`` if not set.
    """
    return getattr(request.state, "user_id", "unknown")


def resolve_gateway(gw: str, request: Request) -> None:
    """FastAPI dependency — set the gateway context for this request.

    Stores a :class:`GatewayContext` on ``request.state`` so downstream
    dependencies and route handlers can retrieve it.

    Args:
        gw: The gateway name from the URL path.
        request: The incoming HTTP request.

    Raises:
        HTTPException: If the gateway name does not match the allowed pattern.
    """
    if not _VALID_GW_RE.match(gw):
        raise HTTPException(400, "Invalid gateway name: must match [a-zA-Z0-9][a-zA-Z0-9._-]*")
    logger.debug("Resolved gateway context: '%s'", gw)
    request.state.gateway = GatewayContext(name=gw)


def get_gateway_context(request: Request) -> GatewayContext:
    """Return the gateway context set by :func:`resolve_gateway`.

    Args:
        request: The incoming HTTP request.

    Returns:
        GatewayContext: The per-request gateway scope.

    Raises:
        HTTPException: If no gateway context has been set.
    """
    ctx = getattr(request.state, "gateway", None)
    if ctx is None:
        raise HTTPException(500, "No gateway context — resolve_gateway dependency missing")
    return ctx


def get_gateway_name(request: Request) -> str:
    """Public helper to read the gateway name from request state.

    Intended for use in route handlers that already have ``request``.

    Args:
        request: The incoming HTTP request.

    Returns:
        str: The gateway name, or empty string if not set.
    """
    ctx = getattr(request.state, "gateway", None)
    return ctx.name if ctx is not None else ""


async def get_client(request: Request) -> ShoreGuardClient:
    """Return a client for the current gateway.

    Args:
        request: The incoming HTTP request.

    Returns:
        ShoreGuardClient: The client bound to the current gateway context.
    """
    return await _get_gateway_service().get_client(name=get_gateway_context(request).name)


def set_client(client: ShoreGuardClient | None, request: Request) -> None:
    """Set or clear a client for the current gateway.

    Args:
        client: The client instance to set, or ``None`` to clear.
        request: The incoming HTTP request.
    """
    _get_gateway_service().set_client(client, name=get_gateway_context(request).name)


def reset_backoff(request: Request) -> None:
    """Reset the connection backoff for the current gateway.

    Args:
        request: The incoming HTTP request.
    """
    _get_gateway_service().reset_backoff(name=get_gateway_context(request).name)
