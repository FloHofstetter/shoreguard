"""Tests for the WebhookService."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import hmac
import json
from email.message import EmailMessage
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.models import Base, WebhookDelivery
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
        assert "secret" not in all_hooks[0]

    def test_get(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="admin@test.com",
        )
        result = webhook_svc.get(wh["id"])
        assert result is not None
        assert result["url"] == "https://example.com/hook"
        assert "secret" not in result

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


# ─── Comprehensive mutation-killing tests ──────────────────────────────────────


# Helper: default webhook settings mock
def _mock_webhook_settings(**overrides):
    defaults = {
        "delivery_timeout": 10.0,
        "retry_delays": [5, 30, 120],
        "delivery_max_age_days": 7,
    }
    defaults.update(overrides)
    cfg = MagicMock()
    for k, v in defaults.items():
        setattr(cfg, k, v)
    return cfg


class TestEmailDeliveryComprehensive:
    """Kill mutation survivors in _deliver_email."""

    @pytest.fixture(autouse=True)
    def _setup(self, webhook_svc):
        self._svc = webhook_svc

    def _email_target(self, extra_config=None):
        if extra_config is None:
            extra_config = json.dumps(
                {
                    "smtp_host": "smtp.example.com",
                    "smtp_port": 587,
                    "smtp_user": "user@example.com",
                    "smtp_pass": "secret123",
                    "from_addr": "noreply@shoreguard.io",
                    "to_addrs": ["admin@example.com", "ops@example.com"],
                }
            )
        return _make_target(
            webhook_id=42,
            url="admin@example.com",
            channel_type="email",
            extra_config=extra_config,
        )

    async def test_email_message_subject_is_first_line(self):
        """Subject must be exactly the first line of body."""
        target = self._email_target()
        mock_smtp = MagicMock()
        mock_smtp.send = AsyncMock()
        with (
            patch.dict("sys.modules", {"aiosmtplib": mock_smtp}),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
        ):
            await self._svc._deliver_email(target, "Alert: sandbox created\nDetails here", 1)
            msg = mock_smtp.send.call_args[0][0]
            assert isinstance(msg, EmailMessage)
            assert msg["Subject"] == "Alert: sandbox created"

    async def test_email_message_from_addr(self):
        """From header must match config from_addr."""
        target = self._email_target()
        mock_smtp = MagicMock()
        mock_smtp.send = AsyncMock()
        with (
            patch.dict("sys.modules", {"aiosmtplib": mock_smtp}),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
        ):
            await self._svc._deliver_email(target, "Subject\nBody", 1)
            msg = mock_smtp.send.call_args[0][0]
            assert msg["From"] == "noreply@shoreguard.io"

    async def test_email_message_to_addrs_joined(self):
        """To header must be comma-separated to_addrs."""
        target = self._email_target()
        mock_smtp = MagicMock()
        mock_smtp.send = AsyncMock()
        with (
            patch.dict("sys.modules", {"aiosmtplib": mock_smtp}),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
        ):
            await self._svc._deliver_email(target, "Subject\nBody", 1)
            msg = mock_smtp.send.call_args[0][0]
            assert msg["To"] == "admin@example.com, ops@example.com"

    async def test_email_message_body_content(self):
        """Email body must be the full body text."""
        target = self._email_target()
        mock_smtp = MagicMock()
        mock_smtp.send = AsyncMock()
        with (
            patch.dict("sys.modules", {"aiosmtplib": mock_smtp}),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
        ):
            full_body = "Subject line\nFull body text here"
            await self._svc._deliver_email(target, full_body, 1)
            msg = mock_smtp.send.call_args[0][0]
            assert msg.get_content().strip() == full_body

    async def test_email_smtp_kwargs_with_auth(self):
        """When smtp_user and smtp_pass present, use TLS and auth."""
        target = self._email_target()
        mock_smtp = MagicMock()
        mock_smtp.send = AsyncMock()
        with (
            patch.dict("sys.modules", {"aiosmtplib": mock_smtp}),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
        ):
            await self._svc._deliver_email(target, "Subject\nBody", 1)
            kwargs = mock_smtp.send.call_args[1]
            assert kwargs["hostname"] == "smtp.example.com"
            assert kwargs["port"] == 587
            assert kwargs["timeout"] == 10.0
            assert kwargs["username"] == "user@example.com"
            assert kwargs["password"] == "secret123"
            assert kwargs["use_tls"] is True

    async def test_email_smtp_kwargs_without_auth(self):
        """When no smtp_user/smtp_pass, no TLS/auth kwargs."""
        config = json.dumps(
            {
                "smtp_host": "mail.local",
                "smtp_port": 25,
                "from_addr": "sg@local",
                "to_addrs": ["admin@local"],
            }
        )
        target = self._email_target(extra_config=config)
        mock_smtp = MagicMock()
        mock_smtp.send = AsyncMock()
        with (
            patch.dict("sys.modules", {"aiosmtplib": mock_smtp}),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
        ):
            await self._svc._deliver_email(target, "Subject\nBody", 1)
            kwargs = mock_smtp.send.call_args[1]
            assert kwargs["hostname"] == "mail.local"
            assert kwargs["port"] == 25
            assert kwargs["timeout"] == 10.0
            assert "username" not in kwargs
            assert "password" not in kwargs
            assert "use_tls" not in kwargs

    async def test_email_default_config_values(self):
        """Defaults: smtp_host=localhost, smtp_port=587, from_addr=shoreguard@localhost."""
        target = self._email_target(extra_config="{}")
        mock_smtp = MagicMock()
        mock_smtp.send = AsyncMock()
        with (
            patch.dict("sys.modules", {"aiosmtplib": mock_smtp}),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_email(target, "Subject\nBody", 1)
            kwargs = mock_smtp.send.call_args[1]
            assert kwargs["hostname"] == "localhost"
            assert kwargs["port"] == 587
            msg = mock_smtp.send.call_args[0][0]
            assert msg["From"] == "shoreguard@localhost"

    async def test_email_default_to_addrs_uses_url(self):
        """When to_addrs not in config, defaults to [target.url]."""
        config = json.dumps({"smtp_host": "smtp.example.com"})
        target = _make_target(
            webhook_id=1,
            url="fallback@example.com",
            channel_type="email",
            extra_config=config,
        )
        mock_smtp = MagicMock()
        mock_smtp.send = AsyncMock()
        with (
            patch.dict("sys.modules", {"aiosmtplib": mock_smtp}),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
        ):
            await self._svc._deliver_email(target, "Subject\nBody", 1)
            msg = mock_smtp.send.call_args[0][0]
            assert msg["To"] == "fallback@example.com"

    async def test_email_null_extra_config(self):
        """When extra_config is None, defaults to '{}'."""
        target = _make_target(
            webhook_id=1,
            url="x@test.com",
            channel_type="email",
            extra_config=None,
        )
        mock_smtp = MagicMock()
        mock_smtp.send = AsyncMock()
        with (
            patch.dict("sys.modules", {"aiosmtplib": mock_smtp}),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_email(target, "Subject\nBody", 1)
            kwargs = mock_smtp.send.call_args[1]
            assert kwargs["hostname"] == "localhost"
            assert kwargs["port"] == 587

    async def test_email_success_updates_delivery_status(self):
        """Successful email sets delivery status=success, attempt=1."""
        wh = self._svc.create(
            url="admin@example.com",
            event_types=["*"],
            created_by="test",
            channel_type="email",
            extra_config=json.dumps({"smtp_host": "smtp.test.com"}),
        )
        delivery_id = self._svc._create_delivery(wh["id"], "test.event", "{}")
        target = self._email_target()
        mock_smtp = MagicMock()
        mock_smtp.send = AsyncMock()
        with (
            patch.dict("sys.modules", {"aiosmtplib": mock_smtp}),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
        ):
            await self._svc._deliver_email(target, "Subject\nBody", delivery_id)
        deliveries = self._svc.list_deliveries(wh["id"])
        d = [x for x in deliveries if x["id"] == delivery_id][0]
        assert d["status"] == "success"
        assert d["attempt"] == 1

    async def test_email_success_increments_counter(self):
        """Successful email calls _inc_delivery_counter('success')."""
        target = self._email_target()
        mock_smtp = MagicMock()
        mock_smtp.send = AsyncMock()
        self._svc._inc_delivery_counter = MagicMock()
        with (
            patch.dict("sys.modules", {"aiosmtplib": mock_smtp}),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
        ):
            await self._svc._deliver_email(target, "Subject\nBody", 1)
        self._svc._inc_delivery_counter.assert_called_once_with("success")

    async def test_email_failure_records_error_message(self):
        """Failed email records str(exception) as error_message."""
        wh = self._svc.create(
            url="admin@example.com",
            event_types=["*"],
            created_by="test",
            channel_type="email",
            extra_config=json.dumps({"smtp_host": "smtp.test.com"}),
        )
        delivery_id = self._svc._create_delivery(wh["id"], "test.event", "{}")
        target = self._email_target()
        mock_smtp = MagicMock()
        mock_smtp.send = AsyncMock(side_effect=OSError("Connection refused"))
        with (
            patch.dict("sys.modules", {"aiosmtplib": mock_smtp}),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
        ):
            await self._svc._deliver_email(target, "Subject\nBody", delivery_id)
        deliveries = self._svc.list_deliveries(wh["id"])
        d = [x for x in deliveries if x["id"] == delivery_id][0]
        assert d["status"] == "failed"
        assert d["error_message"] == "Connection refused"
        assert d["attempt"] == 1

    async def test_email_failure_increments_counter(self):
        """Failed email calls _inc_delivery_counter('failed')."""
        target = self._email_target()
        mock_smtp = MagicMock()
        mock_smtp.send = AsyncMock(side_effect=RuntimeError("fail"))
        self._svc._inc_delivery_counter = MagicMock()
        with (
            patch.dict("sys.modules", {"aiosmtplib": mock_smtp}),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
        ):
            await self._svc._deliver_email(target, "Subject\nBody", 1)
        self._svc._inc_delivery_counter.assert_called_once_with("failed")

    async def test_email_ssrf_blocks_private_smtp_host(self):
        """SSRF: private SMTP host blocked, delivery marked failed."""
        config = json.dumps({"smtp_host": "127.0.0.1", "smtp_port": 25})
        target = self._email_target(extra_config=config)
        self._svc._update_delivery = MagicMock()
        self._svc._inc_delivery_counter = MagicMock()
        with patch("shoreguard.services.webhooks.is_private_ip", return_value=True):
            await self._svc._deliver_email(target, "Subject\nBody", 99)
        self._svc._update_delivery.assert_called_once()
        kw = self._svc._update_delivery.call_args
        # Check keyword args — may be positional or keyword
        assert kw.kwargs.get("status") == "failed" or kw[1].get("status") == "failed"
        error = kw.kwargs.get("error_message") or kw[1].get("error_message", "")
        assert "SSRF" in error
        assert "private" in error.lower()
        self._svc._inc_delivery_counter.assert_called_once_with("failed")

    async def test_email_ssrf_returns_early(self):
        """SSRF blocked email does not call aiosmtplib.send."""
        config = json.dumps({"smtp_host": "10.0.0.1", "smtp_port": 25})
        target = self._email_target(extra_config=config)
        self._svc._update_delivery = MagicMock()
        self._svc._inc_delivery_counter = MagicMock()
        mock_smtp = MagicMock()
        mock_smtp.send = AsyncMock()
        with (
            patch.dict("sys.modules", {"aiosmtplib": mock_smtp}),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=True),
        ):
            await self._svc._deliver_email(target, "Subject\nBody", 99)
            mock_smtp.send.assert_not_called()

    async def test_email_ssrf_blocked_attempt_is_1(self):
        """SSRF blocked delivery has attempt=1."""
        config = json.dumps({"smtp_host": "192.168.1.1"})
        target = self._email_target(extra_config=config)
        self._svc._update_delivery = MagicMock()
        self._svc._inc_delivery_counter = MagicMock()
        with patch("shoreguard.services.webhooks.is_private_ip", return_value=True):
            await self._svc._deliver_email(target, "Subject\nBody", 99)
        kw = self._svc._update_delivery.call_args
        assert kw.kwargs.get("attempt", kw[1].get("attempt")) == 1

    async def test_email_only_user_no_pass_skips_tls(self):
        """When only smtp_user present (no smtp_pass), no TLS/auth."""
        config = json.dumps(
            {
                "smtp_host": "smtp.test.com",
                "smtp_port": 587,
                "smtp_user": "user@test.com",
                # no smtp_pass
                "to_addrs": ["admin@test.com"],
            }
        )
        target = self._email_target(extra_config=config)
        mock_smtp = MagicMock()
        mock_smtp.send = AsyncMock()
        with (
            patch.dict("sys.modules", {"aiosmtplib": mock_smtp}),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
        ):
            await self._svc._deliver_email(target, "Subject\nBody", 1)
            kwargs = mock_smtp.send.call_args[1]
            # smtp_user truthy but smtp_pass is None/falsy -> no auth
            assert "use_tls" not in kwargs

    async def test_email_only_pass_no_user_skips_tls(self):
        """When only smtp_pass present (no smtp_user), no TLS/auth."""
        config = json.dumps(
            {
                "smtp_host": "smtp.test.com",
                "smtp_port": 587,
                "smtp_pass": "secret",
                # no smtp_user
                "to_addrs": ["admin@test.com"],
            }
        )
        target = self._email_target(extra_config=config)
        mock_smtp = MagicMock()
        mock_smtp.send = AsyncMock()
        with (
            patch.dict("sys.modules", {"aiosmtplib": mock_smtp}),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
        ):
            await self._svc._deliver_email(target, "Subject\nBody", 1)
            kwargs = mock_smtp.send.call_args[1]
            assert "use_tls" not in kwargs

    async def test_email_single_line_body_subject(self):
        """Single line body: subject = entire body, no IndexError."""
        target = self._email_target()
        mock_smtp = MagicMock()
        mock_smtp.send = AsyncMock()
        with (
            patch.dict("sys.modules", {"aiosmtplib": mock_smtp}),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
        ):
            await self._svc._deliver_email(target, "Only subject no newline", 1)
            msg = mock_smtp.send.call_args[0][0]
            assert msg["Subject"] == "Only subject no newline"


class TestHTTPDeliveryComprehensive:
    """Kill mutation survivors in _deliver_http_with_retry."""

    @pytest.fixture(autouse=True)
    def _patch_sleep(self):
        with patch("shoreguard.services.webhooks.asyncio.sleep", new_callable=AsyncMock) as mock:
            self._mock_sleep = mock
            yield

    @pytest.fixture(autouse=True)
    def _setup(self, webhook_svc):
        webhook_svc._update_delivery = MagicMock()
        webhook_svc._inc_delivery_counter = MagicMock()
        self._svc = webhook_svc

    def _make_client_mock(self, responses):
        """Create an AsyncClient mock with responses (list or single)."""
        mock_cls = MagicMock()
        mock_client = AsyncMock()
        if isinstance(responses, list):
            mock_client.post.side_effect = responses
        else:
            mock_client.post.return_value = responses
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        return mock_cls, mock_client

    async def test_hmac_signature_exact_value(self):
        """HMAC signature must match exactly sha256=hex(hmac(secret, body))."""
        body = '{"event":"test","data":{}}'
        secret = "mysecret"
        expected_sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        mock_cls, mock_client = self._make_client_mock(httpx.Response(200))
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(
                _make_target(secret=secret, channel_type="generic"), body, 1
            )
        headers = mock_client.post.call_args.kwargs["headers"]
        assert headers["X-Shoreguard-Signature"] == f"sha256={expected_sig}"

    async def test_content_type_header_always_json(self):
        """Content-Type header is application/json for all channel types."""
        for ch_type in ["generic", "slack", "discord"]:
            mock_cls, mock_client = self._make_client_mock(httpx.Response(200))
            with (
                patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
                patch(
                    "shoreguard.services.webhooks._webhook_settings",
                    return_value=_mock_webhook_settings(),
                ),
                patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
            ):
                await self._svc._deliver_http_with_retry(
                    _make_target(channel_type=ch_type), "{}", 1
                )
            headers = mock_client.post.call_args.kwargs["headers"]
            assert headers["Content-Type"] == "application/json"

    async def test_success_response_code_recorded(self):
        """Success records the exact HTTP response code."""
        mock_cls, mock_client = self._make_client_mock(httpx.Response(201))
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(_make_target(), "{}", 42)
        kw = self._svc._update_delivery.call_args.kwargs
        assert kw["status"] == "success"
        assert kw["response_code"] == 201
        assert kw["attempt"] == 1

    async def test_success_on_status_399(self):
        """Status 399 is < 400, so counts as success."""
        mock_cls, mock_client = self._make_client_mock(httpx.Response(399))
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(_make_target(), "{}", 1)
        assert self._svc._update_delivery.call_args.kwargs["status"] == "success"

    async def test_4xx_records_exact_error_message(self):
        """4xx error records 'HTTP <code>' as error_message."""
        mock_cls, _ = self._make_client_mock(httpx.Response(403))
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(_make_target(), "{}", 42)
        kw = self._svc._update_delivery.call_args.kwargs
        assert kw["status"] == "failed"
        assert kw["response_code"] == 403
        assert kw["error_message"] == "HTTP 403"
        assert kw["attempt"] == 1

    async def test_4xx_increments_failed_counter(self):
        """4xx calls _inc_delivery_counter('failed')."""
        mock_cls, _ = self._make_client_mock(httpx.Response(422))
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(_make_target(), "{}", 1)
        self._svc._inc_delivery_counter.assert_called_once_with("failed")

    async def test_400_boundary_no_retry(self):
        """Status 400 is >= 400 and < 500, so no retry."""
        mock_cls, mock_client = self._make_client_mock(httpx.Response(400))
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(_make_target(), "{}", 1)
        assert mock_client.post.call_count == 1
        assert self._svc._update_delivery.call_args.kwargs["status"] == "failed"
        self._mock_sleep.assert_not_called()

    async def test_499_boundary_no_retry(self):
        """Status 499 is < 500, client error, no retry."""
        mock_cls, mock_client = self._make_client_mock(httpx.Response(499))
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(_make_target(), "{}", 1)
        assert mock_client.post.call_count == 1
        self._mock_sleep.assert_not_called()

    async def test_500_triggers_retry(self):
        """Status 500 triggers retries."""
        mock_cls, mock_client = self._make_client_mock([httpx.Response(500), httpx.Response(200)])
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(_make_target(), "{}", 1)
        assert mock_client.post.call_count == 2
        assert self._svc._update_delivery.call_args.kwargs["status"] == "success"
        assert self._svc._update_delivery.call_args.kwargs["attempt"] == 2

    async def test_all_5xx_exhausted_error_message(self):
        """All retries exhausted: error_message contains last status code."""
        max_attempts = len(RETRY_DELAYS) + 1
        mock_cls, mock_client = self._make_client_mock([httpx.Response(503)] * max_attempts)
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(_make_target(), "{}", 42)
        kw = self._svc._update_delivery.call_args.kwargs
        assert kw["status"] == "failed"
        assert kw["error_message"] == "HTTP 503"
        assert kw["attempt"] == max_attempts
        self._svc._inc_delivery_counter.assert_called_once_with("failed")

    async def test_retry_delays_exact_values(self):
        """Retries use exact delay values from settings."""
        max_attempts = len(RETRY_DELAYS) + 1
        mock_cls, _ = self._make_client_mock([httpx.Response(502)] * max_attempts)
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(_make_target(), "{}", 1)
        sleep_calls = [c[0][0] for c in self._mock_sleep.call_args_list]
        assert sleep_calls == [5, 30, 120]

    async def test_retry_count_matches_delays_plus_one(self):
        """Total attempts = len(retry_delays) + 1."""
        max_attempts = len(RETRY_DELAYS) + 1
        mock_cls, mock_client = self._make_client_mock(
            [httpx.TimeoutException("timeout")] * max_attempts
        )
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(_make_target(), "{}", 1)
        assert mock_client.post.call_count == max_attempts
        assert self._mock_sleep.call_count == len(RETRY_DELAYS)

    async def test_os_error_triggers_retry(self):
        """OSError triggers retry like TimeoutException."""
        mock_cls, mock_client = self._make_client_mock(
            [OSError("network down"), httpx.Response(200)]
        )
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(_make_target(), "{}", 1)
        assert mock_client.post.call_count == 2
        assert self._svc._update_delivery.call_args.kwargs["status"] == "success"

    async def test_timeout_error_message_recorded(self):
        """Timeout error message is str(exception)."""
        max_attempts = len(RETRY_DELAYS) + 1
        mock_cls, _ = self._make_client_mock(
            [httpx.TimeoutException("request timed out")] * max_attempts
        )
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(_make_target(), "{}", 1)
        assert self._svc._update_delivery.call_args.kwargs["error_message"] == "request timed out"

    async def test_ssrf_blocks_private_url(self):
        """SSRF: private URL blocked, delivery marked failed, no HTTP call."""
        target = _make_target(url="http://192.168.1.1/hook")
        self._svc._update_delivery = MagicMock()
        self._svc._inc_delivery_counter = MagicMock()
        mock_cls, mock_client = self._make_client_mock(httpx.Response(200))
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=True),
        ):
            await self._svc._deliver_http_with_retry(target, "{}", 99)
        mock_client.post.assert_not_called()
        kw = self._svc._update_delivery.call_args.kwargs
        assert kw["status"] == "failed"
        assert "SSRF" in kw["error_message"]
        assert kw["attempt"] == 1
        self._svc._inc_delivery_counter.assert_called_once_with("failed")

    async def test_ssrf_non_private_proceeds(self):
        """Non-private URL proceeds normally."""
        mock_cls, mock_client = self._make_client_mock(httpx.Response(200))
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(_make_target(), "{}", 1)
        mock_client.post.assert_called_once()

    async def test_no_signature_for_slack_channel(self):
        """Slack channel does not get X-Shoreguard-Signature."""
        mock_cls, mock_client = self._make_client_mock(httpx.Response(200))
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(_make_target(channel_type="slack"), "{}", 1)
        headers = mock_client.post.call_args.kwargs["headers"]
        assert "X-Shoreguard-Signature" not in headers
        assert headers["Content-Type"] == "application/json"

    async def test_no_signature_for_discord_channel(self):
        """Discord channel does not get X-Shoreguard-Signature."""
        mock_cls, mock_client = self._make_client_mock(httpx.Response(200))
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(_make_target(channel_type="discord"), "{}", 1)
        headers = mock_client.post.call_args.kwargs["headers"]
        assert "X-Shoreguard-Signature" not in headers

    async def test_post_called_with_exact_url_and_body(self):
        """client.post called with exact target URL and body."""
        mock_cls, mock_client = self._make_client_mock(httpx.Response(200))
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(
                _make_target(url="https://hooks.example.com/abc"), '{"key":"val"}', 1
            )
        assert mock_client.post.call_args.args[0] == "https://hooks.example.com/abc"
        assert mock_client.post.call_args.kwargs["content"] == '{"key":"val"}'

    async def test_success_returns_early_no_further_attempts(self):
        """After success on attempt 2, no further attempts or sleeps."""
        mock_cls, mock_client = self._make_client_mock(
            [httpx.Response(502), httpx.Response(200), httpx.Response(200)]
        )
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(_make_target(), "{}", 1)
        assert mock_client.post.call_count == 2
        assert self._mock_sleep.call_count == 1
        self._svc._inc_delivery_counter.assert_called_once_with("success")

    async def test_client_timeout_uses_settings(self):
        """AsyncClient created with timeout from settings."""
        mock_cls, _ = self._make_client_mock(httpx.Response(200))
        cfg = _mock_webhook_settings(delivery_timeout=42.0)
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch("shoreguard.services.webhooks._webhook_settings", return_value=cfg),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(_make_target(), "{}", 1)
        assert mock_cls.call_args.kwargs["timeout"] == 42.0

    async def test_custom_retry_delays(self):
        """Custom retry_delays from settings are used."""
        cfg = _mock_webhook_settings(retry_delays=[1, 2])
        responses = [httpx.Response(500)] * 3  # 3 attempts
        mock_cls, mock_client = self._make_client_mock(responses)
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch("shoreguard.services.webhooks._webhook_settings", return_value=cfg),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(_make_target(), "{}", 1)
        assert mock_client.post.call_count == 3
        sleep_calls = [c[0][0] for c in self._mock_sleep.call_args_list]
        assert sleep_calls == [1, 2]

    async def test_success_no_sleep_called(self):
        """First-attempt success never calls asyncio.sleep."""
        mock_cls, _ = self._make_client_mock(httpx.Response(200))
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(_make_target(), "{}", 1)
        self._mock_sleep.assert_not_called()

    async def test_connect_error_message_recorded(self):
        """ConnectError message preserved in error_message."""
        max_attempts = len(RETRY_DELAYS) + 1
        mock_cls, _ = self._make_client_mock(
            [httpx.ConnectError("Connection refused")] * max_attempts
        )
        with (
            patch("shoreguard.services.webhooks.httpx.AsyncClient", mock_cls),
            patch(
                "shoreguard.services.webhooks._webhook_settings",
                return_value=_mock_webhook_settings(),
            ),
            patch("shoreguard.services.webhooks.is_private_ip", return_value=False),
        ):
            await self._svc._deliver_http_with_retry(_make_target(), "{}", 1)
        assert self._svc._update_delivery.call_args.kwargs["error_message"] == "Connection refused"


class TestFireComprehensive:
    """Kill mutation survivors in fire()."""

    async def test_fire_creates_delivery_record(self, webhook_svc):
        """fire() creates a delivery record in the DB."""
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["test.event"],
            created_by="test",
        )
        with patch.object(WebhookService, "_deliver", new_callable=AsyncMock):
            await webhook_svc.fire("test.event", {"sandbox": "s1"})
            await asyncio.sleep(0.05)
        deliveries = webhook_svc.list_deliveries(wh["id"])
        assert len(deliveries) == 1
        assert deliveries[0]["event_type"] == "test.event"
        assert deliveries[0]["status"] == "pending"

    async def test_fire_delivery_payload_json(self, webhook_svc):
        """fire() stores the payload as JSON in the delivery record."""
        webhook_svc.create(
            url="https://example.com/hook",
            event_types=["test.event"],
            created_by="test",
        )
        with patch.object(WebhookService, "_deliver", new_callable=AsyncMock):
            await webhook_svc.fire("test.event", {"key": "value"})
            await asyncio.sleep(0.05)
        with webhook_svc._session_factory() as session:
            rows = session.query(WebhookDelivery).all()
            assert len(rows) == 1
            payload = json.loads(rows[0].payload_json)
            assert payload == {"key": "value"}

    async def test_fire_uses_correct_formatter(self, webhook_svc):
        """fire() passes formatter output as body to _deliver."""
        webhook_svc.create(
            url="https://hooks.slack.com/T/B/x",
            event_types=["*"],
            created_by="test",
            channel_type="slack",
        )
        with patch.object(WebhookService, "_deliver", new_callable=AsyncMock) as mock_del:
            await webhook_svc.fire("sandbox.created", {"sandbox": "s1"})
            await asyncio.sleep(0.05)
            args = mock_del.call_args[0]
            target = args[0]
            body = args[1]
            assert target.channel_type == "slack"
            parsed = json.loads(body)
            assert "attachments" in parsed  # Slack format

    async def test_fire_passes_delivery_id(self, webhook_svc):
        """fire() passes delivery_id to _deliver."""
        webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        with patch.object(WebhookService, "_deliver", new_callable=AsyncMock) as mock_del:
            await webhook_svc.fire("test.event", {"a": 1})
            await asyncio.sleep(0.05)
            delivery_id = mock_del.call_args[0][2]
            assert isinstance(delivery_id, int)
            assert delivery_id > 0

    async def test_fire_no_targets_returns_early(self, webhook_svc):
        """fire() returns early when no targets match."""
        with patch.object(WebhookService, "_deliver", new_callable=AsyncMock) as mock_del:
            await webhook_svc.fire("nonexistent.event", {})
            await asyncio.sleep(0.05)
            mock_del.assert_not_called()

    async def test_fire_multiple_targets(self, webhook_svc):
        """fire() delivers to all matching targets."""
        webhook_svc.create(
            url="https://a.com/hook",
            event_types=["*"],
            created_by="test",
        )
        webhook_svc.create(
            url="https://b.com/hook",
            event_types=["*"],
            created_by="test",
        )
        with patch.object(WebhookService, "_deliver", new_callable=AsyncMock) as mock_del:
            await webhook_svc.fire("test.event", {"a": 1})
            await asyncio.sleep(0.05)
            assert mock_del.call_count == 2

    async def test_fire_email_channel_routes_to_email_deliver(self, webhook_svc):
        """fire() routes email channel through _deliver -> _deliver_email."""
        webhook_svc.create(
            url="admin@test.com",
            event_types=["*"],
            created_by="test",
            channel_type="email",
            extra_config=json.dumps({"smtp_host": "smtp.test.com"}),
        )
        with patch.object(WebhookService, "_deliver_email", new_callable=AsyncMock) as mock_email:
            with patch.object(
                WebhookService, "_deliver_http_with_retry", new_callable=AsyncMock
            ) as mock_http:
                # Don't mock _deliver itself, let it route
                await webhook_svc.fire("test.event", {"a": 1})
                await asyncio.sleep(0.1)
                mock_email.assert_called_once()
                mock_http.assert_not_called()

    async def test_fire_generic_channel_routes_to_http(self, webhook_svc):
        """fire() routes generic channel through _deliver -> _deliver_http_with_retry."""
        webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        with patch.object(WebhookService, "_deliver_email", new_callable=AsyncMock) as mock_email:
            with patch.object(
                WebhookService, "_deliver_http_with_retry", new_callable=AsyncMock
            ) as mock_http:
                await webhook_svc.fire("test.event", {"a": 1})
                await asyncio.sleep(0.1)
                mock_http.assert_called_once()
                mock_email.assert_not_called()


class TestGetActiveForEvent:
    """Kill mutation survivors in _get_active_for_event."""

    def test_returns_target_with_correct_fields(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["test.event"],
            created_by="test",
            channel_type="generic",
        )
        targets = webhook_svc._get_active_for_event("test.event")
        assert len(targets) == 1
        t = targets[0]
        assert t.webhook_id == wh["id"]
        assert t.url == "https://example.com/hook"
        assert t.channel_type == "generic"
        assert t.secret == wh["secret"]
        assert t.extra_config is None

    def test_wildcard_matches_any_event(self, webhook_svc):
        webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        assert len(webhook_svc._get_active_for_event("anything.here")) == 1
        assert len(webhook_svc._get_active_for_event("other.event")) == 1

    def test_specific_event_does_not_match_other(self, webhook_svc):
        webhook_svc.create(
            url="https://example.com/hook",
            event_types=["approval.approved"],
            created_by="test",
        )
        assert len(webhook_svc._get_active_for_event("sandbox.created")) == 0

    def test_inactive_webhooks_excluded(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        webhook_svc.update(wh["id"], is_active=False)
        assert len(webhook_svc._get_active_for_event("test.event")) == 0

    def test_multiple_event_types(self, webhook_svc):
        webhook_svc.create(
            url="https://example.com/hook",
            event_types=["approval.approved", "sandbox.created"],
            created_by="test",
        )
        assert len(webhook_svc._get_active_for_event("approval.approved")) == 1
        assert len(webhook_svc._get_active_for_event("sandbox.created")) == 1
        assert len(webhook_svc._get_active_for_event("sandbox.deleted")) == 0

    def test_extra_config_preserved(self, webhook_svc):
        cfg = json.dumps({"smtp_host": "smtp.test.com"})
        webhook_svc.create(
            url="admin@test.com",
            event_types=["*"],
            created_by="test",
            channel_type="email",
            extra_config=cfg,
        )
        targets = webhook_svc._get_active_for_event("test.event")
        assert targets[0].extra_config == cfg

    def test_db_error_returns_empty(self, webhook_svc):
        """SQLAlchemyError returns empty list."""
        from sqlalchemy.exc import SQLAlchemyError

        with patch.object(webhook_svc, "_session_factory", side_effect=SQLAlchemyError("db err")):
            result = webhook_svc._get_active_for_event("test.event")
            assert result == []


class TestListDeliveriesComprehensive:
    """Kill mutation survivors in list_deliveries."""

    def test_delivery_dict_keys(self, webhook_svc):
        """All expected keys present in delivery dict."""
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        webhook_svc._create_delivery(wh["id"], "test.event", '{"a":1}')
        deliveries = webhook_svc.list_deliveries(wh["id"])
        assert len(deliveries) == 1
        d = deliveries[0]
        expected_keys = {
            "id",
            "webhook_id",
            "event_type",
            "status",
            "response_code",
            "error_message",
            "attempt",
            "created_at",
            "delivered_at",
        }
        assert set(d.keys()) == expected_keys

    def test_delivery_field_values(self, webhook_svc):
        """Delivery dict has correct initial values."""
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        webhook_svc._create_delivery(wh["id"], "sandbox.created", '{"x":1}')
        d = webhook_svc.list_deliveries(wh["id"])[0]
        assert d["webhook_id"] == wh["id"]
        assert d["event_type"] == "sandbox.created"
        assert d["status"] == "pending"
        assert d["response_code"] is None
        assert d["error_message"] is None
        assert d["attempt"] == 1
        assert d["created_at"] is not None
        assert d["delivered_at"] is None

    def test_limit_parameter(self, webhook_svc):
        """list_deliveries respects the limit parameter."""
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        for i in range(5):
            webhook_svc._create_delivery(wh["id"], f"event.{i}", "{}")
        assert len(webhook_svc.list_deliveries(wh["id"], limit=3)) == 3
        assert len(webhook_svc.list_deliveries(wh["id"], limit=50)) == 5

    def test_order_newest_first(self, webhook_svc):
        """Deliveries are ordered newest first (desc by created_at)."""
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        webhook_svc._create_delivery(wh["id"], "event.first", "{}")
        webhook_svc._create_delivery(wh["id"], "event.second", "{}")
        deliveries = webhook_svc.list_deliveries(wh["id"])
        assert deliveries[0]["id"] > deliveries[1]["id"]

    def test_only_returns_for_specified_webhook(self, webhook_svc):
        """list_deliveries filters by webhook_id."""
        wh1 = webhook_svc.create(
            url="https://a.com/hook",
            event_types=["*"],
            created_by="test",
        )
        wh2 = webhook_svc.create(
            url="https://b.com/hook",
            event_types=["*"],
            created_by="test",
        )
        webhook_svc._create_delivery(wh1["id"], "event.a", "{}")
        webhook_svc._create_delivery(wh2["id"], "event.b", "{}")
        deliveries = webhook_svc.list_deliveries(wh1["id"])
        assert len(deliveries) == 1
        assert deliveries[0]["webhook_id"] == wh1["id"]

    def test_db_error_returns_empty(self, webhook_svc):
        from sqlalchemy.exc import SQLAlchemyError

        with patch.object(webhook_svc, "_session_factory", side_effect=SQLAlchemyError("err")):
            assert webhook_svc.list_deliveries(1) == []


class TestCleanupComprehensive:
    """Kill mutation survivors in cleanup_old_deliveries."""

    def test_returns_exact_count_deleted(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        for _ in range(3):
            d_id = webhook_svc._create_delivery(wh["id"], "test", "{}")
            with webhook_svc._session_factory() as session:
                row = session.get(WebhookDelivery, d_id)
                row.created_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=30)
                session.commit()
        assert webhook_svc.cleanup_old_deliveries(max_age_days=7) == 3

    def test_keeps_recent_records(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        webhook_svc._create_delivery(wh["id"], "test", "{}")
        assert webhook_svc.cleanup_old_deliveries(max_age_days=7) == 0
        assert len(webhook_svc.list_deliveries(wh["id"])) == 1

    def test_mixed_old_and_new(self, webhook_svc):
        """Only old records deleted, new ones kept."""
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        # Old record
        old_id = webhook_svc._create_delivery(wh["id"], "old", "{}")
        with webhook_svc._session_factory() as session:
            row = session.get(WebhookDelivery, old_id)
            row.created_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=30)
            session.commit()
        # New record
        webhook_svc._create_delivery(wh["id"], "new", "{}")

        purged = webhook_svc.cleanup_old_deliveries(max_age_days=7)
        assert purged == 1
        remaining = webhook_svc.list_deliveries(wh["id"])
        assert len(remaining) == 1
        assert remaining[0]["event_type"] == "new"

    def test_uses_default_max_age_from_settings(self, webhook_svc):
        """When max_age_days=None, uses settings value."""
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        d_id = webhook_svc._create_delivery(wh["id"], "test", "{}")
        with webhook_svc._session_factory() as session:
            row = session.get(WebhookDelivery, d_id)
            row.created_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=10)
            session.commit()
        with patch(
            "shoreguard.services.webhooks._webhook_settings",
            return_value=_mock_webhook_settings(delivery_max_age_days=7),
        ):
            assert webhook_svc.cleanup_old_deliveries() == 1

    def test_db_error_returns_zero(self, webhook_svc):
        from sqlalchemy.exc import SQLAlchemyError

        with patch.object(webhook_svc, "_session_factory", side_effect=SQLAlchemyError("err")):
            assert webhook_svc.cleanup_old_deliveries(max_age_days=7) == 0

    def test_boundary_exact_age(self, webhook_svc):
        """Record exactly at cutoff boundary (< cutoff) should be deleted."""
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        d_id = webhook_svc._create_delivery(wh["id"], "test", "{}")
        with webhook_svc._session_factory() as session:
            row = session.get(WebhookDelivery, d_id)
            # Exactly 7 days + 1 second ago
            row.created_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
                days=7, seconds=1
            )
            session.commit()
        assert webhook_svc.cleanup_old_deliveries(max_age_days=7) == 1


class TestUpdateDeliveryComprehensive:
    """Kill mutation survivors in _update_delivery."""

    def test_success_sets_delivered_at(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        d_id = webhook_svc._create_delivery(wh["id"], "test", "{}")
        webhook_svc._update_delivery(d_id, status="success", response_code=200, attempt=1)
        d = webhook_svc.list_deliveries(wh["id"])[0]
        assert d["delivered_at"] is not None

    def test_failed_does_not_set_delivered_at(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        d_id = webhook_svc._create_delivery(wh["id"], "test", "{}")
        webhook_svc._update_delivery(d_id, status="failed", error_message="err", attempt=3)
        d = webhook_svc.list_deliveries(wh["id"])[0]
        assert d["delivered_at"] is None
        assert d["status"] == "failed"
        assert d["error_message"] == "err"
        assert d["attempt"] == 3

    def test_nonexistent_delivery_no_crash(self, webhook_svc):
        """Updating a nonexistent delivery doesn't raise."""
        webhook_svc._update_delivery(99999, status="success")

    def test_response_code_none_for_network_error(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        d_id = webhook_svc._create_delivery(wh["id"], "test", "{}")
        webhook_svc._update_delivery(d_id, status="failed", error_message="timeout", attempt=4)
        d = webhook_svc.list_deliveries(wh["id"])[0]
        assert d["response_code"] is None

    def test_all_fields_updated(self, webhook_svc):
        """All fields (status, response_code, error_message, attempt) are set."""
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        d_id = webhook_svc._create_delivery(wh["id"], "test", "{}")
        webhook_svc._update_delivery(
            d_id,
            status="failed",
            response_code=502,
            error_message="HTTP 502",
            attempt=3,
        )
        d = webhook_svc.list_deliveries(wh["id"])[0]
        assert d["status"] == "failed"
        assert d["response_code"] == 502
        assert d["error_message"] == "HTTP 502"
        assert d["attempt"] == 3

    def test_db_error_handled_gracefully(self, webhook_svc):
        from sqlalchemy.exc import SQLAlchemyError

        with patch.object(webhook_svc, "_session_factory", side_effect=SQLAlchemyError("err")):
            # Should not raise
            webhook_svc._update_delivery(1, status="success")


class TestCreateDelivery:
    """Kill mutation survivors in _create_delivery."""

    def test_creates_pending_delivery(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        d_id = webhook_svc._create_delivery(wh["id"], "sandbox.created", '{"x":1}')
        assert isinstance(d_id, int)
        assert d_id > 0

    def test_delivery_initial_fields(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        d_id = webhook_svc._create_delivery(wh["id"], "sandbox.created", '{"x":1}')
        with webhook_svc._session_factory() as session:
            row = session.get(WebhookDelivery, d_id)
            assert row.webhook_id == wh["id"]
            assert row.event_type == "sandbox.created"
            assert row.payload_json == '{"x":1}'
            assert row.status == "pending"
            assert row.attempt == 1
            assert row.created_at is not None


class TestCRUDComprehensive:
    """Kill mutation survivors in CRUD methods."""

    def test_list_db_error_returns_empty(self, webhook_svc):
        from sqlalchemy.exc import SQLAlchemyError

        with patch.object(webhook_svc, "_session_factory", side_effect=SQLAlchemyError("err")):
            assert webhook_svc.list() == []

    def test_get_db_error_returns_none(self, webhook_svc):
        from sqlalchemy.exc import SQLAlchemyError

        with patch.object(webhook_svc, "_session_factory", side_effect=SQLAlchemyError("err")):
            assert webhook_svc.get(1) is None

    def test_update_db_error_returns_none(self, webhook_svc):
        from sqlalchemy.exc import SQLAlchemyError

        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        with patch.object(webhook_svc, "_session_factory", side_effect=SQLAlchemyError("err")):
            assert webhook_svc.update(wh["id"], url="https://new.com") is None

    def test_delete_db_error_returns_false(self, webhook_svc):
        from sqlalchemy.exc import SQLAlchemyError

        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        with patch.object(webhook_svc, "_session_factory", side_effect=SQLAlchemyError("err")):
            assert webhook_svc.delete(wh["id"]) is False

    def test_create_sets_is_active_true(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        assert wh["is_active"] is True

    def test_create_sets_created_at(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        assert wh["created_at"] is not None

    def test_to_dict_keys(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["test"],
            created_by="admin",
        )
        fetched = webhook_svc.get(wh["id"])
        expected_keys = {
            "id",
            "url",
            "event_types",
            "is_active",
            "channel_type",
            "created_by",
            "created_at",
        }
        assert set(fetched.keys()) == expected_keys

    def test_to_dict_with_secret_keys(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["test"],
            created_by="admin",
        )
        expected_keys = {
            "id",
            "url",
            "event_types",
            "is_active",
            "channel_type",
            "created_by",
            "created_at",
            "secret",
        }
        assert set(wh.keys()) == expected_keys

    def test_to_dict_extra_config_json(self, webhook_svc):
        """extra_config is parsed as JSON when present."""
        cfg = json.dumps({"smtp_host": "mail.test.com"})
        wh = webhook_svc.create(
            url="admin@test.com",
            event_types=["*"],
            created_by="test",
            channel_type="email",
            extra_config=cfg,
        )
        fetched = webhook_svc.get(wh["id"])
        assert fetched["extra_config"] == {"smtp_host": "mail.test.com"}

    def test_update_channel_type(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        updated = webhook_svc.update(wh["id"], channel_type="slack")
        assert updated["channel_type"] == "slack"

    def test_update_extra_config(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        cfg = json.dumps({"key": "val"})
        updated = webhook_svc.update(wh["id"], extra_config=cfg)
        assert updated is not None
        fetched = webhook_svc.get(wh["id"])
        assert fetched["extra_config"] == {"key": "val"}

    def test_update_partial_only_changes_specified(self, webhook_svc):
        """Updating only url doesn't change other fields."""
        wh = webhook_svc.create(
            url="https://old.com/hook",
            event_types=["a", "b"],
            created_by="test",
        )
        updated = webhook_svc.update(wh["id"], url="https://new.com/hook")
        assert updated["url"] == "https://new.com/hook"
        assert updated["event_types"] == ["a", "b"]
        assert updated["is_active"] is True

    def test_list_returns_newest_first(self, webhook_svc):
        wh1 = webhook_svc.create(
            url="https://a.com",
            event_types=["*"],
            created_by="test",
        )
        wh2 = webhook_svc.create(
            url="https://b.com",
            event_types=["*"],
            created_by="test",
        )
        all_wh = webhook_svc.list()
        assert all_wh[0]["id"] == wh2["id"]
        assert all_wh[1]["id"] == wh1["id"]

    def test_delete_returns_true_only_once(self, webhook_svc):
        wh = webhook_svc.create(
            url="https://example.com/hook",
            event_types=["*"],
            created_by="test",
        )
        assert webhook_svc.delete(wh["id"]) is True
        assert webhook_svc.delete(wh["id"]) is False


class TestShutdown:
    """Kill mutation survivors in shutdown."""

    async def test_shutdown_no_tasks(self, webhook_svc):
        cancelled = await webhook_svc.shutdown()
        assert cancelled == 0

    async def test_shutdown_cancels_tasks(self, webhook_svc):
        async def slow_task():
            await asyncio.sleep(100)

        task = asyncio.create_task(slow_task())
        webhook_svc._delivery_tasks.add(task)
        cancelled = await webhook_svc.shutdown(timeout=1.0)
        assert cancelled == 1

    async def test_shutdown_returns_task_count(self, webhook_svc):
        tasks = []
        for _ in range(3):

            async def slow():
                await asyncio.sleep(100)

            t = asyncio.create_task(slow())
            webhook_svc._delivery_tasks.add(t)
            tasks.append(t)
        cancelled = await webhook_svc.shutdown(timeout=1.0)
        assert cancelled == 3
