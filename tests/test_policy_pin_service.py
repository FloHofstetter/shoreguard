"""Tests for the PolicyPinService."""

import datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from shoreguard.exceptions import PolicyLockedError
from shoreguard.models import Base
from shoreguard.services.policy_pin import PolicyPinService


@pytest.fixture
async def pin_svc():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    svc = PolicyPinService(factory)
    yield svc
    await engine.dispose()


class TestPin:
    async def test_pin_creates_entry(self, pin_svc):
        result = await pin_svc.pin("gw1", "sb1", 5, "admin@test.com", reason="freeze")
        assert result["gateway_name"] == "gw1"
        assert result["sandbox_name"] == "sb1"
        assert result["pinned_version"] == 5
        assert result["pinned_by"] == "admin@test.com"
        assert result["reason"] == "freeze"
        assert result["expires_at"] is None

    async def test_pin_with_expiry(self, pin_svc):
        expires = datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)
        result = await pin_svc.pin("gw1", "sb1", 3, "admin@test.com", expires_at=expires)
        assert result["expires_at"] is not None

    async def test_pin_upserts_existing(self, pin_svc):
        await pin_svc.pin("gw1", "sb1", 5, "admin@test.com", reason="v1")
        result = await pin_svc.pin("gw1", "sb1", 7, "other@test.com", reason="v2")
        assert result["pinned_version"] == 7
        assert result["pinned_by"] == "other@test.com"
        assert result["reason"] == "v2"

    async def test_pin_different_sandboxes(self, pin_svc):
        await pin_svc.pin("gw1", "sb1", 1, "a@test.com")
        await pin_svc.pin("gw1", "sb2", 2, "b@test.com")
        assert (await pin_svc.get_pin("gw1", "sb1"))["pinned_version"] == 1
        assert (await pin_svc.get_pin("gw1", "sb2"))["pinned_version"] == 2

    async def test_pin_different_gateways(self, pin_svc):
        await pin_svc.pin("gw1", "sb1", 1, "a@test.com")
        await pin_svc.pin("gw2", "sb1", 2, "b@test.com")
        assert (await pin_svc.get_pin("gw1", "sb1"))["pinned_version"] == 1
        assert (await pin_svc.get_pin("gw2", "sb1"))["pinned_version"] == 2

    async def test_pin_no_reason(self, pin_svc):
        result = await pin_svc.pin("gw1", "sb1", 1, "a@test.com")
        assert result["reason"] is None


class TestUnpin:
    async def test_unpin_existing(self, pin_svc):
        await pin_svc.pin("gw1", "sb1", 5, "admin@test.com")
        assert await pin_svc.unpin("gw1", "sb1") is True
        assert await pin_svc.get_pin("gw1", "sb1") is None

    async def test_unpin_nonexistent(self, pin_svc):
        assert await pin_svc.unpin("gw1", "sb1") is False

    async def test_unpin_only_targeted(self, pin_svc):
        await pin_svc.pin("gw1", "sb1", 1, "a@test.com")
        await pin_svc.pin("gw1", "sb2", 2, "b@test.com")
        await pin_svc.unpin("gw1", "sb1")
        assert await pin_svc.get_pin("gw1", "sb1") is None
        assert await pin_svc.get_pin("gw1", "sb2") is not None


class TestGetPin:
    async def test_get_pin_existing(self, pin_svc):
        await pin_svc.pin("gw1", "sb1", 5, "admin@test.com", reason="freeze")
        result = await pin_svc.get_pin("gw1", "sb1")
        assert result is not None
        assert result["pinned_version"] == 5

    async def test_get_pin_nonexistent(self, pin_svc):
        assert await pin_svc.get_pin("gw1", "sb1") is None

    async def test_get_pin_expired(self, pin_svc):
        past = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        await pin_svc.pin("gw1", "sb1", 5, "admin@test.com", expires_at=past)
        assert await pin_svc.get_pin("gw1", "sb1") is None

    async def test_get_pin_not_yet_expired(self, pin_svc):
        future = datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)
        await pin_svc.pin("gw1", "sb1", 5, "admin@test.com", expires_at=future)
        result = await pin_svc.get_pin("gw1", "sb1")
        assert result is not None

    async def test_get_pin_returns_iso_timestamp(self, pin_svc):
        await pin_svc.pin("gw1", "sb1", 5, "admin@test.com")
        result = await pin_svc.get_pin("gw1", "sb1")
        # pinned_at should be a valid ISO 8601 string
        datetime.datetime.fromisoformat(result["pinned_at"])


class TestIsPinned:
    async def test_is_pinned_true(self, pin_svc):
        await pin_svc.pin("gw1", "sb1", 5, "admin@test.com")
        assert await pin_svc.is_pinned("gw1", "sb1") is True

    async def test_is_pinned_false(self, pin_svc):
        assert await pin_svc.is_pinned("gw1", "sb1") is False

    async def test_is_pinned_after_unpin(self, pin_svc):
        await pin_svc.pin("gw1", "sb1", 5, "admin@test.com")
        await pin_svc.unpin("gw1", "sb1")
        assert await pin_svc.is_pinned("gw1", "sb1") is False

    async def test_is_pinned_expired(self, pin_svc):
        past = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        await pin_svc.pin("gw1", "sb1", 5, "admin@test.com", expires_at=past)
        assert await pin_svc.is_pinned("gw1", "sb1") is False


class TestCheckPin:
    async def test_check_pin_raises_when_pinned(self, pin_svc):
        await pin_svc.pin("gw1", "sb1", 5, "admin@test.com")
        with pytest.raises(PolicyLockedError, match="pinned at version 5"):
            await pin_svc.check_pin("gw1", "sb1")

    async def test_check_pin_silent_when_not_pinned(self, pin_svc):
        await pin_svc.check_pin("gw1", "sb1")  # should not raise

    async def test_check_pin_silent_when_expired(self, pin_svc):
        past = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        await pin_svc.pin("gw1", "sb1", 5, "admin@test.com", expires_at=past)
        await pin_svc.check_pin("gw1", "sb1")  # should not raise

    async def test_check_pin_message_includes_actor(self, pin_svc):
        await pin_svc.pin("gw1", "sb1", 3, "ops@corp.com")
        with pytest.raises(PolicyLockedError, match="ops@corp.com"):
            await pin_svc.check_pin("gw1", "sb1")


class TestExpiry:
    async def test_expired_pin_auto_deleted(self, pin_svc):
        past = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        await pin_svc.pin("gw1", "sb1", 5, "admin@test.com", expires_at=past)
        # First call should auto-delete
        assert await pin_svc.get_pin("gw1", "sb1") is None
        # Subsequent is_pinned should also be False
        assert await pin_svc.is_pinned("gw1", "sb1") is False

    async def test_upsert_clears_expiry(self, pin_svc):
        past = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        await pin_svc.pin("gw1", "sb1", 5, "admin@test.com", expires_at=past)
        # Auto-expire
        await pin_svc.get_pin("gw1", "sb1")
        # Re-pin without expiry
        await pin_svc.pin("gw1", "sb1", 6, "admin@test.com")
        result = await pin_svc.get_pin("gw1", "sb1")
        assert result is not None
        assert result["pinned_version"] == 6
        assert result["expires_at"] is None
