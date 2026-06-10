"""Unit tests for ServiceManager — FakeStub pattern."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from shoreguard.client._proto import datamodel_pb2, openshell_pb2
from shoreguard.client.services import ServiceManager, _service_endpoint_to_dict


def _make_endpoint_response(sandbox: str = "sb1", service: str = "web", port: int = 8080):
    return openshell_pb2.ServiceEndpointResponse(
        endpoint=openshell_pb2.ServiceEndpoint(
            metadata=datamodel_pb2.ObjectMeta(id="ep-1", name="web", created_at_ms=123),
            sandbox_id="sid-1",
            sandbox_name=sandbox,
            service_name=service,
            target_port=port,
            domain=True,
        ),
        url=f"https://{service}.local",
    )


class _FakeStub:
    def __init__(self) -> None:
        self.request = None

    async def ListServices(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(
            services=[_make_endpoint_response("sb1", "web"), _make_endpoint_response("sb1", "api")]
        )

    async def GetService(self, req, timeout=None):
        self.request = req
        return _make_endpoint_response(req.sandbox, req.service)

    async def ExposeService(self, req, timeout=None):
        self.request = req
        return _make_endpoint_response(req.sandbox, req.service, req.target_port)

    async def DeleteService(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(deleted=True)


@pytest.fixture
def stub():
    return _FakeStub()


@pytest.fixture
def mgr(stub):
    m = object.__new__(ServiceManager)
    m._stub = stub
    m._timeout = 30.0
    return m


async def test_endpoint_to_dict_all_fields():
    """_service_endpoint_to_dict flattens endpoint and hoists url."""
    result = _service_endpoint_to_dict(_make_endpoint_response("sbX", "svcX", 9000))
    assert result == {
        "id": "ep-1",
        "created_at_ms": 123,
        "sandbox_id": "sid-1",
        "sandbox_name": "sbX",
        "service_name": "svcX",
        "target_port": 9000,
        "domain": True,
        "url": "https://svcX.local",
    }


async def test_list_sends_filters(mgr, stub):
    """list() forwards sandbox/limit/offset and converts each entry."""
    result = await mgr.list(sandbox="sb1", limit=10, offset=2)
    assert stub.request.sandbox == "sb1"
    assert stub.request.limit == 10
    assert stub.request.offset == 2
    assert [r["service_name"] for r in result] == ["web", "api"]


async def test_get_sends_sandbox_service(mgr, stub):
    """get() forwards sandbox/service and returns the endpoint dict."""
    result = await mgr.get(sandbox="sb1", service="web")
    assert stub.request.sandbox == "sb1"
    assert stub.request.service == "web"
    assert result["service_name"] == "web"
    assert result["url"] == "https://web.local"


async def test_expose_sends_all_fields(mgr, stub):
    """expose() forwards sandbox/service/target_port/domain."""
    result = await mgr.expose(sandbox="sb1", service="api", target_port=3000, domain=True)
    assert stub.request.sandbox == "sb1"
    assert stub.request.service == "api"
    assert stub.request.target_port == 3000
    assert stub.request.domain is True
    assert result["target_port"] == 3000


async def test_expose_domain_defaults_false(mgr, stub):
    """expose() sends domain=False by default."""
    await mgr.expose(sandbox="sb1", service="api", target_port=3000)
    assert stub.request.domain is False


async def test_delete_returns_bool(mgr, stub):
    """delete() forwards sandbox/service and returns the deleted flag."""
    result = await mgr.delete(sandbox="sb1", service="web")
    assert stub.request.sandbox == "sb1"
    assert stub.request.service == "web"
    assert result is True


async def test_delete_false_when_absent():
    """delete() returns False when the server reports deleted=False."""

    class _Stub(_FakeStub):
        async def DeleteService(self, req, timeout=None):
            self.request = req
            return SimpleNamespace(deleted=False)

    m = object.__new__(ServiceManager)
    m._stub = _Stub()  # type: ignore[assignment]
    m._timeout = 30.0
    assert await m.delete(sandbox="sb1", service="nope") is False
