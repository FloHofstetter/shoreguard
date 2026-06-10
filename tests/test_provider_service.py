"""Tests for ProviderService credential mapping."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from shoreguard.services.providers import ProviderService


@pytest.fixture
def provider_svc(mock_client):
    """ProviderService with a mocked client."""
    return ProviderService(mock_client)


def _mock_meta(provider_types: dict):
    """Create a mock OpenShellMeta with given provider types."""
    meta = MagicMock()
    meta.provider_types = provider_types

    def get_provider_type(name):
        return provider_types.get(name)

    meta.get_provider_type = get_provider_type
    return meta


@patch("shoreguard.services.providers.get_openshell_meta")
async def test_create_maps_cred_key(mock_get_meta, provider_svc, mock_client):
    """Create maps api_key to the correct credential key from openshell.yaml."""
    mock_get_meta.return_value = _mock_meta(
        {
            "anthropic": {"label": "Anthropic", "cred_key": "ANTHROPIC_API_KEY"},
        }
    )
    mock_client.providers.create.return_value = {"name": "my-claude"}

    result = await provider_svc.create(
        name="my-claude",
        provider_type="anthropic",
        api_key="sk-ant-xxx",
    )

    assert result == {"name": "my-claude"}
    call_kwargs = mock_client.providers.create.call_args.kwargs
    assert call_kwargs["credentials"] == {"ANTHROPIC_API_KEY": "sk-ant-xxx"}


@patch("shoreguard.services.providers.get_openshell_meta")
async def test_create_unknown_type_fallback(mock_get_meta, provider_svc, mock_client):
    """Unknown provider type falls back to API_KEY as credential key."""
    mock_get_meta.return_value = _mock_meta({})
    mock_client.providers.create.return_value = {"name": "custom"}

    await provider_svc.create(
        name="custom",
        provider_type="unknown_provider",
        api_key="key123",
    )

    call_kwargs = mock_client.providers.create.call_args.kwargs
    assert call_kwargs["credentials"] == {"API_KEY": "key123"}


@patch("shoreguard.services.providers.get_openshell_meta")
async def test_list_known_types(mock_get_meta):
    """list_known_types returns formatted list from openshell meta."""
    mock_get_meta.return_value = _mock_meta(
        {
            "openai": {"label": "OpenAI", "cred_key": "OPENAI_API_KEY"},
            "anthropic": {"label": "Anthropic", "cred_key": "ANTHROPIC_API_KEY"},
        }
    )

    result = ProviderService.list_known_types()

    assert len(result) == 2
    types = {r["type"] for r in result}
    assert types == {"openai", "anthropic"}


async def test_list(provider_svc, mock_client):
    """list() delegates limit/offset to client."""
    mock_client.providers.list.return_value = [{"name": "p1"}, {"name": "p2"}]

    result = await provider_svc.list(limit=50, offset=5)

    mock_client.providers.list.assert_called_once_with(limit=50, offset=5)
    assert len(result) == 2


async def test_get(provider_svc, mock_client):
    """get() delegates name to client."""
    mock_client.providers.get.return_value = {"name": "my-prov"}

    result = await provider_svc.get("my-prov")

    mock_client.providers.get.assert_called_once_with("my-prov")
    assert result["name"] == "my-prov"


async def test_update(provider_svc, mock_client):
    """update() forwards all kwargs to client."""
    mock_client.providers.update.return_value = {"name": "my-prov"}

    result = await provider_svc.update(
        name="my-prov",
        provider_type="openai",
        credentials={"OPENAI_API_KEY": "sk-xxx"},
    )

    mock_client.providers.update.assert_called_once_with(
        name="my-prov",
        provider_type="openai",
        credentials={"OPENAI_API_KEY": "sk-xxx"},
        config=None,
    )
    assert result["name"] == "my-prov"


async def test_delete(provider_svc, mock_client):
    """delete() delegates name to client and returns bool."""
    mock_client.providers.delete.return_value = True

    result = await provider_svc.delete("my-prov")

    mock_client.providers.delete.assert_called_once_with("my-prov")
    assert result is True


async def test_list_inference_providers():
    """list_inference_providers returns a list from openshell meta."""
    result = ProviderService.list_inference_providers()

    assert isinstance(result, list)


async def test_list_community_sandboxes():
    """list_community_sandboxes returns a list (may be empty if openshell.yaml absent)."""
    result = ProviderService.list_community_sandboxes()

    assert isinstance(result, list)


# ─── Credential refresh / rotation delegation ────────────────────────────────


async def test_configure_refresh_delegates(provider_svc, mock_client):
    """configure_refresh forwards every argument to the client manager."""
    mock_client.providers.configure_refresh.return_value = {"status": "active"}

    await provider_svc.configure_refresh(
        provider="p1",
        credential_key="K",
        strategy="oauth2_refresh_token",
        material={"token_url": "https://x"},
        secret_material_keys=["client_secret"],
        expires_at_ms=42,
    )

    mock_client.providers.configure_refresh.assert_called_once_with(
        provider="p1",
        credential_key="K",
        strategy="oauth2_refresh_token",
        material={"token_url": "https://x"},
        secret_material_keys=["client_secret"],
        expires_at_ms=42,
    )


async def test_get_refresh_status_delegates(provider_svc, mock_client):
    """get_refresh_status forwards provider and credential_key."""
    mock_client.providers.get_refresh_status.return_value = []

    await provider_svc.get_refresh_status("p1", credential_key="K")

    mock_client.providers.get_refresh_status.assert_called_once_with("p1", credential_key="K")


async def test_rotate_credential_delegates(provider_svc, mock_client):
    """rotate_credential forwards provider and credential_key."""
    mock_client.providers.rotate_credential.return_value = {"status": "active"}

    await provider_svc.rotate_credential(provider="p1", credential_key="K")

    mock_client.providers.rotate_credential.assert_called_once_with(
        provider="p1", credential_key="K"
    )


async def test_delete_refresh_delegates(provider_svc, mock_client):
    """delete_refresh forwards provider and credential_key and returns the bool."""
    mock_client.providers.delete_refresh.return_value = True

    result = await provider_svc.delete_refresh(provider="p1", credential_key="K")

    assert result is True
    mock_client.providers.delete_refresh.assert_called_once_with(provider="p1", credential_key="K")
