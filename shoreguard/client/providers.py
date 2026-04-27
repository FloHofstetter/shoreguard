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

    Identity fields (id, name) come from the OpenShell ObjectMeta convention
    (`Provider.metadata`) introduced in OpenShell #919. Pre-#919 wire payloads
    are not supported on this branch — Shoreguard requires gateway version
    v0.0.37+.

    Args:
        provider: Provider protobuf message.

    Returns:
        dict[str, Any]: Provider data with id, name, type, credentials,
            and config.
    """
    return {
        "id": provider.metadata.id,
        "name": provider.metadata.name,
        "type": provider.type,
        "credentials": dict(provider.credentials),
        "config": dict(provider.config),
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

    def list(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """List all providers.

        Args:
            limit: Maximum number of providers to return.
            offset: Pagination offset.

        Returns:
            list[dict[str, Any]]: List of provider dicts.
        """
        resp = self._stub.ListProviders(
            openshell_pb2.ListProvidersRequest(limit=limit, offset=offset),
            timeout=self._timeout,
        )
        return [_provider_to_dict(p) for p in resp.providers]

    def get(self, name: str) -> dict[str, Any]:
        """Get a provider by name.

        Args:
            name: Provider name.

        Returns:
            dict[str, Any]: Provider data dict.
        """
        resp = self._stub.GetProvider(
            openshell_pb2.GetProviderRequest(name=name), timeout=self._timeout
        )
        return _provider_to_dict(resp.provider)

    def create(
        self,
        *,
        name: str,
        provider_type: str,
        credentials: dict[str, str] | None = None,
        config: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create a new provider.

        Args:
            name: Provider name.
            provider_type: Provider type identifier.
            credentials: Provider credential key-value pairs.
            config: Provider configuration key-value pairs.

        Returns:
            dict[str, Any]: Created provider data dict.
        """
        provider = datamodel_pb2.Provider(
            metadata=datamodel_pb2.ObjectMeta(name=name),
            type=provider_type,
            credentials=credentials or {},
            config=config or {},
        )
        resp = self._stub.CreateProvider(
            openshell_pb2.CreateProviderRequest(provider=provider),
            timeout=self._timeout,
        )
        return _provider_to_dict(resp.provider)

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
            provider_type: Provider type identifier.
            credentials: Provider credential key-value pairs.
            config: Provider configuration key-value pairs.

        Returns:
            dict[str, Any]: Updated provider data dict.
        """
        provider = datamodel_pb2.Provider(
            metadata=datamodel_pb2.ObjectMeta(name=name),
            type=provider_type,
            credentials=credentials or {},
            config=config or {},
        )
        resp = self._stub.UpdateProvider(
            openshell_pb2.UpdateProviderRequest(provider=provider),
            timeout=self._timeout,
        )
        return _provider_to_dict(resp.provider)

    def delete(self, name: str) -> bool:
        """Delete a provider by name.

        Args:
            name: Provider name.

        Returns:
            bool: True if the provider was deleted.
        """
        resp = self._stub.DeleteProvider(
            openshell_pb2.DeleteProviderRequest(name=name), timeout=self._timeout
        )
        return bool(resp.deleted)
