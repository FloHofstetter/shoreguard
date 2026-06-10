"""Service routing and sandbox token schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

# ─── Service routing ──────────────────────────────────────────────────────────


class ExposeServiceRequest(BaseModel):
    """Body for exposing a sandbox loopback port as a routed service.

    Attributes:
        sandbox (str): Sandbox name owning the service.
        service (str): Service name within the sandbox.
        target_port (int): Loopback TCP port inside the sandbox to publish.
        domain (bool): Whether to enable the browser-facing service URL.
    """

    sandbox: str = Field(min_length=1, max_length=253)
    service: str = Field(min_length=1, max_length=253)
    target_port: int = Field(ge=1, le=65535)
    domain: bool = False


class ServiceEndpointResponse(BaseModel):
    """A routed sandbox service endpoint.

    Attributes:
        id (str): Endpoint object id.
        created_at_ms (int): Creation timestamp, milliseconds since the epoch.
        sandbox_id (str): Sandbox object id.
        sandbox_name (str): Sandbox name.
        service_name (str): Service name within the sandbox.
        target_port (int): Loopback TCP port inside the sandbox.
        domain (bool): Whether browser-facing routing is enabled.
        url (str): Browser-facing service URL (empty when ``domain`` is false).
    """

    id: str
    created_at_ms: int
    sandbox_id: str
    sandbox_name: str
    service_name: str
    target_port: int
    domain: bool
    url: str


class ServiceEndpointListResponse(BaseModel):
    """List of routed sandbox service endpoints.

    Attributes:
        services (list[ServiceEndpointResponse]): Exposed service endpoints.
    """

    services: list[ServiceEndpointResponse]


class ServiceDeleteResponse(BaseModel):
    """Confirmation for deleting a service endpoint.

    Attributes:
        deleted (bool): Whether an endpoint existed and was deleted.
    """

    deleted: bool


# ─── Gateway sandbox tokens (diagnostic) ──────────────────────────────────────


class SandboxTokenResponse(BaseModel):
    """A gateway-minted JWT bound to the caller's mTLS identity.

    Attributes:
        token (str): The minted JWT.
        expires_at_ms (int): Absolute expiry, milliseconds since the epoch
            (0 means the token is non-expiring).
    """

    token: str
    expires_at_ms: int
