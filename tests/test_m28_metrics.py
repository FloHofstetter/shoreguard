"""Unit tests for the M28 Prometheus metrics surfaces."""

from __future__ import annotations

import grpc

from shoreguard.api.metrics import (
    record_boot_hook_run,
    record_gateway_cert_expiry,
    record_grpc_attempt,
    record_grpc_duration,
    record_sandbox_phase_transition,
    sg_boot_hook_runs_total,
    sg_gateway_cert_expiry_seconds,
    sg_grpc_call_total,
    sg_grpc_retry_total,
    sg_sandbox_phase_transitions_total,
)


def _counter_value(counter, **labels) -> float:
    return counter.labels(**labels)._value.get()  # type: ignore[attr-defined]


def _gauge_value(gauge, **labels) -> float:
    return gauge.labels(**labels)._value.get()  # type: ignore[attr-defined]


def test_record_grpc_attempt_retry_increments_retry_counter():
    before = _counter_value(sg_grpc_retry_total, op="test.op", code="UNAVAILABLE")
    record_grpc_attempt(
        op_name="test.op",
        attempt=1,
        code=grpc.StatusCode.UNAVAILABLE,
        outcome="retry",
    )
    after = _counter_value(sg_grpc_retry_total, op="test.op", code="UNAVAILABLE")
    assert after == before + 1


def test_record_grpc_attempt_ok_increments_call_counter():
    before = _counter_value(sg_grpc_call_total, op="test.ok", code="OK")
    record_grpc_attempt(op_name="test.ok", attempt=1, code=None, outcome="ok")
    after = _counter_value(sg_grpc_call_total, op="test.ok", code="OK")
    assert after == before + 1


def test_record_grpc_attempt_giveup_records_final_code():
    before = _counter_value(sg_grpc_call_total, op="test.giveup", code="DEADLINE_EXCEEDED")
    record_grpc_attempt(
        op_name="test.giveup",
        attempt=3,
        code=grpc.StatusCode.DEADLINE_EXCEEDED,
        outcome="giveup",
    )
    after = _counter_value(sg_grpc_call_total, op="test.giveup", code="DEADLINE_EXCEEDED")
    assert after == before + 1


def test_record_grpc_duration_observes_histogram():
    # The helper should not raise; histogram values are visible as samples.
    record_grpc_duration("test.dur", 0.123)


def test_record_boot_hook_run_increments_counter():
    before = _counter_value(
        sg_boot_hook_runs_total, gateway="gw1", phase="pre_create", status="success"
    )
    record_boot_hook_run(gateway="gw1", phase="pre_create", status="success", duration_s=0.5)
    after = _counter_value(
        sg_boot_hook_runs_total, gateway="gw1", phase="pre_create", status="success"
    )
    assert after == before + 1


def test_record_sandbox_phase_transition_increments_counter():
    labels = {"gateway": "gw1", "from": "none", "to": "ready"}
    before = _counter_value(sg_sandbox_phase_transitions_total, **labels)
    record_sandbox_phase_transition(gateway="gw1", from_phase="none", to_phase="ready")
    after = _counter_value(sg_sandbox_phase_transitions_total, **labels)
    assert after == before + 1


def test_record_gateway_cert_expiry_sets_gauge():
    record_gateway_cert_expiry("gw-expiry", 3600.0)
    assert _gauge_value(sg_gateway_cert_expiry_seconds, gateway="gw-expiry") == 3600.0
    record_gateway_cert_expiry("gw-expiry", None)
    assert _gauge_value(sg_gateway_cert_expiry_seconds, gateway="gw-expiry") == 0.0


# ── SandboxManager integration: retry metric flows through ──────────────────


def test_sandbox_manager_invoke_emits_metrics(monkeypatch):
    from types import SimpleNamespace

    from shoreguard.client._proto import datamodel_pb2, openshell_pb2
    from shoreguard.client._resilience import RetryPolicy
    from shoreguard.client.sandboxes import SandboxManager

    class _FakeRpcError(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.UNAVAILABLE

    monkeypatch.setattr("shoreguard.client._resilience.time.sleep", lambda _s: None)

    calls = {"n": 0}

    class _Stub:
        def ListSandboxes(self, req, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _FakeRpcError()
            return SimpleNamespace(
                sandboxes=[
                    openshell_pb2.Sandbox(
                        metadata=datamodel_pb2.ObjectMeta(id="x", name="sb"),
                        phase=openshell_pb2.SANDBOX_PHASE_READY,
                    )
                ]  # type: ignore[arg-type]
            )

    m = object.__new__(SandboxManager)
    m._stub = _Stub()  # type: ignore[assignment]
    m._timeout = 30.0
    m._retry_policy = RetryPolicy(max_attempts=3, initial_backoff=0.0, max_backoff=0.0, jitter=0.0)
    m._retry_deadline = None

    before_retry = _counter_value(sg_grpc_retry_total, op="sandboxes.list", code="UNAVAILABLE")
    before_ok = _counter_value(sg_grpc_call_total, op="sandboxes.list", code="OK")

    result = m.list(limit=1, offset=0)

    assert result[0]["name"] == "sb"
    assert (
        _counter_value(sg_grpc_retry_total, op="sandboxes.list", code="UNAVAILABLE")
        == before_retry + 1
    )
    assert _counter_value(sg_grpc_call_total, op="sandboxes.list", code="OK") == before_ok + 1
