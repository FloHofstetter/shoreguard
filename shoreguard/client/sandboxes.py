"""gRPC wrapper for OpenShell's sandbox lifecycle and exec RPCs.

Covers create / list / get / delete plus ``ExecSandbox`` — both
regular and TTY variants — and the SSH session open / revoke
pair. Sandbox metadata (labels, description) kept on the
ShoreGuard side is not handled here; callers merge that in at
the service layer.

``ExecSandbox`` is also the delegation target for the post-create
boot hooks flow: the hook service reuses this manager to run
warm-up commands inside a sandbox once it has been created.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
from typing import Any

from shoreguard.exceptions import SandboxError

from ._converters import _dict_to_policy
from ._proto import openshell_pb2, openshell_pb2_grpc, sandbox_pb2
from ._resilience import DEFAULT_POLICY, RetryPolicy, call_with_retry, stream_with_retry
from .policies import _policy_to_dict

logger = logging.getLogger(__name__)


def _lazy_metrics() -> tuple[Any, Any]:  # pragma: no cover - trivial
    """Lazy handle to the metrics helpers to avoid an import cycle.

    Returns:
        tuple[Any, Any]: ``(record_grpc_attempt, record_grpc_duration)`` or
            ``(None, None)`` if the metrics module cannot be imported.
    """
    try:
        from shoreguard.api.metrics import record_grpc_attempt, record_grpc_duration

        return record_grpc_attempt, record_grpc_duration
    except Exception:  # noqa: BLE001
        return None, None


PHASE_NAMES = {
    0: "unspecified",
    1: "provisioning",
    2: "ready",
    3: "error",
    4: "deleting",
    5: "unknown",
}


# ---------------------------------------------------------------------------
# CreateSshSessionResponse charset contract (upstream parity, PR #876)
# ---------------------------------------------------------------------------
#
# Each regex mirrors the charset documented on the corresponding
# ``CreateSshSessionResponse`` field in ``openshell.proto`` after
# `NVIDIA/OpenShell#876 <https://github.com/NVIDIA/OpenShell/pull/876>`_.
# The gateway holds the contract; ShoreGuard re-checks as
# defence-in-depth because the fields are interpolated into an SSH
# ``ProxyCommand`` that OpenSSH executes via ``/bin/sh -c``, so any
# single shell metacharacter that leaks through is a command-injection
# primitive.

_SSH_SANDBOX_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")
_SSH_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/=\-]{1,4096}$")
# Conservative approximation of an RFC 3986 host: IPv4, bracketed IPv6,
# or DNS-Punycode (which is alphanumeric + `.-`). No `@`, no `/`, no
# whitespace, no shell metacharacters.
_SSH_GATEWAY_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,253}$")
# RFC 3986 path-absolute, plus `%HH` sequences. Rejects `?`, `#`,
# whitespace, backtick, backslash — anything that could break out of
# the ProxyCommand URL grammar.
_SSH_CONNECT_PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@/%\-]*$")
_SSH_FINGERPRINT_RE = re.compile(r"^[A-Za-z0-9:+/=\-]+$")
_SSH_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _validate_ssh_session_response(resp: Any) -> None:
    """Enforce the upstream CreateSshSessionResponse charset contract.

    Args:
        resp: Protobuf response message returned by the gateway.

    Raises:
        SandboxError: If any field is outside the documented charset or
            numeric range. The offending field name is surfaced but the
            value is *not* logged — a violating payload may itself be an
            injection attempt.
    """
    violation: str | None = None
    if not _SSH_SANDBOX_ID_RE.match(resp.sandbox_id or ""):
        violation = "sandbox_id"
    elif not _SSH_TOKEN_RE.match(resp.token or ""):
        violation = "token"
    elif not _SSH_GATEWAY_HOST_RE.match(resp.gateway_host or ""):
        violation = "gateway_host"
    elif not 1 <= int(resp.gateway_port) <= 65535:
        violation = "gateway_port"
    elif resp.gateway_scheme not in _SSH_ALLOWED_SCHEMES:
        violation = "gateway_scheme"
    elif not _SSH_CONNECT_PATH_RE.match(resp.connect_path or ""):
        violation = "connect_path"
    # host_key_fingerprint is optional — empty string is valid.
    elif resp.host_key_fingerprint and not _SSH_FINGERPRINT_RE.match(resp.host_key_fingerprint):
        violation = "host_key_fingerprint"
    if violation is not None:
        raise SandboxError(
            "ssh session response violates upstream charset contract "
            f"(field={violation}); refusing to surface response"
        )


def _sandbox_to_dict(sb: openshell_pb2.Sandbox) -> dict[str, Any]:
    """Convert a protobuf Sandbox to a plain dict.

    Args:
        sb: Sandbox protobuf message.

    Returns:
        dict[str, Any]: Sandbox data with id, name, phase, and spec fields.
    """
    return {
        "id": sb.id,
        "name": sb.name,
        "namespace": sb.namespace,
        "phase": PHASE_NAMES.get(sb.phase, "unknown"),
        "phase_code": sb.phase,
        "created_at_ms": sb.created_at_ms,
        "current_policy_version": sb.current_policy_version,
        "image": sb.spec.template.image if sb.spec.template.image else None,
        "gpu": sb.spec.gpu if sb.HasField("spec") else False,
    }


class SandboxManager:
    """Sandbox CRUD, execution, and lifecycle operations.

    Args:
        stub: OpenShell gRPC stub.
        timeout: gRPC call timeout in seconds.
        retry_policy: Retry/backoff policy applied to every unary RPC and to
            stream-open calls. Defaults to :data:`DEFAULT_POLICY`.
        retry_deadline: Total wall-clock budget in seconds including retries.
            ``None`` lets ``max_attempts`` be the only limiter.
    """

    # Class-level defaults so instances built via ``object.__new__`` (unit
    # tests) inherit a safe retry policy without touching every fixture.
    _retry_policy: RetryPolicy = DEFAULT_POLICY
    _retry_deadline: float | None = None

    def __init__(  # noqa: D107
        self,
        stub: openshell_pb2_grpc.OpenShellStub,
        *,
        timeout: float = 30.0,
        retry_policy: RetryPolicy | None = None,
        retry_deadline: float | None = 60.0,
    ) -> None:
        self._stub = stub
        self._timeout = timeout
        self._retry_policy = retry_policy or DEFAULT_POLICY
        self._retry_deadline = retry_deadline

    def _invoke(self, op_name: str, fn: Any) -> Any:
        """Execute a unary gRPC call through the resilience wrapper.

        Args:
            op_name: Logical-op label used for logs and metrics.
            fn: Zero-arg callable that issues the gRPC call.

        Returns:
            Any: The result returned by ``fn``.
        """
        record_attempt, record_duration = _lazy_metrics()
        start = time.monotonic()
        try:
            return call_with_retry(
                fn,
                op_name=op_name,
                policy=self._retry_policy,
                deadline_s=self._retry_deadline,
                on_attempt=record_attempt,
            )
        finally:
            if record_duration is not None:
                record_duration(op_name, time.monotonic() - start)

    def _open_stream(self, op_name: str, fn: Any) -> Any:
        """Open a gRPC server-stream; retry only the open, not in-flight reads.

        Args:
            op_name: Logical-op label used for logs and metrics.
            fn: Zero-arg callable that opens the stream.

        Returns:
            Any: The opened stream iterator.
        """
        record_attempt, record_duration = _lazy_metrics()
        start = time.monotonic()
        try:
            return stream_with_retry(
                fn,
                op_name=op_name,
                policy=self._retry_policy,
                deadline_s=self._retry_deadline,
                on_attempt=record_attempt,
            )
        finally:
            if record_duration is not None:
                record_duration(op_name, time.monotonic() - start)

    def list(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """List all sandboxes.

        Args:
            limit: Maximum number of sandboxes to return.
            offset: Pagination offset.

        Returns:
            list[dict[str, Any]]: List of sandbox dicts.
        """
        resp = self._invoke(
            "sandboxes.list",
            lambda: self._stub.ListSandboxes(
                openshell_pb2.ListSandboxesRequest(limit=limit, offset=offset),
                timeout=self._timeout,
            ),
        )
        return [_sandbox_to_dict(sb) for sb in resp.sandboxes]

    def get(self, name: str) -> dict[str, Any]:
        """Get a sandbox by name.

        Args:
            name: Sandbox name.

        Returns:
            dict[str, Any]: Sandbox data dict.
        """
        resp = self._invoke(
            "sandboxes.get",
            lambda: self._stub.GetSandbox(
                openshell_pb2.GetSandboxRequest(name=name), timeout=self._timeout
            ),
        )
        return _sandbox_to_dict(resp.sandbox)

    def create(
        self,
        *,
        name: str = "",
        image: str = "",
        policy: dict | None = None,
        providers: list[str] | None = None,
        gpu: bool = False,
        environment: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create a new sandbox.

        Args:
            name: Sandbox name.
            image: Container image to use.
            policy: Optional sandbox policy dict.
            providers: Optional list of provider names.
            gpu: Whether to request GPU resources.
            environment: Optional environment variable key-value pairs.

        Returns:
            dict[str, Any]: Created sandbox data dict.
        """
        spec = openshell_pb2.SandboxSpec(gpu=gpu)
        if image:
            spec.template.CopyFrom(openshell_pb2.SandboxTemplate(image=image))
        if providers:
            spec.providers.extend(providers)
        if environment:
            spec.environment.update(environment)
        if policy:
            spec.policy.CopyFrom(_dict_to_policy(policy))

        resp = self._invoke(
            "sandboxes.create",
            lambda: self._stub.CreateSandbox(
                openshell_pb2.CreateSandboxRequest(spec=spec, name=name),
                timeout=self._timeout,
            ),
        )
        return _sandbox_to_dict(resp.sandbox)

    def delete(self, name: str) -> bool:
        """Delete a sandbox by name.

        Args:
            name: Sandbox name.

        Returns:
            bool: True if the sandbox was deleted.
        """
        resp = self._invoke(
            "sandboxes.delete",
            lambda: self._stub.DeleteSandbox(
                openshell_pb2.DeleteSandboxRequest(name=name), timeout=self._timeout
            ),
        )
        return bool(resp.deleted)

    def wait_ready(self, name: str, *, timeout_seconds: float = 300.0) -> dict[str, Any]:
        """Block until a sandbox reaches READY phase.

        Args:
            name: Sandbox name.
            timeout_seconds: Maximum time to wait in seconds.

        Returns:
            dict[str, Any]: Sandbox data dict once ready.

        Raises:
            SandboxError: If the sandbox enters an error phase.
            TimeoutError: If the sandbox is not ready within the timeout.
        """
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            sb = self.get(name)
            if sb["phase"] == "ready":
                return sb
            if sb["phase"] == "error":
                raise SandboxError(f"Sandbox {name} entered error phase")
            time.sleep(1)
        raise TimeoutError(f"Sandbox {name} was not ready within {timeout_seconds}s")

    def get_config(self, sandbox_id: str) -> dict[str, Any]:
        """Fetch the effective sandbox config (policy + settings + revision).

        Args:
            sandbox_id: Sandbox identifier (proto field accepts the name).

        Returns:
            dict[str, Any]: ``{policy, version, policy_hash, settings,
                config_revision, policy_source, global_policy_version}``.
                ``settings`` is a flat ``{key: {value, scope}}`` map.
        """
        resp = self._invoke(
            "sandboxes.get_config",
            lambda: self._stub.GetSandboxConfig(
                sandbox_pb2.GetSandboxConfigRequest(sandbox_id=sandbox_id),
                timeout=self._timeout,
            ),
        )
        settings: dict[str, dict[str, Any]] = {}
        for key, eff in resp.settings.items():
            field = eff.value.WhichOneof("value")
            value: Any
            if field == "string_value":
                value = eff.value.string_value
            elif field == "bool_value":
                value = eff.value.bool_value
            elif field == "int_value":
                value = eff.value.int_value
            elif field == "bytes_value":
                value = eff.value.bytes_value
            else:
                value = None
            settings[key] = {"value": value, "scope": int(eff.scope)}
        return {
            "policy": _policy_to_dict(resp.policy) if resp.HasField("policy") else None,
            "version": resp.version,
            "policy_hash": resp.policy_hash,
            "settings": settings,
            "config_revision": resp.config_revision,
            "policy_source": resp.policy_source,
            "global_policy_version": resp.global_policy_version,
        }

    def get_provider_environment(self, sandbox_id: str) -> dict[str, str]:
        """Fetch the resolved provider environment variables for a sandbox.

        Args:
            sandbox_id: Sandbox identifier (proto field accepts the name).

        Returns:
            dict[str, str]: Environment variables map.
        """
        resp = self._invoke(
            "sandboxes.get_provider_environment",
            lambda: self._stub.GetSandboxProviderEnvironment(
                openshell_pb2.GetSandboxProviderEnvironmentRequest(sandbox_id=sandbox_id),
                timeout=self._timeout,
            ),
        )
        return dict(resp.environment)

    def exec(
        self,
        sandbox_id: str,
        command: list[str],
        *,
        workdir: str = "",
        env: dict[str, str] | None = None,
        timeout_seconds: int = 0,
        tty: bool = False,
    ) -> dict[str, Any]:
        """Execute a command in a sandbox and return the result.

        Args:
            sandbox_id: Sandbox identifier.
            command: Command and arguments to execute.
            workdir: Working directory inside the sandbox.
            env: Optional environment variables for the command.
            timeout_seconds: Command timeout in seconds (0 for default).
            tty: Allocate a TTY for the command (for interactive programs
                that detect ``isatty()``).  Added in OpenShell v0.0.23.

        Returns:
            dict[str, Any]: Execution result with exit_code, stdout,
                and stderr.
        """
        request = openshell_pb2.ExecSandboxRequest(
            sandbox_id=sandbox_id,
            command=command,
            workdir=workdir,
            environment=dict(env or {}),
            timeout_seconds=timeout_seconds,
            tty=tty,
        )
        grpc_timeout = max(self._timeout, (timeout_seconds or 600) + 10)
        stream = self._open_stream(
            "sandboxes.exec",
            lambda: self._stub.ExecSandbox(request, timeout=grpc_timeout),
        )

        stdout_parts: list[bytes] = []
        stderr_parts: list[bytes] = []
        exit_code: int | None = None

        for event in stream:
            payload = event.WhichOneof("payload")
            if payload == "stdout":
                stdout_parts.append(bytes(event.stdout.data))
            elif payload == "stderr":
                stderr_parts.append(bytes(event.stderr.data))
            elif payload == "exit":
                exit_code = int(event.exit.exit_code)

        return {
            "exit_code": exit_code,
            "stdout": b"".join(stdout_parts).decode("utf-8", errors="replace"),
            "stderr": b"".join(stderr_parts).decode("utf-8", errors="replace"),
        }

    def create_ssh_session(self, sandbox_id: str) -> dict[str, Any]:
        """Create a temporary SSH session for shell access to a sandbox.

        The upstream gateway holds a documented charset contract on every
        response field (see ``CreateSshSessionResponse`` in
        ``openshell.proto``, tightened in
        `NVIDIA/OpenShell#876 <https://github.com/NVIDIA/OpenShell/pull/876>`_).
        The fields flow into an SSH ``ProxyCommand`` string executed
        through ``/bin/sh -c`` on the caller's workstation, so anything
        outside the contract is a ProxyCommand-injection vector.
        ShoreGuard enforces the same contract client-side as
        defence-in-depth: a compromised or misconfigured gateway cannot
        push shell metacharacters into ShoreGuard's REST response.

        Args:
            sandbox_id: Sandbox identifier.

        :func:`_validate_ssh_session_response` raises
        :class:`SandboxError` before the response can escape if any field
        is outside the documented charset or numeric range.

        Returns:
            dict[str, Any]: SSH session details including token and
                gateway connection info.
        """
        resp = self._invoke(
            "sandboxes.create_ssh_session",
            lambda: self._stub.CreateSshSession(
                openshell_pb2.CreateSshSessionRequest(sandbox_id=sandbox_id),
                timeout=self._timeout,
            ),
        )
        _validate_ssh_session_response(resp)
        return {
            "sandbox_id": resp.sandbox_id,
            "token": resp.token,
            "gateway_host": resp.gateway_host,
            "gateway_port": resp.gateway_port,
            "gateway_scheme": resp.gateway_scheme,
            "connect_path": resp.connect_path,
            "host_key_fingerprint": resp.host_key_fingerprint,
            "expires_at_ms": resp.expires_at_ms,
        }

    def revoke_ssh_session(self, token: str) -> bool:
        """Revoke an active SSH session.

        Args:
            token: Session token to revoke.

        Returns:
            bool: True if the session was revoked.
        """
        resp = self._invoke(
            "sandboxes.revoke_ssh_session",
            lambda: self._stub.RevokeSshSession(
                openshell_pb2.RevokeSshSessionRequest(token=token),
                timeout=self._timeout,
            ),
        )
        return bool(resp.revoked)

    def get_logs(
        self,
        sandbox_id: str,
        *,
        lines: int = 200,
        since_ms: int = 0,
        sources: list[str] | None = None,
        min_level: str = "",
    ) -> list[dict[str, Any]]:
        """Fetch recent sandbox logs.

        Args:
            sandbox_id: Sandbox identifier.
            lines: Maximum number of log lines to return.
            since_ms: Only return logs after this timestamp (ms).
            sources: Optional list of log sources to filter by.
            min_level: Minimum log level to include.

        Returns:
            list[dict[str, Any]]: List of log entry dicts.
        """
        resp = self._invoke(
            "sandboxes.get_logs",
            lambda: self._stub.GetSandboxLogs(
                openshell_pb2.GetSandboxLogsRequest(
                    sandbox_id=sandbox_id,
                    lines=lines,
                    since_ms=since_ms,
                    sources=sources or [],
                    min_level=min_level,
                ),
                timeout=self._timeout,
            ),
        )
        return [
            {
                "timestamp_ms": log.timestamp_ms,
                "level": log.level,
                "message": log.message,
                "source": log.source,
                "target": log.target,
                "fields": dict(log.fields),
            }
            for log in resp.logs
        ]

    def watch(
        self,
        sandbox_id: str,
        *,
        follow_status: bool = True,
        follow_logs: bool = True,
        follow_events: bool = True,
        log_tail_lines: int = 50,
    ) -> Iterator[dict[str, Any]]:
        """Stream live sandbox events (status, logs, platform events, draft updates).

        Args:
            sandbox_id: Sandbox identifier.
            follow_status: Subscribe to status changes.
            follow_logs: Subscribe to log output.
            follow_events: Subscribe to platform events.
            log_tail_lines: Number of existing log lines to replay.

        Yields:
            dict[str, Any]: Event dict with ``type`` and ``data`` keys.
        """
        stream = self._open_stream(
            "sandboxes.watch",
            lambda: self._stub.WatchSandbox(
                openshell_pb2.WatchSandboxRequest(
                    id=sandbox_id,
                    follow_status=follow_status,
                    follow_logs=follow_logs,
                    follow_events=follow_events,
                    log_tail_lines=log_tail_lines,
                ),
            ),
        )
        for event in stream:
            payload_type = event.WhichOneof("payload")
            if payload_type == "sandbox":
                yield {"type": "status", "data": _sandbox_to_dict(event.sandbox)}
            elif payload_type == "log":
                yield {
                    "type": "log",
                    "data": {
                        "timestamp_ms": event.log.timestamp_ms,
                        "level": event.log.level,
                        "message": event.log.message,
                        "source": event.log.source,
                        "target": event.log.target,
                        "fields": dict(event.log.fields),
                    },
                }
            elif payload_type == "event":
                yield {
                    "type": "event",
                    "data": {
                        "timestamp_ms": event.event.timestamp_ms,
                        "source": event.event.source,
                        "type": event.event.type,
                        "reason": event.event.reason,
                        "message": event.event.message,
                    },
                }
            elif payload_type == "draft_policy_update":
                yield {
                    "type": "draft_policy_update",
                    "data": {
                        "draft_version": event.draft_policy_update.draft_version,
                        "new_chunks": event.draft_policy_update.new_chunks,
                        "total_pending": event.draft_policy_update.total_pending,
                        "summary": event.draft_policy_update.summary,
                    },
                }
            elif payload_type == "warning":
                yield {"type": "warning", "data": {"message": event.warning.message}}
