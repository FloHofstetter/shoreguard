from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ObjectMeta(_message.Message):
    __slots__ = ("id", "name", "created_at_ms", "labels", "resource_version")
    class LabelsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_MS_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    RESOURCE_VERSION_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    created_at_ms: int
    labels: _containers.ScalarMap[str, str]
    resource_version: int
    def __init__(
        self,
        id: _Optional[str] = ...,
        name: _Optional[str] = ...,
        created_at_ms: _Optional[int] = ...,
        labels: _Optional[_Mapping[str, str]] = ...,
        resource_version: _Optional[int] = ...,
    ) -> None: ...

class Provider(_message.Message):
    __slots__ = ("metadata", "type", "credentials", "config", "credential_expires_at_ms")
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

    class CredentialExpiresAtMsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...

    METADATA_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    CREDENTIALS_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_EXPIRES_AT_MS_FIELD_NUMBER: _ClassVar[int]
    metadata: ObjectMeta
    type: str
    credentials: _containers.ScalarMap[str, str]
    config: _containers.ScalarMap[str, str]
    credential_expires_at_ms: _containers.ScalarMap[str, int]
    def __init__(
        self,
        metadata: _Optional[_Union[ObjectMeta, _Mapping]] = ...,
        type: _Optional[str] = ...,
        credentials: _Optional[_Mapping[str, str]] = ...,
        config: _Optional[_Mapping[str, str]] = ...,
        credential_expires_at_ms: _Optional[_Mapping[str, int]] = ...,
    ) -> None: ...
