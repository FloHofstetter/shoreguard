"""Tests for the update-check service, version skew, and route."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shoreguard import __version__
from shoreguard.services.update_check import UpdateCheckService, _is_newer
from shoreguard.settings import UpdateSettings


def test_is_newer_pep440() -> None:
    assert _is_newer("1.2.0", "1.1.9") is True
    assert _is_newer("1.1.9", "1.2.0") is False
    assert _is_newer("1.2.0", "1.2.0") is False
    assert _is_newer("1.10.0", "1.9.0") is True  # not lexicographic


class _StubGateways:
    def __init__(self, versions: dict[str, str] | None = None) -> None:
        self._versions = versions or {}

    def known_versions(self) -> dict[str, str]:
        return dict(self._versions)


def _patch_httpx_get(version: str):
    resp = MagicMock()
    resp.json.return_value = {"info": {"version": version}}
    resp.raise_for_status.return_value = None
    client = AsyncMock()
    client.get.return_value = resp
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    return patch("shoreguard.services.update_check.httpx.AsyncClient", return_value=client)


@pytest.fixture
def fire(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr("shoreguard.services.webhooks.fire_webhook", mock)
    return mock


async def test_run_once_fires_event_once_per_version(fire) -> None:
    svc = UpdateCheckService(_StubGateways(), UpdateSettings(enabled=True))  # type: ignore[arg-type]

    with _patch_httpx_get("99.0.0"):
        status = await svc.run_once()
        await svc.run_once()  # same version: no second event

    assert status["latest"] == "99.0.0"
    assert status["update_available"] is True
    assert fire.await_count == 1
    event, payload = fire.await_args.args
    assert event == "shoreguard.update_available"
    assert payload == {"current": __version__, "latest": "99.0.0"}


async def test_run_once_no_event_when_current(fire) -> None:
    svc = UpdateCheckService(_StubGateways(), UpdateSettings())  # type: ignore[arg-type]

    with _patch_httpx_get(__version__):
        status = await svc.run_once()

    assert status["update_available"] is False
    fire.assert_not_awaited()


async def test_run_once_survives_network_failure(fire) -> None:
    svc = UpdateCheckService(_StubGateways(), UpdateSettings())  # type: ignore[arg-type]

    client = AsyncMock()
    client.get.side_effect = OSError("offline")
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    with patch("shoreguard.services.update_check.httpx.AsyncClient", return_value=client):
        status = await svc.run_once()

    assert status["latest"] is None
    fire.assert_not_awaited()


def test_status_reports_version_skew() -> None:
    svc = UpdateCheckService(
        _StubGateways({"gw1": "0.5.0", "gw2": "0.6.0"}),  # type: ignore[arg-type]
        UpdateSettings(),
    )
    status = svc.status()
    assert status["version_skew"] is True
    assert status["gateway_versions"] == {"gw1": "0.5.0", "gw2": "0.6.0"}


def test_status_no_skew_with_single_version() -> None:
    svc = UpdateCheckService(
        _StubGateways({"gw1": "0.6.0", "gw2": "0.6.0"}),  # type: ignore[arg-type]
        UpdateSettings(),
    )
    assert svc.status()["version_skew"] is False


# ─── Route ───────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    from tests.conftest import make_auth_test_db

    factory, dispose = make_auth_test_db()
    yield factory
    dispose()


async def test_updates_route(db) -> None:
    from httpx import ASGITransport, AsyncClient

    from shoreguard.api.auth import create_user
    from shoreguard.api.main import app

    await create_user("viewer@test.com", "viewerpass1", "viewer")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/auth/login", json={"email": "viewer@test.com", "password": "viewerpass1"}
        )
        assert resp.status_code == 200
        resp = await client.get("/api/system/updates")
        assert resp.status_code == 200
        data = resp.json()
        assert data["current"] == __version__
        assert data["check_enabled"] is False  # off by default
