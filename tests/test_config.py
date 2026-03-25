"""Tests for shared configuration helpers."""

from __future__ import annotations

from pathlib import Path

from shoreguard.config import openshell_config_dir, xdg_config_home


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
