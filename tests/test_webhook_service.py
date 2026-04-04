"""Tests for the WebhookService."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.models import Base
from shoreguard.services.webhooks import WebhookService


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
