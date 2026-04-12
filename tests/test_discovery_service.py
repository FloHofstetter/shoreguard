"""Tests for the DiscoveryService (M22)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import dns.exception
import dns.resolver
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.models import Base
from shoreguard.services.discovery import (
    DiscoveredEndpoint,
    DiscoveryService,
)
from shoreguard.services.gateway import GatewayService
from shoreguard.services.registry import GatewayRegistry
from shoreguard.settings import DiscoverySettings


def _settings(**overrides) -> DiscoverySettings:
    base = {
        "enabled": True,
        "domains": ["openshell.internal"],
        "interval_seconds": 60,
        "default_scheme": "grpc+tls",
        "auto_register": True,
        "resolver_timeout_seconds": 1.0,
    }
    base.update(overrides)
    return DiscoverySettings(**base)


@pytest.fixture
def discovery_setup():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    registry = GatewayRegistry(factory)
    gw_svc = GatewayService(registry)
    svc = DiscoveryService(registry, gw_svc, _settings())
    yield svc, registry, gw_svc
    engine.dispose()


class _FakeTarget:
    def __init__(self, name: str) -> None:
        self._name = name

    def __str__(self) -> str:
        return self._name + "."


def _srv(target: str, port: int, priority: int = 10, weight: int = 5):
    """Build a fake dnspython SRV rdata."""
    return SimpleNamespace(
        target=_FakeTarget(target),
        port=port,
        priority=priority,
        weight=weight,
    )


class _FakeAnswer(list):
    """Iterable returned by resolver.resolve."""


def _patch_resolve(records: list):
    return patch(
        "dns.resolver.Resolver.resolve",
        return_value=_FakeAnswer(records),
    )


class TestDiscoverDomain:
    def test_basic(self, discovery_setup):
        svc, _, _ = discovery_setup
        with _patch_resolve([_srv("gw1.openshell.internal", 30051)]):
            results = svc.discover_domain("openshell.internal")
        assert len(results) == 1
        assert results[0].host == "gw1.openshell.internal"
        assert results[0].port == 30051
        assert results[0].endpoint == "gw1.openshell.internal:30051"

    def test_sorted_by_priority(self, discovery_setup):
        svc, _, _ = discovery_setup
        with _patch_resolve(
            [
                _srv("c", 30051, priority=20),
                _srv("a", 30051, priority=10),
                _srv("b", 30051, priority=10, weight=10),
            ]
        ):
            results = svc.discover_domain("d")
        assert [r.host for r in results] == ["b", "a", "c"]

    def test_nxdomain(self, discovery_setup):
        svc, _, _ = discovery_setup
        with patch("dns.resolver.Resolver.resolve", side_effect=dns.resolver.NXDOMAIN()):
            assert svc.discover_domain("missing") == []

    def test_no_answer(self, discovery_setup):
        svc, _, _ = discovery_setup

        class _FakeNoAnswer(dns.resolver.NoAnswer):
            def __init__(self) -> None:  # noqa: D401
                Exception.__init__(self, "no answer")

        with patch(
            "dns.resolver.Resolver.resolve",
            side_effect=_FakeNoAnswer(),
        ):
            assert svc.discover_domain("d") == []

    def test_timeout(self, discovery_setup):
        svc, _, _ = discovery_setup
        with patch(
            "dns.resolver.Resolver.resolve",
            side_effect=dns.exception.Timeout(),
        ):
            assert svc.discover_domain("d") == []

    def test_generic_dns_error(self, discovery_setup):
        svc, _, _ = discovery_setup
        with patch(
            "dns.resolver.Resolver.resolve",
            side_effect=dns.exception.DNSException("boom"),
        ):
            assert svc.discover_domain("d") == []


class TestDiscoverAll:
    def test_iterates_settings_domains(self, discovery_setup):
        svc, _, _ = discovery_setup
        svc._settings = _settings(domains=["a", "b"])
        with patch.object(svc, "discover_domain", return_value=[]) as mock:
            svc.discover_all()
        assert [c.args[0] for c in mock.call_args_list] == ["a", "b"]

    def test_explicit_override(self, discovery_setup):
        svc, _, _ = discovery_setup
        with patch.object(svc, "discover_domain", return_value=[]) as mock:
            svc.discover_all(domains=["x"])
        assert [c.args[0] for c in mock.call_args_list] == ["x"]


class TestAutoRegister:
    def test_registers_new_endpoint(self, discovery_setup):
        svc, registry, _ = discovery_setup
        ep = DiscoveredEndpoint("gw.example.com", 30051, 10, 5, "example.com")
        result = svc.auto_register([ep])
        assert len(result["registered"]) == 1
        assert result["registered"][0]["name"]
        assert len(registry.list_all()) == 1

    def test_skips_already_registered(self, discovery_setup):
        svc, registry, gw_svc = discovery_setup
        gw_svc.register("preexisting", "gw.example.com:30051", scheme="grpc+tls")
        ep = DiscoveredEndpoint("gw.example.com", 30051, 10, 5, "example.com")
        result = svc.auto_register([ep])
        assert result["registered"] == []
        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["reason"] == "already_registered"

    def test_skips_private_ip(self, discovery_setup):
        svc, _, _ = discovery_setup
        ep = DiscoveredEndpoint("10.0.0.5", 30051, 10, 5, "example.com")
        result = svc.auto_register([ep])
        assert result["registered"] == []
        assert len(result["skipped"]) == 1

    def test_allows_svc_cluster_local(self, discovery_setup):
        svc, registry, _ = discovery_setup
        ep = DiscoveredEndpoint("openshell.default.svc.cluster.local", 30051, 10, 5, "internal")
        result = svc.auto_register([ep])
        assert len(result["registered"]) == 1
        assert len(registry.list_all()) == 1

    def test_dedupes_within_batch(self, discovery_setup):
        svc, registry, _ = discovery_setup
        ep1 = DiscoveredEndpoint("gw.example.com", 30051, 10, 5, "example.com")
        ep2 = DiscoveredEndpoint("gw.example.com", 30051, 20, 1, "example.com")
        result = svc.auto_register([ep1, ep2])
        assert len(result["registered"]) == 1
        assert len(result["skipped"]) == 1


class TestRunOnce:
    def test_run_once_persists(self, discovery_setup):
        svc, registry, _ = discovery_setup
        with _patch_resolve([_srv("gw.example.com", 30051)]):
            result = svc.run_once()
        assert len(result["registered"]) == 1
        assert len(registry.list_all()) == 1
        assert svc._last_run_at is not None

    def test_run_once_auto_register_off(self, discovery_setup):
        svc, registry, _ = discovery_setup
        svc._settings = _settings(auto_register=False)
        with _patch_resolve([_srv("gw.example.com", 30051)]):
            result = svc.run_once()
        assert result["registered"] == []
        assert len(result["skipped"]) == 1
        assert registry.list_all() == []

    def test_run_once_explicit_domains(self, discovery_setup):
        svc, _, _ = discovery_setup
        with patch.object(svc, "discover_domain", return_value=[]) as mock:
            svc.run_once(domains=["custom.example.com"])
        mock.assert_called_with("custom.example.com")


class TestStatus:
    def test_status_initial(self, discovery_setup):
        svc, _, _ = discovery_setup
        s = svc.status()
        assert s["enabled"] is True
        assert s["domains"] == ["openshell.internal"]
        assert s["last_run_at"] is None
        assert s["last_registered_count"] == 0

    def test_status_after_run(self, discovery_setup):
        svc, _, _ = discovery_setup
        with _patch_resolve([_srv("gw.example.com", 30051)]):
            svc.run_once()
        s = svc.status()
        assert s["last_run_at"] is not None
        assert s["last_registered_count"] == 1


class TestNameDerivation:
    def test_short_name(self, discovery_setup):
        svc, _, _ = discovery_setup
        ep = DiscoveredEndpoint("gw1.example.com", 30051, 10, 5, "example.com")
        assert DiscoveryService._derive_name(ep) == "gw1"

    def test_non_default_port(self, discovery_setup):
        svc, _, _ = discovery_setup
        ep = DiscoveredEndpoint("gw1.example.com", 9999, 10, 5, "example.com")
        assert DiscoveryService._derive_name(ep) == "gw1-9999"

    def test_sanitizes_invalid_chars(self, discovery_setup):
        svc, _, _ = discovery_setup
        ep = DiscoveredEndpoint("gw_one!", 30051, 10, 5, "example.com")
        name = DiscoveryService._derive_name(ep)
        assert "!" not in name
