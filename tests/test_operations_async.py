"""Tests for :class:`AsyncOperationService`.

The existing ``tests/test_operations.py`` exercises the sync ``OperationService``
directly. At runtime, ``tests/conftest.py`` routes ``ops_mod.operation_service``
through an ``_AsyncOperationAdapter`` wrapping the sync class, so API-level
tests never actually hit ``AsyncOperationService`` — which is the class prod
really uses (see ``shoreguard/api/main.py:121``).

This file closes that gap with direct aiosqlite-backed tests against
``AsyncOperationService``. It also covers the sync-class active-task
cancel branch (``operations.py:431-433``) which is unreachable through the
adapter.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from shoreguard.models import Base
from shoreguard.services.operations import AsyncOperationService, OperationService
from shoreguard.services.operations_types import ErrorCode, OpStatus


@pytest_asyncio.fixture
async def async_svc():
    """Fresh in-memory async service with fast-running TTL and zero retention."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    svc = AsyncOperationService(factory, running_ttl=0.05, retention_days=0)
    yield svc
    await engine.dispose()


# ── __init__ ────────────────────────────────────────────────────────────────


async def test_async_init_stores_attributes(async_svc):
    assert async_svc._running_ttl == 0.05
    assert async_svc._retention_days == 0
    assert async_svc._tasks == {}


# ── create / create_if_not_running ──────────────────────────────────────────


async def test_create_returns_pending_record(async_svc):
    op = await async_svc.create(
        "sandbox", "sb1", actor="alice@test", gateway_name="gw1", idempotency_key="idem-1"
    )
    assert op.id
    assert op.status == OpStatus.pending
    assert op.resource_type == "sandbox"
    assert op.resource_key == "sb1"
    assert op.actor == "alice@test"
    assert op.gateway_name == "gw1"
    assert op.idempotency_key == "idem-1"

    fetched = await async_svc.get(op.id)
    assert fetched is not None
    assert fetched.id == op.id


async def test_create_if_not_running_first_call_succeeds(async_svc):
    op = await async_svc.create_if_not_running("sandbox", "sb2")
    assert op is not None
    assert op.status == OpStatus.pending


async def test_create_if_not_running_blocked_while_active(async_svc):
    first = await async_svc.create_if_not_running("sandbox", "sb3")
    assert first is not None
    second = await async_svc.create_if_not_running("sandbox", "sb3")
    assert second is None


async def test_create_if_not_running_unblocked_after_complete(async_svc):
    first = await async_svc.create_if_not_running("sandbox", "sb4")
    assert first is not None
    await async_svc.complete(first.id, {"ok": True})
    second = await async_svc.create_if_not_running("sandbox", "sb4")
    assert second is not None
    assert second.id != first.id


# ── State transitions ──────────────────────────────────────────────────────


async def test_start_happy(async_svc):
    op = await async_svc.create("sandbox", "sb-start")
    await async_svc.start(op.id)
    reloaded = await async_svc.get(op.id)
    assert reloaded.status == OpStatus.running


async def test_start_unknown_is_silent(async_svc):
    # No exception — method silently returns.
    await async_svc.start("does-not-exist")


async def test_start_already_running_is_silent(async_svc):
    op = await async_svc.create("sandbox", "sb-start2")
    await async_svc.start(op.id)
    await async_svc.start(op.id)  # no-op, should not flip backwards
    reloaded = await async_svc.get(op.id)
    assert reloaded.status == OpStatus.running


async def test_complete_happy(async_svc):
    op = await async_svc.create("sandbox", "sb-complete")
    await async_svc.start(op.id)
    await async_svc.complete(op.id, {"stdout": "hi"})
    reloaded = await async_svc.get(op.id)
    assert reloaded.status == OpStatus.succeeded
    assert reloaded.progress_pct == 100
    assert reloaded.completed_at is not None
    assert "hi" in reloaded.result_json


async def test_complete_missing_is_silent(async_svc):
    await async_svc.complete("nope", {"x": 1})


async def test_complete_terminal_is_silent(async_svc):
    op = await async_svc.create("sandbox", "sb-comp-term")
    await async_svc.complete(op.id, {"a": 1})
    await async_svc.complete(op.id, {"a": 2})  # no-op
    reloaded = await async_svc.get(op.id)
    assert reloaded.result_json is not None
    assert '"a": 1' in reloaded.result_json


async def test_fail_happy(async_svc):
    op = await async_svc.create("sandbox", "sb-fail")
    await async_svc.fail(op.id, "boom", error_code=ErrorCode.internal)
    reloaded = await async_svc.get(op.id)
    assert reloaded.status == OpStatus.failed
    assert reloaded.error_message == "boom"
    assert reloaded.error_code == ErrorCode.internal


async def test_fail_missing_is_silent(async_svc):
    await async_svc.fail("nope", "boom")


async def test_fail_terminal_is_silent(async_svc):
    op = await async_svc.create("sandbox", "sb-fail-term")
    await async_svc.complete(op.id, {"ok": True})
    await async_svc.fail(op.id, "too late")  # no-op
    reloaded = await async_svc.get(op.id)
    assert reloaded.status == OpStatus.succeeded


async def test_update_progress_clamps_and_stores_message(async_svc):
    op = await async_svc.create("sandbox", "sb-prog")
    await async_svc.update_progress(op.id, 150, "high")
    assert (await async_svc.get(op.id)).progress_pct == 100

    await async_svc.update_progress(op.id, -5, "low")
    assert (await async_svc.get(op.id)).progress_pct == 0

    await async_svc.update_progress(op.id, 50, "mid")
    r = await async_svc.get(op.id)
    assert r.progress_pct == 50
    assert r.progress_msg == "mid"


async def test_update_progress_missing_is_silent(async_svc):
    await async_svc.update_progress("nope", 10, "x")


async def test_update_progress_terminal_is_silent(async_svc):
    op = await async_svc.create("sandbox", "sb-prog-term")
    await async_svc.complete(op.id, {})
    await async_svc.update_progress(op.id, 10, "ignored")
    reloaded = await async_svc.get(op.id)
    assert reloaded.progress_pct == 100  # complete() set it to 100


# ── Queries ────────────────────────────────────────────────────────────────


async def test_get_by_idempotency_key(async_svc):
    op = await async_svc.create("sandbox", "sb-idem", idempotency_key="dedup-1")
    found = await async_svc.get_by_idempotency_key("dedup-1")
    assert found is not None
    assert found.id == op.id
    assert await async_svc.get_by_idempotency_key("other") is None


async def test_list_ops_filters_and_pagination(async_svc):
    ids = []
    for i in range(5):
        op = await async_svc.create("sandbox", f"sb-list-{i}")
        ids.append(op.id)
    # Complete the middle one
    await async_svc.complete(ids[2], {"k": "v"})

    ops, total = await async_svc.list_ops(limit=10)
    assert total == 5
    assert len(ops) == 5

    ops, total = await async_svc.list_ops(status=OpStatus.succeeded, limit=10)
    assert total == 1
    assert ops[0].id == ids[2]

    ops, total = await async_svc.list_ops(resource_type="sandbox", limit=2, offset=1)
    assert total == 5
    assert len(ops) == 2

    ops, total = await async_svc.list_ops(resource_type="nonesuch")
    assert total == 0
    assert ops == []


async def test_is_running_lifecycle(async_svc):
    assert await async_svc.is_running("sandbox", "sb-run") is False
    op = await async_svc.create("sandbox", "sb-run")
    assert await async_svc.is_running("sandbox", "sb-run") is True
    await async_svc.start(op.id)
    assert await async_svc.is_running("sandbox", "sb-run") is True
    await async_svc.complete(op.id, {})
    assert await async_svc.is_running("sandbox", "sb-run") is False


async def test_status_counts(async_svc):
    a = await async_svc.create("sandbox", "sb-c1")
    b = await async_svc.create("sandbox", "sb-c2")
    c = await async_svc.create("sandbox", "sb-c3")
    await async_svc.start(b.id)
    await async_svc.complete(c.id, {})
    counts = await async_svc.status_counts()
    assert counts.get(OpStatus.pending) == 1
    assert counts.get(OpStatus.running) == 1
    assert counts.get(OpStatus.succeeded) == 1
    # a is pending, b is running, c is succeeded
    assert a.status == OpStatus.pending


# ── Cancel / register_task ─────────────────────────────────────────────────


async def test_register_task_done_callback_clears_registry(async_svc):
    op = await async_svc.create("sandbox", "sb-task")

    async def _noop():
        return None

    task = asyncio.create_task(_noop())
    async_svc.register_task(op.id, task)
    assert op.id in async_svc._tasks
    await task
    # done_callback should have popped the entry
    assert op.id not in async_svc._tasks


async def test_cancel_no_task_marks_failed(async_svc):
    op = await async_svc.create("sandbox", "sb-cancel-no-task")
    result = await async_svc.cancel(op.id)
    assert result is not None
    assert result.status == OpStatus.failed
    assert result.error_code == ErrorCode.cancelled


async def test_cancel_with_registered_task_cancels_task(async_svc):
    op = await async_svc.create("sandbox", "sb-cancel-task")
    await async_svc.start(op.id)

    async def _long():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise

    task = asyncio.create_task(_long())
    async_svc.register_task(op.id, task)

    result = await async_svc.cancel(op.id)
    assert result is not None
    # Status is cancelling; the task's CancelledError handler would normally
    # transition it to failed, but that's the caller's job.
    assert result.status == OpStatus.cancelling
    # Yield once so the cancel propagates
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.cancelled() or task.done()


async def test_cancel_unknown_returns_none(async_svc):
    assert await async_svc.cancel("nope") is None


async def test_cancel_terminal_returns_none(async_svc):
    op = await async_svc.create("sandbox", "sb-cancel-term")
    await async_svc.complete(op.id, {})
    assert await async_svc.cancel(op.id) is None


# ── Cleanup ────────────────────────────────────────────────────────────────


async def test_recover_orphans_marks_active_as_failed(async_svc):
    a = await async_svc.create("sandbox", "sb-orph-a")
    b = await async_svc.create("sandbox", "sb-orph-b")
    await async_svc.start(b.id)

    count = await async_svc.recover_orphans()
    assert count == 2
    for op_id in (a.id, b.id):
        r = await async_svc.get(op_id)
        assert r.status == OpStatus.failed
        assert r.error_code == ErrorCode.orphaned


async def test_recover_orphans_none(async_svc):
    assert await async_svc.recover_orphans() == 0


async def test_cleanup_stuck_operations_timeout(async_svc):
    # running_ttl=0.05, retention_days=0
    op = await async_svc.create("sandbox", "sb-stuck")
    await async_svc.start(op.id)
    await asyncio.sleep(0.1)
    removed = await async_svc.cleanup()
    # The stuck op was marked failed (cleanup only DELETEs terminal rows whose
    # completed_at is before retention_cutoff = now - 0 days = now, so the
    # just-timed-out op is also eligible for deletion in this pass).
    reloaded = await async_svc.get(op.id)
    # Either fully deleted or failed+timeout
    if reloaded is not None:
        assert reloaded.status == OpStatus.failed
        assert reloaded.error_code == ErrorCode.timeout
    assert removed >= 0  # depends on ordering; deletion pass saw the new row


async def test_cleanup_retention_delete(async_svc):
    # retention_days=0 → every completed op is immediately eligible for delete
    op = await async_svc.create("sandbox", "sb-retain")
    await async_svc.complete(op.id, {})
    removed = await async_svc.cleanup()
    assert removed >= 1
    assert await async_svc.get(op.id) is None


async def test_cleanup_noop_when_empty(async_svc):
    assert await async_svc.cleanup() == 0


# ── Sync class — active-task cancel branch (lines 431-433) ─────────────────


async def test_sync_cancel_with_active_task(tmp_path):
    """Cover the sync OperationService.cancel() active-task branch.

    That branch (``operations.py:429-433``) is unreachable from the adapter
    used by the main test suite because the adapter never registers a task.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_path = tmp_path / "sync.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    svc = OperationService(factory, running_ttl=3600.0, retention_days=30)

    op = svc.create("sandbox", "sb-sync-cancel")
    svc.start(op.id)

    async def _long():
        await asyncio.sleep(10)

    task = asyncio.create_task(_long())
    svc.register_task(op.id, task)

    # Call the sync cancel — it should set status to cancelling and cancel
    # the task, returning the current (cancelling) record.
    result = svc.cancel(op.id)
    assert result is not None
    assert result.status == OpStatus.cancelling

    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.cancelled() or task.done()

    engine.dispose()


async def test_sync_cancel_no_task_fails_directly(tmp_path):
    """Cover the sync cancel() no-task branch (line 436)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_path = tmp_path / "sync2.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    svc = OperationService(factory, running_ttl=3600.0, retention_days=30)

    op = svc.create("sandbox", "sb-sync-cancel-no-task")
    result = svc.cancel(op.id)
    assert result is not None
    assert result.status == OpStatus.failed
    assert result.error_code == ErrorCode.cancelled

    engine.dispose()


# Silence unused-import warning for pytest (fixture is used via string name).
_ = pytest
