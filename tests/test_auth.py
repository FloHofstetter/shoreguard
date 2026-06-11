"""Unit tests for shoreguard.api.auth — user-based auth with service principals."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from shoreguard.api.auth import (
    ROLES,
    authenticate_user,
    create_service_principal,
    create_session_token,
    create_user,
    hash_password,
    is_setup_complete,
    verify_password,
    verify_session_token,
)
from shoreguard.api.auth.core import _hash_key


@pytest.fixture
def db():
    from tests.conftest import make_auth_test_db

    factory, dispose = make_auth_test_db()
    yield factory
    dispose()


class TestPasswordHashing:
    def test_hash_and_verify(self):
        h = hash_password("test123")
        assert verify_password("test123", h)
        assert not verify_password("wrong", h)

    def test_different_passwords_different_hashes(self):
        assert hash_password("a") != hash_password("b")


class TestKeyHashing:
    def test_deterministic(self):
        assert _hash_key("test") == _hash_key("test")

    def test_hex_length(self):
        assert len(_hash_key("any")) == 64


class TestSessionToken:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_create_and_verify(self):
        token = create_session_token(user_id=1, role="admin")
        result = verify_session_token(token)
        assert result == (1, "admin")

    def test_each_role_roundtrips(self):
        for role in ROLES:
            token = create_session_token(user_id=42, role=role)
            result = verify_session_token(token)
            assert result == (42, role)

    def test_tampered_role_rejected(self):
        token = create_session_token(user_id=1, role="viewer")
        parts = token.split(".")
        parts[3] = "admin"
        assert verify_session_token(".".join(parts)) is None

    def test_tampered_user_id_rejected(self):
        token = create_session_token(user_id=1, role="admin")
        parts = token.split(".")
        parts[2] = "999"
        assert verify_session_token(".".join(parts)) is None

    def test_expired_token(self):
        with patch("shoreguard.api.auth.core.time") as mock_time:
            mock_time.time.return_value = time.time() - 86400 * 8
            token = create_session_token(user_id=1, role="admin")
        assert verify_session_token(token) is None

    def test_malformed_tokens(self):
        assert verify_session_token("") is None
        assert verify_session_token("a.b") is None
        assert verify_session_token("a.b.c") is None
        assert verify_session_token("a.b.c.d") is None  # 4 parts (old format)

    def test_invalid_role(self):
        assert verify_session_token("a.b.1.badrole.sig") is None


class TestUserCRUD:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    async def test_create_and_authenticate(self):
        info = await create_user("test@example.com", "password123", "operator")
        assert info["email"] == "test@example.com"
        assert info["role"] == "operator"

        user = await authenticate_user("test@example.com", "password123")
        assert user is not None
        assert user["role"] == "operator"

    async def test_wrong_password(self):
        await create_user("test@example.com", "password123", "viewer")
        assert await authenticate_user("test@example.com", "wrong") is None

    async def test_nonexistent_user(self):
        assert await authenticate_user("nobody@example.com", "pass") is None

    async def test_invalid_role_raises(self):
        from shoreguard.exceptions import ValidationError as DomainValidationError

        with pytest.raises(DomainValidationError, match="Invalid role"):
            await create_user("test@example.com", "pass", "superadmin")

    async def test_email_normalized_on_create(self):
        info = await create_user("UPPER@Example.COM", "password123", "viewer")
        assert info["email"] == "upper@example.com"

    async def test_email_normalized_on_authenticate(self):
        await create_user("user@test.com", "password123", "viewer")
        assert await authenticate_user("USER@Test.COM", "password123") is not None
        assert await authenticate_user("user@test.com", "password123") is not None


class TestServicePrincipalCRUD:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    async def test_create_sp(self):
        key, info = await create_service_principal("terraform", "operator")
        assert len(key) > 20
        assert info["name"] == "terraform"
        assert info["role"] == "operator"

    async def test_invalid_role_raises(self):
        from shoreguard.exceptions import ValidationError as DomainValidationError

        with pytest.raises(DomainValidationError, match="Invalid role"):
            await create_service_principal("bad", "superadmin")


class TestSetupComplete:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    async def test_empty_db(self):
        assert not await is_setup_complete()

    async def test_with_user(self):
        await create_user("admin@localhost", "pass", "admin")
        assert await is_setup_complete()
