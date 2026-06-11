"""Session registry: record, list, and revoke a user's active sessions.

Sessions are stateless HMAC cookies, so on their own they cannot be
enumerated or individually revoked. This module is the revocation
ledger that fixes that: every minted session is recorded (keyed by the
SHA-256 of its token nonce), the per-request auth check rejects a
cookie whose nonce is revoked, and the user can list their devices and
kill any of them — without deactivating the account or rotating the
global secret. It is a denylist, not an allowlist: a cookie with no
recorded row still authenticates, so enabling tracking does not log
existing sessions out.
"""

from __future__ import annotations

import datetime
import hashlib
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update

from shoreguard.api.auth.core import mint_session, state
from shoreguard.models import UserSession

if TYPE_CHECKING:
    from fastapi import Request

_TOUCH_INTERVAL = datetime.timedelta(seconds=60)


def _now() -> datetime.datetime:
    """Return the current UTC time.

    Returns:
        datetime.datetime: Timezone-aware current UTC time.
    """
    return datetime.datetime.now(datetime.UTC)


def _hash(nonce: str) -> str:
    """Return the SHA-256 hex digest of a session nonce.

    Args:
        nonce: The token nonce.

    Returns:
        str: Hex-encoded SHA-256 digest used as the stored session id.
    """
    return hashlib.sha256(nonce.encode()).hexdigest()


async def create_tracked_session(
    request: Request,
    user_id: int,
    role: str,
    *,
    kind: str,
    max_age: int | None = None,
) -> str:
    """Mint a session token and record it in the revocation ledger.

    Replaces a bare :func:`create_session_token` call at each login
    site. Recording is best-effort — a ledger failure must never block
    a sign-in — and is skipped entirely when tracking is disabled.

    Args:
        request: The incoming HTTP request (for IP and user-agent).
        user_id: Database id of the authenticated user.
        role: The user's role.
        kind: How the session was created (``password``, ``passkey``, …).
        max_age: Token lifetime in seconds (defaults to ``session_max_age``).

    Returns:
        str: The signed session token to set as the cookie.
    """
    token, nonce, expiry_epoch = mint_session(user_id, role, max_age)
    from shoreguard.settings import get_settings

    if not get_settings().auth.session_tracking or state.session_factory is None:
        return token
    ip = request.client.host if request.client else None
    user_agent = (request.headers.get("user-agent") or "")[:512] or None
    now = _now()
    row = UserSession(
        session_id=_hash(nonce),
        user_id=user_id,
        kind=kind,
        created_at=now,
        last_seen_at=now,
        expires_at=datetime.datetime.fromtimestamp(expiry_epoch, datetime.UTC),
        ip=ip,
        user_agent=user_agent,
    )
    try:
        async with state.session_factory() as session:
            session.add(row)
            await session.commit()
    except Exception:  # noqa: BLE001 — never let the ledger block a login
        import logging

        logging.getLogger(__name__).warning("Failed to record session", exc_info=True)
    return token


async def is_revoked(nonce: str) -> bool:
    """Return whether the session for *nonce* has been revoked.

    Args:
        nonce: The token nonce from the presented cookie.

    Returns:
        bool: ``True`` only if a recorded row exists and is revoked.
        Unrecorded sessions return ``False`` (denylist semantics).
    """
    if state.session_factory is None:
        return False
    async with state.session_factory() as session:
        revoked_at = (
            await session.execute(
                select(UserSession.revoked_at).where(UserSession.session_id == _hash(nonce))
            )
        ).scalar_one_or_none()
        return revoked_at is not None


async def touch(nonce: str) -> None:
    """Bump ``last_seen_at`` for an active session, throttled to 1/min.

    Args:
        nonce: The token nonce from the presented cookie.
    """
    if state.session_factory is None:
        return
    now = _now()
    async with state.session_factory() as session:
        await session.execute(
            update(UserSession)
            .where(
                UserSession.session_id == _hash(nonce),
                UserSession.revoked_at.is_(None),
                UserSession.last_seen_at < now - _TOUCH_INTERVAL,
            )
            .values(last_seen_at=now)
        )
        await session.commit()


async def list_for_user(user_id: int, current_nonce: str | None) -> list[dict[str, Any]]:
    """List a user's active (unrevoked, unexpired) sessions, newest first.

    Args:
        user_id: Database id of the user.
        current_nonce: Nonce of the requesting session, to flag "this device".

    Returns:
        list[dict[str, Any]]: One entry per active session.
    """
    if state.session_factory is None:
        return []
    now = _now()
    current_hash = _hash(current_nonce) if current_nonce else None
    async with state.session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(UserSession)
                    .where(
                        UserSession.user_id == user_id,
                        UserSession.revoked_at.is_(None),
                        UserSession.expires_at > now,
                    )
                    .order_by(UserSession.last_seen_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": r.id,
                "kind": r.kind,
                "ip": r.ip,
                "user_agent": r.user_agent,
                "created_at": r.created_at.isoformat(),
                "last_seen_at": r.last_seen_at.isoformat(),
                "current": r.session_id == current_hash,
            }
            for r in rows
        ]


async def revoke(user_id: int, session_pk: int) -> bool:
    """Revoke one of *user_id*'s sessions by primary key.

    Args:
        user_id: Database id of the owning user (ownership guard).
        session_pk: Primary key of the session row to revoke.

    Returns:
        bool: ``True`` if an active session was revoked.
    """
    if state.session_factory is None:
        return False
    async with state.session_factory() as session:
        res = await session.execute(
            update(UserSession)
            .where(
                UserSession.id == session_pk,
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
            )
            .values(revoked_at=_now())
        )
        await session.commit()
        return getattr(res, "rowcount", 0) == 1


async def revoke_others(user_id: int, current_nonce: str | None) -> int:
    """Revoke all of *user_id*'s sessions except the current one.

    Args:
        user_id: Database id of the user.
        current_nonce: Nonce of the session to keep (``None`` revokes all).

    Returns:
        int: Number of sessions revoked.
    """
    if state.session_factory is None:
        return 0
    async with state.session_factory() as session:
        stmt = update(UserSession).where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
        if current_nonce:
            stmt = stmt.where(UserSession.session_id != _hash(current_nonce))
        res = await session.execute(stmt.values(revoked_at=_now()))
        await session.commit()
        return int(getattr(res, "rowcount", 0) or 0)


async def revoke_by_nonce(nonce: str) -> None:
    """Revoke the session identified by *nonce* (used on logout).

    Args:
        nonce: The token nonce of the session to revoke.
    """
    if state.session_factory is None:
        return
    async with state.session_factory() as session:
        await session.execute(
            update(UserSession)
            .where(UserSession.session_id == _hash(nonce), UserSession.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
        await session.commit()
