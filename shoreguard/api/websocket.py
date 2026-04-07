"""WebSocket handler for live sandbox event streaming."""

import asyncio
import logging
import threading
import time

import grpc
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from shoreguard.exceptions import GatewayNotConnectedError, friendly_grpc_error
from shoreguard.services.webhooks import fire_webhook

from .auth import require_auth_ws
from .deps import _VALID_GW_RE, _current_gateway, _get_gateway_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/{gw}/{sandbox_name}")
async def sandbox_events(
    websocket: WebSocket,
    gw: str,
    sandbox_name: str,
    _auth: None = Depends(require_auth_ws),
) -> None:
    """Stream live sandbox events over WebSocket.

    Args:
        websocket: The WebSocket connection.
        gw: The gateway name from the URL path.
        sandbox_name: The sandbox to stream events for.
        _auth: Authentication dependency (unused sentinel).
    """
    try:
        await websocket.accept()
    except RuntimeError:
        logger.warning("WebSocket closed before accept: %s/%s", gw, sandbox_name, exc_info=True)
        return
    if not _VALID_GW_RE.match(gw):
        try:
            await websocket.send_json(
                {"type": "error", "data": {"message": "Invalid gateway name"}}
            )
        except RuntimeError, WebSocketDisconnect:
            logger.debug(
                "WebSocket closed before sending validation error: %s/%s",
                gw,
                sandbox_name,
            )
        return

    _current_gateway.set(gw)
    try:
        client = await asyncio.to_thread(_get_gateway_service().get_client, name=gw)
    except GatewayNotConnectedError:
        try:
            await websocket.send_json(
                {"type": "error", "data": {"message": f"Gateway '{gw}' not connected"}}
            )
        except RuntimeError, WebSocketDisconnect:
            logger.debug("WebSocket closed before sending error: %s/%s", gw, sandbox_name)
        return

    try:
        sandbox = await asyncio.to_thread(client.sandboxes.get, sandbox_name)
        sandbox_id = sandbox["id"]

        from shoreguard.settings import get_settings

        ws_cfg = get_settings().websocket
        queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=ws_cfg.queue_maxsize)
        cancel_event = threading.Event()
        drop_count = 0

        async def _producer():
            """Run the blocking gRPC watch in a thread and enqueue events."""

            def _iter_watch() -> None:
                """Iterate the gRPC watch stream, forwarding events to the queue."""
                nonlocal drop_count
                consecutive_drops = 0
                try:
                    for event in client.sandboxes.watch(
                        sandbox_id,
                        follow_status=True,
                        follow_logs=True,
                        follow_events=True,
                    ):
                        if cancel_event.is_set():
                            break
                        try:
                            queue.put_nowait(event)
                            consecutive_drops = 0
                        except asyncio.QueueFull:
                            drop_count += 1
                            consecutive_drops += 1
                            logger.warning(
                                "WebSocket queue full for %s, dropped %d total (%d consecutive)",
                                sandbox_name,
                                drop_count,
                                consecutive_drops,
                            )
                            if consecutive_drops >= ws_cfg.backpressure_drop_limit:
                                logger.warning(
                                    "Disconnecting slow consumer for %s after %d consecutive drops",
                                    sandbox_name,
                                    consecutive_drops,
                                )
                                cancel_event.set()
                                break
                except grpc.RpcError as exc:
                    if cancel_event.is_set():
                        return
                    detail = friendly_grpc_error(exc)
                    logger.warning("WatchSandbox stream error for %s: %s", sandbox_name, detail)
                    try:
                        queue.put_nowait(
                            {"type": "error", "data": {"message": f"Stream error: {detail}"}}
                        )
                    except asyncio.QueueFull:
                        pass
                finally:
                    try:
                        queue.put_nowait(None)
                    except asyncio.QueueFull:
                        logger.warning(
                            "Could not send sentinel for %s, setting cancel event",
                            sandbox_name,
                        )
                        cancel_event.set()

            await asyncio.to_thread(_iter_watch)

        producer_task = asyncio.create_task(_producer())
        last_send_time = time.monotonic()

        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=ws_cfg.queue_get_timeout)
                except TimeoutError:
                    if cancel_event.is_set():
                        break
                    if time.monotonic() - last_send_time >= ws_cfg.heartbeat_interval:
                        await websocket.send_json(
                            {"type": "heartbeat", "data": {"dropped_events": drop_count}}
                        )
                        last_send_time = time.monotonic()
                    continue
                if event is None:
                    break
                await websocket.send_json(event)
                last_send_time = time.monotonic()
                if event.get("type") == "draft_policy_update":
                    asyncio.create_task(
                        fire_webhook(
                            "approval.pending",
                            {
                                "sandbox": sandbox_name,
                                "gateway": gw,
                                **event.get("data", {}),
                            },
                        )
                    )
        finally:
            cancel_event.set()
            producer_task.cancel()
            try:
                await producer_task
            except asyncio.CancelledError:
                pass

    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected: %s/%s", gw, sandbox_name)
    except grpc.RpcError as e:
        code = e.code() if hasattr(e, "code") else None
        if code == grpc.StatusCode.NOT_FOUND:
            msg = f"Sandbox '{sandbox_name}' not found"
        else:
            msg = friendly_grpc_error(e)
        logger.error("WebSocket gRPC error for %s/%s: %s", gw, sandbox_name, msg, exc_info=True)
        try:
            await websocket.send_json({"type": "error", "data": {"message": msg}})
        except WebSocketDisconnect:
            pass
        except RuntimeError as ws_err:
            logger.debug("WebSocket send failed for %s/%s: %s", gw, sandbox_name, ws_err)
    except Exception as e:
        logger.error("WebSocket error for %s/%s: %s", gw, sandbox_name, e, exc_info=True)
        try:
            await websocket.send_json({"type": "error", "data": {"message": "Internal error"}})
        except WebSocketDisconnect:
            pass
        except RuntimeError as ws_err:
            logger.debug("WebSocket send failed for %s/%s: %s", gw, sandbox_name, ws_err)
