"""The ops job's probe and hourly check (Plan 7a §2.1, §2.2): each says only whether the thing
answered, so nothing they report can carry data."""

from __future__ import annotations

import json

import httpx
import pytest
from botocore.exceptions import ClientError
from conftest import Database, RuntimeTable

from nettriage.adapters.postgres import create_database_engine
from nettriage.adapters.probes import database_answers, dynamodb_answers, health_answers

APP = "https://d1234.cloudfront.net"


def http(
    reply: httpx.Response | Exception, seen: list[httpx.Request] | None = None
) -> httpx.Client:
    def respond(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if isinstance(reply, Exception):
            raise reply
        return reply

    return httpx.Client(transport=httpx.MockTransport(respond))


def test_a_healthy_app_answers_ok_through_cloudfront() -> None:
    seen: list[httpx.Request] = []
    client = http(httpx.Response(200, json={"status": "ok", "version": "abc123"}), seen)

    assert health_answers(client, APP)
    assert [(request.method, str(request.url)) for request in seen] == [
        ("GET", f"{APP}/api/health")
    ]


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(503, json={"status": "ok"}),
        httpx.Response(200, content=b"<html>error</html>"),
        httpx.Response(200, content=json.dumps({"status": "starting"}).encode()),
        httpx.Response(200, json=["ok"]),
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
    ],
)
def test_anything_but_an_ok_answer_is_a_failed_probe(reply: httpx.Response | Exception) -> None:
    assert not health_answers(http(reply), APP)


def test_the_database_answers_as_the_ops_role(database: Database) -> None:
    assert database_answers(database.app_ops)


def test_a_database_that_refuses_is_a_failed_check() -> None:
    unreachable = create_database_engine("postgresql://app_ops:x@127.0.0.1:9/neondb", pool_size=1)

    assert not database_answers(unreachable)


def test_dynamodb_answers_even_without_the_item(runtime_table: RuntimeTable) -> None:
    assert dynamodb_answers(runtime_table.client, runtime_table.name)


def test_a_missing_table_is_a_failed_check(runtime_table: RuntimeTable) -> None:
    with pytest.raises(ClientError):
        runtime_table.client.describe_table(TableName="nettriage-test-gone")

    assert not dynamodb_answers(runtime_table.client, "nettriage-test-gone")
