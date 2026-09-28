from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL


def test_each_test_database_starts_empty_and_is_dropped_afterwards(empty_database: URL) -> None:
    engine = create_engine(empty_database)
    with engine.connect() as connection:
        tables: int = connection.execute(
            text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
        ).scalar_one()
    engine.dispose()

    assert empty_database.database is not None
    assert empty_database.database.startswith("nettriage_test_")
    assert tables == 0
