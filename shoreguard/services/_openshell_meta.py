"""Loader for openshell.yaml — OpenShell metadata not available via gRPC."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_YAML_PATH = Path(__file__).parent.parent / "openshell.yaml"
_cached: OpenShellMeta | None = None


class OpenShellMeta:
    """Parsed OpenShell metadata from openshell.yaml.

    Args:
        data: Raw YAML dict loaded from ``openshell.yaml``.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self.provider_types: dict[str, dict[str, str]] = data.get("provider_types", {})
        self.inference_providers: list[dict[str, str]] = data.get("inference_providers", [])
        self.community_sandboxes: list[dict[str, Any]] = data.get("community_sandboxes", [])

    def get_provider_type(self, type_name: str) -> dict[str, str] | None:
        """Look up metadata for a provider type.

        Args:
            type_name: Provider type identifier (e.g. ``"nvcf"``).

        Returns:
            dict[str, str] | None: Metadata dict for the provider type,
                or ``None`` if unknown.
        """
        return self.provider_types.get(type_name)


def get_openshell_meta() -> OpenShellMeta:
    """Return cached OpenShell metadata (loaded once from YAML).

    Returns:
        OpenShellMeta: Singleton instance.
    """
    global _cached
    if _cached is None:
        data = yaml.safe_load(_YAML_PATH.read_text())
        _cached = OpenShellMeta(data)
    return _cached
