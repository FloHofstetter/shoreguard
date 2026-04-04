"""Webhook notification service for external integrations."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import hmac
import json
import logging
import secrets
from typing import TYPE_CHECKING, Any, NamedTuple

import httpx
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker as SessionMaker

from shoreguard.models import Webhook
from shoreguard.services.formatters import FORMATTERS

logger = logging.getLogger(__name__)

# Module-level singleton — set during app lifespan (see shoreguard.api.main).
webhook_service: WebhookService | None = None

DELIVERY_TIMEOUT = 10.0


class _Target(NamedTuple):
    """Resolved delivery target for a webhook.

    Attributes:
        url: Target URL for delivery.
        secret: HMAC signing secret.
        channel_type: Channel type (generic, slack, discord, email).
        extra_config: Optional JSON config for channel-specific settings.
    """

    url: str
    secret: str
    channel_type: str
    extra_config: str | None


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
            dict[str, Any] | None: Webhook data, or None if not found.
        """
        try:
            with self._session_factory() as session:
                wh = session.get(Webhook, webhook_id)
                return self._to_dict(wh) if wh else None
        except SQLAlchemyError:
            logger.exception("Failed to get webhook %d", webhook_id)
            return None

    def create(
        self,
        *,
        url: str,
        event_types: list[str],
        created_by: str,
        channel_type: str = "generic",
        extra_config: str | None = None,
    ) -> dict[str, Any]:
        """Create a new webhook with an auto-generated secret.

        Args:
            url: Target URL for POST requests.
            event_types: List of event type strings to subscribe to.
            created_by: Identity of the creator.
            channel_type: Channel type (generic, slack, discord, email).
            extra_config: Optional JSON config for channel-specific settings.

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
                channel_type=channel_type,
                extra_config=extra_config,
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
        channel_type: str | None = None,
        extra_config: str | None = None,
    ) -> dict[str, Any] | None:
        """Update an existing webhook.

        Args:
            webhook_id: Primary key of the webhook.
            url: New target URL.
            event_types: New event type subscriptions.
            is_active: New active state.
            channel_type: New channel type.
            extra_config: New channel-specific config.

        Returns:
            dict[str, Any] | None: Updated webhook data, or None if not found.
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
                if channel_type is not None:
                    wh.channel_type = channel_type
                if extra_config is not None:
                    wh.extra_config = extra_config
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

    def _get_active_for_event(self, event_type: str) -> list[_Target]:
        """Return targets for active webhooks subscribed to event_type.

        Args:
            event_type: The event type to match.

        Returns:
            list[_Target]: Matching delivery targets.
        """
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
                        result.append(
                            _Target(
                                url=wh.url,
                                secret=wh.secret,
                                channel_type=wh.channel_type,
                                extra_config=wh.extra_config,
                            )
                        )
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

        timestamp = datetime.datetime.now(datetime.UTC).isoformat()

        for target in targets:
            formatter = FORMATTERS.get(target.channel_type, FORMATTERS["generic"])
            body = formatter(event_type, payload, timestamp)
            asyncio.create_task(self._deliver(target, body))

    @staticmethod
    async def _deliver(target: _Target, body: str) -> None:
        """Deliver a payload to a webhook target.

        Args:
            target: Delivery target with URL, secret, and channel type.
            body: Formatted request body string.
        """
        if target.channel_type == "email":
            await WebhookService._deliver_email(target, body)
            return
        await WebhookService._deliver_http(target, body)

    @staticmethod
    async def _deliver_http(target: _Target, body: str) -> None:
        """POST a payload to an HTTP webhook URL.

        Args:
            target: Delivery target.
            body: JSON-encoded request body.
        """
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if target.channel_type == "generic":
            signature = hmac.new(target.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
            headers["X-Shoreguard-Signature"] = f"sha256={signature}"

        try:
            async with httpx.AsyncClient(timeout=DELIVERY_TIMEOUT) as client:
                resp = await client.post(target.url, content=body, headers=headers)
                if resp.status_code >= 400:
                    logger.warning(
                        "Webhook delivery to %s returned %d", target.url, resp.status_code
                    )
                    WebhookService._inc_delivery_counter("failed")
                else:
                    WebhookService._inc_delivery_counter("success")
        except Exception:
            logger.warning("Webhook delivery to %s failed", target.url, exc_info=True)
            WebhookService._inc_delivery_counter("failed")

    @staticmethod
    async def _deliver_email(target: _Target, body: str) -> None:
        """Send a notification email via SMTP.

        Args:
            target: Delivery target with SMTP config in extra_config.
            body: Plain-text email body.
        """
        try:
            from email.message import EmailMessage

            import aiosmtplib

            config = json.loads(target.extra_config or "{}")
            smtp_host = config.get("smtp_host", "localhost")
            smtp_port = config.get("smtp_port", 587)
            smtp_user = config.get("smtp_user")
            smtp_pass = config.get("smtp_pass")
            from_addr = config.get("from_addr", "shoreguard@localhost")
            to_addrs = config.get("to_addrs", [target.url])

            msg = EmailMessage()
            msg["Subject"] = body.split("\n", 1)[0]  # First line as subject
            msg["From"] = from_addr
            msg["To"] = ", ".join(to_addrs)
            msg.set_content(body)

            kwargs: dict[str, Any] = {
                "hostname": smtp_host,
                "port": smtp_port,
                "timeout": DELIVERY_TIMEOUT,
            }
            if smtp_user and smtp_pass:
                kwargs["username"] = smtp_user
                kwargs["password"] = smtp_pass
                kwargs["use_tls"] = True

            await aiosmtplib.send(msg, **kwargs)
            WebhookService._inc_delivery_counter("success")
        except Exception:
            logger.warning("Email delivery failed", exc_info=True)
            WebhookService._inc_delivery_counter("failed")

    @staticmethod
    def _inc_delivery_counter(status: str) -> None:
        """Increment the Prometheus webhook delivery counter.

        Args:
            status: Delivery result ("success" or "failed").
        """
        try:
            from shoreguard.api.metrics import webhook_deliveries_total

            webhook_deliveries_total.labels(status=status).inc()
        except ImportError:
            pass

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
        result: dict[str, Any] = {
            "id": wh.id,
            "url": wh.url,
            "secret": wh.secret,
            "event_types": event_types,
            "is_active": wh.is_active,
            "channel_type": wh.channel_type,
            "created_by": wh.created_by,
            "created_at": wh.created_at.isoformat() if wh.created_at else None,
        }
        if wh.extra_config:
            try:
                result["extra_config"] = json.loads(wh.extra_config)
            except (json.JSONDecodeError, TypeError):
                result["extra_config"] = wh.extra_config
        return result


async def fire_webhook(event_type: str, payload: dict[str, Any]) -> None:
    """Convenience function to fire a webhook event.

    Args:
        event_type: Type of event.
        payload: Event data payload.
    """
    if webhook_service is None:
        return
    await webhook_service.fire(event_type, payload)
