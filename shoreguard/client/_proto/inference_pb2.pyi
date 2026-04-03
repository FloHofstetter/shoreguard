from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ClusterInferenceConfig(_message.Message):
    __slots__ = ("provider_name", "model_id", "timeout_secs")
    PROVIDER_NAME_FIELD_NUMBER: _ClassVar[int]
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_SECS_FIELD_NUMBER: _ClassVar[int]
    provider_name: str
    model_id: str
    timeout_secs: int
    def __init__(
        self,
        provider_name: _Optional[str] = ...,
        model_id: _Optional[str] = ...,
        timeout_secs: _Optional[int] = ...,
    ) -> None: ...

class InferenceRoute(_message.Message):
    __slots__ = ("id", "config", "name", "version")
    ID_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    id: str
    config: ClusterInferenceConfig
    name: str
    version: int
    def __init__(
        self,
        id: _Optional[str] = ...,
        config: _Optional[_Union[ClusterInferenceConfig, _Mapping]] = ...,
        name: _Optional[str] = ...,
        version: _Optional[int] = ...,
    ) -> None: ...

class SetClusterInferenceRequest(_message.Message):
    __slots__ = ("provider_name", "model_id", "route_name", "verify", "no_verify", "timeout_secs")
    PROVIDER_NAME_FIELD_NUMBER: _ClassVar[int]
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    ROUTE_NAME_FIELD_NUMBER: _ClassVar[int]
    VERIFY_FIELD_NUMBER: _ClassVar[int]
    NO_VERIFY_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_SECS_FIELD_NUMBER: _ClassVar[int]
    provider_name: str
    model_id: str
    route_name: str
    verify: bool
    no_verify: bool
    timeout_secs: int
    def __init__(
        self,
        provider_name: _Optional[str] = ...,
        model_id: _Optional[str] = ...,
        route_name: _Optional[str] = ...,
        verify: bool = ...,
        no_verify: bool = ...,
        timeout_secs: _Optional[int] = ...,
    ) -> None: ...

class ValidatedEndpoint(_message.Message):
    __slots__ = ("url", "protocol")
    URL_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_FIELD_NUMBER: _ClassVar[int]
    url: str
    protocol: str
    def __init__(self, url: _Optional[str] = ..., protocol: _Optional[str] = ...) -> None: ...

class SetClusterInferenceResponse(_message.Message):
    __slots__ = (
        "provider_name",
        "model_id",
        "version",
        "route_name",
        "validation_performed",
        "validated_endpoints",
        "timeout_secs",
    )
    PROVIDER_NAME_FIELD_NUMBER: _ClassVar[int]
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    ROUTE_NAME_FIELD_NUMBER: _ClassVar[int]
    VALIDATION_PERFORMED_FIELD_NUMBER: _ClassVar[int]
    VALIDATED_ENDPOINTS_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_SECS_FIELD_NUMBER: _ClassVar[int]
    provider_name: str
    model_id: str
    version: int
    route_name: str
    validation_performed: bool
    validated_endpoints: _containers.RepeatedCompositeFieldContainer[ValidatedEndpoint]
    timeout_secs: int
    def __init__(
        self,
        provider_name: _Optional[str] = ...,
        model_id: _Optional[str] = ...,
        version: _Optional[int] = ...,
        route_name: _Optional[str] = ...,
        validation_performed: bool = ...,
        validated_endpoints: _Optional[_Iterable[_Union[ValidatedEndpoint, _Mapping]]] = ...,
        timeout_secs: _Optional[int] = ...,
    ) -> None: ...

class GetClusterInferenceRequest(_message.Message):
    __slots__ = ("route_name",)
    ROUTE_NAME_FIELD_NUMBER: _ClassVar[int]
    route_name: str
    def __init__(self, route_name: _Optional[str] = ...) -> None: ...

class GetClusterInferenceResponse(_message.Message):
    __slots__ = ("provider_name", "model_id", "version", "route_name", "timeout_secs")
    PROVIDER_NAME_FIELD_NUMBER: _ClassVar[int]
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    ROUTE_NAME_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_SECS_FIELD_NUMBER: _ClassVar[int]
    provider_name: str
    model_id: str
    version: int
    route_name: str
    timeout_secs: int
    def __init__(
        self,
        provider_name: _Optional[str] = ...,
        model_id: _Optional[str] = ...,
        version: _Optional[int] = ...,
        route_name: _Optional[str] = ...,
        timeout_secs: _Optional[int] = ...,
    ) -> None: ...

class GetInferenceBundleRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ResolvedRoute(_message.Message):
    __slots__ = (
        "name",
        "base_url",
        "protocols",
        "api_key",
        "model_id",
        "provider_type",
        "timeout_secs",
    )
    NAME_FIELD_NUMBER: _ClassVar[int]
    BASE_URL_FIELD_NUMBER: _ClassVar[int]
    PROTOCOLS_FIELD_NUMBER: _ClassVar[int]
    API_KEY_FIELD_NUMBER: _ClassVar[int]
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_TYPE_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_SECS_FIELD_NUMBER: _ClassVar[int]
    name: str
    base_url: str
    protocols: _containers.RepeatedScalarFieldContainer[str]
    api_key: str
    model_id: str
    provider_type: str
    timeout_secs: int
    def __init__(
        self,
        name: _Optional[str] = ...,
        base_url: _Optional[str] = ...,
        protocols: _Optional[_Iterable[str]] = ...,
        api_key: _Optional[str] = ...,
        model_id: _Optional[str] = ...,
        provider_type: _Optional[str] = ...,
        timeout_secs: _Optional[int] = ...,
    ) -> None: ...

class GetInferenceBundleResponse(_message.Message):
    __slots__ = ("routes", "revision", "generated_at_ms")
    ROUTES_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    GENERATED_AT_MS_FIELD_NUMBER: _ClassVar[int]
    routes: _containers.RepeatedCompositeFieldContainer[ResolvedRoute]
    revision: str
    generated_at_ms: int
    def __init__(
        self,
        routes: _Optional[_Iterable[_Union[ResolvedRoute, _Mapping]]] = ...,
        revision: _Optional[str] = ...,
        generated_at_ms: _Optional[int] = ...,
    ) -> None: ...
