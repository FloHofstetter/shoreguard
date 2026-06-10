"""Sandbox template schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

# ─── Templates ────────────────────────────────────────────────────────────────


class TemplateSummaryResponse(BaseModel):
    """Sandbox template list entry.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        name (str | None): Template name.
        description (str | None): Human-readable template description.
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version

    name: str | None = None
    description: str | None = None


class TemplateDetailResponse(BaseModel):
    """Full sandbox template with configuration.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        name (str | None): Template name.
        description (str | None): Human-readable template description.
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version

    name: str | None = None
    description: str | None = None
