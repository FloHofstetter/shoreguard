"""Unit tests for the gRPC resilience primitives."""

from __future__ import annotations

import random

import grpc
import pytest

from shoreguard.client._resilience import (
    DEFAULT_POLICY,
    NON_RETRYABLE_CODES,
    RETRYABLE_CODES,
    RetryPolicy,
    call_with_retry,
    classify_grpc_error,
    stream_with_retry,
)
from shoreguard.exceptions import (
    ConflictError,
    GatewayNotConnectedError,
    NotFoundError,
    SandboxError,
    ValidationError,
)


class _FakeRpcError(grpc.RpcError):
    """grpc.RpcError doesn't expose ``code()``; stub one for tests."""

    def __init__(self, code: grpc.StatusCode, details: str = "fake") -> None:
        super().__init__(details)
        self._code = code
        self._details = details

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _make_fn(outcomes: list):
    """Build a zero-arg callable that replays scripted outcomes.

    Each entry is either an exception (raised) or a value (returned).
    """
    it = iter(outcomes)

    def fn():
        nxt = next(it)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt

    return fn


# ── Policy validation ────────────────────────────────────────────────────────


def test_retry_policy_rejects_invalid_jitter():
    with pytest.raises(ValueError):
        RetryPolicy(jitter=1.5)


def test_retry_policy_rejects_bad_backoff_bounds():
    with pytest.raises(ValueError):
        RetryPolicy(initial_backoff=5.0, max_backoff=1.0)


def test_retry_policy_rejects_zero_attempts():
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)


def test_retryable_sets_are_disjoint():
    assert RETRYABLE_CODES.isdisjoint(NON_RETRYABLE_CODES)


# ── call_with_retry happy + retry paths ──────────────────────────────────────


def test_call_returns_immediately_on_success():
    clock = _Clock()
    fn = _make_fn(["payload"])
    result = call_with_retry(
        fn,
        op_name="test.ok",
        _sleep=clock.sleep,
        _monotonic=clock.monotonic,
    )
    assert result == "payload"
    assert clock.slept == []


def test_call_retries_on_unavailable_then_succeeds():
    clock = _Clock()
    fn = _make_fn(
        [
            _FakeRpcError(grpc.StatusCode.UNAVAILABLE),
            _FakeRpcError(grpc.StatusCode.UNAVAILABLE),
            "done",
        ]
    )
    result = call_with_retry(
        fn,
        op_name="test.retry",
        policy=RetryPolicy(max_attempts=4, initial_backoff=0.1, max_backoff=1.0, jitter=0.0),
        _sleep=clock.sleep,
        _monotonic=clock.monotonic,
        _rng=random.Random(0),
    )
    assert result == "done"
    assert len(clock.slept) == 2


def test_call_does_not_retry_non_retryable_code():
    clock = _Clock()
    fn = _make_fn([_FakeRpcError(grpc.StatusCode.INVALID_ARGUMENT), "never"])
    with pytest.raises(grpc.RpcError):
        call_with_retry(
            fn,
            op_name="test.non_retryable",
            _sleep=clock.sleep,
            _monotonic=clock.monotonic,
        )
    assert clock.slept == []


def test_call_gives_up_after_max_attempts():
    clock = _Clock()
    attempts: list[tuple[int, str]] = []

    def on_attempt(*, op_name, attempt, code, outcome):
        attempts.append((attempt, outcome))

    fn = _make_fn([_FakeRpcError(grpc.StatusCode.UNAVAILABLE)] * 5)
    with pytest.raises(grpc.RpcError):
        call_with_retry(
            fn,
            op_name="test.exhaust",
            policy=RetryPolicy(max_attempts=3, initial_backoff=0.05, max_backoff=0.2, jitter=0.0),
            _sleep=clock.sleep,
            _monotonic=clock.monotonic,
            _rng=random.Random(0),
            on_attempt=on_attempt,
        )
    # 3 attempts: retry, retry, giveup
    outcomes = [o for _, o in attempts]
    assert outcomes == ["retry", "retry", "giveup"]


def test_call_respects_absolute_deadline():
    clock = _Clock()
    fn = _make_fn([_FakeRpcError(grpc.StatusCode.UNAVAILABLE)] * 10)
    with pytest.raises(grpc.RpcError):
        call_with_retry(
            fn,
            op_name="test.deadline",
            policy=RetryPolicy(max_attempts=10, initial_backoff=1.0, max_backoff=4.0, jitter=0.0),
            deadline_s=1.5,
            _sleep=clock.sleep,
            _monotonic=clock.monotonic,
            _rng=random.Random(0),
        )
    # Total slept must never exceed the deadline.
    assert sum(clock.slept) <= 1.5 + 1e-9


def test_call_backoff_is_exponential():
    clock = _Clock()
    fn = _make_fn([_FakeRpcError(grpc.StatusCode.UNAVAILABLE)] * 4 + ["ok"])
    call_with_retry(
        fn,
        op_name="test.backoff",
        policy=RetryPolicy(max_attempts=5, initial_backoff=0.1, max_backoff=10.0, jitter=0.0),
        _sleep=clock.sleep,
        _monotonic=clock.monotonic,
        _rng=random.Random(0),
    )
    assert clock.slept == [0.1, 0.2, 0.4, 0.8]


def test_on_attempt_fires_on_success():
    clock = _Clock()
    seen: list[str] = []

    def cb(*, op_name, attempt, code, outcome):
        seen.append(outcome)

    call_with_retry(
        _make_fn(["ok"]),
        op_name="test.cb",
        on_attempt=cb,
        _sleep=clock.sleep,
        _monotonic=clock.monotonic,
    )
    assert seen == ["ok"]


# ── stream_with_retry ────────────────────────────────────────────────────────


def test_stream_with_retry_retries_open_only():
    clock = _Clock()
    calls = {"n": 0}

    def opener():
        calls["n"] += 1
        if calls["n"] < 2:
            raise _FakeRpcError(grpc.StatusCode.UNAVAILABLE)
        return iter(["a", "b", "c"])

    stream = call_with_retry(  # exercise the shared path
        opener,
        op_name="test.stream",
        _sleep=clock.sleep,
        _monotonic=clock.monotonic,
        _rng=random.Random(0),
    )
    assert list(stream) == ["a", "b", "c"]
    assert calls["n"] == 2


def test_stream_with_retry_wrapper_delegates():
    # Smoke test that the public stream_with_retry alias returns the iterator.
    stream = stream_with_retry(lambda: iter([1, 2]), op_name="test.s")
    assert list(stream) == [1, 2]


# ── classify_grpc_error mapping ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (grpc.StatusCode.UNAVAILABLE, GatewayNotConnectedError),
        (grpc.StatusCode.UNAUTHENTICATED, GatewayNotConnectedError),
        (grpc.StatusCode.NOT_FOUND, NotFoundError),
        (grpc.StatusCode.ALREADY_EXISTS, ConflictError),
        (grpc.StatusCode.FAILED_PRECONDITION, ConflictError),
        (grpc.StatusCode.ABORTED, ConflictError),
        (grpc.StatusCode.INVALID_ARGUMENT, ValidationError),
        (grpc.StatusCode.INTERNAL, SandboxError),
    ],
)
def test_classify_grpc_error(code, expected):
    exc = _FakeRpcError(code, details="boom")
    assert isinstance(classify_grpc_error(exc), expected)


def test_classify_non_grpc_falls_back_to_sandbox_error():
    assert isinstance(classify_grpc_error(RuntimeError("nope")), SandboxError)


def test_default_policy_uses_documented_retryable_set():
    assert DEFAULT_POLICY.retryable_codes == RETRYABLE_CODES


# ── SandboxManager integration: transparent retry through _invoke ────────────


def test_sandbox_manager_retries_list_on_unavailable(monkeypatch):
    from types import SimpleNamespace

    from shoreguard.client._proto import datamodel_pb2, openshell_pb2
    from shoreguard.client.sandboxes import SandboxManager

    # Keep sleeps fast.
    monkeypatch.setattr("shoreguard.client._resilience.time.sleep", lambda _s: None, raising=False)

    calls = {"n": 0}

    class _FlakyStub:
        def ListSandboxes(self, req, timeout=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise _FakeRpcError(grpc.StatusCode.UNAVAILABLE)
            return SimpleNamespace(
                sandboxes=[
                    openshell_pb2.Sandbox(
                        metadata=datamodel_pb2.ObjectMeta(id="id1", name="sb1"),
                        phase=openshell_pb2.SANDBOX_PHASE_READY,
                    )
                ]  # type: ignore[arg-type]
            )

    m = object.__new__(SandboxManager)
    m._stub = _FlakyStub()  # type: ignore[assignment]
    m._timeout = 30.0
    m._retry_policy = RetryPolicy(max_attempts=5, initial_backoff=0.0, max_backoff=0.0, jitter=0.0)
    m._retry_deadline = None

    result = m.list(limit=1, offset=0)
    assert calls["n"] == 3
    assert result[0]["name"] == "sb1"


def test_sandbox_manager_raises_on_non_retryable():
    from shoreguard.client.sandboxes import SandboxManager

    class _BadStub:
        def GetSandbox(self, req, timeout=None):
            raise _FakeRpcError(grpc.StatusCode.INVALID_ARGUMENT, details="nope")

    m = object.__new__(SandboxManager)
    m._stub = _BadStub()  # type: ignore[assignment]
    m._timeout = 30.0
    m._retry_policy = RetryPolicy(max_attempts=4, initial_backoff=0.0, max_backoff=0.0, jitter=0.0)
    m._retry_deadline = None

    with pytest.raises(grpc.RpcError):
        m.get("sb1")
