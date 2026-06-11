"""Fleet view — the moment the second box arrives.

The land-and-expand thesis: ShoreGuard wins single-box homelabs first,
then the second Spark shows up and suddenly the questions are
cross-gateway: are both boxes healthy and on the same OpenShell
version? Do same-named sandboxes run the same policy? This service
answers them and offers the one bulk action that matters — pushing one
sandbox's policy to its namesakes on other gateways.

Everything degrades gracefully per gateway: an unreachable box appears
with its registry status and contributes nothing else, it never blocks
the rest of the fleet.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shoreguard.services.gateway import GatewayService
    from shoreguard.services.registry import GatewayRegistry

logger = logging.getLogger(__name__)

# Keys of a policy dict that are actual policy content (vs. response
# metadata like version/status that must not be pushed to a target).
_POLICY_CONTENT_KEYS = ("filesystem", "process", "landlock", "network_policies")


class FleetService:
    """Cross-gateway overview, policy drift, and policy sync.

    Args:
        registry: Persistent gateway registry (what gateways exist).
        gateway_service: Live connections (clients, health, versions).
    """

    def __init__(self, registry: GatewayRegistry, gateway_service: GatewayService) -> None:  # noqa: D107
        self._registry = registry
        self._gateways = gateway_service

    async def overview(self) -> list[dict[str, Any]]:
        """Collect per-gateway status, version, and sandbox policy hashes.

        Returns:
            list[dict[str, Any]]: One entry per registered gateway with
            ``name``, ``status``, ``version``, ``reachable``,
            ``sandbox_count``, and ``sandboxes`` (name → policy hash,
            empty string when the sandbox has no readable policy).
        """
        gateways = await self._registry.list_all()
        versions = self._gateways.known_versions()

        async def _collect(gw: dict[str, Any]) -> dict[str, Any]:
            name = gw["name"]
            entry: dict[str, Any] = {
                "name": name,
                "status": gw.get("last_status") or "unknown",
                "version": versions.get(name),
                "reachable": False,
                "sandbox_count": 0,
                "sandboxes": {},
            }
            try:
                client = await self._gateways.get_client(name)
                sandboxes = await client.sandboxes.list(limit=1000)
            except Exception as exc:  # noqa: BLE001 — one dead box must not block the fleet
                logger.debug("Fleet overview: gateway %s unreachable: %s", name, exc)
                return entry
            entry["reachable"] = True
            hashes: dict[str, str] = {}
            for sb in sandboxes:
                sb_name = sb.get("name") or sb.get("sandbox_name")
                if not sb_name:
                    continue
                try:
                    snapshot = await client.policies.get(sb_name)
                    revision = snapshot.get("revision") or {}
                    hashes[sb_name] = str(revision.get("policy_hash") or "")
                except Exception:  # noqa: BLE001 — sandbox without policy
                    hashes[sb_name] = ""
            entry["sandboxes"] = hashes
            entry["sandbox_count"] = len(hashes)
            return entry

        return list(await asyncio.gather(*(_collect(gw) for gw in gateways)))

    async def policy_drift(self) -> list[dict[str, Any]]:
        """Find same-named sandboxes whose policies differ across gateways.

        Returns:
            list[dict[str, Any]]: One entry per sandbox name present on
            two or more reachable gateways: ``sandbox``, ``hashes``
            (gateway → policy hash), and ``drifted``.
        """
        overview = await self.overview()
        by_sandbox: dict[str, dict[str, str]] = {}
        for gw in overview:
            if not gw["reachable"]:
                continue
            for sb_name, policy_hash in gw["sandboxes"].items():
                by_sandbox.setdefault(sb_name, {})[gw["name"]] = policy_hash

        result = []
        for sb_name, hashes in sorted(by_sandbox.items()):
            if len(hashes) < 2:
                continue
            distinct = {h for h in hashes.values()}
            result.append(
                {
                    "sandbox": sb_name,
                    "hashes": hashes,
                    "drifted": len(distinct) > 1,
                }
            )
        return result

    async def sync_policy(
        self, *, source_gateway: str, sandbox: str, targets: list[str]
    ) -> dict[str, Any]:
        """Push one sandbox's policy to its namesakes on other gateways.

        Args:
            source_gateway: Gateway whose policy is the source of truth.
            sandbox: Sandbox name (must exist on source and targets).
            targets: Gateways to push to.

        Returns:
            dict[str, Any]: Per-target results and errors.

        Raises:
            ValueError: If the source policy cannot be read, a target
                equals the source, or no policy content is present.
        """
        from shoreguard.services.policy import PolicyService

        if source_gateway in targets:
            raise ValueError("source gateway cannot be among the targets")

        source_client = await self._gateways.get_client(source_gateway)
        snapshot = await PolicyService(source_client).get(sandbox)
        policy = snapshot.get("policy") or {}
        content = {k: policy[k] for k in _POLICY_CONTENT_KEYS if k in policy}
        if not content:
            raise ValueError(
                f"Sandbox '{sandbox}' on '{source_gateway}' has no readable policy content"
            )

        results: dict[str, Any] = {"synced": [], "errors": {}}
        for target in targets:
            try:
                client = await self._gateways.get_client(target)
                await PolicyService(client).update(sandbox, dict(content))
                results["synced"].append(target)
            except Exception as exc:  # noqa: BLE001 — report per target
                results["errors"][target] = str(exc)
        logger.info(
            "Fleet policy sync: %s/%s → synced=%s errors=%s",
            source_gateway,
            sandbox,
            results["synced"],
            list(results["errors"]),
        )
        return results
