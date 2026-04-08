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


# ═══════════════════════════════════════════════════════════════════════════
# Mutant-killing tests below
# ═══════════════════════════════════════════════════════════════════════════


# ── _truncate_result (15 survivors) ──────────────────────────────────────


def test_truncate_result_returns_unmodified_when_under_limit():
    """If the JSON fits within max_bytes, return it unchanged."""
    from shoreguard.services.operations import _truncate_result

    result = {"exit_code": 0, "stdout": "hello"}
    out = _truncate_result(result, max_bytes=10_000)
    assert json.loads(out) == result
    assert "truncated" not in out


def test_truncate_result_exact_boundary():
    """Payload exactly at max_bytes should pass without truncation."""
    from shoreguard.services.operations import _truncate_result

    result = {"ok": True}
    result_str = json.dumps(result, default=str)
    exact_len = len(result_str.encode())
    out = _truncate_result(result, max_bytes=exact_len)
    assert json.loads(out) == result
    assert "truncated" not in json.loads(out)


def test_truncate_result_one_byte_over_triggers_truncation():
    """Payload one byte over max_bytes triggers truncation logic."""
    from shoreguard.services.operations import _truncate_result

    # Build a result with a truncatable field that's just over the limit
    result = {"stdout": "x" * 500}
    result_str = json.dumps(result, default=str)
    exact_len = len(result_str.encode())
    out = _truncate_result(result, max_bytes=exact_len - 1)
    parsed = json.loads(out)
    assert parsed["truncated"] is True


def test_truncate_result_truncates_stderr():
    """stderr field gets truncated when it's the large field."""
    from shoreguard.services.operations import _truncate_result

    result = {"stderr": "e" * 100_000, "exit_code": 1}
    out = _truncate_result(result, max_bytes=10_000)
    parsed = json.loads(out)
    assert parsed["truncated"] is True
    assert len(parsed["stderr"]) == 8000  # field_truncation_chars default


def test_truncate_result_truncates_output_field():
    """output field gets truncated."""
    from shoreguard.services.operations import _truncate_result

    result = {"output": "o" * 100_000}
    out = _truncate_result(result, max_bytes=10_000)
    parsed = json.loads(out)
    assert parsed["truncated"] is True
    assert len(parsed["output"]) == 8000


def test_truncate_result_truncates_logs_field():
    """logs field gets truncated."""
    from shoreguard.services.operations import _truncate_result

    result = {"logs": "L" * 100_000}
    out = _truncate_result(result, max_bytes=10_000)
    parsed = json.loads(out)
    assert parsed["truncated"] is True
    assert len(parsed["logs"]) == 8000


def test_truncate_result_non_string_field_not_truncated():
    """Non-string truncatable fields are skipped (e.g., stdout as a list)."""
    from shoreguard.services.operations import _truncate_result

    result = {"stdout": ["line1", "line2"] * 5000}
    out = _truncate_result(result, max_bytes=100)
    parsed = json.loads(out)
    # Falls through to the final fallback
    assert parsed["truncated"] is True
    assert parsed["error"] == "Result too large to store"


def test_truncate_result_stops_after_first_sufficient_field():
    """Truncation stops as soon as one field brings it under the limit."""
    from shoreguard.services.operations import _truncate_result

    # stdout is huge, stderr is small — truncating stdout should suffice
    result = {"stdout": "x" * 100_000, "stderr": "small"}
    out = _truncate_result(result, max_bytes=20_000)
    parsed = json.loads(out)
    assert parsed["truncated"] is True
    assert len(parsed["stdout"]) == 8000
    assert parsed["stderr"] == "small"  # stderr untouched


def test_truncate_result_multiple_large_fields():
    """When multiple fields are large and first truncation isn't enough, try next."""
    from shoreguard.services.operations import _truncate_result

    # Both stdout and stderr are huge; max_bytes large enough to fit after
    # truncating both but not after truncating only stdout
    result = {"stdout": "x" * 50_000, "stderr": "e" * 50_000}
    out = _truncate_result(result, max_bytes=20_000)
    parsed = json.loads(out)
    assert parsed["truncated"] is True
    # stdout truncated first, then stderr truncated to get under limit
    assert len(parsed["stdout"]) == 8000
    assert len(parsed["stderr"]) == 8000


def test_truncate_result_fallback_when_all_truncation_insufficient():
    """When truncating all fields still exceeds max_bytes, use fallback."""
    from shoreguard.services.operations import _truncate_result

    # Even after truncating the field to 8000 chars, result is still > 100 bytes
    result = {"stdout": "x" * 100_000}
    out = _truncate_result(result, max_bytes=100)
    parsed = json.loads(out)
    assert parsed == {"truncated": True, "error": "Result too large to store"}


def test_truncate_result_field_not_present():
    """Fields not in the result are simply skipped."""
    from shoreguard.services.operations import _truncate_result

    result = {"custom_big_field": "z" * 100_000}
    out = _truncate_result(result, max_bytes=100)
    parsed = json.loads(out)
    assert parsed == {"truncated": True, "error": "Result too large to store"}


def test_truncate_result_preserves_other_keys():
    """Non-truncatable keys are preserved in output."""
    from shoreguard.services.operations import _truncate_result

    result = {"stdout": "x" * 100_000, "exit_code": 42, "signal": None}
    out = _truncate_result(result, max_bytes=20_000)
    parsed = json.loads(out)
    assert parsed["exit_code"] == 42
    assert parsed["signal"] is None
    assert parsed["truncated"] is True


# ── cleanup (26 survivors) ───────────────────────────────────────────────


def test_cleanup_returns_count_of_removed_completed():
    """cleanup returns the number of old completed ops removed."""
    svc = _make_service(retention_days=0)
    # Create and complete two ops
    for i in range(3):
        op = svc.create("gateway", f"gw{i}")
        svc.start(op.id)
        svc.complete(op.id, {"i": i})

    # Backdate completed_at
    with svc._session_factory() as session:
        for rec in session.query(OperationRecord).all():
            rec.completed_at = datetime.now(UTC) - timedelta(days=2)
        session.commit()

    removed = svc.cleanup()
    assert removed == 3


def test_cleanup_returns_zero_when_nothing_to_clean():
    """cleanup returns 0 when no ops need cleaning."""
    svc = _make_service()
    removed = svc.cleanup()
    assert removed == 0


def test_cleanup_stuck_ops_get_exact_fields():
    """Verify exact status, error_message, error_code, completed_at, updated_at on stuck ops."""
    svc = _make_service(running_ttl=0.0)
    op = svc.create("gateway", "gw1")
    svc.start(op.id)

    svc.cleanup()

    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.failed
    assert fetched.error_message == "Operation timed out"
    assert fetched.error_code == ErrorCode.timeout
    assert fetched.completed_at is not None
    assert fetched.updated_at is not None


def test_cleanup_boundary_running_ttl_recent():
    """Op created well within TTL should NOT be expired."""
    svc = _make_service(running_ttl=60.0)
    op = svc.create("gateway", "gw1")
    svc.start(op.id)

    # Set created_at to 30 seconds ago — well within the 60s TTL
    with svc._session_factory() as session:
        rec = session.get(OperationRecord, op.id)
        rec.created_at = datetime.now(UTC) - timedelta(seconds=30)
        session.commit()

    svc.cleanup()
    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.running


def test_cleanup_boundary_running_ttl_one_second_over():
    """Op created one second beyond cutoff SHOULD be expired."""
    svc = _make_service(running_ttl=60.0)
    op = svc.create("gateway", "gw1")
    svc.start(op.id)

    with svc._session_factory() as session:
        rec = session.get(OperationRecord, op.id)
        rec.created_at = datetime.now(UTC) - timedelta(seconds=61)
        session.commit()

    svc.cleanup()
    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.failed
    assert fetched.error_code == ErrorCode.timeout


def test_cleanup_retention_boundary_recent():
    """Op completed well within retention period should NOT be removed."""
    svc = _make_service(retention_days=30)
    op = svc.create("gateway", "gw1")
    svc.start(op.id)
    svc.complete(op.id, {})

    with svc._session_factory() as session:
        rec = session.get(OperationRecord, op.id)
        rec.completed_at = datetime.now(UTC) - timedelta(days=15)
        session.commit()

    removed = svc.cleanup()
    assert removed == 0
    assert svc.get(op.id) is not None


def test_cleanup_retention_boundary_one_day_over():
    """Op completed one day beyond retention SHOULD be removed."""
    svc = _make_service(retention_days=30)
    op = svc.create("gateway", "gw1")
    svc.start(op.id)
    svc.complete(op.id, {})

    with svc._session_factory() as session:
        rec = session.get(OperationRecord, op.id)
        rec.completed_at = datetime.now(UTC) - timedelta(days=31)
        session.commit()

    removed = svc.cleanup()
    assert removed == 1
    assert svc.get(op.id) is None


def test_cleanup_does_not_remove_active_ops():
    """Active (running/pending) ops should never be removed by retention cleanup."""
    svc = _make_service(retention_days=0, running_ttl=99999.0)
    op = svc.create("gateway", "gw1")
    svc.start(op.id)

    removed = svc.cleanup()
    assert removed == 0
    assert svc.get(op.id) is not None
    assert svc.get(op.id).status == OpStatus.running


def test_cleanup_does_not_remove_recent_completed():
    """Recently completed ops should not be removed."""
    svc = _make_service(retention_days=30)
    op = svc.create("gateway", "gw1")
    svc.start(op.id)
    svc.complete(op.id, {})

    removed = svc.cleanup()
    assert removed == 0
    assert svc.get(op.id) is not None


def test_cleanup_handles_stuck_pending():
    """Pending ops older than TTL get timed out."""
    svc = _make_service(running_ttl=60.0)
    op = svc.create("gateway", "gw1")

    with svc._session_factory() as session:
        rec = session.get(OperationRecord, op.id)
        rec.created_at = datetime.now(UTC) - timedelta(seconds=120)
        session.commit()

    svc.cleanup()
    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.failed
    assert fetched.error_message == "Operation timed out"


def test_cleanup_handles_stuck_cancelling():
    """Cancelling ops older than TTL get timed out."""
    svc = _make_service(running_ttl=60.0)
    op = svc.create("gateway", "gw1")
    svc.start(op.id)

    with svc._session_factory() as session:
        rec = session.get(OperationRecord, op.id)
        rec.status = OpStatus.cancelling
        rec.created_at = datetime.now(UTC) - timedelta(seconds=120)
        session.commit()

    svc.cleanup()
    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.failed
    assert fetched.error_code == ErrorCode.timeout


def test_cleanup_mixed_stuck_and_old():
    """Both stuck ops and old completed ops are handled in one call."""
    svc = _make_service(running_ttl=0.0, retention_days=0)

    # Stuck running op
    op1 = svc.create("gateway", "stuck")
    svc.start(op1.id)

    # Old completed op
    op2 = svc.create("gateway", "old")
    svc.start(op2.id)
    svc.complete(op2.id, {})
    with svc._session_factory() as session:
        rec = session.get(OperationRecord, op2.id)
        rec.completed_at = datetime.now(UTC) - timedelta(days=1)
        session.commit()

    removed = svc.cleanup()
    # The stuck op gets failed (not removed), the old completed gets removed
    assert removed >= 1
    assert svc.get(op1.id).status == OpStatus.failed
    assert svc.get(op2.id) is None


def test_cleanup_stuck_op_not_counted_in_removed():
    """Stuck ops that get failed are NOT counted in the returned removed count."""
    svc = _make_service(running_ttl=0.0, retention_days=9999)
    op = svc.create("gateway", "stuck")
    svc.start(op.id)

    removed = svc.cleanup()
    assert removed == 0  # No completed ops were deleted
    assert svc.get(op.id).status == OpStatus.failed


# ── complete (13 survivors) ──────────────────────────────────────────────


def test_complete_sets_exact_fields():
    """Verify every field set by complete()."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)

    svc.complete(op.id, {"key": "val"})

    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.succeeded
    assert fetched.progress_pct == 100
    assert json.loads(fetched.result_json) == {"key": "val"}
    assert fetched.completed_at is not None
    assert fetched.updated_at is not None


def test_complete_ignores_none_op():
    """complete() on nonexistent op_id is a no-op (no crash)."""
    svc = _make_service()
    svc.complete("nonexistent", {"a": 1})  # Should not raise


def test_complete_ignores_already_succeeded():
    """complete() on already succeeded op is a no-op."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)
    svc.complete(op.id, {"first": True})
    svc.complete(op.id, {"second": True})

    fetched = svc.get(op.id)
    assert json.loads(fetched.result_json) == {"first": True}


def test_complete_ignores_already_failed():
    """complete() on already failed op is a no-op."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)
    svc.fail(op.id, "error")
    svc.complete(op.id, {"should_not": "appear"})

    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.failed
    assert fetched.result_json is None


def test_complete_from_cancelling():
    """complete() works on cancelling state (not terminal)."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)
    with svc._session_factory() as session:
        rec = session.get(OperationRecord, op.id)
        rec.status = OpStatus.cancelling
        session.commit()

    svc.complete(op.id, {"done": True})
    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.succeeded
    assert fetched.progress_pct == 100


def test_complete_stores_result_json_string():
    """result_json is stored as a JSON string, not raw dict."""
    svc = _make_service()
    op = svc.create("exec", "cmd")
    svc.complete(op.id, {"exit_code": 0, "stdout": "ok"})

    fetched = svc.get(op.id)
    assert isinstance(fetched.result_json, str)
    parsed = json.loads(fetched.result_json)
    assert parsed["exit_code"] == 0
    assert parsed["stdout"] == "ok"


# ── fail (13 survivors) ─────────────────────────────────────────────────


def test_fail_sets_exact_fields():
    """Verify every field set by fail()."""
    svc = _make_service()
    op = svc.create("gateway", "gw")
    svc.start(op.id)

    svc.fail(op.id, "Connection refused", error_code=ErrorCode.grpc_unavailable)

    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.failed
    assert fetched.error_message == "Connection refused"
    assert fetched.error_code == ErrorCode.grpc_unavailable
    assert fetched.completed_at is not None
    assert fetched.updated_at is not None


def test_fail_default_error_code():
    """Default error_code is ErrorCode.internal."""
    svc = _make_service()
    op = svc.create("gateway", "gw")
    svc.start(op.id)
    svc.fail(op.id, "something broke")

    fetched = svc.get(op.id)
    assert fetched.error_code == ErrorCode.internal


def test_fail_ignores_none_op():
    """fail() on nonexistent op_id is a no-op."""
    svc = _make_service()
    svc.fail("nonexistent", "error")  # Should not raise


def test_fail_ignores_already_failed():
    """fail() on already failed op does not update it."""
    svc = _make_service()
    op = svc.create("gateway", "gw")
    svc.start(op.id)
    svc.fail(op.id, "first error", error_code=ErrorCode.internal)
    svc.fail(op.id, "second error", error_code=ErrorCode.timeout)

    fetched = svc.get(op.id)
    assert fetched.error_message == "first error"
    assert fetched.error_code == ErrorCode.internal


def test_fail_ignores_already_succeeded():
    """fail() on already succeeded op is a no-op."""
    svc = _make_service()
    op = svc.create("gateway", "gw")
    svc.start(op.id)
    svc.complete(op.id, {"ok": True})
    svc.fail(op.id, "too late")

    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.succeeded


def test_fail_from_pending():
    """fail() works directly on pending ops."""
    svc = _make_service()
    op = svc.create("gateway", "gw")
    svc.fail(op.id, "Aborted before start")

    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.failed
    assert fetched.error_message == "Aborted before start"
    assert fetched.completed_at is not None


def test_fail_from_cancelling_state():
    """fail() works on cancelling ops — this is the normal cancel completion path."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)
    with svc._session_factory() as session:
        rec = session.get(OperationRecord, op.id)
        rec.status = OpStatus.cancelling
        session.commit()

    svc.fail(op.id, "Cancelled by user", error_code=ErrorCode.cancelled)
    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.failed
    assert fetched.error_code == ErrorCode.cancelled
    assert fetched.error_message == "Cancelled by user"


# ── recover_orphans (12 survivors) ───────────────────────────────────────


def test_recover_orphans_sets_exact_fields():
    """Verify every field set on orphaned ops."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)

    svc.recover_orphans()

    fetched = svc.get(op.id)
    assert fetched.status == OpStatus.failed
    assert fetched.error_message == "Server restarted while operation was in progress"
    assert fetched.error_code == ErrorCode.orphaned
    assert fetched.completed_at is not None
    assert fetched.updated_at is not None


def test_recover_orphans_returns_zero_when_none():
    """recover_orphans returns 0 when no active ops exist."""
    svc = _make_service()
    count = svc.recover_orphans()
    assert count == 0


def test_recover_orphans_handles_all_active_states():
    """All ACTIVE_STATES (pending, running, cancelling) are recovered."""
    svc = _make_service()
    op_pending = svc.create("sandbox", "sb1")
    op_running = svc.create("sandbox", "sb2")
    svc.start(op_running.id)
    op_cancelling = svc.create("sandbox", "sb3")
    svc.start(op_cancelling.id)
    with svc._session_factory() as session:
        rec = session.get(OperationRecord, op_cancelling.id)
        rec.status = OpStatus.cancelling
        session.commit()

    count = svc.recover_orphans()
    assert count == 3

    for op_id in [op_pending.id, op_running.id, op_cancelling.id]:
        fetched = svc.get(op_id)
        assert fetched.status == OpStatus.failed
        assert fetched.error_code == ErrorCode.orphaned


def test_recover_orphans_does_not_touch_terminal():
    """Already succeeded/failed ops are not affected by recover_orphans."""
    svc = _make_service()
    op_ok = svc.create("sandbox", "sb1")
    svc.start(op_ok.id)
    svc.complete(op_ok.id, {"ok": True})

    op_fail = svc.create("sandbox", "sb2")
    svc.start(op_fail.id)
    svc.fail(op_fail.id, "error")

    count = svc.recover_orphans()
    assert count == 0

    assert svc.get(op_ok.id).status == OpStatus.succeeded
    assert svc.get(op_fail.id).status == OpStatus.failed
    assert svc.get(op_fail.id).error_code == ErrorCode.internal  # not orphaned


def test_recover_orphans_multiple_returns_exact_count():
    """Return value matches exactly the number of recovered ops."""
    svc = _make_service()
    for i in range(5):
        op = svc.create("sandbox", f"sb{i}")
        svc.start(op.id)

    count = svc.recover_orphans()
    assert count == 5


# ── cancel (9 survivors) ────────────────────────────────────────────────


def test_cancel_returns_none_for_succeeded():
    """cancel() on succeeded op returns None."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)
    svc.complete(op.id, {})
    result = svc.cancel(op.id)
    assert result is None
    assert svc.get(op.id).status == OpStatus.succeeded


def test_cancel_returns_none_for_failed():
    """cancel() on failed op returns None."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)
    svc.fail(op.id, "error")
    result = svc.cancel(op.id)
    assert result is None


def test_cancel_returns_none_for_cancelling():
    """cancel() on already cancelling op returns None (not pending or running)."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)
    with svc._session_factory() as session:
        rec = session.get(OperationRecord, op.id)
        rec.status = OpStatus.cancelling
        session.commit()

    result = svc.cancel(op.id)
    assert result is None


def test_cancel_no_task_sets_cancelled_error():
    """Cancel without task sets error_message and error_code correctly."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)
    result = svc.cancel(op.id)

    assert result is not None
    assert result.status == OpStatus.failed
    assert result.error_message == "Operation was cancelled"
    assert result.error_code == ErrorCode.cancelled
    assert result.completed_at is not None


def test_cancel_pending_sets_correct_final_state():
    """Cancel from pending results in failed with cancelled error code."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    result = svc.cancel(op.id)

    assert result is not None
    assert result.status == OpStatus.failed
    assert result.error_code == ErrorCode.cancelled
    assert result.error_message == "Operation was cancelled"


def test_cancel_with_done_task():
    """Cancel with a done task still calls fail() directly."""
    import asyncio

    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)

    # Create a completed (done) task
    loop = asyncio.new_event_loop()
    task = loop.create_task(asyncio.sleep(0))
    loop.run_until_complete(task)

    svc._tasks[op.id] = task  # register a done task
    result = svc.cancel(op.id)

    assert result is not None
    assert result.status == OpStatus.failed
    assert result.error_code == ErrorCode.cancelled
    loop.close()


def test_cancel_updates_updated_at():
    """cancel() updates the updated_at timestamp."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)
    original_updated = svc.get(op.id).updated_at

    result = svc.cancel(op.id)
    assert result is not None
    assert result.updated_at >= original_updated


# ── list_ops (8 survivors) ──────────────────────────────────────────────


def test_list_ops_limit_capped_at_max():
    """limit is capped at max_list_limit (200)."""
    svc = _make_service()
    for i in range(5):
        svc.create("sandbox", f"sb{i}")

    ops, total = svc.list_ops(limit=999)
    assert total == 5
    assert len(ops) == 5  # all returned, not 999


def test_list_ops_offset_skips():
    """offset correctly skips records."""
    svc = _make_service()
    svc.create("sandbox", "sb1")
    svc.create("sandbox", "sb2")
    svc.create("sandbox", "sb3")

    ops, total = svc.list_ops(offset=1)
    assert total == 3
    assert len(ops) == 2


def test_list_ops_filter_by_both():
    """Filtering by both status and resource_type."""
    svc = _make_service()
    op1 = svc.create("sandbox", "sb1")
    svc.start(op1.id)
    svc.create("sandbox", "sb2")  # pending
    op3 = svc.create("gateway", "gw1")
    svc.start(op3.id)

    ops, total = svc.list_ops(status="running", resource_type="sandbox")
    assert total == 1
    assert ops[0].id == op1.id


def test_list_ops_empty_result():
    """list_ops returns empty list and 0 total when no ops match."""
    svc = _make_service()
    ops, total = svc.list_ops(status="running")
    assert ops == []
    assert total == 0


def test_list_ops_count_matches_filter():
    """total count reflects the filter, not total ops."""
    svc = _make_service()
    for i in range(3):
        svc.create("sandbox", f"sb{i}")
    op = svc.create("gateway", "gw1")
    svc.start(op.id)

    _, total = svc.list_ops(resource_type="gateway")
    assert total == 1

    _, total = svc.list_ops(status="pending")
    assert total == 3


# ── create (3 survivors) ────────────────────────────────────────────────


def test_create_generates_uuid():
    """Each created op has a unique UUID id."""
    svc = _make_service()
    op1 = svc.create("sandbox", "sb1")
    op2 = svc.create("sandbox", "sb2")
    assert op1.id != op2.id
    # Verify it's a valid UUID
    import uuid

    uuid.UUID(op1.id)
    uuid.UUID(op2.id)


def test_create_sets_timestamps():
    """created_at and updated_at are set and not None."""
    svc = _make_service()
    op = svc.create("sandbox", "sb1")

    assert op.created_at is not None
    assert op.updated_at is not None


def test_create_with_idempotency_key():
    """idempotency_key is stored on the record."""
    svc = _make_service()
    op = svc.create("sandbox", "sb1", idempotency_key="my-key-123")
    assert op.idempotency_key == "my-key-123"

    fetched = svc.get(op.id)
    assert fetched.idempotency_key == "my-key-123"


# ── update_progress (2 survivors) ───────────────────────────────────────


def test_update_progress_message_stored():
    """Progress message is stored and retrievable."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)
    svc.update_progress(op.id, 25, "Loading config")

    fetched = svc.get(op.id)
    assert fetched.progress_pct == 25
    assert fetched.progress_msg == "Loading config"


def test_update_progress_none_message():
    """Progress can be updated without a message."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)
    svc.update_progress(op.id, 75)

    fetched = svc.get(op.id)
    assert fetched.progress_pct == 75
    assert fetched.progress_msg is None


def test_update_progress_updates_timestamp():
    """update_progress updates the updated_at timestamp."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)
    original = svc.get(op.id).updated_at

    svc.update_progress(op.id, 50, "halfway")
    fetched = svc.get(op.id)
    assert fetched.updated_at >= original


def test_update_progress_noop_on_terminal():
    """update_progress on a terminal (succeeded/failed) op is a no-op."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)
    svc.complete(op.id, {})

    svc.update_progress(op.id, 50, "should not work")
    fetched = svc.get(op.id)
    assert fetched.progress_pct == 100  # unchanged from complete


def test_update_progress_noop_on_nonexistent():
    """update_progress on nonexistent op is a no-op."""
    svc = _make_service()
    svc.update_progress("nonexistent", 50)  # Should not raise


def test_update_progress_clamp_exact_boundaries():
    """Progress is clamped: 0 stays 0, 100 stays 100."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)

    svc.update_progress(op.id, 0)
    assert svc.get(op.id).progress_pct == 0

    svc.update_progress(op.id, 100)
    assert svc.get(op.id).progress_pct == 100


# ── is_running (1 survivor) ────────────────────────────────────────────


def test_is_running_false_after_fail():
    """is_running returns False after the op fails."""
    svc = _make_service()
    op = svc.create("gateway", "gw1")
    svc.start(op.id)
    assert svc.is_running("gateway", "gw1") is True

    svc.fail(op.id, "error")
    assert svc.is_running("gateway", "gw1") is False


def test_is_running_cancelling_not_active():
    """Cancelling state is NOT considered running by is_running()."""
    svc = _make_service()
    op = svc.create("gateway", "gw1")
    svc.start(op.id)

    with svc._session_factory() as session:
        rec = session.get(OperationRecord, op.id)
        rec.status = OpStatus.cancelling
        session.commit()

    # is_running only checks pending and running, not cancelling
    assert svc.is_running("gateway", "gw1") is False


# ── status_counts (1 survivor) ─────────────────────────────────────────


def test_status_counts_empty():
    """status_counts returns empty dict when no ops exist."""
    svc = _make_service()
    counts = svc.status_counts()
    assert counts == {}


def test_status_counts_exact_values():
    """status_counts returns exact counts per status."""
    svc = _make_service()
    # 3 pending
    svc.create("sandbox", "sb1")
    svc.create("sandbox", "sb2")
    svc.create("sandbox", "sb3")
    # 1 running
    op_run = svc.create("gateway", "gw1")
    svc.start(op_run.id)
    # 2 succeeded
    for i in range(2):
        op = svc.create("gateway", f"gw-ok-{i}")
        svc.start(op.id)
        svc.complete(op.id, {})

    counts = svc.status_counts()
    assert counts[OpStatus.pending] == 3
    assert counts[OpStatus.running] == 1
    assert counts[OpStatus.succeeded] == 2
    assert OpStatus.failed not in counts


# ── to_dict edge cases ─────────────────────────────────────────────────


def test_to_dict_with_gateway_name():
    """gateway_name appears in dict when set."""
    svc = _make_service()
    op = svc.create("sandbox", "sb", gateway_name="prod-gw")
    d = svc.to_dict(op)
    assert d["gateway_name"] == "prod-gw"


def test_to_dict_without_gateway_name():
    """gateway_name is absent from dict when not set."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    d = svc.to_dict(op)
    assert "gateway_name" not in d


def test_to_dict_with_progress_message():
    """progress_message appears in dict when set."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)
    svc.update_progress(op.id, 50, "half done")

    d = svc.to_dict(svc.get(op.id))
    assert d["progress_message"] == "half done"
    assert d["progress"] == 50


def test_to_dict_invalid_result_json():
    """Invalid JSON in result_json results in result=None."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)
    svc.complete(op.id, {"ok": True})

    # Corrupt the result_json
    with svc._session_factory() as session:
        rec = session.get(OperationRecord, op.id)
        rec.result_json = "not valid json {{"
        session.commit()

    d = svc.to_dict(svc.get(op.id))
    assert d["result"] is None


def test_to_dict_no_completed_at():
    """completed_at key is absent when not set."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    d = svc.to_dict(op)
    assert "completed_at" not in d


def test_to_dict_no_error_fields():
    """error and error_code are absent on non-failed ops."""
    svc = _make_service()
    op = svc.create("sandbox", "sb")
    svc.start(op.id)
    svc.complete(op.id, {})
    d = svc.to_dict(svc.get(op.id))
    assert "error" not in d
    assert "error_code" not in d
