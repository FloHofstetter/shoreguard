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
        registry.register("gw1", "10.0.0.1:8443")
        with pytest.raises(ValueError, match="already registered"):
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
