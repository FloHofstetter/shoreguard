"""Unit tests for DriftDetectionService (M23)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from shoreguard.services.drift_detection import DriftDetectionService
from shoreguard.settings import DriftDetectionSettings


def _gateway_service(*, gateways, sandboxes_per_gw, hash_per_sandbox):
    """Build a fake gateway service whose state is mutable per test."""
    gw_svc = MagicMock()
    gw_svc.list_all.return_value = [{"name": gw} for gw in gateways]

    def _get_client(name):
        client = MagicMock()
        client.sandboxes.list.return_value = [{"name": sb} for sb in sandboxes_per_gw.get(name, [])]

        def _policies_get(sb_name):
            return {
                "active_version": 1,
                "revision": {
                    "version": 1,
                    "status": "loaded",
                    "policy_hash": hash_per_sandbox.get((name, sb_name), ""),
                },
                "policy": {},
            }

        client.policies.get.side_effect = _policies_get
        return client

    gw_svc.get_client.side_effect = _get_client
    return gw_svc


@pytest.mark.asyncio
async def test_first_tick_bootstraps_no_events():
    fired: list[tuple[str, dict]] = []

    async def emit(event, payload):
        fired.append((event, payload))

    gw_svc = _gateway_service(
        gateways=["gw1"],
        sandboxes_per_gw={"gw1": ["sb1"]},
        hash_per_sandbox={("gw1", "sb1"): "hash-A"},
    )
    svc = DriftDetectionService(gw_svc, DriftDetectionSettings(), webhook_emit=emit)
    events = await svc.run_once()
    assert events == []
    assert fired == []
    assert svc.snapshot[("gw1", "sb1")] == "hash-A"


@pytest.mark.asyncio
async def test_second_tick_detects_change():
    fired: list[tuple[str, dict]] = []

    async def emit(event, payload):
        fired.append((event, payload))

    state = {("gw1", "sb1"): "hash-A"}
    gw_svc = _gateway_service(
        gateways=["gw1"],
        sandboxes_per_gw={"gw1": ["sb1"]},
        hash_per_sandbox=state,
    )
    svc = DriftDetectionService(gw_svc, DriftDetectionSettings(), webhook_emit=emit)
    await svc.run_once()  # bootstrap
    state[("gw1", "sb1")] = "hash-B"  # someone edited out-of-band
    events = await svc.run_once()
    assert len(events) == 1
    assert events[0]["previous_hash"] == "hash-A"
    assert events[0]["current_hash"] == "hash-B"
    assert fired[0][0] == "policy.drift_detected"


@pytest.mark.asyncio
async def test_unchanged_hash_no_event():
    fired: list = []

    async def emit(event, payload):
        fired.append((event, payload))

    gw_svc = _gateway_service(
        gateways=["gw1"],
        sandboxes_per_gw={"gw1": ["sb1"]},
        hash_per_sandbox={("gw1", "sb1"): "hash-A"},
    )
    svc = DriftDetectionService(gw_svc, DriftDetectionSettings(), webhook_emit=emit)
    await svc.run_once()
    events = await svc.run_once()
    assert events == []
    assert fired == []


@pytest.mark.asyncio
async def test_one_broken_sandbox_does_not_kill_loop():
    fired: list = []

    async def emit(event, payload):
        fired.append((event, payload))

    gw_svc = MagicMock()
    gw_svc.list_all.return_value = [{"name": "gw1"}]

    client = MagicMock()
    client.sandboxes.list.return_value = [{"name": "broken"}, {"name": "ok"}]

    def _policies_get(sb_name):
        if sb_name == "broken":
            raise RuntimeError("policy fetch boom")
        return {
            "active_version": 1,
            "revision": {"version": 1, "status": "loaded", "policy_hash": "hash-A"},
            "policy": {},
        }

    client.policies.get.side_effect = _policies_get
    gw_svc.get_client.return_value = client

    svc = DriftDetectionService(gw_svc, DriftDetectionSettings(), webhook_emit=emit)
    await svc.run_once()
    assert svc.snapshot.get(("gw1", "ok")) == "hash-A"
    assert ("gw1", "broken") not in svc.snapshot
