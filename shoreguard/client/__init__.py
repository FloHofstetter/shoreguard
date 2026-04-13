"""OpenShell gRPC client wrapper used throughout ShoreGuard.

Provides :class:`ShoreGuardClient`, the single object every
service layer uses to talk to an OpenShell gateway. Manages the
gRPC channel (plaintext or mTLS), builds the stubs once at
construction time, and exposes four submanagers —
:class:`ApprovalManager`, :class:`PolicyManager`,
:class:`ProviderManager`, :class:`SandboxManager` — each of
which maps a logical surface onto a subset of the underlying
RPCs.

The client is intentionally synchronous: gRPC itself is sync,
and all the service layers wrap calls in
``asyncio.to_thread`` when they need to run inside a FastAPI
async route. Keeping the client sync avoids the double-wrapping
that an async gRPC client would require without any benefit at
this scale.
"""

from __future__ import annotations

import json
import logging
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
from ._resilience import RetryPolicy
from ._tls import CertInfo, validate_bundle
from .approvals import ApprovalManager
from .policies import PolicyManager
from .providers import ProviderManager
from .sandboxes import SandboxManager

logger = logging.getLogger(__name__)


def _default_retry_policy() -> RetryPolicy:
    """Build a RetryPolicy from :class:`GatewaySettings`.

    Imported lazily to avoid a settings import at module load time for code
    paths (tests, proto-only usage) that do not need the full settings stack.

    Returns:
        RetryPolicy: Policy built from ``GatewaySettings.grpc_retry_*`` values.
    """
    from shoreguard.settings import get_settings

    gw = get_settings().gateway
    return RetryPolicy(
        max_attempts=gw.grpc_retry_max_attempts,
        initial_backoff=gw.grpc_retry_initial_backoff,
        max_backoff=gw.grpc_retry_max_backoff,
    )


def _default_retry_deadline() -> float:
    """Return the configured total retry budget in seconds.

    Returns:
        float: ``GatewaySettings.grpc_retry_deadline``.
    """
    from shoreguard.settings import get_settings

    return get_settings().gateway.grpc_retry_deadline


def _default_require_mtls() -> bool:
    """Return whether plaintext gateway channels should be rejected.

    Returns:
        bool: ``GatewaySettings.require_mtls``.
    """
    from shoreguard.settings import get_settings

    return get_settings().gateway.require_mtls


def _default_cert_warn_days() -> int:
    """Return the configured cert-expiry warning window in days.

    Returns:
        int: ``GatewaySettings.cert_expiry_warn_days``.
    """
    from shoreguard.settings import get_settings

    return get_settings().gateway.cert_expiry_warn_days


def _endpoint_host(endpoint: str) -> str:
    """Extract the hostname portion of a ``host:port`` endpoint string.

    Args:
        endpoint: Gateway endpoint as ``host:port``.

    Returns:
        str: The hostname, or the original string if no port separator is
            present.
    """
    if ":" not in endpoint:
        return endpoint
    host, _, _ = endpoint.rpartition(":")
    return host or endpoint


class ShoreGuardClient:
    """Unified gRPC client bound to a single OpenShell gateway.

    Holds the channel, the protobuf stubs, and the four submanagers
    (``approvals``, ``policies``, ``providers``, ``sandboxes``)
    that expose OpenShell's RPC surface. One instance maps to one
    gateway endpoint — do not reuse across gateways; instead,
    construct a new client per ``(endpoint, credentials)`` pair.

    The channel is opened lazily on first use so construction is
    cheap and a bad cert bundle only fails the first call rather
    than the registry read that created the client.

    Args:
        endpoint: gRPC endpoint address as ``host:port``.
        ca_path: Path to the CA certificate for TLS. ``None`` for
            plaintext channels (permitted only in local mode).
        cert_path: Path to the client certificate for mTLS.
            ``None`` for server-only TLS.
        key_path: Path to the client private key for mTLS.
            ``None`` for server-only TLS.
        timeout: Default gRPC call timeout in seconds.
        retry_policy: Optional retry policy; defaults to
            :func:`_default_retry_policy`.
        retry_deadline: Optional total retry budget in seconds; defaults
            to :func:`_default_retry_deadline`.
        require_mtls: Reject plaintext channels when ``True``. The direct
            constructor defaults to ``False`` for local dev; the
            ``from_credentials`` factory defaults to the configured
            ``GatewaySettings.require_mtls``.

    Attributes:
        cert_info (CertInfo | None): Parsed metadata from the most recent
            bundle validation, or ``None`` for plaintext channels.

    Raises:
        GatewayNotConnectedError: If ``require_mtls`` is set but no client
            bundle is provided, or if eager bundle validation fails.
    """

    def __init__(  # noqa: D107
        self,
        endpoint: str,
        *,
        ca_path: pathlib.Path | None = None,
        cert_path: pathlib.Path | None = None,
        key_path: pathlib.Path | None = None,
        timeout: float = 30.0,
        retry_policy: RetryPolicy | None = None,
        retry_deadline: float | None = None,
        require_mtls: bool = False,
    ) -> None:
        self._endpoint = endpoint
        self._timeout = timeout
        self._retry_policy = retry_policy or _default_retry_policy()
        self._retry_deadline = (
            retry_deadline if retry_deadline is not None else _default_retry_deadline()
        )
        self._require_mtls = require_mtls
        self._cert_info: CertInfo | None = None

        has_bundle = bool(ca_path and cert_path and key_path)
        if require_mtls and not has_bundle:
            raise GatewayNotConnectedError(
                f"mTLS required but no client bundle provided for {endpoint!r}"
            )

        if has_bundle:
            ca_bytes = ca_path.read_bytes()  # type: ignore[union-attr]
            cert_bytes = cert_path.read_bytes()  # type: ignore[union-attr]
            key_bytes = key_path.read_bytes()  # type: ignore[union-attr]
            self._cert_info = validate_bundle(
                ca_cert=ca_bytes,
                client_cert=cert_bytes,
                client_key=key_bytes,
                endpoint_host=_endpoint_host(endpoint),
                warn_within_days=_default_cert_warn_days(),
            )
            credentials = grpc.ssl_channel_credentials(
                root_certificates=ca_bytes,
                private_key=key_bytes,
                certificate_chain=cert_bytes,
            )
            self._channel = grpc.secure_channel(endpoint, credentials)
        else:
            self._channel = grpc.insecure_channel(endpoint)

        self._stub = openshell_pb2_grpc.OpenShellStub(self._channel)
        self._inference_stub = inference_pb2_grpc.InferenceStub(self._channel)

        self.sandboxes = SandboxManager(
            self._stub,
            timeout=timeout,
            retry_policy=self._retry_policy,
            retry_deadline=self._retry_deadline,
        )
        self.policies = PolicyManager(self._stub, timeout=timeout)
        self.approvals = ApprovalManager(self._stub, timeout=timeout)
        self.providers = ProviderManager(self._stub, timeout=timeout)

    @classmethod
    def from_credentials(
        cls,
        endpoint: str,
        *,
        ca_cert: bytes | None = None,
        client_cert: bytes | None = None,
        client_key: bytes | None = None,
        timeout: float = 30.0,
        retry_policy: RetryPolicy | None = None,
        retry_deadline: float | None = None,
        require_mtls: bool | None = None,
    ) -> ShoreGuardClient:
        """Connect using raw certificate bytes (from DB or registry).

        Args:
            endpoint: gRPC endpoint address (host:port).
            ca_cert: CA certificate bytes for TLS.
            client_cert: Client certificate bytes for mTLS.
            client_key: Client private key bytes for mTLS.
            timeout: Default gRPC call timeout in seconds.
            retry_policy: Optional retry policy; defaults to
                :func:`_default_retry_policy`.
            retry_deadline: Optional total retry budget in seconds; defaults
                to :func:`_default_retry_deadline`.
            require_mtls: Enforce mTLS. ``None`` (default) reads the setting
                from :class:`GatewaySettings`.

        Returns:
            ShoreGuardClient: Connected client instance.

        Raises:
            GatewayNotConnectedError: If mTLS is required but the bundle is
                missing or fails eager validation.
        """
        instance = cls.__new__(cls)
        instance._endpoint = endpoint
        instance._timeout = timeout
        instance._retry_policy = retry_policy or _default_retry_policy()
        instance._retry_deadline = (
            retry_deadline if retry_deadline is not None else _default_retry_deadline()
        )
        instance._require_mtls = (
            require_mtls if require_mtls is not None else _default_require_mtls()
        )
        instance._cert_info = None

        has_bundle = bool(ca_cert and client_cert and client_key)
        if instance._require_mtls and not has_bundle:
            raise GatewayNotConnectedError(
                f"mTLS required but no client bundle provided for {endpoint!r}"
            )

        if has_bundle:
            ca_bytes: bytes = ca_cert  # type: ignore[assignment]
            cert_bytes: bytes = client_cert  # type: ignore[assignment]
            key_bytes: bytes = client_key  # type: ignore[assignment]
            instance._cert_info = validate_bundle(
                ca_cert=ca_bytes,
                client_cert=cert_bytes,
                client_key=key_bytes,
                endpoint_host=_endpoint_host(endpoint),
                warn_within_days=_default_cert_warn_days(),
            )
            credentials = grpc.ssl_channel_credentials(
                root_certificates=ca_bytes,
                private_key=key_bytes,
                certificate_chain=cert_bytes,
            )
            instance._channel = grpc.secure_channel(endpoint, credentials)
            logger.debug("Creating secure gRPC channel to %s", endpoint)
        else:
            instance._channel = grpc.insecure_channel(endpoint)
            logger.debug("Creating insecure gRPC channel to %s", endpoint)

        instance._stub = openshell_pb2_grpc.OpenShellStub(instance._channel)
        instance._inference_stub = inference_pb2_grpc.InferenceStub(instance._channel)
        instance.sandboxes = SandboxManager(
            instance._stub,
            timeout=timeout,
            retry_policy=instance._retry_policy,
            retry_deadline=instance._retry_deadline,
        )
        instance.policies = PolicyManager(instance._stub, timeout=timeout)
        instance.approvals = ApprovalManager(instance._stub, timeout=timeout)
        instance.providers = ProviderManager(instance._stub, timeout=timeout)
        return instance

    @classmethod
    def from_active_cluster(
        cls,
        *,
        cluster: str | None = None,
        timeout: float = 30.0,
    ) -> ShoreGuardClient:
        """Connect to the active OpenShell gateway using mTLS credentials.

        Args:
            cluster: Cluster name override. Defaults to the active gateway.
            timeout: Default gRPC call timeout in seconds.

        Returns:
            ShoreGuardClient: Connected client instance.

        Raises:
            GatewayNotConnectedError: If gateway metadata is missing or
                invalid.
        """
        cluster_name = cluster or _resolve_active_cluster()
        gateway_dir = openshell_config_dir() / "gateways" / cluster_name

        try:
            metadata = json.loads((gateway_dir / "metadata.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
            raise GatewayNotConnectedError(
                f"Failed to load metadata for gateway '{cluster_name}': {e}"
            ) from e
        gateway_endpoint = metadata.get("gateway_endpoint")
        if not gateway_endpoint:
            raise GatewayNotConnectedError(
                f"Missing 'gateway_endpoint' in metadata for gateway '{cluster_name}'"
            )
        parsed = urlparse(gateway_endpoint)
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
        """Check gateway health.

        Returns:
            dict: Status and version of the gateway.
        """
        resp = self._stub.Health(openshell_pb2.HealthRequest(), timeout=self._timeout)
        status_names = {0: "unspecified", 1: "healthy", 2: "degraded", 3: "unhealthy"}
        return {"status": status_names.get(resp.status, "unknown"), "version": resp.version}

    def get_inference_bundle(self) -> dict:
        """Get the resolved inference bundle (routes after policy overlay).

        API keys are redacted: each route exposes only ``has_api_key`` (bool),
        never the secret value.

        Returns:
            dict: ``{revision, generated_at_ms, routes: [...]}`` where each
                route contains name, base_url, protocols, model_id,
                provider_type, timeout_secs, has_api_key.
        """
        resp = self._inference_stub.GetInferenceBundle(
            inference_pb2.GetInferenceBundleRequest(),
            timeout=self._timeout,
        )
        return {
            "revision": resp.revision,
            "generated_at_ms": resp.generated_at_ms,
            "routes": [
                {
                    "name": r.name,
                    "base_url": r.base_url,
                    "protocols": list(r.protocols),
                    "model_id": r.model_id,
                    "provider_type": r.provider_type,
                    "timeout_secs": r.timeout_secs,
                    "has_api_key": bool(r.api_key),
                }
                for r in resp.routes
            ],
        }

    def get_cluster_inference(self, *, route_name: str = "") -> dict:
        """Get current cluster inference configuration.

        Args:
            route_name: Optional route name to filter by.

        Returns:
            dict: Inference configuration with provider, model, and route.
        """
        resp = self._inference_stub.GetClusterInference(
            inference_pb2.GetClusterInferenceRequest(route_name=route_name),
            timeout=self._timeout,
        )
        return {
            "provider_name": resp.provider_name,
            "model_id": resp.model_id,
            "version": resp.version,
            "route_name": resp.route_name,
            "timeout_secs": resp.timeout_secs,
        }

    def get_gateway_config(self) -> dict:
        """Get the global gateway configuration (settings and revision).

        Returns:
            dict: Settings map and settings revision number.
        """
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

    def update_gateway_setting(
        self,
        *,
        key: str,
        value: str | bool | int | None = None,
        delete: bool = False,
    ) -> dict:
        """Update (or delete) a single global gateway setting.

        Args:
            key: Setting key name.
            value: New value. Type determines the ``SettingValue`` oneof field
                (``bool`` before ``int`` since ``bool`` is a subclass of ``int``).
                Ignored when ``delete`` is True.
            delete: If True, remove the setting instead of updating it.

        Returns:
            dict: ``{"settings_revision": int, "deleted": bool}``.

        Raises:
            TypeError: If ``value`` has an unsupported Python type.
        """
        if delete:
            setting_value = None
        else:
            sv = sandbox_pb2.SettingValue()
            if isinstance(value, bool):
                sv.bool_value = value
            elif isinstance(value, int):
                sv.int_value = value
            elif isinstance(value, str):
                sv.string_value = value
            elif isinstance(value, bytes):
                sv.bytes_value = value
            else:
                raise TypeError(f"Unsupported setting value type: {type(value).__name__}")
            setting_value = sv

        resp = self._stub.UpdateConfig(
            openshell_pb2.UpdateConfigRequest(
                setting_key=key,
                setting_value=setting_value,
                delete_setting=delete,
                **{"global": True},  # type: ignore[arg-type]
            ),
            timeout=self._timeout,
        )
        return {
            "settings_revision": resp.settings_revision,
            "deleted": resp.deleted,
        }

    def set_cluster_inference(
        self,
        *,
        provider_name: str,
        model_id: str,
        verify: bool = True,
        route_name: str = "",
        timeout_secs: int = 0,
    ) -> dict:
        """Set cluster inference configuration.

        Args:
            provider_name: Name of the inference provider.
            model_id: Model identifier to use.
            verify: Whether to validate endpoints before saving.
            route_name: Optional route name to configure.
            timeout_secs: Per-route request timeout in seconds (0 = default 60s).

        Returns:
            dict: Updated inference configuration with validation results.
        """
        resp = self._inference_stub.SetClusterInference(
            inference_pb2.SetClusterInferenceRequest(
                provider_name=provider_name,
                model_id=model_id,
                verify=verify,
                no_verify=not verify,
                route_name=route_name,
                timeout_secs=timeout_secs,
            ),
            timeout=self._timeout,
        )
        result: dict = {
            "provider_name": resp.provider_name,
            "model_id": resp.model_id,
            "version": resp.version,
            "route_name": resp.route_name,
            "timeout_secs": resp.timeout_secs,
        }
        if hasattr(resp, "validation_performed"):
            result["validation_performed"] = resp.validation_performed
        if hasattr(resp, "validated_endpoints"):
            result["validated_endpoints"] = [
                {"host": ve.host, "port": ve.port, "reachable": ve.reachable, "error": ve.error}
                for ve in resp.validated_endpoints
            ]
        return result

    @property
    def cert_info(self) -> CertInfo | None:
        """Return the parsed client-cert metadata for the current channel.

        Returns:
            CertInfo | None: Metadata from the most recent successful bundle
                validation, or ``None`` for plaintext channels.
        """
        return self._cert_info

    def reload_credentials(
        self,
        *,
        ca_cert: bytes,
        client_cert: bytes,
        client_key: bytes,
    ) -> None:
        """Rotate the mTLS bundle by rebuilding the channel and sub-managers.

        Validates the new bundle eagerly (via :func:`validate_bundle`, which
        raises :class:`GatewayNotConnectedError` on failure), closes the
        existing channel, and rebuilds stubs plus every manager so subsequent
        calls run over the fresh credentials. Callers must serialize
        invocations: in-flight streams held by other callers will observe a
        ``ChannelClosed`` error and should reconnect on their own iteration.

        Args:
            ca_cert: New CA certificate bytes.
            client_cert: New client certificate bytes.
            client_key: New client private key bytes.
        """
        self._cert_info = validate_bundle(
            ca_cert=ca_cert,
            client_cert=client_cert,
            client_key=client_key,
            endpoint_host=_endpoint_host(self._endpoint),
            warn_within_days=_default_cert_warn_days(),
        )
        credentials = grpc.ssl_channel_credentials(
            root_certificates=ca_cert,
            private_key=client_key,
            certificate_chain=client_cert,
        )
        old_channel = self._channel
        self._channel = grpc.secure_channel(self._endpoint, credentials)
        self._stub = openshell_pb2_grpc.OpenShellStub(self._channel)
        self._inference_stub = inference_pb2_grpc.InferenceStub(self._channel)
        self.sandboxes = SandboxManager(
            self._stub,
            timeout=self._timeout,
            retry_policy=self._retry_policy,
            retry_deadline=self._retry_deadline,
        )
        self.policies = PolicyManager(self._stub, timeout=self._timeout)
        self.approvals = ApprovalManager(self._stub, timeout=self._timeout)
        self.providers = ProviderManager(self._stub, timeout=self._timeout)
        try:
            old_channel.close()
        except Exception:  # noqa: BLE001
            logger.debug("old channel close raised during reload_credentials", exc_info=True)

    def close(self) -> None:
        """Close the underlying gRPC channel."""
        self._channel.close()

    def __enter__(self) -> ShoreGuardClient:
        """Support usage as a context manager.

        Returns:
            ShoreGuardClient: This client instance.
        """
        return self

    def __exit__(self, *args: object) -> None:
        """Close the channel on context exit.

        Args:
            *args: Exception info (exc_type, exc_val, exc_tb).
        """
        self.close()


def _resolve_active_cluster() -> str:
    """Resolve the active gateway cluster name from env or config file.

    Returns:
        str: Active cluster name.

    Raises:
        GatewayNotConnectedError: If no active gateway is configured.
    """
    env_gateway = os.environ.get("OPENSHELL_GATEWAY")
    if env_gateway:
        return env_gateway
    active_file = openshell_config_dir() / "active_gateway"
    value = active_file.read_text().strip()
    if not value:
        raise GatewayNotConnectedError("No active OpenShell gateway configured")
    return value
