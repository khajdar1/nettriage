"""The ops job's probe and hourly check (Plan 7a §2.1, §2.2). Each answers only whether the thing
answered; a failure is a False, never an exception or a message, so nothing reported can carry
data, and the next run, minutes away, tries again."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from types_boto3_dynamodb.client import DynamoDBClient

HEALTH_TIMEOUT_SECONDS = 10
# Any key: a missing item still proves the table answered.
PROBE_KEY = "ops#probe"


def health_answers(http: httpx.Client, app_url: str) -> bool:
    """`/api/health` through CloudFront answers 200 with `{"status": "ok"}`. It never touches the
    database (Plan 4a), so probing every 5 minutes doesn't keep Neon awake."""
    try:
        response = http.get(f"{app_url}/api/health", timeout=HEALTH_TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return False
    if response.status_code != httpx.codes.OK:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("status") == "ok"


def database_answers(engine: Engine) -> bool:
    """`SELECT 1`, within the engine's 10-second connect timeout (Neon wakes in seconds)."""
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT 1")).scalar_one() == 1
    except SQLAlchemyError:
        return False


def dynamodb_answers(client: DynamoDBClient, table: str) -> bool:
    try:
        client.get_item(TableName=table, Key={"pk": {"S": PROBE_KEY}})
    except ClientError, BotoCoreError:
        return False
    return True
