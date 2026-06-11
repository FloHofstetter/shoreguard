"""Threshold alerts on the host node-stats sample.

The dashboard card shows GPU temperature, memory, and disk — but nobody
stares at a dashboard all night. This service evaluates the same sample
against configured thresholds and fires ``node.threshold_breached`` /
``node.recovered`` webhook events on **state transitions** only, so a
hot GPU reaches the phone exactly once and the all-clear follows when
it cools down. On GB10's unified memory architecture, host memory
pressure *is* GPU memory pressure, which makes the host-scoped sample
the right alert source for the single-box deployment.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shoreguard.services.node_stats import NodeStatsService
    from shoreguard.settings import NodeAlertSettings

logger = logging.getLogger(__name__)


class NodeAlertService:
    """Evaluates host thresholds and fires webhook events on transitions.

    Args:
        node_stats: The cached host stats sampler to evaluate.
        settings: Threshold configuration.
    """

    def __init__(self, node_stats: NodeStatsService, settings: NodeAlertSettings) -> None:  # noqa: D107
        self._node_stats = node_stats
        self._settings = settings
        self._breached: dict[str, bool] = {}

    def _checks(self, stats: dict[str, Any]) -> list[tuple[str, float | None, float]]:
        """Build the (metric, current value, threshold) triples to evaluate.

        Args:
            stats: A node-stats sample from :class:`NodeStatsService`.

        Returns:
            list[tuple[str, float | None, float]]: One triple per metric;
                value is ``None`` when the sample lacks the metric.
        """
        cfg = self._settings
        gpu_temps = [
            g["temperature_c"]
            for g in stats.get("gpus") or []
            if g.get("temperature_c") is not None
        ]
        memory = stats.get("memory") or {}
        disk = stats.get("disk") or {}
        return [
            ("gpu_temp_c", max(gpu_temps) if gpu_temps else None, cfg.gpu_temp_c),
            ("mem_used_pct", memory.get("used_pct"), cfg.mem_used_pct),
            ("disk_used_pct", disk.get("used_pct"), cfg.disk_used_pct),
        ]

    async def run_once(self) -> list[dict[str, Any]]:
        """Evaluate all thresholds once and fire events on transitions.

        Returns:
            list[dict[str, Any]]: Current alert states (one per metric).
        """
        from shoreguard.services.webhooks import fire_webhook

        stats = await self._node_stats.collect()
        states: list[dict[str, Any]] = []
        for metric, value, threshold in self._checks(stats):
            previously = self._breached.get(metric, False)
            breached = value is not None and value >= threshold
            states.append(
                {
                    "metric": metric,
                    "value": value,
                    "threshold": threshold,
                    "breached": breached,
                }
            )
            if breached == previously:
                continue
            self._breached[metric] = breached
            event = "node.threshold_breached" if breached else "node.recovered"
            payload = {
                "metric": metric,
                "value": value,
                "threshold": threshold,
                "scope": "shoreguard-host",
                "message": (
                    f"{metric} at {value} (threshold {threshold})"
                    if breached
                    else f"{metric} back at {value} (threshold {threshold})"
                ),
            }
            logger.warning(
                "Host threshold %s: %s", "breached" if breached else "recovered", payload
            )
            await fire_webhook(event, payload)
        return states

    def status(self) -> dict[str, Any]:
        """Return the threshold configuration and current breach states.

        Returns:
            dict[str, Any]: Enabled flag, thresholds, and active breaches.
        """
        cfg = self._settings
        return {
            "enabled": cfg.enabled,
            "thresholds": {
                "gpu_temp_c": cfg.gpu_temp_c,
                "mem_used_pct": cfg.mem_used_pct,
                "disk_used_pct": cfg.disk_used_pct,
            },
            "breached": sorted(m for m, b in self._breached.items() if b),
        }
