"""JSON log lines with trace correlation and redaction of sensitive fields."""

import dataclasses
import json
import logging
import re
import sys
import traceback
from collections.abc import Mapping
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

from pydantic import BaseModel

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
        "api_key",
        "api-key",
        "apikey",
        "passwd",
        "pwd",
    }
)
# Attributes every LogRecord has; anything else arrived through `extra=`.
_STANDARD_ATTRS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


def redact(value: Any) -> Any:
    if isinstance(value, BaseModel):
        value = value.model_dump()
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    if isinstance(value, Mapping):
        return {
            key: REDACTED if str(key).lower() in SENSITIVE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    return value


# `_sanitize_message` defends the message text itself: `redact` only reaches `extra=`, but a
# %-arg, an f-string, or a third-party logger (uvicorn's access log, for example) can put a
# secret straight into `record.msg`. Each rule is a compiled regex, applied in order.

# 1. A query string on a URL or path: "?" right after a path/URL character, up to whitespace or
#    a quote. The path itself is left readable.
_QUERY_STRING_RE = re.compile(r"(?<=[\w/])\?[^\s\"']+")

# 2. "Bearer <token>", case-insensitive.
_BEARER_TOKEN_RE = re.compile(r"\bBearer\s+\S+", re.IGNORECASE)

# 3. "key=value", "key: value" or '"key": "value"' for any name in SENSITIVE_KEYS, case
#    insensitive. The value runs to whitespace, ",", ";", "&" or a closing quote; a quoted value
#    is matched together with its quotes.
_SENSITIVE_KEY_ALTERNATION = "|".join(
    sorted((re.escape(key) for key in SENSITIVE_KEYS), key=len, reverse=True)
)
_SENSITIVE_KV_RE = re.compile(
    rf'"?\b(?P<key>{_SENSITIVE_KEY_ALTERNATION})\b"?\s*(?P<sep>[:=])\s*'
    r"""(?:"[^"]*"|'[^']*'|[^\s,;&]+)""",
    re.IGNORECASE,
)

# 4. A bare email address.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")


def _redact_sensitive_kv(match: re.Match[str]) -> str:
    return f"{match.group('key')}{match.group('sep')}{REDACTED}"


def _sanitize_message(text: str) -> str:
    """A plain event name (`"unhandled_error"`) or a path with no query string passes through
    unchanged. Anything that looks like a query string, a bearer token, a sensitive key/value
    pair or an email address is redacted."""
    text = _QUERY_STRING_RE.sub(f"?{REDACTED}", text)
    text = _BEARER_TOKEN_RE.sub(f"Bearer {REDACTED}", text)
    text = _SENSITIVE_KV_RE.sub(_redact_sensitive_kv, text)
    text = _EMAIL_RE.sub(REDACTED, text)
    return text


def _exception_type_name(exc_type: type[BaseException]) -> str:
    """`module.QualName`; for a builtin, just the name (`ValueError`, not `builtins.ValueError`)."""
    if exc_type.__module__ in ("builtins", "__main__"):
        return exc_type.__qualname__
    return f"{exc_type.__module__}.{exc_type.__qualname__}"


def _stack_frames(tb: TracebackType | None) -> list[dict[str, Any]]:
    """Frames as code (file, line, function), never the source line text or the exception's
    message: a source line can itself hold a literal secret, such as a hardcoded DSN."""
    return [
        {"file": f.filename, "line": f.lineno, "function": f.name} for f in traceback.extract_tb(tb)
    ]


def _exception_summary(exc: BaseException) -> dict[str, Any]:
    """An exception's type and stack: code, never its message."""
    return {"type": _exception_type_name(type(exc)), "stack": _stack_frames(exc.__traceback__)}


def _chained(exc: BaseException) -> BaseException | None:
    if exc.__cause__ is not None:
        return exc.__cause__
    return None if exc.__suppress_context__ else exc.__context__


def _exception_entry(exc: BaseException) -> dict[str, Any]:
    """The exception's type and stack, plus the same for every chained cause/context, with no
    exception messages anywhere: a message can hold a password, token, DSN, email or upload
    line."""
    causes: list[dict[str, Any]] = []
    seen = {id(exc)}
    chained = _chained(exc)
    while chained is not None and id(chained) not in seen:
        seen.add(id(chained))
        causes.append(_exception_summary(chained))
        chained = _chained(chained)
    return {**_exception_summary(exc), "causes": causes}


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
            "message": _sanitize_message(record.getMessage()),
            "service": self._service,
            "stage": self._stage,
            "trace_id": current_trace_id(),
            "span_id": current_span_id(),
            **redact(extras),
        }
        if record.exc_info and record.exc_info[1] is not None:
            entry["exception"] = _exception_entry(record.exc_info[1])
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
