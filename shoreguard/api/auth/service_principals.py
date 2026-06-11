"""Service principal CRUD (API-key identities for Terraform/CI)."""

from __future__ import annotations

import datetime
import logging
import secrets
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from shoreguard.exceptions import ValidationError as DomainValidationError

if TYPE_CHECKING:
    pass

from shoreguard.api.auth.core import ROLES, _hash_key, state

logger = logging.getLogger(__name__)


# ─── Service Principal CRUD ────────────────────────────────────────────────


async def create_service_principal(
    name: str,
    role: str,
    created_by: int | None = None,
    expires_at: datetime.datetime | None = None,
) -> tuple[str, dict]:
    """Create a new service principal.

    Args:
        name: Human-readable name for the principal.
        role: One of ``admin``, ``operator``, ``viewer``.
        created_by: Database ID of the creating user, or ``None``.
        expires_at: Optional expiry timestamp; ``None`` means never expires.

    Returns:
        tuple[str, dict]: ``(plaintext_key, info_dict)``.

    Raises:
        DomainValidationError: If the role is invalid.
        RuntimeError: If the database is not available.
        IntegrityError: If the name already exists.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if role not in ROLES:
        raise DomainValidationError(f"Invalid role: {role!r}")
    if state.session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import ServicePrincipal

    plaintext = "sg_" + secrets.token_urlsafe(32)
    key_hash = _hash_key(plaintext)
    key_prefix = plaintext[:12]
    now = datetime.datetime.now(datetime.UTC)

    async with state.session_factory() as session:
        try:
            sp = ServicePrincipal(
                name=name,
                key_hash=key_hash,
                key_prefix=key_prefix,
                role=role,
                created_by=created_by,
                created_at=now,
                expires_at=expires_at,
            )
            session.add(sp)
            await session.commit()
            logger.info(
                "Service principal created (id=%d, name=%s, role=%s, created_by=%s)",
                sp.id,
                name,
                role,
                created_by,
            )
            return plaintext, {
                "id": sp.id,
                "name": name,
                "role": role,
                "key_prefix": key_prefix,
                "created_at": now.isoformat(),
                "expires_at": expires_at.isoformat() if expires_at else None,
            }
        except IntegrityError:
            await session.rollback()
            raise
        except Exception:
            await session.rollback()
            logger.exception("Failed to create service principal (name=%s)", name)
            raise


async def list_service_principals() -> list[dict]:
    """Return all service principals (without key hashes).

    Returns:
        list[dict]: SP info dicts ordered by creation time.
    """
    if state.session_factory is None:
        return []
    from shoreguard.models import ServicePrincipal

    async with state.session_factory() as session:
        try:
            rows = (
                (
                    await session.execute(
                        select(ServicePrincipal).order_by(ServicePrincipal.created_at)
                    )
                )
                .scalars()
                .all()
            )
            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "role": r.role,
                    "key_prefix": r.key_prefix,
                    "created_by": r.created_by,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "last_used": r.last_used.isoformat() if r.last_used else None,
                    "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                }
                for r in rows
            ]
        except SQLAlchemyError:
            logger.exception("Failed to list service principals")
            return []


async def delete_service_principal(sp_id: int) -> bool:
    """Delete a service principal by ID.

    Args:
        sp_id: Database ID of the service principal to delete.

    Returns:
        bool: ``True`` if the principal was found and deleted.

    Raises:
        RuntimeError: If the database is not available.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if state.session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import ServicePrincipal

    async with state.session_factory() as session:
        try:
            row = (
                (
                    await session.execute(
                        select(ServicePrincipal).where(ServicePrincipal.id == sp_id)
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return False
            name, role = row.name, row.role
            await session.delete(row)
            await session.commit()
            logger.info("Service principal deleted (sp_id=%d, name=%s, role=%s)", sp_id, name, role)
            return True
        except Exception:
            await session.rollback()
            logger.exception("Failed to delete service principal (sp_id=%d)", sp_id)
            raise


async def rotate_service_principal(sp_id: int) -> tuple[str, dict] | None:
    """Rotate the API key for a service principal.

    Generates a new key and immediately invalidates the old one.

    Args:
        sp_id: Database ID of the service principal.

    Returns:
        tuple[str, dict] | None: ``(new_plaintext_key, info_dict)`` or ``None``
            if the principal was not found.

    Raises:
        RuntimeError: If the database is not available.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if state.session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import ServicePrincipal

    new_plaintext = "sg_" + secrets.token_urlsafe(32)
    new_hash = _hash_key(new_plaintext)
    new_prefix = new_plaintext[:12]

    async with state.session_factory() as session:
        try:
            row = (
                (
                    await session.execute(
                        select(ServicePrincipal).where(ServicePrincipal.id == sp_id)
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return None
            row.key_hash = new_hash
            row.key_prefix = new_prefix
            await session.commit()
            logger.info("Service principal key rotated (sp_id=%d, name=%s)", sp_id, row.name)
            return new_plaintext, {
                "id": row.id,
                "name": row.name,
                "role": row.role,
                "key_prefix": new_prefix,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            }
        except Exception:
            await session.rollback()
            logger.exception("Failed to rotate service principal key (sp_id=%d)", sp_id)
            raise
