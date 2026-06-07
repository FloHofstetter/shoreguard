"""Integration tests for service-routing API routes."""

from __future__ import annotations

from shoreguard.exceptions import NotFoundError

GW = "test"
BASE = f"/api/gateways/{GW}/services"

_ENDPOINT = {
    "id": "ep-1",
    "created_at_ms": 123,
    "sandbox_id": "sid-1",
    "sandbox_name": "sb1",
    "service_name": "web",
    "target_port": 8080,
    "domain": True,
    "url": "https://web.local",
}


async def test_list_services(api_client, mock_client):
    """GET /services returns the endpoint list."""
    mock_client.services.list.return_value = [_ENDPOINT]

    resp = await api_client.get(BASE)

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["services"]) == 1
    assert data["services"][0]["url"] == "https://web.local"


async def test_list_services_filter_sandbox(api_client, mock_client):
    """GET /services?sandbox=sb1 forwards the sandbox filter."""
    mock_client.services.list.return_value = []

    resp = await api_client.get(f"{BASE}?sandbox=sb1")

    assert resp.status_code == 200
    _, kwargs = mock_client.services.list.call_args
    assert kwargs["sandbox"] == "sb1"


async def test_expose_service(api_client, mock_client):
    """POST /services exposes a service and returns 201."""
    mock_client.services.expose.return_value = _ENDPOINT

    resp = await api_client.post(
        BASE,
        json={"sandbox": "sb1", "service": "web", "target_port": 8080, "domain": True},
    )

    assert resp.status_code == 201
    assert resp.json()["service_name"] == "web"
    _, kwargs = mock_client.services.expose.call_args
    assert kwargs["target_port"] == 8080
    assert kwargs["domain"] is True


async def test_expose_service_rejects_bad_port(api_client):
    """POST /services 422s on an out-of-range port."""
    resp = await api_client.post(
        BASE,
        json={"sandbox": "sb1", "service": "web", "target_port": 70000},
    )

    assert resp.status_code == 422


async def test_get_service(api_client, mock_client):
    """GET /services/{sandbox}/{service} returns one endpoint."""
    mock_client.services.get.return_value = _ENDPOINT

    resp = await api_client.get(f"{BASE}/sb1/web")

    assert resp.status_code == 200
    assert resp.json()["sandbox_name"] == "sb1"


async def test_delete_service(api_client, mock_client):
    """DELETE /services/{sandbox}/{service} removes an endpoint."""
    mock_client.services.delete.return_value = True

    resp = await api_client.delete(f"{BASE}/sb1/web")

    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


async def test_get_service_not_found(api_client, mock_client):
    """GET /services/{sandbox}/{service} 404s for an unknown endpoint."""
    mock_client.services.get.side_effect = NotFoundError("no such service")

    resp = await api_client.get(f"{BASE}/sb1/missing")

    assert resp.status_code == 404
