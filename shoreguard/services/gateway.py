"""Gateway connection management and registry-backed discovery."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import grpc

from shoreguard.client import ShoreGuardClient
from shoreguard.config import is_private_ip
from shoreguard.exceptions import GatewayNotConnectedError, NotFoundError
from shoreguard.services.registry import _UNSET, GatewayRegistry

logger = logging.getLogger(__name__)

# ─── Connection state ────────────────────────────────────────────────────────

_BACKOFF_MIN = 5.0
_BACKOFF_MAX = 60.0
_BACKOFF_FACTOR = 2.0


class _ClientEntry:
    """Per-gateway connection state with backoff."""

    __slots__ = ("client", "last_attempt", "backoff")

    def __init__(self) -> None:  # noqa: D107
        self.client: ShoreGuardClient | None = None
        self.last_attempt: float = 0.0
        self.backoff: float = 0.0


_clients: dict[str, _ClientEntry] = {}
_clients_lock = threading.Lock()


def _reset_clients() -> None:
    """Clear all cached gateway clients. For test teardown only."""
    with _clients_lock:
        _clients.clear()


def _derive_status(connected: bool, last_status: str | None) -> str:
    """Derive a single status string from connection and health state.

    Args:
        connected: Whether the gateway is currently connected.
        last_status: Last known health status from the registry.

    Returns:
        str: Derived status string.
    """
    if connected:
        return "connected"
    if last_status in ("healthy", "degraded"):
        return "unreachable"
    return "offline"


# ─── Gateway Service ─────────────────────────────────────────────────────────


class GatewayService:
    """Gateway connection management and registry-backed discovery.

    Handles gRPC client connections, backoff, health probing,
    and gateway registration/unregistration.

    Args:
        registry: Gateway registry for persistence.

    Attributes:
        registry: The underlying gateway registry.
    """

    def __init__(self, registry: GatewayRegistry) -> None:  # noqa: D107
        self._registry = registry

    @property
    def registry(self) -> GatewayRegistry:
        """The underlying gateway registry."""
        return self._registry

    # ── Connection management ─────────────────────────────────────────────

    def get_client(self, name: str) -> ShoreGuardClient:
        """Return a client for the given gateway, attempting reconnect with backoff.

        Args:
            name: Gateway name.

        Returns:
            ShoreGuardClient: Connected gRPC client.

        Raises:
            GatewayNotConnectedError: If connection fails.
        """
        gw_name = name

        # Phase 1: read state under the lock
        with _clients_lock:
            entry = _clients.get(gw_name)
            if entry is None:
                entry = _ClientEntry()
                _clients[gw_name] = entry
            existing_client = entry.client

        # Phase 2: health-check existing client (blocking I/O, no lock)
        if existing_client is not None:
            try:
                existing_client.health()
                return existing_client
            except grpc.RpcError:
                logger.warning("Gateway '%s' connection lost, attempting reconnect...", gw_name)
                try:
                    existing_client.close()
                except Exception:
                    logger.debug("Error closing stale connection for '%s'", gw_name, exc_info=True)
                with _clients_lock:
                    entry.client = None
                    entry.backoff = 0.0

        # Phase 3: check backoff under the lock
        now = time.monotonic()
        with _clients_lock:
            if entry.backoff > 0 and (now - entry.last_attempt) < entry.backoff:
                raise GatewayNotConnectedError(f"Gateway '{gw_name}' not connected.")
            entry.last_attempt = now

        # Phase 4: attempt connection (blocking I/O, no lock)
        new_client = self._try_connect(gw_name)

        # Phase 5: write result under the lock
        with _clients_lock:
            if new_client is None:
                if entry.backoff == 0:
                    entry.backoff = _BACKOFF_MIN
                else:
                    entry.backoff = min(entry.backoff * _BACKOFF_FACTOR, _BACKOFF_MAX)
                raise GatewayNotConnectedError(f"Gateway '{gw_name}' not connected.")
            entry.client = new_client
            entry.backoff = 0.0
        return new_client

    def set_client(self, client: ShoreGuardClient | None, name: str) -> None:
        """Set or clear a client for the given gateway.

        Args:
            client: Client to cache, or None to clear.
            name: Gateway name.
        """
        gw_name = name
        with _clients_lock:
            if client is None:
                _clients.pop(gw_name, None)
                logger.debug("Cleared client for gateway '%s'", gw_name)
            else:
                entry = _clients.get(gw_name)
                if entry is None:
                    entry = _ClientEntry()
                    _clients[gw_name] = entry
                entry.client = client
                entry.backoff = 0.0
                logger.debug("Set client for gateway '%s'", gw_name)

    def reset_backoff(self, name: str) -> None:
        """Reset connection backoff for a gateway.

        Args:
            name: Gateway name.
        """
        gw_name = name
        with _clients_lock:
            if gw_name and gw_name in _clients:
                _clients[gw_name].backoff = 0.0
                _clients[gw_name].last_attempt = 0.0
                logger.debug("Reset backoff for gateway '%s'", gw_name)

    def _try_connect(self, name: str) -> ShoreGuardClient | None:
        """Attempt to create a client for a specific gateway.

        Args:
            name: Gateway name.

        Returns:
            ShoreGuardClient | None: Connected client, or None on failure.
        """
        creds = self._registry.get_credentials(name)
        if creds is not None:
            return self._try_connect_from_registry(name, creds)
        return self._try_connect_from_config(name)

    def _try_connect_from_registry(
        self, name: str, creds: dict[str, str | bytes | None]
    ) -> ShoreGuardClient | None:
        """Connect using credentials from the database.

        Args:
            name: Gateway name.
            creds: Credential dict from the registry.

        Returns:
            ShoreGuardClient | None: Connected client, or None on failure.
        """
        endpoint = str(creds["endpoint"])
        host = endpoint.rsplit(":", 1)[0] if ":" in endpoint else endpoint
        if is_private_ip(host) and not os.environ.get("SHOREGUARD_LOCAL_MODE"):
            logger.warning(
                "Gateway '%s' endpoint '%s' resolves to a private IP — blocking connection",
                name,
                endpoint,
            )
            return None
        ca_cert = creds.get("ca_cert")
        client_cert = creds.get("client_cert")
        client_key = creds.get("client_key")
        try:
            client = ShoreGuardClient.from_credentials(
                endpoint,
                ca_cert=ca_cert if isinstance(ca_cert, bytes) else None,
                client_cert=client_cert if isinstance(client_cert, bytes) else None,
                client_key=client_key if isinstance(client_key, bytes) else None,
            )
        except (grpc.RpcError, OSError, ConnectionError, TimeoutError) as e:
            logger.debug("Gateway '%s' connection failed (type=%s): %s", name, type(e).__name__, e)
            return None
        try:
            client.health()
            logger.info("Connected to OpenShell gateway '%s'", name)
            return client
        except (grpc.RpcError, OSError, ConnectionError, TimeoutError) as e:
            logger.debug(
                "Gateway '%s' health check failed (type=%s): %s",
                name,
                type(e).__name__,
                e,
            )
            try:
                client.close()
            except (grpc.RpcError, OSError):
                logger.debug("Failed to close client for '%s'", name)
            return None

    def _try_connect_from_config(self, name: str) -> ShoreGuardClient | None:
        """Fallback: connect using filesystem config (local mode / backward compat).

        Args:
            name: Gateway name.

        Returns:
            ShoreGuardClient | None: Connected client, or None on failure.
        """
        import json

        try:
            client = ShoreGuardClient.from_active_cluster(cluster=name)
        except (
            grpc.RpcError,
            GatewayNotConnectedError,
            OSError,
            ConnectionError,
            TimeoutError,
            KeyError,
            ValueError,
            json.JSONDecodeError,
        ) as e:
            logger.debug("Gateway '%s' connection failed: %s", name, e, exc_info=True)
            return None
        try:
            client.health()
            logger.info("Connected to OpenShell gateway '%s'", name)
            return client
        except (grpc.RpcError, OSError, ConnectionError, TimeoutError) as e:
            logger.debug("Gateway '%s' health check failed: %s", name, e, exc_info=True)
            try:
                client.close()
            except (grpc.RpcError, OSError):
                logger.debug("Failed to close client for '%s'", name)
            return None

    # ── Registration ─────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        endpoint: str,
        scheme: str = "https",
        auth_mode: str | None = "mtls",
        *,
        ca_cert: bytes | None = None,
        client_cert: bytes | None = None,
        client_key: bytes | None = None,
        metadata: dict[str, Any] | None = None,
        description: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Register a gateway and attempt initial connection.

        Args:
            name: Unique gateway name.
            endpoint: Gateway endpoint address.
            scheme: Connection scheme (e.g. "https").
            auth_mode: Authentication mode (e.g. "mtls").
            ca_cert: CA certificate bytes for TLS.
            client_cert: Client certificate bytes for mTLS.
            client_key: Client private key bytes for mTLS.
            metadata: Optional metadata dict.
            description: Optional free-text description.
            labels: Optional key-value labels for filtering.

        Returns:
            dict[str, Any]: Gateway record with connection status.
        """
        logger.info("Registering gateway '%s' (endpoint=%s)", name, endpoint)
        record = self._registry.register(
            name,
            endpoint,
            scheme,
            auth_mode,
            ca_cert=ca_cert,
            client_cert=client_cert,
            client_key=client_key,
            metadata=metadata,
            description=description,
            labels=labels,
        )

        # Attempt connection to validate
        connected = False
        try:
            self.get_client(name=name)
            connected = True
        except (GatewayNotConnectedError, grpc.RpcError):
            logger.debug("Could not connect to newly registered gateway '%s'", name)

        record["connected"] = connected
        record["status"] = "connected" if connected else "unreachable"

        return record

    def unregister(self, name: str) -> bool:
        """Unregister a gateway and close its connection.

        Args:
            name: Gateway name.

        Returns:
            bool: True if the gateway existed and was removed.
        """
        logger.info("Unregistering gateway '%s'", name)
        self.set_client(None, name=name)
        return self._registry.unregister(name)

    def test_connection(self, name: str) -> dict[str, Any]:
        """Explicitly test connectivity to a registered gateway.

        Args:
            name: Gateway name.

        Returns:
            dict[str, Any]: Connection test result.

        Raises:
            NotFoundError: If the gateway is not registered.
        """
        record = self._registry.get(name)
        if record is None:
            raise NotFoundError(f"Gateway '{name}' not registered")

        self.reset_backoff(name)
        try:
            client = self.get_client(name=name)
            health = client.health()
            return {
                "success": True,
                "connected": True,
                "version": health.get("version"),
                "health_status": health.get("status"),
            }
        except (GatewayNotConnectedError, grpc.RpcError) as e:
            return {"success": False, "connected": False, "error": str(e)}

    # ── List & Info ───────────────────────────────────────────────────────

    def update_gateway_metadata(
        self,
        name: str,
        *,
        description: str | None | object = _UNSET,
        labels: dict[str, str] | None | object = _UNSET,
    ) -> dict[str, Any]:
        """Update description and/or labels for a gateway.

        Args:
            name: Gateway name.
            description: New description, None to clear, or sentinel to skip.
            labels: New labels dict, None to clear, or sentinel to skip.

        Returns:
            dict[str, Any]: Updated gateway record.

        Raises:
            NotFoundError: If the gateway does not exist.
        """
        kwargs: dict[str, Any] = {}
        if description is not _UNSET:
            kwargs["description"] = description
        if labels is not _UNSET:
            kwargs["labels"] = labels
        result = self._registry.update_gateway_metadata(name, **kwargs)
        if result is None:
            raise NotFoundError(f"Gateway '{name}' not found")
        return result

    def list_all(self, *, labels_filter: dict[str, str] | None = None) -> list[dict[str, Any]]:
        """List all registered gateways with cached connection status.

        Uses the cached client state instead of live health probes to avoid
        N+1 blocking gRPC calls.  The background health monitor keeps
        ``last_status`` up-to-date.

        Args:
            labels_filter: If provided, only return gateways matching all
                specified label key-value pairs.

        Returns:
            list[dict[str, Any]]: Gateway records with connection status.
        """
        gateways = self._registry.list_all(labels_filter=labels_filter)

        for gw in gateways:
            with _clients_lock:
                cached = _clients.get(gw["name"])
                connected = cached is not None and cached.client is not None

            gw["connected"] = connected
            gw["status"] = _derive_status(connected, gw.get("last_status"))

        return gateways

    def get_info(self, name: str) -> dict[str, Any]:
        """Get detailed info for a gateway.

        Args:
            name: Gateway name.

        Returns:
            dict[str, Any]: Detailed gateway information.
        """
        record = self._registry.get(name)
        if record is None:
            return {"configured": False, "error": f"Gateway '{name}' not registered"}

        record["configured"] = True

        connected = False
        version = None
        with _clients_lock:
            cached = _clients.get(name)
            cached_client = cached.client if cached else None
        if cached_client is not None:
            try:
                health = cached_client.health()
                connected = True
                version = health.get("version")
            except grpc.RpcError:
                self.set_client(None, name=name)

        record["connected"] = connected
        if version:
            record["version"] = version
        record["status"] = _derive_status(connected, record.get("last_status"))
        return record

    def get_config(self, name: str) -> dict[str, Any]:
        """Fetch the gateway configuration via gRPC.

        Args:
            name: Gateway name.

        Returns:
            dict[str, Any]: Gateway configuration.
        """
        client = self.get_client(name=name)
        return client.get_gateway_config()

    # ── Health monitor ────────────────────────────────────────────────────

    def check_all_health(self) -> None:
        """Probe all registered gateways and update their health in the registry."""
        from datetime import UTC, datetime

        gateways = self._registry.list_all()
        if not gateways:
            return
        logger.debug("Starting health check for %d gateway(s)", len(gateways))
        for gw in gateways:
            name = gw["name"]
            try:
                client = self.get_client(name=name)
                health = client.health()
                status = health.get("status", "unknown")
            except (GatewayNotConnectedError, grpc.RpcError) as e:
                logger.debug("Health probe failed for '%s': %s", name, e)
                status = "unreachable"
            try:
                self._registry.update_health(name, status, datetime.now(UTC))
            except Exception:
                logger.warning("Failed to update health for '%s'", name, exc_info=True)

    def get_cached_client(self, name: str) -> ShoreGuardClient | None:
        """Return the cached client for a gateway, or None if not connected.

        Args:
            name: Gateway name.

        Returns:
            ShoreGuardClient | None: Cached client, or None.
        """
        with _clients_lock:
            entry = _clients.get(name)
            if entry is not None and entry.client is not None:
                return entry.client
        return None


# Module-level reference — set during app lifespan (see shoreguard.api.main).
gateway_service: GatewayService | None = None
