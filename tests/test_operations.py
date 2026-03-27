"""Tests for the in-memory OperationStore."""

from __future__ import annotations

from shoreguard.services.operations import OperationStore


def test_create_and_get():
    store = OperationStore()
    op = store.create("gateway", "my-gw")
    assert op.status == "running"
    assert op.resource_type == "gateway"
    assert op.resource_key == "my-gw"

    fetched = store.get(op.id)
    assert fetched is not None
    assert fetched.id == op.id


def test_complete_operation():
    store = OperationStore()
    op = store.create("sandbox", "my-sb")
    store.complete(op.id, {"name": "my-sb", "phase": "ready"})

    fetched = store.get(op.id)
    assert fetched.status == "succeeded"
    assert fetched.result == {"name": "my-sb", "phase": "ready"}
    assert fetched.completed_at is not None


def test_fail_operation():
    store = OperationStore()
    op = store.create("gateway", "fail-gw")
    store.fail(op.id, "Docker not running")

    fetched = store.get(op.id)
    assert fetched.status == "failed"
    assert fetched.error == "Docker not running"
    assert fetched.completed_at is not None


def test_get_unknown_returns_none():
    store = OperationStore()
    assert store.get("nonexistent-id") is None


def test_is_running():
    store = OperationStore()
    assert store.is_running("gateway", "gw1") is False

    op = store.create("gateway", "gw1")
    assert store.is_running("gateway", "gw1") is True
    assert store.is_running("gateway", "gw2") is False
    assert store.is_running("sandbox", "gw1") is False

    store.complete(op.id, {})
    assert store.is_running("gateway", "gw1") is False


def test_cleanup_removes_expired():
    store = OperationStore(ttl=0.0)  # Immediate expiry
    op = store.create("gateway", "old-gw")
    store.complete(op.id, {})

    removed = store.cleanup()
    assert removed == 1
    assert store.get(op.id) is None


def test_cleanup_keeps_running():
    store = OperationStore(ttl=0.0)
    op = store.create("gateway", "running-gw")

    removed = store.cleanup()
    assert removed == 0
    assert store.get(op.id) is not None


def test_to_dict():
    store = OperationStore()
    op = store.create("sandbox", "sb1")
    store.complete(op.id, {"name": "sb1", "id": "abc"})

    d = store.to_dict(store.get(op.id))
    assert d["id"] == op.id
    assert d["status"] == "succeeded"
    assert d["resource_type"] == "sandbox"
    assert d["result"] == {"name": "sb1", "id": "abc"}
    # Internal fields should be stripped
    assert "created_at" not in d
    assert "completed_at" not in d
    assert "resource_key" not in d


def test_to_dict_running_no_result():
    store = OperationStore()
    op = store.create("gateway", "gw1")

    d = store.to_dict(op)
    assert d["status"] == "running"
    assert "result" not in d
    assert "error" not in d


def test_create_if_not_running_success():
    store = OperationStore()
    op = store.create_if_not_running("gateway", "gw1")
    assert op is not None
    assert op.status == "running"
    assert store.get(op.id) is not None


def test_create_if_not_running_blocked():
    store = OperationStore()
    op1 = store.create_if_not_running("gateway", "gw1")
    assert op1 is not None

    # Second call for same resource returns None
    op2 = store.create_if_not_running("gateway", "gw1")
    assert op2 is None


def test_create_if_not_running_different_resource():
    store = OperationStore()
    op1 = store.create_if_not_running("gateway", "gw1")
    assert op1 is not None

    # Different resource_key is allowed
    op2 = store.create_if_not_running("gateway", "gw2")
    assert op2 is not None


def test_create_if_not_running_after_complete():
    store = OperationStore()
    op1 = store.create_if_not_running("gateway", "gw1")
    store.complete(op1.id, {})

    # After completion, a new operation can be created
    op2 = store.create_if_not_running("gateway", "gw1")
    assert op2 is not None
    assert op2.id != op1.id


def test_cleanup_expires_stuck_running(monkeypatch):
    store = OperationStore(running_ttl=0.0)  # Immediate running expiry
    op = store.create("gateway", "stuck-gw")

    # Operation is running but running_ttl=0 means it should be expired
    store.cleanup()

    fetched = store.get(op.id)
    assert fetched.status == "failed"
    assert fetched.error == "Operation timed out"
    assert fetched.completed_at is not None


def test_cleanup_keeps_recent_running():
    store = OperationStore(running_ttl=9999.0)
    op = store.create("gateway", "recent-gw")

    store.cleanup()

    fetched = store.get(op.id)
    assert fetched.status == "running"


def test_reset():
    store = OperationStore()
    store.create("gateway", "gw1")
    store.create("sandbox", "sb1")
    store._reset()
    assert store.get("anything") is None
