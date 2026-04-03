from google.protobuf import struct_pb2 as _struct_pb2
from . import sandbox_pb2 as _sandbox_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SandboxPhase(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SANDBOX_PHASE_UNSPECIFIED: _ClassVar[SandboxPhase]
    SANDBOX_PHASE_PROVISIONING: _ClassVar[SandboxPhase]
    SANDBOX_PHASE_READY: _ClassVar[SandboxPhase]
    SANDBOX_PHASE_ERROR: _ClassVar[SandboxPhase]
    SANDBOX_PHASE_DELETING: _ClassVar[SandboxPhase]
    SANDBOX_PHASE_UNKNOWN: _ClassVar[SandboxPhase]

SANDBOX_PHASE_UNSPECIFIED: SandboxPhase
SANDBOX_PHASE_PROVISIONING: SandboxPhase
SANDBOX_PHASE_READY: SandboxPhase
SANDBOX_PHASE_ERROR: SandboxPhase
SANDBOX_PHASE_DELETING: SandboxPhase
SANDBOX_PHASE_UNKNOWN: SandboxPhase

class Sandbox(_message.Message):
    __slots__ = (
        "id",
        "name",
        "namespace",
        "spec",
        "status",
        "phase",
        "created_at_ms",
        "current_policy_version",
    )
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    SPEC_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    PHASE_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_MS_FIELD_NUMBER: _ClassVar[int]
    CURRENT_POLICY_VERSION_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    namespace: str
    spec: SandboxSpec
    status: SandboxStatus
    phase: SandboxPhase
    created_at_ms: int
    current_policy_version: int
    def __init__(
        self,
        id: _Optional[str] = ...,
        name: _Optional[str] = ...,
        namespace: _Optional[str] = ...,
        spec: _Optional[_Union[SandboxSpec, _Mapping]] = ...,
        status: _Optional[_Union[SandboxStatus, _Mapping]] = ...,
        phase: _Optional[_Union[SandboxPhase, str]] = ...,
        created_at_ms: _Optional[int] = ...,
        current_policy_version: _Optional[int] = ...,
    ) -> None: ...

class SandboxSpec(_message.Message):
    __slots__ = ("log_level", "environment", "template", "policy", "providers", "gpu")
    class EnvironmentEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

    LOG_LEVEL_FIELD_NUMBER: _ClassVar[int]
    ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    TEMPLATE_FIELD_NUMBER: _ClassVar[int]
    POLICY_FIELD_NUMBER: _ClassVar[int]
    PROVIDERS_FIELD_NUMBER: _ClassVar[int]
    GPU_FIELD_NUMBER: _ClassVar[int]
    log_level: str
    environment: _containers.ScalarMap[str, str]
    template: SandboxTemplate
    policy: _sandbox_pb2.SandboxPolicy
    providers: _containers.RepeatedScalarFieldContainer[str]
    gpu: bool
    def __init__(
        self,
        log_level: _Optional[str] = ...,
        environment: _Optional[_Mapping[str, str]] = ...,
        template: _Optional[_Union[SandboxTemplate, _Mapping]] = ...,
        policy: _Optional[_Union[_sandbox_pb2.SandboxPolicy, _Mapping]] = ...,
        providers: _Optional[_Iterable[str]] = ...,
        gpu: bool = ...,
    ) -> None: ...

class SandboxTemplate(_message.Message):
    __slots__ = (
        "image",
        "runtime_class_name",
        "agent_socket",
        "labels",
        "annotations",
        "environment",
        "resources",
        "volume_claim_templates",
    )
    class LabelsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

    class AnnotationsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

    class EnvironmentEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

    IMAGE_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_CLASS_NAME_FIELD_NUMBER: _ClassVar[int]
    AGENT_SOCKET_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    ANNOTATIONS_FIELD_NUMBER: _ClassVar[int]
    ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    RESOURCES_FIELD_NUMBER: _ClassVar[int]
    VOLUME_CLAIM_TEMPLATES_FIELD_NUMBER: _ClassVar[int]
    image: str
    runtime_class_name: str
    agent_socket: str
    labels: _containers.ScalarMap[str, str]
    annotations: _containers.ScalarMap[str, str]
    environment: _containers.ScalarMap[str, str]
    resources: _struct_pb2.Struct
    volume_claim_templates: _struct_pb2.Struct
    def __init__(
        self,
        image: _Optional[str] = ...,
        runtime_class_name: _Optional[str] = ...,
        agent_socket: _Optional[str] = ...,
        labels: _Optional[_Mapping[str, str]] = ...,
        annotations: _Optional[_Mapping[str, str]] = ...,
        environment: _Optional[_Mapping[str, str]] = ...,
        resources: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...,
        volume_claim_templates: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...,
    ) -> None: ...

class SandboxStatus(_message.Message):
    __slots__ = ("sandbox_name", "agent_pod", "agent_fd", "sandbox_fd", "conditions")
    SANDBOX_NAME_FIELD_NUMBER: _ClassVar[int]
    AGENT_POD_FIELD_NUMBER: _ClassVar[int]
    AGENT_FD_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FD_FIELD_NUMBER: _ClassVar[int]
    CONDITIONS_FIELD_NUMBER: _ClassVar[int]
    sandbox_name: str
    agent_pod: str
    agent_fd: str
    sandbox_fd: str
    conditions: _containers.RepeatedCompositeFieldContainer[SandboxCondition]
    def __init__(
        self,
        sandbox_name: _Optional[str] = ...,
        agent_pod: _Optional[str] = ...,
        agent_fd: _Optional[str] = ...,
        sandbox_fd: _Optional[str] = ...,
        conditions: _Optional[_Iterable[_Union[SandboxCondition, _Mapping]]] = ...,
    ) -> None: ...

class SandboxCondition(_message.Message):
    __slots__ = ("type", "status", "reason", "message", "last_transition_time")
    TYPE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    LAST_TRANSITION_TIME_FIELD_NUMBER: _ClassVar[int]
    type: str
    status: str
    reason: str
    message: str
    last_transition_time: str
    def __init__(
        self,
        type: _Optional[str] = ...,
        status: _Optional[str] = ...,
        reason: _Optional[str] = ...,
        message: _Optional[str] = ...,
        last_transition_time: _Optional[str] = ...,
    ) -> None: ...

class Provider(_message.Message):
    __slots__ = ("id", "name", "type", "credentials", "config")
    class CredentialsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

    class ConfigEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    CREDENTIALS_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    type: str
    credentials: _containers.ScalarMap[str, str]
    config: _containers.ScalarMap[str, str]
    def __init__(
        self,
        id: _Optional[str] = ...,
        name: _Optional[str] = ...,
        type: _Optional[str] = ...,
        credentials: _Optional[_Mapping[str, str]] = ...,
        config: _Optional[_Mapping[str, str]] = ...,
    ) -> None: ...
