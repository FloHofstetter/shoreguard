"""SQLAlchemy ORM models for Shoreguard."""

from __future__ import annotations

import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all Shoreguard models."""


class Gateway(Base):
    """A registered OpenShell gateway.

    Attributes:
        id: Auto-incremented primary key.
        name: Unique gateway name (max 253 chars).
        endpoint: Host:port of the gateway gRPC endpoint.
        scheme: Connection scheme (``https`` by default).
        auth_mode: Optional authentication mode identifier.
        ca_cert: Optional CA certificate bytes for mTLS.
        client_cert: Optional client certificate bytes for mTLS.
        client_key: Optional client private key bytes for mTLS.
        metadata_json: Optional JSON-encoded metadata blob.
        description: Optional free-text description of the gateway's purpose.
        labels_json: Optional JSON-encoded key-value labels for filtering.
        registered_at: Timestamp when the gateway was registered.
        last_seen: Timestamp of the most recent health check.
        last_status: Last known health status string.
    """

    __tablename__ = "gateways"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(253), unique=True, nullable=False)
    endpoint: Mapped[str] = mapped_column(String(260), nullable=False)
    scheme: Mapped[str] = mapped_column(String, nullable=False, default="https")
    auth_mode: Mapped[str | None] = mapped_column(String)
    ca_cert: Mapped[bytes | None] = mapped_column(LargeBinary)
    client_cert: Mapped[bytes | None] = mapped_column(LargeBinary)
    client_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    metadata_json: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    labels_json: Mapped[str | None] = mapped_column(Text)
    registered_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_seen: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str] = mapped_column(String, default="unknown")


class User(Base):
    """A user account with email/password authentication.

    Attributes:
        id: Auto-incremented primary key.
        email: Unique email address (max 254 chars).
        hashed_password: Bcrypt-hashed password, or ``None`` for invite-only.
        role: Global role (``admin``, ``operator``, ``viewer``).
        is_active: Whether the account is enabled.
        invite_token_hash: SHA-256 hash of the invite token, if pending.
        created_at: Timestamp when the user was created.
        oidc_provider: Name of the OIDC provider, or ``None`` for local accounts.
        oidc_sub: OIDC subject identifier, or ``None`` for local accounts.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    invite_token_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    oidc_provider: Mapped[str | None] = mapped_column(String(100))
    oidc_sub: Mapped[str | None] = mapped_column(String(255))


class ServicePrincipal(Base):
    """A service principal (API key) for programmatic access.

    Attributes:
        id: Auto-incremented primary key.
        name: Unique human-readable name (max 100 chars).
        key_hash: SHA-256 hash of the API key.
        key_prefix: First 12 characters of the key for identification.
        role: Global role (``admin``, ``operator``, ``viewer``).
        created_by: FK to the user who created this principal, or ``None``.
        created_at: Timestamp when the principal was created.
        last_used: Timestamp of the most recent API call, or ``None``.
        expires_at: Optional expiry timestamp; ``None`` means never expires.
    """

    __tablename__ = "service_principals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    key_prefix: Mapped[str | None] = mapped_column(String(12))
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))


class UserGatewayRole(Base):
    """A per-gateway role override for a user.

    Attributes:
        id: Auto-incremented primary key.
        user_id: FK to the user.
        gateway_id: FK to the gateway.
        role: Scoped role for this user on this gateway.
    """

    __tablename__ = "user_gateway_roles"
    __table_args__ = (UniqueConstraint("user_id", "gateway_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    gateway_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("gateways.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)


class SPGatewayRole(Base):
    """A per-gateway role override for a service principal.

    Attributes:
        id: Auto-incremented primary key.
        sp_id: FK to the service principal.
        gateway_id: FK to the gateway.
        role: Scoped role for this principal on this gateway.
    """

    __tablename__ = "sp_gateway_roles"
    __table_args__ = (UniqueConstraint("sp_id", "gateway_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sp_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("service_principals.id", ondelete="CASCADE"), nullable=False
    )
    gateway_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("gateways.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)


class Group(Base):
    """A named collection of users for group-based RBAC.

    Attributes:
        id: Auto-incremented primary key.
        name: Unique group name (max 100 chars).
        description: Optional human-readable description.
        role: Global group role (``admin``, ``operator``, ``viewer``).
        created_at: Timestamp when the group was created.
    """

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GroupMember(Base):
    """Junction table linking users to groups.

    Attributes:
        id: Auto-incremented primary key.
        group_id: FK to the group.
        user_id: FK to the user.
    """

    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )


class GroupGatewayRole(Base):
    """A per-gateway role override for a group.

    Attributes:
        id: Auto-incremented primary key.
        group_id: FK to the group.
        gateway_id: FK to the gateway.
        role: Scoped role for this group on this gateway.
    """

    __tablename__ = "group_gateway_roles"
    __table_args__ = (UniqueConstraint("group_id", "gateway_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    gateway_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("gateways.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)


class AuditEntry(Base):
    """A persistent audit log entry for state-changing operations.

    Attributes:
        id: Auto-incremented primary key.
        timestamp: When the action occurred.
        actor: Email or service principal name of the acting identity.
        actor_role: Effective role at time of action.
        action: Machine-readable action identifier.
        resource_type: Type of resource affected (e.g. ``sandbox``).
        resource_id: Identifier of the affected resource.
        gateway_name: Human-readable gateway name, if applicable.
        gateway_id: FK to the gateway, or ``None`` if deleted.
        detail: Optional free-text detail or JSON payload.
        client_ip: IP address of the requesting client, if available.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_timestamp", "timestamp"),
        Index("ix_audit_log_actor", "actor"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str] = mapped_column(String(254), nullable=False)
    actor_role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown", server_default="unknown"
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[str] = mapped_column(
        String(253), nullable=False, default="", server_default=""
    )
    gateway_name: Mapped[str | None] = mapped_column(String(253))
    gateway_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("gateways.id", ondelete="SET NULL")
    )
    detail: Mapped[str | None] = mapped_column(Text)
    client_ip: Mapped[str | None] = mapped_column(String(45))


class SandboxMeta(Base):
    """ShoreGuard-side metadata for a sandbox (labels, description).

    Sandboxes live on the OpenShell gateway; this table stores metadata
    that ShoreGuard manages independently.

    Attributes:
        id: Auto-incremented primary key.
        gateway_name: Name of the gateway the sandbox belongs to.
        sandbox_name: Name of the sandbox (unique per gateway).
        description: Optional free-text description.
        labels_json: Optional JSON-encoded key-value labels.
        created_at: Timestamp when the metadata was first stored.
        updated_at: Timestamp of the last metadata update.
    """

    __tablename__ = "sandbox_meta"
    __table_args__ = (UniqueConstraint("gateway_name", "sandbox_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway_name: Mapped[str] = mapped_column(String(253), nullable=False)
    sandbox_name: Mapped[str] = mapped_column(String(253), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    labels_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))


class Webhook(Base):
    """A webhook endpoint for event notifications.

    Attributes:
        id: Auto-incremented primary key.
        url: Target URL for POST requests (max 2048 chars).
        secret: HMAC-SHA256 signing secret.
        event_types: JSON-encoded list of subscribed event types.
        is_active: Whether the webhook is enabled.
        channel_type: Channel type (generic, slack, discord, email).
        extra_config: Optional JSON config for channel-specific settings.
        created_by: Email or service principal name of the creator.
        created_at: Timestamp when the webhook was created.
    """

    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    secret: Mapped[str] = mapped_column(String(128), nullable=False)
    event_types: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    channel_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="generic", server_default="generic"
    )
    extra_config: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(254), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WebhookDelivery(Base):
    """A delivery attempt for a webhook event.

    Attributes:
        id: Auto-incremented primary key.
        webhook_id: FK to the webhook that was triggered.
        event_type: The event type that triggered the delivery.
        payload_json: JSON-encoded event payload.
        status: Delivery status (``pending``, ``success``, ``failed``).
        response_code: HTTP response code from the target, if any.
        error_message: Error details on failure, if any.
        attempt: Current attempt number (1-based).
        created_at: Timestamp when the delivery was created.
        delivered_at: Timestamp when delivery succeeded, if any.
    """

    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_webhook_deliveries_webhook_id", "webhook_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    webhook_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    response_code: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))


class PolicyPin(Base):
    """A policy pin that locks a sandbox's policy at a specific version.

    When a pin is active, policy updates and draft approvals are blocked
    until the pin is removed or expires.

    Attributes:
        id: Auto-incremented primary key.
        gateway_name: Name of the gateway the sandbox belongs to.
        sandbox_name: Name of the pinned sandbox.
        pinned_version: The policy version that is locked.
        pinned_by: Email or service principal name of the actor who set the pin.
        reason: Optional human-readable reason for pinning.
        pinned_at: Timestamp when the pin was created.
        expires_at: Optional expiry timestamp; ``None`` means pin never expires.
    """

    __tablename__ = "policy_pins"
    __table_args__ = (UniqueConstraint("gateway_name", "sandbox_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway_name: Mapped[str] = mapped_column(String(253), nullable=False)
    sandbox_name: Mapped[str] = mapped_column(String(253), nullable=False)
    pinned_version: Mapped[int] = mapped_column(Integer, nullable=False)
    pinned_by: Mapped[str] = mapped_column(String(254), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    pinned_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))


class ApprovalWorkflow(Base):
    """A multi-stage approval (quorum) configuration for a sandbox.

    When a workflow exists, ``POST .../approvals/{chunk_id}/approve`` records
    a vote rather than calling the upstream gateway directly. The upstream
    approve fires only when the configured quorum is reached.

    Attributes:
        id: Auto-incremented primary key.
        gateway_name: Gateway the sandbox belongs to.
        sandbox_name: Sandbox this workflow applies to.
        required_approvals: Number of distinct approve votes needed.
        required_roles_json: JSON array of roles eligible to vote (empty = any).
        distinct_actors: If true, the same actor cannot vote twice.
        escalation_timeout_minutes: Fire ``approval.escalated`` webhook after
            this many minutes since the first vote on a chunk; ``None`` = off.
        created_by: Identity of the admin who configured the workflow.
        created_at: When the workflow was created.
        updated_at: When the workflow was last updated.
    """

    __tablename__ = "approval_workflows"
    __table_args__ = (UniqueConstraint("gateway_name", "sandbox_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway_name: Mapped[str] = mapped_column(String(253), nullable=False)
    sandbox_name: Mapped[str] = mapped_column(String(253), nullable=False)
    required_approvals: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    required_roles_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    distinct_actors: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    escalation_timeout_minutes: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(String(254), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApprovalDecision(Base):
    """A single vote cast against an approval chunk under a workflow.

    Append-only log; pending/approved/rejected state is derived from the row
    set. Rows are cleared once the upstream gateway approve fires (on quorum
    met) or the chunk is rejected.

    Attributes:
        id: Auto-incremented primary key.
        workflow_id: FK to the active workflow configuration.
        gateway_name: Gateway the sandbox belongs to (denormalised for lookup).
        sandbox_name: Sandbox the chunk belongs to (denormalised for lookup).
        chunk_id: The draft chunk being voted on.
        actor: Identity of the voting user.
        role: Role the voter held at vote time.
        decision: ``approve`` or ``reject``.
        comment: Optional free-text comment.
        created_at: When the vote was cast.
    """

    __tablename__ = "approval_decisions"
    __table_args__ = (
        Index(
            "ix_approval_decisions_chunk",
            "gateway_name",
            "sandbox_name",
            "chunk_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("approval_workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    gateway_name: Mapped[str] = mapped_column(String(253), nullable=False)
    sandbox_name: Mapped[str] = mapped_column(String(253), nullable=False)
    chunk_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor: Mapped[str] = mapped_column(String(254), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PolicyApplyProposal(Base):
    """A YAML policy apply proposal waiting for workflow quorum.

    Created on the first apply call for a sandbox with an active
    quorum approval workflow, and deleted once the proposal reaches
    a terminal state (quorum met, rejected, or superseded by a new
    YAML body). Lets subsequent vote-only calls reference the same
    proposal by its synthetic ``chunk_id`` without requiring the
    second runner to resubmit the YAML body — useful when the
    second voter is a human on the UI rather than the same CI
    pipeline.

    Attributes:
        id: Auto-incremented primary key.
        gateway_name: Gateway the sandbox belongs to.
        sandbox_name: Sandbox the apply targets.
        chunk_id: Synthetic chunk id ``policy.apply:<sha16>`` derived from yaml.
        yaml_text: Raw YAML document body.
        expected_hash: Optimistic-lock etag captured at proposal time.
        proposed_by: Identity of the actor that opened the proposal.
        proposed_at: When the proposal was created.
    """

    __tablename__ = "policy_apply_proposals"
    __table_args__ = (UniqueConstraint("gateway_name", "sandbox_name", "chunk_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway_name: Mapped[str] = mapped_column(String(253), nullable=False)
    sandbox_name: Mapped[str] = mapped_column(String(253), nullable=False)
    chunk_id: Mapped[str] = mapped_column(String(80), nullable=False)
    yaml_text: Mapped[str] = mapped_column(Text, nullable=False)
    expected_hash: Mapped[str | None] = mapped_column(String(80))
    proposed_by: Mapped[str] = mapped_column(String(254), nullable=False)
    proposed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SBOMSnapshot(Base):
    """A CycloneDX SBOM uploaded for a sandbox.

    One snapshot per ``(gateway, sandbox)`` pair — a new upload
    replaces the previous snapshot rather than appending. Historical
    snapshots are intentionally out of scope; if you need them,
    archive the raw CycloneDX in object storage from CI before
    uploading, because the ``raw_json`` column reflects only the
    latest upload.

    Attributes:
        id: Auto-incremented primary key.
        gateway_name: Gateway the sandbox belongs to.
        sandbox_name: Sandbox the SBOM describes.
        bom_format: CycloneDX-only for now ("CycloneDX").
        spec_version: CycloneDX spec version (e.g. "1.5").
        serial_number: Optional CycloneDX serialNumber URN.
        uploaded_by: Identity of the user who uploaded the snapshot.
        uploaded_at: When the snapshot was uploaded.
        component_count: Number of components in the SBOM.
        vulnerability_count: Number of vulnerabilities declared in the SBOM.
        max_severity: Highest severity across all vulnerabilities, or None.
        raw_json: The original CycloneDX JSON document, retained for download.
        components: Cascade-delete relationship to ``SBOMComponent`` rows.
    """

    __tablename__ = "sbom_snapshots"
    __table_args__ = (UniqueConstraint("gateway_name", "sandbox_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway_name: Mapped[str] = mapped_column(String(253), nullable=False, index=True)
    sandbox_name: Mapped[str] = mapped_column(String(253), nullable=False, index=True)
    bom_format: Mapped[str] = mapped_column(String(32), nullable=False)
    spec_version: Mapped[str] = mapped_column(String(16), nullable=False)
    serial_number: Mapped[str | None] = mapped_column(String(128))
    uploaded_by: Mapped[str] = mapped_column(String(254), nullable=False)
    uploaded_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    component_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vulnerability_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_severity: Mapped[str | None] = mapped_column(String(16))
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)

    components: Mapped[list[SBOMComponent]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class SBOMComponent(Base):
    """A single component row denormalised from a CycloneDX SBOM.

    Components are stored as flat rows so the components search
    endpoint can paginate and filter via SQL without re-parsing the
    raw CycloneDX JSON on each request. The ``vuln_count`` and
    ``max_severity`` columns are maintained at ingest time by
    joining through ``bom_ref`` against the document's
    ``vulnerabilities`` array, so the search endpoint never has to
    open the raw document.

    Attributes:
        id: Auto-incremented primary key.
        snapshot_id: Foreign key to the parent SBOM snapshot.
        bom_ref: CycloneDX bom-ref of the component (used to join vulns).
        name: Component name (e.g. "requests").
        version: Component version (e.g. "2.31.0").
        purl: Package URL (e.g. "pkg:pypi/requests@2.31.0").
        type: CycloneDX type (library, framework, container, ...).
        licenses: Comma-joined license identifiers.
        vuln_count: Number of vulnerabilities affecting this component.
        max_severity: Highest severity across the component's vulnerabilities.
        snapshot: Backref to the parent ``SBOMSnapshot`` row.
    """

    __tablename__ = "sbom_components"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("sbom_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bom_ref: Mapped[str | None] = mapped_column(String(512), index=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    version: Mapped[str | None] = mapped_column(String(128))
    purl: Mapped[str | None] = mapped_column(String(1024), index=True)
    type: Mapped[str | None] = mapped_column(String(32))
    licenses: Mapped[str | None] = mapped_column(Text)
    vuln_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_severity: Mapped[str | None] = mapped_column(String(16))

    snapshot: Mapped[SBOMSnapshot] = relationship(back_populates="components")


class SandboxBootHook(Base):
    """A pre- or post-create boot hook attached to a sandbox.

    Pre-create hooks act as ShoreGuard-side validation gates: their
    commands execute via ``subprocess.run`` inside the ShoreGuard
    process *before* ``CreateSandbox`` reaches the gateway, with a
    whitelisted environment exposing only ``SG_SANDBOX_NAME``,
    ``SG_SANDBOX_IMAGE``, ``SG_SANDBOX_POLICY_ID``, and the hook's
    user-defined ``env`` entries.

    Post-create hooks run *inside* the new sandbox via the existing
    ``ExecSandbox`` RPC once creation succeeds, intended for warm-up
    tasks like package updates or telemetry initialisation.

    The execution surface is deliberately on the ShoreGuard side
    because the upstream gRPC contract has no native hook RPC. Once
    one exists, ``BootHookService`` can detect it and delegate
    without the schema changing.

    Attributes:
        id: Auto-incremented primary key.
        gateway_name: Gateway the sandbox belongs to.
        sandbox_name: Sandbox this hook attaches to.
        name: Human-readable hook name (unique per sandbox+phase).
        phase: ``pre_create`` or ``post_create``.
        command: Shell command to execute (parsed via shlex).
        workdir: Working directory inside the sandbox (post-create only).
        env_json: JSON-encoded extra environment variables.
        timeout_seconds: Hard wall-clock timeout for the hook.
        order: Sort key within (sandbox, phase).
        enabled: Whether the hook participates in automatic runs.
        continue_on_failure: If true, post-create failures don't abort
            subsequent hooks (pre-create always aborts on failure).
        created_by: Identity of the user who created the hook.
        created_at: Timestamp when the hook was created.
        updated_at: Timestamp of the last update.
        last_run_at: Timestamp of the most recent run.
        last_status: ``success`` / ``failure`` / ``skipped`` / ``None``.
        last_output: Captured stdout+stderr (truncated to 4 KiB).
    """

    __tablename__ = "sandbox_boot_hooks"
    __table_args__ = (
        UniqueConstraint("gateway_name", "sandbox_name", "phase", "name"),
        Index(
            "ix_sandbox_boot_hooks_lookup",
            "gateway_name",
            "sandbox_name",
            "phase",
            "order",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway_name: Mapped[str] = mapped_column(String(253), nullable=False)
    sandbox_name: Mapped[str] = mapped_column(String(253), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    workdir: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    env_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    continue_on_failure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str] = mapped_column(String(254), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_run_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(16))
    last_output: Mapped[str | None] = mapped_column(Text)


class OperationRecord(Base):
    """A tracked long-running operation with DB persistence.

    Attributes:
        id: UUID primary key.
        status: Lifecycle state (pending → running → succeeded/failed, or cancelling → failed).
        resource_type: Type of resource (sandbox, exec, gateway).
        resource_key: Resource identifier for duplicate detection.
        idempotency_key: Optional client-provided key for idempotent requests.
        progress_pct: Progress percentage (0-100).
        progress_msg: Human-readable progress message.
        result_json: JSON-encoded result payload on success.
        error_message: Error description on failure.
        error_code: Machine-readable error code (timeout, cancelled, etc.).
        actor: Identity of the user who started the operation.
        gateway_name: Gateway the operation targets.
        created_at: When the operation was created.
        updated_at: When the operation was last updated.
        completed_at: When the operation finished.
    """

    __tablename__ = "operations"
    __table_args__ = (
        Index("ix_operations_status", "status"),
        Index("ix_operations_resource", "resource_type", "resource_key"),
        Index(
            "ix_operations_active_resource",
            "resource_type",
            "resource_key",
            unique=True,
            sqlite_where=text("status IN ('pending', 'running', 'cancelling')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(253), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(253), unique=True)
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_msg: Mapped[str | None] = mapped_column(String(500))
    result_json: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(50))
    actor: Mapped[str | None] = mapped_column(String(254))
    gateway_name: Mapped[str | None] = mapped_column(String(253))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
