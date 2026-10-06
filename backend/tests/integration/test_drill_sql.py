"""The restore drill's SQL against a real server (Plan 7a §2.5): setup statements run outside a
transaction, as CREATE DATABASE needs, and the restored tables are counted like the backup's."""

from __future__ import annotations

from uuid import uuid4

from conftest import Database
from psycopg.conninfo import make_conninfo
from sqlalchemy import text

from nettriage.adapters.pg_client import PsycopgSql


def conninfo(database: Database) -> str:
    url = database.admin.url
    return make_conninfo(
        host=url.host or "",
        port=str(url.port or 5432),
        user=url.username or "",
        password=url.password or "",
        dbname=url.database or "",
    )


def test_the_restored_tables_are_counted_like_the_backup_counted_them(database: Database) -> None:
    counts = PsycopgSql().count_tables(conninfo(database))

    with database.admin.begin() as connection:
        organizations: int = connection.execute(
            text("SELECT count(*) FROM organizations")
        ).scalar_one()
    assert counts["organizations"] == organizations
    assert {"uploads", "findings", "audit_log", "alembic_version"} <= set(counts)


def test_setup_statements_run_outside_a_transaction(database: Database) -> None:
    name = f"drill_check_{uuid4().hex[:8]}"

    PsycopgSql().execute(
        conninfo(database), [f'CREATE DATABASE "{name}"', f'DROP DATABASE "{name}"']
    )

    with database.admin.begin() as connection:
        left: int = connection.execute(
            text("SELECT count(*) FROM pg_database WHERE datname = :name"), {"name": name}
        ).scalar_one()
    assert left == 0
