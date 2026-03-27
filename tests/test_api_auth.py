"""Tests for authentication routes and middleware."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from shoreguard.api import auth

TEST_KEY = "test-api-key-abc123"


@pytest.fixture(autouse=True)
def _enable_auth():
    """Enable auth for all tests in this module."""
    auth.configure(TEST_KEY)
    yield
    auth.configure(None)


@pytest.fixture
async def client():
    from shoreguard.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.fixture
async def authed_client():
    """Client with a valid session cookie."""
    from shoreguard.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        resp = await c.post("/api/auth/login", json={"key": TEST_KEY})
        assert resp.status_code == 200
        # Cookies are automatically stored on the client
        yield c


# ─── Login endpoint ──────────────────────────────────────────────────────────


async def test_login_success(client):
    resp = await client.post("/api/auth/login", json={"key": TEST_KEY})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert "sg_session" in resp.cookies


async def test_login_wrong_key(client):
    resp = await client.post("/api/auth/login", json={"key": "wrong"})
    assert resp.status_code == 401
    assert "sg_session" not in resp.cookies


async def test_login_disabled():
    auth.configure(None)
    from shoreguard.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/auth/login", json={"key": "anything"})
    assert resp.status_code == 400


# ─── Logout endpoint ─────────────────────────────────────────────────────────


async def test_logout(authed_client):
    resp = await authed_client.post("/api/auth/logout")
    assert resp.status_code == 200


# ─── Auth check endpoint ─────────────────────────────────────────────────────


async def test_auth_check_unauthenticated(client):
    resp = await client.get("/api/auth/check")
    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is False
    assert data["auth_enabled"] is True


async def test_auth_check_with_cookie(authed_client):
    resp = await authed_client.get("/api/auth/check")
    assert resp.status_code == 200
    assert resp.json()["authenticated"] is True


async def test_auth_check_with_bearer(client):
    resp = await client.get("/api/auth/check", headers={"Authorization": f"Bearer {TEST_KEY}"})
    assert resp.status_code == 200
    assert resp.json()["authenticated"] is True


async def test_auth_check_disabled():
    auth.configure(None)
    from shoreguard.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/auth/check")
    data = resp.json()
    assert data["authenticated"] is True
    assert data["auth_enabled"] is False


# ─── API route protection ────────────────────────────────────────────────────


async def test_api_route_rejected_without_auth(client):
    resp = await client.get("/api/gateway/list")
    assert resp.status_code == 401


async def test_api_route_allowed_with_bearer(client):
    resp = await client.get("/api/gateway/list", headers={"Authorization": f"Bearer {TEST_KEY}"})
    # May be 200 or 503 (no gateway) — but NOT 401
    assert resp.status_code != 401


async def test_api_route_allowed_with_cookie(authed_client):
    resp = await authed_client.get("/api/gateway/list")
    assert resp.status_code != 401


# ─── Page route protection ───────────────────────────────────────────────────


async def test_page_redirects_to_login(client):
    resp = await client.get("/gateways", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]


async def test_page_accessible_with_cookie(authed_client):
    resp = await authed_client.get("/gateways", follow_redirects=False)
    # Should NOT redirect to login
    assert resp.status_code == 200


async def test_login_page_always_accessible(client):
    resp = await client.get("/login")
    assert resp.status_code == 200
    assert "Shoreguard" in resp.text


# ─── WebSocket auth ─────────────────────────────────────────────────────────


async def test_ws_auth_valid_token(client):
    from starlette.testclient import TestClient

    from shoreguard.api.main import app

    with TestClient(app) as tc:
        with tc.websocket_connect(f"/ws/test-gw/test-sb?token={TEST_KEY}"):
            # Connection accepted — auth passed
            pass


async def test_ws_auth_invalid_token():
    from starlette.testclient import TestClient

    from shoreguard.api.main import app

    with TestClient(app) as tc:
        with pytest.raises(Exception):
            with tc.websocket_connect("/ws/test-gw/test-sb?token=wrong"):
                pass


async def test_ws_auth_valid_cookie(authed_client):
    from starlette.testclient import TestClient

    from shoreguard.api.main import app

    # Get a valid session cookie
    cookie = authed_client.cookies.get("sg_session")
    with TestClient(app, cookies={"sg_session": cookie}) as tc:
        with tc.websocket_connect("/ws/test-gw/test-sb"):
            # Connection accepted — auth passed via cookie
            pass


async def test_ws_auth_no_credentials():
    from starlette.testclient import TestClient

    from shoreguard.api.main import app

    with TestClient(app) as tc:
        with pytest.raises(Exception):
            with tc.websocket_connect("/ws/test-gw/test-sb"):
                pass


async def test_ws_auth_disabled():
    auth.configure(None)
    from starlette.testclient import TestClient

    from shoreguard.api.main import app

    with TestClient(app) as tc:
        with tc.websocket_connect("/ws/test-gw/test-sb"):
            # Connection accepted — auth disabled
            pass


# ─── Bearer edge cases ──────────────────────────────────────────────────────


async def test_bearer_empty_token(client):
    resp = await client.get("/api/auth/check", headers={"Authorization": "Bearer "})
    assert resp.json()["authenticated"] is False


async def test_bearer_lowercase_scheme(client):
    resp = await client.get("/api/auth/check", headers={"Authorization": f"bearer {TEST_KEY}"})
    assert resp.json()["authenticated"] is True


async def test_bearer_uppercase_scheme(client):
    resp = await client.get("/api/auth/check", headers={"Authorization": f"BEARER {TEST_KEY}"})
    assert resp.json()["authenticated"] is True


async def test_bearer_mixed_case_scheme(client):
    resp = await client.get("/api/auth/check", headers={"Authorization": f"BeArEr {TEST_KEY}"})
    assert resp.json()["authenticated"] is True


async def test_bearer_double_space(client):
    resp = await client.get("/api/auth/check", headers={"Authorization": f"Bearer  {TEST_KEY}"})
    # Double space means token starts with a space — should NOT match
    assert resp.json()["authenticated"] is False


# ─── Page redirect URL encoding ─────────────────────────────────────────────


async def test_page_redirect_includes_next(client):
    resp = await client.get("/gateways", follow_redirects=False)
    assert resp.status_code == 302
    assert "next=/gateways" in resp.headers["location"]


async def test_page_redirect_encodes_special_chars(client):
    resp = await client.get("/gateways/my%20gw", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "next=" in location
    # Ensure no double-slash injection possible
    assert "//" not in location.split("next=")[1]
