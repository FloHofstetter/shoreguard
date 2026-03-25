"""OpenShell gRPC client wrapper for Shoreguard."""

from __future__ import annotations

import json
import os
import pathlib
from urllib.parse import urlparse

import grpc

from shoreguard.config import openshell_config_dir
from shoreguard.exceptions import GatewayNotConnectedError

from ._proto import (
    inference_pb2,
    inference_pb2_grpc,
    openshell_pb2,
    openshell_pb2_grpc,
    sandbox_pb2,
)
from .approvals import ApprovalManager
from .policies import PolicyManager
from .providers import ProviderManager
from .sandboxes import SandboxManager


class ShoreGuardClient:
    """Unified client for OpenShell gateway operations."""

    def __init__(
        self,
        endpoint: str,
        *,
        ca_path: pathlib.Path | None = None,
        cert_path: pathlib.Path | None = None,
        key_path: pathlib.Path | None = None,
        timeout: float = 30.0,
    ) -> None:
        """Create a client connected to the given gRPC endpoint."""
        self._endpoint = endpoint
        self._timeout = timeout

        if ca_path and cert_path and key_path:
            credentials = grpc.ssl_channel_credentials(
                root_certificates=ca_path.read_bytes(),
                private_key=key_path.read_bytes(),
                certificate_chain=cert_path.read_bytes(),
            )
            self._channel = grpc.secure_channel(endpoint, credentials)
        else:
            self._channel = grpc.insecure_channel(endpoint)

        self._stub = openshell_pb2_grpc.OpenShellStub(self._channel)
        self._inference_stub = inference_pb2_grpc.InferenceStub(self._channel)

        self.sandboxes = SandboxManager(self._stub, timeout=timeout)
        self.policies = PolicyManager(self._stub, timeout=timeout)
        self.approvals = ApprovalManager(self._stub, timeout=timeout)
        self.providers = ProviderManager(self._stub, timeout=timeout)

    @classmethod
    def from_active_cluster(
        cls,
        *,
        cluster: str | None = None,
        timeout: float = 30.0,
    ) -> ShoreGuardClient:
        """Connect to the active OpenShell gateway using mTLS credentials."""
        cluster_name = cluster or _resolve_active_cluster()
        gateway_dir = openshell_config_dir() / "gateways" / cluster_name

        metadata = json.loads((gateway_dir / "metadata.json").read_text())
        parsed = urlparse(metadata["gateway_endpoint"])
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        endpoint = f"{host}:{port}"

        if parsed.scheme == "https":
            mtls_dir = gateway_dir / "mtls"
            return cls(
                endpoint,
                ca_path=mtls_dir / "ca.crt",
                cert_path=mtls_dir / "tls.crt",
                key_path=mtls_dir / "tls.key",
                timeout=timeout,
            )
        return cls(endpoint, timeout=timeout)

    def health(self) -> dict:
        """Check gateway health."""
        resp = self._stub.Health(openshell_pb2.HealthRequest(), timeout=self._timeout)
        status_names = {0: "unspecified", 1: "healthy", 2: "degraded", 3: "unhealthy"}
        return {"status": status_names.get(resp.status, "unknown"), "version": resp.version}

    def get_cluster_inference(self, *, route_name: str = "") -> dict:
        """Get current cluster inference configuration."""
        resp = self._inference_stub.GetClusterInference(
            inference_pb2.GetClusterInferenceRequest(route_name=route_name),
            timeout=self._timeout,
        )
        return {
            "provider_name": resp.provider_name,
            "model_id": resp.model_id,
            "version": resp.version,
            "route_name": resp.route_name,
        }

    def get_gateway_config(self) -> dict:
        """Get the global gateway configuration (settings and revision)."""
        resp = self._stub.GetGatewayConfig(
            sandbox_pb2.GetGatewayConfigRequest(), timeout=self._timeout
        )
        settings: dict[str, str | bool | int | bytes] = {}
        for key, val in resp.settings.items():
            field = val.WhichOneof("value")
            if field == "string_value":
                settings[key] = val.string_value
            elif field == "bool_value":
                settings[key] = val.bool_value
            elif field == "int_value":
                settings[key] = val.int_value
            elif field == "bytes_value":
                settings[key] = val.bytes_value
        return {"settings": settings, "settings_revision": resp.settings_revision}

    def set_cluster_inference(
        self,
        *,
        provider_name: str,
        model_id: str,
        verify: bool = True,
        route_name: str = "",
    ) -> dict:
        """Set cluster inference configuration."""
        resp = self._inference_stub.SetClusterInference(
            inference_pb2.SetClusterInferenceRequest(
                provider_name=provider_name,
                model_id=model_id,
                verify=verify,
                route_name=route_name,
            ),
            timeout=self._timeout,
        )
        result: dict = {
            "provider_name": resp.provider_name,
            "model_id": resp.model_id,
            "version": resp.version,
            "route_name": resp.route_name,
        }
        if hasattr(resp, "validation_performed"):
            result["validation_performed"] = resp.validation_performed
        if hasattr(resp, "validated_endpoints"):
            result["validated_endpoints"] = [
                {"host": ve.host, "port": ve.port, "reachable": ve.reachable, "error": ve.error}
                for ve in resp.validated_endpoints
            ]
        return result

    def close(self) -> None:
        """Close the underlying gRPC channel."""
        self._channel.close()

    def __enter__(self) -> ShoreGuardClient:
        """Support usage as a context manager."""
        return self

    def __exit__(self, *args: object) -> None:
        """Close the channel on context exit."""
        self.close()


def _resolve_active_cluster() -> str:
    env_gateway = os.environ.get("OPENSHELL_GATEWAY")
    if env_gateway:
        return env_gateway
    active_file = openshell_config_dir() / "active_gateway"
    value = active_file.read_text().strip()
    if not value:
        raise GatewayNotConnectedError("No active OpenShell gateway configured")
    return value
