"""gRPC-Resilience-Primitive für den Sandbox-Pfad.

Stellt Retry-/Deadline-Wrapping und strukturiertes Error-Mapping für
unary- und stream-opening gRPC-Calls bereit. Die Funktionen sind
bewusst framework-frei (keine Prometheus- oder FastAPI-Importe), damit
sie aus der ``shoreguard.client``-Schicht genutzt werden können, ohne
Import-Zyklen in höhere Schichten zu öffnen. Metriken werden über
einen optionalen ``on_attempt``-Callback injiziert und erst in einem
späteren Chunk an ``shoreguard.api.metrics`` verdrahtet.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import grpc

from shoreguard.exceptions import (
    ConflictError,
    GatewayNotConnectedError,
    NotFoundError,
    SandboxError,
    ShoreGuardError,
    ValidationError,
)

logger = logging.getLogger(__name__)

RETRYABLE_CODES: frozenset[grpc.StatusCode] = frozenset(
    {
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.DEADLINE_EXCEEDED,
        grpc.StatusCode.RESOURCE_EXHAUSTED,
        grpc.StatusCode.ABORTED,
    }
)

NON_RETRYABLE_CODES: frozenset[grpc.StatusCode] = frozenset(
    {
        grpc.StatusCode.INVALID_ARGUMENT,
        grpc.StatusCode.NOT_FOUND,
        grpc.StatusCode.PERMISSION_DENIED,
        grpc.StatusCode.UNAUTHENTICATED,
        grpc.StatusCode.FAILED_PRECONDITION,
        grpc.StatusCode.ALREADY_EXISTS,
    }
)


@dataclass(frozen=True)
class RetryPolicy:
    """Konfiguration für exponentielles Retry mit Jitter.

    Attributes:
        max_attempts: Maximale Zahl der Versuche inklusive des ersten.
        initial_backoff: Startwert des exponentiellen Backoffs in Sekunden.
        max_backoff: Obergrenze des Backoffs in Sekunden.
        jitter: Relativer Jitter-Anteil (0.1 = ±10%).
        retryable_codes: gRPC-Status-Codes, bei denen erneut versucht wird.
    """

    max_attempts: int = 4
    initial_backoff: float = 0.25
    max_backoff: float = 4.0
    jitter: float = 0.1
    retryable_codes: frozenset[grpc.StatusCode] = field(default_factory=lambda: RETRYABLE_CODES)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.initial_backoff < 0 or self.max_backoff < self.initial_backoff:
            raise ValueError("invalid backoff bounds")
        if not 0.0 <= self.jitter < 1.0:
            raise ValueError("jitter must be in [0, 1)")


DEFAULT_POLICY = RetryPolicy()


AttemptCallback = Callable[..., None]


def _grpc_code(exc: BaseException) -> grpc.StatusCode | None:
    """Best-effort access to the status code of a gRPC error.

    Args:
        exc: The exception to inspect.

    Returns:
        grpc.StatusCode | None: The status code, or ``None`` if the exception
            is not a gRPC error or the call to ``code()`` fails.
    """
    code = getattr(exc, "code", None)
    if callable(code):
        try:
            result = code()
        except Exception:  # noqa: BLE001
            return None
        if isinstance(result, grpc.StatusCode):
            return result
    return None


def _sleep_for(
    attempt: int,
    policy: RetryPolicy,
    remaining: float,
    *,
    rng: random.Random,
) -> float:
    """Compute the next sleep duration honoring the remaining deadline.

    Args:
        attempt: 1-based attempt number that just failed.
        policy: Retry policy controlling backoff bounds and jitter.
        remaining: Wall-clock seconds left before the deadline expires.
        rng: Random source used for jitter.

    Returns:
        float: Sleep duration in seconds, clamped to the remaining budget.
    """
    base = min(policy.initial_backoff * (2 ** (attempt - 1)), policy.max_backoff)
    jitter = 1 + rng.uniform(-policy.jitter, policy.jitter)
    candidate = base * jitter
    return max(0.0, min(candidate, remaining))


def call_with_retry[T](
    fn: Callable[[], T],
    *,
    op_name: str,
    policy: RetryPolicy = DEFAULT_POLICY,
    deadline_s: float | None = None,
    on_attempt: AttemptCallback | None = None,
    _sleep: Callable[[float], None] = time.sleep,
    _monotonic: Callable[[], float] = time.monotonic,
    _rng: random.Random | None = None,
) -> T:
    """Execute ``fn`` with retry, jitter and an optional wall-clock deadline.

    Args:
        fn: Zero-arg callable that issues the gRPC call. The caller binds the
            request and per-call timeout up front.
        op_name: Logical-op label (for example ``"sandboxes.create"``) used
            for logs and future metric labels.
        policy: Retry policy. Defaults to :data:`DEFAULT_POLICY`.
        deadline_s: Optional total budget in seconds. ``None`` lets
            ``max_attempts`` be the only limiter. Retries must not exceed the
            budget; jitter is clamped to the remaining time.
        on_attempt: Optional callback invoked per attempt with keyword args
            ``op_name``, ``attempt``, ``code`` and ``outcome`` (one of
            ``"ok"``, ``"retry"``, ``"giveup"``). Wired to Prometheus in a
            later chunk.
        _sleep: Sleep function used between retries. Injection point for
            tests; defaults to :func:`time.sleep`.
        _monotonic: Monotonic-clock function used to track the deadline.
            Injection point for tests; defaults to :func:`time.monotonic`.
        _rng: Random source used for jitter. Injection point for tests;
            defaults to a fresh :class:`random.Random`.

    Returns:
        T: The value returned by ``fn``.

    Raises:
        grpc.RpcError: The last error after the retry budget is exhausted or
            after a non-retryable status code.
        RuntimeError: If the retry loop completes without a result. This is
            unreachable under normal control flow and exists only to satisfy
            the static type checker.
    """
    rng = _rng or random.Random()
    start = _monotonic()
    for attempt in range(1, policy.max_attempts + 1):
        try:
            result = fn()
        except grpc.RpcError as exc:
            code = _grpc_code(exc)
            is_retryable = code is not None and code in policy.retryable_codes
            is_last = attempt >= policy.max_attempts
            elapsed = _monotonic() - start
            remaining = None if deadline_s is None else max(0.0, deadline_s - elapsed)
            budget_exhausted = remaining is not None and remaining <= 0
            if not is_retryable or is_last or budget_exhausted:
                if on_attempt is not None:
                    on_attempt(op_name=op_name, attempt=attempt, code=code, outcome="giveup")
                logger.debug(
                    "grpc call %s failed on attempt %d (code=%s, retryable=%s)",
                    op_name,
                    attempt,
                    getattr(code, "name", code),
                    is_retryable,
                )
                raise
            sleep_for = _sleep_for(attempt, policy, remaining or policy.max_backoff, rng=rng)
            if on_attempt is not None:
                on_attempt(op_name=op_name, attempt=attempt, code=code, outcome="retry")
            logger.debug(
                "grpc call %s retrying after %.3fs (attempt %d, code=%s)",
                op_name,
                sleep_for,
                attempt,
                getattr(code, "name", code),
            )
            if sleep_for > 0:
                _sleep(sleep_for)
            continue
        else:
            if on_attempt is not None:
                on_attempt(op_name=op_name, attempt=attempt, code=None, outcome="ok")
            return result
    raise RuntimeError("call_with_retry loop exited without result")  # pragma: no cover


def stream_with_retry[T](
    open_fn: Callable[[], Iterator[T]],
    *,
    op_name: str,
    policy: RetryPolicy = DEFAULT_POLICY,
    deadline_s: float | None = None,
    on_attempt: AttemptCallback | None = None,
) -> Iterator[T]:
    """Retry only the opening of a gRPC server-stream.

    In-flight errors propagate unchanged: streams such as ``ExecSandbox`` or
    ``WatchSandbox`` are not idempotent, so a mid-stream retry would double
    side effects or lose events.

    Args:
        open_fn: Callable that opens the gRPC stream and returns an iterator.
        op_name: Logical-op label for logs and future metrics.
        policy: Retry policy applied to the open call only.
        deadline_s: Optional total budget in seconds for opening the stream.
        on_attempt: Callback forwarded to :func:`call_with_retry`.

    Returns:
        Iterator[T]: The opened stream iterator.
    """
    return call_with_retry(
        open_fn,
        op_name=op_name,
        policy=policy,
        deadline_s=deadline_s,
        on_attempt=on_attempt,
    )


_CODE_TO_EXC: dict[grpc.StatusCode, type[ShoreGuardError]] = {
    grpc.StatusCode.UNAVAILABLE: GatewayNotConnectedError,
    grpc.StatusCode.UNAUTHENTICATED: GatewayNotConnectedError,
    grpc.StatusCode.NOT_FOUND: NotFoundError,
    grpc.StatusCode.ALREADY_EXISTS: ConflictError,
    grpc.StatusCode.FAILED_PRECONDITION: ConflictError,
    grpc.StatusCode.ABORTED: ConflictError,
    grpc.StatusCode.INVALID_ARGUMENT: ValidationError,
}


def classify_grpc_error(exc: BaseException) -> ShoreGuardError:
    """Map a ``grpc.RpcError`` to a ``ShoreGuardError`` subclass.

    Unknown codes fall back to :class:`SandboxError`, which covers the common
    sandbox path. Callers should use ``raise classify_grpc_error(exc) from
    exc`` to preserve the original gRPC error via ``__cause__``.

    Args:
        exc: The caught exception, typically a :class:`grpc.RpcError`.

    Returns:
        ShoreGuardError: A matching domain exception. Non-gRPC errors yield a
            :class:`SandboxError` carrying ``str(exc)``.
    """
    code = _grpc_code(exc)
    if code is None:
        return SandboxError(str(exc))
    details: Any = None
    raw_details = getattr(exc, "details", None)
    if callable(raw_details):
        try:
            details = raw_details()
        except Exception:  # noqa: BLE001
            details = None
    message = details or code.name
    exc_cls = _CODE_TO_EXC.get(code, SandboxError)
    return exc_cls(message)
