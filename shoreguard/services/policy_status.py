"""Event-driven wait helper for policy reload status.

Replaces the original 1-second polling loop in the approvals routes with a
WatchSandbox-driven wake mechanism plus a slow fallback poll.  On the happy
path the broker wakes on the first ``draft_policy_update`` event from the
gateway and confirms via a single ``GetSandboxPolicyStatus`` call, instead of
firing one status RPC per second for the duration of the reload.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from shoreguard.client._proto import openshell_pb2

if TYPE_CHECKING:
    from shoreguard.client import ShoreGuardClient

logger = logging.getLogger(__name__)


class PolicyStatusBroker:
    """Wait for a sandbox policy version to reach the ``loaded`` state."""

    async def wait_for_loaded(
        self,
        client: ShoreGuardClient,
        sandbox_name: str,
        target_version: int,
        *,
        timeout: float = 30.0,
        slow_poll: float = 2.0,
    ) -> None:
        """Block until the proxy reports ``target_version`` as loaded.

        Args:
            client: gRPC client for the active gateway.
            sandbox_name: Sandbox whose policy is being waited on.
            target_version: Policy version that must reach ``loaded``.
            timeout: Hard ceiling before raising 504.
            slow_poll: Maximum interval between confirmation RPCs when no
                push event arrives.

        Raises:
            HTTPException: 504 if the policy does not reach ``loaded`` in time.
        """
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        stream_holder: dict[str, Any] = {}

        def _consume_stream() -> None:
            try:
                stream = client._stub.WatchSandbox(
                    openshell_pb2.WatchSandboxRequest(
                        id=sandbox_name,
                        follow_status=True,
                        follow_logs=False,
                        follow_events=False,
                        log_tail_lines=0,
                    ),
                )
            except Exception:
                logger.debug("WatchSandbox open failed", exc_info=True)
                loop.call_soon_threadsafe(wake.set)
                return
            stream_holder["stream"] = stream
            try:
                for event in stream:
                    payload = event.WhichOneof("payload")
                    if payload in ("draft_policy_update", "sandbox"):
                        loop.call_soon_threadsafe(wake.set)
            except Exception:
                # Stream cancelled or gateway closed it — fine, just stop.
                pass

        consumer_future = loop.run_in_executor(None, _consume_stream)

        async def _is_loaded() -> bool:
            status = await asyncio.to_thread(client.policies.get, sandbox_name)
            return (
                status.get("active_version") == target_version
                and status.get("revision", {}).get("status") == "loaded"
            )

        try:
            deadline = loop.time() + timeout
            if await _is_loaded():
                return
            while loop.time() < deadline:
                remaining = deadline - loop.time()
                try:
                    await asyncio.wait_for(wake.wait(), timeout=min(slow_poll, max(remaining, 0.0)))
                except TimeoutError:
                    pass
                wake.clear()
                if await _is_loaded():
                    return
            raise HTTPException(
                status_code=504,
                detail=(
                    f"Policy v{target_version} did not reach 'loaded' state within {int(timeout)}s"
                ),
            )
        finally:
            stream = stream_holder.get("stream")
            if stream is not None:
                try:
                    stream.cancel()
                except Exception:
                    pass
            try:
                await asyncio.wait_for(consumer_future, timeout=1.0)
            except Exception:
                pass


policy_status_broker = PolicyStatusBroker()
