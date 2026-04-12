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
    client.approvals = MagicMock()
    return client


@pytest.fixture(autouse=True)
async def _init_gateway_service():
    """Initialize gateway, audit, sandbox-meta, and operations services per test.

    Uses a sync in-memory SQLite engine for the services that still use sync
    SQLAlchemy (gateway registry, audit, sandbox meta), and a separate
    async aiosqlite engine for ``AsyncOperationService`` — the class used
    in production (see ``shoreguard/api/main.py``).
    """
    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import shoreguard.services.approval_workflow as wf_mod
    import shoreguard.services.audit as audit_mod
    import shoreguard.services.gateway as gw_mod
    import shoreguard.services.operations as ops_mod
    import shoreguard.services.policy_pin as pin_mod
    import shoreguard.services.sandbox_meta as sandbox_meta_mod
    import shoreguard.services.sbom as sbom_mod
    from shoreguard.models import Base
    from shoreguard.services.gateway import _reset_clients
    from shoreguard.services.registry import GatewayRegistry

    # Sync engine for services that still use sync SQLAlchemy.
    sync_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(sync_engine)
    sync_factory = sessionmaker(bind=sync_engine)
    registry = GatewayRegistry(sync_factory)
    gw_mod.gateway_service = gw_mod.GatewayService(registry)
    audit_mod.audit_service = audit_mod.AuditService(sync_factory)
    sandbox_meta_mod.sandbox_meta_store = sandbox_meta_mod.SandboxMetaStore(sync_factory)
    pin_mod.policy_pin_service = pin_mod.PolicyPinService(sync_factory)
    wf_mod.approval_workflow_service = wf_mod.ApprovalWorkflowService(sync_factory)
    sbom_mod.sbom_service = sbom_mod.SBOMService(sync_factory)

    # Async engine for AsyncOperationService — the prod class (see api/main.py).
    async_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_factory = async_sessionmaker(async_engine, expire_on_commit=False)
    ops_mod.operation_service = ops_mod.AsyncOperationService(async_factory)

    yield

    # Drain any still-running LRO background tasks before disposing the
    # engine, otherwise a late progress-update hits a closed DB.
    pending = list(ops_mod.operation_service._tasks.values())  # type: ignore[union-attr]
    for task in pending:
        if not task.done():
            task.cancel()
    for task in pending:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    _reset_clients()
    audit_mod.audit_service = None
    sandbox_meta_mod.sandbox_meta_store = None
    ops_mod.operation_service = None
    pin_mod.policy_pin_service = None
    wf_mod.approval_workflow_service = None
    sbom_mod.sbom_service = None
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
    from shoreguard.api.ratelimit import reset_login_limiter
    from shoreguard.settings import reset_settings

    reset_settings()
    auth.reset()
    reset_login_limiter()
    auth._no_auth = True  # noqa: SLF001
    yield
    auth.reset()
    reset_login_limiter()
    reset_settings()


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
