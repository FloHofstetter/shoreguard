"""Shared configuration helpers for Shoreguard."""

from __future__ import annotations

import os
from pathlib import Path


def xdg_config_home() -> Path:
    """Return the XDG config home directory."""
    configured = os.environ.get("XDG_CONFIG_HOME")
    if configured:
        return Path(configured)
    return Path.home() / ".config"


def openshell_config_dir() -> Path:
    """Return the openshell config directory."""
    return xdg_config_home() / "openshell"
