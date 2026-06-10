"""Unit tests for the bidirectional WebSocket↔gRPC session pump."""

from __future__ import annotations

import asyncio

import grpc

from shoreguard.api.websocket import _run_bidi_session


class _FakeWS:
    """Minimal WebSocket stand-in capturing sends and replaying receives."""

    def __init__(self, incoming):
        self._incoming = list(incoming)
        self.sent: list[tuple[str, object]] = []

    async def receive(self):
        if self._incoming:
            return self._incoming.pop(0)
        # No more client frames — block until the session tears us down.
        await asyncio.sleep(3600)

    async def send_text(self, text):
        self.sent.append(("text", text))

    async def send_bytes(self, payload):
        self.sent.append(("bytes", payload))


async def test_bridge_relays_events_and_forwards_input():
    """Events from the stream reach the socket; client frames reach the stream."""
    received_inbound: list = []
    inbound: asyncio.Queue = asyncio.Queue()

    async def stream():
        # First drain whatever the reader forwarded, then emit two events.
        item = await inbound.get()
        if item is not None:
            received_inbound.append(item)
        yield {"type": "stdout", "data": b"a"}
        yield {"type": "exit", "exit_code": 0}

    def decode_client(message):
        if message.get("text") == "ping":
            return {"type": "stdin", "data": b"ping"}
        return None

    def encode_event(event):
        return ("text", event["type"])

    ws = _FakeWS(
        [
            {"type": "websocket.receive", "text": "ping"},
            {"type": "websocket.disconnect"},
        ]
    )

    await asyncio.wait_for(
        _run_bidi_session(
            ws,  # type: ignore[arg-type]
            stream=stream(),
            inbound=inbound,
            decode_client=decode_client,
            encode_event=encode_event,
        ),
        timeout=5,
    )

    assert ("text", "stdout") in ws.sent
    assert ("text", "exit") in ws.sent
    assert received_inbound == [{"type": "stdin", "data": b"ping"}]


class _FakeRpcError(grpc.RpcError):
    """RpcError carrying a details() string like a live call."""

    def details(self):  # noqa: D102
        return "boom"

    def code(self):  # noqa: D102
        return grpc.StatusCode.UNAVAILABLE


async def test_bridge_surfaces_stream_error():
    """A gRPC stream exception is encoded as an error frame to the socket."""
    inbound: asyncio.Queue = asyncio.Queue()

    async def stream():
        raise _FakeRpcError()
        yield  # pragma: no cover — makes this an async generator

    def decode_client(message):
        return None

    def encode_event(event):
        return None

    ws = _FakeWS([{"type": "websocket.disconnect"}])

    await asyncio.wait_for(
        _run_bidi_session(
            ws,  # type: ignore[arg-type]
            stream=stream(),
            inbound=inbound,
            decode_client=decode_client,
            encode_event=encode_event,
        ),
        timeout=5,
    )

    assert any(kind == "text" and "error" in str(payload) for kind, payload in ws.sent)
