"""Unit tests for SandboxManager.forward_tcp and the TCP-forward request builder."""

from __future__ import annotations

import queue
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


def test_tcp_forward_iter_emits_init_then_data():
    """_tcp_forward_iter yields the init frame, then data frames, then stops."""
    inbound: queue.Queue = queue.Queue()
    inbound.put(b"hello")
    inbound.put(b"world")
    inbound.put(None)

    frames = list(
        _tcp_forward_iter({"sandbox_id": "sid", "target": "tcp", "host": "h", "port": 1}, inbound)
    )

    assert len(frames) == 3
    assert frames[0].WhichOneof("payload") == "init"
    assert frames[1].WhichOneof("payload") == "data"
    assert bytes(frames[1].data) == b"hello"
    assert bytes(frames[2].data) == b"world"


class _FakeCall:
    def __init__(self, frames, parent):
        self._frames = frames
        self._parent = parent

    def __iter__(self):
        return iter(self._frames)

    def cancel(self):
        self._parent.cancelled = True


class _FakeForwardStub:
    def __init__(self, frames):
        self._frames = frames
        self.sent: Any = None
        self.cancelled = False

    def ForwardTcp(self, request_iter, timeout=None):
        self.sent = list(request_iter)
        return _FakeCall(self._frames, self)


def test_forward_tcp_relays_data_and_cancels():
    """forward_tcp sends the init + data frames and yields inbound data chunks."""
    frames = [
        openshell_pb2.TcpForwardFrame(data=b"resp1"),
        openshell_pb2.TcpForwardFrame(data=b"resp2"),
    ]
    stub = _FakeForwardStub(frames)
    mgr = object.__new__(SandboxManager)
    mgr._stub = stub  # type: ignore[assignment]
    mgr._timeout = 30.0

    inbound: queue.Queue = queue.Queue()
    inbound.put(b"req")
    inbound.put(None)
    captured = {}

    out = list(
        mgr.forward_tcp(
            init={"sandbox_id": "sid", "target": "tcp", "host": "h", "port": 2},
            inbound=inbound,
            on_call=lambda c: captured.setdefault("call", c),
        )
    )

    assert out == [
        {"type": "data", "data": b"resp1"},
        {"type": "data", "data": b"resp2"},
    ]
    assert stub.sent[0].WhichOneof("payload") == "init"
    assert bytes(stub.sent[1].data) == b"req"
    assert "call" in captured
    assert stub.cancelled is True
