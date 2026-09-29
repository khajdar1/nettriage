import io
import logging
import os
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import boto3
import httpx
import pytest
from alembic import command
from alembic.config import Config
from fake_idp import FakeIdentityProvider
from fake_idp import settings as idp_settings
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, NumberDataPoint
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL

from nettriage.adapters.idempotency import IdempotencyStore
from nettriage.adapters.kill_switch import KillSwitch
from nettriage.adapters.login_states import LoginStateStore
from nettriage.adapters.oidc import OidcClient
from nettriage.adapters.postgres import create_database_engine, engine_url
from nettriage.adapters.rate_limiter import RateLimiter
from nettriage.adapters.sessions import SessionStore
from nettriage.adapters.upload_storage import UploadStorage, uploads_client
from nettriage.entrypoints.api.app import create_app
from nettriage.entrypoints.api.services import Services
from nettriage.platform.config import Settings
from nettriage.platform.logging import QUIET_LOGGERS, configure_logging
from nettriage.platform.metrics import AppMetrics

if TYPE_CHECKING:
    from types_boto3_dynamodb.client import DynamoDBClient

TEST_DATABASE_ENV = "NETTRIAGE_TEST_DATABASE_URL"
BACKEND = Path(__file__).resolve().parents[1]
APP_API_PASSWORD = "app-api-test-only"  # noqa: S105 - a throwaway password on a test server


@pytest.fixture
def settings() -> Settings:
    return Settings(stage="local", version="1.2.3-test")


# Database fixtures. Integration tests need a Postgres superuser URL in
# NETTRIAGE_TEST_DATABASE_URL. `just test` starts a local server and sets it; CI sets it for
# its Postgres service container. Each session gets a throwaway database.


@dataclass(frozen=True)
class Database:
    """A database with every migration applied. `admin` is a superuser engine that seeds data
    past row-level security; `app_api` connects as the API's role."""

    url: URL
    admin: Engine
    app_api: Engine


def server_url() -> URL:
    url = os.environ.get(TEST_DATABASE_ENV)
    if not url:
        pytest.fail(
            f"{TEST_DATABASE_ENV} isn't set. Run `just test`, which starts the local database, "
            "or run `just db-up` and set it to the URL in .localdb/url."
        )
    return engine_url(url)


def alembic_config(url: URL) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["database_url"] = url.render_as_string(hide_password=False)
    return config


def create_database(server: URL) -> URL:
    name = f"nettriage_test_{uuid4().hex[:12]}"
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    return server.set(database=name)


def drop_database(server: URL, url: URL) -> None:
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE "{url.database}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(scope="session")
def database() -> Iterator[Database]:
    server = server_url()
    url = create_database(server)
    command.upgrade(alembic_config(url), "head")
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f"ALTER ROLE app_api WITH LOGIN PASSWORD '{APP_API_PASSWORD}'"))
    app_url = url.set(username="app_api", password=APP_API_PASSWORD)
    app_api = create_database_engine(app_url.render_as_string(hide_password=False), pool_size=1)
    yield Database(url=url, admin=admin, app_api=app_api)
    app_api.dispose()
    admin.dispose()
    drop_database(server, url)


@pytest.fixture
def empty_database() -> Iterator[URL]:
    """A database with no migrations applied, for migration tests."""
    server = server_url()
    url = create_database(server)
    yield url
    drop_database(server, url)


# The DynamoDB `runtime` table, mocked in-process by moto (spec §11.4). The concurrency test in
# tests/security uses a real DynamoDB Local instead, because moto's writes aren't atomic.

RUNTIME_TABLE = "nettriage-test-runtime"
REGION = "eu-north-1"


@dataclass(frozen=True)
class RuntimeTable:
    client: DynamoDBClient
    name: str


def create_runtime_table(client: DynamoDBClient, name: str) -> None:
    """The same key schema as infra/modules/data."""
    client.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def runtime_table() -> Iterator[RuntimeTable]:
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        create_runtime_table(client, RUNTIME_TABLE)
        yield RuntimeTable(client=client, name=RUNTIME_TABLE)


class CountingClient:
    """A boto3 client that counts calls per operation, to prove when DynamoDB isn't asked."""

    def __init__(self, client: DynamoDBClient) -> None:
        self._client = client
        self.calls: Counter[str] = Counter()

    def __getattr__(self, name: str) -> Any:
        method = getattr(self._client, name)

        def counted(*args: Any, **kwargs: Any) -> Any:
            self.calls[name] += 1
            return method(*args, **kwargs)

        return counted


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


# The API with everything it uses: moto for DynamoDB, a fake Cognito, and a database engine that
# never connects. `database_client` puts the test database behind the API instead.

APP_ORIGIN = "https://app.test"
NO_DATABASE = "postgresql://unused@db.nettriage.invalid/unused"  # fails fast if ever used
UPLOADS_BUCKET = "nettriage-test-uploads-00000000"


def presigning_session() -> boto3.session.Session:
    """Presigning is offline: dummy credentials sign URLs that nothing ever calls."""
    return boto3.session.Session(
        aws_access_key_id="testing",
        aws_secret_access_key="testing",  # noqa: S106 - not a secret
        region_name="eu-north-1",
    )


@pytest.fixture
def idp() -> FakeIdentityProvider:
    return FakeIdentityProvider()


@pytest.fixture
def metric_reader() -> InMemoryMetricReader:
    return InMemoryMetricReader()


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


@pytest.fixture
def services(
    runtime_table: RuntimeTable,
    clock: FakeClock,
    idp: FakeIdentityProvider,
    metric_reader: InMemoryMetricReader,
) -> Services:
    table, client = runtime_table.name, runtime_table.client
    return Services(
        database=create_database_engine(NO_DATABASE),
        sessions=SessionStore(client, table),
        login_states=LoginStateStore(client, table),
        rate_limiter=RateLimiter(client, table, clock),
        idempotency=IdempotencyStore(client, table),
        upload_storage=UploadStorage(uploads_client(presigning_session()), UPLOADS_BUCKET),
        uploads_switch=KillSwitch(lambda: "true", clock),
        oidc=OidcClient(idp_settings(), httpx.Client(transport=idp.transport()), clock),
        clock=clock,
        metrics=AppMetrics(MeterProvider(metric_readers=[metric_reader])),
    )


@pytest.fixture
def client(settings: Settings, services: Services) -> TestClient:
    return TestClient(create_app(settings, services), base_url=APP_ORIGIN)


@pytest.fixture
def database_client(settings: Settings, services: Services, database: Database) -> TestClient:
    """The API with the test database behind it, as `app_api`."""
    return TestClient(
        create_app(settings, replace(services, database=database.app_api)), base_url=APP_ORIGIN
    )


def counter(reader: InMemoryMetricReader, name: str) -> int:
    """The total of a counter across all its attributes."""
    data = reader.get_metrics_data()
    if data is None:
        return 0
    return sum(
        int(point.value)
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
        if isinstance(point, NumberDataPoint)
    )
