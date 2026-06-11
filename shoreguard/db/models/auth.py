"""User, service-principal, group, and role models."""

from __future__ import annotations

import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from shoreguard.db.models.base import Base


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


class WebAuthnCredential(Base):
    """A registered passkey (WebAuthn credential) for a user.

    Attributes:
        id: Auto-incremented primary key.
        user_id: FK to the owning user (cascade delete).
        credential_id: WebAuthn credential ID (base64url, unique).
        public_key: COSE public key (base64url).
        sign_count: Authenticator signature counter (clone detection).
        transports: JSON list of authenticator transports, or ``None``.
        name: Operator-given device label (e.g. "Pixel 9").
        created_at: When the passkey was registered.
        last_used: When the passkey last signed in, or ``None``.
    """

    __tablename__ = "webauthn_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    credential_id: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    sign_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    transports: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="passkey")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))


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
