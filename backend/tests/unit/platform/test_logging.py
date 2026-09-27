import json
import logging
import logging.config
import sys
from dataclasses import dataclass

import pytest
import uvicorn.config
from opentelemetry.sdk.trace import TracerProvider
from pydantic import BaseModel

from nettriage.platform.config import Settings
from nettriage.platform.logging import REDACTED, JsonFormatter, configure_logging


def _record(msg: str, **extra: object) -> logging.LogRecord:
    record = logging.makeLogRecord(
        {"name": "test", "levelname": "INFO", "levelno": logging.INFO, "msg": msg}
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def _format(record: logging.LogRecord) -> dict[str, object]:
    line: dict[str, object] = json.loads(
        JsonFormatter(service="nettriage-api", stage="local").format(record)
    )
    return line


def test_log_line_is_json_with_service_fields() -> None:
    line = _format(_record("upload_created", org_id="o1"))

    assert line["message"] == "upload_created"
    assert line["level"] == "INFO"
    assert line["service"] == "nettriage-api"
    assert line["stage"] == "local"
    assert line["org_id"] == "o1"


def test_log_line_carries_the_current_trace_id() -> None:
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("request") as span:
        line = _format(_record("inside_span"))

    assert line["trace_id"] == format(span.get_span_context().trace_id, "032x")
    assert isinstance(line["span_id"], str)
    assert len(line["span_id"]) == 16


@pytest.mark.parametrize(
    "key",
    [
        "email",
        "password",
        "token",
        "cookie",
        "authorization",
        "session_id",
        "invite_token",
        "prompt",
        "raw_line",
    ],
)
def test_sensitive_fields_are_redacted(key: str) -> None:
    line = _format(_record("event", **{key: "sensitive-value"}))

    assert line[key] == REDACTED
    assert "sensitive-value" not in json.dumps(line)


def test_sensitive_nested_fields_are_redacted_case_insensitively() -> None:
    line = _format(_record("event", details={"user": {"Email": "a@b.c"}, "count": 3}))

    assert line["details"] == {"user": {"Email": REDACTED}, "count": 3}


class _UserModel(BaseModel):
    email: str
    name: str


@dataclass
class _UserRecord:
    email: str
    name: str


def test_pydantic_models_in_extra_are_redacted() -> None:
    user = _UserModel(email="user@example.com", name="Ada")
    line = _format(_record("event", user=user))

    assert line["user"] == {"email": REDACTED, "name": "Ada"}
    assert "user@example.com" not in json.dumps(line)


def test_dataclasses_in_extra_are_redacted() -> None:
    user = _UserRecord(email="user@example.com", name="Ada")
    line = _format(_record("event", user=user))

    assert line["user"] == {"email": REDACTED, "name": "Ada"}
    assert "user@example.com" not in json.dumps(line)


def test_exception_messages_and_chained_causes_are_never_logged() -> None:
    cause_message = "token=abc123"
    outer_message = "password=hunter2"
    try:
        try:
            raise ValueError(cause_message)
        except ValueError as cause:
            raise RuntimeError(outer_message) from cause
    except RuntimeError:
        record = _record("unhandled_error")
        record.exc_info = sys.exc_info()
        line_text = JsonFormatter(service="nettriage-api", stage="local").format(record)

    assert "hunter2" not in line_text
    assert "abc123" not in line_text
    line = json.loads(line_text)
    exception = line["exception"]
    assert exception["type"] == "RuntimeError"
    assert any(
        frame["function"] == "test_exception_messages_and_chained_causes_are_never_logged"
        for frame in exception["stack"]
    )
    [cause_entry] = exception["causes"]
    assert cause_entry["type"] == "ValueError"
    assert cause_entry["stack"]


def test_configure_logging_routes_uvicorns_loggers_through_the_json_formatter(
    capsys: pytest.CaptureFixture[str],
) -> None:
    uvicorn_logger_names = ["uvicorn", "uvicorn.error", "uvicorn.access"]
    root = logging.getLogger()
    uvicorn_loggers = [logging.getLogger(name) for name in uvicorn_logger_names]
    saved_root = (list(root.handlers), root.level)
    saved_uvicorn = [(list(lg.handlers), lg.propagate, lg.level) for lg in uvicorn_loggers]

    try:
        logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)  # simulate uvicorn starting

        configure_logging(Settings(stage="local", version="t"))
        logging.getLogger("uvicorn.error").info("Started server process")

        line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert line["message"] == "Started server process"
        assert line["service"] == "nettriage-api"
    finally:
        root.handlers = saved_root[0]
        root.level = saved_root[1]
        for lg, (handlers, propagate, lvl) in zip(uvicorn_loggers, saved_uvicorn, strict=True):
            lg.handlers = handlers
            lg.propagate = propagate
            lg.level = lvl
