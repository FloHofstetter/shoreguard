import time
from unittest.mock import patch

from shoreguard.api.auth import (
    clear_lockout,
    is_account_locked,
    record_failed_login,
    reset_lockouts,
)


class TestAccountLockout:
    def setup_method(self):
        reset_lockouts()

    def teardown_method(self):
        reset_lockouts()

    def test_no_lockout_initially(self):
        locked, _ = is_account_locked("user@example.com")
        assert not locked

    def test_under_threshold_not_locked(self, monkeypatch):
        monkeypatch.setenv("SHOREGUARD_ACCOUNT_LOCKOUT_ATTEMPTS", "5")
        from shoreguard.settings import reset_settings

        reset_settings()
        for _ in range(4):
            record_failed_login("user@example.com")
        locked, _ = is_account_locked("user@example.com")
        assert not locked

    def test_at_threshold_locks(self, monkeypatch):
        monkeypatch.setenv("SHOREGUARD_ACCOUNT_LOCKOUT_ATTEMPTS", "3")
        monkeypatch.setenv("SHOREGUARD_ACCOUNT_LOCKOUT_DURATION", "60")
        from shoreguard.settings import reset_settings

        reset_settings()
        for _ in range(3):
            record_failed_login("user@example.com")
        locked, retry_after = is_account_locked("user@example.com")
        assert locked
        assert retry_after > 0

    def test_lockout_auto_expires(self, monkeypatch):
        monkeypatch.setenv("SHOREGUARD_ACCOUNT_LOCKOUT_ATTEMPTS", "2")
        monkeypatch.setenv("SHOREGUARD_ACCOUNT_LOCKOUT_DURATION", "10")
        from shoreguard.settings import reset_settings

        reset_settings()
        base = time.monotonic()
        with patch("shoreguard.api.auth.time.monotonic", return_value=base):
            record_failed_login("user@example.com")
            record_failed_login("user@example.com")
        with patch("shoreguard.api.auth.time.monotonic", return_value=base):
            locked, _ = is_account_locked("user@example.com")
            assert locked
        with patch("shoreguard.api.auth.time.monotonic", return_value=base + 11):
            locked, _ = is_account_locked("user@example.com")
            assert not locked

    def test_clear_lockout_resets(self, monkeypatch):
        monkeypatch.setenv("SHOREGUARD_ACCOUNT_LOCKOUT_ATTEMPTS", "1")
        from shoreguard.settings import reset_settings

        reset_settings()
        record_failed_login("user@example.com")
        locked, _ = is_account_locked("user@example.com")
        assert locked
        clear_lockout("user@example.com")
        locked, _ = is_account_locked("user@example.com")
        assert not locked

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("SHOREGUARD_ACCOUNT_LOCKOUT_ATTEMPTS", "1")
        from shoreguard.settings import reset_settings

        reset_settings()
        record_failed_login("User@Example.COM")
        locked, _ = is_account_locked("user@example.com")
        assert locked

    def test_different_emails_independent(self, monkeypatch):
        monkeypatch.setenv("SHOREGUARD_ACCOUNT_LOCKOUT_ATTEMPTS", "1")
        from shoreguard.settings import reset_settings

        reset_settings()
        record_failed_login("a@example.com")
        locked_a, _ = is_account_locked("a@example.com")
        locked_b, _ = is_account_locked("b@example.com")
        assert locked_a
        assert not locked_b
