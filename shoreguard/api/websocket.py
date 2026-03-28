"""WebSocket handler for live sandbox event streaming."""

import asyncio
import logging
import threading

import grpc
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from shoreguard.exceptions import GatewayNotConnectedError, friendly_grpc_error

from .auth import require_auth_ws
from .deps import _VALID_GW_RE, _current_gateway, get_client

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/{gw}/{sandbox_name}")
async def sandbox_events(
    websocket: WebSocket,
    gw: str,
    sandbox_name: str,
    _auth: None = Depends(require_auth_ws),
):
    """Stream live sandbox events over WebSocket."""
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
        except (RuntimeError, WebSocketDisconnect):
            logger.debug(
                "WebSocket closed before sending validation error: %s/%s",
                gw,
                sandbox_name,
            )
        return

    _current_gateway.set(gw)
    try:
        client = await asyncio.to_thread(get_client)
    except GatewayNotConnectedError:
        try:
            await websocket.send_json(
                {"type": "error", "data": {"message": f"Gateway '{gw}' not connected"}}
            )
        except (RuntimeError, WebSocketDisconnect):
            logger.debug("WebSocket closed before sending error: %s/%s", gw, sandbox_name)
        return

    try:
        sandbox = await asyncio.to_thread(client.sandboxes.get, sandbox_name)
        sandbox_id = sandbox["id"]

        queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=1000)
        cancel_event = threading.Event()

        async def _producer():
            def _iter_watch():
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
                        except asyncio.QueueFull:
                            logger.warning(
                                "WebSocket event queue full for %s, dropping event",
                                sandbox_name,
                            )
                except grpc.RpcError as exc:
                    if cancel_event.is_set():
                        return
                    detail = exc.details() if hasattr(exc, "details") else str(exc)
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

        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    if cancel_event.is_set():
                        break
                    continue
                if event is None:
                    break
                await websocket.send_json(event)
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
