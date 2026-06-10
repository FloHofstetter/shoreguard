"""Unit tests for SandboxManager.issue_token / refresh_token."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from shoreguard.client.sandboxes import SandboxManager


class _FakeStub:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def IssueSandboxToken(self, req, timeout=None):
        self.requests.append(("issue", req))
        return SimpleNamespace(token="jwt-issue", expires_at_ms=1000)

    async def RefreshSandboxToken(self, req, timeout=None):
        self.requests.append(("refresh", req))
        return SimpleNamespace(token="jwt-refresh", expires_at_ms=0)


def _mgr(stub):
    m = object.__new__(SandboxManager)
    m._stub = stub  # type: ignore[assignment]
    m._timeout = 30.0
    return m


async def test_issue_token_returns_token_and_expiry():
    """issue_token returns the minted token and expiry."""
    stub = _FakeStub()
    result = await _mgr(stub).issue_token()
    assert result == {"token": "jwt-issue", "expires_at_ms": 1000}
    assert stub.requests[0][0] == "issue"


async def test_refresh_token_returns_token_and_expiry():
    """refresh_token returns the fresh token (0 expiry = non-expiring)."""
    stub = _FakeStub()
    result = await _mgr(stub).refresh_token()
    assert result == {"token": "jwt-refresh", "expires_at_ms": 0}
    assert stub.requests[0][0] == "refresh"
