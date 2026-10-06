"""The Neon database: the owner's connection string (stored once by the owner), migrations, and
a login for each function's role (spec §5.4, §5.8, §6.8). Connection strings and passwords are
never printed or put on a command line: they travel in the environment or to SSM, and every
error is redacted."""

from __future__ import annotations

import secrets
import sys
from collections.abc import Callable, Mapping
from urllib.parse import quote, urlsplit, urlunsplit

from tools.deploy import config
from tools.deploy import secrets as ssm
from tools.deploy.runner import CommandError, Runner

MIGRATION_URL_ENV = "NETTRIAGE_MIGRATION_DATABASE_URL"

type SetPassword = Callable[[str, str, str], None]  # (owner URL, role, password)


def check_owner_url(url: str) -> str:
    """The owner's connection string as copied from the Neon console: the direct (unpooled)
    endpoint, in Frankfurt. Errors describe the problem without repeating the string."""
    url = url.strip()
    parts = urlsplit(url)
    host = parts.hostname or ""
    if (
        parts.scheme not in ("postgresql", "postgres")
        or not parts.username
        or not parts.password
        or not parts.path.strip("/")
    ):
        raise CommandError(
            "That isn't a Postgres connection string (postgresql://user:password@host/database)."
        )
    if not host.endswith(config.NEON_HOST_SUFFIX):
        raise CommandError(
            "The database must be a Neon project in AWS Europe Central 1 (Frankfurt): "
            f"its host ends with {config.NEON_HOST_SUFFIX}."
        )
    if host.split(".")[0].endswith("-pooler"):
        raise CommandError(
            "Use the direct connection string: turn off 'Connection pooling' in the Neon console."
        )
    return url


def pooled_url(owner_url: str, role: str, password: str) -> str:
    """The connection string a function uses: its role, through Neon's pooler, verifying TLS."""
    parts = urlsplit(owner_url)
    endpoint, _, domain = (parts.hostname or "").partition(".")
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{quote(role, safe='')}:{quote(password, safe='')}@{endpoint}-pooler.{domain}{port}"
    return urlunsplit(("postgresql", netloc, parts.path, "sslmode=verify-full", ""))


def direct_url(owner_url: str, role: str, password: str) -> str:
    """The connection string for a role that needs a session (`app_backup`'s pg_dump): its
    role, on the owner's direct endpoint, verifying TLS."""
    parts = urlsplit(owner_url)
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{quote(role, safe='')}:{quote(password, safe='')}@{parts.hostname}{port}"
    return urlunsplit(("postgresql", netloc, parts.path, "sslmode=verify-full", ""))


def migrate(run: Runner, env: Mapping[str, str], owner_url: str) -> None:
    """`alembic upgrade head` from this checkout, as the database owner."""
    try:
        run(
            [sys.executable, "-m", "alembic", "-c", str(config.MIGRATIONS_CONFIG),
             "upgrade", "head"],
            env={**env, MIGRATION_URL_ENV: owner_url},
            redact=[owner_url, urlsplit(owner_url).password or owner_url],
        )
    except CommandError as exc:
        reason = str(exc).partition(" failed with ")[2] or str(exc)  # drop the interpreter path
        raise CommandError(
            f"Database migrations failed; nothing was deployed. Alembic stopped with {reason}"
        ) from exc


def sync_reference_data(run: Runner, env: Mapping[str, str], owner_url: str) -> str:
    """Upsert the detectors and ATT&CK techniques from this checkout (spec §5.2), as the
    database owner, right after migrating. Returns its one-line summary."""
    try:
        result = run(
            [sys.executable, "-m", "nettriage.adapters.reference_data"],
            env={**env, MIGRATION_URL_ENV: owner_url},
            redact=[owner_url, urlsplit(owner_url).password or owner_url],
        )
    except CommandError as exc:
        reason = str(exc).partition(" failed with ")[2] or str(exc)
        raise CommandError(
            f"Syncing reference data failed; nothing in AWS changed. It stopped with {reason}"
        ) from exc
    return result.stdout.strip()


def set_role_password(owner_url: str, role: str, password: str) -> None:
    from nettriage.adapters.postgres import set_login_password  # the backend's own client

    set_login_password(owner_url, role, password)


def ensure_role_logins(
    run: Runner,
    env: Mapping[str, str],
    stage: str,
    owner_url: str,
    set_password: SetPassword | None = None,
) -> list[str]:
    """Give each function's role a login the first time: a new random password, stored as the
    role's pooled connection string in SSM. Existing logins are kept. To rotate one, delete its
    parameter and deploy again. Returns the roles that got a new login."""
    created: list[str] = []
    for role in config.APP_DB_ROLES:
        name = config.db_role_url_parameter(stage, role)
        if ssm.parameter_exists(run, env, name):
            continue
        password = secrets.token_urlsafe(32)
        try:
            (set_password or set_role_password)(owner_url, role, password)
        except Exception as exc:  # psycopg's errors can quote the connection details
            raise CommandError(
                f"Couldn't give the database role {role} a login ({type(exc).__name__}). "
                "Check the connection string with: just store-database-url " + stage
            ) from None
        url = direct_url if role in config.DIRECT_DB_ROLES else pooled_url
        ssm.store_parameter(run, env, name, url(owner_url, role, password))
        created.append(role)
    return created
