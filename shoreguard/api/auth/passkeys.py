"""WebAuthn passkey registration and login.

Passkeys are the homelab-friendly login: register once with the phone
or laptop authenticator, never type a password on the PWA again. The
flow is the standard two-step WebAuthn dance — the server issues
options with a one-time challenge, the browser answers with a signed
credential, the server verifies and (for login) mints the same session
token password login uses.

Challenges live in an in-process TTL store keyed by an opaque state
token; ShoreGuard is a single process, so no shared store is needed.
The relying-party ID defaults to the host of ``SHOREGUARD_PUBLIC_URL``
(or the request host) and can be pinned via ``SHOREGUARD_PASSKEY_RP_ID``.
Browsers require a secure context — HTTPS or localhost; `tailscale
serve` provides exactly that.
"""

from __future__ import annotations

import datetime
import json
import logging
import secrets
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from sqlalchemy import select

from shoreguard.api.auth.core import state
from shoreguard.models import User, WebAuthnCredential

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)

_CHALLENGE_TTL_S = 300.0

# state token -> (challenge bytes, user_id or None for login, expiry)
_challenges: dict[str, tuple[bytes, int | None, float]] = {}


def _store_challenge(challenge: bytes, user_id: int | None) -> str:
    """Stash a one-time challenge and return its opaque state token.

    Args:
        challenge: The WebAuthn challenge bytes.
        user_id: Owning user for registration, ``None`` for login.

    Returns:
        str: The state token the client must echo back.
    """
    now = time.monotonic()
    for key in [k for k, (_, _, exp) in _challenges.items() if exp < now]:
        _challenges.pop(key, None)
    token = secrets.token_urlsafe(24)
    _challenges[token] = (challenge, user_id, now + _CHALLENGE_TTL_S)
    return token


def _pop_challenge(token: str) -> tuple[bytes, int | None] | None:
    """Consume a stored challenge (single use).

    Args:
        token: The state token issued with the options.

    Returns:
        tuple[bytes, int | None] | None: ``(challenge, user_id)`` or
        ``None`` when unknown or expired.
    """
    entry = _challenges.pop(token, None)
    if entry is None or entry[2] < time.monotonic():
        return None
    return entry[0], entry[1]


def _rp_id(request: Request) -> str:
    """Resolve the relying-party ID for this request.

    Args:
        request: The incoming HTTP request.

    Returns:
        str: Hostname from the override setting, public_url, or request.
    """
    from shoreguard.settings import get_settings

    settings = get_settings()
    if settings.auth.passkey_rp_id:
        return settings.auth.passkey_rp_id
    public_url = settings.server.public_url
    if public_url:
        host = urlsplit(public_url).hostname
        if host:
            return host
    return request.url.hostname or "localhost"


def _expected_origins(request: Request) -> list[str]:
    """Resolve the accepted WebAuthn origins for this request.

    Args:
        request: The incoming HTTP request.

    Returns:
        list[str]: Origins derived from public_url and the request URL.
    """
    from shoreguard.settings import get_settings

    origins: list[str] = []
    public_url = get_settings().server.public_url
    if public_url:
        parts = urlsplit(public_url)
        if parts.scheme and parts.netloc:
            origins.append(f"{parts.scheme}://{parts.netloc}")
    request_origin = f"{request.url.scheme}://{request.url.netloc}"
    if request_origin not in origins:
        origins.append(request_origin)
    return origins


async def registration_options(user_id: int, email: str, request: Request) -> dict[str, Any]:
    """Build WebAuthn registration options for the logged-in user.

    Args:
        user_id: Database ID of the user registering a passkey.
        email: The user's email (used as the WebAuthn user name).
        request: The incoming HTTP request (relying-party derivation).

    Returns:
        dict[str, Any]: ``{"options": <WebAuthn JSON>, "state": token}``.
    """
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers import base64url_to_bytes
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
    )

    existing = await list_credentials(user_id)
    options = generate_registration_options(
        rp_id=_rp_id(request),
        rp_name="ShoreGuard",
        user_name=email,
        user_id=str(user_id).encode(),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["credential_id"]))
            for c in existing
        ],
    )
    token = _store_challenge(options.challenge, user_id)
    return {"options": json.loads(options_to_json(options)), "state": token}


async def verify_registration(
    *, state_token: str, credential: dict[str, Any], name: str, user_id: int, request: Request
) -> dict[str, Any]:
    """Verify a registration response and store the new passkey.

    Args:
        state_token: The state token from :func:`registration_options`.
        credential: The browser's ``PublicKeyCredential.toJSON()`` output.
        name: Operator-given device label.
        user_id: Database ID of the registering user.
        request: The incoming HTTP request.

    Returns:
        dict[str, Any]: The stored credential record.

    Raises:
        ValueError: If the challenge is unknown/expired, belongs to a
            different user, or the credential fails verification.
    """
    from webauthn import verify_registration_response
    from webauthn.helpers import bytes_to_base64url
    from webauthn.helpers.exceptions import InvalidRegistrationResponse

    entry = _pop_challenge(state_token)
    if entry is None or entry[1] != user_id:
        raise ValueError("Registration challenge is unknown or expired — try again")
    challenge, _ = entry

    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=_rp_id(request),
            expected_origin=_expected_origins(request),
        )
    except InvalidRegistrationResponse as e:
        raise ValueError(f"Passkey registration failed verification: {e}") from e

    if state.session_factory is None:
        raise ValueError("Auth database not initialised")
    transports = credential.get("response", {}).get("transports")
    async with state.session_factory() as session:
        row = WebAuthnCredential(
            user_id=user_id,
            credential_id=bytes_to_base64url(verified.credential_id),
            public_key=bytes_to_base64url(verified.credential_public_key),
            sign_count=verified.sign_count,
            transports=json.dumps(transports) if transports else None,
            name=name[:100] or "passkey",
            created_at=datetime.datetime.now(datetime.UTC),
        )
        session.add(row)
        await session.commit()
        logger.info("Passkey registered for user %d (%s)", user_id, row.name)
        return _to_dict(row)


async def authentication_options(request: Request) -> dict[str, Any]:
    """Build WebAuthn authentication options (discoverable credentials).

    Args:
        request: The incoming HTTP request.

    Returns:
        dict[str, Any]: ``{"options": <WebAuthn JSON>, "state": token}``.
    """
    from webauthn import generate_authentication_options, options_to_json

    options = generate_authentication_options(rp_id=_rp_id(request))
    token = _store_challenge(options.challenge, None)
    return {"options": json.loads(options_to_json(options)), "state": token}


async def verify_authentication(
    *, state_token: str, credential: dict[str, Any], request: Request
) -> dict[str, Any]:
    """Verify a login assertion and return the authenticated user.

    Args:
        state_token: The state token from :func:`authentication_options`.
        credential: The browser's assertion (``toJSON()`` output).
        request: The incoming HTTP request.

    Returns:
        dict[str, Any]: ``{"id", "email", "role"}`` of the user.

    Raises:
        ValueError: If the challenge or credential is unknown, the user
            is inactive, or verification fails.
    """
    from webauthn import verify_authentication_response
    from webauthn.helpers import base64url_to_bytes
    from webauthn.helpers.exceptions import InvalidAuthenticationResponse

    entry = _pop_challenge(state_token)
    if entry is None:
        raise ValueError("Login challenge is unknown or expired — try again")
    challenge, _ = entry

    credential_id = str(credential.get("id") or "")
    if not credential_id:
        raise ValueError("Credential id missing from assertion")

    if state.session_factory is None:
        raise ValueError("Auth database not initialised")
    async with state.session_factory() as session:
        row = (
            await session.execute(
                select(WebAuthnCredential).where(WebAuthnCredential.credential_id == credential_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError("Unknown passkey")
        user = (
            await session.execute(select(User).where(User.id == row.user_id))
        ).scalar_one_or_none()
        if user is None or not user.is_active:
            raise ValueError("User is unknown or inactive")

        try:
            verified = verify_authentication_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=_rp_id(request),
                expected_origin=_expected_origins(request),
                credential_public_key=base64url_to_bytes(row.public_key),
                credential_current_sign_count=row.sign_count,
            )
        except InvalidAuthenticationResponse as e:
            raise ValueError(f"Passkey login failed verification: {e}") from e

        row.sign_count = verified.new_sign_count
        row.last_used = datetime.datetime.now(datetime.UTC)
        await session.commit()
        logger.info("Passkey login for user %d (%s)", user.id, row.name)
        return {"id": user.id, "email": user.email, "role": user.role}


async def list_credentials(user_id: int) -> list[dict[str, Any]]:
    """List a user's registered passkeys.

    Args:
        user_id: Database ID of the user.

    Returns:
        list[dict[str, Any]]: Credential records, oldest first.
    """
    if state.session_factory is None:
        return []
    async with state.session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(WebAuthnCredential)
                    .where(WebAuthnCredential.user_id == user_id)
                    .order_by(WebAuthnCredential.id)
                )
            )
            .scalars()
            .all()
        )
        return [_to_dict(r) for r in rows]


async def delete_credential(user_id: int, credential_pk: int) -> bool:
    """Delete one of the user's passkeys.

    Args:
        user_id: Database ID of the calling user (ownership check).
        credential_pk: Primary key of the credential to delete.

    Returns:
        bool: ``True`` if a credential was deleted.
    """
    if state.session_factory is None:
        return False
    async with state.session_factory() as session:
        row = (
            await session.execute(
                select(WebAuthnCredential).where(
                    WebAuthnCredential.id == credential_pk,
                    WebAuthnCredential.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
        return True


def _to_dict(row: WebAuthnCredential) -> dict[str, Any]:
    """Serialize a credential row for the API.

    Args:
        row: The ORM row.

    Returns:
        dict[str, Any]: Display-safe record (no public key material).
    """
    return {
        "id": row.id,
        "credential_id": row.credential_id,
        "name": row.name,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_used": row.last_used.isoformat() if row.last_used else None,
    }
