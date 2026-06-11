"""Agent curfew — quiet hours that auto-engage the kill switch.

The overnight runaway ("the agent burned $47 while I slept") is the
single most-cited homelab incident. A curfew closes that gap without
deleting anything: inside the configured window the reversible kill
switch is engaged (providers detached, state kept), outside it the
curfew-engaged switch is released and agents pick up where they left
off. A switch engaged by a human or by budget enforcement is never
touched — the curfew only manages what it engaged itself.
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from shoreguard.models import GatewayCurfew

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from shoreguard.services.kill_switch import KillSwitchService

logger = logging.getLogger(__name__)

CURFEW_ACTOR = "curfew"


def minute_in_window(minute: int, start: int, end: int) -> bool:
    """Return whether a minute-of-day falls inside a (possibly wrapping) window.

    Args:
        minute: Minutes after midnight (0–1439).
        start: Window start minute.
        end: Window end minute. ``start > end`` wraps past midnight;
            ``start == end`` means the window is empty.

    Returns:
        bool: ``True`` when inside the window.
    """
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


class CurfewService:
    """Stores curfews and drives the kill switch on window transitions.

    Args:
        session_factory: Async SQLAlchemy session factory.
        kill_switch: The kill-switch service used to engage/release.
    """

    def __init__(  # noqa: D107
        self,
        session_factory: async_sessionmaker[AsyncSession],
        kill_switch: KillSwitchService,
    ) -> None:
        self._session_factory = session_factory
        self._kill_switch = kill_switch
        # Last successfully handled window state per gateway, so each
        # transition acts (and notifies) exactly once. A zero-sandbox
        # engage leaves no kill-switch rows, so raw status alone would
        # re-fire every tick. Updated only after a successful pass;
        # failures retry on the next tick.
        self._last_in_window: dict[str, bool] = {}

    # ─── CRUD ────────────────────────────────────────────────────────────────

    async def get(self, gateway: str) -> dict[str, Any] | None:
        """Return the curfew configured for a gateway, if any.

        Args:
            gateway: Gateway name.

        Returns:
            dict[str, Any] | None: The curfew record, or ``None``.
        """
        async with self._session_factory() as session:
            row = (
                await session.execute(select(GatewayCurfew).where(GatewayCurfew.gateway == gateway))
            ).scalar_one_or_none()
            return self._to_dict(row) if row else None

    async def set(
        self,
        gateway: str,
        *,
        enabled: bool,
        start_minute: int,
        end_minute: int,
        timezone: str = "UTC",
    ) -> dict[str, Any]:
        """Create or update a gateway's curfew.

        Args:
            gateway: Gateway name.
            enabled: Whether the curfew is active.
            start_minute: Window start (minutes after local midnight).
            end_minute: Window end; smaller than start wraps overnight.
            timezone: IANA timezone for window evaluation.

        Returns:
            dict[str, Any]: The stored curfew record.

        Raises:
            ValueError: If the timezone is unknown or minutes are out of
                range.
        """
        if not 0 <= start_minute <= 1439 or not 0 <= end_minute <= 1439:
            raise ValueError("start_minute/end_minute must be within 0–1439")
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as e:
            raise ValueError(f"Unknown timezone: {timezone}") from e

        now = datetime.datetime.now(datetime.UTC)
        async with self._session_factory() as session:
            row = (
                await session.execute(select(GatewayCurfew).where(GatewayCurfew.gateway == gateway))
            ).scalar_one_or_none()
            if row is None:
                row = GatewayCurfew(
                    gateway=gateway,
                    enabled=enabled,
                    start_minute=start_minute,
                    end_minute=end_minute,
                    timezone=timezone,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.enabled = enabled
                row.start_minute = start_minute
                row.end_minute = end_minute
                row.timezone = timezone
                row.updated_at = now
            await session.commit()
            return self._to_dict(row)

    async def delete(self, gateway: str) -> bool:
        """Remove a gateway's curfew (does not release the switch).

        Args:
            gateway: Gateway name.

        Returns:
            bool: ``True`` if a curfew was removed.
        """
        async with self._session_factory() as session:
            row = (
                await session.execute(select(GatewayCurfew).where(GatewayCurfew.gateway == gateway))
            ).scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    @staticmethod
    def _to_dict(row: GatewayCurfew) -> dict[str, Any]:
        """Serialize a curfew row.

        Args:
            row: The ORM row.

        Returns:
            dict[str, Any]: API-shaped record.
        """
        return {
            "gateway": row.gateway,
            "enabled": row.enabled,
            "start_minute": row.start_minute,
            "end_minute": row.end_minute,
            "timezone": row.timezone,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    # ─── Evaluation ──────────────────────────────────────────────────────────

    async def run_once(self) -> list[dict[str, Any]]:
        """Evaluate every enabled curfew and engage/release as needed.

        Returns:
            list[dict[str, Any]]: Actions taken this pass (one entry per
            transition; steady states produce nothing).
        """
        from shoreguard.services.webhooks import fire_webhook

        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(GatewayCurfew).where(GatewayCurfew.enabled.is_(True))
                    )
                )
                .scalars()
                .all()
            )
            curfews = [self._to_dict(r) for r in rows]

        actions: list[dict[str, Any]] = []
        for curfew in curfews:
            gateway = curfew["gateway"]
            try:
                tz = ZoneInfo(curfew["timezone"])
            except ZoneInfoNotFoundError:
                logger.warning(
                    "Curfew for %s has unknown timezone %r — skipping",
                    gateway,
                    curfew["timezone"],
                )
                continue
            local = datetime.datetime.now(tz)
            in_window = minute_in_window(
                local.hour * 60 + local.minute, curfew["start_minute"], curfew["end_minute"]
            )
            if self._last_in_window.get(gateway) == in_window:
                continue
            try:
                status = await self._kill_switch.status(gateway)
                if in_window:
                    if not status["engaged"]:
                        report = await self._kill_switch.engage(gateway, actor=CURFEW_ACTOR)
                        actions.append({"gateway": gateway, "action": "engaged"})
                        await fire_webhook(
                            "kill_switch.engaged",
                            {
                                "gateway": gateway,
                                "actor": CURFEW_ACTOR,
                                "sandboxes": len(report["sandboxes"]),
                            },
                        )
                    self._last_in_window[gateway] = True
                else:
                    if status["engaged"] and status["engaged_by"] == CURFEW_ACTOR:
                        report = await self._kill_switch.resume(gateway, actor=CURFEW_ACTOR)
                        # A partial resume keeps providers detached;
                        # retry next tick instead of announcing a
                        # release that did not (fully) happen.
                        after = await self._kill_switch.status(gateway)
                        if after["engaged"]:
                            logger.warning(
                                "Curfew release for %s incomplete — retrying next tick",
                                gateway,
                            )
                            continue
                        actions.append({"gateway": gateway, "action": "released"})
                        await fire_webhook(
                            "kill_switch.released",
                            {
                                "gateway": gateway,
                                "actor": CURFEW_ACTOR,
                                "sandboxes": len(report["sandboxes"]),
                            },
                        )
                    self._last_in_window[gateway] = False
            except Exception as exc:  # noqa: BLE001 — one gateway must not block the rest
                logger.warning("Curfew evaluation failed for %s: %s", gateway, exc)
        return actions
