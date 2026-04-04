"""Integration tests for sandbox template API routes."""

from __future__ import annotations


async def test_list_templates_route(api_client):
    """GET /api/sandbox-templates returns template list."""
    resp = await api_client.get("/api/sandbox-templates")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert any(t["name"] == "web-dev" for t in data)


async def test_get_template_route(api_client):
    """GET /api/sandbox-templates/{name} returns template data."""
    resp = await api_client.get("/api/sandbox-templates/web-dev")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "web-dev"
    assert "sandbox" in data


async def test_get_template_not_found(api_client):
    """GET /api/sandbox-templates/{name} returns 404 for unknown template."""
    resp = await api_client.get("/api/sandbox-templates/nonexistent-xyz")
    assert resp.status_code == 404


async def test_get_template_path_traversal_route(api_client):
    """GET /api/sandbox-templates/../../etc/passwd returns 404."""
    resp = await api_client.get("/api/sandbox-templates/..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code == 404
