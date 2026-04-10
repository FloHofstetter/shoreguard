"""Tests for the ``_truncate_result`` helper in ``shoreguard.services.operations``.

The service classes themselves are covered by ``test_operations_async.py``
against the production ``AsyncOperationService``. This file targets only the
module-level ``_truncate_result`` helper, which has no DB dependencies and is
exercised by both sync and async code paths.
"""

from __future__ import annotations

import json


def test_truncate_result_returns_unmodified_when_under_limit():
    """If the JSON fits within max_bytes, return it unchanged."""
    from shoreguard.services.operations import _truncate_result

    result = {"exit_code": 0, "stdout": "hello"}
    out = _truncate_result(result, max_bytes=10_000)
    assert json.loads(out) == result
    assert "truncated" not in out


def test_truncate_result_exact_boundary():
    """Payload exactly at max_bytes should pass without truncation."""
    from shoreguard.services.operations import _truncate_result

    result = {"ok": True}
    result_str = json.dumps(result, default=str)
    exact_len = len(result_str.encode())
    out = _truncate_result(result, max_bytes=exact_len)
    assert json.loads(out) == result
    assert "truncated" not in json.loads(out)


def test_truncate_result_one_byte_over_triggers_truncation():
    """Payload one byte over max_bytes triggers truncation logic."""
    from shoreguard.services.operations import _truncate_result

    result = {"stdout": "x" * 500}
    result_str = json.dumps(result, default=str)
    exact_len = len(result_str.encode())
    out = _truncate_result(result, max_bytes=exact_len - 1)
    parsed = json.loads(out)
    assert parsed["truncated"] is True


def test_truncate_result_truncates_stderr():
    """stderr field gets truncated when it's the large field."""
    from shoreguard.services.operations import _truncate_result

    result = {"stderr": "e" * 100_000, "exit_code": 1}
    out = _truncate_result(result, max_bytes=10_000)
    parsed = json.loads(out)
    assert parsed["truncated"] is True
    assert len(parsed["stderr"]) == 8000  # field_truncation_chars default


def test_truncate_result_truncates_output_field():
    """output field gets truncated."""
    from shoreguard.services.operations import _truncate_result

    result = {"output": "o" * 100_000}
    out = _truncate_result(result, max_bytes=10_000)
    parsed = json.loads(out)
    assert parsed["truncated"] is True
    assert len(parsed["output"]) == 8000


def test_truncate_result_truncates_logs_field():
    """logs field gets truncated."""
    from shoreguard.services.operations import _truncate_result

    result = {"logs": "L" * 100_000}
    out = _truncate_result(result, max_bytes=10_000)
    parsed = json.loads(out)
    assert parsed["truncated"] is True
    assert len(parsed["logs"]) == 8000


def test_truncate_result_non_string_field_not_truncated():
    """Non-string truncatable fields are skipped (e.g., stdout as a list)."""
    from shoreguard.services.operations import _truncate_result

    result = {"stdout": ["line1", "line2"] * 5000}
    out = _truncate_result(result, max_bytes=100)
    parsed = json.loads(out)
    assert parsed["truncated"] is True
    assert parsed["error"] == "Result too large to store"


def test_truncate_result_stops_after_first_sufficient_field():
    """Truncation stops as soon as one field brings it under the limit."""
    from shoreguard.services.operations import _truncate_result

    result = {"stdout": "x" * 100_000, "stderr": "small"}
    out = _truncate_result(result, max_bytes=20_000)
    parsed = json.loads(out)
    assert parsed["truncated"] is True
    assert len(parsed["stdout"]) == 8000
    assert parsed["stderr"] == "small"


def test_truncate_result_multiple_large_fields():
    """When multiple fields are large and first truncation isn't enough, try next."""
    from shoreguard.services.operations import _truncate_result

    result = {"stdout": "x" * 50_000, "stderr": "e" * 50_000}
    out = _truncate_result(result, max_bytes=20_000)
    parsed = json.loads(out)
    assert parsed["truncated"] is True
    assert len(parsed["stdout"]) == 8000
    assert len(parsed["stderr"]) == 8000


def test_truncate_result_fallback_when_all_truncation_insufficient():
    """When truncating all fields still exceeds max_bytes, use fallback."""
    from shoreguard.services.operations import _truncate_result

    result = {"stdout": "x" * 100_000}
    out = _truncate_result(result, max_bytes=100)
    parsed = json.loads(out)
    assert parsed == {"truncated": True, "error": "Result too large to store"}


def test_truncate_result_field_not_present():
    """Fields not in the result are simply skipped."""
    from shoreguard.services.operations import _truncate_result

    result = {"custom_big_field": "z" * 100_000}
    out = _truncate_result(result, max_bytes=100)
    parsed = json.loads(out)
    assert parsed == {"truncated": True, "error": "Result too large to store"}


def test_truncate_result_preserves_other_keys():
    """Non-truncatable keys are preserved in output."""
    from shoreguard.services.operations import _truncate_result

    result = {"stdout": "x" * 100_000, "exit_code": 42, "signal": None}
    out = _truncate_result(result, max_bytes=20_000)
    parsed = json.loads(out)
    assert parsed["exit_code"] == 42
    assert parsed["signal"] is None
    assert parsed["truncated"] is True
