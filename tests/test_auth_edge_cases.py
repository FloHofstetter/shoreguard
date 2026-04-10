"""Edge-case coverage for ``shoreguard.api.auth``.

Covers the security-sensitive paths that must be tested before API freeze:

* Token expiry (session cookie AND service-principal expiry)
* Account lockout recovery (clear on success, timeout expiry)
* OIDC user creation / linking edge cases
* 4xx / 5xx branches in ``check_request_auth`` / ``require_role`` / ``require_auth_ws``
* Verification of corrupt hash handling and malformed tokens
"""

from __future__ import annotations

import datetime
import hmac
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.api import auth
from shoreguard.api.auth import (
    _account_failures,
    _GatewayRoleLookupError,
    _lookup_gateway_role,
    _lookup_group_global_role,
    _lookup_sp,
    _lookup_sp_identity,
    _lookup_user,
    accept_invite,
    authenticate_user,
    check_request_auth,
    clear_lockout,
    create_service_principal,
    create_session_token,
    create_user,
    find_or_create_oidc_user,
    is_account_locked,
    is_setup_complete,
    record_failed_login,
    require_auth,
    require_auth_ws,
    require_role,
    reset_lockouts,
    verify_password,
    verify_session_token,
)
from shoreguard.models import Base


@pytest.fixture
def db():
    """Real in-memory DB with a fresh auth module state (no_auth = False)."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    auth.init_auth_for_test(factory)
    yield factory
    auth.reset()
    engine.dispose()


def _make_request(
    *,
    headers: dict | None = None,
    cookies: dict | None = None,
    path: str = "/api/test",
    method: str = "GET",
    state: dict | None = None,
) -> MagicMock:
    """Build a minimal ``Request``-like mock for unit tests."""
    req = MagicMock()
    req.headers = headers or {}
    req.cookies = cookies or {}
    req.url = SimpleNamespace(path=path)
    req.method = method
    req.client = SimpleNamespace(host="1.2.3.4")
    req.state = SimpleNamespace(**(state or {}))
    return req


# ─── Password / hash edge cases ────────────────────────────────────────────


class TestVerifyPasswordCorruptHash:
    def test_non_string_password_triggers_type_error_branch(self):
        """``verify_password`` swallows ``TypeError`` from the hasher."""
        # pwdlib raises TypeError for non-str/bytes input → caught → False
        assert verify_password(None, "$2b$12$abcd") is False  # type: ignore[arg-type]

    def test_non_string_hash_triggers_type_error_branch(self):
        assert verify_password("pw", None) is False  # type: ignore[arg-type]

    def test_unrecognised_hash_format_returns_false(self):
        """Garbage hash strings raise ``UnknownHashError`` from pwdlib.

        Regression test: before the PwdlibError fix, an unrecognised hash
        propagated as an exception instead of returning ``False`` like the
        function comment promises.
        """
        assert verify_password("anything", "totally-not-a-real-hash") is False
        assert verify_password("x", "plaintext-not-a-hash") is False
        # Empty string is also "unrecognised" by pwdlib.
        assert verify_password("x", "") is False


# ─── Session token expiry / malformed ─────────────────────────────────────


class TestSessionTokenEdges:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_expired_token_rejected(self):
        """JWT-style expiry: a token generated with a past ``expiry`` is rejected."""
        with patch("shoreguard.api.auth.time") as mock_time:
            mock_time.time.return_value = time.time() - 86400 * 30
            token = create_session_token(user_id=1, role="admin")
        assert verify_session_token(token) is None

    def test_token_with_non_integer_user_id(self):
        """The ``int(user_id_str)`` conversion protects against malformed payloads."""
        nonce = "abc"
        expiry = str(int(time.time()) + 3600)
        payload = f"{nonce}.{expiry}.not-an-int.admin"
        import hashlib

        sig = hmac.new(auth._hmac_secret, payload.encode(), hashlib.sha256).hexdigest()
        token = f"{payload}.{sig}"
        assert verify_session_token(token) is None

    def test_token_with_non_integer_expiry(self):
        nonce = "abc"
        payload = f"{nonce}.not-a-time.1.admin"
        import hashlib

        sig = hmac.new(auth._hmac_secret, payload.encode(), hashlib.sha256).hexdigest()
        token = f"{payload}.{sig}"
        assert verify_session_token(token) is None

    def test_unknown_role_rejected(self):
        assert verify_session_token("n.1.1.ghost.sig") is None

    def test_not_five_parts_rejected(self):
        assert verify_session_token("only.two") is None
        assert verify_session_token("a.b.c.d.e.f") is None


# ─── Service-principal expiry ──────────────────────────────────────────────


class TestServicePrincipalExpiry:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_expired_sp_not_found(self):
        past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=30)
        key, _info = create_service_principal("old-sp", "operator", expires_at=past)
        assert _lookup_sp_identity(key) is None
        # Deprecated helper also returns None
        assert _lookup_sp(key) is None

    def test_non_expired_sp_found(self):
        future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)
        key, _info = create_service_principal("fresh-sp", "viewer", expires_at=future)
        result = _lookup_sp_identity(key)
        assert result is not None
        assert result["role"] == "viewer"
        # Deprecated helper returns role string
        assert _lookup_sp(key) == "viewer"

    def test_lookup_unknown_key_returns_none(self):
        assert _lookup_sp_identity("sg_not-a-real-key") is None

    def test_lookup_sp_session_factory_none_returns_none(self):
        auth._session_factory = None  # type: ignore[assignment]
        assert _lookup_sp_identity("anything") is None

    def test_lookup_sp_db_error_returns_none(self, db):
        """A SQLAlchemyError in the SP lookup returns ``None`` and is logged."""
        with patch.object(auth, "_session_factory") as mock_factory:
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=None)
            mock_session.query.side_effect = SQLAlchemyError("boom")
            mock_factory.return_value = mock_session
            # Re-point session factory to our mock
            auth._session_factory = lambda: mock_session  # type: ignore[assignment]
            assert _lookup_sp_identity("any-key") is None


# ─── Account lockout recovery ─────────────────────────────────────────────


class TestAccountLockoutRecovery:
    def setup_method(self):
        reset_lockouts()

    def teardown_method(self):
        reset_lockouts()

    def test_clear_lockout_removes_counter(self, monkeypatch):
        monkeypatch.setenv("SHOREGUARD_ACCOUNT_LOCKOUT_ATTEMPTS", "2")
        from shoreguard.settings import reset_settings

        reset_settings()
        record_failed_login("bob@example.com")
        record_failed_login("bob@example.com")
        assert is_account_locked("bob@example.com")[0]
        clear_lockout("bob@example.com")
        assert "bob@example.com" not in _account_failures
        assert is_account_locked("bob@example.com") == (False, 0)

    def test_lockout_auto_expires_clears_entry(self, monkeypatch):
        """After the lockout duration elapses, the entry is removed lazily."""
        monkeypatch.setenv("SHOREGUARD_ACCOUNT_LOCKOUT_ATTEMPTS", "1")
        monkeypatch.setenv("SHOREGUARD_ACCOUNT_LOCKOUT_DURATION", "5")
        from shoreguard.settings import reset_settings

        reset_settings()
        base = time.monotonic()
        with patch("shoreguard.api.auth.time.monotonic", return_value=base):
            record_failed_login("carol@example.com")
        with patch("shoreguard.api.auth.time.monotonic", return_value=base + 99):
            locked, _ = is_account_locked("carol@example.com")
            assert not locked
        assert "carol@example.com" not in _account_failures

    def test_failed_login_below_threshold_stays_unlocked(self, monkeypatch):
        monkeypatch.setenv("SHOREGUARD_ACCOUNT_LOCKOUT_ATTEMPTS", "5")
        from shoreguard.settings import reset_settings

        reset_settings()
        record_failed_login("dave@example.com")
        locked, retry_after = is_account_locked("dave@example.com")
        assert not locked
        assert retry_after == 0

    def test_record_failed_login_normalizes_email(self):
        record_failed_login("  MiXed@Example.COM ")
        assert "mixed@example.com" in _account_failures


# ─── OIDC user creation / linking ─────────────────────────────────────────


class TestFindOrCreateOIDCUser:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_create_new_oidc_user(self):
        res = find_or_create_oidc_user(
            email="new@ex.com", oidc_provider="google", oidc_sub="sub-1", role="operator"
        )
        assert res["action"] == "create"
        assert res["user"]["email"] == "new@ex.com"
        assert res["user"]["role"] == "operator"

    def test_invalid_role_falls_back_to_viewer(self):
        res = find_or_create_oidc_user(
            email="x@y.com", oidc_provider="g", oidc_sub="s", role="superadmin"
        )
        assert res["user"]["role"] == "viewer"
        assert res["action"] == "create"

    def test_returning_user_is_login_not_create(self):
        first = find_or_create_oidc_user(
            email="same@x.com", oidc_provider="g", oidc_sub="sub-2", role="viewer"
        )
        second = find_or_create_oidc_user(
            email="same@x.com", oidc_provider="g", oidc_sub="sub-2", role="viewer"
        )
        assert first["action"] == "create"
        assert second["action"] == "login"
        assert second["user"]["id"] == first["user"]["id"]

    def test_existing_local_user_gets_linked(self):
        create_user("local@x.com", "password123", "operator")
        res = find_or_create_oidc_user(
            email="local@x.com", oidc_provider="g", oidc_sub="sub-3", role="viewer"
        )
        assert res["action"] == "link"
        # Original operator role preserved, OIDC identity linked
        assert res["user"]["role"] == "operator"

    def test_session_factory_none_raises_runtime_error(self):
        auth._session_factory = None  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="Database not available"):
            find_or_create_oidc_user(
                email="a@b.com", oidc_provider="g", oidc_sub="s", role="viewer"
            )


# ─── Invite acceptance expiry ─────────────────────────────────────────────


class TestAcceptInviteExpiry:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_unknown_token_returns_none(self):
        assert accept_invite("definitely-not-a-real-token", "newpass") is None

    def test_expired_invite_returns_none(self, monkeypatch):
        info = create_user("invited@x.com", None, "viewer")
        token = info["invite_token"]

        # Shift the created_at timestamp far into the past by poking the row
        from shoreguard.models import User

        with auth._session_factory() as session:  # type: ignore[misc]
            user = session.query(User).filter(User.email == "invited@x.com").first()
            user.created_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=999)
            session.commit()

        assert accept_invite(token, "newpass12") is None

    def test_valid_invite_accepted(self):
        info = create_user("invited2@x.com", None, "operator")
        token = info["invite_token"]
        res = accept_invite(token, "newpass12")
        assert res is not None
        assert res["role"] == "operator"
        # Authenticate with the new password
        u = authenticate_user("invited2@x.com", "newpass12")
        assert u is not None


# ─── ``check_request_auth`` / ``require_auth`` branches ────────────────────


class TestCheckRequestAuthBranches:
    def test_no_auth_mode(self, db):
        auth._no_auth = True  # type: ignore[assignment]
        try:
            req = _make_request()
            assert check_request_auth(req) == "admin"
            assert req.state.user_id == "no-auth"
        finally:
            auth._no_auth = False  # type: ignore[assignment]

    def test_no_session_factory_raises_503(self):
        auth.reset()  # clears _session_factory
        req = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            check_request_auth(req)
        assert exc_info.value.status_code == 503

    def test_setup_not_complete_blocks_non_setup_paths(self, db):
        # Empty DB = setup incomplete
        req = _make_request(path="/api/sandboxes")
        assert check_request_auth(req) is None
        assert req.state.user_id == "setup-pending"

    def test_setup_not_complete_allows_setup_paths(self, db):
        req = _make_request(path="/api/auth/setup")
        assert check_request_auth(req) == "admin"

    def test_setup_not_complete_allows_static_paths(self, db):
        req = _make_request(path="/static/css/main.css")
        assert check_request_auth(req) == "admin"

    def test_expired_session_cookie_rejected(self, db):
        create_user("u@x.com", "pass123", "admin")
        with patch("shoreguard.api.auth.time") as mock_time:
            mock_time.time.return_value = time.time() - 86400 * 30
            token = create_session_token(user_id=1, role="admin")
        req = _make_request(cookies={"sg_session": token})
        assert check_request_auth(req) is None

    def test_session_cookie_for_deleted_user(self, db):
        """A valid signature but the user row has been deleted → denied."""
        create_user("ghost@x.com", "pass123", "admin")
        # Manufacture a token for a user_id that does not exist
        token = create_session_token(user_id=9999, role="admin")
        req = _make_request(cookies={"sg_session": token})
        assert check_request_auth(req) is None

    def test_bearer_header_wrong_scheme_ignored(self, db):
        create_user("a@b.com", "pw", "admin")
        req = _make_request(headers={"authorization": "Basic abcdef"})
        assert check_request_auth(req) is None

    def test_bearer_unknown_key_returns_none(self, db):
        create_user("a@b.com", "pw", "admin")
        req = _make_request(headers={"authorization": "Bearer sg_not-a-real-key"})
        assert check_request_auth(req) is None

    def test_bearer_valid_sp_returns_role(self, db):
        create_user("a@b.com", "pw", "admin")
        key, _ = create_service_principal("ci", "operator")
        req = _make_request(headers={"authorization": f"Bearer {key}"})
        assert check_request_auth(req) == "operator"
        assert req.state.user_id == "sp:ci"

    def test_valid_session_cookie_returns_role(self, db):
        info = create_user("real@x.com", "pw", "viewer")
        token = create_session_token(user_id=info["id"], role="viewer")
        req = _make_request(cookies={"sg_session": token})
        assert check_request_auth(req) == "viewer"
        assert req.state.user_id == "real@x.com"


class TestRequireAuth:
    def test_missing_credentials_raises_401(self, db):
        create_user("a@b.com", "pw", "admin")
        req = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            require_auth(req)
        assert exc_info.value.status_code == 401
        assert "WWW-Authenticate" in (exc_info.value.headers or {})

    def test_valid_session_passes(self, db):
        info = create_user("a@b.com", "pw", "admin")
        token = create_session_token(user_id=info["id"], role="admin")
        req = _make_request(cookies={"sg_session": token})
        require_auth(req)
        assert req.state.role == "admin"


class TestRequireRoleBranches:
    async def test_unauthenticated_raises_401(self, db):
        create_user("a@b.com", "pw", "admin")
        dep = require_role("viewer")
        req = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await dep(req)
        assert exc_info.value.status_code == 401

    async def test_insufficient_role_raises_403(self, db):
        info = create_user("v@x.com", "pw", "viewer")
        token = create_session_token(user_id=info["id"], role="viewer")
        dep = require_role("admin")
        req = _make_request(cookies={"sg_session": token})
        with pytest.raises(HTTPException) as exc_info:
            await dep(req)
        assert exc_info.value.status_code == 403

    async def test_sufficient_role_passes(self, db):
        info = create_user("admin2@x.com", "pw", "admin")
        token = create_session_token(user_id=info["id"], role="admin")
        dep = require_role("operator")
        req = _make_request(cookies={"sg_session": token})
        await dep(req)  # no exception
        assert req.state.role == "admin"

    async def test_gateway_role_lookup_failure_returns_503(self, db):
        info = create_user("op@x.com", "pw", "operator")
        token = create_session_token(user_id=info["id"], role="operator")
        dep = require_role("viewer")
        req = _make_request(cookies={"sg_session": token}, path="/api/gateways/foo/thing")
        req.state._gateway = "foo"
        with patch(
            "shoreguard.api.auth._lookup_gateway_role",
            side_effect=_GatewayRoleLookupError("boom"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await dep(req)
        assert exc_info.value.status_code == 503

    async def test_group_role_lookup_failure_returns_503(self, db):
        info = create_user("grp@x.com", "pw", "viewer")
        token = create_session_token(user_id=info["id"], role="viewer")
        dep = require_role("viewer")
        req = _make_request(cookies={"sg_session": token})
        with patch(
            "shoreguard.api.auth._lookup_group_global_role",
            side_effect=_GatewayRoleLookupError("boom"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await dep(req)
        assert exc_info.value.status_code == 503


# ─── WebSocket auth dependency ────────────────────────────────────────────


class TestRequireAuthWebSocket:
    def _ws(self, path: str = "/ws") -> MagicMock:
        ws = MagicMock()
        ws.url = SimpleNamespace(path=path)
        ws.client = SimpleNamespace(host="1.2.3.4")
        return ws

    def test_no_auth_mode_passes(self, db):
        auth._no_auth = True  # type: ignore[assignment]
        try:
            require_auth_ws(self._ws(), token=None, sg_session=None)
        finally:
            auth._no_auth = False  # type: ignore[assignment]

    def test_setup_incomplete_passes(self, db):
        # No users → setup incomplete → WS is permitted (UI bootstrap flow)
        require_auth_ws(self._ws(), token=None, sg_session=None)

    def test_no_credentials_raises_403(self, db):
        create_user("a@b.com", "pw", "admin")
        with pytest.raises(HTTPException) as exc_info:
            require_auth_ws(self._ws(), token=None, sg_session=None)
        assert exc_info.value.status_code == 403

    def test_invalid_sp_token_raises_403(self, db):
        create_user("a@b.com", "pw", "admin")
        with pytest.raises(HTTPException):
            require_auth_ws(self._ws(), token="sg_bogus", sg_session=None)

    def test_valid_sp_token_passes(self, db):
        create_user("a@b.com", "pw", "admin")
        key, _ = create_service_principal("ws-sp", "viewer")
        require_auth_ws(self._ws(), token=key, sg_session=None)  # no exception

    def test_valid_session_cookie_passes(self, db):
        info = create_user("ws@x.com", "pw", "admin")
        token = create_session_token(user_id=info["id"], role="admin")
        require_auth_ws(self._ws(), token=None, sg_session=token)

    def test_session_cookie_for_deleted_user_raises_403(self, db):
        create_user("ws@x.com", "pw", "admin")
        ghost = create_session_token(user_id=9999, role="admin")
        with pytest.raises(HTTPException) as exc_info:
            require_auth_ws(self._ws(), token=None, sg_session=ghost)
        assert exc_info.value.status_code == 403

    def test_invalid_session_cookie_raises_403(self, db):
        create_user("ws@x.com", "pw", "admin")
        with pytest.raises(HTTPException):
            require_auth_ws(self._ws(), token=None, sg_session="garbage.cookie.value")


# ─── Gateway / group role lookup edges ─────────────────────────────────────


class TestGatewayRoleLookupEdges:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_lookup_gateway_role_no_user_no_sp_returns_none(self):
        assert _lookup_gateway_role(gateway="any") is None

    def test_lookup_gateway_role_unknown_gateway_returns_none(self):
        info = create_user("u@x.com", "pw", "viewer")
        assert _lookup_gateway_role(user_id=info["id"], gateway="ghost-gw") is None

    def test_lookup_gateway_role_db_error_raises(self):
        with patch.object(auth, "_session_factory") as mock_factory:
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=None)
            mock_session.query.side_effect = SQLAlchemyError("boom")
            mock_factory.return_value = mock_session
            auth._session_factory = lambda: mock_session  # type: ignore[assignment]
            with pytest.raises(_GatewayRoleLookupError):
                _lookup_gateway_role(user_id=1, gateway="gw")

    def test_lookup_group_global_role_db_error_raises(self):
        with patch.object(auth, "_session_factory") as mock_factory:
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=None)
            mock_session.query.side_effect = SQLAlchemyError("boom")
            mock_factory.return_value = mock_session
            auth._session_factory = lambda: mock_session  # type: ignore[assignment]
            with pytest.raises(_GatewayRoleLookupError):
                _lookup_group_global_role(user_id=1)

    def test_lookup_group_global_role_no_groups_returns_none(self):
        info = create_user("lone@x.com", "pw", "viewer")
        assert _lookup_group_global_role(info["id"]) is None


# ─── Factory-None fall-throughs ───────────────────────────────────────────


class TestSessionFactoryNone:
    """Every helper returns a safe default when the factory is not set."""

    def setup_method(self):
        auth.reset()

    def test_authenticate_user(self):
        assert authenticate_user("a@b.com", "pw") is None

    def test_lookup_user(self):
        assert _lookup_user(1) is None

    def test_is_setup_complete(self):
        assert is_setup_complete() is False

    def test_lookup_gateway_role(self):
        assert _lookup_gateway_role(user_id=1, gateway="gw") is None

    def test_lookup_group_global_role(self):
        assert _lookup_group_global_role(1) is None


# ─── Authenticate edge cases ──────────────────────────────────────────────


class TestAuthenticateUserEdges:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_inactive_user_rejected(self):
        info = create_user("inactive@x.com", "pw", "viewer")
        from shoreguard.models import User

        with auth._session_factory() as session:  # type: ignore[misc]
            user = session.query(User).filter(User.id == info["id"]).first()
            user.is_active = False
            session.commit()
        assert authenticate_user("inactive@x.com", "pw") is None

    def test_pending_invite_user_rejected(self):
        """A user with an invite still pending cannot log in with a password."""
        create_user("pending@x.com", None, "viewer")  # password=None → invite
        assert authenticate_user("pending@x.com", "anything") is None


# ─── init_auth / _load_or_create_secret_key ───────────────────────────────


class TestInitAuth:
    def setup_method(self):
        auth.reset()

    def teardown_method(self):
        auth.reset()

    def test_init_auth_with_secret_key_from_settings(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SHOREGUARD_SECRET_KEY", "a-very-secret-key")
        monkeypatch.setenv("SHOREGUARD_CONFIG_DIR", str(tmp_path))
        from shoreguard.settings import reset_settings

        reset_settings()
        factory = MagicMock()
        auth.init_auth(factory)
        assert auth._session_factory is factory
        assert len(auth._hmac_secret) == 32  # sha256 digest

    def test_init_auth_generates_and_loads_secret_file(self, monkeypatch, tmp_path):
        """First call creates the key file; second call reads it back."""
        monkeypatch.delenv("SHOREGUARD_SECRET_KEY", raising=False)
        monkeypatch.setenv("SHOREGUARD_CONFIG_DIR", str(tmp_path))
        from shoreguard.settings import reset_settings

        reset_settings()
        factory = MagicMock()
        auth.init_auth(factory)
        first_secret = auth._hmac_secret
        assert len(first_secret) == 32

        # Second init should read the same file
        auth.reset()
        reset_settings()
        auth.init_auth(factory)
        assert auth._hmac_secret == first_secret


# ─── DB-error rollback paths (create_user / CRUD) ─────────────────────────


def _mock_factory_with_error(error: Exception) -> object:
    """Return a patched session factory whose ``query`` raises *error*."""

    def _factory():
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=None)
        session.query.side_effect = error
        session.add.side_effect = error
        session.commit.side_effect = error
        return session

    return _factory


class TestDBErrorPaths:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_is_setup_complete_db_error_returns_false(self):
        auth._session_factory = _mock_factory_with_error(SQLAlchemyError("boom"))  # type: ignore[assignment]
        assert is_setup_complete() is False

    def test_list_users_db_error_returns_empty(self):
        from shoreguard.api.auth import list_users

        auth._session_factory = _mock_factory_with_error(SQLAlchemyError("boom"))  # type: ignore[assignment]
        assert list_users() == []

    def test_list_service_principals_db_error_returns_empty(self):
        from shoreguard.api.auth import list_service_principals

        auth._session_factory = _mock_factory_with_error(SQLAlchemyError("boom"))  # type: ignore[assignment]
        assert list_service_principals() == []

    def test_list_gateway_roles_for_user_db_error(self):
        from shoreguard.api.auth import list_gateway_roles_for_user

        auth._session_factory = _mock_factory_with_error(SQLAlchemyError("boom"))  # type: ignore[assignment]
        assert list_gateway_roles_for_user(1) == []

    def test_list_gateway_roles_for_sp_db_error(self):
        from shoreguard.api.auth import list_gateway_roles_for_sp

        auth._session_factory = _mock_factory_with_error(SQLAlchemyError("boom"))  # type: ignore[assignment]
        assert list_gateway_roles_for_sp(1) == []

    def test_list_groups_db_error(self):
        from shoreguard.api.auth import list_groups

        auth._session_factory = _mock_factory_with_error(SQLAlchemyError("boom"))  # type: ignore[assignment]
        assert list_groups() == []

    def test_get_group_db_error(self):
        from shoreguard.api.auth import get_group

        auth._session_factory = _mock_factory_with_error(SQLAlchemyError("boom"))  # type: ignore[assignment]
        assert get_group(1) is None

    def test_list_group_members_db_error(self):
        from shoreguard.api.auth import list_group_members

        auth._session_factory = _mock_factory_with_error(SQLAlchemyError("boom"))  # type: ignore[assignment]
        assert list_group_members(1) == []

    def test_list_user_groups_db_error(self):
        from shoreguard.api.auth import list_user_groups

        auth._session_factory = _mock_factory_with_error(SQLAlchemyError("boom"))  # type: ignore[assignment]
        assert list_user_groups(1) == []

    def test_list_group_gateway_roles_db_error(self):
        from shoreguard.api.auth import list_group_gateway_roles

        auth._session_factory = _mock_factory_with_error(SQLAlchemyError("boom"))  # type: ignore[assignment]
        assert list_group_gateway_roles(1) == []


# ─── RuntimeError when session factory is None ───────────────────────────


class TestRuntimeErrorWhenFactoryNone:
    def setup_method(self):
        auth.reset()

    def teardown_method(self):
        auth.reset()

    def test_create_user_raises(self):
        with pytest.raises(RuntimeError, match="Database not available"):
            create_user("a@x.com", "pw", "viewer")

    def test_create_service_principal_raises(self):
        with pytest.raises(RuntimeError, match="Database not available"):
            create_service_principal("sp", "viewer")

    def test_delete_user_raises(self):
        from shoreguard.api.auth import delete_user

        with pytest.raises(RuntimeError, match="Database not available"):
            delete_user(1)

    def test_delete_service_principal_raises(self):
        from shoreguard.api.auth import delete_service_principal

        with pytest.raises(RuntimeError, match="Database not available"):
            delete_service_principal(1)

    def test_rotate_service_principal_raises(self):
        from shoreguard.api.auth import rotate_service_principal

        with pytest.raises(RuntimeError, match="Database not available"):
            rotate_service_principal(1)

    def test_set_gateway_role_raises(self):
        from shoreguard.api.auth import set_gateway_role

        with pytest.raises(RuntimeError, match="Database not available"):
            set_gateway_role(user_id=1, gateway_name="gw", role="viewer")

    def test_remove_gateway_role_raises(self):
        from shoreguard.api.auth import remove_gateway_role

        with pytest.raises(RuntimeError, match="Database not available"):
            remove_gateway_role(user_id=1, gateway_name="gw")

    def test_create_group_raises(self):
        from shoreguard.api.auth import create_group

        with pytest.raises(RuntimeError, match="Database not available"):
            create_group("g")

    def test_update_group_raises(self):
        from shoreguard.api.auth import update_group

        with pytest.raises(RuntimeError, match="Database not available"):
            update_group(1, name="x")

    def test_delete_group_raises(self):
        from shoreguard.api.auth import delete_group

        with pytest.raises(RuntimeError, match="Database not available"):
            delete_group(1)

    def test_add_group_member_raises(self):
        from shoreguard.api.auth import add_group_member

        with pytest.raises(RuntimeError, match="Database not available"):
            add_group_member(1, 2)

    def test_remove_group_member_raises(self):
        from shoreguard.api.auth import remove_group_member

        with pytest.raises(RuntimeError, match="Database not available"):
            remove_group_member(1, 2)

    def test_set_group_gateway_role_raises(self):
        from shoreguard.api.auth import set_group_gateway_role

        with pytest.raises(RuntimeError, match="Database not available"):
            set_group_gateway_role(1, "gw", "viewer")

    def test_remove_group_gateway_role_raises(self):
        from shoreguard.api.auth import remove_group_gateway_role

        with pytest.raises(RuntimeError, match="Database not available"):
            remove_group_gateway_role(1, "gw")


# ─── Exception-during-commit rollback branches ────────────────────────────


class TestCommitFailureRollback:
    """Force ``session.commit`` to raise so the generic ``except`` block runs."""

    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def _patch_commit_error(self):
        """Return a patcher that makes every session.commit raise ValueError."""
        from sqlalchemy.orm import Session

        original_commit = Session.commit

        def _boom(self):  # type: ignore[no-untyped-def]
            raise ValueError("synthetic commit failure")

        return patch.object(Session, "commit", _boom), original_commit

    def test_create_user_generic_exception_rollbacks(self):
        patcher, _ = self._patch_commit_error()
        with patcher:
            with pytest.raises(ValueError, match="synthetic"):
                create_user("boom@x.com", "pw", "viewer")

    def test_create_sp_generic_exception_rollbacks(self):
        patcher, _ = self._patch_commit_error()
        with patcher:
            with pytest.raises(ValueError):
                create_service_principal("boom-sp", "viewer")

    def test_delete_user_generic_exception_rollbacks(self):
        create_user("kill@x.com", "pw", "operator")
        from shoreguard.api.auth import delete_user

        patcher, _ = self._patch_commit_error()
        with patcher:
            with pytest.raises(ValueError):
                delete_user(1)

    def test_delete_sp_generic_exception_rollbacks(self):
        _key, info = create_service_principal("doomed", "viewer")
        from shoreguard.api.auth import delete_service_principal

        patcher, _ = self._patch_commit_error()
        with patcher:
            with pytest.raises(ValueError):
                delete_service_principal(info["id"])

    def test_rotate_sp_generic_exception_rollbacks(self):
        _key, info = create_service_principal("rot", "viewer")
        from shoreguard.api.auth import rotate_service_principal

        patcher, _ = self._patch_commit_error()
        with patcher:
            with pytest.raises(ValueError):
                rotate_service_principal(info["id"])

    def test_accept_invite_generic_exception_rollbacks(self):
        info = create_user("inv@x.com", None, "viewer")
        token = info["invite_token"]
        patcher, _ = self._patch_commit_error()
        with patcher:
            with pytest.raises(ValueError):
                accept_invite(token, "newpass12")


# ─── SP-not-found / group-not-found branches in CRUD ─────────────────────


class TestNotFoundBranches:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_rotate_nonexistent_sp_returns_none(self):
        from shoreguard.api.auth import rotate_service_principal

        assert rotate_service_principal(9999) is None

    def test_delete_nonexistent_sp_returns_false(self):
        from shoreguard.api.auth import delete_service_principal

        assert delete_service_principal(9999) is False

    def test_delete_nonexistent_user_returns_false(self):
        from shoreguard.api.auth import delete_user

        assert delete_user(9999) is False

    def test_delete_nonexistent_group_returns_false(self):
        from shoreguard.api.auth import delete_group

        assert delete_group(9999) is False

    def test_update_nonexistent_group_raises(self):
        from shoreguard.api.auth import update_group
        from shoreguard.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            update_group(9999, name="x")

    def test_get_nonexistent_group_returns_none(self):
        from shoreguard.api.auth import get_group

        assert get_group(9999) is None

    def test_remove_gateway_role_unknown_gateway_returns_false(self):
        from shoreguard.api.auth import remove_gateway_role

        assert remove_gateway_role(user_id=1, gateway_name="ghost") is False

    def test_remove_gateway_role_neither_user_nor_sp_returns_false(self):
        from shoreguard.api.auth import remove_gateway_role

        # Create gateway first so the early-return for unknown gateway does not fire
        from shoreguard.models import Gateway

        with auth._session_factory() as session:  # type: ignore[misc]
            session.add(
                Gateway(
                    name="realgw",
                    endpoint="localhost:50051",
                    scheme="http",
                    registered_at=datetime.datetime.now(datetime.UTC),
                )
            )
            session.commit()
        assert remove_gateway_role(gateway_name="realgw") is False

    def test_remove_group_gateway_role_unknown_gateway(self):
        from shoreguard.api.auth import remove_group_gateway_role

        assert remove_group_gateway_role(1, "ghost-gw") is False

    def test_set_gateway_role_unknown_gateway_raises(self):
        from shoreguard.api.auth import set_gateway_role
        from shoreguard.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            set_gateway_role(user_id=1, gateway_name="ghost", role="viewer")


# ─── Bootstrap admin ──────────────────────────────────────────────────────


class TestBootstrapAdmin:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_no_password_noop(self, monkeypatch):
        from shoreguard.api.auth import bootstrap_admin_user
        from shoreguard.settings import reset_settings

        monkeypatch.delenv("SHOREGUARD_ADMIN_PASSWORD", raising=False)
        reset_settings()
        bootstrap_admin_user()  # no-op, no error
        assert not is_setup_complete()

    def test_with_password_creates_admin(self, monkeypatch):
        from shoreguard.api.auth import bootstrap_admin_user
        from shoreguard.settings import reset_settings

        monkeypatch.setenv("SHOREGUARD_ADMIN_PASSWORD", "bootstrappass")
        reset_settings()
        bootstrap_admin_user()
        assert is_setup_complete()
        u = authenticate_user("admin@localhost", "bootstrappass")
        assert u is not None
        assert u["role"] == "admin"

    def test_setup_already_complete_noop(self, monkeypatch):
        from shoreguard.api.auth import bootstrap_admin_user
        from shoreguard.settings import reset_settings

        create_user("existing@x.com", "pw", "admin")
        monkeypatch.setenv("SHOREGUARD_ADMIN_PASSWORD", "otherpass")
        reset_settings()
        bootstrap_admin_user()  # should skip — setup already complete
        # admin@localhost was not created
        assert authenticate_user("admin@localhost", "otherpass") is None


# ─── Gateway-scoped role override in require_role ────────────────────────


class TestRequireRoleGatewayOverride:
    async def test_gateway_override_elevates_role(self, db):
        """A per-gateway role override applies over the global role."""
        from shoreguard.api.auth import set_gateway_role
        from shoreguard.models import Gateway

        info = create_user("u@x.com", "pw", "viewer")
        with auth._session_factory() as session:  # type: ignore[misc]
            session.add(
                Gateway(
                    name="prod",
                    endpoint="localhost:50051",
                    scheme="http",
                    registered_at=datetime.datetime.now(datetime.UTC),
                )
            )
            session.commit()
        set_gateway_role(user_id=info["id"], gateway_name="prod", role="admin")

        token = create_session_token(user_id=info["id"], role="viewer")
        dep = require_role("admin")
        req = _make_request(cookies={"sg_session": token})
        req.state._gateway = "prod"
        await dep(req)  # should pass — gateway override is admin
        assert req.state.role == "admin"

    async def test_group_global_role_elevates(self, db):
        """Group global role raises the effective role above the user's own."""
        from shoreguard.api.auth import add_group_member, create_group

        info = create_user("grpuser@x.com", "pw", "viewer")
        grp = create_group("admins", role="admin")
        add_group_member(grp["id"], info["id"])

        token = create_session_token(user_id=info["id"], role="viewer")
        dep = require_role("admin")
        req = _make_request(cookies={"sg_session": token})
        await dep(req)  # should pass — group elevates to admin
        assert req.state.role == "admin"

    async def test_sp_gateway_override(self, db):
        """Same path for service principals (sp_id branch)."""
        from shoreguard.api.auth import set_gateway_role
        from shoreguard.models import Gateway

        create_user("a@b.com", "pw", "admin")
        key, info = create_service_principal("ci-sp", "viewer")
        with auth._session_factory() as session:  # type: ignore[misc]
            session.add(
                Gateway(
                    name="stage",
                    endpoint="localhost:50051",
                    scheme="http",
                    registered_at=datetime.datetime.now(datetime.UTC),
                )
            )
            session.commit()
        set_gateway_role(sp_id=info["id"], gateway_name="stage", role="operator")

        dep = require_role("operator")
        req = _make_request(headers={"authorization": f"Bearer {key}"})
        req.state._gateway = "stage"
        await dep(req)
        assert req.state.role == "operator"


# ─── IntegrityError pass-through branches ────────────────────────────────


class TestIntegrityErrorPassthrough:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_create_user_duplicate_email_raises_integrity(self):
        from sqlalchemy.exc import IntegrityError

        create_user("dup@x.com", "pw", "viewer")
        with pytest.raises(IntegrityError):
            create_user("dup@x.com", "pw2", "viewer")

    def test_create_sp_duplicate_name_raises_integrity(self):
        from sqlalchemy.exc import IntegrityError

        create_service_principal("dup-sp", "viewer")
        with pytest.raises(IntegrityError):
            create_service_principal("dup-sp", "operator")

    def test_create_group_duplicate_name_raises_integrity(self):
        from sqlalchemy.exc import IntegrityError

        from shoreguard.api.auth import create_group

        create_group("team-a")
        with pytest.raises(IntegrityError):
            create_group("team-a")

    def test_add_group_member_duplicate_raises_integrity(self):
        from sqlalchemy.exc import IntegrityError

        from shoreguard.api.auth import add_group_member, create_group

        info = create_user("m@x.com", "pw", "viewer")
        grp = create_group("dupg")
        add_group_member(grp["id"], info["id"])
        with pytest.raises(IntegrityError):
            add_group_member(grp["id"], info["id"])


# ─── Bootstrap error path ────────────────────────────────────────────────


class TestBootstrapError:
    def setup_method(self):
        auth.reset()

    def teardown_method(self):
        auth.reset()

    def test_bootstrap_admin_rollbacks_on_failure(self, db, monkeypatch):
        """If user creation fails, ``bootstrap_admin_user`` re-raises."""
        from shoreguard.api.auth import bootstrap_admin_user
        from shoreguard.settings import reset_settings

        monkeypatch.setenv("SHOREGUARD_ADMIN_PASSWORD", "pw12345")
        reset_settings()

        with patch("shoreguard.api.auth.create_user", side_effect=RuntimeError("broke")):
            with pytest.raises(RuntimeError, match="broke"):
                bootstrap_admin_user()
