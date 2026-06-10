"""User CRUD, invites, and OIDC user provisioning."""

from __future__ import annotations

import datetime
import logging
import secrets
from typing import TYPE_CHECKING

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from shoreguard.exceptions import ValidationError as DomainValidationError

if TYPE_CHECKING:
    pass

from shoreguard.api.auth.core import (
    ROLES,
    _get_auth_settings,
    _hash_key,
    hash_password,
    is_setup_complete,
    state,
)

logger = logging.getLogger(__name__)


# ─── User CRUD ─────────────────────────────────────────────────────────────


def create_user(email: str, password: str | None, role: str) -> dict:
    """Create a new user account.

    If *password* is None, an invite token is generated instead.
    The user must accept the invite to set their password.

    Args:
        email: User email address.
        password: Plaintext password, or ``None`` for invite-based creation.
        role: One of ``admin``, ``operator``, ``viewer``.

    Returns:
        dict: User info dict (includes ``invite_token`` when applicable).

    Raises:
        DomainValidationError: If the role is invalid.
        RuntimeError: If the database is not available.
        IntegrityError: If the email already exists.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if role not in ROLES:
        raise DomainValidationError(f"Invalid role: {role!r}")
    if state.session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import User

    email = email.strip().lower()
    now = datetime.datetime.now(datetime.UTC)
    invite_token = None
    invite_token_hash = None
    hashed_pw = None
    if password:
        hashed_pw = hash_password(password)
    else:
        invite_token = secrets.token_urlsafe(32)
        invite_token_hash = _hash_key(invite_token)

    with state.session_factory() as session:
        try:
            user = User(
                email=email,
                hashed_password=hashed_pw,
                role=role,
                invite_token_hash=invite_token_hash,
                created_at=now,
            )
            session.add(user)
            session.commit()
            result: dict = {
                "id": user.id,
                "email": user.email,
                "role": user.role,
                "created_at": now.isoformat(),
            }
            if invite_token:
                result["invite_token"] = invite_token
            logger.info(
                "User created (id=%d, email=%s, role=%s, has_invite=%s)",
                user.id,
                email,
                role,
                invite_token is not None,
            )
            return result
        except IntegrityError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            logger.exception("Failed to create user (email=%s)", email)
            raise


INVITE_MAX_AGE = 86400 * 7  # 7 days — module-level alias for backwards compat


def accept_invite(token: str, password: str) -> dict | None:
    """Accept an invite by setting the user's password.

    Rejects tokens older than the configured invite max age.

    Args:
        token: The invite token from the invite link.
        password: The new plaintext password to set.

    Returns:
        dict | None: ``{id, email, role}`` on success, else ``None``.

    Raises:
        IntegrityError: On constraint violation during update.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if state.session_factory is None:
        return None
    from shoreguard.models import User

    token_hash = _hash_key(token)
    with state.session_factory() as session:
        try:
            user = (
                session.query(User)
                .filter(User.invite_token_hash == token_hash)
                .with_for_update()
                .first()
            )
            if user is None:
                return None
            # Check token age
            if user.created_at:
                created_at = user.created_at
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=datetime.UTC)
                age = (datetime.datetime.now(datetime.UTC) - created_at).total_seconds()
                if age > _get_auth_settings().invite_max_age:
                    logger.warning(
                        "Invite token expired (email=%s, age_hours=%.1f)", user.email, age / 3600
                    )
                    return None
            user.hashed_password = hash_password(password)
            user.invite_token_hash = None
            session.commit()
            logger.info(
                "Invite accepted (user_id=%d, email=%s, role=%s)",
                user.id,
                user.email,
                user.role,
            )
            return {"id": user.id, "email": user.email, "role": user.role}
        except IntegrityError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            logger.exception("Failed to accept invite")
            raise


def list_users() -> list[dict]:
    """Return all users (without password hashes).

    Returns:
        list[dict]: User info dicts ordered by creation time.
    """
    if state.session_factory is None:
        return []
    from shoreguard.models import User

    with state.session_factory() as session:
        try:
            rows = session.query(User).order_by(User.created_at).all()
            return [
                {
                    "id": r.id,
                    "email": r.email,
                    "role": r.role,
                    "is_active": r.is_active,
                    "pending_invite": r.invite_token_hash is not None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "oidc_provider": r.oidc_provider,
                }
                for r in rows
            ]
        except SQLAlchemyError:
            logger.exception("Failed to list users")
            return []


def find_or_create_oidc_user(email: str, oidc_provider: str, oidc_sub: str, role: str) -> dict:
    """Find an existing user or create one for an OIDC login.

    Lookup order:
    1. By ``(oidc_provider, oidc_sub)`` — returning OIDC user.
    2. By ``email`` — existing local user, link OIDC identity.
    3. No match — create a new user with OIDC identity, no password.

    Args:
        email: Email from the OIDC claims.
        oidc_provider: Provider name (e.g. ``"google"``).
        oidc_sub: The ``sub`` claim from the ID token.
        role: Role for new users (from role mapping).

    Returns:
        dict: Mapping with ``"user"`` (user info dict) and ``"action"``
        (``"login"``, ``"link"``, or ``"create"``).

    Raises:
        RuntimeError: If the database is not available.
    """
    if state.session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import User

    email = email.strip().lower()

    with state.session_factory() as session:
        # 1. Lookup by OIDC identity
        user = (
            session.query(User)
            .filter(User.oidc_provider == oidc_provider, User.oidc_sub == oidc_sub)
            .first()
        )
        if user:
            info = {"id": user.id, "email": user.email, "role": user.role}
            return {"user": info, "action": "login"}

        # 2. Lookup by email — link OIDC identity
        user = session.query(User).filter(User.email == email).first()
        if user:
            user.oidc_provider = oidc_provider
            user.oidc_sub = oidc_sub
            session.commit()
            logger.info("Linked OIDC identity (user=%s, provider=%s)", email, oidc_provider)
            info = {"id": user.id, "email": user.email, "role": user.role}
            return {"user": info, "action": "link"}

        # 3. Create new user
        now = datetime.datetime.now(datetime.UTC)
        if role not in ROLES:
            role = "viewer"
        user = User(
            email=email,
            hashed_password=None,
            role=role,
            created_at=now,
            oidc_provider=oidc_provider,
            oidc_sub=oidc_sub,
        )
        session.add(user)
        session.commit()
        logger.info(
            "Created OIDC user (id=%d, email=%s, provider=%s, role=%s)",
            user.id,
            email,
            oidc_provider,
            role,
        )
        info = {"id": user.id, "email": user.email, "role": user.role}
        return {"user": info, "action": "create"}


def delete_user(user_id: int) -> bool:
    """Delete a user by ID.

    Uses a single transaction with locked read to prevent TOCTOU races.

    Args:
        user_id: Database ID of the user to delete.

    Returns:
        bool: ``True`` if the user was found and deleted.

    Raises:
        DomainValidationError: If the user is the last active admin.
        ValueError: Re-raised after rollback on generic value errors.
        RuntimeError: If the database is not available.
        IntegrityError: On constraint violation.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if state.session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import User

    with state.session_factory() as session:
        try:
            row = session.query(User).filter(User.id == user_id).with_for_update().first()
            if row is None:
                return False
            if row.role == "admin" and row.is_active:
                admin_count = (
                    session.query(func.count(User.id))
                    .filter(
                        User.role == "admin",
                        User.is_active == True,  # noqa: E712
                        User.id != user_id,
                    )
                    .scalar()
                )
                if admin_count == 0:
                    raise DomainValidationError("Cannot delete the last active admin user")
            email, role = row.email, row.role
            session.delete(row)
            session.commit()
            logger.info(
                "User deleted from DB (user_id=%d, email=%s, role=%s)",
                user_id,
                email,
                role,
            )
            return True
        except IntegrityError:
            session.rollback()
            raise
        except ValueError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            logger.exception("Failed to delete user (user_id=%d)", user_id)
            raise


def bootstrap_admin_user() -> None:
    """Seed the first admin user from env var if the users table is empty.

    Raises:
        Exception: If user creation fails (re-raised after logging).
    """
    password = _get_auth_settings().admin_password
    if not password or state.session_factory is None:
        return
    if is_setup_complete():
        return
    try:
        create_user("admin@localhost", password, "admin")
        logger.info("Bootstrap admin user created (admin@localhost)")
    except Exception:
        logger.exception("Failed to bootstrap admin user")
        raise
