"""JSON log lines with trace correlation and redaction of sensitive fields."""

import json
import logging
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from nettriage.platform.config import Settings
from nettriage.platform.trace_context import current_span_id, current_trace_id

REDACTED = "[REDACTED]"
SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "session",
        "session_id",
        "email",
        "invite_token",
        "prompt",
        "completion",
        "model_output",
        "raw_line",
    }
)
# Attributes every LogRecord has; anything else arrived through `extra=`.
_STANDARD_ATTRS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: REDACTED if str(key).lower() in SENSITIVE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str, stage: str) -> None:
        super().__init__()
        self._service = service
        self._stage = stage

    def format(self, record: logging.LogRecord) -> str:
        extras = {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS}
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self._service,
            "stage": self._stage,
            "trace_id": current_trace_id(),
            "span_id": current_span_id(),
            **redact(extras),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(settings: Settings, level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(settings.service_name, settings.stage))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # uvicorn's own dictConfig gives these loggers their own handlers and propagate=False;
    # strip both so uvicorn's records reach the root JSON handler too.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
