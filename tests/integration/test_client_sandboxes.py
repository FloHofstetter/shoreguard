"""Integration tests for sandbox CRUD lifecycle via client layer."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_list_sandboxes(sg_client):
    """list() returns a list of sandbox dicts."""
    result = sg_client.sandboxes.list()

    assert isinstance(result, list)
    for sb in result:
        assert "id" in sb
        assert "name" in sb
        assert "phase" in sb


def test_create_and_delete(sandbox_factory, sg_client):
    """Create a sandbox with auto-name, verify fields, then delete."""
    sb = sandbox_factory()

    assert "id" in sb
    assert "name" in sb
    assert sb["name"]  # non-empty
    assert "phase" in sb

    deleted = sg_client.sandboxes.delete(sb["name"])
    assert deleted is True


def test_create_named(sandbox_factory):
    """Create a sandbox with explicit name."""
    sb = sandbox_factory(name="integ-named-test")

    assert sb["name"] == "integ-named-test"


def test_get_sandbox(sandbox_factory, sg_client):
    """Create a sandbox, then get it by name."""
    sb = sandbox_factory()
    fetched = sg_client.sandboxes.get(sb["name"])

    assert fetched["id"] == sb["id"]
    assert fetched["name"] == sb["name"]


def test_wait_ready(sandbox_factory, sg_client):
    """Create a sandbox and wait for it to reach ready phase."""
    sb = sandbox_factory()
    ready = sg_client.sandboxes.wait_ready(sb["name"], timeout_seconds=120.0)

    assert ready["phase"] == "ready"


def test_exec_command(ready_sandbox, sg_client):
    """Execute a command in a ready sandbox."""
    result = sg_client.sandboxes.exec(ready_sandbox["id"], ["echo", "hello-integration"])

    assert result["exit_code"] == 0
    assert "hello-integration" in result["stdout"]


def test_get_logs(ready_sandbox, sg_client):
    """Fetch logs from a ready sandbox."""
    logs = sg_client.sandboxes.get_logs(ready_sandbox["id"], lines=50)

    assert isinstance(logs, list)
    if logs:
        log = logs[0]
        assert "timestamp_ms" in log
        assert "message" in log
