"""Structured JSON logging.

One event per line. Every record carries ts, level, component (logger name),
and any structured fields passed via ``extra={"context": {...}}`` (e.g. run_id,
group_id). This replaces the old channel-filter scheme that dropped INFO lines
and emitted timestamp-less, alphabetically-sorted key=value blobs.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Any

_RESERVED = set(logging.makeLogRecord({}).__dict__.keys()) | {"context", "message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _dt.datetime.fromtimestamp(record.created, _dt.timezone.utc).isoformat(),
            "level": record.levelname,
            "component": record.name,
            "msg": record.getMessage(),
        }
        ctx = getattr(record, "context", None)
        if isinstance(ctx, dict):
            for key, value in ctx.items():
                if key not in payload:
                    payload[key] = value
        # Allow ad-hoc structured fields passed directly via extra=.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))
    # Quiet noisy third-party access logs; we emit our own structured events.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Emit a structured event: ``logger`` is the component, ``event`` the msg."""
    logger.log(level, event, extra={"context": fields})
