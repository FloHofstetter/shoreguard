"""Tenant CRUD, membership, and per-tenant rollups.

A tenant groups gateways and users so a non-admin user's view (gateway
list, fleet overview, digest) is restricted to their tenants' gateways
— a control-plane visibility boundary, never data-plane isolation. The
scoping primitive that the read paths call lives in
:mod:`shoreguard.api.auth.rbac` (it needs the request identity); this
service owns the persistence and the per-tenant rollup.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from shoreguard.models import Gateway, Tenant, TenantGateway, TenantUser, User

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from shoreguard.services.budgets import BudgetService
    from shoreguard.services.registry import GatewayRegistry


class TenantService:
    """Persist tenants and resolve their gateway/user membership.

    Args:
        session_factory: Async SQLAlchemy session factory.
        registry: Gateway registry (for rollup health and name lookups).
    """

    def __init__(  # noqa: D107
        self,
        session_factory: async_sessionmaker[AsyncSession],
        registry: GatewayRegistry,
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry

    # ── tenant CRUD ────────────────────────────────────────────────────────

    async def create_tenant(self, name: str, description: str | None) -> dict[str, Any]:
        """Create a tenant.

        Args:
            name: Unique tenant name.
            description: Optional description.

        Returns:
            dict[str, Any]: The created tenant record.

        Raises:
            ValueError: If a tenant with this name already exists.
        """
        now = datetime.datetime.now(datetime.UTC)
        async with self._session_factory() as session:
            row = Tenant(name=name, description=description, created_at=now)
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ValueError(f"Tenant '{name}' already exists") from exc
            return self._tenant_dict(row)

    async def list_tenants(self) -> list[dict[str, Any]]:
        """Return all tenants with their gateway and user counts.

        Returns:
            list[dict[str, Any]]: Tenant records ordered by name.
        """
        async with self._session_factory() as session:
            tenants = (await session.execute(select(Tenant).order_by(Tenant.name))).scalars().all()
            gw_count_rows = (
                await session.execute(
                    select(TenantGateway.tenant_id, func.count()).group_by(TenantGateway.tenant_id)
                )
            ).all()
            gw_counts: dict[int, int] = {int(tid): int(cnt) for tid, cnt in gw_count_rows}
            user_count_rows = (
                await session.execute(
                    select(TenantUser.tenant_id, func.count()).group_by(TenantUser.tenant_id)
                )
            ).all()
            user_counts: dict[int, int] = {int(tid): int(cnt) for tid, cnt in user_count_rows}
        return [
            {
                **self._tenant_dict(t),
                "gateway_count": int(gw_counts.get(t.id, 0)),
                "user_count": int(user_counts.get(t.id, 0)),
            }
            for t in tenants
        ]

    async def get_tenant(self, tenant_id: int) -> dict[str, Any] | None:
        """Return a tenant with its gateway names and user ids.

        Args:
            tenant_id: Tenant primary key.

        Returns:
            dict[str, Any] | None: The tenant with ``gateways`` and
                ``users`` members, or ``None`` if not found.
        """
        async with self._session_factory() as session:
            tenant = await session.get(Tenant, tenant_id)
            if tenant is None:
                return None
            gateways = (
                (
                    await session.execute(
                        select(Gateway.name)
                        .join(TenantGateway, TenantGateway.gateway_id == Gateway.id)
                        .where(TenantGateway.tenant_id == tenant_id)
                        .order_by(Gateway.name)
                    )
                )
                .scalars()
                .all()
            )
            users = (
                await session.execute(
                    select(User.id, User.email)
                    .join(TenantUser, TenantUser.user_id == User.id)
                    .where(TenantUser.tenant_id == tenant_id)
                    .order_by(User.email)
                )
            ).all()
        return {
            **self._tenant_dict(tenant),
            "gateways": list(gateways),
            "users": [{"id": uid, "email": email} for uid, email in users],
        }

    async def update_tenant(
        self, tenant_id: int, *, name: str, description: str | None
    ) -> dict[str, Any] | None:
        """Update a tenant's name/description.

        Args:
            tenant_id: Tenant primary key.
            name: New tenant name.
            description: New description.

        Returns:
            dict[str, Any] | None: The updated record, or ``None`` if not found.

        Raises:
            ValueError: If the new name collides with another tenant.
        """
        async with self._session_factory() as session:
            tenant = await session.get(Tenant, tenant_id)
            if tenant is None:
                return None
            tenant.name = name
            tenant.description = description
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ValueError(f"Tenant '{name}' already exists") from exc
            return self._tenant_dict(tenant)

    async def delete_tenant(self, tenant_id: int) -> bool:
        """Delete a tenant (cascades to its memberships).

        Args:
            tenant_id: Tenant primary key.

        Returns:
            bool: True when a tenant existed and was deleted.
        """
        async with self._session_factory() as session:
            tenant = await session.get(Tenant, tenant_id)
            if tenant is None:
                return False
            await session.delete(tenant)
            await session.commit()
            return True

    # ── membership ─────────────────────────────────────────────────────────

    async def add_gateway(self, tenant_id: int, gateway_name: str) -> bool:
        """Assign a gateway to a tenant.

        Args:
            tenant_id: Tenant primary key.
            gateway_name: Gateway name to add.

        Returns:
            bool: True on success; False if the tenant or gateway is unknown.
        """
        async with self._session_factory() as session:
            if await session.get(Tenant, tenant_id) is None:
                return False
            gateway_id = await self._gateway_id(session, gateway_name)
            if gateway_id is None:
                return False
            session.add(TenantGateway(tenant_id=tenant_id, gateway_id=gateway_id))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()  # already a member — idempotent
            return True

    async def remove_gateway(self, tenant_id: int, gateway_name: str) -> bool:
        """Remove a gateway from a tenant.

        Args:
            tenant_id: Tenant primary key.
            gateway_name: Gateway name to remove.

        Returns:
            bool: True if a membership row was removed.
        """
        async with self._session_factory() as session:
            gateway_id = await self._gateway_id(session, gateway_name)
            if gateway_id is None:
                return False
            row = (
                (
                    await session.execute(
                        select(TenantGateway).where(
                            TenantGateway.tenant_id == tenant_id,
                            TenantGateway.gateway_id == gateway_id,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def add_user(self, tenant_id: int, user_id: int) -> bool:
        """Assign a user to a tenant.

        Args:
            tenant_id: Tenant primary key.
            user_id: User primary key to add.

        Returns:
            bool: True on success; False if the tenant or user is unknown.
        """
        async with self._session_factory() as session:
            if await session.get(Tenant, tenant_id) is None:
                return False
            if await session.get(User, user_id) is None:
                return False
            session.add(TenantUser(tenant_id=tenant_id, user_id=user_id))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()  # already a member — idempotent
            return True

    async def remove_user(self, tenant_id: int, user_id: int) -> bool:
        """Remove a user from a tenant.

        Args:
            tenant_id: Tenant primary key.
            user_id: User primary key to remove.

        Returns:
            bool: True if a membership row was removed.
        """
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        select(TenantUser).where(
                            TenantUser.tenant_id == tenant_id,
                            TenantUser.user_id == user_id,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def scoped_gateway_names_for_user(self, user_id: int) -> set[str] | None:
        """Return the gateway names visible to a user, or None if unscoped.

        ``None`` means "not in any tenant" (the caller treats this as the
        full fleet — preserving today's behaviour). A non-empty set is the
        union of the user's tenants' gateways; an empty set means the user
        is in tenants that hold no gateways (and so sees nothing).

        Args:
            user_id: User primary key.

        Returns:
            set[str] | None: Allowed gateway names, or ``None`` if the user
                belongs to no tenant.
        """
        async with self._session_factory() as session:
            tenant_ids = (
                (
                    await session.execute(
                        select(TenantUser.tenant_id).where(TenantUser.user_id == user_id)
                    )
                )
                .scalars()
                .all()
            )
            if not tenant_ids:
                return None
            names = (
                (
                    await session.execute(
                        select(Gateway.name)
                        .join(TenantGateway, TenantGateway.gateway_id == Gateway.id)
                        .where(TenantGateway.tenant_id.in_(tenant_ids))
                    )
                )
                .scalars()
                .all()
            )
        return set(names)

    # ── rollup ─────────────────────────────────────────────────────────────

    async def rollup(
        self, tenant_id: int, budget: BudgetService, *, days: int = 7
    ) -> dict[str, Any]:
        """Summarise a tenant's spend and gateway health.

        Args:
            tenant_id: Tenant primary key.
            budget: Budget service used for the cross-gateway spend summary.
            days: Trailing window for the spend rollup.

        Returns:
            dict[str, Any]: ``{"tenant_id", "gateways", "unreachable",
                "spend": {...filtered to the tenant's gateways...}}``.
        """
        detail = await self.get_tenant(tenant_id)
        names = set(detail["gateways"]) if detail else set()
        gateways = await self._registry.list_all()
        unreachable = [
            gw["name"]
            for gw in gateways
            if gw["name"] in names and gw.get("last_status") in ("unreachable", "offline")
        ]
        summary = await budget.summary(days=days)
        top = [row for row in summary.get("top", []) if row.get("gateway") in names]
        spend = {
            "since": summary.get("since"),
            "top": top,
            "estimated_cost": round(sum(float(r.get("estimated_cost", 0.0)) for r in top), 6),
            "currency_label": summary.get("currency_label", ""),
        }
        return {
            "tenant_id": tenant_id,
            "gateways": sorted(names),
            "unreachable": unreachable,
            "spend": spend,
        }

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    async def _gateway_id(session: AsyncSession, gateway_name: str) -> int | None:
        """Resolve a gateway name to its primary key.

        Args:
            session: Open async session.
            gateway_name: Gateway name.

        Returns:
            int | None: The gateway id, or ``None`` if unknown.
        """
        return (
            await session.execute(select(Gateway.id).where(Gateway.name == gateway_name))
        ).scalar_one_or_none()

    @staticmethod
    def _tenant_dict(row: Tenant) -> dict[str, Any]:
        """Serialise a tenant row.

        Args:
            row: The tenant ORM row.

        Returns:
            dict[str, Any]: JSON-serialisable tenant record.
        """
        return {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
