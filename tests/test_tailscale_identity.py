"""Tests for Tailscale Serve identity-header authentication (opt-in)."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from httpx import ASGITransport, AsyncClient

from shoreguard.api.auth import create_user
from shoreguard.settings import reset_settings

TS_LOGIN = "flo@example.com"


@pytest.fixture
def db():
    from tests.conftest import make_auth_test_db

    factory, dispose = make_auth_test_db()
    yield factory
    dispose()


@pytest.fixture
def _ts_enabled(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    monkeypatch.setenv("SHOREGUARD_TAILSCALE_IDENTITY", "true")
    reset_settings()
    yield
    monkeypatch.delenv("SHOREGUARD_TAILSCALE_IDENTITY", raising=False)
    reset_settings()


def _client(client_addr: tuple[str, int] = ("127.0.0.1", 1234)) -> AsyncClient:
    from shoreguard.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app, client=client_addr),
        base_url="http://test",
    )


async def test_identity_header_authenticates_matching_user(db, _ts_enabled) -> None:
    await create_user(TS_LOGIN, "irrelevant1234", "operator")
    async with _client() as client:
        resp = await client.get("/api/auth/check", headers={"Tailscale-User-Login": TS_LOGIN})
        assert resp.status_code == 200
        body = resp.json()
        assert body["authenticated"] is True
        assert body["role"] == "operator"


async def test_identity_header_ignored_when_disabled(db, monkeypatch) -> None:
    monkeypatch.delenv("SHOREGUARD_TAILSCALE_IDENTITY", raising=False)
    reset_settings()
    await create_user(TS_LOGIN, "irrelevant1234", "operator")
    async with _client() as client:
        resp = await client.get("/api/auth/check", headers={"Tailscale-User-Login": TS_LOGIN})
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False


async def test_identity_header_rejected_from_non_loopback(db, _ts_enabled) -> None:
    await create_user(TS_LOGIN, "irrelevant1234", "operator")
    async with _client(client_addr=("10.0.0.5", 1234)) as client:
        resp = await client.get("/api/auth/check", headers={"Tailscale-User-Login": TS_LOGIN})
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False


async def test_identity_header_unknown_login_unauthenticated(db, _ts_enabled) -> None:
    await create_user(TS_LOGIN, "irrelevant1234", "operator")
    async with _client() as client:
        resp = await client.get(
            "/api/auth/check", headers={"Tailscale-User-Login": "stranger@example.com"}
        )
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False
