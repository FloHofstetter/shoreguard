"""ShoreGuard's concrete background tasks.

:func:`build_tasks` turns the container + settings into the list of
:class:`~shoreguard.tasks.supervisor.PeriodicTask` specs the lifespan
hands to the :class:`~shoreguard.tasks.supervisor.TaskSupervisor`.
Disabled features (discovery, drift detection, cert rotation) simply
produce no task, so the readiness probe never judges loops that are
not supposed to run.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shoreguard.container import ServiceContainer
    from shoreguard.settings import Settings

from shoreguard.tasks.supervisor import PeriodicTask

logger = logging.getLogger(__name__)


def build_tasks(container: ServiceContainer, settings: Settings) -> list[PeriodicTask]:
    """Build the periodic task specs for this process.

    Args:
        container: The installed service container.
        settings: Settings snapshot (intervals, feature flags).

    Returns:
        list[PeriodicTask]: Enabled background tasks.
    """

    async def _cleanup() -> None:
        # Purge expired operations, audit entries, and webhook deliveries.
        await container.operations.cleanup()
        await container.audit.cleanup()
        await container.webhooks.cleanup_old_deliveries()

    async def _health_monitor() -> None:
        await container.gateway.check_all_health()

    async def _discovery() -> None:
        await container.discovery.run_once()

    async def _drift_detection() -> None:
        await container.drift_detection.run_once()

    async def _cert_rotation() -> None:
        outcomes = await container.cert_rotation.run_once()
        if any(outcomes.get(k) for k in ("success", "failure")):
            logger.info(
                "Cert rotation cycle: %s",
                ", ".join(f"{k}={v}" for k, v in outcomes.items() if v),
            )

    tasks = [
        PeriodicTask(
            name="cleanup",
            interval=settings.background.cleanup_interval,
            max_interval=settings.background.cleanup_max_interval,
            backoff_threshold=settings.background.cleanup_backoff_threshold,
            run=_cleanup,
        ),
        PeriodicTask(
            name="health_monitor",
            interval=settings.background.health_interval,
            max_interval=settings.background.health_max_interval,
            backoff_threshold=settings.background.health_backoff_threshold,
            run=_health_monitor,
        ),
    ]
    if settings.discovery.enabled:
        tasks.append(
            PeriodicTask(
                name="discovery",
                interval=settings.discovery.interval_seconds,
                run=_discovery,
            )
        )
    if settings.drift_detection.enabled:
        tasks.append(
            PeriodicTask(
                name="drift_detection",
                interval=settings.drift_detection.interval_seconds,
                run=_drift_detection,
            )
        )
    if settings.cert_rotation.enabled:
        tasks.append(
            PeriodicTask(
                name="cert_rotation",
                interval=settings.cert_rotation.poll_interval_s,
                run=_cert_rotation,
            )
        )
    if settings.digest.enabled:

        async def _daily_digest() -> None:
            await container.digest.dispatch_if_due(hour=settings.digest.hour, audit=container.audit)

        tasks.append(
            PeriodicTask(
                name="daily_digest",
                interval=settings.digest.check_interval,
                run=_daily_digest,
            )
        )
    return tasks
