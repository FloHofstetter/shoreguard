"""Integration tests for policy operations via client layer."""

from __future__ import annotations

import pytest

from shoreguard.client._converters import _dict_to_policy

pytestmark = pytest.mark.integration


def test_get_policy(ready_sandbox, sg_client):
    """get() returns policy data for a ready sandbox."""
    result = sg_client.policies.get(ready_sandbox["name"])

    assert "active_version" in result
    assert "revision" in result
    assert result["revision"]["version"] >= 1
    assert result["revision"]["status"] in ("loaded", "pending")


def test_list_revisions(ready_sandbox, sg_client):
    """list_revisions() returns at least the initial policy revision."""
    revisions = sg_client.policies.list_revisions(ready_sandbox["name"])

    assert isinstance(revisions, list)
    assert len(revisions) >= 1
    assert "version" in revisions[0]
    assert "status" in revisions[0]


def test_update_policy(ready_sandbox, sg_client):
    """Updating a policy increments the version."""
    current = sg_client.policies.get(ready_sandbox["name"])
    policy = current.get("policy", {})
    old_version = current["active_version"]

    # Add a filesystem path
    if "filesystem" not in policy:
        policy["filesystem"] = {"read_only": [], "read_write": [], "include_workdir": False}
    existing = policy["filesystem"].get("read_only", [])
    policy["filesystem"]["read_only"] = list(set(existing + ["/opt/test"]))

    proto = _dict_to_policy(policy)
    result = sg_client.policies.update(ready_sandbox["name"], proto)

    assert result["version"] > old_version


def test_policy_roundtrip(ready_sandbox, sg_client):
    """Update policy with known values, re-read, verify roundtrip."""
    current = sg_client.policies.get(ready_sandbox["name"])
    policy = current.get("policy", {})

    policy["process"] = {"run_as_user": "integ-test", "run_as_group": "integ-test"}
    proto = _dict_to_policy(policy)
    sg_client.policies.update(ready_sandbox["name"], proto)

    updated = sg_client.policies.get(ready_sandbox["name"])
    assert updated["policy"]["process"]["run_as_user"] == "integ-test"
    assert updated["policy"]["process"]["run_as_group"] == "integ-test"
