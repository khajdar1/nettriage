import logging
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nettriage.entrypoints.api.app import create_app
from nettriage.platform.config import Settings
from nettriage.platform.logging import JsonFormatter

PROBLEM_JSON = "application/problem+json"


def test_unknown_route_returns_problem_details(client: TestClient) -> None:
    response = client.get("/api/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON
    body = response.json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Not Found"
    assert body["status"] == 404
    assert body["instance"] == "/api/does-not-exist"
    assert "trace_id" in body


def test_wrong_method_returns_problem_details_with_allow_header(client: TestClient) -> None:
    response = client.post("/api/health")

    assert response.status_code == 405
    assert response.headers["content-type"] == PROBLEM_JSON
    assert "GET" in response.headers["allow"]
    assert response.json()["title"] == "Method Not Allowed"


def test_unhandled_error_hides_internals(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    app: FastAPI = create_app(settings)

    @app.get("/api/boom")
    def boom() -> None:
        message = "database password is hunter2"
        raise RuntimeError(message)

    logger = logging.getLogger("nettriage.platform.errors")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(settings.service_name, settings.stage))
    logger.addHandler(handler)
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/boom")
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 500
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["title"] == "Internal Server Error"
    assert "hunter2" not in response.text
    assert "Traceback" not in response.text

    log_output = capsys.readouterr().out
    assert "hunter2" not in log_output
    assert "RuntimeError" in log_output


def test_request_validation_errors_are_problem_details(settings: Settings) -> None:
    app: FastAPI = create_app(settings)

    @app.get("/api/test-validation")
    def typed_route(n: int) -> dict[str, int]:
        return {"n": n}

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/test-validation", params={"n": "not-a-number-secret"})

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    body = response.json()
    assert body["status"] == 422
    assert body["errors"][0]["loc"][-1] == "n"
    assert "not-a-number-secret" not in response.text


def test_api_docs_are_disabled_in_prod() -> None:
    client = TestClient(create_app(Settings(stage="prod", version="1")))

    assert client.get("/api/docs").status_code == 404
    assert client.get("/api/openapi.json").status_code == 404
