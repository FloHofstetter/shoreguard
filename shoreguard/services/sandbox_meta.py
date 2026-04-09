"""CRUD for sandbox metadata (labels, description) stored in ShoreGuard DB."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy.orm import Session, sessionmaker

from shoreguard.models import SandboxMeta

logger = logging.getLogger(__name__)

_UNSET: object = object()

sandbox_meta_store: SandboxMetaStore | None = None


class SandboxMetaStore:
    """Persistence layer for sandbox metadata.

    Args:
        session_factory: SQLAlchemy session factory for database access.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:  # noqa: D107
        self._session_factory = session_factory

    def upsert(
        self,
        gateway_name: str,
        sandbox_name: str,
        *,
        description: str | None | object = _UNSET,
        labels: dict[str, str] | None | object = _UNSET,
    ) -> dict[str, Any]:
        """Create or update metadata for a sandbox.

        Args:
            gateway_name: Name of the gateway.
            sandbox_name: Name of the sandbox.
            description: Free-text description (pass _UNSET to skip).
            labels: Key-value labels (pass _UNSET to skip).

        Returns:
            dict[str, Any]: The stored metadata.
        """
        with self._session_factory() as session:
            meta = (
                session.query(SandboxMeta)
                .filter(
                    SandboxMeta.gateway_name == gateway_name,
                    SandboxMeta.sandbox_name == sandbox_name,
                )
                .first()
            )
            now = datetime.now(UTC)
            if meta is None:
                meta = SandboxMeta(
                    gateway_name=gateway_name,
                    sandbox_name=sandbox_name,
                    description=description if description is not _UNSET else None,
                    labels_json=(json.dumps(labels) if labels is not _UNSET and labels else None),
                    created_at=now,
                )
                session.add(meta)
            else:
                if description is not _UNSET:
                    meta.description = cast("str | None", description)
                if labels is not _UNSET:
                    meta.labels_json = json.dumps(labels) if labels else None
                meta.updated_at = now
            session.commit()
            return self._to_dict(meta)

    def get(self, gateway_name: str, sandbox_name: str) -> dict[str, Any] | None:
        """Return metadata for a sandbox, or None if not stored.

        Args:
            gateway_name: Name of the gateway.
            sandbox_name: Name of the sandbox.

        Returns:
            dict[str, Any] | None: Metadata dict or None.
        """
        with self._session_factory() as session:
            meta = (
                session.query(SandboxMeta)
                .filter(
                    SandboxMeta.gateway_name == gateway_name,
                    SandboxMeta.sandbox_name == sandbox_name,
                )
                .first()
            )
            if meta is None:
                return None
            return self._to_dict(meta)

    def delete(self, gateway_name: str, sandbox_name: str) -> bool:
        """Delete metadata for a sandbox.

        Args:
            gateway_name: Name of the gateway.
            sandbox_name: Name of the sandbox.

        Returns:
            bool: True if a row was deleted.
        """
        with self._session_factory() as session:
            count = (
                session.query(SandboxMeta)
                .filter(
                    SandboxMeta.gateway_name == gateway_name,
                    SandboxMeta.sandbox_name == sandbox_name,
                )
                .delete()
            )
            session.commit()
            return count > 0

    def list_for_gateway(self, gateway_name: str) -> dict[str, dict[str, Any]]:
        """Return all metadata for sandboxes on a gateway.

        Args:
            gateway_name: Name of the gateway.

        Returns:
            dict[str, dict[str, Any]]: Mapping of sandbox_name to metadata.
        """
        with self._session_factory() as session:
            rows = session.query(SandboxMeta).filter(SandboxMeta.gateway_name == gateway_name).all()
            return {row.sandbox_name: self._to_dict(row) for row in rows}

    @staticmethod
    def _to_dict(meta: SandboxMeta) -> dict[str, Any]:
        """Convert a SandboxMeta row to a dict.

        Args:
            meta: The ORM instance.

        Returns:
            dict[str, Any]: Metadata with parsed labels.
        """
        try:
            labels = json.loads(meta.labels_json) if meta.labels_json else {}
        except json.JSONDecodeError:
            logger.warning(
                "Corrupt labels_json for sandbox '%s' on gateway '%s'",
                meta.sandbox_name,
                meta.gateway_name,
            )
            labels = {}
        return {
            "description": meta.description,
            "labels": labels,
        }
