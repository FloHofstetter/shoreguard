"""Shared dependencies for API routes.

Stores the gateway name on ``request.state`` so it is visible to all
downstream dependencies and route handlers within the same request.

.. note::

   Starlette ≥ 1.0 runs each ``Depends()`` callable in its own
   ``contextvars.copy_context()``, so a ``ContextVar`` set in one
   dependency is invisible to siblings.  ``request.state`` is the
   supported way to share per-request data.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

from shoreguard.client import ShoreGuardClient
from shoreguard.config import VALID_GATEWAY_NAME_RE

if TYPE_CHECKING:
    from shoreguard.services.gateway import GatewayService

logger = logging.getLogger(__name__)

# Kept for the WebSocket handler which has no Request object.
_current_gateway: ContextVar[str | None] = ContextVar("_current_gateway", default=None)

_VALID_GW_RE = VALID_GATEWAY_NAME_RE


def _get_gateway_service() -> GatewayService:
    """Return the global gateway service singleton.

    Raises:
        HTTPException: If the gateway service has not been initialised.

    Returns:
        GatewayService: The active gateway service instance.
    """
    from shoreguard.services.gateway import gateway_service

    if gateway_service is None:
        raise HTTPException(503, "GatewayService not initialised — app lifespan has not started")
    return gateway_service


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

    Stores the gateway name on ``request.state`` so downstream
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
    request.state._gateway = gw
    # Also set the ContextVar for WebSocket and background-task compat.
    _current_gateway.set(gw)


def _require_gateway_name(request: Request) -> str:
    """Return the current gateway name from request state.

    Args:
        request: The incoming HTTP request.

    Returns:
        str: The gateway name from the request context.

    Raises:
        HTTPException: If no gateway context has been set.
    """
    gw: str | None = getattr(request.state, "_gateway", None)
    if gw is None:
        # Fallback to ContextVar (WebSocket path).
        gw = _current_gateway.get()
    if gw is None:
        raise HTTPException(500, "No gateway context — resolve_gateway dependency missing")
    return gw


def get_gateway_name(request: Request) -> str:
    """Public helper to read the gateway name from request state.

    Intended for use in route handlers that already have ``request``.

    Args:
        request: The incoming HTTP request.

    Returns:
        str: The gateway name, or empty string if not set.
    """
    return getattr(request.state, "_gateway", "") or _current_gateway.get() or ""


def get_client(request: Request) -> ShoreGuardClient:
    """Return a client for the current gateway.

    Args:
        request: The incoming HTTP request.

    Returns:
        ShoreGuardClient: The client bound to the current gateway context.
    """
    return _get_gateway_service().get_client(name=_require_gateway_name(request))


def set_client(client: ShoreGuardClient | None, request: Request) -> None:
    """Set or clear a client for the current gateway.

    Args:
        client: The client instance to set, or ``None`` to clear.
        request: The incoming HTTP request.
    """
    _get_gateway_service().set_client(client, name=_require_gateway_name(request))


def reset_backoff(request: Request) -> None:
    """Reset the connection backoff for the current gateway.

    Args:
        request: The incoming HTTP request.
    """
    _get_gateway_service().reset_backoff(name=_require_gateway_name(request))
