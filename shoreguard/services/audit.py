"""Persistent audit log for every state-changing operation.

Every API route that mutates state calls ``audit_log`` with a
short action verb (``policy.update``, ``approval.approved``,
``sbom.uploaded``, ...), a resource type, a resource id, and
optional detail payload. The row is written asynchronously so a
slow audit path never blocks the request, and a background task
fans it out to the webhook pipeline.

The audit log is the single source of truth for "who did what
when" — it is filterable, exportable as CSV or JSON, and
deliberately append-only. Retention is up to operators; there is
no built-in prune.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import datetime
import io
import json
import logging
from collections.abc import Iterator
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker as SessionMaker

    from shoreguard.services.audit_export import AuditExporter

from shoreguard.models import AuditEntry, Gateway

logger = logging.getLogger(__name__)

# Module-level singleton — set during app lifespan (see shoreguard.api.main).
audit_service: AuditService | None = None


# ── Append-only enforcement ──────────────────────────────────────────────
#
# AuditEntry rows must never be mutated and may only be deleted by
# AuditService.cleanup().  The ContextVar-gated SQLAlchemy event listeners
# below raise ``AuditIntegrityError`` when any other code path tries to
# UPDATE or DELETE an audit entry through the ORM.
#
# Caveats:
# * Enforcement happens at the ORM layer only.  Direct SQL (``sqlite3``,
#   ``psql``, or raw ``connection.execute``) bypasses it.  DB-level
#   triggers as a defense-in-depth layer are a post-v1.0 item.
# * ``Query.delete()`` bulk-deletes do NOT fire ``before_delete`` on
#   individual rows, so cleanup() uses row-by-row deletion inside an
#   explicit bypass context.  See :meth:`AuditService.cleanup`.

_audit_mutation_allowed: ContextVar[bool] = ContextVar("_audit_mutation_allowed", default=False)


class AuditIntegrityError(RuntimeError):
    """Raised when code tries to mutate or delete an AuditEntry illegally."""


@contextlib.contextmanager
def _allow_audit_mutation() -> Iterator[None]:
    """Context manager that permits AuditEntry deletes for its duration.

    Only :meth:`AuditService.cleanup` should enter this context.

    Yields:
        None: Control is yielded with mutation temporarily permitted.
    """
    token = _audit_mutation_allowed.set(True)
    try:
        yield
    finally:
        _audit_mutation_allowed.reset(token)


@event.listens_for(AuditEntry, "before_update", propagate=True)
def _block_audit_update(_mapper: Any, _conn: Any, _target: Any) -> None:
    """ORM-level guard: AuditEntry rows are never updatable.

    Args:
        _mapper: SQLAlchemy mapper (unused).
        _conn: SQLAlchemy connection (unused).
        _target: The AuditEntry instance being updated (unused).

    Raises:
        AuditIntegrityError: Always, because AuditEntry is append-only.
    """
    raise AuditIntegrityError("AuditEntry is append-only — UPDATE is not allowed")


@event.listens_for(AuditEntry, "before_delete", propagate=True)
def _block_audit_delete(_mapper: Any, _conn: Any, _target: Any) -> None:
    """ORM-level guard: AuditEntry deletion is only permitted from cleanup().

    Args:
        _mapper: SQLAlchemy mapper (unused).
        _conn: SQLAlchemy connection (unused).
        _target: The AuditEntry instance being deleted (unused).

    Raises:
        AuditIntegrityError: If called outside the ``_allow_audit_mutation`` context.
    """
    if not _audit_mutation_allowed.get():
        raise AuditIntegrityError(
            "AuditEntry is append-only — DELETE only allowed via AuditService.cleanup()"
        )


class AuditService:
    """DB-backed audit trail for all state-changing operations.

    Args:
        session_factory: SQLAlchemy session factory for database access.
        exporter: Optional :class:`AuditExporter` that fans successful
            writes out across stdout-JSON, syslog, and webhook lanes.
    """

    def __init__(  # noqa: D107
        self,
        session_factory: SessionMaker,
        exporter: AuditExporter | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._exporter = exporter

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
                if self._exporter is not None and self._exporter.enabled:
                    try:
                        self._exporter.dispatch(self._to_dict(entry))
                    except Exception:
                        logger.warning(
                            "Audit exporter raised unexpectedly; ignoring",
                            exc_info=True,
                        )
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
        gateway: str | None = None,
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
            gateway: Filter by gateway name (matches the ``gateway_name`` column).
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
                if gateway:
                    q = q.filter(AuditEntry.gateway_name == gateway)
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

    def list_with_count(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        actor: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        gateway: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Query audit entries with total count for pagination.

        Args:
            limit: Maximum number of entries to return.
            offset: Number of entries to skip.
            actor: Filter by actor identity.
            action: Filter by action type.
            resource_type: Filter by resource type.
            gateway: Filter by gateway name.
            since: ISO-format start timestamp filter.
            until: ISO-format end timestamp filter.

        Returns:
            tuple[list[dict[str, Any]], int]: Entries and total matching count.
        """
        from sqlalchemy import func

        try:
            with self._session_factory() as session:
                q = session.query(AuditEntry)
                if actor:
                    q = q.filter(AuditEntry.actor == actor)
                if action:
                    q = q.filter(AuditEntry.action == action)
                if resource_type:
                    q = q.filter(AuditEntry.resource_type == resource_type)
                if gateway:
                    q = q.filter(AuditEntry.gateway_name == gateway)
                if since:
                    since_dt = datetime.datetime.fromisoformat(since)
                    q = q.filter(AuditEntry.timestamp >= since_dt)
                if until:
                    until_dt = datetime.datetime.fromisoformat(until)
                    q = q.filter(AuditEntry.timestamp <= until_dt)
                total = q.with_entities(func.count(AuditEntry.id)).scalar() or 0
                q = q.order_by(AuditEntry.timestamp.desc())
                rows = q.offset(offset).limit(limit).all()
                return [self._to_dict(r) for r in rows], total
        except SQLAlchemyError:
            logger.exception("Failed to list audit entries")
            return [], 0

    def export_csv(
        self,
        *,
        actor: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        gateway: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> str:
        """Export audit entries as a CSV string.

        Args:
            actor: Filter by actor identity.
            action: Filter by action type.
            resource_type: Filter by resource type.
            gateway: Filter by gateway name.
            since: ISO-format start timestamp filter.
            until: ISO-format end timestamp filter.

        Returns:
            str: CSV-formatted string of matching entries.
        """
        from shoreguard.settings import get_settings

        entries = self.list(
            limit=get_settings().audit.export_limit,
            actor=actor,
            action=action,
            resource_type=resource_type,
            gateway=gateway,
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

    def cleanup(self, older_than_days: int | None = None) -> int:
        """Delete audit entries older than the given number of days.

        Uses row-by-row deletion (not ``Query.delete()``) so that the
        ``before_delete`` listener fires and the ContextVar bypass is
        honoured.  Retention cleanup runs once per ``cleanup_interval``
        so the slight extra cost is negligible.

        Args:
            older_than_days: Age threshold in days.

        Returns:
            int: Number of entries deleted.
        """
        if older_than_days is None:
            from shoreguard.settings import get_settings

            older_than_days = get_settings().audit.retention_days
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=older_than_days)
        try:
            with self._session_factory() as session, _allow_audit_mutation():
                stale = session.query(AuditEntry).filter(AuditEntry.timestamp < cutoff).all()
                count = len(stale)
                for entry in stale:
                    session.delete(entry)
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

    def export_json(
        self,
        *,
        actor: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        gateway: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> str:
        """Export audit entries as a JSON array string.

        Args:
            actor: Filter by actor identity.
            action: Filter by action type.
            resource_type: Filter by resource type.
            gateway: Filter by gateway name.
            since: ISO-format start timestamp filter.
            until: ISO-format end timestamp filter.

        Returns:
            str: JSON-formatted list of matching entries.
        """
        from shoreguard.settings import get_settings

        entries = self.list(
            limit=get_settings().audit.export_limit,
            actor=actor,
            action=action,
            resource_type=resource_type,
            gateway=gateway,
            since=since,
            until=until,
        )
        return json.dumps(entries, indent=2, default=str)

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
