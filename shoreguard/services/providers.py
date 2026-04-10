"""Provider management with metadata from openshell.yaml."""

from __future__ import annotations

from typing import Any

from shoreguard.client import ShoreGuardClient
from shoreguard.services._openshell_meta import get_openshell_meta


class ProviderService:
    """Provider management shared by Web UI and TUI.

    Args:
        client: OpenShell gRPC client instance.
    """

    def __init__(self, client: ShoreGuardClient) -> None:  # noqa: D107
        self._client = client

    def list(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """List all providers.

        Args:
            limit: Maximum number of providers to return.
            offset: Number of providers to skip.

        Returns:
            list[dict[str, Any]]: Provider records.
        """
        return self._client.providers.list(limit=limit, offset=offset)

    def get(self, name: str) -> dict[str, Any]:
        """Get a provider by name.

        Args:
            name: Provider name.

        Returns:
            dict[str, Any]: Provider record.
        """
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

        Args:
            name: Provider name.
            provider_type: Provider type identifier.
            api_key: Primary API key value.
            extra_credentials: Additional credential key-value pairs.
            config: Optional provider configuration.

        Returns:
            dict[str, Any]: The created provider record.
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
        """Update an existing provider.

        Args:
            name: Provider name.
            provider_type: New provider type (empty string to keep current).
            credentials: New credential key-value pairs.
            config: New provider configuration.

        Returns:
            dict[str, Any]: The updated provider record.
        """
        return self._client.providers.update(
            name=name,
            provider_type=provider_type,
            credentials=credentials,
            config=config,
        )

    def delete(self, name: str) -> bool:
        """Delete a provider.

        Args:
            name: Provider name.

        Returns:
            bool: True if the provider was deleted.
        """
        return self._client.providers.delete(name)

    def get_env(self, name: str) -> dict[str, Any]:
        """Get the redacted environment projection for a provider.

        Returns the environment variables this provider injects into
        sandboxes. Secret values are never included — only keys, their
        source (``credential``, ``config``, ``type_default``), and a
        constant ``[REDACTED]`` placeholder.

        The key set is derived from three places:

        1. Every key in the provider's ``credentials`` dict (source=``credential``).
        2. Every key in the provider's ``config`` dict (source=``config``).
        3. The provider type's ``cred_key`` from ``openshell.yaml``
           (source=``type_default``), added only if it was not already
           present in the credentials dict.

        Args:
            name: Provider name.

        Returns:
            dict[str, Any]: Record with ``provider``, ``type`` and ``env``.
        """
        provider = self._client.providers.get(name)
        provider_type = provider.get("type") if isinstance(provider, dict) else None

        env: list[dict[str, str]] = []
        seen: set[str] = set()

        creds = provider.get("credentials") or {} if isinstance(provider, dict) else {}
        for key in creds:
            if key in seen:
                continue
            env.append({"key": key, "source": "credential", "redacted_value": "[REDACTED]"})
            seen.add(key)

        config = provider.get("config") or {} if isinstance(provider, dict) else {}
        for key in config:
            if key in seen:
                continue
            env.append({"key": key, "source": "config", "redacted_value": "[REDACTED]"})
            seen.add(key)

        if provider_type:
            meta = get_openshell_meta()
            type_info = meta.get_provider_type(provider_type)
            default_key = type_info.get("cred_key") if type_info else None
            if default_key and default_key not in seen:
                env.append(
                    {"key": default_key, "source": "type_default", "redacted_value": "[REDACTED]"}
                )

        return {"provider": name, "type": provider_type, "env": env}

    @staticmethod
    def list_known_types() -> list[dict[str, str]]:
        """Return metadata about known provider types from openshell.yaml.

        Returns:
            list[dict[str, str]]: Provider type metadata records.
        """
        meta = get_openshell_meta()
        return [{"type": k, **v} for k, v in meta.provider_types.items()]

    @staticmethod
    def list_inference_providers() -> list[dict[str, str]]:
        """Return known inference provider options from openshell.yaml.

        Returns:
            list[dict[str, str]]: Inference provider option records.
        """
        meta = get_openshell_meta()
        return meta.inference_providers

    @staticmethod
    def list_community_sandboxes() -> list[dict[str, Any]]:
        """Return community sandbox templates from openshell.yaml.

        Returns:
            list[dict[str, Any]]: Community sandbox template records.
        """
        meta = get_openshell_meta()
        return meta.community_sandboxes
