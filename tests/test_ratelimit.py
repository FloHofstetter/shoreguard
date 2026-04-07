import time
from unittest.mock import patch

from shoreguard.api.ratelimit import SlidingWindowRateLimiter


class TestSlidingWindowRateLimiter:
    def test_under_limit(self):
        limiter = SlidingWindowRateLimiter(max_attempts=5, window_seconds=60, lockout_seconds=120)
        for _ in range(4):
            limiter.record("1.2.3.4")
        blocked, _ = limiter.is_limited("1.2.3.4")
        assert not blocked

    def test_at_limit_blocks(self):
        limiter = SlidingWindowRateLimiter(max_attempts=3, window_seconds=60, lockout_seconds=120)
        for _ in range(3):
            limiter.record("1.2.3.4")
        blocked, retry_after = limiter.is_limited("1.2.3.4")
        assert blocked
        assert retry_after > 0

    def test_unknown_key_not_limited(self):
        limiter = SlidingWindowRateLimiter(max_attempts=3, window_seconds=60, lockout_seconds=120)
        blocked, _ = limiter.is_limited("unknown")
        assert not blocked

    def test_window_expiry(self):
        limiter = SlidingWindowRateLimiter(max_attempts=2, window_seconds=10, lockout_seconds=5)
        base = time.monotonic()
        with patch("shoreguard.api.ratelimit.time.monotonic", return_value=base):
            limiter.record("ip")
            limiter.record("ip")
        blocked, _ = limiter.is_limited("ip")
        assert blocked

        # Advance past window + lockout
        with patch("shoreguard.api.ratelimit.time.monotonic", return_value=base + 16):
            blocked, _ = limiter.is_limited("ip")
        assert not blocked

    def test_reset_clears_key(self):
        limiter = SlidingWindowRateLimiter(max_attempts=1, window_seconds=60, lockout_seconds=60)
        limiter.record("ip")
        blocked, _ = limiter.is_limited("ip")
        assert blocked
        limiter.reset("ip")
        blocked, _ = limiter.is_limited("ip")
        assert not blocked

    def test_reset_nonexistent_key(self):
        limiter = SlidingWindowRateLimiter(max_attempts=1, window_seconds=60, lockout_seconds=60)
        limiter.reset("nope")  # should not raise

    def test_cleanup_removes_stale_keys(self):
        limiter = SlidingWindowRateLimiter(max_attempts=2, window_seconds=10, lockout_seconds=5)
        base = time.monotonic()
        with patch("shoreguard.api.ratelimit.time.monotonic", return_value=base):
            limiter.record("stale")

        with patch("shoreguard.api.ratelimit.time.monotonic", return_value=base + 20):
            limiter._cleanup()
        assert "stale" not in limiter._buckets

    def test_different_keys_independent(self):
        limiter = SlidingWindowRateLimiter(max_attempts=1, window_seconds=60, lockout_seconds=60)
        limiter.record("ip-a")
        blocked_a, _ = limiter.is_limited("ip-a")
        blocked_b, _ = limiter.is_limited("ip-b")
        assert blocked_a
        assert not blocked_b
