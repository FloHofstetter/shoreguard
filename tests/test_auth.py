"""Unit tests for shoreguard.api.auth."""

from __future__ import annotations

import time
from unittest.mock import patch

from shoreguard.api.auth import (
    check_api_key,
    configure,
    create_session_token,
    is_auth_enabled,
    verify_session_token,
)


class TestConfigure:
    def test_no_key(self):
        configure(None)
        assert not is_auth_enabled()

    def test_with_key(self):
        configure("test-key")
        assert is_auth_enabled()
        configure(None)


class TestSessionToken:
    def setup_method(self):
        configure("test-key-123")

    def teardown_method(self):
        configure(None)

    def test_create_and_verify(self):
        token = create_session_token()
        assert verify_session_token(token)

    def test_tampered_signature(self):
        token = create_session_token()
        parts = token.split(".")
        parts[2] = "bad" * 20
        assert not verify_session_token(".".join(parts))

    def test_expired_token(self):
        with patch("shoreguard.api.auth.time") as mock_time:
            mock_time.time.return_value = time.time() - 86400 * 8
            token = create_session_token()
        assert not verify_session_token(token)

    def test_malformed_token(self):
        assert not verify_session_token("")
        assert not verify_session_token("a.b")
        assert not verify_session_token("a.b.c.d")

    def test_non_numeric_expiry(self):
        assert not verify_session_token("nonce.notanumber.sig")

    def test_different_key_rejects(self):
        token = create_session_token()
        configure("different-key")
        assert not verify_session_token(token)
        configure("test-key-123")


class TestCheckApiKey:
    def setup_method(self):
        configure("my-secret")

    def teardown_method(self):
        configure(None)

    def test_correct_key(self):
        assert check_api_key("my-secret")

    def test_wrong_key(self):
        assert not check_api_key("wrong")

    def test_no_key_configured(self):
        configure(None)
        assert not check_api_key("anything")
