import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from nettriage.adapters.postgres import engine_url
from nettriage.entrypoints.api.app import create_app
from nettriage.platform.config import Settings

TEST_DATABASE_ENV = "NETTRIAGE_TEST_DATABASE_URL"


@pytest.fixture
def settings() -> Settings:
    return Settings(stage="local", version="1.2.3-test")


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


# Database fixtures. Integration tests need a Postgres superuser URL in
# NETTRIAGE_TEST_DATABASE_URL. `just test` starts a local server and sets it; CI sets it for
# its Postgres service container. Each test that asks gets a throwaway database.


def server_url() -> URL:
    url = os.environ.get(TEST_DATABASE_ENV)
    if not url:
        pytest.fail(
            f"{TEST_DATABASE_ENV} isn't set. Run `just test`, which starts the local database, "
            "or run `just db-up` and set it to the URL in .localdb/url."
        )
    return engine_url(url)


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


@pytest.fixture
def empty_database() -> Iterator[URL]:
    """A database with no migrations applied, for migration tests."""
    server = server_url()
    url = create_database(server)
    yield url
    drop_database(server, url)
