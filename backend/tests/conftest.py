import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL

from nettriage.adapters.postgres import create_database_engine, engine_url
from nettriage.entrypoints.api.app import create_app
from nettriage.platform.config import Settings

TEST_DATABASE_ENV = "NETTRIAGE_TEST_DATABASE_URL"
BACKEND = Path(__file__).resolve().parents[1]
APP_API_PASSWORD = "app-api-test-only"  # noqa: S105 - a throwaway password on a test server


@pytest.fixture
def settings() -> Settings:
    return Settings(stage="local", version="1.2.3-test")


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


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
