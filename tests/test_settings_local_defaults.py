"""Tests for the local-mode solo-developer defaults (metering + digest on)."""

from __future__ import annotations

import pytest

from shoreguard.settings import ServerSettings, Settings


def test_local_mode_enables_metering_and_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHOREGUARD_BUDGET_METERING_ENABLED", raising=False)
    monkeypatch.delenv("SHOREGUARD_DIGEST_ENABLED", raising=False)
    s = Settings(server=ServerSettings(local_mode=True))
    assert s.budget.metering_enabled is True
    assert s.digest.enabled is True


def test_non_local_mode_keeps_metering_and_digest_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHOREGUARD_BUDGET_METERING_ENABLED", raising=False)
    monkeypatch.delenv("SHOREGUARD_DIGEST_ENABLED", raising=False)
    s = Settings(server=ServerSettings(local_mode=False))
    assert s.budget.metering_enabled is False
    assert s.digest.enabled is False


def test_explicit_env_override_wins_in_local_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    # An operator who explicitly disables metering on a local box must keep it off.
    monkeypatch.setenv("SHOREGUARD_BUDGET_METERING_ENABLED", "false")
    monkeypatch.delenv("SHOREGUARD_DIGEST_ENABLED", raising=False)
    s = Settings(server=ServerSettings(local_mode=True))
    assert s.budget.metering_enabled is False
    # The digest env was left unset, so local mode still turns it on.
    assert s.digest.enabled is True
