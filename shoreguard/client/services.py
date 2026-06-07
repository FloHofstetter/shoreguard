"""gRPC wrapper for OpenShell's service-routing RPCs.

Exposes ExposeService / GetService / ListServices / DeleteService against the
upstream local-domain service-routing surface (upstream PR #1101, v0.0.57). A
*service* is a loopback TCP port inside a sandbox that the gateway publishes on
a browser-reachable endpoint; the gateway is authoritative for the endpoint
records, so this manager is a thin projection with no local state.
"""

from __future__ import annotations

from typing import Any

from ._proto import openshell_pb2, openshell_pb2_grpc


def _service_endpoint_to_dict(resp: openshell_pb2.ServiceEndpointResponse) -> dict[str, Any]:
    """Convert a ServiceEndpointResponse protobuf to a plain dict.

    Args:
        resp: ServiceEndpointResponse protobuf message.

    Returns:
        dict[str, Any]: Flat projection of the endpoint plus the browser-facing
            ``url`` hoisted from the wrapping response message.
    """
    ep = resp.endpoint
    meta = ep.metadata
    return {
        "id": meta.id,
        "created_at_ms": meta.created_at_ms,
        "sandbox_id": ep.sandbox_id,
        "sandbox_name": ep.sandbox_name,
        "service_name": ep.service_name,
        "target_port": ep.target_port,
        "domain": ep.domain,
        "url": resp.url,
    }


class ServiceManager:
    """Sandbox service-routing operations against an OpenShell gateway.

    Args:
        stub: OpenShell gRPC stub.
        timeout: gRPC call timeout in seconds.
    """

    def __init__(self, stub: openshell_pb2_grpc.OpenShellStub, *, timeout: float = 30.0) -> None:  # noqa: D107
        self._stub = stub
        self._timeout = timeout

    def list(self, *, sandbox: str = "", limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """List exposed service endpoints.

        Args:
            sandbox: Optional sandbox name filter; empty lists all sandboxes.
            limit: Maximum number of endpoints to return.
            offset: Pagination offset.

        Returns:
            list[dict[str, Any]]: One dict per exposed service endpoint.
        """
        resp = self._stub.ListServices(
            openshell_pb2.ListServicesRequest(sandbox=sandbox, limit=limit, offset=offset),
            timeout=self._timeout,
        )
        return [_service_endpoint_to_dict(s) for s in resp.services]

    def get(self, *, sandbox: str, service: str = "") -> dict[str, Any]:
        """Get a single exposed service endpoint.

        Args:
            sandbox: Sandbox name.
            service: Service name within the sandbox; empty selects the unnamed
                endpoint.

        Returns:
            dict[str, Any]: The service endpoint dict.
        """
        resp = self._stub.GetService(
            openshell_pb2.GetServiceRequest(sandbox=sandbox, service=service),
            timeout=self._timeout,
        )
        return _service_endpoint_to_dict(resp)

    def expose(
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
        resp = self._stub.ExposeService(
            openshell_pb2.ExposeServiceRequest(
                sandbox=sandbox, service=service, target_port=target_port, domain=domain
            ),
            timeout=self._timeout,
        )
        return _service_endpoint_to_dict(resp)

    def delete(self, *, sandbox: str, service: str = "") -> bool:
        """Delete an exposed service endpoint.

        Args:
            sandbox: Sandbox name.
            service: Service name within the sandbox; empty selects the unnamed
                endpoint.

        Returns:
            bool: True if an endpoint existed and was deleted.
        """
        resp = self._stub.DeleteService(
            openshell_pb2.DeleteServiceRequest(sandbox=sandbox, service=service),
            timeout=self._timeout,
        )
        return bool(resp.deleted)
