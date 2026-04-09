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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
