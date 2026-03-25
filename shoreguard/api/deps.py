"""Shared dependencies for API routes.

Uses a ContextVar to resolve the gateway name per request,
so route handlers don't need to accept a gateway parameter.
"""

from __future__ import annotations

from contextvars import ContextVar

from shoreguard.client import ShoreGuardClient
from shoreguard.services.gateway import gateway_service

_current_gateway: ContextVar[str | None] = ContextVar("_current_gateway", default=None)


def resolve_gateway(gw: str) -> None:
    """FastAPI dependency — set the gateway context for this request."""
    _current_gateway.set(gw)


def get_client() -> ShoreGuardClient:
    """Return a client for the current gateway (from ContextVar or active config)."""
    gw = _current_gateway.get()
    return gateway_service.get_client(name=gw)


def set_client(client: ShoreGuardClient | None) -> None:
    """Set or clear a client for the current gateway."""
    gw = _current_gateway.get()
    gateway_service.set_client(client, name=gw)


def reset_backoff() -> None:
    """Reset the connection backoff for the current gateway."""
    gw = _current_gateway.get()
    gateway_service.reset_backoff(name=gw)
