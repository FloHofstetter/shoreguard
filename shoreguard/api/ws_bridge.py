"""Reusable bridge between a browser WebSocket and a bidirectional gRPC stream.

Both the interactive-exec terminal and the TCP-forward tunnel relay raw,
session-length traffic in both directions. The gRPC client is synchronous, so
the blocking stream is consumed on a worker thread and its events are handed to
the event loop via :meth:`asyncio.loop.call_soon_threadsafe`. Inbound frames
flow the other way through a thread-safe :class:`queue.Queue` that feeds the
gRPC request generator. Teardown half-closes the request stream (sentinel) and
cancels the live call so the worker thread unblocks.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
import threading
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import grpc
from fastapi import WebSocket, WebSocketDisconnect

from shoreguard.exceptions import friendly_grpc_error

logger = logging.getLogger(__name__)

# Frame returned by ``encode_event``: ("text", str) or ("bytes", bytes).
Frame = tuple[str, Any]
# Inbound items are opaque to the bridge — exec passes stdin/resize dicts, the
# TCP forwarder passes raw byte chunks — so the queue element type is ``Any``.
InboundQueue = queue.Queue[Any]
StreamFactory = Callable[[InboundQueue, Callable[[Any], None]], Iterator[dict[str, Any]]]


async def run_bidi_session(
    websocket: WebSocket,
    *,
    stream_factory: StreamFactory,
    decode_client: Callable[[Mapping[str, Any]], Any],
    encode_event: Callable[[dict[str, Any]], Frame | None],
    queue_maxsize: int = 512,
) -> None:
    """Pump a bidirectional gRPC stream over an accepted WebSocket.

    Args:
        websocket: An already-accepted WebSocket connection.
        stream_factory: Builds the gRPC bidi generator from an inbound queue and
            an ``on_call`` callback (handed the live call for cancellation). It
            yields event dicts.
        decode_client: Maps a Starlette ``receive()`` message to an inbound queue
            item, or ``None`` to ignore the frame.
        encode_event: Maps a stream event dict to a WebSocket frame, or ``None``
            to drop it.
        queue_maxsize: Bound on the outbound (gateway → browser) buffer.
    """
    loop = asyncio.get_running_loop()
    inbound: queue.Queue[Any] = queue.Queue()
    outbound: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=queue_maxsize)
    call_holder: dict[str, Any] = {}
    cancel = threading.Event()

    def _on_call(call: Any) -> None:
        call_holder["call"] = call

    def _emit(event: dict[str, Any] | None) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            outbound.put_nowait(event)

    def _run_stream() -> None:
        """Consume the blocking gRPC bidi stream on a worker thread."""
        try:
            for event in stream_factory(inbound, _on_call):
                if cancel.is_set():
                    break
                loop.call_soon_threadsafe(_emit, event)
        except grpc.RpcError as exc:
            if not cancel.is_set():
                detail = friendly_grpc_error(exc)
                loop.call_soon_threadsafe(_emit, {"type": "error", "data": {"message": detail}})
        except Exception as exc:  # noqa: BLE001 — surface unexpected stream failures
            if not cancel.is_set():
                logger.exception("bidi stream worker failed")
                loop.call_soon_threadsafe(_emit, {"type": "error", "data": {"message": str(exc)}})
        finally:
            loop.call_soon_threadsafe(_emit, None)

    async def _reader() -> None:
        """Forward inbound WebSocket frames into the gRPC request queue."""
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                item = decode_client(message)
                if item is not None:
                    inbound.put(item)
        except WebSocketDisconnect:
            pass
        finally:
            inbound.put(None)

    producer = asyncio.create_task(asyncio.to_thread(_run_stream))
    reader = asyncio.create_task(_reader())

    try:
        while True:
            event = await outbound.get()
            if event is None:
                break
            frame = encode_event(event)
            if frame is None:
                continue
            kind, payload = frame
            if kind == "bytes":
                await websocket.send_bytes(payload)
            else:
                await websocket.send_text(payload)
    except WebSocketDisconnect:
        pass
    finally:
        cancel.set()
        inbound.put(None)
        call = call_holder.get("call")
        if call is not None:
            with contextlib.suppress(Exception):
                call.cancel()
        reader.cancel()
        producer.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reader
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await producer
