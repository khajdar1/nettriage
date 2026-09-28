"""100 parallel requests for one key: exactly the allowed number pass (spec §11.4).

This needs a real DynamoDB, whose conditional writes are atomic; moto's aren't. CI runs DynamoDB
Local as a service container and sets NETTRIAGE_TEST_DYNAMODB_URL. Without it the test is skipped:
this machine can't run DynamoDB Local (no Docker, no Java).
"""

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import boto3
import pytest
from botocore.config import Config
from conftest import FakeClock, RuntimeTable, create_runtime_table

from nettriage.adapters.rate_limiter import RateLimiter
from nettriage.application.rate_limits import Policy

DYNAMODB_ENV = "NETTRIAGE_TEST_DYNAMODB_URL"
POLICY = Policy("concurrency", 5, timedelta(hours=1), 5)


@pytest.fixture
def dynamodb_local() -> Iterator[RuntimeTable]:
    endpoint = os.environ.get(DYNAMODB_ENV)
    if not endpoint:
        pytest.skip(f"{DYNAMODB_ENV} isn't set; CI runs this against DynamoDB Local")
    client = boto3.client(
        "dynamodb",
        endpoint_url=endpoint,
        region_name="eu-north-1",
        aws_access_key_id="local",  # DynamoDB Local accepts any credentials
        aws_secret_access_key="local",  # noqa: S106
        config=Config(max_pool_connections=100),
    )
    name = f"runtime-{uuid4().hex[:8]}"
    create_runtime_table(client, name)
    yield RuntimeTable(client=client, name=name)
    client.delete_table(TableName=name)


def test_exactly_the_burst_passes_when_100_requests_race(
    dynamodb_local: RuntimeTable, clock: FakeClock
) -> None:
    limiter = RateLimiter(dynamodb_local.client, dynamodb_local.name, clock)

    with ThreadPoolExecutor(max_workers=100) as pool:
        decisions = list(pool.map(lambda _: limiter.check(POLICY, "racer"), range(100)))

    assert not any(decision.degraded for decision in decisions)
    assert sum(decision.allowed for decision in decisions) == POLICY.burst
