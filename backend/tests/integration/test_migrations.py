import os
import subprocess
import sys

from alembic import command
from conftest import BACKEND, alembic_config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

TABLES = {"users", "organizations", "memberships", "invitations", "audit_log", "uploads"}


def tables(url: URL) -> set[str]:
    engine = create_engine(url)
    with engine.connect() as connection:
        names: set[str] = set(
            connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).scalars()
        )
    engine.dispose()
    return names - {"alembic_version"}


def test_every_migration_applies_rolls_back_and_applies_again(empty_database: URL) -> None:
    config = alembic_config(empty_database)

    command.upgrade(config, "head")
    assert tables(empty_database) == TABLES

    command.downgrade(config, "base")
    assert tables(empty_database) == set()

    command.upgrade(config, "head")
    assert tables(empty_database) == TABLES


def test_a_failed_migration_ends_with_one_readable_error_line() -> None:
    """The deploy keeps the first `Error:` line of stderr (tools/deploy/runner.py), so env.py
    must print the cause there, not just SQLAlchemy's link line."""
    unreachable = "postgresql://nobody:not-a-real-password@db.nettriage.invalid/none"  # RFC 2606
    result = subprocess.run(  # noqa: S603 - fixed arguments, no shell
        [sys.executable, "-m", "alembic", "-c", str(BACKEND / "alembic.ini"), "upgrade", "head"],
        env={**os.environ, "NETTRIAGE_MIGRATION_DATABASE_URL": unreachable},
        capture_output=True,
        text=True,
        check=False,
    )

    errors = [line for line in result.stderr.splitlines() if line.startswith("Error: ")]
    assert result.returncode == 1
    assert errors, result.stderr
    assert errors[0].startswith("Error: OperationalError: ")
    assert "not-a-real-password" not in result.stderr
