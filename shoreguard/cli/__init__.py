"""ShoreGuard's Typer CLI.

The ``shoreguard`` console script points at :data:`cli` (see
``[project.scripts]`` in pyproject.toml). Subcommand groups live in
their own modules: :mod:`shoreguard.cli.audit`,
:mod:`shoreguard.cli.config`, and :mod:`shoreguard.cli.policy`.
"""

from shoreguard.cli.main import cli

__all__ = ("cli",)
