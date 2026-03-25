"""Integration tests for PolicyService with a real gateway."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_apply_preset(policy_service, ready_sandbox):
    """Applying a preset adds network rules to the policy."""
    result = policy_service.apply_preset(ready_sandbox["name"], "pypi")

    assert "version" in result or "policy_hash" in result

    # Verify the policy now contains pypi rules
    current = policy_service.get(ready_sandbox["name"])
    policy = current.get("policy", {})
    assert "pypi" in policy.get("network_policies", {})


def test_add_network_rule(policy_service, ready_sandbox):
    """Add a custom network rule and verify it's in the policy."""
    rule = {
        "name": "integ-test-rule",
        "endpoints": [{"host": "test.example.com", "port": 443}],
        "binaries": [],
    }
    policy_service.add_network_rule(ready_sandbox["name"], "integ-test", rule)

    current = policy_service.get(ready_sandbox["name"])
    policy = current.get("policy", {})
    assert "integ-test" in policy.get("network_policies", {})


def test_delete_network_rule(policy_service, ready_sandbox):
    """Add then delete a network rule."""
    rule = {
        "name": "to-delete",
        "endpoints": [{"host": "delete.example.com", "port": 80}],
        "binaries": [],
    }
    policy_service.add_network_rule(ready_sandbox["name"], "del-rule", rule)
    policy_service.delete_network_rule(ready_sandbox["name"], "del-rule")

    current = policy_service.get(ready_sandbox["name"])
    policy = current.get("policy", {})
    assert "del-rule" not in policy.get("network_policies", {})


def test_add_filesystem_path(policy_service, ready_sandbox):
    """Add a filesystem path to the policy."""
    policy_service.add_filesystem_path(ready_sandbox["name"], "/opt/integ-test", "rw")

    current = policy_service.get(ready_sandbox["name"])
    policy = current.get("policy", {})
    fs = policy.get("filesystem", {})
    assert "/opt/integ-test" in fs.get("read_write", [])


def test_update_process_policy(policy_service, ready_sandbox):
    """Update process policy and verify roundtrip."""
    policy_service.update_process_policy(
        ready_sandbox["name"],
        run_as_user="integ-user",
    )

    current = policy_service.get(ready_sandbox["name"])
    policy = current.get("policy", {})
    assert policy.get("process", {}).get("run_as_user") == "integ-user"
