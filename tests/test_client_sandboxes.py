"""Unit tests for SandboxManager — FakeStub pattern, no live gRPC server."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from shoreguard.client._proto import datamodel_pb2, openshell_pb2
from shoreguard.client.sandboxes import SandboxManager, _sandbox_to_dict
from shoreguard.exceptions import SandboxError


class _FakeStub:
    """Minimal stub that captures requests and returns mock proto responses."""

    def __init__(self) -> None:
        self.request = None

    def ListSandboxes(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(
            sandboxes=[
                datamodel_pb2.Sandbox(id="abc", name="sb1", phase=2),
            ]
        )

    def GetSandbox(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(sandbox=datamodel_pb2.Sandbox(id="abc", name="sb1", phase=2))

    def CreateSandbox(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(sandbox=datamodel_pb2.Sandbox(id="new", name="new-sb", phase=1))

    def DeleteSandbox(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(deleted=True)

    def ExecSandbox(self, req, timeout=None):
        self.request = req
        yield openshell_pb2.ExecSandboxEvent(stdout=openshell_pb2.ExecSandboxStdout(data=b"hello"))
        yield openshell_pb2.ExecSandboxEvent(exit=openshell_pb2.ExecSandboxExit(exit_code=0))

    def CreateSshSession(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(
            sandbox_id="abc",
            token="tok-xyz",
            gateway_host="127.0.0.1",
            gateway_port=8080,
            gateway_scheme="https",
            connect_path="/connect",
            host_key_fingerprint="SHA256:x",
            expires_at_ms=9999,
        )

    def RevokeSshSession(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(revoked=True)

    def GetSandboxLogs(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(
            logs=[
                openshell_pb2.SandboxLogLine(
                    timestamp_ms=1000, level="info", message="started", source="sandbox"
                ),
            ]
        )


@pytest.fixture
def stub():
    return _FakeStub()


@pytest.fixture
def mgr(stub):
    m = object.__new__(SandboxManager)
    m._stub = stub
    m._timeout = 30.0
    return m


def test_list_sends_correct_limit_offset(mgr, stub):
    """list() sends limit/offset in request and returns converted dicts."""
    result = mgr.list(limit=50, offset=5)

    assert stub.request.limit == 50
    assert stub.request.offset == 5
    assert len(result) == 1
    assert result[0]["name"] == "sb1"
    assert result[0]["phase"] == "ready"


def test_get_sends_name(mgr, stub):
    """get() sends sandbox name and returns converted dict with id."""
    result = mgr.get("sb1")

    assert stub.request.name == "sb1"
    assert result["id"] == "abc"
    assert result["name"] == "sb1"


def test_create_sends_spec(mgr, stub):
    """create() builds spec with image and name, returns converted dict."""
    result = mgr.create(name="new-sb", image="base-image")

    assert stub.request.name == "new-sb"
    assert stub.request.spec.template.image == "base-image"
    assert result["id"] == "new"
    assert result["phase"] == "provisioning"


def test_delete_returns_bool(mgr, stub):
    """delete() sends name and returns bool from resp.deleted."""
    result = mgr.delete("sb1")

    assert stub.request.name == "sb1"
    assert result is True


def test_exec_parses_stdout(mgr, stub):
    """exec() aggregates stdout stream events into result dict."""
    result = mgr.exec("abc", ["echo", "hello"])

    assert stub.request.sandbox_id == "abc"
    assert stub.request.command == ["echo", "hello"]
    assert result["stdout"] == "hello"
    assert result["exit_code"] == 0


def test_create_ssh_session(mgr, stub):
    """create_ssh_session() sends sandbox_id and returns token dict."""
    result = mgr.create_ssh_session("abc")

    assert stub.request.sandbox_id == "abc"
    assert result["token"] == "tok-xyz"
    assert result["gateway_host"] == "127.0.0.1"


def test_revoke_ssh_session(mgr, stub):
    """revoke_ssh_session() sends token and returns bool."""
    result = mgr.revoke_ssh_session("tok-xyz")

    assert stub.request.token == "tok-xyz"
    assert result is True


def test_get_logs_sends_params(mgr, stub):
    """get_logs() sends sandbox_id, lines, since_ms and returns log list."""
    result = mgr.get_logs("abc", lines=50, since_ms=1000, min_level="info")

    assert stub.request.sandbox_id == "abc"
    assert stub.request.lines == 50
    assert stub.request.since_ms == 1000
    assert stub.request.min_level == "info"
    assert len(result) == 1
    assert result[0]["message"] == "started"


# ─── wait_ready ──────────────────────────────────────────────────────────────


def test_wait_ready_immediate(monkeypatch):
    """Sandbox already ready returns immediately."""
    monkeypatch.setattr("time.sleep", lambda _: None)

    class _ReadyStub(_FakeStub):
        def GetSandbox(self, req, timeout=None):
            return SimpleNamespace(sandbox=datamodel_pb2.Sandbox(id="abc", name="sb1", phase=2))

    s = _ReadyStub()
    m = object.__new__(SandboxManager)
    m._stub = s
    m._timeout = 30.0

    result = m.wait_ready("sb1")
    assert result["phase"] == "ready"


def test_wait_ready_error_phase(monkeypatch):
    """Error phase raises SandboxError."""
    monkeypatch.setattr("time.sleep", lambda _: None)

    class _ErrorStub(_FakeStub):
        def GetSandbox(self, req, timeout=None):
            return SimpleNamespace(sandbox=datamodel_pb2.Sandbox(id="abc", name="sb1", phase=3))

    s = _ErrorStub()
    m = object.__new__(SandboxManager)
    m._stub = s
    m._timeout = 30.0

    with pytest.raises(SandboxError, match="error phase"):
        m.wait_ready("sb1")


def test_wait_ready_timeout(monkeypatch):
    """Exceeds timeout raises TimeoutError."""
    monkeypatch.setattr("time.sleep", lambda _: None)
    # Make time.time always return values past the deadline
    counter = iter(range(0, 1000, 10))
    monkeypatch.setattr("time.time", lambda: next(counter))

    class _ProvisioningStub(_FakeStub):
        def GetSandbox(self, req, timeout=None):
            return SimpleNamespace(sandbox=datamodel_pb2.Sandbox(id="abc", name="sb1", phase=1))

    s = _ProvisioningStub()
    m = object.__new__(SandboxManager)
    m._stub = s
    m._timeout = 30.0

    with pytest.raises(TimeoutError, match="not ready"):
        m.wait_ready("sb1", timeout_seconds=5)


# ─── watch ───────────────────────────────────────────────────────────────────


def _make_watch_mgr(events):
    """Create a SandboxManager with a FakeStub that yields watch events."""

    class _WatchStub(_FakeStub):
        def WatchSandbox(self, req, **kw):
            yield from events

    s = _WatchStub()
    m = object.__new__(SandboxManager)
    m._stub = s
    m._timeout = 30.0
    return m


def test_watch_status_event():
    """Watch yields status dict for sandbox payload."""
    event = openshell_pb2.SandboxStreamEvent(
        sandbox=datamodel_pb2.Sandbox(id="abc", name="sb1", phase=2),
    )
    mgr = _make_watch_mgr([event])
    results = list(mgr.watch("abc"))

    assert len(results) == 1
    assert results[0]["type"] == "status"
    assert results[0]["data"]["name"] == "sb1"
    assert results[0]["data"]["phase"] == "ready"


def test_watch_log_event():
    """Watch yields log dict for log payload."""
    event = openshell_pb2.SandboxStreamEvent(
        log=openshell_pb2.SandboxLogLine(
            timestamp_ms=1000,
            level="info",
            message="hello",
            source="sandbox",
        ),
    )
    mgr = _make_watch_mgr([event])
    results = list(mgr.watch("abc"))

    assert results[0]["type"] == "log"
    assert results[0]["data"]["message"] == "hello"
    assert results[0]["data"]["source"] == "sandbox"


def test_watch_platform_event():
    """Watch yields event dict for platform event payload."""
    event = openshell_pb2.SandboxStreamEvent(
        event=openshell_pb2.PlatformEvent(
            timestamp_ms=2000,
            source="kubelet",
            type="Normal",
            reason="Pulled",
            message="Image pulled",
        ),
    )
    mgr = _make_watch_mgr([event])
    results = list(mgr.watch("abc"))

    assert results[0]["type"] == "event"
    assert results[0]["data"]["reason"] == "Pulled"
    assert results[0]["data"]["source"] == "kubelet"


def test_watch_draft_policy_update():
    """Watch yields draft_policy_update dict."""
    event = openshell_pb2.SandboxStreamEvent(
        draft_policy_update=openshell_pb2.DraftPolicyUpdate(
            draft_version=3,
            new_chunks=2,
            total_pending=5,
            summary="2 new rules",
        ),
    )
    mgr = _make_watch_mgr([event])
    results = list(mgr.watch("abc"))

    assert results[0]["type"] == "draft_policy_update"
    assert results[0]["data"]["draft_version"] == 3
    assert results[0]["data"]["new_chunks"] == 2
    assert results[0]["data"]["total_pending"] == 5


def test_watch_warning_event():
    """Watch yields warning dict."""
    event = openshell_pb2.SandboxStreamEvent(
        warning=openshell_pb2.SandboxStreamWarning(message="low disk space"),
    )
    mgr = _make_watch_mgr([event])
    results = list(mgr.watch("abc"))

    assert results[0]["type"] == "warning"
    assert results[0]["data"]["message"] == "low disk space"


# ─── _sandbox_to_dict comprehensive ─────────────────────────────────────────


@pytest.mark.parametrize(
    "phase_code,phase_name",
    [
        (0, "unspecified"),
        (1, "provisioning"),
        (2, "ready"),
        (3, "error"),
        (4, "deleting"),
        (5, "unknown"),
    ],
)
def test_sandbox_to_dict_all_phase_codes(phase_code, phase_name):
    """_sandbox_to_dict maps each phase code 0-5 to correct name."""
    sb = datamodel_pb2.Sandbox(
        id="id1",
        name="sb1",
        namespace="ns1",
        phase=phase_code,
        created_at_ms=12345,
        current_policy_version=7,
    )
    d = _sandbox_to_dict(sb)
    assert d["phase"] == phase_name
    assert d["phase_code"] == phase_code


def test_sandbox_to_dict_unknown_phase_code():
    """Unknown phase code (e.g. 99) returns 'unknown'."""
    sb = datamodel_pb2.Sandbox(id="id1", name="sb1", phase=99)
    d = _sandbox_to_dict(sb)
    assert d["phase"] == "unknown"
    assert d["phase_code"] == 99


def test_sandbox_to_dict_all_fields():
    """_sandbox_to_dict returns all expected fields with correct values."""
    sb = datamodel_pb2.Sandbox(
        id="id42",
        name="my-sb",
        namespace="default",
        phase=2,
        created_at_ms=9999,
        current_policy_version=3,
        spec=datamodel_pb2.SandboxSpec(
            template=datamodel_pb2.SandboxTemplate(image="ubuntu:22.04"),
            gpu=True,
        ),
    )
    d = _sandbox_to_dict(sb)
    assert d["id"] == "id42"
    assert d["name"] == "my-sb"
    assert d["namespace"] == "default"
    assert d["phase"] == "ready"
    assert d["phase_code"] == 2
    assert d["created_at_ms"] == 9999
    assert d["current_policy_version"] == 3
    assert d["image"] == "ubuntu:22.04"
    assert d["gpu"] is True


def test_sandbox_to_dict_empty_image_is_none():
    """When image is empty string, _sandbox_to_dict returns None."""
    sb = datamodel_pb2.Sandbox(
        id="id1",
        name="sb1",
        spec=datamodel_pb2.SandboxSpec(
            template=datamodel_pb2.SandboxTemplate(image=""),
        ),
    )
    d = _sandbox_to_dict(sb)
    assert d["image"] is None


def test_sandbox_to_dict_no_spec_gpu_false():
    """When spec is not set, gpu defaults to False."""
    sb = datamodel_pb2.Sandbox(id="id1", name="sb1")
    d = _sandbox_to_dict(sb)
    assert d["gpu"] is False


# ─── create() with full params ──────────────────────────────────────────────


def test_create_with_gpu_providers_env_policy(mgr, stub):
    """create() forwards gpu, providers, environment, and policy to spec."""
    result = mgr.create(
        name="full-sb",
        image="img:latest",
        gpu=True,
        providers=["openai", "anthropic"],
        environment={"KEY": "VAL", "FOO": "BAR"},
        policy={"network": {"outbound": {"allow_all": True}}},
    )
    req = stub.request
    assert req.name == "full-sb"
    assert req.spec.gpu is True
    assert req.spec.template.image == "img:latest"
    assert list(req.spec.providers) == ["openai", "anthropic"]
    assert dict(req.spec.environment) == {"KEY": "VAL", "FOO": "BAR"}
    # policy should be set (CopyFrom was called)
    assert req.spec.HasField("policy")
    assert result["id"] == "new"


def test_create_no_image_no_providers():
    """create() without image/providers leaves those fields empty."""

    class _CreateStub(_FakeStub):
        def CreateSandbox(self, req, timeout=None):
            self.request = req
            return SimpleNamespace(
                sandbox=datamodel_pb2.Sandbox(id="bare", name="bare-sb", phase=1)
            )

    s = _CreateStub()
    m = object.__new__(SandboxManager)
    m._stub = s
    m._timeout = 30.0

    m.create(name="bare-sb")
    assert s.request.spec.template.image == ""
    assert list(s.request.spec.providers) == []
    assert s.request.spec.gpu is False


# ─── exec() extended ────────────────────────────────────────────────────────


def test_exec_stderr_stream():
    """exec() aggregates stderr stream events."""

    class _StderrStub(_FakeStub):
        def ExecSandbox(self, req, timeout=None):
            self.request = req
            yield openshell_pb2.ExecSandboxEvent(
                stderr=openshell_pb2.ExecSandboxStderr(data=b"err1")
            )
            yield openshell_pb2.ExecSandboxEvent(
                stderr=openshell_pb2.ExecSandboxStderr(data=b"err2")
            )
            yield openshell_pb2.ExecSandboxEvent(exit=openshell_pb2.ExecSandboxExit(exit_code=1))

    s = _StderrStub()
    m = object.__new__(SandboxManager)
    m._stub = s
    m._timeout = 30.0

    result = m.exec("sb1", ["false"])
    assert result["stderr"] == "err1err2"
    assert result["stdout"] == ""
    assert result["exit_code"] == 1


def test_exec_workdir_env_timeout():
    """exec() forwards workdir, env, and timeout_seconds to request."""

    class _ExecStub(_FakeStub):
        def ExecSandbox(self, req, timeout=None):
            self.request = req
            self.grpc_timeout = timeout
            yield openshell_pb2.ExecSandboxEvent(exit=openshell_pb2.ExecSandboxExit(exit_code=0))

    s = _ExecStub()
    m = object.__new__(SandboxManager)
    m._stub = s
    m._timeout = 30.0

    m.exec("sb1", ["ls"], workdir="/tmp", env={"A": "B"}, timeout_seconds=60)
    assert s.request.workdir == "/tmp"
    assert dict(s.request.environment) == {"A": "B"}
    assert s.request.timeout_seconds == 60


def test_exec_grpc_timeout_uses_max_of_timeout_and_command_timeout():
    """grpc_timeout = max(_timeout, timeout_seconds + 10)."""

    class _ExecStub(_FakeStub):
        def ExecSandbox(self, req, timeout=None):
            self.request = req
            self.grpc_timeout = timeout
            yield openshell_pb2.ExecSandboxEvent(exit=openshell_pb2.ExecSandboxExit(exit_code=0))

    # Case 1: timeout_seconds + 10 > _timeout
    s = _ExecStub()
    m = object.__new__(SandboxManager)
    m._stub = s
    m._timeout = 30.0
    m.exec("sb1", ["sleep", "100"], timeout_seconds=100)
    assert s.grpc_timeout == 110  # 100 + 10 > 30

    # Case 2: _timeout > timeout_seconds + 10
    s2 = _ExecStub()
    m2 = object.__new__(SandboxManager)
    m2._stub = s2
    m2._timeout = 200.0
    m2.exec("sb1", ["ls"], timeout_seconds=5)
    assert s2.grpc_timeout == 200.0  # 200 > 5 + 10


def test_exec_empty_stream():
    """exec() handles empty stream (no events) gracefully."""

    class _EmptyStub(_FakeStub):
        def ExecSandbox(self, req, timeout=None):
            self.request = req
            return iter([])  # empty stream

    s = _EmptyStub()
    m = object.__new__(SandboxManager)
    m._stub = s
    m._timeout = 30.0

    result = m.exec("sb1", ["noop"])
    assert result["exit_code"] is None
    assert result["stdout"] == ""
    assert result["stderr"] == ""


# ─── get_logs() extended ────────────────────────────────────────────────────


def test_get_logs_all_fields():
    """get_logs() returns all log fields including target, fields, level, source."""

    class _LogsStub(_FakeStub):
        def GetSandboxLogs(self, req, timeout=None):
            self.request = req
            return SimpleNamespace(
                logs=[
                    openshell_pb2.SandboxLogLine(
                        timestamp_ms=5000,
                        level="warn",
                        message="disk full",
                        source="agent",
                        target="sandbox.fs",
                        fields={"path": "/data", "usage": "99%"},
                    ),
                ]
            )

    s = _LogsStub()
    m = object.__new__(SandboxManager)
    m._stub = s
    m._timeout = 30.0

    result = m.get_logs("sb1")
    assert len(result) == 1
    log = result[0]
    assert log["timestamp_ms"] == 5000
    assert log["level"] == "warn"
    assert log["message"] == "disk full"
    assert log["source"] == "agent"
    assert log["target"] == "sandbox.fs"
    assert log["fields"] == {"path": "/data", "usage": "99%"}


def test_get_logs_sources_forwarded():
    """get_logs() forwards sources list to request."""

    class _LogsStub(_FakeStub):
        def GetSandboxLogs(self, req, timeout=None):
            self.request = req
            return SimpleNamespace(logs=[])

    s = _LogsStub()
    m = object.__new__(SandboxManager)
    m._stub = s
    m._timeout = 30.0

    m.get_logs("sb1", sources=["agent", "runtime"])
    assert list(s.request.sources) == ["agent", "runtime"]


def test_get_logs_no_sources_sends_empty():
    """get_logs() without sources sends empty list (not None)."""

    class _LogsStub(_FakeStub):
        def GetSandboxLogs(self, req, timeout=None):
            self.request = req
            return SimpleNamespace(logs=[])

    s = _LogsStub()
    m = object.__new__(SandboxManager)
    m._stub = s
    m._timeout = 30.0

    m.get_logs("sb1")
    assert list(s.request.sources) == []


# ─── watch() request params ─────────────────────────────────────────────────


def test_watch_request_params():
    """watch() forwards all request params correctly."""

    class _WatchStub(_FakeStub):
        def WatchSandbox(self, req, **kw):
            self.request = req
            return iter([])

    s = _WatchStub()
    m = object.__new__(SandboxManager)
    m._stub = s
    m._timeout = 30.0

    list(
        m.watch(
            "sb99",
            follow_status=False,
            follow_logs=False,
            follow_events=False,
            log_tail_lines=200,
        )
    )

    assert s.request.id == "sb99"
    assert s.request.follow_status is False
    assert s.request.follow_logs is False
    assert s.request.follow_events is False
    assert s.request.log_tail_lines == 200


def test_watch_default_params():
    """watch() sends default param values when not overridden."""

    class _WatchStub(_FakeStub):
        def WatchSandbox(self, req, **kw):
            self.request = req
            return iter([])

    s = _WatchStub()
    m = object.__new__(SandboxManager)
    m._stub = s
    m._timeout = 30.0

    list(m.watch("sb1"))

    assert s.request.follow_status is True
    assert s.request.follow_logs is True
    assert s.request.follow_events is True
    assert s.request.log_tail_lines == 50


# ─── create_ssh_session() all fields ────────────────────────────────────────


def test_create_ssh_session_all_fields(mgr, stub):
    """create_ssh_session() returns all fields from response."""
    result = mgr.create_ssh_session("abc")

    assert result["sandbox_id"] == "abc"
    assert result["token"] == "tok-xyz"
    assert result["gateway_host"] == "127.0.0.1"
    assert result["gateway_port"] == 8080
    assert result["gateway_scheme"] == "https"
    assert result["connect_path"] == "/connect"
    assert result["host_key_fingerprint"] == "SHA256:x"
    assert result["expires_at_ms"] == 9999


# ─── wait_ready transitions ─────────────────────────────────────────────────


def test_wait_ready_provisioning_then_ready(monkeypatch):
    """wait_ready polls through provisioning then returns when ready."""
    monkeypatch.setattr("time.sleep", lambda _: None)
    # time.time returns values well within deadline
    times = iter([0, 1, 2, 3, 4, 5])
    monkeypatch.setattr("time.time", lambda: next(times))

    call_count = 0

    class _TransitionStub(_FakeStub):
        def GetSandbox(self, req, timeout=None):
            nonlocal call_count
            call_count += 1
            # First 2 calls: provisioning; then ready
            phase = 1 if call_count <= 2 else 2
            return SimpleNamespace(sandbox=datamodel_pb2.Sandbox(id="abc", name="sb1", phase=phase))

    s = _TransitionStub()
    m = object.__new__(SandboxManager)
    m._stub = s
    m._timeout = 30.0

    result = m.wait_ready("sb1", timeout_seconds=300)
    assert result["phase"] == "ready"
    assert call_count == 3  # 2 provisioning + 1 ready


def test_wait_ready_checks_phase_exactly():
    """wait_ready only accepts phase=='ready', not other phases like 'deleting'."""
    import time as _time

    class _DeletingStub(_FakeStub):
        def GetSandbox(self, req, timeout=None):
            return SimpleNamespace(sandbox=datamodel_pb2.Sandbox(id="abc", name="sb1", phase=4))

    s = _DeletingStub()
    m = object.__new__(SandboxManager)
    m._stub = s
    m._timeout = 30.0

    # Use a tight deadline so it times out fast
    original_time = _time.time
    times = iter([0, 1, 100])  # 3rd call past deadline

    with pytest.raises(TimeoutError):
        import time

        time.sleep = lambda _: None
        time.time = lambda: next(times)
        try:
            m.wait_ready("sb1", timeout_seconds=5)
        finally:
            time.time = original_time
            time.sleep = _time.sleep
