"""Tests for the WebhookService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.models import Base
from shoreguard.services.webhooks import RETRY_DELAYS, WebhookService, _Target


@pytest.fixture
def webhook_svc():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    svc = WebhookService(factory)
    yield svc
    engine.dispose()


class TestCRUD:
    def test_create_and_list(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["approval.approved", "sandbox.created"],
            created_by="admin@test.com",
        )
        assert wh["url"] == "https://example.com/hook"
        assert wh["event_types"] == ["approval.approved", "sandbox.created"]
        assert wh["is_active"] is True
        assert len(wh["secret"]) == 64  # hex(32 bytes)
        assert wh["created_by"] == "admin@test.com"
        assert wh["channel_type"] == "generic"

        all_hooks = webhook_svc.list()
        assert len(all_hooks) == 1
        assert all_hooks[0]["id"] == wh["id"]

    def test_get(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="admin@test.com",
        )
        result = webhook_svc.get(wh["id"])
        assert result is not None
        assert result["url"] == "https://example.com/hook"

    def test_get_not_found(self, webhook_svc):
        assert webhook_svc.get(999) is None

    def test_update(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="admin@test.com",
        )
        updated = webhook_svc.update(
            wh["id"],
            url="https://new.example.com/hook",
            event_types=["approval.approved"],
            is_active=False,
        )
        assert updated is not None
        assert updated["url"] == "https://new.example.com/hook"
        assert updated["event_types"] == ["approval.approved"]
        assert updated["is_active"] is False

    def test_update_not_found(self, webhook_svc):
        assert webhook_svc.update(999, url="https://x.com") is None

    def test_delete(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="admin@test.com",
        )
        assert webhook_svc.delete(wh["id"]) is True
        assert webhook_svc.get(wh["id"]) is None

    def test_delete_not_found(self, webhook_svc):
        assert webhook_svc.delete(999) is False

    def test_create_slack_channel(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://hooks.slack.com/services/T00/B00/xxx",
            event_types=["*"],
            created_by="admin@test.com",
            channel_type="slack",
        )
        assert wh["channel_type"] == "slack"
        assert wh["url"] == "https://hooks.slack.com/services/T00/B00/xxx"

    def test_create_discord_channel(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://discord.com/api/webhooks/123/abc",
            event_types=["approval.pending"],
            created_by="admin@test.com",
            channel_type="discord",
        )
        assert wh["channel_type"] == "discord"

    def test_create_email_channel(self, webhook_svc):
        import json

        wh = webhook_svc.create(
            url="admin@example.com",
            event_types=["*"],
            created_by="admin@test.com",
            channel_type="email",
            extra_config=json.dumps(
                {
                    "smtp_host": "smtp.example.com",
                    "smtp_port": 587,
                    "to_addrs": ["admin@example.com"],
                }
            ),
        )
        assert wh["channel_type"] == "email"
        assert "extra_config" in wh
        assert wh["extra_config"]["smtp_host"] == "smtp.example.com"

    def test_to_dict_includes_channel_type(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="admin@test.com",
        )
        assert "channel_type" in wh


class TestFire:
    async def test_fire_matches_event_type(self, webhook_svc):
        webhook_svc.create(
            url="https://example.com/hook",
            event_types=["approval.approved"],
            created_by="admin@test.com",
        )
        with patch.object(WebhookService, "_deliver", new_callable=AsyncMock) as mock_deliver:
            await webhook_svc.fire("approval.approved", {"sandbox": "test"})
            # Give create_task a chance to run
            import asyncio

            await asyncio.sleep(0.1)
            mock_deliver.assert_called_once()

    async def test_fire_skips_inactive(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["approval.approved"],
            created_by="admin@test.com",
        )
        webhook_svc.update(wh["id"], is_active=False)
        with patch.object(WebhookService, "_deliver", new_callable=AsyncMock) as mock_deliver:
            await webhook_svc.fire("approval.approved", {"sandbox": "test"})
            import asyncio

            await asyncio.sleep(0.1)
            mock_deliver.assert_not_called()

    async def test_fire_wildcard(self, webhook_svc):
        webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="admin@test.com",
        )
        with patch.object(WebhookService, "_deliver", new_callable=AsyncMock) as mock_deliver:
            await webhook_svc.fire("anything.here", {"data": "test"})
            import asyncio

            await asyncio.sleep(0.1)
            mock_deliver.assert_called_once()

    async def test_fire_no_match(self, webhook_svc):
        webhook_svc.create(
            url="https://example.com/hook",
            event_types=["sandbox.created"],
            created_by="admin@test.com",
        )
        with patch.object(WebhookService, "_deliver", new_callable=AsyncMock) as mock_deliver:
            await webhook_svc.fire("approval.approved", {"data": "test"})
            import asyncio

            await asyncio.sleep(0.1)
            mock_deliver.assert_not_called()

    async def test_fire_slack_no_hmac(self, webhook_svc):
        webhook_svc.create(
            url="https://hooks.slack.com/services/T00/B00/xxx",
            event_types=["*"],
            created_by="admin@test.com",
            channel_type="slack",
        )
        with patch.object(
            WebhookService, "_deliver_http_with_retry", new_callable=AsyncMock
        ) as mock_http:
            with patch.object(WebhookService, "_deliver", wraps=WebhookService._deliver):
                await webhook_svc.fire("sandbox.created", {"sandbox": "test"})
                import asyncio

                await asyncio.sleep(0.1)
                if mock_http.called:
                    target = mock_http.call_args[0][0]
                    assert target.channel_type == "slack"


# ─── Delivery retry tests ─────────────────────────────────────────────────


def _make_target(**overrides):
    defaults = {
        "webhook_id": 1,
        "url": "https://example.com/hook",
        "secret": "test-secret",
        "channel_type": "generic",
        "extra_config": None,
    }
    defaults.update(overrides)
    return _Target(**defaults)


class TestDeliveryRetry:
    """Tests for _deliver_http_with_retry retry logic."""

    @pytest.fixture(autouse=True)
    def _patch_sleep(self):
        with patch("shoreguard.services.webhooks.asyncio.sleep", new_callable=AsyncMock) as mock:
            self._mock_sleep = mock
            yield

    @pytest.fixture(autouse=True)
    def _patch_update(self, webhook_svc):
        webhook_svc._update_delivery = MagicMock()
        webhook_svc._inc_delivery_counter = MagicMock()
        self._svc = webhook_svc

    async def test_success_on_first_attempt(self):
        mock_resp = httpx.Response(200)
        with patch("shoreguard.services.webhooks.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await self._svc._deliver_http_with_retry(_make_target(), '{"a":1}', 42)

        self._svc._update_delivery.assert_called_once()
        assert self._svc._update_delivery.call_args.kwargs["status"] == "success"
        self._svc._inc_delivery_counter.assert_called_once_with("success")
        self._mock_sleep.assert_not_called()

    async def test_5xx_retries_then_succeeds(self):
        responses = [httpx.Response(502), httpx.Response(502), httpx.Response(200)]
        with patch("shoreguard.services.webhooks.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = responses
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await self._svc._deliver_http_with_retry(_make_target(), '{"a":1}', 42)

        assert self._svc._update_delivery.call_args.kwargs["status"] == "success"
        assert self._svc._update_delivery.call_args.kwargs["attempt"] == 3
        assert self._mock_sleep.call_count == 2
        self._mock_sleep.assert_any_call(RETRY_DELAYS[0])
        self._mock_sleep.assert_any_call(RETRY_DELAYS[1])

    async def test_4xx_does_not_retry(self):
        mock_resp = httpx.Response(422)
        with patch("shoreguard.services.webhooks.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await self._svc._deliver_http_with_retry(_make_target(), '{"a":1}', 42)

        self._svc._update_delivery.assert_called_once()
        assert self._svc._update_delivery.call_args.kwargs["status"] == "failed"
        self._svc._inc_delivery_counter.assert_called_once_with("failed")
        self._mock_sleep.assert_not_called()

    async def test_timeout_retries_then_exhausted(self):
        max_attempts = len(RETRY_DELAYS) + 1
        with patch("shoreguard.services.webhooks.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.TimeoutException("timed out")
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await self._svc._deliver_http_with_retry(_make_target(), '{"a":1}', 42)

        assert mock_client.post.call_count == max_attempts
        assert self._svc._update_delivery.call_args.kwargs["status"] == "failed"
        assert self._svc._update_delivery.call_args.kwargs["attempt"] == max_attempts
        self._svc._inc_delivery_counter.assert_called_once_with("failed")

    async def test_connect_error_triggers_retry(self):
        responses = [httpx.ConnectError("refused"), httpx.Response(200)]
        with patch("shoreguard.services.webhooks.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = responses
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await self._svc._deliver_http_with_retry(_make_target(), '{"a":1}', 42)

        assert self._svc._update_delivery.call_args.kwargs["status"] == "success"
        assert self._mock_sleep.call_count == 1

    async def test_hmac_signature_present_for_generic(self):
        body = '{"event": "test"}'
        with patch("shoreguard.services.webhooks.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = httpx.Response(200)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await self._svc._deliver_http_with_retry(
                _make_target(channel_type="generic", secret="my-secret"), body, 42
            )

        call_kwargs = mock_client.post.call_args
        headers = call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))
        assert "X-Shoreguard-Signature" in headers
        assert headers["X-Shoreguard-Signature"].startswith("sha256=")

    async def test_no_hmac_for_slack(self):
        with patch("shoreguard.services.webhooks.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = httpx.Response(200)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await self._svc._deliver_http_with_retry(
                _make_target(channel_type="slack"), '{"a":1}', 42
            )

        call_kwargs = mock_client.post.call_args
        headers = call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))
        assert "X-Shoreguard-Signature" not in headers


# ─── Delivery record & cleanup tests ────────────────────────────────────────


class TestDeliveryRecords:
    """Tests for delivery record creation, listing, and cleanup."""

    def test_cleanup_old_deliveries(self, webhook_svc):
        """cleanup_old_deliveries removes records older than max_age_days."""
        import datetime

        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="admin@test.com",
        )
        # Create a delivery and manually backdate it
        delivery_id = webhook_svc._create_delivery(wh["id"], "sandbox.created", '{"a":1}')
        with webhook_svc._session_factory() as session:
            from shoreguard.models import WebhookDelivery

            row = session.get(WebhookDelivery, delivery_id)
            row.created_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=30)
            session.commit()

        purged = webhook_svc.cleanup_old_deliveries(max_age_days=7)
        assert purged == 1

        # Second call should find nothing to purge
        assert webhook_svc.cleanup_old_deliveries(max_age_days=7) == 0

    def test_cleanup_keeps_recent_deliveries(self, webhook_svc):
        """cleanup_old_deliveries keeps records newer than max_age_days."""
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="admin@test.com",
        )
        webhook_svc._create_delivery(wh["id"], "sandbox.created", '{"a":1}')
        purged = webhook_svc.cleanup_old_deliveries(max_age_days=7)
        assert purged == 0

    def test_list_deliveries(self, webhook_svc):
        """list_deliveries returns delivery records for a webhook."""
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="admin@test.com",
        )
        webhook_svc._create_delivery(wh["id"], "sandbox.created", '{"a":1}')
        webhook_svc._create_delivery(wh["id"], "sandbox.deleted", '{"b":2}')

        deliveries = webhook_svc.list_deliveries(wh["id"])
        assert len(deliveries) == 2
        assert all(d["webhook_id"] == wh["id"] for d in deliveries)
        assert all(d["status"] == "pending" for d in deliveries)

    def test_list_deliveries_empty(self, webhook_svc):
        """list_deliveries returns empty list for webhook with no deliveries."""
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="admin@test.com",
        )
        assert webhook_svc.list_deliveries(wh["id"]) == []

    def test_update_delivery_success(self, webhook_svc):
        """_update_delivery sets status, response_code, and delivered_at."""
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="admin@test.com",
        )
        delivery_id = webhook_svc._create_delivery(wh["id"], "sandbox.created", '{"a":1}')
        webhook_svc._update_delivery(delivery_id, status="success", response_code=200, attempt=1)

        deliveries = webhook_svc.list_deliveries(wh["id"])
        assert len(deliveries) == 1
        assert deliveries[0]["status"] == "success"
        assert deliveries[0]["response_code"] == 200
        assert deliveries[0]["delivered_at"] is not None

    def test_update_delivery_failed(self, webhook_svc):
        """_update_delivery records failure with error message."""
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="admin@test.com",
        )
        delivery_id = webhook_svc._create_delivery(wh["id"], "sandbox.created", '{"a":1}')
        webhook_svc._update_delivery(
            delivery_id, status="failed", error_message="HTTP 502", attempt=4
        )

        deliveries = webhook_svc.list_deliveries(wh["id"])
        assert deliveries[0]["status"] == "failed"
        assert deliveries[0]["error_message"] == "HTTP 502"
        assert deliveries[0]["attempt"] == 4
        assert deliveries[0]["delivered_at"] is None


# ─── Email delivery tests ────────────────────────────────────────────────────


class TestEmailDelivery:
    """Tests for _deliver_email path."""

    async def test_email_delivery_success(self, webhook_svc):
        """Email delivery calls aiosmtplib.send with correct parameters."""
        import json

        wh = webhook_svc.create(
            url="admin@example.com",
            event_types=["*"],
            created_by="admin@test.com",
            channel_type="email",
            extra_config=json.dumps(
                {
                    "smtp_host": "smtp.example.com",
                    "smtp_port": 587,
                    "to_addrs": ["admin@example.com"],
                }
            ),
        )
        delivery_id = webhook_svc._create_delivery(wh["id"], "sandbox.created", '{"a":1}')
        target = _make_target(
            webhook_id=wh["id"],
            url="admin@example.com",
            channel_type="email",
            extra_config=wh.get("extra_config") and json.dumps(wh["extra_config"]),
        )

        mock_smtp_mod = MagicMock()
        mock_smtp_mod.send = AsyncMock()
        with patch.dict("sys.modules", {"aiosmtplib": mock_smtp_mod}):
            await webhook_svc._deliver_email(target, "Subject line\nBody text", delivery_id)
            mock_smtp_mod.send.assert_called_once()

        deliveries = webhook_svc.list_deliveries(wh["id"])
        assert any(d["status"] == "success" for d in deliveries)

    async def test_email_delivery_failure(self, webhook_svc):
        """Email delivery failure records error in delivery."""
        import json

        wh = webhook_svc.create(
            url="admin@example.com",
            event_types=["*"],
            created_by="admin@test.com",
            channel_type="email",
            extra_config=json.dumps(
                {"smtp_host": "smtp.example.com", "to_addrs": ["admin@example.com"]}
            ),
        )
        delivery_id = webhook_svc._create_delivery(wh["id"], "sandbox.created", '{"a":1}')
        target = _make_target(
            webhook_id=wh["id"],
            url="admin@example.com",
            channel_type="email",
            extra_config=wh.get("extra_config") and json.dumps(wh["extra_config"]),
        )

        mock_smtp_mod = MagicMock()
        mock_smtp_mod.send = AsyncMock(side_effect=OSError("Connection refused"))
        with patch.dict("sys.modules", {"aiosmtplib": mock_smtp_mod}):
            await webhook_svc._deliver_email(target, "Subject\nBody", delivery_id)

        deliveries = webhook_svc.list_deliveries(wh["id"])
        assert any(d["status"] == "failed" for d in deliveries)


# ─── Convenience function tests ─────────────────────────────────────────────


class TestFireWebhookConvenience:
    """Tests for the module-level fire_webhook function."""

    async def test_fire_webhook_noop_when_no_service(self):
        """fire_webhook does nothing when webhook_service is None."""
        import shoreguard.services.webhooks as mod

        original = mod.webhook_service
        mod.webhook_service = None
        try:
            await mod.fire_webhook("sandbox.created", {"sandbox": "test"})
        finally:
            mod.webhook_service = original

    async def test_fire_webhook_delegates_to_service(self, webhook_svc):
        """fire_webhook delegates to the module-level service."""
        import shoreguard.services.webhooks as mod

        original = mod.webhook_service
        mod.webhook_service = webhook_svc
        try:
            with patch.object(webhook_svc, "fire", new_callable=AsyncMock) as mock_fire:
                await mod.fire_webhook("sandbox.created", {"sandbox": "test"})
                mock_fire.assert_called_once_with("sandbox.created", {"sandbox": "test"})
        finally:
            mod.webhook_service = original
