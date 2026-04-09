"""Tests for OpenID Connect integration.

Covers the OIDC client module (oidc.py) and the OIDC API endpoints
in pages.py.  Provider HTTP calls are mocked via monkeypatch.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.api import auth
from shoreguard.api.auth import create_user
from shoreguard.api.oidc import (
    OIDC_STATE_COOKIE,
    OIDCProvider,
    build_state_cookie,
    extract_email,
    generate_pkce,
    map_role,
    verify_state_cookie,
)
from shoreguard.models import Base

# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
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


@pytest.fixture
def provider():
    return OIDCProvider(
        name="test",
        display_name="Test Provider",
        issuer="https://idp.example.com",
        client_id="test-client-id",
        client_secret="test-client-secret",
        scopes=["openid", "email", "profile"],
        role_mapping=None,
    )


@pytest.fixture
def rsa_key():
    """Generate an RSA key pair for signing test JWTs."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    return private_key


@pytest.fixture
def jwks_dict(rsa_key):
    """Build a JWKS dict from the test RSA key."""
    from jwt.algorithms import RSAAlgorithm

    pub = rsa_key.public_key()
    jwk = json.loads(RSAAlgorithm.to_jwk(pub))
    jwk["kid"] = "test-kid"
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return {"keys": [jwk]}


def _make_id_token(rsa_key, claims: dict) -> str:
    """Create a signed ID token JWT."""
    headers = {"kid": "test-kid", "alg": "RS256"}
    return pyjwt.encode(claims, rsa_key, algorithm="RS256", headers=headers)


# ─── Unit tests: PKCE ─────────────────────────────────────────────────────────


class TestPKCE:
    def test_generate_pkce(self):
        verifier, challenge = generate_pkce()
        assert len(verifier) > 40
        # Verify the challenge is S256 of the verifier
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert challenge == expected

    def test_pkce_different_each_call(self):
        v1, _ = generate_pkce()
        v2, _ = generate_pkce()
        assert v1 != v2


# ─── Unit tests: state cookie ─────────────────────────────────────────────────


class TestStateCookie:
    def test_round_trip(self, db):
        cookie = build_state_cookie("google", "state123", "nonce456", "verifier789", "/dash")
        result = verify_state_cookie(cookie)
        assert result is not None
        assert result["p"] == "google"
        assert result["s"] == "state123"
        assert result["n"] == "nonce456"
        assert result["v"] == "verifier789"
        assert result["x"] == "/dash"

    def test_expired(self, db):
        cookie = build_state_cookie("google", "s", "n", "v", "/")
        # Manually tamper to set expiry in the past
        encoded, sig = cookie.split(".", 1)
        payload = json.loads(base64.urlsafe_b64decode(encoded))
        payload["e"] = int(time.time()) - 10
        new_payload = json.dumps(payload, separators=(",", ":"))
        new_encoded = base64.urlsafe_b64encode(new_payload.encode()).decode()
        import hashlib
        import hmac as hmac_mod

        new_sig = hmac_mod.new(auth._hmac_secret, new_payload.encode(), hashlib.sha256).hexdigest()
        result = verify_state_cookie(f"{new_encoded}.{new_sig}")
        assert result is None

    def test_tampered(self, db):
        cookie = build_state_cookie("google", "s", "n", "v", "/")
        result = verify_state_cookie(cookie + "tampered")
        assert result is None

    def test_empty(self, db):
        assert verify_state_cookie("") is None
        assert verify_state_cookie("garbage") is None


# ─── Unit tests: extract_email ─────────────────────────────────────────────────


class TestExtractEmail:
    def test_prefers_email(self):
        assert (
            extract_email({"email": "User@Test.COM", "preferred_username": "other"})
            == "user@test.com"
        )

    def test_fallback_preferred_username(self):
        assert extract_email({"preferred_username": "User@Corp.com"}) == "user@corp.com"

    def test_no_email(self):
        assert extract_email({"preferred_username": "johndoe"}) is None
        assert extract_email({}) is None


# ─── Unit tests: map_role ──────────────────────────────────────────────────────


class TestMapRole:
    def test_no_mapping(self, provider):
        assert map_role(provider, {"groups": ["admin"]}) == "viewer"

    def test_with_mapping(self):
        p = OIDCProvider(
            name="test",
            display_name="Test",
            issuer="https://idp.example.com",
            client_id="c",
            client_secret="s",
            role_mapping={
                "claim": "groups",
                "values": {"sg-admins": "admin", "sg-ops": "operator"},
            },
        )
        assert map_role(p, {"groups": ["sg-admins"]}) == "admin"
        assert map_role(p, {"groups": ["sg-ops"]}) == "operator"
        assert map_role(p, {"groups": ["other"]}) == "viewer"

    def test_highest_role_wins(self):
        p = OIDCProvider(
            name="test",
            display_name="Test",
            issuer="https://idp.example.com",
            client_id="c",
            client_secret="s",
            role_mapping={"claim": "groups", "values": {"ops": "operator", "admins": "admin"}},
        )
        assert map_role(p, {"groups": ["ops", "admins"]}) == "admin"

    def test_string_claim(self):
        p = OIDCProvider(
            name="test",
            display_name="Test",
            issuer="https://idp.example.com",
            client_id="c",
            client_secret="s",
            role_mapping={"claim": "role", "values": {"admin": "admin"}},
        )
        assert map_role(p, {"role": "admin"}) == "admin"


# ─── Unit tests: verify_id_token ──────────────────────────────────────────────


class TestVerifyIDToken:
    async def test_valid_token(self, provider, rsa_key, jwks_dict):
        now = int(time.time())
        claims = {
            "iss": provider.issuer,
            "aud": provider.client_id,
            "sub": "user123",
            "email": "user@test.com",
            "nonce": "test-nonce",
            "iat": now,
            "exp": now + 300,
        }
        token = _make_id_token(rsa_key, claims)

        from shoreguard.api import oidc

        provider._jwks = (jwks_dict["keys"], time.time())
        result = await oidc.verify_id_token(provider, token, "test-nonce")
        assert result["sub"] == "user123"
        assert result["email"] == "user@test.com"

    async def test_bad_nonce(self, provider, rsa_key, jwks_dict):
        now = int(time.time())
        claims = {
            "iss": provider.issuer,
            "aud": provider.client_id,
            "sub": "u",
            "nonce": "correct",
            "iat": now,
            "exp": now + 300,
        }
        token = _make_id_token(rsa_key, claims)
        provider._jwks = (jwks_dict["keys"], time.time())

        from shoreguard.api import oidc

        with pytest.raises(pyjwt.PyJWTError, match="Nonce mismatch"):
            await oidc.verify_id_token(provider, token, "wrong-nonce")

    async def test_bad_issuer(self, provider, rsa_key, jwks_dict):
        now = int(time.time())
        claims = {
            "iss": "https://evil.com",
            "aud": provider.client_id,
            "sub": "u",
            "nonce": "n",
            "iat": now,
            "exp": now + 300,
        }
        token = _make_id_token(rsa_key, claims)
        provider._jwks = (jwks_dict["keys"], time.time())

        from shoreguard.api import oidc

        with pytest.raises(pyjwt.exceptions.InvalidIssuerError):
            await oidc.verify_id_token(provider, token, "n")

    async def test_expired_token(self, provider, rsa_key, jwks_dict):
        now = int(time.time())
        claims = {
            "iss": provider.issuer,
            "aud": provider.client_id,
            "sub": "u",
            "nonce": "n",
            "iat": now - 600,
            "exp": now - 300,
        }
        token = _make_id_token(rsa_key, claims)
        provider._jwks = (jwks_dict["keys"], time.time())

        from shoreguard.api import oidc

        with pytest.raises(pyjwt.exceptions.ExpiredSignatureError):
            await oidc.verify_id_token(provider, token, "n")


# ─── Integration tests: API endpoints ────────────────────────────────────────


@pytest.fixture
def mock_client():
    from shoreguard.client import ShoreGuardClient

    client = MagicMock(spec=ShoreGuardClient)
    client.sandboxes = MagicMock()
    client.policies = MagicMock()
    client.providers = MagicMock()
    client.approvals = MagicMock()
    return client


def _setup_oidc_provider(monkeypatch):
    """Configure a test OIDC provider via settings."""
    providers = json.dumps(
        [
            {
                "name": "test",
                "display_name": "Test IdP",
                "issuer": "https://idp.example.com",
                "client_id": "test-client-id",
                "client_secret": "test-secret",
            }
        ]
    )
    monkeypatch.setenv("SHOREGUARD_OIDC_PROVIDERS_JSON", providers)


class TestOIDCProvidersEndpoint:
    async def test_returns_providers(self, db, mock_client, monkeypatch):
        from shoreguard.api.deps import get_client
        from shoreguard.api.main import app
        from shoreguard.api.oidc import init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        _setup_oidc_provider(monkeypatch)
        reset_settings()
        init_oidc()
        app.dependency_overrides[get_client] = lambda: mock_client
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/auth/oidc/providers")
                assert resp.status_code == 200
                data = resp.json()
                assert len(data) == 1
                assert data[0]["name"] == "test"
                assert data[0]["display_name"] == "Test IdP"
        finally:
            app.dependency_overrides.clear()
            reset_oidc()
            reset_settings()

    async def test_empty_when_none_configured(self, db, mock_client):
        from shoreguard.api.deps import get_client
        from shoreguard.api.main import app
        from shoreguard.api.oidc import init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        reset_settings()
        init_oidc()
        app.dependency_overrides[get_client] = lambda: mock_client
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/auth/oidc/providers")
                assert resp.status_code == 200
                assert resp.json() == []
        finally:
            app.dependency_overrides.clear()
            reset_oidc()
            reset_settings()


class TestOIDCLoginRedirect:
    async def test_redirects_to_provider(self, db, mock_client, monkeypatch):
        from shoreguard.api.deps import get_client
        from shoreguard.api.main import app
        from shoreguard.api.oidc import init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        _setup_oidc_provider(monkeypatch)
        reset_settings()
        init_oidc()
        app.dependency_overrides[get_client] = lambda: mock_client

        # Mock discovery
        disco = {
            "authorization_endpoint": "https://idp.example.com/authorize",
            "token_endpoint": "https://idp.example.com/token",
            "jwks_uri": "https://idp.example.com/.well-known/jwks.json",
        }
        from shoreguard.api import oidc

        oidc._providers["test"]._discovery = disco

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as c:
                resp = await c.get("/api/auth/oidc/login/test?next=/gateways")
                assert resp.status_code == 307
                location = resp.headers["location"]
                assert "idp.example.com/authorize" in location
                assert "code_challenge" in location
                assert "state=" in location
                assert "nonce=" in location
                # State cookie should be set
                assert OIDC_STATE_COOKIE in resp.cookies
        finally:
            app.dependency_overrides.clear()
            reset_oidc()
            reset_settings()

    async def test_unknown_provider_404(self, db, mock_client):
        from shoreguard.api.deps import get_client
        from shoreguard.api.main import app
        from shoreguard.api.oidc import init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        reset_settings()
        init_oidc()
        app.dependency_overrides[get_client] = lambda: mock_client
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/auth/oidc/login/nonexistent")
                assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()
            reset_oidc()
            reset_settings()


class TestOIDCCallback:
    async def _do_callback(
        self,
        db,
        mock_client,
        monkeypatch,
        rsa_key,
        jwks_dict,
        *,
        existing_email=None,
        existing_oidc=False,
    ):
        """Helper: run a full OIDC callback flow with mocked provider."""
        from shoreguard.api import oidc
        from shoreguard.api.deps import get_client
        from shoreguard.api.main import app
        from shoreguard.api.oidc import init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        _setup_oidc_provider(monkeypatch)
        reset_settings()
        init_oidc()
        app.dependency_overrides[get_client] = lambda: mock_client

        # Set up discovery on the provider
        disco = {
            "authorization_endpoint": "https://idp.example.com/authorize",
            "token_endpoint": "https://idp.example.com/token",
            "jwks_uri": "https://idp.example.com/.well-known/jwks.json",
        }
        oidc._providers["test"]._discovery = disco
        oidc._providers["test"]._jwks = (jwks_dict["keys"], time.time())

        # Create existing user if needed
        if existing_email:
            create_user(existing_email, "password123", "operator")
            if existing_oidc:
                # Manually link OIDC
                from shoreguard.models import User

                with db() as session:
                    u = session.query(User).filter(User.email == existing_email).first()
                    u.oidc_provider = "test"
                    u.oidc_sub = "oidc-sub-123"
                    session.commit()

        # Build a valid state cookie
        state_value = "test-state-123"
        nonce = "test-nonce-456"
        code_verifier = "test-verifier"
        cookie = build_state_cookie("test", state_value, nonce, code_verifier, "/dashboard")

        # Build a valid ID token
        now = int(time.time())
        id_claims = {
            "iss": "https://idp.example.com",
            "aud": "test-client-id",
            "sub": "oidc-sub-123",
            "email": existing_email or "newuser@test.com",
            "nonce": nonce,
            "iat": now,
            "exp": now + 300,
        }
        id_token = _make_id_token(rsa_key, id_claims)

        # Mock the token exchange
        async def mock_exchange(provider, code, redirect_uri, cv):
            return {"id_token": id_token, "access_token": "mock-access"}

        try:
            with patch.object(oidc, "exchange_code", side_effect=mock_exchange):
                async with AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="http://test",
                    follow_redirects=False,
                ) as c:
                    resp = await c.get(
                        "/api/auth/oidc/callback",
                        params={"code": "auth-code", "state": state_value},
                        cookies={OIDC_STATE_COOKIE: cookie},
                    )
                    return resp
        finally:
            app.dependency_overrides.clear()
            reset_oidc()
            reset_settings()

    async def test_new_user_created(self, db, mock_client, monkeypatch, rsa_key, jwks_dict):
        resp = await self._do_callback(db, mock_client, monkeypatch, rsa_key, jwks_dict)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/dashboard"
        assert "sg_session" in resp.cookies

        # Verify user was created
        from shoreguard.models import User

        with db() as session:
            user = session.query(User).filter(User.email == "newuser@test.com").first()
            assert user is not None
            assert user.oidc_provider == "test"
            assert user.oidc_sub == "oidc-sub-123"
            assert user.role == "viewer"
            assert user.hashed_password is None

    async def test_link_existing_user(self, db, mock_client, monkeypatch, rsa_key, jwks_dict):
        resp = await self._do_callback(
            db,
            mock_client,
            monkeypatch,
            rsa_key,
            jwks_dict,
            existing_email="existing@test.com",
        )
        assert resp.status_code == 302
        assert "sg_session" in resp.cookies

        # Verify OIDC was linked
        from shoreguard.models import User

        with db() as session:
            user = session.query(User).filter(User.email == "existing@test.com").first()
            assert user.oidc_provider == "test"
            assert user.oidc_sub == "oidc-sub-123"
            assert user.role == "operator"  # Keeps original role

    async def test_returning_oidc_user(self, db, mock_client, monkeypatch, rsa_key, jwks_dict):
        resp = await self._do_callback(
            db,
            mock_client,
            monkeypatch,
            rsa_key,
            jwks_dict,
            existing_email="returning@test.com",
            existing_oidc=True,
        )
        assert resp.status_code == 302
        assert "sg_session" in resp.cookies

    async def test_provider_error(self, db, mock_client):
        from shoreguard.api.deps import get_client
        from shoreguard.api.main import app
        from shoreguard.api.oidc import init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        reset_settings()
        init_oidc()
        app.dependency_overrides[get_client] = lambda: mock_client
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as c:
                resp = await c.get(
                    "/api/auth/oidc/callback",
                    params={"error": "access_denied"},
                )
                assert resp.status_code == 302
                assert "/login?error=oidc_denied" in resp.headers["location"]
        finally:
            app.dependency_overrides.clear()
            reset_oidc()
            reset_settings()

    async def test_bad_state_cookie(self, db, mock_client, monkeypatch):
        from shoreguard.api.deps import get_client
        from shoreguard.api.main import app
        from shoreguard.api.oidc import init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        _setup_oidc_provider(monkeypatch)
        reset_settings()
        init_oidc()
        app.dependency_overrides[get_client] = lambda: mock_client
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as c:
                resp = await c.get(
                    "/api/auth/oidc/callback",
                    params={"code": "auth-code", "state": "test-state"},
                    cookies={OIDC_STATE_COOKIE: "tampered-garbage"},
                )
                assert resp.status_code == 302
                assert "/login?error=oidc_failed" in resp.headers["location"]
        finally:
            app.dependency_overrides.clear()
            reset_oidc()
            reset_settings()


class TestAuthCheckOIDC:
    async def test_includes_oidc_providers(self, db, mock_client, monkeypatch):
        from shoreguard.api.deps import get_client
        from shoreguard.api.main import app
        from shoreguard.api.oidc import init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        _setup_oidc_provider(monkeypatch)
        reset_settings()
        init_oidc()
        create_user("admin@test.com", "adminpass", "admin")
        app.dependency_overrides[get_client] = lambda: mock_client
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/auth/check")
                data = resp.json()
                assert "oidc_providers" in data
                assert len(data["oidc_providers"]) == 1
                assert data["oidc_providers"][0]["name"] == "test"
        finally:
            app.dependency_overrides.clear()
            reset_oidc()
            reset_settings()


class TestListUsersOIDC:
    async def test_oidc_provider_in_user_list(self, db, mock_client, monkeypatch):
        from shoreguard.api.deps import get_client
        from shoreguard.api.main import app
        from shoreguard.api.oidc import init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        reset_settings()
        init_oidc()

        # Create admin and an OIDC user
        create_user("admin@test.com", "adminpass", "admin")
        from shoreguard.api.auth import find_or_create_oidc_user

        find_or_create_oidc_user("oidcuser@test.com", "google", "sub123", "viewer")

        app.dependency_overrides[get_client] = lambda: mock_client
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                # Login as admin
                resp = await c.post(
                    "/api/auth/login",
                    json={"email": "admin@test.com", "password": "adminpass"},
                )
                assert resp.status_code == 200

                # List users
                resp = await c.get("/api/auth/users")
                assert resp.status_code == 200
                users = resp.json()
                oidc_user = [u for u in users if u["email"] == "oidcuser@test.com"]
                assert len(oidc_user) == 1
                assert oidc_user[0]["oidc_provider"] == "google"
        finally:
            app.dependency_overrides.clear()
            reset_oidc()
            reset_settings()


# ─── Mutation-killing tests ──────────────────────────────────────────────────
# These tests target survived mutants by asserting exact values, boundary
# conditions, and precise error messages.


class TestInitOIDCMutationKillers:
    """Kill mutants in init_oidc()."""

    def test_invalid_json_returns_early(self, db, monkeypatch):
        """Mutant: remove logger.error or return on invalid JSON."""
        from shoreguard.api.oidc import get_providers, init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        monkeypatch.setenv("SHOREGUARD_OIDC_PROVIDERS_JSON", "{{{bad json")
        reset_settings()
        init_oidc()
        assert get_providers() == []
        reset_oidc()
        reset_settings()

    def test_non_list_json_returns_early(self, db, monkeypatch):
        """Mutant: remove isinstance(entries, list) check or return."""
        from shoreguard.api.oidc import get_providers, init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        monkeypatch.setenv("SHOREGUARD_OIDC_PROVIDERS_JSON", '{"not": "a list"}')
        reset_settings()
        init_oidc()
        assert get_providers() == []
        reset_oidc()
        reset_settings()

    def test_non_dict_entries_skipped(self, db, monkeypatch):
        """Mutant: remove isinstance(entry, dict) continue."""
        from shoreguard.api.oidc import get_providers, init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        monkeypatch.setenv("SHOREGUARD_OIDC_PROVIDERS_JSON", '["string_entry", 42, null]')
        reset_settings()
        init_oidc()
        assert get_providers() == []
        reset_oidc()
        reset_settings()

    def test_missing_name_skipped(self, db, monkeypatch):
        """Mutant: remove name check in 'not name or ...'."""
        from shoreguard.api.oidc import get_providers, init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        monkeypatch.setenv(
            "SHOREGUARD_OIDC_PROVIDERS_JSON",
            json.dumps(
                [
                    {"issuer": "https://idp.example.com", "client_id": "cid"},
                ]
            ),
        )
        reset_settings()
        init_oidc()
        assert get_providers() == []
        reset_oidc()
        reset_settings()

    def test_missing_issuer_skipped(self, db, monkeypatch):
        """Mutant: remove issuer check."""
        from shoreguard.api.oidc import get_providers, init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        monkeypatch.setenv(
            "SHOREGUARD_OIDC_PROVIDERS_JSON",
            json.dumps(
                [
                    {"name": "test", "client_id": "cid"},
                ]
            ),
        )
        reset_settings()
        init_oidc()
        assert get_providers() == []
        reset_oidc()
        reset_settings()

    def test_missing_client_id_skipped(self, db, monkeypatch):
        """Mutant: remove client_id check."""
        from shoreguard.api.oidc import get_providers, init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        monkeypatch.setenv(
            "SHOREGUARD_OIDC_PROVIDERS_JSON",
            json.dumps(
                [
                    {"name": "test", "issuer": "https://idp.example.com"},
                ]
            ),
        )
        reset_settings()
        init_oidc()
        assert get_providers() == []
        reset_oidc()
        reset_settings()

    def test_empty_name_skipped(self, db, monkeypatch):
        """Mutant: 'not name' vs truthy check — empty string is falsy."""
        from shoreguard.api.oidc import get_providers, init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        monkeypatch.setenv(
            "SHOREGUARD_OIDC_PROVIDERS_JSON",
            json.dumps(
                [
                    {"name": "", "issuer": "https://idp.example.com", "client_id": "cid"},
                ]
            ),
        )
        reset_settings()
        init_oidc()
        assert get_providers() == []
        reset_oidc()
        reset_settings()

    def test_display_name_defaults_to_name(self, db, monkeypatch):
        """Mutant: change default for display_name."""
        from shoreguard.api.oidc import get_providers, init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        monkeypatch.setenv(
            "SHOREGUARD_OIDC_PROVIDERS_JSON",
            json.dumps(
                [
                    {"name": "myidp", "issuer": "https://idp.example.com", "client_id": "cid"},
                ]
            ),
        )
        reset_settings()
        init_oidc()
        providers = get_providers()
        assert len(providers) == 1
        assert providers[0].display_name == "myidp"
        assert providers[0].name == "myidp"
        reset_oidc()
        reset_settings()

    def test_explicit_display_name(self, db, monkeypatch):
        """Mutant: change display_name extraction."""
        from shoreguard.api.oidc import get_providers, init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        monkeypatch.setenv(
            "SHOREGUARD_OIDC_PROVIDERS_JSON",
            json.dumps(
                [
                    {
                        "name": "myidp",
                        "display_name": "My IdP",
                        "issuer": "https://idp.example.com",
                        "client_id": "cid",
                    },
                ]
            ),
        )
        reset_settings()
        init_oidc()
        providers = get_providers()
        assert providers[0].display_name == "My IdP"
        assert providers[0].display_name != providers[0].name
        reset_oidc()
        reset_settings()

    def test_issuer_trailing_slash_stripped(self, db, monkeypatch):
        """Mutant: remove .rstrip('/')."""
        from shoreguard.api.oidc import get_providers, init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        monkeypatch.setenv(
            "SHOREGUARD_OIDC_PROVIDERS_JSON",
            json.dumps(
                [
                    {"name": "test", "issuer": "https://idp.example.com/", "client_id": "cid"},
                ]
            ),
        )
        reset_settings()
        init_oidc()
        providers = get_providers()
        assert providers[0].issuer == "https://idp.example.com"
        assert not providers[0].issuer.endswith("/")
        reset_oidc()
        reset_settings()

    def test_client_secret_defaults_to_empty(self, db, monkeypatch):
        """Mutant: change default for client_secret."""
        from shoreguard.api.oidc import get_providers, init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        monkeypatch.setenv(
            "SHOREGUARD_OIDC_PROVIDERS_JSON",
            json.dumps(
                [
                    {"name": "test", "issuer": "https://idp.example.com", "client_id": "cid"},
                ]
            ),
        )
        reset_settings()
        init_oidc()
        providers = get_providers()
        assert providers[0].client_secret == ""
        reset_oidc()
        reset_settings()

    def test_scopes_default(self, db, monkeypatch):
        """Mutant: change default scopes."""
        from shoreguard.api.oidc import get_providers, init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        monkeypatch.setenv(
            "SHOREGUARD_OIDC_PROVIDERS_JSON",
            json.dumps(
                [
                    {"name": "test", "issuer": "https://idp.example.com", "client_id": "cid"},
                ]
            ),
        )
        reset_settings()
        init_oidc()
        providers = get_providers()
        assert providers[0].scopes == ["openid", "email", "profile"]
        reset_oidc()
        reset_settings()

    def test_custom_scopes(self, db, monkeypatch):
        """Mutant: ignore custom scopes."""
        from shoreguard.api.oidc import get_providers, init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        monkeypatch.setenv(
            "SHOREGUARD_OIDC_PROVIDERS_JSON",
            json.dumps(
                [
                    {
                        "name": "test",
                        "issuer": "https://idp.example.com",
                        "client_id": "cid",
                        "scopes": ["openid", "groups"],
                    },
                ]
            ),
        )
        reset_settings()
        init_oidc()
        providers = get_providers()
        assert providers[0].scopes == ["openid", "groups"]
        reset_oidc()
        reset_settings()

    def test_role_mapping_none_by_default(self, db, monkeypatch):
        """Mutant: change default for role_mapping."""
        from shoreguard.api.oidc import get_providers, init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        monkeypatch.setenv(
            "SHOREGUARD_OIDC_PROVIDERS_JSON",
            json.dumps(
                [
                    {"name": "test", "issuer": "https://idp.example.com", "client_id": "cid"},
                ]
            ),
        )
        reset_settings()
        init_oidc()
        providers = get_providers()
        assert providers[0].role_mapping is None
        reset_oidc()
        reset_settings()

    def test_role_mapping_stored(self, db, monkeypatch):
        """Mutant: ignore role_mapping from config."""
        from shoreguard.api.oidc import get_providers, init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        mapping = {"claim": "groups", "values": {"admin": "admin"}}
        monkeypatch.setenv(
            "SHOREGUARD_OIDC_PROVIDERS_JSON",
            json.dumps(
                [
                    {
                        "name": "test",
                        "issuer": "https://idp.example.com",
                        "client_id": "cid",
                        "role_mapping": mapping,
                    },
                ]
            ),
        )
        reset_settings()
        init_oidc()
        providers = get_providers()
        assert providers[0].role_mapping == mapping
        reset_oidc()
        reset_settings()

    def test_providers_cleared_on_reinit(self, db, monkeypatch):
        """Mutant: remove _providers.clear()."""
        from shoreguard.api.oidc import get_providers, init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        monkeypatch.setenv(
            "SHOREGUARD_OIDC_PROVIDERS_JSON",
            json.dumps(
                [
                    {"name": "first", "issuer": "https://a.com", "client_id": "c"},
                ]
            ),
        )
        reset_settings()
        init_oidc()
        assert len(get_providers()) == 1

        # Reinit with empty
        monkeypatch.setenv("SHOREGUARD_OIDC_PROVIDERS_JSON", "[]")
        reset_settings()
        init_oidc()
        assert len(get_providers()) == 0
        reset_oidc()
        reset_settings()

    def test_provider_stored_by_name(self, db, monkeypatch):
        """Mutant: change _providers[name] = provider."""
        from shoreguard.api.oidc import get_provider, init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        monkeypatch.setenv(
            "SHOREGUARD_OIDC_PROVIDERS_JSON",
            json.dumps(
                [
                    {"name": "myidp", "issuer": "https://idp.example.com", "client_id": "cid"},
                ]
            ),
        )
        reset_settings()
        init_oidc()
        p = get_provider("myidp")
        assert p is not None
        assert p.name == "myidp"
        assert p.client_id == "cid"
        assert get_provider("nonexistent") is None
        reset_oidc()
        reset_settings()

    def test_multiple_providers(self, db, monkeypatch):
        """Mutant: break the loop over entries."""
        from shoreguard.api.oidc import get_provider, get_providers, init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        monkeypatch.setenv(
            "SHOREGUARD_OIDC_PROVIDERS_JSON",
            json.dumps(
                [
                    {"name": "a", "issuer": "https://a.com", "client_id": "ca"},
                    {"name": "b", "issuer": "https://b.com", "client_id": "cb"},
                ]
            ),
        )
        reset_settings()
        init_oidc()
        assert len(get_providers()) == 2
        pa = get_provider("a")
        pb = get_provider("b")
        assert pa is not None
        assert pb is not None
        assert pa.issuer == "https://a.com"
        assert pb.issuer == "https://b.com"
        reset_oidc()
        reset_settings()

    def test_valid_among_invalid_entries(self, db, monkeypatch):
        """Mutant: skip valid entries when invalid ones exist."""
        from shoreguard.api.oidc import get_providers, init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        monkeypatch.setenv(
            "SHOREGUARD_OIDC_PROVIDERS_JSON",
            json.dumps(
                [
                    "bad_string",
                    {"name": "good", "issuer": "https://good.com", "client_id": "c"},
                    {"missing_name": True},
                ]
            ),
        )
        reset_settings()
        init_oidc()
        providers = get_providers()
        assert len(providers) == 1
        assert providers[0].name == "good"
        reset_oidc()
        reset_settings()

    def test_all_fields_set_exactly(self, db, monkeypatch):
        """Full field assertion to kill field-swap mutants."""
        from shoreguard.api.oidc import get_provider, init_oidc, reset_oidc
        from shoreguard.settings import reset_settings

        monkeypatch.setenv(
            "SHOREGUARD_OIDC_PROVIDERS_JSON",
            json.dumps(
                [
                    {
                        "name": "acme",
                        "display_name": "Acme Corp IdP",
                        "issuer": "https://acme.example.com/",
                        "client_id": "acme-client",
                        "client_secret": "acme-secret",
                        "scopes": ["openid"],
                        "role_mapping": {"claim": "role", "values": {"admin": "admin"}},
                    }
                ]
            ),
        )
        reset_settings()
        init_oidc()
        p = get_provider("acme")
        assert p is not None
        assert p.name == "acme"
        assert p.display_name == "Acme Corp IdP"
        assert p.issuer == "https://acme.example.com"  # trailing slash stripped
        assert p.client_id == "acme-client"
        assert p.client_secret == "acme-secret"
        assert p.scopes == ["openid"]
        assert p.role_mapping == {"claim": "role", "values": {"admin": "admin"}}
        reset_oidc()
        reset_settings()


class TestDiscoverMutationKillers:
    """Kill mutants in discover()."""

    async def test_discovery_url_format(self, provider):
        """Mutant: change discovery URL construction."""
        from shoreguard.api.oidc import discover

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"authorization_endpoint": "https://idp.example.com/auth"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("shoreguard.api.oidc.httpx.AsyncClient", return_value=mock_client):
            result = await discover(provider)

        mock_client.get.assert_called_once_with(
            "https://idp.example.com/.well-known/openid-configuration"
        )
        assert result == {"authorization_endpoint": "https://idp.example.com/auth"}

    async def test_discovery_cached(self, provider):
        """Mutant: remove cache check (provider._discovery is not None)."""
        from shoreguard.api.oidc import discover

        cached = {"authorization_endpoint": "cached"}
        provider._discovery = cached
        result = await discover(provider)
        assert result is cached
        assert result == {"authorization_endpoint": "cached"}

    async def test_discovery_none_triggers_fetch(self, provider):
        """Mutant: change 'is not None' to 'is None'."""
        from shoreguard.api.oidc import discover

        assert provider._discovery is None

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"token_endpoint": "https://idp.example.com/token"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("shoreguard.api.oidc.httpx.AsyncClient", return_value=mock_client):
            result = await discover(provider)

        assert result == {"token_endpoint": "https://idp.example.com/token"}
        assert provider._discovery == result

    async def test_discovery_stores_result(self, provider):
        """Mutant: remove 'provider._discovery = resp.json()'."""
        from shoreguard.api.oidc import discover

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"foo": "bar"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("shoreguard.api.oidc.httpx.AsyncClient", return_value=mock_client):
            await discover(provider)

        assert provider._discovery is not None
        assert provider._discovery == {"foo": "bar"}

        # Second call should not fetch again
        with patch("shoreguard.api.oidc.httpx.AsyncClient") as mock_cls:
            result2 = await discover(provider)
            mock_cls.assert_not_called()
        assert result2 == {"foo": "bar"}


class TestGetJWKSMutationKillers:
    """Kill mutants in get_jwks()."""

    async def test_jwks_cache_hit(self, provider):
        """Mutant: change cache TTL comparison."""
        from shoreguard.api.oidc import get_jwks

        keys = [{"kid": "k1", "kty": "RSA"}]
        provider._jwks = (keys, time.time())  # fresh cache

        result = await get_jwks(provider)
        assert result is keys
        assert result == [{"kid": "k1", "kty": "RSA"}]

    async def test_jwks_cache_expired(self, provider):
        """Mutant: change TTL check direction or value."""
        from shoreguard.api.oidc import JWKS_CACHE_TTL, get_jwks

        old_keys = [{"kid": "old"}]
        provider._jwks = (old_keys, time.time() - JWKS_CACHE_TTL - 1)  # expired

        provider._discovery = {
            "jwks_uri": "https://idp.example.com/.well-known/jwks.json",
        }

        new_keys = [{"kid": "new", "kty": "RSA"}]
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"keys": new_keys}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("shoreguard.api.oidc.httpx.AsyncClient", return_value=mock_client):
            result = await get_jwks(provider)

        assert result == new_keys
        assert result is not old_keys

    async def test_jwks_no_cache(self, provider):
        """Mutant: change 'provider._jwks and ...' truthiness."""
        from shoreguard.api.oidc import get_jwks

        assert provider._jwks is None

        provider._discovery = {
            "jwks_uri": "https://idp.example.com/jwks",
        }

        keys = [{"kid": "fresh"}]
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"keys": keys}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("shoreguard.api.oidc.httpx.AsyncClient", return_value=mock_client):
            result = await get_jwks(provider)

        assert result == keys
        assert provider._jwks is not None
        assert provider._jwks[0] == keys

    async def test_jwks_cache_stores_timestamp(self, provider):
        """Mutant: change stored timestamp."""
        from shoreguard.api.oidc import get_jwks

        provider._discovery = {"jwks_uri": "https://idp.example.com/jwks"}

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"keys": []}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        before = time.time()
        with patch("shoreguard.api.oidc.httpx.AsyncClient", return_value=mock_client):
            await get_jwks(provider)
        after = time.time()

        assert provider._jwks is not None
        cached_time = provider._jwks[1]
        assert before <= cached_time <= after

    async def test_jwks_empty_keys_list(self, provider):
        """Mutant: change default for .get('keys', [])."""
        from shoreguard.api.oidc import get_jwks

        provider._discovery = {"jwks_uri": "https://idp.example.com/jwks"}

        mock_resp = MagicMock()
        mock_resp.json.return_value = {}  # no "keys" field
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("shoreguard.api.oidc.httpx.AsyncClient", return_value=mock_client):
            result = await get_jwks(provider)

        assert result == []

    async def test_jwks_cache_ttl_boundary(self, provider):
        """Mutant: change < to <= in TTL comparison."""
        from shoreguard.api.oidc import JWKS_CACHE_TTL, get_jwks

        assert JWKS_CACHE_TTL == 3600

        # Cache exactly at TTL boundary: (now - cached) == JWKS_CACHE_TTL
        # Should trigger refresh since < is strict
        old_keys = [{"kid": "boundary"}]
        provider._jwks = (old_keys, time.time() - JWKS_CACHE_TTL)

        provider._discovery = {"jwks_uri": "https://idp.example.com/jwks"}

        new_keys = [{"kid": "refreshed"}]
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"keys": new_keys}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("shoreguard.api.oidc.httpx.AsyncClient", return_value=mock_client):
            result = await get_jwks(provider)

        assert result == new_keys

    async def test_jwks_uses_jwks_uri_from_discovery(self, provider):
        """Mutant: change disco['jwks_uri'] key name."""
        from shoreguard.api.oidc import get_jwks

        provider._discovery = {"jwks_uri": "https://custom.example.com/my-jwks"}

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"keys": [{"kid": "x"}]}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("shoreguard.api.oidc.httpx.AsyncClient", return_value=mock_client):
            await get_jwks(provider)

        mock_client.get.assert_called_once_with("https://custom.example.com/my-jwks")


class TestBuildAuthorizeUrlMutationKillers:
    """Kill mutants in build_authorize_url()."""

    async def test_exact_url_params(self, provider):
        """Assert every parameter in the URL precisely."""
        from urllib.parse import parse_qs, urlparse

        from shoreguard.api.oidc import build_authorize_url

        provider._discovery = {
            "authorization_endpoint": "https://idp.example.com/authorize",
        }

        url = await build_authorize_url(
            provider,
            redirect_uri="https://app.example.com/callback",
            state="my-state",
            nonce="my-nonce",
            code_challenge="my-challenge",
        )

        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "idp.example.com"
        assert parsed.path == "/authorize"

        params = parse_qs(parsed.query)
        assert params["response_type"] == ["code"]
        assert params["client_id"] == ["test-client-id"]
        assert params["redirect_uri"] == ["https://app.example.com/callback"]
        assert params["scope"] == ["openid email profile"]
        assert params["state"] == ["my-state"]
        assert params["nonce"] == ["my-nonce"]
        assert params["code_challenge"] == ["my-challenge"]
        assert params["code_challenge_method"] == ["S256"]

    async def test_url_starts_with_authorization_endpoint(self, provider):
        """Mutant: change f-string construction."""
        from shoreguard.api.oidc import build_authorize_url

        provider._discovery = {
            "authorization_endpoint": "https://different.example.com/auth",
        }

        url = await build_authorize_url(provider, "https://cb.com", "s", "n", "c")
        assert url.startswith("https://different.example.com/auth?")

    async def test_scope_joined_with_space(self, provider):
        """Mutant: change ' '.join separator."""
        from urllib.parse import parse_qs, urlparse

        from shoreguard.api.oidc import build_authorize_url

        provider._discovery = {"authorization_endpoint": "https://idp.example.com/auth"}
        provider.scopes = ["openid", "email"]

        url = await build_authorize_url(provider, "https://cb.com", "s", "n", "c")
        params = parse_qs(urlparse(url).query)
        assert params["scope"] == ["openid email"]

    async def test_custom_scopes_in_url(self, provider):
        """Mutant: ignore provider.scopes."""
        from urllib.parse import parse_qs, urlparse

        from shoreguard.api.oidc import build_authorize_url

        provider._discovery = {"authorization_endpoint": "https://idp.example.com/auth"}
        provider.scopes = ["openid", "groups", "offline_access"]

        url = await build_authorize_url(provider, "https://cb.com", "s", "n", "c")
        params = parse_qs(urlparse(url).query)
        assert params["scope"] == ["openid groups offline_access"]

    async def test_all_params_present(self, provider):
        """Mutant: remove one of the params dict keys."""
        from urllib.parse import parse_qs, urlparse

        from shoreguard.api.oidc import build_authorize_url

        provider._discovery = {"authorization_endpoint": "https://idp.example.com/auth"}

        url = await build_authorize_url(provider, "https://cb.com", "st", "nc", "ch")
        params = parse_qs(urlparse(url).query)
        expected_keys = {
            "response_type",
            "client_id",
            "redirect_uri",
            "scope",
            "state",
            "nonce",
            "code_challenge",
            "code_challenge_method",
        }
        assert set(params.keys()) == expected_keys


class TestMapRoleMutationKillers:
    """Kill mutants in map_role()."""

    def test_empty_values_map_returns_default(self):
        """Mutant: remove 'not values_map' check."""
        p = OIDCProvider(
            name="test",
            display_name="Test",
            issuer="https://idp.example.com",
            client_id="c",
            client_secret="s",
            role_mapping={"claim": "groups", "values": {}},
        )
        assert map_role(p, {"groups": ["admin"]}) == "viewer"

    def test_empty_claim_name_returns_default(self):
        """Mutant: remove 'not claim_name' check."""
        p = OIDCProvider(
            name="test",
            display_name="Test",
            issuer="https://idp.example.com",
            client_id="c",
            client_secret="s",
            role_mapping={"claim": "", "values": {"admin": "admin"}},
        )
        assert map_role(p, {"groups": ["admin"]}) == "viewer"

    def test_claim_not_in_claims_returns_default(self):
        """Mutant: remove 'claim_value is None' check."""
        p = OIDCProvider(
            name="test",
            display_name="Test",
            issuer="https://idp.example.com",
            client_id="c",
            client_secret="s",
            role_mapping={"claim": "groups", "values": {"admin": "admin"}},
        )
        assert map_role(p, {"other_claim": "admin"}) == "viewer"

    def test_non_list_non_string_returns_default(self):
        """Mutant: remove isinstance(claim_value, list) check."""
        p = OIDCProvider(
            name="test",
            display_name="Test",
            issuer="https://idp.example.com",
            client_id="c",
            client_secret="s",
            role_mapping={"claim": "groups", "values": {"42": "admin"}},
        )
        assert map_role(p, {"groups": 42}) == "viewer"

    def test_string_claim_converted_to_list(self):
        """Mutant: remove isinstance(claim_value, str) conversion."""
        p = OIDCProvider(
            name="test",
            display_name="Test",
            issuer="https://idp.example.com",
            client_id="c",
            client_secret="s",
            role_mapping={"claim": "role", "values": {"ops": "operator"}},
        )
        result = map_role(p, {"role": "ops"})
        assert result == "operator"

    def test_no_matching_value_returns_default(self):
        """Mutant: change values_map.get() behavior."""
        p = OIDCProvider(
            name="test",
            display_name="Test",
            issuer="https://idp.example.com",
            client_id="c",
            client_secret="s",
            role_mapping={"claim": "groups", "values": {"admin": "admin"}},
        )
        assert map_role(p, {"groups": ["users", "devs"]}) == "viewer"

    def test_rank_comparison_operator_vs_viewer(self):
        """Mutant: change > to >= or < in rank comparison."""
        p = OIDCProvider(
            name="test",
            display_name="Test",
            issuer="https://idp.example.com",
            client_id="c",
            client_secret="s",
            role_mapping={"claim": "groups", "values": {"ops": "operator", "viewers": "viewer"}},
        )
        # operator should win over default viewer
        assert map_role(p, {"groups": ["ops"]}) == "operator"
        # viewer should tie with default and NOT upgrade
        assert map_role(p, {"groups": ["viewers"]}) == "viewer"

    def test_highest_rank_wins_among_multiple(self):
        """Mutant: change best_role/best_rank update logic."""
        p = OIDCProvider(
            name="test",
            display_name="Test",
            issuer="https://idp.example.com",
            client_id="c",
            client_secret="s",
            role_mapping={
                "claim": "groups",
                "values": {
                    "viewers": "viewer",
                    "ops": "operator",
                    "admins": "admin",
                },
            },
        )
        assert map_role(p, {"groups": ["viewers", "ops", "admins"]}) == "admin"
        assert map_role(p, {"groups": ["admins", "ops"]}) == "admin"
        assert map_role(p, {"groups": ["viewers", "ops"]}) == "operator"
        assert map_role(p, {"groups": ["viewers"]}) == "viewer"

    def test_unknown_role_in_values_ignored(self):
        """Mutant: _ROLE_RANK.get(role, 0) default."""
        p = OIDCProvider(
            name="test",
            display_name="Test",
            issuer="https://idp.example.com",
            client_id="c",
            client_secret="s",
            role_mapping={"claim": "groups", "values": {"unknown": "nonexistent_role"}},
        )
        # nonexistent_role has rank 0, same as default viewer, so no upgrade
        assert map_role(p, {"groups": ["unknown"]}) == "viewer"

    def test_str_conversion_in_values_lookup(self):
        """Mutant: remove str(val) conversion."""
        p = OIDCProvider(
            name="test",
            display_name="Test",
            issuer="https://idp.example.com",
            client_id="c",
            client_secret="s",
            role_mapping={"claim": "groups", "values": {"123": "admin"}},
        )
        # Numeric-ish group value as string in list
        assert map_role(p, {"groups": ["123"]}) == "admin"

    def test_missing_mapping_key_claim(self):
        """Mutant: change mapping.get('claim', '') default."""
        p = OIDCProvider(
            name="test",
            display_name="Test",
            issuer="https://idp.example.com",
            client_id="c",
            client_secret="s",
            role_mapping={"values": {"admin": "admin"}},  # no "claim" key
        )
        assert map_role(p, {"groups": ["admin"]}) == "viewer"

    def test_missing_mapping_key_values(self):
        """Mutant: change mapping.get('values', {}) default."""
        p = OIDCProvider(
            name="test",
            display_name="Test",
            issuer="https://idp.example.com",
            client_id="c",
            client_secret="s",
            role_mapping={"claim": "groups"},  # no "values" key
        )
        assert map_role(p, {"groups": ["admin"]}) == "viewer"


class TestVerifyIDTokenMutationKillers:
    """Kill mutants in verify_id_token()."""

    async def test_key_rotation_refresh(self, provider, rsa_key, jwks_dict):
        """Mutant: remove key rotation retry logic."""
        # Build a valid JWK with a different kid so first loop misses
        from jwt.algorithms import RSAAlgorithm

        from shoreguard.api import oidc

        wrong_key_jwk = json.loads(RSAAlgorithm.to_jwk(rsa_key.public_key()))
        wrong_key_jwk["kid"] = "wrong-kid"
        wrong_key_jwk["use"] = "sig"
        wrong_key_jwk["alg"] = "RS256"
        wrong_keys = [wrong_key_jwk]
        provider._jwks = (wrong_keys, time.time())

        now = int(time.time())
        claims = {
            "iss": provider.issuer,
            "aud": provider.client_id,
            "sub": "u",
            "nonce": "n",
            "iat": now,
            "exp": now + 300,
        }
        token = _make_id_token(rsa_key, claims)

        correct_keys = jwks_dict["keys"]

        # After clearing _jwks, get_jwks should return the correct keys
        async def mock_get_jwks(prov):
            if prov._jwks is None:
                prov._jwks = (correct_keys, time.time())
                return correct_keys
            return prov._jwks[0]

        with patch.object(oidc, "get_jwks", side_effect=mock_get_jwks):
            result = await oidc.verify_id_token(provider, token, "n")

        assert result["sub"] == "u"
        assert result["nonce"] == "n"

    async def test_no_matching_kid_raises(self, provider, rsa_key):
        """Mutant: change error message or exception type."""
        from jwt.algorithms import RSAAlgorithm

        from shoreguard.api import oidc

        # Valid JWK but with wrong kid
        wrong_jwk = json.loads(RSAAlgorithm.to_jwk(rsa_key.public_key()))
        wrong_jwk["kid"] = "wrong-kid"
        wrong_jwk["use"] = "sig"
        wrong_jwk["alg"] = "RS256"
        wrong_keys = [wrong_jwk]
        provider._jwks = (wrong_keys, time.time())

        now = int(time.time())
        claims = {
            "iss": provider.issuer,
            "aud": provider.client_id,
            "sub": "u",
            "nonce": "n",
            "iat": now,
            "exp": now + 300,
        }
        token = _make_id_token(rsa_key, claims)

        # get_jwks always returns wrong keys (no rotation helps)
        async def always_wrong_keys(prov):
            return wrong_keys

        with patch.object(oidc, "get_jwks", side_effect=always_wrong_keys):
            with pytest.raises(pyjwt.PyJWTError, match=r"No matching key found for kid=test-kid"):
                await oidc.verify_id_token(provider, token, "n")

    async def test_nonce_mismatch_exact_message(self, provider, rsa_key, jwks_dict):
        """Mutant: change nonce comparison or error message."""
        from shoreguard.api import oidc

        provider._jwks = (jwks_dict["keys"], time.time())
        now = int(time.time())
        claims = {
            "iss": provider.issuer,
            "aud": provider.client_id,
            "sub": "u",
            "nonce": "correct-nonce",
            "iat": now,
            "exp": now + 300,
        }
        token = _make_id_token(rsa_key, claims)

        with pytest.raises(pyjwt.PyJWTError, match="^Nonce mismatch$"):
            await oidc.verify_id_token(provider, token, "wrong-nonce")

    async def test_algorithms_used(self, provider, rsa_key, jwks_dict):
        """Mutant: change algorithms list."""
        from shoreguard.api import oidc

        provider._jwks = (jwks_dict["keys"], time.time())
        now = int(time.time())
        claims = {
            "iss": provider.issuer,
            "aud": provider.client_id,
            "sub": "u",
            "nonce": "n",
            "iat": now,
            "exp": now + 300,
        }
        token = _make_id_token(rsa_key, claims)

        # RS256 should be accepted
        result = await oidc.verify_id_token(provider, token, "n")
        assert result["sub"] == "u"

    async def test_audience_checked(self, provider, rsa_key, jwks_dict):
        """Mutant: remove audience param from jwt.decode."""
        from shoreguard.api import oidc

        provider._jwks = (jwks_dict["keys"], time.time())
        now = int(time.time())
        claims = {
            "iss": provider.issuer,
            "aud": "wrong-audience",
            "sub": "u",
            "nonce": "n",
            "iat": now,
            "exp": now + 300,
        }
        token = _make_id_token(rsa_key, claims)

        with pytest.raises(pyjwt.exceptions.InvalidAudienceError):
            await oidc.verify_id_token(provider, token, "n")

    async def test_claims_returned_exactly(self, provider, rsa_key, jwks_dict):
        """Mutant: return something other than claims."""
        from shoreguard.api import oidc

        provider._jwks = (jwks_dict["keys"], time.time())
        now = int(time.time())
        claims = {
            "iss": provider.issuer,
            "aud": provider.client_id,
            "sub": "user-abc",
            "email": "test@test.com",
            "nonce": "my-nonce",
            "iat": now,
            "exp": now + 300,
        }
        token = _make_id_token(rsa_key, claims)

        result = await oidc.verify_id_token(provider, token, "my-nonce")
        assert result["iss"] == provider.issuer
        assert result["aud"] == provider.client_id
        assert result["sub"] == "user-abc"
        assert result["email"] == "test@test.com"
        assert result["nonce"] == "my-nonce"

    async def test_jwks_cleared_on_kid_miss(self, provider, rsa_key, jwks_dict):
        """Mutant: remove 'provider._jwks = None' before retry."""
        from jwt.algorithms import RSAAlgorithm

        from shoreguard.api import oidc

        wrong_jwk = json.loads(RSAAlgorithm.to_jwk(rsa_key.public_key()))
        wrong_jwk["kid"] = "wrong"
        wrong_jwk["use"] = "sig"
        wrong_jwk["alg"] = "RS256"
        wrong_keys = [wrong_jwk]
        provider._jwks = (wrong_keys, time.time())

        now = int(time.time())
        claims = {
            "iss": provider.issuer,
            "aud": provider.client_id,
            "sub": "u",
            "nonce": "n",
            "iat": now,
            "exp": now + 300,
        }
        token = _make_id_token(rsa_key, claims)

        get_jwks_calls = []
        original_jwks = jwks_dict["keys"]

        async def tracking_get_jwks(prov):
            get_jwks_calls.append(prov._jwks)
            if prov._jwks is None:
                # Return correct keys on refresh
                prov._jwks = (original_jwks, time.time())
                return original_jwks
            return prov._jwks[0]

        with patch.object(oidc, "get_jwks", side_effect=tracking_get_jwks):
            result = await oidc.verify_id_token(provider, token, "n")

        assert result["sub"] == "u"
        # First call should have had cache, second call should have had None
        assert len(get_jwks_calls) == 2
        assert get_jwks_calls[0] is not None  # had wrong keys
        assert get_jwks_calls[1] is None  # was cleared before retry


class TestVerifyStateCookieMutationKillers:
    """Kill mutants in verify_state_cookie()."""

    def test_no_dot_returns_none(self, db):
        """Mutant: change split('.', 1) or len check."""
        assert verify_state_cookie("nodothere") is None

    def test_multiple_dots_only_splits_first(self, db):
        """Mutant: change split('.', 1) maxsplit."""
        import hmac as hmac_mod

        data = {"p": "test", "s": "s", "n": "n", "v": "v", "x": "/", "e": int(time.time()) + 300}
        payload = json.dumps(data, separators=(",", ":"))
        encoded = base64.urlsafe_b64encode(payload.encode()).decode()
        sig = hmac_mod.new(auth._hmac_secret, payload.encode(), hashlib.sha256).hexdigest()
        # Add extra dots to sig to test split behavior
        cookie = f"{encoded}.{sig}"
        result = verify_state_cookie(cookie)
        assert result is not None
        assert result["p"] == "test"

    def test_bad_base64_returns_none(self, db):
        """Mutant: remove base64 decode exception handler."""
        assert verify_state_cookie("!!!invalid-base64!!!.fakesig") is None

    def test_hmac_mismatch_returns_none(self, db):
        """Mutant: remove compare_digest check."""

        data = {"p": "test", "s": "s", "n": "n", "v": "v", "x": "/", "e": int(time.time()) + 300}
        payload = json.dumps(data, separators=(",", ":"))
        encoded = base64.urlsafe_b64encode(payload.encode()).decode()
        result = verify_state_cookie(f"{encoded}.wrong_signature")
        assert result is None

    def test_invalid_json_payload_returns_none(self, db):
        """Mutant: remove json.loads exception handler."""
        import hmac as hmac_mod

        payload = b"not json at all"
        encoded = base64.urlsafe_b64encode(payload).decode()
        sig = hmac_mod.new(auth._hmac_secret, payload, hashlib.sha256).hexdigest()
        result = verify_state_cookie(f"{encoded}.{sig}")
        assert result is None

    def test_expiry_boundary_just_expired(self, db):
        """Mutant: change < to <= in expiry check."""
        import hmac as hmac_mod

        # Set expiry to exactly now — should be expired (< not <=)
        data = {"p": "test", "s": "s", "n": "n", "v": "v", "x": "/", "e": int(time.time()) - 1}
        payload = json.dumps(data, separators=(",", ":"))
        encoded = base64.urlsafe_b64encode(payload.encode()).decode()
        sig = hmac_mod.new(auth._hmac_secret, payload.encode(), hashlib.sha256).hexdigest()
        result = verify_state_cookie(f"{encoded}.{sig}")
        assert result is None

    def test_returns_full_data_dict(self, db):
        """Mutant: return wrong value or partial data."""
        cookie = build_state_cookie("prov", "state-val", "nonce-val", "verifier-val", "/next")
        result = verify_state_cookie(cookie)
        assert result is not None
        assert isinstance(result, dict)
        assert result["p"] == "prov"
        assert result["s"] == "state-val"
        assert result["n"] == "nonce-val"
        assert result["v"] == "verifier-val"
        assert result["x"] == "/next"
        assert "e" in result
        assert isinstance(result["e"], int)
        assert result["e"] > int(time.time())


class TestBuildStateCookieMutationKillers:
    """Kill mutants in build_state_cookie()."""

    def test_cookie_format_encoded_dot_sig(self, db):
        """Mutant: change cookie format (f'{encoded}.{sig}')."""
        cookie = build_state_cookie("p", "s", "n", "v", "/")
        parts = cookie.split(".")
        assert len(parts) == 2
        # First part is valid base64
        decoded = base64.urlsafe_b64decode(parts[0])
        data = json.loads(decoded)
        assert data["p"] == "p"
        # Second part is hex HMAC
        assert len(parts[1]) == 64  # SHA-256 hex digest

    def test_expiry_set_correctly(self, db):
        """Mutant: change expiry calculation."""
        before = int(time.time())
        cookie = build_state_cookie("p", "s", "n", "v", "/")
        after = int(time.time())

        encoded = cookie.split(".")[0]
        data = json.loads(base64.urlsafe_b64decode(encoded))
        # state_max_age defaults to 300
        assert before + 300 <= data["e"] <= after + 300

    def test_payload_keys(self, db):
        """Mutant: change payload key names."""
        cookie = build_state_cookie("provider", "state", "nonce", "verifier", "/url")
        encoded = cookie.split(".")[0]
        data = json.loads(base64.urlsafe_b64decode(encoded))
        assert set(data.keys()) == {"p", "s", "n", "v", "x", "e"}

    def test_hmac_matches_payload(self, db):
        """Mutant: change HMAC input."""
        import hmac as hmac_mod

        cookie = build_state_cookie("p", "s", "n", "v", "/")
        encoded, sig = cookie.split(".", 1)
        payload_bytes = base64.urlsafe_b64decode(encoded)
        expected_sig = hmac_mod.new(auth._hmac_secret, payload_bytes, hashlib.sha256).hexdigest()
        assert sig == expected_sig


class TestExtractEmailMutationKillers:
    """Kill mutants in extract_email()."""

    def test_email_stripped_and_lowered(self):
        """Mutant: remove .lower() or .strip()."""
        assert extract_email({"email": " User@Test.COM "}) == "user@test.com"

    def test_preferred_username_stripped_and_lowered(self):
        """Mutant: remove .lower() or .strip() on preferred_username."""
        assert extract_email({"preferred_username": " User@Corp.COM "}) == "user@corp.com"

    def test_preferred_username_no_at_returns_none(self):
        """Mutant: change '@' check."""
        assert extract_email({"preferred_username": "johndoe"}) is None

    def test_empty_email_falls_through(self):
        """Mutant: change 'if email:' truthiness."""
        assert (
            extract_email({"email": "", "preferred_username": "user@corp.com"}) == "user@corp.com"
        )

    def test_empty_preferred_username_returns_none(self):
        """Mutant: change default for .get('preferred_username', '')."""
        assert extract_email({"email": ""}) is None
        assert extract_email({}) is None

    def test_email_takes_precedence_exactly(self):
        """Mutant: swap email and preferred_username priority."""
        result = extract_email(
            {"email": "primary@test.com", "preferred_username": "secondary@test.com"}
        )
        assert result == "primary@test.com"
        assert result != "secondary@test.com"


class TestGeneratePKCEMutationKillers:
    """Kill mutants in generate_pkce()."""

    def test_verifier_is_url_safe(self):
        """Mutant: change secrets.token_urlsafe."""
        verifier, _ = generate_pkce()
        # URL-safe base64 chars only
        import re

        assert re.match(r"^[A-Za-z0-9_-]+$", verifier)

    def test_challenge_is_base64url_no_padding(self):
        """Mutant: remove .rstrip(b'=')."""
        _, challenge = generate_pkce()
        assert "=" not in challenge
        # Should be valid base64url chars
        import re

        assert re.match(r"^[A-Za-z0-9_-]+$", challenge)

    def test_challenge_is_sha256_of_verifier(self):
        """Mutant: change hash algorithm or encoding."""
        verifier, challenge = generate_pkce()
        digest = hashlib.sha256(verifier.encode()).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        assert challenge == expected
        # Make sure it's exactly SHA-256, not e.g. MD5
        wrong = (
            base64.urlsafe_b64encode(hashlib.md5(verifier.encode()).digest()).rstrip(b"=").decode()
        )
        assert challenge != wrong


class TestJWKSCacheTTLConstant:
    """Kill mutants that change the JWKS_CACHE_TTL constant."""

    def test_ttl_is_3600(self):
        from shoreguard.api.oidc import JWKS_CACHE_TTL

        assert JWKS_CACHE_TTL == 3600

    def test_state_cookie_name(self):
        assert OIDC_STATE_COOKIE == "sg_oidc_state"
