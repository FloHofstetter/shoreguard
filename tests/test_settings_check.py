"""Tests for Settings.check_production_readiness()."""

from __future__ import annotations

import pytest

from shoreguard.settings import (
    AuthSettings,
    CORSSettings,
    DatabaseSettings,
    OIDCSettings,
    ServerSettings,
    Settings,
)


def _make_settings(**overrides: object) -> Settings:
    """Build a Settings with prod-like defaults overridable per test."""
    defaults: dict[str, object] = {
        "server": ServerSettings(
            host="0.0.0.0",
            port=8888,
            log_level="info",
            log_format="json",
            local_mode=False,
        ),
        "auth": AuthSettings(
            no_auth=False,
            secret_key="x" * 32,
            allow_registration=False,
            hsts_enabled=True,
            csp_policy="default-src 'self'",
        ),
        "cors": CORSSettings(),
        "database": DatabaseSettings(),
        "oidc": OIDCSettings(),
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_clean_prod_config_has_no_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHOREGUARD_DATABASE_URL", "postgresql://u:p@h/db")
    s = _make_settings()
    warnings = s.check_production_readiness()
    assert warnings == []


def test_is_prod_like_false_in_dev_mode() -> None:
    s = _make_settings(
        server=ServerSettings(host="127.0.0.1", log_format="text"),
        auth=AuthSettings(no_auth=False, secret_key="x" * 32),
    )
    assert s._is_prod_like() is False


def test_is_prod_like_true_on_public_bind() -> None:
    s = _make_settings()
    assert s._is_prod_like() is True


def test_warns_on_hsts_disabled_in_prod() -> None:
    s = _make_settings(
        auth=AuthSettings(
            no_auth=False,
            secret_key="x" * 32,
            hsts_enabled=False,
            csp_policy="default-src 'self'",
        ),
    )
    warnings = s.check_production_readiness()
    assert any("hsts_enabled=false" in w for w in warnings)


def test_warns_on_unsafe_csp_always() -> None:
    # Even in local mode — unsafe-* in CSP is never acceptable
    s = _make_settings(
        server=ServerSettings(host="127.0.0.1"),
        auth=AuthSettings(
            no_auth=False,
            secret_key="x" * 32,
            csp_policy="default-src 'self'; script-src 'self' 'unsafe-inline'",
        ),
    )
    warnings = s.check_production_readiness()
    assert any("unsafe-*" in w and "ERROR" in w for w in warnings)


def test_warns_on_allow_registration_in_prod() -> None:
    s = _make_settings(
        auth=AuthSettings(
            no_auth=False,
            secret_key="x" * 32,
            hsts_enabled=True,
            allow_registration=True,
            csp_policy="default-src 'self'",
        ),
    )
    warnings = s.check_production_readiness()
    assert any("allow_registration=true" in w for w in warnings)


def test_no_registration_warning_in_dev() -> None:
    s = _make_settings(
        server=ServerSettings(host="127.0.0.1"),
        auth=AuthSettings(
            no_auth=False,
            secret_key="x" * 32,
            allow_registration=True,
            csp_policy="default-src 'self'",
        ),
    )
    warnings = s.check_production_readiness()
    assert not any("allow_registration" in w for w in warnings)


def test_warns_on_multi_replica_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHOREGUARD_REPLICAS", "3")
    monkeypatch.setenv("SHOREGUARD_DATABASE_URL", "postgresql://u:p@h/db")
    s = _make_settings()
    warnings = s.check_production_readiness()
    assert any("SHOREGUARD_REPLICAS=3" in w for w in warnings)


def test_warns_on_sqlite_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHOREGUARD_DATABASE_URL", raising=False)
    s = _make_settings(
        server=ServerSettings(
            host="0.0.0.0",
            log_format="json",
            database_url="sqlite:///tmp/sg.db",
        ),
    )
    warnings = s.check_production_readiness()
    assert any("SQLite" in w and "ERROR" in w for w in warnings)


def test_warns_on_text_log_format_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHOREGUARD_DATABASE_URL", "postgresql://u:p@h/db")
    s = _make_settings(
        server=ServerSettings(host="0.0.0.0", log_format="text"),
    )
    warnings = s.check_production_readiness()
    assert any("log_format='text'" in w for w in warnings)


def test_no_prod_warnings_in_local_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHOREGUARD_DATABASE_URL", "sqlite:///tmp/local.db")
    s = _make_settings(
        server=ServerSettings(host="0.0.0.0", local_mode=True, log_format="text"),
        auth=AuthSettings(
            no_auth=False,
            secret_key="x" * 32,
            hsts_enabled=False,
            csp_policy="default-src 'self'",
        ),
    )
    warnings = s.check_production_readiness()
    # local_mode=True disables all prod-like gated warnings
    assert not any("hsts_enabled" in w for w in warnings)
    assert not any("SQLite" in w for w in warnings)
    assert not any("log_format" in w for w in warnings)
