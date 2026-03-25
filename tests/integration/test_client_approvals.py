"""Integration tests for draft policy approval flow via client layer."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_get_draft_empty(ready_sandbox, sg_client):
    """get_draft() returns a valid structure on a fresh sandbox."""
    result = sg_client.approvals.get_draft(ready_sandbox["name"])

    assert "chunks" in result
    assert isinstance(result["chunks"], list)
    assert "draft_version" in result
    assert "rolling_summary" in result
    assert "last_analyzed_at_ms" in result


def test_get_pending_empty(ready_sandbox, sg_client):
    """get_pending() returns a list (likely empty on fresh sandbox)."""
    result = sg_client.approvals.get_pending(ready_sandbox["name"])

    assert isinstance(result, list)


def test_get_history(ready_sandbox, sg_client):
    """get_history() returns a list of decision entries."""
    result = sg_client.approvals.get_history(ready_sandbox["name"])

    assert isinstance(result, list)
    for entry in result:
        assert "timestamp_ms" in entry
        assert "event_type" in entry
