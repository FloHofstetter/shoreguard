"""Shared envelopes: errors, status, pagination, LRO, health probes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

# ─── Generic / shared ─────────────────────────────────────────────────────────


class ErrorResponse(BaseModel):
    """Standard error response returned by all error handlers.

    Attributes:
        detail (str): Human-readable error message.
        code (str | None): Optional machine-readable error code.
        request_id (str | None): Correlation ID for tracing the failing request.
    """

    detail: str
    code: str | None = None
    request_id: str | None = None


class StatusResponse(BaseModel):
    """Simple boolean-status response (delete, revoke, etc.).

    Attributes:
        model_config (ConfigDict): Pydantic config.
        ok (bool | None): Whether the operation succeeded.
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool | None = None


class MessageResponse(BaseModel):
    """Response with a status message string.

    Attributes:
        status (str): Status message text.
    """

    status: str


class OkResponse(BaseModel):
    """Simple ok confirmation response.

    Attributes:
        ok (bool): Always ``True`` when the operation succeeded.
    """

    ok: bool = True


class PaginatedResponse(BaseModel):
    """Generic paginated list envelope for consistent API responses.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        items (list[Any]): Page of items — structure varies by service.
        total (int | None): Total number of items across all pages, if known.
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": items contain dynamic structures from various services

    items: list[Any]
    total: int | None = None


class LROAcceptedResponse(BaseModel):
    """202 Accepted — long-running operation created.

    Attributes:
        operation_id (str): Unique ID of the created LRO.
        status (str): Initial operation status.
        resource_type (str): Type of resource the operation targets.
    """

    operation_id: str
    status: str
    resource_type: str


# ─── Health ───────────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    """Liveness probe response.

    Attributes:
        status (str): Liveness status string.
    """

    status: str


class VersionResponse(BaseModel):
    """Build identity — lets operators verify which artifact is running.

    Attributes:
        version (str): Package version (from ``pyproject.toml``).
        git_sha (str): Git commit hash the image was built from, or
            ``"unknown"`` when built outside of CI.
        build_time (str): ISO-8601 UTC timestamp of the image build, or
            ``"unknown"`` when built outside of CI.
    """

    version: str
    git_sha: str
    build_time: str


class ReadinessCheck(BaseModel):
    """Individual readiness check results.

    Attributes:
        model_config (ConfigDict): Pydantic config.
        database (str): Database connectivity status.
        gateway_service (str): Gateway service connectivity status.
    """

    model_config = ConfigDict(extra="forbid")

    database: str
    gateway_service: str


class ReadinessResponse(BaseModel):
    """Readiness probe response with dependency checks.

    Attributes:
        status (str): Overall readiness status.
        checks (ReadinessCheck): Per-dependency readiness results.
    """

    status: str
    checks: ReadinessCheck
