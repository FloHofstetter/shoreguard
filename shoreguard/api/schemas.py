"""Pydantic response models for OpenAPI documentation.

These models formalise the response shapes returned by the API. They
are used as ``response_model`` parameters on route decorators so that
the auto-generated OpenAPI schema includes typed response definitions.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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


# ─── Operations ───────────────────────────────────────────────────────────────


class OperationResponse(BaseModel):
    """Single operation record.

    Attributes:
        model_config (ConfigDict): Pydantic config.
        id (str): Operation ID.
        status (str): Current operation status.
        resource_type (str): Type of resource the operation targets.
        progress (int | None): Progress percentage (0–100), if known.
        created_at (str | None): ISO timestamp when the operation was created.
        updated_at (str | None): ISO timestamp of the last update.
        progress_message (str | None): Human-readable progress message.
        result (dict[str, Any] | None): Result payload once the operation finishes.
        error (str | None): Error message if the operation failed.
        error_code (str | None): Machine-readable error code if the operation failed.
        completed_at (str | None): ISO timestamp when the operation completed.
        gateway_name (str | None): Name of the gateway that ran the operation.
    """

    model_config = ConfigDict(extra="forbid")

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
    """Paginated operation list.

    Attributes:
        operations (list[OperationResponse]): Page of operation records.
        total (int): Total number of operations matching the query.
    """

    operations: list[OperationResponse]
    total: int


# ─── Audit ────────────────────────────────────────────────────────────────────


class AuditEntryResponse(BaseModel):
    """Single audit log entry.

    Attributes:
        model_config (ConfigDict): Pydantic config.
        id (int | None): Audit entry ID.
        timestamp (str | None): ISO timestamp when the event was recorded.
        actor (str | None): Identifier of the actor who performed the action.
        actor_role (str | None): Role of the actor at the time of the action.
        action (str | None): Action name (e.g. ``sandbox.create``).
        resource_type (str | None): Type of resource the action targeted.
        resource_id (str | None): ID of the targeted resource.
        gateway (str | None): Name of the gateway involved, if any.
        detail (dict[str, Any] | None): Additional structured context.
        client_ip (str | None): Remote client IP address.
    """

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    timestamp: str | None = None
    actor: str | None = None
    actor_role: str | None = None
    action: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    gateway: str | None = None
    detail: dict[str, Any] | None = None
    client_ip: str | None = None


class AuditListResponse(BaseModel):
    """Paginated audit log response.

    Attributes:
        entries (list[AuditEntryResponse]): Page of audit entries.
        total (int): Total number of entries matching the query.
    """

    entries: list[AuditEntryResponse]
    total: int


# ─── Webhooks ─────────────────────────────────────────────────────────────────


class WebhookResponse(BaseModel):
    """Webhook subscription record (without secret).

    Attributes:
        model_config (ConfigDict): Pydantic config.
        id (int): Webhook ID.
        url (str): Target URL invoked on delivery.
        event_types (list[str] | Any): Event type filter for this subscription.
        is_active (bool): Whether the webhook is currently active.
        channel_type (str): Delivery channel type (e.g. ``generic``, ``slack``).
        created_at (str | None): ISO timestamp when the webhook was created.
        created_by (str | None): Identifier of the user who created the webhook.
        extra_config (dict[str, Any] | None): Channel-specific extra configuration.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    url: str
    event_types: list[str] | Any = None
    is_active: bool = True
    channel_type: str = "generic"
    created_at: str | None = None
    created_by: str | None = None
    extra_config: dict[str, Any] | None = None


class WebhookCreateResponse(WebhookResponse):
    """Webhook creation response — includes the HMAC secret (shown only once).

    Attributes:
        secret (str): HMAC signing secret — returned only at creation time.
    """

    secret: str


class WebhookDeliveryResponse(BaseModel):
    """Webhook delivery attempt record.

    Attributes:
        model_config (ConfigDict): Pydantic config.
        id (int | None): Delivery attempt ID.
        webhook_id (int | None): ID of the webhook that was delivered.
        event_type (str | None): Event type of the delivered payload.
        status (str | None): Delivery status (e.g. ``success``, ``failed``).
        response_code (int | None): HTTP response code from the target.
        error_message (str | None): Error message if delivery failed.
        attempt (int | None): Attempt number (starting at 1).
        created_at (str | None): ISO timestamp when the attempt was created.
        delivered_at (str | None): ISO timestamp when the delivery completed.
    """

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    webhook_id: int | None = None
    event_type: str | None = None
    status: str | None = None
    response_code: int | None = None
    error_message: str | None = None
    attempt: int | None = None
    created_at: str | None = None
    delivered_at: str | None = None


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


# ─── Sandboxes ────────────────────────────────────────────────────────────────


class SandboxResponse(BaseModel):
    """Sandbox record (CRUD + metadata).

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        name (str | None): Sandbox name.
        status (str | None): Current sandbox status.
        image (str | None): Container image backing the sandbox.
        gpu (bool | None): Whether the sandbox has GPU access.
        description (str | None): Human-readable sandbox description.
        labels (dict[str, str] | None): Label key/value pairs.
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version

    name: str | None = None
    status: str | None = None
    image: str | None = None
    gpu: bool | None = None
    description: str | None = None
    labels: dict[str, str] | None = None


class SandboxDeleteResponse(BaseModel):
    """Sandbox deletion confirmation.

    Attributes:
        deleted (bool): Whether the sandbox was deleted.
    """

    deleted: bool


class SshSessionResponse(BaseModel):
    """SSH session details.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        token (str | None): Session token used to authenticate the SSH connection.
        host (str | None): SSH host to connect to.
        port (int | None): SSH port to connect to.
        username (str | None): SSH username to use.
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version

    token: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None


class SshRevokeResponse(BaseModel):
    """SSH session revocation confirmation.

    Attributes:
        revoked (bool): Whether the SSH session was revoked.
    """

    revoked: bool


class ExecResultResponse(BaseModel):
    """Command execution result.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        exit_code (int | None): Process exit code.
        stdout (str | None): Captured standard output.
        stderr (str | None): Captured standard error.
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version

    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None


class LogEntryResponse(BaseModel):
    """Single sandbox log entry.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version


# ─── Providers ────────────────────────────────────────────────────────────────


class ProviderResponse(BaseModel):
    """Provider record.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        name (str | None): Provider name.
        type (str | None): Provider type identifier.
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version

    name: str | None = None
    type: str | None = None


class ProviderDeleteResponse(BaseModel):
    """Provider deletion confirmation.

    Attributes:
        deleted (bool): Whether the provider was deleted.
    """

    deleted: bool


class ProviderTypeResponse(BaseModel):
    """Provider type metadata.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        type (str | None): Provider type identifier.
        label (str | None): Human-readable label for the provider type.
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version

    type: str | None = None
    label: str | None = None


class ProviderEnvVar(BaseModel):
    """A single environment variable projected into sandboxes by a provider.

    Secret values are never returned — only the key, its source, and a
    redacted placeholder. Use this endpoint to debug agent misconfiguration
    without exposing credentials.

    Attributes:
        key (str): Environment variable name (e.g. ``ANTHROPIC_API_KEY``).
        source (str): Origin of the value: ``credential`` (from
            ``provider.credentials``), ``config`` (from ``provider.config``),
            or ``type_default`` (implied by the provider type's cred_key
            mapping in ``openshell.yaml`` when no matching credential exists).
        redacted_value (str): Constant placeholder (``[REDACTED]``) so callers
            can distinguish "key is set" from "key is absent" without seeing
            the real value.
    """

    key: str
    source: str
    redacted_value: str = "[REDACTED]"


class ProviderEnvResponse(BaseModel):
    """Environment-variable projection for a provider.

    Attributes:
        provider (str): Provider name.
        type (str | None): Provider type identifier.
        env (list[ProviderEnvVar]): Environment variables the provider
            projects into sandboxes. Values are redacted.
    """

    provider: str
    type: str | None = None
    env: list[ProviderEnvVar]


# ─── Policies ─────────────────────────────────────────────────────────────────


class PolicyResponse(BaseModel):
    """Policy document (dynamic structure from gateway).

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version


class PolicyDiffResponse(BaseModel):
    """Diff between two policy revisions.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version


class PolicyExportResponse(BaseModel):
    """Response body for GET /sandboxes/{name}/policy/export.

    Attributes:
        yaml: YAML document with metadata + policy blocks.
        gateway: Gateway name (mirrors metadata.gateway).
        sandbox: Sandbox name.
        version: Active policy version (may be 0 if no revision exists).
        policy_hash: OpenShell-computed policy hash (etag for optimistic locking).
    """

    yaml: str
    gateway: str
    sandbox: str
    version: int
    policy_hash: str


class PolicyApplyRequest(BaseModel):
    """Request body for POST /sandboxes/{name}/policy/apply.

    Attributes:
        yaml: YAML document body (with optional metadata block).
        dry_run: When true, compute diff without writing.
        expected_version: Optional optimistic-lock etag (overrides metadata).
    """

    yaml: str
    dry_run: bool = False
    expected_version: str | None = None


class PolicyApplyResponse(BaseModel):
    """Response body for POST /sandboxes/{name}/policy/apply.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        status (str): One of ``up_to_date``, ``dry_run``, ``applied``,
            ``vote_recorded``, ``rejected``.
        current_hash (str): Server policy hash before this call.
        diff (dict): Structured policy diff.
        applied_version (str | None): Hash after the apply (only on ``applied``).
        votes_needed (int | None): Required approval count if under workflow.
        votes_cast (int | None): Approve votes already recorded if under workflow.
    """

    model_config = ConfigDict(extra="allow")

    status: str
    current_hash: str
    diff: dict
    applied_version: str | None = None
    votes_needed: int | None = None
    votes_cast: int | None = None


class PolicyAnalysisRequest(BaseModel):
    """Request body for POST /sandboxes/{name}/policy/analysis.

    Pass-through envelope for the OpenShell ``SubmitPolicyAnalysis`` RPC.
    The two list fields are dicts shaped like the upstream
    ``DenialSummary`` and ``PolicyChunk`` proto messages; ShoreGuard
    does not duplicate the proto schemas in Pydantic because the field
    set is large (33 fields combined) and will drift with OpenShell
    releases. Unknown keys in the dicts surface as ``TypeError`` from
    the proto constructor at the client layer.

    Attributes:
        model_config (ConfigDict): Pydantic config (unknown top-level
            fields rejected).
        summaries (list[dict[str, Any]]): ``DenialSummary`` dicts.
        proposed_chunks (list[dict[str, Any]]): ``PolicyChunk`` dicts —
            the rules that would fix the denials in *summaries*.
        analysis_mode (str): Opaque mode tag forwarded verbatim to the
            gateway (e.g. ``"auto"``, ``"manual"``).
    """

    model_config = ConfigDict(extra="forbid")

    summaries: list[dict[str, Any]] = Field(default_factory=list)
    proposed_chunks: list[dict[str, Any]] = Field(default_factory=list)
    analysis_mode: str = ""


class PolicyAnalysisResponse(BaseModel):
    """Response body for POST /sandboxes/{name}/policy/analysis.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed
            to accommodate upstream proto drift).
        accepted_chunks (int): Number of proposed chunks merged into the
            draft policy.
        rejected_chunks (int): Number of proposed chunks the gateway
            rejected.
        rejection_reasons (list[str]): Per-rejection reason strings from
            the gateway, aligned by index with the rejected subset.
    """

    model_config = ConfigDict(extra="allow")

    accepted_chunks: int
    rejected_chunks: int
    rejection_reasons: list[str] = Field(default_factory=list)


class PresetSummaryResponse(BaseModel):
    """Policy preset list entry.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        name (str | None): Preset name.
        description (str | None): Human-readable preset description.
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version

    name: str | None = None
    description: str | None = None


# ─── Approvals ────────────────────────────────────────────────────────────────


class ApprovalDraftResponse(BaseModel):
    """Draft policy with approval metadata.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version


class ApprovalChunkResponse(BaseModel):
    """Single approval chunk status.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version


class ApprovalBulkResponse(BaseModel):
    """Bulk approval result with counts.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version


class ApprovalClearResponse(BaseModel):
    """Approval clear result.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        cleared (int | None): Number of approvals cleared.
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version

    cleared: int | None = None


class ApprovalWorkflowConfig(BaseModel):
    """Configuration for a multi-stage approval workflow.

    Attributes:
        required_approvals (int): Number of distinct approve votes needed.
        required_roles (list[str]): Roles eligible to vote (empty = any).
        distinct_actors (bool): If true, the same actor cannot vote twice.
        escalation_timeout_minutes (int | None): Escalation timeout in
            minutes; ``None`` disables escalation.
    """

    required_approvals: int = Field(ge=1, le=20, default=2)
    required_roles: list[str] = Field(default_factory=list)
    distinct_actors: bool = True
    escalation_timeout_minutes: int | None = Field(default=None, ge=1, le=10080)


class ApprovalWorkflowResponse(BaseModel):
    """Stored workflow configuration returned to clients.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
    """

    model_config = ConfigDict(extra="allow")


class ApprovalDecisionEntry(BaseModel):
    """A single recorded vote on an approval chunk.

    Attributes:
        actor (str): Voting user identity.
        role (str): Role at vote time.
        decision (str): ``approve`` or ``reject``.
        comment (str | None): Optional comment.
        created_at (str): ISO-8601 vote timestamp.
    """

    actor: str
    role: str
    decision: str
    comment: str | None = None
    created_at: str


class ApprovalVoteResponse(BaseModel):
    """Response body when a vote is recorded but quorum is not yet met.

    Returned with HTTP 202 from ``/approve`` or ``/reject`` under an active
    workflow. For the approved-path response (quorum met), the normal
    ``ApprovalChunkResponse`` shape is returned instead.

    Attributes:
        status (str): ``pending``, ``approved``, or ``rejected``.
        votes (int): Approve votes recorded so far.
        needed (int): Quorum threshold.
        decisions (list[ApprovalDecisionEntry]): Current decision rows.
    """

    status: str
    votes: int
    needed: int
    decisions: list[ApprovalDecisionEntry] = Field(default_factory=list)


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


# ─── Auth ─────────────────────────────────────────────────────────────────────


class AuthCheckResponse(BaseModel):
    """Authentication status response.

    Attributes:
        authenticated (bool): Whether the caller is authenticated.
        auth_enabled (bool): Whether authentication is enabled on the server.
        role (str | None): Role of the authenticated caller, if any.
        email (str | None): Email of the authenticated caller, if any.
        needs_setup (bool): Whether initial admin setup is still required.
        registration_enabled (bool): Whether self-registration is permitted.
        local_mode (bool | None): Whether the server runs in local (single-user) mode.
        oidc_providers (list[dict[str, str]] | None): Public OIDC providers available for login.
    """

    authenticated: bool
    auth_enabled: bool
    role: str | None = None
    email: str | None = None
    needs_setup: bool
    registration_enabled: bool = False
    local_mode: bool | None = None
    oidc_providers: list[dict[str, str]] | None = None


class OidcProviderInfo(BaseModel):
    """Public OIDC provider info.

    Attributes:
        name (str): Provider identifier used in URLs.
        display_name (str): Human-readable provider name.
    """

    name: str
    display_name: str


class UserResponse(BaseModel):
    """User record (safe fields only).

    Attributes:
        id (int): User ID.
        email (str): User email address.
        role (str): Global role assigned to the user.
        is_active (bool): Whether the account is active.
        pending_invite (bool): Whether the user has a pending invite.
        created_at (str | None): ISO timestamp when the user was created.
        oidc_provider (str | None): Name of the OIDC provider, if federated.
    """

    id: int
    email: str
    role: str
    is_active: bool = True
    pending_invite: bool = False
    created_at: str | None = None
    oidc_provider: str | None = None


class UserCreateResponse(BaseModel):
    """User creation response — includes the invite token.

    Attributes:
        id (int): User ID.
        email (str): User email address.
        role (str): Global role assigned to the user.
        created_at (str | None): ISO timestamp when the user was created.
        invite_token (str | None): One-time invite token for account activation.
    """

    id: int
    email: str
    role: str
    created_at: str | None = None
    invite_token: str | None = None


class GatewayRoleResponse(BaseModel):
    """Per-gateway role override.

    Attributes:
        gateway_name (str): Name of the gateway the override applies to.
        role (str): Overridden role.
        user_id (int | None): User ID the override applies to, if any.
        sp_id (int | None): Service principal ID the override applies to, if any.
        group_id (int | None): Group ID the override applies to, if any.
    """

    gateway_name: str
    role: str
    user_id: int | None = None
    sp_id: int | None = None
    group_id: int | None = None


class ServicePrincipalResponse(BaseModel):
    """Service principal record (without key hash).

    Attributes:
        id (int): Service principal ID.
        name (str): Service principal name.
        role (str): Global role assigned to the service principal.
        key_prefix (str): Short prefix of the API key for identification.
        created_at (str | None): ISO timestamp when the principal was created.
        created_by (int | None): ID of the user who created the principal.
        last_used (str | None): ISO timestamp of the last successful auth.
        expires_at (str | None): ISO timestamp when the key expires, if any.
    """

    id: int
    name: str
    role: str
    key_prefix: str
    created_at: str | None = None
    created_by: int | None = None
    last_used: str | None = None
    expires_at: str | None = None


class ServicePrincipalCreateResponse(ServicePrincipalResponse):
    """Service principal creation/rotation response — includes the plaintext key.

    Attributes:
        key (str): Plaintext API key — returned only at creation/rotation time.
    """

    key: str


class GroupResponse(BaseModel):
    """User group record.

    Attributes:
        id (int): Group ID.
        name (str): Group name.
        role (str): Default role granted to members.
        description (str | None): Human-readable group description.
        created_at (str | None): ISO timestamp when the group was created.
        member_count (int | None): Number of members in the group.
    """

    id: int
    name: str
    role: str
    description: str | None = None
    created_at: str | None = None
    member_count: int | None = None


class GroupDetailResponse(GroupResponse):
    """Group with member list.

    Attributes:
        members (list[dict[str, Any]] | None): List of member records.
    """

    members: list[dict[str, Any]] | None = None


class GroupMemberResponse(BaseModel):
    """Group membership record.

    Attributes:
        group_id (int): ID of the group.
        group_name (str): Name of the group.
        user_id (int): ID of the member user.
        user_email (str): Email of the member user.
    """

    group_id: int
    group_name: str
    user_id: int
    user_email: str


# ─── Policy Pinning ──────────────────────────────────────────────────────────


class PolicyPinRequest(BaseModel):
    """Body for pinning a sandbox's policy.

    Attributes:
        reason (str | None): Optional human-readable reason for pinning.
        expires_at (str | None): Optional ISO 8601 expiry timestamp.
    """

    reason: str | None = None
    expires_at: str | None = None


class PolicyPinResponse(BaseModel):
    """Response for a policy pin.

    Attributes:
        gateway_name (str): Gateway the sandbox belongs to.
        sandbox_name (str): Name of the pinned sandbox.
        pinned_version (int): Policy version that is locked.
        pinned_by (str): Actor who set the pin.
        reason (str | None): Optional reason for pinning.
        pinned_at (str): ISO 8601 timestamp when pinned.
        expires_at (str | None): Optional ISO 8601 expiry timestamp.
    """

    gateway_name: str
    sandbox_name: str
    pinned_version: int
    pinned_by: str
    reason: str | None = None
    pinned_at: str
    expires_at: str | None = None
