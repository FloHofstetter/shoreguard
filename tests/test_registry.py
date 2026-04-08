"""Tests for the GatewayRegistry service."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shoreguard.models import Base
from shoreguard.services.registry import GatewayRegistry


@pytest.fixture
def registry():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    reg = GatewayRegistry(factory)
    yield reg
    engine.dispose()


class TestRegister:
    def test_register_returns_dict(self, registry):
        result = registry.register("gw1", "10.0.0.1:8443")
        assert result["name"] == "gw1"
        assert result["endpoint"] == "10.0.0.1:8443"
        assert result["scheme"] == "https"
        assert result["auth_mode"] == "mtls"
        assert result["last_status"] == "unknown"
        assert result["registered_at"] is not None

    def test_register_with_all_fields(self, registry):
        result = registry.register(
            "gw1",
            "10.0.0.1:8443",
            scheme="http",
            auth_mode="insecure",
            ca_cert=b"ca-data",
            client_cert=b"cert-data",
            client_key=b"key-data",
            metadata={"gpu": True, "labels": ["prod"]},
        )
        assert result["scheme"] == "http"
        assert result["auth_mode"] == "insecure"
        assert result["has_ca_cert"] is True
        assert result["has_client_cert"] is True
        assert result["has_client_key"] is True
        assert result["metadata"] == {"gpu": True, "labels": ["prod"]}

    def test_register_duplicate_raises(self, registry):
        from shoreguard.exceptions import ConflictError

        registry.register("gw1", "10.0.0.1:8443")
        with pytest.raises(ConflictError, match="already registered"):
            registry.register("gw1", "10.0.0.2:8443")

    def test_register_without_certs(self, registry):
        result = registry.register("gw1", "10.0.0.1:8443", auth_mode="insecure")
        assert result["has_ca_cert"] is False
        assert result["has_client_cert"] is False
        assert result["has_client_key"] is False

    def test_register_without_metadata(self, registry):
        result = registry.register("gw1", "10.0.0.1:8443")
        assert result["metadata"] == {}


class TestUnregister:
    def test_unregister_existing(self, registry):
        registry.register("gw1", "10.0.0.1:8443")
        assert registry.unregister("gw1") is True
        assert registry.get("gw1") is None

    def test_unregister_nonexistent(self, registry):
        assert registry.unregister("nope") is False


class TestGet:
    def test_get_existing(self, registry):
        registry.register("gw1", "10.0.0.1:8443")
        result = registry.get("gw1")
        assert result is not None
        assert result["name"] == "gw1"

    def test_get_nonexistent(self, registry):
        assert registry.get("nope") is None


class TestListAll:
    def test_list_empty(self, registry):
        assert registry.list_all() == []

    def test_list_multiple_sorted(self, registry):
        registry.register("beta", "10.0.0.2:8443")
        registry.register("alpha", "10.0.0.1:8443")
        registry.register("gamma", "10.0.0.3:8443")
        names = [gw["name"] for gw in registry.list_all()]
        assert names == ["alpha", "beta", "gamma"]

    def test_list_preserves_all_fields(self, registry):
        registry.register(
            "gw1",
            "10.0.0.1:8443",
            ca_cert=b"ca",
            client_cert=b"cert",
            client_key=b"key",
            metadata={"gpu": True},
        )
        result = registry.list_all()
        assert len(result) == 1
        gw = result[0]
        assert gw["has_ca_cert"] is True
        assert gw["metadata"] == {"gpu": True}


class TestUpdateHealth:
    def test_update_health(self, registry):
        registry.register("gw1", "10.0.0.1:8443")
        registry.update_health("gw1", "healthy", datetime(2026, 3, 28, 10, 0, 0, tzinfo=UTC))
        gw = registry.get("gw1")
        assert gw["last_status"] == "healthy"
        assert gw["last_seen"] in ("2026-03-28T10:00:00+00:00", "2026-03-28T10:00:00")

    def test_update_health_nonexistent_is_noop(self, registry):
        registry.update_health("nope", "healthy", datetime(2026, 3, 28, 10, 0, 0, tzinfo=UTC))


class TestUpdateMetadata:
    def test_update_metadata(self, registry):
        registry.register("gw1", "10.0.0.1:8443", metadata={"gpu": False})
        registry.update_metadata("gw1", {"gpu": True, "region": "eu"})
        gw = registry.get("gw1")
        assert gw["metadata"] == {"gpu": True, "region": "eu"}

    def test_update_metadata_nonexistent_is_noop(self, registry):
        registry.update_metadata("nope", {"foo": "bar"})


class TestToDict:
    def test_corrupt_metadata_json_returns_empty(self, registry):
        """Corrupt metadata_json is handled gracefully."""
        from shoreguard.models import Gateway

        registry.register("gw1", "10.0.0.1:8443", metadata={"valid": True})
        # Corrupt the metadata_json directly
        with registry._session_factory() as session:
            gw = session.query(Gateway).filter(Gateway.name == "gw1").first()
            gw.metadata_json = "not-valid-json{{"
            session.commit()
        result = registry.get("gw1")
        assert result["metadata"] == {}


class TestDatabaseErrors:
    def test_unregister_db_error_raises(self, registry):
        """unregister re-raises SQLAlchemyError on commit failure."""
        from unittest.mock import patch

        from sqlalchemy.exc import SQLAlchemyError

        registry.register("gw1", "10.0.0.1:8443")

        with patch.object(
            type(registry._session_factory()),
            "commit",
            side_effect=SQLAlchemyError("disk full"),
        ):
            with pytest.raises(SQLAlchemyError):
                registry.unregister("gw1")

    def test_update_health_db_error_raises(self, registry):
        """update_health re-raises SQLAlchemyError on commit failure."""
        from unittest.mock import patch

        from sqlalchemy.exc import SQLAlchemyError

        registry.register("gw1", "10.0.0.1:8443")

        with patch.object(
            type(registry._session_factory()),
            "commit",
            side_effect=SQLAlchemyError("disk full"),
        ):
            with pytest.raises(SQLAlchemyError):
                registry.update_health(
                    "gw1",
                    "healthy",
                    datetime(2026, 3, 28, 10, 0, 0, tzinfo=UTC),
                )

    def test_update_metadata_db_error_raises(self, registry):
        """update_metadata re-raises SQLAlchemyError on commit failure."""
        from unittest.mock import patch

        from sqlalchemy.exc import SQLAlchemyError

        registry.register("gw1", "10.0.0.1:8443")

        with patch.object(
            type(registry._session_factory()),
            "commit",
            side_effect=SQLAlchemyError("disk full"),
        ):
            with pytest.raises(SQLAlchemyError):
                registry.update_metadata("gw1", {"key": "value"})


class TestCertStorage:
    def test_binary_certs_roundtrip(self, registry):
        ca = b"\x00\x01\x02" * 100
        cert = b"-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----"
        key = b"-----BEGIN PRIVATE KEY-----\nMIIE...\n-----END PRIVATE KEY-----"
        registry.register("gw1", "10.0.0.1:8443", ca_cert=ca, client_cert=cert, client_key=key)
        # API dict should not expose raw cert bytes
        gw = registry.get("gw1")
        assert gw["has_ca_cert"] is True
        assert gw["has_client_cert"] is True
        assert gw["has_client_key"] is True
        assert "ca_cert" not in gw
        assert "client_key" not in gw
        # Credentials endpoint should return raw bytes
        creds = registry.get_credentials("gw1")
        assert creds["ca_cert"] == ca
        assert creds["client_cert"] == cert
        assert creds["client_key"] == key

    def test_get_credentials_nonexistent(self, registry):
        assert registry.get_credentials("nope") is None


# ─── Mutation-killing tests ──────────────────────────────────────────────────


class TestUnregisterMutationKill:
    """Kill mutation survivors in unregister()."""

    def test_unregister_returns_true_not_truthy(self, registry):
        """Return value must be exactly True, not just truthy."""
        registry.register("gw1", "10.0.0.1:8443")
        result = registry.unregister("gw1")
        assert result is True
        assert type(result) is bool

    def test_unregister_returns_false_not_falsy(self, registry):
        """Return value must be exactly False, not just falsy."""
        result = registry.unregister("nope")
        assert result is False
        assert type(result) is bool

    def test_unregister_actually_deletes(self, registry):
        """After unregister, the gateway must not appear in list_all."""
        registry.register("gw1", "10.0.0.1:8443")
        registry.register("gw2", "10.0.0.2:8443")
        registry.unregister("gw1")
        names = [gw["name"] for gw in registry.list_all()]
        assert names == ["gw2"]
        assert registry.get("gw1") is None

    def test_unregister_nonexistent_doesnt_affect_others(self, registry):
        """Unregistering a nonexistent gateway must not remove existing ones."""
        registry.register("gw1", "10.0.0.1:8443")
        registry.unregister("nope")
        assert registry.get("gw1") is not None

    def test_unregister_same_gateway_twice(self, registry):
        """Second unregister must return False."""
        registry.register("gw1", "10.0.0.1:8443")
        assert registry.unregister("gw1") is True
        assert registry.unregister("gw1") is False

    def test_unregister_queries_by_name_filter(self, registry):
        """Unregister must filter by exact name, not delete all."""
        registry.register("gw1", "10.0.0.1:8443")
        registry.register("gw2", "10.0.0.2:8443")
        registry.unregister("gw1")
        assert registry.get("gw2") is not None
        assert registry.get("gw2")["name"] == "gw2"

    def test_unregister_db_error_rolls_back(self, registry):
        """On SQLAlchemyError the gateway must still exist after rollback."""
        from unittest.mock import patch

        from sqlalchemy.exc import SQLAlchemyError

        registry.register("gw1", "10.0.0.1:8443")

        type(registry._session_factory()).commit

        call_count = 0

        def fail_on_delete_commit(self_session):
            nonlocal call_count
            call_count += 1
            raise SQLAlchemyError("disk full")

        # We need to patch at session level - use a different approach
        with pytest.raises(SQLAlchemyError):
            with patch.object(
                type(registry._session_factory()),
                "commit",
                side_effect=SQLAlchemyError("disk full"),
            ):
                registry.unregister("gw1")

        # Gateway should still exist after rollback
        gw = registry.get("gw1")
        assert gw is not None
        assert gw["name"] == "gw1"


class TestUpdateHealthMutationKill:
    """Kill mutation survivors in update_health()."""

    def test_update_health_sets_status_exactly(self, registry):
        """Status must be exactly what was passed, not any other string."""
        registry.register("gw1", "10.0.0.1:8443")
        ts = datetime(2026, 3, 28, 10, 0, 0, tzinfo=UTC)
        registry.update_health("gw1", "healthy", ts)
        gw = registry.get("gw1")
        assert gw["last_status"] == "healthy"
        assert gw["last_status"] != "unknown"

    def test_update_health_sets_last_seen_exactly(self, registry):
        """last_seen must reflect the exact timestamp passed."""
        registry.register("gw1", "10.0.0.1:8443")
        ts = datetime(2026, 6, 15, 14, 30, 45, tzinfo=UTC)
        registry.update_health("gw1", "healthy", ts)
        gw = registry.get("gw1")
        assert "2026-06-15" in gw["last_seen"]
        assert "14:30:45" in gw["last_seen"]

    def test_update_health_persists_across_reads(self, registry):
        """Health update must be persisted in the database."""
        registry.register("gw1", "10.0.0.1:8443")
        ts = datetime(2026, 3, 28, 10, 0, 0, tzinfo=UTC)
        registry.update_health("gw1", "degraded", ts)
        # Read twice to ensure persistence
        gw1 = registry.get("gw1")
        gw2 = registry.get("gw1")
        assert gw1["last_status"] == "degraded"
        assert gw2["last_status"] == "degraded"

    def test_update_health_nonexistent_returns_none(self, registry):
        """update_health on nonexistent gateway returns None (implicitly)."""
        result = registry.update_health("nope", "healthy", datetime(2026, 1, 1, tzinfo=UTC))
        assert result is None

    def test_update_health_status_transition(self, registry):
        """Updating status multiple times must reflect the latest value."""
        registry.register("gw1", "10.0.0.1:8443")
        ts1 = datetime(2026, 3, 28, 10, 0, 0, tzinfo=UTC)
        ts2 = datetime(2026, 3, 28, 11, 0, 0, tzinfo=UTC)
        registry.update_health("gw1", "healthy", ts1)
        gw = registry.get("gw1")
        assert gw["last_status"] == "healthy"

        registry.update_health("gw1", "degraded", ts2)
        gw = registry.get("gw1")
        assert gw["last_status"] == "degraded"
        assert "11:00:00" in gw["last_seen"]

    def test_update_health_same_status_no_crash(self, registry):
        """Updating to the same status must not crash (old_status == status path)."""
        registry.register("gw1", "10.0.0.1:8443")
        ts = datetime(2026, 3, 28, 10, 0, 0, tzinfo=UTC)
        registry.update_health("gw1", "healthy", ts)
        registry.update_health("gw1", "healthy", ts)
        gw = registry.get("gw1")
        assert gw["last_status"] == "healthy"

    def test_update_health_old_status_checked(self, registry):
        """The old_status != status branch controls logging, verify both paths work."""
        registry.register("gw1", "10.0.0.1:8443")
        ts = datetime(2026, 3, 28, 10, 0, 0, tzinfo=UTC)
        # Initial status is "unknown", updating to "healthy" triggers the != branch
        registry.update_health("gw1", "healthy", ts)
        gw = registry.get("gw1")
        assert gw["last_status"] == "healthy"
        # Now update to same status - the == branch
        registry.update_health("gw1", "healthy", datetime(2026, 3, 28, 11, 0, 0, tzinfo=UTC))
        gw = registry.get("gw1")
        assert gw["last_status"] == "healthy"

    def test_update_health_does_not_modify_other_fields(self, registry):
        """Health update must not change name, endpoint, etc."""
        registry.register("gw1", "10.0.0.1:8443", metadata={"env": "prod"})
        ts = datetime(2026, 3, 28, 10, 0, 0, tzinfo=UTC)
        registry.update_health("gw1", "healthy", ts)
        gw = registry.get("gw1")
        assert gw["name"] == "gw1"
        assert gw["endpoint"] == "10.0.0.1:8443"
        assert gw["metadata"] == {"env": "prod"}


class TestUpdateGatewayMetadataMutationKill:
    """Kill mutation survivors in update_gateway_metadata()."""

    def test_update_description_only(self, registry):
        """Setting only description should not affect labels."""
        registry.register("gw1", "10.0.0.1:8443", labels={"env": "prod"})
        result = registry.update_gateway_metadata("gw1", description="New desc")
        assert result is not None
        assert result["description"] == "New desc"
        assert result["labels"] == {"env": "prod"}

    def test_update_labels_only(self, registry):
        """Setting only labels should not affect description."""
        registry.register("gw1", "10.0.0.1:8443", description="Original desc")
        result = registry.update_gateway_metadata("gw1", labels={"env": "staging"})
        assert result is not None
        assert result["labels"] == {"env": "staging"}
        assert result["description"] == "Original desc"

    def test_update_both_description_and_labels(self, registry):
        registry.register("gw1", "10.0.0.1:8443")
        result = registry.update_gateway_metadata(
            "gw1", description="updated", labels={"tier": "1"}
        )
        assert result["description"] == "updated"
        assert result["labels"] == {"tier": "1"}

    def test_clear_description_with_none(self, registry):
        """Passing description=None should clear it."""
        registry.register("gw1", "10.0.0.1:8443", description="old")
        result = registry.update_gateway_metadata("gw1", description=None)
        assert result["description"] is None

    def test_clear_labels_with_none(self, registry):
        """Passing labels=None should clear them."""
        registry.register("gw1", "10.0.0.1:8443", labels={"env": "prod"})
        result = registry.update_gateway_metadata("gw1", labels=None)
        assert result["labels"] == {}

    def test_nonexistent_gateway_returns_none(self, registry):
        result = registry.update_gateway_metadata("nope", description="x")
        assert result is None

    def test_no_changes_when_both_unset(self, registry):
        """When neither arg is passed, gateway is returned unchanged."""
        registry.register("gw1", "10.0.0.1:8443", description="orig", labels={"a": "b"})
        result = registry.update_gateway_metadata("gw1")
        assert result is not None
        assert result["description"] == "orig"
        assert result["labels"] == {"a": "b"}

    def test_returns_full_dict(self, registry):
        """Return value must be a full gateway dict with all expected keys."""
        registry.register("gw1", "10.0.0.1:8443")
        result = registry.update_gateway_metadata("gw1", description="desc")
        expected_keys = {
            "name",
            "endpoint",
            "scheme",
            "auth_mode",
            "has_ca_cert",
            "has_client_cert",
            "has_client_key",
            "metadata",
            "description",
            "labels",
            "registered_at",
            "last_seen",
            "last_status",
        }
        assert set(result.keys()) == expected_keys

    def test_update_persists(self, registry):
        """Metadata update must be persisted in the database."""
        registry.register("gw1", "10.0.0.1:8443")
        registry.update_gateway_metadata("gw1", description="persisted")
        gw = registry.get("gw1")
        assert gw["description"] == "persisted"

    def test_labels_json_null_when_cleared(self, registry):
        """When labels=None, labels_json should be None in DB (empty labels dict on read)."""
        registry.register("gw1", "10.0.0.1:8443", labels={"env": "prod"})
        registry.update_gateway_metadata("gw1", labels=None)
        gw = registry.get("gw1")
        assert gw["labels"] == {}

    def test_labels_json_set_when_non_empty(self, registry):
        """When labels is a non-empty dict, it must be stored and returned correctly."""
        registry.register("gw1", "10.0.0.1:8443")
        registry.update_gateway_metadata("gw1", labels={"region": "eu", "tier": "gold"})
        gw = registry.get("gw1")
        assert gw["labels"] == {"region": "eu", "tier": "gold"}

    def test_db_error_raises(self, registry):
        from unittest.mock import patch

        from sqlalchemy.exc import SQLAlchemyError

        registry.register("gw1", "10.0.0.1:8443")
        with patch.object(
            type(registry._session_factory()),
            "commit",
            side_effect=SQLAlchemyError("fail"),
        ):
            with pytest.raises(SQLAlchemyError):
                registry.update_gateway_metadata("gw1", description="new")


class TestUpdateMetadataMutationKill:
    """Kill mutation survivors in update_metadata()."""

    def test_metadata_replaced_completely(self, registry):
        """update_metadata replaces the entire blob, not merges."""
        registry.register("gw1", "10.0.0.1:8443", metadata={"a": 1, "b": 2})
        registry.update_metadata("gw1", {"c": 3})
        gw = registry.get("gw1")
        assert gw["metadata"] == {"c": 3}
        assert "a" not in gw["metadata"]
        assert "b" not in gw["metadata"]

    def test_metadata_json_is_serialized(self, registry):
        """The metadata dict must be JSON-serialized in the database."""
        import json

        from shoreguard.models import Gateway

        registry.register("gw1", "10.0.0.1:8443")
        registry.update_metadata("gw1", {"key": "value"})
        with registry._session_factory() as session:
            gw = session.query(Gateway).filter(Gateway.name == "gw1").first()
            parsed = json.loads(gw.metadata_json)
            assert parsed == {"key": "value"}

    def test_update_metadata_nonexistent_no_error(self, registry):
        """Updating metadata for nonexistent gateway should not raise."""
        registry.update_metadata("nope", {"foo": "bar"})
        # No exception means success

    def test_update_metadata_persists(self, registry):
        """Metadata must persist across multiple reads."""
        registry.register("gw1", "10.0.0.1:8443")
        registry.update_metadata("gw1", {"version": 42})
        assert registry.get("gw1")["metadata"] == {"version": 42}
        assert registry.get("gw1")["metadata"]["version"] == 42


class TestGetMutationKill:
    """Kill mutation survivors in get()."""

    def test_get_returns_none_not_empty(self, registry):
        """get() must return exactly None for nonexistent, not empty dict/list."""
        result = registry.get("nope")
        assert result is None
        assert result is not False
        assert result != {}

    def test_get_returns_dict_with_all_keys(self, registry):
        """get() must return a full dict with all expected keys."""
        registry.register("gw1", "10.0.0.1:8443")
        gw = registry.get("gw1")
        expected_keys = {
            "name",
            "endpoint",
            "scheme",
            "auth_mode",
            "has_ca_cert",
            "has_client_cert",
            "has_client_key",
            "metadata",
            "description",
            "labels",
            "registered_at",
            "last_seen",
            "last_status",
        }
        assert set(gw.keys()) == expected_keys

    def test_get_returns_correct_values(self, registry):
        """Every field in get() must match what was registered."""
        registry.register(
            "gw1",
            "10.0.0.1:8443",
            scheme="http",
            auth_mode="bearer",
            description="test gw",
            labels={"env": "dev"},
            metadata={"gpu": False},
        )
        gw = registry.get("gw1")
        assert gw["name"] == "gw1"
        assert gw["endpoint"] == "10.0.0.1:8443"
        assert gw["scheme"] == "http"
        assert gw["auth_mode"] == "bearer"
        assert gw["description"] == "test gw"
        assert gw["labels"] == {"env": "dev"}
        assert gw["metadata"] == {"gpu": False}
        assert gw["has_ca_cert"] is False
        assert gw["has_client_cert"] is False
        assert gw["has_client_key"] is False
        assert gw["last_status"] == "unknown"
        assert gw["last_seen"] is None

    def test_get_db_error_raises(self, registry):
        from unittest.mock import patch

        from sqlalchemy.exc import SQLAlchemyError

        with patch.object(
            type(registry._session_factory()),
            "query",
            side_effect=SQLAlchemyError("broken"),
        ):
            with pytest.raises(SQLAlchemyError):
                registry.get("gw1")


class TestListAllMutationKill:
    """Kill mutation survivors in list_all()."""

    def test_list_all_returns_list(self, registry):
        """list_all must return a list, not some other iterable."""
        result = registry.list_all()
        assert isinstance(result, list)

    def test_list_all_label_filter_exact_match(self, registry):
        """Labels filter must match exact key-value pairs."""
        registry.register("gw1", "10.0.0.1:8443", labels={"env": "prod", "region": "eu"})
        registry.register("gw2", "10.0.0.2:8443", labels={"env": "staging"})
        registry.register("gw3", "10.0.0.3:8443", labels={"env": "prod", "region": "us"})

        result = registry.list_all(labels_filter={"env": "prod"})
        names = [gw["name"] for gw in result]
        assert "gw1" in names
        assert "gw3" in names
        assert "gw2" not in names

    def test_list_all_label_filter_multi_key(self, registry):
        """Labels filter with multiple keys must match ALL of them."""
        registry.register("gw1", "10.0.0.1:8443", labels={"env": "prod", "region": "eu"})
        registry.register("gw2", "10.0.0.2:8443", labels={"env": "prod", "region": "us"})

        result = registry.list_all(labels_filter={"env": "prod", "region": "eu"})
        assert len(result) == 1
        assert result[0]["name"] == "gw1"

    def test_list_all_label_filter_no_match(self, registry):
        """Labels filter returns empty list when nothing matches."""
        registry.register("gw1", "10.0.0.1:8443", labels={"env": "prod"})
        result = registry.list_all(labels_filter={"env": "staging"})
        assert result == []

    def test_list_all_label_filter_none_returns_all(self, registry):
        """No filter (or None) should return all gateways."""
        registry.register("gw1", "10.0.0.1:8443")
        registry.register("gw2", "10.0.0.2:8443")
        assert len(registry.list_all(labels_filter=None)) == 2
        assert len(registry.list_all()) == 2

    def test_list_all_label_filter_gateway_without_labels(self, registry):
        """Gateways without labels must not match a filter."""
        registry.register("gw1", "10.0.0.1:8443")  # No labels
        registry.register("gw2", "10.0.0.2:8443", labels={"env": "prod"})
        result = registry.list_all(labels_filter={"env": "prod"})
        assert len(result) == 1
        assert result[0]["name"] == "gw2"

    def test_list_all_each_item_is_full_dict(self, registry):
        """Each item in the list must be a full gateway dict."""
        registry.register("gw1", "10.0.0.1:8443", metadata={"x": 1})
        result = registry.list_all()
        assert len(result) == 1
        gw = result[0]
        assert gw["name"] == "gw1"
        assert gw["metadata"] == {"x": 1}
        assert gw["last_status"] == "unknown"

    def test_list_all_db_error_raises(self, registry):
        from unittest.mock import patch

        from sqlalchemy.exc import SQLAlchemyError

        with patch.object(
            type(registry._session_factory()),
            "query",
            side_effect=SQLAlchemyError("broken"),
        ):
            with pytest.raises(SQLAlchemyError):
                registry.list_all()

    def test_list_all_ordered_by_name(self, registry):
        """Results must be sorted by name ascending."""
        registry.register("zz", "10.0.0.3:8443")
        registry.register("aa", "10.0.0.1:8443")
        registry.register("mm", "10.0.0.2:8443")
        names = [gw["name"] for gw in registry.list_all()]
        assert names == ["aa", "mm", "zz"]


class TestToDictMutationKill:
    """Kill mutation survivors in _to_dict()."""

    def test_corrupt_labels_json_returns_empty(self, registry):
        """Corrupt labels_json is handled gracefully."""
        from shoreguard.models import Gateway

        registry.register("gw1", "10.0.0.1:8443", labels={"valid": "true"})
        with registry._session_factory() as session:
            gw = session.query(Gateway).filter(Gateway.name == "gw1").first()
            gw.labels_json = "not-valid-json{{"
            session.commit()
        result = registry.get("gw1")
        assert result["labels"] == {}

    def test_none_metadata_json_returns_empty(self, registry):
        """None metadata_json returns empty dict."""
        registry.register("gw1", "10.0.0.1:8443")
        gw = registry.get("gw1")
        assert gw["metadata"] == {}

    def test_none_labels_json_returns_empty(self, registry):
        """None labels_json returns empty dict."""
        registry.register("gw1", "10.0.0.1:8443")
        gw = registry.get("gw1")
        assert gw["labels"] == {}

    def test_registered_at_is_iso_string(self, registry):
        """registered_at should be an ISO-format string."""
        registry.register("gw1", "10.0.0.1:8443")
        gw = registry.get("gw1")
        assert isinstance(gw["registered_at"], str)
        assert "T" in gw["registered_at"]  # ISO format has T separator

    def test_last_seen_none_when_not_set(self, registry):
        """last_seen should be None when no health update has occurred."""
        registry.register("gw1", "10.0.0.1:8443")
        gw = registry.get("gw1")
        assert gw["last_seen"] is None

    def test_last_seen_iso_when_set(self, registry):
        """last_seen should be an ISO-format string after health update."""
        registry.register("gw1", "10.0.0.1:8443")
        ts = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        registry.update_health("gw1", "healthy", ts)
        gw = registry.get("gw1")
        assert isinstance(gw["last_seen"], str)
        assert "2026-06-01" in gw["last_seen"]

    def test_has_cert_flags_reflect_actual_state(self, registry):
        """has_ca_cert, has_client_cert, has_client_key must reflect cert presence."""
        registry.register("gw1", "10.0.0.1:8443", ca_cert=b"ca")
        gw = registry.get("gw1")
        assert gw["has_ca_cert"] is True
        assert gw["has_client_cert"] is False
        assert gw["has_client_key"] is False


class TestRegisterMutationKill:
    """Kill remaining mutation survivors in register()."""

    def test_register_with_description_and_labels(self, registry):
        result = registry.register(
            "gw1",
            "10.0.0.1:8443",
            description="A test gateway",
            labels={"env": "prod", "team": "infra"},
        )
        assert result["description"] == "A test gateway"
        assert result["labels"] == {"env": "prod", "team": "infra"}

    def test_register_default_last_seen_is_none(self, registry):
        result = registry.register("gw1", "10.0.0.1:8443")
        assert result["last_seen"] is None

    def test_register_metadata_empty_when_none(self, registry):
        result = registry.register("gw1", "10.0.0.1:8443", metadata=None)
        assert result["metadata"] == {}

    def test_register_labels_empty_when_none(self, registry):
        result = registry.register("gw1", "10.0.0.1:8443", labels=None)
        assert result["labels"] == {}


class TestGetCredentialsMutationKill:
    """Kill mutation survivors in get_credentials()."""

    def test_get_credentials_returns_endpoint(self, registry):
        registry.register("gw1", "10.0.0.1:8443", ca_cert=b"ca")
        creds = registry.get_credentials("gw1")
        assert creds["endpoint"] == "10.0.0.1:8443"

    def test_get_credentials_none_certs(self, registry):
        """When no certs are registered, credential values must be None."""
        registry.register("gw1", "10.0.0.1:8443")
        creds = registry.get_credentials("gw1")
        assert creds is not None
        assert creds["ca_cert"] is None
        assert creds["client_cert"] is None
        assert creds["client_key"] is None

    def test_get_credentials_exact_keys(self, registry):
        """Credentials dict must have exactly the expected keys."""
        registry.register("gw1", "10.0.0.1:8443")
        creds = registry.get_credentials("gw1")
        assert set(creds.keys()) == {"endpoint", "ca_cert", "client_cert", "client_key"}

    def test_get_credentials_db_error_raises(self, registry):
        from unittest.mock import patch

        from sqlalchemy.exc import SQLAlchemyError

        with patch.object(
            type(registry._session_factory()),
            "query",
            side_effect=SQLAlchemyError("broken"),
        ):
            with pytest.raises(SQLAlchemyError):
                registry.get_credentials("gw1")
