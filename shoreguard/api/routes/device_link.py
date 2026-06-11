"""Device-link sign-in handoff endpoints (QR "Open on phone").

Four endpoints implement a deliberately conservative handoff:

* ``POST /api/auth/device-link`` (session required) mints a one-time
  code for the logged-in operator.
* ``POST /api/auth/device-link/redeem`` (anonymous, same-origin) is
  polled by the *phone*: the first call claims the code, later calls
  report progress, and once approved it mints a fresh, short-lived
  session.
* ``GET /api/auth/device-link/pending`` (session required) lets the
  *issuing* device see requests awaiting its approval.
* ``POST /api/auth/device-link/approve`` (session required) approves or
  denies one of those requests.

The approval step happens on the already-authenticated device, which is
what defends against someone scanning a shoulder-surfed/screen-shared
QR: the legitimate operator would simply deny a request they did not
start. Everything 404s unless the feature is explicitly enabled.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from shoreguard.api.auth import COOKIE_NAME, require_auth, user_sessions
from shoreguard.api.auth import device_link as dl
from shoreguard.api.ratelimit import get_limiter
from shoreguard.api.validation import client_ip
from shoreguard.services.audit import audit_log

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_enabled() -> None:
    """Gate every device-link route behind the feature flag.

    Raises:
        HTTPException: 404 when device-link is disabled.
    """
    from shoreguard.settings import get_settings

    if not get_settings().auth.device_link_enabled:
        raise HTTPException(404, "Device-link sign-in is disabled")


def _require_same_origin(request: Request) -> None:
    """Reject a request the browser flags as cross-site.

    Modern browsers send ``Sec-Fetch-Site`` and it cannot be forged from
    JavaScript, so a cross-origin ``fetch`` that tries to drive the
    anonymous redeem endpoint (login CSRF) is rejected. Non-browser
    clients (no ``Sec-Fetch-Site``) fall back to an ``Origin`` allowlist
    check; absence of both headers is allowed (e.g. ``curl``), since the
    threat being closed is specifically a *browser* being tricked.

    Args:
        request: The incoming HTTP request.

    Raises:
        HTTPException: 403 when the request is positively cross-origin.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        if site not in ("same-origin", "none"):
            raise HTTPException(403, "Cross-origin request rejected")
        return
    origin = request.headers.get("origin")
    if origin:
        expected = f"{request.url.scheme}://{request.url.netloc}"
        if origin != expected:
            raise HTTPException(403, "Cross-origin request rejected")


def _user_db_id(request: Request) -> int:
    """Return the database id of the session-authenticated user.

    Args:
        request: The incoming HTTP request.

    Returns:
        int: The user's database id.

    Raises:
        HTTPException: 400 when the caller has no real user identity
            (service principals, ``--no-auth`` dev bypass).
    """
    user_db_id = getattr(request.state, "user_db_id", None)
    if not isinstance(user_db_id, int):
        raise HTTPException(
            400, "Device-link needs a real user session (not --no-auth or an API key)"
        )
    return user_db_id


@router.post("/api/auth/device-link", dependencies=[Depends(require_auth)])
async def mint_code(request: Request) -> dict[str, Any]:
    """Mint a one-time device-link code for the logged-in operator.

    The handoff session inherits the operator's role — like any login,
    the role is re-read from the database on every request, so a token
    cannot pin a stale or downscoped role. Blast radius is instead
    bounded by a deliberately short session lifetime
    (``device_link_session_max_age``).

    Args:
        request: The incoming HTTP request.

    Returns:
        dict[str, Any]: ``{"id", "code", "expires_at", "role"}`` — the
        plaintext code is encoded into the QR fragment by the caller.
    """
    _require_enabled()
    user_id = _user_db_id(request)
    role = str(getattr(request.state, "role", "viewer"))

    from shoreguard.settings import get_settings

    ttl = get_settings().auth.device_link_ttl
    result = await dl.mint(user_id, role, ttl)
    await audit_log(
        request,
        "auth.device_link.create",
        "user",
        str(getattr(request.state, "user_id", "")),
        detail={"role": role, "code_id": result["id"]},
    )
    return {
        "id": result["id"],
        "code": result["code"],
        "expires_at": result["expires_at"].isoformat(),
        "role": role,
    }


@router.get("/api/auth/device-link/pending", dependencies=[Depends(require_auth)])
async def list_pending(request: Request) -> dict[str, Any]:
    """List device-link requests awaiting this operator's approval.

    Args:
        request: The incoming HTTP request.

    Returns:
        dict[str, Any]: ``{"pending": [...]}`` claimed-but-undecided
        requests issued by the caller.
    """
    _require_enabled()
    user_id = _user_db_id(request)
    return {"pending": await dl.pending_for_user(user_id)}


class DecisionRequest(BaseModel):
    """Request body for approving or denying a device-link request.

    Attributes:
        id: Primary key of the device-link code.
        approve: ``True`` to approve, ``False`` to deny.
    """

    id: int
    approve: bool


@router.post("/api/auth/device-link/approve", dependencies=[Depends(require_auth)])
async def approve_request(request: Request, body: DecisionRequest) -> dict[str, str]:
    """Approve or deny a claimed device-link request.

    Args:
        request: The incoming HTTP request.
        body: The code id and the decision.

    Returns:
        dict[str, str]: ``{"status": "approved" | "denied"}``.

    Raises:
        HTTPException: 404 when no matching claimable request exists.
    """
    _require_enabled()
    user_id = _user_db_id(request)
    outcome = await dl.decide(body.id, user_id, body.approve)
    if outcome == "not_found":
        raise HTTPException(404, "No such pending request")
    await audit_log(
        request,
        f"auth.device_link.{outcome}",
        "user",
        str(getattr(request.state, "user_id", "")),
        detail={"code_id": body.id},
    )
    return {"status": outcome}


class RedeemRequest(BaseModel):
    """Request body for the phone's redeem poll.

    Attributes:
        code: The one-time code read from the QR fragment.
    """

    code: str = Field(min_length=1, max_length=128)


@router.post("/api/auth/device-link/redeem")
async def redeem(request: Request, body: RedeemRequest) -> JSONResponse:
    """Claim, poll, and ultimately consume a device-link code (phone side).

    Anonymous by design — the code is the credential — but same-origin
    only and rate-limited. On approval, mints a fresh short-lived
    session cookie identical in shape to a password login.

    Args:
        request: The incoming HTTP request.
        body: The presented code.

    Returns:
        JSONResponse: ``{"status": ...}``; sets the session cookie when
        status is ``approved``.

    Raises:
        HTTPException: 429 when the client IP is rate-limited.
    """
    _require_enabled()
    _require_same_origin(request)

    limiter = get_limiter("device_link")
    ip = client_ip(request)
    blocked, retry_after = limiter.is_limited(ip)
    if blocked:
        raise HTTPException(
            429, "Too many requests. Try again later.", headers={"Retry-After": str(retry_after)}
        )
    limiter.record(ip)

    user_agent = request.headers.get("user-agent", "")[:512]
    result = await dl.redeem_poll(body.code, ip, user_agent)
    status = result["status"]

    if status in ("invalid", "expired", "denied", "consumed"):
        # Log the first failure for incident response; the one-tap
        # precedent omits this, but a redeem failure may be an
        # interception/replay attempt and must be visible.
        request.state.user_id = "device-link"
        request.state.role = "anonymous"
        await audit_log(
            request,
            "auth.device_link.redeem_failed",
            "user",
            "device-link",
            detail={"reason": status, "ip": ip},
        )
        return JSONResponse(content={"status": status}, status_code=200)

    if status == "pending":
        return JSONResponse(content={"status": "pending", "email": result.get("email")})

    # status == "approved": mint the handoff session.
    mint = result["mint"]
    email = result.get("email")
    request.state.user_id = email or "device-link"
    request.state.role = mint["role"]
    await audit_log(
        request,
        "auth.device_link.redeem",
        "user",
        email or "device-link",
        detail={"role": mint["role"], "ip": ip},
    )

    from shoreguard.settings import get_settings

    max_age = get_settings().auth.device_link_session_max_age
    token = await user_sessions.create_tracked_session(
        request, mint["user_id"], mint["role"], kind="device-link", max_age=max_age
    )
    response = JSONResponse(content={"status": "approved", "email": email, "role": mint["role"]})
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        max_age=max_age,
        path="/",
    )
    return response
