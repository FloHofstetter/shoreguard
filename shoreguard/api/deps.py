"""Shared dependencies for API routes.

Uses a ContextVar to resolve the gateway name per request,
so route handlers don't need to accept a gateway parameter.
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


def resolve_gateway(gw: str) -> None:
    """FastAPI dependency — set the gateway context for this request.

    Args:
        gw: The gateway name from the URL path.

    Raises:
        HTTPException: If the gateway name does not match the allowed pattern.
    """
    if not _VALID_GW_RE.match(gw):
        raise HTTPException(400, "Invalid gateway name: must match [a-zA-Z0-9][a-zA-Z0-9._-]*")
    logger.debug("Resolved gateway context: '%s'", gw)
    _current_gateway.set(gw)


def get_client() -> ShoreGuardClient:
    """Return a client for the current gateway (from ContextVar or active config).

    Returns:
        ShoreGuardClient: The client bound to the current gateway context.
    """
    gw = _current_gateway.get()
    return _get_gateway_service().get_client(name=gw)


def set_client(client: ShoreGuardClient | None) -> None:
    """Set or clear a client for the current gateway.

    Args:
        client: The client instance to set, or ``None`` to clear.
    """
    gw = _current_gateway.get()
    _get_gateway_service().set_client(client, name=gw)


def reset_backoff() -> None:
    """Reset the connection backoff for the current gateway."""
    gw = _current_gateway.get()
    _get_gateway_service().reset_backoff(name=gw)
