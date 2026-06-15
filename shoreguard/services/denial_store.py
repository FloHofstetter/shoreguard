"""Durable denial-sample corpus for policy-simulation replay.

The live denial cache (:mod:`shoreguard.services.denial_context`) is
in-memory and lost on restart. This store persists the same inbound
``SubmitPolicyAnalysis`` summaries so the policy simulator can replay them
against a candidate policy. It is gated by ``settings.simulator.replay_enabled``
at the call site (in ``PolicyService.submit_analysis``).
"""

from __future__ import annotations

import datetime
import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from shoreguard.models import DenialSample

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class DenialSampleStore:
    """Persist and retrieve L7 denial samples for replay.

    Args:
        session_factory: Async SQLAlchemy session factory.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:  # noqa: D107
        self._session_factory = session_factory

    async def persist_summaries(
        self, gateway: str, sandbox: str, summaries: list[dict[str, Any]]
    ) -> int:
        """Upsert denial summaries into the durable corpus.

        Args:
            gateway: Gateway name.
            sandbox: Sandbox name.
            summaries: Raw ``DenialSummary`` dicts from ``SubmitPolicyAnalysis``.

        Returns:
            int: Number of summaries persisted (upserted).
        """
        now = datetime.datetime.now(datetime.UTC)
        persisted = 0
        async with self._session_factory() as session:
            for s in summaries:
                host = str(s.get("host") or "").strip().lower().rstrip(".")
                if not host:
                    continue
                binary = str(s.get("binary") or "")
                port = int(s.get("port") or 0)
                l7 = [
                    {"method": str(x.get("method") or ""), "path": str(x.get("path") or "")}
                    for x in (s.get("l7_request_samples") or [])
                    if isinstance(x, dict)
                ]
                row = (
                    (
                        await session.execute(
                            select(DenialSample).where(
                                DenialSample.gateway == gateway,
                                DenialSample.sandbox == sandbox,
                                DenialSample.binary == binary,
                                DenialSample.host == host,
                                DenialSample.port == port,
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if row is None:
                    session.add(
                        DenialSample(
                            gateway=gateway,
                            sandbox=sandbox,
                            binary=binary,
                            host=host,
                            port=port,
                            l7_samples_json=json.dumps(l7),
                            deny_reason=str(s.get("deny_reason") or ""),
                            count=int(s.get("count") or 0),
                            created_at=now,
                        )
                    )
                else:
                    row.l7_samples_json = json.dumps(l7)
                    row.deny_reason = str(s.get("deny_reason") or "")
                    row.count = int(s.get("count") or 0)
                    row.created_at = now
                persisted += 1
            await session.commit()
        return persisted

    async def list_for_sandbox(
        self, gateway: str, sandbox: str, *, limit: int = 500
    ) -> list[dict[str, Any]]:
        """Return persisted denial samples for a sandbox, newest first.

        Args:
            gateway: Gateway name.
            sandbox: Sandbox name.
            limit: Maximum number of samples.

        Returns:
            list[dict[str, Any]]: ``{binary, host, port, deny_reason, l7}`` rows.
        """
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(DenialSample)
                        .where(DenialSample.gateway == gateway, DenialSample.sandbox == sandbox)
                        .order_by(DenialSample.created_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                l7 = json.loads(row.l7_samples_json)
            except json.JSONDecodeError:
                l7 = []
            result.append(
                {
                    "binary": row.binary,
                    "host": row.host,
                    "port": row.port,
                    "deny_reason": row.deny_reason,
                    "l7": l7,
                }
            )
        return result

    async def prune(self, retention_days: int) -> int:
        """Delete denial samples older than the retention window.

        Args:
            retention_days: Age threshold in days.

        Returns:
            int: Always ``0`` (count not tracked) — kept for the cleanup hook.
        """
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=retention_days)
        async with self._session_factory() as session:
            await session.execute(delete(DenialSample).where(DenialSample.created_at < cutoff))
            await session.commit()
        return 0
