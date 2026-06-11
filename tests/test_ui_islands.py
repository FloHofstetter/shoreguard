"""The migrated users page serves the island mount and bundle tag."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from shoreguard.api.auth import create_user


@pytest.fixture
def db():

    from tests.conftest import make_auth_test_db

    factory, dispose = make_auth_test_db()
    yield factory
    dispose()


async def test_users_page_serves_island(db):
    from shoreguard.api.main import app

    await create_user("admin@test.com", "adminpass123", "admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/auth/login", json={"email": "admin@test.com", "password": "adminpass123"}
        )
        assert resp.status_code == 200
        resp = await client.get("/users")
        assert resp.status_code == 200
        assert 'data-island="users-page"' in resp.text
        assert "/static/dist/main.js" in resp.text
        # legacy per-page script is gone from the migrated page
        assert "/static/js/users.js" not in resp.text
