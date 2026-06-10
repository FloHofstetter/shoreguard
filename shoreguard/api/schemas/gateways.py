"""Gateway registration and connection schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

# ─── Gateways ─────────────────────────────────────────────────────────────────


class GatewayResponse(BaseModel):
    """Gateway record (registration info + status).

    Attributes:
        model_config (ConfigDict): Pydantic config.
        name (str): Gateway name (unique identifier).
        endpoint (str | None): Gateway endpoint URL.
        scheme (str | None): Connection scheme (e.g. ``https``, ``grpc``).
        auth_mode (str | None): Authentication mode used to reach the gateway.
        has_ca_cert (bool | None): Whether a CA certificate is configured.
        has_client_cert (bool | None): Whether a client certificate is configured.
        has_client_key (bool | None): Whether a client key is configured.
        metadata (dict[str, Any] | None): Arbitrary gateway metadata.
        status (str | None): Current gateway status.
        last_status (str | None): Previous known status.
        connected (bool | None): Whether the gateway is currently connected.
        description (str | None): Human-readable gateway description.
        labels (dict[str, str] | None): Label key/value pairs for filtering.
        registered_at (str | None): ISO timestamp of initial registration.
        last_seen (str | None): ISO timestamp of the last successful contact.
        configured (bool | None): Whether the gateway is registered (only set by ``get_info``).
        version (str | None): Upstream openshell version reported by the live gateway when
            reachable.
        runtime (str | None): Gateway runtime tag (``docker``, ``kubernetes``, ``libkrun``)
            derived from ``metadata.runtime``. ``None`` when the tag is absent.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    endpoint: str | None = None
    scheme: str | None = None
    auth_mode: str | None = None
    has_ca_cert: bool | None = None
    has_client_cert: bool | None = None
    has_client_key: bool | None = None
    metadata: dict[str, Any] | None = None
    status: str | None = None
    last_status: str | None = None
    connected: bool | None = None
    description: str | None = None
    labels: dict[str, str] | None = None
    registered_at: str | None = None
    last_seen: str | None = None
    configured: bool | None = None
    version: str | None = None
    runtime: str | None = None


class GatewayUnregisterResponse(BaseModel):
    """Gateway unregistration confirmation.

    Attributes:
        success (bool): Whether unregistration succeeded.
        name (str): Name of the unregistered gateway.
    """

    success: bool
    name: str


class ConnectionTestResponse(BaseModel):
    """Gateway connection test result.

    Attributes:
        model_config (ConfigDict): Pydantic config.
        success (bool | None): Whether the test completed without error.
        connected (bool | None): Whether a connection was established.
        version (str | None): Remote gateway version string.
        health_status (str | None): Reported gateway health status.
        error (str | None): Error message if the test failed.
        latency_ms (float | None): Measured round-trip latency in milliseconds.
    """

    model_config = ConfigDict(extra="forbid")

    success: bool | None = None
    connected: bool | None = None
    version: str | None = None
    health_status: str | None = None
    error: str | None = None
    latency_ms: float | None = None
