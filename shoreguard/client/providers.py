"""gRPC wrapper for OpenShell's provider RPCs.

Exposes list / get / create / update / delete against the
upstream provider surface plus the env-projection call used to
render a redacted view of a provider's environment variables.
Credentials flow only into this manager via ``create`` and
``update``; reads never return raw secrets — the wrapping
service layer relies on that invariant when it renders the
``[REDACTED]`` env projection.
"""

from __future__ import annotations

from typing import Any

from ._proto import datamodel_pb2, openshell_pb2, openshell_pb2_grpc


def _provider_to_dict(provider: datamodel_pb2.Provider) -> dict[str, Any]:
    """Convert a Provider protobuf to a plain dict.

    Args:
        provider: Provider protobuf message.

    Returns:
        dict[str, Any]: Flat projection of the provider with id/name/labels/
            created_at_ms hoisted from ``provider.metadata`` plus type,
            credentials and config.
    """
    meta = provider.metadata
    return {
        "id": meta.id,
        "name": meta.name,
        "created_at_ms": meta.created_at_ms,
        "resource_version": meta.resource_version,
        "labels": dict(meta.labels),
        "type": provider.type,
        "credentials": dict(provider.credentials),
        "config": dict(provider.config),
        "credential_expires_at_ms": dict(provider.credential_expires_at_ms),
    }


_STRATEGY_PREFIX = "PROVIDER_CREDENTIAL_REFRESH_STRATEGY_"


def _strategy_to_str(value: int) -> str:
    """Map a ProviderCredentialRefreshStrategy enum value to a friendly string.

    Args:
        value: Enum integer (e.g. ``OAUTH2_REFRESH_TOKEN``).

    Returns:
        str: Lower-case name without the enum prefix (e.g. ``oauth2_refresh_token``).
    """
    name = openshell_pb2.ProviderCredentialRefreshStrategy.Name(value)
    return name.removeprefix(_STRATEGY_PREFIX).lower()


def _strategy_to_enum(value: str) -> str:
    """Map a friendly strategy string to a ProviderCredentialRefreshStrategy enum name.

    The full enum-value name is returned (not the integer) because protobuf
    accepts the name string for enum fields and pyright types those fields as
    ``Enum | str`` — passing the name keeps the assignment type-safe.

    Args:
        value: Friendly name such as ``static`` or ``oauth2_client_credentials``.

    Returns:
        str: The matching enum-value name (e.g. ``PROVIDER_CREDENTIAL_REFRESH_STRATEGY_STATIC``).

    Raises:
        ValueError: If *value* is not a known strategy.
    """
    full = _STRATEGY_PREFIX + value.upper()
    if full not in openshell_pb2.ProviderCredentialRefreshStrategy.keys():
        raise ValueError(f"Unknown refresh strategy: {value!r}")
    return full


def _refresh_status_to_dict(
    status: openshell_pb2.ProviderCredentialRefreshStatus,
) -> dict[str, Any]:
    """Convert a ProviderCredentialRefreshStatus protobuf to a plain dict.

    Args:
        status: Refresh-status protobuf message.

    Returns:
        dict[str, Any]: Flat projection with the strategy rendered as a friendly
            string and millisecond timestamps left as integers (0 = unset).
    """
    return {
        "provider_name": status.provider_name,
        "provider_id": status.provider_id,
        "credential_key": status.credential_key,
        "strategy": _strategy_to_str(status.strategy),
        "status": status.status,
        "expires_at_ms": status.expires_at_ms,
        "next_refresh_at_ms": status.next_refresh_at_ms,
        "last_refresh_at_ms": status.last_refresh_at_ms,
        "last_error": status.last_error,
    }


class ProviderManager:
    """Provider CRUD operations against OpenShell gateway.

    Args:
        stub: OpenShell gRPC stub.
        timeout: gRPC call timeout in seconds.
    """

    def __init__(self, stub: openshell_pb2_grpc.OpenShellStub, *, timeout: float = 30.0) -> None:  # noqa: D107
        self._stub = stub
        self._timeout = timeout

    async def list(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """List all providers.

        Args:
            limit: Maximum number of providers to return.
            offset: Pagination offset.

        Returns:
            list[dict[str, Any]]: List of provider dicts.
        """
        resp = await self._stub.ListProviders(
            openshell_pb2.ListProvidersRequest(limit=limit, offset=offset),
            timeout=self._timeout,
        )
        return [_provider_to_dict(p) for p in resp.providers]

    async def get(self, name: str) -> dict[str, Any]:
        """Get a provider by name.

        Args:
            name: Provider name.

        Returns:
            dict[str, Any]: Provider data dict.
        """
        resp = await self._stub.GetProvider(
            openshell_pb2.GetProviderRequest(name=name), timeout=self._timeout
        )
        return _provider_to_dict(resp.provider)

    async def create(
        self,
        *,
        name: str,
        provider_type: str,
        credentials: dict[str, str] | None = None,
        config: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create a new provider.

        Args:
            name: Provider name.
            provider_type: Provider type identifier.
            credentials: Provider credential key-value pairs.
            config: Provider configuration key-value pairs.
            labels: Optional Kubernetes-style labels.

        Returns:
            dict[str, Any]: Created provider data dict.
        """
        provider = datamodel_pb2.Provider(
            metadata=datamodel_pb2.ObjectMeta(name=name, labels=labels or {}),
            type=provider_type,
            credentials=credentials or {},
            config=config or {},
        )
        resp = await self._stub.CreateProvider(
            openshell_pb2.CreateProviderRequest(provider=provider),
            timeout=self._timeout,
        )
        return _provider_to_dict(resp.provider)

    async def update(
        self,
        *,
        name: str,
        provider_type: str = "",
        credentials: dict[str, str] | None = None,
        config: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Update an existing provider.

        Args:
            name: Provider name.
            provider_type: Provider type identifier.
            credentials: Provider credential key-value pairs.
            config: Provider configuration key-value pairs.
            labels: Optional Kubernetes-style labels.

        Returns:
            dict[str, Any]: Updated provider data dict.
        """
        provider = datamodel_pb2.Provider(
            metadata=datamodel_pb2.ObjectMeta(name=name, labels=labels or {}),
            type=provider_type,
            credentials=credentials or {},
            config=config or {},
        )
        resp = await self._stub.UpdateProvider(
            openshell_pb2.UpdateProviderRequest(provider=provider),
            timeout=self._timeout,
        )
        return _provider_to_dict(resp.provider)

    async def delete(self, name: str) -> bool:
        """Delete a provider by name.

        Args:
            name: Provider name.

        Returns:
            bool: True if the provider was deleted.
        """
        resp = await self._stub.DeleteProvider(
            openshell_pb2.DeleteProviderRequest(name=name), timeout=self._timeout
        )
        return bool(resp.deleted)

    # ── Credential refresh / rotation (upstream PR #1349, v0.0.57) ──────────

    async def configure_refresh(
        self,
        *,
        provider: str,
        credential_key: str,
        strategy: str,
        material: dict[str, str] | None = None,
        secret_material_keys: list[str] | None = None,
        expires_at_ms: int | None = None,
    ) -> dict[str, Any]:
        """Configure automatic refresh for a provider credential.

        Args:
            provider: Provider name.
            credential_key: Credential key within the provider to refresh.
            strategy: Refresh strategy (e.g. ``static``, ``oauth2_refresh_token``).
            material: Strategy-specific material (token URLs, client ids, etc.).
            secret_material_keys: Keys within *material* that hold secret values.
            expires_at_ms: Optional absolute expiry of the current credential.

        Returns:
            dict[str, Any]: The resulting refresh status.
        """
        req = openshell_pb2.ConfigureProviderRefreshRequest(
            provider=provider,
            credential_key=credential_key,
            strategy=_strategy_to_enum(strategy),
            material=material or {},
            secret_material_keys=list(secret_material_keys or []),
        )
        if expires_at_ms is not None:
            req.expires_at_ms = expires_at_ms
        resp = await self._stub.ConfigureProviderRefresh(req, timeout=self._timeout)
        return _refresh_status_to_dict(resp.status)

    async def get_refresh_status(
        self, provider: str, *, credential_key: str = ""
    ) -> list[dict[str, Any]]:
        """List credential-refresh status entries for a provider.

        Args:
            provider: Provider name.
            credential_key: Optional credential key; empty returns all entries.

        Returns:
            list[dict[str, Any]]: One status dict per configured credential.
        """
        resp = await self._stub.GetProviderRefreshStatus(
            openshell_pb2.GetProviderRefreshStatusRequest(
                provider=provider, credential_key=credential_key
            ),
            timeout=self._timeout,
        )
        return [_refresh_status_to_dict(s) for s in resp.credentials]

    async def rotate_credential(self, *, provider: str, credential_key: str) -> dict[str, Any]:
        """Rotate a provider credential immediately.

        Args:
            provider: Provider name.
            credential_key: Credential key to rotate.

        Returns:
            dict[str, Any]: The refresh status after rotation.
        """
        resp = await self._stub.RotateProviderCredential(
            openshell_pb2.RotateProviderCredentialRequest(
                provider=provider, credential_key=credential_key
            ),
            timeout=self._timeout,
        )
        return _refresh_status_to_dict(resp.status)

    async def delete_refresh(self, *, provider: str, credential_key: str) -> bool:
        """Delete a credential-refresh configuration.

        Args:
            provider: Provider name.
            credential_key: Credential key whose refresh config is removed.

        Returns:
            bool: True if a configuration existed and was deleted.
        """
        resp = await self._stub.DeleteProviderRefresh(
            openshell_pb2.DeleteProviderRefreshRequest(
                provider=provider, credential_key=credential_key
            ),
            timeout=self._timeout,
        )
        return bool(resp.deleted)
