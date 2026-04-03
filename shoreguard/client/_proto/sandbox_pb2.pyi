from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SettingScope(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SETTING_SCOPE_UNSPECIFIED: _ClassVar[SettingScope]
    SETTING_SCOPE_SANDBOX: _ClassVar[SettingScope]
    SETTING_SCOPE_GLOBAL: _ClassVar[SettingScope]

class PolicySource(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    POLICY_SOURCE_UNSPECIFIED: _ClassVar[PolicySource]
    POLICY_SOURCE_SANDBOX: _ClassVar[PolicySource]
    POLICY_SOURCE_GLOBAL: _ClassVar[PolicySource]

SETTING_SCOPE_UNSPECIFIED: SettingScope
SETTING_SCOPE_SANDBOX: SettingScope
SETTING_SCOPE_GLOBAL: SettingScope
POLICY_SOURCE_UNSPECIFIED: PolicySource
POLICY_SOURCE_SANDBOX: PolicySource
POLICY_SOURCE_GLOBAL: PolicySource

class SandboxPolicy(_message.Message):
    __slots__ = ("version", "filesystem", "landlock", "process", "network_policies")
    class NetworkPoliciesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: NetworkPolicyRule
        def __init__(
            self,
            key: _Optional[str] = ...,
            value: _Optional[_Union[NetworkPolicyRule, _Mapping]] = ...,
        ) -> None: ...

    VERSION_FIELD_NUMBER: _ClassVar[int]
    FILESYSTEM_FIELD_NUMBER: _ClassVar[int]
    LANDLOCK_FIELD_NUMBER: _ClassVar[int]
    PROCESS_FIELD_NUMBER: _ClassVar[int]
    NETWORK_POLICIES_FIELD_NUMBER: _ClassVar[int]
    version: int
    filesystem: FilesystemPolicy
    landlock: LandlockPolicy
    process: ProcessPolicy
    network_policies: _containers.MessageMap[str, NetworkPolicyRule]
    def __init__(
        self,
        version: _Optional[int] = ...,
        filesystem: _Optional[_Union[FilesystemPolicy, _Mapping]] = ...,
        landlock: _Optional[_Union[LandlockPolicy, _Mapping]] = ...,
        process: _Optional[_Union[ProcessPolicy, _Mapping]] = ...,
        network_policies: _Optional[_Mapping[str, NetworkPolicyRule]] = ...,
    ) -> None: ...

class FilesystemPolicy(_message.Message):
    __slots__ = ("include_workdir", "read_only", "read_write")
    INCLUDE_WORKDIR_FIELD_NUMBER: _ClassVar[int]
    READ_ONLY_FIELD_NUMBER: _ClassVar[int]
    READ_WRITE_FIELD_NUMBER: _ClassVar[int]
    include_workdir: bool
    read_only: _containers.RepeatedScalarFieldContainer[str]
    read_write: _containers.RepeatedScalarFieldContainer[str]
    def __init__(
        self,
        include_workdir: bool = ...,
        read_only: _Optional[_Iterable[str]] = ...,
        read_write: _Optional[_Iterable[str]] = ...,
    ) -> None: ...

class LandlockPolicy(_message.Message):
    __slots__ = ("compatibility",)
    COMPATIBILITY_FIELD_NUMBER: _ClassVar[int]
    compatibility: str
    def __init__(self, compatibility: _Optional[str] = ...) -> None: ...

class ProcessPolicy(_message.Message):
    __slots__ = ("run_as_user", "run_as_group")
    RUN_AS_USER_FIELD_NUMBER: _ClassVar[int]
    RUN_AS_GROUP_FIELD_NUMBER: _ClassVar[int]
    run_as_user: str
    run_as_group: str
    def __init__(
        self, run_as_user: _Optional[str] = ..., run_as_group: _Optional[str] = ...
    ) -> None: ...

class NetworkPolicyRule(_message.Message):
    __slots__ = ("name", "endpoints", "binaries")
    NAME_FIELD_NUMBER: _ClassVar[int]
    ENDPOINTS_FIELD_NUMBER: _ClassVar[int]
    BINARIES_FIELD_NUMBER: _ClassVar[int]
    name: str
    endpoints: _containers.RepeatedCompositeFieldContainer[NetworkEndpoint]
    binaries: _containers.RepeatedCompositeFieldContainer[NetworkBinary]
    def __init__(
        self,
        name: _Optional[str] = ...,
        endpoints: _Optional[_Iterable[_Union[NetworkEndpoint, _Mapping]]] = ...,
        binaries: _Optional[_Iterable[_Union[NetworkBinary, _Mapping]]] = ...,
    ) -> None: ...

class NetworkEndpoint(_message.Message):
    __slots__ = (
        "host",
        "port",
        "protocol",
        "tls",
        "enforcement",
        "access",
        "rules",
        "allowed_ips",
        "ports",
    )
    HOST_FIELD_NUMBER: _ClassVar[int]
    PORT_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_FIELD_NUMBER: _ClassVar[int]
    TLS_FIELD_NUMBER: _ClassVar[int]
    ENFORCEMENT_FIELD_NUMBER: _ClassVar[int]
    ACCESS_FIELD_NUMBER: _ClassVar[int]
    RULES_FIELD_NUMBER: _ClassVar[int]
    ALLOWED_IPS_FIELD_NUMBER: _ClassVar[int]
    PORTS_FIELD_NUMBER: _ClassVar[int]
    host: str
    port: int
    protocol: str
    tls: str
    enforcement: str
    access: str
    rules: _containers.RepeatedCompositeFieldContainer[L7Rule]
    allowed_ips: _containers.RepeatedScalarFieldContainer[str]
    ports: _containers.RepeatedScalarFieldContainer[int]
    def __init__(
        self,
        host: _Optional[str] = ...,
        port: _Optional[int] = ...,
        protocol: _Optional[str] = ...,
        tls: _Optional[str] = ...,
        enforcement: _Optional[str] = ...,
        access: _Optional[str] = ...,
        rules: _Optional[_Iterable[_Union[L7Rule, _Mapping]]] = ...,
        allowed_ips: _Optional[_Iterable[str]] = ...,
        ports: _Optional[_Iterable[int]] = ...,
    ) -> None: ...

class L7Rule(_message.Message):
    __slots__ = ("allow",)
    ALLOW_FIELD_NUMBER: _ClassVar[int]
    allow: L7Allow
    def __init__(self, allow: _Optional[_Union[L7Allow, _Mapping]] = ...) -> None: ...

class L7Allow(_message.Message):
    __slots__ = ("method", "path", "command", "query")
    class QueryEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: L7QueryMatcher
        def __init__(
            self,
            key: _Optional[str] = ...,
            value: _Optional[_Union[L7QueryMatcher, _Mapping]] = ...,
        ) -> None: ...

    METHOD_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    COMMAND_FIELD_NUMBER: _ClassVar[int]
    QUERY_FIELD_NUMBER: _ClassVar[int]
    method: str
    path: str
    command: str
    query: _containers.MessageMap[str, L7QueryMatcher]
    def __init__(
        self,
        method: _Optional[str] = ...,
        path: _Optional[str] = ...,
        command: _Optional[str] = ...,
        query: _Optional[_Mapping[str, L7QueryMatcher]] = ...,
    ) -> None: ...

class L7QueryMatcher(_message.Message):
    __slots__ = ("glob", "any")
    GLOB_FIELD_NUMBER: _ClassVar[int]
    ANY_FIELD_NUMBER: _ClassVar[int]
    glob: str
    any: _containers.RepeatedScalarFieldContainer[str]
    def __init__(
        self, glob: _Optional[str] = ..., any: _Optional[_Iterable[str]] = ...
    ) -> None: ...

class NetworkBinary(_message.Message):
    __slots__ = ("path", "harness")
    PATH_FIELD_NUMBER: _ClassVar[int]
    HARNESS_FIELD_NUMBER: _ClassVar[int]
    path: str
    harness: bool
    def __init__(self, path: _Optional[str] = ..., harness: bool = ...) -> None: ...

class GetSandboxConfigRequest(_message.Message):
    __slots__ = ("sandbox_id",)
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    def __init__(self, sandbox_id: _Optional[str] = ...) -> None: ...

class GetGatewayConfigRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GetGatewayConfigResponse(_message.Message):
    __slots__ = ("settings", "settings_revision")
    class SettingsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: SettingValue
        def __init__(
            self, key: _Optional[str] = ..., value: _Optional[_Union[SettingValue, _Mapping]] = ...
        ) -> None: ...

    SETTINGS_FIELD_NUMBER: _ClassVar[int]
    SETTINGS_REVISION_FIELD_NUMBER: _ClassVar[int]
    settings: _containers.MessageMap[str, SettingValue]
    settings_revision: int
    def __init__(
        self,
        settings: _Optional[_Mapping[str, SettingValue]] = ...,
        settings_revision: _Optional[int] = ...,
    ) -> None: ...

class SettingValue(_message.Message):
    __slots__ = ("string_value", "bool_value", "int_value", "bytes_value")
    STRING_VALUE_FIELD_NUMBER: _ClassVar[int]
    BOOL_VALUE_FIELD_NUMBER: _ClassVar[int]
    INT_VALUE_FIELD_NUMBER: _ClassVar[int]
    BYTES_VALUE_FIELD_NUMBER: _ClassVar[int]
    string_value: str
    bool_value: bool
    int_value: int
    bytes_value: bytes
    def __init__(
        self,
        string_value: _Optional[str] = ...,
        bool_value: bool = ...,
        int_value: _Optional[int] = ...,
        bytes_value: _Optional[bytes] = ...,
    ) -> None: ...

class EffectiveSetting(_message.Message):
    __slots__ = ("value", "scope")
    VALUE_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    value: SettingValue
    scope: SettingScope
    def __init__(
        self,
        value: _Optional[_Union[SettingValue, _Mapping]] = ...,
        scope: _Optional[_Union[SettingScope, str]] = ...,
    ) -> None: ...

class GetSandboxConfigResponse(_message.Message):
    __slots__ = (
        "policy",
        "version",
        "policy_hash",
        "settings",
        "config_revision",
        "policy_source",
        "global_policy_version",
    )
    class SettingsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: EffectiveSetting
        def __init__(
            self,
            key: _Optional[str] = ...,
            value: _Optional[_Union[EffectiveSetting, _Mapping]] = ...,
        ) -> None: ...

    POLICY_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    POLICY_HASH_FIELD_NUMBER: _ClassVar[int]
    SETTINGS_FIELD_NUMBER: _ClassVar[int]
    CONFIG_REVISION_FIELD_NUMBER: _ClassVar[int]
    POLICY_SOURCE_FIELD_NUMBER: _ClassVar[int]
    GLOBAL_POLICY_VERSION_FIELD_NUMBER: _ClassVar[int]
    policy: SandboxPolicy
    version: int
    policy_hash: str
    settings: _containers.MessageMap[str, EffectiveSetting]
    config_revision: int
    policy_source: PolicySource
    global_policy_version: int
    def __init__(
        self,
        policy: _Optional[_Union[SandboxPolicy, _Mapping]] = ...,
        version: _Optional[int] = ...,
        policy_hash: _Optional[str] = ...,
        settings: _Optional[_Mapping[str, EffectiveSetting]] = ...,
        config_revision: _Optional[int] = ...,
        policy_source: _Optional[_Union[PolicySource, str]] = ...,
        global_policy_version: _Optional[int] = ...,
    ) -> None: ...
