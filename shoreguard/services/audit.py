"""Persistent audit log for state-changing operations."""

from __future__ import annotations

import asyncio
import csv
import datetime
import io
import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker as SessionMaker

from shoreguard.models import AuditEntry, Gateway

logger = logging.getLogger(__name__)

# Module-level singleton — set during app lifespan (see shoreguard.api.main).
audit_service: AuditService | None = None


class AuditService:
    """DB-backed audit trail for all state-changing operations.

    Args:
        session_factory: SQLAlchemy session factory for database access.
    """

    def __init__(self, session_factory: SessionMaker) -> None:  # noqa: D107
        self._session_factory = session_factory

    def log(
        self,
        *,
        actor: str,
        actor_role: str,
        action: str,
        resource_type: str,
        resource_id: str = "",
        gateway: str | None = None,
        detail: dict[str, Any] | None = None,
        client_ip: str | None = None,
    ) -> None:
        """Write an audit entry. Never raises -- failures are logged and swallowed.

        Args:
            actor: Identity of the user performing the action.
            actor_role: Role of the actor (e.g. "admin", "viewer").
            action: Action being performed (e.g. "create", "delete").
            resource_type: Type of resource affected.
            resource_id: Identifier of the affected resource.
            gateway: Optional gateway name for scoping.
            detail: Optional structured detail payload.
            client_ip: IP address of the client.
        """
        try:
            with self._session_factory() as session:
                gateway_id = None
                if gateway:
                    gw = session.query(Gateway).filter(Gateway.name == gateway).first()
                    if gw:
                        gateway_id = gw.id
                entry = AuditEntry(
                    timestamp=datetime.datetime.now(datetime.UTC),
                    actor=actor,
                    actor_role=actor_role,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    gateway_name=gateway,
                    gateway_id=gateway_id,
                    detail=json.dumps(detail) if detail else None,
                    client_ip=client_ip,
                )
                session.add(entry)
                session.commit()
        except SQLAlchemyError:
            logger.warning("Failed to write audit entry (action=%s)", action, exc_info=True)

    def list(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        actor: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query audit entries with optional filters and pagination.

        Args:
            limit: Maximum number of entries to return.
            offset: Number of entries to skip.
            actor: Filter by actor identity.
            action: Filter by action type.
            resource_type: Filter by resource type.
            since: ISO-format start timestamp filter.
            until: ISO-format end timestamp filter.

        Returns:
            list[dict[str, Any]]: Matching audit entries.
        """
        try:
            with self._session_factory() as session:
                q = session.query(AuditEntry)
                if actor:
                    q = q.filter(AuditEntry.actor == actor)
                if action:
                    q = q.filter(AuditEntry.action == action)
                if resource_type:
                    q = q.filter(AuditEntry.resource_type == resource_type)
                if since:
                    since_dt = datetime.datetime.fromisoformat(since)
                    q = q.filter(AuditEntry.timestamp >= since_dt)
                if until:
                    until_dt = datetime.datetime.fromisoformat(until)
                    q = q.filter(AuditEntry.timestamp <= until_dt)
                q = q.order_by(AuditEntry.timestamp.desc())
                rows = q.offset(offset).limit(limit).all()
                return [self._to_dict(r) for r in rows]
        except SQLAlchemyError:
            logger.exception("Failed to list audit entries")
            return []

    def export_csv(
        self,
        *,
        actor: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> str:
        """Export audit entries as a CSV string.

        Args:
            actor: Filter by actor identity.
            action: Filter by action type.
            resource_type: Filter by resource type.
            since: ISO-format start timestamp filter.
            until: ISO-format end timestamp filter.

        Returns:
            str: CSV-formatted string of matching entries.
        """
        entries = self.list(
            limit=10000,
            actor=actor,
            action=action,
            resource_type=resource_type,
            since=since,
            until=until,
        )
        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "id",
                "timestamp",
                "actor",
                "actor_role",
                "action",
                "resource_type",
                "resource_id",
                "gateway",
                "detail",
                "client_ip",
            ],
        )
        writer.writeheader()
        writer.writerows(entries)
        return output.getvalue()

    def cleanup(self, older_than_days: int = 90) -> int:
        """Delete audit entries older than the given number of days.

        Args:
            older_than_days: Age threshold in days.

        Returns:
            int: Number of entries deleted.
        """
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=older_than_days)
        try:
            with self._session_factory() as session:
                count = session.query(AuditEntry).filter(AuditEntry.timestamp < cutoff).delete()
                session.commit()
                if count:
                    logger.info(
                        "Audit cleanup: removed %d entries older than %d days",
                        count,
                        older_than_days,
                    )
                return count
        except SQLAlchemyError:
            logger.warning("Audit cleanup failed", exc_info=True)
            return 0

    @staticmethod
    def _to_dict(entry: AuditEntry) -> dict[str, Any]:
        """Convert an AuditEntry ORM object to a plain dict.

        Args:
            entry: The audit entry to convert.

        Returns:
            dict[str, Any]: JSON-serializable representation.
        """
        detail = None
        if entry.detail:
            try:
                detail = json.loads(entry.detail)
            except json.JSONDecodeError:
                detail = entry.detail
        return {
            "id": entry.id,
            "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
            "actor": entry.actor,
            "actor_role": entry.actor_role,
            "action": entry.action,
            "resource_type": entry.resource_type,
            "resource_id": entry.resource_id,
            "gateway": entry.gateway_name,
            "detail": detail,
            "client_ip": entry.client_ip,
        }


async def audit_log(
    request: Request,
    action: str,
    resource_type: str,
    resource_id: str = "",
    *,
    gateway: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Route-handler helper that extracts actor/role/IP from the request.

    Args:
        request: The incoming HTTP request.
        action: Action being performed.
        resource_type: Type of resource affected.
        resource_id: Identifier of the affected resource.
        gateway: Optional gateway name for scoping.
        detail: Optional structured detail payload.
    """
    if audit_service is None:
        logger.warning("audit_log() called but audit_service is not initialised")
        return
    actor = getattr(request.state, "user_id", "unknown")
    actor_role = getattr(request.state, "role", "unknown")
    client_ip = request.client.host if request.client else None
    await asyncio.to_thread(
        audit_service.log,
        actor=str(actor),
        actor_role=str(actor_role),
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        gateway=gateway,
        detail=detail,
        client_ip=client_ip,
    )
