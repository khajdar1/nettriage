# NetTriage Plan 3a: Data Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give NetTriage its Postgres foundation on Neon:
- the identity schema (users, organizations, memberships, invitations, audit log);
- tenant isolation through row-level security, and a least-privilege role for the API;
- migrations that the owner's deploy runs;
- a local Postgres, so tests run against a real database on this machine without Docker.

**Architecture:** The schema lives in explicit-SQL Alembic migrations (`backend/migrations`).
- A small Postgres adapter (`nettriage.adapters.postgres`) creates engines. They verify TLS to Neon, skip prepared statements (for Neon's pooler), and run each transaction scoped to one tenant.
- The owner creates the Neon project in the Neon console once, and stores the owner's connection string in SSM.
- Every `just deploy-dev` then runs the migrations *before* Terraform ships new code. The first deploy also gives the `app_api` role a generated password, stored as its own SSM SecureString.

**Tech Stack:** Python 3.14, SQLAlchemy 2 (Core), psycopg 3, Alembic, pytest · Neon Postgres 17 (Frankfurt) · local Postgres 16 via `pgserver`, and a Postgres 17 service container in CI · AWS SSM Parameter Store.

**Spec:** `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (revision 2). Read it alongside this plan; section numbers (§) refer to it. This plan implements:
- §5.1 (the Neon store);
- §5.2 (the identity tables and the audit log);
- §5.3 (tenant isolation in three layers);
- §5.4 (roles and grants, for `app_api`);
- §5.8 (migrations);
- §6.8 (Neon connection rules and passwords in SSM);
- §11.4's RLS isolation test.

**Plan series:** Plan 3 of 7 ("data, identity and access") is split in two, as Plan 1 was:
- **3a (this plan): the data foundation;**
- **3b: identity and access.** That covers Cognito sign-in with MFA, sessions, CSRF, the permission matrix, rate limiting, and the org, member and invitation APIs. It builds on these tables.

Uploads, findings and AI tables come with Plans 4 and 5.

**Branch:** `plan-3a/data-foundation`, from `main` at `0c31d01` or later.

## Global Constraints

- **Stack.** Python **3.14**. New backend dependencies: `sqlalchemy` 2 (Core only, no ORM), `psycopg[binary]` 3, `alembic` and `certifi`. mypy `--strict` and Ruff pass on `src` and `tests`. Work test-first.
- **Commands on this Windows machine.** Run Python tools as modules (`uv run python -m …`, `uv run --project backend python -m …`). Host policy blocks some uv launchers and every unsigned executable outside trusted tools.
- **Neon (§3.2, §6.8, Revision 2 D2).**
  - Region `aws-eu-central-1` (Frankfurt), Postgres **17**, one project per stage.
  - Functions connect through the **pooled** endpoint, and migrations through the **direct** endpoint.
  - Every non-local connection uses `sslmode=verify-full` against certifi's CA bundle.
  - psycopg's prepared statements are off (`prepare_threshold=None`).
- **Schema conventions (§5.2).**
  - UUIDv7 primary keys, generated in the application (`uuid.uuid7()`).
  - `timestamptz` columns.
  - Allowed values as `text` + `CHECK`.
  - `created_at` on every table, and `updated_at` (kept by a trigger) where rows change.
  - Tenant tables cascade from `organizations`; `audit_log` has no foreign key.
- **Tenant isolation (§5.3).** Every tenant table has `ENABLE` and `FORCE ROW LEVEL SECURITY`.
  - The policies compare with `app.org_id`, and for memberships also `app.user_id`. Both are set per transaction with `set_config(…, true)`.
  - An unset or empty setting matches no rows.
  - A dedicated test queries with no org filter and sees no other tenant's rows.
- **Roles (§5.4).**
  - `app_api` gets exactly the SELECT, INSERT, DELETE and column-level UPDATE grants the API needs, and only INSERT and SELECT on `audit_log`.
  - It isn't a superuser, doesn't have `BYPASSRLS`, and doesn't own any table.
  - `audit_log` rejects UPDATE and DELETE through a trigger.
- **Secrets.**
  - The owner's connection string and each role's password live only in SSM SecureStrings: `/nettriage/<stage>/db/owner-url` and `/nettriage/<stage>/db/app-api-url`.
  - They are never printed and never written to disk.
  - They never appear in Git, GitHub, chat or error messages.
  - They appear on a command line only in the single `aws ssm put-parameter` call, which is redacted.
  - Migrations receive the URL through the environment.
- **CI still holds no cloud access (ADR 0013).** Its Postgres is a service container on the runner.
- **Owner-only commands.** Claude never runs `just store-database-url`, `aws login` or `just deploy-*`.
- **Commits.** Use Conventional Commits. Every commit made by Claude ends with the trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Decisions this plan makes

1. **Neon is created in its console, not by Terraform.**
   - Neon's Terraform provider (`kislerdm/neon`) isn't code-signed. The owner's Windows host blocks unsigned executables: `terraform validate` fails with "Access is denied", which was verified while writing this plan.
   - This is spec §13.2's documented fallback. ADR 0002 and §13.2 record it.
   - Tables, row-level security and grants stay in reviewed migrations.
2. **Migrations run from the owner's deploy, before Terraform.** Spec §5.8 says "CI runs migrations", but Revision 2 took all cloud access away from CI. So `just deploy-dev` migrates first, after every guard and preflight and before `terraform apply`. Migrations stay backward compatible (expand, migrate, contract).
3. **Role logins come from the deploy.**
   - The first deploy generates a 256-bit password for `app_api` and sets it with `ALTER ROLE … LOGIN PASSWORD`.
   - It stores the role's pooled URL in SSM, where the function will read it at cold start (Plan 3b).
   - To rotate a password, delete its SSM parameter and deploy again.
   - Only `app_api` exists now; Plans 4, 5 and 7 add their roles to `APP_DB_ROLES` and grant them in their own migrations.
4. **Local tests use `pgserver`.** It's a pip-installed Postgres 16, which runs here without Docker, WSL or admin rights. CI uses Postgres 17. `just dev`'s Docker Compose setup (§11.3) waits until a plan needs the full local stack.
5. **Row-level security details the spec leaves open.**
   - The settings are read through `NULLIF(current_setting(…, true), '')`. Once a pooled connection has held a transaction-local value, Postgres returns `''` rather than NULL.
   - `tenant_transaction` always sets both values, to `''` when absent.
   - An organization is visible when it's the transaction's org or one the user is a member of, for "my organizations"; writes go only to the transaction's org.
   - *Amended by the final review:* inside an org's transaction only that org is visible. The user's other orgs and memberships show only when no org is set, so "my organizations" runs in a user-only transaction. The policies are split per command (read, insert, update, delete), so UPDATE and DELETE never use the wider read rule.
   - `users` has no row-level security: the spec lists tenant tables only, and Plan 3b's repositories scope user reads.
   - `audit_log` rows are read per org, and may be written with no org (sign-in events).
6. **A pending invitation is one that's neither accepted nor revoked.** A unique index can't call `now()`, so expiry isn't part of the uniqueness rule. Plan 3b revokes an expired invitation before re-inviting the same email.
7. **The audit log is append-only for everyone, including the owner role.** Plan 7's retention function amends the trigger.

## Review Focus

1. **A pooled connection reused after another tenant's transaction.** It must see no rows, and raise no error. Test: Task 2 `test_a_pooled_connection_forgets_the_previous_tenant`.
2. **A query that forgets its org filter**, the classic multi-tenant bug. It must still return only its own org's rows. Test: Task 2 `test_a_query_without_an_org_filter_sees_only_its_own_org`.
3. **A connection string that is pooled, in another region, not Postgres, or missing its password.** It must be refused with a fix, and the string must never be repeated back. Test: Task 3 `test_other_connection_strings_are_refused_without_being_repeated`.
4. **A migration that fails, or a database URL that was never stored, during a deploy.** The deploy must stop before Terraform, and nothing in AWS may change. Tests: Task 3 `test_a_failed_migration_stops_the_deploy_before_terraform` and `test_deploy_stops_before_terraform_without_a_stored_database_url`.
5. **Database secrets in command lines, output or errors.** They must never appear. Tests: Task 3 `test_deploy_never_puts_the_database_owner_url_in_any_call_args`, `test_store_database_url_checks_and_stores_it_without_printing_it` and `test_a_database_error_names_the_role_but_not_the_connection_details`.

## Owner prerequisites

- **Before the first deploy of this plan:** runbook **A7**, "Create the Neon database (once)", which Task 4 adds. You create the Neon project and run `just store-database-url dev`. Task 5 walks through it.
- **Nothing is needed to build or review this plan.** Tests use the local Postgres, which starts by itself.

---

### Task 1: A local Postgres, database dependencies and the test harness

**Files:**
- Modify: `backend/pyproject.toml`, `backend/uv.lock` (via `uv add`), `justfile`, `.gitignore`, `.github/workflows/ci.yml`, `CLAUDE.md`, `README.md`
- Create: `tools/localdb.py`, `backend/src/nettriage/adapters/__init__.py`, `backend/src/nettriage/adapters/postgres.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/unit/adapters/test_postgres.py`, `backend/tests/integration/test_database_server.py`

**Interfaces:**
- Produces:
  - In `nettriage.adapters.postgres`:
    - `LOCAL_HOSTS`;
    - `engine_url(url: str) -> URL`, which uses the psycopg 3 driver;
    - `connect_args(url: URL) -> dict[str, Any]`, which gives `prepare_threshold=None`, and for non-local hosts `sslmode=verify-full` plus `sslrootcert=certifi.where()`;
    - `create_database_engine(url: str, *, pool_size: int = 2) -> Engine`.
  - In the test harness (`backend/tests/conftest.py`):
    - `TEST_DATABASE_ENV = "NETTRIAGE_TEST_DATABASE_URL"`;
    - `server_url() -> URL`, which fails the test with instructions when the variable is unset;
    - `create_database(server) -> URL` and `drop_database(server, url)`;
    - the `empty_database` fixture, which gives each test that asks a throwaway database.
  - `just db-up` and `just db-down`. `just test` now starts the local Postgres first.

- [ ] **Step 1: Add the dependencies and the local Postgres**

```bash
cd backend
uv add sqlalchemy "psycopg[binary]" alembic certifi
```

`tools/localdb.py`:
```python
"""A local Postgres for tests, without Docker: `pgserver` bundles the server binaries.

Run with its own Python (pgserver has no Python 3.14 wheels):
    uv run --no-project --python 3.12 --with pgserver==0.1.4 python tools/localdb.py up|down

`up` starts the server in `.localdb/` (git-ignored), leaves it running, and writes its URL to
`.localdb/url` for `just test`. `down` stops it. CI uses a Postgres 17 service container
instead, through NETTRIAGE_TEST_DATABASE_URL.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pgserver  # type: ignore[import-not-found]

HOME = Path(__file__).resolve().parents[1] / ".localdb"
DATA = HOME / "data"
URL_FILE = HOME / "url"


def main(argv: list[str]) -> int:
    command = argv[0] if argv else ""
    if command == "up":
        HOME.mkdir(exist_ok=True)
        server = pgserver.get_server(DATA, cleanup_mode=None)
        URL_FILE.write_text(server.get_uri(), encoding="utf-8")
        print(f"Local Postgres is running: {server.get_uri()}")
        return 0
    if command == "down":
        if not DATA.exists():
            print("Local Postgres isn't set up.")
            return 0
        pgserver.get_server(DATA, cleanup_mode="stop")
        URL_FILE.unlink(missing_ok=True)
        print("Local Postgres stopped.")
        return 0
    print("usage: localdb.py up|down", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

In `justfile`, replace the `test` recipe:
```
# Backend: tests
test:
    cd backend && uv run python -m pytest --cov=nettriage.domain --cov-report=term-missing --cov-fail-under=85
```
with:
```
# Backend: tests. Integration tests need Postgres, so this starts the local one first.
test: db-up
    cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest --cov=nettriage.domain --cov-report=term-missing --cov-fail-under=85

# Local Postgres for tests, without Docker (tools/localdb.py); it keeps running until db-down
db-up:
    uv run --no-project --python 3.12 --with pgserver==0.1.4 python tools/localdb.py up

db-down:
    uv run --no-project --python 3.12 --with pgserver==0.1.4 python tools/localdb.py down
```

In `.gitignore`, add `.localdb/` on its own line after `.hypothesis/`.

In `.github/workflows/ci.yml`, give the `backend` job a Postgres 17 service. Replace:
```yaml
  backend:
    runs-on: ubuntu-24.04
    steps:
```
with:
```yaml
  backend:
    runs-on: ubuntu-24.04
    services:
      postgres:
        image: postgres:17
        env:
          POSTGRES_PASSWORD: postgres
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U postgres"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10
    env:
      NETTRIAGE_TEST_DATABASE_URL: postgresql://postgres:postgres@localhost:5432/postgres
    steps:
```

Run: `just db-up`
Expected: `Local Postgres is running: postgresql://postgres:@127.0.0.1:<port>/postgres`. The first run downloads Python 3.12 and `pgserver` into uv's cache.

- [ ] **Step 2: Write the failing tests**

`backend/tests/unit/adapters/test_postgres.py`:
```python
import certifi

from nettriage.adapters.postgres import connect_args, engine_url


def test_urls_use_the_psycopg_3_driver() -> None:
    url = engine_url("postgresql://app_api:secret@ep-x.eu-central-1.aws.neon.tech/nettriage")

    assert url.drivername == "postgresql+psycopg"
    assert (url.username, url.host) == ("app_api", "ep-x.eu-central-1.aws.neon.tech")


def test_remote_hosts_always_verify_tls_and_skip_prepared_statements() -> None:
    url = engine_url(
        "postgresql://app_api:secret@ep-x.eu-central-1.aws.neon.tech/nettriage?sslmode=disable"
    )

    assert connect_args(url) == {
        "prepare_threshold": None,
        "sslmode": "verify-full",
        "sslrootcert": certifi.where(),
    }


def test_a_local_test_server_needs_no_tls() -> None:
    url = engine_url("postgresql://postgres:@127.0.0.1:55432/postgres")

    assert connect_args(url) == {"prepare_threshold": None}
```

`backend/tests/integration/test_database_server.py`:
```python
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
```

Replace `backend/tests/conftest.py` with:
```python
import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from nettriage.adapters.postgres import engine_url
from nettriage.entrypoints.api.app import create_app
from nettriage.platform.config import Settings

TEST_DATABASE_ENV = "NETTRIAGE_TEST_DATABASE_URL"


@pytest.fixture
def settings() -> Settings:
    return Settings(stage="local", version="1.2.3-test")


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


# Database fixtures. Integration tests need a Postgres superuser URL in
# NETTRIAGE_TEST_DATABASE_URL. `just test` starts a local server and sets it; CI sets it for
# its Postgres service container. Each test that asks gets a throwaway database.


def server_url() -> URL:
    url = os.environ.get(TEST_DATABASE_ENV)
    if not url:
        pytest.fail(
            f"{TEST_DATABASE_ENV} isn't set. Run `just test`, which starts the local database, "
            "or run `just db-up` and set it to the URL in .localdb/url."
        )
    return engine_url(url)


def create_database(server: URL) -> URL:
    name = f"nettriage_test_{uuid4().hex[:12]}"
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    return server.set(database=name)


def drop_database(server: URL, url: URL) -> None:
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE "{url.database}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def empty_database() -> Iterator[URL]:
    """A database with no migrations applied, for migration tests."""
    server = server_url()
    url = create_database(server)
    yield url
    drop_database(server, url)
```

- [ ] **Step 3: Run them to see them fail**

Run: `cd backend && uv run python -m pytest tests/unit/adapters -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'nettriage.adapters'`.

- [ ] **Step 4: Implement the adapter**

`backend/src/nettriage/adapters/__init__.py`:
```python
"""Adapters to the outside world: databases, queues, storage, model providers."""
```

`backend/src/nettriage/adapters/postgres.py`:
```python
"""Postgres access (spec §6.8): verified TLS to Neon, and no prepared statements (Neon's
transaction-mode pooler can't keep them)."""

from __future__ import annotations

from typing import Any

import certifi
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def engine_url(url: str) -> URL:
    """`postgresql://…` with the psycopg 3 driver."""
    return make_url(url).set(drivername="postgresql+psycopg")


def connect_args(url: URL) -> dict[str, Any]:
    """Remote hosts always use `sslmode=verify-full` against certifi's CA bundle, whatever the URL
    says; only a local test server may skip TLS."""
    args: dict[str, Any] = {"prepare_threshold": None}
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
    )
```

- [ ] **Step 5: Document the local database**

In `CLAUDE.md`, replace:
```
Windows machine, run Python tools as modules (`uv run python -m pytest`); host policy blocks
some uv launchers.
```
with:
```
Windows machine, run Python tools as modules (`uv run python -m pytest`); host policy blocks
some uv launchers and every unsigned executable outside trusted tools.
Backend tests need Postgres: `just test` starts a local one first (`just db-up`, no Docker) and
`just db-down` stops it; CI uses a Postgres 17 service container.
```

In `README.md`, insert this paragraph before the one that starts `Detector quality:`:
```markdown
Backend tests start a local Postgres on their own (no Docker needed); `just db-down` stops it.
```

- [ ] **Step 6: Run the tests and checks**

Run: `just lint test tools-test`
Expected:
- lint is clean;
- backend `168 passed` (164 existing + 3 unit + 1 integration), and domain coverage is still at least 85%;
- tools `190 passed`.

- [ ] **Step 7: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock tools/localdb.py justfile .gitignore .github/workflows/ci.yml CLAUDE.md README.md backend/src/nettriage/adapters backend/tests/conftest.py backend/tests/unit/adapters backend/tests/integration/test_database_server.py
git commit -m "feat(data): Postgres adapter, a local Postgres without Docker, and a CI database

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The identity schema, tenant isolation and the API role

**Files:**
- Create: `backend/alembic.ini`, `backend/migrations/env.py`, `backend/migrations/script.py.mako`, `backend/migrations/versions/0001_identity_schema.py`, `backend/migrations/versions/0002_tenant_isolation.py`
- Modify: `backend/src/nettriage/adapters/postgres.py` (adds `tenant_transaction` and `set_login_password`), `backend/tests/conftest.py` (adds the migrated `database` fixture)
- Create (test helper): `backend/tests/integration/tenantdata.py`
- Test: `backend/tests/integration/test_migrations.py`, `test_schema.py`, `test_tenant_isolation.py`, `test_role_logins.py`

**Interfaces:**
- Consumes: `engine_url`, `connect_args` and `create_database_engine` (Task 1); the harness in `conftest.py` (Task 1).
- Produces:
  - **Migrations** `0001` (tables) and `0002` (RLS, the `app_api` role, grants). They read the database URL from `NETTRIAGE_MIGRATION_DATABASE_URL`, or from Alembic's `config.attributes["database_url"]`.
  - In `nettriage.adapters.postgres`:
    - `tenant_transaction(engine, *, org_id: UUID | None = None, user_id: UUID | None = None) -> Iterator[Connection]`, a context manager;
    - `set_login_password(owner_url: str, role: str, password: str) -> None`.
  - **SQL functions** `app_org_id()` and `app_user_id()`.
  - **Test harness:** the `Database(url, admin, app_api)` fixture `database` (session-scoped), `alembic_config(url) -> Config` and `APP_API_PASSWORD`.
  - **Seed helpers** in `tenantdata`: `add_user`, `add_org`, `add_member`, `add_invitation`, and `add_tenant -> Tenant(org_id, owner_id, invitation_id)`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/integration/tenantdata.py`:
```python
"""Seed users, organizations, memberships and invitations for integration tests. Seeding uses
the superuser engine, which row-level security doesn't apply to."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

from sqlalchemy import Connection, Engine, text


@dataclass(frozen=True)
class Tenant:
    org_id: UUID
    owner_id: UUID
    invitation_id: UUID


def add_user(connection: Connection, email: str | None = None) -> UUID:
    user_id = uuid7()
    connection.execute(
        text("INSERT INTO users (id, cognito_sub, email) VALUES (:id, :sub, :email)"),
        {"id": user_id, "sub": f"sub-{user_id}", "email": email or f"{user_id}@example.com"},
    )
    return user_id


def add_org(connection: Connection, created_by: UUID) -> UUID:
    org_id = uuid7()
    connection.execute(
        text(
            "INSERT INTO organizations (id, name, slug, created_by) "
            "VALUES (:id, :name, :slug, :created_by)"
        ),
        {"id": org_id, "name": "Org", "slug": f"org-{org_id.hex}", "created_by": created_by},
    )
    return org_id


def add_member(connection: Connection, org_id: UUID, user_id: UUID, role: str) -> None:
    connection.execute(
        text("INSERT INTO memberships (org_id, user_id, role) VALUES (:org, :user, :role)"),
        {"org": org_id, "user": user_id, "role": role},
    )


def add_invitation(
    connection: Connection, org_id: UUID, created_by: UUID, email: str, role: str = "viewer"
) -> UUID:
    invitation_id = uuid7()
    connection.execute(
        text(
            "INSERT INTO invitations (id, org_id, email, role, token_hash, expires_at, created_by) "
            "VALUES (:id, :org, :email, :role, :hash, :expires, :created_by)"
        ),
        {
            "id": invitation_id,
            "org": org_id,
            "email": email,
            "role": role,
            "hash": hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
            "expires": datetime.now(UTC) + timedelta(days=7),
            "created_by": created_by,
        },
    )
    return invitation_id


def add_tenant(admin: Engine) -> Tenant:
    """An org with an owner, and a pending invitation."""
    with admin.begin() as connection:
        owner = add_user(connection)
        org = add_org(connection, owner)
        add_member(connection, org, owner, "owner")
        invitation = add_invitation(connection, org, owner, f"invitee-{org.hex}@example.com")
    return Tenant(org_id=org, owner_id=owner, invitation_id=invitation)
```

`backend/tests/integration/test_migrations.py`:
```python
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
```

`backend/tests/integration/test_schema.py`:
```python
"""Constraints the database enforces whatever the application does (spec §5.2)."""

from datetime import datetime
from uuid import uuid7

import pytest
from conftest import Database
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from tenantdata import add_invitation, add_tenant, add_user


def execute(engine: Engine, statement: str, **params: object) -> None:
    with engine.begin() as connection:
        connection.execute(text(statement), params)


def test_a_membership_role_must_be_one_of_the_four_roles(database: Database) -> None:
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        user = add_user(connection)

    with pytest.raises(IntegrityError):
        execute(
            database.admin,
            "INSERT INTO memberships (org_id, user_id, role) VALUES (:org, :user, 'superadmin')",
            org=tenant.org_id,
            user=user,
        )


@pytest.mark.parametrize(
    "slug", ["Upper", "two--dashes", "-leading", "trailing-", "sp ace", "x" * 61]
)
def test_organization_slugs_are_lowercase_words_joined_by_dashes(
    database: Database, slug: str
) -> None:
    with pytest.raises(IntegrityError):
        execute(
            database.admin,
            "INSERT INTO organizations (id, name, slug) VALUES (:id, 'Org', :slug)",
            id=uuid7(),
            slug=slug,
        )


def test_an_email_has_at_most_one_pending_invitation_per_org(database: Database) -> None:
    tenant = add_tenant(database.admin)

    with pytest.raises(IntegrityError), database.admin.begin() as connection:
        add_invitation(
            connection, tenant.org_id, tenant.owner_id, f"INVITEE-{tenant.org_id.hex}@Example.com"
        )


def test_a_revoked_invitation_does_not_block_a_new_one(database: Database) -> None:
    tenant = add_tenant(database.admin)
    execute(
        database.admin,
        "UPDATE invitations SET revoked_at = now() WHERE id = :id",
        id=tenant.invitation_id,
    )

    with database.admin.begin() as connection:
        add_invitation(
            connection, tenant.org_id, tenant.owner_id, f"invitee-{tenant.org_id.hex}@example.com"
        )


def test_deleting_an_org_removes_its_memberships_and_invitations_not_its_audit_log(
    database: Database,
) -> None:
    tenant = add_tenant(database.admin)
    execute(
        database.admin,
        "INSERT INTO audit_log (id, org_id, actor_type, action, outcome) "
        "VALUES (:id, :org, 'system', 'test.event', 'success')",
        id=uuid7(),
        org=tenant.org_id,
    )

    execute(database.admin, "DELETE FROM organizations WHERE id = :id", id=tenant.org_id)

    with database.admin.begin() as connection:
        remaining: dict[str, int] = {
            table: connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE org_id = :org"),  # noqa: S608
                {"org": tenant.org_id},
            ).scalar_one()
            for table in ("memberships", "invitations", "audit_log")
        }
    assert remaining == {"memberships": 0, "invitations": 0, "audit_log": 1}


@pytest.mark.parametrize(
    "statement", ["UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log"]
)
def test_the_audit_log_is_append_only_even_for_a_superuser(
    database: Database, statement: str
) -> None:
    execute(
        database.admin,
        "INSERT INTO audit_log (id, actor_type, action, outcome) "
        "VALUES (:id, 'system', 'test.event', 'success')",
        id=uuid7(),
    )

    with pytest.raises(DBAPIError, match="append-only"):
        execute(database.admin, statement)


def test_updates_refresh_updated_at(database: Database) -> None:
    tenant = add_tenant(database.admin)
    query = text("SELECT updated_at FROM organizations WHERE id = :id")
    with database.admin.begin() as connection:
        before: datetime = connection.execute(query, {"id": tenant.org_id}).scalar_one()

    execute(
        database.admin,
        "UPDATE organizations SET name = 'Renamed' WHERE id = :id",
        id=tenant.org_id,
    )

    with database.admin.begin() as connection:
        after: datetime = connection.execute(query, {"id": tenant.org_id}).scalar_one()
    assert after > before
```

`backend/tests/integration/test_tenant_isolation.py`:
```python
"""Row-level security and the API role's grants (spec §5.3, §5.4). Every query here runs as
`app_api`, the role the API Lambda uses."""

from uuid import UUID, uuid7

import pytest
from conftest import Database
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from tenantdata import add_invitation, add_member, add_tenant

from nettriage.adapters.postgres import tenant_transaction

TENANT_TABLES = ("organizations", "memberships", "invitations", "audit_log")
INSERT_AUDIT_EVENT = text(
    "INSERT INTO audit_log (id, org_id, actor_type, action, outcome) "
    "VALUES (:id, :org, 'user', 'test.event', 'success')"
)


def org_ids(connection: Connection, table: str) -> set[UUID]:
    """The org IDs of every row the connection can see, with no WHERE clause."""
    column = "id" if table == "organizations" else "org_id"
    return set(connection.execute(text(f"SELECT {column} FROM {table}")).scalars())  # noqa: S608


def run_as_api(database: Database, statement: str, org_id: UUID, **params: object) -> None:
    with tenant_transaction(database.app_api, org_id=org_id) as connection:
        connection.execute(text(statement), params)


def test_a_query_without_an_org_filter_sees_only_its_own_org(database: Database) -> None:
    """The spec's dedicated isolation test: no WHERE clause, and still no other tenant's rows."""
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    with tenant_transaction(database.app_api, org_id=mine.org_id) as connection:
        seen = {
            table: org_ids(connection, table)
            for table in ("organizations", "memberships", "invitations")
        }

    assert seen == {table: {mine.org_id} for table in seen}
    assert all(theirs.org_id not in ids for ids in seen.values())


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_no_tenant_settings_means_no_rows(database: Database, table: str) -> None:
    add_tenant(database.admin)

    with tenant_transaction(database.app_api) as connection:
        assert org_ids(connection, table) == set()


def test_a_pooled_connection_forgets_the_previous_tenant(database: Database) -> None:
    tenant = add_tenant(database.admin)
    with tenant_transaction(database.app_api, org_id=tenant.org_id) as connection:
        assert org_ids(connection, "invitations") == {tenant.org_id}

    with database.app_api.begin() as connection:  # the same pooled connection, no settings
        assert org_ids(connection, "invitations") == set()


def test_rows_for_another_org_cannot_be_written(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    def invite_into_their_org() -> None:
        with tenant_transaction(database.app_api, org_id=mine.org_id) as connection:
            add_invitation(connection, theirs.org_id, mine.owner_id, "someone@example.com")

    with pytest.raises(ProgrammingError, match="row-level security"):
        invite_into_their_org()


def test_a_user_sees_their_memberships_and_orgs_everywhere_but_not_other_invitations(
    database: Database,
) -> None:
    first, second = add_tenant(database.admin), add_tenant(database.admin)
    with database.admin.begin() as connection:
        add_member(connection, second.org_id, first.owner_id, "viewer")

    with tenant_transaction(database.app_api, user_id=first.owner_id) as connection:
        orgs = org_ids(connection, "organizations")
        memberships = org_ids(connection, "memberships")
        invitations = org_ids(connection, "invitations")

    assert orgs == memberships == {first.org_id, second.org_id}
    assert invitations == set()


def test_audit_events_may_have_no_org_and_are_read_per_org(database: Database) -> None:
    mine = add_tenant(database.admin)

    with tenant_transaction(database.app_api, org_id=mine.org_id) as connection:
        connection.execute(INSERT_AUDIT_EVENT, {"id": uuid7(), "org": None})
        connection.execute(INSERT_AUDIT_EVENT, {"id": uuid7(), "org": mine.org_id})
        assert org_ids(connection, "audit_log") == {mine.org_id}


def test_audit_events_cannot_be_written_for_another_org(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    def log_into_their_org() -> None:
        with tenant_transaction(database.app_api, org_id=mine.org_id) as connection:
            connection.execute(INSERT_AUDIT_EVENT, {"id": uuid7(), "org": theirs.org_id})

    with pytest.raises(ProgrammingError, match="row-level security"):
        log_into_their_org()


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
        "UPDATE organizations SET slug = 'taken-over'",
        "UPDATE users SET cognito_sub = 'someone-else'",
        "UPDATE invitations SET token_hash = repeat('0', 64)",
    ],
)
def test_the_api_role_only_has_the_grants_it_needs(database: Database, statement: str) -> None:
    tenant = add_tenant(database.admin)

    with pytest.raises(ProgrammingError, match="permission denied"):
        run_as_api(database, statement, tenant.org_id)


def test_the_api_role_cannot_bypass_row_level_security(database: Database) -> None:
    with database.app_api.connect() as connection:
        flags = connection.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
        owned: int = connection.execute(
            text("SELECT count(*) FROM pg_tables WHERE tableowner = current_user")
        ).scalar_one()

    assert tuple(flags) == (False, False)
    assert owned == 0


def test_an_invalid_tenant_setting_is_an_error_not_a_match(database: Database) -> None:
    def query_with_a_bad_setting() -> None:
        with database.app_api.begin() as connection:
            connection.execute(text("SELECT set_config('app.org_id', 'not-a-uuid', true)"))
            connection.execute(text("SELECT count(*) FROM invitations"))

    with pytest.raises(DBAPIError, match="uuid"):
        query_with_a_bad_setting()
```

`backend/tests/integration/test_role_logins.py`:
```python
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
```

Replace `backend/tests/conftest.py` with the version that adds the migrated `database` fixture:
```python
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL

from nettriage.adapters.postgres import create_database_engine, engine_url
from nettriage.entrypoints.api.app import create_app
from nettriage.platform.config import Settings

TEST_DATABASE_ENV = "NETTRIAGE_TEST_DATABASE_URL"
BACKEND = Path(__file__).resolve().parents[1]
APP_API_PASSWORD = "app-api-test-only"  # noqa: S105 - a throwaway password on a test server


@pytest.fixture
def settings() -> Settings:
    return Settings(stage="local", version="1.2.3-test")


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


# Database fixtures. Integration tests need a Postgres superuser URL in
# NETTRIAGE_TEST_DATABASE_URL. `just test` starts a local server and sets it; CI sets it for
# its Postgres service container. Each session gets a throwaway database.


@dataclass(frozen=True)
class Database:
    """A database with every migration applied. `admin` is a superuser engine that seeds data
    past row-level security; `app_api` connects as the API's role."""

    url: URL
    admin: Engine
    app_api: Engine


def server_url() -> URL:
    url = os.environ.get(TEST_DATABASE_ENV)
    if not url:
        pytest.fail(
            f"{TEST_DATABASE_ENV} isn't set. Run `just test`, which starts the local database, "
            "or run `just db-up` and set it to the URL in .localdb/url."
        )
    return engine_url(url)


def alembic_config(url: URL) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["database_url"] = url.render_as_string(hide_password=False)
    return config


def create_database(server: URL) -> URL:
    name = f"nettriage_test_{uuid4().hex[:12]}"
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    return server.set(database=name)


def drop_database(server: URL, url: URL) -> None:
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE "{url.database}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(scope="session")
def database() -> Iterator[Database]:
    server = server_url()
    url = create_database(server)
    command.upgrade(alembic_config(url), "head")
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f"ALTER ROLE app_api WITH LOGIN PASSWORD '{APP_API_PASSWORD}'"))
    app_url = url.set(username="app_api", password=APP_API_PASSWORD)
    app_api = create_database_engine(app_url.render_as_string(hide_password=False), pool_size=1)
    yield Database(url=url, admin=admin, app_api=app_api)
    app_api.dispose()
    admin.dispose()
    drop_database(server, url)


@pytest.fixture
def empty_database() -> Iterator[URL]:
    """A database with no migrations applied, for migration tests."""
    server = server_url()
    url = create_database(server)
    yield url
    drop_database(server, url)
```

- [ ] **Step 2: Run them to see them fail**

Run: `just test`
Expected: FAIL. Collection errors say `cannot import name 'tenant_transaction' from 'nettriage.adapters.postgres'`, and the migration tests can't find `alembic.ini`.

- [ ] **Step 3: Write the migrations**

`backend/alembic.ini`:
```ini
[alembic]
# Migrations are explicit SQL (spec §5.8). The database URL comes from
# NETTRIAGE_MIGRATION_DATABASE_URL: the database owner's URL, which the deploy reads from SSM.
script_location = %(here)s/migrations
```

`backend/migrations/env.py`:
```python
"""Alembic entry point. There's no ORM metadata: every migration is explicit SQL (spec §5.8)."""

from __future__ import annotations

import os

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


run_migrations()
```

`backend/migrations/script.py.mako`:
```
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""

from alembic import op

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("")


def downgrade() -> None:
    op.execute("")
```

`backend/migrations/versions/0001_identity_schema.py`:
```python
"""Identity and organizations: users, organizations, memberships, invitations, audit log
(spec §5.2).

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ROLES = "('owner', 'admin', 'analyst', 'viewer')"


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END $$;

        CREATE TABLE users (
            id uuid PRIMARY KEY,
            cognito_sub text NOT NULL UNIQUE,
            email text NOT NULL CHECK (length(email) <= 320),
            display_name text CHECK (length(display_name) <= 100),
            last_login_at timestamptz,
            disabled_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE organizations (
            id uuid PRIMARY KEY,
            name text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
            slug text NOT NULL UNIQUE
                CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND length(slug) <= 60),
            is_demo boolean NOT NULL DEFAULT false,
            created_by uuid REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        """
        + f"""
        CREATE TABLE memberships (
            org_id uuid NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            role text NOT NULL CHECK (role IN {ROLES}),
            invited_by uuid REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (org_id, user_id)
        );

        CREATE TABLE invitations (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
            email text NOT NULL CHECK (length(email) <= 320),
            role text NOT NULL CHECK (role IN {ROLES}),
            token_hash text NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{{64}}$'),
            expires_at timestamptz NOT NULL,
            created_by uuid NOT NULL REFERENCES users (id),
            accepted_at timestamptz,
            accepted_by uuid REFERENCES users (id),
            revoked_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, id)
        );
        """
        + """
        -- At most one pending invitation per email in an org. "Pending" can't include
        -- "not expired" (an index can't call now()); expired rows are revoked before a new
        -- invitation for the same email is created.
        CREATE UNIQUE INDEX invitations_one_pending_per_email
            ON invitations (org_id, lower(email))
            WHERE accepted_at IS NULL AND revoked_at IS NULL;

        CREATE TABLE audit_log (
            id uuid PRIMARY KEY,
            org_id uuid,
            actor_user_id uuid,
            actor_type text NOT NULL CHECK (actor_type IN ('user', 'system', 'anonymous')),
            action text NOT NULL CHECK (length(action) <= 100),
            target_type text CHECK (length(target_type) <= 50),
            target_id text CHECK (length(target_id) <= 100),
            outcome text NOT NULL CHECK (outcome IN ('success', 'denied', 'error')),
            ip inet,
            user_agent text CHECK (length(user_agent) <= 256),
            request_id text CHECK (length(request_id) <= 100),
            trace_id text CHECK (length(trace_id) <= 64),
            details jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX audit_log_org_created ON audit_log (org_id, created_at DESC);

        CREATE TRIGGER users_updated_at BEFORE UPDATE ON users
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        CREATE TRIGGER organizations_updated_at BEFORE UPDATE ON organizations
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        CREATE TRIGGER memberships_updated_at BEFORE UPDATE ON memberships
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        CREATE TRIGGER invitations_updated_at BEFORE UPDATE ON invitations
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();

        -- The audit log is append-only. Plan 7's retention function amends this trigger.
        CREATE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
        END $$;
        CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log
            FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE audit_log;
        DROP FUNCTION audit_log_append_only();
        DROP TABLE invitations;
        DROP TABLE memberships;
        DROP TABLE organizations;
        DROP TABLE users;
        DROP FUNCTION set_updated_at();
        """
    )
```

`backend/migrations/versions/0002_tenant_isolation.py`:
```python
"""Tenant isolation: row-level security, the `app_api` role and its grants (spec §5.3, §5.4).

Every tenant table has ENABLE and FORCE ROW LEVEL SECURITY. The policies compare against
`app.org_id` and `app.user_id`, which `tenant_transaction` sets per transaction; an unset or
empty setting reads as NULL and matches no rows.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TENANT_TABLES = ("organizations", "memberships", "invitations", "audit_log")


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION app_org_id() RETURNS uuid LANGUAGE sql STABLE
            AS $$ SELECT NULLIF(current_setting('app.org_id', true), '')::uuid $$;
        CREATE FUNCTION app_user_id() RETURNS uuid LANGUAGE sql STABLE
            AS $$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$;
        """
    )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        -- An organization is visible when it's the transaction's org, or the user is a member
        -- (for "my organizations"). Writes are only allowed to the transaction's org.
        CREATE POLICY tenant ON organizations
            USING (id = app_org_id()
                   OR id IN (SELECT org_id FROM memberships WHERE user_id = app_user_id()))
            WITH CHECK (id = app_org_id());

        -- A user also sees their own memberships in every org.
        CREATE POLICY tenant ON memberships
            USING (org_id = app_org_id() OR user_id = app_user_id())
            WITH CHECK (org_id = app_org_id());

        CREATE POLICY tenant ON invitations
            USING (org_id = app_org_id())
            WITH CHECK (org_id = app_org_id());

        -- Events outside any org (sign-in, denials on non-org routes) have a NULL org_id.
        CREATE POLICY tenant_read ON audit_log FOR SELECT
            USING (org_id = app_org_id());
        CREATE POLICY tenant_append ON audit_log FOR INSERT
            WITH CHECK (org_id IS NULL OR org_id = app_org_id());

        -- On Neon, Terraform creates app_api with a login and password first. Locally and in
        -- CI it's created here, without a login.
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_api') THEN
                CREATE ROLE app_api NOLOGIN;
            END IF;
        END $$;

        GRANT USAGE ON SCHEMA public TO app_api;
        GRANT EXECUTE ON FUNCTION app_org_id(), app_user_id() TO app_api;
        GRANT SELECT, INSERT ON users TO app_api;
        GRANT UPDATE (email, display_name, last_login_at) ON users TO app_api;
        GRANT SELECT, INSERT, DELETE ON organizations TO app_api;
        GRANT UPDATE (name) ON organizations TO app_api;
        GRANT SELECT, INSERT, DELETE ON memberships TO app_api;
        GRANT UPDATE (role) ON memberships TO app_api;
        GRANT SELECT, INSERT, DELETE ON invitations TO app_api;
        GRANT UPDATE (accepted_at, accepted_by, revoked_at) ON invitations TO app_api;
        GRANT SELECT, INSERT ON audit_log TO app_api;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        REVOKE ALL ON users, organizations, memberships, invitations, audit_log FROM app_api;
        REVOKE ALL ON FUNCTION app_org_id(), app_user_id() FROM app_api;
        REVOKE USAGE ON SCHEMA public FROM app_api;
        DROP POLICY tenant_append ON audit_log;
        DROP POLICY tenant_read ON audit_log;
        DROP POLICY tenant ON invitations;
        DROP POLICY tenant ON memberships;
        DROP POLICY tenant ON organizations;
        """
    )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION app_user_id(); DROP FUNCTION app_org_id();")
```

- [ ] **Step 4: Add tenant transactions and role logins to the adapter**

Replace `backend/src/nettriage/adapters/postgres.py` with:
```python
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


def engine_url(url: str) -> URL:
    """`postgresql://…` with the psycopg 3 driver."""
    return make_url(url).set(drivername="postgresql+psycopg")


def connect_args(url: URL) -> dict[str, Any]:
    """Remote hosts always use `sslmode=verify-full` against certifi's CA bundle, whatever the URL
    says; only a local test server may skip TLS."""
    args: dict[str, Any] = {"prepare_threshold": None}
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
```

- [ ] **Step 5: Run the tests and checks**

Run: `just lint test`
Expected:
- lint is clean;
- backend `200 passed`, which adds 1 migration test, 13 schema tests, 17 isolation tests and 1 role-login test;
- no `nettriage_test_*` databases are left behind.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic.ini backend/migrations backend/src/nettriage/adapters/postgres.py backend/tests/conftest.py backend/tests/integration
git commit -m "feat(data): identity schema with row-level security and a least-privilege API role

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: The deploy stores the database, migrates it and creates role logins

**Files:**
- Modify: `tools/deploy/config.py`, `tools/deploy/secrets.py`, `tools/deploy/preflight.py`, `tools/deploy/__main__.py`, `justfile`, `CLAUDE.md`
- Create: `tools/deploy/database.py`
- Modify (tests): `tools/tests/deploy_fakes.py`, `tools/tests/test_deploy_cli.py`, `tools/tests/test_deploy_preflight.py`
- Test: `tools/tests/test_deploy_database.py`

**Interfaces:**
- Consumes: `set_login_password` (Task 2), and the Alembic config at `backend/alembic.ini` (Task 2).
- Produces:
  - In `config`: `NEON_HOST_SUFFIX`, `APP_DB_ROLES = ("app_api",)`, `MIGRATIONS_CONFIG`, `db_owner_url_parameter(stage)` and `db_role_url_parameter(stage, role)`.
  - In `secrets`: `read_parameter`, `store_parameter` and `parameter_exists`. The Grafana helpers now use these.
  - In `database`: `check_owner_url`, `pooled_url`, `migrate`, `set_role_password` and `ensure_role_logins`.
  - In `preflight`: `ssm_parameter_check`, and a stage check named "Database connection in SSM".
  - The CLI command `store-database-url` and the recipe `just store-database-url <stage>`. `deploy` now calls `migrate_database` right before `terraform init`/`apply`.

- [ ] **Step 1: Write the failing tests**

Replace `tools/tests/deploy_fakes.py` with (it adds `OWNER_URL`, `ssm_names` and `ssm_values`):
```python
"""A scripted stand-in for tools.deploy.runner.run, shared by the deploy tests."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tools.deploy.runner import CommandError, Result


@dataclass
class Call:
    args: list[str]
    env: Mapping[str, str] | None
    cwd: Path | None
    interactive: bool
    redact: tuple[str, ...]


# A rule's answer: stdout text; an exit code (for check=False probes); an exception to raise;
# or a function of the call that returns stdout, or a full Result for a non-zero exit code with
# its own stdout (e.g. `terraform plan -json`'s diagnostics), and may create files or raise.
Answer = str | int | Exception | Callable[[Call], "str | Result"]


@dataclass
class FakeRun:
    """Answers each command with the first rule whose prefix matches, and records every call."""

    rules: list[tuple[tuple[str, ...], Answer]] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)

    def on(self, *prefix: str, returns: Answer = "") -> FakeRun:
        self.rules.append((prefix, returns))
        return self

    def __call__(
        self,
        args: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        interactive: bool = False,
        check: bool = True,
        redact: Sequence[str] = (),
    ) -> Result:
        call = Call(list(args), env, cwd, interactive, tuple(redact))
        self.calls.append(call)
        for prefix, answer in self.rules:
            if tuple(call.args[: len(prefix)]) != prefix:
                continue
            if isinstance(answer, Exception):
                raise answer
            if isinstance(answer, int):
                if check and answer != 0:
                    raise CommandError(f"`{' '.join(call.args[:2])}` failed with exit code {answer}.")
                return Result(answer, "")
            if callable(answer):
                outcome = answer(call)
                if not isinstance(outcome, Result):
                    return Result(0, outcome)
                if check and outcome.returncode != 0:
                    raise CommandError(f"`{' '.join(call.args[:2])}` failed with exit code {outcome.returncode}.")
                return outcome
            return Result(0, answer)
        raise AssertionError(f"unexpected command: {call.args}")

    def called(self, *prefix: str) -> list[Call]:
        return [call for call in self.calls if tuple(call.args[: len(prefix)]) == prefix]

    def first(self, *prefix: str) -> int:
        """Index of the first call starting with prefix, for ordering assertions."""
        return next(
            i for i, call in enumerate(self.calls) if tuple(call.args[: len(prefix)]) == prefix
        )


SESSION = json.dumps(
    {
        "Version": 1,
        "AccessKeyId": "ASIAEXAMPLE",
        "SecretAccessKey": "example-secret",
        "SessionToken": "example-session",
        "Expiration": "2026-09-27T20:00:00Z",
    }
)


TOOLS_CREDENTIAL_PROCESS = "aws configure export-credentials --profile nettriage --format process"


def signed_in() -> FakeRun:
    """A runner whose owner is signed in to account 123456789012, with the nettriage-tools
    helper profile already set up so aws_env's `aws configure get` check succeeds without change."""
    return (
        FakeRun()
        .on("aws", "configure", "export-credentials", returns=SESSION)
        .on("aws", "configure", "get", returns=f"{TOOLS_CREDENTIAL_PROCESS}\n")
        .on("aws", "sts", "get-caller-identity", returns="123456789012\n")
    )


def runs(*items: tuple[int, str, str, str]) -> str:
    """`gh run list --json databaseId,status,conclusion,event` output, newest first."""
    return json.dumps(
        [{"databaseId": i, "status": s, "conclusion": c, "event": e} for i, s, c, e in items]
    )


OWNER_URL = (
    "postgresql://neondb_owner:owner-s3cret@ep-quiet-sun-123456.eu-central-1.aws.neon.tech/"
    "neondb?sslmode=require&channel_binding=require"
)


def ssm_names(missing: Sequence[str] = ()) -> Callable[[Call], str]:
    """`aws ssm describe-parameters` answers: every parameter exists except `missing`."""

    def answer(call: Call) -> str:
        filters = [arg for arg in call.args if arg.startswith("Key=Name,Values=")]
        if not filters:  # preflight's unfiltered "SSM in eu-north-1" probe
            return ""
        name = filters[0].split("=", 2)[2]
        return "None\n" if name in missing else f"{name}\n"

    return answer


def ssm_values(values: Mapping[str, str]) -> Callable[[Call], str]:
    """`aws ssm get-parameter` answers, by parameter name."""

    def answer(call: Call) -> str:
        return values[call.args[call.args.index("--name") + 1]] + "\n"

    return answer
```

`tools/tests/test_deploy_database.py`:
```python
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
```

Replace `tools/tests/test_deploy_cli.py` with:
```python
import base64
import json
import sys
from pathlib import Path

import pytest

from tools.deploy import __main__ as cli
from tools.deploy import config
from tools.deploy.runner import CommandError
from tools.tests.deploy_fakes import (
    OWNER_URL,
    Call,
    FakeRun,
    runs,
    signed_in,
    ssm_names,
    ssm_values,
)

SHA = "d" * 40
LWA = "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerArm64:30"
OTEL = "arn:aws:lambda:eu-north-1:184161586896:layer:opentelemetry-collector-arm64-0_22_0:1"
OUTPUTS = json.dumps(
    {
        "cloudfront_domain": {"value": "d111.cloudfront.net"},
        "distribution_id": {"value": "E123"},
        "web_bucket": {"value": "nettriage-dev-web-1"},
        "function_url": {"value": "https://fn.lambda-url.eu-north-1.on.aws/"},
    }
)


@pytest.fixture
def stage_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "terraform.tfvars").write_text(
        f'lwa_layer_arn = "{LWA}"\notel_collector_layer_arn = "{OTEL}"\n'
        'grafana_otlp_endpoint = "https://otlp.example/otlp"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "stage_dir", lambda stage: tmp_path)
    return tmp_path


STORED = {"/nettriage/dev/grafana-otlp-auth": "dG9rZW4=", "/nettriage/dev/db/owner-url": OWNER_URL}


def healthy_account(run: FakeRun) -> FakeRun:
    """Every read-only AWS check passes, and the Grafana token, the database owner's URL and
    the app roles' logins are all stored."""
    return (
        run.on("aws", "lambda", "get-layer-version-by-arn", returns=json.dumps({"CompatibleArchitectures": ["arm64"]}))
        .on("aws", "ssm", "describe-parameters", returns=ssm_names())
        .on("aws", "ssm", "get-parameter", returns=ssm_values(STORED))
        .on("aws")
    )


def main_checkout(run: FakeRun) -> FakeRun:
    return (
        run.on("git", "rev-parse", "--abbrev-ref", returns="main\n")
        .on("git", "status", returns="")
        .on("git", "fetch")
        .on("git", "rev-parse", returns=f"{SHA}\n")
    )


def deployable() -> FakeRun:
    run = main_checkout(signed_in())
    run.on("gh", "run", "list", returns=runs((9, "completed", "success", "push")))
    run.on("gh", "run", "download").on("terraform", "output", returns=OUTPUTS).on("terraform")
    run.on(sys.executable, "-m", "alembic")
    return healthy_account(run)


def test_deploy_ships_ci_artifacts_of_a_green_main_commit_then_smoke_tests(stage_dir: Path) -> None:
    run = deployable()
    smoke_argv: list[list[str]] = []

    cli.deploy(run, {}, "dev", smoke_main=lambda argv: smoke_argv.append(argv) or 0)

    workflows = [call.args[call.args.index("--workflow") + 1] for call in run.called("gh", "run", "list")]
    assert workflows == ["ci.yml", "codeql.yml"]
    assert [call.args[call.args.index("--name") + 1] for call in run.called("gh", "run", "download")] == [
        "backend-zip", "web-dist",
    ]
    assert run.first("gh", "run", "download") < run.first("terraform", "apply") < run.first("aws", "s3", "sync")
    [init] = run.called("terraform", "init")
    assert init.cwd == stage_dir
    assert "-backend-config=bucket=nettriage-tfstate-123456789012" in init.args
    assert "-backend-config=key=envs/dev/terraform.tfstate" in init.args
    assert "-backend-config=region=eu-north-1" in init.args
    [apply] = run.called("terraform", "apply")
    assert apply.interactive
    assert apply.env is not None
    assert apply.env["TF_VAR_app_version"] == SHA
    assert apply.env["TF_VAR_grafana_otlp_auth"] == "dG9rZW4="
    assert apply.env["TF_VAR_lambda_zip_path"].endswith("backend.zip")
    assert smoke_argv == [[
        "--base-url", "https://d111.cloudfront.net",
        "--function-url", "https://fn.lambda-url.eu-north-1.on.aws/",
        "--version", SHA,
    ]]


@pytest.mark.parametrize(
    "listing",
    [runs(), runs((9, "completed", "failure", "push")), runs((9, "in_progress", "", "push"))],
)
def test_nothing_changes_in_aws_unless_ci_is_green(stage_dir: Path, listing: str) -> None:
    run = main_checkout(signed_in()).on("gh", "run", "list", returns=listing)
    with pytest.raises(CommandError, match="ci.yml"):
        cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)
    assert run.called("terraform") == []
    assert run.called("aws") == []


def test_codeql_red_blocks_the_deploy(stage_dir: Path) -> None:
    run = main_checkout(signed_in())
    # The specific codeql.yml rule must come before the generic "gh run list" success rule.
    run.on("gh", "run", "list", "--workflow", "codeql.yml", returns=runs((11, "completed", "failure", "push")))
    run.on("gh", "run", "list", returns=runs((9, "completed", "success", "push")))
    with pytest.raises(CommandError, match="codeql.yml"):
        cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)
    assert run.called("terraform") == []
    assert run.called("aws") == []


def _run_on_another_branch() -> FakeRun:
    return FakeRun().on("git", "rev-parse", "--abbrev-ref", returns="feature\n")


def _run_with_a_dirty_tree() -> FakeRun:
    return FakeRun().on("git", "rev-parse", "--abbrev-ref", returns="main\n").on(
        "git", "status", returns=" M file.py\n"
    )


@pytest.mark.parametrize(
    "build, match",
    [(_run_on_another_branch, "Deploys run from main"), (_run_with_a_dirty_tree, "uncommitted changes")],
    ids=["other-branch", "dirty-tree"],
)
def test_a_dirty_or_other_branch_checkout_never_reaches_terraform(
    stage_dir: Path, build, match: str
) -> None:
    run = build()
    with pytest.raises(CommandError, match=match):
        cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)
    assert run.called("terraform") == []
    assert run.called("aws") == []
    assert run.called("gh") == []


def test_a_checkout_that_turns_dirty_during_the_deploy_makes_no_terraform_call(stage_dir: Path) -> None:
    """require_clean_main's own check passes; the tree turns dirty only afterwards (while CI and
    the artifact downloads are checked), so the pre-apply recheck must catch it."""
    porcelain_calls = {"n": 0}

    def porcelain_status(call: Call) -> str:
        porcelain_calls["n"] += 1
        return "" if porcelain_calls["n"] == 1 else " M file.py\n"

    run = healthy_account(signed_in())
    run.on("git", "rev-parse", "--abbrev-ref", returns="main\n")
    run.on("git", "status", "--porcelain", returns=porcelain_status)
    run.on("git", "fetch")
    run.on("git", "rev-parse", returns=f"{SHA}\n")
    run.on("gh", "run", "list", returns=runs((9, "completed", "success", "push")))
    run.on("gh", "run", "download")

    with pytest.raises(CommandError, match="checkout changed during the deploy"):
        cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)

    assert run.called("terraform") == []
    assert run.called("aws", "s3", "sync") == []
    assert run.called("aws", "cloudfront", "create-invalidation") == []


def test_deploy_stops_before_terraform_when_preflight_fails(stage_dir: Path) -> None:
    """R16: preflight (the only guard that makes AWS calls) runs last, after every local and
    GitHub guard, but it must still stop the deploy before Terraform touches anything."""
    run = main_checkout(signed_in().on("aws", "cloudfront", returns=CommandError("explicit deny")))
    run.on("gh", "run", "list", returns=runs((9, "completed", "success", "push")))
    run = healthy_account(run)
    with pytest.raises(CommandError, match="Preflight failed"):
        cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)
    assert run.called("terraform") == []


def test_failed_smoke_tests_fail_the_deploy(stage_dir: Path) -> None:
    with pytest.raises(CommandError, match="Smoke tests failed"):
        cli.deploy(deployable(), {}, "dev", smoke_main=lambda argv: 1)


def planning(listing: str, plan_output: str = "") -> FakeRun:
    run = signed_in().on("git", "status", "--porcelain", returns="").on("git", "rev-parse", "HEAD", returns=f"{SHA}\n")
    run.on("gh", "run", "list", returns=listing).on("gh", "run", "download").on("gh", "pr", "comment")
    run.on("terraform", "plan", returns=plan_output).on("terraform")
    return healthy_account(run)


def test_plan_posts_addresses_only_to_the_pr(stage_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan_output = json.dumps(
        {
            "type": "planned_change",
            "change": {
                "action": "create",
                "resource": {"addr": "module.app.aws_lambda_function.api"},
                "after_unknown": {"secret": "dG9rZW4="},
            },
        }
    )
    run = planning(runs((5, "completed", "success", "pull_request")), plan_output)

    changes = cli.plan(run, {}, "dev", post_comment=True)

    assert changes == ["create module.app.aws_lambda_function.api"]
    assert len(run.called("gh", "pr", "comment")) == 1
    [plan_call] = run.called("terraform", "plan")
    assert "-json" in plan_call.args
    assert not any(arg.startswith("-out") for arg in plan_call.args)
    out = capsys.readouterr().out
    assert "### Terraform plan: dev (ddddddd)" in out
    assert "dG9rZW4=" not in out


def test_plan_with_no_comment_leaves_the_pr_alone(stage_dir: Path) -> None:
    run = planning(runs((5, "completed", "success", "pull_request")))
    cli.plan(run, {}, "dev", post_comment=False)
    assert run.called("gh", "pr", "comment") == []


def test_plan_refuses_a_missing_ci_run(stage_dir: Path) -> None:
    run = planning(runs())
    with pytest.raises(CommandError, match="No ci.yml run found"):
        cli.plan(run, {}, "dev", post_comment=True)
    assert run.called("terraform") == []


def test_plan_refuses_a_completed_but_failed_ci_run(stage_dir: Path) -> None:
    run = planning(runs((5, "completed", "failure", "pull_request")))
    with pytest.raises(CommandError, match="only green commits are planned or deployed"):
        cli.plan(run, {}, "dev", post_comment=True)
    assert run.called("terraform") == []


def test_plan_refuses_a_dirty_tree_before_any_gh_or_aws_call(stage_dir: Path) -> None:
    run = FakeRun().on("git", "status", "--porcelain", returns=" M file.py\n")
    with pytest.raises(CommandError, match="uncommitted changes"):
        cli.plan(run, {}, "dev", post_comment=True)
    assert run.called("gh") == []
    assert run.called("aws") == []
    assert run.called("terraform") == []


def test_plan_refuses_a_tree_that_turns_dirty_after_the_ci_check(stage_dir: Path) -> None:
    """require_clean_tree's own check passes; the tree turns dirty only afterwards (while the CI
    check and artifact download run), so the pre-terraform recheck must catch it."""
    porcelain_calls = {"n": 0}

    def porcelain_status(call: Call) -> str:
        porcelain_calls["n"] += 1
        return "" if porcelain_calls["n"] == 1 else " M file.py\n"

    run = healthy_account(signed_in()).on("git", "status", "--porcelain", returns=porcelain_status)
    run.on("git", "rev-parse", "HEAD", returns=f"{SHA}\n")
    run.on("gh", "run", "list", returns=runs((5, "completed", "success", "pull_request")))
    run.on("gh", "run", "download")

    with pytest.raises(CommandError, match="checkout changed during the plan"):
        cli.plan(run, {}, "dev", post_comment=True)

    assert run.called("terraform") == []
    assert run.called("gh", "pr", "comment") == []


def test_plan_refuses_a_head_that_moves_after_the_ci_check(stage_dir: Path) -> None:
    head_calls = {"n": 0}

    def head_sha_answer(call: Call) -> str:
        head_calls["n"] += 1
        return f"{SHA}\n" if head_calls["n"] == 1 else f"{'c' * 40}\n"

    run = healthy_account(signed_in()).on("git", "status", "--porcelain", returns="")
    run.on("git", "rev-parse", "HEAD", returns=head_sha_answer)
    run.on("gh", "run", "list", returns=runs((5, "completed", "success", "pull_request")))
    run.on("gh", "run", "download")

    with pytest.raises(CommandError, match="checkout changed during the plan"):
        cli.plan(run, {}, "dev", post_comment=True)

    assert run.called("terraform") == []
    assert run.called("gh", "pr", "comment") == []


@pytest.fixture
def bootstrap_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    boot = tmp_path / "bootstrap"
    boot.mkdir()
    (boot / "main.tf").write_text("# the stack\n", encoding="utf-8")
    (boot / "backend.tf").write_text('terraform {\n  backend "s3" {}\n}\n', encoding="utf-8")
    monkeypatch.setattr(config, "BOOTSTRAP_DIR", boot)
    return boot


def test_first_bootstrap_applies_locally_then_pushes_state_into_the_new_bucket(bootstrap_dir: Path) -> None:
    seen: dict[str, bool] = {}

    def apply(call: Call) -> str:
        assert call.cwd is not None
        seen["backend_in_scratch"] = (call.cwd / "backend.tf").exists()
        (call.cwd / "terraform.tfstate").write_text("{}", encoding="utf-8")
        return ""

    run = signed_in().on("aws", "s3api", "head-bucket", returns=254)
    run.on("terraform", "apply", returns=apply).on("terraform").on("aws")

    cli.bootstrap(run, {}, "owner@example.com", "")

    assert seen["backend_in_scratch"] is False
    [apply_call] = run.called("terraform", "apply")
    assert "-var=budget_email=owner@example.com" in apply_call.args
    [push] = run.called("terraform", "state", "push")
    assert push.cwd == bootstrap_dir
    assert "-no-color" in push.args
    repo_init = [call for call in run.called("terraform", "init") if call.cwd == bootstrap_dir]
    assert "-backend-config=key=bootstrap/terraform.tfstate" in repo_init[0].args
    assert all("-no-color" in call.args for call in run.called("terraform", "init"))
    assert all("-lockfile=readonly" in call.args for call in run.called("terraform", "init"))


def test_a_failed_first_apply_keeps_its_partial_state(bootstrap_dir: Path) -> None:
    def apply(call: Call) -> str:
        assert call.cwd is not None
        (call.cwd / "terraform.tfstate").write_text('{"partial": true}', encoding="utf-8")
        raise CommandError("`terraform apply` failed with exit code 1.")

    run = signed_in().on("aws", "s3api", "head-bucket", returns=254)
    run.on("terraform", "apply", returns=apply).on("terraform").on("aws")

    with pytest.raises(CommandError, match="state is saved in"):
        cli.bootstrap(run, {}, "owner@example.com", "")

    assert (bootstrap_dir / "terraform.tfstate.recovered").read_text(encoding="utf-8") == '{"partial": true}'
    assert run.called("terraform", "state", "push") == []


def test_a_failed_first_state_push_keeps_its_partial_state(bootstrap_dir: Path) -> None:
    def apply(call: Call) -> str:
        assert call.cwd is not None
        (call.cwd / "terraform.tfstate").write_text('{"partial": true}', encoding="utf-8")
        return ""

    run = signed_in().on("aws", "s3api", "head-bucket", returns=254)
    run.on("terraform", "apply", returns=apply)
    run.on("terraform", "state", "push", returns=CommandError("`terraform state push` failed with exit code 1."))
    run.on("terraform").on("aws")

    with pytest.raises(CommandError, match="state is saved in"):
        cli.bootstrap(run, {}, "owner@example.com", "")

    assert (bootstrap_dir / "terraform.tfstate.recovered").read_text(encoding="utf-8") == '{"partial": true}'


def test_a_ctrl_c_during_first_bootstrap_keeps_its_partial_state(bootstrap_dir: Path) -> None:
    def apply(call: Call) -> str:
        assert call.cwd is not None
        (call.cwd / "terraform.tfstate").write_text('{"partial": true}', encoding="utf-8")
        raise KeyboardInterrupt

    run = signed_in().on("aws", "s3api", "head-bucket", returns=254)
    run.on("terraform", "apply", returns=apply).on("terraform").on("aws")

    with pytest.raises(CommandError, match="state is saved in"):
        cli.bootstrap(run, {}, "owner@example.com", "")

    assert (bootstrap_dir / "terraform.tfstate.recovered").read_text(encoding="utf-8") == '{"partial": true}'
    assert run.called("terraform", "state", "push") == []


def test_bootstrap_refuses_to_run_again_over_a_saved_state(bootstrap_dir: Path) -> None:
    (bootstrap_dir / "terraform.tfstate.recovered").write_text('{"partial": true}', encoding="utf-8")
    run = FakeRun()

    with pytest.raises(CommandError, match="A saved bootstrap state exists"):
        cli.bootstrap(run, {}, "owner@example.com", "")

    assert run.calls == []


def test_later_bootstraps_apply_against_the_bucket(bootstrap_dir: Path) -> None:
    run = signed_in().on("aws", "s3api", "head-bucket", returns=0).on("terraform").on("aws")
    cli.bootstrap(run, {}, "owner@example.com", "arn:aws:ce::123456789012:anomalymonitor/x")
    [apply] = run.called("terraform", "apply")
    assert apply.cwd == bootstrap_dir
    assert "-var=anomaly_monitor_arn=arn:aws:ce::123456789012:anomalymonitor/x" in apply.args
    assert run.called("terraform", "state", "push") == []


def test_store_grafana_token_prompts_and_never_prints_the_token(capsys: pytest.CaptureFixture[str]) -> None:
    run = FakeRun().on("aws", "ssm", "put-parameter")
    cli.store_grafana_token(run, {}, "dev", ask_id=lambda prompt: "123456", ask_secret=lambda prompt: "glc_secret")
    args = run.calls[0].args
    assert base64.b64decode(args[args.index("--value") + 1]).decode() == "123456:glc_secret"
    captured = capsys.readouterr()
    assert "glc_secret" not in captured.out + captured.err


def test_store_grafana_token_never_leaks_the_token_outside_the_one_put_parameter_call() -> None:
    """The raw token, and its base64 basic-auth form, must appear in exactly one recorded call's
    args: the `aws ssm put-parameter` that stores it (Global Constraints: the token appears on a
    command line only in that single call)."""
    token = "glc_super_secret_token"
    run = FakeRun().on("aws", "ssm", "put-parameter")

    cli.store_grafana_token(run, {}, "dev", ask_id=lambda prompt: "123456", ask_secret=lambda prompt: token)

    b64_value = base64.b64encode(f"123456:{token}".encode()).decode()
    put_calls = run.called("aws", "ssm", "put-parameter")
    assert len(put_calls) == 1
    assert b64_value in put_calls[0].args
    for call in run.calls:
        for arg in call.args:
            assert token not in arg
            if call is not put_calls[0]:
                assert b64_value not in arg


def test_deploy_never_puts_the_grafana_token_in_any_call_args(stage_dir: Path) -> None:
    """The Grafana token (here SSM's raw parameter value "dG9rZW4=") reaches Terraform only as
    the TF_VAR_grafana_otlp_auth environment variable (stage_env), never as a command argument,
    for every call a full deploy makes."""
    run = deployable()

    cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)

    token = "dG9rZW4="
    for call in run.calls:
        assert all(token not in arg for arg in call.args)


def test_main_stops_with_a_sign_in_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(config, "PLUGIN_CACHE", tmp_path / "cache")
    run = FakeRun().on("aws", "configure", "export-credentials", returns=CommandError("expired"))
    assert cli.main(["preflight"], run=run) == 1
    assert "STOP: No usable AWS session" in capsys.readouterr().err


def test_main_preflight_passes_on_a_healthy_account(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stage_dir: Path
) -> None:
    monkeypatch.setattr(config, "PLUGIN_CACHE", tmp_path / "cache")
    assert cli.main(["preflight", "--stage", "dev"], run=healthy_account(signed_in())) == 0


def test_deploy_migrates_the_database_before_terraform_applies_the_new_code(stage_dir: Path) -> None:
    run = deployable()

    cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)

    [migrate] = run.called(sys.executable, "-m", "alembic")
    assert migrate.args[-2:] == ["upgrade", "head"]
    assert migrate.env is not None
    assert migrate.env["NETTRIAGE_MIGRATION_DATABASE_URL"] == OWNER_URL
    assert run.first(sys.executable) < run.first("terraform", "init") < run.first("terraform", "apply")


def test_deploy_never_puts_the_database_owner_url_in_any_call_args(stage_dir: Path) -> None:
    run = deployable()

    cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)

    for call in run.calls:
        assert all("owner-s3cret" not in arg for arg in call.args)


def test_deploy_stops_before_terraform_without_a_stored_database_url(stage_dir: Path) -> None:
    run = deployable()
    run.rules.insert(
        0,
        (
            ("aws", "ssm", "get-parameter", "--name", "/nettriage/dev/db/owner-url"),
            CommandError("ParameterNotFound"),
        ),
    )

    with pytest.raises(CommandError, match="just store-database-url dev"):
        cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)

    assert run.called(sys.executable) == []
    assert run.called("terraform", "apply") == []


def test_a_failed_migration_stops_the_deploy_before_terraform(stage_dir: Path) -> None:
    run = deployable()
    run.rules.insert(0, ((sys.executable,), CommandError("`alembic upgrade` failed with exit code 1.")))

    with pytest.raises(CommandError, match="Database migrations failed; nothing was deployed"):
        cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)

    assert run.called("terraform") == []


def test_store_database_url_checks_and_stores_it_without_printing_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run = FakeRun().on("aws", "ssm", "put-parameter")

    cli.store_database_url(run, {}, "dev", ask_secret=lambda prompt: f"  {OWNER_URL}  ")

    [put] = run.calls
    assert put.args[put.args.index("--name") + 1] == "/nettriage/dev/db/owner-url"
    assert put.args[put.args.index("--value") + 1] == OWNER_URL
    output = capsys.readouterr()
    assert "Stored /nettriage/dev/db/owner-url as a SecureString." in output.out
    assert "owner-s3cret" not in output.out + output.err


def test_store_database_url_refuses_a_pooled_string_and_stores_nothing() -> None:
    run = FakeRun()
    pooled = OWNER_URL.replace("ep-quiet-sun-123456.", "ep-quiet-sun-123456-pooler.")

    with pytest.raises(CommandError, match="direct"):
        cli.store_database_url(run, {}, "dev", ask_secret=lambda prompt: pooled)

    assert run.calls == []
```

Replace `tools/tests/test_deploy_preflight.py` with:
```python
import json
from pathlib import Path

import pytest

from tools.deploy import preflight
from tools.deploy.runner import CommandError
from tools.tests.deploy_fakes import FakeRun, ssm_names

LWA = "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerArm64:30"


def test_tfvars_are_read(tmp_path: Path) -> None:
    path = tmp_path / "terraform.tfvars"
    path.write_text('# a comment\nlwa_layer_arn = "x"\ngrafana_otlp_endpoint    = "https://g"\n', encoding="utf-8")
    assert preflight.read_tfvars(path) == {"lwa_layer_arn": "x", "grafana_otlp_endpoint": "https://g"}


def test_a_layer_from_another_region_fails_without_calling_aws() -> None:
    run = FakeRun()
    check = preflight.layer_check(run, {}, "Lambda Web Adapter layer", LWA.replace("eu-north-1", "us-east-1"))
    assert not check.ok and "eu-north-1" in check.detail
    assert run.calls == []


def test_an_arm64_layer_in_stockholm_passes() -> None:
    run = FakeRun().on(
        "aws", "lambda", "get-layer-version-by-arn", returns=json.dumps({"CompatibleArchitectures": ["arm64"]})
    )
    assert preflight.layer_check(run, {}, "Lambda Web Adapter layer", LWA).ok


def test_a_missing_or_x86_only_layer_fails() -> None:
    missing = FakeRun().on("aws", "lambda", "get-layer-version-by-arn", returns=CommandError("not found"))
    x86 = FakeRun().on(
        "aws", "lambda", "get-layer-version-by-arn", returns=json.dumps({"CompatibleArchitectures": ["x86_64"]})
    )
    assert not preflight.layer_check(missing, {}, "layer", LWA).ok
    assert not preflight.layer_check(x86, {}, "layer", LWA).ok


def test_the_grafana_parameter_must_exist() -> None:
    absent = FakeRun().on("aws", "ssm", "describe-parameters", returns="None\n")
    present = FakeRun().on("aws", "ssm", "describe-parameters", returns="/nettriage/dev/grafana-otlp-auth\n")
    assert not preflight.parameter_check(absent, {}, "dev").ok
    assert preflight.parameter_check(present, {}, "dev").ok


def test_a_placeholder_endpoint_fails() -> None:
    assert not preflight.endpoint_check({"grafana_otlp_endpoint": "<your Grafana Cloud OTLP endpoint>"}).ok
    assert preflight.endpoint_check({"grafana_otlp_endpoint": "https://otlp-gateway.grafana.net/otlp"}).ok


def test_a_denied_service_fails_only_its_own_check() -> None:
    run = FakeRun().on(
        "aws", "cloudfront", returns=CommandError("explicit deny in a service control policy")
    ).on("aws")
    checks = preflight.account_checks(run, {}, "123456789012")
    assert [check.name for check in checks if not check.ok] == ["CloudFront"]
    assert [check.name for check in checks] == [
        "Lambda in eu-north-1", "IAM", "CloudFront", "Budgets", "SSM in eu-north-1",
    ]


@pytest.mark.parametrize(
    "error",
    [
        "AccessDenied: User is not authorized",
        "explicit deny in a service control policy",
        "UnauthorizedOperation",
        "You are not authorized to perform this operation",
        "accessdenied exception",  # case-insensitive
    ],
)
def test_a_denial_error_gets_the_policy_hint(error: str) -> None:
    run = FakeRun().on("aws", "cloudfront", returns=CommandError(error)).on("aws")
    [check] = [c for c in preflight.account_checks(run, {}, "123456789012") if c.name == "CloudFront"]
    assert "AWS denied this call; the account's policies may have changed" in check.detail
    assert error in check.detail


@pytest.mark.parametrize(
    "error",
    ["Could not connect to the endpoint URL", "timed out", "Name or service not known"],
)
def test_a_non_denial_error_keeps_the_plain_message(error: str) -> None:
    run = FakeRun().on("aws", "cloudfront", returns=CommandError(error)).on("aws")
    [check] = [c for c in preflight.account_checks(run, {}, "123456789012") if c.name == "CloudFront"]
    assert "AWS denied this call" not in check.detail
    assert check.detail == error


def test_report_prints_each_check_and_fails_if_any_fails(capsys: pytest.CaptureFixture[str]) -> None:
    ok = preflight.report([preflight.Check("A", True), preflight.Check("B", False, "why")])
    out = capsys.readouterr().out
    assert not ok
    assert "PASS  A" in out
    assert "FAIL  B  why" in out


def test_a_layer_named_like_the_region_in_another_region_fails() -> None:
    run = FakeRun()
    check = preflight.layer_check(run, {}, "layer", "arn:aws:lambda:us-east-1:753240598075:layer:eu-north-1:5")
    assert not check.ok and "eu-north-1" in check.detail
    assert run.calls == []


def test_tfvars_values_with_inline_comments_are_read(tmp_path: Path) -> None:
    path = tmp_path / "terraform.tfvars"
    path.write_text('lwa_layer_arn = "x"  # pinned\ngrafana_otlp_endpoint = "https://g" // note\n', encoding="utf-8")
    assert preflight.read_tfvars(path) == {"lwa_layer_arn": "x", "grafana_otlp_endpoint": "https://g"}


def test_stage_checks_names_and_order() -> None:
    run = FakeRun().on(
        "aws", "lambda", "get-layer-version-by-arn", returns='{"CompatibleArchitectures": ["arm64"]}'
    ).on("aws", "ssm", "describe-parameters", returns=ssm_names()).on("aws")
    tfvars = {
        "lwa_layer_arn": "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerArm64:30",
        "otel_collector_layer_arn": "arn:aws:lambda:eu-north-1:753240598075:layer:OtelLayerArm64:1",
        "grafana_otlp_endpoint": "https://otlp-gateway.grafana.net/otlp",
    }
    checks = preflight.stage_checks(run, {}, "dev", "123456789012", tfvars)
    assert [check.name for check in checks] == [
        "Terraform state bucket", "Grafana token in SSM", "Database connection in SSM",
        "Grafana OTLP endpoint in terraform.tfvars",
        "Lambda Web Adapter layer", "OpenTelemetry collector layer",
    ]
    assert all(check.ok for check in checks)


def test_a_layer_without_listed_architectures_passes() -> None:
    run = FakeRun().on(
        "aws", "lambda", "get-layer-version-by-arn", returns="{}"
    )
    assert preflight.layer_check(run, {}, "layer", LWA).ok


def test_a_missing_database_connection_says_how_to_store_it() -> None:
    run = FakeRun().on(
        "aws", "ssm", "describe-parameters", returns=ssm_names(missing=["/nettriage/dev/db/owner-url"])
    )
    run.on("aws", "lambda", returns='{"CompatibleArchitectures": ["arm64"]}').on("aws")

    checks = {check.name: check for check in preflight.stage_checks(run, {}, "dev", "123456789012", {})}

    assert not checks["Database connection in SSM"].ok
    assert checks["Database connection in SSM"].detail == "missing; run: just store-database-url dev"
```

- [ ] **Step 2: Run them to see them fail**

Run: `just tools-test`
Expected: FAIL. You get `ImportError: cannot import name 'database' from 'tools.deploy'`, and the CLI tests fail on the missing `store_database_url` and migration step.

- [ ] **Step 3: Implement**

`tools/deploy/config.py`:
```python
"""Names and places the deploy commands agree on (spec §3.4 and Revision 2)."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REGION = "eu-north-1"
DEFAULT_PROFILE = "nettriage"
STAGES = ("dev",)
CI_WORKFLOW = "ci.yml"
CODEQL_WORKFLOW = "codeql.yml"
BACKEND_ARTIFACT = "backend-zip"
WEB_ARTIFACT = "web-dist"
BOOTSTRAP_DIR = REPO / "infra" / "bootstrap"
BOOTSTRAP_STATE_KEY = "bootstrap/terraform.tfstate"
PLUGIN_CACHE = Path.home() / ".terraform.d" / "plugin-cache"
# Neon in AWS Europe (Frankfurt), spec Revision 2 D2. The owner creates the project by hand
# (spec §13.2): the Neon Terraform provider isn't code-signed, and the owner's Windows host
# blocks unsigned executables.
NEON_HOST_SUFFIX = ".eu-central-1.aws.neon.tech"
# Database roles that get a login from the deploy. Plans 4, 5 and 7 add theirs.
APP_DB_ROLES = ("app_api",)
MIGRATIONS_CONFIG = REPO / "backend" / "alembic.ini"


def state_bucket(account_id: str) -> str:
    return f"nettriage-tfstate-{account_id}"


def state_key(stage: str) -> str:
    return f"envs/{stage}/terraform.tfstate"


def otlp_auth_parameter(stage: str) -> str:
    return f"/nettriage/{stage}/grafana-otlp-auth"


def stage_dir(stage: str) -> Path:
    return REPO / "infra" / "envs" / stage


def db_owner_url_parameter(stage: str) -> str:
    return f"/nettriage/{stage}/db/owner-url"


def db_role_url_parameter(stage: str, role: str) -> str:
    """For `app_api`: /nettriage/<stage>/db/app-api-url, which the function reads at cold start."""
    return f"/nettriage/{stage}/db/{role.replace('_', '-')}-url"
```

`tools/deploy/secrets.py`:
```python
"""Deploy secrets live in SSM Parameter Store as SecureStrings (spec §6.8): the Grafana OTLP
token and the database connection strings. They are never printed or written to disk."""

from __future__ import annotations

import base64
from collections.abc import Mapping

from tools.deploy.config import otlp_auth_parameter
from tools.deploy.runner import CommandError, Runner


def read_parameter(run: Runner, env: Mapping[str, str], name: str, store_with: str) -> str:
    """A SecureString's decrypted value. `store_with` is the command that stores it."""
    try:
        value = run(
            ["aws", "ssm", "get-parameter", "--name", name, "--with-decryption",
             "--query", "Parameter.Value", "--output", "text"],
            env=env,
        ).stdout.strip()
    except CommandError as exc:
        raise CommandError(f"Can't read {name} from SSM. Store it with: {store_with}") from exc
    if not value:
        raise CommandError(f"{name} is empty. Store it with: {store_with}")
    return value


def store_parameter(run: Runner, env: Mapping[str, str], name: str, value: str) -> None:
    """Create or replace a SecureString. The value is on the command line only here, and it's
    redacted from any error."""
    if not value.strip():
        raise CommandError("The value is empty; nothing was stored.")
    run(
        ["aws", "ssm", "put-parameter", "--name", name,
         "--type", "SecureString", "--overwrite", "--value", value.strip()],
        env=env,
        redact=[value.strip()],
    )


def parameter_exists(run: Runner, env: Mapping[str, str], name: str) -> bool:
    found = run(
        ["aws", "ssm", "describe-parameters", "--parameter-filters", f"Key=Name,Values={name}",
         "--query", "Parameters[0].Name", "--output", "text"],
        env=env,
    ).stdout.strip()
    return found == name


def read_otlp_auth(run: Runner, env: Mapping[str, str], stage: str) -> str:
    return read_parameter(
        run, env, otlp_auth_parameter(stage), f"just store-grafana-token {stage}"
    )


def store_otlp_auth(run: Runner, env: Mapping[str, str], stage: str, value: str) -> None:
    if not value.strip():
        raise CommandError("The token is empty; nothing was stored.")
    store_parameter(run, env, otlp_auth_parameter(stage), value)


def otlp_auth_value(instance_id: str, token: str) -> str:
    """Grafana Cloud's OTLP basic-auth value: base64("<instanceID>:<token>")."""
    instance_id, token = instance_id.strip(), token.strip()
    if not instance_id.isdigit() or not token:
        raise CommandError("Enter the numeric Grafana instance ID and a non-empty token.")
    return base64.b64encode(f"{instance_id}:{token}".encode()).decode()
```

`tools/deploy/database.py`:
```python
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
        raise CommandError(f"Database migrations failed; nothing was deployed. {exc}") from exc


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
        ssm.store_parameter(run, env, name, pooled_url(owner_url, role, password))
        created.append(role)
    return created
```

`tools/deploy/preflight.py`:
```python
"""Read-only checks that the account still allows what a deploy needs (spec Revision 2, D9)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from tools.deploy.config import REGION, db_owner_url_parameter, otlp_auth_parameter, state_bucket
from tools.deploy.runner import CommandError, Runner

TFVAR = re.compile(r'^\s*(\w+)\s*=\s*"([^"]*)"\s*(?:(?:#|//).*)?$', re.MULTILINE)
DENIED = re.compile(r"accessdenied|explicit deny|unauthorizedoperation|not authorized", re.IGNORECASE)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


def read_tfvars(path: Path) -> dict[str, str]:
    return dict(TFVAR.findall(path.read_text(encoding="utf-8"))) if path.exists() else {}


def _probe(run: Runner, env: Mapping[str, str], name: str, args: Sequence[str], hint: str = "") -> Check:
    try:
        run(args, env=env)
    except CommandError as exc:
        return Check(name, False, f"{hint} ({exc})" if hint else str(exc))
    return Check(name, True)


def _denial_probe(run: Runner, env: Mapping[str, str], name: str, args: Sequence[str]) -> Check:
    """Like `_probe`, but the "account's policies may have changed" hint only fits an actual
    denial; a network error or a bad endpoint should keep its own plain message."""
    check = _probe(run, env, name, args)
    if check.ok or not DENIED.search(check.detail):
        return check
    return Check(name, False, f"AWS denied this call; the account's policies may have changed (see the runbook) ({check.detail})")


def account_checks(run: Runner, env: Mapping[str, str], account_id: str) -> list[Check]:
    return [
        _denial_probe(run, env, f"Lambda in {REGION}", ["aws", "lambda", "list-functions", "--region", REGION, "--max-items", "1"]),
        _denial_probe(run, env, "IAM", ["aws", "iam", "list-roles", "--max-items", "1"]),
        _denial_probe(run, env, "CloudFront", ["aws", "cloudfront", "list-distributions", "--max-items", "1"]),
        _denial_probe(run, env, "Budgets", ["aws", "budgets", "describe-budgets", "--account-id", account_id, "--max-results", "1"]),
        _denial_probe(run, env, f"SSM in {REGION}", ["aws", "ssm", "describe-parameters", "--region", REGION, "--max-results", "1"]),
    ]


def layer_check(run: Runner, env: Mapping[str, str], name: str, arn: str) -> Check:
    parts = arn.split(":")
    if len(parts) < 4 or parts[3] != REGION:
        return Check(name, False, f"'{arn}' isn't a {REGION} layer ARN; fix it in terraform.tfvars")
    try:
        info = json.loads(
            run(["aws", "lambda", "get-layer-version-by-arn", "--arn", arn, "--region", REGION], env=env).stdout
        )
    except CommandError as exc:
        return Check(name, False, f"not found or not shared; check the layer's current version ({exc})")
    architectures = info.get("CompatibleArchitectures") or ["arm64", "x86_64"]  # none listed = any
    return Check(name, "arm64" in architectures, f"architectures: {', '.join(architectures)}")


def parameter_check(run: Runner, env: Mapping[str, str], stage: str) -> Check:
    return ssm_parameter_check(
        run, env, "Grafana token in SSM", otlp_auth_parameter(stage),
        f"missing; run: just store-grafana-token {stage}",
    )


def ssm_parameter_check(
    run: Runner, env: Mapping[str, str], label: str, name: str, missing: str
) -> Check:
    try:
        found = run(
            ["aws", "ssm", "describe-parameters", "--parameter-filters", f"Key=Name,Values={name}",
             "--query", "Parameters[0].Name", "--output", "text"],
            env=env,
        ).stdout.strip()
    except CommandError as exc:
        return Check(label, False, str(exc))
    if found != name:
        return Check(label, False, missing)
    return Check(label, True)


def endpoint_check(tfvars: Mapping[str, str]) -> Check:
    ok = tfvars.get("grafana_otlp_endpoint", "").startswith("https://")
    detail = "" if ok else "set grafana_otlp_endpoint in terraform.tfvars to your stack's https OTLP endpoint"
    return Check("Grafana OTLP endpoint in terraform.tfvars", ok, detail)


def stage_checks(
    run: Runner, env: Mapping[str, str], stage: str, account_id: str, tfvars: Mapping[str, str]
) -> list[Check]:
    bucket = state_bucket(account_id)
    return [
        _probe(run, env, "Terraform state bucket", ["aws", "s3api", "head-bucket", "--bucket", bucket],
               "missing; run: just bootstrap <your-email>"),
        parameter_check(run, env, stage),
        ssm_parameter_check(
            run, env, "Database connection in SSM", db_owner_url_parameter(stage),
            f"missing; run: just store-database-url {stage}",
        ),
        endpoint_check(tfvars),
        layer_check(run, env, "Lambda Web Adapter layer", tfvars.get("lwa_layer_arn", "")),
        layer_check(run, env, "OpenTelemetry collector layer", tfvars.get("otel_collector_layer_arn", "")),
    ]


def report(checks: list[Check]) -> bool:
    for check in checks:
        line = f"{'PASS' if check.ok else 'FAIL'}  {check.name}"
        print(f"{line}  {check.detail}" if check.detail else line)
    return all(check.ok for check in checks)
```

`tools/deploy/__main__.py`:
```python
"""Owner-run infrastructure commands (spec §11.5, ADR 0013).

  python -m tools.deploy preflight [--stage dev]
  python -m tools.deploy bootstrap --budget-email you@example.com [--anomaly-monitor-arn ARN]
  python -m tools.deploy store-grafana-token [--stage dev]
  python -m tools.deploy plan [--stage dev] [--no-comment]
  python -m tools.deploy deploy [--stage dev]

Every command uses the owner's short-lived `aws login` session: profile "nettriage", or
$NETTRIAGE_AWS_PROFILE, or --profile before the command name.
"""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

from tools import smoke
from tools.deploy import database, config, github, gitguards, preflight, publish, secrets, session, terraform
from tools.deploy import runner
from tools.deploy.runner import CommandError, Runner

SmokeMain = Callable[[list[str]], int]


def run_preflight(run: Runner, env: Mapping[str, str], stage: str | None) -> bool:
    account = session.account_id(run, env)
    print(f"AWS account {account}, region {config.REGION}")
    checks = preflight.account_checks(run, env, account)
    if stage is not None:
        tfvars = preflight.read_tfvars(config.stage_dir(stage) / "terraform.tfvars")
        checks += preflight.stage_checks(run, env, stage, account, tfvars)
    return preflight.report(checks)


def stage_env(run: Runner, env: Mapping[str, str], stage: str, sha: str, lambda_zip: Path) -> dict[str, str]:
    """Terraform's inputs for a stage, passed as environment variables, never as arguments."""
    return {
        **env,
        "TF_VAR_lambda_zip_path": str(lambda_zip),
        "TF_VAR_app_version": sha,
        "TF_VAR_grafana_otlp_auth": secrets.read_otlp_auth(run, env, stage),
    }


def bootstrap(run: Runner, env: Mapping[str, str], budget_email: str, anomaly_monitor_arn: str) -> None:
    recovered = config.BOOTSTRAP_DIR / "terraform.tfstate.recovered"
    if recovered.exists():
        raise CommandError(
            f"A saved bootstrap state exists at {recovered}. Don't bootstrap again; "
            "ask for help to push it into the state bucket."
        )
    if not run_preflight(run, env, stage=None):
        raise CommandError("Preflight failed; nothing was created.")
    bucket = config.state_bucket(session.account_id(run, env))
    variables = [f"-var=budget_email={budget_email}", f"-var=anomaly_monitor_arn={anomaly_monitor_arn}"]
    if run(["aws", "s3api", "head-bucket", "--bucket", bucket], env=env, check=False).returncode == 0:
        terraform.init(run, env, config.BOOTSTRAP_DIR, bucket, config.BOOTSTRAP_STATE_KEY)
        terraform.apply(run, env, config.BOOTSTRAP_DIR, variables)
        return
    # First run: the state bucket doesn't exist yet. Apply with local state in a scratch copy
    # (without backend.tf), then push that state into the bucket the apply just created.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        work = Path(scratch) / "bootstrap"
        shutil.copytree(
            config.BOOTSTRAP_DIR, work,
            ignore=shutil.ignore_patterns("backend.tf", ".terraform", "tests", "*.tfstate*"),
        )
        local_state = work / "terraform.tfstate"
        run(["terraform", "init", "-input=false", "-no-color", "-lockfile=readonly"], env=env, cwd=work)
        try:
            terraform.apply(run, env, work, variables)
            terraform.init(run, env, config.BOOTSTRAP_DIR, bucket, config.BOOTSTRAP_STATE_KEY)
            run(["terraform", "state", "push", "-no-color", str(local_state)], env=env, cwd=config.BOOTSTRAP_DIR)
        except BaseException as exc:
            # Ctrl+C (KeyboardInterrupt) counts too: never lose state with the scratch directory.
            if not local_state.exists():
                raise
            shutil.copy2(local_state, recovered)
            raise CommandError(
                f"The first bootstrap didn't finish; its state is saved in {recovered} (git-ignored). "
                "Keep that file and ask for help before retrying."
            ) from exc
    print(f"Bootstrap state is now in s3://{bucket}/{config.BOOTSTRAP_STATE_KEY}")


def plan_comment(stage: str, sha: str, changes: list[str]) -> str:
    lines = [f"### Terraform plan: {stage} ({sha[:7]})", ""]
    lines += ["```", *changes, "```"] if changes else ["No changes."]
    return "\n".join(lines) + "\n"


def plan(run: Runner, env: Mapping[str, str], stage: str, post_comment: bool) -> list[str]:
    sha = gitguards.require_clean_tree(run)
    ci = github.require_success(run, config.CI_WORKFLOW, sha)
    bucket = config.state_bucket(session.account_id(run, env))
    workdir = config.stage_dir(stage)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        dist = github.download(run, ci.run_id, config.BACKEND_ARTIFACT, Path(scratch) / "dist")
        tf_env = stage_env(run, env, stage, sha, dist / "backend.zip")
        gitguards.require_unchanged_since(run, sha, "plan")
        terraform.init(run, tf_env, workdir, bucket, config.state_key(stage))
        changes = terraform.plan(run, tf_env, workdir)
        body = plan_comment(stage, sha, changes)
        print(body)
        if post_comment:
            comment_file = Path(scratch) / "plan.md"
            comment_file.write_text(body, encoding="utf-8")
            github.comment_on_pr(run, comment_file)
    return changes


def deploy(run: Runner, env: Mapping[str, str], stage: str, smoke_main: SmokeMain = smoke.main) -> None:
    sha = gitguards.require_clean_main(run)
    ci = github.require_success(run, config.CI_WORKFLOW, sha, event="push")
    github.require_success(run, config.CODEQL_WORKFLOW, sha, event="push")
    if not run_preflight(run, env, stage):
        raise CommandError("Preflight failed; nothing was deployed.")
    bucket = config.state_bucket(session.account_id(run, env))
    workdir = config.stage_dir(stage)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        dist = github.download(run, ci.run_id, config.BACKEND_ARTIFACT, Path(scratch) / "dist")
        web = github.download(run, ci.run_id, config.WEB_ARTIFACT, Path(scratch) / "web")
        tf_env = stage_env(run, env, stage, sha, dist / "backend.zip")
        gitguards.require_unchanged_since(run, sha, "deploy")
        migrate_database(run, env, stage)
        terraform.init(run, tf_env, workdir, bucket, config.state_key(stage))
        terraform.apply(run, tf_env, workdir)
        outputs = terraform.outputs(run, tf_env, workdir)
        publish.publish_web(run, env, web, outputs["web_bucket"], outputs["distribution_id"])
    code = smoke_main(
        ["--base-url", f"https://{outputs['cloudfront_domain']}",
         "--function-url", outputs["function_url"],
         "--version", sha]
    )
    if code != 0:
        raise CommandError(
            "Smoke tests failed (see the FAIL lines above). To roll back, revert the PR on GitHub, "
            "then deploy main again."
        )
    print(f"Deployed {sha[:7]} to {stage}: https://{outputs['cloudfront_domain']}")


def migrate_database(run: Runner, env: Mapping[str, str], stage: str) -> None:
    """Migrations run before the new code deploys (spec §5.8), so code only meets a schema it
    was written for; migrations stay backward compatible (expand, migrate, contract)."""
    owner_url = secrets.read_parameter(
        run, env, config.db_owner_url_parameter(stage), f"just store-database-url {stage}"
    )
    database.migrate(run, env, owner_url)
    created = database.ensure_role_logins(run, env, stage, owner_url)
    print("Database migrated." + (f" New logins: {', '.join(created)}." if created else ""))


def store_database_url(
    run: Runner,
    env: Mapping[str, str],
    stage: str,
    ask_secret: Callable[[str], str] = getpass.getpass,
) -> None:
    url = database.check_owner_url(
        ask_secret("Neon connection string for the database owner, direct (hidden): ")
    )
    secrets.store_parameter(run, env, config.db_owner_url_parameter(stage), url)
    print(f"Stored {config.db_owner_url_parameter(stage)} as a SecureString.")


def store_grafana_token(
    run: Runner,
    env: Mapping[str, str],
    stage: str,
    ask_id: Callable[[str], str] = input,
    ask_secret: Callable[[str], str] = getpass.getpass,
) -> None:
    instance_id = ask_id("Grafana Cloud OTLP instance ID (a number): ")
    token = ask_secret("Grafana Cloud token with metrics:write, logs:write, traces:write (hidden): ")
    secrets.store_otlp_auth(run, env, stage, secrets.otlp_auth_value(instance_id, token))
    print(f"Stored {config.otlp_auth_parameter(stage)} as a SecureString.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.deploy", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default=os.environ.get("NETTRIAGE_AWS_PROFILE", config.DEFAULT_PROFILE))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "store-grafana-token", "store-database-url", "plan", "deploy"):
        command = commands.add_parser(name)
        command.add_argument("--stage", choices=config.STAGES, default="dev")
    commands.choices["plan"].add_argument("--no-comment", action="store_true")
    boot = commands.add_parser("bootstrap")
    boot.add_argument("--budget-email", required=True)
    boot.add_argument("--anomaly-monitor-arn", default="")
    return parser


def main(argv: list[str] | None = None, run: Runner = runner.run) -> int:
    args = build_parser().parse_args(argv)
    try:
        env = session.aws_env(run, args.profile)
        config.PLUGIN_CACHE.mkdir(parents=True, exist_ok=True)
        env.setdefault("TF_PLUGIN_CACHE_DIR", str(config.PLUGIN_CACHE))
        if args.command == "preflight":
            return 0 if run_preflight(run, env, args.stage) else 1
        if args.command == "bootstrap":
            bootstrap(run, env, args.budget_email, args.anomaly_monitor_arn)
        elif args.command == "store-grafana-token":
            store_grafana_token(run, env, args.stage)
        elif args.command == "store-database-url":
            store_database_url(run, env, args.stage)
        elif args.command == "plan":
            plan(run, env, args.stage, post_comment=not args.no_comment)
        else:
            deploy(run, env, args.stage)
    except CommandError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

In `justfile`, add this after the `store-grafana-token` recipe:
```
# Store the Neon owner's connection string in SSM (prompts; never printed)
store-database-url stage="dev":
    uv run --project backend python -m tools.deploy store-database-url --stage {{stage}}
```

In `CLAUDE.md`, replace `` `just store-grafana-token` or `just deploy-*`. `` with `` `just store-grafana-token`, `just store-database-url` or `just deploy-*`. ``

- [ ] **Step 4: Run the tests and checks**

Run: `just lint test tools-test pin-check cloud-check`
Expected:
- lint is clean;
- backend `200 passed`;
- tools `209 passed`, which adds 12 database tests, 6 CLI tests and 1 preflight test;
- every action is pinned, and CI holds no cloud access.

- [ ] **Step 5: Commit**

```bash
git add tools/deploy tools/tests justfile CLAUDE.md
git commit -m "feat(tools): store the Neon owner URL, migrate before Terraform, create role logins

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The owner's runbook, ADR 0002 and the spec note

**Files:**
- Modify: `docs/runbooks/setup-and-deploy.md`, `docs/adr/0002-neon-postgres.md`, `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`

- [ ] **Step 1: Runbook: add the preflight line and A7**

In A5, replace:
```
Every line must say `PASS`, including the Terraform state bucket, the Grafana token in SSM, the
Grafana endpoint and both Lambda layers.
```
with:
```
Every line must say `PASS`, including the Terraform state bucket, the Grafana token in SSM, the
database connection in SSM (after A7), the Grafana endpoint and both Lambda layers.
```

Insert this new section directly before `### A6. Set up the GitHub repository (once)`:
````markdown
### A7. Create the Neon database (once)
The database runs on Neon's free plan. You create the project in Neon's console; the deploy
then creates the tables and a separate login for the app. (Terraform can't manage Neon from this
machine: Neon's Terraform provider isn't code-signed, and Windows policy here blocks unsigned
programs.)
1. Sign in at **console.neon.tech** and click **New project**.
2. Fill in:
   - **Project name:** `nettriage-dev`
   - **Postgres version:** 17
   - **Cloud provider:** AWS
   - **Region:** AWS Europe Central 1 (Frankfurt)

   Leave everything else as it is and click **Create project**.
3. On the project dashboard, click **Connect**. In the dialog:
   - leave **Branch** `main`, **Database** `neondb` and **Role** `neondb_owner` as they are;
   - turn **Connection pooling** off, so the host has no `-pooler` in it;
   - click **Show password**, then **Copy snippet** (or copy the `postgresql://…` string).
4. Store it in AWS (it never goes into Git, GitHub or chat):
   ```bash
   just store-database-url dev
   ```
   Paste the string and press Enter; nothing is shown. Expected:
   `Stored /nettriage/dev/db/owner-url as a SecureString.`
5. Run `just preflight`: `PASS  Database connection in SSM` appears.

The next `just deploy-dev` runs the migrations, gives the app's role `app_api` a generated
password, and stores its connection string as `/nettriage/dev/db/app-api-url`. It prints
`Database migrated. New logins: app_api.` the first time and `Database migrated.` afterwards.
````

- [ ] **Step 2: Runbook: add the new messages to Part C**

In the "Messages and fixes" table, insert these rows directly after the `FAIL  Grafana token in SSM` row:
```markdown
| `FAIL  Database connection in SSM` | Run A7 |
| `STOP: That isn't a Postgres connection string …`, `STOP: The database must be a Neon project in AWS Europe Central 1 (Frankfurt) …` or `STOP: Use the direct connection string …` | Copy the string again as in A7 step 3, then rerun `just store-database-url dev` |
| `STOP: Can't read /nettriage/dev/db/owner-url from SSM. …` | Run A7 |
| `STOP: Database migrations failed; nothing was deployed. …` | Send the output to Claude. Nothing in AWS changed |
| `STOP: Couldn't give the database role app_api a login …` | Check the stored string (A7), then send the output to Claude |
```

- [ ] **Step 3: ADR 0002 and the spec**

In `docs/adr/0002-neon-postgres.md`:
- Change the status line to `- Status: Accepted; provisioning amended 2026-09-28 (Plan 3a)`.
- Append this bullet to Consequences:
```markdown
- **Provisioning (amended in Plan 3a):** the owner creates each stage's Neon project in the Neon
  console, which is spec §13.2's fallback. Neon's Terraform provider isn't code-signed, and the
  owner's Windows machine blocks unsigned executables, so Terraform can't run it there. The
  owner stores the owner role's connection string once (`just store-database-url`). Each deploy
  then runs the migrations and gives every function's role a generated password, stored as its
  own SSM SecureString. Tables, row-level security and grants stay in reviewed migrations.
```

In the spec's §13.2 table, replace the row `| Neon Terraform provider reliability | Create the projects by hand and document it |` with:
```markdown
| Neon Terraform provider reliability | Create the projects by hand and document it (taken in Plan 3a: the provider isn't code-signed and the owner's machine blocks unsigned executables; see ADR 0002) |
```

- [ ] **Step 4: Check and commit**

Run: `just lint test tools-test` → the same counts as Task 3 (backend 200, tools 209).
Check that every message the runbook quotes matches the code: run `grep -n "CommandError(\|print(" tools/deploy/database.py tools/deploy/__main__.py`.

```bash
git add docs/runbooks/setup-and-deploy.md docs/adr/0002-neon-postgres.md docs/superpowers/specs/2026-09-26-nettriage-m1-design.md
git commit -m "docs: create the Neon database by hand (runbook A7) and why

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5 (Claude, then the owner): Pull request, Neon and the first migrated deploy

- [ ] **Step 1 (Claude):** Run `just lint test tools-test edge-test web-check tf-check pin-check cloud-check detection-report`; everything must pass. Get a final review of the whole branch, fix what it finds, then push `plan-3a/data-foundation`. Open the PR and watch CI: the `backend` job now runs the database tests against Postgres 17.
- [ ] **Step 2 (owner):** Runbook **A7**. Create the Neon project `nettriage-dev` (Postgres 17, AWS Europe Central 1), copy the **direct** connection string, run `just store-database-url dev`, then `just preflight`. Expected: `PASS  Database connection in SSM`.
- [ ] **Step 3 (owner):** Review the PR, then squash-merge it.
- [ ] **Step 4 (owner):** Runbook B2 (`just deploy-dev`). Expected output:
  - `Database migrated. New logins: app_api.` before Terraform's plan;
  - Terraform: no changes;
  - the eight smoke `PASS` lines.
- [ ] **Step 5 (owner):** In the Neon console, open **Tables**. You see `users`, `organizations`, `memberships`, `invitations`, `audit_log` and `alembic_version`, all empty.

## Plan 3a is done when

- [ ] `just lint test tools-test` passes locally, and CI passes with the Postgres service.
- [ ] The dev database on Neon has the schema, and `/nettriage/dev/db/app-api-url` exists in SSM.
- [ ] PR merged through review, with every thread resolved.

## Spec coverage of this plan

| Spec | Covered here |
|---|---|
| §5.1 Neon Postgres store; §3.2 Frankfurt, pooled endpoint | Tasks 1, 3 (connection rules), Task 4 (A7) |
| §5.2 users, organizations, memberships, invitations, audit_log, their constraints, index and cascades | Task 2 (0001) |
| §5.3 three-layer tenant isolation, and the dedicated no-filter test | Task 2 (0002, `tenant_transaction`, `test_tenant_isolation.py`); repositories come in Plan 3b |
| §5.4 `app_api` grants; no superuser, BYPASSRLS or ownership; the append-only audit trigger | Task 2 |
| §5.8 Alembic with explicit SQL; build from nothing, apply, roll back | Task 2 (`test_migrations.py`), Task 3 (migrations in the deploy) |
| §6.8 TLS verify-full, `prepare_threshold=None`, passwords as SSM SecureStrings | Tasks 1–3 |
| §11.4 RLS cross-tenant isolation suite; integration tests on real Postgres | Tasks 1, 2 |
| §13.2 fallback: Neon created by hand | Tasks 3, 4 |
| §5.2 uploads, findings, AI and reference tables; other roles | Not in 3a (Plans 4, 5, 7) |
| §6 Cognito, sessions, CSRF, permissions, rate limits, the org/member/invitation API | Plan 3b |
