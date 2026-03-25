"""Provider management with metadata from openshell.yaml."""

from __future__ import annotations

from typing import Any

from shoreguard.client import ShoreGuardClient
from shoreguard.services._openshell_meta import get_openshell_meta


class ProviderService:
    """Provider management shared by Web UI and TUI."""

    def __init__(self, client: ShoreGuardClient) -> None:
        """Initialize with an OpenShell client."""
        self._client = client

    def list(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """List all providers."""
        return self._client.providers.list(limit=limit, offset=offset)

    def get(self, name: str) -> dict[str, Any]:
        """Get a provider by name."""
        return self._client.providers.get(name)

    def create(
        self,
        *,
        name: str,
        provider_type: str,
        api_key: str,
        extra_credentials: dict[str, str] | None = None,
        config: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create a provider with automatic credential key mapping.

        Looks up the correct environment variable name from openshell.yaml.
        Falls back to API_KEY for unknown types.
        """
        meta = get_openshell_meta()
        type_info = meta.get_provider_type(provider_type)
        cred_key = type_info.get("cred_key", "API_KEY") if type_info else "API_KEY"

        credentials = {cred_key: api_key}
        if extra_credentials:
            credentials.update(extra_credentials)

        return self._client.providers.create(
            name=name,
            provider_type=provider_type,
            credentials=credentials,
            config=config,
        )

    def update(
        self,
        *,
        name: str,
        provider_type: str = "",
        credentials: dict[str, str] | None = None,
        config: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Update an existing provider."""
        return self._client.providers.update(
            name=name,
            provider_type=provider_type,
            credentials=credentials,
            config=config,
        )

    def delete(self, name: str) -> bool:
        """Delete a provider."""
        return self._client.providers.delete(name)

    @staticmethod
    def list_known_types() -> list[dict[str, str]]:
        """Return metadata about known provider types from openshell.yaml."""
        meta = get_openshell_meta()
        return [{"type": k, **v} for k, v in meta.provider_types.items()]

    @staticmethod
    def list_inference_providers() -> list[dict[str, str]]:
        """Return known inference provider options from openshell.yaml."""
        meta = get_openshell_meta()
        return meta.inference_providers

    @staticmethod
    def list_community_sandboxes() -> list[dict[str, Any]]:
        """Return community sandbox templates from openshell.yaml."""
        meta = get_openshell_meta()
        return meta.community_sandboxes
