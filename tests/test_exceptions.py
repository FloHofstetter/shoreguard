"""Tests for shoreguard.exceptions — friendly_grpc_error()."""

from __future__ import annotations

import grpc

from shoreguard.exceptions import friendly_grpc_error


class _FakeRpcError(grpc.RpcError):
    """Minimal gRPC error stub for testing."""

    def __init__(self, code, details=""):
        self._code = code
        self._details = details

    def code(self):
        return self._code

    def details(self):
        return self._details


def test_grpc_error_with_details():
    """When gRPC error has details(), return the detail string."""
    exc = _FakeRpcError(grpc.StatusCode.INTERNAL, "something broke")
    assert friendly_grpc_error(exc) == "something broke"


def test_grpc_error_known_code_no_details():
    """Known code without details returns a friendly message from _GRPC_FRIENDLY."""
    exc = _FakeRpcError(grpc.StatusCode.UNIMPLEMENTED, "")
    result = friendly_grpc_error(exc)
    # Should return the friendly string, not the raw code name
    assert result != "gRPC error: UNIMPLEMENTED"
    assert len(result) > 0


def test_grpc_error_unknown_code_no_details():
    """Unknown code without details returns 'gRPC error: CODE_NAME'."""
    exc = _FakeRpcError(grpc.StatusCode.DATA_LOSS, "")
    assert friendly_grpc_error(exc) == "gRPC error: DATA_LOSS"


def test_non_grpc_exception():
    """Non-gRPC exceptions return str(exc)."""
    exc = ValueError("bad value")
    assert friendly_grpc_error(exc) == "bad value"
