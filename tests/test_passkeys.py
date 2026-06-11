"""Tests for passkey (WebAuthn) registration, login, and management."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from shoreguard.api.auth import passkeys as pk

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "adminpassword1"


@pytest.fixture
def db():
    from tests.conftest import make_auth_test_db

    factory, dispose = make_auth_test_db()
    yield factory
    dispose()


@pytest.fixture
async def admin_client(db):
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
async def anon_client(db):
    from shoreguard.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


# ─── Challenge store ─────────────────────────────────────────────────────────


def test_challenge_store_is_single_use() -> None:
    token = pk._store_challenge(b"chal", 1)
    assert pk._pop_challenge(token) == (b"chal", 1)
    assert pk._pop_challenge(token) is None


def test_challenge_store_expires(monkeypatch) -> None:
    token = pk._store_challenge(b"chal", None)
    entry = pk._challenges[token]
    pk._challenges[token] = (entry[0], entry[1], 0.0)
    assert pk._pop_challenge(token) is None


# ─── Registration ────────────────────────────────────────────────────────────


async def test_register_options_shape(admin_client) -> None:
    resp = await admin_client.post("/api/auth/passkeys/register/options")
    assert resp.status_code == 200
    data = resp.json()
    assert data["options"]["rp"]["id"] == "test"
    assert data["options"]["user"]["name"] == ADMIN_EMAIL
    assert data["options"]["challenge"]
    assert data["state"]


async def test_register_verify_stores_credential(admin_client, monkeypatch) -> None:
    options = (await admin_client.post("/api/auth/passkeys/register/options")).json()

    monkeypatch.setattr(
        "webauthn.verify_registration_response",
        lambda **kwargs: SimpleNamespace(
            credential_id=b"\x01\x02", credential_public_key=b"\x03\x04", sign_count=0
        ),
    )
    resp = await admin_client.post(
        "/api/auth/passkeys/register/verify",
        json={
            "state": options["state"],
            "credential": {"id": "AQI", "response": {"transports": ["internal"]}},
            "name": "Pixel 9",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Pixel 9"

    listing = (await admin_client.get("/api/auth/passkeys")).json()
    assert len(listing) == 1
    assert listing[0]["credential_id"] == "AQI"


async def test_register_verify_unknown_state_rejected(admin_client) -> None:
    resp = await admin_client.post(
        "/api/auth/passkeys/register/verify",
        json={"state": "bogus", "credential": {"id": "AQI"}, "name": "x"},
    )
    assert resp.status_code == 400
    assert "challenge" in resp.json()["detail"].lower()


async def test_delete_passkey(admin_client, monkeypatch) -> None:
    options = (await admin_client.post("/api/auth/passkeys/register/options")).json()
    monkeypatch.setattr(
        "webauthn.verify_registration_response",
        lambda **kwargs: SimpleNamespace(
            credential_id=b"\x01\x02", credential_public_key=b"\x03\x04", sign_count=0
        ),
    )
    await admin_client.post(
        "/api/auth/passkeys/register/verify",
        json={"state": options["state"], "credential": {"id": "AQI"}, "name": "k"},
    )
    listing = (await admin_client.get("/api/auth/passkeys")).json()

    resp = await admin_client.delete(f"/api/auth/passkeys/{listing[0]['id']}")
    assert resp.status_code == 200
    assert (await admin_client.get("/api/auth/passkeys")).json() == []


async def test_delete_unknown_passkey_404(admin_client) -> None:
    resp = await admin_client.delete("/api/auth/passkeys/999")
    assert resp.status_code == 404


# ─── Login ───────────────────────────────────────────────────────────────────


async def _register_credential(admin_client, monkeypatch) -> None:
    options = (await admin_client.post("/api/auth/passkeys/register/options")).json()
    monkeypatch.setattr(
        "webauthn.verify_registration_response",
        lambda **kwargs: SimpleNamespace(
            credential_id=b"\x01\x02", credential_public_key=b"\x03\x04", sign_count=0
        ),
    )
    resp = await admin_client.post(
        "/api/auth/passkeys/register/verify",
        json={"state": options["state"], "credential": {"id": "AQI"}, "name": "k"},
    )
    assert resp.status_code == 200


async def test_login_options_anonymous(anon_client) -> None:
    resp = await anon_client.post("/api/auth/login/passkey/options")
    assert resp.status_code == 200
    data = resp.json()
    assert data["options"]["challenge"]
    assert data["state"]


async def test_login_with_passkey_sets_session(admin_client, anon_client, monkeypatch) -> None:
    await _register_credential(admin_client, monkeypatch)

    options = (await anon_client.post("/api/auth/login/passkey/options")).json()
    monkeypatch.setattr(
        "webauthn.verify_authentication_response",
        lambda **kwargs: SimpleNamespace(new_sign_count=5),
    )
    resp = await anon_client.post(
        "/api/auth/login/passkey/verify",
        json={"state": options["state"], "credential": {"id": "AQI"}},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "role": "admin", "email": ADMIN_EMAIL}
    assert "sg_session" in resp.cookies

    # The minted session authenticates real API calls.
    me = await anon_client.get("/api/auth/passkeys")
    assert me.status_code == 200
    assert me.json()[0]["last_used"] is not None


async def test_login_unknown_credential_rejected(anon_client) -> None:
    options = (await anon_client.post("/api/auth/login/passkey/options")).json()
    resp = await anon_client.post(
        "/api/auth/login/passkey/verify",
        json={"state": options["state"], "credential": {"id": "nope"}},
    )
    assert resp.status_code == 401


async def test_login_reused_state_rejected(admin_client, anon_client, monkeypatch) -> None:
    await _register_credential(admin_client, monkeypatch)
    options = (await anon_client.post("/api/auth/login/passkey/options")).json()
    monkeypatch.setattr(
        "webauthn.verify_authentication_response",
        lambda **kwargs: SimpleNamespace(new_sign_count=1),
    )
    first = await anon_client.post(
        "/api/auth/login/passkey/verify",
        json={"state": options["state"], "credential": {"id": "AQI"}},
    )
    assert first.status_code == 200
    second = await anon_client.post(
        "/api/auth/login/passkey/verify",
        json={"state": options["state"], "credential": {"id": "AQI"}},
    )
    assert second.status_code == 401


# ─── Feature flag ────────────────────────────────────────────────────────────


async def test_passkeys_disabled_404(admin_client, monkeypatch) -> None:
    from shoreguard.settings import reset_settings

    monkeypatch.setenv("SHOREGUARD_PASSKEYS_ENABLED", "false")
    reset_settings()
    try:
        resp = await admin_client.post("/api/auth/passkeys/register/options")
        assert resp.status_code == 404
        resp = await admin_client.post("/api/auth/login/passkey/options")
        assert resp.status_code == 404
    finally:
        reset_settings()
