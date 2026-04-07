"""Shared test fixtures."""

from __future__ import annotations

import asyncio
import multiprocessing
from typing import Any
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from shoreguard.client import ShoreGuardClient
from shoreguard.models import OperationRecord

# Workaround for mutmut v3: its __main__.py calls set_start_method('fork')
# at import time, which crashes when imported inside an asyncio worker thread.
# Pre-set the method so mutmut's call becomes a no-op (already set = same value).
try:
    multiprocessing.set_start_method("fork", force=True)
except RuntimeError:
    pass


class _AsyncOperationAdapter:
    """Wraps the sync OperationService so routes can ``await`` its methods.

    Used in tests where we don't want the aiosqlite dependency for the
    in-memory database but need the async call interface that the routes
    and ``run_lro`` helper expect.
    """

    def __init__(self, sync_svc):
        self._svc = sync_svc

    # Async wrappers for all methods called by routes / run_lro.

    async def create(self, *args, **kwargs) -> OperationRecord:
        return self._svc.create(*args, **kwargs)

    async def create_if_not_running(self, *args, **kwargs) -> OperationRecord | None:
        return self._svc.create_if_not_running(*args, **kwargs)

    async def start(self, *args, **kwargs) -> None:
        self._svc.start(*args, **kwargs)

    async def complete(self, *args, **kwargs) -> None:
        self._svc.complete(*args, **kwargs)

    async def fail(self, *args, **kwargs) -> None:
        self._svc.fail(*args, **kwargs)

    async def update_progress(self, *args, **kwargs) -> None:
        self._svc.update_progress(*args, **kwargs)

    async def get(self, *args, **kwargs) -> OperationRecord | None:
        return self._svc.get(*args, **kwargs)

    async def get_by_idempotency_key(self, *args, **kwargs) -> OperationRecord | None:
        return self._svc.get_by_idempotency_key(*args, **kwargs)

    async def list_ops(self, *args, **kwargs) -> tuple[list[OperationRecord], int]:
        return self._svc.list_ops(*args, **kwargs)

    async def is_running(self, *args, **kwargs) -> bool:
        return self._svc.is_running(*args, **kwargs)

    async def status_counts(self) -> dict[str, int]:
        return self._svc.status_counts()

    def register_task(self, op_id: str, task: asyncio.Task[None]) -> None:
        self._svc.register_task(op_id, task)

    async def cancel(self, *args, **kwargs) -> OperationRecord | None:
        return self._svc.cancel(*args, **kwargs)

    async def recover_orphans(self) -> int:
        return self._svc.recover_orphans()

    async def cleanup(self) -> int:
        return self._svc.cleanup()

    @staticmethod
    def to_dict(op: OperationRecord) -> dict[str, Any]:
        from shoreguard.services.operations import OperationService

        return OperationService.to_dict(op)

    # Expose internal session factory for test cleanup.
    @property
    def _session_factory(self):
        return self._svc._session_factory


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
    import shoreguard.services.operations as ops_mod
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
    sync_ops = ops_mod.OperationService(factory)
    ops_mod.operation_service = _AsyncOperationAdapter(sync_ops)
    yield
    _reset_clients()
    audit_mod.audit_service = None
    sandbox_meta_mod.sandbox_meta_store = None
    ops_mod.operation_service = None
    engine.dispose()


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
    from shoreguard.settings import reset_settings

    reset_settings()
    auth.reset()
    auth._no_auth = True  # noqa: SLF001
    yield
    auth.reset()
    reset_settings()


@pytest.fixture(autouse=True)
def _reset_operations():
    """Clean operation records between tests."""
    from shoreguard.services.operations import operation_service

    if operation_service is not None:
        from shoreguard.models import OperationRecord

        with operation_service._session_factory() as session:
            session.query(OperationRecord).delete()
            session.commit()
    yield
    if operation_service is not None:
        from shoreguard.models import OperationRecord

        with operation_service._session_factory() as session:
            session.query(OperationRecord).delete()
            session.commit()


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
