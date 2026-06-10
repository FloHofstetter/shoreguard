"""Integration tests for provider CRUD via client layer."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_list_providers(sg_client):
    """list() returns a list (possibly empty)."""
    result = sg_client.providers.list()
    assert isinstance(result, list)


async def test_create_provider(provider_factory):
    """Create a provider and verify returned fields."""
    prov = await provider_factory(
        name="integ-test-prov",
        provider_type="anthropic",
        credentials={"ANTHROPIC_API_KEY": "sk-test-xxx"},
    )

    assert prov["name"] == "integ-test-prov"
    assert prov["type"] == "anthropic"
    assert "id" in prov


async def test_get_provider(provider_factory, sg_client):
    """Create a provider, then get it by name."""
    await provider_factory(
        name="integ-get-prov",
        provider_type="anthropic",
        credentials={"ANTHROPIC_API_KEY": "sk-test"},
    )

    fetched = sg_client.providers.get("integ-get-prov")
    assert fetched["name"] == "integ-get-prov"
    assert fetched["type"] == "anthropic"


async def test_update_provider(provider_factory, sg_client):
    """Create a provider, update it, verify change."""
    await provider_factory(
        name="integ-upd-prov",
        provider_type="anthropic",
        credentials={"ANTHROPIC_API_KEY": "sk-old"},
    )

    updated = sg_client.providers.update(
        name="integ-upd-prov",
        provider_type="anthropic",
        credentials={"ANTHROPIC_API_KEY": "sk-new"},
    )
    assert updated["name"] == "integ-upd-prov"


async def test_delete_provider(provider_factory, sg_client):
    """Create and delete a provider."""
    await provider_factory(
        name="integ-del-prov",
        provider_type="anthropic",
        credentials={"ANTHROPIC_API_KEY": "sk-del"},
    )

    deleted = sg_client.providers.delete("integ-del-prov")
    assert deleted is True
