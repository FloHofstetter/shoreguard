"""Tenant models — a governance/visibility grouping of gateways.

A tenant groups gateways and users so a non-admin user sees only the
spend/audit/approvals/health of the gateways in their tenants. This is a
control-plane *visibility* boundary, not data-plane isolation: ShoreGuard
never blocks a gateway gRPC call based on tenant, and namespace/quota/GPU
isolation stays OpenShell's job. Membership is an explicit, auditable join
(not derived from gateway labels) so it survives gateway rename and is
queryable by id. Mirrors the Group / GroupMember / GroupGatewayRole shapes.
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from shoreguard.db.models.base import Base


class Tenant(Base):
    """A named governance unit grouping gateways and users.

    Attributes:
        id: Auto-incremented primary key.
        name: Unique tenant name (max 100 chars).
        description: Optional human-readable description.
        created_at: Timestamp when the tenant was created.
    """

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TenantGateway(Base):
    """Junction table linking gateways to a tenant.

    Attributes:
        id: Auto-incremented primary key.
        tenant_id: FK to the tenant (cascade delete).
        gateway_id: FK to the gateway (cascade delete).
    """

    __tablename__ = "tenant_gateways"
    __table_args__ = (UniqueConstraint("tenant_id", "gateway_id", name="uq_tenant_gateway"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gateway_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("gateways.id", ondelete="CASCADE"), nullable=False
    )


class TenantUser(Base):
    """Junction table linking users to a tenant.

    Attributes:
        id: Auto-incremented primary key.
        tenant_id: FK to the tenant (cascade delete).
        user_id: FK to the user (cascade delete).
    """

    __tablename__ = "tenant_users"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_tenant_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
