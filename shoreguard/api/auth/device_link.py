"""Device-link sign-in handoff: mint, claim, approve, and consume codes.

The QR "Open on phone" dialog can mint a one-time code so a phone gets
its own session without typing a password. The code is never stored in
the clear (only its SHA-256 hash), travels in a URL fragment, and only
becomes a session after the phone claims it AND the issuing operator
approves the request on the original (already-trusted) device. Every
state transition is an atomic conditional UPDATE so replays and races
are impossible, not merely unlikely.

State machine on :class:`~shoreguard.models.DeviceLinkCode`:
``minted -> claimed (redeemed_at) -> approved (approved_at) /
denied (denied_at) -> consumed (consumed_at)``.
"""

from __future__ import annotations

import datetime
import hashlib
import secrets
from typing import Any

from sqlalchemy import select, update

from shoreguard.api.auth.core import state
from shoreguard.models import DeviceLinkCode, User


def _now() -> datetime.datetime:
    """Return the current UTC time.

    Returns:
        datetime.datetime: Timezone-aware current UTC time.
    """
    return datetime.datetime.now(datetime.UTC)


def _hash(code: str) -> str:
    """Return the SHA-256 hex digest of a device-link code.

    Args:
        code: The plaintext one-time code.

    Returns:
        str: Hex-encoded SHA-256 digest.
    """
    return hashlib.sha256(code.encode()).hexdigest()


async def _email_for(session: Any, user_id: int) -> str | None:
    """Look up a user's email by id.

    Args:
        session: An open async DB session.
        user_id: Database id of the user.

    Returns:
        str | None: The email, or ``None`` if the user is gone.
    """
    return (
        await session.execute(select(User.email).where(User.id == user_id))
    ).scalar_one_or_none()


async def mint(user_id: int, role: str, ttl_seconds: int) -> dict[str, Any]:
    """Mint a one-time device-link code for *user_id*.

    Args:
        user_id: Database id of the issuing user.
        role: Role to grant the eventual handoff session.
        ttl_seconds: Seconds the code stays claimable.

    Returns:
        dict[str, Any]: ``{"code", "id", "expires_at"}`` — *code* is the
        plaintext one-time secret (only the hash is stored).

    Raises:
        RuntimeError: If the auth subsystem has no DB session factory.
    """
    if state.session_factory is None:
        raise RuntimeError("auth not initialised")
    code = secrets.token_urlsafe(32)
    now = _now()
    expires_at = now + datetime.timedelta(seconds=ttl_seconds)
    async with state.session_factory() as session:
        row = DeviceLinkCode(
            code_hash=_hash(code),
            user_id=user_id,
            role=role,
            created_at=now,
            expires_at=expires_at,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return {"code": code, "id": row.id, "expires_at": expires_at}


async def redeem_poll(code: str, ip: str | None, user_agent: str | None) -> dict[str, Any]:
    """Advance the redemption state machine for a presented *code*.

    Idempotent per state: the first call by the phone claims the code;
    later calls report progress; once the issuer has approved, the next
    call atomically consumes the code and returns the minting payload.

    Args:
        code: The plaintext code presented by the phone.
        ip: Claiming client IP (recorded on first contact).
        user_agent: Claiming user-agent (recorded on first contact).

    Returns:
        dict[str, Any]: ``{"status": ...}`` where status is one of
        ``invalid``, ``expired``, ``denied``, ``consumed``, ``pending``
        (with ``email``), or ``approved`` (with ``email`` and a ``mint``
        payload ``{"user_id", "role"}`` the caller turns into a session).
    """
    if state.session_factory is None:
        return {"status": "invalid"}
    code_hash = _hash(code)
    now = _now()
    async with state.session_factory() as session:
        row = (
            await session.execute(
                select(DeviceLinkCode).where(DeviceLinkCode.code_hash == code_hash)
            )
        ).scalar_one_or_none()
        if row is None:
            return {"status": "invalid"}
        if row.consumed_at is not None:
            return {"status": "consumed"}
        if row.denied_at is not None:
            return {"status": "denied"}

        if row.approved_at is not None:
            # Approved: atomically consume exactly once and hand the
            # caller the minting payload.
            res = await session.execute(
                update(DeviceLinkCode)
                .where(
                    DeviceLinkCode.id == row.id,
                    DeviceLinkCode.approved_at.is_not(None),
                    DeviceLinkCode.consumed_at.is_(None),
                )
                .values(consumed_at=now)
            )
            await session.commit()
            if getattr(res, "rowcount", 0) != 1:
                return {"status": "consumed"}
            email = await _email_for(session, row.user_id)
            return {
                "status": "approved",
                "email": email,
                "mint": {"user_id": row.user_id, "role": row.role},
            }

        if _aware(row.expires_at) <= now:
            return {"status": "expired"}

        email = await _email_for(session, row.user_id)
        if row.redeemed_at is None:
            # First contact: claim atomically.
            res = await session.execute(
                update(DeviceLinkCode)
                .where(
                    DeviceLinkCode.id == row.id,
                    DeviceLinkCode.redeemed_at.is_(None),
                    DeviceLinkCode.denied_at.is_(None),
                )
                .values(redeemed_at=now, redeemer_ip=ip, redeemer_user_agent=user_agent)
            )
            await session.commit()
            if getattr(res, "rowcount", 0) != 1:
                # Lost the claim race; fall through to pending.
                return {"status": "pending", "email": email}
            return {"status": "pending", "email": email, "claimed": True}
        return {"status": "pending", "email": email}


async def pending_for_user(user_id: int) -> list[dict[str, Any]]:
    """Return claimed-but-undecided requests issued by *user_id*.

    Backs the issuing device's approval prompt.

    Args:
        user_id: Database id of the issuing user.

    Returns:
        list[dict[str, Any]]: One entry per pending request with
        ``id``, ``ip``, ``user_agent``, and ``created_at``.
    """
    if state.session_factory is None:
        return []
    now = _now()
    async with state.session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(DeviceLinkCode).where(
                        DeviceLinkCode.user_id == user_id,
                        DeviceLinkCode.redeemed_at.is_not(None),
                        DeviceLinkCode.approved_at.is_(None),
                        DeviceLinkCode.denied_at.is_(None),
                        DeviceLinkCode.consumed_at.is_(None),
                        DeviceLinkCode.expires_at > now,
                    )
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": r.id,
                "ip": r.redeemer_ip,
                "user_agent": r.redeemer_user_agent,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]


async def decide(code_id: int, user_id: int, approve: bool) -> str:
    """Approve or deny a claimed request, scoped to the issuing user.

    Args:
        code_id: Primary key of the device-link code.
        user_id: Database id of the issuing user (ownership guard).
        approve: ``True`` to approve, ``False`` to deny.

    Returns:
        str: ``"approved"``, ``"denied"``, or ``"not_found"`` when no
        matching claimable row exists (already decided/expired/foreign).
    """
    if state.session_factory is None:
        return "not_found"
    now = _now()
    async with state.session_factory() as session:
        if approve:
            stmt = (
                update(DeviceLinkCode)
                .where(
                    DeviceLinkCode.id == code_id,
                    DeviceLinkCode.user_id == user_id,
                    DeviceLinkCode.redeemed_at.is_not(None),
                    DeviceLinkCode.approved_at.is_(None),
                    DeviceLinkCode.denied_at.is_(None),
                    DeviceLinkCode.consumed_at.is_(None),
                    DeviceLinkCode.expires_at > now,
                )
                .values(approved_at=now)
            )
        else:
            stmt = (
                update(DeviceLinkCode)
                .where(
                    DeviceLinkCode.id == code_id,
                    DeviceLinkCode.user_id == user_id,
                    DeviceLinkCode.denied_at.is_(None),
                    DeviceLinkCode.consumed_at.is_(None),
                )
                .values(denied_at=now)
            )
        res = await session.execute(stmt)
        await session.commit()
        if getattr(res, "rowcount", 0) != 1:
            return "not_found"
        return "approved" if approve else "denied"


def _aware(dt: datetime.datetime) -> datetime.datetime:
    """Coerce a possibly-naive DB timestamp to timezone-aware UTC.

    SQLite round-trips ``DateTime(timezone=True)`` as naive values; treat
    those as UTC so comparisons against :func:`_now` are correct.

    Args:
        dt: A timestamp read back from the database.

    Returns:
        datetime.datetime: Timezone-aware UTC timestamp.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=datetime.UTC)
