"""Inference spend metering and per-sandbox budgets (phase 1).

OpenShell's gRPC surface has no usage RPC yet, so metering is
approximated ShoreGuard-side: the ``usage_metering`` background task
polls each running sandbox's gateway logs with a persisted cursor and
counts inference-proxy lines (source/target matching is configurable
via ``SHOREGUARD_BUDGET_INFERENCE_SOURCES``). Counts land in per-day
rows; budgets compare the window sum against a request ceiling.

At the limit a budget either fires a ``budget.exceeded`` webhook
(action ``notify``, once per window) or **detaches the sandbox's
providers** (action ``detach``) — recorded as kill-switch entries with
``engaged_by="budget"`` so the existing kill-switch resume path
re-attaches them.

Known phase-1 limits, by design: counting starts at budget/metering
activation (history is never billed), a poll fetches at most
``log_batch_lines`` lines per cycle (extremely chatty sandboxes can
undercount), and "requests" are proxy log lines, not tokens. The
upstream metering RPC replaces this when it lands.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from shoreguard.models import KillSwitchEntry, SandboxBudget, SandboxUsage, UsageCursor

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from shoreguard.services.gateway import GatewayService
    from shoreguard.services.registry import GatewayRegistry
    from shoreguard.settings import BudgetSettings

logger = logging.getLogger(__name__)

WINDOWS = ("daily", "weekly", "monthly", "total")
ACTIONS = ("notify", "detach")

_WINDOW_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}


def _today() -> str:
    """Return the current UTC day as ``YYYY-MM-DD``.

    Returns:
        str: UTC day string.
    """
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")


def _window_start(window: str) -> str | None:
    """Return the first UTC day inside a budget window, or None for total.

    Args:
        window: One of ``daily``, ``weekly``, ``monthly``, ``total``.

    Returns:
        str | None: ``YYYY-MM-DD`` lower bound, or ``None`` (no bound).
    """
    days = _WINDOW_DAYS.get(window)
    if days is None:
        return None
    start = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days - 1)
    return start.strftime("%Y-%m-%d")


class BudgetService:
    """Meter inference usage from gateway logs and enforce budgets.

    Args:
        session_factory: Async SQLAlchemy session factory.
        gateway_service: Live-connection service used to reach gateways.
        registry: Gateway registry (which gateways to meter).
        settings: Active ``BudgetSettings``.
    """

    def __init__(  # noqa: D107
        self,
        session_factory: async_sessionmaker[AsyncSession],
        gateway_service: GatewayService,
        registry: GatewayRegistry,
        settings: BudgetSettings,
    ) -> None:
        self._session_factory = session_factory
        self._gateways = gateway_service
        self._registry = registry
        self._settings = settings

    # ── budget CRUD ────────────────────────────────────────────────────────

    async def get_budget(self, gateway: str, sandbox: str) -> dict[str, Any] | None:
        """Return the budget for a sandbox, or None.

        Args:
            gateway: Gateway name.
            sandbox: Sandbox name.

        Returns:
            dict[str, Any] | None: Budget record or ``None``.
        """
        async with self._session_factory() as session:
            row = await self._get_budget_row(session, gateway, sandbox)
            return self._budget_dict(row) if row else None

    async def set_budget(
        self, gateway: str, sandbox: str, *, limit_requests: int, window: str, action: str
    ) -> dict[str, Any]:
        """Create or update a sandbox budget.

        Args:
            gateway: Gateway name.
            sandbox: Sandbox name.
            limit_requests: Inference request ceiling (>= 1).
            window: ``daily`` / ``weekly`` / ``monthly`` / ``total``.
            action: ``notify`` or ``detach``.

        Returns:
            dict[str, Any]: The saved budget record.

        Raises:
            ValueError: On invalid window, action, or limit.
        """
        if window not in WINDOWS:
            raise ValueError(f"Invalid window: {window!r}")
        if action not in ACTIONS:
            raise ValueError(f"Invalid action: {action!r}")
        if limit_requests < 1:
            raise ValueError("limit_requests must be >= 1")
        now = datetime.datetime.now(datetime.UTC)
        async with self._session_factory() as session:
            row = await self._get_budget_row(session, gateway, sandbox)
            if row is None:
                row = SandboxBudget(
                    gateway=gateway,
                    sandbox=sandbox,
                    limit_requests=limit_requests,
                    window=window,
                    action=action,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.limit_requests = limit_requests
                row.window = window
                row.action = action
                row.notified_key = None
                row.updated_at = now
            await session.commit()
            return self._budget_dict(row)

    async def delete_budget(self, gateway: str, sandbox: str) -> bool:
        """Remove the budget for a sandbox.

        Args:
            gateway: Gateway name.
            sandbox: Sandbox name.

        Returns:
            bool: True when a budget existed and was removed.
        """
        async with self._session_factory() as session:
            row = await self._get_budget_row(session, gateway, sandbox)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    # ── usage queries ──────────────────────────────────────────────────────

    async def usage(self, gateway: str, sandbox: str, *, days: int = 7) -> dict[str, Any]:
        """Return per-day usage plus the active budget's window status.

        Args:
            gateway: Gateway name.
            sandbox: Sandbox name.
            days: How many trailing days of per-day rows to include.

        Returns:
            dict[str, Any]: ``{"days": [...], "today", "budget",
                "window_used"}``.
        """
        start = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days - 1)).strftime(
            "%Y-%m-%d"
        )
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(SandboxUsage)
                        .where(
                            SandboxUsage.gateway == gateway,
                            SandboxUsage.sandbox == sandbox,
                            SandboxUsage.day >= start,
                        )
                        .order_by(SandboxUsage.day)
                    )
                )
                .scalars()
                .all()
            )
            budget_row = await self._get_budget_row(session, gateway, sandbox)
            window_used = 0
            if budget_row is not None:
                window_used = await self._window_usage(session, gateway, sandbox, budget_row.window)
        today = _today()
        return {
            "days": [{"day": r.day, "requests": r.requests} for r in rows],
            "today": next((r.requests for r in rows if r.day == today), 0),
            "budget": self._budget_dict(budget_row) if budget_row else None,
            "window_used": window_used,
        }

    async def summary(self, *, days: int = 7, limit: int = 20) -> dict[str, Any]:
        """Return the top consumers across all gateways.

        Args:
            days: Trailing window for the totals.
            limit: Maximum number of sandboxes to return.

        Returns:
            dict[str, Any]: ``{"since", "top": [{gateway, sandbox,
                requests}]}``.
        """
        start = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days - 1)).strftime(
            "%Y-%m-%d"
        )
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        SandboxUsage.gateway,
                        SandboxUsage.sandbox,
                        func.sum(SandboxUsage.requests).label("total"),
                    )
                    .where(SandboxUsage.day >= start)
                    .group_by(SandboxUsage.gateway, SandboxUsage.sandbox)
                    .order_by(func.sum(SandboxUsage.requests).desc())
                    .limit(limit)
                )
            ).all()
        return {
            "since": start,
            "top": [{"gateway": g, "sandbox": s, "requests": int(t)} for g, s, t in rows],
        }

    # ── metering ───────────────────────────────────────────────────────────

    def _is_inference_line(self, line: dict[str, Any]) -> bool:
        """Decide whether a log line represents one inference request.

        Args:
            line: Log line dict from the gateway.

        Returns:
            bool: True when the line's source matches the configured
                inference sources or its target mentions inference.
        """
        source = str(line.get("source") or "").lower()
        target = str(line.get("target") or "").lower()
        if any(s in source for s in self._settings.inference_sources):
            return True
        return "inference" in target

    async def poll_once(self) -> dict[str, int]:
        """Poll every reachable gateway's sandboxes and update counters.

        Returns:
            dict[str, int]: ``{"sandboxes": polled, "counted": new
                requests}`` summary for logging/tests.
        """
        polled = 0
        counted = 0
        try:
            gateways = await self._registry.list_all()
        except SQLAlchemyError:
            logger.warning("metering: registry unavailable", exc_info=True)
            return {"sandboxes": 0, "counted": 0}
        for gw in gateways:
            name = gw["name"]
            if gw.get("last_status") in ("unreachable", "offline"):
                continue
            try:
                client = await self._gateways.get_client(name)
                sandboxes = await client.sandboxes.list(limit=1000)
            except Exception:  # noqa: BLE001 — skip unreachable gateways
                logger.debug("metering: gateway %s unavailable", name, exc_info=True)
                continue
            for sb in sandboxes:
                sb_name = sb.get("name") or sb.get("sandbox_name")
                if not sb_name:
                    continue
                try:
                    counted += await self._poll_sandbox(client, name, sb_name)
                    polled += 1
                except Exception:  # noqa: BLE001 — one sandbox must not stop the rest
                    logger.debug(
                        "metering: poll failed (gw=%s, sb=%s)", name, sb_name, exc_info=True
                    )
        await self._evaluate_budgets()
        return {"sandboxes": polled, "counted": counted}

    async def _poll_sandbox(self, client: Any, gateway: str, sandbox: str) -> int:
        """Count new inference lines for one sandbox and advance its cursor.

        Args:
            client: Connected gateway client.
            gateway: Gateway name.
            sandbox: Sandbox name.

        Returns:
            int: Newly counted inference requests.
        """
        now_ms = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
        async with self._session_factory() as session:
            cursor = (
                (
                    await session.execute(
                        select(UsageCursor).where(
                            UsageCursor.gateway == gateway, UsageCursor.sandbox == sandbox
                        )
                    )
                )
                .scalars()
                .first()
            )
            if cursor is None:
                # First sight of this sandbox: start metering from now —
                # never bill history.
                session.add(UsageCursor(gateway=gateway, sandbox=sandbox, last_ms=now_ms))
                await session.commit()
                return 0
            since_ms = cursor.last_ms

        logs = await client.sandboxes.get_logs(
            sandbox, lines=self._settings.log_batch_lines, since_ms=since_ms
        )
        if not logs:
            return 0
        count = sum(1 for line in logs if self._is_inference_line(line))
        max_ts = max(int(line.get("timestamp_ms") or since_ms) for line in logs)

        async with self._session_factory() as session:
            cursor = (
                (
                    await session.execute(
                        select(UsageCursor).where(
                            UsageCursor.gateway == gateway, UsageCursor.sandbox == sandbox
                        )
                    )
                )
                .scalars()
                .first()
            )
            if cursor is not None:
                cursor.last_ms = max(max_ts, since_ms) + 1
            if count:
                day = _today()
                usage_row = (
                    (
                        await session.execute(
                            select(SandboxUsage).where(
                                SandboxUsage.gateway == gateway,
                                SandboxUsage.sandbox == sandbox,
                                SandboxUsage.day == day,
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if usage_row is None:
                    session.add(
                        SandboxUsage(gateway=gateway, sandbox=sandbox, day=day, requests=count)
                    )
                else:
                    usage_row.requests += count
            await session.commit()
        return count

    # ── enforcement ────────────────────────────────────────────────────────

    async def _window_usage(
        self, session: AsyncSession, gateway: str, sandbox: str, window: str
    ) -> int:
        """Sum a sandbox's usage inside a budget window.

        Args:
            session: Open async session.
            gateway: Gateway name.
            sandbox: Sandbox name.
            window: Budget window name.

        Returns:
            int: Total requests inside the window.
        """
        stmt = select(func.coalesce(func.sum(SandboxUsage.requests), 0)).where(
            SandboxUsage.gateway == gateway, SandboxUsage.sandbox == sandbox
        )
        start = _window_start(window)
        if start is not None:
            stmt = stmt.where(SandboxUsage.day >= start)
        return int((await session.execute(stmt)).scalar_one())

    async def _evaluate_budgets(self) -> None:
        """Check every budget and fire its action when the limit is reached."""
        from shoreguard.services.webhooks import fire_webhook

        async with self._session_factory() as session:
            budgets = (await session.execute(select(SandboxBudget))).scalars().all()
            for budget in budgets:
                used = await self._window_usage(
                    session, budget.gateway, budget.sandbox, budget.window
                )
                if used < budget.limit_requests:
                    continue
                window_key = _window_start(budget.window) or "total"
                if budget.notified_key == window_key:
                    continue  # already acted in this window
                budget.notified_key = window_key
                await session.commit()
                payload = {
                    "gateway": budget.gateway,
                    "sandbox": budget.sandbox,
                    "used": used,
                    "limit": budget.limit_requests,
                    "window": budget.window,
                    "action": budget.action,
                }
                logger.warning(
                    "Budget exceeded (gw=%s, sb=%s, %d/%d %s, action=%s)",
                    budget.gateway,
                    budget.sandbox,
                    used,
                    budget.limit_requests,
                    budget.window,
                    budget.action,
                )
                if budget.action == "detach":
                    await self._detach_sandbox(budget.gateway, budget.sandbox)
                await fire_webhook("budget.exceeded", payload)

    async def _detach_sandbox(self, gateway: str, sandbox: str) -> None:
        """Detach one sandbox's providers, recorded as kill-switch entries.

        Uses ``engaged_by="budget"`` so the gateway kill-switch resume path
        re-attaches the providers when the operator decides to.

        Args:
            gateway: Gateway name.
            sandbox: Sandbox name.
        """
        async with self._session_factory() as session:
            existing = (
                (
                    await session.execute(
                        select(KillSwitchEntry).where(
                            KillSwitchEntry.gateway == gateway,
                            KillSwitchEntry.sandbox == sandbox,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if existing is not None:
                return  # already cut (kill switch or earlier budget action)
        try:
            client = await self._gateways.get_client(gateway)
            providers = await client.sandboxes.list_providers(sandbox)
        except Exception:  # noqa: BLE001 — enforcement is best effort
            logger.warning(
                "Budget detach: gateway %s unavailable for %s", gateway, sandbox, exc_info=True
            )
            return
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
                    "Budget detach failed (gw=%s, sb=%s, provider=%s)",
                    gateway,
                    sandbox,
                    pname,
                    exc_info=True,
                )
        async with self._session_factory() as session:
            session.add(
                KillSwitchEntry(
                    gateway=gateway,
                    sandbox=sandbox,
                    providers_json=json.dumps(detached),
                    engaged_at=datetime.datetime.now(datetime.UTC),
                    engaged_by="budget",
                )
            )
            await session.commit()

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    async def _get_budget_row(
        session: AsyncSession, gateway: str, sandbox: str
    ) -> SandboxBudget | None:
        """Fetch the budget row for a sandbox.

        Args:
            session: Open async session.
            gateway: Gateway name.
            sandbox: Sandbox name.

        Returns:
            SandboxBudget | None: The row, or ``None``.
        """
        return (
            (
                await session.execute(
                    select(SandboxBudget).where(
                        SandboxBudget.gateway == gateway, SandboxBudget.sandbox == sandbox
                    )
                )
            )
            .scalars()
            .first()
        )

    @staticmethod
    def _budget_dict(row: SandboxBudget) -> dict[str, Any]:
        """Serialise a budget row.

        Args:
            row: The budget ORM row.

        Returns:
            dict[str, Any]: JSON-serialisable budget record.
        """
        return {
            "gateway": row.gateway,
            "sandbox": row.sandbox,
            "limit_requests": row.limit_requests,
            "window": row.window,
            "action": row.action,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
