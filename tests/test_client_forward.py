"""Unit tests for SandboxManager.forward_tcp and the TCP-forward request builder."""

from __future__ import annotations

import asyncio
from typing import Any

from shoreguard.client._proto import openshell_pb2
from shoreguard.client.sandboxes import (
    SandboxManager,
    _build_tcp_forward_init,
    _tcp_forward_iter,
)


def test_build_tcp_forward_init_tcp():
    """A tcp target builds a TcpRelayTarget with host/port and common fields."""
    msg = _build_tcp_forward_init(
        {
            "sandbox_id": "sid",
            "service_id": "svc",
            "authorization_token": "tok",
            "target": "tcp",
            "host": "10.0.0.1",
            "port": 5432,
        }
    )
    assert msg.WhichOneof("target") == "tcp"
    assert msg.tcp.host == "10.0.0.1"
    assert msg.tcp.port == 5432
    assert msg.sandbox_id == "sid"
    assert msg.service_id == "svc"
    assert msg.authorization_token == "tok"


def test_build_tcp_forward_init_ssh():
    """An ssh target builds an SshRelayTarget and carries the auth token."""
    msg = _build_tcp_forward_init(
        {"sandbox_id": "sid", "target": "ssh", "authorization_token": "ssh-tok"}
    )
    assert msg.WhichOneof("target") == "ssh"
    assert msg.authorization_token == "ssh-tok"


async def test_tcp_forward_iter_emits_init_then_data():
    """_tcp_forward_iter yields the init frame, then data frames, then stops."""
    inbound: asyncio.Queue = asyncio.Queue()
    await inbound.put(b"hello")
    await inbound.put(b"world")
    await inbound.put(None)

    frames = [
        f
        async for f in _tcp_forward_iter(
            {"sandbox_id": "sid", "target": "tcp", "host": "h", "port": 1}, inbound
        )
    ]

    assert len(frames) == 3
    assert frames[0].WhichOneof("payload") == "init"
    assert frames[1].WhichOneof("payload") == "data"
    assert bytes(frames[1].data) == b"hello"
    assert bytes(frames[2].data) == b"world"


class _FakeCall:
    def __init__(self, frames, parent):
        self._frames = list(frames)
        self._parent = parent

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._frames:
            return self._frames.pop(0)
        raise StopAsyncIteration

    def cancel(self):
        self._parent.cancelled = True


class _FakeForwardStub:
    def __init__(self, frames):
        self._frames = frames
        self.sent: Any = None
        self.cancelled = False

    def ForwardTcp(self, request_iter, timeout=None):
        self._request_iter = request_iter
        return _FakeCall(self._frames, self)

    async def drain_requests(self):
        """Capture the init + data frames (stops at the None sentinel)."""
        self.sent = [f async for f in self._request_iter]


async def test_forward_tcp_relays_data_and_cancels():
    """forward_tcp sends the init + data frames and yields inbound data chunks."""
    frames = [
        openshell_pb2.TcpForwardFrame(data=b"resp1"),
        openshell_pb2.TcpForwardFrame(data=b"resp2"),
    ]
    stub = _FakeForwardStub(frames)
    mgr = object.__new__(SandboxManager)
    mgr._stub = stub  # type: ignore[assignment]
    mgr._timeout = 30.0

    inbound: asyncio.Queue = asyncio.Queue()
    await inbound.put(b"req")
    await inbound.put(None)
    captured = {}

    out = [
        e
        async for e in mgr.forward_tcp(
            init={"sandbox_id": "sid", "target": "tcp", "host": "h", "port": 2},
            inbound=inbound,
            on_call=lambda c: captured.setdefault("call", c),
        )
    ]
    await stub.drain_requests()

    assert out == [
        {"type": "data", "data": b"resp1"},
        {"type": "data", "data": b"resp2"},
    ]
    assert stub.sent[0].WhichOneof("payload") == "init"
    assert bytes(stub.sent[1].data) == b"req"
    assert "call" in captured
    assert stub.cancelled is True
