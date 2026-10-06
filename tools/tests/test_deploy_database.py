import sys
from urllib.parse import unquote, urlsplit

import pytest

from tools.deploy import config, database
from tools.deploy.runner import CommandError
from tools.tests.deploy_fakes import OWNER_URL, FakeRun, ssm_names

APP_API_URL_PARAMETER = "/nettriage/dev/db/app-api-url"


def test_the_consoles_direct_frankfurt_connection_string_is_accepted() -> None:
    assert database.check_owner_url(f"  {OWNER_URL}\n") == OWNER_URL


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("not a url", "isn't a Postgres connection string"),
        ("postgresql://neondb_owner@ep-a.eu-central-1.aws.neon.tech/neondb", "isn't a Postgres"),
        ("mysql://u:owner-s3cret@ep-a.eu-central-1.aws.neon.tech/db", "isn't a Postgres"),
        ("postgresql://u:owner-s3cret@ep-a.us-east-2.aws.neon.tech/neondb", "Frankfurt"),
        ("postgresql://u:owner-s3cret@db.example.com/neondb", "Frankfurt"),
        ("postgresql://u:owner-s3cret@ep-a-pooler.eu-central-1.aws.neon.tech/neondb", "direct"),
    ],
)
def test_other_connection_strings_are_refused_without_being_repeated(url: str, reason: str) -> None:
    with pytest.raises(CommandError, match=reason) as caught:
        database.check_owner_url(url)

    assert "s3cret" not in str(caught.value)


def test_a_role_connects_through_the_pooler_with_its_escaped_password_and_verified_tls() -> None:
    url = database.pooled_url(OWNER_URL, "app_api", "p/w+1")

    assert url == (
        "postgresql://app_api:p%2Fw%2B1@ep-quiet-sun-123456-pooler.eu-central-1.aws.neon.tech/"
        "neondb?sslmode=verify-full"
    )


def test_migrations_get_the_owner_url_only_through_the_environment() -> None:
    run = FakeRun().on(sys.executable)

    database.migrate(run, {"AWS_PROFILE": "nettriage-tools"}, OWNER_URL)

    [call] = run.calls
    assert call.args == [
        sys.executable, "-m", "alembic", "-c", str(config.MIGRATIONS_CONFIG), "upgrade", "head",
    ]
    assert call.env == {
        "AWS_PROFILE": "nettriage-tools",
        "NETTRIAGE_MIGRATION_DATABASE_URL": OWNER_URL,
    }
    assert set(call.redact) == {OWNER_URL, "owner-s3cret"}


def test_a_role_without_a_login_gets_a_new_password_stored_as_its_pooled_url() -> None:
    run = FakeRun().on("aws", "ssm", "describe-parameters", returns=ssm_names(missing=[APP_API_URL_PARAMETER]))
    run.on("aws", "ssm", "put-parameter")
    given: list[tuple[str, str, str]] = []

    created = database.ensure_role_logins(
        run, {}, "dev", OWNER_URL, set_password=lambda *args: given.append(args)
    )

    assert created == ["app_api"]
    [(owner_url, role, password)] = given
    assert (owner_url, role) == (OWNER_URL, "app_api")
    assert len(password) >= 43
    [put] = run.called("aws", "ssm", "put-parameter")
    stored = put.args[put.args.index("--value") + 1]
    assert put.args[put.args.index("--name") + 1] == APP_API_URL_PARAMETER
    assert unquote(urlsplit(stored).password or "") == password
    assert put.redact == (stored,)


def test_the_analyze_worker_gets_its_own_login() -> None:
    missing = "/nettriage/dev/db/app-analyze-url"
    run = FakeRun().on("aws", "ssm", "describe-parameters", returns=ssm_names(missing=[missing]))
    run.on("aws", "ssm", "put-parameter")

    created = database.ensure_role_logins(run, {}, "dev", OWNER_URL, set_password=lambda *a: None)

    assert created == ["app_analyze"]
    [put] = run.called("aws", "ssm", "put-parameter")
    assert put.args[put.args.index("--name") + 1] == missing
    assert urlsplit(put.args[put.args.index("--value") + 1]).username == "app_analyze"


def test_an_existing_login_is_kept() -> None:
    run = FakeRun().on("aws", "ssm", "describe-parameters", returns=ssm_names())

    created = database.ensure_role_logins(
        run, {}, "dev", OWNER_URL, set_password=lambda *args: pytest.fail("password changed")
    )

    assert created == []
    assert run.called("aws", "ssm", "put-parameter") == []


def test_a_database_error_names_the_role_but_not_the_connection_details() -> None:
    run = FakeRun().on("aws", "ssm", "describe-parameters", returns=ssm_names(missing=[APP_API_URL_PARAMETER]))

    def refuse(owner_url: str, role: str, password: str) -> None:
        raise RuntimeError(f"connection to {owner_url} failed")

    with pytest.raises(CommandError, match="app_api") as caught:
        database.ensure_role_logins(run, {}, "dev", OWNER_URL, set_password=refuse)

    assert "s3cret" not in str(caught.value)
    assert run.called("aws", "ssm", "put-parameter") == []


def test_a_failed_migration_names_its_cause_without_the_interpreter_path() -> None:
    failure = CommandError(
        r"`C:\dev\nettriage\backend\.venv\Scripts\python.exe -m` failed with exit code 1: "
        'Error: ProgrammingError: relation "users" already exists'
    )
    run = FakeRun().on(sys.executable, returns=failure)

    with pytest.raises(CommandError) as caught:
        database.migrate(run, {}, OWNER_URL)

    assert str(caught.value) == (
        "Database migrations failed; nothing was deployed. Alembic stopped with exit code 1: "
        'Error: ProgrammingError: relation "users" already exists'
    )


def test_the_triage_worker_gets_its_own_login() -> None:
    missing = "/nettriage/dev/db/app-triage-url"
    run = FakeRun().on("aws", "ssm", "describe-parameters", returns=ssm_names(missing=[missing]))
    run.on("aws", "ssm", "put-parameter")

    created = database.ensure_role_logins(run, {}, "dev", OWNER_URL, set_password=lambda *a: None)

    assert created == ["app_triage"]
    [put] = run.called("aws", "ssm", "put-parameter")
    assert put.args[put.args.index("--name") + 1] == missing
    assert urlsplit(put.args[put.args.index("--value") + 1]).username == "app_triage"


def test_the_ops_job_gets_its_own_login_through_the_pooler() -> None:
    missing = "/nettriage/dev/db/app-ops-url"
    run = FakeRun().on("aws", "ssm", "describe-parameters", returns=ssm_names(missing=[missing]))
    run.on("aws", "ssm", "put-parameter")

    created = database.ensure_role_logins(run, {}, "dev", OWNER_URL, set_password=lambda *a: None)

    assert created == ["app_ops"]
    [put] = run.called("aws", "ssm", "put-parameter")
    stored = urlsplit(put.args[put.args.index("--value") + 1])
    assert put.args[put.args.index("--name") + 1] == missing
    assert stored.username == "app_ops"
    assert stored.hostname == "ep-quiet-sun-123456-pooler.eu-central-1.aws.neon.tech"


def test_the_backup_role_connects_directly_because_pg_dump_needs_a_session() -> None:
    missing = "/nettriage/dev/db/app-backup-url"
    run = FakeRun().on("aws", "ssm", "describe-parameters", returns=ssm_names(missing=[missing]))
    run.on("aws", "ssm", "put-parameter")
    given: list[tuple[str, str, str]] = []

    created = database.ensure_role_logins(
        run, {}, "dev", OWNER_URL, set_password=lambda *args: given.append(args)
    )

    assert created == ["app_backup"]
    [(_, _, password)] = given
    [put] = run.called("aws", "ssm", "put-parameter")
    assert put.args[put.args.index("--name") + 1] == missing
    assert put.args[put.args.index("--value") + 1] == database.direct_url(
        OWNER_URL, "app_backup", password
    )


def test_a_direct_connection_keeps_the_owners_endpoint_with_an_escaped_password_and_verified_tls() -> None:
    url = database.direct_url(OWNER_URL, "app_backup", "p/w+1")

    assert url == (
        "postgresql://app_backup:p%2Fw%2B1@ep-quiet-sun-123456.eu-central-1.aws.neon.tech/"
        "neondb?sslmode=verify-full"
    )
