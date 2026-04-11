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


# ── enforce_production_safety ──────────────────────────────────────────────


def test_enforce_production_safety_noop_on_clean_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev-default settings must not be misclassified as ERROR."""
    monkeypatch.setenv("SHOREGUARD_DATABASE_URL", "postgresql://u:p@h/db")
    s = _make_settings()
    # Must not raise.
    s.enforce_production_safety()


def test_enforce_production_safety_raises_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single ERROR-severity entry should block startup."""
    monkeypatch.delenv("SHOREGUARD_ALLOW_UNSAFE_CONFIG", raising=False)
    s = _make_settings(
        # allow_registration=True in prod-like → ERROR.
        auth=AuthSettings(
            no_auth=False,
            secret_key="x" * 32,
            allow_registration=True,
            hsts_enabled=True,
            csp_policy="default-src 'self'",
        ),
    )
    with pytest.raises(RuntimeError, match="prod-readiness ERROR"):
        s.enforce_production_safety()


def test_enforce_production_safety_override_allows_start(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The override env-var should turn the hard-fail into a CRITICAL log."""
    import logging

    monkeypatch.setenv("SHOREGUARD_ALLOW_UNSAFE_CONFIG", "true")
    s = _make_settings(
        auth=AuthSettings(
            no_auth=False,
            secret_key="x" * 32,
            allow_registration=True,
            hsts_enabled=True,
            csp_policy="default-src 'self'",
        ),
    )
    with caplog.at_level(logging.CRITICAL, logger="shoreguard.settings"):
        s.enforce_production_safety()  # must not raise
    assert any("SHOREGUARD_ALLOW_UNSAFE_CONFIG" in r.message for r in caplog.records)


def test_warns_on_unsafe_csp_in_legacy_mode() -> None:
    # When strict mode is explicitly disabled, unsafe-* in the legacy
    # csp_policy must still trigger an error — even on local binds.
    s = _make_settings(
        server=ServerSettings(host="127.0.0.1"),
        auth=AuthSettings(
            no_auth=False,
            secret_key="x" * 32,
            csp_strict=False,
            csp_policy="default-src 'self'; script-src 'self' 'unsafe-inline'",
        ),
    )
    warnings = s.check_production_readiness()
    assert any("unsafe-*" in w and "ERROR" in w for w in warnings)


def test_no_unsafe_csp_warning_in_strict_mode() -> None:
    # Strict mode is the default as of v0.27.0 — the legacy csp_policy
    # field is unused, so unsafe-* content in it must not trigger the warning.
    s = _make_settings(
        server=ServerSettings(host="127.0.0.1"),
        auth=AuthSettings(
            no_auth=False,
            secret_key="x" * 32,
            csp_strict=True,
            csp_policy="default-src 'self'; script-src 'self' 'unsafe-inline'",
        ),
    )
    warnings = s.check_production_readiness()
    assert not any("unsafe-*" in w for w in warnings)


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
    # With secret_key set (the _make_settings default), we still warn about
    # in-process rate limiters but do not escalate to an ERROR.
    assert any("WARN: SHOREGUARD_REPLICAS=3" in w for w in warnings)
    assert not any("ERROR: SHOREGUARD_REPLICAS=3" in w for w in warnings)


def test_errors_on_multi_replica_without_secret_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHOREGUARD_REPLICAS", "3")
    monkeypatch.setenv("SHOREGUARD_DATABASE_URL", "postgresql://u:p@h/db")
    s = _make_settings(
        auth=AuthSettings(
            no_auth=False,
            secret_key=None,
            allow_registration=False,
            hsts_enabled=True,
            csp_policy="default-src 'self'",
        ),
    )
    warnings = s.check_production_readiness()
    assert any("ERROR: SHOREGUARD_REPLICAS=3" in w and "secret_key is unset" in w for w in warnings)
    # enforce_production_safety() must refuse to start without the override.
    monkeypatch.delenv("SHOREGUARD_ALLOW_UNSAFE_CONFIG", raising=False)
    with pytest.raises(RuntimeError, match="SHOREGUARD_REPLICAS=3"):
        s.enforce_production_safety()


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
