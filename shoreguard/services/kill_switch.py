"""Reversible gateway kill switch — cut every agent off, instantly.

``engage`` detaches all providers from every sandbox on a gateway:
agents keep their state but lose inference and tool credentials at the
L7 proxy (the OpenShell credential broker no longer injects anything).
The detached set is persisted per sandbox so ``resume`` can re-attach
exactly what was cut. This is the "big red button" homelab operators
ask for — stronger than pausing notifications, weaker (and safer) than
deleting sandboxes.

Partial failures are recorded, not raised: cutting nine of ten
sandboxes is strictly better than cutting none, so engage/resume return
a report instead of aborting on the first RPC error.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from shoreguard.models import KillSwitchEntry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from shoreguard.services.gateway import GatewayService

logger = logging.getLogger(__name__)


class KillSwitchService:
    """Engage/resume the per-gateway provider kill switch.

    Args:
        session_factory: Async SQLAlchemy session factory for the
            persisted kill-switch state.
        gateway_service: Live-connection service used to reach gateways.
    """

    def __init__(  # noqa: D107
        self,
        session_factory: async_sessionmaker[AsyncSession],
        gateway_service: GatewayService,
    ) -> None:
        self._session_factory = session_factory
        self._gateways = gateway_service

    async def status(self, gateway: str) -> dict[str, Any]:
        """Return whether the kill switch is engaged for a gateway.

        Args:
            gateway: Gateway name.

        Returns:
            dict[str, Any]: ``{"engaged", "sandboxes", "engaged_at",
                "engaged_by"}``.
        """
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(KillSwitchEntry).where(KillSwitchEntry.gateway == gateway)
                    )
                )
                .scalars()
                .all()
            )
        if not rows:
            return {"engaged": False, "sandboxes": 0, "engaged_at": None, "engaged_by": None}
        return {
            "engaged": True,
            "sandboxes": len(rows),
            "engaged_at": min(r.engaged_at for r in rows).isoformat(),
            "engaged_by": rows[0].engaged_by,
        }

    async def engage(self, gateway: str, *, actor: str) -> dict[str, Any]:
        """Detach all providers from every sandbox on a gateway.

        Already-engaged gateways are not re-engaged (idempotent): the stored
        state is the source of truth for ``resume`` and must not be
        overwritten with the now-empty attachment list.

        Args:
            gateway: Gateway name.
            actor: Who pressed the button (for the audit trail).

        Returns:
            dict[str, Any]: Report with per-sandbox detached provider counts
                and any errors.

        Raises:
            RuntimeError: If the kill switch is already engaged.
        """
        current = await self.status(gateway)
        if current["engaged"]:
            raise RuntimeError(f"Kill switch already engaged for gateway '{gateway}'")

        client = await self._gateways.get_client(gateway)
        sandboxes = await client.sandboxes.list(limit=1000)
        now = datetime.datetime.now(datetime.UTC)
        report: dict[str, Any] = {"gateway": gateway, "sandboxes": [], "errors": []}

        async with self._session_factory() as session:
            for sb in sandboxes:
                name = sb.get("name") or sb.get("sandbox_name")
                if not name:
                    continue
                try:
                    providers = await client.sandboxes.list_providers(name)
                    provider_names: list[str] = []
                    for p in providers:
                        pname = p.get("name") or p.get("provider_name")
                        if isinstance(pname, str) and pname:
                            provider_names.append(pname)
                    detached: list[str] = []
                    for provider in provider_names:
                        try:
                            await client.sandboxes.detach_provider(name, provider)
                            detached.append(provider)
                        except Exception as exc:  # noqa: BLE001 — keep cutting the rest
                            report["errors"].append(f"{name}/{provider}: {exc}")
                    session.add(
                        KillSwitchEntry(
                            gateway=gateway,
                            sandbox=name,
                            providers_json=json.dumps(detached),
                            engaged_at=now,
                            engaged_by=actor,
                        )
                    )
                    report["sandboxes"].append({"name": name, "detached": len(detached)})
                except Exception as exc:  # noqa: BLE001 — keep cutting the rest
                    report["errors"].append(f"{name}: {exc}")
            await session.commit()

        logger.warning(
            "Kill switch ENGAGED (gateway=%s, sandboxes=%d, errors=%d, actor=%s)",
            gateway,
            len(report["sandboxes"]),
            len(report["errors"]),
            actor,
        )
        return report

    async def resume(self, gateway: str, *, actor: str) -> dict[str, Any]:
        """Re-attach the providers stored at engage time and clear the state.

        Sandboxes whose re-attach fails keep their entry so a later resume
        can retry them.

        Args:
            gateway: Gateway name.
            actor: Who released the switch.

        Returns:
            dict[str, Any]: Report with re-attached counts and any errors.
        """
        client = await self._gateways.get_client(gateway)
        report: dict[str, Any] = {"gateway": gateway, "sandboxes": [], "errors": []}

        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(KillSwitchEntry).where(KillSwitchEntry.gateway == gateway)
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                try:
                    providers = json.loads(row.providers_json)
                except json.JSONDecodeError:
                    providers = []
                still_failing: list[str] = []
                attached = 0
                for provider in providers:
                    try:
                        await client.sandboxes.attach_provider(row.sandbox, provider)
                        attached += 1
                    except Exception as exc:  # noqa: BLE001 — record and continue
                        still_failing.append(provider)
                        report["errors"].append(f"{row.sandbox}/{provider}: {exc}")
                if still_failing:
                    # Keep the entry, but only for the providers that are
                    # still detached — a retry must not double-attach the
                    # ones that already succeeded.
                    row.providers_json = json.dumps(still_failing)
                else:
                    await session.delete(row)
                report["sandboxes"].append({"name": row.sandbox, "attached": attached})
            await session.commit()

        logger.warning(
            "Kill switch released (gateway=%s, sandboxes=%d, errors=%d, actor=%s)",
            gateway,
            len(report["sandboxes"]),
            len(report["errors"]),
            actor,
        )
        return report
