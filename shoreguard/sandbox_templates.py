"""Sandbox template loading — pure functions, no gRPC dependency."""

from __future__ import annotations

import logging
import pathlib
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = pathlib.Path(__file__).parent / "sandbox_templates"


def list_templates() -> list[dict[str, str]]:
    """List available sandbox templates bundled with Shoreguard.

    Returns:
        list[dict[str, str]]: Dicts with keys ``name``, ``description``,
            ``category``, and ``file``.
    """
    templates: list[dict[str, str]] = []
    if not _TEMPLATES_DIR.exists():
        return templates
    for path in sorted(_TEMPLATES_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
        except (yaml.YAMLError, AttributeError, OSError):
            logger.warning("Failed to load template file %s", path.name, exc_info=True)
            continue
        if not isinstance(data, dict):
            continue
        meta = data.get("template", {})
        templates.append(
            {
                "name": meta.get("name", path.stem),
                "description": meta.get("description", ""),
                "category": meta.get("category", ""),
                "file": path.name,
            }
        )
    return templates


def get_template(template_name: str) -> dict[str, Any] | None:
    """Load a single sandbox template by name.

    Args:
        template_name: Filesystem-safe template identifier (no path separators).

    Returns:
        dict[str, Any] | None: Dict with keys ``name``, ``description``,
            ``category``, and ``sandbox`` containing the full sandbox config,
            or ``None`` if not found.
    """
    template_path = _TEMPLATES_DIR / f"{template_name}.yaml"
    if not template_path.resolve().is_relative_to(_TEMPLATES_DIR.resolve()):
        return None
    if not template_path.exists():
        return None
    try:
        data = yaml.safe_load(template_path.read_text())
    except (yaml.YAMLError, AttributeError, OSError):
        logger.warning("Failed to load template file %s", template_path.name, exc_info=True)
        return None
    if not isinstance(data, dict):
        return None
    meta = data.get("template", {})
    sandbox = data.get("sandbox", {})
    return {
        "name": meta.get("name", template_name),
        "description": meta.get("description", ""),
        "category": meta.get("category", ""),
        "sandbox": sandbox,
    }
