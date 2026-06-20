"""Gateway inventory snapshots and restart reap-diff.

A gateway/Docker restart destroys all sandboxes; ShoreGuard cannot prevent
that (it is data-plane), but it can *surface* the blast radius. This store
snapshots each gateway's sandboxes and their provider attachments on every
successful health probe, and on an ``unreachable → recovered`` transition
diffs the last pre-down snapshot against a fresh one to record what the
restart reaped. Everything here reuses existing client RPCs
(``sandboxes.list`` / ``list_providers``) — no new upstream RPC — and writes
append-only forensic rows (never reversible state, so it never touches the
kill-switch table).
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from shoreguard.models import GatewayInventorySnapshot, GatewayReapRecord

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


class GatewayInventoryStore:
    """Persist gateway inventory snapshots and reap records.

    Args:
        session_factory: Async SQLAlchemy session factory.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:  # noqa: D107
        self._session_factory = session_factory

    async def capture(self, gateway: str, client: Any) -> dict[str, list[str]]:
        """Snapshot a gateway's sandboxes and their provider attachments.

        Args:
            gateway: Gateway name.
            client: Connected gateway client.

        Returns:
            dict[str, list[str]]: ``{sandbox_name: [provider names sorted]}``.
        """
        sandboxes = await client.sandboxes.list(limit=1000)
        inventory: dict[str, list[str]] = {}
        for sb in sandboxes:
            name = sb.get("name") or sb.get("sandbox_name")
            if not name:
                continue
            try:
                providers = await client.sandboxes.list_providers(name)
            except Exception:  # noqa: BLE001 — a sandbox without readable providers
                providers = []
            provider_names = sorted(
                p.get("name") or p.get("provider_name")
                for p in providers
                if isinstance(p.get("name") or p.get("provider_name"), str)
            )
            inventory[name] = provider_names
        async with self._session_factory() as session:
            session.add(
                GatewayInventorySnapshot(
                    gateway=gateway,
                    captured_at=datetime.datetime.now(datetime.UTC),
                    sandboxes_json=json.dumps(inventory),
                    sandbox_count=len(inventory),
                )
            )
            await session.commit()
        return inventory

    async def latest(self, gateway: str) -> dict[str, list[str]] | None:
        """Return the most recent snapshot's inventory map for a gateway.

        Args:
            gateway: Gateway name.

        Returns:
            dict[str, list[str]] | None: The newest inventory map, or ``None``
                if no snapshot exists.
        """
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        select(GatewayInventorySnapshot)
                        .where(GatewayInventorySnapshot.gateway == gateway)
                        .order_by(GatewayInventorySnapshot.captured_at.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
        if row is None:
            return None
        try:
            data = json.loads(row.sandboxes_json)
        except json.JSONDecodeError:
            return {}
        return {k: list(v) for k, v in data.items()}

    async def diff_and_record(
        self,
        gateway: str,
        pre: dict[str, list[str]],
        post: dict[str, list[str]],
        recovered_from: str,
    ) -> list[dict[str, Any]]:
        """Diff pre-down vs post-recovery inventory and record any reap.

        Args:
            gateway: Gateway name.
            pre: Pre-down inventory map (sandbox → providers).
            post: Post-recovery inventory map.
            recovered_from: The down status the gateway recovered from.

        Returns:
            list[dict[str, Any]]: ``[{sandbox, lost_providers}]`` for sandboxes
                that vanished or lost attachments; empty when nothing was lost.
        """
        reaped: list[dict[str, Any]] = []
        for sandbox, providers in pre.items():
            if sandbox not in post:
                reaped.append({"sandbox": sandbox, "lost_providers": list(providers)})
            else:
                lost = [p for p in providers if p not in post[sandbox]]
                if lost:
                    reaped.append({"sandbox": sandbox, "lost_providers": lost})
        if not reaped:
            return []
        async with self._session_factory() as session:
            session.add(
                GatewayReapRecord(
                    gateway=gateway,
                    detected_at=datetime.datetime.now(datetime.UTC),
                    recovered_from_status=recovered_from[:16],
                    reaped_json=json.dumps(reaped),
                    reaped_count=len(reaped),
                )
            )
            await session.commit()
        return reaped

    async def latest_inventory(self, gateway: str) -> dict[str, Any] | None:
        """Return the latest snapshot (with metadata) for a gateway, or None.

        Args:
            gateway: Gateway name.

        Returns:
            dict[str, Any] | None: ``{gateway, captured_at, sandbox_count,
                sandboxes}`` or ``None``.
        """
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        select(GatewayInventorySnapshot)
                        .where(GatewayInventorySnapshot.gateway == gateway)
                        .order_by(GatewayInventorySnapshot.captured_at.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
        if row is None:
            return None
        try:
            sandboxes = json.loads(row.sandboxes_json)
        except json.JSONDecodeError:
            sandboxes = {}
        return {
            "gateway": row.gateway,
            "captured_at": row.captured_at.isoformat(),
            "sandbox_count": row.sandbox_count,
            "sandboxes": sandboxes,
        }

    async def list_recent_reaps(
        self, *, limit: int = 50, gateway: str | None = None
    ) -> list[dict[str, Any]]:
        """Return recent reap records, newest first (fleet-wide by default).

        Args:
            limit: Maximum number of records.
            gateway: When set, restrict to this gateway.

        Returns:
            list[dict[str, Any]]: Reap records.
        """
        stmt = select(GatewayReapRecord).order_by(GatewayReapRecord.detected_at.desc()).limit(limit)
        if gateway is not None:
            stmt = stmt.where(GatewayReapRecord.gateway == gateway)
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                reaped = json.loads(row.reaped_json)
            except json.JSONDecodeError:
                reaped = []
            result.append(
                {
                    "gateway": row.gateway,
                    "detected_at": row.detected_at.isoformat(),
                    "recovered_from_status": row.recovered_from_status,
                    "reaped": reaped,
                    "reaped_count": row.reaped_count,
                }
            )
        return result

    async def count_reaps_since(self, since: datetime.datetime) -> int:
        """Return the total sandboxes reaped across the fleet since a time.

        Args:
            since: Lower-bound timestamp.

        Returns:
            int: Sum of ``reaped_count`` over reap records since ``since``.
        """
        from sqlalchemy import func

        async with self._session_factory() as session:
            total = (
                await session.execute(
                    select(func.coalesce(func.sum(GatewayReapRecord.reaped_count), 0)).where(
                        GatewayReapRecord.detected_at >= since
                    )
                )
            ).scalar_one()
        return int(total)

    async def prune(self, retention_days: int) -> int:
        """Delete snapshots and reap records older than the retention window.

        Args:
            retention_days: Age threshold in days.

        Returns:
            int: Always ``0`` (count not tracked) — kept for the cleanup hook.
        """
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=retention_days)
        async with self._session_factory() as session:
            await session.execute(
                delete(GatewayInventorySnapshot).where(
                    GatewayInventorySnapshot.captured_at < cutoff
                )
            )
            await session.execute(
                delete(GatewayReapRecord).where(GatewayReapRecord.detected_at < cutoff)
            )
            await session.commit()
        return 0
