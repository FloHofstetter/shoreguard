"""Passkey (WebAuthn) endpoints: registration, management, and login.

Management routes require an authenticated session; the login pair is
anonymous by design (the assertion is the credential). All routes 404
when ``SHOREGUARD_PASSKEYS_ENABLED`` is off, mirroring the one-tap
feature gating.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from shoreguard.api.auth import COOKIE_NAME, create_session_token, require_auth
from shoreguard.api.auth import passkeys as pk
from shoreguard.api.ratelimit import get_login_limiter
from shoreguard.api.validation import client_ip
from shoreguard.services.audit import audit_log

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_enabled() -> None:
    """Gate every passkey route behind the feature flag.

    Raises:
        HTTPException: 404 when passkeys are disabled.
    """
    from shoreguard.settings import get_settings

    if not get_settings().auth.passkeys_enabled:
        raise HTTPException(404, "Passkeys are disabled")


def _user_db_id(request: Request) -> int:
    """Return the database ID of the session-authenticated user.

    Args:
        request: The incoming HTTP request.

    Returns:
        int: The user's database ID.

    Raises:
        HTTPException: 400 when the caller has no user identity
            (service principals, --no-auth dev bypass).
    """
    user_db_id = getattr(request.state, "user_db_id", None)
    if not isinstance(user_db_id, int):
        raise HTTPException(400, "Passkeys need a real user session (not --no-auth or an API key)")
    return user_db_id


class RegisterVerifyRequest(BaseModel):
    """Request body for finishing passkey registration.

    Attributes:
        state: The state token issued with the options.
        credential: The browser's ``PublicKeyCredential.toJSON()`` output.
        name: Operator-given device label.
    """

    state: str = Field(max_length=64)
    credential: dict[str, Any]
    name: str = Field(default="passkey", max_length=100)


class LoginVerifyRequest(BaseModel):
    """Request body for finishing passkey login.

    Attributes:
        state: The state token issued with the options.
        credential: The browser's assertion (``toJSON()`` output).
    """

    state: str = Field(max_length=64)
    credential: dict[str, Any]


# ─── Management (session required) ───────────────────────────────────────────


@router.post("/api/auth/passkeys/register/options", dependencies=[Depends(require_auth)])
async def register_options(request: Request) -> dict[str, Any]:
    """Start passkey registration for the logged-in user.

    Args:
        request: The incoming HTTP request.

    Returns:
        dict[str, Any]: WebAuthn creation options plus the state token.
    """
    _require_enabled()
    user_id = _user_db_id(request)
    email = str(getattr(request.state, "user_id", ""))
    return await pk.registration_options(user_id, email, request)


@router.post("/api/auth/passkeys/register/verify", dependencies=[Depends(require_auth)])
async def register_verify(body: RegisterVerifyRequest, request: Request) -> dict[str, Any]:
    """Finish passkey registration and store the credential.

    Args:
        body: State token, credential, and device label.
        request: The incoming HTTP request.

    Returns:
        dict[str, Any]: The stored credential record.

    Raises:
        HTTPException: 400 when verification fails.
    """
    _require_enabled()
    user_id = _user_db_id(request)
    try:
        result = await pk.verify_registration(
            state_token=body.state,
            credential=body.credential,
            name=body.name,
            user_id=user_id,
            request=request,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    await audit_log(request, "user.passkey_registered", "user", str(user_id))
    return result


@router.get("/api/auth/passkeys", dependencies=[Depends(require_auth)])
async def list_passkeys(request: Request) -> list[dict[str, Any]]:
    """List the logged-in user's passkeys.

    Args:
        request: The incoming HTTP request.

    Returns:
        list[dict[str, Any]]: Credential records, oldest first.
    """
    _require_enabled()
    return await pk.list_credentials(_user_db_id(request))


@router.delete("/api/auth/passkeys/{credential_pk}", dependencies=[Depends(require_auth)])
async def delete_passkey(credential_pk: int, request: Request) -> dict[str, str]:
    """Delete one of the logged-in user's passkeys.

    Args:
        credential_pk: Primary key of the credential.
        request: The incoming HTTP request.

    Returns:
        dict[str, str]: Confirmation message.

    Raises:
        HTTPException: 404 when the passkey does not exist or belongs to
            another user.
    """
    _require_enabled()
    user_id = _user_db_id(request)
    if not await pk.delete_credential(user_id, credential_pk):
        raise HTTPException(404, "Passkey not found")
    await audit_log(request, "user.passkey_deleted", "user", str(user_id))
    return {"status": "deleted"}


# ─── Login (anonymous by design) ─────────────────────────────────────────────


@router.post("/api/auth/login/passkey/options")
async def login_options(request: Request) -> dict[str, Any]:
    """Start a passkey login (discoverable credentials).

    Args:
        request: The incoming HTTP request.

    Returns:
        dict[str, Any]: WebAuthn request options plus the state token.
    """
    _require_enabled()
    return await pk.authentication_options(request)


@router.post("/api/auth/login/passkey/verify")
async def login_verify(body: LoginVerifyRequest, request: Request) -> JSONResponse:
    """Finish a passkey login and set the session cookie.

    Args:
        body: State token and assertion.
        request: The incoming HTTP request.

    Returns:
        JSONResponse: Same shape and cookie as password login.

    Raises:
        HTTPException: 401 when the assertion fails verification, 429
            when rate-limited.
    """
    _require_enabled()
    limiter = get_login_limiter()
    ip = client_ip(request)
    blocked, retry_after = limiter.is_limited(ip)
    if blocked:
        raise HTTPException(
            429, "Too many requests. Try again later.", headers={"Retry-After": str(retry_after)}
        )
    limiter.record(ip)

    try:
        user = await pk.verify_authentication(
            state_token=body.state, credential=body.credential, request=request
        )
    except ValueError as e:
        logger.warning("Passkey login failed (client=%s): %s", ip, e)
        raise HTTPException(401, "Passkey login failed") from e

    logger.info("Passkey login successful (client=%s, email=%s)", ip, user["email"])
    request.state.user_id = user["email"]
    request.state.role = user["role"]
    await audit_log(request, "user.login_passkey", "user", user["email"])
    token = create_session_token(user_id=user["id"], role=user["role"])
    response = JSONResponse(content={"ok": True, "role": user["role"], "email": user["email"]})
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=86400 * 7,
        path="/",
    )
    return response
