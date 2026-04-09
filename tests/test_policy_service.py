"""Tests for PolicyService atomic network rule operations."""

from __future__ import annotations

import pytest

from shoreguard.exceptions import NotFoundError, PolicyError
from shoreguard.services.policy import PolicyService


@pytest.fixture
def policy_svc(mock_client):
    """PolicyService with a mocked client."""
    return PolicyService(mock_client)


def _make_policy(network_policies: dict | None = None) -> dict:
    """Helper to build a policy response dict."""
    policy: dict[str, object] = {"status": "loaded"}
    if network_policies is not None:
        policy["network_policies"] = network_policies
    return {"policy": policy}


def test_add_network_rule(policy_svc, mock_client):
    """Adding a rule merges it into the existing network_policies."""
    mock_client.policies.get.return_value = _make_policy(
        {"existing_rule": {"hosts": ["example.com"]}}
    )
    mock_client.policies.update.return_value = {"revision": 2}

    result = policy_svc.add_network_rule("sb1", "new_rule", {"hosts": ["test.com"]})

    assert "policy" in result
    call_args = mock_client.policies.update.call_args
    updated_policy = call_args[0][1]
    # Should contain both old and new rules
    assert "existing_rule" in updated_policy.network_policies
    assert "new_rule" in updated_policy.network_policies


def test_add_network_rule_creates_section(policy_svc, mock_client):
    """Adding a rule when no network_policies section exists creates one."""
    mock_client.policies.get.return_value = _make_policy()
    mock_client.policies.update.return_value = {"revision": 2}

    policy_svc.add_network_rule("sb1", "first_rule", {"hosts": ["test.com"]})

    call_args = mock_client.policies.update.call_args
    updated_policy = call_args[0][1]
    assert "first_rule" in updated_policy.network_policies


def test_delete_network_rule(policy_svc, mock_client):
    """Deleting a rule removes it from network_policies."""
    mock_client.policies.get.return_value = _make_policy(
        {"keep": {"hosts": ["a.com"]}, "remove": {"hosts": ["b.com"]}}
    )
    mock_client.policies.update.return_value = {"revision": 3}

    policy_svc.delete_network_rule("sb1", "remove")

    call_args = mock_client.policies.update.call_args
    updated_policy = call_args[0][1]
    assert "keep" in updated_policy.network_policies
    assert "remove" not in updated_policy.network_policies


def test_delete_nonexistent_rule(policy_svc, mock_client):
    """Deleting a rule that doesn't exist is a no-op (idempotent)."""
    mock_client.policies.get.return_value = _make_policy({"keep": {"hosts": ["a.com"]}})
    mock_client.policies.update.return_value = {"revision": 3}

    policy_svc.delete_network_rule("sb1", "nonexistent")

    call_args = mock_client.policies.update.call_args
    updated_policy = call_args[0][1]
    assert "keep" in updated_policy.network_policies


def test_add_rule_raises_when_no_policy(policy_svc, mock_client):
    """Adding a rule raises when sandbox has no policy yet."""
    mock_client.policies.get.return_value = {"policy": None}

    with pytest.raises(PolicyError, match="Could not read current policy"):
        policy_svc.add_network_rule("sb1", "rule", {"hosts": ["test.com"]})


def test_get_policy(policy_svc, mock_client):
    """get() delegates directly to the client."""
    mock_client.policies.get.return_value = _make_policy()

    result = policy_svc.get("sb1")

    mock_client.policies.get.assert_called_once_with("sb1")
    assert result == _make_policy()


def test_update_policy(policy_svc, mock_client):
    """update() converts dict to protobuf, calls update, then re-fetches full policy."""
    mock_client.policies.update.return_value = {"version": 5, "policy_hash": "abc"}
    mock_client.policies.get.return_value = _make_policy()

    result = policy_svc.update("sb1", {"network_policies": {}})

    assert mock_client.policies.update.called
    mock_client.policies.get.assert_called_with("sb1")
    assert result == _make_policy()


def test_list_revisions(policy_svc, mock_client):
    """list_revisions() forwards limit/offset to the client."""
    mock_client.policies.list_revisions.return_value = [{"revision": 1}]

    result = policy_svc.list_revisions("sb1", limit=5, offset=10)

    mock_client.policies.list_revisions.assert_called_once_with("sb1", limit=5, offset=10)
    assert result == [{"revision": 1}]


def test_apply_preset_found(policy_svc, mock_client):
    """apply_preset() merges preset network_policies into current policy."""
    mock_client.policies.get.return_value = _make_policy()
    mock_client.policies.update.return_value = {"revision": 2}

    policy_svc.apply_preset("sb1", "pypi")

    proto = mock_client.policies.update.call_args[0][1]
    assert "pypi" in proto.network_policies


def test_apply_preset_not_found(policy_svc, mock_client):
    """apply_preset() raises NotFoundError for unknown preset name."""
    with pytest.raises(NotFoundError, match="Preset"):
        policy_svc.apply_preset("sb1", "nonexistent-preset-xyz")


def _make_policy_with_fs(read_only: list[str], read_write: list[str]) -> dict:
    return {
        "policy": {
            "status": "loaded",
            "filesystem": {
                "read_only": read_only,
                "read_write": read_write,
                "include_workdir": False,
            },
        }
    }


def test_add_filesystem_path_ro(policy_svc, mock_client):
    """add_filesystem_path with access='ro' adds path to read_only."""
    mock_client.policies.get.return_value = _make_policy_with_fs([], [])
    mock_client.policies.update.return_value = {"revision": 2}

    policy_svc.add_filesystem_path("sb1", "/tmp", "ro")

    proto = mock_client.policies.update.call_args[0][1]
    assert "/tmp" in list(proto.filesystem.read_only)
    assert "/tmp" not in list(proto.filesystem.read_write)


def test_add_filesystem_path_rw(policy_svc, mock_client):
    """add_filesystem_path with access='rw' adds path to read_write."""
    mock_client.policies.get.return_value = _make_policy_with_fs([], [])
    mock_client.policies.update.return_value = {"revision": 2}

    policy_svc.add_filesystem_path("sb1", "/data", "rw")

    proto = mock_client.policies.update.call_args[0][1]
    assert "/data" in list(proto.filesystem.read_write)
    assert "/data" not in list(proto.filesystem.read_only)


def test_delete_filesystem_path(policy_svc, mock_client):
    """delete_filesystem_path removes path from both lists."""
    mock_client.policies.get.return_value = _make_policy_with_fs(["/tmp"], [])
    mock_client.policies.update.return_value = {"revision": 3}

    policy_svc.delete_filesystem_path("sb1", "/tmp")

    proto = mock_client.policies.update.call_args[0][1]
    assert "/tmp" not in list(proto.filesystem.read_only)


def test_update_process_policy(policy_svc, mock_client):
    """update_process_policy sets run_as_user, run_as_group, and landlock."""
    mock_client.policies.get.return_value = _make_policy()
    mock_client.policies.update.return_value = {"revision": 2}

    policy_svc.update_process_policy(
        "sb1",
        run_as_user="nobody",
        run_as_group="nogroup",
        landlock_compatibility="soft",
    )

    proto = mock_client.policies.update.call_args[0][1]
    assert proto.process.run_as_user == "nobody"
    assert proto.process.run_as_group == "nogroup"
    assert proto.landlock.compatibility == "soft"


# ─── Mutation-killing tests ──────────────────────────────────────────────────


def test_add_filesystem_path_moves_ro_to_rw(policy_svc, mock_client):
    """Adding path as rw that exists in read_only moves it to read_write."""
    mock_client.policies.get.return_value = _make_policy_with_fs(["/data"], [])
    mock_client.policies.update.return_value = {"revision": 2}

    policy_svc.add_filesystem_path("sb1", "/data", "rw")

    proto = mock_client.policies.update.call_args[0][1]
    assert "/data" in list(proto.filesystem.read_write)
    assert "/data" not in list(proto.filesystem.read_only)


def test_add_filesystem_path_creates_section(policy_svc, mock_client):
    """Adding path with no filesystem section creates one with include_workdir=False."""
    mock_client.policies.get.return_value = _make_policy()
    mock_client.policies.update.return_value = {"revision": 2}

    policy_svc.add_filesystem_path("sb1", "/opt", "ro")

    proto = mock_client.policies.update.call_args[0][1]
    assert "/opt" in list(proto.filesystem.read_only)
    assert proto.filesystem.include_workdir is False


def test_delete_filesystem_path_from_read_write(policy_svc, mock_client):
    """delete_filesystem_path removes path from read_write list."""
    mock_client.policies.get.return_value = _make_policy_with_fs([], ["/data"])
    mock_client.policies.update.return_value = {"revision": 3}

    policy_svc.delete_filesystem_path("sb1", "/data")

    proto = mock_client.policies.update.call_args[0][1]
    assert "/data" not in list(proto.filesystem.read_write)


def test_delete_filesystem_path_no_filesystem_section(policy_svc, mock_client):
    """delete_filesystem_path when no filesystem section is a no-op."""
    mock_client.policies.get.return_value = _make_policy()
    mock_client.policies.update.return_value = {"revision": 3}

    policy_svc.delete_filesystem_path("sb1", "/nonexistent")

    # Should still call update (read-modify-write), just no crash
    assert mock_client.policies.update.called


def test_update_process_policy_only_user(policy_svc, mock_client):
    """update_process_policy with only run_as_user — run_as_group not overridden."""
    mock_client.policies.get.return_value = _make_policy()
    mock_client.policies.update.return_value = {"revision": 2}

    policy_svc.update_process_policy("sb1", run_as_user="nobody")

    proto = mock_client.policies.update.call_args[0][1]
    assert proto.process.run_as_user == "nobody"
    # run_as_group should be default (empty string), not set
    assert proto.process.run_as_group == ""


def test_update_process_policy_only_landlock(policy_svc, mock_client):
    """update_process_policy with only landlock_compatibility."""
    mock_client.policies.get.return_value = _make_policy()
    mock_client.policies.update.return_value = {"revision": 2}

    policy_svc.update_process_policy("sb1", landlock_compatibility="best_effort")

    proto = mock_client.policies.update.call_args[0][1]
    assert proto.landlock.compatibility == "best_effort"
    # process should exist but be empty defaults
    assert proto.process.run_as_user == ""
    assert proto.process.run_as_group == ""


# ─── Mutation-killing tests: add_filesystem_path ────────────────────────────


class TestAddFilesystemPathMutationKill:
    """Kill all 27 survivors in add_filesystem_path."""

    def test_ro_path_not_in_rw(self, policy_svc, mock_client):
        """A read_only path must NOT appear in read_write."""
        mock_client.policies.get.return_value = _make_policy_with_fs([], [])
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.add_filesystem_path("sb1", "/etc", "ro")

        proto = mock_client.policies.update.call_args[0][1]
        assert list(proto.filesystem.read_only) == ["/etc"]
        assert list(proto.filesystem.read_write) == []

    def test_rw_path_not_in_ro(self, policy_svc, mock_client):
        """A read_write path must NOT appear in read_only."""
        mock_client.policies.get.return_value = _make_policy_with_fs([], [])
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.add_filesystem_path("sb1", "/var", "rw")

        proto = mock_client.policies.update.call_args[0][1]
        assert list(proto.filesystem.read_write) == ["/var"]
        assert list(proto.filesystem.read_only) == []

    def test_move_rw_to_ro(self, policy_svc, mock_client):
        """Adding a path as ro that exists in rw must remove it from rw."""
        mock_client.policies.get.return_value = _make_policy_with_fs([], ["/data"])
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.add_filesystem_path("sb1", "/data", "ro")

        proto = mock_client.policies.update.call_args[0][1]
        assert "/data" in list(proto.filesystem.read_only)
        assert "/data" not in list(proto.filesystem.read_write)

    def test_dedup_ro_path(self, policy_svc, mock_client):
        """Adding a path already in ro must not create duplicates."""
        mock_client.policies.get.return_value = _make_policy_with_fs(["/etc"], [])
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.add_filesystem_path("sb1", "/etc", "ro")

        proto = mock_client.policies.update.call_args[0][1]
        ro = list(proto.filesystem.read_only)
        assert ro.count("/etc") == 1

    def test_dedup_rw_path(self, policy_svc, mock_client):
        """Adding a path already in rw must not create duplicates."""
        mock_client.policies.get.return_value = _make_policy_with_fs([], ["/data"])
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.add_filesystem_path("sb1", "/data", "rw")

        proto = mock_client.policies.update.call_args[0][1]
        rw = list(proto.filesystem.read_write)
        assert rw.count("/data") == 1

    def test_creates_filesystem_section_with_defaults(self, policy_svc, mock_client):
        """When no filesystem section, creates one with correct defaults."""
        mock_client.policies.get.return_value = _make_policy()
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.add_filesystem_path("sb1", "/opt", "rw")

        proto = mock_client.policies.update.call_args[0][1]
        assert "/opt" in list(proto.filesystem.read_write)
        assert list(proto.filesystem.read_only) == []
        assert proto.filesystem.include_workdir is False

    def test_preserves_existing_paths(self, policy_svc, mock_client):
        """Adding a new path must not remove existing paths."""
        mock_client.policies.get.return_value = _make_policy_with_fs(["/etc"], ["/var"])
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.add_filesystem_path("sb1", "/tmp", "ro")

        proto = mock_client.policies.update.call_args[0][1]
        ro = list(proto.filesystem.read_only)
        rw = list(proto.filesystem.read_write)
        assert "/etc" in ro
        assert "/tmp" in ro
        assert "/var" in rw

    def test_preserves_include_workdir(self, policy_svc, mock_client):
        """Adding a path must not change include_workdir."""
        policy_resp = {
            "policy": {
                "status": "loaded",
                "filesystem": {
                    "read_only": [],
                    "read_write": [],
                    "include_workdir": True,
                },
            }
        }
        mock_client.policies.get.return_value = policy_resp
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.add_filesystem_path("sb1", "/opt", "ro")

        proto = mock_client.policies.update.call_args[0][1]
        assert proto.filesystem.include_workdir is True

    def test_calls_update_with_sandbox_name(self, policy_svc, mock_client):
        """update must be called with the correct sandbox name."""
        mock_client.policies.get.return_value = _make_policy_with_fs([], [])
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.add_filesystem_path("my-sandbox", "/etc", "ro")

        call_args = mock_client.policies.update.call_args
        assert call_args[0][0] == "my-sandbox"

    def test_calls_get_with_sandbox_name(self, policy_svc, mock_client):
        """get must be called with the correct sandbox name."""
        mock_client.policies.get.return_value = _make_policy_with_fs([], [])
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.add_filesystem_path("my-sandbox", "/etc", "ro")

        # First call is in _read_modify_write, second is in update's re-fetch
        assert mock_client.policies.get.call_args_list[0][0] == ("my-sandbox",)

    def test_no_policy_raises_error(self, policy_svc, mock_client):
        """If policy is None/empty, must raise PolicyError."""
        mock_client.policies.get.return_value = {"policy": {}}

        with pytest.raises(PolicyError, match="Could not read current policy"):
            policy_svc.add_filesystem_path("sb1", "/etc", "ro")

    def test_access_ro_default_behavior(self, policy_svc, mock_client):
        """Non-'rw' access value should add to read_only (else branch)."""
        mock_client.policies.get.return_value = _make_policy_with_fs([], [])
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.add_filesystem_path("sb1", "/etc", "readonly")

        proto = mock_client.policies.update.call_args[0][1]
        assert "/etc" in list(proto.filesystem.read_only)
        assert "/etc" not in list(proto.filesystem.read_write)


# ─── Mutation-killing tests: delete_filesystem_path ─────────────────────────


class TestDeleteFilesystemPathMutationKill:
    """Kill all 17 survivors in delete_filesystem_path."""

    def test_deletes_from_ro_only(self, policy_svc, mock_client):
        """Path removed from read_only, read_write unchanged."""
        mock_client.policies.get.return_value = _make_policy_with_fs(["/tmp", "/etc"], ["/var"])
        mock_client.policies.update.return_value = {"revision": 3}

        policy_svc.delete_filesystem_path("sb1", "/tmp")

        proto = mock_client.policies.update.call_args[0][1]
        assert "/tmp" not in list(proto.filesystem.read_only)
        assert "/etc" in list(proto.filesystem.read_only)
        assert "/var" in list(proto.filesystem.read_write)

    def test_deletes_from_rw_only(self, policy_svc, mock_client):
        """Path removed from read_write, read_only unchanged."""
        mock_client.policies.get.return_value = _make_policy_with_fs(["/etc"], ["/var", "/data"])
        mock_client.policies.update.return_value = {"revision": 3}

        policy_svc.delete_filesystem_path("sb1", "/var")

        proto = mock_client.policies.update.call_args[0][1]
        assert "/var" not in list(proto.filesystem.read_write)
        assert "/data" in list(proto.filesystem.read_write)
        assert "/etc" in list(proto.filesystem.read_only)

    def test_deletes_from_both_lists(self, policy_svc, mock_client):
        """If path is in both lists (unusual), remove from both."""
        mock_client.policies.get.return_value = _make_policy_with_fs(["/tmp"], ["/tmp"])
        mock_client.policies.update.return_value = {"revision": 3}

        policy_svc.delete_filesystem_path("sb1", "/tmp")

        proto = mock_client.policies.update.call_args[0][1]
        assert "/tmp" not in list(proto.filesystem.read_only)
        assert "/tmp" not in list(proto.filesystem.read_write)

    def test_nonexistent_path_is_noop(self, policy_svc, mock_client):
        """Deleting a path that doesn't exist should not raise or alter others."""
        mock_client.policies.get.return_value = _make_policy_with_fs(["/etc"], ["/var"])
        mock_client.policies.update.return_value = {"revision": 3}

        policy_svc.delete_filesystem_path("sb1", "/nonexistent")

        proto = mock_client.policies.update.call_args[0][1]
        assert list(proto.filesystem.read_only) == ["/etc"]
        assert list(proto.filesystem.read_write) == ["/var"]

    def test_no_filesystem_section(self, policy_svc, mock_client):
        """When no filesystem section exists, should still call update."""
        mock_client.policies.get.return_value = _make_policy()
        mock_client.policies.update.return_value = {"revision": 3}

        policy_svc.delete_filesystem_path("sb1", "/etc")

        mock_client.policies.update.assert_called_once()

    def test_calls_update_with_correct_sandbox(self, policy_svc, mock_client):
        mock_client.policies.get.return_value = _make_policy_with_fs(["/etc"], [])
        mock_client.policies.update.return_value = {"revision": 3}

        policy_svc.delete_filesystem_path("my-sb", "/etc")

        assert mock_client.policies.update.call_args[0][0] == "my-sb"

    def test_empty_policy_raises(self, policy_svc, mock_client):
        mock_client.policies.get.return_value = {"policy": None}

        with pytest.raises(PolicyError):
            policy_svc.delete_filesystem_path("sb1", "/etc")

    def test_preserves_include_workdir(self, policy_svc, mock_client):
        """Deleting a path must not change include_workdir."""
        policy_resp = {
            "policy": {
                "status": "loaded",
                "filesystem": {
                    "read_only": ["/etc"],
                    "read_write": [],
                    "include_workdir": True,
                },
            }
        }
        mock_client.policies.get.return_value = policy_resp
        mock_client.policies.update.return_value = {"revision": 3}

        policy_svc.delete_filesystem_path("sb1", "/etc")

        proto = mock_client.policies.update.call_args[0][1]
        assert proto.filesystem.include_workdir is True


# ─── Mutation-killing tests: update_process_policy ──────────────────────────


class TestUpdateProcessPolicyMutationKill:
    """Kill all 10 survivors in update_process_policy."""

    def test_only_group(self, policy_svc, mock_client):
        mock_client.policies.get.return_value = _make_policy()
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.update_process_policy("sb1", run_as_group="wheel")

        proto = mock_client.policies.update.call_args[0][1]
        assert proto.process.run_as_group == "wheel"
        assert proto.process.run_as_user == ""

    def test_no_args_leaves_process_empty(self, policy_svc, mock_client):
        """Calling with no args should create empty process dict but not set values."""
        mock_client.policies.get.return_value = _make_policy()
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.update_process_policy("sb1")

        proto = mock_client.policies.update.call_args[0][1]
        assert proto.process.run_as_user == ""
        assert proto.process.run_as_group == ""

    def test_user_and_group_together(self, policy_svc, mock_client):
        mock_client.policies.get.return_value = _make_policy()
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.update_process_policy("sb1", run_as_user="app", run_as_group="app")

        proto = mock_client.policies.update.call_args[0][1]
        assert proto.process.run_as_user == "app"
        assert proto.process.run_as_group == "app"

    def test_landlock_and_user(self, policy_svc, mock_client):
        mock_client.policies.get.return_value = _make_policy()
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.update_process_policy("sb1", run_as_user="root", landlock_compatibility="strict")

        proto = mock_client.policies.update.call_args[0][1]
        assert proto.process.run_as_user == "root"
        assert proto.landlock.compatibility == "strict"

    def test_existing_process_section_preserved(self, policy_svc, mock_client):
        """If policy already has process section, it should be updated not replaced."""
        policy_resp = {
            "policy": {
                "status": "loaded",
                "process": {"run_as_user": "existing"},
            }
        }
        mock_client.policies.get.return_value = policy_resp
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.update_process_policy("sb1", run_as_group="newgroup")

        proto = mock_client.policies.update.call_args[0][1]
        assert proto.process.run_as_user == "existing"
        assert proto.process.run_as_group == "newgroup"

    def test_calls_correct_sandbox(self, policy_svc, mock_client):
        mock_client.policies.get.return_value = _make_policy()
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.update_process_policy("prod-sandbox", run_as_user="nobody")

        assert mock_client.policies.update.call_args[0][0] == "prod-sandbox"
        assert mock_client.policies.get.call_args_list[0][0] == ("prod-sandbox",)


# ─── Mutation-killing tests: apply_preset ───────────────────────────────────


class TestApplyPresetMutationKill:
    """Kill all 9 survivors in apply_preset."""

    def test_preset_merges_into_existing_rules(self, policy_svc, mock_client):
        """Preset network rules merge with existing ones, not replace."""
        mock_client.policies.get.return_value = _make_policy({"existing": {"hosts": ["a.com"]}})
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.apply_preset("sb1", "pypi")

        proto = mock_client.policies.update.call_args[0][1]
        assert "existing" in proto.network_policies
        assert "pypi" in proto.network_policies

    def test_preset_creates_network_section(self, policy_svc, mock_client):
        """If no network_policies section, preset creates one."""
        mock_client.policies.get.return_value = _make_policy()
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.apply_preset("sb1", "pypi")

        proto = mock_client.policies.update.call_args[0][1]
        assert "pypi" in proto.network_policies

    def test_not_found_preset_raises(self, policy_svc, mock_client):
        with pytest.raises(NotFoundError, match="not found"):
            policy_svc.apply_preset("sb1", "totally-nonexistent-preset-abc123")

    def test_not_found_preset_does_not_call_get(self, policy_svc, mock_client):
        """If preset is not found, should not even fetch the current policy."""
        with pytest.raises(NotFoundError):
            policy_svc.apply_preset("sb1", "totally-nonexistent-preset-abc123")
        mock_client.policies.get.assert_not_called()

    def test_calls_correct_sandbox(self, policy_svc, mock_client):
        mock_client.policies.get.return_value = _make_policy()
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.apply_preset("my-sb", "pypi")

        assert mock_client.policies.update.call_args[0][0] == "my-sb"


# ─── Mutation-killing tests: delete_network_rule ────────────────────────────


class TestDeleteNetworkRuleMutationKill:
    """Kill all 7 survivors in delete_network_rule."""

    def test_removes_correct_key(self, policy_svc, mock_client):
        mock_client.policies.get.return_value = _make_policy(
            {"rule_a": {"hosts": ["a.com"]}, "rule_b": {"hosts": ["b.com"]}}
        )
        mock_client.policies.update.return_value = {"revision": 3}

        policy_svc.delete_network_rule("sb1", "rule_a")

        proto = mock_client.policies.update.call_args[0][1]
        assert "rule_a" not in proto.network_policies
        assert "rule_b" in proto.network_policies

    def test_nonexistent_key_is_noop(self, policy_svc, mock_client):
        """Deleting nonexistent key must not raise and must preserve others."""
        mock_client.policies.get.return_value = _make_policy({"keep": {"hosts": ["a.com"]}})
        mock_client.policies.update.return_value = {"revision": 3}

        policy_svc.delete_network_rule("sb1", "nonexistent")

        proto = mock_client.policies.update.call_args[0][1]
        assert "keep" in proto.network_policies
        assert len(proto.network_policies) == 1

    def test_no_network_section(self, policy_svc, mock_client):
        """Deleting from empty network_policies must not raise."""
        mock_client.policies.get.return_value = _make_policy()
        mock_client.policies.update.return_value = {"revision": 3}

        policy_svc.delete_network_rule("sb1", "anything")

        mock_client.policies.update.assert_called_once()

    def test_calls_correct_sandbox(self, policy_svc, mock_client):
        mock_client.policies.get.return_value = _make_policy({"r": {"hosts": []}})
        mock_client.policies.update.return_value = {"revision": 3}

        policy_svc.delete_network_rule("prod", "r")

        assert mock_client.policies.update.call_args[0][0] == "prod"


# ─── Mutation-killing tests: update ─────────────────────────────────────────


class TestUpdateMutationKill:
    """Kill all 6 survivors in update."""

    def test_returns_refetched_policy(self, policy_svc, mock_client):
        """update() must return the re-fetched policy, not the update response."""
        mock_client.policies.update.return_value = {"revision": 5}
        refetched = _make_policy({"r": {"hosts": ["x.com"]}})
        mock_client.policies.get.return_value = refetched

        result = policy_svc.update("sb1", {"network_policies": {}})

        assert result == refetched
        assert result is not mock_client.policies.update.return_value

    def test_calls_update_then_get(self, policy_svc, mock_client):
        """update must call client.policies.update THEN client.policies.get."""
        mock_client.policies.update.return_value = {"revision": 5}
        mock_client.policies.get.return_value = _make_policy()

        policy_svc.update("sb1", {"network_policies": {}})

        mock_client.policies.update.assert_called_once()
        # get is called with sandbox name
        mock_client.policies.get.assert_called_once_with("sb1")

    def test_passes_correct_sandbox_to_update(self, policy_svc, mock_client):
        mock_client.policies.update.return_value = {"revision": 5}
        mock_client.policies.get.return_value = _make_policy()

        policy_svc.update("my-sandbox", {"status": "loaded"})

        assert mock_client.policies.update.call_args[0][0] == "my-sandbox"

    def test_converts_dict_to_proto(self, policy_svc, mock_client):
        """The dict must be converted to protobuf before calling client.update."""
        mock_client.policies.update.return_value = {"revision": 5}
        mock_client.policies.get.return_value = _make_policy()

        policy_svc.update("sb1", {"network_policies": {"r1": {"hosts": ["a.com"]}}})

        proto = mock_client.policies.update.call_args[0][1]
        # Should be a protobuf object, not a dict
        assert not isinstance(proto, dict)
        assert "r1" in proto.network_policies


# ─── Mutation-killing tests: diff_revisions ─────────────────────────────────


class TestDiffRevisionsMutationKill:
    """Kill all 4 survivors in diff_revisions."""

    def test_returns_correct_structure(self, policy_svc, mock_client):
        mock_client.policies.get_version.side_effect = [
            {"policy": {"v": 1}, "revision": {"id": "a"}},
            {"policy": {"v": 2}, "revision": {"id": "b"}},
        ]

        result = policy_svc.diff_revisions("sb1", 1, 2)

        assert result["version_a"] == 1
        assert result["version_b"] == 2
        assert result["policy_a"] == {"v": 1}
        assert result["policy_b"] == {"v": 2}
        assert result["revision_a"] == {"id": "a"}
        assert result["revision_b"] == {"id": "b"}

    def test_versions_not_swapped(self, policy_svc, mock_client):
        """version_a and version_b must map to the correct policies."""
        mock_client.policies.get_version.side_effect = [
            {"policy": "FIRST", "revision": "REV_A"},
            {"policy": "SECOND", "revision": "REV_B"},
        ]

        result = policy_svc.diff_revisions("sb1", 10, 20)

        assert result["version_a"] == 10
        assert result["version_b"] == 20
        assert result["policy_a"] == "FIRST"
        assert result["policy_b"] == "SECOND"
        assert result["revision_a"] == "REV_A"
        assert result["revision_b"] == "REV_B"

    def test_calls_get_version_with_correct_args(self, policy_svc, mock_client):
        mock_client.policies.get_version.return_value = {"policy": {}, "revision": {}}

        policy_svc.diff_revisions("sb1", 3, 7)

        calls = mock_client.policies.get_version.call_args_list
        assert len(calls) == 2
        assert calls[0][0] == ("sb1", 3)
        assert calls[1][0] == ("sb1", 7)

    def test_missing_keys_return_none(self, policy_svc, mock_client):
        """If revision doesn't have 'policy' or 'revision' keys, return None."""
        mock_client.policies.get_version.side_effect = [
            {},  # no policy or revision keys
            {"policy": "p"},
        ]

        result = policy_svc.diff_revisions("sb1", 1, 2)

        assert result["policy_a"] is None
        assert result["revision_a"] is None
        assert result["policy_b"] == "p"
        assert result["revision_b"] is None


# ─── Mutation-killing tests: list_revisions ─────────────────────────────────


class TestListRevisionsMutationKill:
    """Kill all 2 survivors in list_revisions."""

    def test_default_limit_and_offset(self, policy_svc, mock_client):
        """Default limit=20 and offset=0 must be passed."""
        mock_client.policies.list_revisions.return_value = []

        policy_svc.list_revisions("sb1")

        mock_client.policies.list_revisions.assert_called_once_with("sb1", limit=20, offset=0)

    def test_custom_limit_and_offset(self, policy_svc, mock_client):
        mock_client.policies.list_revisions.return_value = [{"rev": 1}]

        result = policy_svc.list_revisions("sb1", limit=50, offset=5)

        mock_client.policies.list_revisions.assert_called_once_with("sb1", limit=50, offset=5)
        assert result == [{"rev": 1}]

    def test_returns_client_result_directly(self, policy_svc, mock_client):
        """Return value must be exactly what the client returns."""
        expected = [{"rev": 1}, {"rev": 2}]
        mock_client.policies.list_revisions.return_value = expected

        result = policy_svc.list_revisions("sb1")

        assert result is expected


# ─── Mutation-killing tests: _read_modify_write ────────────────────────────


class TestReadModifyWriteMutationKill:
    """Kill all 2 survivors in _read_modify_write."""

    def test_empty_policy_dict_raises(self, policy_svc, mock_client):
        """An empty dict (falsy) should raise PolicyError."""
        mock_client.policies.get.return_value = {"policy": {}}

        with pytest.raises(PolicyError, match="Could not read current policy"):
            policy_svc._read_modify_write("sb1", lambda p: None)

    def test_none_policy_raises(self, policy_svc, mock_client):
        """None policy should raise PolicyError."""
        mock_client.policies.get.return_value = {"policy": None}

        with pytest.raises(PolicyError, match="Could not read current policy"):
            policy_svc._read_modify_write("sb1", lambda p: None)

    def test_mutation_fn_receives_policy_dict(self, policy_svc, mock_client):
        """The mutation function must receive the actual policy dict."""
        mock_client.policies.get.return_value = _make_policy({"r": {"hosts": []}})
        mock_client.policies.update.return_value = {"revision": 2}

        received = []

        def capture(policy):
            received.append(dict(policy))

        policy_svc._read_modify_write("sb1", capture)

        assert len(received) == 1
        assert "status" in received[0]
        assert "network_policies" in received[0]

    def test_returns_update_result(self, policy_svc, mock_client):
        """_read_modify_write must return the result of self.update()."""
        mock_client.policies.get.return_value = _make_policy()
        expected = _make_policy({"new": {"hosts": []}})
        # update calls client.policies.update then client.policies.get
        mock_client.policies.update.return_value = {"revision": 2}
        # The second get call (from update) returns the expected
        mock_client.policies.get.side_effect = [
            _make_policy(),  # first call from _read_modify_write
            expected,  # second call from update's re-fetch
        ]

        result = policy_svc._read_modify_write("sb1", lambda p: None)

        assert result == expected


# ─── Mutation-killing tests: add_network_rule ───────────────────────────────


class TestAddNetworkRuleMutationKill:
    """Kill all 5 survivors in add_network_rule."""

    def test_rule_value_is_exact(self, policy_svc, mock_client):
        """The rule dict must be stored exactly as passed with correct name."""
        mock_client.policies.get.return_value = _make_policy()
        mock_client.policies.update.return_value = {"revision": 2}

        rule = {"name": "my test rule", "endpoints": [{"host": "test.com", "port": 443}]}
        policy_svc.add_network_rule("sb1", "my_rule", rule)

        proto = mock_client.policies.update.call_args[0][1]
        assert "my_rule" in proto.network_policies
        stored_rule = proto.network_policies["my_rule"]
        assert stored_rule.name == "my test rule"

    def test_overwrites_existing_key(self, policy_svc, mock_client):
        """Adding a rule with existing key must overwrite it."""
        mock_client.policies.get.return_value = _make_policy({"my_rule": {"name": "old rule"}})
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.add_network_rule("sb1", "my_rule", {"name": "new rule"})

        proto = mock_client.policies.update.call_args[0][1]
        assert proto.network_policies["my_rule"].name == "new rule"

    def test_correct_sandbox_name(self, policy_svc, mock_client):
        mock_client.policies.get.return_value = _make_policy()
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.add_network_rule("test-sb", "r", {"hosts": []})

        assert mock_client.policies.update.call_args[0][0] == "test-sb"

    def test_creates_network_section_if_missing(self, policy_svc, mock_client):
        mock_client.policies.get.return_value = _make_policy()
        mock_client.policies.update.return_value = {"revision": 2}

        policy_svc.add_network_rule("sb1", "new", {"hosts": ["x.com"]})

        proto = mock_client.policies.update.call_args[0][1]
        assert "new" in proto.network_policies
