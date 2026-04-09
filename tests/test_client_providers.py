"""Unit tests for ProviderManager — FakeStub pattern."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from shoreguard.client._proto import datamodel_pb2
from shoreguard.client.providers import ProviderManager, _provider_to_dict


def _make_provider(name: str = "prov-1", ptype: str = "anthropic") -> datamodel_pb2.Provider:
    return datamodel_pb2.Provider(id="id-1", name=name, type=ptype)


class _FakeStub:
    def __init__(self) -> None:
        self.request = None

    def ListProviders(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(providers=[_make_provider("p1"), _make_provider("p2")])

    def GetProvider(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(provider=_make_provider("prov-1"))

    def CreateProvider(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(provider=_make_provider("new-prov"))

    def UpdateProvider(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(provider=_make_provider("prov-1", "openai"))

    def DeleteProvider(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(deleted=True)


@pytest.fixture
def stub():
    return _FakeStub()


@pytest.fixture
def mgr(stub):
    m = object.__new__(ProviderManager)
    m._stub = stub
    m._timeout = 30.0
    return m


def test_list_sends_limit_offset(mgr, stub):
    """list() forwards limit/offset and returns converted dicts."""
    result = mgr.list(limit=50, offset=5)

    assert stub.request.limit == 50
    assert stub.request.offset == 5
    assert len(result) == 2
    assert result[0]["name"] == "p1"


def test_get_sends_name(mgr, stub):
    """get() sends provider name and returns dict."""
    result = mgr.get("prov-1")

    assert stub.request.name == "prov-1"
    assert result["name"] == "prov-1"
    assert result["type"] == "anthropic"


def test_create_sends_credentials(mgr, stub):
    """create() builds Provider proto with credentials and sends it."""
    result = mgr.create(
        name="new-prov",
        provider_type="anthropic",
        credentials={"ANTHROPIC_API_KEY": "sk-xxx"},
    )

    assert stub.request.provider.name == "new-prov"
    assert stub.request.provider.type == "anthropic"
    assert dict(stub.request.provider.credentials) == {"ANTHROPIC_API_KEY": "sk-xxx"}
    assert result["name"] == "new-prov"


def test_update_sends_name_and_type(mgr, stub):
    """update() builds Provider proto with new type and sends it."""
    result = mgr.update(name="prov-1", provider_type="openai")

    assert stub.request.provider.name == "prov-1"
    assert stub.request.provider.type == "openai"
    assert result["type"] == "openai"


def test_delete_returns_bool(mgr, stub):
    """delete() sends name and returns bool."""
    result = mgr.delete("prov-1")

    assert stub.request.name == "prov-1"
    assert result is True


# ─── Mutation-killing tests ──────────────────────────────────────────────────


def test_provider_to_dict_all_fields():
    """Assert all fields: id, name, type, credentials, config."""
    provider = datamodel_pb2.Provider(
        id="id-42",
        name="my-prov",
        type="openai",
        credentials={"API_KEY": "sk-123"},
        config={"region": "us-east-1"},
    )
    result = _provider_to_dict(provider)
    assert result["id"] == "id-42"
    assert result["name"] == "my-prov"
    assert result["type"] == "openai"
    assert result["credentials"] == {"API_KEY": "sk-123"}
    assert result["config"] == {"region": "us-east-1"}


def test_create_with_config(mgr, stub):
    """create() forwards config parameter."""
    result = mgr.create(
        name="new-prov",
        provider_type="anthropic",
        credentials={"KEY": "val"},
        config={"region": "eu"},
    )
    assert dict(stub.request.provider.config) == {"region": "eu"}
    assert dict(stub.request.provider.credentials) == {"KEY": "val"}
    assert result["name"] == "new-prov"


def test_update_with_credentials_and_config(mgr, stub):
    """update() forwards credentials and config parameters."""
    mgr.update(
        name="prov-1",
        provider_type="openai",
        credentials={"KEY": "new-val"},
        config={"endpoint": "https://api.example.com"},
    )
    assert dict(stub.request.provider.credentials) == {"KEY": "new-val"}
    assert dict(stub.request.provider.config) == {"endpoint": "https://api.example.com"}


def test_list_returns_multiple_providers_with_correct_fields(mgr, stub):
    """list() returns multiple providers with all expected fields."""
    result = mgr.list()
    assert len(result) == 2
    for p in result:
        assert "id" in p
        assert "name" in p
        assert "type" in p
        assert "credentials" in p
        assert "config" in p
    assert result[0]["name"] == "p1"
    assert result[1]["name"] == "p2"


def test_delete_returns_false():
    """delete() returns False when server responds with deleted=False."""

    class _StubDeleteFalse(_FakeStub):
        def DeleteProvider(self, req, timeout=None):
            self.request = req
            return SimpleNamespace(deleted=False)

    s = _StubDeleteFalse()
    m = object.__new__(ProviderManager)
    m._stub = s  # type: ignore[assignment]
    m._timeout = 30.0

    result = m.delete("prov-1")
    assert result is False


# ─── Additional mutation-killing tests ──────────────────────────────────────


class TestProviderToDictMutations:
    """Kill mutations in _provider_to_dict field mappings."""

    def test_empty_credentials_and_config(self):
        provider = datamodel_pb2.Provider(id="i", name="n", type="t")
        result = _provider_to_dict(provider)
        assert result["credentials"] == {}
        assert result["config"] == {}

    def test_each_field_maps_correctly(self):
        provider = datamodel_pb2.Provider(
            id="ID",
            name="NAME",
            type="TYPE",
            credentials={"k1": "v1"},
            config={"k2": "v2"},
        )
        result = _provider_to_dict(provider)
        assert result["id"] == "ID"
        assert result["name"] == "NAME"
        assert result["type"] == "TYPE"
        assert result["credentials"] == {"k1": "v1"}
        assert result["config"] == {"k2": "v2"}

    def test_dict_keys_exact(self):
        provider = datamodel_pb2.Provider(id="i", name="n", type="t")
        result = _provider_to_dict(provider)
        assert set(result.keys()) == {"id", "name", "type", "credentials", "config"}


class TestProviderManagerMutations:
    """Kill mutations in ProviderManager method argument passing."""

    def test_list_default_params(self):
        class _Stub(_FakeStub):
            def ListProviders(self, req, timeout=None):
                self.request = req
                return SimpleNamespace(providers=[])

        s = _Stub()
        m = object.__new__(ProviderManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        m.list()
        assert s.request.limit == 100
        assert s.request.offset == 0

    def test_list_uses_timeout(self):
        class _Stub(_FakeStub):
            def ListProviders(self, req, timeout=None):
                self.timeout = timeout
                return SimpleNamespace(providers=[])

        s = _Stub()
        m = object.__new__(ProviderManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 77.0
        m.list()
        assert s.timeout == 77.0

    def test_get_uses_timeout(self):
        class _Stub(_FakeStub):
            def GetProvider(self, req, timeout=None):
                self.timeout = timeout
                return SimpleNamespace(provider=_make_provider())

        s = _Stub()
        m = object.__new__(ProviderManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 42.0
        m.get("p")
        assert s.timeout == 42.0

    def test_create_no_credentials_empty(self):
        class _Stub(_FakeStub):
            def CreateProvider(self, req, timeout=None):
                self.request = req
                return SimpleNamespace(provider=_make_provider("p"))

        s = _Stub()
        m = object.__new__(ProviderManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        m.create(name="p", provider_type="t")
        assert dict(s.request.provider.credentials) == {}
        assert dict(s.request.provider.config) == {}

    def test_create_uses_timeout(self):
        class _Stub(_FakeStub):
            def CreateProvider(self, req, timeout=None):
                self.timeout = timeout
                return SimpleNamespace(provider=_make_provider("p"))

        s = _Stub()
        m = object.__new__(ProviderManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 99.0
        m.create(name="p", provider_type="t")
        assert s.timeout == 99.0

    def test_update_no_credentials_empty(self):
        class _Stub(_FakeStub):
            def UpdateProvider(self, req, timeout=None):
                self.request = req
                return SimpleNamespace(provider=_make_provider("p"))

        s = _Stub()
        m = object.__new__(ProviderManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        m.update(name="p")
        assert dict(s.request.provider.credentials) == {}
        assert dict(s.request.provider.config) == {}

    def test_update_default_type_empty(self):
        class _Stub(_FakeStub):
            def UpdateProvider(self, req, timeout=None):
                self.request = req
                return SimpleNamespace(provider=_make_provider("p"))

        s = _Stub()
        m = object.__new__(ProviderManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        m.update(name="p")
        assert s.request.provider.type == ""

    def test_delete_uses_timeout(self):
        class _Stub(_FakeStub):
            def DeleteProvider(self, req, timeout=None):
                self.timeout = timeout
                return SimpleNamespace(deleted=True)

        s = _Stub()
        m = object.__new__(ProviderManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 88.0
        m.delete("p")
        assert s.timeout == 88.0

    def test_delete_sends_name_in_request(self):
        class _Stub(_FakeStub):
            def DeleteProvider(self, req, timeout=None):
                self.request = req
                return SimpleNamespace(deleted=True)

        s = _Stub()
        m = object.__new__(ProviderManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        m.delete("my-prov")
        assert s.request.name == "my-prov"

    def test_create_provider_type_set(self):
        class _Stub(_FakeStub):
            def CreateProvider(self, req, timeout=None):
                self.request = req
                return SimpleNamespace(provider=_make_provider("p"))

        s = _Stub()
        m = object.__new__(ProviderManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        m.create(name="p", provider_type="openai")
        assert s.request.provider.type == "openai"
        assert s.request.provider.name == "p"
