"""WebSocket handlers for live sandbox event streaming, exec, and forwarding.

All three endpoints consume ``grpc.aio`` streams directly on the event
loop — no worker threads or thread-safe queues. Bidirectional sessions
(exec terminal, TCP forward) pump inbound WebSocket frames into an
``asyncio.Queue`` that feeds the gRPC request stream, while the
response stream is async-iterated and re-encoded for the browser.
"""

import asyncio
import base64
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

import grpc
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from shoreguard.exceptions import GatewayNotConnectedError, friendly_grpc_error
from shoreguard.services.ocsf import parse_log_line as parse_ocsf_log
from shoreguard.services.webhooks import fire_webhook

from .auth import require_auth_ws, require_role_ws
from .deps import _VALID_GW_RE, _get_gateway_service

logger = logging.getLogger(__name__)

router = APIRouter()

# Frame returned by an encoder: ("text", str) or ("bytes", bytes).
Frame = tuple[str, Any]


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

    try:
        client = await _get_gateway_service().get_client(name=gw)
    except GatewayNotConnectedError:
        try:
            await websocket.send_json(
                {"type": "error", "data": {"message": f"Gateway '{gw}' not connected"}}
            )
        except RuntimeError, WebSocketDisconnect:
            logger.debug("WebSocket closed before sending error: %s/%s", gw, sandbox_name)
        return

    try:
        sandbox = await client.sandboxes.get(sandbox_name)
        sandbox_id = sandbox["id"]

        from shoreguard.settings import get_settings

        ws_cfg = get_settings().websocket
        queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=ws_cfg.queue_maxsize)
        drop_count = 0

        async def _producer() -> None:
            """Consume the gRPC watch stream and enqueue events with backpressure."""
            nonlocal drop_count
            consecutive_drops = 0
            try:
                async for event in client.sandboxes.watch(
                    sandbox_id,
                    follow_status=True,
                    follow_logs=True,
                    follow_events=True,
                ):
                    if event.get("type") == "log":
                        data = event.get("data")
                        if isinstance(data, dict):
                            ocsf = parse_ocsf_log(data)
                            if ocsf is not None:
                                data["ocsf"] = ocsf
                                # Feed bypass detection service.
                                from shoreguard.container import try_get_container

                                container = try_get_container()
                                if container is not None:
                                    container.bypass.ingest_log(
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
                            break
            except grpc.RpcError as exc:
                detail = friendly_grpc_error(exc)
                logger.warning("WatchSandbox stream error for %s: %s", sandbox_name, detail)
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(
                        {"type": "error", "data": {"message": f"Stream error: {detail}"}}
                    )
            finally:
                try:
                    queue.put_nowait(None)
                except asyncio.QueueFull:
                    logger.warning("Could not send sentinel for %s", sandbox_name)

        producer_task = asyncio.create_task(_producer())
        last_send_time = time.monotonic()

        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=ws_cfg.queue_get_timeout)
                except TimeoutError:
                    if producer_task.done() and queue.empty():
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
            producer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await producer_task

        # The stream ended (sentinel or backpressure cutoff) — close the
        # socket explicitly so clients see a clean close frame.
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close()

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
    try:
        client = await _get_gateway_service().get_client(name=gw)
        sandbox = await client.sandboxes.get(sandbox_name)
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


async def _run_bidi_session(
    websocket: WebSocket,
    *,
    stream: AsyncIterator[dict[str, Any]],
    inbound: asyncio.Queue[Any],
    decode_client: Callable[[Mapping[str, Any]], Any],
    encode_event: Callable[[dict[str, Any]], Frame | None],
) -> None:
    """Pump a bidirectional gRPC stream over an accepted WebSocket.

    A reader task forwards inbound WebSocket frames into the request
    queue (``None`` half-closes the request stream); the response
    stream is iterated inline and re-encoded for the browser.

    Args:
        websocket: An already-accepted WebSocket connection.
        stream: Async iterator of event dicts from the gRPC bidi call.
        inbound: Queue feeding the gRPC request stream.
        decode_client: Maps a Starlette ``receive()`` message to an inbound
            queue item, or ``None`` to ignore the frame.
        encode_event: Maps a stream event dict to a WebSocket frame, or
            ``None`` to drop it.
    """

    async def _reader() -> None:
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                item = decode_client(message)
                if item is not None:
                    await inbound.put(item)
        except WebSocketDisconnect:
            pass
        finally:
            await inbound.put(None)

    reader = asyncio.create_task(_reader())
    try:
        async for event in stream:
            frame = encode_event(event)
            if frame is None:
                continue
            kind, payload = frame
            if kind == "bytes":
                await websocket.send_bytes(payload)
            else:
                await websocket.send_text(payload)
    except grpc.RpcError as exc:
        detail = friendly_grpc_error(exc)
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.send_text(json.dumps({"type": "error", "data": {"message": detail}}))
    except WebSocketDisconnect:
        pass
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reader
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()


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

    inbound: asyncio.Queue[Any] = asyncio.Queue()
    stream = client.sandboxes.exec_interactive(
        sandbox_id,
        command,
        inbound=inbound,
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

    def _encode(event: dict[str, Any]) -> Frame | None:
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
    await _run_bidi_session(
        websocket,
        stream=stream,
        inbound=inbound,
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

    inbound_bytes: asyncio.Queue[Any] = asyncio.Queue()
    stream = client.sandboxes.forward_tcp(init=forward_init, inbound=inbound_bytes)

    def _decode(message: Mapping[str, Any]) -> bytes | None:
        data = message.get("bytes")
        return data if data is not None else None

    def _encode(event: dict[str, Any]) -> Frame | None:
        if event["type"] == "data":
            return ("bytes", event["data"])
        if event["type"] == "error":
            return ("text", json.dumps(event))
        return None

    logger.info("TCP forward started (gw=%s, sandbox=%s, target=%s)", gw, sandbox_name, target)
    await _run_bidi_session(
        websocket,
        stream=stream,
        inbound=inbound_bytes,
        decode_client=_decode,
        encode_event=_encode,
    )
