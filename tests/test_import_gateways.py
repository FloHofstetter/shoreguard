"""Tests for _import_filesystem_gateways and the auto-import on startup."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shoreguard.api.cli import _import_filesystem_gateways
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


def _make_gateway(gateways_dir, name, endpoint="https://8.8.8.8:8443", **extra_meta):
    """Create a filesystem gateway directory with metadata.json."""
    gw_dir = gateways_dir / name
    gw_dir.mkdir(parents=True, exist_ok=True)
    metadata = {"gateway_endpoint": endpoint, **extra_meta}
    (gw_dir / "metadata.json").write_text(json.dumps(metadata))
    return gw_dir


# ─── Happy path ──────────────────────────────────────────────────────────────


class TestImportBasic:
    def test_import_single_gateway(self, registry, tmp_path, monkeypatch):
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        gateways_dir = tmp_path / "gateways"
        _make_gateway(gateways_dir, "my-gw", "https://8.8.8.8:8443")

        imported, skipped = _import_filesystem_gateways(registry)

        assert imported == 1
        assert skipped == 0
        gw = registry.get("my-gw")
        assert gw is not None
        assert gw["endpoint"] == "8.8.8.8:8443"
        assert gw["scheme"] == "https"

    def test_import_multiple_gateways(self, registry, tmp_path, monkeypatch):
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        gateways_dir = tmp_path / "gateways"
        _make_gateway(gateways_dir, "alpha", "https://8.8.8.8:8443")
        _make_gateway(gateways_dir, "beta", "http://1.1.1.1:8080")

        imported, skipped = _import_filesystem_gateways(registry)

        assert imported == 2
        assert skipped == 0
        assert registry.get("alpha") is not None
        assert registry.get("beta") is not None

    def test_import_http_scheme(self, registry, tmp_path, monkeypatch):
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        gateways_dir = tmp_path / "gateways"
        _make_gateway(gateways_dir, "insecure-gw", "http://8.8.4.4:8080")

        _import_filesystem_gateways(registry)

        gw = registry.get("insecure-gw")
        assert gw["scheme"] == "http"

    def test_import_with_mtls_certs(self, registry, tmp_path, monkeypatch):
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        gateways_dir = tmp_path / "gateways"
        gw_dir = _make_gateway(gateways_dir, "tls-gw", "https://8.8.8.8:8443")
        mtls_dir = gw_dir / "mtls"
        mtls_dir.mkdir()
        (mtls_dir / "ca.crt").write_bytes(b"ca-data")
        (mtls_dir / "tls.crt").write_bytes(b"cert-data")
        (mtls_dir / "tls.key").write_bytes(b"key-data")

        _import_filesystem_gateways(registry)

        creds = registry.get_credentials("tls-gw")
        assert creds["ca_cert"] == b"ca-data"
        assert creds["client_cert"] == b"cert-data"
        assert creds["client_key"] == b"key-data"

    def test_import_preserves_metadata(self, registry, tmp_path, monkeypatch):
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        gateways_dir = tmp_path / "gateways"
        _make_gateway(
            gateways_dir,
            "gpu-gw",
            "https://8.8.8.8:8443",
            gpu=True,
            is_remote=True,
            remote_host="192.168.1.100",
        )

        _import_filesystem_gateways(registry)

        gw = registry.get("gpu-gw")
        assert gw["metadata"]["gpu"] is True
        assert gw["metadata"]["is_remote"] is True
        assert gw["metadata"]["remote_host"] == "192.168.1.100"


# ─── Skip / idempotency ─────────────────────────────────────────────────────


class TestImportSkip:
    def test_skip_already_registered(self, registry, tmp_path, monkeypatch):
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        gateways_dir = tmp_path / "gateways"
        _make_gateway(gateways_dir, "existing-gw", "https://8.8.8.8:8443")

        # Pre-register
        registry.register("existing-gw", "8.8.8.8:8443")

        imported, skipped = _import_filesystem_gateways(registry)

        assert imported == 0
        assert skipped == 1

    def test_idempotent_double_import(self, registry, tmp_path, monkeypatch):
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        gateways_dir = tmp_path / "gateways"
        _make_gateway(gateways_dir, "my-gw", "https://8.8.8.8:8443")

        imported1, _ = _import_filesystem_gateways(registry)
        imported2, skipped2 = _import_filesystem_gateways(registry)

        assert imported1 == 1
        assert imported2 == 0
        assert skipped2 == 1

    def test_skip_non_directory_entries(self, registry, tmp_path, monkeypatch):
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        gateways_dir = tmp_path / "gateways"
        gateways_dir.mkdir(parents=True)
        (gateways_dir / "random-file.txt").write_text("not a gateway")

        imported, skipped = _import_filesystem_gateways(registry)

        assert imported == 0
        assert skipped == 0

    def test_skip_directory_without_metadata(self, registry, tmp_path, monkeypatch):
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        gateways_dir = tmp_path / "gateways"
        (gateways_dir / "empty-gw").mkdir(parents=True)

        imported, skipped = _import_filesystem_gateways(registry)

        assert imported == 0
        assert skipped == 0


# ─── No gateways directory ───────────────────────────────────────────────────


class TestImportNoDirectory:
    def test_missing_gateways_dir(self, registry, tmp_path, monkeypatch):
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        # No gateways/ directory at all

        imported, skipped = _import_filesystem_gateways(registry)

        assert imported == 0
        assert skipped == 0

    def test_empty_gateways_dir(self, registry, tmp_path, monkeypatch):
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        (tmp_path / "gateways").mkdir()

        imported, skipped = _import_filesystem_gateways(registry)

        assert imported == 0
        assert skipped == 0


# ─── Error handling ──────────────────────────────────────────────────────────


class TestImportErrors:
    def test_corrupt_json_skips_gateway(self, registry, tmp_path, monkeypatch):
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        gateways_dir = tmp_path / "gateways"
        gw_dir = gateways_dir / "bad-json"
        gw_dir.mkdir(parents=True)
        (gw_dir / "metadata.json").write_text("not valid json{{{")

        # Also add a valid one to verify partial success
        _make_gateway(gateways_dir, "good-gw", "https://8.8.8.8:8443")

        imported, skipped = _import_filesystem_gateways(registry)

        assert imported == 1
        assert skipped == 1
        assert registry.get("bad-json") is None
        assert registry.get("good-gw") is not None

    def test_register_value_error_skips(self, registry, tmp_path, monkeypatch):
        """If registry.register raises ValueError, the gateway is skipped."""
        from unittest.mock import patch

        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        gateways_dir = tmp_path / "gateways"
        _make_gateway(gateways_dir, "conflict-gw", "https://8.8.8.8:8443")

        with patch.object(registry, "register", side_effect=ValueError("already registered")):
            imported, skipped = _import_filesystem_gateways(registry)

        assert imported == 0
        assert skipped == 1

    def test_register_unexpected_error_skips(self, registry, tmp_path, monkeypatch):
        """Unexpected errors during register are caught and skipped."""
        from unittest.mock import patch

        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        gateways_dir = tmp_path / "gateways"
        _make_gateway(gateways_dir, "broken-gw", "https://8.8.8.8:8443")

        with patch.object(registry, "register", side_effect=RuntimeError("unexpected")):
            imported, skipped = _import_filesystem_gateways(registry)

        assert imported == 0
        assert skipped == 1

    def test_missing_endpoint_is_skipped(self, registry, tmp_path, monkeypatch):
        """Gateway with empty gateway_endpoint is skipped (no hostname)."""
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        gateways_dir = tmp_path / "gateways"
        gw_dir = gateways_dir / "no-endpoint"
        gw_dir.mkdir(parents=True)
        (gw_dir / "metadata.json").write_text(json.dumps({"some_key": "value"}))

        imported, skipped = _import_filesystem_gateways(registry)

        assert imported == 0
        assert skipped == 1
        assert registry.get("no-endpoint") is None


# ─── log_fn callback ────────────────────────────────────────────────────────


class TestImportLogging:
    def test_log_fn_receives_messages(self, registry, tmp_path, monkeypatch):
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        gateways_dir = tmp_path / "gateways"
        _make_gateway(gateways_dir, "my-gw", "https://8.8.8.8:8443")

        messages: list[str] = []
        _import_filesystem_gateways(registry, log_fn=messages.append)

        assert any("imported" in m and "my-gw" in m for m in messages)

    def test_log_fn_reports_skips(self, registry, tmp_path, monkeypatch):
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        gateways_dir = tmp_path / "gateways"
        _make_gateway(gateways_dir, "my-gw", "https://8.8.8.8:8443")
        registry.register("my-gw", "8.8.8.8:8443")

        messages: list[str] = []
        _import_filesystem_gateways(registry, log_fn=messages.append)

        assert any("skip" in m and "my-gw" in m for m in messages)

    def test_log_fn_reports_errors(self, registry, tmp_path, monkeypatch):
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        gateways_dir = tmp_path / "gateways"
        gw_dir = gateways_dir / "bad"
        gw_dir.mkdir(parents=True)
        (gw_dir / "metadata.json").write_text("broken{{{")

        messages: list[str] = []
        _import_filesystem_gateways(registry, log_fn=messages.append)

        assert any("error" in m and "bad" in m for m in messages)

    def test_no_log_fn_uses_logger(self, registry, tmp_path, monkeypatch, caplog):
        """When log_fn is None, messages go to the module logger."""
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        gateways_dir = tmp_path / "gateways"
        _make_gateway(gateways_dir, "my-gw", "https://8.8.8.8:8443")

        import logging

        with caplog.at_level(logging.INFO, logger="shoreguard"):
            _import_filesystem_gateways(registry)

        assert any("imported" in r.message and "my-gw" in r.message for r in caplog.records)


# ─── Security: private IP blocking in import ─────────────────────────────────


class TestImportSSRF:
    def test_private_ip_skipped(self, registry, tmp_path, monkeypatch):
        """Import skips gateways pointing at private/loopback IPs (non-local mode)."""
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        monkeypatch.delenv("SHOREGUARD_LOCAL_MODE", raising=False)
        gateways_dir = tmp_path / "gateways"
        _make_gateway(gateways_dir, "private-gw", "https://127.0.0.1:8443")

        imported, skipped = _import_filesystem_gateways(registry)

        assert imported == 0
        assert skipped == 1
        assert registry.get("private-gw") is None

    def test_rfc1918_skipped(self, registry, tmp_path, monkeypatch):
        """Import skips gateways pointing at RFC1918 addresses (non-local mode)."""
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        monkeypatch.delenv("SHOREGUARD_LOCAL_MODE", raising=False)
        gateways_dir = tmp_path / "gateways"
        _make_gateway(gateways_dir, "internal-gw", "https://192.168.1.1:8443")

        imported, skipped = _import_filesystem_gateways(registry)

        assert imported == 0
        assert skipped == 1

    def test_private_ip_allowed_in_local_mode(self, registry, tmp_path, monkeypatch):
        """Import allows private IPs in local mode."""
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        monkeypatch.setenv("SHOREGUARD_LOCAL_MODE", "1")
        gateways_dir = tmp_path / "gateways"
        _make_gateway(gateways_dir, "local-gw", "https://127.0.0.1:8443")

        imported, skipped = _import_filesystem_gateways(registry)

        assert imported == 1
        assert skipped == 0
        assert registry.get("local-gw") is not None


# ─── mTLS cert size limits in import ──────────────────────────────────────────


class TestImportCertLimits:
    def test_oversized_cert_skipped(self, registry, tmp_path, monkeypatch):
        """Import skips gateways with certs exceeding 64KB."""
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        gateways_dir = tmp_path / "gateways"
        gw_dir = _make_gateway(gateways_dir, "big-cert-gw", "https://8.8.8.8:8443")
        mtls_dir = gw_dir / "mtls"
        mtls_dir.mkdir()
        (mtls_dir / "ca.crt").write_bytes(b"x" * 70_000)
        (mtls_dir / "tls.crt").write_bytes(b"cert")
        (mtls_dir / "tls.key").write_bytes(b"key")

        imported, skipped = _import_filesystem_gateways(registry)

        assert imported == 0
        assert skipped == 1
        assert registry.get("big-cert-gw") is None

    def test_mtls_read_error_skipped(self, registry, tmp_path, monkeypatch):
        """Import skips gateways when mTLS cert files can't be read."""
        monkeypatch.setattr("shoreguard.config.openshell_config_dir", lambda: tmp_path)
        gateways_dir = tmp_path / "gateways"
        gw_dir = _make_gateway(gateways_dir, "bad-mtls-gw", "https://8.8.8.8:8443")
        mtls_dir = gw_dir / "mtls"
        mtls_dir.mkdir()
        # Create a "file" that is actually a directory — will cause read_bytes() to fail
        (mtls_dir / "ca.crt").mkdir()

        imported, skipped = _import_filesystem_gateways(registry)

        assert imported == 0
        assert skipped == 1
