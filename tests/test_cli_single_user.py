"""CLI validation tests for ``shoreguard --single-user``."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from typer.testing import CliRunner

from shoreguard.cli import cli
from shoreguard.settings import reset_settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    monkeypatch.delenv("SHOREGUARD_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("SHOREGUARD_SINGLE_USER", raising=False)
    monkeypatch.delenv("SHOREGUARD_NO_AUTH", raising=False)
    reset_settings()
    yield
    reset_settings()


def test_single_user_with_no_auth_rejected() -> None:
    result = runner.invoke(cli, ["--single-user", "--no-auth"])
    assert result.exit_code != 0
    assert "contradict" in result.output


def test_single_user_without_password_and_tty_rejected() -> None:
    # CliRunner's stdin is not a TTY, so the interactive prompt fallback
    # must not fire — the flag has to fail with a clear pointer to
    # --admin-password / SHOREGUARD_ADMIN_PASSWORD.
    result = runner.invoke(cli, ["--single-user"])
    assert result.exit_code != 0
    assert "SHOREGUARD_ADMIN_PASSWORD" in result.output
