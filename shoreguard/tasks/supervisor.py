"""Generic supervisor for periodic background tasks.

Replaces the five hand-rolled polling loops that previously lived in
the FastAPI lifespan, each with its own copy of the same backoff and
health-tracking logic. A :class:`PeriodicTask` declares *what* runs and
how often; :class:`TaskSupervisor` owns the asyncio tasks, applies
exponential backoff after repeated failures, tracks per-task health,
and exposes a snapshot for the ``/readyz`` probe.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PeriodicTask:
    """Declarative spec for one periodic background task.

    Attributes:
        name: Stable identifier, used in logs and the health snapshot.
        interval: Seconds between runs under normal operation.
        run: Async callable executed once per tick.
        max_interval: Upper bound for the backed-off interval. Defaults
            to ``interval * 8`` when not set.
        backoff_threshold: Consecutive failures after which the interval
            starts doubling.
        run_at_start: Run once immediately instead of sleeping a full
            interval first. For long intervals (update check: 24h) the
            sleep-first default would delay the first result past any
            realistic homelab uptime.
        effective_max_interval: Resolved backoff ceiling (property).
    """

    name: str
    interval: float
    run: Callable[[], Awaitable[None]]
    max_interval: float | None = None
    backoff_threshold: int = 3
    run_at_start: bool = False

    @property
    def effective_max_interval(self) -> float:
        """Resolved backoff ceiling.

        Returns:
            float: ``max_interval`` or ``interval * 8`` when unset.
        """
        return self.max_interval if self.max_interval is not None else self.interval * 8


@dataclass
class _TaskState:
    """Mutable runtime state for one supervised task.

    Attributes:
        spec: The task spec being supervised.
        task: The running asyncio task.
        alive: False once the task has exited for any reason.
        last_success: Epoch seconds of the last successful run.
        consecutive_failures: Failures since the last success.
    """

    spec: PeriodicTask
    task: asyncio.Task[None]
    alive: bool = True
    last_success: float | None = field(default_factory=time.time)
    consecutive_failures: int = 0


class TaskSupervisor:
    """Owns and monitors a set of :class:`PeriodicTask` loops."""

    def __init__(self) -> None:  # noqa: D107
        self._states: dict[str, _TaskState] = {}

    def start(self, specs: list[PeriodicTask]) -> None:
        """Spawn one asyncio task per spec.

        Args:
            specs: Tasks to supervise. Names must be unique.

        Raises:
            ValueError: If a spec name is already supervised.
        """
        for spec in specs:
            if spec.name in self._states:
                raise ValueError(f"Task '{spec.name}' is already supervised")
            task = asyncio.create_task(self._runner(spec), name=f"periodic:{spec.name}")
            state = _TaskState(spec=spec, task=task)
            task.add_done_callback(self._make_done_cb(spec.name))
            self._states[spec.name] = state
        if specs:
            logger.info(
                "Task supervisor started: %s",
                ", ".join(f"{s.name} ({s.interval:.0f}s)" for s in specs),
            )

    async def _runner(self, spec: PeriodicTask) -> None:
        """Drive one task loop with failure backoff.

        Args:
            spec: The task to run.
        """
        interval = spec.interval
        first = spec.run_at_start
        while True:
            if first:
                first = False
            else:
                await asyncio.sleep(interval)
            state = self._states[spec.name]
            try:
                await spec.run()
            except Exception:
                state.consecutive_failures += 1
                logger.exception(
                    "Background task %s failed (consecutive failures: %d)",
                    spec.name,
                    state.consecutive_failures,
                )
                if state.consecutive_failures >= spec.backoff_threshold:
                    interval = min(interval * 2, spec.effective_max_interval)
                    logger.error(
                        "Background task %s has failed %d consecutive times, "
                        "backing off to %.0fs interval",
                        spec.name,
                        state.consecutive_failures,
                        interval,
                    )
            else:
                state.consecutive_failures = 0
                state.last_success = time.time()
                interval = spec.interval

    def _make_done_cb(self, name: str) -> Callable[[asyncio.Task[None]], None]:
        """Build a done-callback that records task exit.

        Args:
            name: Task name.

        Returns:
            Callable[[asyncio.Task[None]], None]: The callback.
        """

        def _cb(t: asyncio.Task[None]) -> None:
            state = self._states.get(name)
            if state is not None:
                state.alive = False
            if t.cancelled():
                logger.info("Background task %s cancelled", name)
                return
            exc = t.exception()
            if exc is not None:
                logger.error(
                    "Background task %s exited with exception: %s", name, exc, exc_info=exc
                )
            else:
                logger.warning("Background task %s exited unexpectedly", name)

        return _cb

    def health_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return per-task health for the readiness probe.

        Returns:
            dict[str, dict[str, Any]]: Per task: ``alive``,
            ``last_success`` (epoch seconds or None), ``age_s``,
            ``consecutive_failures``, and ``stalled`` (True when the
            last success is older than twice the backoff ceiling).
        """
        now = time.time()
        snapshot: dict[str, dict[str, Any]] = {}
        for name, state in self._states.items():
            age = None if state.last_success is None else now - state.last_success
            stalled = age is not None and age > 2.0 * state.spec.effective_max_interval
            snapshot[name] = {
                "alive": state.alive,
                "last_success": state.last_success,
                "age_s": None if age is None else round(age, 1),
                "consecutive_failures": state.consecutive_failures,
                "stalled": stalled,
            }
        return snapshot

    async def shutdown(self, timeout: float = 10.0) -> None:
        """Cancel all supervised tasks and wait for them to finish.

        A hard deadline ensures a task that swallows ``CancelledError``
        cannot block shutdown forever.

        Args:
            timeout: Seconds to wait for tasks to exit after cancel.
        """
        tasks = [s.task for s in self._states.values() if not s.task.done()]
        for t in tasks:
            t.cancel()
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            logger.warning(
                "Background tasks did not exit within %.1fs: %d still pending",
                timeout,
                len(pending),
            )
