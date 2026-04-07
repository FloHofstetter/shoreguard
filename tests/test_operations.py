"""Tests for the DB-backed OperationService."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.models import Base, OperationRecord
from shoreguard.services.operations import OperationService
from shoreguard.services.operations_types import ErrorCode, OpStatus


def _make_service(**kwargs) -> OperationService:
    """Create a fresh OperationService with an in-memory SQLite DB."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    return OperationService(factory, **kwargs)


# ── Create ────────────────────────────────────────────────────────────────


def test_create_and_get():
    svc = _make_service()
    op = svc.create("gateway", "my-gw")
    assert op.status == OpStatus.pending
    assert op.resource_type == "gateway"
    assert op.resource_key == "my-gw"
    assert op.created_at is not None
    assert op.updated_at is not None

    fetched = svc.get(op.id)
    assert fetched is not None
    assert fetched.id == op.id


def test_create_with_actor_and_gateway():
    svc = _make_service()
    op = svc.create("sandbox", "my-sb", actor="admin@test.com", gateway_name="dev")
    assert op.actor == "admin@test.com"
    assert op.gateway_name == "dev"


# ── Start (pending → running) ────────────────────────────────────────────


def test_start_transitions_pending_to_running():
    svc = _make_service()
    op = svc.create("sandbox", "sb1")
    assert op.status == OpStatus.pending

    svc.start(op.id)
    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.running


def test_start_non_pending_is_noop():
    svc = _make_service()
    op = svc.create("sandbox", "sb1")
    svc.start(op.id)
    svc.start(op.id)  # already running — should be no-op
    assert svc.get(op.id).status == OpStatus.running


# ── Complete ──────────────────────────────────────────────────────────────


def test_complete_operation():
    svc = _make_service()
    op = svc.create("sandbox", "my-sb")
    svc.start(op.id)
    svc.complete(op.id, {"name": "my-sb", "phase": "ready"})

    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.succeeded
    assert fetched.progress_pct == 100
    assert json.loads(fetched.result_json) == {"name": "my-sb", "phase": "ready"}
    assert fetched.completed_at is not None


def test_complete_from_pending():
    """Operations can be completed directly from pending (fast-path)."""
    svc = _make_service()
    op = svc.create("exec", "cmd")
    svc.complete(op.id, {"exit_code": 0})
    assert svc.get(op.id).status == OpStatus.succeeded


def test_complete_non_running_is_noop():
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)
    svc.fail(op.id, "error")
    svc.complete(op.id, {"should": "not update"})
    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.failed
    assert fetched.result_json is None


# ── Fail ──────────────────────────────────────────────────────────────────


def test_fail_operation():
    svc = _make_service()
    op = svc.create("gateway", "fail-gw")
    svc.start(op.id)
    svc.fail(op.id, "Docker not running", error_code=ErrorCode.internal)

    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.failed
    assert fetched.error_message == "Docker not running"
    assert fetched.error_code == ErrorCode.internal
    assert fetched.completed_at is not None


def test_fail_non_running_is_noop():
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)
    svc.complete(op.id, {"ok": True})
    svc.fail(op.id, "too late")
    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.succeeded


def test_fail_from_cancelling():
    """Operations in cancelling state can be failed (cancel completion)."""
    svc = _make_service()
    op = svc.create("sandbox", "sb1")
    svc.start(op.id)
    # Simulate cancel setting cancelling state directly
    with svc._session_factory() as session:
        rec = session.get(OperationRecord, op.id)
        rec.status = OpStatus.cancelling
        session.commit()

    svc.fail(op.id, "Cancelled", error_code=ErrorCode.cancelled)
    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.failed
    assert fetched.error_code == ErrorCode.cancelled


# ── Queries ───────────────────────────────────────────────────────────────


def test_get_unknown_returns_none():
    svc = _make_service()
    assert svc.get("nonexistent-id") is None


def test_is_running():
    svc = _make_service()
    assert svc.is_running("gateway", "gw1") is False

    op = svc.create("gateway", "gw1")
    # Pending counts as active for is_running.
    assert svc.is_running("gateway", "gw1") is True
    assert svc.is_running("gateway", "gw2") is False
    assert svc.is_running("sandbox", "gw1") is False

    svc.start(op.id)
    assert svc.is_running("gateway", "gw1") is True

    svc.complete(op.id, {})
    assert svc.is_running("gateway", "gw1") is False


def test_update_progress():
    svc = _make_service()
    op = svc.create("sandbox", "sb1")
    svc.start(op.id)
    svc.update_progress(op.id, 50, "Halfway there")

    fetched = svc.get(op.id)
    assert fetched.progress_pct == 50
    assert fetched.progress_msg == "Halfway there"


def test_update_progress_clamps():
    svc = _make_service()
    op = svc.create("sandbox", "sb1")
    svc.start(op.id)
    svc.update_progress(op.id, 150)
    assert svc.get(op.id).progress_pct == 100
    svc.update_progress(op.id, -10)
    assert svc.get(op.id).progress_pct == 0


# ── Idempotency key ──────────────────────────────────────────────────────


def test_get_by_idempotency_key():
    svc = _make_service()
    op = svc.create("sandbox", "sb1", idempotency_key="key-123")
    found = svc.get_by_idempotency_key("key-123")
    assert found is not None
    assert found.id == op.id


def test_get_by_idempotency_key_not_found():
    svc = _make_service()
    assert svc.get_by_idempotency_key("nonexistent") is None


# ── create_if_not_running ────────────────────────────────────────────────


def test_create_if_not_running_success():
    svc = _make_service()
    op = svc.create_if_not_running("gateway", "gw1")
    assert op is not None
    assert op.status == OpStatus.pending
    assert svc.get(op.id) is not None


def test_create_if_not_running_blocked():
    svc = _make_service()
    op1 = svc.create_if_not_running("gateway", "gw1")
    assert op1 is not None

    op2 = svc.create_if_not_running("gateway", "gw1")
    assert op2 is None


def test_create_if_not_running_blocked_by_running():
    """Also blocked when existing op is in running state."""
    svc = _make_service()
    op1 = svc.create_if_not_running("gateway", "gw1")
    svc.start(op1.id)

    op2 = svc.create_if_not_running("gateway", "gw1")
    assert op2 is None


def test_create_if_not_running_different_resource():
    svc = _make_service()
    op1 = svc.create_if_not_running("gateway", "gw1")
    assert op1 is not None

    op2 = svc.create_if_not_running("gateway", "gw2")
    assert op2 is not None


def test_create_if_not_running_after_complete():
    svc = _make_service()
    op1 = svc.create_if_not_running("gateway", "gw1")
    svc.start(op1.id)
    svc.complete(op1.id, {})

    op2 = svc.create_if_not_running("gateway", "gw1")
    assert op2 is not None
    assert op2.id != op1.id


# ── List & counts ────────────────────────────────────────────────────────


def test_list_ops():
    svc = _make_service()
    svc.create("sandbox", "sb1")
    svc.create("sandbox", "sb2")
    op3 = svc.create("gateway", "gw1")
    svc.start(op3.id)
    svc.complete(op3.id, {})

    ops, total = svc.list_ops()
    assert total == 3
    assert len(ops) == 3

    # Filter by status — pending ops
    ops, total = svc.list_ops(status="pending")
    assert total == 2

    # Filter by resource_type
    ops, total = svc.list_ops(resource_type="gateway")
    assert total == 1

    # Pagination
    ops, total = svc.list_ops(limit=1, offset=0)
    assert len(ops) == 1
    assert total == 3


def test_list_ops_ordered_by_created_at():
    svc = _make_service()
    op1 = svc.create("sandbox", "sb1")
    op2 = svc.create("sandbox", "sb2")
    ops, _ = svc.list_ops()
    assert ops[0].id == op2.id
    assert ops[1].id == op1.id


def test_status_counts():
    svc = _make_service()
    svc.create("sandbox", "sb1")
    svc.create("sandbox", "sb2")
    op3 = svc.create("gateway", "gw1")
    svc.start(op3.id)
    svc.complete(op3.id, {})
    op4 = svc.create("gateway", "gw2")
    svc.start(op4.id)
    svc.fail(op4.id, "error")

    counts = svc.status_counts()
    assert counts[OpStatus.pending] == 2
    assert counts[OpStatus.succeeded] == 1
    assert counts[OpStatus.failed] == 1


# ── Cancel ────────────────────────────────────────────────────────────────


def test_cancel_no_task():
    """Cancel without an active asyncio task marks the op as failed directly."""
    svc = _make_service()
    op = svc.create("sandbox", "sb1")
    svc.start(op.id)
    result = svc.cancel(op.id)
    assert result is not None
    assert result.status == OpStatus.failed
    assert result.error_code == ErrorCode.cancelled


def test_cancel_pending_operation():
    """Pending operations can be cancelled too."""
    svc = _make_service()
    op = svc.create("sandbox", "sb1")
    result = svc.cancel(op.id)
    assert result is not None
    assert result.status == OpStatus.failed
    assert result.error_code == ErrorCode.cancelled


def test_cancel_sets_cancelling_state():
    """Cancel first transitions to cancelling before failing."""
    svc = _make_service()
    op = svc.create("sandbox", "sb1")
    svc.start(op.id)

    # Peek at intermediate state by overriding fail to check
    states_seen = []
    original_fail = svc.fail

    def tracking_fail(op_id, error, error_code=ErrorCode.internal):
        fetched = svc.get(op_id)
        states_seen.append(fetched.status)
        original_fail(op_id, error, error_code=error_code)

    svc.fail = tracking_fail
    svc.cancel(op.id)
    # The cancel() sets cancelling, then since no task, calls fail() directly.
    # At the point fail() is called, the op should be in cancelling state.
    assert OpStatus.cancelling in states_seen


def test_cancel_not_running_returns_none():
    svc = _make_service()
    op = svc.create("sandbox", "sb1")
    svc.start(op.id)
    svc.complete(op.id, {})
    result = svc.cancel(op.id)
    assert result is None


def test_cancel_unknown_returns_none():
    svc = _make_service()
    assert svc.cancel("nonexistent") is None


# ── Cleanup ───────────────────────────────────────────────────────────────


def test_recover_orphans():
    svc = _make_service()
    op1 = svc.create("sandbox", "sb1")
    svc.start(op1.id)
    op2 = svc.create("gateway", "gw1")
    svc.start(op2.id)
    svc.complete(op2.id, {})

    count = svc.recover_orphans()
    assert count == 1

    fetched = svc.get(op1.id)
    assert fetched.status == OpStatus.failed
    assert fetched.error_code == ErrorCode.orphaned

    fetched2 = svc.get(op2.id)
    assert fetched2.status == OpStatus.succeeded


def test_recover_orphans_includes_pending():
    """Pending operations are also recovered as orphans on startup."""
    svc = _make_service()
    op = svc.create("sandbox", "sb1")
    assert op.status == OpStatus.pending

    count = svc.recover_orphans()
    assert count == 1
    assert svc.get(op.id).status == OpStatus.failed


def test_cleanup_expires_stuck_running():
    svc = _make_service(running_ttl=0.0)
    op = svc.create("gateway", "stuck-gw")
    svc.start(op.id)

    svc.cleanup()

    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.failed
    assert fetched.error_code == ErrorCode.timeout


def test_cleanup_expires_stuck_pending():
    svc = _make_service(running_ttl=0.0)
    op = svc.create("gateway", "stuck-gw")

    svc.cleanup()

    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.failed
    assert fetched.error_code == ErrorCode.timeout


def test_cleanup_keeps_recent_running():
    svc = _make_service(running_ttl=9999.0)
    op = svc.create("gateway", "recent-gw")
    svc.start(op.id)

    svc.cleanup()

    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.running


def test_cleanup_removes_old_completed():
    svc = _make_service(retention_days=0)
    op = svc.create("gateway", "old-gw")
    svc.start(op.id)
    svc.complete(op.id, {})

    with svc._session_factory() as session:
        rec = session.get(OperationRecord, op.id)
        rec.completed_at = datetime.now(UTC) - timedelta(days=1)
        session.commit()

    removed = svc.cleanup()
    assert removed == 1
    assert svc.get(op.id) is None


# ── Serialization ─────────────────────────────────────────────────────────


def test_to_dict_succeeded():
    svc = _make_service()
    op = svc.create("sandbox", "sb1")
    svc.start(op.id)
    svc.complete(op.id, {"name": "sb1", "id": "abc"})

    d = svc.to_dict(svc.get(op.id))
    assert d["id"] == op.id
    assert d["status"] == OpStatus.succeeded
    assert d["resource_type"] == "sandbox"
    assert d["progress"] == 100
    assert d["result"] == {"name": "sb1", "id": "abc"}
    assert "created_at" in d
    assert "updated_at" in d
    assert "completed_at" in d
    assert "resource_key" not in d
    assert "result_json" not in d


def test_to_dict_pending():
    svc = _make_service()
    op = svc.create("gateway", "gw1")

    d = svc.to_dict(op)
    assert d["status"] == OpStatus.pending
    assert d["progress"] == 0
    assert "result" not in d
    assert "error" not in d


def test_to_dict_running():
    svc = _make_service()
    op = svc.create("gateway", "gw1")
    svc.start(op.id)

    d = svc.to_dict(svc.get(op.id))
    assert d["status"] == OpStatus.running
    assert d["progress"] == 0


def test_to_dict_failed():
    svc = _make_service()
    op = svc.create("gateway", "gw1")
    svc.start(op.id)
    svc.fail(op.id, "boom", error_code=ErrorCode.grpc_unavailable)

    d = svc.to_dict(svc.get(op.id))
    assert d["status"] == OpStatus.failed
    assert d["error"] == "boom"
    assert d["error_code"] == ErrorCode.grpc_unavailable


# ── Result truncation ────────────────────────────────────────────────────


def test_result_truncation():
    svc = _make_service()
    op = svc.create("exec", "big-exec")
    svc.start(op.id)
    big_stdout = "x" * 100_000
    svc.complete(op.id, {"stdout": big_stdout, "exit_code": 0})

    fetched = svc.get(op.id)
    result = json.loads(fetched.result_json)
    assert result["truncated"] is True
    assert len(result["stdout"]) == 8000


def test_result_truncation_no_stdout_produces_valid_json():
    """When no truncatable field exists, result is still valid JSON."""
    svc = _make_service()
    op = svc.create("exec", "big-exec")
    svc.start(op.id)
    big_result = {"data": "x" * 100_000}
    svc.complete(op.id, big_result)

    fetched = svc.get(op.id)
    result = json.loads(fetched.result_json)
    assert result["truncated"] is True
    assert "error" in result
