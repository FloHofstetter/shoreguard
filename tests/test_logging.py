"""Tests for the structured JSON log formatter and request-ID plumbing."""

from __future__ import annotations

import json
import logging

import pytest

from shoreguard.api.logging_config import JSONFormatter
from shoreguard.api.metrics import RequestIdFilter, request_id_ctx


def _make_record(
    *,
    name: str = "shoreguard.test",
    level: int = logging.INFO,
    msg: str = "hello",
    args: tuple = (),
    extra: dict | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname="/tmp/x.py",
        lineno=42,
        msg=msg,
        args=args,
        exc_info=None,
        func="test_func",
    )
    if extra:
        for k, v in extra.items():
            setattr(record, k, v)
    return record


def test_json_formatter_basic_fields() -> None:
    record = _make_record(msg="hello world")
    out = json.loads(JSONFormatter().format(record))
    assert out["level"] == "INFO"
    assert out["logger"] == "shoreguard.test"
    assert out["message"] == "hello world"
    assert out["func"] == "test_func"
    assert out["line"] == 42
    assert out["module"] == "x"
    assert "timestamp" in out


def test_json_formatter_includes_request_id() -> None:
    token = request_id_ctx.set("abc123")
    try:
        record = _make_record()
        out = json.loads(JSONFormatter().format(record))
        assert out["request_id"] == "abc123"
    finally:
        request_id_ctx.reset(token)


def test_json_formatter_omits_request_id_when_unset() -> None:
    # Ensure a clean slate
    token = request_id_ctx.set(None)
    try:
        record = _make_record()
        out = json.loads(JSONFormatter().format(record))
        assert "request_id" not in out
    finally:
        request_id_ctx.reset(token)


def test_json_formatter_merges_extras() -> None:
    record = _make_record(extra={"gateway": "g1", "operation_id": 42})
    out = json.loads(JSONFormatter().format(record))
    assert out["gateway"] == "g1"
    assert out["operation_id"] == 42


def test_json_formatter_skips_private_extras() -> None:
    record = _make_record(extra={"_internal": "secret", "public": "ok"})
    out = json.loads(JSONFormatter().format(record))
    assert "_internal" not in out
    assert out["public"] == "ok"


def test_json_formatter_handles_exception() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        exc_info = sys.exc_info()
    record = logging.LogRecord(
        name="x",
        level=logging.ERROR,
        pathname="/tmp/x.py",
        lineno=1,
        msg="failed",
        args=(),
        exc_info=exc_info,
    )
    out = json.loads(JSONFormatter().format(record))
    assert "exception" in out
    assert "ValueError: boom" in out["exception"]


def test_request_id_filter_injects_default() -> None:
    token = request_id_ctx.set(None)
    try:
        f = RequestIdFilter()
        record = _make_record()
        assert f.filter(record) is True
        assert record.request_id == "-"  # type: ignore[attr-defined]
    finally:
        request_id_ctx.reset(token)


def test_request_id_filter_injects_contextvar() -> None:
    token = request_id_ctx.set("xyz")
    try:
        f = RequestIdFilter()
        record = _make_record()
        f.filter(record)
        assert record.request_id == "xyz"  # type: ignore[attr-defined]
    finally:
        request_id_ctx.reset(token)


def test_text_format_renders_request_id_via_filter(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Text-mode format string %(request_id)s must not raise KeyError."""
    fmt = "%(levelname)s [%(request_id)s] %(message)s"
    formatter = logging.Formatter(fmt)
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.addFilter(RequestIdFilter())

    token = request_id_ctx.set("req42")
    try:
        record = _make_record(msg="hi")
        # Filter attaches request_id, formatter consumes it
        handler.filter(record)
        out = formatter.format(record)
        assert "[req42]" in out
        assert "hi" in out
    finally:
        request_id_ctx.reset(token)
