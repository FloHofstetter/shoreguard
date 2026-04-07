"""Pydantic response models for OpenAPI documentation.

These models formalise the response shapes returned by the API. They
are used as ``response_model`` parameters on route decorators so that
the auto-generated OpenAPI schema includes typed response definitions.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

# ─── Generic / shared ─────────────────────────────────────────────────────────


class ErrorResponse(BaseModel):
    """Standard error response returned by all error handlers."""

    detail: str
    code: str | None = None
    request_id: str | None = None


class StatusResponse(BaseModel):
    """Simple boolean-status response (delete, revoke, etc.)."""

    model_config = ConfigDict(extra="allow")


class MessageResponse(BaseModel):
    """Response with a status message string."""

    status: str


class PaginatedResponse(BaseModel):
    """Generic paginated list envelope for consistent API responses."""

    model_config = ConfigDict(extra="allow")

    items: list[Any]
    total: int | None = None


class LROAcceptedResponse(BaseModel):
    """202 Accepted — long-running operation created."""

    operation_id: str
    status: str
    resource_type: str


# ─── Health ───────────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    """Liveness probe response."""

    status: str


class ReadinessCheck(BaseModel):
    """Individual readiness check results."""

    model_config = ConfigDict(extra="allow")

    database: str
    gateway_service: str


class ReadinessResponse(BaseModel):
    """Readiness probe response with dependency checks."""

    status: str
    checks: ReadinessCheck


# ─── Operations ───────────────────────────────────────────────────────────────


class OperationResponse(BaseModel):
    """Single operation record."""

    model_config = ConfigDict(extra="allow")

    id: str
    status: str
    resource_type: str
    progress: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    progress_message: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None
    completed_at: str | None = None
    gateway_name: str | None = None


class OperationListResponse(BaseModel):
    """Paginated operation list."""

    operations: list[OperationResponse]
    total: int


# ─── Audit ────────────────────────────────────────────────────────────────────


class AuditEntryResponse(BaseModel):
    """Single audit log entry."""

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    timestamp: str | None = None
    actor: str | None = None
    action: str | None = None
    resource_type: str | None = None
    resource_key: str | None = None
    gateway: str | None = None
    detail: dict[str, Any] | None = None


class AuditListResponse(BaseModel):
    """Paginated audit log response."""

    entries: list[AuditEntryResponse]
    total: int


# ─── Webhooks ─────────────────────────────────────────────────────────────────


class WebhookResponse(BaseModel):
    """Webhook subscription record."""

    model_config = ConfigDict(extra="allow")

    id: int
    url: str
    event_types: list[str] | Any = None
    is_active: bool = True
    channel_type: str = "generic"
    created_at: str | None = None
    created_by: str | None = None


class WebhookDeliveryResponse(BaseModel):
    """Webhook delivery attempt record."""

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    webhook_id: int | None = None
    event_type: str | None = None
    status_code: int | None = None
    success: bool | None = None
    created_at: str | None = None


# ─── Gateways ─────────────────────────────────────────────────────────────────


class GatewayResponse(BaseModel):
    """Gateway record (registration info + status)."""

    model_config = ConfigDict(extra="allow")

    name: str
    endpoint: str | None = None
    status: str | None = None
    last_seen: str | None = None
    description: str | None = None
    labels: dict[str, str] | None = None


class GatewayUnregisterResponse(BaseModel):
    """Gateway unregistration confirmation."""

    success: bool
    name: str


class ConnectionTestResponse(BaseModel):
    """Gateway connection test result."""

    model_config = ConfigDict(extra="allow")

    success: bool | None = None
    error: str | None = None
    latency_ms: float | None = None


# ─── Sandboxes ────────────────────────────────────────────────────────────────


class SandboxResponse(BaseModel):
    """Sandbox record (CRUD + metadata)."""

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    status: str | None = None
    image: str | None = None
    gpu: bool | None = None
    description: str | None = None
    labels: dict[str, str] | None = None


class SandboxDeleteResponse(BaseModel):
    """Sandbox deletion confirmation."""

    deleted: bool


class SshSessionResponse(BaseModel):
    """SSH session details."""

    model_config = ConfigDict(extra="allow")

    token: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None


class SshRevokeResponse(BaseModel):
    """SSH session revocation confirmation."""

    revoked: bool


class ExecResultResponse(BaseModel):
    """Command execution result."""

    model_config = ConfigDict(extra="allow")

    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None


class LogEntryResponse(BaseModel):
    """Single sandbox log entry."""

    model_config = ConfigDict(extra="allow")


# ─── Providers ────────────────────────────────────────────────────────────────


class ProviderResponse(BaseModel):
    """Provider record."""

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    type: str | None = None


class ProviderDeleteResponse(BaseModel):
    """Provider deletion confirmation."""

    deleted: bool


class ProviderTypeResponse(BaseModel):
    """Provider type metadata."""

    model_config = ConfigDict(extra="allow")

    type: str | None = None
    label: str | None = None


# ─── Policies ─────────────────────────────────────────────────────────────────


class PolicyResponse(BaseModel):
    """Policy document (dynamic structure from gateway)."""

    model_config = ConfigDict(extra="allow")


class PolicyDiffResponse(BaseModel):
    """Diff between two policy revisions."""

    model_config = ConfigDict(extra="allow")


class PresetSummaryResponse(BaseModel):
    """Policy preset list entry."""

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    description: str | None = None


# ─── Approvals ────────────────────────────────────────────────────────────────


class ApprovalDraftResponse(BaseModel):
    """Draft policy with approval metadata."""

    model_config = ConfigDict(extra="allow")


class ApprovalChunkResponse(BaseModel):
    """Single approval chunk status."""

    model_config = ConfigDict(extra="allow")


class ApprovalBulkResponse(BaseModel):
    """Bulk approval result with counts."""

    model_config = ConfigDict(extra="allow")


class ApprovalClearResponse(BaseModel):
    """Approval clear result."""

    model_config = ConfigDict(extra="allow")

    cleared: int | None = None


# ─── Templates ────────────────────────────────────────────────────────────────


class TemplateSummaryResponse(BaseModel):
    """Sandbox template list entry."""

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    description: str | None = None


class TemplateDetailResponse(BaseModel):
    """Full sandbox template with configuration."""

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    description: str | None = None


# ─── Inference ────────────────────────────────────────────────────────────────


class InferenceConfigResponse(BaseModel):
    """Cluster inference configuration."""

    model_config = ConfigDict(extra="allow")
