"""Tests for shared configuration helpers."""

from __future__ import annotations

from pathlib import Path

from shoreguard.config import (
    default_database_url,
    openshell_config_dir,
    shoreguard_config_dir,
    xdg_config_home,
)


def test_xdg_config_home_default(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    result = xdg_config_home()
    assert result == Path.home() / ".config"


def test_xdg_config_home_custom(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "/custom/config")
    result = xdg_config_home()
    assert result == Path("/custom/config")


# ─── Mutation-killing tests ──────────────────────────────────────────────────


def test_openshell_config_dir_default(monkeypatch):
    """openshell_config_dir() returns xdg_config_home() / 'openshell'."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    result = openshell_config_dir()
    assert result == Path.home() / ".config" / "openshell"


def test_openshell_config_dir_custom_xdg(monkeypatch):
    """openshell_config_dir() with custom XDG_CONFIG_HOME."""
    monkeypatch.setenv("XDG_CONFIG_HOME", "/my/config")
    result = openshell_config_dir()
    assert result == Path("/my/config/openshell")


# ─── shoreguard_config_dir ──────────────────────────────────────────────────


def test_shoreguard_config_dir_default(monkeypatch):
    """shoreguard_config_dir() returns xdg_config_home() / 'shoreguard'."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    result = shoreguard_config_dir()
    assert result == Path.home() / ".config" / "shoreguard"


def test_shoreguard_config_dir_custom_xdg(monkeypatch):
    """shoreguard_config_dir() with custom XDG_CONFIG_HOME."""
    monkeypatch.setenv("XDG_CONFIG_HOME", "/my/config")
    result = shoreguard_config_dir()
    assert result == Path("/my/config/shoreguard")


# ─── default_database_url ───────────────────────────────────────────────────


def test_default_database_url_from_env(monkeypatch):
    """Uses SHOREGUARD_DATABASE_URL when set."""
    monkeypatch.setenv("SHOREGUARD_DATABASE_URL", "postgresql://localhost/sg")
    assert default_database_url() == "postgresql://localhost/sg"


def test_default_database_url_sqlite_fallback(monkeypatch):
    """Falls back to SQLite in shoreguard config dir."""
    monkeypatch.delenv("SHOREGUARD_DATABASE_URL", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/cfg")
    result = default_database_url()
    assert result == "sqlite:////tmp/cfg/shoreguard/shoreguard.db"


def test_default_database_url_empty_env_uses_default(monkeypatch):
    """Empty string SHOREGUARD_DATABASE_URL falls through to default."""
    monkeypatch.setenv("SHOREGUARD_DATABASE_URL", "")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/cfg")
    result = default_database_url()
    assert result == "sqlite:////tmp/cfg/shoreguard/shoreguard.db"
