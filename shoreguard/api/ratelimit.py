"""In-memory sliding-window rate limiter for login and write endpoints."""

from __future__ import annotations

import time
from collections import deque


class SlidingWindowRateLimiter:
    """IP-based rate limiter using a sliding time window.

    Each key (typically a client IP) maintains a deque of timestamps.
    When the number of recorded attempts within *window_seconds* exceeds
    *max_attempts*, the key is blocked for *lockout_seconds* after the
    oldest relevant timestamp.

    Args:
        max_attempts: Maximum allowed attempts within the window.
        window_seconds: Sliding window duration in seconds.
        lockout_seconds: How long a blocked key must wait.
    """

    _CLEANUP_INTERVAL = 100  # run cleanup every N calls to ``is_limited``

    def __init__(self, max_attempts: int, window_seconds: int, lockout_seconds: int) -> None:  # noqa: D107
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._buckets: dict[str, deque[float]] = {}
        self._call_count = 0

    def is_limited(self, key: str) -> tuple[bool, int]:
        """Check whether *key* is rate-limited.

        Args:
            key: The rate-limit key (e.g. client IP address).

        Returns:
            tuple[bool, int]: A ``(blocked, retry_after)`` tuple.  When *blocked* is
                ``True``, *retry_after* is the number of seconds the caller should wait.
        """
        self._call_count += 1
        if self._call_count % self._CLEANUP_INTERVAL == 0:
            self._cleanup()

        now = time.monotonic()
        bucket = self._buckets.get(key)
        if bucket is None:
            return False, 0

        cutoff = now - self.window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= self.max_attempts:
            retry_after = int(bucket[0] + self.lockout_seconds - now) + 1
            return True, max(retry_after, 1)

        return False, 0

    def record(self, key: str) -> None:
        """Record an attempt for *key*.

        Args:
            key: The rate-limit key.
        """
        now = time.monotonic()
        if key not in self._buckets:
            self._buckets[key] = deque()
        self._buckets[key].append(now)

    def reset(self, key: str) -> None:
        """Clear all recorded attempts for *key*.

        Args:
            key: The rate-limit key.
        """
        self._buckets.pop(key, None)

    def _cleanup(self) -> None:
        """Remove stale entries to prevent unbounded memory growth."""
        now = time.monotonic()
        cutoff = now - self.window_seconds - self.lockout_seconds
        stale = [k for k, v in self._buckets.items() if not v or v[-1] < cutoff]
        for k in stale:
            del self._buckets[k]


# ── Module-level singleton ────────────────────────────────────────────────

_limiter: SlidingWindowRateLimiter | None = None


def get_login_limiter() -> SlidingWindowRateLimiter:
    """Return the global login rate limiter, creating it on first call.

    Returns:
        SlidingWindowRateLimiter: The singleton instance.
    """
    global _limiter  # noqa: PLW0603
    if _limiter is None:
        from shoreguard.settings import get_settings

        s = get_settings().auth
        _limiter = SlidingWindowRateLimiter(
            max_attempts=s.login_rate_limit_attempts,
            window_seconds=s.login_rate_limit_window,
            lockout_seconds=s.login_rate_limit_lockout,
        )
    return _limiter


def reset_login_limiter() -> None:
    """Clear the singleton (for tests)."""
    global _limiter  # noqa: PLW0603
    _limiter = None


# ── Write rate limiter (for authenticated mutation endpoints) ────────────

_write_limiter: SlidingWindowRateLimiter | None = None


def get_write_limiter() -> SlidingWindowRateLimiter:
    """Return the global write rate limiter, creating it on first call.

    Returns:
        SlidingWindowRateLimiter: The singleton instance.
    """
    global _write_limiter  # noqa: PLW0603
    if _write_limiter is None:
        from shoreguard.settings import get_settings

        s = get_settings().auth
        _write_limiter = SlidingWindowRateLimiter(
            max_attempts=s.write_rate_limit_attempts,
            window_seconds=s.write_rate_limit_window,
            lockout_seconds=s.write_rate_limit_lockout,
        )
    return _write_limiter


def reset_write_limiter() -> None:
    """Clear the singleton (for tests)."""
    global _write_limiter  # noqa: PLW0603
    _write_limiter = None


# ── Global API rate limiter (coarse DDoS guardrail, per client IP) ────────

_global_limiter: SlidingWindowRateLimiter | None = None


def get_global_limiter() -> SlidingWindowRateLimiter:
    """Return the global API rate limiter, creating it on first call.

    Applied by ``global_rate_limit_middleware`` to every HTTP request
    except health/metrics endpoints. Intended as a coarse DDoS guardrail,
    not fine-grained abuse protection.

    Returns:
        SlidingWindowRateLimiter: The singleton instance.
    """
    global _global_limiter  # noqa: PLW0603
    if _global_limiter is None:
        from shoreguard.settings import get_settings

        s = get_settings().auth
        _global_limiter = SlidingWindowRateLimiter(
            max_attempts=s.global_rate_limit_attempts,
            window_seconds=s.global_rate_limit_window,
            lockout_seconds=s.global_rate_limit_lockout,
        )
    return _global_limiter


def reset_global_limiter() -> None:
    """Clear the singleton (for tests)."""
    global _global_limiter  # noqa: PLW0603
    _global_limiter = None
