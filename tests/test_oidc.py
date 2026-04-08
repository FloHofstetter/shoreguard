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
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
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
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
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

        new_sig = hmac_mod.new(
            auth._hmac_secret, new_payload.encode(), hashlib.sha256
        ).hexdigest()
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
        assert extract_email({"email": "User@Test.COM", "preferred_username": "other"}) == "user@test.com"

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
            name="test", display_name="Test", issuer="https://idp.example.com",
            client_id="c", client_secret="s",
            role_mapping={"claim": "groups", "values": {"sg-admins": "admin", "sg-ops": "operator"}},
        )
        assert map_role(p, {"groups": ["sg-admins"]}) == "admin"
        assert map_role(p, {"groups": ["sg-ops"]}) == "operator"
        assert map_role(p, {"groups": ["other"]}) == "viewer"

    def test_highest_role_wins(self):
        p = OIDCProvider(
            name="test", display_name="Test", issuer="https://idp.example.com",
            client_id="c", client_secret="s",
            role_mapping={"claim": "groups", "values": {"ops": "operator", "admins": "admin"}},
        )
        assert map_role(p, {"groups": ["ops", "admins"]}) == "admin"

    def test_string_claim(self):
        p = OIDCProvider(
            name="test", display_name="Test", issuer="https://idp.example.com",
            client_id="c", client_secret="s",
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
            "iss": provider.issuer, "aud": provider.client_id,
            "sub": "u", "nonce": "correct", "iat": now, "exp": now + 300,
        }
        token = _make_id_token(rsa_key, claims)
        provider._jwks = (jwks_dict["keys"], time.time())

        from shoreguard.api import oidc

        with pytest.raises(pyjwt.PyJWTError, match="Nonce mismatch"):
            await oidc.verify_id_token(provider, token, "wrong-nonce")

    async def test_bad_issuer(self, provider, rsa_key, jwks_dict):
        now = int(time.time())
        claims = {
            "iss": "https://evil.com", "aud": provider.client_id,
            "sub": "u", "nonce": "n", "iat": now, "exp": now + 300,
        }
        token = _make_id_token(rsa_key, claims)
        provider._jwks = (jwks_dict["keys"], time.time())

        from shoreguard.api import oidc

        with pytest.raises(pyjwt.exceptions.InvalidIssuerError):
            await oidc.verify_id_token(provider, token, "n")

    async def test_expired_token(self, provider, rsa_key, jwks_dict):
        now = int(time.time())
        claims = {
            "iss": provider.issuer, "aud": provider.client_id,
            "sub": "u", "nonce": "n", "iat": now - 600, "exp": now - 300,
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
    providers = json.dumps([{
        "name": "test",
        "display_name": "Test IdP",
        "issuer": "https://idp.example.com",
        "client_id": "test-client-id",
        "client_secret": "test-secret",
    }])
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
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
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
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
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
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                resp = await c.get("/api/auth/oidc/login/nonexistent")
                assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()
            reset_oidc()
            reset_settings()


class TestOIDCCallback:
    async def _do_callback(
        self, db, mock_client, monkeypatch, rsa_key, jwks_dict, *,
        existing_email=None, existing_oidc=False,
    ):
        """Helper: run a full OIDC callback flow with mocked provider."""
        from shoreguard.api.deps import get_client
        from shoreguard.api.main import app
        from shoreguard.api import oidc
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
            user = create_user(existing_email, "password123", "operator")
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
            db, mock_client, monkeypatch, rsa_key, jwks_dict,
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
            db, mock_client, monkeypatch, rsa_key, jwks_dict,
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
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
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
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
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
