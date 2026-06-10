"""Cluster inference configuration schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

# ─── Inference ────────────────────────────────────────────────────────────────


class InferenceConfigResponse(BaseModel):
    """Cluster inference configuration.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version


class InferenceBundleRoute(BaseModel):
    """One resolved route in the gateway's inference bundle.

    The ``api_key`` field from the proto is intentionally absent — only
    ``has_api_key`` (bool) is exposed so the secret never crosses the API
    boundary.

    Attributes:
        name (str): Route identifier.
        base_url (str): Provider endpoint base URL.
        protocols (list[str]): Wire protocols (e.g. ``["https"]``).
        model_id (str): Model identifier resolved for this route.
        provider_type (str): Provider type tag (anthropic, openai, …).
        timeout_secs (int): Per-request timeout in seconds.
        has_api_key (bool): True if the gateway has a credential for this
            route. The credential value itself is never exposed.
    """

    name: str
    base_url: str
    protocols: list[str] = []
    model_id: str = ""
    provider_type: str = ""
    timeout_secs: int = 0
    has_api_key: bool = False


class InferenceBundleResponse(BaseModel):
    """Resolved inference bundle (routes after policy overlay).

    Attributes:
        revision: Bundle revision tag from the gateway.
        generated_at_ms: When the gateway generated the bundle (epoch ms).
        routes: Resolved routes with redacted credentials.
    """

    revision: str = ""
    generated_at_ms: int = 0
    routes: list[InferenceBundleRoute] = []
