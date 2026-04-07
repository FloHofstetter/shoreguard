"""Tests for gateway description and labels (v0.15.0)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.models import Base
from shoreguard.services.registry import GatewayRegistry


@pytest.fixture()
def registry():
    """Create a GatewayRegistry backed by an in-memory SQLite database."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    reg = GatewayRegistry(factory)
    yield reg
    engine.dispose()


# ─── Registry: register with description + labels ──────────────────────────


class TestRegisterWithMetadata:
    def test_register_with_description(self, registry):
        gw = registry.register("gw1", "10.0.0.1:8443", description="Production EU-West")
        assert gw["description"] == "Production EU-West"

    def test_register_with_labels(self, registry):
        gw = registry.register("gw1", "10.0.0.1:8443", labels={"env": "prod", "team": "ml"})
        assert gw["labels"] == {"env": "prod", "team": "ml"}

    def test_register_with_both(self, registry):
        gw = registry.register(
            "gw1",
            "10.0.0.1:8443",
            description="Staging",
            labels={"env": "staging"},
        )
        assert gw["description"] == "Staging"
        assert gw["labels"] == {"env": "staging"}

    def test_register_without_metadata(self, registry):
        gw = registry.register("gw1", "10.0.0.1:8443")
        assert gw["description"] is None
        assert gw["labels"] == {}


# ─── Registry: _to_dict ────────────────────────────────────────────────────


class TestToDict:
    def test_labels_in_dict(self, registry):
        registry.register("gw1", "10.0.0.1:8443", labels={"env": "prod"})
        gw = registry.get("gw1")
        assert gw is not None
        assert gw["labels"] == {"env": "prod"}

    def test_description_in_dict(self, registry):
        registry.register("gw1", "10.0.0.1:8443", description="Test gateway")
        gw = registry.get("gw1")
        assert gw is not None
        assert gw["description"] == "Test gateway"

    def test_corrupt_labels_json(self, registry):
        """Corrupt labels_json returns empty dict instead of crashing."""
        registry.register("gw1", "10.0.0.1:8443")
        # Directly corrupt the JSON in the database
        with registry._session_factory() as session:
            from shoreguard.models import Gateway

            gw = session.query(Gateway).filter(Gateway.name == "gw1").first()
            assert gw is not None
            gw.labels_json = "not valid json{{{"
            session.commit()
        gw_dict = registry.get("gw1")
        assert gw_dict is not None
        assert gw_dict["labels"] == {}


# ─── Registry: update_gateway_metadata ──────────────────────────────────────


class TestUpdateGatewayMetadata:
    def test_update_description_only(self, registry):
        registry.register("gw1", "10.0.0.1:8443", labels={"env": "prod"})
        result = registry.update_gateway_metadata("gw1", description="Updated desc")
        assert result is not None
        assert result["description"] == "Updated desc"
        assert result["labels"] == {"env": "prod"}  # Labels unchanged

    def test_update_labels_only(self, registry):
        registry.register("gw1", "10.0.0.1:8443", description="Original")
        result = registry.update_gateway_metadata("gw1", labels={"env": "staging"})
        assert result is not None
        assert result["labels"] == {"env": "staging"}
        assert result["description"] == "Original"  # Description unchanged

    def test_update_both(self, registry):
        registry.register("gw1", "10.0.0.1:8443")
        result = registry.update_gateway_metadata(
            "gw1", description="New desc", labels={"team": "ml"}
        )
        assert result is not None
        assert result["description"] == "New desc"
        assert result["labels"] == {"team": "ml"}

    def test_clear_description(self, registry):
        registry.register("gw1", "10.0.0.1:8443", description="Will be cleared")
        result = registry.update_gateway_metadata("gw1", description=None)
        assert result is not None
        assert result["description"] is None

    def test_clear_labels(self, registry):
        registry.register("gw1", "10.0.0.1:8443", labels={"env": "prod"})
        result = registry.update_gateway_metadata("gw1", labels=None)
        assert result is not None
        assert result["labels"] == {}

    def test_not_found(self, registry):
        result = registry.update_gateway_metadata("nonexistent", description="test")
        assert result is None


# ─── Registry: list_all with label filtering ────────────────────────────────


class TestListAllFiltering:
    def test_filter_by_single_label(self, registry):
        registry.register("prod-gw", "10.0.0.1:8443", labels={"env": "prod"})
        registry.register("staging-gw", "10.0.0.2:8443", labels={"env": "staging"})
        registry.register("no-labels", "10.0.0.3:8443")

        result = registry.list_all(labels_filter={"env": "prod"})
        assert len(result) == 1
        assert result[0]["name"] == "prod-gw"

    def test_filter_by_multiple_labels(self, registry):
        registry.register("gw1", "10.0.0.1:8443", labels={"env": "prod", "team": "ml"})
        registry.register("gw2", "10.0.0.2:8443", labels={"env": "prod", "team": "infra"})
        registry.register("gw3", "10.0.0.3:8443", labels={"env": "staging"})

        result = registry.list_all(labels_filter={"env": "prod", "team": "ml"})
        assert len(result) == 1
        assert result[0]["name"] == "gw1"

    def test_filter_no_match(self, registry):
        registry.register("gw1", "10.0.0.1:8443", labels={"env": "prod"})
        result = registry.list_all(labels_filter={"env": "nonexistent"})
        assert len(result) == 0

    def test_no_filter_returns_all(self, registry):
        registry.register("gw1", "10.0.0.1:8443", labels={"env": "prod"})
        registry.register("gw2", "10.0.0.2:8443")
        result = registry.list_all()
        assert len(result) == 2


# ─── API validation helpers ─────────────────────────────────────────────────


class TestValidationHelpers:
    def test_validate_labels_valid(self):
        from shoreguard.api.validation import validate_labels as _validate_labels

        _validate_labels({"env": "prod", "team": "ml"})  # Should not raise

    def test_validate_labels_none(self):
        from shoreguard.api.validation import validate_labels as _validate_labels

        _validate_labels(None)  # Should not raise

    def test_validate_labels_invalid_key(self):
        from fastapi import HTTPException

        from shoreguard.api.validation import validate_labels as _validate_labels

        with pytest.raises(HTTPException) as exc_info:
            _validate_labels({"--bad": "value"})
        assert exc_info.value.status_code == 400

    def test_validate_labels_too_many(self):
        from fastapi import HTTPException

        from shoreguard.api.validation import validate_labels as _validate_labels

        labels = {f"key{i}": f"val{i}" for i in range(21)}
        with pytest.raises(HTTPException) as exc_info:
            _validate_labels(labels)
        assert exc_info.value.status_code == 400

    def test_validate_labels_value_too_long(self):
        from fastapi import HTTPException

        from shoreguard.api.validation import validate_labels as _validate_labels

        with pytest.raises(HTTPException) as exc_info:
            _validate_labels({"key": "x" * 254})
        assert exc_info.value.status_code == 400

    def test_validate_description_valid(self):
        from shoreguard.api.validation import validate_description as _validate_description

        _validate_description("Short description")  # Should not raise

    def test_validate_description_too_long(self):
        from fastapi import HTTPException

        from shoreguard.api.validation import validate_description as _validate_description

        with pytest.raises(HTTPException) as exc_info:
            _validate_description("x" * 1001)
        assert exc_info.value.status_code == 400

    def test_validate_description_none(self):
        from shoreguard.api.validation import validate_description as _validate_description

        _validate_description(None)  # Should not raise
