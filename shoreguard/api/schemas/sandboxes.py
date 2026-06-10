"""Sandbox lifecycle, exec, SSH, and provider-attach schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ─── Sandboxes ────────────────────────────────────────────────────────────────


class SandboxResponse(BaseModel):
    """Sandbox record (CRUD + metadata).

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        name (str | None): Sandbox name.
        status (str | None): Current sandbox status.
        image (str | None): Container image backing the sandbox.
        gpu (bool | None): Whether the sandbox has GPU access.
        description (str | None): Human-readable sandbox description.
        labels (dict[str, str] | None): Label key/value pairs.
        current_policy_version (int | None): Revision of the policy
            currently loaded by the sandbox supervisor. May differ from
            the configured revision in the policy-pinning workflow
            (M18) when an approval has just been applied but the
            supervisor has not reported back yet. Set from the gateway's
            ``Sandbox.current_policy_version`` field; omitted when the
            gateway does not expose it.
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version

    name: str | None = None
    status: str | None = None
    image: str | None = None
    gpu: bool | None = None
    description: str | None = None
    labels: dict[str, str] | None = None
    current_policy_version: int | None = None


class SandboxDeleteResponse(BaseModel):
    """Sandbox deletion confirmation.

    Attributes:
        deleted (bool): Whether the sandbox was deleted.
    """

    deleted: bool


class SandboxConfigResponse(BaseModel):
    """Stored sandbox configuration as held by the gateway.

    The gateway returns a protobuf-derived dict whose fields depend on the
    pinned OpenShell version. We pass it through verbatim under
    ``extra="allow"`` so a future upstream field (e.g. new policy
    section, new template knob) reaches the REST surface without a
    schema bump.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
    """

    model_config = ConfigDict(extra="allow")


class AttachSandboxProviderRequest(BaseModel):
    """Body for attaching a provider record to a sandbox.

    Attributes:
        provider_name (str): Provider name to attach.
    """

    provider_name: str = Field(
        min_length=1, max_length=253, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$"
    )


class AttachSandboxProviderResponse(BaseModel):
    """Result of attaching a provider to a sandbox.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        sandbox (dict[str, Any]): Sandbox record after the attach.
        attached (bool): True when the provider was newly attached. False
            means it was already attached.
    """

    model_config = ConfigDict(extra="allow")

    sandbox: dict[str, Any]
    attached: bool


class DetachSandboxProviderResponse(BaseModel):
    """Result of detaching a provider from a sandbox.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        sandbox (dict[str, Any]): Sandbox record after the detach.
        detached (bool): True when the provider was removed. False means
            it was not attached.
    """

    model_config = ConfigDict(extra="allow")

    sandbox: dict[str, Any]
    detached: bool


class SandboxProviderEnvResponse(BaseModel):
    """Environment-variable keys the gateway injects into this sandbox.

    Values are always redacted. Use ``GET /providers/{name}/env`` for
    provider-level context on *where* a key comes from (credential vs.
    config). This endpoint is the sandbox-scoped version — what this
    specific sandbox actually receives at runtime.

    Attributes:
        env (dict[str, str]): Key → ``"[REDACTED]"`` map. An empty dict
            means the gateway has no provider environment to inject for
            this sandbox.
    """

    env: dict[str, str]


class SshSessionResponse(BaseModel):
    """SSH session details.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        token (str | None): Session token used to authenticate the SSH connection.
        host (str | None): SSH host to connect to.
        port (int | None): SSH port to connect to.
        username (str | None): SSH username to use.
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version

    token: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None


class SshRevokeResponse(BaseModel):
    """SSH session revocation confirmation.

    Attributes:
        revoked (bool): Whether the SSH session was revoked.
    """

    revoked: bool


class ExecResultResponse(BaseModel):
    """Command execution result.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        exit_code (int | None): Process exit code.
        stdout (str | None): Captured standard output.
        stderr (str | None): Captured standard error.
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version

    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None


class LogEntryResponse(BaseModel):
    """Single sandbox log entry.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version
