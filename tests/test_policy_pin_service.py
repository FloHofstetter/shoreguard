"""Tests for the PolicyPinService."""

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.exceptions import PolicyLockedError
from shoreguard.models import Base
from shoreguard.services.policy_pin import PolicyPinService


@pytest.fixture
def pin_svc():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    svc = PolicyPinService(factory)
    yield svc
    engine.dispose()


class TestPin:
    def test_pin_creates_entry(self, pin_svc):
        result = pin_svc.pin("gw1", "sb1", 5, "admin@test.com", reason="freeze")
        assert result["gateway_name"] == "gw1"
        assert result["sandbox_name"] == "sb1"
        assert result["pinned_version"] == 5
        assert result["pinned_by"] == "admin@test.com"
        assert result["reason"] == "freeze"
        assert result["expires_at"] is None

    def test_pin_with_expiry(self, pin_svc):
        expires = datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)
        result = pin_svc.pin("gw1", "sb1", 3, "admin@test.com", expires_at=expires)
        assert result["expires_at"] is not None

    def test_pin_upserts_existing(self, pin_svc):
        pin_svc.pin("gw1", "sb1", 5, "admin@test.com", reason="v1")
        result = pin_svc.pin("gw1", "sb1", 7, "other@test.com", reason="v2")
        assert result["pinned_version"] == 7
        assert result["pinned_by"] == "other@test.com"
        assert result["reason"] == "v2"

    def test_pin_different_sandboxes(self, pin_svc):
        pin_svc.pin("gw1", "sb1", 1, "a@test.com")
        pin_svc.pin("gw1", "sb2", 2, "b@test.com")
        assert pin_svc.get_pin("gw1", "sb1")["pinned_version"] == 1
        assert pin_svc.get_pin("gw1", "sb2")["pinned_version"] == 2

    def test_pin_different_gateways(self, pin_svc):
        pin_svc.pin("gw1", "sb1", 1, "a@test.com")
        pin_svc.pin("gw2", "sb1", 2, "b@test.com")
        assert pin_svc.get_pin("gw1", "sb1")["pinned_version"] == 1
        assert pin_svc.get_pin("gw2", "sb1")["pinned_version"] == 2

    def test_pin_no_reason(self, pin_svc):
        result = pin_svc.pin("gw1", "sb1", 1, "a@test.com")
        assert result["reason"] is None


class TestUnpin:
    def test_unpin_existing(self, pin_svc):
        pin_svc.pin("gw1", "sb1", 5, "admin@test.com")
        assert pin_svc.unpin("gw1", "sb1") is True
        assert pin_svc.get_pin("gw1", "sb1") is None

    def test_unpin_nonexistent(self, pin_svc):
        assert pin_svc.unpin("gw1", "sb1") is False

    def test_unpin_only_targeted(self, pin_svc):
        pin_svc.pin("gw1", "sb1", 1, "a@test.com")
        pin_svc.pin("gw1", "sb2", 2, "b@test.com")
        pin_svc.unpin("gw1", "sb1")
        assert pin_svc.get_pin("gw1", "sb1") is None
        assert pin_svc.get_pin("gw1", "sb2") is not None


class TestGetPin:
    def test_get_pin_existing(self, pin_svc):
        pin_svc.pin("gw1", "sb1", 5, "admin@test.com", reason="freeze")
        result = pin_svc.get_pin("gw1", "sb1")
        assert result is not None
        assert result["pinned_version"] == 5

    def test_get_pin_nonexistent(self, pin_svc):
        assert pin_svc.get_pin("gw1", "sb1") is None

    def test_get_pin_expired(self, pin_svc):
        past = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        pin_svc.pin("gw1", "sb1", 5, "admin@test.com", expires_at=past)
        assert pin_svc.get_pin("gw1", "sb1") is None

    def test_get_pin_not_yet_expired(self, pin_svc):
        future = datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)
        pin_svc.pin("gw1", "sb1", 5, "admin@test.com", expires_at=future)
        result = pin_svc.get_pin("gw1", "sb1")
        assert result is not None

    def test_get_pin_returns_iso_timestamp(self, pin_svc):
        pin_svc.pin("gw1", "sb1", 5, "admin@test.com")
        result = pin_svc.get_pin("gw1", "sb1")
        # pinned_at should be a valid ISO 8601 string
        datetime.datetime.fromisoformat(result["pinned_at"])


class TestIsPinned:
    def test_is_pinned_true(self, pin_svc):
        pin_svc.pin("gw1", "sb1", 5, "admin@test.com")
        assert pin_svc.is_pinned("gw1", "sb1") is True

    def test_is_pinned_false(self, pin_svc):
        assert pin_svc.is_pinned("gw1", "sb1") is False

    def test_is_pinned_after_unpin(self, pin_svc):
        pin_svc.pin("gw1", "sb1", 5, "admin@test.com")
        pin_svc.unpin("gw1", "sb1")
        assert pin_svc.is_pinned("gw1", "sb1") is False

    def test_is_pinned_expired(self, pin_svc):
        past = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        pin_svc.pin("gw1", "sb1", 5, "admin@test.com", expires_at=past)
        assert pin_svc.is_pinned("gw1", "sb1") is False


class TestCheckPin:
    def test_check_pin_raises_when_pinned(self, pin_svc):
        pin_svc.pin("gw1", "sb1", 5, "admin@test.com")
        with pytest.raises(PolicyLockedError, match="pinned at version 5"):
            pin_svc.check_pin("gw1", "sb1")

    def test_check_pin_silent_when_not_pinned(self, pin_svc):
        pin_svc.check_pin("gw1", "sb1")  # should not raise

    def test_check_pin_silent_when_expired(self, pin_svc):
        past = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        pin_svc.pin("gw1", "sb1", 5, "admin@test.com", expires_at=past)
        pin_svc.check_pin("gw1", "sb1")  # should not raise

    def test_check_pin_message_includes_actor(self, pin_svc):
        pin_svc.pin("gw1", "sb1", 3, "ops@corp.com")
        with pytest.raises(PolicyLockedError, match="ops@corp.com"):
            pin_svc.check_pin("gw1", "sb1")


class TestExpiry:
    def test_expired_pin_auto_deleted(self, pin_svc):
        past = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        pin_svc.pin("gw1", "sb1", 5, "admin@test.com", expires_at=past)
        # First call should auto-delete
        assert pin_svc.get_pin("gw1", "sb1") is None
        # Subsequent is_pinned should also be False
        assert pin_svc.is_pinned("gw1", "sb1") is False

    def test_upsert_clears_expiry(self, pin_svc):
        past = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        pin_svc.pin("gw1", "sb1", 5, "admin@test.com", expires_at=past)
        # Auto-expire
        pin_svc.get_pin("gw1", "sb1")
        # Re-pin without expiry
        pin_svc.pin("gw1", "sb1", 6, "admin@test.com")
        result = pin_svc.get_pin("gw1", "sb1")
        assert result is not None
        assert result["pinned_version"] == 6
        assert result["expires_at"] is None
