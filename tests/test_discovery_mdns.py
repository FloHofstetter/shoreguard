"""Tests for mDNS discovery integration and the filesystem-import route."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from shoreguard.services.discovery import DiscoveredEndpoint, DiscoveryService
from shoreguard.settings import DiscoverySettings


def _svc(settings: DiscoverySettings) -> DiscoveryService:
    return DiscoveryService(registry=None, gateway_service=None, settings=settings)  # type: ignore[arg-type]


def test_discover_all_excludes_mdns_by_default(monkeypatch) -> None:
    svc = _svc(DiscoverySettings(domains=[]))
    monkeypatch.setattr(
        svc, "discover_mdns", lambda: pytest.fail("mDNS must not run when disabled")
    )
    assert svc.discover_all() == {}


def test_discover_all_merges_mdns_when_enabled(monkeypatch) -> None:
    svc = _svc(DiscoverySettings(domains=[], mdns_enabled=True))
    ep = DiscoveredEndpoint(
        host="192.168.1.50", port=30051, priority=0, weight=0, source_domain="local"
    )
    monkeypatch.setattr(svc, "discover_mdns", lambda: [ep])
    result = svc.discover_all()
    assert result == {"local": [ep]}


def test_discover_mdns_handles_missing_announcements() -> None:
    # Real browse against the local network with a very short window:
    # asserts the call is safe (no exception) and returns a list. In CI
    # there is no announcing gateway, so this exercises the empty path.
    svc = _svc(DiscoverySettings(domains=[], mdns_enabled=True, mdns_timeout_seconds=0.5))
    result = svc.discover_mdns()
    assert isinstance(result, list)


# ─── filesystem import route ───────────────────────────────────────────────


@pytest.fixture
def db():
    from tests.conftest import make_auth_test_db

    factory, dispose = make_auth_test_db()
    yield factory
    dispose()


async def test_import_filesystem_route(db, monkeypatch, tmp_path) -> None:
    from shoreguard.api.auth import create_user

    # Point the openshell config dir at an empty temp dir — the route
    # must respond cleanly with zero imports and a log line.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    await create_user("admin@test.com", "adminpass123", "admin")
    from shoreguard.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/auth/login",
            json={"email": "admin@test.com", "password": "adminpass123"},
        )
        assert resp.status_code == 200
        resp = await client.post("/api/gateway/import-filesystem")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["imported"] == 0
        assert isinstance(body["log"], list)


async def test_import_filesystem_route_requires_admin(db) -> None:
    from shoreguard.api.auth import create_user

    await create_user("viewer@test.com", "viewerpass1", "viewer")
    from shoreguard.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/auth/login",
            json={"email": "viewer@test.com", "password": "viewerpass1"},
        )
        assert resp.status_code == 200
        resp = await client.post("/api/gateway/import-filesystem")
        assert resp.status_code == 403
