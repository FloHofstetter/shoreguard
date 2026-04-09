"""Generic OpenID Connect client for ShoreGuard.

Supports any OIDC-compliant provider (Google, Microsoft Entra, Okta, Keycloak, …)
via a single implementation.  Providers are configured as a JSON array in the
``SHOREGUARD_OIDC_PROVIDERS_JSON`` environment variable.

Security features:
- PKCE (S256) on every authorization request
- HMAC-signed state cookie (stateless, no DB/memory cleanup needed)
- Nonce validation to prevent replay attacks
- JWT signature verification via provider JWKS
- Issuer and audience checks
"""

from __future__ import annotations

import base64
import hashlib
import hmac as hmac_mod
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx
import jwt

from shoreguard.api.validation import DomainValidationError, validate_webhook_url

logger = logging.getLogger(__name__)

JWKS_CACHE_TTL = 3600  # 1 hour


def _check_oidc_url(url: str, *, field: str) -> None:
    """Reject OIDC endpoint URLs that point to private/loopback addresses.

    Protects against SSRF via a malicious or compromised OIDC provider that
    could direct discovery/JWKS/token requests to internal services (e.g.
    cloud metadata endpoints).  Reuses :func:`validate_url` which honours
    ``server.local_mode``.

    Args:
        url: The URL to validate.
        field: Logical name of the URL (``issuer``, ``jwks_uri``,
            ``token_endpoint``) for logging.

    Raises:
        DomainValidationError: If the URL points to a private address.
    """
    try:
        validate_webhook_url(url)
    except DomainValidationError:
        logger.warning("OIDC %s points to private address — rejected: %s", field, url)
        raise


# ─── Provider dataclass ───────────────────────────────────────────────────────


@dataclass
class OIDCProvider:
    """A configured OIDC identity provider.

    Attributes:
        name (str): Unique machine name used to look up the provider.
        display_name (str): Human-readable label shown in the UI.
        issuer (str): OIDC issuer URL (no trailing slash).
        client_id (str): OAuth2 client identifier registered with the provider.
        client_secret (str): OAuth2 client secret used for token exchange.
        scopes (list[str]): OAuth2 scopes requested at the authorize endpoint.
        role_mapping (dict | None): Optional mapping from claim values to ShoreGuard roles.
    """

    name: str
    display_name: str
    issuer: str
    client_id: str
    client_secret: str
    scopes: list[str] = field(default_factory=lambda: ["openid", "email", "profile"])
    role_mapping: dict | None = None
    # Lazy-cached discovery and JWKS
    _discovery: dict | None = field(default=None, repr=False)
    _jwks: tuple[list, float] | None = field(default=None, repr=False)


# ─── Module state ─────────────────────────────────────────────────────────────

_providers: dict[str, OIDCProvider] = {}


def init_oidc() -> None:
    """Parse provider config from settings and populate the registry.

    Called once from the application lifespan.  Does NOT eagerly fetch
    discovery documents — that happens lazily on first use.
    """
    from shoreguard.settings import get_settings

    _providers.clear()
    raw = get_settings().oidc.providers_json
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Invalid OIDC providers JSON — ignoring")
        return
    if not isinstance(entries, list):
        logger.error("OIDC providers_json must be a JSON array — ignoring")
        return

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not name or not entry.get("issuer") or not entry.get("client_id"):
            logger.warning("Skipping OIDC provider with missing name/issuer/client_id")
            continue
        provider = OIDCProvider(
            name=name,
            display_name=entry.get("display_name", name),
            issuer=entry["issuer"].rstrip("/"),
            client_id=entry["client_id"],
            client_secret=entry.get("client_secret", ""),
            scopes=entry.get("scopes", ["openid", "email", "profile"]),
            role_mapping=entry.get("role_mapping"),
        )
        _providers[name] = provider
        logger.info("Registered OIDC provider: %s (%s)", name, provider.issuer)


def reset_oidc() -> None:
    """Clear all provider state.  For test teardown."""
    _providers.clear()


def get_providers() -> list[OIDCProvider]:
    """Return all configured OIDC providers.

    Returns:
        list[OIDCProvider]: All providers currently registered in the module.
    """
    return list(_providers.values())


def get_provider(name: str) -> OIDCProvider | None:
    """Look up a provider by name.

    Args:
        name: Machine name of the provider to look up.

    Returns:
        OIDCProvider | None: The matching provider or ``None`` if not found.
    """
    return _providers.get(name)


# ─── Discovery & JWKS ────────────────────────────────────────────────────────


async def discover(provider: OIDCProvider) -> dict:
    """Fetch and cache the provider's OpenID Connect discovery document.

    Args:
        provider: The provider whose discovery document to fetch.

    Returns:
        dict: The parsed OpenID discovery JSON document.
    """
    if provider._discovery is not None:
        return provider._discovery
    url = f"{provider.issuer}/.well-known/openid-configuration"
    _check_oidc_url(url, field="issuer")
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    discovery: dict = resp.json()
    provider._discovery = discovery
    return discovery


async def get_jwks(provider: OIDCProvider) -> list[dict]:
    """Fetch and cache the provider's JSON Web Key Set (1-hour TTL).

    Args:
        provider: The provider whose JWKS to fetch.

    Returns:
        list[dict]: The list of JWK dicts advertised by the provider.
    """
    now = time.time()
    if provider._jwks and (now - provider._jwks[1]) < JWKS_CACHE_TTL:
        return provider._jwks[0]
    disco = await discover(provider)
    jwks_uri = disco["jwks_uri"]
    _check_oidc_url(jwks_uri, field="jwks_uri")
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(jwks_uri)
        resp.raise_for_status()
    keys = resp.json().get("keys", [])
    provider._jwks = (keys, now)
    return keys


# ─── PKCE helpers ─────────────────────────────────────────────────────────────


def generate_pkce() -> tuple[str, str]:
    """Generate a PKCE code verifier and S256 challenge.

    Returns:
        tuple[str, str]: ``(code_verifier, code_challenge)`` pair.
    """
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ─── Authorization URL ────────────────────────────────────────────────────────


async def build_authorize_url(
    provider: OIDCProvider,
    redirect_uri: str,
    state: str,
    nonce: str,
    code_challenge: str,
) -> str:
    """Build the authorization endpoint URL with PKCE.

    Args:
        provider: The provider to authorize against.
        redirect_uri: Callback URL registered with the provider.
        state: Opaque CSRF state value.
        nonce: Nonce to embed in the ID token for replay protection.
        code_challenge: PKCE S256 code challenge.

    Returns:
        str: Fully formed authorization URL with query parameters.
    """
    disco = await discover(provider)
    params = {
        "response_type": "code",
        "client_id": provider.client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(provider.scopes),
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{disco['authorization_endpoint']}?{urlencode(params)}"


# ─── Token exchange ───────────────────────────────────────────────────────────


async def exchange_code(
    provider: OIDCProvider,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict:
    """Exchange an authorization code for tokens at the provider's token endpoint.

    Args:
        provider: The provider to exchange the code with.
        code: Authorization code returned from the authorize step.
        redirect_uri: Same redirect URI used in the authorize request.
        code_verifier: PKCE verifier that matches the original challenge.

    Returns:
        dict: Parsed JSON token response from the provider.
    """
    disco = await discover(provider)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": provider.client_id,
        "client_secret": provider.client_secret,
        "code_verifier": code_verifier,
    }
    token_endpoint = disco["token_endpoint"]
    _check_oidc_url(token_endpoint, field="token_endpoint")
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            token_endpoint,
            data=data,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
    return resp.json()


# ─── ID token verification ───────────────────────────────────────────────────


async def verify_id_token(provider: OIDCProvider, id_token: str, nonce: str) -> dict:
    """Decode and verify an ID token JWT using the provider's JWKS.

    Checks: signature, issuer, audience, expiry, nonce.

    Args:
        provider: Provider whose JWKS and issuer/audience to check against.
        id_token: The JWT ID token received from the token endpoint.
        nonce: Expected nonce value embedded in the token.

    Returns:
        dict: Decoded claims dict.

    Raises:
        jwt.PyJWTError: On any verification failure.
    """
    keys = await get_jwks(provider)
    jwks = jwt.PyJWKSet.from_dict({"keys": keys})
    # Decode header to find the key ID
    header = jwt.get_unverified_header(id_token)
    kid = header.get("kid")
    signing_key = None
    for key in jwks.keys:
        if key.key_id == kid:
            signing_key = key
            break
    if signing_key is None:
        # Refresh JWKS once and retry (key rotation)
        provider._jwks = None
        keys = await get_jwks(provider)
        jwks = jwt.PyJWKSet.from_dict({"keys": keys})
        for key in jwks.keys:
            if key.key_id == kid:
                signing_key = key
                break
    if signing_key is None:
        msg = f"No matching key found for kid={kid}"
        raise jwt.PyJWTError(msg)

    claims = jwt.decode(
        id_token,
        signing_key,
        algorithms=["RS256", "ES256"],
        audience=provider.client_id,
        issuer=provider.issuer,
        leeway=30,
    )
    # Verify nonce
    if claims.get("nonce") != nonce:
        msg = "Nonce mismatch"
        raise jwt.PyJWTError(msg)
    return claims


# ─── Claim helpers ────────────────────────────────────────────────────────────


def extract_email(claims: dict) -> str | None:
    """Extract an email address from OIDC claims.

    Prefers ``email``, falls back to ``preferred_username`` if it looks
    like an email address.

    Args:
        claims: Decoded ID token claims.

    Returns:
        str | None: The normalized email address, or ``None`` if not present.
    """
    email = claims.get("email")
    if email:
        return email.lower().strip()
    pref = claims.get("preferred_username", "")
    if "@" in pref:
        return pref.lower().strip()
    return None


def map_role(provider: OIDCProvider, claims: dict) -> str:
    """Map OIDC claims to a ShoreGuard role using the provider's role_mapping.

    Falls back to the configured ``default_role`` if no mapping matches.

    Args:
        provider: Provider whose ``role_mapping`` is applied.
        claims: Decoded ID token claims.

    Returns:
        str: The resolved ShoreGuard role name.
    """
    from shoreguard.settings import get_settings

    default = get_settings().oidc.default_role
    mapping = provider.role_mapping
    if not mapping:
        return default
    claim_name = mapping.get("claim", "")
    values_map = mapping.get("values", {})
    if not claim_name or not values_map:
        return default
    claim_value = claims.get(claim_name)
    if claim_value is None:
        return default
    # claim_value can be a string or a list of strings (e.g. groups)
    if isinstance(claim_value, str):
        claim_value = [claim_value]
    if not isinstance(claim_value, list):
        return default
    # Return the highest-ranking matched role
    from shoreguard.api.auth import _ROLE_RANK

    best_role = default
    best_rank = _ROLE_RANK.get(default, 0)
    for val in claim_value:
        role = values_map.get(str(val))
        if role and _ROLE_RANK.get(role, 0) > best_rank:
            best_role = role
            best_rank = _ROLE_RANK[role]
    return best_role


# ─── State cookie (HMAC-signed, stateless) ───────────────────────────────────

OIDC_STATE_COOKIE = "sg_oidc_state"


def _get_hmac_secret() -> bytes:
    """Get the HMAC secret from the auth module.

    Returns:
        bytes: The shared HMAC secret used to sign OIDC state cookies.
    """
    from shoreguard.api.auth import _hmac_secret

    return _hmac_secret


def build_state_cookie(
    provider_name: str,
    state: str,
    nonce: str,
    code_verifier: str,
    next_url: str,
) -> str:
    """Build an HMAC-signed state cookie for the OIDC flow.

    Contains all values needed to verify the callback: provider name,
    state, nonce, PKCE verifier, redirect target, and expiry.

    Args:
        provider_name: Machine name of the provider handling the flow.
        state: Opaque CSRF state value.
        nonce: Nonce to match against the ID token.
        code_verifier: PKCE code verifier.
        next_url: URL to redirect the user to after successful login.

    Returns:
        str: The encoded ``payload.signature`` cookie value.
    """
    from shoreguard.settings import get_settings

    expiry = int(time.time()) + get_settings().oidc.state_max_age
    data = {
        "p": provider_name,
        "s": state,
        "n": nonce,
        "v": code_verifier,
        "x": next_url,
        "e": expiry,
    }
    payload = json.dumps(data, separators=(",", ":"))
    encoded = base64.urlsafe_b64encode(payload.encode()).decode()
    sig = hmac_mod.new(_get_hmac_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{sig}"


def verify_state_cookie(cookie_value: str) -> dict | None:
    """Verify an HMAC-signed state cookie and return its payload.

    Args:
        cookie_value: The raw ``payload.signature`` cookie string.

    Returns:
        dict | None: Parsed payload dict with keys ``p``, ``s``, ``n``, ``v``, ``x``,
        or ``None`` if verification fails.
    """
    parts = cookie_value.split(".", 1)
    if len(parts) != 2:
        return None
    encoded, sig = parts
    try:
        payload_bytes = base64.urlsafe_b64decode(encoded)
    except Exception:
        return None
    expected = hmac_mod.new(_get_hmac_secret(), payload_bytes, hashlib.sha256).hexdigest()
    if not hmac_mod.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(payload_bytes)
    except json.JSONDecodeError:
        return None
    # Check expiry
    if data.get("e", 0) < int(time.time()):
        return None
    return data
