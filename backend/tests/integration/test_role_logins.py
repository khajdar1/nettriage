from conftest import APP_API_PASSWORD, Database
from sqlalchemy import text

from nettriage.adapters.postgres import create_database_engine, set_login_password


def test_the_owner_gives_a_role_a_working_login(database: Database) -> None:
    owner_url = database.url.render_as_string(hide_password=False)
    password = "generated-with-a-quote-'-and-a-backslash-\\"  # noqa: S105 - test only

    set_login_password(owner_url, "app_api", password)
    try:
        login = database.url.set(username="app_api", password=password)
        engine = create_database_engine(login.render_as_string(hide_password=False), pool_size=1)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT current_user")).scalar_one() == "app_api"
        engine.dispose()
    finally:
        set_login_password(owner_url, "app_api", APP_API_PASSWORD)
