from alembic import command
from conftest import alembic_config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

TABLES = {"users", "organizations", "memberships", "invitations", "audit_log"}


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
