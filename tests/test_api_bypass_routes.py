"""Integration tests for bypass detection API routes."""

from __future__ import annotations

import pytest

import shoreguard.services.bypass as bypass_mod
from shoreguard.services.bypass import BypassService

GW = "test"
SB = "sb1"
BASE = f"/api/gateways/{GW}/sandboxes/{SB}/bypass"


@pytest.fixture(autouse=True)
def _init_bypass_service():
    """Initialise a fresh BypassService per test."""
    bypass_mod.bypass_service = BypassService(ring_size=100)
    yield
    bypass_mod.bypass_service = None


def _feed_bypass_event(svc: BypassService, msg: str, ts: int = 1000) -> None:
    """Helper: feed a raw OCSF log into the bypass service."""
    log = {
        "timestamp_ms": ts,
        "level": "OCSF",
        "target": "ocsf",
        "source": "sandbox",
        "message": msg,
        "fields": {},
    }
    svc.ingest_log(log, sandbox_name=SB, gateway_name=GW)


async def test_get_bypass_events_empty(api_client, mock_client):
    """GET /bypass returns empty list when no events exist."""
    resp = await api_client.get(BASE)
    assert resp.status_code == 200
    data = resp.json()
    assert data["events"] == []
    assert data["count"] == 0


async def test_get_bypass_events_populated(api_client, mock_client):
    """GET /bypass returns events after ingestion."""
    svc = bypass_mod.bypass_service
    assert svc is not None
    _feed_bypass_event(svc, 'FINDING:BLOCKED [HIGH] "bypass attempt"', ts=1000)
    _feed_bypass_event(svc, 'FINDING:BLOCKED [CRIT] "nsenter escape"', ts=2000)

    resp = await api_client.get(BASE)
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    # Newest first.
    assert data["events"][0]["timestamp_ms"] == 2000
    assert data["events"][1]["timestamp_ms"] == 1000


async def test_get_bypass_events_since_ms(api_client, mock_client):
    """GET /bypass?since_ms filters by timestamp."""
    svc = bypass_mod.bypass_service
    assert svc is not None
    _feed_bypass_event(svc, 'FINDING:BLOCKED [HIGH] "bypass old"', ts=1000)
    _feed_bypass_event(svc, 'FINDING:BLOCKED [HIGH] "bypass new"', ts=5000)

    resp = await api_client.get(f"{BASE}?since_ms=3000")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["events"][0]["timestamp_ms"] == 5000


async def test_get_bypass_events_limit(api_client, mock_client):
    """GET /bypass?limit restricts event count."""
    svc = bypass_mod.bypass_service
    assert svc is not None
    for i in range(5):
        _feed_bypass_event(svc, f'FINDING:BLOCKED [HIGH] "bypass #{i}"', ts=1000 + i)

    resp = await api_client.get(f"{BASE}?limit=2")
    assert resp.status_code == 200
    assert resp.json()["count"] == 2


async def test_get_bypass_summary_empty(api_client, mock_client):
    """GET /bypass/summary returns zeros when no events exist."""
    resp = await api_client.get(f"{BASE}/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["by_technique"] == {}
    assert data["by_severity"] == {}
    assert data["latest_timestamp_ms"] is None


async def test_get_bypass_summary_populated(api_client, mock_client):
    """GET /bypass/summary returns aggregated counts."""
    svc = bypass_mod.bypass_service
    assert svc is not None
    _feed_bypass_event(
        svc,
        "NET:OPEN [HIGH] DENIED dst:evil.com:443 [engine:iptables]",
        ts=1000,
    )
    _feed_bypass_event(
        svc,
        'FINDING:BLOCKED [CRIT] "nsenter escape"',
        ts=2000,
    )

    resp = await api_client.get(f"{BASE}/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["by_technique"]["iptables"] == 1
    assert data["by_technique"]["nsenter"] == 1
    assert data["by_severity"]["HIGH"] == 1
    assert data["by_severity"]["CRIT"] == 1
    assert data["latest_timestamp_ms"] == 2000


async def test_bypass_events_isolated_per_sandbox(api_client, mock_client):
    """Events for sandbox B do not appear in sandbox A's response."""
    svc = bypass_mod.bypass_service
    assert svc is not None
    # Feed event for a different sandbox.
    log = {
        "timestamp_ms": 1000,
        "level": "OCSF",
        "target": "ocsf",
        "source": "sandbox",
        "message": 'FINDING:BLOCKED [HIGH] "bypass for other sandbox"',
        "fields": {},
    }
    svc.ingest_log(log, sandbox_name="other-sb", gateway_name=GW)

    resp = await api_client.get(BASE)
    assert resp.status_code == 200
    assert resp.json()["count"] == 0
