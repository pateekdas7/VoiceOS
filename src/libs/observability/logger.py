"""StructuredLogger — JSON structured logging with mandatory correlation fields (V3 Ch16).

Every log line is a single JSON object carrying ``timestamp``, ``level``,
``service``, ``tenant_id``, ``call_id``, ``trace_id``, ``correlation_id``,
and ``message`` — the fields needed to reconstruct a call's story without
duplicating the authoritative event log (V3 Ch16 §16.2, §16.12).

Sprint-020: every emitted string (the ``message`` plus any string-valued
``**fields``) is passed through ``PIIRedactor`` before serialization — V4
Ch10 §10.12 "PII never logged in clear (redaction at emission)". This is
unconditional (not an opt-in constructor flag) so no log path can bypass it
by omission (Sprint-020.md DoD "PIIRedactor applied to ALL log paths").

Architecture: V3 Ch16 (Logging Architecture) §16.7, §16.12; V4 Ch10 (PII
Protection) §10.12.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import TextIO

from src.libs.pii.redactor import PIIRedactor

_REDACTOR = PIIRedactor()

_LEVEL_TO_NUMERIC = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


class StructuredLogger:
    """Emits one JSON object per log line, with mandatory correlation fields."""

    def __init__(self, service_name: str, *, stream: TextIO | None = None) -> None:
        """
        Args:
            service_name: This process's service name (the ``service`` field).
            stream: Output stream; defaults to ``sys.stdout``. Inject an
                ``io.StringIO()`` in tests to capture emitted lines.
        """
        self._service_name = service_name
        self._logger = logging.getLogger(f"voiceos.{service_name}.{id(self)}")
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        self._logger.handlers.clear()

        handler = logging.StreamHandler(stream or sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(handler)

    def log(
        self,
        level: str,
        message: str,
        *,
        tenant_id: str = "",
        call_id: str = "",
        trace_id: str = "",
        correlation_id: str = "",
        **fields: object,
    ) -> None:
        """Emit one structured JSON log line.

        Args:
            level: One of DEBUG/INFO/WARNING/ERROR.
            message: Human-readable message.
            tenant_id: Tenant scope (AR-8), if known.
            call_id: The call this event belongs to, if any.
            trace_id: The OTel trace this event belongs to, if any (V3 Ch17).
            correlation_id: The turn/request correlation id, if any.
            **fields: Additional structured context, merged into the JSON object.
        """
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": level,
            "service": self._service_name,
            "tenant_id": tenant_id,
            "call_id": call_id,
            "trace_id": trace_id,
            "correlation_id": correlation_id,
            "message": _REDACTOR.redact(message),
            **{key: (_REDACTOR.redact(value) if isinstance(value, str) else value) for key, value in fields.items()},
        }
        self._logger.log(_LEVEL_TO_NUMERIC[level], json.dumps(record))

    def debug(self, message: str, **kwargs: object) -> None:
        self.log("DEBUG", message, **kwargs)  # type: ignore[arg-type]

    def info(self, message: str, **kwargs: object) -> None:
        self.log("INFO", message, **kwargs)  # type: ignore[arg-type]

    def warning(self, message: str, **kwargs: object) -> None:
        self.log("WARNING", message, **kwargs)  # type: ignore[arg-type]

    def error(self, message: str, **kwargs: object) -> None:
        self.log("ERROR", message, **kwargs)  # type: ignore[arg-type]
