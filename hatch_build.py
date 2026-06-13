"""Hatch build hook for the ShoreGuard wheel.

The wheel force-includes ``frontend/dist`` — the compiled Preact/TypeScript
island bundle that ships inside the package as ``shoreguard/_frontend/dist``.
That directory is a Vite build artifact (git-ignored), so it is absent in a
fresh checkout. The two build shapes need it handled differently:

* **Standard wheel / sdist** (a real distribution artifact): the bundle must
  exist and be populated. The release pipeline builds the frontend first
  (the Dockerfile node stage and the PyPI job both run ``npm run build``), so
  the static ``force-include`` finds real files. If it is missing the build
  fails loudly — exactly what we want, since a wheel without the bundle ships
  a broken UI.
* **Editable install** (``uv sync`` in dev and every CI job that needs the
  project importable — lint, typecheck, tests): the app serves the source
  ``frontend/`` tree directly and Node is not available, so the bundle is
  neither built nor needed. This hook only guarantees the directory exists so
  the ``force-include`` does not raise ``Forced include not found``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class FrontendBuildHook(BuildHookInterface):
    """Ensure ``frontend/dist`` exists for editable installs."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        """Create an empty ``frontend/dist`` for editable builds.

        Args:
            version: The build target version — ``"editable"`` for editable
                installs, ``"standard"`` for a real wheel.
            build_data: Hatch's mutable build-data mapping (unused).
        """
        if version != "editable":
            # Real wheels must carry the built bundle; the static
            # force-include verifies it and fails loudly if it is missing.
            return
        dist = Path(self.root) / "frontend" / "dist"
        dist.mkdir(parents=True, exist_ok=True)
