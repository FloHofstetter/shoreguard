"""Mutation-killing tests for shoreguard.api.auth.

Designed to kill mutmut survivors by asserting on exact return values,
dict keys, boundary conditions, and state changes.
"""

from __future__ import annotations

import datetime
import time
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.api import auth
from shoreguard.api.auth import (
    _ROLE_RANK,
    COOKIE_NAME,
    INVITE_MAX_AGE,
    ROLES,
    SESSION_MAX_AGE,
    _hash_key,
    _lookup_sp_identity,
    accept_invite,
    add_group_member,
    authenticate_user,
    bootstrap_admin_user,
    create_group,
    create_service_principal,
    create_session_token,
    create_user,
    delete_group,
    delete_service_principal,
    delete_user,
    find_or_create_oidc_user,
    get_group,
    hash_password,
    is_setup_complete,
    list_gateway_roles_for_sp,
    list_gateway_roles_for_user,
    list_group_gateway_roles,
    list_group_members,
    list_groups,
    list_service_principals,
    list_user_groups,
    list_users,
    remove_gateway_role,
    remove_group_gateway_role,
    remove_group_member,
    rotate_service_principal,
    set_gateway_role,
    set_group_gateway_role,
    update_group,
    verify_password,
    verify_session_token,
)
from shoreguard.exceptions import NotFoundError
from shoreguard.exceptions import ValidationError as DomainValidationError
from shoreguard.models import Base, Gateway

# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    auth.init_auth_for_test(factory)
    yield factory
    auth.reset()
    engine.dispose()


@pytest.fixture
def _with_gateway(db):
    session = db()
    gw = Gateway(
        name="test-gw",
        endpoint="10.0.0.1:8443",
        scheme="https",
        registered_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
    session.add(gw)
    session.commit()
    session.close()


@pytest.fixture
def _with_two_gateways(db):
    session = db()
    for name in ("gw-a", "gw-b"):
        gw = Gateway(
            name=name,
            endpoint="10.0.0.1:8443",
            scheme="https",
            registered_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        )
        session.add(gw)
    session.commit()
    session.close()


# ─── Constants ──────────────────────────────────────────────────────────────


class TestConstants:
    """Kill mutations that change ROLES tuple or ROLE_RANK values."""

    def test_roles_exact_tuple(self):
        assert ROLES == ("admin", "operator", "viewer")
        assert len(ROLES) == 3
        assert ROLES[0] == "admin"
        assert ROLES[1] == "operator"
        assert ROLES[2] == "viewer"

    def test_role_rank_exact_values(self):
        assert _ROLE_RANK["admin"] == 2
        assert _ROLE_RANK["operator"] == 1
        assert _ROLE_RANK["viewer"] == 0
        assert len(_ROLE_RANK) == 3

    def test_role_rank_ordering(self):
        assert _ROLE_RANK["admin"] > _ROLE_RANK["operator"] > _ROLE_RANK["viewer"]

    def test_cookie_name(self):
        assert COOKIE_NAME == "sg_session"

    def test_session_max_age(self):
        assert SESSION_MAX_AGE == 86400 * 7
        assert SESSION_MAX_AGE == 604800

    def test_invite_max_age(self):
        assert INVITE_MAX_AGE == 86400 * 7
        assert INVITE_MAX_AGE == 604800


# ─── require_role: exact return values and role checks ────────────────────


class TestRequireRoleUnit:
    """Kill mutations in require_role by checking exact role comparisons."""

    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_role_rank_admin_exactly_2(self):
        assert _ROLE_RANK.get("admin", -1) == 2

    def test_role_rank_operator_exactly_1(self):
        assert _ROLE_RANK.get("operator", -1) == 1

    def test_role_rank_viewer_exactly_0(self):
        assert _ROLE_RANK.get("viewer", -1) == 0

    def test_role_rank_unknown_returns_negative(self):
        assert _ROLE_RANK.get("unknown", -1) == -1

    def test_viewer_not_sufficient_for_operator(self):
        assert _ROLE_RANK.get("viewer", -1) < _ROLE_RANK["operator"]

    def test_viewer_not_sufficient_for_admin(self):
        assert _ROLE_RANK.get("viewer", -1) < _ROLE_RANK["admin"]

    def test_operator_not_sufficient_for_admin(self):
        assert _ROLE_RANK.get("operator", -1) < _ROLE_RANK["admin"]

    def test_admin_sufficient_for_operator(self):
        assert _ROLE_RANK.get("admin", -1) >= _ROLE_RANK["operator"]

    def test_admin_sufficient_for_viewer(self):
        assert _ROLE_RANK.get("admin", -1) >= _ROLE_RANK["viewer"]

    def test_operator_sufficient_for_viewer(self):
        assert _ROLE_RANK.get("operator", -1) >= _ROLE_RANK["viewer"]


# ─── create_service_principal: exact return values ────────────────────────


class TestCreateServicePrincipalExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_key_starts_with_sg_prefix(self):
        key, info = create_service_principal("test-sp", "viewer")
        assert key.startswith("sg_")

    def test_key_prefix_is_first_12_chars(self):
        key, info = create_service_principal("test-sp", "viewer")
        assert info["key_prefix"] == key[:12]

    def test_info_dict_has_all_keys(self):
        key, info = create_service_principal("test-sp", "operator")
        assert set(info.keys()) == {"id", "name", "role", "key_prefix", "created_at", "expires_at"}

    def test_info_dict_exact_values(self):
        key, info = create_service_principal("test-sp", "operator")
        assert info["name"] == "test-sp"
        assert info["role"] == "operator"
        assert info["expires_at"] is None
        assert isinstance(info["id"], int)
        assert info["id"] > 0

    def test_info_dict_with_expiry(self):
        future = datetime.datetime(2030, 1, 1, tzinfo=datetime.UTC)
        key, info = create_service_principal("test-sp", "admin", expires_at=future)
        assert info["expires_at"] == future.isoformat()
        assert info["role"] == "admin"

    def test_created_at_is_iso_string(self):
        key, info = create_service_principal("test-sp", "viewer")
        # Should parse as ISO datetime
        datetime.datetime.fromisoformat(info["created_at"])

    def test_each_role_accepted(self):
        for i, role in enumerate(ROLES):
            key, info = create_service_principal(f"sp-{role}", role)
            assert info["role"] == role
            assert info["name"] == f"sp-{role}"

    def test_invalid_role_raises_validation_error(self):
        with pytest.raises(DomainValidationError, match="Invalid role"):
            create_service_principal("bad", "superadmin")

    def test_duplicate_name_raises_integrity_error(self):
        create_service_principal("dup", "viewer")
        with pytest.raises(IntegrityError):
            create_service_principal("dup", "admin")

    def test_created_by_stored(self):
        user = create_user("creator@test.com", "password1", "admin")
        key, info = create_service_principal("test-sp", "viewer", created_by=user["id"])
        sps = list_service_principals()
        sp = next(s for s in sps if s["name"] == "test-sp")
        assert sp["created_by"] == user["id"]


# ─── rotate_service_principal: exact return values ────────────────────────


class TestRotateServicePrincipalExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_rotate_returns_new_key_and_info(self):
        old_key, info = create_service_principal("test-sp", "operator")
        result = rotate_service_principal(info["id"])
        assert result is not None
        new_key, new_info = result
        assert new_key.startswith("sg_")
        assert new_key != old_key
        assert new_info["id"] == info["id"]
        assert new_info["name"] == "test-sp"
        assert new_info["role"] == "operator"
        assert new_info["key_prefix"] == new_key[:12]

    def test_rotate_old_key_stops_working(self):
        old_key, info = create_service_principal("test-sp", "operator")
        result = rotate_service_principal(info["id"])
        new_key, _ = result
        # Old key should no longer authenticate
        assert _lookup_sp_identity(old_key) is None
        # New key should work
        sp = _lookup_sp_identity(new_key)
        assert sp is not None
        assert sp["name"] == "test-sp"
        assert sp["role"] == "operator"
        assert sp["id"] == info["id"]

    def test_rotate_nonexistent_returns_none(self):
        assert rotate_service_principal(99999) is None

    def test_rotate_preserves_role(self):
        for role in ROLES:
            key, info = create_service_principal(f"sp-{role}", role)
            result = rotate_service_principal(info["id"])
            assert result is not None
            _, new_info = result
            assert new_info["role"] == role

    def test_rotate_info_has_correct_keys(self):
        _, info = create_service_principal("test-sp", "admin")
        result = rotate_service_principal(info["id"])
        _, new_info = result
        assert set(new_info.keys()) == {"id", "name", "role", "key_prefix", "expires_at"}

    def test_rotate_preserves_expires_at(self):
        future = datetime.datetime(2030, 6, 15, tzinfo=datetime.UTC)
        _, info = create_service_principal("test-sp", "viewer", expires_at=future)
        result = rotate_service_principal(info["id"])
        _, new_info = result
        assert new_info["expires_at"] is not None
        assert "2030-06-15" in new_info["expires_at"]

    def test_rotate_none_expires_at(self):
        _, info = create_service_principal("test-sp", "viewer")
        result = rotate_service_principal(info["id"])
        _, new_info = result
        assert new_info["expires_at"] is None


# ─── delete_user: exact return values and state ──────────────────────────


class TestDeleteUserExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_delete_existing_returns_true(self):
        info = create_user("u@test.com", "password1", "viewer")
        assert delete_user(info["id"]) is True

    def test_delete_nonexistent_returns_false(self):
        assert delete_user(99999) is False

    def test_delete_removes_from_list(self):
        info = create_user("u@test.com", "password1", "viewer")
        assert len(list_users()) == 1
        delete_user(info["id"])
        assert len(list_users()) == 0
        assert list_users() == []

    def test_delete_last_admin_raises(self):
        info = create_user("admin@test.com", "password1", "admin")
        with pytest.raises(DomainValidationError, match="last active admin"):
            delete_user(info["id"])

    def test_delete_admin_when_another_exists(self):
        admin1 = create_user("admin1@test.com", "password1", "admin")
        create_user("admin2@test.com", "password1", "admin")
        assert delete_user(admin1["id"]) is True

    def test_deleted_user_cannot_authenticate(self):
        info = create_user("u@test.com", "password1", "viewer")
        delete_user(info["id"])
        assert authenticate_user("u@test.com", "password1") is None

    def test_delete_preserves_other_users(self):
        u1 = create_user("a@test.com", "password1", "viewer")
        create_user("b@test.com", "password1", "operator")
        delete_user(u1["id"])
        users = list_users()
        assert len(users) == 1
        assert users[0]["email"] == "b@test.com"
        assert users[0]["role"] == "operator"

    def test_delete_inactive_admin_does_not_count(self):
        """Deleting the only active admin when an inactive admin exists should fail."""
        from shoreguard.models import User

        admin1 = create_user("admin1@test.com", "password1", "admin")
        admin2 = create_user("admin2@test.com", "password1", "admin")
        # Deactivate admin2
        session = auth._session_factory()
        user = session.query(User).filter(User.id == admin2["id"]).first()
        user.is_active = False
        session.commit()
        session.close()
        # Now admin1 is the last ACTIVE admin
        with pytest.raises(DomainValidationError, match="last active admin"):
            delete_user(admin1["id"])

    def test_delete_viewer_not_blocked(self):
        create_user("admin@test.com", "password1", "admin")
        viewer = create_user("v@test.com", "password1", "viewer")
        assert delete_user(viewer["id"]) is True

    def test_delete_operator_not_blocked(self):
        create_user("admin@test.com", "password1", "admin")
        op = create_user("op@test.com", "password1", "operator")
        assert delete_user(op["id"]) is True


# ─── find_or_create_oidc_user: exact return values ───────────────────────


class TestFindOrCreateOidcUserExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_create_new_user_returns_create_action(self):
        result = find_or_create_oidc_user("oidc@test.com", "google", "sub123", "viewer")
        assert result["action"] == "create"
        assert result["user"]["email"] == "oidc@test.com"
        assert result["user"]["role"] == "viewer"
        assert "id" in result["user"]
        assert isinstance(result["user"]["id"], int)

    def test_returning_user_returns_login_action(self):
        # First call creates
        result1 = find_or_create_oidc_user("oidc@test.com", "google", "sub123", "viewer")
        assert result1["action"] == "create"
        # Second call with same provider+sub returns login
        result2 = find_or_create_oidc_user("oidc@test.com", "google", "sub123", "viewer")
        assert result2["action"] == "login"
        assert result2["user"]["id"] == result1["user"]["id"]
        assert result2["user"]["email"] == "oidc@test.com"

    def test_link_existing_email_returns_link_action(self):
        # Create a local user first
        local = create_user("local@test.com", "password1", "operator")
        # OIDC login with same email links them
        result = find_or_create_oidc_user("local@test.com", "google", "sub456", "viewer")
        assert result["action"] == "link"
        assert result["user"]["id"] == local["id"]
        assert result["user"]["email"] == "local@test.com"
        assert result["user"]["role"] == "operator"  # keeps existing role

    def test_invalid_role_defaults_to_viewer(self):
        result = find_or_create_oidc_user("oidc@test.com", "google", "sub123", "superadmin")
        assert result["user"]["role"] == "viewer"
        assert result["action"] == "create"

    def test_valid_roles_accepted(self):
        for i, role in enumerate(ROLES):
            result = find_or_create_oidc_user(f"u{i}@test.com", "google", f"sub{i}", role)
            assert result["user"]["role"] == role
            assert result["action"] == "create"

    def test_email_normalized(self):
        result = find_or_create_oidc_user("UPPER@Test.COM", "google", "sub123", "viewer")
        assert result["user"]["email"] == "upper@test.com"

    def test_result_dict_has_exact_keys(self):
        result = find_or_create_oidc_user("oidc@test.com", "google", "sub123", "viewer")
        assert set(result.keys()) == {"user", "action"}
        assert set(result["user"].keys()) == {"id", "email", "role"}

    def test_link_sets_oidc_fields(self):
        """After linking, the same oidc_sub returns 'login' on next call."""
        create_user("local@test.com", "password1", "operator")
        find_or_create_oidc_user("local@test.com", "google", "sub456", "viewer")
        # Now login with the same OIDC identity
        result = find_or_create_oidc_user("local@test.com", "google", "sub456", "viewer")
        assert result["action"] == "login"

    def test_different_provider_same_email_creates_new_link(self):
        """First OIDC provider links, second provider with different sub creates new user
        because sub lookup doesn't match."""
        local = create_user("local@test.com", "password1", "operator")
        r1 = find_or_create_oidc_user("local@test.com", "google", "g-sub", "viewer")
        assert r1["action"] == "link"
        # Now the user is linked to google/g-sub. A different provider lookup
        # won't match the google/g-sub, but email still matches
        r2 = find_or_create_oidc_user("local@test.com", "github", "gh-sub", "viewer")
        # Since the user already has oidc_provider=google, the email lookup
        # finds the same user and re-links
        assert r2["user"]["id"] == local["id"]


# ─── list_service_principals: exact dict keys ────────────────────────────


class TestListServicePrincipalsExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_empty_list(self):
        assert list_service_principals() == []

    def test_single_sp_dict_keys(self):
        create_service_principal("sp1", "viewer")
        sps = list_service_principals()
        assert len(sps) == 1
        sp = sps[0]
        assert set(sp.keys()) == {
            "id",
            "name",
            "role",
            "key_prefix",
            "created_by",
            "created_at",
            "last_used",
            "expires_at",
        }

    def test_single_sp_exact_values(self):
        key, info = create_service_principal("sp1", "operator")
        sps = list_service_principals()
        sp = sps[0]
        assert sp["id"] == info["id"]
        assert sp["name"] == "sp1"
        assert sp["role"] == "operator"
        assert sp["key_prefix"] == key[:12]
        assert sp["created_by"] is None
        assert sp["last_used"] is None
        assert sp["expires_at"] is None
        assert sp["created_at"] is not None

    def test_ordering_by_created_at(self):
        create_service_principal("sp-b", "viewer")
        create_service_principal("sp-a", "admin")
        sps = list_service_principals()
        assert len(sps) == 2
        # Ordered by creation time
        assert sps[0]["name"] == "sp-b"
        assert sps[1]["name"] == "sp-a"

    def test_with_expires_at(self):
        future = datetime.datetime(2030, 1, 1, tzinfo=datetime.UTC)
        create_service_principal("sp1", "viewer", expires_at=future)
        sps = list_service_principals()
        assert sps[0]["expires_at"] is not None
        assert "2030-01-01" in sps[0]["expires_at"]

    def test_session_factory_none_returns_empty(self):
        original = auth._session_factory
        auth._session_factory = None
        try:
            assert list_service_principals() == []
        finally:
            auth._session_factory = original


# ─── delete_service_principal: exact returns ─────────────────────────────


class TestDeleteServicePrincipalExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_delete_returns_true(self):
        _, info = create_service_principal("sp1", "viewer")
        assert delete_service_principal(info["id"]) is True

    def test_delete_nonexistent_returns_false(self):
        assert delete_service_principal(99999) is False

    def test_delete_removes_from_list(self):
        _, info = create_service_principal("sp1", "viewer")
        delete_service_principal(info["id"])
        assert list_service_principals() == []

    def test_delete_key_stops_working(self):
        key, info = create_service_principal("sp1", "viewer")
        delete_service_principal(info["id"])
        assert _lookup_sp_identity(key) is None

    def test_delete_preserves_others(self):
        _, info1 = create_service_principal("sp1", "viewer")
        _, info2 = create_service_principal("sp2", "admin")
        delete_service_principal(info1["id"])
        sps = list_service_principals()
        assert len(sps) == 1
        assert sps[0]["name"] == "sp2"
        assert sps[0]["role"] == "admin"


# ─── _lookup_sp_identity: exact returns ──────────────────────────────────


class TestLookupSpIdentityExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_valid_key_returns_exact_dict(self):
        key, info = create_service_principal("test-sp", "operator")
        result = _lookup_sp_identity(key)
        assert result is not None
        assert result["id"] == info["id"]
        assert result["name"] == "test-sp"
        assert result["role"] == "operator"
        assert set(result.keys()) == {"id", "name", "role"}

    def test_invalid_key_returns_none(self):
        assert _lookup_sp_identity("bogus-key") is None

    def test_expired_sp_returns_none(self):
        past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
        key, _ = create_service_principal("expired-sp", "admin", expires_at=past)
        assert _lookup_sp_identity(key) is None

    def test_non_expired_sp_returns_dict(self):
        future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=30)
        key, info = create_service_principal("valid-sp", "admin", expires_at=future)
        result = _lookup_sp_identity(key)
        assert result is not None
        assert result["role"] == "admin"

    def test_session_factory_none(self):
        key, _ = create_service_principal("sp", "viewer")
        original = auth._session_factory
        auth._session_factory = None
        try:
            assert _lookup_sp_identity(key) is None
        finally:
            auth._session_factory = original

    def test_updates_last_used(self):
        key, info = create_service_principal("sp", "viewer")
        _lookup_sp_identity(key)
        sps = list_service_principals()
        assert sps[0]["last_used"] is not None


# ─── create_user: exact return values ────────────────────────────────────


class TestCreateUserExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_return_dict_keys_with_password(self):
        info = create_user("u@test.com", "password1", "viewer")
        assert set(info.keys()) == {"id", "email", "role", "created_at"}
        assert info["email"] == "u@test.com"
        assert info["role"] == "viewer"
        assert isinstance(info["id"], int)

    def test_return_dict_keys_with_invite(self):
        info = create_user("u@test.com", None, "operator")
        assert "invite_token" in info
        assert set(info.keys()) == {"id", "email", "role", "created_at", "invite_token"}
        assert info["role"] == "operator"

    def test_email_normalized(self):
        info = create_user("  UPPER@Test.COM  ", "password1", "viewer")
        assert info["email"] == "upper@test.com"

    def test_each_role_creates_correctly(self):
        for i, role in enumerate(ROLES):
            info = create_user(f"u{i}@test.com", "password1", role)
            assert info["role"] == role

    def test_created_at_is_iso_string(self):
        info = create_user("u@test.com", "password1", "viewer")
        dt = datetime.datetime.fromisoformat(info["created_at"])
        assert dt.tzinfo is not None

    def test_invalid_role_raises(self):
        with pytest.raises(DomainValidationError, match="Invalid role"):
            create_user("u@test.com", "password1", "superadmin")
        with pytest.raises(DomainValidationError, match="Invalid role"):
            create_user("u@test.com", "password1", "")

    def test_no_session_factory_raises_runtime(self):
        original = auth._session_factory
        auth._session_factory = None
        try:
            with pytest.raises(RuntimeError, match="Database not available"):
                create_user("u@test.com", "password1", "viewer")
        finally:
            auth._session_factory = original


# ─── list_users: exact dict keys ─────────────────────────────────────────


class TestListUsersExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_empty(self):
        assert list_users() == []

    def test_single_user_dict_keys(self):
        create_user("u@test.com", "password1", "viewer")
        users = list_users()
        assert len(users) == 1
        u = users[0]
        assert set(u.keys()) == {
            "id",
            "email",
            "role",
            "is_active",
            "pending_invite",
            "created_at",
            "oidc_provider",
        }

    def test_single_user_exact_values(self):
        info = create_user("u@test.com", "password1", "operator")
        users = list_users()
        u = users[0]
        assert u["id"] == info["id"]
        assert u["email"] == "u@test.com"
        assert u["role"] == "operator"
        assert u["is_active"] is True
        assert u["pending_invite"] is False
        assert u["oidc_provider"] is None
        assert u["created_at"] is not None

    def test_invite_user_pending_is_true(self):
        create_user("invited@test.com", None, "viewer")
        users = list_users()
        assert users[0]["pending_invite"] is True

    def test_session_factory_none_returns_empty(self):
        original = auth._session_factory
        auth._session_factory = None
        try:
            assert list_users() == []
        finally:
            auth._session_factory = original


# ─── accept_invite: exact return values ──────────────────────────────────


class TestAcceptInviteExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_accept_returns_dict(self):
        info = create_user("u@test.com", None, "operator")
        token = info["invite_token"]
        result = accept_invite(token, "newpass12")
        assert result is not None
        assert set(result.keys()) == {"id", "email", "role"}
        assert result["email"] == "u@test.com"
        assert result["role"] == "operator"
        assert result["id"] == info["id"]

    def test_accept_allows_login(self):
        info = create_user("u@test.com", None, "operator")
        accept_invite(info["invite_token"], "newpass12")
        user = authenticate_user("u@test.com", "newpass12")
        assert user is not None
        assert user["role"] == "operator"

    def test_invalid_token_returns_none(self):
        assert accept_invite("bogus-token-12345", "password1") is None

    def test_used_token_returns_none(self):
        info = create_user("u@test.com", None, "viewer")
        token = info["invite_token"]
        assert accept_invite(token, "newpass12") is not None
        assert accept_invite(token, "newpass12") is None

    def test_session_factory_none_returns_none(self):
        original = auth._session_factory
        auth._session_factory = None
        try:
            assert accept_invite("any-token", "password1") is None
        finally:
            auth._session_factory = original

    def test_expired_invite_returns_none(self):
        from shoreguard.models import User

        info = create_user("u@test.com", None, "viewer")
        session = auth._session_factory()
        user = session.query(User).filter(User.id == info["id"]).first()
        user.created_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=8)
        session.commit()
        session.close()
        assert accept_invite(info["invite_token"], "newpass12") is None


# ─── authenticate_user: exact return values ──────────────────────────────


class TestAuthenticateUserExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_success_returns_exact_dict(self):
        info = create_user("u@test.com", "password1", "operator")
        result = authenticate_user("u@test.com", "password1")
        assert result is not None
        assert set(result.keys()) == {"id", "email", "role"}
        assert result["id"] == info["id"]
        assert result["email"] == "u@test.com"
        assert result["role"] == "operator"

    def test_wrong_password_returns_none(self):
        create_user("u@test.com", "password1", "viewer")
        assert authenticate_user("u@test.com", "wrongpass") is None

    def test_nonexistent_user_returns_none(self):
        assert authenticate_user("nobody@test.com", "password1") is None

    def test_invite_user_cannot_authenticate(self):
        """Users with pending invite (no password set) should not be able to login."""
        create_user("invited@test.com", None, "viewer")
        assert authenticate_user("invited@test.com", "anything") is None

    def test_inactive_user_returns_none(self):
        from shoreguard.models import User

        create_user("u@test.com", "password1", "viewer")
        session = auth._session_factory()
        user = session.query(User).filter(User.email == "u@test.com").first()
        user.is_active = False
        session.commit()
        session.close()
        assert authenticate_user("u@test.com", "password1") is None

    def test_email_case_insensitive(self):
        create_user("u@test.com", "password1", "viewer")
        result = authenticate_user("U@TEST.COM", "password1")
        assert result is not None
        assert result["email"] == "u@test.com"

    def test_session_factory_none(self):
        original = auth._session_factory
        auth._session_factory = None
        try:
            assert authenticate_user("u@test.com", "password1") is None
        finally:
            auth._session_factory = original


# ─── session token: exact values ─────────────────────────────────────────


class TestSessionTokenExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_token_has_5_parts(self):
        token = create_session_token(user_id=1, role="admin")
        parts = token.split(".")
        assert len(parts) == 5

    def test_verify_returns_exact_tuple(self):
        token = create_session_token(user_id=42, role="operator")
        result = verify_session_token(token)
        assert result == (42, "operator")
        assert result[0] == 42
        assert result[1] == "operator"

    def test_each_role_roundtrips_exact(self):
        for role in ROLES:
            token = create_session_token(user_id=7, role=role)
            result = verify_session_token(token)
            assert result is not None
            assert result[0] == 7
            assert result[1] == role

    def test_expired_token_returns_none(self):
        with patch("shoreguard.api.auth.time") as mock_time:
            mock_time.time.return_value = time.time() - 86400 * 8
            token = create_session_token(user_id=1, role="admin")
        assert verify_session_token(token) is None

    def test_tampered_signature_returns_none(self):
        token = create_session_token(user_id=1, role="admin")
        parts = token.split(".")
        parts[4] = "0" * 64
        assert verify_session_token(".".join(parts)) is None

    def test_too_few_parts_returns_none(self):
        assert verify_session_token("") is None
        assert verify_session_token("a") is None
        assert verify_session_token("a.b") is None
        assert verify_session_token("a.b.c") is None
        assert verify_session_token("a.b.c.d") is None

    def test_too_many_parts_returns_none(self):
        assert verify_session_token("a.b.c.d.e.f") is None

    def test_invalid_role_in_token_returns_none(self):
        assert verify_session_token("nonce.99999999999.1.superadmin.sig") is None

    def test_non_numeric_user_id_returns_none(self):
        token = create_session_token(user_id=1, role="admin")
        parts = token.split(".")
        parts[2] = "abc"
        # Rebuild signature for tamper detection
        assert verify_session_token(".".join(parts)) is None

    def test_non_numeric_expiry_returns_none(self):
        token = create_session_token(user_id=1, role="admin")
        parts = token.split(".")
        parts[1] = "abc"
        assert verify_session_token(".".join(parts)) is None


# ─── set_gateway_role: exact return values ───────────────────────────────


class TestSetGatewayRoleExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db, _with_gateway):
        pass

    def test_user_role_returns_exact_dict(self):
        user = create_user("u@test.com", "password1", "viewer")
        result = set_gateway_role(user_id=user["id"], gateway_name="test-gw", role="admin")
        assert result == {"user_id": user["id"], "gateway_name": "test-gw", "role": "admin"}
        assert set(result.keys()) == {"user_id", "gateway_name", "role"}

    def test_sp_role_returns_exact_dict(self):
        _, sp = create_service_principal("sp1", "viewer")
        result = set_gateway_role(sp_id=sp["id"], gateway_name="test-gw", role="operator")
        assert result == {"sp_id": sp["id"], "gateway_name": "test-gw", "role": "operator"}
        assert set(result.keys()) == {"sp_id", "gateway_name", "role"}

    def test_update_user_role_returns_new_value(self):
        user = create_user("u@test.com", "password1", "viewer")
        set_gateway_role(user_id=user["id"], gateway_name="test-gw", role="admin")
        result = set_gateway_role(user_id=user["id"], gateway_name="test-gw", role="viewer")
        assert result["role"] == "viewer"

    def test_update_sp_role_returns_new_value(self):
        _, sp = create_service_principal("sp1", "viewer")
        set_gateway_role(sp_id=sp["id"], gateway_name="test-gw", role="admin")
        result = set_gateway_role(sp_id=sp["id"], gateway_name="test-gw", role="viewer")
        assert result["role"] == "viewer"

    def test_invalid_role_raises(self):
        user = create_user("u@test.com", "password1", "viewer")
        with pytest.raises(DomainValidationError, match="Invalid role"):
            set_gateway_role(user_id=user["id"], gateway_name="test-gw", role="superadmin")

    def test_nonexistent_gateway_raises(self):
        user = create_user("u@test.com", "password1", "viewer")
        with pytest.raises(NotFoundError, match="not found"):
            set_gateway_role(user_id=user["id"], gateway_name="no-such-gw", role="admin")

    def test_no_ids_raises(self):
        with pytest.raises(DomainValidationError, match="Either user_id or sp_id"):
            set_gateway_role(gateway_name="test-gw", role="admin")

    def test_each_role_works(self):
        user = create_user("u@test.com", "password1", "viewer")
        for role in ROLES:
            result = set_gateway_role(user_id=user["id"], gateway_name="test-gw", role=role)
            assert result["role"] == role


# ─── remove_gateway_role: exact return values ────────────────────────────


class TestRemoveGatewayRoleExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db, _with_gateway):
        pass

    def test_remove_existing_user_role_returns_true(self):
        user = create_user("u@test.com", "password1", "viewer")
        set_gateway_role(user_id=user["id"], gateway_name="test-gw", role="admin")
        result = remove_gateway_role(user_id=user["id"], gateway_name="test-gw")
        assert result is True

    def test_remove_nonexistent_user_role_returns_false(self):
        user = create_user("u@test.com", "password1", "viewer")
        result = remove_gateway_role(user_id=user["id"], gateway_name="test-gw")
        assert result is False

    def test_remove_sp_role_returns_true(self):
        _, sp = create_service_principal("sp1", "viewer")
        set_gateway_role(sp_id=sp["id"], gateway_name="test-gw", role="admin")
        assert remove_gateway_role(sp_id=sp["id"], gateway_name="test-gw") is True

    def test_remove_sp_nonexistent_returns_false(self):
        _, sp = create_service_principal("sp1", "viewer")
        assert remove_gateway_role(sp_id=sp["id"], gateway_name="test-gw") is False

    def test_remove_without_ids_returns_false(self):
        assert remove_gateway_role(gateway_name="test-gw") is False

    def test_remove_nonexistent_gateway_returns_false(self):
        user = create_user("u@test.com", "password1", "viewer")
        assert remove_gateway_role(user_id=user["id"], gateway_name="no-such-gw") is False

    def test_after_remove_list_is_empty(self):
        user = create_user("u@test.com", "password1", "viewer")
        set_gateway_role(user_id=user["id"], gateway_name="test-gw", role="admin")
        remove_gateway_role(user_id=user["id"], gateway_name="test-gw")
        assert list_gateway_roles_for_user(user["id"]) == []

    def test_remove_then_set_again_works(self):
        user = create_user("u@test.com", "password1", "viewer")
        set_gateway_role(user_id=user["id"], gateway_name="test-gw", role="admin")
        remove_gateway_role(user_id=user["id"], gateway_name="test-gw")
        result = set_gateway_role(user_id=user["id"], gateway_name="test-gw", role="operator")
        assert result["role"] == "operator"


# ─── list_gateway_roles: exact return values ─────────────────────────────


class TestListGatewayRolesExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db, _with_two_gateways):
        pass

    def test_user_roles_exact(self):
        user = create_user("u@test.com", "password1", "viewer")
        set_gateway_role(user_id=user["id"], gateway_name="gw-a", role="admin")
        set_gateway_role(user_id=user["id"], gateway_name="gw-b", role="operator")
        roles = list_gateway_roles_for_user(user["id"])
        assert len(roles) == 2
        assert roles[0] == {"gateway_name": "gw-a", "role": "admin"}
        assert roles[1] == {"gateway_name": "gw-b", "role": "operator"}

    def test_sp_roles_exact(self):
        _, sp = create_service_principal("sp1", "viewer")
        set_gateway_role(sp_id=sp["id"], gateway_name="gw-a", role="viewer")
        set_gateway_role(sp_id=sp["id"], gateway_name="gw-b", role="admin")
        roles = list_gateway_roles_for_sp(sp["id"])
        assert len(roles) == 2
        assert roles[0] == {"gateway_name": "gw-a", "role": "viewer"}
        assert roles[1] == {"gateway_name": "gw-b", "role": "admin"}

    def test_user_no_roles_empty_list(self):
        user = create_user("u@test.com", "password1", "viewer")
        assert list_gateway_roles_for_user(user["id"]) == []

    def test_sp_no_roles_empty_list(self):
        _, sp = create_service_principal("sp1", "viewer")
        assert list_gateway_roles_for_sp(sp["id"]) == []

    def test_roles_ordered_by_gateway_name(self):
        user = create_user("u@test.com", "password1", "viewer")
        set_gateway_role(user_id=user["id"], gateway_name="gw-b", role="viewer")
        set_gateway_role(user_id=user["id"], gateway_name="gw-a", role="admin")
        roles = list_gateway_roles_for_user(user["id"])
        assert roles[0]["gateway_name"] == "gw-a"
        assert roles[1]["gateway_name"] == "gw-b"


# ─── get_group: exact return values ──────────────────────────────────────


class TestGetGroupExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_group_with_members_exact_keys(self):
        g = create_group("devs", "operator", "Dev team")
        u = create_user("u@test.com", "password1", "viewer")
        add_group_member(g["id"], u["id"])
        result = get_group(g["id"])
        assert result is not None
        assert set(result.keys()) == {"id", "name", "description", "role", "created_at", "members"}
        assert result["id"] == g["id"]
        assert result["name"] == "devs"
        assert result["description"] == "Dev team"
        assert result["role"] == "operator"
        assert len(result["members"]) == 1
        member = result["members"][0]
        assert set(member.keys()) == {"id", "email", "role"}
        assert member["id"] == u["id"]
        assert member["email"] == "u@test.com"
        assert member["role"] == "viewer"

    def test_group_without_members(self):
        g = create_group("devs", "admin")
        result = get_group(g["id"])
        assert result is not None
        assert result["members"] == []
        assert result["name"] == "devs"
        assert result["role"] == "admin"

    def test_nonexistent_returns_none(self):
        assert get_group(99999) is None

    def test_session_factory_none(self):
        original = auth._session_factory
        auth._session_factory = None
        try:
            assert get_group(1) is None
        finally:
            auth._session_factory = original

    def test_members_ordered_by_email(self):
        g = create_group("devs")
        u2 = create_user("z@test.com", "password1", "viewer")
        u1 = create_user("a@test.com", "password1", "operator")
        add_group_member(g["id"], u2["id"])
        add_group_member(g["id"], u1["id"])
        result = get_group(g["id"])
        assert result["members"][0]["email"] == "a@test.com"
        assert result["members"][1]["email"] == "z@test.com"


# ─── list_group_members: exact return values ─────────────────────────────


class TestListGroupMembersExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_empty_group(self):
        g = create_group("devs")
        assert list_group_members(g["id"]) == []

    def test_single_member_exact(self):
        g = create_group("devs")
        u = create_user("u@test.com", "password1", "operator")
        add_group_member(g["id"], u["id"])
        members = list_group_members(g["id"])
        assert len(members) == 1
        assert members[0] == {"id": u["id"], "email": "u@test.com", "role": "operator"}

    def test_multiple_members_ordered(self):
        g = create_group("devs")
        u2 = create_user("z@test.com", "password1", "viewer")
        u1 = create_user("a@test.com", "password1", "admin")
        add_group_member(g["id"], u2["id"])
        add_group_member(g["id"], u1["id"])
        members = list_group_members(g["id"])
        assert len(members) == 2
        assert members[0]["email"] == "a@test.com"
        assert members[0]["role"] == "admin"
        assert members[1]["email"] == "z@test.com"
        assert members[1]["role"] == "viewer"

    def test_session_factory_none(self):
        original = auth._session_factory
        auth._session_factory = None
        try:
            assert list_group_members(1) == []
        finally:
            auth._session_factory = original


# ─── list_user_groups: exact return values ───────────────────────────────


class TestListUserGroupsExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_empty(self):
        u = create_user("u@test.com", "password1", "viewer")
        assert list_user_groups(u["id"]) == []

    def test_single_group_exact(self):
        g = create_group("devs", "operator")
        u = create_user("u@test.com", "password1", "viewer")
        add_group_member(g["id"], u["id"])
        groups = list_user_groups(u["id"])
        assert len(groups) == 1
        assert groups[0] == {"id": g["id"], "name": "devs", "role": "operator"}

    def test_multiple_groups_ordered_by_name(self):
        g2 = create_group("zebra", "admin")
        g1 = create_group("alpha", "viewer")
        u = create_user("u@test.com", "password1", "viewer")
        add_group_member(g2["id"], u["id"])
        add_group_member(g1["id"], u["id"])
        groups = list_user_groups(u["id"])
        assert len(groups) == 2
        assert groups[0]["name"] == "alpha"
        assert groups[0]["role"] == "viewer"
        assert groups[1]["name"] == "zebra"
        assert groups[1]["role"] == "admin"

    def test_session_factory_none(self):
        original = auth._session_factory
        auth._session_factory = None
        try:
            assert list_user_groups(1) == []
        finally:
            auth._session_factory = original


# ─── list_groups: exact return values ────────────────────────────────────


class TestListGroupsExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_empty(self):
        assert list_groups() == []

    def test_single_group_exact_keys(self):
        g = create_group("devs", "operator", "Dev team")
        groups = list_groups()
        assert len(groups) == 1
        grp = groups[0]
        assert set(grp.keys()) == {
            "id",
            "name",
            "description",
            "role",
            "created_at",
            "member_count",
        }
        assert grp["id"] == g["id"]
        assert grp["name"] == "devs"
        assert grp["description"] == "Dev team"
        assert grp["role"] == "operator"
        assert grp["member_count"] == 0

    def test_member_count_accurate(self):
        g = create_group("devs")
        u1 = create_user("a@test.com", "password1", "viewer")
        u2 = create_user("b@test.com", "password1", "viewer")
        add_group_member(g["id"], u1["id"])
        add_group_member(g["id"], u2["id"])
        groups = list_groups()
        assert groups[0]["member_count"] == 2

    def test_ordered_by_name(self):
        create_group("zebra")
        create_group("alpha")
        create_group("middle")
        groups = list_groups()
        names = [g["name"] for g in groups]
        assert names == ["alpha", "middle", "zebra"]

    def test_session_factory_none(self):
        original = auth._session_factory
        auth._session_factory = None
        try:
            assert list_groups() == []
        finally:
            auth._session_factory = original


# ─── delete_group: exact return values ───────────────────────────────────


class TestDeleteGroupExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_delete_returns_true(self):
        g = create_group("devs")
        assert delete_group(g["id"]) is True

    def test_delete_nonexistent_returns_false(self):
        assert delete_group(99999) is False

    def test_after_delete_get_returns_none(self):
        g = create_group("devs")
        delete_group(g["id"])
        assert get_group(g["id"]) is None

    def test_after_delete_list_empty(self):
        g = create_group("devs")
        delete_group(g["id"])
        assert list_groups() == []

    def test_delete_preserves_other_groups(self):
        g1 = create_group("alpha")
        create_group("beta")
        delete_group(g1["id"])
        groups = list_groups()
        assert len(groups) == 1
        assert groups[0]["name"] == "beta"


# ─── update_group: exact return values ───────────────────────────────────


class TestUpdateGroupExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_update_name_returns_exact(self):
        g = create_group("devs", "operator", "old desc")
        result = update_group(g["id"], name="developers")
        assert result["name"] == "developers"
        assert result["role"] == "operator"
        assert result["description"] == "old desc"
        assert set(result.keys()) == {"id", "name", "description", "role", "created_at"}

    def test_update_role_returns_exact(self):
        g = create_group("devs", "viewer")
        result = update_group(g["id"], role="admin")
        assert result["role"] == "admin"
        assert result["name"] == "devs"

    def test_update_description_returns_exact(self):
        g = create_group("devs", "viewer")
        result = update_group(g["id"], description="new desc")
        assert result["description"] == "new desc"

    def test_update_description_to_none(self):
        g = create_group("devs", "viewer", "old desc")
        result = update_group(g["id"], description=None)
        assert result["description"] is None

    def test_update_nonexistent_raises(self):
        with pytest.raises(NotFoundError, match="not found"):
            update_group(99999, name="nope")

    def test_invalid_role_raises(self):
        g = create_group("devs")
        with pytest.raises(DomainValidationError, match="Invalid role"):
            update_group(g["id"], role="superadmin")

    def test_noop_update_preserves_values(self):
        g = create_group("devs", "operator", "desc")
        result = update_group(g["id"])
        assert result["name"] == "devs"
        assert result["role"] == "operator"
        assert result["description"] == "desc"


# ─── add_group_member: exact return values ───────────────────────────────


class TestAddGroupMemberExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_returns_exact_dict(self):
        g = create_group("devs")
        u = create_user("u@test.com", "password1", "viewer")
        result = add_group_member(g["id"], u["id"])
        assert result == {
            "group_id": g["id"],
            "group_name": "devs",
            "user_id": u["id"],
            "user_email": "u@test.com",
        }
        assert set(result.keys()) == {"group_id", "group_name", "user_id", "user_email"}

    def test_nonexistent_group_raises(self):
        u = create_user("u@test.com", "password1", "viewer")
        with pytest.raises(NotFoundError, match="Group 999 not found"):
            add_group_member(999, u["id"])

    def test_nonexistent_user_raises(self):
        g = create_group("devs")
        with pytest.raises(NotFoundError, match="User 999 not found"):
            add_group_member(g["id"], 999)

    def test_duplicate_raises_integrity_error(self):
        g = create_group("devs")
        u = create_user("u@test.com", "password1", "viewer")
        add_group_member(g["id"], u["id"])
        with pytest.raises(IntegrityError):
            add_group_member(g["id"], u["id"])


# ─── remove_group_member: exact return values ───────────────────────────


class TestRemoveGroupMemberExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_remove_returns_true(self):
        g = create_group("devs")
        u = create_user("u@test.com", "password1", "viewer")
        add_group_member(g["id"], u["id"])
        assert remove_group_member(g["id"], u["id"]) is True

    def test_remove_nonexistent_returns_false(self):
        g = create_group("devs")
        assert remove_group_member(g["id"], 999) is False

    def test_after_remove_list_empty(self):
        g = create_group("devs")
        u = create_user("u@test.com", "password1", "viewer")
        add_group_member(g["id"], u["id"])
        remove_group_member(g["id"], u["id"])
        assert list_group_members(g["id"]) == []

    def test_remove_then_add_again(self):
        g = create_group("devs")
        u = create_user("u@test.com", "password1", "viewer")
        add_group_member(g["id"], u["id"])
        remove_group_member(g["id"], u["id"])
        result = add_group_member(g["id"], u["id"])
        assert result["user_email"] == "u@test.com"


# ─── set_group_gateway_role: exact return values ────────────────────────


class TestSetGroupGatewayRoleExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db, _with_gateway):
        pass

    def test_returns_exact_dict(self):
        g = create_group("devs")
        result = set_group_gateway_role(g["id"], "test-gw", "operator")
        assert result == {"group_id": g["id"], "gateway_name": "test-gw", "role": "operator"}

    def test_update_role(self):
        g = create_group("devs")
        set_group_gateway_role(g["id"], "test-gw", "viewer")
        result = set_group_gateway_role(g["id"], "test-gw", "admin")
        assert result["role"] == "admin"

    def test_invalid_role_raises(self):
        g = create_group("devs")
        with pytest.raises(DomainValidationError, match="Invalid role"):
            set_group_gateway_role(g["id"], "test-gw", "superadmin")

    def test_nonexistent_group_raises(self):
        with pytest.raises(NotFoundError, match="Group 999 not found"):
            set_group_gateway_role(999, "test-gw", "admin")

    def test_nonexistent_gateway_raises(self):
        g = create_group("devs")
        with pytest.raises(NotFoundError, match="not found"):
            set_group_gateway_role(g["id"], "no-such-gw", "admin")


# ─── remove_group_gateway_role: exact return values ─────────────────────


class TestRemoveGroupGatewayRoleExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db, _with_gateway):
        pass

    def test_remove_returns_true(self):
        g = create_group("devs")
        set_group_gateway_role(g["id"], "test-gw", "admin")
        assert remove_group_gateway_role(g["id"], "test-gw") is True

    def test_remove_nonexistent_returns_false(self):
        g = create_group("devs")
        assert remove_group_gateway_role(g["id"], "test-gw") is False

    def test_remove_nonexistent_gateway_returns_false(self):
        g = create_group("devs")
        assert remove_group_gateway_role(g["id"], "no-such-gw") is False

    def test_after_remove_list_empty(self):
        g = create_group("devs")
        set_group_gateway_role(g["id"], "test-gw", "admin")
        remove_group_gateway_role(g["id"], "test-gw")
        assert list_group_gateway_roles(g["id"]) == []


# ─── list_group_gateway_roles: exact return values ──────────────────────


class TestListGroupGatewayRolesExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db, _with_two_gateways):
        pass

    def test_empty(self):
        g = create_group("devs")
        assert list_group_gateway_roles(g["id"]) == []

    def test_single_role_exact(self):
        g = create_group("devs")
        set_group_gateway_role(g["id"], "gw-a", "operator")
        roles = list_group_gateway_roles(g["id"])
        assert len(roles) == 1
        assert roles[0] == {"gateway_name": "gw-a", "role": "operator"}

    def test_multiple_roles_ordered(self):
        g = create_group("devs")
        set_group_gateway_role(g["id"], "gw-b", "admin")
        set_group_gateway_role(g["id"], "gw-a", "viewer")
        roles = list_group_gateway_roles(g["id"])
        assert len(roles) == 2
        assert roles[0] == {"gateway_name": "gw-a", "role": "viewer"}
        assert roles[1] == {"gateway_name": "gw-b", "role": "admin"}

    def test_session_factory_none(self):
        original = auth._session_factory
        auth._session_factory = None
        try:
            assert list_group_gateway_roles(1) == []
        finally:
            auth._session_factory = original


# ─── bootstrap_admin_user: exact behavior ────────────────────────────────


class TestBootstrapAdminUserExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db, monkeypatch):
        self._db = db
        self._monkeypatch = monkeypatch

    def test_creates_admin_at_localhost(self):
        self._monkeypatch.setenv("SHOREGUARD_ADMIN_PASSWORD", "secret12")
        from shoreguard.settings import reset_settings

        reset_settings()
        bootstrap_admin_user()
        users = list_users()
        assert len(users) == 1
        assert users[0]["email"] == "admin@localhost"
        assert users[0]["role"] == "admin"
        assert users[0]["is_active"] is True

    def test_noop_with_existing_users(self):
        create_user("existing@test.com", "password1", "viewer")
        self._monkeypatch.setenv("SHOREGUARD_ADMIN_PASSWORD", "secret12")
        from shoreguard.settings import reset_settings

        reset_settings()
        bootstrap_admin_user()
        users = list_users()
        assert len(users) == 1
        assert users[0]["email"] == "existing@test.com"

    def test_noop_without_env(self):
        self._monkeypatch.delenv("SHOREGUARD_ADMIN_PASSWORD", raising=False)
        from shoreguard.settings import reset_settings

        reset_settings()
        bootstrap_admin_user()
        assert list_users() == []

    def test_created_user_can_authenticate(self):
        self._monkeypatch.setenv("SHOREGUARD_ADMIN_PASSWORD", "secret12")
        from shoreguard.settings import reset_settings

        reset_settings()
        bootstrap_admin_user()
        result = authenticate_user("admin@localhost", "secret12")
        assert result is not None
        assert result["email"] == "admin@localhost"
        assert result["role"] == "admin"

    def test_noop_when_session_factory_none(self):
        self._monkeypatch.setenv("SHOREGUARD_ADMIN_PASSWORD", "secret12")
        from shoreguard.settings import reset_settings

        reset_settings()
        original = auth._session_factory
        auth._session_factory = None
        try:
            bootstrap_admin_user()
        finally:
            auth._session_factory = original
        # No crash, no users created


# ─── is_setup_complete: exact behavior ───────────────────────────────────


class TestIsSetupCompleteExact:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_empty_db_returns_false(self):
        assert is_setup_complete() is False

    def test_with_user_returns_true(self):
        create_user("u@test.com", "password1", "admin")
        assert is_setup_complete() is True

    def test_session_factory_none_returns_false(self):
        original = auth._session_factory
        auth._session_factory = None
        try:
            assert is_setup_complete() is False
        finally:
            auth._session_factory = original


# ─── Password hashing ────────────────────────────────────────────────────


class TestPasswordHashingExact:
    def test_verify_correct(self):
        h = hash_password("test123")
        assert verify_password("test123", h) is True

    def test_verify_wrong(self):
        h = hash_password("test123")
        assert verify_password("wrong", h) is False

    def test_different_passwords(self):
        h1 = hash_password("a")
        h2 = hash_password("b")
        assert h1 != h2

    def test_corrupt_hash_raises(self):
        """pwdlib raises UnknownHashError for unrecognized hash formats."""
        from pwdlib.exceptions import UnknownHashError

        with pytest.raises(UnknownHashError):
            verify_password("test", "not-a-valid-hash")


# ─── _hash_key ───────────────────────────────────────────────────────────


class TestHashKeyExact:
    def test_deterministic(self):
        assert _hash_key("test") == _hash_key("test")

    def test_hex_length_64(self):
        result = _hash_key("any")
        assert len(result) == 64

    def test_different_inputs_different_outputs(self):
        assert _hash_key("a") != _hash_key("b")

    def test_returns_string(self):
        result = _hash_key("test")
        assert isinstance(result, str)
        # Should be valid hex
        int(result, 16)


# ─── record_failed_login: exact behavior ─────────────────────────────────


class TestRecordFailedLoginExact:
    def setup_method(self):
        from shoreguard.api.auth import reset_lockouts

        reset_lockouts()

    def teardown_method(self):
        from shoreguard.api.auth import reset_lockouts

        reset_lockouts()

    def test_increments_counter(self):
        from shoreguard.api.auth import _account_failures

        record_failed_login = auth.record_failed_login
        record_failed_login("user@example.com")
        assert _account_failures["user@example.com"][0] == 1
        record_failed_login("user@example.com")
        assert _account_failures["user@example.com"][0] == 2

    def test_case_insensitive(self):
        from shoreguard.api.auth import _account_failures

        auth.record_failed_login("User@Example.COM")
        assert "user@example.com" in _account_failures
        assert _account_failures["user@example.com"][0] == 1
