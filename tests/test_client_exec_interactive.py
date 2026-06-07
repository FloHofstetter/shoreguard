"""Unit tests for SandboxManager.exec_interactive and the request generator."""

from __future__ import annotations

import queue
from typing import Any

from shoreguard.client._proto import openshell_pb2
from shoreguard.client.sandboxes import SandboxManager, _exec_input_iter


def test_exec_input_iter_emits_start_then_frames():
    """_exec_input_iter yields the start frame, then stdin/resize, then stops."""
    inbound: queue.Queue = queue.Queue()
    inbound.put({"type": "stdin", "data": b"ls\n"})
    inbound.put({"type": "resize", "cols": 120, "rows": 40})
    inbound.put(None)

    frames = list(
        _exec_input_iter("sid", ["bash"], inbound, workdir="/w", env={"A": "B"}, cols=80, rows=24)
    )

    assert len(frames) == 3
    assert frames[0].WhichOneof("payload") == "start"
    assert list(frames[0].start.command) == ["bash"]
    assert frames[0].start.tty is True
    assert frames[0].start.cols == 80
    assert dict(frames[0].start.environment) == {"A": "B"}
    assert frames[1].WhichOneof("payload") == "stdin"
    assert bytes(frames[1].stdin) == b"ls\n"
    assert frames[2].WhichOneof("payload") == "resize"
    assert frames[2].resize.cols == 120
    assert frames[2].resize.rows == 40


class _FakeCall:
    def __init__(self, events, parent):
        self._events = events
        self._parent = parent

    def __iter__(self):
        return iter(self._events)

    def cancel(self):
        self._parent.cancelled = True


class _FakeBidiStub:
    def __init__(self, events):
        self._events = events
        self.sent: Any = None
        self.cancelled = False
        self.timeout = "unset"

    def ExecSandboxInteractive(self, request_iter, timeout=None):
        # Draining the request iterator captures start + stdin/resize frames
        # (it stops at the None sentinel placed on the inbound queue).
        self.sent = list(request_iter)
        self.timeout = timeout
        return _FakeCall(self._events, self)


def test_exec_interactive_sends_input_and_yields_events():
    """exec_interactive forwards start/stdin frames and converts output events."""
    events = [
        openshell_pb2.ExecSandboxEvent(stdout=openshell_pb2.ExecSandboxStdout(data=b"hi")),
        openshell_pb2.ExecSandboxEvent(stderr=openshell_pb2.ExecSandboxStderr(data=b"warn")),
        openshell_pb2.ExecSandboxEvent(exit=openshell_pb2.ExecSandboxExit(exit_code=3)),
    ]
    stub = _FakeBidiStub(events)
    mgr = object.__new__(SandboxManager)
    mgr._stub = stub  # type: ignore[assignment]
    mgr._timeout = 30.0

    inbound: queue.Queue = queue.Queue()
    inbound.put({"type": "stdin", "data": b"x"})
    inbound.put(None)
    captured = {}

    out = list(
        mgr.exec_interactive(
            "sid",
            ["bash"],
            inbound=inbound,
            on_call=lambda c: captured.setdefault("call", c),
            cols=90,
            rows=30,
        )
    )

    assert out == [
        {"type": "stdout", "data": b"hi"},
        {"type": "stderr", "data": b"warn"},
        {"type": "exit", "exit_code": 3},
    ]
    # First sent frame is the start request with the initial window size.
    assert stub.sent[0].start.cols == 90
    assert stub.sent[0].start.rows == 30
    assert bytes(stub.sent[1].stdin) == b"x"
    # on_call received the live call, and teardown cancelled it.
    assert "call" in captured
    assert stub.cancelled is True
