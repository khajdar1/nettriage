"""Sign-in never writes a secret or an email to the logs (spec §9.3), even when it fails."""

import io
import logging
from collections.abc import Iterator
from uuid import uuid4

import pytest
from browser import csrf_headers, finish_sign_in, start_sign_in
from fake_idp import FakeIdentityProvider
from fastapi.testclient import TestClient

from nettriage.application.sessions import COOKIE_NAME
from nettriage.platform.config import Settings
from nettriage.platform.logging import QUIET_LOGGERS, configure_logging


@pytest.fixture
def logs() -> Iterator[io.StringIO]:
    """The production logging setup at DEBUG, the most a stage could ever log, written to a
    buffer instead of stdout."""
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    quiet = {name: logging.getLogger(name).level for name in QUIET_LOGGERS}
    configure_logging(Settings(), level=logging.DEBUG)
    buffer = io.StringIO()
    handler = root.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    handler.setStream(buffer)
    yield buffer
    root.handlers, root.level = saved[0], saved[1]
    for name, level in quiet.items():
        logging.getLogger(name).setLevel(level)


def test_a_sign_in_and_out_log_no_secret_and_no_email(
    database_client: TestClient,
    idp: FakeIdentityProvider,
    logs: io.StringIO,
) -> None:
    email = f"{uuid4().hex}@example.com"
    login = start_sign_in(database_client)
    finish_sign_in(database_client, idp, login, sub=f"sub-{uuid4()}", email=email)
    session_id = database_client.cookies[COOKIE_NAME]
    headers = csrf_headers(database_client)
    failed = start_sign_in(database_client)
    database_client.get(
        "/api/auth/callback",
        params={"code": "leaked-code", "state": failed["state"]},
        follow_redirects=False,
    )
    database_client.post("/api/auth/logout", headers=headers)

    lines = logs.getvalue().splitlines()
    secrets = [
        email,
        session_id,
        headers["X-CSRF-Token"],
        login["state"],
        login["nonce"],
        failed["state"],
        "leaked-code",
    ]
    assert any('"sign_in_failed"' in line for line in lines)
    leaked = [line for line in lines if any(secret in line for secret in secrets)]
    assert leaked == []
