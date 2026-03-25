"""Integration tests for SandboxService with a real gateway."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_service_list(sandbox_service):
    """SandboxService.list() returns a list."""
    result = sandbox_service.list()
    assert isinstance(result, list)


def test_service_create_no_presets(sandbox_service, sg_client):
    """Create a sandbox without presets via the service layer."""
    result = sandbox_service.create(name="integ-svc-basic")

    assert "name" in result
    assert result["name"] == "integ-svc-basic"
    assert "preset_error" not in result

    sg_client.sandboxes.delete("integ-svc-basic")


def test_service_exec_string(sandbox_service, ready_sandbox):
    """exec() with a string command uses shlex parsing."""
    result = sandbox_service.exec(ready_sandbox["name"], "echo integration-test")

    assert result["exit_code"] == 0
    assert "integration-test" in result["stdout"]


def test_service_create_with_preset(sandbox_service, sg_client):
    """Create a sandbox with a preset applied."""
    import uuid

    name = f"integ-preset-{uuid.uuid4().hex[:8]}"
    result = sandbox_service.create(name=name, presets=["pypi"])

    assert "presets_applied" in result
    if result.get("presets_failed"):
        # UpdateConfig might be UNIMPLEMENTED on this gateway version
        pytest.skip("Preset application failed (likely UNIMPLEMENTED UpdateConfig)")
    assert "pypi" in result["presets_applied"]

    sg_client.sandboxes.delete(result["name"])
