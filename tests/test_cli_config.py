"""Tests for ``shoreguard config show`` / ``config schema`` CLI commands."""

from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from typer.testing import CliRunner

from shoreguard.api.cli import cli
from shoreguard.settings import reset_settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_settings_around_test() -> Generator[None]:
    reset_settings()
    yield
    reset_settings()


def test_config_show_all_sections_table() -> None:
    result = runner.invoke(cli, ["config", "show"])
    assert result.exit_code == 0, result.output
    # A handful of representative ENV vars from various sections must be present.
    for env_var in (
        "SHOREGUARD_HOST",
        "SHOREGUARD_DB_POOL_SIZE",
        "SHOREGUARD_GATEWAY_BACKOFF_MIN",
        "SHOREGUARD_AUDIT_RETENTION_DAYS",
        "SHOREGUARD_LIMIT_MAX_REQUEST_BODY_BYTES",
    ):
        assert env_var in result.output


def test_config_show_section_filter() -> None:
    result = runner.invoke(cli, ["config", "show", "gateway"])
    assert result.exit_code == 0
    assert "SHOREGUARD_GATEWAY_BACKOFF_MIN" in result.output
    # Other sections must NOT leak in
    assert "SHOREGUARD_HOST" not in result.output
    assert "SHOREGUARD_AUDIT_RETENTION_DAYS" not in result.output


def test_config_show_unknown_section_exits_nonzero() -> None:
    result = runner.invoke(cli, ["config", "show", "doesnotexist"])
    assert result.exit_code != 0
    assert "doesnotexist" in result.output


def test_config_show_json_format_parses() -> None:
    result = runner.invoke(cli, ["config", "show", "server", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "SHOREGUARD_HOST" in data
    assert data["SHOREGUARD_HOST"]["value"] == "0.0.0.0"
    assert data["SHOREGUARD_HOST"]["is_default"] is True


def test_config_show_redacts_secrets_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHOREGUARD_SECRET_KEY", "supersecretvalue")
    reset_settings()
    result = runner.invoke(cli, ["config", "show", "auth", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["SHOREGUARD_SECRET_KEY"]["value"] == "***REDACTED***"


def test_config_show_show_sensitive_reveals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHOREGUARD_SECRET_KEY", "supersecretvalue")
    reset_settings()
    result = runner.invoke(
        cli,
        ["config", "show", "auth", "--format", "json", "--show-sensitive"],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["SHOREGUARD_SECRET_KEY"]["value"] == "supersecretvalue"


def test_config_show_env_format_emits_comments() -> None:
    result = runner.invoke(cli, ["config", "show", "server", "--format", "env"])
    assert result.exit_code == 0
    # .env-style output should contain ENV=VALUE lines and # comments
    assert "SHOREGUARD_HOST=0.0.0.0" in result.output
    assert "# Bind address for the HTTP server" in result.output


def test_config_schema_markdown_format() -> None:
    result = runner.invoke(cli, ["config", "schema", "--format", "markdown"])
    assert result.exit_code == 0
    assert "# ShoreGuard Settings Reference" in result.output
    assert "## `server`" in result.output
    assert "| `SHOREGUARD_HOST` |" in result.output


def test_config_schema_ignores_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """`config schema` shows defaults regardless of the runtime environment."""
    monkeypatch.setenv("SHOREGUARD_PORT", "9999")
    reset_settings()
    result = runner.invoke(cli, ["config", "schema", "server", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    # value mirrors default because schema mode overrides effective values
    assert data["SHOREGUARD_PORT"]["value"] == 8888
    assert data["SHOREGUARD_PORT"]["is_default"] is True


def test_config_show_descriptions_present() -> None:
    """Sanity check: every field has a non-empty description."""
    result = runner.invoke(cli, ["config", "show", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    missing = [env_var for env_var, row in data.items() if not row["description"].strip()]
    assert not missing, f"Fields without description: {missing}"
