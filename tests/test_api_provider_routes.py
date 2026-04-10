"""Integration tests for provider API routes."""

from __future__ import annotations

from shoreguard.exceptions import NotFoundError, SandboxError

GW = "test"
BASE = f"/api/gateways/{GW}/providers"


async def test_list_providers(api_client, mock_client):
    """GET /providers returns provider list."""
    mock_client.providers.list.return_value = [{"name": "p1"}, {"name": "p2"}]

    resp = await api_client.get(BASE)

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["total"] is None


async def test_create_provider(api_client, mock_client):
    """POST /providers creates a provider and returns 201."""
    mock_client.providers.create.return_value = {"name": "my-prov"}

    resp = await api_client.post(
        BASE,
        json={"name": "my-prov", "type": "anthropic", "api_key": "sk-xxx"},
    )

    assert resp.status_code == 201
    assert resp.json()["name"] == "my-prov"


async def test_get_provider(api_client, mock_client):
    """GET /providers/{name} returns provider data."""
    mock_client.providers.get.return_value = {"name": "my-prov", "type": "anthropic"}

    resp = await api_client.get(f"{BASE}/my-prov")

    assert resp.status_code == 200
    assert resp.json()["name"] == "my-prov"


async def test_update_provider(api_client, mock_client):
    """PUT /providers/{name} updates a provider."""
    mock_client.providers.update.return_value = {"name": "my-prov"}

    resp = await api_client.put(
        f"{BASE}/my-prov",
        json={"type": "openai"},
    )

    assert resp.status_code == 200


async def test_delete_provider(api_client, mock_client):
    """DELETE /providers/{name} deletes a provider."""
    mock_client.providers.delete.return_value = True

    resp = await api_client.delete(f"{BASE}/my-prov")

    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


async def test_list_provider_types(api_client):
    """GET /providers/types returns static list (no gateway needed)."""
    resp = await api_client.get(f"{BASE}/types")

    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_list_inference_providers(api_client):
    """GET /providers/inference-providers returns static list."""
    resp = await api_client.get(f"{BASE}/inference-providers")

    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_list_community_sandboxes(api_client):
    """GET /providers/community-sandboxes returns static list."""
    resp = await api_client.get(f"{BASE}/community-sandboxes")

    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_get_nonexistent_provider(api_client, mock_client):
    """GET /providers/{name} returns 404 for unknown provider."""
    mock_client.providers.get.side_effect = NotFoundError("Provider not found")

    resp = await api_client.get(f"{BASE}/nonexistent-prov")

    assert resp.status_code == 404


async def test_delete_nonexistent_provider(api_client, mock_client):
    """DELETE /providers/{name} returns 404 for unknown provider."""
    mock_client.providers.delete.side_effect = NotFoundError("Provider not found")

    resp = await api_client.delete(f"{BASE}/nonexistent-prov")

    assert resp.status_code == 404


async def test_update_nonexistent_provider(api_client, mock_client):
    """PUT /providers/{name} returns 404 for unknown provider."""
    mock_client.providers.update.side_effect = NotFoundError("Provider not found")

    resp = await api_client.put(f"{BASE}/nonexistent-prov", json={"type": "openai"})

    assert resp.status_code == 404


async def test_create_duplicate_provider(api_client, mock_client):
    """POST /providers returns 409 when provider already exists."""
    mock_client.providers.create.side_effect = SandboxError("already exists")

    resp = await api_client.post(
        BASE,
        json={"name": "dup-prov", "type": "anthropic", "api_key": "sk-xxx"},
    )

    assert resp.status_code == 409


# ── GET /{name}/env ─────────────────────────────────────────────────────────


async def test_get_provider_env_with_credentials(api_client, mock_client):
    """GET /providers/{name}/env returns credential keys redacted."""
    mock_client.providers.get.return_value = {
        "name": "my-prov",
        "type": "anthropic",
        "credentials": {"ANTHROPIC_API_KEY": "sk-ant-secret"},  # pragma: allowlist secret
        "config": {},
    }

    resp = await api_client.get(f"{BASE}/my-prov/env")

    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "my-prov"
    assert data["type"] == "anthropic"
    assert len(data["env"]) == 1
    entry = data["env"][0]
    assert entry["key"] == "ANTHROPIC_API_KEY"
    assert entry["source"] == "credential"
    assert entry["redacted_value"] == "[REDACTED]"
    # Secret value must NEVER appear in the response body.
    assert "sk-ant-secret" not in resp.text  # pragma: allowlist secret


async def test_get_provider_env_includes_config_and_type_default(api_client, mock_client):
    """Env projection covers credentials, config, and type_default fallback."""
    mock_client.providers.get.return_value = {
        "name": "gh-prov",
        "type": "github",
        "credentials": {},
        "config": {"GITHUB_ORG": "acme"},
    }

    resp = await api_client.get(f"{BASE}/gh-prov/env")

    assert resp.status_code == 200
    entries = {e["key"]: e for e in resp.json()["env"]}
    # Config key is surfaced
    assert entries["GITHUB_ORG"]["source"] == "config"
    # Type default (GITHUB_TOKEN from openshell.yaml) is added when missing
    assert entries["GITHUB_TOKEN"]["source"] == "type_default"


async def test_get_provider_env_dedupes_type_default(api_client, mock_client):
    """When the type's cred_key is already in credentials, don't duplicate it."""
    mock_client.providers.get.return_value = {
        "name": "my-prov",
        "type": "anthropic",
        "credentials": {"ANTHROPIC_API_KEY": "sk-xxx"},
        "config": {},
    }

    resp = await api_client.get(f"{BASE}/my-prov/env")

    env = resp.json()["env"]
    keys = [e["key"] for e in env]
    assert keys.count("ANTHROPIC_API_KEY") == 1
    assert env[0]["source"] == "credential"


async def test_get_provider_env_unknown_type(api_client, mock_client):
    """Unknown provider types don't crash — just no type_default entry."""
    mock_client.providers.get.return_value = {
        "name": "weird",
        "type": "not-a-real-type",
        "credentials": {"FOO": "bar"},
        "config": {},
    }

    resp = await api_client.get(f"{BASE}/weird/env")

    assert resp.status_code == 200
    env = resp.json()["env"]
    assert len(env) == 1
    assert env[0]["key"] == "FOO"


async def test_get_provider_env_not_found(api_client, mock_client):
    """GET /providers/{name}/env returns 404 for unknown provider."""
    mock_client.providers.get.side_effect = NotFoundError("Provider not found")

    resp = await api_client.get(f"{BASE}/nonexistent/env")

    assert resp.status_code == 404
