"""Structured logging and request correlation support."""

from __future__ import annotations

import contextvars
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

# Context variable holding the current request's correlation ID.
# Set per tool-call in server.py; available throughout the call chain.
correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)


def new_correlation_id() -> str:
    """Generate and set a new correlation ID for the current context."""
    cid = uuid.uuid4().hex[:16]
    correlation_id_var.set(cid)
    return cid


def get_correlation_id() -> str:
    """Return current correlation ID or empty string."""
    return correlation_id_var.get()


class JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Add correlation ID if present
        cid = correlation_id_var.get()
        if cid:
            log_entry["correlation_id"] = cid

        # Include extra fields attached by callers
        for key in ("method", "path", "status", "duration_ms", "tool", "event"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val

        # Include exception info if present
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


def configure_logging(level: str, fmt: str) -> None:
    """Configure root logger with either text or JSON format.

    Args:
        level: Log level name (e.g. "INFO", "DEBUG").
        fmt: Either "text" for plain format or "json" for structured JSON lines.
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers (avoids duplicate output if called twice)
    root.handlers.clear()

    handler = logging.StreamHandler()
    if fmt == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    root.addHandler(handler)
