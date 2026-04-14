"""Structured audit-log export lanes for M28 Observability (pillar 3).

Three independent dispatch lanes, each togglable via ``AuditSettings``:

* **stdout-JSON** — one JSON line per audit entry on stdout, suitable for
  container log collectors (Loki via Promtail, Vector, Fluent Bit, ...).
* **Syslog** — RFC 5424-ish framing via ``logging.handlers.SysLogHandler``;
  the message body is the same JSON document as the stdout lane.
* **Webhook** — reuses the existing :func:`shoreguard.services.webhooks.fire_webhook`
  bus by firing ``audit.entry`` events; SIEM connectors subscribe to that
  event type via the normal webhook UI.

Every lane is best-effort: a failure in one lane is logged at WARNING and
never propagates to the audit write path. The exporter deliberately does
not retry on its own — the webhook lane already has its own delivery
retry, and stdout/syslog failures are infrastructure problems that should
surface as log noise rather than half-blocked audit writes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shoreguard.settings import AuditSettings

logger = logging.getLogger(__name__)


_SYSLOG_FACILITIES: dict[str, int] = {
    "kern": logging.handlers.SysLogHandler.LOG_KERN,
    "user": logging.handlers.SysLogHandler.LOG_USER,
    "mail": logging.handlers.SysLogHandler.LOG_MAIL,
    "daemon": logging.handlers.SysLogHandler.LOG_DAEMON,
    "auth": logging.handlers.SysLogHandler.LOG_AUTH,
    "syslog": logging.handlers.SysLogHandler.LOG_SYSLOG,
    "local0": logging.handlers.SysLogHandler.LOG_LOCAL0,
    "local1": logging.handlers.SysLogHandler.LOG_LOCAL1,
    "local2": logging.handlers.SysLogHandler.LOG_LOCAL2,
    "local3": logging.handlers.SysLogHandler.LOG_LOCAL3,
    "local4": logging.handlers.SysLogHandler.LOG_LOCAL4,
    "local5": logging.handlers.SysLogHandler.LOG_LOCAL5,
    "local6": logging.handlers.SysLogHandler.LOG_LOCAL6,
    "local7": logging.handlers.SysLogHandler.LOG_LOCAL7,
}


class AuditExporter:
    """Fan out audit entries across enabled export lanes.

    The exporter is constructed once at app startup and reads its
    configuration from :class:`shoreguard.settings.AuditSettings`. The
    webhook lane requires a reference to the main asyncio loop so it can
    schedule delivery from inside the synchronous ``AuditService.log``
    call (which runs in an ``asyncio.to_thread`` worker thread).

    Args:
        settings: Current audit settings snapshot.
        loop: Main event loop used to schedule async webhook dispatch.
            If ``None``, the webhook lane is disabled.

    Attributes:
        enabled (bool): True when at least one export lane is active.
    """

    def __init__(  # noqa: D107
        self,
        settings: AuditSettings,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._settings = settings
        self._loop = loop
        self._stdout_logger: logging.Logger | None = None
        self._syslog_logger: logging.Logger | None = None

        if settings.export_stdout_json:
            self._stdout_logger = self._build_stdout_logger()
        if settings.export_syslog_enabled:
            self._syslog_logger = self._build_syslog_logger()

    @property
    def enabled(self) -> bool:
        """True when at least one export lane is active."""
        return (
            self._settings.export_stdout_json
            or self._settings.export_syslog_enabled
            or self._settings.export_webhook_enabled
        )

    def dispatch(self, entry: dict[str, Any]) -> None:
        """Fan *entry* out across every enabled lane; never raises.

        Args:
            entry: Serialised audit entry (output of ``AuditService._to_dict``).
        """
        if self._stdout_logger is not None:
            self._emit_stdout(entry)
        if self._syslog_logger is not None:
            self._emit_syslog(entry)
        if self._settings.export_webhook_enabled and self._loop is not None:
            self._emit_webhook(entry)

    def _emit_stdout(self, entry: dict[str, Any]) -> None:
        try:
            assert self._stdout_logger is not None
            self._stdout_logger.info(json.dumps(entry, default=str))
        except Exception:
            logger.warning("Audit export: stdout lane failed", exc_info=True)

    def _emit_syslog(self, entry: dict[str, Any]) -> None:
        try:
            assert self._syslog_logger is not None
            self._syslog_logger.info(json.dumps(entry, default=str))
        except Exception:
            logger.warning("Audit export: syslog lane failed", exc_info=True)

    def _emit_webhook(self, entry: dict[str, Any]) -> None:
        try:
            from shoreguard.services.webhooks import fire_webhook

            assert self._loop is not None
            asyncio.run_coroutine_threadsafe(
                fire_webhook("audit.entry", entry),
                self._loop,
            )
        except Exception:
            logger.warning("Audit export: webhook lane failed", exc_info=True)

    def _build_stdout_logger(self) -> logging.Logger:
        """Return a dedicated ``shoreguard.audit.export.stdout`` logger.

        The logger writes to stdout via a :class:`logging.StreamHandler`
        with an empty formatter so the message (already a JSON string)
        lands on its own line. ``propagate=False`` keeps it out of the
        root logger's normal formatter chain.

        Returns:
            logging.Logger: Configured logger dedicated to the stdout lane.
        """
        import sys

        log = logging.getLogger("shoreguard.audit.export.stdout")
        log.setLevel(logging.INFO)
        log.propagate = False
        # Always rebuild handlers so the StreamHandler latches onto the
        # current ``sys.stdout`` — matters for test isolation and for any
        # host that swaps stdout at runtime.
        for h in list(log.handlers):
            log.removeHandler(h)
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(handler)
        return log

    def _build_syslog_logger(self) -> logging.Logger:
        """Return a dedicated ``shoreguard.audit.export.syslog`` logger.

        Creates a :class:`logging.handlers.SysLogHandler` pointed at the
        configured host/port/facility. Failures to contact the syslog
        receiver at construction time are NOT raised — they log a warning
        and the lane becomes a silent no-op until restart.

        Returns:
            logging.Logger: Configured logger dedicated to the syslog lane.
        """
        log = logging.getLogger("shoreguard.audit.export.syslog")
        log.setLevel(logging.INFO)
        log.propagate = False
        for h in list(log.handlers):
            log.removeHandler(h)

        facility = _SYSLOG_FACILITIES.get(
            self._settings.export_syslog_facility.lower(),
            logging.handlers.SysLogHandler.LOG_USER,
        )
        try:
            handler = logging.handlers.SysLogHandler(
                address=(self._settings.export_syslog_host, self._settings.export_syslog_port),
                facility=facility,
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            log.addHandler(handler)
        except OSError:
            logger.warning(
                "Audit export: could not connect syslog to %s:%d — lane disabled",
                self._settings.export_syslog_host,
                self._settings.export_syslog_port,
                exc_info=True,
            )
        return log
