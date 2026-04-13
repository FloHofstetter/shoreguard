"""CLI tests for ``shoreguard policy`` — export, apply, and diff subcommands."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from typer.testing import CliRunner

import shoreguard.api.cli_policy as cli_policy_mod
from shoreguard.api.cli_policy import policy_app


@pytest.fixture
def runner():
    return CliRunner()


def _mock_response(
    status_code: int, json_body: dict | None = None, text: str = ""
) -> httpx.Response:
    request = httpx.Request("POST", "http://test")
    if json_body is not None:
        return httpx.Response(status_code, json=json_body, request=request)
    return httpx.Response(status_code, text=text, request=request)


def _patch_request(monkeypatch, response: httpx.Response) -> MagicMock:
    fake = MagicMock(return_value=response)
    monkeypatch.setattr(cli_policy_mod, "_request", fake)
    return fake


class TestExportCmd:
    def test_export_to_stdout(self, runner, monkeypatch):
        _patch_request(
            monkeypatch,
            _mock_response(200, {"yaml": "policy:\n  filesystem: {}\n"}),
        )
        result = runner.invoke(policy_app, ["export", "-g", "gw", "-s", "sb"])
        assert result.exit_code == 0
        assert "policy:" in result.stdout

    def test_export_to_file(self, runner, monkeypatch, tmp_path):
        _patch_request(
            monkeypatch,
            _mock_response(200, {"yaml": "policy: {}\n"}),
        )
        out = tmp_path / "p.yaml"
        result = runner.invoke(policy_app, ["export", "-g", "gw", "-s", "sb", "-o", str(out)])
        assert result.exit_code == 0
        assert out.read_text() == "policy: {}\n"

    def test_export_failure_exit_2(self, runner, monkeypatch):
        _patch_request(monkeypatch, _mock_response(500, text="boom"))
        result = runner.invoke(policy_app, ["export", "-g", "gw", "-s", "sb"])
        assert result.exit_code == 2


class TestDiffCmd:
    def _diff_payload(self, drift: bool) -> dict:
        diff = {
            "filesystem": {"read_only_added": ["/etc"]} if drift else {},
            "process": {},
            "network_policies": {"added": [], "removed": [], "changed": []},
        }
        return {"status": "dry_run", "current_hash": "h", "diff": diff}

    def test_diff_no_drift_exit_0(self, runner, monkeypatch, tmp_path):
        f = tmp_path / "p.yaml"
        f.write_text("policy: {}\n")
        _patch_request(monkeypatch, _mock_response(200, self._diff_payload(drift=False)))
        result = runner.invoke(policy_app, ["diff", "-g", "gw", "-s", "sb", "-f", str(f)])
        assert result.exit_code == 0
        assert "(no changes)" in result.stdout

    def test_diff_with_drift_exit_1(self, runner, monkeypatch, tmp_path):
        f = tmp_path / "p.yaml"
        f.write_text("policy: {}\n")
        _patch_request(monkeypatch, _mock_response(200, self._diff_payload(drift=True)))
        result = runner.invoke(policy_app, ["diff", "-g", "gw", "-s", "sb", "-f", str(f)])
        assert result.exit_code == 1
        assert "/etc" in result.stdout

    def test_diff_pin_423_exit_2(self, runner, monkeypatch, tmp_path):
        f = tmp_path / "p.yaml"
        f.write_text("policy: {}\n")
        _patch_request(monkeypatch, _mock_response(423, text="pinned"))
        result = runner.invoke(policy_app, ["diff", "-g", "gw", "-s", "sb", "-f", str(f)])
        assert result.exit_code == 2


class TestApplyCmd:
    def test_apply_up_to_date(self, runner, monkeypatch, tmp_path):
        f = tmp_path / "p.yaml"
        f.write_text("policy: {}\n")
        _patch_request(
            monkeypatch,
            _mock_response(
                200,
                {
                    "status": "up_to_date",
                    "current_hash": "h",
                    "diff": {
                        "filesystem": {},
                        "process": {},
                        "network_policies": {"added": [], "removed": [], "changed": []},
                    },
                },
            ),
        )
        result = runner.invoke(policy_app, ["apply", "-g", "gw", "-s", "sb", "-f", str(f)])
        assert result.exit_code == 0
        assert "up-to-date" in result.stdout

    def test_apply_applied(self, runner, monkeypatch, tmp_path):
        f = tmp_path / "p.yaml"
        f.write_text("policy: {}\n")
        _patch_request(
            monkeypatch,
            _mock_response(
                200,
                {
                    "status": "applied",
                    "current_hash": "h_old",
                    "applied_version": "h_new",
                    "diff": {
                        "filesystem": {"read_only_added": ["/etc"]},
                        "process": {},
                        "network_policies": {"added": [], "removed": [], "changed": []},
                    },
                },
            ),
        )
        result = runner.invoke(policy_app, ["apply", "-g", "gw", "-s", "sb", "-f", str(f)])
        assert result.exit_code == 0
        assert "h_new" in result.stdout

    def test_apply_vote_recorded_exit_1(self, runner, monkeypatch, tmp_path):
        f = tmp_path / "p.yaml"
        f.write_text("policy: {}\n")
        _patch_request(
            monkeypatch,
            _mock_response(
                202,
                {
                    "status": "vote_recorded",
                    "votes_cast": 1,
                    "votes_needed": 2,
                    "diff": {
                        "filesystem": {},
                        "process": {},
                        "network_policies": {"added": ["x"], "removed": [], "changed": []},
                    },
                },
            ),
        )
        result = runner.invoke(policy_app, ["apply", "-g", "gw", "-s", "sb", "-f", str(f)])
        assert result.exit_code == 1
        # CliRunner merges stderr into output by default in newer Click
        assert "1/2" in result.output

    def test_apply_version_mismatch_exit_2(self, runner, monkeypatch, tmp_path):
        f = tmp_path / "p.yaml"
        f.write_text("policy: {}\n")
        _patch_request(
            monkeypatch,
            _mock_response(409, {"status": "version_mismatch", "current_hash": "h"}),
        )
        result = runner.invoke(policy_app, ["apply", "-g", "gw", "-s", "sb", "-f", str(f)])
        assert result.exit_code == 2
