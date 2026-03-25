"""Tests for ApprovalService delegation to client.approvals."""

from __future__ import annotations

import pytest

from shoreguard.services.approvals import ApprovalService


@pytest.fixture
def approval_svc(mock_client):
    """ApprovalService with a mocked client."""
    return ApprovalService(mock_client)


def test_get_draft_delegates(approval_svc, mock_client):
    """get_draft passes sandbox_name and status_filter to client."""
    mock_client.approvals.get_draft.return_value = {"chunks": [], "draft_version": 1}

    result = approval_svc.get_draft("sb1", status_filter="pending")

    mock_client.approvals.get_draft.assert_called_once_with("sb1", status_filter="pending")
    assert result["draft_version"] == 1


def test_get_pending_delegates(approval_svc, mock_client):
    """get_pending returns list from client."""
    mock_client.approvals.get_pending.return_value = [{"id": "c1"}]

    result = approval_svc.get_pending("sb1")

    mock_client.approvals.get_pending.assert_called_once_with("sb1")
    assert result[0]["id"] == "c1"


def test_approve_delegates(approval_svc, mock_client):
    """approve passes sandbox_name and chunk_id to client."""
    mock_client.approvals.approve.return_value = {"policy_version": 3}

    result = approval_svc.approve("sb1", "chunk-abc")

    mock_client.approvals.approve.assert_called_once_with("sb1", "chunk-abc")
    assert result["policy_version"] == 3


def test_reject_delegates(approval_svc, mock_client):
    """reject passes reason keyword arg to client."""
    approval_svc.reject("sb1", "chunk-abc", reason="too broad")

    mock_client.approvals.reject.assert_called_once_with("sb1", "chunk-abc", reason="too broad")


def test_approve_all_delegates(approval_svc, mock_client):
    """approve_all forwards include_security_flagged."""
    mock_client.approvals.approve_all.return_value = {"chunks_approved": 5}

    result = approval_svc.approve_all("sb1", include_security_flagged=True)

    mock_client.approvals.approve_all.assert_called_once_with("sb1", include_security_flagged=True)
    assert result["chunks_approved"] == 5


def test_edit_delegates(approval_svc, mock_client):
    """edit passes proposed_rule to client."""
    rule = {"endpoints": [{"host": "example.com", "port": 443}]}

    approval_svc.edit("sb1", "chunk-abc", rule)

    mock_client.approvals.edit.assert_called_once_with("sb1", "chunk-abc", rule)


def test_undo_delegates(approval_svc, mock_client):
    """undo passes chunk_id to client."""
    mock_client.approvals.undo.return_value = {"policy_version": 2}

    result = approval_svc.undo("sb1", "chunk-abc")

    mock_client.approvals.undo.assert_called_once_with("sb1", "chunk-abc")
    assert result["policy_version"] == 2


def test_clear_delegates(approval_svc, mock_client):
    """clear returns chunks_cleared count."""
    mock_client.approvals.clear.return_value = {"chunks_cleared": 3}

    result = approval_svc.clear("sb1")

    mock_client.approvals.clear.assert_called_once_with("sb1")
    assert result["chunks_cleared"] == 3


def test_get_history_delegates(approval_svc, mock_client):
    """get_history returns list from client."""
    mock_client.approvals.get_history.return_value = [{"event_type": "approved"}]

    result = approval_svc.get_history("sb1")

    mock_client.approvals.get_history.assert_called_once_with("sb1")
    assert result[0]["event_type"] == "approved"
