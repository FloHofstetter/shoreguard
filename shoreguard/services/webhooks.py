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

from shoreguard.config import is_private_ip

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker as SessionMaker

    from shoreguard.settings import WebhookSettings

from shoreguard.models import Webhook, WebhookDelivery
from shoreguard.services.formatters import FORMATTERS

logger = logging.getLogger(__name__)

# Module-level singleton — set during app lifespan (see shoreguard.api.main).
webhook_service: WebhookService | None = None


def _webhook_settings() -> WebhookSettings:
    from shoreguard.settings import get_settings

    return get_settings().webhooks


# Backward-compatible aliases for test imports.
RETRY_DELAYS = [5, 30, 120]


class _Target(NamedTuple):
    """Resolved delivery target for a webhook.

    Attributes:
        webhook_id: Database ID of the webhook.
        url: Target URL for delivery.
        secret: HMAC signing secret.
        channel_type: Channel type (generic, slack, discord, email).
        extra_config: Optional JSON config for channel-specific settings.
    """

    webhook_id: int
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
        self._delivery_tasks: set[asyncio.Task[None]] = set()

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
            return self._to_dict_with_secret(wh)

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
                    except json.JSONDecodeError, TypeError:
                        continue
                    if event_type in types or "*" in types:
                        result.append(
                            _Target(
                                webhook_id=wh.id,
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
        """Deliver event to all matching webhooks with delivery tracking.

        Args:
            event_type: Type of event (e.g. "approval.approved").
            payload: Event data payload.
        """
        targets = await asyncio.to_thread(self._get_active_for_event, event_type)
        if not targets:
            return

        timestamp = datetime.datetime.now(datetime.UTC).isoformat()
        payload_json = json.dumps(payload, default=str)

        for target in targets:
            delivery_id = await asyncio.to_thread(
                self._create_delivery, target.webhook_id, event_type, payload_json
            )
            formatter = FORMATTERS.get(target.channel_type, FORMATTERS["generic"])
            body = formatter(event_type, payload, timestamp)
            task = asyncio.create_task(self._deliver(target, body, delivery_id))
            self._delivery_tasks.add(task)
            task.add_done_callback(self._delivery_tasks.discard)

    async def shutdown(self, timeout: float = 5.0) -> int:
        """Cancel all in-flight webhook deliveries.

        Args:
            timeout: Maximum seconds to wait for tasks to finish.

        Returns:
            int: Number of tasks that were cancelled.
        """
        tasks = list(self._delivery_tasks)
        if not tasks:
            return 0
        for t in tasks:
            t.cancel()
        await asyncio.wait(tasks, timeout=timeout)
        return len(tasks)

    def _create_delivery(self, webhook_id: int, event_type: str, payload_json: str) -> int:
        """Create a pending delivery record.

        Args:
            webhook_id: Target webhook ID.
            event_type: Event type string.
            payload_json: JSON-encoded payload.

        Returns:
            int: Delivery row ID.
        """
        with self._session_factory() as session:
            delivery = WebhookDelivery(
                webhook_id=webhook_id,
                event_type=event_type,
                payload_json=payload_json,
                status="pending",
                attempt=1,
                created_at=datetime.datetime.now(datetime.UTC),
            )
            session.add(delivery)
            session.commit()
            session.refresh(delivery)
            return delivery.id

    def _update_delivery(
        self,
        delivery_id: int,
        *,
        status: str,
        response_code: int | None = None,
        error_message: str | None = None,
        attempt: int = 1,
    ) -> None:
        """Update a delivery record with result.

        Args:
            delivery_id: Delivery row ID.
            status: New status (success or failed).
            response_code: HTTP response code, if any.
            error_message: Error details, if any.
            attempt: Current attempt number.
        """
        try:
            with self._session_factory() as session:
                row = session.get(WebhookDelivery, delivery_id)
                if row:
                    row.status = status
                    row.response_code = response_code
                    row.error_message = error_message
                    row.attempt = attempt
                    if status == "success":
                        row.delivered_at = datetime.datetime.now(datetime.UTC)
                    session.commit()
        except SQLAlchemyError:
            logger.exception("Failed to update delivery %d", delivery_id)

    async def _deliver(self, target: _Target, body: str, delivery_id: int) -> None:
        """Deliver a payload to a webhook target with retry.

        Args:
            target: Delivery target with URL, secret, and channel type.
            body: Formatted request body string.
            delivery_id: Delivery record ID for status updates.
        """
        if target.channel_type == "email":
            await self._deliver_email(target, body, delivery_id)
            return
        await self._deliver_http_with_retry(target, body, delivery_id)

    async def _deliver_http_with_retry(self, target: _Target, body: str, delivery_id: int) -> None:
        """POST a payload with retry on 5xx and network errors.

        Args:
            target: Delivery target.
            body: JSON-encoded request body.
            delivery_id: Delivery record ID.
        """
        # DNS-rebinding protection: re-check target at delivery time
        from urllib.parse import urlparse

        hostname = urlparse(target.url).hostname
        if hostname and is_private_ip(hostname):
            logger.warning(
                "SSRF blocked: webhook %d target %s resolves to private address",
                target.webhook_id,
                target.url,
            )
            await asyncio.to_thread(
                self._update_delivery,
                delivery_id,
                status="failed",
                error_message="SSRF blocked: target resolves to private address",
                attempt=1,
            )
            self._inc_delivery_counter("failed")
            return

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if target.channel_type == "generic":
            signature = hmac.new(target.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
            headers["X-Shoreguard-Signature"] = f"sha256={signature}"

        wh_cfg = _webhook_settings()
        retry_delays = wh_cfg.retry_delays
        max_attempts = len(retry_delays) + 1
        error_msg: str | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=wh_cfg.delivery_timeout) as client:
                    resp = await client.post(target.url, content=body, headers=headers)
                    if resp.status_code < 400:
                        await asyncio.to_thread(
                            self._update_delivery,
                            delivery_id,
                            status="success",
                            response_code=resp.status_code,
                            attempt=attempt,
                        )
                        self._inc_delivery_counter("success")
                        logger.info(
                            "Webhook %d delivered to %s (HTTP %d)",
                            target.webhook_id,
                            target.url,
                            resp.status_code,
                        )
                        return
                    if resp.status_code < 500:
                        # Client error — don't retry
                        await asyncio.to_thread(
                            self._update_delivery,
                            delivery_id,
                            status="failed",
                            response_code=resp.status_code,
                            error_message=f"HTTP {resp.status_code}",
                            attempt=attempt,
                        )
                        self._inc_delivery_counter("failed")
                        logger.warning(
                            "Webhook delivery to %s returned %d (no retry)",
                            target.url,
                            resp.status_code,
                        )
                        return
                    # 5xx — retry
                    error_msg = f"HTTP {resp.status_code}"
            except (httpx.TimeoutException, httpx.ConnectError, OSError) as e:
                error_msg = str(e)

            if attempt < max_attempts:
                delay = retry_delays[attempt - 1]
                logger.info(
                    "Webhook delivery to %s failed (attempt %d/%d), retrying in %ds",
                    target.url,
                    attempt,
                    max_attempts,
                    delay,
                )
                await asyncio.sleep(delay)

        # All attempts exhausted
        await asyncio.to_thread(
            self._update_delivery,
            delivery_id,
            status="failed",
            error_message=error_msg,
            attempt=max_attempts,
        )
        self._inc_delivery_counter("failed")
        logger.warning(
            "Webhook delivery to %s failed after %d attempts: %s",
            target.url,
            max_attempts,
            error_msg,
        )

    async def _deliver_email(self, target: _Target, body: str, delivery_id: int) -> None:
        """Send a notification email via SMTP.

        Args:
            target: Delivery target with SMTP config in extra_config.
            body: Plain-text email body.
            delivery_id: Delivery record ID.
        """
        try:
            from email.message import EmailMessage

            import aiosmtplib

            config = json.loads(target.extra_config or "{}")
            smtp_host = config.get("smtp_host", "localhost")
            smtp_port = config.get("smtp_port", 587)

            # DNS-rebinding protection for SMTP targets
            if is_private_ip(smtp_host):
                logger.warning(
                    "SSRF blocked: webhook %d SMTP host %s resolves to private address",
                    target.webhook_id,
                    smtp_host,
                )
                await asyncio.to_thread(
                    self._update_delivery,
                    delivery_id,
                    status="failed",
                    error_message="SSRF blocked: SMTP host resolves to private address",
                    attempt=1,
                )
                self._inc_delivery_counter("failed")
                return
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
                "timeout": _webhook_settings().delivery_timeout,
            }
            if smtp_user and smtp_pass:
                kwargs["username"] = smtp_user
                kwargs["password"] = smtp_pass
                kwargs["use_tls"] = True

            await aiosmtplib.send(msg, **kwargs)
            await asyncio.to_thread(
                self._update_delivery,
                delivery_id,
                status="success",
                attempt=1,
            )
            self._inc_delivery_counter("success")
            logger.info("Email notification delivered for webhook %d", target.webhook_id)
        except Exception as e:
            logger.warning("Email delivery failed", exc_info=True)
            await asyncio.to_thread(
                self._update_delivery,
                delivery_id,
                status="failed",
                error_message=str(e),
                attempt=1,
            )
            self._inc_delivery_counter("failed")

    # ─── Delivery log queries ────────────────────────────────────────────────

    def list_deliveries(self, webhook_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent deliveries for a webhook.

        Args:
            webhook_id: Webhook ID to query.
            limit: Maximum number of records to return.

        Returns:
            list[dict[str, Any]]: Delivery records, newest first.
        """
        try:
            with self._session_factory() as session:
                rows = (
                    session.query(WebhookDelivery)
                    .filter(WebhookDelivery.webhook_id == webhook_id)
                    .order_by(WebhookDelivery.created_at.desc())
                    .limit(limit)
                    .all()
                )
                return [
                    {
                        "id": r.id,
                        "webhook_id": r.webhook_id,
                        "event_type": r.event_type,
                        "status": r.status,
                        "response_code": r.response_code,
                        "error_message": r.error_message,
                        "attempt": r.attempt,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                        "delivered_at": r.delivered_at.isoformat() if r.delivered_at else None,
                    }
                    for r in rows
                ]
        except SQLAlchemyError:
            logger.exception("Failed to list deliveries for webhook %d", webhook_id)
            return []

    def cleanup_old_deliveries(self, max_age_days: int | None = None) -> int:
        """Purge delivery records older than max_age_days.

        Args:
            max_age_days: Maximum age of records to keep.

        Returns:
            int: Number of records deleted.
        """
        if max_age_days is None:
            max_age_days = _webhook_settings().delivery_max_age_days
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=max_age_days)
        try:
            with self._session_factory() as session:
                count = (
                    session.query(WebhookDelivery)
                    .filter(WebhookDelivery.created_at < cutoff)
                    .delete()
                )
                session.commit()
                if count:
                    logger.info("Purged %d old webhook deliveries", count)
                return count
        except SQLAlchemyError:
            logger.exception("Failed to cleanup old webhook deliveries")
            return 0

    # ─── Helpers ─────────────────────────────────────────────────────────────

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
        """Convert a Webhook ORM object to a plain dict (without secret).

        Args:
            wh: The webhook to convert.

        Returns:
            dict[str, Any]: JSON-serializable representation.
        """
        try:
            event_types = json.loads(wh.event_types)
        except json.JSONDecodeError, TypeError:
            event_types = []
        result: dict[str, Any] = {
            "id": wh.id,
            "url": wh.url,
            "event_types": event_types,
            "is_active": wh.is_active,
            "channel_type": wh.channel_type,
            "created_by": wh.created_by,
            "created_at": wh.created_at.isoformat() if wh.created_at else None,
        }
        if wh.extra_config:
            try:
                result["extra_config"] = json.loads(wh.extra_config)
            except json.JSONDecodeError, TypeError:
                result["extra_config"] = wh.extra_config
        return result

    @staticmethod
    def _to_dict_with_secret(wh: Webhook) -> dict[str, Any]:
        """Convert a Webhook ORM object including the HMAC secret.

        Used only for the creation response (secret shown once).

        Args:
            wh: The webhook to convert.

        Returns:
            dict[str, Any]: JSON-serializable representation including secret.
        """
        result = WebhookService._to_dict(wh)
        result["secret"] = wh.secret
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
