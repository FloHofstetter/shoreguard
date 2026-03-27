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
    policy = {"status": "loaded"}
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
