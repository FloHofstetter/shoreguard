"""WebSocket handler for live sandbox event streaming."""

import asyncio
import base64
import contextlib
import json
import logging
import threading
import time
from collections.abc import Mapping
from typing import Any

import grpc
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from shoreguard.exceptions import GatewayNotConnectedError, friendly_grpc_error
from shoreguard.services.ocsf import parse_log_line as parse_ocsf_log
from shoreguard.services.webhooks import fire_webhook

from .auth import require_auth_ws, require_role_ws
from .deps import _VALID_GW_RE, _current_gateway, _get_gateway_service
from .ws_bridge import run_bidi_session

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
                        if event.get("type") == "log":
                            data = event.get("data")
                            if isinstance(data, dict):
                                ocsf = parse_ocsf_log(data)
                                if ocsf is not None:
                                    data["ocsf"] = ocsf
                                    # Feed bypass detection service.
                                    from shoreguard.services.bypass import bypass_service

                                    if bypass_service is not None:
                                        bypass_service.ingest_log(
                                            data,
                                            sandbox_name=sandbox_name,
                                            gateway_name=gw,
                                        )
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


async def _accept_and_resolve_sandbox(
    websocket: WebSocket, gw: str, sandbox_name: str
) -> tuple[Any, str] | None:
    """Accept a WebSocket and resolve the client + sandbox id for *gw*.

    Args:
        websocket: The WebSocket connection.
        gw: Gateway name from the URL path.
        sandbox_name: Sandbox name from the URL path.

    Returns:
        tuple[Any, str] | None: ``(client, sandbox_id)`` on success, or ``None``
            after an error frame has been sent and the caller should return.
    """
    try:
        await websocket.accept()
    except RuntimeError:
        logger.warning("WebSocket closed before accept: %s/%s", gw, sandbox_name)
        return None
    if not _VALID_GW_RE.match(gw):
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.send_json(
                {"type": "error", "data": {"message": "Invalid gateway name"}}
            )
        return None
    _current_gateway.set(gw)
    try:
        client = await asyncio.to_thread(_get_gateway_service().get_client, name=gw)
        sandbox = await asyncio.to_thread(client.sandboxes.get, sandbox_name)
    except GatewayNotConnectedError:
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.send_json(
                {"type": "error", "data": {"message": f"Gateway '{gw}' not connected"}}
            )
        return None
    except grpc.RpcError as exc:
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.send_json(
                {"type": "error", "data": {"message": friendly_grpc_error(exc)}}
            )
        return None
    return client, sandbox["id"]


@router.websocket("/ws/{gw}/{sandbox_name}/exec")
async def sandbox_exec(
    websocket: WebSocket,
    gw: str,
    sandbox_name: str,
    _auth: None = Depends(require_role_ws("operator")),
) -> None:
    """Run an interactive (TTY) command in a sandbox over a bidirectional stream.

    Protocol: the browser sends a first ``{"type": "start", "command": [...],
    "cols": int, "rows": int}`` frame, then ``{"type": "stdin", "data": <base64>}``
    and ``{"type": "resize", "cols": int, "rows": int}`` frames. The server sends
    ``{"type": "stdout"|"stderr", "data": <base64>}`` and ``{"type": "exit",
    "exit_code": int}`` frames.

    Args:
        websocket: The WebSocket connection.
        gw: Gateway name from the URL path.
        sandbox_name: Sandbox name from the URL path.
        _auth: Operator-role auth dependency (unused sentinel).
    """
    resolved = await _accept_and_resolve_sandbox(websocket, gw, sandbox_name)
    if resolved is None:
        return
    client, sandbox_id = resolved

    try:
        start_raw = await websocket.receive_text()
        start = json.loads(start_raw)
    except WebSocketDisconnect, json.JSONDecodeError, RuntimeError:
        return
    if start.get("type") != "start" or not start.get("command"):
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.send_json(
                {"type": "error", "data": {"message": "Expected a start frame with a command"}}
            )
        return

    command = [str(c) for c in start["command"]]
    cols = int(start.get("cols") or 80)
    rows = int(start.get("rows") or 24)
    env = {str(k): str(v) for k, v in (start.get("env") or {}).items()}
    workdir = str(start.get("workdir") or "")

    def _stream_factory(inbound, on_call):
        return client.sandboxes.exec_interactive(
            sandbox_id,
            command,
            inbound=inbound,
            on_call=on_call,
            workdir=workdir,
            env=env or None,
            cols=cols,
            rows=rows,
        )

    def _decode(message: Mapping[str, Any]) -> dict[str, Any] | None:
        text = message.get("text")
        if text is None:
            return None
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return None
        kind = obj.get("type")
        if kind == "stdin":
            return {"type": "stdin", "data": base64.b64decode(obj.get("data", ""))}
        if kind == "resize":
            return {
                "type": "resize",
                "cols": int(obj.get("cols", 80)),
                "rows": int(obj.get("rows", 24)),
            }
        return None

    def _encode(event: dict[str, Any]):
        etype = event["type"]
        if etype in ("stdout", "stderr"):
            return (
                "text",
                json.dumps({"type": etype, "data": base64.b64encode(event["data"]).decode()}),
            )
        if etype == "exit":
            return ("text", json.dumps({"type": "exit", "exit_code": event["exit_code"]}))
        if etype == "error":
            return ("text", json.dumps(event))
        return None

    logger.info(
        "Interactive exec started (gw=%s, sandbox=%s, cmd=%s)", gw, sandbox_name, command[:3]
    )
    await run_bidi_session(
        websocket,
        stream_factory=_stream_factory,
        decode_client=_decode,
        encode_event=_encode,
    )


@router.websocket("/ws/{gw}/{sandbox_name}/forward")
async def sandbox_forward(
    websocket: WebSocket,
    gw: str,
    sandbox_name: str,
    _auth: None = Depends(require_role_ws("operator")),
) -> None:
    """Relay a raw TCP/SSH tunnel to a sandbox over a bidirectional stream.

    Protocol: the browser sends a first JSON ``{"target": "tcp", "host": str,
    "port": int}`` or ``{"target": "ssh", "authorization_token": str}`` frame,
    then raw binary frames. The server relays raw binary frames back.

    Args:
        websocket: The WebSocket connection.
        gw: Gateway name from the URL path.
        sandbox_name: Sandbox name from the URL path.
        _auth: Operator-role auth dependency (unused sentinel).
    """
    resolved = await _accept_and_resolve_sandbox(websocket, gw, sandbox_name)
    if resolved is None:
        return
    client, sandbox_id = resolved

    try:
        init_raw = await websocket.receive_text()
        init = json.loads(init_raw)
    except WebSocketDisconnect, json.JSONDecodeError, RuntimeError:
        return
    target = init.get("target")
    if target == "tcp" and (not init.get("host") or not init.get("port")):
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.send_json(
                {"type": "error", "data": {"message": "tcp target requires host and port"}}
            )
        return
    if target not in ("tcp", "ssh"):
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.send_json(
                {"type": "error", "data": {"message": "target must be 'tcp' or 'ssh'"}}
            )
        return

    forward_init: dict[str, Any] = {
        "sandbox_id": sandbox_id,
        "service_id": str(init.get("service_id") or ""),
        "authorization_token": str(init.get("authorization_token") or ""),
        "target": target,
    }
    if target == "tcp":
        forward_init["host"] = str(init["host"])
        forward_init["port"] = int(init["port"])

    def _stream_factory(inbound, on_call):
        return client.sandboxes.forward_tcp(init=forward_init, inbound=inbound, on_call=on_call)

    def _decode(message: Mapping[str, Any]) -> bytes | None:
        data = message.get("bytes")
        return data if data is not None else None

    def _encode(event: dict[str, Any]):
        if event["type"] == "data":
            return ("bytes", event["data"])
        if event["type"] == "error":
            return ("text", json.dumps(event))
        return None

    logger.info("TCP forward started (gw=%s, sandbox=%s, target=%s)", gw, sandbox_name, target)
    await run_bidi_session(
        websocket,
        stream_factory=_stream_factory,
        decode_client=_decode,
        encode_event=_encode,
    )
