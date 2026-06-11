"""Tests for session tracking: listing and revoking active sessions."""

from __future__ import annotations

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


async def _client() -> AsyncClient:
    from shoreguard.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login(client: AsyncClient, email=ADMIN_EMAIL, password=ADMIN_PASS) -> None:
    resp = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200


# ─── Recording ───────────────────────────────────────────────────────────────


async def test_login_records_a_session(db) -> None:
    from shoreguard.api.auth import create_user

    await create_user(ADMIN_EMAIL, ADMIN_PASS, "admin")
    async with await _client() as c:
        await _login(c)
        sessions = (await c.get("/api/auth/sessions")).json()["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["kind"] == "password"
        assert sessions[0]["current"] is True


async def test_two_logins_two_sessions(db) -> None:
    from shoreguard.api.auth import create_user

    await create_user(ADMIN_EMAIL, ADMIN_PASS, "admin")
    async with await _client() as c1, await _client() as c2:
        await _login(c1)
        await _login(c2)
        # Each client sees both sessions; exactly one is "current".
        s1 = (await c1.get("/api/auth/sessions")).json()["sessions"]
        assert len(s1) == 2
        assert sum(1 for s in s1 if s["current"]) == 1


# ─── Revocation ──────────────────────────────────────────────────────────────


async def test_revoke_other_session_logs_it_out(db) -> None:
    from shoreguard.api.auth import create_user

    await create_user(ADMIN_EMAIL, ADMIN_PASS, "admin")
    async with await _client() as c1, await _client() as c2:
        await _login(c1)
        await _login(c2)
        # c1 finds c2's (non-current) session and revokes it.
        sessions = (await c1.get("/api/auth/sessions")).json()["sessions"]
        other = next(s for s in sessions if not s["current"])
        resp = await c1.delete(f"/api/auth/sessions/{other['id']}")
        assert resp.status_code == 200
        # c2 is now signed out; c1 still works.
        assert (await c2.get("/api/auth/check")).json()["authenticated"] is False
        assert (await c1.get("/api/auth/check")).json()["authenticated"] is True


async def test_revoke_others_keeps_current(db) -> None:
    from shoreguard.api.auth import create_user

    await create_user(ADMIN_EMAIL, ADMIN_PASS, "admin")
    async with await _client() as c1, await _client() as c2, await _client() as c3:
        await _login(c1)
        await _login(c2)
        await _login(c3)
        r = await c1.post("/api/auth/sessions/revoke-others")
        assert r.json()["revoked"] == 2
        assert (await c1.get("/api/auth/check")).json()["authenticated"] is True
        assert (await c2.get("/api/auth/check")).json()["authenticated"] is False
        assert (await c3.get("/api/auth/check")).json()["authenticated"] is False
        # Only the current session remains.
        assert len((await c1.get("/api/auth/sessions")).json()["sessions"]) == 1


async def test_logout_revokes_current_session(db) -> None:
    from shoreguard.api.auth import create_user

    await create_user(ADMIN_EMAIL, ADMIN_PASS, "admin")
    async with await _client() as c1, await _client() as c2:
        await _login(c1)
        await _login(c2)
        await c1.post("/api/auth/logout")
        # From c2's view, only its own session remains.
        sessions = (await c2.get("/api/auth/sessions")).json()["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["current"] is True


async def test_revoke_unknown_session_404(db) -> None:
    from shoreguard.api.auth import create_user

    await create_user(ADMIN_EMAIL, ADMIN_PASS, "admin")
    async with await _client() as c:
        await _login(c)
        assert (await c.delete("/api/auth/sessions/999999")).status_code == 404


async def test_cannot_revoke_another_users_session(db) -> None:
    from shoreguard.api.auth import create_user

    await create_user(ADMIN_EMAIL, ADMIN_PASS, "admin")
    await create_user("viewer@test.com", "viewerpass1", "viewer")
    async with await _client() as admin, await _client() as viewer:
        await _login(admin)
        await _login(viewer, "viewer@test.com", "viewerpass1")
        admin_sess = (await admin.get("/api/auth/sessions")).json()["sessions"][0]
        # The viewer cannot revoke the admin's session id.
        resp = await viewer.delete(f"/api/auth/sessions/{admin_sess['id']}")
        assert resp.status_code == 404
        assert (await admin.get("/api/auth/check")).json()["authenticated"] is True


# ─── Tracking disabled ───────────────────────────────────────────────────────


async def test_tracking_disabled_no_rows(db, monkeypatch) -> None:
    from shoreguard.api.auth import create_user
    from shoreguard.settings import reset_settings

    monkeypatch.setenv("SHOREGUARD_SESSION_TRACKING", "false")
    reset_settings()
    await create_user(ADMIN_EMAIL, ADMIN_PASS, "admin")
    async with await _client() as c:
        await _login(c)
        # Auth still works, but nothing is recorded.
        assert (await c.get("/api/auth/check")).json()["authenticated"] is True
        assert (await c.get("/api/auth/sessions")).json()["sessions"] == []
    reset_settings()


async def test_unrecorded_cookie_still_authenticates(db) -> None:
    """Denylist semantics: a forged-but-valid cookie with no ledger row
    still authenticates (no forced logout on upgrade)."""
    from shoreguard.api.auth import create_session_token, create_user

    info = await create_user(ADMIN_EMAIL, ADMIN_PASS, "admin")
    token = create_session_token(user_id=info["id"], role="admin")
    async with await _client() as c:
        c.cookies.set("sg_session", token)
        check = (await c.get("/api/auth/check")).json()
        assert check["authenticated"] is True
        # It has no ledger row, so it is not listed.
        assert (await c.get("/api/auth/sessions")).json()["sessions"] == []
