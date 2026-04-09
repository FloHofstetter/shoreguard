"""Structured JSON log formatter for production use.

When ``SHOREGUARD_LOG_FORMAT=json`` (the default inside Docker), all log
output is emitted as one JSON object per line.  This makes logs
machine-parseable for Loki, CloudWatch, or any log aggregator.

For local development use ``SHOREGUARD_LOG_FORMAT=text`` (the default
when running ``shoreguard`` directly) to keep the human-friendly format.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from shoreguard.api.metrics import request_id_ctx

# Standard LogRecord attributes that should NOT be treated as "extras".
# Anything in ``record.__dict__`` that is not in this set is assumed to
# have come from a caller passing ``extra={...}`` to the logger and is
# merged into the output JSON object.
_STANDARD_LOGRECORD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
        "request_id",  # injected by RequestIdFilter — we render it separately
    }
)


class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        """Format *record* as a JSON string.

        Args:
            record: The log record to format.

        Returns:
            str: A single-line JSON string.
        """
        ts = datetime.fromtimestamp(record.created, tz=UTC).isoformat()
        entry: dict[str, object] = {
            "timestamp": ts,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }
        rid = request_id_ctx.get()
        if rid:
            entry["request_id"] = rid

        # Merge caller-supplied extras.  Anything attached to the record
        # that isn't a standard LogRecord attribute is treated as an
        # extra and included in the output.  This makes ``logger.info(
        # "x", extra={"gateway": "g1"})`` show up as a first-class field.
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOGRECORD_ATTRS or key in entry:
                continue
            if key.startswith("_"):
                continue
            entry[key] = value

        if record.exc_info and record.exc_info[1] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            entry["stack"] = record.stack_info
        return json.dumps(entry, default=str)
