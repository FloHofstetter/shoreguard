"""Web Push for the installed PWA — phone notifications without a third party.

ntfy and Telegram work great, but both route through an external
service. With Web Push the installed PWA itself receives notifications:
the browser exposes a push endpoint, ShoreGuard encrypts the payload to
the device keys and signs the request with a VAPID keypair that is
generated on first use and persisted next to the secret key. The
``webpush`` webhook channel fans an event out to every registered
device, so the existing event-subscription machinery (event types,
delivery log, retry policy) applies unchanged.

Requires a secure context in the browser (HTTPS or localhost) — in the
homelab that is exactly what `tailscale serve` provides.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from shoreguard.config import shoreguard_config_dir
from shoreguard.models import PushSubscription

if TYPE_CHECKING:
    from py_vapid import Vapid02
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from shoreguard.settings import PushSettings

logger = logging.getLogger(__name__)

VAPID_KEY_FILENAME = ".vapid_private"


class PushService:
    """Stores device subscriptions and sends encrypted push messages.

    Args:
        async_session_factory: Async SQLAlchemy session factory.
        settings: Push configuration (VAPID contact claim).
    """

    def __init__(  # noqa: D107
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        settings: PushSettings,
    ) -> None:
        self._session_factory = async_session_factory
        self._settings = settings
        self._vapid_instance: Vapid02 | None = None

    # ─── VAPID keys ──────────────────────────────────────────────────────────

    def _vapid(self) -> Vapid02:
        """Load (or create on first use) the persistent VAPID keypair.

        Returns:
            Vapid02: The signing key, cached for the process lifetime.
        """
        if self._vapid_instance is None:
            from py_vapid import Vapid02

            key_path = shoreguard_config_dir() / VAPID_KEY_FILENAME
            key_path.parent.mkdir(parents=True, exist_ok=True)
            existed = key_path.is_file()
            self._vapid_instance = Vapid02.from_file(str(key_path))
            if not existed and key_path.is_file():
                os.chmod(key_path, 0o600)
                logger.info("Generated VAPID keypair at %s", key_path)
        return self._vapid_instance

    def public_key(self) -> str:
        """Return the VAPID public key for ``pushManager.subscribe``.

        Returns:
            str: Base64url-encoded uncompressed P-256 public point.
        """
        from cryptography.hazmat.primitives import serialization
        from py_vapid import b64urlencode

        raw = self._vapid().public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return b64urlencode(raw)

    # ─── Subscription CRUD ───────────────────────────────────────────────────

    async def subscribe(
        self,
        *,
        user_email: str,
        endpoint: str,
        p256dh: str,
        auth: str,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """Register (or refresh) a device subscription.

        Args:
            user_email: Owning user's email.
            endpoint: Push-service endpoint URL (unique per browser).
            p256dh: Client public key (base64url).
            auth: Client auth secret (base64url).
            user_agent: Browser user-agent for display.

        Returns:
            dict[str, Any]: The stored subscription record.
        """
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(PushSubscription).where(PushSubscription.endpoint == endpoint)
                )
            ).scalar_one_or_none()
            if row is None:
                row = PushSubscription(
                    user_email=user_email,
                    endpoint=endpoint,
                    p256dh=p256dh,
                    auth=auth,
                    user_agent=user_agent,
                    created_at=datetime.datetime.now(datetime.UTC),
                )
                session.add(row)
            else:
                row.user_email = user_email
                row.p256dh = p256dh
                row.auth = auth
                row.user_agent = user_agent
            await session.commit()
            return self._to_dict(row)

    async def unsubscribe(self, endpoint: str) -> bool:
        """Remove a device subscription by endpoint.

        Args:
            endpoint: The push-service endpoint URL.

        Returns:
            bool: ``True`` if a subscription was removed.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                sa_delete(PushSubscription).where(PushSubscription.endpoint == endpoint)
            )
            await session.commit()
            return bool(result.rowcount)

    async def list_for_user(self, user_email: str) -> list[dict[str, Any]]:
        """List subscriptions registered by one user.

        Args:
            user_email: The owning user's email.

        Returns:
            list[dict[str, Any]]: Subscription records, newest first.
        """
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(PushSubscription)
                        .where(PushSubscription.user_email == user_email)
                        .order_by(PushSubscription.id.desc())
                    )
                )
                .scalars()
                .all()
            )
            return [self._to_dict(r) for r in rows]

    @staticmethod
    def _to_dict(row: PushSubscription) -> dict[str, Any]:
        """Serialize a subscription row (endpoint truncated for display).

        Args:
            row: The ORM row.

        Returns:
            dict[str, Any]: Display-safe record.
        """
        return {
            "id": row.id,
            "user_email": row.user_email,
            "endpoint": row.endpoint[:60] + ("…" if len(row.endpoint) > 60 else ""),
            "user_agent": row.user_agent,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    # ─── Sending ─────────────────────────────────────────────────────────────

    async def send_payload(self, body: str, *, only_email: str | None = None) -> dict[str, int]:
        """Encrypt and send a payload to registered devices.

        Expired subscriptions (push service answers 404/410) are pruned
        so dead devices do not accumulate.

        Args:
            body: JSON payload string (``{"title", "body", "url"}``).
            only_email: Restrict to one user's devices (test sends).

        Returns:
            dict[str, int]: ``sent`` / ``failed`` / ``pruned`` counts.
        """
        from pywebpush import WebPushException, webpush

        async with self._session_factory() as session:
            stmt = select(PushSubscription)
            if only_email is not None:
                stmt = stmt.where(PushSubscription.user_email == only_email)
            rows = (await session.execute(stmt)).scalars().all()
            subs = [
                (r.id, {"endpoint": r.endpoint, "keys": {"p256dh": r.p256dh, "auth": r.auth}})
                for r in rows
            ]

        sent = failed = 0
        prune_ids: list[int] = []
        vapid = self._vapid()
        for sub_id, sub_info in subs:
            try:
                await asyncio.to_thread(
                    webpush,
                    subscription_info=sub_info,
                    data=body,
                    vapid_private_key=vapid,
                    vapid_claims={"sub": self._settings.contact},
                    ttl=3600,
                )
                sent += 1
            except WebPushException as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status in (404, 410):
                    prune_ids.append(sub_id)
                else:
                    failed += 1
                    logger.warning("Web push to subscription %d failed: %s", sub_id, e)
            except Exception:
                failed += 1
                logger.warning("Web push to subscription %d failed", sub_id, exc_info=True)

        if prune_ids:
            async with self._session_factory() as session:
                await session.execute(
                    sa_delete(PushSubscription).where(PushSubscription.id.in_(prune_ids))
                )
                await session.commit()
            logger.info("Pruned %d expired push subscriptions", len(prune_ids))

        return {"sent": sent, "failed": failed, "pruned": len(prune_ids)}
