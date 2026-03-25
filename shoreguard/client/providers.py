"""Provider management operations."""

from __future__ import annotations

from typing import Any

from ._proto import datamodel_pb2, openshell_pb2, openshell_pb2_grpc


def _provider_to_dict(provider: datamodel_pb2.Provider) -> dict[str, Any]:
    """Convert a Provider protobuf to a plain dict."""
    return {
        "id": provider.id,
        "name": provider.name,
        "type": provider.type,
        "credentials": dict(provider.credentials),
        "config": dict(provider.config),
    }


class ProviderManager:
    """Provider CRUD operations against OpenShell gateway."""

    def __init__(self, stub: openshell_pb2_grpc.OpenShellStub, *, timeout: float = 30.0) -> None:
        """Initialize with an OpenShell gRPC stub."""
        self._stub = stub
        self._timeout = timeout

    def list(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """List all providers."""
        resp = self._stub.ListProviders(
            openshell_pb2.ListProvidersRequest(limit=limit, offset=offset),
            timeout=self._timeout,
        )
        return [_provider_to_dict(p) for p in resp.providers]

    def get(self, name: str) -> dict[str, Any]:
        """Get a provider by name."""
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
        """Create a new provider."""
        provider = datamodel_pb2.Provider(
            name=name,
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
        """Update an existing provider."""
        provider = datamodel_pb2.Provider(
            name=name,
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
        """Delete a provider by name."""
        resp = self._stub.DeleteProvider(
            openshell_pb2.DeleteProviderRequest(name=name), timeout=self._timeout
        )
        return bool(resp.deleted)
