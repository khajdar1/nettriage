"""Alembic entry point. There's no ORM metadata: every migration is explicit SQL (spec §5.8)."""

from __future__ import annotations

import os
import sys

from alembic import context

from nettriage.adapters.postgres import create_database_engine

URL_ENV = "NETTRIAGE_MIGRATION_DATABASE_URL"


def database_url() -> str:
    url = context.config.attributes.get("database_url") or os.environ.get(URL_ENV)
    if not url:
        raise RuntimeError(f"Set {URL_ENV} to the database owner's URL.")
    return str(url)


def run_migrations() -> None:
    if context.is_offline_mode():
        raise RuntimeError("Offline (SQL script) migrations aren't supported.")
    engine = create_database_engine(database_url(), pool_size=1)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, transaction_per_migration=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


def main() -> None:
    """Run the migrations. On failure, first print one `Error:` line naming the cause: the
    deploy shows only that line (tools/deploy/runner.py), and a SQLAlchemy error's last line
    is just a link to its docs."""
    try:
        run_migrations()
    except Exception as exc:
        cause = str(exc).strip().splitlines()[0] if str(exc).strip() else "no details"
        print(f"Error: {type(exc).__name__}: {cause}", file=sys.stderr)
        raise


main()
