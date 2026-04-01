"""Policy preset loading — pure functions, no gRPC dependency."""

from __future__ import annotations

import logging
import pathlib
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_PRESETS_DIR = pathlib.Path(__file__).parent / "presets"


def list_presets() -> list[dict[str, str]]:
    """List available policy presets bundled with Shoreguard.

    Returns:
        list[dict[str, str]]: Dicts with keys ``name``, ``description``,
            and ``file``.
    """
    presets: list[dict[str, str]] = []
    if not _PRESETS_DIR.exists():
        return presets
    for path in sorted(_PRESETS_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
        except (yaml.YAMLError, AttributeError, OSError):
            logger.warning("Failed to load preset file %s", path.name, exc_info=True)
            continue
        if not isinstance(data, dict):
            continue
        preset_meta = data.get("preset", {})
        presets.append(
            {
                "name": preset_meta.get("name", path.stem),
                "description": preset_meta.get("description", ""),
                "file": path.name,
            }
        )
    return presets


def get_preset(preset_name: str) -> dict[str, Any] | None:
    """Load a single preset by name.

    Args:
        preset_name: Filesystem-safe preset identifier (no path separators).

    Returns:
        dict[str, Any] | None: Dict with keys ``name``, ``description``,
            and ``policy`` where *policy* contains the network_policies /
            filesystem / process definitions, or ``None`` if not found.
    """
    preset_path = _PRESETS_DIR / f"{preset_name}.yaml"
    if not preset_path.resolve().is_relative_to(_PRESETS_DIR.resolve()):
        return None
    if not preset_path.exists():
        return None
    try:
        data = yaml.safe_load(preset_path.read_text())
    except (yaml.YAMLError, AttributeError, OSError):
        logger.warning("Failed to load preset file %s", preset_path.name, exc_info=True)
        return None
    if not isinstance(data, dict):
        return None
    preset_meta = data.pop("preset", {})
    return {
        "name": preset_meta.get("name", preset_name),
        "description": preset_meta.get("description", ""),
        "policy": data,
    }
