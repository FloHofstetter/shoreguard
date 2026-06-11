"""Per-sandbox activity timeline — "what did my agent do last night?".

The digest answers that question in aggregate; the timeline answers it
for one sandbox, stitched chronologically from the data ShoreGuard
already holds: audit entries, approval decisions, kill-switch
engagements, and metered usage. Pure DB reads — no gateway RPC — so it
works even while the gateway is down (which is exactly when you want
to reconstruct what happened).
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from shoreguard.models import ApprovalDecision, AuditEntry, KillSwitchEntry, SandboxUsage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


class TimelineService:
    """Merges per-sandbox events from all local stores into one timeline.

    Args:
        session_factory: Async SQLAlchemy session factory.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:  # noqa: D107
        self._session_factory = session_factory

    async def for_sandbox(
        self, gateway: str, sandbox: str, *, hours: int = 24, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Build the merged timeline for one sandbox, newest first.

        Args:
            gateway: Gateway name.
            sandbox: Sandbox name.
            hours: Look-back window in hours.
            limit: Maximum number of entries returned.

        Returns:
            list[dict[str, Any]]: Entries with ``ts`` (ISO-8601),
            ``kind`` (``audit`` / ``approval`` / ``kill_switch`` /
            ``usage``), ``title``, and ``detail``.
        """
        since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=hours)
        since_naive = since.replace(tzinfo=None)
        items: list[dict[str, Any]] = []

        async with self._session_factory() as session:
            audit_rows = (
                (
                    await session.execute(
                        select(AuditEntry)
                        .where(
                            AuditEntry.gateway_name == gateway,
                            AuditEntry.resource_id == sandbox,
                            AuditEntry.timestamp >= since_naive,
                        )
                        .order_by(AuditEntry.timestamp.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            for row in audit_rows:
                detail = ""
                if row.detail:
                    try:
                        parsed = json.loads(row.detail)
                        detail = ", ".join(f"{k}={v}" for k, v in list(parsed.items())[:4])
                    except TypeError, ValueError:
                        detail = row.detail[:120]
                items.append(
                    {
                        "ts": self._iso(row.timestamp),
                        "kind": "audit",
                        "title": row.action,
                        "detail": f"by {row.actor}" + (f" — {detail}" if detail else ""),
                    }
                )

            approval_rows = (
                (
                    await session.execute(
                        select(ApprovalDecision)
                        .where(
                            ApprovalDecision.gateway_name == gateway,
                            ApprovalDecision.sandbox_name == sandbox,
                            ApprovalDecision.created_at >= since_naive,
                        )
                        .order_by(ApprovalDecision.created_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            for row in approval_rows:
                items.append(
                    {
                        "ts": self._iso(row.created_at),
                        "kind": "approval",
                        "title": f"rule {row.decision}",
                        "detail": f"chunk {row.chunk_id} by {row.actor}"
                        + (f" — {row.comment}" if row.comment else ""),
                    }
                )

            ks_rows = (
                (
                    await session.execute(
                        select(KillSwitchEntry).where(
                            KillSwitchEntry.gateway == gateway,
                            KillSwitchEntry.sandbox == sandbox,
                            KillSwitchEntry.engaged_at >= since_naive,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in ks_rows:
                try:
                    count = len(json.loads(row.providers_json))
                except TypeError, ValueError:
                    count = 0
                items.append(
                    {
                        "ts": self._iso(row.engaged_at),
                        "kind": "kill_switch",
                        "title": "providers detached",
                        "detail": f"{count} provider(s) cut by {row.engaged_by}",
                    }
                )

            first_day = since.date().isoformat()
            usage_rows = (
                (
                    await session.execute(
                        select(SandboxUsage).where(
                            SandboxUsage.gateway == gateway,
                            SandboxUsage.sandbox == sandbox,
                            SandboxUsage.day >= first_day,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in usage_rows:
                items.append(
                    {
                        "ts": f"{row.day}T00:00:00+00:00",
                        "kind": "usage",
                        "title": f"{row.requests} inference request(s)",
                        "detail": f"metered on {row.day}",
                    }
                )

        items.sort(key=lambda item: item["ts"], reverse=True)
        return items[:limit]

    @staticmethod
    def _iso(value: datetime.datetime | None) -> str:
        """Normalise a DB timestamp to a sortable UTC ISO-8601 string.

        Args:
            value: The timestamp (naive values are assumed UTC).

        Returns:
            str: ISO-8601 string with ``+00:00`` offset.
        """
        if value is None:
            return "1970-01-01T00:00:00+00:00"
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.UTC)
        return value.astimezone(datetime.UTC).isoformat()
