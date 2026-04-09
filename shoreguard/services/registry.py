"""Gateway registry backed by SQLAlchemy."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from shoreguard.exceptions import ConflictError
from shoreguard.models import Gateway

logger = logging.getLogger(__name__)

_UNSET: object = object()


class GatewayRegistry:
    """CRUD and health tracking for registered gateways.

    Args:
        session_factory: SQLAlchemy session factory for database access.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:  # noqa: D107
        self._session_factory = session_factory

    def register(
        self,
        name: str,
        endpoint: str,
        scheme: str = "https",
        auth_mode: str | None = "mtls",
        *,
        ca_cert: bytes | None = None,
        client_cert: bytes | None = None,
        client_key: bytes | None = None,
        metadata: dict[str, Any] | None = None,
        description: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Register a new gateway.

        Args:
            name: Unique gateway name.
            endpoint: Gateway endpoint address.
            scheme: Connection scheme (e.g. "https").
            auth_mode: Authentication mode (e.g. "mtls").
            ca_cert: CA certificate bytes for TLS.
            client_cert: Client certificate bytes for mTLS.
            client_key: Client private key bytes for mTLS.
            metadata: Optional metadata dict.
            description: Optional free-text description.
            labels: Optional key-value labels for filtering.

        Returns:
            dict[str, Any]: The registered gateway record.

        Raises:
            ConflictError: If a gateway with the given name already exists.
        """
        with self._session_factory() as session:
            gw = Gateway(
                name=name,
                endpoint=endpoint,
                scheme=scheme,
                auth_mode=auth_mode,
                ca_cert=ca_cert,
                client_cert=client_cert,
                client_key=client_key,
                metadata_json=json.dumps(metadata) if metadata else None,
                description=description,
                labels_json=json.dumps(labels) if labels else None,
                registered_at=datetime.now(UTC),
                last_status="unknown",
            )
            session.add(gw)
            try:
                session.commit()
            except IntegrityError as e:
                session.rollback()
                logger.warning("Duplicate gateway registration attempt: '%s'", name)
                raise ConflictError(f"Gateway '{name}' is already registered") from e
            logger.info("Registered gateway '%s' (endpoint=%s, scheme=%s)", name, endpoint, scheme)
            return self._to_dict(gw)

    def unregister(self, name: str) -> bool:
        """Remove a gateway.

        Args:
            name: Gateway name to unregister.

        Returns:
            bool: True if the gateway existed and was removed.

        Raises:
            SQLAlchemyError: If the commit fails.
        """
        with self._session_factory() as session:
            gw = session.query(Gateway).filter(Gateway.name == name).first()
            if gw is None:
                logger.debug("Unregister called for unknown gateway '%s'", name)
                return False
            session.delete(gw)
            try:
                session.commit()
            except SQLAlchemyError:
                session.rollback()
                logger.error("Failed to unregister gateway '%s'", name, exc_info=True)
                raise
            logger.info("Unregistered gateway '%s'", name)
            return True

    def get(self, name: str) -> dict[str, Any] | None:
        """Return a single gateway or None.

        Args:
            name: Gateway name.

        Returns:
            dict[str, Any] | None: Gateway record, or None if not found.

        Raises:
            SQLAlchemyError: If the query fails.
        """
        try:
            with self._session_factory() as session:
                gw = session.query(Gateway).filter(Gateway.name == name).first()
                if gw is None:
                    return None
                return self._to_dict(gw)
        except SQLAlchemyError:
            logger.error("Failed to get gateway '%s'", name, exc_info=True)
            raise

    def list_all(self, *, labels_filter: dict[str, str] | None = None) -> list[dict[str, Any]]:
        """Return all registered gateways, optionally filtered by labels.

        Args:
            labels_filter: If provided, only return gateways whose labels
                contain all specified key-value pairs.

        Returns:
            list[dict[str, Any]]: Gateway records ordered by name.

        Raises:
            SQLAlchemyError: If the query fails.
        """
        try:
            with self._session_factory() as session:
                gateways = session.query(Gateway).order_by(Gateway.name).all()
                result = [self._to_dict(gw) for gw in gateways]
        except SQLAlchemyError:
            logger.error("Failed to list gateways", exc_info=True)
            raise
        if labels_filter:
            result = [
                gw
                for gw in result
                if all(gw.get("labels", {}).get(k) == v for k, v in labels_filter.items())
            ]
        return result

    def update_health(self, name: str, status: str, last_seen: datetime) -> None:
        """Update health status and last-seen timestamp.

        Args:
            name: Gateway name.
            status: New health status string.
            last_seen: Timestamp of the health check.

        Raises:
            SQLAlchemyError: If the commit fails.
        """
        with self._session_factory() as session:
            gw = session.query(Gateway).filter(Gateway.name == name).first()
            if gw is None:
                logger.debug("update_health called for unknown gateway '%s'", name)
                return
            old_status = gw.last_status
            gw.last_status = status
            gw.last_seen = last_seen
            try:
                session.commit()
            except SQLAlchemyError:
                session.rollback()
                logger.error("Failed to update health for gateway '%s'", name, exc_info=True)
                raise
            if old_status != status:
                logger.info("Gateway '%s' health: %s → %s", name, old_status, status)

    def update_metadata(self, name: str, metadata: dict[str, Any]) -> None:
        """Replace the metadata JSON blob.

        Args:
            name: Gateway name.
            metadata: New metadata dict to store.

        Raises:
            SQLAlchemyError: If the commit fails.
        """
        with self._session_factory() as session:
            gw = session.query(Gateway).filter(Gateway.name == name).first()
            if gw is None:
                logger.debug("update_metadata called for unknown gateway '%s'", name)
                return
            gw.metadata_json = json.dumps(metadata)
            try:
                session.commit()
            except SQLAlchemyError:
                session.rollback()
                logger.error("Failed to update metadata for gateway '%s'", name, exc_info=True)
                raise

    def update_gateway_metadata(
        self,
        name: str,
        *,
        description: str | None | object = _UNSET,
        labels: dict[str, str] | None | object = _UNSET,
    ) -> dict[str, Any] | None:
        """Update description and/or labels for a gateway.

        Args:
            name: Gateway name.
            description: New description, None to clear, or _UNSET to skip.
            labels: New labels dict, None to clear, or _UNSET to skip.

        Returns:
            dict[str, Any] | None: Updated gateway dict, or None if not found.

        Raises:
            SQLAlchemyError: If the commit fails.
        """
        with self._session_factory() as session:
            gw = session.query(Gateway).filter(Gateway.name == name).first()
            if gw is None:
                return None
            if description is not _UNSET:
                gw.description = description  # type: ignore[assignment]
            if labels is not _UNSET:
                gw.labels_json = json.dumps(labels) if labels else None  # type: ignore[arg-type]
            try:
                session.commit()
            except SQLAlchemyError:
                session.rollback()
                logger.error("Failed to update metadata for gateway '%s'", name, exc_info=True)
                raise
            logger.info("Updated metadata for gateway '%s'", name)
            return self._to_dict(gw)

    def get_credentials(self, name: str) -> dict[str, str | bytes | None] | None:
        """Return raw cert bytes for a gateway (for connection logic only).

        Args:
            name: Gateway name.

        Returns:
            dict[str, str | bytes | None] | None: Credential dict, or None if
                not found.

        Raises:
            SQLAlchemyError: If the query fails.
        """
        try:
            with self._session_factory() as session:
                gw = session.query(Gateway).filter(Gateway.name == name).first()
                if gw is None:
                    return None
                return {
                    "endpoint": gw.endpoint,
                    "ca_cert": gw.ca_cert,
                    "client_cert": gw.client_cert,
                    "client_key": gw.client_key,
                }
        except SQLAlchemyError:
            logger.error("Failed to get credentials for gateway '%s'", name, exc_info=True)
            raise

    @staticmethod
    def _to_dict(gw: Gateway) -> dict[str, Any]:
        """Convert a Gateway ORM object to a plain dict.

        Args:
            gw: The gateway ORM instance.

        Returns:
            dict[str, Any]: JSON-serializable representation.
        """
        try:
            metadata = json.loads(gw.metadata_json) if gw.metadata_json else {}
        except json.JSONDecodeError:
            logger.warning("Corrupt metadata_json for gateway '%s'", gw.name)
            metadata = {}
        try:
            labels = json.loads(gw.labels_json) if gw.labels_json else {}
        except json.JSONDecodeError:
            logger.warning("Corrupt labels_json for gateway '%s'", gw.name)
            labels = {}
        return {
            "name": gw.name,
            "endpoint": gw.endpoint,
            "scheme": gw.scheme,
            "auth_mode": gw.auth_mode,
            "has_ca_cert": gw.ca_cert is not None,
            "has_client_cert": gw.client_cert is not None,
            "has_client_key": gw.client_key is not None,
            "metadata": metadata,
            "description": gw.description,
            "labels": labels,
            "registered_at": gw.registered_at.isoformat() if gw.registered_at else None,
            "last_seen": gw.last_seen.isoformat() if gw.last_seen else None,
            "last_status": gw.last_status,
        }
