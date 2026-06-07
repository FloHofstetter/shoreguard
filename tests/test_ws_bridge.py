"""Unit tests for the bidirectional WebSocket↔gRPC bridge."""

from __future__ import annotations

import asyncio

from shoreguard.api.ws_bridge import run_bidi_session


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

    def stream_factory(inbound, on_call):
        on_call(object())
        # First drain whatever the reader forwarded, then emit two events.
        item = inbound.get()
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
        run_bidi_session(
            ws,  # type: ignore[arg-type]
            stream_factory=stream_factory,
            decode_client=decode_client,
            encode_event=encode_event,
        ),
        timeout=5,
    )

    assert ("text", "stdout") in ws.sent
    assert ("text", "exit") in ws.sent
    assert received_inbound == [{"type": "stdin", "data": b"ping"}]


async def test_bridge_surfaces_stream_error():
    """A stream exception is encoded as an error frame to the socket."""

    def stream_factory(inbound, on_call):
        raise RuntimeError("boom")
        yield  # pragma: no cover — makes this a generator

    def decode_client(message):
        return None

    def encode_event(event):
        if event["type"] == "error":
            return ("text", f"ERR:{event['data']['message']}")
        return None

    ws = _FakeWS([{"type": "websocket.disconnect"}])

    await asyncio.wait_for(
        run_bidi_session(
            ws,  # type: ignore[arg-type]
            stream_factory=stream_factory,
            decode_client=decode_client,
            encode_event=encode_event,
        ),
        timeout=5,
    )

    assert ("text", "ERR:boom") in ws.sent
