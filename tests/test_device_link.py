"""Tests for the QR device-link sign-in handoff."""

from __future__ import annotations

import datetime

import pytest
from httpx import ASGITransport, AsyncClient

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "adminpassword1"


@pytest.fixture
def db():
    from tests.conftest import make_auth_test_db

    factory, dispose = make_auth_test_db()
    yield factory
    dispose()


@pytest.fixture
def enabled(monkeypatch):
    from shoreguard.settings import reset_settings

    monkeypatch.setenv("SHOREGUARD_DEVICE_LINK_ENABLED", "true")
    reset_settings()
    yield
    reset_settings()


@pytest.fixture
async def admin_client(db, enabled):
    from shoreguard.api.auth import create_user
    from shoreguard.api.main import app

    await create_user(ADMIN_EMAIL, ADMIN_PASS, "admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}
        )
        assert resp.status_code == 200
        yield client


@pytest.fixture
async def phone_client(db, enabled):
    """A fresh cookie jar standing in for the scanning phone."""
    from shoreguard.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


# ─── Feature gate ────────────────────────────────────────────────────────────


async def test_disabled_returns_404(db) -> None:
    from shoreguard.api.auth import create_user
    from shoreguard.api.main import app

    await create_user(ADMIN_EMAIL, ADMIN_PASS, "admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as admin:
        await admin.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS})
        # Login still works; only device-link is gated off.
        resp = await admin.post("/api/auth/device-link", json={})
        assert resp.status_code == 404
        assert (
            await admin.post("/api/auth/device-link/redeem", json={"code": "x"})
        ).status_code == 404
        assert (await admin.get("/login/device")).status_code == 404


# ─── Happy path ──────────────────────────────────────────────────────────────


async def test_full_handoff_flow(admin_client, phone_client) -> None:
    # 1. Desktop mints a code.
    mint = (await admin_client.post("/api/auth/device-link", json={})).json()
    assert set(mint) == {"id", "code", "expires_at", "role"}
    assert mint["role"] == "admin"
    code = mint["code"]

    # 2. Phone claims it (first redeem poll) — pending, identity shown.
    r1 = (await phone_client.post("/api/auth/device-link/redeem", json={"code": code})).json()
    assert r1["status"] == "pending"
    assert r1["email"] == ADMIN_EMAIL

    # 3. Desktop sees the pending request and approves it.
    pending = (await admin_client.get("/api/auth/device-link/pending")).json()["pending"]
    assert len(pending) == 1
    assert pending[0]["id"] == mint["id"]
    appr = await admin_client.post(
        "/api/auth/device-link/approve", json={"id": mint["id"], "approve": True}
    )
    assert appr.status_code == 200 and appr.json()["status"] == "approved"

    # 4. Phone's next poll mints a session.
    resp = await phone_client.post("/api/auth/device-link/redeem", json={"code": code})
    assert resp.status_code == 200 and resp.json()["status"] == "approved"
    assert "sg_session" in resp.cookies

    # 5. The phone is now authenticated as the admin.
    check = (await phone_client.get("/api/auth/check")).json()
    assert check["authenticated"] is True
    assert check["role"] == "admin"
    assert check["email"] == ADMIN_EMAIL


async def test_pending_before_approval(admin_client, phone_client) -> None:
    code = (await admin_client.post("/api/auth/device-link", json={})).json()["code"]
    await phone_client.post("/api/auth/device-link/redeem", json={"code": code})
    # Polling again before approval stays pending — no session.
    r = (await phone_client.post("/api/auth/device-link/redeem", json={"code": code})).json()
    assert r["status"] == "pending"
    assert (await phone_client.get("/api/auth/check")).json()["authenticated"] is False


async def test_handoff_session_is_short_lived(admin_client, phone_client) -> None:
    mint = (await admin_client.post("/api/auth/device-link", json={})).json()
    await phone_client.post("/api/auth/device-link/redeem", json={"code": mint["code"]})
    await admin_client.post(
        "/api/auth/device-link/approve", json={"id": mint["id"], "approve": True}
    )
    resp = await phone_client.post("/api/auth/device-link/redeem", json={"code": mint["code"]})
    set_cookie = resp.headers.get("set-cookie", "")
    # Default device_link_session_max_age is 24h, not the 7-day desktop session.
    assert "Max-Age=86400" in set_cookie
    assert "samesite=strict" in set_cookie.lower()


# ─── Single-use / state machine ──────────────────────────────────────────────


async def test_replay_after_consume(admin_client, phone_client) -> None:
    mint = (await admin_client.post("/api/auth/device-link", json={})).json()
    code = mint["code"]
    await phone_client.post("/api/auth/device-link/redeem", json={"code": code})
    await admin_client.post(
        "/api/auth/device-link/approve", json={"id": mint["id"], "approve": True}
    )
    assert (await phone_client.post("/api/auth/device-link/redeem", json={"code": code})).json()[
        "status"
    ] == "approved"
    # A second attempt to redeem the same code is rejected as consumed.
    replay = await phone_client.post("/api/auth/device-link/redeem", json={"code": code})
    assert replay.json()["status"] == "consumed"


async def test_deny_blocks_redemption(admin_client, phone_client) -> None:
    mint = (await admin_client.post("/api/auth/device-link", json={})).json()
    await phone_client.post("/api/auth/device-link/redeem", json={"code": mint["code"]})
    deny = await admin_client.post(
        "/api/auth/device-link/approve", json={"id": mint["id"], "approve": False}
    )
    assert deny.json()["status"] == "denied"
    r = await phone_client.post("/api/auth/device-link/redeem", json={"code": mint["code"]})
    assert r.json()["status"] == "denied"


async def test_expired_code(admin_client, phone_client, db) -> None:
    from shoreguard.models import DeviceLinkCode

    mint = (await admin_client.post("/api/auth/device-link", json={})).json()
    # Force the code into the past.
    with db() as s:
        row = s.get(DeviceLinkCode, mint["id"])
        row.expires_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=10)
        s.commit()
    r = await phone_client.post("/api/auth/device-link/redeem", json={"code": mint["code"]})
    assert r.json()["status"] == "expired"


async def test_invalid_code(phone_client) -> None:
    r = await phone_client.post("/api/auth/device-link/redeem", json={"code": "not-a-real-code"})
    assert r.json()["status"] == "invalid"


async def test_approve_unknown_id_404(admin_client) -> None:
    r = await admin_client.post(
        "/api/auth/device-link/approve", json={"id": 999999, "approve": True}
    )
    assert r.status_code == 404


# ─── Authorization guards ────────────────────────────────────────────────────


async def test_mint_requires_auth(phone_client) -> None:
    # No session cookie → 401 from require_auth.
    resp = await phone_client.post("/api/auth/device-link", json={})
    assert resp.status_code == 401


# ─── CSRF / same-origin ──────────────────────────────────────────────────────


async def test_cross_origin_redeem_rejected(admin_client, phone_client) -> None:
    code = (await admin_client.post("/api/auth/device-link", json={})).json()["code"]
    # Forged cross-site fetch: browser would stamp Sec-Fetch-Site.
    r1 = await phone_client.post(
        "/api/auth/device-link/redeem",
        json={"code": code},
        headers={"sec-fetch-site": "cross-site"},
    )
    assert r1.status_code == 403
    # Or a mismatched Origin for non-Sec-Fetch clients.
    r2 = await phone_client.post(
        "/api/auth/device-link/redeem",
        json={"code": code},
        headers={"origin": "http://evil.example"},
    )
    assert r2.status_code == 403


async def test_same_origin_redeem_allowed(admin_client, phone_client) -> None:
    code = (await admin_client.post("/api/auth/device-link", json={})).json()["code"]
    r = await phone_client.post(
        "/api/auth/device-link/redeem",
        json={"code": code},
        headers={"sec-fetch-site": "same-origin"},
    )
    assert r.status_code == 200 and r.json()["status"] == "pending"


# ─── Service-level atomicity ─────────────────────────────────────────────────


async def test_decide_is_idempotent(admin_client, phone_client) -> None:
    from shoreguard.api.auth import device_link as dl

    mint = (await admin_client.post("/api/auth/device-link", json={})).json()
    await phone_client.post("/api/auth/device-link/redeem", json={"code": mint["code"]})
    # The issuing user is id 1 (first created).
    assert await dl.decide(mint["id"], 1, True) == "approved"
    # Second approval finds nothing claimable.
    assert await dl.decide(mint["id"], 1, True) == "not_found"
