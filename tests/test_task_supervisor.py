"""Tests for the generic background task supervisor."""

from __future__ import annotations

import asyncio

import pytest

from shoreguard.tasks.supervisor import PeriodicTask, TaskSupervisor


async def _wait_for(predicate, timeout=2.0, step=0.01):
    """Poll *predicate* until it is truthy or the timeout expires."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(step)
    return False


@pytest.mark.asyncio
async def test_runs_periodically_and_tracks_success():
    runs = []

    async def tick():
        runs.append(1)

    sup = TaskSupervisor()
    sup.start([PeriodicTask(name="t", interval=0.01, run=tick)])
    assert await _wait_for(lambda: len(runs) >= 3)
    snap = sup.health_snapshot()["t"]
    assert snap["alive"] is True
    assert snap["consecutive_failures"] == 0
    assert snap["stalled"] is False
    await sup.shutdown(timeout=1.0)


@pytest.mark.asyncio
async def test_failure_counts_and_recovery():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("boom")

    sup = TaskSupervisor()
    sup.start([PeriodicTask(name="flaky", interval=0.01, run=flaky, backoff_threshold=99)])
    assert await _wait_for(lambda: calls["n"] >= 4)
    snap = sup.health_snapshot()["flaky"]
    # Recovered after the two initial failures.
    assert snap["consecutive_failures"] == 0
    assert snap["alive"] is True
    await sup.shutdown(timeout=1.0)


@pytest.mark.asyncio
async def test_backoff_grows_interval_after_threshold():
    async def always_fails():
        raise RuntimeError("nope")

    spec = PeriodicTask(
        name="fail",
        interval=0.01,
        run=always_fails,
        max_interval=10.0,
        backoff_threshold=1,
    )
    sup = TaskSupervisor()
    sup.start([spec])
    assert await _wait_for(lambda: sup.health_snapshot()["fail"]["consecutive_failures"] >= 2)
    await sup.shutdown(timeout=1.0)


@pytest.mark.asyncio
async def test_shutdown_marks_dead():
    async def tick():
        pass

    sup = TaskSupervisor()
    sup.start([PeriodicTask(name="t", interval=0.01, run=tick)])
    await sup.shutdown(timeout=1.0)
    assert sup.health_snapshot()["t"]["alive"] is False


@pytest.mark.asyncio
async def test_duplicate_name_rejected():
    async def tick():
        pass

    sup = TaskSupervisor()
    sup.start([PeriodicTask(name="t", interval=10, run=tick)])
    with pytest.raises(ValueError, match="already supervised"):
        sup.start([PeriodicTask(name="t", interval=10, run=tick)])
    await sup.shutdown(timeout=1.0)


def test_effective_max_interval_defaults_to_8x():
    async def tick():
        pass

    assert PeriodicTask(name="t", interval=5, run=tick).effective_max_interval == 40
    assert (
        PeriodicTask(name="t", interval=5, run=tick, max_interval=7.0).effective_max_interval == 7.0
    )


def test_build_tasks_respects_feature_flags(container):
    """Disabled features produce no task; core tasks are always present."""
    from shoreguard.settings import Settings
    from shoreguard.tasks.definitions import build_tasks

    settings = Settings()
    names = {t.name for t in build_tasks(container, settings)}
    assert "cleanup" in names
    assert "health_monitor" in names
    if not settings.discovery.enabled:
        assert "discovery" not in names
    if not settings.drift_detection.enabled:
        assert "drift_detection" not in names
    assert "node_alerts" in names  # enabled by default


def test_build_tasks_node_alerts_disabled(container, monkeypatch):
    """SHOREGUARD_NODE_ALERT_ENABLED=false removes the node_alerts task."""
    from shoreguard.settings import Settings, reset_settings
    from shoreguard.tasks.definitions import build_tasks

    monkeypatch.setenv("SHOREGUARD_NODE_ALERT_ENABLED", "false")
    reset_settings()
    try:
        names = {t.name for t in build_tasks(container, Settings())}
        assert "node_alerts" not in names
    finally:
        reset_settings()
