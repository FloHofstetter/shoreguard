"""Unit tests for PolicyStatusBroker (M20 push-based wait_loaded)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from shoreguard.services.policy_status import PolicyStatusBroker


def _make_client(policy_statuses, watch_events=None):
    """Build a stand-in client whose policies.get() walks ``policy_statuses``."""
    client = MagicMock()
    statuses = iter(policy_statuses)
    last = {"v": policy_statuses[-1]}

    def _get(_name):
        try:
            last["v"] = next(statuses)
        except StopIteration:
            pass
        return last["v"]

    client.policies.get.side_effect = _get
    client._stub.WatchSandbox.return_value = iter(watch_events or [])
    return client


@pytest.mark.asyncio
async def test_wait_for_loaded_immediate():
    """Returns instantly when the very first GetSandboxPolicyStatus is loaded."""
    client = _make_client(
        [{"active_version": 5, "revision": {"status": "loaded"}}],
    )
    broker = PolicyStatusBroker()

    await broker.wait_for_loaded(client, "sb1", target_version=5, timeout=2.0)

    assert client.policies.get.call_count == 1


@pytest.mark.asyncio
async def test_wait_for_loaded_after_one_recheck():
    """Falls back to slow_poll, succeeds on the second status RPC."""
    client = _make_client(
        [
            {"active_version": 4, "revision": {"status": "pending"}},
            {"active_version": 5, "revision": {"status": "loaded"}},
        ],
    )
    broker = PolicyStatusBroker()

    await broker.wait_for_loaded(client, "sb1", target_version=5, timeout=5.0, slow_poll=0.1)

    assert client.policies.get.call_count >= 2


@pytest.mark.asyncio
async def test_wait_for_loaded_times_out():
    """Raises HTTPException(504) when target version never reaches 'loaded'."""
    client = _make_client(
        [{"active_version": 4, "revision": {"status": "pending"}}],
    )
    broker = PolicyStatusBroker()

    with pytest.raises(HTTPException) as exc:
        await broker.wait_for_loaded(client, "sb1", target_version=5, timeout=0.3, slow_poll=0.05)

    assert exc.value.status_code == 504
    assert "v5" in exc.value.detail


@pytest.mark.asyncio
async def test_wait_for_loaded_wrong_version_blocks():
    """A 'loaded' status for a different version does not satisfy the wait."""
    client = _make_client(
        [
            {"active_version": 4, "revision": {"status": "loaded"}},
            {"active_version": 4, "revision": {"status": "loaded"}},
        ],
    )
    broker = PolicyStatusBroker()

    with pytest.raises(HTTPException) as exc:
        await broker.wait_for_loaded(client, "sb1", target_version=5, timeout=0.2, slow_poll=0.05)

    assert exc.value.status_code == 504


@pytest.mark.asyncio
async def test_wait_for_loaded_cancels_watch_stream():
    """The watch stream is cancelled on the way out, even on success."""
    cancelled = {"v": False}
    fake_stream = MagicMock()
    fake_stream.__iter__ = lambda self: iter([])

    def _cancel():
        cancelled["v"] = True

    fake_stream.cancel.side_effect = _cancel

    client = MagicMock()
    client.policies.get.return_value = {
        "active_version": 5,
        "revision": {"status": "loaded"},
    }
    client._stub.WatchSandbox.return_value = fake_stream

    broker = PolicyStatusBroker()
    await broker.wait_for_loaded(client, "sb1", target_version=5, timeout=2.0)

    assert cancelled["v"] is True


@pytest.mark.asyncio
async def test_wait_for_loaded_handles_watch_open_failure():
    """Falls back to polling when WatchSandbox raises on open."""
    client = MagicMock()
    statuses = [
        {"active_version": 4, "revision": {"status": "pending"}},
        {"active_version": 5, "revision": {"status": "loaded"}},
    ]
    it = iter(statuses)
    client.policies.get.side_effect = lambda _n: next(it, statuses[-1])
    client._stub.WatchSandbox.side_effect = RuntimeError("connection refused")

    broker = PolicyStatusBroker()
    await broker.wait_for_loaded(client, "sb1", target_version=5, timeout=5.0, slow_poll=0.05)

    assert client.policies.get.call_count >= 2


@pytest.mark.asyncio
async def test_wait_event_dispatch(monkeypatch):
    """Watch event with payload draft_policy_update wakes the main loop."""

    class _Event:
        def __init__(self, payload_name):
            self._p = payload_name

        def WhichOneof(self, _field):
            return self._p

    fake_stream = MagicMock()
    fake_stream.__iter__ = lambda self: iter([_Event("draft_policy_update"), _Event("log")])

    client = MagicMock()
    client._stub.WatchSandbox.return_value = fake_stream
    statuses = [
        {"active_version": 4, "revision": {"status": "pending"}},
        {"active_version": 5, "revision": {"status": "loaded"}},
    ]
    it = iter(statuses)
    client.policies.get.side_effect = lambda _n: next(it, statuses[-1])

    broker = PolicyStatusBroker()
    await broker.wait_for_loaded(client, "sb1", target_version=5, timeout=5.0, slow_poll=10.0)

    # If the watch event hadn't woken us, we'd be stuck on slow_poll=10s
    # well past test timeouts.  Reaching here within 5s proves the wake.
    assert client.policies.get.call_count >= 2
