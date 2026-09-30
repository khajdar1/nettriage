"""Building the analyze worker from SSM (spec §6.8): it gets the worker role's database URL and
the uploads bucket, never a value in its environment."""

from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from nettriage.adapters.parameters import MissingParameterError
from nettriage.entrypoints.analyze.wiring import build_worker
from nettriage.platform.config import Settings

SETTINGS = Settings(
    stage="dev",
    service_name="nettriage-analyze",
    database_url_parameter="/nettriage/dev/db/app-analyze-url",
    uploads_bucket="nettriage-dev-uploads-12345678",
)


@pytest.fixture
def session() -> Iterator[boto3.session.Session]:
    with mock_aws():
        yield boto3.session.Session(region_name="eu-north-1")


def test_the_worker_is_built_from_its_own_database_url(session: boto3.session.Session) -> None:
    session.client("ssm").put_parameter(
        Name="/nettriage/dev/db/app-analyze-url",
        Value="postgresql://app_analyze:pw@ep-x-pooler.eu-central-1.aws.neon.tech/neondb",
        Type="SecureString",
    )

    worker = build_worker(SETTINGS, TracerProvider(), MeterProvider(), session)

    assert worker.database.url.username == "app_analyze"
    assert worker.database.url.host == "ep-x-pooler.eu-central-1.aws.neon.tech"
    assert worker.database.pool.size() == 1  # type: ignore[attr-defined]
    worker.flush()


def test_a_missing_database_url_is_named(session: boto3.session.Session) -> None:
    with pytest.raises(MissingParameterError, match="/nettriage/dev/db/app-analyze-url"):
        build_worker(SETTINGS, TracerProvider(), MeterProvider(), session)
