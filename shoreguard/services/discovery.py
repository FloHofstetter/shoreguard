"""MicroVM gateway discovery via DNS SRV (M22).

Periodically queries DNS for ``_openshell._tcp.<domain>`` SRV records and
auto-registers any newly seen endpoints in the gateway registry. Discovery
runs both as an explicit ``POST /api/gateways/discover`` trigger and as a
background loop driven from ``shoreguard.api.main`` (analogous to
``_health_monitor``).

OpenShell does not advertise SRV records itself today; this is the
ShoreGuard-side hook so a fleet of MicroVM gateways behind a DNS façade
(or a CoreDNS plugin) can register without manual ``shoreguard gateway
register`` calls.
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from shoreguard.api.routes.gateway import _validate_endpoint_format

if TYPE_CHECKING:
    from shoreguard.services.gateway import GatewayService
    from shoreguard.services.registry import GatewayRegistry
    from shoreguard.settings import DiscoverySettings

logger = logging.getLogger(__name__)

# Module-level singleton populated during app lifespan startup.
discovery_service: DiscoveryService | None = None

_SRV_LABEL = "_openshell._tcp"
_NAME_SANITIZE = re.compile(r"[^a-zA-Z0-9._-]+")


@dataclass(frozen=True)
class DiscoveredEndpoint:
    """A single SRV record returned from a discovery scan.

    Attributes:
        host: Resolved target host (FQDN or short name).
        port: TCP port from the SRV record.
        priority: SRV priority (lower = preferred).
        weight: SRV weight within the same priority bucket.
        source_domain: The base domain that produced this record.
        endpoint: Convenience ``host:port`` form (computed property).
    """

    host: str
    port: int
    priority: int
    weight: int
    source_domain: str

    @property
    def endpoint(self) -> str:
        """Return the ``host:port`` form used by the gateway registry.

        Returns:
            str: ``host:port`` string.
        """
        return f"{self.host}:{self.port}"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation.

        Returns:
            dict[str, Any]: Plain dict suitable for API responses.
        """
        return {
            "host": self.host,
            "port": self.port,
            "priority": self.priority,
            "weight": self.weight,
            "source_domain": self.source_domain,
            "endpoint": self.endpoint,
        }


class DiscoveryService:
    """Discover OpenShell gateways via DNS SRV and auto-register them.

    Args:
        registry: Gateway registry used to dedupe + persist new gateways.
        gateway_service: High-level gateway service used for the actual
            ``register()`` call (so the discovery path goes through the
            same validation as the manual API).
        settings: Active ``DiscoverySettings`` instance.
    """

    def __init__(  # noqa: D107
        self,
        registry: GatewayRegistry,
        gateway_service: GatewayService,
        settings: DiscoverySettings,
    ) -> None:
        self._registry = registry
        self._gateway_service = gateway_service
        self._settings = settings
        self._last_run_at: datetime.datetime | None = None
        self._last_result: dict[str, Any] | None = None

    # ----------------------------------------------------------------- queries

    def discover_domain(self, domain: str) -> list[DiscoveredEndpoint]:
        """Resolve ``_openshell._tcp.<domain>`` and return SRV targets.

        DNS failures (NXDOMAIN, no answer, timeout) return an empty list
        with a warning log so a single broken domain does not poison the
        whole scan.

        Args:
            domain: Base domain to query.

        Returns:
            list[DiscoveredEndpoint]: Sorted (priority, -weight) targets.
        """
        # Imported lazily so the rest of the module loads even when
        # dnspython is not yet installed in some sandbox environment.
        import dns.exception
        import dns.resolver

        qname = f"{_SRV_LABEL}.{domain}".rstrip(".")
        resolver = dns.resolver.Resolver()
        resolver.lifetime = float(self._settings.resolver_timeout_seconds)
        try:
            answer = resolver.resolve(qname, "SRV")
        except dns.resolver.NXDOMAIN:
            logger.info("discovery: NXDOMAIN for %s", qname)
            return []
        except dns.resolver.NoAnswer:
            logger.info("discovery: no SRV records at %s", qname)
            return []
        except dns.exception.Timeout:
            logger.warning("discovery: DNS timeout for %s", qname)
            return []
        except dns.exception.DNSException:
            logger.warning("discovery: DNS error for %s", qname, exc_info=True)
            return []

        results: list[DiscoveredEndpoint] = []
        for rdata in answer:
            target = str(rdata.target).rstrip(".")
            if not target:
                continue
            results.append(
                DiscoveredEndpoint(
                    host=target,
                    port=int(rdata.port),
                    priority=int(rdata.priority),
                    weight=int(rdata.weight),
                    source_domain=domain,
                )
            )
        results.sort(key=lambda r: (r.priority, -r.weight, r.host))
        return results

    def discover_all(
        self, *, domains: list[str] | None = None
    ) -> dict[str, list[DiscoveredEndpoint]]:
        """Run a discovery scan against every configured domain.

        Args:
            domains: Optional explicit override; defaults to
                ``settings.discovery.domains``.

        Returns:
            dict[str, list[DiscoveredEndpoint]]: Per-domain results.
        """
        targets = domains if domains is not None else list(self._settings.domains)
        return {domain: self.discover_domain(domain) for domain in targets}

    # --------------------------------------------------------------- registration

    def auto_register(
        self,
        endpoints: list[DiscoveredEndpoint],
    ) -> dict[str, Any]:
        """Persist newly discovered endpoints into the gateway registry.

        Endpoints already known to the registry are skipped. Endpoints
        that fail the standard ``_validate_endpoint_format`` guard
        (private IPs outside ``*.svc.cluster.local`` while not in local
        mode) are skipped with a warning.

        Args:
            endpoints: SRV scan results from ``discover_domain``.

        Returns:
            dict[str, Any]: ``{registered, skipped, errors}`` summary.
        """
        existing = {gw.get("endpoint") for gw in self._registry.list_all()}
        registered: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for ep in endpoints:
            endpoint_str = ep.endpoint
            if endpoint_str in existing:
                skipped.append({**ep.to_dict(), "reason": "already_registered"})
                continue
            try:
                _validate_endpoint_format(endpoint_str)
            except ValueError as exc:
                logger.info(
                    "discovery: rejecting endpoint %s — %s",
                    endpoint_str,
                    exc,
                )
                skipped.append({**ep.to_dict(), "reason": str(exc)})
                continue

            name = self._derive_name(ep)
            try:
                self._gateway_service.register(
                    name=name,
                    endpoint=endpoint_str,
                    scheme=self._settings.default_scheme,
                    auth_mode="none",
                    metadata={
                        "discovered_via": ep.source_domain,
                        "discovered_at": datetime.datetime.now(datetime.UTC).isoformat(),
                        "srv_priority": ep.priority,
                        "srv_weight": ep.weight,
                    },
                    description=f"Auto-discovered via {ep.source_domain}",
                    labels={"source": "discovery", "domain": ep.source_domain},
                )
                registered.append({**ep.to_dict(), "name": name})
                existing.add(endpoint_str)
            except Exception as exc:  # noqa: BLE001 - non-fatal per-endpoint
                logger.warning(
                    "discovery: failed to register %s as %s: %s",
                    endpoint_str,
                    name,
                    exc,
                )
                errors.append({**ep.to_dict(), "name": name, "error": str(exc)})

        return {
            "registered": registered,
            "skipped": skipped,
            "errors": errors,
        }

    def run_once(self, *, domains: list[str] | None = None) -> dict[str, Any]:
        """Run a single discovery + register cycle.

        Args:
            domains: Optional explicit domain list (overrides settings).

        Returns:
            dict[str, Any]: ``{discovered, registered, skipped, errors}``.
        """
        scan = self.discover_all(domains=domains)
        flat: list[DiscoveredEndpoint] = []
        for items in scan.values():
            flat.extend(items)
        if self._settings.auto_register:
            outcome = self.auto_register(flat)
        else:
            outcome = {
                "registered": [],
                "skipped": [ep.to_dict() for ep in flat],
                "errors": [],
            }
        result = {
            "discovered": [ep.to_dict() for ep in flat],
            **outcome,
        }
        self._last_run_at = datetime.datetime.now(datetime.UTC)
        self._last_result = result
        return result

    # -------------------------------------------------------------------- info

    def status(self) -> dict[str, Any]:
        """Return the most recent run summary for the status endpoint.

        Returns:
            dict[str, Any]: Counters + config snapshot for the UI.
        """
        return {
            "enabled": self._settings.enabled,
            "domains": list(self._settings.domains),
            "interval_seconds": self._settings.interval_seconds,
            "auto_register": self._settings.auto_register,
            "last_run_at": (self._last_run_at.isoformat() if self._last_run_at else None),
            "last_registered_count": (
                len(self._last_result["registered"]) if self._last_result else 0
            ),
            "last_skipped_count": (len(self._last_result["skipped"]) if self._last_result else 0),
        }

    @staticmethod
    def _derive_name(ep: DiscoveredEndpoint) -> str:
        """Derive a deterministic gateway name from a discovered endpoint.

        Args:
            ep: The discovered SRV endpoint.

        Returns:
            str: Sanitised name (max 253 chars) for ``GatewayRegistry``.
        """
        base = ep.host.split(".")[0] or ep.host
        cleaned = _NAME_SANITIZE.sub("-", base).strip("-") or "discovered"
        if ep.port not in (443, 30051):
            cleaned = f"{cleaned}-{ep.port}"
        return cleaned[:253]
