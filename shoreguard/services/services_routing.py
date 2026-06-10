"""Sandbox service-routing management.

Thin wrapper over the gRPC service-routing surface (ExposeService /
GetService / ListServices / DeleteService, upstream PR #1101, v0.0.57). The
gateway owns the endpoint records, so this service holds no local state — it
exists to give routes a stable, testable seam over the client manager.
"""

from __future__ import annotations

from typing import Any

from shoreguard.client import ShoreGuardClient


class ServiceRoutingService:
    """Sandbox service-routing operations shared by Web UI and API.

    Args:
        client: OpenShell gRPC client instance.
    """

    def __init__(self, client: ShoreGuardClient) -> None:  # noqa: D107
        self._client = client

    async def list(
        self, *, sandbox: str = "", limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """List exposed service endpoints.

        Args:
            sandbox: Optional sandbox name filter; empty lists all sandboxes.
            limit: Maximum number of endpoints to return.
            offset: Pagination offset.

        Returns:
            list[dict[str, Any]]: One dict per exposed service endpoint.
        """
        return await self._client.services.list(sandbox=sandbox, limit=limit, offset=offset)

    async def get(self, *, sandbox: str, service: str = "") -> dict[str, Any]:
        """Get a single exposed service endpoint.

        Args:
            sandbox: Sandbox name.
            service: Service name within the sandbox; empty selects the unnamed
                endpoint.

        Returns:
            dict[str, Any]: The service endpoint dict.
        """
        return await self._client.services.get(sandbox=sandbox, service=service)

    async def expose(
        self, *, sandbox: str, service: str, target_port: int, domain: bool = False
    ) -> dict[str, Any]:
        """Expose a loopback port inside a sandbox as a routed service.

        Args:
            sandbox: Sandbox name.
            service: Service name within the sandbox.
            target_port: Loopback TCP port inside the sandbox to publish.
            domain: Whether to enable the browser-facing service URL.

        Returns:
            dict[str, Any]: The created service endpoint dict (includes ``url``).
        """
        return await self._client.services.expose(
            sandbox=sandbox, service=service, target_port=target_port, domain=domain
        )

    async def delete(self, *, sandbox: str, service: str = "") -> bool:
        """Delete an exposed service endpoint.

        Args:
            sandbox: Sandbox name.
            service: Service name within the sandbox; empty selects the unnamed
                endpoint.

        Returns:
            bool: True if an endpoint existed and was deleted.
        """
        return await self._client.services.delete(sandbox=sandbox, service=service)
