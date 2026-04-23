"""Unit tests for SandboxManager — FakeStub pattern, no live gRPC server."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from shoreguard.client._proto import openshell_pb2
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
                openshell_pb2.Sandbox(id="abc", name="sb1", phase=2),  # type: ignore[arg-type]
            ]
        )

    def GetSandbox(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(sandbox=openshell_pb2.Sandbox(id="abc", name="sb1", phase=2))  # type: ignore[arg-type]

    def CreateSandbox(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(sandbox=openshell_pb2.Sandbox(id="new", name="new-sb", phase=1))  # type: ignore[arg-type]

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
                    timestamp_ms=1000,
                    level="info",
                    message="started",
                    source="sandbox",
                    target="openshell_sandbox",
                    fields={"k": "v"},
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


def test_exec_tty_default_false(mgr, stub):
    """exec() without tty kwarg sends tty=False (the proto default)."""
    mgr.exec("abc", ["bash"])
    assert stub.request.tty is False


def test_exec_tty_true_forwarded(mgr, stub):
    """exec(tty=True) forwards the flag into ExecSandboxRequest."""
    mgr.exec("abc", ["python"], tty=True)
    assert stub.request.tty is True


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
    assert result[0]["target"] == "openshell_sandbox"
    assert result[0]["fields"] == {"k": "v"}


def test_watch_stream_passes_target_and_fields():
    """watch() forwards target and fields from SandboxLogLine to the consumer."""
    event = openshell_pb2.SandboxStreamEvent(
        log=openshell_pb2.SandboxLogLine(
            timestamp_ms=2000,
            level="OCSF",
            target="ocsf",
            message=(
                "NET:OPEN [INFO] ALLOWED /usr/bin/curl(58) -> api.github.com:443 "
                "[policy:github_api engine:opa]"
            ),
            source="sandbox",
            fields={"dst_host": "api.github.com"},
        )
    )
    mgr = _make_watch_mgr([event])
    events = list(mgr.watch("abc"))

    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "log"
    assert ev["data"]["target"] == "ocsf"
    assert ev["data"]["fields"] == {"dst_host": "api.github.com"}
    assert ev["data"]["level"] == "OCSF"


# ─── wait_ready ──────────────────────────────────────────────────────────────


def test_wait_ready_immediate(monkeypatch):
    """Sandbox already ready returns immediately."""
    monkeypatch.setattr("time.sleep", lambda _: None)

    class _ReadyStub(_FakeStub):
        def GetSandbox(self, req, timeout=None):
            return SimpleNamespace(sandbox=openshell_pb2.Sandbox(id="abc", name="sb1", phase=2))  # type: ignore[arg-type]

    s = _ReadyStub()
    m = object.__new__(SandboxManager)
    m._stub = s  # type: ignore[assignment]
    m._timeout = 30.0

    result = m.wait_ready("sb1")
    assert result["phase"] == "ready"


def test_wait_ready_error_phase(monkeypatch):
    """Error phase raises SandboxError."""
    monkeypatch.setattr("time.sleep", lambda _: None)

    class _ErrorStub(_FakeStub):
        def GetSandbox(self, req, timeout=None):
            return SimpleNamespace(sandbox=openshell_pb2.Sandbox(id="abc", name="sb1", phase=3))  # type: ignore[arg-type]

    s = _ErrorStub()
    m = object.__new__(SandboxManager)
    m._stub = s  # type: ignore[assignment]
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
            return SimpleNamespace(sandbox=openshell_pb2.Sandbox(id="abc", name="sb1", phase=1))  # type: ignore[arg-type]

    s = _ProvisioningStub()
    m = object.__new__(SandboxManager)
    m._stub = s  # type: ignore[assignment]
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
    m._stub = s  # type: ignore[assignment]
    m._timeout = 30.0
    return m


def test_watch_status_event():
    """Watch yields status dict for sandbox payload."""
    event = openshell_pb2.SandboxStreamEvent(
        sandbox=openshell_pb2.Sandbox(id="abc", name="sb1", phase=2),  # type: ignore[arg-type]
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
    sb = openshell_pb2.Sandbox(
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
    sb = openshell_pb2.Sandbox(id="id1", name="sb1", phase=99)  # type: ignore[arg-type]
    d = _sandbox_to_dict(sb)
    assert d["phase"] == "unknown"
    assert d["phase_code"] == 99


def test_sandbox_to_dict_all_fields():
    """_sandbox_to_dict returns all expected fields with correct values."""
    sb = openshell_pb2.Sandbox(
        id="id42",
        name="my-sb",
        namespace="default",
        phase=2,  # type: ignore[arg-type]
        created_at_ms=9999,
        current_policy_version=3,
        spec=openshell_pb2.SandboxSpec(
            template=openshell_pb2.SandboxTemplate(image="ubuntu:22.04"),
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
    sb = openshell_pb2.Sandbox(
        id="id1",
        name="sb1",
        spec=openshell_pb2.SandboxSpec(
            template=openshell_pb2.SandboxTemplate(image=""),
        ),
    )
    d = _sandbox_to_dict(sb)
    assert d["image"] is None


def test_sandbox_to_dict_no_spec_gpu_false():
    """When spec is not set, gpu defaults to False."""
    sb = openshell_pb2.Sandbox(id="id1", name="sb1")
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
                sandbox=openshell_pb2.Sandbox(id="bare", name="bare-sb", phase=1)  # type: ignore[arg-type]
            )

    s = _CreateStub()
    m = object.__new__(SandboxManager)
    m._stub = s  # type: ignore[assignment]
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
    m._stub = s  # type: ignore[assignment]
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
    m._stub = s  # type: ignore[assignment]
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
    m._stub = s  # type: ignore[assignment]
    m._timeout = 30.0
    m.exec("sb1", ["sleep", "100"], timeout_seconds=100)
    assert s.grpc_timeout == 110  # 100 + 10 > 30

    # Case 2: _timeout > timeout_seconds + 10
    s2 = _ExecStub()
    m2 = object.__new__(SandboxManager)
    m2._stub = s2  # type: ignore[assignment]
    m2._timeout = 200.0
    m2.exec("sb1", ["ls"], timeout_seconds=5)
    assert s2.grpc_timeout == 200.0  # 200 > 5 + 10


def test_exec_empty_stream():
    """exec() handles empty stream (no events) gracefully."""

    class _EmptyStub(_FakeStub):
        def ExecSandbox(self, req, timeout=None):  # type: ignore[override]
            self.request = req
            return iter([])  # empty stream

    s = _EmptyStub()
    m = object.__new__(SandboxManager)
    m._stub = s  # type: ignore[assignment]
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
    m._stub = s  # type: ignore[assignment]
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
    m._stub = s  # type: ignore[assignment]
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
    m._stub = s  # type: ignore[assignment]
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
    m._stub = s  # type: ignore[assignment]
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
    m._stub = s  # type: ignore[assignment]
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


# ─── create_ssh_session() charset contract (upstream #876 parity) ──────────


def _ssh_response(**overrides: Any) -> SimpleNamespace:
    """Return a clean CreateSshSessionResponse with targeted overrides."""
    baseline = {
        "sandbox_id": "abc",
        "token": "tok-xyz",
        "gateway_host": "127.0.0.1",
        "gateway_port": 8080,
        "gateway_scheme": "https",
        "connect_path": "/connect",
        "host_key_fingerprint": "SHA256:x",
        "expires_at_ms": 9999,
    }
    baseline.update(overrides)
    return SimpleNamespace(**baseline)


def _mgr_with_ssh_response(resp: SimpleNamespace) -> SandboxManager:
    stub = SimpleNamespace(CreateSshSession=lambda req, timeout=None: resp)
    m = object.__new__(SandboxManager)
    m._stub = stub  # type: ignore[assignment]
    m._timeout = 30.0
    return m


@pytest.mark.parametrize(
    ("field", "value"),
    [
        # Command-injection vectors on sandbox_id.
        ("sandbox_id", "abc; rm -rf /"),
        ("sandbox_id", "abc`whoami`"),
        ("sandbox_id", ""),
        ("sandbox_id", "x" * 129),
        # Token with shell metacharacters.
        ("token", "tok xyz"),
        ("token", "tok;rm"),
        ("token", "tok`whoami`"),
        ("token", ""),
        ("token", "a" * 4097),
        # gateway_host with @ (would reshape URL userinfo).
        ("gateway_host", "evil@127.0.0.1"),
        ("gateway_host", "127.0.0.1 && curl evil.com"),
        ("gateway_host", ""),
        # Invalid ports.
        ("gateway_port", 0),
        ("gateway_port", 65536),
        ("gateway_port", -1),
        # Unexpected schemes.
        ("gateway_scheme", "ssh"),
        ("gateway_scheme", "file"),
        ("gateway_scheme", ""),
        # Query / fragment / whitespace in connect_path.
        ("connect_path", "/connect?shell=/bin/bash"),
        ("connect_path", "/connect#frag"),
        ("connect_path", "/connect space"),
        ("connect_path", "connect"),  # missing leading slash
        ("connect_path", "/connect`whoami`"),
        # Fingerprint with shell chars.
        ("host_key_fingerprint", "SHA256:x;echo pwned"),
    ],
)
def test_create_ssh_session_rejects_charset_violations(field: str, value: Any) -> None:
    mgr = _mgr_with_ssh_response(_ssh_response(**{field: value}))
    with pytest.raises(SandboxError, match=r"ssh session response violates"):
        mgr.create_ssh_session("abc")


def test_create_ssh_session_accepts_empty_host_key_fingerprint() -> None:
    """An absent fingerprint is valid (opt-in field per upstream)."""
    mgr = _mgr_with_ssh_response(_ssh_response(host_key_fingerprint=""))
    result = mgr.create_ssh_session("abc")
    assert result["host_key_fingerprint"] == ""


def test_create_ssh_session_accepts_bracketed_ipv6_host() -> None:
    """Bracketed IPv6 is RFC-3986-valid host syntax."""
    mgr = _mgr_with_ssh_response(_ssh_response(gateway_host="[::1]"))
    result = mgr.create_ssh_session("abc")
    assert result["gateway_host"] == "[::1]"


def test_create_ssh_session_accepts_percent_encoded_connect_path() -> None:
    """RFC-3986 %HH escapes are permitted in connect_path."""
    mgr = _mgr_with_ssh_response(_ssh_response(connect_path="/connect%20foo/bar"))
    result = mgr.create_ssh_session("abc")
    assert result["connect_path"] == "/connect%20foo/bar"


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
            return SimpleNamespace(sandbox=openshell_pb2.Sandbox(id="abc", name="sb1", phase=phase))  # type: ignore[arg-type]

    s = _TransitionStub()
    m = object.__new__(SandboxManager)
    m._stub = s  # type: ignore[assignment]
    m._timeout = 30.0

    result = m.wait_ready("sb1", timeout_seconds=300)
    assert result["phase"] == "ready"
    assert call_count == 3  # 2 provisioning + 1 ready


def test_wait_ready_checks_phase_exactly():
    """wait_ready only accepts phase=='ready', not other phases like 'deleting'."""
    import time as _time

    class _DeletingStub(_FakeStub):
        def GetSandbox(self, req, timeout=None):
            return SimpleNamespace(sandbox=openshell_pb2.Sandbox(id="abc", name="sb1", phase=4))  # type: ignore[arg-type]

    s = _DeletingStub()
    m = object.__new__(SandboxManager)
    m._stub = s  # type: ignore[assignment]
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


# ─── Mutation-killing tests ──────────────────────────────────────────────────


class TestSandboxToDictMutations:
    """Kill mutations in _sandbox_to_dict field mappings."""

    def test_each_field_is_from_correct_proto_field(self):
        """Each dict key maps to the correct proto field, not swapped."""
        sb = openshell_pb2.Sandbox(
            id="ID",
            name="NAME",
            namespace="NS",
            phase=2,  # type: ignore[arg-type]
            created_at_ms=42,
            current_policy_version=7,
            spec=openshell_pb2.SandboxSpec(
                template=openshell_pb2.SandboxTemplate(image="IMG"),
                gpu=True,
            ),
        )
        d = _sandbox_to_dict(sb)
        assert d["id"] == "ID"
        assert d["name"] == "NAME"
        assert d["namespace"] == "NS"
        assert d["phase"] == "ready"
        assert d["phase_code"] == 2
        assert d["created_at_ms"] == 42
        assert d["current_policy_version"] == 7
        assert d["image"] == "IMG"
        assert d["gpu"] is True

    def test_gpu_false_when_no_spec(self):
        """When sb.HasField('spec') is false, gpu should be False."""
        sb = openshell_pb2.Sandbox(id="x", name="y")
        d = _sandbox_to_dict(sb)
        assert d["gpu"] is False

    def test_image_none_when_empty(self):
        sb = openshell_pb2.Sandbox(
            id="x",
            name="y",
            spec=openshell_pb2.SandboxSpec(template=openshell_pb2.SandboxTemplate(image="")),
        )
        d = _sandbox_to_dict(sb)
        assert d["image"] is None

    def test_image_value_when_set(self):
        sb = openshell_pb2.Sandbox(
            id="x",
            name="y",
            spec=openshell_pb2.SandboxSpec(template=openshell_pb2.SandboxTemplate(image="ubuntu")),
        )
        d = _sandbox_to_dict(sb)
        assert d["image"] == "ubuntu"


class TestListMutations:
    """Kill mutations in list() default params and return structure."""

    def test_list_default_params(self):
        class _Stub(_FakeStub):
            def ListSandboxes(self, req, timeout=None):
                self.request = req
                return SimpleNamespace(sandboxes=[])

        s = _Stub()
        m = object.__new__(SandboxManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        m.list()
        assert s.request.limit == 100
        assert s.request.offset == 0

    def test_list_empty_returns_empty(self):
        class _Stub(_FakeStub):
            def ListSandboxes(self, req, timeout=None):
                return SimpleNamespace(sandboxes=[])

        s = _Stub()
        m = object.__new__(SandboxManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        assert m.list() == []

    def test_list_uses_timeout(self):
        class _Stub(_FakeStub):
            def ListSandboxes(self, req, timeout=None):
                self.timeout = timeout
                return SimpleNamespace(sandboxes=[])

        s = _Stub()
        m = object.__new__(SandboxManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 42.0
        m.list()
        assert s.timeout == 42.0


class TestCreateMutations:
    """Kill mutations in create() spec building."""

    def test_create_no_policy_no_field(self):
        class _Stub(_FakeStub):
            def CreateSandbox(self, req, timeout=None):
                self.request = req
                return SimpleNamespace(sandbox=openshell_pb2.Sandbox(id="x", name="y", phase=1))  # type: ignore[arg-type]

        s = _Stub()
        m = object.__new__(SandboxManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        m.create(name="y")
        assert not s.request.spec.HasField("policy")

    def test_create_no_environment_empty(self):
        class _Stub(_FakeStub):
            def CreateSandbox(self, req, timeout=None):
                self.request = req
                return SimpleNamespace(sandbox=openshell_pb2.Sandbox(id="x", name="y", phase=1))  # type: ignore[arg-type]

        s = _Stub()
        m = object.__new__(SandboxManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        m.create(name="y")
        assert dict(s.request.spec.environment) == {}

    def test_create_gpu_false_default(self):
        class _Stub(_FakeStub):
            def CreateSandbox(self, req, timeout=None):
                self.request = req
                return SimpleNamespace(sandbox=openshell_pb2.Sandbox(id="x", name="y", phase=1))  # type: ignore[arg-type]

        s = _Stub()
        m = object.__new__(SandboxManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        m.create(name="y")
        assert s.request.spec.gpu is False

    def test_create_gpu_true(self):
        class _Stub(_FakeStub):
            def CreateSandbox(self, req, timeout=None):
                self.request = req
                return SimpleNamespace(sandbox=openshell_pb2.Sandbox(id="x", name="y", phase=1))  # type: ignore[arg-type]

        s = _Stub()
        m = object.__new__(SandboxManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        m.create(name="y", gpu=True)
        assert s.request.spec.gpu is True


class TestDeleteMutations:
    """Kill mutations in delete() bool conversion."""

    def test_delete_false(self):
        class _Stub(_FakeStub):
            def DeleteSandbox(self, req, timeout=None):
                self.request = req
                return SimpleNamespace(deleted=False)

        s = _Stub()
        m = object.__new__(SandboxManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        assert m.delete("sb1") is False

    def test_delete_zero_is_false(self):
        class _Stub(_FakeStub):
            def DeleteSandbox(self, req, timeout=None):
                return SimpleNamespace(deleted=0)

        s = _Stub()
        m = object.__new__(SandboxManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        assert m.delete("sb1") is False

    def test_delete_uses_timeout(self):
        class _Stub(_FakeStub):
            def DeleteSandbox(self, req, timeout=None):
                self.timeout = timeout
                return SimpleNamespace(deleted=True)

        s = _Stub()
        m = object.__new__(SandboxManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 55.0
        m.delete("sb1")
        assert s.timeout == 55.0


class TestExecMutations:
    """Kill mutations in exec() stream parsing and timeout calculation."""

    def test_exec_no_exit_event(self):
        """When no exit event, exit_code should be None."""

        class _Stub(_FakeStub):
            def ExecSandbox(self, req, timeout=None):
                yield openshell_pb2.ExecSandboxEvent(
                    stdout=openshell_pb2.ExecSandboxStdout(data=b"data")
                )

        s = _Stub()
        m = object.__new__(SandboxManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        result = m.exec("sb1", ["cmd"])
        assert result["exit_code"] is None
        assert result["stdout"] == "data"

    def test_exec_multiple_stdout_concatenated(self):
        class _Stub(_FakeStub):
            def ExecSandbox(self, req, timeout=None):
                yield openshell_pb2.ExecSandboxEvent(
                    stdout=openshell_pb2.ExecSandboxStdout(data=b"a")
                )
                yield openshell_pb2.ExecSandboxEvent(
                    stdout=openshell_pb2.ExecSandboxStdout(data=b"b")
                )
                yield openshell_pb2.ExecSandboxEvent(
                    exit=openshell_pb2.ExecSandboxExit(exit_code=0)
                )

        s = _Stub()
        m = object.__new__(SandboxManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        result = m.exec("sb1", ["cmd"])
        assert result["stdout"] == "ab"

    def test_exec_default_timeout_0_uses_600(self):
        """timeout_seconds=0 -> grpc_timeout = max(_timeout, 600+10)."""

        class _Stub(_FakeStub):
            def ExecSandbox(self, req, timeout=None):  # type: ignore[override]
                self.grpc_timeout = timeout
                return iter([])

        s = _Stub()
        m = object.__new__(SandboxManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        m.exec("sb1", ["cmd"])
        assert s.grpc_timeout == 610  # max(30, 600+10)

    def test_exec_env_none_becomes_empty(self):
        class _Stub(_FakeStub):
            def ExecSandbox(self, req, timeout=None):  # type: ignore[override]
                self.request = req
                return iter([])

        s = _Stub()
        m = object.__new__(SandboxManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        m.exec("sb1", ["cmd"])
        assert dict(s.request.environment) == {}


class TestSshSessionMutations:
    """Kill mutations in create_ssh_session / revoke_ssh_session."""

    def test_ssh_session_all_fields_exact(self, mgr, stub):
        result = mgr.create_ssh_session("abc")
        assert set(result.keys()) == {
            "sandbox_id",
            "token",
            "gateway_host",
            "gateway_port",
            "gateway_scheme",
            "connect_path",
            "host_key_fingerprint",
            "expires_at_ms",
        }

    def test_revoke_false(self):
        class _Stub(_FakeStub):
            def RevokeSshSession(self, req, timeout=None):
                return SimpleNamespace(revoked=False)

        s = _Stub()
        m = object.__new__(SandboxManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        assert m.revoke_ssh_session("tok") is False


class TestGetLogsMutations:
    """Kill mutations in get_logs field extraction."""

    def test_get_logs_defaults(self):
        class _Stub(_FakeStub):
            def GetSandboxLogs(self, req, timeout=None):
                self.request = req
                return SimpleNamespace(logs=[])

        s = _Stub()
        m = object.__new__(SandboxManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        m.get_logs("sb1")
        assert s.request.lines == 200
        assert s.request.since_ms == 0
        assert s.request.min_level == ""

    def test_get_logs_multiple_entries(self):
        class _Stub(_FakeStub):
            def GetSandboxLogs(self, req, timeout=None):
                return SimpleNamespace(
                    logs=[
                        openshell_pb2.SandboxLogLine(
                            timestamp_ms=1, level="info", message="a", source="s1", target="t1"
                        ),
                        openshell_pb2.SandboxLogLine(
                            timestamp_ms=2, level="error", message="b", source="s2", target="t2"
                        ),
                    ]
                )

        s = _Stub()
        m = object.__new__(SandboxManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        result = m.get_logs("sb1")
        assert len(result) == 2
        assert result[0]["timestamp_ms"] == 1
        assert result[0]["level"] == "info"
        assert result[0]["message"] == "a"
        assert result[0]["source"] == "s1"
        assert result[0]["target"] == "t1"
        assert result[1]["timestamp_ms"] == 2
        assert result[1]["level"] == "error"
        assert result[1]["message"] == "b"


class TestWatchMutations:
    """Kill mutations in watch() event type handling."""

    def test_watch_log_all_fields(self):
        event = openshell_pb2.SandboxStreamEvent(
            log=openshell_pb2.SandboxLogLine(
                timestamp_ms=999, level="warn", message="msg", source="src"
            ),
        )
        mgr = _make_watch_mgr([event])
        results = list(mgr.watch("sb1"))
        assert results[0]["data"]["timestamp_ms"] == 999
        assert results[0]["data"]["level"] == "warn"
        assert results[0]["data"]["message"] == "msg"
        assert results[0]["data"]["source"] == "src"

    def test_watch_event_all_fields(self):
        event = openshell_pb2.SandboxStreamEvent(
            event=openshell_pb2.PlatformEvent(
                timestamp_ms=100, source="s", type="t", reason="r", message="m"
            ),
        )
        mgr = _make_watch_mgr([event])
        results = list(mgr.watch("sb1"))
        assert results[0]["data"] == {
            "timestamp_ms": 100,
            "source": "s",
            "type": "t",
            "reason": "r",
            "message": "m",
        }

    def test_watch_draft_update_all_fields(self):
        event = openshell_pb2.SandboxStreamEvent(
            draft_policy_update=openshell_pb2.DraftPolicyUpdate(
                draft_version=1, new_chunks=2, total_pending=3, summary="s"
            ),
        )
        mgr = _make_watch_mgr([event])
        results = list(mgr.watch("sb1"))
        assert results[0]["data"] == {
            "draft_version": 1,
            "new_chunks": 2,
            "total_pending": 3,
            "summary": "s",
        }

    def test_watch_warning_exact(self):
        event = openshell_pb2.SandboxStreamEvent(
            warning=openshell_pb2.SandboxStreamWarning(message="w"),
        )
        mgr = _make_watch_mgr([event])
        results = list(mgr.watch("sb1"))
        assert results[0] == {"type": "warning", "data": {"message": "w"}}

    def test_watch_multiple_events_order(self):
        events = [
            openshell_pb2.SandboxStreamEvent(
                sandbox=openshell_pb2.Sandbox(id="a", name="s", phase=1)  # type: ignore[arg-type]
            ),
            openshell_pb2.SandboxStreamEvent(
                log=openshell_pb2.SandboxLogLine(
                    timestamp_ms=1, level="info", message="m", source="s"
                )
            ),
        ]
        mgr = _make_watch_mgr(events)
        results = list(mgr.watch("sb1"))
        assert results[0]["type"] == "status"
        assert results[1]["type"] == "log"

    def test_watch_empty_stream(self):
        mgr = _make_watch_mgr([])
        results = list(mgr.watch("sb1"))
        assert results == []


class TestWaitReadyMutations:
    """Kill mutations in wait_ready conditions."""

    def test_wait_ready_error_message_contains_name(self, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda _: None)

        class _Stub(_FakeStub):
            def GetSandbox(self, req, timeout=None):
                return SimpleNamespace(sandbox=openshell_pb2.Sandbox(id="a", name="my-sb", phase=3))  # type: ignore[arg-type]

        s = _Stub()
        m = object.__new__(SandboxManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        with pytest.raises(SandboxError, match="my-sb"):
            m.wait_ready("my-sb")

    def test_wait_ready_timeout_message_contains_seconds(self, monkeypatch):
        import itertools

        monkeypatch.setattr("time.sleep", lambda _: None)
        # Prometheus label initialization in this worker consumes one or
        # more time.time() calls depending on xdist-worker ordering;
        # chain with an infinite 999 tail so exhaustion cannot happen.
        times = itertools.chain([0, 1], itertools.repeat(999))
        monkeypatch.setattr("time.time", lambda: next(times))

        class _Stub(_FakeStub):
            def GetSandbox(self, req, timeout=None):
                return SimpleNamespace(sandbox=openshell_pb2.Sandbox(id="a", name="sb", phase=1))  # type: ignore[arg-type]

        s = _Stub()
        m = object.__new__(SandboxManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        with pytest.raises(TimeoutError, match="10"):
            m.wait_ready("sb", timeout_seconds=10)
