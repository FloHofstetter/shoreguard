"""Live gRPC channel management for registered gateways.

Companion to :mod:`shoreguard.services.registry`: the registry
owns *what* gateways exist, this module owns the actual live
:class:`ShoreGuardClient` instances connected to each one.
Provides lazy client lookup, connection health probing, graceful
channel shutdown on gateway removal, and an in-memory cache so
every API call does not pay the mTLS handshake cost.

Separating client lifecycle from persistent CRUD means registry
edits (e.g. rename, label change) never need to tear down an
in-flight call, and a channel failure only affects the one
gateway rather than wedging the whole service.

Concurrency: the service is async-native and confined to one event
loop. Cache reads/writes happen in synchronous sections (atomic under
asyncio), while connection attempts and health probes await between
phases — the per-entry backoff state tolerates interleaving exactly as
it previously tolerated lock releases between phases.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import grpc

from shoreguard.client import ShoreGuardClient
from shoreguard.config import is_private_ip
from shoreguard.exceptions import GatewayNotConnectedError, NotFoundError
from shoreguard.services.registry import _UNSET, GatewayRegistry
from shoreguard.settings import get_settings

logger = logging.getLogger(__name__)

# ─── Connection state ────────────────────────────────────────────────────────


class _ClientEntry:
    """Per-gateway connection state with backoff."""

    __slots__ = ("client", "last_attempt", "backoff")

    def __init__(self) -> None:  # noqa: D107
        self.client: ShoreGuardClient | None = None
        self.last_attempt: float = 0.0
        self.backoff: float = 0.0


def _publish_cert_expiry_gauge(name: str, client: ShoreGuardClient) -> None:
    """Best-effort: publish the cert expiry gauge after a successful connect.

    Args:
        name: Gateway name.
        client: Freshly connected client with optional :attr:`cert_info`.
    """
    if client.cert_info is None:
        return
    try:
        from shoreguard.api.metrics import record_gateway_cert_expiry

        record_gateway_cert_expiry(name, client.cert_info.seconds_until_expiry)
    except Exception:  # noqa: BLE001
        logger.debug("cert-expiry gauge update failed for '%s'", name, exc_info=True)


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
        # Per-gateway connection cache with backoff state. Instance state so
        # each container (prod app, each test) owns its own connections.
        self._clients: dict[str, _ClientEntry] = {}

    @property
    def registry(self) -> GatewayRegistry:
        """The underlying gateway registry."""
        return self._registry

    # ── Connection management ─────────────────────────────────────────────

    async def get_client(self, name: str) -> ShoreGuardClient:
        """Return a client for the given gateway, attempting reconnect with backoff.

        Args:
            name: Gateway name.

        Returns:
            ShoreGuardClient: Connected gRPC client.

        Raises:
            GatewayNotConnectedError: If connection fails.
        """
        gw_name = name

        # Phase 1: read state (atomic — no awaits)
        entry = self._clients.get(gw_name)
        if entry is None:
            entry = _ClientEntry()
            self._clients[gw_name] = entry
        existing_client = entry.client

        # Phase 2: health-check existing client (awaits)
        if existing_client is not None:
            try:
                await existing_client.health()
                return existing_client
            except grpc.RpcError:
                logger.warning("Gateway '%s' connection lost, attempting reconnect...", gw_name)
                try:
                    await existing_client.close()
                except Exception:
                    logger.debug("Error closing stale connection for '%s'", gw_name, exc_info=True)
                entry.client = None
                entry.backoff = 0.0

        # Phase 3: check backoff (atomic)
        now = time.monotonic()
        if entry.backoff > 0 and (now - entry.last_attempt) < entry.backoff:
            raise GatewayNotConnectedError(f"Gateway '{gw_name}' not connected.")
        entry.last_attempt = now

        # Phase 4: attempt connection (awaits)
        new_client = await self._try_connect(gw_name)

        # Phase 5: write result (atomic)
        if new_client is None:
            gw_cfg = get_settings().gateway
            if entry.backoff == 0:
                entry.backoff = gw_cfg.backoff_min
            else:
                entry.backoff = min(entry.backoff * gw_cfg.backoff_factor, gw_cfg.backoff_max)
            raise GatewayNotConnectedError(f"Gateway '{gw_name}' not connected.")
        entry.client = new_client
        entry.backoff = 0.0
        logger.info("Gateway '%s' reconnected successfully", gw_name)
        return new_client

    def set_client(self, client: ShoreGuardClient | None, name: str) -> None:
        """Set or clear a client for the given gateway.

        Args:
            client: Client to cache, or None to clear.
            name: Gateway name.
        """
        gw_name = name
        if client is None:
            self._clients.pop(gw_name, None)
            logger.debug("Cleared client for gateway '%s'", gw_name)
        else:
            entry = self._clients.get(gw_name)
            if entry is None:
                entry = _ClientEntry()
                self._clients[gw_name] = entry
            entry.client = client
            entry.backoff = 0.0
            logger.debug("Set client for gateway '%s'", gw_name)

    def reset_backoff(self, name: str) -> None:
        """Reset connection backoff for a gateway.

        Args:
            name: Gateway name.
        """
        gw_name = name
        if gw_name and gw_name in self._clients:
            self._clients[gw_name].backoff = 0.0
            self._clients[gw_name].last_attempt = 0.0
            logger.debug("Reset backoff for gateway '%s'", gw_name)

    async def _try_connect(self, name: str) -> ShoreGuardClient | None:
        """Attempt to create a client for a specific gateway.

        Args:
            name: Gateway name.

        Returns:
            ShoreGuardClient | None: Connected client, or None on failure.
        """
        creds = await self._registry.get_credentials(name)
        if creds is not None:
            return await self._try_connect_from_registry(name, creds)
        return await self._try_connect_from_config(name)

    async def _try_connect_from_registry(
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
        # Mirror the register-time bypass: trust *.svc.cluster.local
        # (kube-dns / CoreDNS is authoritative, not user-controllable,
        # so DNS-rebinding is not possible against this suffix).
        is_cluster_dns = host.lower().endswith(".svc.cluster.local")
        is_local_mode = get_settings().server.local_mode
        is_private = is_private_ip(host)
        if not is_cluster_dns and is_private and not is_local_mode:
            logger.warning(
                "Gateway '%s' endpoint '%s' resolves to a private IP — blocking connection",
                name,
                endpoint,
            )
            return None
        ca_cert = creds.get("ca_cert")
        client_cert = creds.get("client_cert")
        client_key = creds.get("client_key")
        has_bundle = (
            isinstance(ca_cert, bytes)
            and isinstance(client_cert, bytes)
            and isinstance(client_key, bytes)
        )
        # Solo-dev on-ramp: in local mode a loopback/private gateway registered
        # without a cert bundle connects in plaintext. This mirrors the private-IP
        # SSRF bypass above — local mode means "trust my local box", and certs are
        # exactly the ceremony a single-box dev avoids by using the OpenShell TUI.
        # Strictly gated on local_mode + a private/loopback host, so production
        # (require_mtls default True, public endpoints) is unaffected.
        require_mtls = None
        if is_local_mode and is_private and not has_bundle:
            require_mtls = False
            logger.warning(
                "Gateway '%s' (%s): local mode, connecting without mTLS (plaintext)",
                name,
                endpoint,
            )
        try:
            client = ShoreGuardClient.from_credentials(
                endpoint,
                ca_cert=ca_cert if isinstance(ca_cert, bytes) else None,
                client_cert=client_cert if isinstance(client_cert, bytes) else None,
                client_key=client_key if isinstance(client_key, bytes) else None,
                require_mtls=require_mtls,
            )
        except (grpc.RpcError, OSError, ConnectionError, TimeoutError) as e:
            logger.debug("Gateway '%s' connection failed (type=%s): %s", name, type(e).__name__, e)
            return None
        try:
            await client.health()
            logger.info("Connected to OpenShell gateway '%s'", name)
            _publish_cert_expiry_gauge(name, client)
            return client
        except (grpc.RpcError, OSError, ConnectionError, TimeoutError) as e:
            logger.debug(
                "Gateway '%s' health check failed (type=%s): %s",
                name,
                type(e).__name__,
                e,
            )
            try:
                await client.close()
            except grpc.RpcError, OSError:
                logger.debug("Failed to close client for '%s'", name)
            return None

    async def _try_connect_from_config(self, name: str) -> ShoreGuardClient | None:
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
            await client.health()
            logger.info("Connected to OpenShell gateway '%s'", name)
            return client
        except (grpc.RpcError, OSError, ConnectionError, TimeoutError) as e:
            logger.debug("Gateway '%s' health check failed: %s", name, e, exc_info=True)
            try:
                await client.close()
            except grpc.RpcError, OSError:
                logger.debug("Failed to close client for '%s'", name)
            return None

    # ── Registration ─────────────────────────────────────────────────────

    async def register(
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
        record = await self._registry.register(
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
            await self.get_client(name=name)
            connected = True
        except GatewayNotConnectedError, grpc.RpcError:
            logger.debug("Could not connect to newly registered gateway '%s'", name)

        record["connected"] = connected
        record["status"] = "connected" if connected else "unreachable"

        return record

    async def unregister(self, name: str) -> bool:
        """Unregister a gateway and close its connection.

        Args:
            name: Gateway name.

        Returns:
            bool: True if the gateway existed and was removed.
        """
        logger.info("Unregistering gateway '%s'", name)
        self.set_client(None, name=name)
        return await self._registry.unregister(name)

    async def test_connection(self, name: str) -> dict[str, Any]:
        """Explicitly test connectivity to a registered gateway.

        Args:
            name: Gateway name.

        Returns:
            dict[str, Any]: Connection test result.

        Raises:
            NotFoundError: If the gateway is not registered.
        """
        record = await self._registry.get(name)
        if record is None:
            raise NotFoundError(f"Gateway '{name}' not registered")

        self.reset_backoff(name)
        try:
            client = await self.get_client(name=name)
            health = await client.health()
            return {
                "success": True,
                "connected": True,
                "version": health.get("version"),
                "health_status": health.get("status"),
            }
        except (GatewayNotConnectedError, grpc.RpcError) as e:
            return {"success": False, "connected": False, "error": str(e)}

    # ── List & Info ───────────────────────────────────────────────────────

    async def update_gateway_metadata(
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
        result = await self._registry.update_gateway_metadata(name, **kwargs)
        if result is None:
            raise NotFoundError(f"Gateway '{name}' not found")
        return result

    async def list_all(
        self, *, labels_filter: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
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
        gateways = await self._registry.list_all(labels_filter=labels_filter)

        for gw in gateways:
            cached = self._clients.get(gw["name"])
            connected = cached is not None and cached.client is not None
            gw["connected"] = connected
            gw["status"] = _derive_status(connected, gw.get("last_status"))

        return gateways

    async def get_info(self, name: str) -> dict[str, Any]:
        """Get detailed info for a gateway.

        Args:
            name: Gateway name.

        Returns:
            dict[str, Any]: Detailed gateway information.
        """
        record = await self._registry.get(name)
        if record is None:
            return {"configured": False, "error": f"Gateway '{name}' not registered"}

        record["configured"] = True

        connected = False
        version = None
        cached = self._clients.get(name)
        cached_client = cached.client if cached else None
        if cached_client is not None:
            try:
                health = await cached_client.health()
                connected = True
                version = health.get("version")
            except grpc.RpcError:
                self.set_client(None, name=name)

        record["connected"] = connected
        if version:
            record["version"] = version
        record["status"] = _derive_status(connected, record.get("last_status"))
        return record

    async def get_config(self, name: str) -> dict[str, Any]:
        """Fetch the gateway configuration via gRPC.

        Args:
            name: Gateway name.

        Returns:
            dict[str, Any]: Gateway configuration.
        """
        client = await self.get_client(name=name)
        return await client.get_gateway_config()

    async def update_setting(
        self,
        name: str,
        key: str,
        value: str | bool | int | None = None,
        *,
        delete: bool = False,
    ) -> dict[str, Any]:
        """Update (or delete) a single global gateway setting via gRPC.

        Args:
            name: Gateway name.
            key: Setting key.
            value: New value. Ignored when ``delete`` is True.
            delete: If True, remove the setting instead of updating it.

        Returns:
            dict[str, Any]: ``{"settings_revision": int, "deleted": bool}``.
        """
        client = await self.get_client(name=name)
        return await client.update_gateway_setting(key=key, value=value, delete=delete)

    # ── Health monitor ────────────────────────────────────────────────────

    async def check_all_health(self) -> None:
        """Probe all registered gateways concurrently and persist their health."""
        from datetime import UTC, datetime

        gateways = await self._registry.list_all()
        if not gateways:
            return
        logger.debug("Starting health check for %d gateway(s)", len(gateways))

        async def _probe(name: str) -> None:
            try:
                client = await self.get_client(name=name)
                health = await client.health()
                status = health.get("status", "unknown")
            except (GatewayNotConnectedError, grpc.RpcError) as e:
                logger.debug("Health probe failed for '%s': %s", name, e)
                status = "unreachable"
            try:
                await self._registry.update_health(name, status, datetime.now(UTC))
            except Exception:
                logger.warning("Failed to update health for '%s'", name, exc_info=True)

        await asyncio.gather(*(_probe(gw["name"]) for gw in gateways))

    def get_cached_client(self, name: str) -> ShoreGuardClient | None:
        """Return the cached client for a gateway, or None if not connected.

        Args:
            name: Gateway name.

        Returns:
            ShoreGuardClient | None: Cached client, or None.
        """
        entry = self._clients.get(name)
        if entry is not None and entry.client is not None:
            return entry.client
        return None
