"""Postgres access (spec §5.3, §6.8): verified TLS to Neon, no prepared statements (Neon's
transaction-mode pooler can't keep them), and every transaction scoped to one tenant so
row-level security applies."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import certifi
import psycopg
from psycopg import sql
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import URL, make_url

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
CONNECT_TIMEOUT_SECONDS = 10


def engine_url(url: str) -> URL:
    """`postgresql://…` with the psycopg 3 driver."""
    return make_url(url).set(drivername="postgresql+psycopg")


def connect_args(url: URL) -> dict[str, Any]:
    """Remote hosts always use `sslmode=verify-full` against certifi's CA bundle, whatever the URL
    says; only a local test server may skip TLS. A connection attempt gives up after 10 seconds,
    well inside the API's 29-second timeout, instead of psycopg's default of about two minutes."""
    args: dict[str, Any] = {"prepare_threshold": None, "connect_timeout": CONNECT_TIMEOUT_SECONDS}
    if url.host not in LOCAL_HOSTS:
        args |= {"sslmode": "verify-full", "sslrootcert": certifi.where()}
    return args


def create_database_engine(url: str, *, pool_size: int = 2) -> Engine:
    parsed = engine_url(url)
    return create_engine(
        parsed,
        connect_args=connect_args(parsed),
        pool_size=pool_size,
        max_overflow=0,
        pool_pre_ping=True,
        # Errors reach logs and traces; the values a query was given, such as a sample of an
        # uploaded line, must not (spec §9.3).
        hide_parameters=True,
    )


@contextmanager
def tenant_transaction(
    engine: Engine, *, org_id: UUID | None = None, user_id: UUID | None = None
) -> Iterator[Connection]:
    """One transaction with `app.org_id` and `app.user_id` set for row-level security. Both are
    always set, to '' when absent, so a pooled connection never carries a previous tenant's
    values; the policies read '' as NULL, which matches no rows."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "SELECT set_config('app.org_id', :org_id, true), "
                "set_config('app.user_id', :user_id, true)"
            ),
            {"org_id": str(org_id) if org_id else "", "user_id": str(user_id) if user_id else ""},
        )
        yield connection


def set_login_password(owner_url: str, role: str, password: str) -> None:
    """Let `role` log in with `password`. Runs as the database owner; the deploy uses it to give
    each function's role a generated password (spec §6.8). The password is quoted into the
    statement client-side, because ALTER ROLE takes no bind parameters."""
    url = engine_url(owner_url)
    conninfo = url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(conninfo, **connect_args(url)) as connection:
        connection.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
