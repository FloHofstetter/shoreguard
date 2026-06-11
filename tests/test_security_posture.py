"""Tests for the security posture self-check service and route."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from shoreguard.api.auth import create_user
from shoreguard.services.security_posture import (
    _is_private_endpoint,
    collect_posture,
)
from shoreguard.settings import AuthSettings, ServerSettings, Settings

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "adminpass123"


def _settings(**overrides) -> Settings:
    server = overrides.pop("server", ServerSettings())
    auth = overrides.pop("auth", AuthSettings())
    return Settings(server=server, auth=auth, **overrides)


class _FakeRegistry:
    def __init__(self, gateways: list[dict] | None = None, fail: bool = False):
        self._gateways = gateways or []
        self._fail = fail

    async def list_all(self) -> list[dict]:
        if self._fail:
            raise RuntimeError("db down")
        return self._gateways


def _by_id(report: dict, check_id: str) -> dict:
    matches = [c for c in report["checks"] if c["id"] == check_id]
    assert matches, f"check {check_id!r} missing: {[c['id'] for c in report['checks']]}"
    return matches[0]


# ─── endpoint classification ───────────────────────────────────────────────


def test_private_endpoint_detection() -> None:
    assert _is_private_endpoint("127.0.0.1:30051")
    assert _is_private_endpoint("localhost:30051")
    assert _is_private_endpoint("192.168.1.50:30051")
    assert not _is_private_endpoint("8.8.8.8:30051")
    assert not _is_private_endpoint("gateway.example.com:30051")


# ─── auth/bind checks ──────────────────────────────────────────────────────


async def test_no_auth_on_loopback_is_warn() -> None:
    s = _settings(
        server=ServerSettings(host="127.0.0.1"),
        auth=AuthSettings(no_auth=True),
    )
    report = await collect_posture(s, _FakeRegistry())  # type: ignore[arg-type]
    assert _by_id(report, "auth_mode")["severity"] == "warn"
    assert report["summary"]["error"] == 0


async def test_no_auth_on_lan_is_error() -> None:
    s = _settings(
        server=ServerSettings(host="0.0.0.0", unsafe_lan=True),
        auth=AuthSettings(no_auth=True),
    )
    report = await collect_posture(s, _FakeRegistry())  # type: ignore[arg-type]
    assert _by_id(report, "auth_mode")["severity"] == "error"
    assert _by_id(report, "unsafe_lan")["severity"] == "error"


async def test_single_user_auth_is_ok() -> None:
    s = _settings(
        server=ServerSettings(host="127.0.0.1"),
        auth=AuthSettings(no_auth=False, single_user=True, secret_key="x" * 32),
    )
    report = await collect_posture(s, _FakeRegistry())  # type: ignore[arg-type]
    check = _by_id(report, "auth_mode")
    assert check["severity"] == "ok"
    assert "single-user" in check["detail"]
    assert _by_id(report, "secret_key")["severity"] == "ok"


async def test_short_secret_key_is_error() -> None:
    s = _settings(
        server=ServerSettings(host="127.0.0.1"),
        auth=AuthSettings(no_auth=False, secret_key="short"),
    )
    report = await collect_posture(s, _FakeRegistry())  # type: ignore[arg-type]
    assert _by_id(report, "secret_key")["severity"] == "error"


async def test_open_registration_flagged() -> None:
    s = _settings(
        server=ServerSettings(host="0.0.0.0"),
        auth=AuthSettings(no_auth=False, allow_registration=True, secret_key="x" * 32),
    )
    report = await collect_posture(s, _FakeRegistry())  # type: ignore[arg-type]
    assert _by_id(report, "registration")["severity"] == "error"


# ─── gateway transport checks ──────────────────────────────────────────────


async def test_gateway_with_mtls_is_ok() -> None:
    s = _settings(server=ServerSettings(host="127.0.0.1"), auth=AuthSettings(no_auth=True))
    gws = [
        {
            "name": "prod",
            "endpoint": "8.8.8.8:30051",
            "has_ca_cert": True,
            "has_client_cert": True,
        }
    ]
    report = await collect_posture(s, _FakeRegistry(gws))  # type: ignore[arg-type]
    assert _by_id(report, "gateway:prod")["severity"] == "ok"


async def test_local_plaintext_gateway_is_info_in_local_mode() -> None:
    s = _settings(
        server=ServerSettings(host="127.0.0.1", local_mode=True),
        auth=AuthSettings(no_auth=True),
    )
    gws = [
        {
            "name": "local",
            "endpoint": "127.0.0.1:30051",
            "has_ca_cert": False,
            "has_client_cert": False,
        }
    ]
    report = await collect_posture(s, _FakeRegistry(gws))  # type: ignore[arg-type]
    assert _by_id(report, "gateway:local")["severity"] == "info"


async def test_public_gateway_without_mtls_is_error() -> None:
    s = _settings(server=ServerSettings(host="127.0.0.1"), auth=AuthSettings(no_auth=True))
    gws = [
        {
            "name": "remote",
            "endpoint": "8.8.8.8:30051",
            "has_ca_cert": False,
            "has_client_cert": False,
        }
    ]
    report = await collect_posture(s, _FakeRegistry(gws))  # type: ignore[arg-type]
    assert _by_id(report, "gateway:remote")["severity"] == "error"


async def test_registry_failure_degrades_gracefully() -> None:
    s = _settings(server=ServerSettings(host="127.0.0.1"), auth=AuthSettings(no_auth=True))
    report = await collect_posture(s, _FakeRegistry(fail=True))  # type: ignore[arg-type]
    assert _by_id(report, "registry")["severity"] == "warn"


# ─── REST route ────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    from tests.conftest import make_auth_test_db

    factory, dispose = make_auth_test_db()
    yield factory
    dispose()


async def test_posture_route_requires_admin(db) -> None:
    from shoreguard.api.main import app

    await create_user("viewer@test.com", "viewerpass1", "viewer")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/auth/login",
            json={"email": "viewer@test.com", "password": "viewerpass1"},
        )
        assert resp.status_code == 200
        resp = await client.get("/api/security/posture")
        assert resp.status_code == 403


async def test_posture_route_returns_report(db) -> None:
    from shoreguard.api.main import app

    await create_user(ADMIN_EMAIL, ADMIN_PASS, "admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASS},
        )
        assert resp.status_code == 200
        resp = await client.get("/api/security/posture")
        assert resp.status_code == 200
        body = resp.json()
        assert "checks" in body and "summary" in body
        assert any(c["id"] == "auth_mode" for c in body["checks"])
