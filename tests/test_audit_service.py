"""Tests for the AuditService."""

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.models import Base
from shoreguard.services.audit import AuditService


@pytest.fixture
def audit_svc():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    svc = AuditService(factory)
    yield svc
    engine.dispose()


class TestLog:
    def test_log_creates_entry(self, audit_svc):
        audit_svc.log(
            actor="admin@test.com",
            actor_role="admin",
            action="sandbox.create",
            resource_type="sandbox",
            resource_id="my-sandbox",
            gateway="gw1",
        )
        entries = audit_svc.list()
        assert len(entries) == 1
        assert entries[0]["actor"] == "admin@test.com"
        assert entries[0]["action"] == "sandbox.create"
        assert entries[0]["resource_type"] == "sandbox"
        assert entries[0]["resource_id"] == "my-sandbox"
        assert entries[0]["gateway"] == "gw1"

    def test_log_with_detail_dict(self, audit_svc):
        audit_svc.log(
            actor="admin@test.com",
            actor_role="admin",
            action="policy.update",
            resource_type="policy",
            detail={"key": "pypi", "access": "allow"},
        )
        entries = audit_svc.list()
        assert entries[0]["detail"] == {"key": "pypi", "access": "allow"}

    def test_log_with_no_detail(self, audit_svc):
        audit_svc.log(
            actor="admin@test.com",
            actor_role="admin",
            action="user.login",
            resource_type="user",
        )
        entries = audit_svc.list()
        assert entries[0]["detail"] is None

    def test_log_with_client_ip(self, audit_svc):
        audit_svc.log(
            actor="admin@test.com",
            actor_role="admin",
            action="user.login",
            resource_type="user",
            client_ip="192.168.1.100",
        )
        entries = audit_svc.list()
        assert entries[0]["client_ip"] == "192.168.1.100"

    def test_log_default_resource_id(self, audit_svc):
        audit_svc.log(
            actor="admin@test.com",
            actor_role="admin",
            action="user.logout",
            resource_type="user",
        )
        entries = audit_svc.list()
        assert entries[0]["resource_id"] == ""

    def test_log_swallows_exceptions(self, audit_svc):
        """Log should never raise — failures are logged and swallowed."""
        # Close the engine to trigger a DB error
        audit_svc._session_factory.kw["bind"].dispose()
        # This should not raise
        audit_svc.log(
            actor="admin@test.com",
            actor_role="admin",
            action="sandbox.create",
            resource_type="sandbox",
        )

    def test_log_sets_timestamp(self, audit_svc):
        audit_svc.log(
            actor="admin@test.com",
            actor_role="admin",
            action="test",
            resource_type="test",
        )
        entries = audit_svc.list()
        assert entries[0]["timestamp"]
        # Should be a valid ISO 8601 timestamp
        datetime.datetime.fromisoformat(entries[0]["timestamp"])


class TestList:
    def _seed(self, svc, count=5):
        for i in range(count):
            svc.log(
                actor=f"user{i}@test.com",
                actor_role="admin" if i == 0 else "operator",
                action=f"action.{i % 3}",
                resource_type="sandbox" if i % 2 == 0 else "gateway",
                resource_id=f"resource-{i}",
            )

    def test_list_returns_all(self, audit_svc):
        self._seed(audit_svc, 5)
        entries = audit_svc.list()
        assert len(entries) == 5

    def test_list_ordered_desc(self, audit_svc):
        self._seed(audit_svc, 3)
        entries = audit_svc.list()
        assert entries[0]["timestamp"] >= entries[-1]["timestamp"]

    def test_list_pagination(self, audit_svc):
        self._seed(audit_svc, 10)
        page1 = audit_svc.list(limit=3, offset=0)
        page2 = audit_svc.list(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3
        assert page1[0]["id"] != page2[0]["id"]

    def test_list_filter_actor(self, audit_svc):
        self._seed(audit_svc, 5)
        entries = audit_svc.list(actor="user0@test.com")
        assert len(entries) == 1
        assert entries[0]["actor"] == "user0@test.com"

    def test_list_filter_action(self, audit_svc):
        self._seed(audit_svc, 6)
        entries = audit_svc.list(action="action.0")
        assert all(e["action"] == "action.0" for e in entries)

    def test_list_filter_resource_type(self, audit_svc):
        self._seed(audit_svc, 5)
        entries = audit_svc.list(resource_type="sandbox")
        assert all(e["resource_type"] == "sandbox" for e in entries)

    def test_list_filter_since(self, audit_svc):
        self._seed(audit_svc, 3)
        # All entries should be recent
        old_ts = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)).isoformat()
        entries = audit_svc.list(since=old_ts)
        assert len(entries) == 3

    def test_list_filter_until(self, audit_svc):
        self._seed(audit_svc, 3)
        # All entries should be before "tomorrow"
        future_ts = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1)).isoformat()
        entries = audit_svc.list(until=future_ts)
        assert len(entries) == 3

    def test_list_empty(self, audit_svc):
        entries = audit_svc.list()
        assert entries == []


class TestExportCsv:
    def test_export_csv_has_header(self, audit_svc):
        audit_svc.log(
            actor="admin@test.com",
            actor_role="admin",
            action="test",
            resource_type="test",
        )
        csv_data = audit_svc.export_csv()
        lines = csv_data.strip().split("\n")
        assert len(lines) == 2  # header + 1 row
        assert "timestamp" in lines[0]
        assert "actor" in lines[0]
        assert "action" in lines[0]

    def test_export_csv_empty(self, audit_svc):
        csv_data = audit_svc.export_csv()
        lines = csv_data.strip().split("\n")
        assert len(lines) == 1  # header only

    def test_export_csv_with_filters(self, audit_svc):
        audit_svc.log(actor="a@t.com", actor_role="admin", action="a1", resource_type="x")
        audit_svc.log(actor="b@t.com", actor_role="viewer", action="a2", resource_type="y")
        csv_data = audit_svc.export_csv(actor="a@t.com")
        lines = csv_data.strip().split("\n")
        assert len(lines) == 2  # header + 1 matching row


class TestCleanup:
    def test_cleanup_removes_old_entries(self, audit_svc):
        # Insert an entry with a very old timestamp
        from shoreguard.models import AuditEntry

        session = audit_svc._session_factory()
        old_ts = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=100)).isoformat()
        session.add(
            AuditEntry(
                timestamp=old_ts,
                actor="old@test.com",
                actor_role="admin",
                action="old.action",
                resource_type="test",
            )
        )
        session.commit()
        session.close()

        # Insert a recent entry
        audit_svc.log(actor="new@test.com", actor_role="admin", action="new", resource_type="test")

        removed = audit_svc.cleanup(older_than_days=90)
        assert removed == 1
        remaining = audit_svc.list()
        assert len(remaining) == 1
        assert remaining[0]["actor"] == "new@test.com"

    def test_cleanup_preserves_recent(self, audit_svc):
        audit_svc.log(actor="a@t.com", actor_role="admin", action="test", resource_type="test")
        removed = audit_svc.cleanup(older_than_days=90)
        assert removed == 0
        assert len(audit_svc.list()) == 1
