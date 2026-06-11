"""Generate TypeScript API types from the FastAPI OpenAPI schema.

Dumps ``app.openapi()`` to a temp file and runs ``openapi-typescript``
(from frontend/node_modules) over it, writing the result to
``frontend/src/api/types.gen.ts``. Run after changing REST schemas:

    uv run python scripts/generate_api_types.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "frontend" / "src" / "api" / "types.gen.ts"


def main() -> int:
    """Generate the TypeScript types file.

    Returns:
        int: Shell exit code (0 on success).
    """
    # Docs/OpenAPI are disabled once auth setup is complete; build the
    # schema straight from the app factory without running the lifespan.
    os.environ.setdefault("SHOREGUARD_NO_AUTH", "true")
    from shoreguard.app import create_app

    schema = create_app().openapi()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(schema, fh)
        schema_path = fh.name
    try:
        result = subprocess.run(  # noqa: S603
            ["npx", "openapi-typescript", schema_path, "--output", str(OUTPUT)],  # noqa: S607
            cwd=REPO_ROOT / "frontend",
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(schema_path).unlink(missing_ok=True)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        return result.returncode

    print(f"Wrote {OUTPUT.relative_to(REPO_ROOT)} ({OUTPUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
