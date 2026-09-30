"""Database errors never carry the values a query was given (spec §9.3): they reach logs and
traces, and a value can be a line of an uploaded file."""

import pytest
from conftest import Database
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def test_a_database_error_never_quotes_the_values_it_was_given(database: Database) -> None:
    private_line = "ACCOUNT-SECRET-7f3a not a flow record"

    with pytest.raises(DBAPIError) as failed, database.app_api.connect() as connection:
        connection.execute(text("SELECT CAST(:line AS text), 1 / 0"), {"line": private_line})

    assert "division by zero" in str(failed.value)
    assert private_line not in str(failed.value)
