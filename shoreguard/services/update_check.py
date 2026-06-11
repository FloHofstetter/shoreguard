"""Opt-in update awareness for ShoreGuard and the managed gateways.

Homelab culture is Watchtower-shaped: boxes are expected to tell you
when they are stale. When enabled, a daily task asks PyPI for the
latest ShoreGuard release and fires a one-shot
``shoreguard.update_available`` webhook event per new version; the
dashboard shows a banner. Off by default — no phone-home without
opt-in.

Gateway version *skew* needs no network at all: every health probe
already reports the OpenShell version, so divergence across gateways is
computed locally.
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, Any

import httpx

from shoreguard import __version__

if TYPE_CHECKING:
    from shoreguard.services.gateway import GatewayService
    from shoreguard.settings import UpdateSettings

logger = logging.getLogger(__name__)


def _is_newer(latest: str, current: str) -> bool:
    """Return whether *latest* is a newer release than *current*.

    Args:
        latest: Candidate version string.
        current: Installed version string.

    Returns:
        bool: ``True`` when latest > current (PEP 440 comparison, with a
        conservative string-inequality fallback).
    """
    try:
        from packaging.version import Version

        return Version(latest) > Version(current)
    except Exception:  # noqa: BLE001 — unparsable versions: only flag inequality
        return latest != current


class UpdateCheckService:
    """Checks PyPI for new releases and reports gateway version skew.

    Args:
        gateway_service: Source of per-gateway OpenShell versions.
        settings: Update-check configuration.
    """

    def __init__(self, gateway_service: GatewayService, settings: UpdateSettings) -> None:  # noqa: D107
        self._gateways = gateway_service
        self._settings = settings
        self._latest: str | None = None
        self._checked_at: str | None = None
        self._notified_version: str | None = None

    async def run_once(self) -> dict[str, Any]:
        """Query the release index once and fire the event on a new version.

        Returns:
            dict[str, Any]: The current status snapshot.
        """
        from shoreguard.services.webhooks import fire_webhook

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(self._settings.url)
                resp.raise_for_status()
                latest = str(resp.json()["info"]["version"])
        except Exception as exc:  # noqa: BLE001 — network/parse errors are non-fatal
            logger.debug("Update check failed: %s", exc)
            return self.status()

        self._latest = latest
        self._checked_at = datetime.datetime.now(datetime.UTC).isoformat()
        if _is_newer(latest, __version__) and self._notified_version != latest:
            self._notified_version = latest
            logger.info("ShoreGuard update available: %s (running %s)", latest, __version__)
            await fire_webhook(
                "shoreguard.update_available",
                {"current": __version__, "latest": latest},
            )
        return self.status()

    def status(self) -> dict[str, Any]:
        """Return the update status plus gateway version skew.

        Returns:
            dict[str, Any]: ``current``, ``latest``, ``update_available``,
            ``checked_at``, ``check_enabled``, ``gateway_versions``, and
            ``version_skew`` (more than one distinct gateway version).
        """
        versions = self._gateways.known_versions()
        distinct = sorted(set(versions.values()))
        return {
            "current": __version__,
            "latest": self._latest,
            "update_available": (_is_newer(self._latest, __version__) if self._latest else False),
            "checked_at": self._checked_at,
            "check_enabled": self._settings.enabled,
            "gateway_versions": versions,
            "version_skew": len(distinct) > 1,
        }
