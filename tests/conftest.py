"""Shared test fixtures."""

from __future__ import annotations

import asyncio
import contextlib
import multiprocessing
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from shoreguard.client import ShoreGuardClient

# Workaround for mutmut v3: its __main__.py calls set_start_method('fork')
# at import time, which crashes when imported inside an asyncio worker thread.
# Pre-set the method so mutmut's call becomes a no-op (already set = same value).
try:
    multiprocessing.set_start_method("fork", force=True)
except RuntimeError:
    pass


@pytest.fixture
def mock_client():
    """Create a mock ShoreGuardClient with nested manager mocks."""
    client = MagicMock(spec=ShoreGuardClient)
    client.sandboxes = MagicMock()
    client.policies = MagicMock()
    client.providers = MagicMock()
    client.provider_profiles = MagicMock()
    client.approvals = MagicMock()
    client.services = MagicMock()
    return client


@pytest.fixture(autouse=True)
async def container():
    """Build and install a ServiceContainer against in-memory engines.

    Uses the same :func:`shoreguard.container.build_container` code path
    as the production lifespan, so a service added to the container is
    automatically wired in tests too.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from shoreguard.container import build_container, install, uninstall
    from shoreguard.models import Base
    from shoreguard.settings import Settings

    sync_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(sync_engine)
    sync_factory = sessionmaker(bind=sync_engine)

    async_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_factory = async_sessionmaker(async_engine, expire_on_commit=False)

    # Fresh Settings instance instead of get_settings(): the singleton must
    # stay unset so tests that monkeypatch.setenv still see their values.
    container = build_container(Settings(), sync_factory, async_factory)
    install(container)

    yield container

    # Drain any still-running LRO background tasks before disposing the
    # engine, otherwise a late progress-update hits a closed DB.
    pending = list(container.operations._tasks.values())
    for task in pending:
        if not task.done():
            task.cancel()
    for task in pending:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    uninstall()
    sync_engine.dispose()
    await async_engine.dispose()


@pytest.fixture(autouse=True)
def _disable_auth():
    """Reset auth state so tests start without authentication by default.

    Sets ``_no_auth = True`` so routes that check auth get admin access
    without requiring a DB-backed session factory.

    Clears the Settings singleton before each test so that
    ``monkeypatch.setenv`` changes are picked up by the next
    ``get_settings()`` call.
    """
    from shoreguard.api import auth
    from shoreguard.api.ratelimit import reset_limiters
    from shoreguard.config import _always_blocked_networks, _ssrf_allowed_networks
    from shoreguard.settings import reset_settings

    reset_settings()
    _always_blocked_networks.cache_clear()
    _ssrf_allowed_networks.cache_clear()
    auth.reset()
    reset_limiters()
    auth.state.no_auth = True  # noqa: SLF001
    yield
    auth.reset()
    reset_limiters()
    reset_settings()
    _always_blocked_networks.cache_clear()
    _ssrf_allowed_networks.cache_clear()


@pytest.fixture
async def api_client(mock_client):
    """Async HTTP client for testing FastAPI routes with mocked gateway."""
    from shoreguard.api.deps import get_client
    from shoreguard.api.main import app

    app.dependency_overrides[get_client] = lambda: mock_client
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
    app.dependency_overrides.clear()
