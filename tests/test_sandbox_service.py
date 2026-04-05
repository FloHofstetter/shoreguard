"""Tests for SandboxService create-with-presets workflow."""

from __future__ import annotations

import pytest

from shoreguard.exceptions import SandboxError
from shoreguard.services.sandbox import SandboxService


@pytest.fixture
def sandbox_svc(mock_client):
    """SandboxService with a mocked client."""
    return SandboxService(mock_client)


@pytest.fixture
def meta_store():
    """Real SandboxMetaStore backed by the in-memory test DB."""
    import shoreguard.services.sandbox_meta as mod

    return mod.sandbox_meta_store


@pytest.fixture
def sandbox_svc_with_meta(mock_client, meta_store):
    """SandboxService with a mocked client and real metadata store."""
    return SandboxService(mock_client, meta_store=meta_store)


def test_create_ssh_session(sandbox_svc, mock_client):
    """SSH session resolves sandbox name to ID and delegates to client."""
    mock_client.sandboxes.get.return_value = {"id": "abc-123", "name": "sb1"}
    mock_client.sandboxes.create_ssh_session.return_value = {
        "sandbox_id": "abc-123",
        "token": "tok-xyz",
        "gateway_host": "127.0.0.1",
        "gateway_port": 8080,
        "gateway_scheme": "https",
        "connect_path": "/connect",
        "host_key_fingerprint": "SHA256:abc",
        "expires_at_ms": 9999999,
    }

    result = sandbox_svc.create_ssh_session("sb1")

    mock_client.sandboxes.get.assert_called_once_with("sb1")
    mock_client.sandboxes.create_ssh_session.assert_called_once_with("abc-123")
    assert result["token"] == "tok-xyz"
    assert result["gateway_host"] == "127.0.0.1"


def test_revoke_ssh_session(sandbox_svc, mock_client):
    """Revoke delegates token to client and returns result."""
    mock_client.sandboxes.revoke_ssh_session.return_value = True

    result = sandbox_svc.revoke_ssh_session("tok-xyz")

    mock_client.sandboxes.revoke_ssh_session.assert_called_once_with("tok-xyz")
    assert result is True


def test_create_without_presets(sandbox_svc, mock_client):
    """Create without presets returns immediately, no polling."""
    mock_client.sandboxes.create.return_value = {"name": "sb1", "phase": "provisioning"}

    result = sandbox_svc.create(name="sb1", image="base")

    assert result == {"name": "sb1", "phase": "provisioning"}
    mock_client.sandboxes.wait_ready.assert_not_called()


def test_create_happy_path(sandbox_svc, mock_client):
    """Create with presets: wait_ready succeeds -> presets applied."""
    mock_client.sandboxes.create.return_value = {"name": "sb1"}
    mock_client.sandboxes.wait_ready.return_value = {"phase": "ready"}
    mock_client.policies.get.return_value = {"policy": {"status": "loaded"}}
    mock_client.policies.apply_preset.return_value = {"revision": 2}

    result = sandbox_svc.create(name="sb1", image="base", presets=["pypi"])

    assert result["presets_applied"] == ["pypi"]
    assert "presets_failed" not in result
    assert "preset_error" not in result


def test_create_error_state(sandbox_svc, mock_client):
    """Create returns early when sandbox enters error state."""
    mock_client.sandboxes.create.return_value = {"name": "sb1"}
    mock_client.sandboxes.wait_ready.side_effect = SandboxError("Sandbox sb1 entered error phase")

    result = sandbox_svc.create(name="sb1", image="base", presets=["pypi"])

    assert result["preset_error"] == "Sandbox entered error state"
    mock_client.policies.apply_preset.assert_not_called()


def test_create_timeout(sandbox_svc, mock_client):
    """Create returns early when sandbox does not become ready in time."""
    mock_client.sandboxes.create.return_value = {"name": "sb1"}
    mock_client.sandboxes.wait_ready.side_effect = TimeoutError(
        "Sandbox sb1 was not ready within 120s"
    )

    result = sandbox_svc.create(name="sb1", image="base", presets=["pypi"])

    assert result["preset_error"] == "Sandbox did not become ready in time"
    mock_client.policies.apply_preset.assert_not_called()


def test_create_preset_partial_failure(sandbox_svc, mock_client):
    """One preset succeeds, another fails — both recorded."""
    mock_client.sandboxes.create.return_value = {"name": "sb1"}
    mock_client.sandboxes.wait_ready.return_value = {"phase": "ready"}
    mock_client.policies.get.return_value = {"policy": {"status": "loaded"}}
    mock_client.policies.apply_preset.side_effect = [
        {"revision": 2},
        RuntimeError("preset not found"),
    ]

    result = sandbox_svc.create(name="sb1", image="base", presets=["pypi", "bad"])

    assert result["presets_applied"] == ["pypi"]
    assert len(result["presets_failed"]) == 1
    assert result["presets_failed"][0]["preset"] == "bad"


def test_list(sandbox_svc, mock_client):
    """list() delegates to client with limit/offset."""
    mock_client.sandboxes.list.return_value = [{"name": "sb1"}, {"name": "sb2"}]

    result = sandbox_svc.list(limit=50, offset=10)

    mock_client.sandboxes.list.assert_called_once_with(limit=50, offset=10)
    assert len(result) == 2


def test_get(sandbox_svc, mock_client):
    """get() delegates name to client."""
    mock_client.sandboxes.get.return_value = {"name": "sb1", "phase": "ready"}

    result = sandbox_svc.get("sb1")

    mock_client.sandboxes.get.assert_called_once_with("sb1")
    assert result["name"] == "sb1"


def test_delete(sandbox_svc, mock_client):
    """delete() delegates name to client and returns bool."""
    mock_client.sandboxes.delete.return_value = True

    result = sandbox_svc.delete("sb1")

    mock_client.sandboxes.delete.assert_called_once_with("sb1")
    assert result is True


def test_exec_string_command(sandbox_svc, mock_client):
    """exec() parses a string command via shlex before passing to client."""
    mock_client.sandboxes.get.return_value = {"id": "abc-123", "name": "sb1"}
    mock_client.sandboxes.exec.return_value = {"exit_code": 0, "stdout": "hello"}

    sandbox_svc.exec("sb1", "echo hello")

    _, call_kwargs = mock_client.sandboxes.exec.call_args
    # exec is called with positional (id, command), check command was split
    pos_args = mock_client.sandboxes.exec.call_args[0]
    assert pos_args[1] == ["echo", "hello"]


def test_exec_list_command(sandbox_svc, mock_client):
    """exec() passes a list command through unmodified."""
    mock_client.sandboxes.get.return_value = {"id": "abc-123", "name": "sb1"}
    mock_client.sandboxes.exec.return_value = {"exit_code": 0}

    sandbox_svc.exec("sb1", ["echo", "hello"])

    pos_args = mock_client.sandboxes.exec.call_args[0]
    assert pos_args[1] == ["echo", "hello"]


def test_get_logs(sandbox_svc, mock_client):
    """get_logs() resolves name to ID and forwards all params to client."""
    mock_client.sandboxes.get.return_value = {"id": "abc-123", "name": "sb1"}
    mock_client.sandboxes.get_logs.return_value = [{"message": "started"}]

    result = sandbox_svc.get_logs("sb1", lines=50, since_ms=1000, min_level="info")

    mock_client.sandboxes.get_logs.assert_called_once_with(
        "abc-123", lines=50, since_ms=1000, sources=None, min_level="info"
    )
    assert result == [{"message": "started"}]


# ─── Mutation-killing tests ──────────────────────────────────────────────────


def test_create_with_gpu(sandbox_svc, mock_client):
    """create() forwards gpu=True to client."""
    mock_client.sandboxes.create.return_value = {"name": "sb1", "phase": "provisioning"}

    sandbox_svc.create(name="sb1", image="base", gpu=True)

    mock_client.sandboxes.create.assert_called_once_with(
        name="sb1",
        image="base",
        gpu=True,
        providers=None,
        environment=None,
    )


def test_create_with_environment(sandbox_svc, mock_client):
    """create() forwards environment dict to client."""
    mock_client.sandboxes.create.return_value = {"name": "sb1"}

    sandbox_svc.create(name="sb1", environment={"FOO": "bar"})

    mock_client.sandboxes.create.assert_called_once_with(
        name="sb1",
        image="",
        gpu=False,
        providers=None,
        environment={"FOO": "bar"},
    )


def test_create_with_providers(sandbox_svc, mock_client):
    """create() forwards providers list to client."""
    mock_client.sandboxes.create.return_value = {"name": "sb1"}

    sandbox_svc.create(name="sb1", providers=["prov-1", "prov-2"])

    mock_client.sandboxes.create.assert_called_once_with(
        name="sb1",
        image="",
        gpu=False,
        providers=["prov-1", "prov-2"],
        environment=None,
    )


def test_create_policy_not_ready_warning(sandbox_svc, mock_client):
    """create() warns when policy is not ready after polling."""
    import grpc

    mock_client.sandboxes.create.return_value = {"name": "sb1"}
    mock_client.sandboxes.wait_ready.return_value = {"phase": "ready"}
    # Policy never becomes available
    mock_client.policies.get.side_effect = grpc.RpcError()

    result = sandbox_svc.create(name="sb1", presets=["pypi"])

    assert result.get("preset_warning") == "Could not read initial policy, presets may fail"


def test_create_empty_name_uses_result_name(sandbox_svc, mock_client):
    """create() with name='' uses name from the create response."""
    mock_client.sandboxes.create.return_value = {"name": "auto-sb"}
    mock_client.sandboxes.wait_ready.return_value = {"phase": "ready"}
    mock_client.policies.get.return_value = {"policy": {"status": "loaded"}}

    result = sandbox_svc.create(name="", presets=["pypi"])

    # wait_ready should be called with the auto-generated name
    mock_client.sandboxes.wait_ready.assert_called_once_with("auto-sb", timeout_seconds=120.0)
    assert result["name"] == "auto-sb"


def test_exec_forwards_workdir_env_timeout(sandbox_svc, mock_client):
    """exec() forwards workdir, env, and timeout_seconds to client."""
    mock_client.sandboxes.get.return_value = {"id": "abc-123", "name": "sb1"}
    mock_client.sandboxes.exec.return_value = {"exit_code": 0}

    sandbox_svc.exec("sb1", ["ls"], workdir="/app", env={"KEY": "val"}, timeout_seconds=60)

    mock_client.sandboxes.exec.assert_called_once_with(
        "abc-123",
        ["ls"],
        workdir="/app",
        env={"KEY": "val"},
        timeout_seconds=60,
    )


def test_exec_resolves_name_to_id(sandbox_svc, mock_client):
    """exec() resolves sandbox name to ID via get()."""
    mock_client.sandboxes.get.return_value = {"id": "id-999", "name": "mysb"}
    mock_client.sandboxes.exec.return_value = {"exit_code": 0}

    sandbox_svc.exec("mysb", ["whoami"])

    mock_client.sandboxes.get.assert_called_once_with("mysb")
    assert mock_client.sandboxes.exec.call_args[0][0] == "id-999"


def test_get_logs_sources_forwarded(sandbox_svc, mock_client):
    """get_logs() forwards sources parameter to client."""
    mock_client.sandboxes.get.return_value = {"id": "abc-123", "name": "sb1"}
    mock_client.sandboxes.get_logs.return_value = []

    sandbox_svc.get_logs("sb1", sources=["stdout", "stderr"])

    mock_client.sandboxes.get_logs.assert_called_once_with(
        "abc-123",
        lines=200,
        since_ms=0,
        sources=["stdout", "stderr"],
        min_level="",
    )


def test_create_ssh_session_resolves_name(sandbox_svc, mock_client):
    """create_ssh_session() resolves sandbox name to ID."""
    mock_client.sandboxes.get.return_value = {"id": "id-555", "name": "mysb"}
    mock_client.sandboxes.create_ssh_session.return_value = {"token": "t"}

    sandbox_svc.create_ssh_session("mysb")

    mock_client.sandboxes.get.assert_called_once_with("mysb")
    mock_client.sandboxes.create_ssh_session.assert_called_once_with("id-555")


# ─── Metadata tests ─────────────────────────────────────────────────────────


def test_create_with_labels_stores_metadata(sandbox_svc_with_meta, mock_client, meta_store):
    """create() with labels and description stores metadata in DB."""
    mock_client.sandboxes.create.return_value = {"name": "sb1", "phase": "provisioning"}

    result = sandbox_svc_with_meta.create(
        name="sb1",
        image="base",
        gateway_name="gw1",
        description="test sandbox",
        labels={"team": "platform", "env": "dev"},
    )

    assert result["description"] == "test sandbox"
    assert result["labels"] == {"team": "platform", "env": "dev"}
    # Verify metadata is persisted
    stored = meta_store.get("gw1", "sb1")
    assert stored["description"] == "test sandbox"
    assert stored["labels"] == {"team": "platform", "env": "dev"}


def test_create_without_metadata_no_store_call(sandbox_svc_with_meta, mock_client, meta_store):
    """create() without labels/description does not create metadata row."""
    mock_client.sandboxes.create.return_value = {"name": "sb1"}

    sandbox_svc_with_meta.create(name="sb1", gateway_name="gw1")

    assert meta_store.get("gw1", "sb1") is None


def test_get_merges_metadata(sandbox_svc_with_meta, mock_client, meta_store):
    """get() merges stored metadata into gateway response."""
    meta_store.upsert("gw1", "sb1", description="my sb", labels={"app": "web"})
    mock_client.sandboxes.get.return_value = {"name": "sb1", "phase": "ready"}

    result = sandbox_svc_with_meta.get("sb1", gateway_name="gw1")

    assert result["name"] == "sb1"
    assert result["description"] == "my sb"
    assert result["labels"] == {"app": "web"}


def test_get_without_metadata_returns_defaults(sandbox_svc_with_meta, mock_client):
    """get() returns None/empty when no metadata exists."""
    mock_client.sandboxes.get.return_value = {"name": "sb1", "phase": "ready"}

    result = sandbox_svc_with_meta.get("sb1", gateway_name="gw1")

    assert result["description"] is None
    assert result["labels"] == {}


def test_list_merges_metadata(sandbox_svc_with_meta, mock_client, meta_store):
    """list() merges metadata for all sandboxes."""
    meta_store.upsert("gw1", "sb1", labels={"team": "a"})
    mock_client.sandboxes.list.return_value = [{"name": "sb1"}, {"name": "sb2"}]

    result = sandbox_svc_with_meta.list(gateway_name="gw1")

    assert result[0]["labels"] == {"team": "a"}
    assert result[1]["labels"] == {}


def test_list_filters_by_labels(sandbox_svc_with_meta, mock_client, meta_store):
    """list() with labels_filter returns only matching sandboxes."""
    meta_store.upsert("gw1", "sb1", labels={"team": "a"})
    meta_store.upsert("gw1", "sb2", labels={"team": "b"})
    mock_client.sandboxes.list.return_value = [{"name": "sb1"}, {"name": "sb2"}]

    result = sandbox_svc_with_meta.list(gateway_name="gw1", labels_filter={"team": "a"})

    assert len(result) == 1
    assert result[0]["name"] == "sb1"


def test_delete_cleans_up_metadata(sandbox_svc_with_meta, mock_client, meta_store):
    """delete() removes metadata row after successful gateway delete."""
    meta_store.upsert("gw1", "sb1", description="to delete", labels={"x": "y"})
    mock_client.sandboxes.delete.return_value = True

    sandbox_svc_with_meta.delete("sb1", gateway_name="gw1")

    assert meta_store.get("gw1", "sb1") is None


def test_delete_keeps_metadata_on_failure(sandbox_svc_with_meta, mock_client, meta_store):
    """delete() keeps metadata when gateway delete returns False."""
    meta_store.upsert("gw1", "sb1", description="keep me")
    mock_client.sandboxes.delete.return_value = False

    sandbox_svc_with_meta.delete("sb1", gateway_name="gw1")

    assert meta_store.get("gw1", "sb1") is not None


def test_update_metadata(sandbox_svc_with_meta, mock_client, meta_store):
    """update_metadata() creates/updates metadata and returns merged sandbox."""
    mock_client.sandboxes.get.return_value = {"name": "sb1", "phase": "ready"}

    result = sandbox_svc_with_meta.update_metadata(
        "gw1", "sb1", description="updated", labels={"env": "prod"}
    )

    assert result["description"] == "updated"
    assert result["labels"] == {"env": "prod"}
    assert result["name"] == "sb1"


def test_update_metadata_partial(sandbox_svc_with_meta, mock_client, meta_store):
    """update_metadata() with _UNSET leaves existing fields untouched."""
    from shoreguard.services.sandbox import _UNSET

    meta_store.upsert("gw1", "sb1", description="original", labels={"a": "1"})
    mock_client.sandboxes.get.return_value = {"name": "sb1", "phase": "ready"}

    result = sandbox_svc_with_meta.update_metadata(
        "gw1", "sb1", description="new desc", labels=_UNSET
    )

    assert result["description"] == "new desc"
    assert result["labels"] == {"a": "1"}


def test_update_metadata_no_store_raises(sandbox_svc, mock_client):
    """update_metadata() without meta store raises RuntimeError."""
    with pytest.raises(RuntimeError, match="Metadata store not configured"):
        sandbox_svc.update_metadata("gw1", "sb1", description="x")
