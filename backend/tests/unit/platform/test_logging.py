import json
import logging
import logging.config
import sys
import time
from dataclasses import dataclass

import pytest
import uvicorn.config
from opentelemetry.sdk.trace import TracerProvider
from pydantic import BaseModel

from nettriage.platform.config import Settings
from nettriage.platform.logging import (
    QUIET_LOGGERS,
    REDACTED,
    JsonFormatter,
    _sanitize_message,
    configure_logging,
)


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


def test_exception_stack_frames_never_include_the_source_line_text() -> None:
    try:
        raise ValueError("dsn=postgres://user:s3cr3t-literal-dsn@host/db")
    except ValueError:
        record = _record("unhandled_error")
        record.exc_info = sys.exc_info()
        line_text = JsonFormatter(service="nettriage-api", stage="local").format(record)

    assert "s3cr3t-literal-dsn" not in line_text
    line = json.loads(line_text)
    frames = line["exception"]["stack"]
    assert frames
    for frame in frames:
        assert "source" not in frame
        assert "file" in frame
        assert "line" in frame
        assert "function" in frame


def test_message_percent_args_with_a_sensitive_key_are_redacted() -> None:
    line = _format(_record("token=%s", args=("abc123",)))

    assert "abc123" not in json.dumps(line)
    assert line["message"] == "token=[REDACTED]"


def test_message_email_address_in_an_fstring_is_redacted() -> None:
    address = "user@example.com"
    line = _format(_record(f"welcome back {address}"))

    assert "user@example.com" not in json.dumps(line)


def test_message_email_with_subdomain_is_redacted_in_full() -> None:
    line = _format(_record("contact a.b@mail.example.com for help"))

    assert "a.b@mail.example.com" not in json.dumps(line)
    assert ".com" not in json.dumps(line)


def test_message_bearer_token_is_redacted() -> None:
    line = _format(_record("Authorization: Bearer xyz.abc"))

    assert "xyz.abc" not in json.dumps(line)


def test_uvicorn_access_log_query_string_is_redacted() -> None:
    line = _format(
        _record(
            '%s - "%s %s HTTP/%s" %d',
            args=("1.2.3.4:5", "GET", "/api/invitations/accept?code=s3cr3t", "1.1", 200),
            name="uvicorn.access",
        )
    )

    assert "s3cr3t" not in json.dumps(line)
    assert "/api/invitations/accept?[REDACTED]" in str(line["message"])
    assert "200" in str(line["message"])


def test_message_json_style_sensitive_pair_is_redacted() -> None:
    line = _format(_record('{"password": "hunter2"}'))

    assert "hunter2" not in json.dumps(line)


@pytest.mark.parametrize("event", ["unhandled_error", "Application startup complete."])
def test_plain_event_names_pass_through_message_sanitization_unchanged(event: str) -> None:
    line = _format(_record(event))

    assert line["message"] == event


_ADVERSARIAL_SANITIZE_INPUTS = [
    ("long_run_of_word_characters", "a" * 200_000),
    ("repeated_sensitive_key", "token" * 40_000),
    ("long_path_with_trailing_query_marker", "/" * 200_000 + "?"),
    ("sensitive_key_then_long_whitespace_run", "password" + " " * 200_000),
]


@pytest.mark.parametrize(
    ("label", "text"),
    _ADVERSARIAL_SANITIZE_INPUTS,
    ids=[label for label, _ in _ADVERSARIAL_SANITIZE_INPUTS],
)
def test_sanitize_message_is_linear_time_on_adversarial_input(label: str, text: str) -> None:
    # A regex whose match attempts can start at every position in a long run of the same kind of
    # character, and then scan forward from each one, is quadratic in the length of that run.
    # uvicorn's access log puts the request path (and query string) straight into the message, so
    # a client sending a long URL must not be able to stall every request handling it.
    start = time.perf_counter()
    _sanitize_message(text)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"{label}: sanitizing {len(text)} chars took {elapsed:.2f}s"


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


def test_http_and_aws_clients_only_log_warnings_even_at_debug() -> None:
    """botocore logs every DynamoDB item it writes at DEBUG, sessions included."""
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    quiet = {name: logging.getLogger(name).level for name in QUIET_LOGGERS}
    try:
        configure_logging(Settings(stage="local", version="t"), level=logging.DEBUG)

        levels = {name: logging.getLogger(name).getEffectiveLevel() for name in QUIET_LOGGERS}
        assert set(levels) >= {"botocore", "httpx"}
        assert set(levels.values()) == {logging.WARNING}
        assert logging.getLogger("nettriage").getEffectiveLevel() == logging.DEBUG
    finally:
        root.handlers, root.level = saved
        for name, level in quiet.items():
            logging.getLogger(name).setLevel(level)
