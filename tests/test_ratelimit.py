import time
from unittest.mock import MagicMock, patch

from shoreguard.api.ratelimit import (
    SlidingWindowRateLimiter,
    get_write_limiter,
    reset_write_limiter,
)


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


class TestWriteLimiter:
    def setup_method(self):
        reset_write_limiter()

    def teardown_method(self):
        reset_write_limiter()

    def test_singleton_created_with_settings(self):
        limiter = get_write_limiter()
        assert isinstance(limiter, SlidingWindowRateLimiter)
        assert limiter.max_attempts == 30  # default
        assert limiter.window_seconds == 60
        assert limiter.lockout_seconds == 120

    def test_singleton_is_same_instance(self):
        a = get_write_limiter()
        b = get_write_limiter()
        assert a is b

    def test_reset_clears_singleton(self):
        a = get_write_limiter()
        reset_write_limiter()
        b = get_write_limiter()
        assert a is not b


class TestCheckWriteRateLimit:
    def setup_method(self):
        reset_write_limiter()

    def teardown_method(self):
        reset_write_limiter()

    def test_allows_under_limit(self):

        from shoreguard.api.validation import check_write_rate_limit

        request = MagicMock()
        request.state.user_id = "user-1"
        # Should not raise
        check_write_rate_limit(request)

    def test_blocks_over_limit(self):
        import pytest
        from fastapi import HTTPException

        from shoreguard.api.validation import check_write_rate_limit

        request = MagicMock()
        request.state.user_id = "user-flood"
        limiter = get_write_limiter()
        for _ in range(limiter.max_attempts):
            limiter.record("user-flood")
        with pytest.raises(HTTPException) as exc_info:
            check_write_rate_limit(request)
        assert exc_info.value.status_code == 429

    def test_uses_user_id_as_key(self):
        from shoreguard.api.validation import check_write_rate_limit

        request = MagicMock()
        request.state.user_id = "user-a"
        check_write_rate_limit(request)
        limiter = get_write_limiter()
        assert "user-a" in limiter._buckets

    def test_falls_back_to_ip(self):
        from shoreguard.api.validation import check_write_rate_limit

        request = MagicMock(spec=["state", "client"])
        request.state = MagicMock(spec=[])  # no user_id attribute
        request.client.host = "10.0.0.1"
        check_write_rate_limit(request)
        limiter = get_write_limiter()
        assert "10.0.0.1" in limiter._buckets
