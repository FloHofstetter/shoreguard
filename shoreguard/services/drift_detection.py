"""Background loop that detects out-of-band policy changes.

Off by default. When enabled, iterates every registered sandbox on
the configured interval, fetches its policy hash, and fires the
``policy.drift_detected`` webhook on any hash change between
consecutive scans. This catches edits made outside the GitOps
pipeline — a direct API call, a manual UI edit during an incident,
or an approval that was merged while CI was paused — and turns
them into visible events rather than silent divergence.

The previous-hash snapshot is kept in process memory only. A
restart means one tick with no comparison available, after which
the next real edit fires the next event. Persisting the snapshot
would add complexity with no upside: a missed tick at restart is
not an incident, whereas flaky persistence would be.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shoreguard.services.gateway import GatewayService
    from shoreguard.settings import DriftDetectionSettings

logger = logging.getLogger(__name__)

drift_detection_service: DriftDetectionService | None = None


class DriftDetectionService:
    """Background policy hash poller.

    Args:
        gateway_service: GatewayService used to enumerate gateways and
            obtain ShoreGuardClient instances.
        settings: Drift detection settings (enabled flag + interval).
        webhook_emit: Async callable ``(event_type, payload)`` used to
            fire webhooks (defaults to ``shoreguard.services.webhooks.fire_webhook``).
        audit: Optional async callable ``(action, resource, gateway, detail)``
            for audit logging.

    Attributes:
        snapshot: ``{(gateway, sandbox): hash}`` snapshot of last seen hashes.
    """

    def __init__(  # noqa: D107
        self,
        gateway_service: GatewayService,
        settings: DriftDetectionSettings,
        *,
        webhook_emit: Any | None = None,
        audit: Any | None = None,
    ) -> None:
        self._gateway_service = gateway_service
        self._settings = settings
        self._snapshot: dict[tuple[str, str], str] = {}
        if webhook_emit is None:
            from shoreguard.services.webhooks import fire_webhook as _fw

            webhook_emit = _fw
        self._webhook_emit = webhook_emit
        self._audit = audit

    @property
    def snapshot(self) -> dict[tuple[str, str], str]:
        """Return the in-memory ``{(gateway, sandbox): hash}`` snapshot."""
        return dict(self._snapshot)

    async def run_once(self) -> list[dict[str, Any]]:
        """Run one detection pass.

        Returns:
            list[dict[str, Any]]: Drift events fired during this pass.
        """
        events: list[dict[str, Any]] = []
        try:
            gateways = self._gateway_service.list_all()
        except Exception:
            logger.exception("drift_detection: failed to list gateways")
            return events
        for gw in gateways:
            gw_name = gw.get("name") if isinstance(gw, dict) else getattr(gw, "name", None)
            if not gw_name:
                continue
            try:
                client = self._gateway_service.get_client(gw_name)
            except Exception:
                logger.warning("drift_detection: cannot connect to gateway %s", gw_name)
                continue
            try:
                sandboxes = await asyncio.to_thread(client.sandboxes.list)
            except Exception:
                logger.warning("drift_detection: list sandboxes failed for %s", gw_name)
                continue
            for sb in sandboxes or []:
                sb_name = sb.get("name") if isinstance(sb, dict) else getattr(sb, "name", None)
                if not sb_name:
                    continue
                event = await self._scan_sandbox(client, gw_name, sb_name)
                if event is not None:
                    events.append(event)
        return events

    async def _scan_sandbox(
        self, client: Any, gateway_name: str, sandbox_name: str
    ) -> dict[str, Any] | None:
        try:
            snapshot = await asyncio.to_thread(client.policies.get, sandbox_name)
        except Exception:
            logger.warning(
                "drift_detection: policy fetch failed (gw=%s, sb=%s)",
                gateway_name,
                sandbox_name,
            )
            return None
        revision = snapshot.get("revision") or {}
        current_hash = revision.get("policy_hash") or ""
        if not current_hash:
            return None
        key = (gateway_name, sandbox_name)
        previous_hash = self._snapshot.get(key)
        self._snapshot[key] = current_hash
        if previous_hash is None or previous_hash == current_hash:
            return None
        payload = {
            "gateway": gateway_name,
            "sandbox": sandbox_name,
            "previous_hash": previous_hash,
            "current_hash": current_hash,
            "detected_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }
        try:
            await self._webhook_emit("policy.drift_detected", payload)
        except Exception:
            logger.exception("drift_detection: webhook delivery failed")
        if self._audit is not None:
            try:
                await self._audit(
                    "policy.drift_detected",
                    sandbox_name,
                    gateway_name,
                    payload,
                )
            except Exception:
                logger.exception("drift_detection: audit log failed")
        logger.info(
            "drift detected: gw=%s sb=%s %s -> %s",
            gateway_name,
            sandbox_name,
            previous_hash,
            current_hash,
        )
        return payload
