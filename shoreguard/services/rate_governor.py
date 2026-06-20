"""Per-sandbox inference rate ceilings with a reversible soft-pause.

OpenShell has only a gateway-wide gRPC limiter, so one runaway agent can
exhaust a shared provider key while ShoreGuard's only existing brake is the
hard kill switch. The rate governor adds a per-sandbox request-rate ceiling
(``max_requests`` per a tumbling ``window_seconds``) that, when exceeded,
engages a REVERSIBLE soft-pause: it detaches the sandbox's providers like
the kill switch, but into its OWN ``rate_pause_entries`` table with an
auto-resume cooldown — sitting between budgets and the hard kill switch.

It reuses the metered request counts (no second gateway log poll). Honest
limits inherited from metering: "requests" are proxy log lines (not tokens),
counting starts at metering activation, and per-agent == per-sandbox. The
governor never writes ``KillSwitchEntry`` and skips any sandbox that already
holds one, re-attaching only the providers it itself detached.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from shoreguard.models import KillSwitchEntry, RatePauseEntry, SandboxRateLimit, SandboxUsage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from shoreguard.services.gateway import GatewayService
    from shoreguard.services.registry import GatewayRegistry
    from shoreguard.settings import RateGovernorSettings

logger = logging.getLogger(__name__)


class RateGovernorService:
    """Evaluate per-sandbox rate ceilings and drive the reversible soft-pause.

    Args:
        session_factory: Async SQLAlchemy session factory.
        gateway_service: Live-connection service used to reach gateways.
        registry: Gateway registry (unused today; kept for parity/future use).
        settings: Active ``RateGovernorSettings``.
    """

    def __init__(  # noqa: D107
        self,
        session_factory: async_sessionmaker[AsyncSession],
        gateway_service: GatewayService,
        registry: GatewayRegistry,
        settings: RateGovernorSettings,
    ) -> None:
        self._session_factory = session_factory
        self._gateways = gateway_service
        self._registry = registry
        self._settings = settings

    # ── rate-limit CRUD ─────────────────────────────────────────────────────

    async def get_rate_limit(self, gateway: str, sandbox: str) -> dict[str, Any] | None:
        """Return the rate limit configured for a sandbox, or None.

        Args:
            gateway: Gateway name.
            sandbox: Sandbox name.

        Returns:
            dict[str, Any] | None: Rate-limit record or ``None``.
        """
        async with self._session_factory() as session:
            row = await self._get_row(session, gateway, sandbox)
            return self._limit_dict(row) if row else None

    async def set_rate_limit(
        self,
        gateway: str,
        sandbox: str,
        *,
        max_requests: int,
        window_seconds: int,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Create or update a sandbox rate limit (resets the window).

        Args:
            gateway: Gateway name.
            sandbox: Sandbox name.
            max_requests: Request ceiling within the window (>= 1).
            window_seconds: Tumbling window length in seconds (>= 1).
            enabled: Whether the governor evaluates this limit.

        Returns:
            dict[str, Any]: The saved rate-limit record.

        Raises:
            ValueError: On invalid max_requests or window_seconds.
        """
        if max_requests < 1:
            raise ValueError("max_requests must be >= 1")
        if window_seconds < 1:
            raise ValueError("window_seconds must be >= 1")
        now = datetime.datetime.now(datetime.UTC)
        async with self._session_factory() as session:
            row = await self._get_row(session, gateway, sandbox)
            if row is None:
                row = SandboxRateLimit(
                    gateway=gateway,
                    sandbox=sandbox,
                    max_requests=max_requests,
                    window_seconds=window_seconds,
                    enabled=enabled,
                    window_started_at=None,
                    window_count_start=0,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.max_requests = max_requests
                row.window_seconds = window_seconds
                row.enabled = enabled
                row.window_started_at = None  # reset the window on reconfigure
                row.window_count_start = 0
                row.updated_at = now
            await session.commit()
            return self._limit_dict(row)

    async def delete_rate_limit(self, gateway: str, sandbox: str) -> bool:
        """Remove a sandbox rate limit.

        Args:
            gateway: Gateway name.
            sandbox: Sandbox name.

        Returns:
            bool: True when a limit existed and was removed.
        """
        async with self._session_factory() as session:
            row = await self._get_row(session, gateway, sandbox)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def status(self, gateway: str, sandbox: str) -> dict[str, Any]:
        """Return the rate limit plus current soft-pause state for a sandbox.

        Args:
            gateway: Gateway name.
            sandbox: Sandbox name.

        Returns:
            dict[str, Any]: ``{"rate_limit", "paused", "resume_after"}``.
        """
        async with self._session_factory() as session:
            row = await self._get_row(session, gateway, sandbox)
            pause = await self._get_pause(session, gateway, sandbox)
        return {
            "rate_limit": self._limit_dict(row) if row else None,
            "paused": pause is not None,
            "resume_after": pause.resume_after.isoformat() if pause else None,
        }

    async def list_paused(self) -> list[dict[str, Any]]:
        """Return all active soft-pauses across the fleet.

        Returns:
            list[dict[str, Any]]: Active rate-pause entries.
        """
        async with self._session_factory() as session:
            rows = (await session.execute(select(RatePauseEntry))).scalars().all()
        return [
            {
                "gateway": r.gateway,
                "sandbox": r.sandbox,
                "paused_at": r.paused_at.isoformat(),
                "resume_after": r.resume_after.isoformat(),
                "reason": r.reason,
            }
            for r in rows
        ]

    # ── evaluation ──────────────────────────────────────────────────────────

    async def run_once(self) -> dict[str, int]:
        """Evaluate every rate limit and auto-resume due pauses.

        Returns:
            dict[str, int]: ``{"paused": n, "resumed": n}`` summary.
        """
        paused = await self._evaluate()
        resumed = await self._auto_resume()
        return {"paused": paused, "resumed": resumed}

    async def _cumulative_counts(self) -> dict[tuple[str, str], int]:
        """Sum metered requests per (gateway, sandbox) across all days.

        Returns:
            dict[tuple[str, str], int]: Cumulative request counts.
        """
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        SandboxUsage.gateway,
                        SandboxUsage.sandbox,
                        func.sum(SandboxUsage.requests),
                    ).group_by(SandboxUsage.gateway, SandboxUsage.sandbox)
                )
            ).all()
        return {(g, s): int(c) for g, s, c in rows}

    async def _evaluate(self) -> int:
        """Advance each rate limit's window and soft-pause those over ceiling.

        Returns:
            int: Number of sandboxes soft-paused on this call.
        """
        now = datetime.datetime.now(datetime.UTC)
        counts = await self._cumulative_counts()
        to_pause: list[tuple[str, str]] = []
        async with self._session_factory() as session:
            limits = (
                (
                    await session.execute(
                        select(SandboxRateLimit).where(SandboxRateLimit.enabled.is_(True))
                    )
                )
                .scalars()
                .all()
            )
            for row in limits:
                current = counts.get((row.gateway, row.sandbox), 0)
                started = row.window_started_at
                if started is not None and started.tzinfo is None:
                    # SQLite returns naive datetimes for DateTime(timezone=True).
                    started = started.replace(tzinfo=datetime.UTC)
                expired = started is None or (now - started).total_seconds() >= row.window_seconds
                if expired:
                    row.window_started_at = now
                    row.window_count_start = current
                    used = 0
                else:
                    used = max(0, current - row.window_count_start)
                if used >= row.max_requests:
                    to_pause.append((row.gateway, row.sandbox))
            await session.commit()
        paused = 0
        for gateway, sandbox in to_pause:
            if await self._soft_pause(gateway, sandbox):
                paused += 1
        return paused

    async def _soft_pause(self, gateway: str, sandbox: str) -> bool:
        """Detach a sandbox's providers into a rate-pause entry (reversible).

        Skips any sandbox that already holds a ``KillSwitchEntry`` (so the
        governor never double-detaches or lets an unrelated resume re-attach
        providers it did not cut) or an existing rate pause.

        Args:
            gateway: Gateway name.
            sandbox: Sandbox name.

        Returns:
            bool: True when a new soft-pause was engaged.
        """
        async with self._session_factory() as session:
            if await self._get_kill_switch(session, gateway, sandbox) is not None:
                return False  # already cut by kill switch / budget / curfew
            if await self._get_pause(session, gateway, sandbox) is not None:
                return False  # already soft-paused
        try:
            client = await self._gateways.get_client(gateway)
            providers = await client.sandboxes.list_providers(sandbox)
        except Exception:  # noqa: BLE001 — enforcement is best effort
            logger.warning(
                "Rate governor: gateway %s unavailable for %s", gateway, sandbox, exc_info=True
            )
            return False
        detached: list[str] = []
        for p in providers:
            pname = p.get("name") or p.get("provider_name")
            if not isinstance(pname, str) or not pname:
                continue
            try:
                await client.sandboxes.detach_provider(sandbox, pname)
                detached.append(pname)
            except Exception:  # noqa: BLE001 — keep cutting the rest
                logger.warning(
                    "Rate-pause detach failed (gw=%s, sb=%s, provider=%s)",
                    gateway,
                    sandbox,
                    pname,
                    exc_info=True,
                )
        now = datetime.datetime.now(datetime.UTC)
        resume_after = now + datetime.timedelta(seconds=self._settings.cooldown_seconds)
        async with self._session_factory() as session:
            if await self._get_pause(session, gateway, sandbox) is not None:
                return False  # raced with another pause
            session.add(
                RatePauseEntry(
                    gateway=gateway,
                    sandbox=sandbox,
                    providers_json=json.dumps(detached),
                    paused_at=now,
                    resume_after=resume_after,
                    reason="rate_governor",
                )
            )
            await session.commit()
        logger.warning(
            "Rate governor SOFT-PAUSED %s/%s (detached %d providers, resume after %ds)",
            gateway,
            sandbox,
            len(detached),
            self._settings.cooldown_seconds,
        )
        from shoreguard.services.webhooks import fire_webhook

        await fire_webhook(
            "rate.paused",
            {
                "gateway": gateway,
                "sandbox": sandbox,
                "detached": detached,
                "resume_after": resume_after.isoformat(),
            },
        )
        return True

    async def _auto_resume(self) -> int:
        """Re-attach providers for pauses whose cooldown has elapsed.

        Returns:
            int: Number of sandboxes fully resumed on this call.
        """
        now = datetime.datetime.now(datetime.UTC)
        async with self._session_factory() as session:
            due = (
                (
                    await session.execute(
                        select(RatePauseEntry).where(RatePauseEntry.resume_after <= now)
                    )
                )
                .scalars()
                .all()
            )
            due_keys = [(e.gateway, e.sandbox, e.providers_json) for e in due]
        counts = await self._cumulative_counts()
        resumed = 0
        for gateway, sandbox, providers_json in due_keys:
            try:
                client = await self._gateways.get_client(gateway)
            except Exception:  # noqa: BLE001 — retry on a later tick
                logger.debug("Rate resume: gateway %s unavailable", gateway, exc_info=True)
                continue
            try:
                providers = json.loads(providers_json)
            except json.JSONDecodeError:
                providers = []
            still_failing: list[str] = []
            for provider in providers:
                try:
                    await client.sandboxes.attach_provider(sandbox, provider)
                except Exception:  # noqa: BLE001 — record and continue
                    still_failing.append(provider)
            await self._finish_resume(gateway, sandbox, still_failing, now, counts)
            if not still_failing:
                resumed += 1
                from shoreguard.services.webhooks import fire_webhook

                await fire_webhook("rate.resumed", {"gateway": gateway, "sandbox": sandbox})
        return resumed

    async def _finish_resume(
        self,
        gateway: str,
        sandbox: str,
        still_failing: list[str],
        now: datetime.datetime,
        counts: dict[tuple[str, str], int],
    ) -> None:
        """Persist the outcome of a resume attempt and reset the window.

        On full success the pause row is deleted and the rate-limit window is
        reset (so the just-resumed sandbox is not immediately re-paused). On
        partial failure the entry is kept with only the still-detached
        providers and its cooldown is bumped to retry later.

        Args:
            gateway: Gateway name.
            sandbox: Sandbox name.
            still_failing: Providers that could not be re-attached.
            now: Current time.
            counts: Cumulative metered counts (to reset the window baseline).
        """
        async with self._session_factory() as session:
            entry = await self._get_pause(session, gateway, sandbox)
            if entry is None:
                return
            if still_failing:
                entry.providers_json = json.dumps(still_failing)
                entry.resume_after = now + datetime.timedelta(
                    seconds=self._settings.cooldown_seconds
                )
            else:
                await session.delete(entry)
                row = await self._get_row(session, gateway, sandbox)
                if row is not None:
                    row.window_started_at = now
                    row.window_count_start = counts.get((gateway, sandbox), 0)
            await session.commit()

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    async def _get_row(
        session: AsyncSession, gateway: str, sandbox: str
    ) -> SandboxRateLimit | None:
        """Fetch the rate-limit row for a sandbox.

        Args:
            session: Open async session.
            gateway: Gateway name.
            sandbox: Sandbox name.

        Returns:
            SandboxRateLimit | None: The row, or ``None``.
        """
        return (
            (
                await session.execute(
                    select(SandboxRateLimit).where(
                        SandboxRateLimit.gateway == gateway,
                        SandboxRateLimit.sandbox == sandbox,
                    )
                )
            )
            .scalars()
            .first()
        )

    @staticmethod
    async def _get_pause(
        session: AsyncSession, gateway: str, sandbox: str
    ) -> RatePauseEntry | None:
        """Fetch the active rate-pause entry for a sandbox.

        Args:
            session: Open async session.
            gateway: Gateway name.
            sandbox: Sandbox name.

        Returns:
            RatePauseEntry | None: The entry, or ``None``.
        """
        return (
            (
                await session.execute(
                    select(RatePauseEntry).where(
                        RatePauseEntry.gateway == gateway, RatePauseEntry.sandbox == sandbox
                    )
                )
            )
            .scalars()
            .first()
        )

    @staticmethod
    async def _get_kill_switch(
        session: AsyncSession, gateway: str, sandbox: str
    ) -> KillSwitchEntry | None:
        """Fetch any kill-switch entry for a sandbox.

        Args:
            session: Open async session.
            gateway: Gateway name.
            sandbox: Sandbox name.

        Returns:
            KillSwitchEntry | None: The entry, or ``None``.
        """
        return (
            (
                await session.execute(
                    select(KillSwitchEntry).where(
                        KillSwitchEntry.gateway == gateway, KillSwitchEntry.sandbox == sandbox
                    )
                )
            )
            .scalars()
            .first()
        )

    @staticmethod
    def _limit_dict(row: SandboxRateLimit) -> dict[str, Any]:
        """Serialise a rate-limit row.

        Args:
            row: The rate-limit ORM row.

        Returns:
            dict[str, Any]: JSON-serialisable rate-limit record.
        """
        return {
            "gateway": row.gateway,
            "sandbox": row.sandbox,
            "max_requests": row.max_requests,
            "window_seconds": row.window_seconds,
            "enabled": row.enabled,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
