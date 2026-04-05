"""Shared test fixtures."""

from __future__ import annotations

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
    client.approvals = MagicMock()
    return client


@pytest.fixture(autouse=True)
def _init_gateway_service():
    """Initialize gateway_service and audit_service with shared in-memory DB."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import shoreguard.services.audit as audit_mod
    import shoreguard.services.gateway as gw_mod
    import shoreguard.services.sandbox_meta as sandbox_meta_mod
    from shoreguard.models import Base
    from shoreguard.services.gateway import _reset_clients
    from shoreguard.services.registry import GatewayRegistry

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    registry = GatewayRegistry(factory)
    gw_mod.gateway_service = gw_mod.GatewayService(registry)
    audit_mod.audit_service = audit_mod.AuditService(factory)
    sandbox_meta_mod.sandbox_meta_store = sandbox_meta_mod.SandboxMetaStore(factory)
    yield
    _reset_clients()
    audit_mod.audit_service = None
    sandbox_meta_mod.sandbox_meta_store = None
    engine.dispose()


@pytest.fixture(autouse=True)
def _disable_auth():
    """Reset auth state so tests start without authentication by default.

    Sets ``_no_auth = True`` so routes that check auth get admin access
    without requiring a DB-backed session factory.
    """
    from shoreguard.api import auth

    auth.reset()
    auth._no_auth = True  # noqa: SLF001
    yield
    auth.reset()


@pytest.fixture(autouse=True)
def _reset_operations():
    """Reset operation store between tests."""
    from shoreguard.services.operations import operation_store

    operation_store._reset()
    yield
    operation_store._reset()


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
