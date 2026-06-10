"""Group CRUD, membership, and group gateway-scoped roles."""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from shoreguard.exceptions import NotFoundError
from shoreguard.exceptions import ValidationError as DomainValidationError

if TYPE_CHECKING:
    pass

from shoreguard.api.auth.core import _SENTINEL, ROLES, state

logger = logging.getLogger(__name__)


# ─── Group CRUD ────────────────────────────────────────────────────────────


def create_group(name: str, role: str = "viewer", description: str | None = None) -> dict:
    """Create a new user group.

    Args:
        name: Unique group name.
        role: Global group role (``admin``, ``operator``, ``viewer``).
        description: Optional human-readable description.

    Returns:
        dict: The created group info.

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
    from shoreguard.models import Group

    now = datetime.datetime.now(datetime.UTC)
    with state.session_factory() as session:
        try:
            group = Group(name=name.strip(), description=description, role=role, created_at=now)
            session.add(group)
            session.commit()
            logger.info("Group created (id=%d, name=%s, role=%s)", group.id, name, role)
            return {
                "id": group.id,
                "name": group.name,
                "description": group.description,
                "role": group.role,
                "created_at": now.isoformat(),
                "member_count": 0,
            }
        except IntegrityError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            logger.exception("Failed to create group (name=%s)", name)
            raise


def update_group(
    group_id: int,
    *,
    name: str | None = None,
    role: str | None = None,
    description: str | None | object = _SENTINEL,
) -> dict:
    """Update a group's name, role, or description.

    Args:
        group_id: Database ID of the group.
        name: New name, or ``None`` to keep unchanged.
        role: New role, or ``None`` to keep unchanged.
        description: New description, or sentinel to keep unchanged.

    Returns:
        dict: The updated group info.

    Raises:
        DomainValidationError: If the role is invalid.
        NotFoundError: If the group is not found.
        RuntimeError: If the database is not available.
        IntegrityError: On constraint violation.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if role is not None and role not in ROLES:
        raise DomainValidationError(f"Invalid role: {role!r}")
    if state.session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import Group

    with state.session_factory() as session:
        try:
            group = session.query(Group).filter(Group.id == group_id).first()
            if group is None:
                raise NotFoundError(f"Group {group_id} not found")
            if name is not None:
                group.name = name.strip()
            if role is not None:
                group.role = role
            if description is not _SENTINEL:
                group.description = description
            session.commit()
            return {
                "id": group.id,
                "name": group.name,
                "description": group.description,
                "role": group.role,
                "created_at": group.created_at.isoformat(),
            }
        except IntegrityError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            raise


def delete_group(group_id: int) -> bool:
    """Delete a group (CASCADE removes memberships and gateway roles).

    Args:
        group_id: Database ID of the group.

    Returns:
        bool: ``True`` if deleted, ``False`` if not found.

    Raises:
        RuntimeError: If the database is not available.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if state.session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import Group

    with state.session_factory() as session:
        try:
            group = session.query(Group).filter(Group.id == group_id).first()
            if group is None:
                return False
            session.delete(group)
            session.commit()
            logger.info("Group deleted (id=%d)", group_id)
            return True
        except Exception:
            session.rollback()
            logger.exception("Failed to delete group (id=%d)", group_id)
            raise


def list_groups() -> list[dict]:
    """Return all groups with member counts.

    Returns:
        list[dict]: Group info dicts ordered by name.
    """
    if state.session_factory is None:
        return []
    from shoreguard.models import Group, GroupMember

    with state.session_factory() as session:
        try:
            from sqlalchemy import func

            rows = (
                session.query(
                    Group,
                    func.count(GroupMember.id).label("member_count"),
                )
                .outerjoin(GroupMember, GroupMember.group_id == Group.id)
                .group_by(Group.id)
                .order_by(Group.name)
                .all()
            )
            return [
                {
                    "id": g.id,
                    "name": g.name,
                    "description": g.description,
                    "role": g.role,
                    "created_at": g.created_at.isoformat(),
                    "member_count": cnt,
                }
                for g, cnt in rows
            ]
        except SQLAlchemyError:
            logger.exception("Failed to list groups")
            return []


def get_group(group_id: int) -> dict | None:
    """Return a group with its member list.

    Args:
        group_id: Database ID of the group.

    Returns:
        dict | None: Group info with ``members`` list, or ``None``.
    """
    if state.session_factory is None:
        return None
    from shoreguard.models import Group, GroupMember, User

    with state.session_factory() as session:
        try:
            group = session.query(Group).filter(Group.id == group_id).first()
            if group is None:
                return None
            members = (
                session.query(User.id, User.email, User.role)
                .join(GroupMember, GroupMember.user_id == User.id)
                .filter(GroupMember.group_id == group_id)
                .order_by(User.email)
                .all()
            )
            return {
                "id": group.id,
                "name": group.name,
                "description": group.description,
                "role": group.role,
                "created_at": group.created_at.isoformat(),
                "members": [
                    {"id": uid, "email": email, "role": role} for uid, email, role in members
                ],
            }
        except SQLAlchemyError:
            logger.exception("Failed to get group %d", group_id)
            return None


# ─── Group membership ──────────────────────────────────────────────────────


def add_group_member(group_id: int, user_id: int) -> dict:
    """Add a user to a group.

    Args:
        group_id: Database ID of the group.
        user_id: Database ID of the user.

    Returns:
        dict: Membership info.

    Raises:
        NotFoundError: If the group or user is not found.
        RuntimeError: If the database is not available.
        IntegrityError: If the membership already exists.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if state.session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import Group, GroupMember, User

    with state.session_factory() as session:
        try:
            group = session.query(Group).filter(Group.id == group_id).first()
            if group is None:
                raise NotFoundError(f"Group {group_id} not found")
            user = session.query(User).filter(User.id == user_id).first()
            if user is None:
                raise NotFoundError(f"User {user_id} not found")
            membership = GroupMember(group_id=group_id, user_id=user_id)
            session.add(membership)
            session.commit()
            logger.info("Added user %d to group %d", user_id, group_id)
            return {
                "group_id": group_id,
                "group_name": group.name,
                "user_id": user_id,
                "user_email": user.email,
            }
        except IntegrityError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            raise


def remove_group_member(group_id: int, user_id: int) -> bool:
    """Remove a user from a group.

    Args:
        group_id: Database ID of the group.
        user_id: Database ID of the user.

    Returns:
        bool: ``True`` if removed, ``False`` if membership not found.

    Raises:
        RuntimeError: If the database is not available.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if state.session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import GroupMember

    with state.session_factory() as session:
        try:
            row = (
                session.query(GroupMember)
                .filter(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
                .first()
            )
            if row is None:
                return False
            session.delete(row)
            session.commit()
            logger.info("Removed user %d from group %d", user_id, group_id)
            return True
        except Exception:
            session.rollback()
            raise


def list_group_members(group_id: int) -> list[dict]:
    """Return all members of a group.

    Args:
        group_id: Database ID of the group.

    Returns:
        list[dict]: Member dicts with ``id``, ``email``, ``role``.
    """
    if state.session_factory is None:
        return []
    from shoreguard.models import GroupMember, User

    with state.session_factory() as session:
        try:
            rows = (
                session.query(User.id, User.email, User.role)
                .join(GroupMember, GroupMember.user_id == User.id)
                .filter(GroupMember.group_id == group_id)
                .order_by(User.email)
                .all()
            )
            return [{"id": uid, "email": email, "role": role} for uid, email, role in rows]
        except SQLAlchemyError:
            logger.exception("Failed to list members for group %d", group_id)
            return []


def list_user_groups(user_id: int) -> list[dict]:
    """Return all groups a user belongs to.

    Args:
        user_id: Database ID of the user.

    Returns:
        list[dict]: Group dicts with ``id``, ``name``, ``role``.
    """
    if state.session_factory is None:
        return []
    from shoreguard.models import Group, GroupMember

    with state.session_factory() as session:
        try:
            rows = (
                session.query(Group.id, Group.name, Group.role)
                .join(GroupMember, GroupMember.group_id == Group.id)
                .filter(GroupMember.user_id == user_id)
                .order_by(Group.name)
                .all()
            )
            return [{"id": gid, "name": name, "role": role} for gid, name, role in rows]
        except SQLAlchemyError:
            logger.exception("Failed to list groups for user %d", user_id)
            return []


# ─── Group gateway-scoped roles ───────────────────────────────────────────


def set_group_gateway_role(group_id: int, gateway_name: str, role: str) -> dict:
    """Create or update a per-gateway role override for a group.

    Args:
        group_id: Database ID of the group.
        gateway_name: Name of the gateway.
        role: One of ``admin``, ``operator``, ``viewer``.

    Returns:
        dict: The saved role record.

    Raises:
        DomainValidationError: If the role is invalid.
        NotFoundError: If the group or gateway is not found.
        RuntimeError: If the database is not available.
        IntegrityError: On constraint violation.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if role not in ROLES:
        raise DomainValidationError(f"Invalid role: {role!r}")
    if state.session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import Gateway, Group, GroupGatewayRole

    with state.session_factory() as session:
        try:
            group = session.query(Group).filter(Group.id == group_id).first()
            if group is None:
                raise NotFoundError(f"Group {group_id} not found")
            gw = session.query(Gateway).filter(Gateway.name == gateway_name).first()
            if gw is None:
                raise NotFoundError(f"Gateway '{gateway_name}' not found")
            row = (
                session.query(GroupGatewayRole)
                .filter(
                    GroupGatewayRole.group_id == group_id,
                    GroupGatewayRole.gateway_id == gw.id,
                )
                .first()
            )
            if row:
                row.role = role
            else:
                row = GroupGatewayRole(group_id=group_id, gateway_id=gw.id, role=role)
                session.add(row)
            session.commit()
            return {"group_id": group_id, "gateway_name": gateway_name, "role": role}
        except IntegrityError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            raise


def remove_group_gateway_role(group_id: int, gateway_name: str) -> bool:
    """Remove a per-gateway role override for a group.

    Args:
        group_id: Database ID of the group.
        gateway_name: Name of the gateway.

    Returns:
        bool: ``True`` if removed, ``False`` if not found.

    Raises:
        RuntimeError: If the database is not available.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if state.session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import Gateway, GroupGatewayRole

    with state.session_factory() as session:
        try:
            gw = session.query(Gateway).filter(Gateway.name == gateway_name).first()
            if gw is None:
                return False
            row = (
                session.query(GroupGatewayRole)
                .filter(
                    GroupGatewayRole.group_id == group_id,
                    GroupGatewayRole.gateway_id == gw.id,
                )
                .first()
            )
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise


def list_group_gateway_roles(group_id: int) -> list[dict]:
    """Return all gateway-scoped role overrides for a group.

    Args:
        group_id: Database ID of the group.

    Returns:
        list[dict]: Dicts with ``gateway_name`` and ``role`` keys.
    """
    if state.session_factory is None:
        return []
    from shoreguard.models import Gateway, GroupGatewayRole

    with state.session_factory() as session:
        try:
            rows = (
                session.query(GroupGatewayRole, Gateway.name)
                .join(Gateway, GroupGatewayRole.gateway_id == Gateway.id)
                .filter(GroupGatewayRole.group_id == group_id)
                .order_by(Gateway.name)
                .all()
            )
            return [{"gateway_name": gw_name, "role": r.role} for r, gw_name in rows]
        except SQLAlchemyError:
            logger.exception("Failed to list gateway roles for group %d", group_id)
            return []


# ─── Bootstrap ─────────────────────────────────────────────────────────────
