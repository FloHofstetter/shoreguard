"""Daily digest — "what did my agents do while I slept?".

Aggregates the last N hours of management-plane state into one compact
report: audit activity by action, sandbox churn, approval activity,
gateway health, webhook delivery failures, and engaged kill switches.
Rendered on demand by ``GET /api/digest`` (dashboard card) and pushed
once a day as a ``digest.daily`` webhook event by the background task —
the morning report that plays to an always-on box's strength.

"Sent today" state is persisted as an audit entry (action
``digest.sent``), so a restart does not re-send the digest and no extra
table is needed.
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from shoreguard.models import AuditEntry, KillSwitchEntry, WebhookDelivery

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from shoreguard.services.budgets import BudgetService
    from shoreguard.services.registry import GatewayRegistry

logger = logging.getLogger(__name__)

DIGEST_SENT_ACTION = "digest.sent"


class DigestService:
    """Build and dispatch the daily activity digest.

    Args:
        session_factory: Async SQLAlchemy session factory for the
            aggregate queries.
        registry: Gateway registry for current health states.
        budget: Optional budget service used to add today's inference spend to
            the digest. When ``None`` the spend section is omitted.
    """

    def __init__(  # noqa: D107
        self,
        session_factory: async_sessionmaker[AsyncSession],
        registry: GatewayRegistry,
        budget: BudgetService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry
        self._budget = budget

    async def build(self, *, hours: int = 24, scope: set[str] | None = None) -> dict[str, Any]:
        """Build the digest for the trailing time window.

        Args:
            hours: Window size in hours (default: last 24).
            scope: When provided (a tenant user's gateway set), restrict the
                gateway-attributed sections to these gateways while keeping
                cross-cutting unattributed (NULL-gateway) audit events. The
                pushed daily digest passes ``None`` and stays fleet-wide.

        Returns:
            dict[str, Any]: The digest payload (JSON-serialisable).
        """
        until = datetime.datetime.now(datetime.UTC)
        since = until - datetime.timedelta(hours=hours)

        async with self._session_factory() as session:
            action_stmt = (
                select(AuditEntry.action, func.count())
                .where(AuditEntry.timestamp >= since)
                .group_by(AuditEntry.action)
            )
            if scope is not None:
                # Keep cross-cutting unattributed events (auth, user/tenant
                # CRUD) visible alongside the scoped gateways' events.
                action_stmt = action_stmt.where(
                    AuditEntry.gateway_name.in_(scope) | AuditEntry.gateway_name.is_(None)
                )
            action_rows = (await session.execute(action_stmt)).all()
            by_action: dict[str, int] = {str(a): int(c) for a, c in action_rows}
            webhook_failures = (
                await session.execute(
                    select(func.count())
                    .select_from(WebhookDelivery)
                    .where(
                        WebhookDelivery.created_at >= since,
                        WebhookDelivery.status == "failed",
                    )
                )
            ).scalar_one()
            engaged_stmt = select(KillSwitchEntry.gateway).group_by(KillSwitchEntry.gateway)
            if scope is not None:
                engaged_stmt = engaged_stmt.where(KillSwitchEntry.gateway.in_(scope))
            engaged = (await session.execute(engaged_stmt)).scalars().all()

        try:
            gateways = await self._registry.list_all()
        except Exception:  # noqa: BLE001 — digest must render even if the DB hiccups
            gateways = []
        if scope is not None:
            gateways = [gw for gw in gateways if gw["name"] in scope]
        unreachable = [
            gw["name"] for gw in gateways if gw.get("last_status") in ("unreachable", "offline")
        ]

        total_events = sum(by_action.values())
        digest = {
            "since": since.isoformat(),
            "until": until.isoformat(),
            "window_hours": hours,
            "audit": {
                "total": total_events,
                "by_action": dict(
                    sorted(by_action.items(), key=lambda kv: kv[1], reverse=True)[:15]
                ),
                "forbidden": by_action.get("auth.forbidden", 0),
            },
            "sandboxes": {
                "created": by_action.get("sandbox.create", 0),
                "deleted": by_action.get("sandbox.delete", 0),
            },
            "approvals": {
                "approved": by_action.get("approval.approve", 0),
                "rejected": by_action.get("approval.reject", 0),
                "votes": by_action.get("approval.vote_cast", 0),
            },
            "gateways": {
                "total": len(gateways),
                "unreachable": unreachable,
            },
            "webhook_failures": int(webhook_failures),
            "kill_switch_engaged": list(engaged),
        }
        digest["spending"] = await self._spending(scope)
        digest["message"] = self._summary_line(digest)
        return digest

    async def _spending(self, scope: set[str] | None = None) -> dict[str, Any]:
        """Summarise today's inference spend across all gateways.

        Args:
            scope: When provided, restrict the spend rollup to these gateways.

        Returns:
            dict[str, Any]: ``{"today_total": int, "today_total_cost": float,
                "currency_label": str, "top": [...]}`` — empty when no budget
                service is wired or the query fails (best-effort).
        """
        empty = {"today_total": 0, "today_total_cost": 0.0, "currency_label": "", "top": []}
        if self._budget is None:
            return empty
        try:
            summary = await self._budget.summary(days=1)
        except Exception:  # noqa: BLE001 — spend is best-effort; never break the digest
            return empty
        top = summary.get("top", [])
        if scope is not None:
            top = [t for t in top if t.get("gateway") in scope]
        return {
            "today_total": sum(int(t.get("requests", 0)) for t in top),
            "today_total_cost": round(sum(float(t.get("estimated_cost", 0.0)) for t in top), 6),
            "currency_label": summary.get("currency_label", ""),
            "top": top[:5],
        }

    @staticmethod
    def _summary_line(digest: dict[str, Any]) -> str:
        """Render the one-line human summary used by notification channels.

        Args:
            digest: The digest payload built by :meth:`build`.

        Returns:
            str: Compact human-readable summary.
        """
        parts = [
            f"{digest['audit']['total']} actions",
            f"{digest['sandboxes']['created']} sandboxes created",
            f"{digest['approvals']['approved']} approvals",
        ]
        spend = digest.get("spending") or {}
        if spend.get("today_total"):
            line = f"{spend['today_total']} inference requests"
            if spend.get("today_total_cost"):
                line += f" (est. ${spend['today_total_cost']:.2f})"
            parts.append(line)
        if digest["gateways"]["unreachable"]:
            parts.append(f"⚠ unreachable: {', '.join(digest['gateways']['unreachable'])}")
        if digest["kill_switch_engaged"]:
            parts.append(f"⚠ kill switch on: {', '.join(digest['kill_switch_engaged'])}")
        if digest["webhook_failures"]:
            parts.append(f"{digest['webhook_failures']} webhook failures")
        if digest["audit"]["forbidden"]:
            parts.append(f"{digest['audit']['forbidden']} forbidden attempts")
        return " · ".join(parts)

    async def sent_since(self, cutoff: datetime.datetime) -> bool:
        """Return whether a digest was already dispatched after *cutoff*.

        Args:
            cutoff: Lower bound timestamp (usually local midnight).

        Returns:
            bool: True if a ``digest.sent`` audit entry exists after cutoff.
        """
        async with self._session_factory() as session:
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(AuditEntry)
                    .where(
                        AuditEntry.action == DIGEST_SENT_ACTION,
                        AuditEntry.timestamp >= cutoff,
                    )
                )
            ).scalar_one()
        return count > 0

    async def dispatch_if_due(self, *, hour: int, audit: Any) -> bool:
        """Send the daily digest once per day after the configured hour.

        Called periodically by the background task; cheap when not due.

        Args:
            hour: Local hour of day (0-23) after which the digest is due.
            audit: The audit service used to persist the sent marker.

        Returns:
            bool: True if a digest was dispatched on this call.
        """
        now_local = datetime.datetime.now().astimezone()
        if now_local.hour < hour:
            return False
        midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        if await self.sent_since(midnight.astimezone(datetime.UTC)):
            return False

        digest = await self.build()
        from shoreguard.services.webhooks import fire_webhook

        await fire_webhook("digest.daily", digest)
        await audit.log(
            actor="system",
            actor_role="system",
            action=DIGEST_SENT_ACTION,
            resource_type="digest",
            detail={"total": digest["audit"]["total"]},
        )
        logger.info("Daily digest dispatched: %s", digest["message"])
        return True
