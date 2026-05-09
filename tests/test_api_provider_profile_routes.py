"""Integration tests for the provider-profile REST routes (M37 / WS37.4)."""

from __future__ import annotations

GW = "test"


async def test_list_provider_profiles(api_client, mock_client):
    """GET /provider-profiles returns the paginated list."""
    mock_client.provider_profiles.list.return_value = [
        {"id": "claude", "display_name": "Claude", "category": "inference"},
        {"id": "openai", "display_name": "OpenAI", "category": "inference"},
    ]

    resp = await api_client.get(f"/api/gateways/{GW}/provider-profiles")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] is None
    assert [p["id"] for p in body["items"]] == ["claude", "openai"]


async def test_get_provider_profile(api_client, mock_client):
    """GET /provider-profiles/{id} returns the single profile."""
    mock_client.provider_profiles.get.return_value = {
        "id": "claude",
        "display_name": "Claude",
        "description": "Anthropic Claude",
        "category": "inference",
        "credentials": [],
        "endpoint_count": 0,
        "binary_count": 0,
        "inference_capable": True,
    }

    resp = await api_client.get(f"/api/gateways/{GW}/provider-profiles/claude")

    assert resp.status_code == 200
    assert resp.json()["id"] == "claude"
    mock_client.provider_profiles.get.assert_called_once_with("claude")


async def test_lint_provider_profiles(api_client, mock_client):
    """POST /provider-profiles/lint returns diagnostics without mutating state."""
    mock_client.provider_profiles.lint.return_value = {
        "valid": False,
        "diagnostics": [
            {
                "source": "inline",
                "profile_id": "oops",
                "field": "display_name",
                "message": "empty",
                "severity": "error",
            }
        ],
    }

    resp = await api_client.post(
        f"/api/gateways/{GW}/provider-profiles/lint",
        json={"profiles": [{"profile": {"id": "oops", "display_name": ""}, "source": "inline"}]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert body["diagnostics"][0]["field"] == "display_name"


async def test_import_provider_profiles(api_client, mock_client):
    """POST /provider-profiles/import forwards items and reports outcome."""
    mock_client.provider_profiles.import_.return_value = {
        "imported": True,
        "profiles": [{"id": "claude", "display_name": "Claude", "category": "inference"}],
        "diagnostics": [],
    }

    resp = await api_client.post(
        f"/api/gateways/{GW}/provider-profiles/import",
        json={
            "profiles": [
                {
                    "profile": {"id": "claude", "display_name": "Claude"},
                    "source": "inline",
                }
            ]
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] is True
    assert body["profiles"][0]["id"] == "claude"


async def test_delete_provider_profile(api_client, mock_client):
    """DELETE /provider-profiles/{id} returns deleted status."""
    mock_client.provider_profiles.delete.return_value = True

    resp = await api_client.delete(f"/api/gateways/{GW}/provider-profiles/claude")

    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}
    mock_client.provider_profiles.delete.assert_called_once_with("claude")
