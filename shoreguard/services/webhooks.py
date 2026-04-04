"""Webhook notification service for external integrations."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import hmac
import json
import logging
import secrets
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker as SessionMaker

from shoreguard.models import Webhook

logger = logging.getLogger(__name__)

# Module-level singleton — set during app lifespan (see shoreguard.api.main).
webhook_service: WebhookService | None = None

DELIVERY_TIMEOUT = 10.0


class WebhookService:
    """DB-backed webhook management and async event delivery.

    Args:
        session_factory: SQLAlchemy session factory for database access.
    """

    def __init__(self, session_factory: SessionMaker) -> None:  # noqa: D107
        self._session_factory = session_factory

    def list(self) -> list[dict[str, Any]]:
        """Return all registered webhooks.

        Returns:
            list[dict[str, Any]]: All webhook entries.
        """
        try:
            with self._session_factory() as session:
                rows = session.query(Webhook).order_by(Webhook.created_at.desc()).all()
                return [self._to_dict(w) for w in rows]
        except SQLAlchemyError:
            logger.exception("Failed to list webhooks")
            return []

    def get(self, webhook_id: int) -> dict[str, Any] | None:
        """Return a single webhook by ID.

        Args:
            webhook_id: Primary key of the webhook.

        Returns:
            dict or None: Webhook data, or None if not found.
        """
        try:
            with self._session_factory() as session:
                wh = session.get(Webhook, webhook_id)
                return self._to_dict(wh) if wh else None
        except SQLAlchemyError:
            logger.exception("Failed to get webhook %d", webhook_id)
            return None

    def create(self, *, url: str, event_types: list[str], created_by: str) -> dict[str, Any]:
        """Create a new webhook with an auto-generated secret.

        Args:
            url: Target URL for POST requests.
            event_types: List of event type strings to subscribe to.
            created_by: Identity of the creator.

        Returns:
            dict[str, Any]: Created webhook data including the secret.
        """
        secret = secrets.token_hex(32)
        with self._session_factory() as session:
            wh = Webhook(
                url=url,
                secret=secret,
                event_types=json.dumps(event_types),
                is_active=True,
                created_by=created_by,
                created_at=datetime.datetime.now(datetime.UTC),
            )
            session.add(wh)
            session.commit()
            session.refresh(wh)
            return self._to_dict(wh)

    def update(
        self,
        webhook_id: int,
        *,
        url: str | None = None,
        event_types: list[str] | None = None,
        is_active: bool | None = None,
    ) -> dict[str, Any] | None:
        """Update an existing webhook.

        Args:
            webhook_id: Primary key of the webhook.
            url: New target URL.
            event_types: New event type subscriptions.
            is_active: New active state.

        Returns:
            dict or None: Updated webhook data, or None if not found.
        """
        try:
            with self._session_factory() as session:
                wh = session.get(Webhook, webhook_id)
                if not wh:
                    return None
                if url is not None:
                    wh.url = url
                if event_types is not None:
                    wh.event_types = json.dumps(event_types)
                if is_active is not None:
                    wh.is_active = is_active
                session.commit()
                session.refresh(wh)
                return self._to_dict(wh)
        except SQLAlchemyError:
            logger.exception("Failed to update webhook %d", webhook_id)
            return None

    def delete(self, webhook_id: int) -> bool:
        """Delete a webhook by ID.

        Args:
            webhook_id: Primary key of the webhook.

        Returns:
            bool: True if deleted, False if not found.
        """
        try:
            with self._session_factory() as session:
                wh = session.get(Webhook, webhook_id)
                if not wh:
                    return False
                session.delete(wh)
                session.commit()
                return True
        except SQLAlchemyError:
            logger.exception("Failed to delete webhook %d", webhook_id)
            return False

    def _get_active_for_event(self, event_type: str) -> list[tuple[str, str]]:
        """Return (url, secret) pairs for active webhooks subscribed to event_type."""
        try:
            with self._session_factory() as session:
                rows = session.query(Webhook).filter(Webhook.is_active.is_(True)).all()
                result = []
                for wh in rows:
                    try:
                        types = json.loads(wh.event_types)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if event_type in types or "*" in types:
                        result.append((wh.url, wh.secret))
                return result
        except SQLAlchemyError:
            logger.exception("Failed to query webhooks for event %s", event_type)
            return []

    async def fire(self, event_type: str, payload: dict[str, Any]) -> None:
        """Deliver event to all matching webhooks (fire-and-forget).

        Args:
            event_type: Type of event (e.g. "approval.approved").
            payload: Event data payload.
        """
        targets = await asyncio.to_thread(self._get_active_for_event, event_type)
        if not targets:
            return

        body = json.dumps(
            {
                "event": event_type,
                "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                "data": payload,
            },
            default=str,
        )

        for url, secret in targets:
            asyncio.create_task(self._deliver(url, secret, body))

    @staticmethod
    async def _deliver(url: str, secret: str, body: str) -> None:
        """POST a signed payload to a webhook URL.

        Args:
            url: Target URL.
            secret: HMAC signing secret.
            body: JSON-encoded request body.
        """
        signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        try:
            async with httpx.AsyncClient(timeout=DELIVERY_TIMEOUT) as client:
                resp = await client.post(
                    url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Shoreguard-Signature": f"sha256={signature}",
                    },
                )
                if resp.status_code >= 400:
                    logger.warning("Webhook delivery to %s returned %d", url, resp.status_code)
        except Exception:
            logger.warning("Webhook delivery to %s failed", url, exc_info=True)

    @staticmethod
    def _to_dict(wh: Webhook) -> dict[str, Any]:
        """Convert a Webhook ORM object to a plain dict.

        Args:
            wh: The webhook to convert.

        Returns:
            dict[str, Any]: JSON-serializable representation.
        """
        try:
            event_types = json.loads(wh.event_types)
        except (json.JSONDecodeError, TypeError):
            event_types = []
        return {
            "id": wh.id,
            "url": wh.url,
            "secret": wh.secret,
            "event_types": event_types,
            "is_active": wh.is_active,
            "created_by": wh.created_by,
            "created_at": wh.created_at.isoformat() if wh.created_at else None,
        }


async def fire_webhook(event_type: str, payload: dict[str, Any]) -> None:
    """Convenience function to fire a webhook event.

    Args:
        event_type: Type of event.
        payload: Event data payload.
    """
    if webhook_service is None:
        return
    await webhook_service.fire(event_type, payload)
