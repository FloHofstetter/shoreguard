"""Unit tests for shoreguard.api.auth."""

from __future__ import annotations

import time
from unittest.mock import patch

from shoreguard.api.auth import (
    check_api_key,
    configure,
    create_session_token,
    is_auth_enabled,
    reset,
    verify_session_token,
)


class TestConfigure:
    def teardown_method(self):
        reset()

    def test_no_key(self):
        reset()
        assert not is_auth_enabled()

    def test_with_key(self):
        configure("test-key")
        assert is_auth_enabled()


class TestSessionToken:
    def setup_method(self):
        configure("test-key-123")

    def teardown_method(self):
        reset()

    def test_create_and_verify(self):
        token = create_session_token()
        assert verify_session_token(token) == "admin"

    def test_create_with_role(self):
        token = create_session_token(role="viewer")
        assert verify_session_token(token) == "viewer"

    def test_tampered_signature(self):
        token = create_session_token()
        parts = token.split(".")
        parts[3] = "bad" * 20
        assert verify_session_token(".".join(parts)) is None

    def test_expired_token(self):
        with patch("shoreguard.api.auth.time") as mock_time:
            mock_time.time.return_value = time.time() - 86400 * 8
            token = create_session_token()
        assert verify_session_token(token) is None

    def test_malformed_token(self):
        assert verify_session_token("") is None
        assert verify_session_token("a.b") is None
        assert verify_session_token("a.b.c") is None  # 3 parts (old format)

    def test_four_parts_with_invalid_role(self):
        assert verify_session_token("a.b.invalid_role.d") is None

    def test_non_numeric_expiry(self):
        assert verify_session_token("nonce.notanumber.admin.sig") is None

    def test_different_key_rejects(self):
        token = create_session_token()
        configure("different-key")
        assert verify_session_token(token) is None
        configure("test-key-123")


class TestCheckApiKey:
    def setup_method(self):
        configure("my-secret")

    def teardown_method(self):
        reset()

    def test_correct_key(self):
        assert check_api_key("my-secret") == "admin"

    def test_wrong_key(self):
        assert check_api_key("wrong") is None

    def test_no_key_configured(self):
        reset()
        assert check_api_key("anything") is None
