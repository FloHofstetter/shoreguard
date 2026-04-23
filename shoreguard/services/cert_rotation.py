"""Proactive mTLS client-cert rotation service.

Fulfils the M28 observability promise operationally: the client-cert
metadata exposed via :attr:`shoreguard.client.ShoreGuardClient.cert_info`
and the :func:`~shoreguard.client.ShoreGuardClient.reload_credentials`
hook have existed since v0.31, but no scheduler was wiring them up. This
service polls every registered gateway's cert expiry on a configurable
cadence, and when a client cert drops below the configured days-until-
expiry threshold the current credentials are re-read from the registry
and the client's channel is rebuilt.

Design notes:

- **Idempotent rotation.** Each replica can call ``reload_credentials``
  independently without a lock; the call is idempotent relative to its
  inputs (no server-side mutation). In a multi-replica deployment every
  replica rotates its own client pool, which is the correct behaviour.
- **Fresh creds come from the registry.** The rotation service does not
  generate new certs. It assumes an external process (cert-manager,
  a cron that pushes into the credentials table, an operator running
  ``shoreguard gateway register --client-cert ...``) has landed the
  new material, and that ShoreGuard's job is to pick it up before the
  old material expires. When the registry still holds the expired
  cert, the new ``validate_bundle`` call fails and the rotation
  records ``outcome=failure`` — the condition that should page the
  operator.
- **Retries.** Inside a single poll cycle, failures are retried with
  exponential backoff per ``cert_rotation.max_retries``. Once the
  cycle gives up, the next poll cycle starts clean. There is no
  permanent failure state.

Wired from :mod:`shoreguard.api.main` at startup when
:attr:`shoreguard.settings.Settings.cert_rotation.enabled` is true.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from shoreguard.api.metrics import record_gateway_cert_rotation
from shoreguard.services import audit as _audit_mod
from shoreguard.services.webhooks import fire_webhook

if TYPE_CHECKING:
    from shoreguard.client import ShoreGuardClient
    from shoreguard.services.gateway import GatewayService

logger = logging.getLogger(__name__)


# Module-level handle set from ``api.main`` at startup; the background
# loop grabs the current instance on every tick so a test can swap a
# lighter mock in without restarting the app.
cert_rotation_service: CertRotationService | None = None


class CertRotationService:
    """Polls gateway cert expiry and rotates ahead of deadline.

    Args:
        gateway_service: Service used to list registered gateways and
            re-fetch credentials from the registry.
        threshold_days: Rotate when remaining validity drops below this
            many days.
        max_retries: Retry attempts per rotation before giving up for
            the current cycle.
    """

    def __init__(  # noqa: D107
        self,
        gateway_service: GatewayService,
        *,
        threshold_days: int,
        max_retries: int,
    ) -> None:
        self._gateway_service = gateway_service
        self._threshold_seconds = threshold_days * 86400
        self._max_retries = max_retries

    async def run_once(self) -> dict[str, int]:
        """Inspect every registered gateway and rotate those due.

        Returns:
            dict[str, int]: Counts per outcome label
            (``success``, ``failure``, ``skipped_not_due``,
            ``skipped_no_cert``).
        """
        outcomes: dict[str, int] = {
            "success": 0,
            "failure": 0,
            "skipped_not_due": 0,
            "skipped_no_cert": 0,
        }
        gateways = await asyncio.to_thread(self._gateway_service.list_all)
        for gw in gateways:
            name = gw.get("name")
            if not name:
                continue
            outcome = await self._inspect_and_maybe_rotate(name)
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            record_gateway_cert_rotation(name, outcome)
        return outcomes

    async def _inspect_and_maybe_rotate(self, name: str) -> str:
        client = self._resolve_client(name)
        if client is None or client.cert_info is None:
            # Plaintext channel or no connected client — nothing to rotate.
            return "skipped_no_cert"

        seconds_left = client.cert_info.seconds_until_expiry
        if seconds_left is None or seconds_left > self._threshold_seconds:
            return "skipped_not_due"

        return await self._rotate_with_retries(name, client, seconds_left)

    async def _rotate_with_retries(
        self,
        name: str,
        client: ShoreGuardClient,
        seconds_left_before: float,
    ) -> str:
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                await asyncio.to_thread(self._rotate, name, client)
                seconds_left_after = (
                    client.cert_info.seconds_until_expiry if client.cert_info is not None else None
                )
                if _audit_mod.audit_service is not None:
                    await asyncio.to_thread(
                        _audit_mod.audit_service.log,
                        actor="system",
                        actor_role="system",
                        action="gateway.cert_rotated",
                        resource_type="gateway",
                        resource_id=name,
                        gateway=name,
                        detail={
                            "before_seconds_until_expiry": seconds_left_before,
                            "after_seconds_until_expiry": seconds_left_after,
                            "attempts": attempt,
                        },
                        client_ip=None,
                    )
                return "success"
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "Cert rotation attempt %d/%d for gateway '%s' failed: %s",
                    attempt,
                    self._max_retries,
                    name,
                    exc,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(min(30 * (2 ** (attempt - 1)), 600))
        # All retries exhausted — fire webhook and record failure.
        await fire_webhook(
            "gateway.cert_rotation_failed",
            {
                "gateway": name,
                "reason": str(last_error) if last_error else "unknown",
                "retries": self._max_retries,
                "seconds_until_expiry": seconds_left_before,
                "next_attempt_at": _next_attempt_at(),
            },
        )
        return "failure"

    def _resolve_client(self, name: str) -> ShoreGuardClient | None:
        """Return the live client for *name* via the gateway service cache.

        Args:
            name: Gateway name.

        Returns:
            ShoreGuardClient | None: The cached client, or ``None`` when
                the gateway is not connected.
        """
        # The module-level ``_clients`` dict in services.gateway holds
        # the active ShoreGuardClient instances. Import here to avoid
        # circular imports at module load.
        from shoreguard.services.gateway import _clients, _clients_lock

        with _clients_lock:
            cached = _clients.get(name)
        if cached is None:
            return None
        return cached.client

    def _rotate(self, name: str, client: ShoreGuardClient) -> None:
        """Re-read credentials from the registry and rebuild the channel.

        Args:
            name: Gateway name.
            client: Live client whose channel is being rotated.

        Raises:
            RuntimeError: When the registry has no usable bytes credentials
                for this gateway.
        """
        creds = self._gateway_service._registry.get_credentials(name)  # noqa: SLF001
        if creds is None:
            raise RuntimeError(f"gateway '{name}' has no credentials in the registry to rotate")
        ca_cert = creds.get("ca_cert")
        client_cert = creds.get("client_cert")
        client_key = creds.get("client_key")
        if not (
            isinstance(ca_cert, bytes)
            and isinstance(client_cert, bytes)
            and isinstance(client_key, bytes)
        ):
            raise RuntimeError(f"gateway '{name}' has incomplete bytes credentials in the registry")
        client.reload_credentials(
            ca_cert=ca_cert,
            client_cert=client_cert,
            client_key=client_key,
        )


def _next_attempt_at() -> float:
    """Return an epoch-seconds timestamp anchor for the webhook payload.

    Returns:
        float: Current wall-clock time as ``time.time()`` — dashboards can
            add the configured poll interval to derive the next attempt.
    """
    # The actual next-attempt time depends on poll_interval_s read by the
    # driving loop; the service itself does not own the interval. We
    # approximate with "now" so dashboards can anchor off the webhook
    # timestamp rather than a hard-coded offset.
    return time.time()


def init_cert_rotation_service(
    gateway_service: GatewayService,
    *,
    threshold_days: int,
    max_retries: int,
) -> CertRotationService:
    """Construct and install the module-level service handle.

    Args:
        gateway_service: Service used to list gateways and fetch creds.
        threshold_days: Rotate-ahead threshold.
        max_retries: Retry attempts per rotation.

    Returns:
        CertRotationService: The freshly installed service instance.
    """
    global cert_rotation_service
    cert_rotation_service = CertRotationService(
        gateway_service,
        threshold_days=threshold_days,
        max_retries=max_retries,
    )
    return cert_rotation_service


__all__ = (
    "CertRotationService",
    "cert_rotation_service",
    "init_cert_rotation_service",
)
