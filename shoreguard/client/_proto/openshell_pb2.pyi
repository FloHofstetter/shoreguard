from . import datamodel_pb2 as _datamodel_pb2
from . import sandbox_pb2 as _sandbox_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class PolicyStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    POLICY_STATUS_UNSPECIFIED: _ClassVar[PolicyStatus]
    POLICY_STATUS_PENDING: _ClassVar[PolicyStatus]
    POLICY_STATUS_LOADED: _ClassVar[PolicyStatus]
    POLICY_STATUS_FAILED: _ClassVar[PolicyStatus]
    POLICY_STATUS_SUPERSEDED: _ClassVar[PolicyStatus]

class ServiceStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SERVICE_STATUS_UNSPECIFIED: _ClassVar[ServiceStatus]
    SERVICE_STATUS_HEALTHY: _ClassVar[ServiceStatus]
    SERVICE_STATUS_DEGRADED: _ClassVar[ServiceStatus]
    SERVICE_STATUS_UNHEALTHY: _ClassVar[ServiceStatus]

POLICY_STATUS_UNSPECIFIED: PolicyStatus
POLICY_STATUS_PENDING: PolicyStatus
POLICY_STATUS_LOADED: PolicyStatus
POLICY_STATUS_FAILED: PolicyStatus
POLICY_STATUS_SUPERSEDED: PolicyStatus
SERVICE_STATUS_UNSPECIFIED: ServiceStatus
SERVICE_STATUS_HEALTHY: ServiceStatus
SERVICE_STATUS_DEGRADED: ServiceStatus
SERVICE_STATUS_UNHEALTHY: ServiceStatus

class HealthRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class HealthResponse(_message.Message):
    __slots__ = ("status", "version")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    status: ServiceStatus
    version: str
    def __init__(
        self, status: _Optional[_Union[ServiceStatus, str]] = ..., version: _Optional[str] = ...
    ) -> None: ...

class CreateSandboxRequest(_message.Message):
    __slots__ = ("spec", "name")
    SPEC_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    spec: _datamodel_pb2.SandboxSpec
    name: str
    def __init__(
        self,
        spec: _Optional[_Union[_datamodel_pb2.SandboxSpec, _Mapping]] = ...,
        name: _Optional[str] = ...,
    ) -> None: ...

class GetSandboxRequest(_message.Message):
    __slots__ = ("name",)
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: _Optional[str] = ...) -> None: ...

class ListSandboxesRequest(_message.Message):
    __slots__ = ("limit", "offset")
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    limit: int
    offset: int
    def __init__(self, limit: _Optional[int] = ..., offset: _Optional[int] = ...) -> None: ...

class DeleteSandboxRequest(_message.Message):
    __slots__ = ("name",)
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: _Optional[str] = ...) -> None: ...

class SandboxResponse(_message.Message):
    __slots__ = ("sandbox",)
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    sandbox: _datamodel_pb2.Sandbox
    def __init__(
        self, sandbox: _Optional[_Union[_datamodel_pb2.Sandbox, _Mapping]] = ...
    ) -> None: ...

class ListSandboxesResponse(_message.Message):
    __slots__ = ("sandboxes",)
    SANDBOXES_FIELD_NUMBER: _ClassVar[int]
    sandboxes: _containers.RepeatedCompositeFieldContainer[_datamodel_pb2.Sandbox]
    def __init__(
        self, sandboxes: _Optional[_Iterable[_Union[_datamodel_pb2.Sandbox, _Mapping]]] = ...
    ) -> None: ...

class DeleteSandboxResponse(_message.Message):
    __slots__ = ("deleted",)
    DELETED_FIELD_NUMBER: _ClassVar[int]
    deleted: bool
    def __init__(self, deleted: bool = ...) -> None: ...

class CreateSshSessionRequest(_message.Message):
    __slots__ = ("sandbox_id",)
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    def __init__(self, sandbox_id: _Optional[str] = ...) -> None: ...

class CreateSshSessionResponse(_message.Message):
    __slots__ = (
        "sandbox_id",
        "token",
        "gateway_host",
        "gateway_port",
        "gateway_scheme",
        "connect_path",
        "host_key_fingerprint",
        "expires_at_ms",
    )
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    GATEWAY_HOST_FIELD_NUMBER: _ClassVar[int]
    GATEWAY_PORT_FIELD_NUMBER: _ClassVar[int]
    GATEWAY_SCHEME_FIELD_NUMBER: _ClassVar[int]
    CONNECT_PATH_FIELD_NUMBER: _ClassVar[int]
    HOST_KEY_FINGERPRINT_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_MS_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    token: str
    gateway_host: str
    gateway_port: int
    gateway_scheme: str
    connect_path: str
    host_key_fingerprint: str
    expires_at_ms: int
    def __init__(
        self,
        sandbox_id: _Optional[str] = ...,
        token: _Optional[str] = ...,
        gateway_host: _Optional[str] = ...,
        gateway_port: _Optional[int] = ...,
        gateway_scheme: _Optional[str] = ...,
        connect_path: _Optional[str] = ...,
        host_key_fingerprint: _Optional[str] = ...,
        expires_at_ms: _Optional[int] = ...,
    ) -> None: ...

class RevokeSshSessionRequest(_message.Message):
    __slots__ = ("token",)
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    token: str
    def __init__(self, token: _Optional[str] = ...) -> None: ...

class RevokeSshSessionResponse(_message.Message):
    __slots__ = ("revoked",)
    REVOKED_FIELD_NUMBER: _ClassVar[int]
    revoked: bool
    def __init__(self, revoked: bool = ...) -> None: ...

class ExecSandboxRequest(_message.Message):
    __slots__ = (
        "sandbox_id",
        "command",
        "workdir",
        "environment",
        "timeout_seconds",
        "stdin",
        "tty",
    )
    class EnvironmentEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    COMMAND_FIELD_NUMBER: _ClassVar[int]
    WORKDIR_FIELD_NUMBER: _ClassVar[int]
    ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_SECONDS_FIELD_NUMBER: _ClassVar[int]
    STDIN_FIELD_NUMBER: _ClassVar[int]
    TTY_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    command: _containers.RepeatedScalarFieldContainer[str]
    workdir: str
    environment: _containers.ScalarMap[str, str]
    timeout_seconds: int
    stdin: bytes
    tty: bool
    def __init__(
        self,
        sandbox_id: _Optional[str] = ...,
        command: _Optional[_Iterable[str]] = ...,
        workdir: _Optional[str] = ...,
        environment: _Optional[_Mapping[str, str]] = ...,
        timeout_seconds: _Optional[int] = ...,
        stdin: _Optional[bytes] = ...,
        tty: bool = ...,
    ) -> None: ...

class ExecSandboxStdout(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: bytes
    def __init__(self, data: _Optional[bytes] = ...) -> None: ...

class ExecSandboxStderr(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: bytes
    def __init__(self, data: _Optional[bytes] = ...) -> None: ...

class ExecSandboxExit(_message.Message):
    __slots__ = ("exit_code",)
    EXIT_CODE_FIELD_NUMBER: _ClassVar[int]
    exit_code: int
    def __init__(self, exit_code: _Optional[int] = ...) -> None: ...

class ExecSandboxEvent(_message.Message):
    __slots__ = ("stdout", "stderr", "exit")
    STDOUT_FIELD_NUMBER: _ClassVar[int]
    STDERR_FIELD_NUMBER: _ClassVar[int]
    EXIT_FIELD_NUMBER: _ClassVar[int]
    stdout: ExecSandboxStdout
    stderr: ExecSandboxStderr
    exit: ExecSandboxExit
    def __init__(
        self,
        stdout: _Optional[_Union[ExecSandboxStdout, _Mapping]] = ...,
        stderr: _Optional[_Union[ExecSandboxStderr, _Mapping]] = ...,
        exit: _Optional[_Union[ExecSandboxExit, _Mapping]] = ...,
    ) -> None: ...

class SshSession(_message.Message):
    __slots__ = ("id", "sandbox_id", "token", "created_at_ms", "revoked", "name", "expires_at_ms")
    ID_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_MS_FIELD_NUMBER: _ClassVar[int]
    REVOKED_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_MS_FIELD_NUMBER: _ClassVar[int]
    id: str
    sandbox_id: str
    token: str
    created_at_ms: int
    revoked: bool
    name: str
    expires_at_ms: int
    def __init__(
        self,
        id: _Optional[str] = ...,
        sandbox_id: _Optional[str] = ...,
        token: _Optional[str] = ...,
        created_at_ms: _Optional[int] = ...,
        revoked: bool = ...,
        name: _Optional[str] = ...,
        expires_at_ms: _Optional[int] = ...,
    ) -> None: ...

class WatchSandboxRequest(_message.Message):
    __slots__ = (
        "id",
        "follow_status",
        "follow_logs",
        "follow_events",
        "log_tail_lines",
        "event_tail",
        "stop_on_terminal",
        "log_since_ms",
        "log_sources",
        "log_min_level",
    )
    ID_FIELD_NUMBER: _ClassVar[int]
    FOLLOW_STATUS_FIELD_NUMBER: _ClassVar[int]
    FOLLOW_LOGS_FIELD_NUMBER: _ClassVar[int]
    FOLLOW_EVENTS_FIELD_NUMBER: _ClassVar[int]
    LOG_TAIL_LINES_FIELD_NUMBER: _ClassVar[int]
    EVENT_TAIL_FIELD_NUMBER: _ClassVar[int]
    STOP_ON_TERMINAL_FIELD_NUMBER: _ClassVar[int]
    LOG_SINCE_MS_FIELD_NUMBER: _ClassVar[int]
    LOG_SOURCES_FIELD_NUMBER: _ClassVar[int]
    LOG_MIN_LEVEL_FIELD_NUMBER: _ClassVar[int]
    id: str
    follow_status: bool
    follow_logs: bool
    follow_events: bool
    log_tail_lines: int
    event_tail: int
    stop_on_terminal: bool
    log_since_ms: int
    log_sources: _containers.RepeatedScalarFieldContainer[str]
    log_min_level: str
    def __init__(
        self,
        id: _Optional[str] = ...,
        follow_status: bool = ...,
        follow_logs: bool = ...,
        follow_events: bool = ...,
        log_tail_lines: _Optional[int] = ...,
        event_tail: _Optional[int] = ...,
        stop_on_terminal: bool = ...,
        log_since_ms: _Optional[int] = ...,
        log_sources: _Optional[_Iterable[str]] = ...,
        log_min_level: _Optional[str] = ...,
    ) -> None: ...

class SandboxStreamEvent(_message.Message):
    __slots__ = ("sandbox", "log", "event", "warning", "draft_policy_update")
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    LOG_FIELD_NUMBER: _ClassVar[int]
    EVENT_FIELD_NUMBER: _ClassVar[int]
    WARNING_FIELD_NUMBER: _ClassVar[int]
    DRAFT_POLICY_UPDATE_FIELD_NUMBER: _ClassVar[int]
    sandbox: _datamodel_pb2.Sandbox
    log: SandboxLogLine
    event: PlatformEvent
    warning: SandboxStreamWarning
    draft_policy_update: DraftPolicyUpdate
    def __init__(
        self,
        sandbox: _Optional[_Union[_datamodel_pb2.Sandbox, _Mapping]] = ...,
        log: _Optional[_Union[SandboxLogLine, _Mapping]] = ...,
        event: _Optional[_Union[PlatformEvent, _Mapping]] = ...,
        warning: _Optional[_Union[SandboxStreamWarning, _Mapping]] = ...,
        draft_policy_update: _Optional[_Union[DraftPolicyUpdate, _Mapping]] = ...,
    ) -> None: ...

class SandboxLogLine(_message.Message):
    __slots__ = ("sandbox_id", "timestamp_ms", "level", "target", "message", "source", "fields")
    class FieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_MS_FIELD_NUMBER: _ClassVar[int]
    LEVEL_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    FIELDS_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    timestamp_ms: int
    level: str
    target: str
    message: str
    source: str
    fields: _containers.ScalarMap[str, str]
    def __init__(
        self,
        sandbox_id: _Optional[str] = ...,
        timestamp_ms: _Optional[int] = ...,
        level: _Optional[str] = ...,
        target: _Optional[str] = ...,
        message: _Optional[str] = ...,
        source: _Optional[str] = ...,
        fields: _Optional[_Mapping[str, str]] = ...,
    ) -> None: ...

class PlatformEvent(_message.Message):
    __slots__ = ("timestamp_ms", "source", "type", "reason", "message", "metadata")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

    TIMESTAMP_MS_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    timestamp_ms: int
    source: str
    type: str
    reason: str
    message: str
    metadata: _containers.ScalarMap[str, str]
    def __init__(
        self,
        timestamp_ms: _Optional[int] = ...,
        source: _Optional[str] = ...,
        type: _Optional[str] = ...,
        reason: _Optional[str] = ...,
        message: _Optional[str] = ...,
        metadata: _Optional[_Mapping[str, str]] = ...,
    ) -> None: ...

class SandboxStreamWarning(_message.Message):
    __slots__ = ("message",)
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    message: str
    def __init__(self, message: _Optional[str] = ...) -> None: ...

class CreateProviderRequest(_message.Message):
    __slots__ = ("provider",)
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    provider: _datamodel_pb2.Provider
    def __init__(
        self, provider: _Optional[_Union[_datamodel_pb2.Provider, _Mapping]] = ...
    ) -> None: ...

class GetProviderRequest(_message.Message):
    __slots__ = ("name",)
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: _Optional[str] = ...) -> None: ...

class ListProvidersRequest(_message.Message):
    __slots__ = ("limit", "offset")
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    limit: int
    offset: int
    def __init__(self, limit: _Optional[int] = ..., offset: _Optional[int] = ...) -> None: ...

class UpdateProviderRequest(_message.Message):
    __slots__ = ("provider",)
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    provider: _datamodel_pb2.Provider
    def __init__(
        self, provider: _Optional[_Union[_datamodel_pb2.Provider, _Mapping]] = ...
    ) -> None: ...

class DeleteProviderRequest(_message.Message):
    __slots__ = ("name",)
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: _Optional[str] = ...) -> None: ...

class ProviderResponse(_message.Message):
    __slots__ = ("provider",)
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    provider: _datamodel_pb2.Provider
    def __init__(
        self, provider: _Optional[_Union[_datamodel_pb2.Provider, _Mapping]] = ...
    ) -> None: ...

class ListProvidersResponse(_message.Message):
    __slots__ = ("providers",)
    PROVIDERS_FIELD_NUMBER: _ClassVar[int]
    providers: _containers.RepeatedCompositeFieldContainer[_datamodel_pb2.Provider]
    def __init__(
        self, providers: _Optional[_Iterable[_Union[_datamodel_pb2.Provider, _Mapping]]] = ...
    ) -> None: ...

class DeleteProviderResponse(_message.Message):
    __slots__ = ("deleted",)
    DELETED_FIELD_NUMBER: _ClassVar[int]
    deleted: bool
    def __init__(self, deleted: bool = ...) -> None: ...

class GetSandboxProviderEnvironmentRequest(_message.Message):
    __slots__ = ("sandbox_id",)
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    def __init__(self, sandbox_id: _Optional[str] = ...) -> None: ...

class GetSandboxProviderEnvironmentResponse(_message.Message):
    __slots__ = ("environment",)
    class EnvironmentEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

    ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    environment: _containers.ScalarMap[str, str]
    def __init__(self, environment: _Optional[_Mapping[str, str]] = ...) -> None: ...

class UpdateConfigRequest(_message.Message):
    __slots__ = ("name", "policy", "setting_key", "setting_value", "delete_setting")
    NAME_FIELD_NUMBER: _ClassVar[int]
    POLICY_FIELD_NUMBER: _ClassVar[int]
    SETTING_KEY_FIELD_NUMBER: _ClassVar[int]
    SETTING_VALUE_FIELD_NUMBER: _ClassVar[int]
    DELETE_SETTING_FIELD_NUMBER: _ClassVar[int]
    GLOBAL_FIELD_NUMBER: _ClassVar[int]
    name: str
    policy: _sandbox_pb2.SandboxPolicy
    setting_key: str
    setting_value: _sandbox_pb2.SettingValue
    delete_setting: bool
    def __init__(
        self,
        name: _Optional[str] = ...,
        policy: _Optional[_Union[_sandbox_pb2.SandboxPolicy, _Mapping]] = ...,
        setting_key: _Optional[str] = ...,
        setting_value: _Optional[_Union[_sandbox_pb2.SettingValue, _Mapping]] = ...,
        delete_setting: bool = ...,
        **kwargs,
    ) -> None: ...

class UpdateConfigResponse(_message.Message):
    __slots__ = ("version", "policy_hash", "settings_revision", "deleted")
    VERSION_FIELD_NUMBER: _ClassVar[int]
    POLICY_HASH_FIELD_NUMBER: _ClassVar[int]
    SETTINGS_REVISION_FIELD_NUMBER: _ClassVar[int]
    DELETED_FIELD_NUMBER: _ClassVar[int]
    version: int
    policy_hash: str
    settings_revision: int
    deleted: bool
    def __init__(
        self,
        version: _Optional[int] = ...,
        policy_hash: _Optional[str] = ...,
        settings_revision: _Optional[int] = ...,
        deleted: bool = ...,
    ) -> None: ...

class GetSandboxPolicyStatusRequest(_message.Message):
    __slots__ = ("name", "version")
    NAME_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    GLOBAL_FIELD_NUMBER: _ClassVar[int]
    name: str
    version: int
    def __init__(
        self, name: _Optional[str] = ..., version: _Optional[int] = ..., **kwargs
    ) -> None: ...

class GetSandboxPolicyStatusResponse(_message.Message):
    __slots__ = ("revision", "active_version")
    REVISION_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_VERSION_FIELD_NUMBER: _ClassVar[int]
    revision: SandboxPolicyRevision
    active_version: int
    def __init__(
        self,
        revision: _Optional[_Union[SandboxPolicyRevision, _Mapping]] = ...,
        active_version: _Optional[int] = ...,
    ) -> None: ...

class ListSandboxPoliciesRequest(_message.Message):
    __slots__ = ("name", "limit", "offset")
    NAME_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    GLOBAL_FIELD_NUMBER: _ClassVar[int]
    name: str
    limit: int
    offset: int
    def __init__(
        self,
        name: _Optional[str] = ...,
        limit: _Optional[int] = ...,
        offset: _Optional[int] = ...,
        **kwargs,
    ) -> None: ...

class ListSandboxPoliciesResponse(_message.Message):
    __slots__ = ("revisions",)
    REVISIONS_FIELD_NUMBER: _ClassVar[int]
    revisions: _containers.RepeatedCompositeFieldContainer[SandboxPolicyRevision]
    def __init__(
        self, revisions: _Optional[_Iterable[_Union[SandboxPolicyRevision, _Mapping]]] = ...
    ) -> None: ...

class ReportPolicyStatusRequest(_message.Message):
    __slots__ = ("sandbox_id", "version", "status", "load_error")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    LOAD_ERROR_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    version: int
    status: PolicyStatus
    load_error: str
    def __init__(
        self,
        sandbox_id: _Optional[str] = ...,
        version: _Optional[int] = ...,
        status: _Optional[_Union[PolicyStatus, str]] = ...,
        load_error: _Optional[str] = ...,
    ) -> None: ...

class ReportPolicyStatusResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class SandboxPolicyRevision(_message.Message):
    __slots__ = (
        "version",
        "policy_hash",
        "status",
        "load_error",
        "created_at_ms",
        "loaded_at_ms",
        "policy",
    )
    VERSION_FIELD_NUMBER: _ClassVar[int]
    POLICY_HASH_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    LOAD_ERROR_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_MS_FIELD_NUMBER: _ClassVar[int]
    LOADED_AT_MS_FIELD_NUMBER: _ClassVar[int]
    POLICY_FIELD_NUMBER: _ClassVar[int]
    version: int
    policy_hash: str
    status: PolicyStatus
    load_error: str
    created_at_ms: int
    loaded_at_ms: int
    policy: _sandbox_pb2.SandboxPolicy
    def __init__(
        self,
        version: _Optional[int] = ...,
        policy_hash: _Optional[str] = ...,
        status: _Optional[_Union[PolicyStatus, str]] = ...,
        load_error: _Optional[str] = ...,
        created_at_ms: _Optional[int] = ...,
        loaded_at_ms: _Optional[int] = ...,
        policy: _Optional[_Union[_sandbox_pb2.SandboxPolicy, _Mapping]] = ...,
    ) -> None: ...

class GetSandboxLogsRequest(_message.Message):
    __slots__ = ("sandbox_id", "lines", "since_ms", "sources", "min_level")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    LINES_FIELD_NUMBER: _ClassVar[int]
    SINCE_MS_FIELD_NUMBER: _ClassVar[int]
    SOURCES_FIELD_NUMBER: _ClassVar[int]
    MIN_LEVEL_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    lines: int
    since_ms: int
    sources: _containers.RepeatedScalarFieldContainer[str]
    min_level: str
    def __init__(
        self,
        sandbox_id: _Optional[str] = ...,
        lines: _Optional[int] = ...,
        since_ms: _Optional[int] = ...,
        sources: _Optional[_Iterable[str]] = ...,
        min_level: _Optional[str] = ...,
    ) -> None: ...

class PushSandboxLogsRequest(_message.Message):
    __slots__ = ("sandbox_id", "logs")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    LOGS_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    logs: _containers.RepeatedCompositeFieldContainer[SandboxLogLine]
    def __init__(
        self,
        sandbox_id: _Optional[str] = ...,
        logs: _Optional[_Iterable[_Union[SandboxLogLine, _Mapping]]] = ...,
    ) -> None: ...

class PushSandboxLogsResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GetSandboxLogsResponse(_message.Message):
    __slots__ = ("logs", "buffer_total")
    LOGS_FIELD_NUMBER: _ClassVar[int]
    BUFFER_TOTAL_FIELD_NUMBER: _ClassVar[int]
    logs: _containers.RepeatedCompositeFieldContainer[SandboxLogLine]
    buffer_total: int
    def __init__(
        self,
        logs: _Optional[_Iterable[_Union[SandboxLogLine, _Mapping]]] = ...,
        buffer_total: _Optional[int] = ...,
    ) -> None: ...

class L7RequestSample(_message.Message):
    __slots__ = ("method", "path", "decision", "count")
    METHOD_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    DECISION_FIELD_NUMBER: _ClassVar[int]
    COUNT_FIELD_NUMBER: _ClassVar[int]
    method: str
    path: str
    decision: str
    count: int
    def __init__(
        self,
        method: _Optional[str] = ...,
        path: _Optional[str] = ...,
        decision: _Optional[str] = ...,
        count: _Optional[int] = ...,
    ) -> None: ...

class DenialSummary(_message.Message):
    __slots__ = (
        "sandbox_id",
        "host",
        "port",
        "binary",
        "ancestors",
        "deny_reason",
        "first_seen_ms",
        "last_seen_ms",
        "count",
        "suppressed_count",
        "total_count",
        "sample_cmdlines",
        "binary_sha256",
        "persistent",
        "denial_stage",
        "l7_request_samples",
        "l7_inspection_active",
    )
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    HOST_FIELD_NUMBER: _ClassVar[int]
    PORT_FIELD_NUMBER: _ClassVar[int]
    BINARY_FIELD_NUMBER: _ClassVar[int]
    ANCESTORS_FIELD_NUMBER: _ClassVar[int]
    DENY_REASON_FIELD_NUMBER: _ClassVar[int]
    FIRST_SEEN_MS_FIELD_NUMBER: _ClassVar[int]
    LAST_SEEN_MS_FIELD_NUMBER: _ClassVar[int]
    COUNT_FIELD_NUMBER: _ClassVar[int]
    SUPPRESSED_COUNT_FIELD_NUMBER: _ClassVar[int]
    TOTAL_COUNT_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_CMDLINES_FIELD_NUMBER: _ClassVar[int]
    BINARY_SHA256_FIELD_NUMBER: _ClassVar[int]
    PERSISTENT_FIELD_NUMBER: _ClassVar[int]
    DENIAL_STAGE_FIELD_NUMBER: _ClassVar[int]
    L7_REQUEST_SAMPLES_FIELD_NUMBER: _ClassVar[int]
    L7_INSPECTION_ACTIVE_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    host: str
    port: int
    binary: str
    ancestors: _containers.RepeatedScalarFieldContainer[str]
    deny_reason: str
    first_seen_ms: int
    last_seen_ms: int
    count: int
    suppressed_count: int
    total_count: int
    sample_cmdlines: _containers.RepeatedScalarFieldContainer[str]
    binary_sha256: str
    persistent: bool
    denial_stage: str
    l7_request_samples: _containers.RepeatedCompositeFieldContainer[L7RequestSample]
    l7_inspection_active: bool
    def __init__(
        self,
        sandbox_id: _Optional[str] = ...,
        host: _Optional[str] = ...,
        port: _Optional[int] = ...,
        binary: _Optional[str] = ...,
        ancestors: _Optional[_Iterable[str]] = ...,
        deny_reason: _Optional[str] = ...,
        first_seen_ms: _Optional[int] = ...,
        last_seen_ms: _Optional[int] = ...,
        count: _Optional[int] = ...,
        suppressed_count: _Optional[int] = ...,
        total_count: _Optional[int] = ...,
        sample_cmdlines: _Optional[_Iterable[str]] = ...,
        binary_sha256: _Optional[str] = ...,
        persistent: bool = ...,
        denial_stage: _Optional[str] = ...,
        l7_request_samples: _Optional[_Iterable[_Union[L7RequestSample, _Mapping]]] = ...,
        l7_inspection_active: bool = ...,
    ) -> None: ...

class PolicyChunk(_message.Message):
    __slots__ = (
        "id",
        "status",
        "rule_name",
        "proposed_rule",
        "rationale",
        "security_notes",
        "confidence",
        "denial_summary_ids",
        "created_at_ms",
        "decided_at_ms",
        "stage",
        "supersedes_chunk_id",
        "hit_count",
        "first_seen_ms",
        "last_seen_ms",
        "binary",
    )
    ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    RULE_NAME_FIELD_NUMBER: _ClassVar[int]
    PROPOSED_RULE_FIELD_NUMBER: _ClassVar[int]
    RATIONALE_FIELD_NUMBER: _ClassVar[int]
    SECURITY_NOTES_FIELD_NUMBER: _ClassVar[int]
    CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    DENIAL_SUMMARY_IDS_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_MS_FIELD_NUMBER: _ClassVar[int]
    DECIDED_AT_MS_FIELD_NUMBER: _ClassVar[int]
    STAGE_FIELD_NUMBER: _ClassVar[int]
    SUPERSEDES_CHUNK_ID_FIELD_NUMBER: _ClassVar[int]
    HIT_COUNT_FIELD_NUMBER: _ClassVar[int]
    FIRST_SEEN_MS_FIELD_NUMBER: _ClassVar[int]
    LAST_SEEN_MS_FIELD_NUMBER: _ClassVar[int]
    BINARY_FIELD_NUMBER: _ClassVar[int]
    id: str
    status: str
    rule_name: str
    proposed_rule: _sandbox_pb2.NetworkPolicyRule
    rationale: str
    security_notes: str
    confidence: float
    denial_summary_ids: _containers.RepeatedScalarFieldContainer[str]
    created_at_ms: int
    decided_at_ms: int
    stage: str
    supersedes_chunk_id: str
    hit_count: int
    first_seen_ms: int
    last_seen_ms: int
    binary: str
    def __init__(
        self,
        id: _Optional[str] = ...,
        status: _Optional[str] = ...,
        rule_name: _Optional[str] = ...,
        proposed_rule: _Optional[_Union[_sandbox_pb2.NetworkPolicyRule, _Mapping]] = ...,
        rationale: _Optional[str] = ...,
        security_notes: _Optional[str] = ...,
        confidence: _Optional[float] = ...,
        denial_summary_ids: _Optional[_Iterable[str]] = ...,
        created_at_ms: _Optional[int] = ...,
        decided_at_ms: _Optional[int] = ...,
        stage: _Optional[str] = ...,
        supersedes_chunk_id: _Optional[str] = ...,
        hit_count: _Optional[int] = ...,
        first_seen_ms: _Optional[int] = ...,
        last_seen_ms: _Optional[int] = ...,
        binary: _Optional[str] = ...,
    ) -> None: ...

class DraftPolicyUpdate(_message.Message):
    __slots__ = ("draft_version", "new_chunks", "total_pending", "summary")
    DRAFT_VERSION_FIELD_NUMBER: _ClassVar[int]
    NEW_CHUNKS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_PENDING_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    draft_version: int
    new_chunks: int
    total_pending: int
    summary: str
    def __init__(
        self,
        draft_version: _Optional[int] = ...,
        new_chunks: _Optional[int] = ...,
        total_pending: _Optional[int] = ...,
        summary: _Optional[str] = ...,
    ) -> None: ...

class SubmitPolicyAnalysisRequest(_message.Message):
    __slots__ = ("summaries", "proposed_chunks", "analysis_mode", "name")
    SUMMARIES_FIELD_NUMBER: _ClassVar[int]
    PROPOSED_CHUNKS_FIELD_NUMBER: _ClassVar[int]
    ANALYSIS_MODE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    summaries: _containers.RepeatedCompositeFieldContainer[DenialSummary]
    proposed_chunks: _containers.RepeatedCompositeFieldContainer[PolicyChunk]
    analysis_mode: str
    name: str
    def __init__(
        self,
        summaries: _Optional[_Iterable[_Union[DenialSummary, _Mapping]]] = ...,
        proposed_chunks: _Optional[_Iterable[_Union[PolicyChunk, _Mapping]]] = ...,
        analysis_mode: _Optional[str] = ...,
        name: _Optional[str] = ...,
    ) -> None: ...

class SubmitPolicyAnalysisResponse(_message.Message):
    __slots__ = ("accepted_chunks", "rejected_chunks", "rejection_reasons")
    ACCEPTED_CHUNKS_FIELD_NUMBER: _ClassVar[int]
    REJECTED_CHUNKS_FIELD_NUMBER: _ClassVar[int]
    REJECTION_REASONS_FIELD_NUMBER: _ClassVar[int]
    accepted_chunks: int
    rejected_chunks: int
    rejection_reasons: _containers.RepeatedScalarFieldContainer[str]
    def __init__(
        self,
        accepted_chunks: _Optional[int] = ...,
        rejected_chunks: _Optional[int] = ...,
        rejection_reasons: _Optional[_Iterable[str]] = ...,
    ) -> None: ...

class GetDraftPolicyRequest(_message.Message):
    __slots__ = ("name", "status_filter")
    NAME_FIELD_NUMBER: _ClassVar[int]
    STATUS_FILTER_FIELD_NUMBER: _ClassVar[int]
    name: str
    status_filter: str
    def __init__(self, name: _Optional[str] = ..., status_filter: _Optional[str] = ...) -> None: ...

class GetDraftPolicyResponse(_message.Message):
    __slots__ = ("chunks", "rolling_summary", "draft_version", "last_analyzed_at_ms")
    CHUNKS_FIELD_NUMBER: _ClassVar[int]
    ROLLING_SUMMARY_FIELD_NUMBER: _ClassVar[int]
    DRAFT_VERSION_FIELD_NUMBER: _ClassVar[int]
    LAST_ANALYZED_AT_MS_FIELD_NUMBER: _ClassVar[int]
    chunks: _containers.RepeatedCompositeFieldContainer[PolicyChunk]
    rolling_summary: str
    draft_version: int
    last_analyzed_at_ms: int
    def __init__(
        self,
        chunks: _Optional[_Iterable[_Union[PolicyChunk, _Mapping]]] = ...,
        rolling_summary: _Optional[str] = ...,
        draft_version: _Optional[int] = ...,
        last_analyzed_at_ms: _Optional[int] = ...,
    ) -> None: ...

class ApproveDraftChunkRequest(_message.Message):
    __slots__ = ("name", "chunk_id")
    NAME_FIELD_NUMBER: _ClassVar[int]
    CHUNK_ID_FIELD_NUMBER: _ClassVar[int]
    name: str
    chunk_id: str
    def __init__(self, name: _Optional[str] = ..., chunk_id: _Optional[str] = ...) -> None: ...

class ApproveDraftChunkResponse(_message.Message):
    __slots__ = ("policy_version", "policy_hash")
    POLICY_VERSION_FIELD_NUMBER: _ClassVar[int]
    POLICY_HASH_FIELD_NUMBER: _ClassVar[int]
    policy_version: int
    policy_hash: str
    def __init__(
        self, policy_version: _Optional[int] = ..., policy_hash: _Optional[str] = ...
    ) -> None: ...

class RejectDraftChunkRequest(_message.Message):
    __slots__ = ("name", "chunk_id", "reason")
    NAME_FIELD_NUMBER: _ClassVar[int]
    CHUNK_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    name: str
    chunk_id: str
    reason: str
    def __init__(
        self,
        name: _Optional[str] = ...,
        chunk_id: _Optional[str] = ...,
        reason: _Optional[str] = ...,
    ) -> None: ...

class RejectDraftChunkResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ApproveAllDraftChunksRequest(_message.Message):
    __slots__ = ("name", "include_security_flagged")
    NAME_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_SECURITY_FLAGGED_FIELD_NUMBER: _ClassVar[int]
    name: str
    include_security_flagged: bool
    def __init__(
        self, name: _Optional[str] = ..., include_security_flagged: bool = ...
    ) -> None: ...

class ApproveAllDraftChunksResponse(_message.Message):
    __slots__ = ("policy_version", "policy_hash", "chunks_approved", "chunks_skipped")
    POLICY_VERSION_FIELD_NUMBER: _ClassVar[int]
    POLICY_HASH_FIELD_NUMBER: _ClassVar[int]
    CHUNKS_APPROVED_FIELD_NUMBER: _ClassVar[int]
    CHUNKS_SKIPPED_FIELD_NUMBER: _ClassVar[int]
    policy_version: int
    policy_hash: str
    chunks_approved: int
    chunks_skipped: int
    def __init__(
        self,
        policy_version: _Optional[int] = ...,
        policy_hash: _Optional[str] = ...,
        chunks_approved: _Optional[int] = ...,
        chunks_skipped: _Optional[int] = ...,
    ) -> None: ...

class EditDraftChunkRequest(_message.Message):
    __slots__ = ("name", "chunk_id", "proposed_rule")
    NAME_FIELD_NUMBER: _ClassVar[int]
    CHUNK_ID_FIELD_NUMBER: _ClassVar[int]
    PROPOSED_RULE_FIELD_NUMBER: _ClassVar[int]
    name: str
    chunk_id: str
    proposed_rule: _sandbox_pb2.NetworkPolicyRule
    def __init__(
        self,
        name: _Optional[str] = ...,
        chunk_id: _Optional[str] = ...,
        proposed_rule: _Optional[_Union[_sandbox_pb2.NetworkPolicyRule, _Mapping]] = ...,
    ) -> None: ...

class EditDraftChunkResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class UndoDraftChunkRequest(_message.Message):
    __slots__ = ("name", "chunk_id")
    NAME_FIELD_NUMBER: _ClassVar[int]
    CHUNK_ID_FIELD_NUMBER: _ClassVar[int]
    name: str
    chunk_id: str
    def __init__(self, name: _Optional[str] = ..., chunk_id: _Optional[str] = ...) -> None: ...

class UndoDraftChunkResponse(_message.Message):
    __slots__ = ("policy_version", "policy_hash")
    POLICY_VERSION_FIELD_NUMBER: _ClassVar[int]
    POLICY_HASH_FIELD_NUMBER: _ClassVar[int]
    policy_version: int
    policy_hash: str
    def __init__(
        self, policy_version: _Optional[int] = ..., policy_hash: _Optional[str] = ...
    ) -> None: ...

class ClearDraftChunksRequest(_message.Message):
    __slots__ = ("name",)
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: _Optional[str] = ...) -> None: ...

class ClearDraftChunksResponse(_message.Message):
    __slots__ = ("chunks_cleared",)
    CHUNKS_CLEARED_FIELD_NUMBER: _ClassVar[int]
    chunks_cleared: int
    def __init__(self, chunks_cleared: _Optional[int] = ...) -> None: ...

class GetDraftHistoryRequest(_message.Message):
    __slots__ = ("name",)
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: _Optional[str] = ...) -> None: ...

class DraftHistoryEntry(_message.Message):
    __slots__ = ("timestamp_ms", "event_type", "description", "chunk_id")
    TIMESTAMP_MS_FIELD_NUMBER: _ClassVar[int]
    EVENT_TYPE_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    CHUNK_ID_FIELD_NUMBER: _ClassVar[int]
    timestamp_ms: int
    event_type: str
    description: str
    chunk_id: str
    def __init__(
        self,
        timestamp_ms: _Optional[int] = ...,
        event_type: _Optional[str] = ...,
        description: _Optional[str] = ...,
        chunk_id: _Optional[str] = ...,
    ) -> None: ...

class GetDraftHistoryResponse(_message.Message):
    __slots__ = ("entries",)
    ENTRIES_FIELD_NUMBER: _ClassVar[int]
    entries: _containers.RepeatedCompositeFieldContainer[DraftHistoryEntry]
    def __init__(
        self, entries: _Optional[_Iterable[_Union[DraftHistoryEntry, _Mapping]]] = ...
    ) -> None: ...
