"""Structured JSON logging.

One event per line. Every record carries ts, level, component (logger name),
and any structured fields passed via ``extra={"context": {...}}`` (e.g. job_id,
group_id, run_id). Mirrors the backend logging format so worker and backend
logs interleave cleanly.
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


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Emit a structured event: ``logger`` is the component, ``event`` the msg."""
    logger.log(level, event, extra={"context": fields})
