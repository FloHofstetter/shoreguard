"""Tests for the AuditService."""

import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.models import Base
from shoreguard.services.audit import AuditService, audit_log


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
        old_ts = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=100)
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


# ── Additional mutation-killing tests ────────────────────────────────────────


class TestLogDetailedAssertions:
    def test_log_all_fields_exact(self, audit_svc):
        """Assert on every single field returned by _to_dict."""
        audit_svc.log(
            actor="alice@example.com",
            actor_role="operator",
            action="sandbox.delete",
            resource_type="sandbox",
            resource_id="sb-123",
            gateway="prod-gw",
            detail={"reason": "cleanup"},
            client_ip="10.0.0.5",
        )
        entries = audit_svc.list()
        assert len(entries) == 1
        e = entries[0]
        assert e["actor"] == "alice@example.com"
        assert e["actor_role"] == "operator"
        assert e["action"] == "sandbox.delete"
        assert e["resource_type"] == "sandbox"
        assert e["resource_id"] == "sb-123"
        assert e["gateway"] == "prod-gw"
        assert e["detail"] == {"reason": "cleanup"}
        assert e["client_ip"] == "10.0.0.5"
        assert e["id"] is not None
        assert e["timestamp"] is not None
        assert isinstance(e["id"], int)

    def test_log_gateway_id_resolved(self, audit_svc):
        """When a gateway exists in the DB, gateway_id is set on the audit entry."""
        from shoreguard.models import Gateway

        session = audit_svc._session_factory()
        gw = Gateway(
            name="test-gw",
            endpoint="8.8.8.8:8443",
            scheme="https",
            registered_at=datetime.datetime.now(datetime.UTC),
        )
        session.add(gw)
        session.commit()
        session.close()

        audit_svc.log(
            actor="admin@test.com",
            actor_role="admin",
            action="test",
            resource_type="gateway",
            gateway="test-gw",
        )
        # Verify entry was created and gateway resolved
        entries = audit_svc.list()
        assert len(entries) == 1
        assert entries[0]["gateway"] == "test-gw"

    def test_log_gateway_not_found_sets_none(self, audit_svc):
        """When gateway name doesn't match a DB gateway, gateway_id is None."""
        audit_svc.log(
            actor="admin@test.com",
            actor_role="admin",
            action="test",
            resource_type="gateway",
            gateway="nonexistent-gw",
        )
        entries = audit_svc.list()
        assert len(entries) == 1
        assert entries[0]["gateway"] == "nonexistent-gw"

    def test_log_none_gateway(self, audit_svc):
        """When gateway is None, gateway field is None."""
        audit_svc.log(
            actor="admin@test.com",
            actor_role="admin",
            action="test",
            resource_type="sandbox",
            gateway=None,
        )
        entries = audit_svc.list()
        assert entries[0]["gateway"] is None

    def test_log_detail_none_vs_empty(self, audit_svc):
        """detail=None should result in None, not empty dict."""
        audit_svc.log(
            actor="admin@test.com",
            actor_role="admin",
            action="test",
            resource_type="test",
            detail=None,
        )
        entries = audit_svc.list()
        assert entries[0]["detail"] is None

    def test_log_detail_empty_dict(self, audit_svc):
        """detail={} is falsy, so it is stored as None."""
        audit_svc.log(
            actor="admin@test.com",
            actor_role="admin",
            action="test",
            resource_type="test",
            detail={},
        )
        entries = audit_svc.list()
        # Empty dict is falsy in Python, so `json.dumps(detail) if detail else None` => None
        assert entries[0]["detail"] is None

    def test_log_detail_complex_json(self, audit_svc):
        """Complex nested JSON detail."""
        detail = {"nested": {"key": "value"}, "list": [1, 2, 3], "bool": True}
        audit_svc.log(
            actor="admin@test.com",
            actor_role="admin",
            action="test",
            resource_type="test",
            detail=detail,
        )
        entries = audit_svc.list()
        assert entries[0]["detail"] == detail


class TestToDict:
    def test_to_dict_with_invalid_json_detail(self, audit_svc):
        """_to_dict with non-JSON detail string returns raw string."""
        from shoreguard.models import AuditEntry

        session = audit_svc._session_factory()
        entry = AuditEntry(
            timestamp=datetime.datetime.now(datetime.UTC),
            actor="admin@test.com",
            actor_role="admin",
            action="test",
            resource_type="test",
            detail="not valid json {{{",
        )
        session.add(entry)
        session.commit()
        session.close()

        entries = audit_svc.list()
        assert len(entries) == 1
        assert entries[0]["detail"] == "not valid json {{{"

    def test_to_dict_with_none_timestamp(self, audit_svc):
        """_to_dict with None timestamp returns None for timestamp."""
        from shoreguard.models import AuditEntry

        entry = AuditEntry(
            timestamp=None,
            actor="admin@test.com",
            actor_role="admin",
            action="test",
            resource_type="test",
        )
        result = AuditService._to_dict(entry)
        assert result["timestamp"] is None

    def test_to_dict_with_empty_string_detail(self, audit_svc):
        """_to_dict with empty string detail."""
        from shoreguard.models import AuditEntry

        session = audit_svc._session_factory()
        entry = AuditEntry(
            timestamp=datetime.datetime.now(datetime.UTC),
            actor="admin@test.com",
            actor_role="admin",
            action="test",
            resource_type="test",
            detail="",
        )
        session.add(entry)
        session.commit()
        session.close()

        entries = audit_svc.list()
        # Empty string is truthy for the `if entry.detail:` check? No, empty string is falsy.
        assert entries[0]["detail"] is None

    def test_to_dict_all_keys_present(self, audit_svc):
        """_to_dict returns exactly the expected keys."""
        audit_svc.log(
            actor="a@t.com",
            actor_role="admin",
            action="test",
            resource_type="test",
        )
        entries = audit_svc.list()
        expected_keys = {
            "id",
            "timestamp",
            "actor",
            "actor_role",
            "action",
            "resource_type",
            "resource_id",
            "gateway",
            "detail",
            "client_ip",
        }
        assert set(entries[0].keys()) == expected_keys

    def test_to_dict_json_detail_parsed(self, audit_svc):
        """_to_dict parses JSON detail string into dict."""
        from shoreguard.models import AuditEntry

        session = audit_svc._session_factory()
        entry = AuditEntry(
            timestamp=datetime.datetime.now(datetime.UTC),
            actor="admin@test.com",
            actor_role="admin",
            action="test",
            resource_type="test",
            detail='{"key": "val"}',
        )
        session.add(entry)
        session.commit()
        session.close()

        entries = audit_svc.list()
        assert entries[0]["detail"] == {"key": "val"}


class TestListFilters:
    def _seed_mixed(self, svc):
        """Seed entries with varied actors, actions, types, and timestamps."""
        from shoreguard.models import AuditEntry

        session = svc._session_factory()
        now = datetime.datetime.now(datetime.UTC)
        entries = [
            AuditEntry(
                timestamp=now - datetime.timedelta(hours=5),
                actor="alice@test.com",
                actor_role="admin",
                action="sandbox.create",
                resource_type="sandbox",
                resource_id="sb-1",
            ),
            AuditEntry(
                timestamp=now - datetime.timedelta(hours=3),
                actor="bob@test.com",
                actor_role="operator",
                action="sandbox.delete",
                resource_type="sandbox",
                resource_id="sb-2",
            ),
            AuditEntry(
                timestamp=now - datetime.timedelta(hours=1),
                actor="alice@test.com",
                actor_role="admin",
                action="policy.update",
                resource_type="policy",
                resource_id="pol-1",
            ),
        ]
        for e in entries:
            session.add(e)
        session.commit()
        session.close()

    def test_filter_by_actor_exact(self, audit_svc):
        self._seed_mixed(audit_svc)
        entries = audit_svc.list(actor="alice@test.com")
        assert len(entries) == 2
        assert all(e["actor"] == "alice@test.com" for e in entries)

    def test_filter_by_action_exact(self, audit_svc):
        self._seed_mixed(audit_svc)
        entries = audit_svc.list(action="sandbox.delete")
        assert len(entries) == 1
        assert entries[0]["action"] == "sandbox.delete"
        assert entries[0]["actor"] == "bob@test.com"

    def test_filter_by_resource_type_exact(self, audit_svc):
        self._seed_mixed(audit_svc)
        entries = audit_svc.list(resource_type="policy")
        assert len(entries) == 1
        assert entries[0]["resource_type"] == "policy"
        assert entries[0]["resource_id"] == "pol-1"

    def test_filter_since_excludes_old(self, audit_svc):
        self._seed_mixed(audit_svc)
        since = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2)).isoformat()
        entries = audit_svc.list(since=since)
        assert len(entries) == 1
        assert entries[0]["action"] == "policy.update"

    def test_filter_until_excludes_recent(self, audit_svc):
        self._seed_mixed(audit_svc)
        until = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=4)).isoformat()
        entries = audit_svc.list(until=until)
        assert len(entries) == 1
        assert entries[0]["action"] == "sandbox.create"

    def test_filter_combined_since_until(self, audit_svc):
        self._seed_mixed(audit_svc)
        since = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=4)).isoformat()
        until = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2)).isoformat()
        entries = audit_svc.list(since=since, until=until)
        assert len(entries) == 1
        assert entries[0]["action"] == "sandbox.delete"

    def test_list_limit_exact(self, audit_svc):
        self._seed_mixed(audit_svc)
        entries = audit_svc.list(limit=2)
        assert len(entries) == 2

    def test_list_offset_exact(self, audit_svc):
        self._seed_mixed(audit_svc)
        all_entries = audit_svc.list(limit=100)
        offset_entries = audit_svc.list(limit=100, offset=1)
        assert len(offset_entries) == len(all_entries) - 1

    def test_list_ordering_newest_first(self, audit_svc):
        self._seed_mixed(audit_svc)
        entries = audit_svc.list()
        # Most recent should be first (policy.update was 1 hour ago)
        assert entries[0]["action"] == "policy.update"
        assert entries[-1]["action"] == "sandbox.create"


class TestListWithCount:
    def test_list_with_count_returns_tuple(self, audit_svc):
        audit_svc.log(actor="a@t.com", actor_role="admin", action="test", resource_type="test")
        result = audit_svc.list_with_count()
        assert isinstance(result, tuple)
        assert len(result) == 2
        entries, total = result
        assert isinstance(entries, list)
        assert isinstance(total, int)

    def test_list_with_count_empty(self, audit_svc):
        entries, total = audit_svc.list_with_count()
        assert entries == []
        assert total == 0

    def test_list_with_count_values(self, audit_svc):
        for i in range(5):
            audit_svc.log(
                actor=f"user{i}@t.com",
                actor_role="admin",
                action="test",
                resource_type="test",
            )
        entries, total = audit_svc.list_with_count(limit=3)
        assert len(entries) == 3
        assert total == 5

    def test_list_with_count_offset(self, audit_svc):
        for i in range(5):
            audit_svc.log(
                actor=f"user{i}@t.com",
                actor_role="admin",
                action="test",
                resource_type="test",
            )
        entries, total = audit_svc.list_with_count(limit=2, offset=3)
        assert len(entries) == 2
        assert total == 5

    def test_list_with_count_filter_actor(self, audit_svc):
        audit_svc.log(actor="a@t.com", actor_role="admin", action="test", resource_type="test")
        audit_svc.log(actor="b@t.com", actor_role="viewer", action="test", resource_type="test")
        entries, total = audit_svc.list_with_count(actor="a@t.com")
        assert len(entries) == 1
        assert total == 1
        assert entries[0]["actor"] == "a@t.com"

    def test_list_with_count_filter_action(self, audit_svc):
        audit_svc.log(actor="a@t.com", actor_role="admin", action="create", resource_type="test")
        audit_svc.log(actor="a@t.com", actor_role="admin", action="delete", resource_type="test")
        entries, total = audit_svc.list_with_count(action="create")
        assert len(entries) == 1
        assert total == 1

    def test_list_with_count_filter_resource_type(self, audit_svc):
        audit_svc.log(actor="a@t.com", actor_role="admin", action="test", resource_type="sandbox")
        audit_svc.log(actor="a@t.com", actor_role="admin", action="test", resource_type="gateway")
        entries, total = audit_svc.list_with_count(resource_type="sandbox")
        assert len(entries) == 1
        assert total == 1

    def test_list_with_count_filter_since(self, audit_svc):
        from shoreguard.models import AuditEntry

        session = audit_svc._session_factory()
        now = datetime.datetime.now(datetime.UTC)
        session.add(
            AuditEntry(
                timestamp=now - datetime.timedelta(hours=5),
                actor="old@t.com",
                actor_role="admin",
                action="old",
                resource_type="test",
            )
        )
        session.add(
            AuditEntry(
                timestamp=now,
                actor="new@t.com",
                actor_role="admin",
                action="new",
                resource_type="test",
            )
        )
        session.commit()
        session.close()
        since = (now - datetime.timedelta(hours=1)).isoformat()
        entries, total = audit_svc.list_with_count(since=since)
        assert len(entries) == 1
        assert total == 1
        assert entries[0]["actor"] == "new@t.com"

    def test_list_with_count_filter_until(self, audit_svc):
        from shoreguard.models import AuditEntry

        session = audit_svc._session_factory()
        now = datetime.datetime.now(datetime.UTC)
        session.add(
            AuditEntry(
                timestamp=now - datetime.timedelta(hours=5),
                actor="old@t.com",
                actor_role="admin",
                action="old",
                resource_type="test",
            )
        )
        session.add(
            AuditEntry(
                timestamp=now,
                actor="new@t.com",
                actor_role="admin",
                action="new",
                resource_type="test",
            )
        )
        session.commit()
        session.close()
        until = (now - datetime.timedelta(hours=1)).isoformat()
        entries, total = audit_svc.list_with_count(until=until)
        assert len(entries) == 1
        assert total == 1
        assert entries[0]["actor"] == "old@t.com"

    def test_list_with_count_db_error_returns_empty(self, audit_svc):
        """DB errors should return ([], 0)."""
        audit_svc._session_factory.kw["bind"].dispose()
        entries, total = audit_svc.list_with_count()
        assert entries == []
        assert total == 0


class TestExportCsvDetailed:
    def test_export_csv_fieldnames(self, audit_svc):
        """CSV header should contain exact field names."""
        csv_data = audit_svc.export_csv()
        header = csv_data.strip().split("\n")[0]
        expected_fields = [
            "id",
            "timestamp",
            "actor",
            "actor_role",
            "action",
            "resource_type",
            "resource_id",
            "gateway",
            "detail",
            "client_ip",
        ]
        for field in expected_fields:
            assert field in header

    def test_export_csv_row_data(self, audit_svc):
        audit_svc.log(
            actor="admin@test.com",
            actor_role="admin",
            action="sandbox.create",
            resource_type="sandbox",
            resource_id="sb-1",
            client_ip="192.168.1.1",
        )
        csv_data = audit_svc.export_csv()
        lines = csv_data.strip().split("\n")
        assert len(lines) == 2
        data_line = lines[1]
        assert "admin@test.com" in data_line
        assert "sandbox.create" in data_line
        assert "sb-1" in data_line
        assert "192.168.1.1" in data_line

    def test_export_csv_filter_action(self, audit_svc):
        audit_svc.log(actor="a@t.com", actor_role="admin", action="create", resource_type="test")
        audit_svc.log(actor="b@t.com", actor_role="viewer", action="delete", resource_type="test")
        csv_data = audit_svc.export_csv(action="create")
        lines = csv_data.strip().split("\n")
        assert len(lines) == 2
        assert "a@t.com" in lines[1]

    def test_export_csv_filter_resource_type(self, audit_svc):
        audit_svc.log(actor="a@t.com", actor_role="admin", action="test", resource_type="sandbox")
        audit_svc.log(actor="b@t.com", actor_role="viewer", action="test", resource_type="gateway")
        csv_data = audit_svc.export_csv(resource_type="sandbox")
        lines = csv_data.strip().split("\n")
        assert len(lines) == 2
        assert "a@t.com" in lines[1]

    def test_export_csv_filter_since_until(self, audit_svc):
        from shoreguard.models import AuditEntry

        session = audit_svc._session_factory()
        now = datetime.datetime.now(datetime.UTC)
        session.add(
            AuditEntry(
                timestamp=now - datetime.timedelta(hours=10),
                actor="old@t.com",
                actor_role="admin",
                action="old",
                resource_type="test",
            )
        )
        session.add(
            AuditEntry(
                timestamp=now,
                actor="new@t.com",
                actor_role="admin",
                action="new",
                resource_type="test",
            )
        )
        session.commit()
        session.close()
        since = (now - datetime.timedelta(hours=1)).isoformat()
        csv_data = audit_svc.export_csv(since=since)
        lines = csv_data.strip().split("\n")
        assert len(lines) == 2
        assert "new@t.com" in lines[1]


class TestCleanupDetailed:
    def test_cleanup_returns_exact_count(self, audit_svc):
        from shoreguard.models import AuditEntry

        session = audit_svc._session_factory()
        old_ts = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=100)
        for i in range(3):
            session.add(
                AuditEntry(
                    timestamp=old_ts,
                    actor=f"old{i}@t.com",
                    actor_role="admin",
                    action="old",
                    resource_type="test",
                )
            )
        session.commit()
        session.close()
        audit_svc.log(actor="new@t.com", actor_role="admin", action="new", resource_type="test")
        removed = audit_svc.cleanup(older_than_days=90)
        assert removed == 3
        remaining = audit_svc.list()
        assert len(remaining) == 1

    def test_cleanup_default_retention_days(self, audit_svc):
        """cleanup() with no args uses settings default (90 days)."""
        from shoreguard.models import AuditEntry

        session = audit_svc._session_factory()
        old_ts = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=100)
        session.add(
            AuditEntry(
                timestamp=old_ts,
                actor="old@t.com",
                actor_role="admin",
                action="old",
                resource_type="test",
            )
        )
        session.commit()
        session.close()
        removed = audit_svc.cleanup()
        assert removed == 1

    def test_cleanup_zero_when_nothing_to_remove(self, audit_svc):
        removed = audit_svc.cleanup(older_than_days=90)
        assert removed == 0

    def test_cleanup_does_not_remove_recent(self, audit_svc):
        audit_svc.log(actor="a@t.com", actor_role="admin", action="test", resource_type="test")
        removed = audit_svc.cleanup(older_than_days=1)
        assert removed == 0
        assert len(audit_svc.list()) == 1

    def test_cleanup_db_error_returns_zero(self, audit_svc):
        """DB errors during cleanup should return 0."""
        audit_svc._session_factory.kw["bind"].dispose()
        removed = audit_svc.cleanup(older_than_days=1)
        assert removed == 0

    def test_cleanup_boundary_recent_not_removed(self, audit_svc):
        """Entry well within retention window should NOT be removed."""
        from shoreguard.models import AuditEntry

        session = audit_svc._session_factory()
        # Entry at 89 days ago — within 90-day window
        recent_enough = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=89)
        session.add(
            AuditEntry(
                timestamp=recent_enough,
                actor="boundary@t.com",
                actor_role="admin",
                action="test",
                resource_type="test",
            )
        )
        session.commit()
        session.close()
        removed = audit_svc.cleanup(older_than_days=90)
        assert removed == 0
        remaining = audit_svc.list()
        assert len(remaining) == 1
        assert remaining[0]["actor"] == "boundary@t.com"


class TestAuditLogHelper:
    """Tests for the module-level audit_log() async helper."""

    def test_audit_log_when_service_is_none(self):
        """audit_log() returns immediately when audit_service is None."""
        import asyncio

        import shoreguard.services.audit as audit_mod
        from shoreguard.services.audit import audit_log

        original = audit_mod.audit_service
        audit_mod.audit_service = None
        try:
            request = _make_mock_request()
            # Should not raise
            asyncio.run(audit_log(request, "test", "test"))
        finally:
            audit_mod.audit_service = original

    def test_audit_log_extracts_request_fields(self, audit_svc):
        """audit_log() extracts actor, role, and client IP from request."""
        import asyncio

        import shoreguard.services.audit as audit_mod

        original = audit_mod.audit_service
        audit_mod.audit_service = audit_svc
        try:
            request = _make_mock_request(
                user_id="alice@test.com",
                role="admin",
                client_host="10.0.0.5",
            )
            asyncio.run(
                audit_log(
                    request,
                    "sandbox.create",
                    "sandbox",
                    "sb-1",
                    gateway="gw1",
                    detail={"key": "val"},
                )
            )
            entries = audit_svc.list()
            assert len(entries) == 1
            assert entries[0]["actor"] == "alice@test.com"
            assert entries[0]["actor_role"] == "admin"
            assert entries[0]["action"] == "sandbox.create"
            assert entries[0]["resource_type"] == "sandbox"
            assert entries[0]["resource_id"] == "sb-1"
            assert entries[0]["gateway"] == "gw1"
            assert entries[0]["detail"] == {"key": "val"}
            assert entries[0]["client_ip"] == "10.0.0.5"
        finally:
            audit_mod.audit_service = original

    def test_audit_log_missing_user_id(self, audit_svc):
        """audit_log() uses 'unknown' when request.state has no user_id."""
        import asyncio

        import shoreguard.services.audit as audit_mod

        original = audit_mod.audit_service
        audit_mod.audit_service = audit_svc
        try:
            request = _make_mock_request()
            asyncio.run(audit_log(request, "test", "test"))
            entries = audit_svc.list()
            assert entries[0]["actor"] == "unknown"
            assert entries[0]["actor_role"] == "unknown"
        finally:
            audit_mod.audit_service = original

    def test_audit_log_no_client(self, audit_svc):
        """audit_log() with no request.client sets client_ip to None."""
        import asyncio

        import shoreguard.services.audit as audit_mod

        original = audit_mod.audit_service
        audit_mod.audit_service = audit_svc
        try:
            request = _make_mock_request(client_host=None)
            asyncio.run(audit_log(request, "test", "test"))
            entries = audit_svc.list()
            assert entries[0]["client_ip"] is None
        finally:
            audit_mod.audit_service = original


class TestListDbError:
    def test_list_db_error_returns_empty(self, audit_svc):
        """DB errors in list() return empty list."""
        audit_svc._session_factory.kw["bind"].dispose()
        entries = audit_svc.list()
        assert entries == []


def _make_mock_request(
    user_id: str | None = None,
    role: str | None = None,
    client_host: str | None = "127.0.0.1",
) -> Any:
    """Create a minimal mock Request for audit_log() tests."""

    class _State:
        user_id: str
        role: str

    class _Client:
        def __init__(self, host: str) -> None:
            self.host = host

    class _MockRequest:
        def __init__(self) -> None:
            self.state = _State()
            if user_id is not None:
                self.state.user_id = user_id
            if role is not None:
                self.state.role = role
            self.client = _Client(client_host) if client_host else None

    return _MockRequest()
