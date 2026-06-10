"""Gateway-scoped role CRUD for users and service principals."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from shoreguard.exceptions import NotFoundError
from shoreguard.exceptions import ValidationError as DomainValidationError

if TYPE_CHECKING:
    pass

from shoreguard.api.auth.core import ROLES, state

logger = logging.getLogger(__name__)


# ─── Gateway-scoped role CRUD ─────────────────────────────────────────────


def set_gateway_role(
    *, user_id: int | None = None, sp_id: int | None = None, gateway_name: str, role: str
) -> dict:
    """Create or update a per-gateway role override.

    Args:
        user_id: Database ID of the user, or ``None``.
        sp_id: Database ID of the service principal, or ``None``.
        gateway_name: Name of the gateway to scope the role to.
        role: One of ``admin``, ``operator``, ``viewer``.

    Returns:
        dict: The saved role record.

    Raises:
        DomainValidationError: If the role is invalid.
        NotFoundError: If the gateway is not found.
        RuntimeError: If the database is not available.
        IntegrityError: On constraint violation.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if role not in ROLES:
        raise DomainValidationError(f"Invalid role: {role!r}")
    if state.session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import Gateway, SPGatewayRole, UserGatewayRole

    with state.session_factory() as session:
        try:
            gw = session.query(Gateway).filter(Gateway.name == gateway_name).first()
            if gw is None:
                raise NotFoundError(f"Gateway '{gateway_name}' not found")
            if user_id is not None:
                row = (
                    session.query(UserGatewayRole)
                    .filter(
                        UserGatewayRole.user_id == user_id,
                        UserGatewayRole.gateway_id == gw.id,
                    )
                    .first()
                )
                if row:
                    row.role = role
                else:
                    row = UserGatewayRole(user_id=user_id, gateway_id=gw.id, role=role)
                    session.add(row)
                session.commit()
                return {"user_id": user_id, "gateway_name": gateway_name, "role": role}
            elif sp_id is not None:
                row = (
                    session.query(SPGatewayRole)
                    .filter(
                        SPGatewayRole.sp_id == sp_id,
                        SPGatewayRole.gateway_id == gw.id,
                    )
                    .first()
                )
                if row:
                    row.role = role
                else:
                    row = SPGatewayRole(sp_id=sp_id, gateway_id=gw.id, role=role)
                    session.add(row)
                session.commit()
                return {"sp_id": sp_id, "gateway_name": gateway_name, "role": role}
            else:
                raise DomainValidationError("Either user_id or sp_id must be provided")
        except IntegrityError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            raise


def remove_gateway_role(
    *, user_id: int | None = None, sp_id: int | None = None, gateway_name: str
) -> bool:
    """Remove a per-gateway role override.

    Args:
        user_id: Database ID of the user, or ``None``.
        sp_id: Database ID of the service principal, or ``None``.
        gateway_name: Name of the gateway to remove the override for.

    Returns:
        bool: ``True`` if the override was found and removed.

    Raises:
        RuntimeError: If the database is not available.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if state.session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import Gateway, SPGatewayRole, UserGatewayRole

    with state.session_factory() as session:
        try:
            gw = session.query(Gateway).filter(Gateway.name == gateway_name).first()
            if gw is None:
                return False
            if user_id is not None:
                row = (
                    session.query(UserGatewayRole)
                    .filter(
                        UserGatewayRole.user_id == user_id,
                        UserGatewayRole.gateway_id == gw.id,
                    )
                    .first()
                )
            elif sp_id is not None:
                row = (
                    session.query(SPGatewayRole)
                    .filter(
                        SPGatewayRole.sp_id == sp_id,
                        SPGatewayRole.gateway_id == gw.id,
                    )
                    .first()
                )
            else:
                return False
            if row is None:
                return False
            session.delete(row)
            session.commit()
            logger.info(
                "Gateway role removed (user_id=%s, sp_id=%s, gateway=%s)",
                user_id,
                sp_id,
                gateway_name,
            )
            return True
        except Exception:
            session.rollback()
            logger.exception(
                "Failed to remove gateway role (user_id=%s, sp_id=%s, gateway=%s)",
                user_id,
                sp_id,
                gateway_name,
            )
            raise


def list_gateway_roles_for_user(user_id: int) -> list[dict]:
    """Return all gateway-scoped role overrides for a user.

    Args:
        user_id: Database ID of the user.

    Returns:
        list[dict]: Dicts with ``gateway_name`` and ``role`` keys.
    """
    if state.session_factory is None:
        return []
    from shoreguard.models import Gateway, UserGatewayRole

    with state.session_factory() as session:
        try:
            rows = (
                session.query(UserGatewayRole, Gateway.name)
                .join(Gateway, UserGatewayRole.gateway_id == Gateway.id)
                .filter(UserGatewayRole.user_id == user_id)
                .order_by(Gateway.name)
                .all()
            )
            return [{"gateway_name": gw_name, "role": r.role} for r, gw_name in rows]
        except SQLAlchemyError:
            logger.exception("Failed to list gateway roles for user %d", user_id)
            return []


def list_gateway_roles_for_sp(sp_id: int) -> list[dict]:
    """Return all gateway-scoped role overrides for a service principal.

    Args:
        sp_id: Database ID of the service principal.

    Returns:
        list[dict]: Dicts with ``gateway_name`` and ``role`` keys.
    """
    if state.session_factory is None:
        return []
    from shoreguard.models import Gateway, SPGatewayRole

    with state.session_factory() as session:
        try:
            rows = (
                session.query(SPGatewayRole, Gateway.name)
                .join(Gateway, SPGatewayRole.gateway_id == Gateway.id)
                .filter(SPGatewayRole.sp_id == sp_id)
                .order_by(Gateway.name)
                .all()
            )
            return [{"gateway_name": gw_name, "role": r.role} for r, gw_name in rows]
        except SQLAlchemyError:
            logger.exception("Failed to list gateway roles for SP %d", sp_id)
            return []
