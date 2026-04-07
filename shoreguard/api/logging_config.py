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
        }
        rid = request_id_ctx.get()
        if rid:
            entry["request_id"] = rid
        if record.exc_info and record.exc_info[1] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)
