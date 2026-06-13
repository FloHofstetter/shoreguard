"""Tests for the local-mode solo-developer defaults (metering, digest, one-tap)."""

from __future__ import annotations

import pytest

from shoreguard.settings import ServerSettings, Settings

_LOCAL_DEFAULT_ENVS = (
    "SHOREGUARD_BUDGET_METERING_ENABLED",
    "SHOREGUARD_DIGEST_ENABLED",
    "SHOREGUARD_WEBHOOK_ONE_TAP_APPROVALS",
)


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in _LOCAL_DEFAULT_ENVS:
        monkeypatch.delenv(env, raising=False)


def test_local_mode_enables_metering_digest_and_one_tap(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    s = Settings(server=ServerSettings(local_mode=True))
    assert s.budget.metering_enabled is True
    assert s.digest.enabled is True
    assert s.webhooks.one_tap_approvals is True


def test_non_local_mode_keeps_the_guardrails_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    s = Settings(server=ServerSettings(local_mode=False))
    assert s.budget.metering_enabled is False
    assert s.digest.enabled is False
    assert s.webhooks.one_tap_approvals is False


def test_explicit_env_override_wins_in_local_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    # An operator who explicitly disables a guardrail on a local box must keep it off.
    _clear(monkeypatch)
    monkeypatch.setenv("SHOREGUARD_BUDGET_METERING_ENABLED", "false")
    monkeypatch.setenv("SHOREGUARD_WEBHOOK_ONE_TAP_APPROVALS", "false")
    s = Settings(server=ServerSettings(local_mode=True))
    assert s.budget.metering_enabled is False
    assert s.webhooks.one_tap_approvals is False
    # The digest env was left unset, so local mode still turns it on.
    assert s.digest.enabled is True
