"""Gateway registry backed by SQLAlchemy."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from shoreguard.models import Gateway

logger = logging.getLogger(__name__)


class GatewayRegistry:
    """CRUD and health tracking for registered gateways."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        """Create a registry backed by the given session factory."""
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
    ) -> dict[str, Any]:
        """Register a new gateway. Raises ValueError if name already exists."""
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
                registered_at=datetime.now(UTC).isoformat(),
                last_status="unknown",
            )
            session.add(gw)
            try:
                session.commit()
            except IntegrityError as e:
                session.rollback()
                logger.warning("Duplicate gateway registration attempt: '%s'", name)
                raise ValueError(f"Gateway '{name}' is already registered") from e
            logger.info("Registered gateway '%s' (endpoint=%s, scheme=%s)", name, endpoint, scheme)
            return self._to_dict(gw)

    def unregister(self, name: str) -> bool:
        """Remove a gateway. Returns True if it existed."""
        with self._session_factory() as session:
            gw = session.get(Gateway, name)
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
        """Return a single gateway or None."""
        try:
            with self._session_factory() as session:
                gw = session.get(Gateway, name)
                if gw is None:
                    return None
                return self._to_dict(gw)
        except SQLAlchemyError:
            logger.error("Failed to get gateway '%s'", name, exc_info=True)
            raise

    def list_all(self) -> list[dict[str, Any]]:
        """Return all registered gateways."""
        try:
            with self._session_factory() as session:
                gateways = session.query(Gateway).order_by(Gateway.name).all()
                return [self._to_dict(gw) for gw in gateways]
        except SQLAlchemyError:
            logger.error("Failed to list gateways", exc_info=True)
            raise

    def update_health(self, name: str, status: str, last_seen: str) -> None:
        """Update health status and last-seen timestamp."""
        with self._session_factory() as session:
            gw = session.get(Gateway, name)
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
        """Replace the metadata JSON blob."""
        with self._session_factory() as session:
            gw = session.get(Gateway, name)
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

    def get_credentials(self, name: str) -> dict[str, str | bytes | None] | None:
        """Return raw cert bytes for a gateway (for connection logic only)."""
        try:
            with self._session_factory() as session:
                gw = session.get(Gateway, name)
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
        try:
            metadata = json.loads(gw.metadata_json) if gw.metadata_json else {}
        except json.JSONDecodeError:
            logger.warning("Corrupt metadata_json for gateway '%s'", gw.name)
            metadata = {}
        return {
            "name": gw.name,
            "endpoint": gw.endpoint,
            "scheme": gw.scheme,
            "auth_mode": gw.auth_mode,
            "has_ca_cert": gw.ca_cert is not None,
            "has_client_cert": gw.client_cert is not None,
            "has_client_key": gw.client_key is not None,
            "metadata": metadata,
            "registered_at": gw.registered_at,
            "last_seen": gw.last_seen,
            "last_status": gw.last_status,
        }
