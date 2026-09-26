import json
import logging
import logging.config

import pytest
import uvicorn.config
from opentelemetry.sdk.trace import TracerProvider

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
