"""Backwards-compatible entry module.

The application factory lives in :mod:`shoreguard.app`; this module
keeps the module-level ``app`` instance that ``uvicorn
shoreguard.api.main:app`` (and the test suite) reference, plus the
``cli`` re-export for older ``shoreguard.api.main:cli`` entry points.
"""

from shoreguard.app import create_app
from shoreguard.cli import cli

app = create_app()

__all__ = ("app", "cli")


if __name__ == "__main__":
    cli()
