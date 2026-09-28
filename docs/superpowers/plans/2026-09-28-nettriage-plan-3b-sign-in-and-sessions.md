# NetTriage Plan 3b: Sign-in and Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let people sign up and sign in to NetTriage with mandatory TOTP MFA:
- Cognito's managed login, driven by the API as a backend-for-frontend, so the browser only ever holds an opaque session cookie;
- sessions, sign-in state and GCRA rate limits in the DynamoDB `runtime` table;
- CSRF checks on every state-changing request, and `GET /api/v1/me`;
- row-level security on `users`, and the hardening items from Plan 3a's final review.

**Architecture:**
- `GET /api/auth/login` stores the sign-in's PKCE verifier, nonce and return path in DynamoDB for 5 minutes, binds the sign-in to the browser with a short `__Host-sign-in` cookie, and redirects to Cognito.
- `GET /api/auth/callback` exchanges the code with the client secret and verifies the ID token against Cognito's keys. It then finds or creates the user through a `SECURITY DEFINER` database function, starts a session and sets the `__Host-session` cookie.
- Every route declares `Public(policy)` or `SignedIn`. `SignedIn` validates the session (failing closed), runs the CSRF checks on state-changing methods, and applies the per-user rate limits.
- The function reads its Cognito settings, client secret and database URL from SSM once per cold start. Terraform creates the Cognito pool, the app client, those SSM parameters and the DynamoDB table.

**Tech Stack:** Python 3.14, FastAPI, boto3 (DynamoDB, SSM), PyJWT with `cryptography` (RS256), httpx, SQLAlchemy 2 Core with psycopg 3 · moto and DynamoDB Local for tests · Amazon Cognito (Essentials, managed login), DynamoDB, SSM Parameter Store · Terraform.

**Spec:** `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (revision 2). Read it alongside this plan; section numbers (§) refer to it. This plan implements:
- §4.1 (the sign-in flow);
- §5.3 for `users` and §5.5 (the `runtime` table);
- §6.1 (Cognito), §6.2 (sessions, cookies, CSRF), §6.3 (the first login) and §6.5 (rate limiting);
- §6.8 (the function's secrets from SSM);
- §7's auth routes and `GET /api/v1/me`;
- §9.2's `signups`, `csrf.failed` and `ratelimit.limited` counters, §9.3's log rules and §9.4's `auth.*` and `ratelimit.limited` events;
- §11.4's CSRF, session, rate-limiter concurrency and log-redaction suites.

**Plan series:** Plan 3 of 7 ("data, identity and access") is split in three:
- **3a: the data foundation** (merged);
- **3b (this plan): sign-in and sessions;**
- **3c: organizations and access control.** That covers the org, member and invitation API, §6.4's permission matrix, quotas, `Idempotency-Key`, the 64 KB request-body limit, and their audit events.

The web pages for all of this come in Plan 6.

**Branch:** `plan-3b/sign-in-and-sessions`, from `main` at `b103c79` or later.

## Global Constraints

- **Stack.** Python **3.14**. New runtime dependencies: `boto3`, `pyjwt[crypto]` and `httpx`. New dev dependencies: `moto[dynamodb,ssm]` and `types-boto3[dynamodb,ssm]`. mypy `--strict` and Ruff pass on `src` and `tests`. Work test-first.
- **Commands on this Windows machine.** Run Python tools as modules (`uv run python -m …`, `uv run --project backend python -m …`). Host policy blocks some uv launchers and every unsigned executable outside trusted tools.
- **Region.** DynamoDB, Cognito, SSM and Lambda live in **eu-north-1** (Revision 2, R1).
- **Cognito (§6.1).**
  - One user pool per stage on the **Essentials** plan, with self-service sign-up and a verified email address.
  - The username is the email address, and passwords are at least **12** characters.
  - MFA is **required, TOTP only**, with no SMS.
  - `PreventUserExistenceErrors` is on, and threat protection is off.
  - The app client is confidential and uses only the authorization-code grant with PKCE (S256).
  - It asks for the scopes `openid email profile` and allows only the stage's exact callback and logout URLs.
- **Sessions (§6.2).**
  - The cookie is `__Host-session`, holding 32 random bytes, base64url-encoded. Its attributes are `Secure; HttpOnly; SameSite=Lax; Path=/`, with no `Domain`.
  - The server stores only the value's SHA-256.
  - A session ends after **60** idle minutes or **12** hours in total.
  - Every login creates a new session. Logout deletes it and returns Cognito's logout URL, and "sign out everywhere" deletes every session the user has.
- **CSRF (§6.2).** A state-changing request needs all three:
  - `X-CSRF-Token` equal to the session's token, which `GET /api/v1/me` delivers;
  - `Sec-Fetch-Site` of `same-origin` or `none`;
  - an `Origin` equal to the app's origin, when the header is present.
- **The `runtime` table (§5.5).**
  - The partition key is `pk` (string) and the TTL attribute is `expires_at` (epoch seconds).
  - Capacity is provisioned: dev gets 3 RCU and 3 WCU, prod 10 and 10.
  - Sessions **fail closed** (401 or 503), and rate limits **fail open**.
- **Rate limits (§6.5).**
  - These are the seven policies of §6.5's table.
  - The subject is the user ID for signed-in routes, and for public routes the client IP from `CloudFront-Viewer-Address`, never `X-Forwarded-For`.
  - A limited request gets `429` with `Retry-After`. Limited routes send `RateLimit-Policy` and `RateLimit`.
- **Errors.** API routes answer with RFC 9457 Problem Details. `GET /api/auth/login` and `/callback` are browser navigations, so their failures redirect.
- **Logs (§9.3).** Never log tokens, cookies, emails, session IDs, sign-in `state`, nonces or authorization codes.
- **Secrets.**
  - The Cognito client secret exists only in Terraform's state and in an SSM SecureString.
  - The function gets parameter **names** through its environment, never values, and reads the values at cold start.
- **CI still holds no cloud access (ADR 0013).**
  - Its DynamoDB Local is a service container on the runner.
  - The dummy credentials DynamoDB Local accepts live in test code, never in a workflow; `tools/check_no_cloud_access.py` forbids AWS credential variables in workflows.
- **Owner-only commands.** Claude never runs `aws login`, `just store-*` or `just deploy-*`.
- **Commits.** Use Conventional Commits. Every commit made by Claude ends with the trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Decisions this plan makes

1. **Plan 3 is split three ways.** The owner chose on 2026-09-28 to deliver sign-in before the organization API, as 3b and 3c. Each is a PR with its own deploy and something to try.
2. **`users` gets row-level security (the owner's decision, 2026-09-28).**
   - A user sees their own row. In an org's transaction, they also see the members of that org, but only if they are one of them.
   - The table has `ENABLE` but not `FORCE`, so its owner bypasses the policies. Sign-in calls `sign_in_user(new_id, sub, email)`, a `SECURITY DEFINER` function owned by the owner, with `search_path` pinned to `pg_catalog, public, pg_temp`. That function finds or creates the user before anyone knows who the user is.
   - `app_api` can't insert users. It can change only its own `display_name`.
3. **A sign-in is bound to the browser that started it** (this amends §4.1).
   - The login response sets a 5-minute `__Host-sign-in` cookie holding the `state`, and the callback accepts only a matching `state`.
   - PKCE alone doesn't stop login CSRF. The verifier is stored per `state`, so an attacker who starts a sign-in could otherwise send a victim the callback link and sign them in to the attacker's account.
4. **The rate limiter uses only conditional updates** (this amends §6.5 and ADR 0006).
   - §6.5's read-then-conditional-write design retries 3 times on a conflict and then fails open. Under the 100-parallel-request test it would let more requests through than the limit.
   - Instead, a new or idle key is set to now + T. Otherwise `tat` grows by T, on condition that `tat − now ≤ τ`.
   - A failed condition returns the stored item (`ReturnValuesOnConditionCheckFailure`), which says whether the request is over the limit.
5. **The concurrency test runs against DynamoDB Local, in CI only.** moto's in-process DynamoDB has no lock around conditional writes, so every other test uses moto, which is fast and needs nothing installed. This machine can't run DynamoDB Local (no Docker, no Java), so locally the test is skipped with that reason.
6. **Sign-in failures redirect to `/?sign_in=<reason>`**, where the reason is `expired`, `failed`, `disabled` or `unavailable`. `login` and `callback` are page loads, not API calls; Plan 6's landing page shows the message.
7. **Settings and secrets come from SSM at cold start.**
   - Terraform writes Cognito's settings as a JSON String parameter and the client secret as a SecureString. The deploy already writes `app_api`'s database URL (Plan 3a).
   - The function's environment holds only the parameter names.
   - This also avoids a Terraform cycle: the callback URL needs CloudFront's domain, CloudFront needs the Function URL, and the function would need the callback settings.
8. **The password policy is at least 12 characters, with no composition rules** (NIST SP 800-63B). TOTP is mandatory on top.
9. **Cognito's tokens live as briefly as Cognito allows.** ID and access tokens get 5 minutes and refresh tokens 60, and the API discards them all after checking the ID token. The sign-in session gets Cognito's maximum of 15 minutes, because a first sign-up also verifies the email and sets up the authenticator app.
10. **Public routes are limited per client IP, taken from `CloudFront-Viewer-Address`.**
    - IPv6 clients are limited per /64.
    - Requests without the header aren't IP-limited. Only calls that bypass CloudFront lack it: the Lambda Web Adapter's readiness check, and local runs.
    - `GET /api/health` is `public.ip`, as §7 says.
11. **Audit writes are best-effort.** A failed write is logged as `audit_write_failed` and never fails the request it describes. `ratelimit.limited` is sampled through a `RLAUDIT#` item, at most once per subject and policy per minute.
12. **HTTP and AWS client loggers are pinned at WARNING.** At DEBUG, botocore logs every DynamoDB item it writes, which includes sessions, CSRF tokens and sign-in state.
13. **Plan 3a's review items land here:**
    - a `BEFORE TRUNCATE` trigger on `audit_log`;
    - the migration stops if `app_api` has superuser, `BYPASSRLS`, `CREATEROLE`, `CREATEDB` or `REPLICATION`, or belongs to another role;
    - column-level INSERT grants;
    - `REVOKE TEMPORARY … FROM PUBLIC`;
    - a 10-second `connect_timeout`.

    The invitation lookup by token and the last-owner rule's `SELECT … FOR UPDATE` move to 3c, with the endpoints that need them.
14. **The Lambda zip is checked against Lambda's limits** of 50 MB zipped and 250 MB unzipped. With boto3 bundled it's about 38 MB.
15. **FastAPI's Swagger OAuth redirect route is turned off.** It was registered at `/docs/oauth2-redirect`, outside `/api`, and nothing uses it.

## Review Focus

1. **A callback link opened in a browser that didn't start the sign-in (login CSRF).** It must be refused, and no session may be created. Test: Task 5 `test_a_sign_in_finished_in_another_browser_is_refused`.
2. **Many parallel requests for one rate-limit key.** Exactly the allowed number may pass, with none failing open. Test: Task 3 `test_exactly_the_burst_passes_when_100_requests_race` (CI, DynamoDB Local).
3. **DynamoDB unreachable.** Session checks must fail closed with 503, and rate limits must fail open and be counted. Tests: Task 5 `test_an_unreadable_session_store_fails_closed` and `test_a_broken_limiter_lets_requests_through_and_counts_it`.
4. **Logging turned up to DEBUG during sign-in and sign-out.** No email, session ID, CSRF token, state, nonce or code may appear in any line. Test: Task 5 `test_a_sign_in_and_out_log_no_secret_and_no_email`.
5. **The migration owner isn't a superuser, as on Neon.** The sign-in function must still find existing users. Test: Task 1 `test_sign_in_works_when_the_owner_role_is_not_a_superuser`.

## Owner prerequisites

- **Nothing is needed to build or review this plan.** Tests use moto, a fake Cognito and the local Postgres.
- **Nothing new is needed before the first deploy.** Terraform creates the Cognito pool, the app client and the DynamoDB table.
- **To try signing in afterwards** (runbook B4, which Task 6 adds), you need an authenticator app on your phone: Microsoft Authenticator or Google Authenticator.

---

### How to read the code blocks

Every code step uses one of three forms, so a helper (or a careful human) can apply it exactly:
- A line that is only a path in backticks followed by a colon (`` `backend/…/users.py`: ``), then a fenced block: write that file with exactly the block's content, creating or replacing it.
- ``In `path`, replace:`` with a fenced block, then `with:` and a second fenced block: the first block occurs exactly once in the file, and becomes the second.
- ``Append to `path`:`` with a fenced block: add the block to the end of the file. The block starts with the blank lines that separate it from what's already there.

---

### Task 1: Row-level security on `users`, the sign-in function, and Plan 3a's hardening items

**Files:**
- Create: `backend/migrations/versions/0003_sign_in_hardening.py`, `backend/src/nettriage/application/__init__.py`, `backend/src/nettriage/application/audit.py`, `backend/src/nettriage/adapters/users.py`, `backend/src/nettriage/adapters/audit_log.py`
- Modify: `backend/src/nettriage/adapters/postgres.py`, `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (§5.3)
- Test: `backend/tests/integration/test_users.py`, `backend/tests/integration/test_audit_log.py`; modify `backend/tests/integration/test_tenant_isolation.py` and `backend/tests/unit/adapters/test_postgres.py`

**Interfaces:**
- Consumes (Plan 3a):
  - `tenant_transaction(engine, *, org_id=None, user_id=None)` and `create_database_engine(url, *, pool_size=2)`;
  - the test harness's `Database(url, admin, app_api)` and `tenantdata.add_user/add_org/add_member/add_tenant`.
- Produces:
  - In the database:
    - the function `sign_in_user(p_new_id uuid, p_sub text, p_email text) RETURNS TABLE (user_id uuid, created boolean, disabled boolean)`, which only `app_api` may execute;
    - the policies `self_or_fellow_member` (SELECT) and `self_update` (UPDATE) on `users`.
  - In `nettriage.adapters.users`:
    - `SignedInUser(user_id: UUID, created: bool, disabled: bool)`;
    - `Membership(org_id: UUID, name: str, slug: str, role: str)`;
    - `Me(user_id: UUID, email: str, display_name: str | None, memberships: tuple[Membership, ...])`;
    - `sign_in_user(engine, *, sub: str, email: str) -> SignedInUser` and `load_me(engine, user_id: UUID) -> Me | None`.
  - In `nettriage.application.audit`: `AuditEvent(action, outcome, actor_type, actor_user_id=None, org_id=None, target_type=None, target_id=None, ip=None, user_agent=None, request_id=None, trace_id=None, details={})`, plus the `Outcome` and `ActorType` literals.
  - In `nettriage.adapters.audit_log`: `record(engine, event: AuditEvent) -> None`, which truncates `user_agent` to 256 characters.
  - `nettriage.adapters.postgres.CONNECT_TIMEOUT_SECONDS = 10`. `connect_args` now always includes `connect_timeout`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/integration/test_users.py`:
```python
"""Just-in-time sign-in and row-level security on `users` (spec §5.3, §6.3). Queries run as
`app_api`, the role the API uses; seeding uses the superuser engine."""

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from conftest import Database
from sqlalchemy import Connection, text
from sqlalchemy.exc import ProgrammingError
from tenantdata import add_member, add_org, add_user

from nettriage.adapters.postgres import tenant_transaction
from nettriage.adapters.users import load_me, sign_in_user


def visible_users(connection: Connection) -> set[UUID]:
    return set(connection.execute(text("SELECT id FROM users")).scalars())


def test_the_first_sign_in_creates_the_user_and_later_ones_find_them(database: Database) -> None:
    sub = f"sub-{uuid4()}"

    first = sign_in_user(database.app_api, sub=sub, email="new@example.com")
    again = sign_in_user(database.app_api, sub=sub, email="new@example.com")

    assert (first.created, again.created) == (True, False)
    assert first.user_id == again.user_id
    assert not first.disabled


def test_sign_in_keeps_the_email_current(database: Database) -> None:
    sub = f"sub-{uuid4()}"
    user = sign_in_user(database.app_api, sub=sub, email="old@example.com")

    sign_in_user(database.app_api, sub=sub, email="renamed@example.com")

    me = load_me(database.app_api, user.user_id)
    assert me is not None
    assert me.email == "renamed@example.com"


def test_a_disabled_account_is_reported_and_not_marked_as_signed_in(database: Database) -> None:
    sub = f"sub-{uuid4()}"
    user = sign_in_user(database.app_api, sub=sub, email="gone@example.com")
    with database.admin.begin() as connection:
        connection.execute(
            text("UPDATE users SET disabled_at = now(), last_login_at = NULL WHERE id = :id"),
            {"id": user.user_id},
        )

    result = sign_in_user(database.app_api, sub=sub, email="gone@example.com")

    assert result.disabled
    with database.admin.begin() as connection:
        last_login: datetime | None = connection.execute(
            text("SELECT last_login_at FROM users WHERE id = :id"), {"id": user.user_id}
        ).scalar_one()
    assert last_login is None


def test_me_lists_the_users_memberships_by_org_name(database: Database) -> None:
    with database.admin.begin() as connection:
        user = add_user(connection)
        first, second = add_org(connection, user), add_org(connection, user)
        add_member(connection, first, user, "owner")
        add_member(connection, second, user, "viewer")

    me = load_me(database.app_api, user)

    assert me is not None
    assert {(m.org_id, m.role) for m in me.memberships} == {
        (first, "owner"),
        (second, "viewer"),
    }


def test_me_is_none_for_an_unknown_user(database: Database) -> None:
    assert load_me(database.app_api, uuid4()) is None


def test_outside_an_org_a_user_sees_only_their_own_row(database: Database) -> None:
    with database.admin.begin() as connection:
        me, colleague = add_user(connection), add_user(connection)
        org = add_org(connection, me)
        add_member(connection, org, me, "owner")
        add_member(connection, org, colleague, "viewer")

    with tenant_transaction(database.app_api, user_id=me) as connection:
        assert visible_users(connection) == {me}


def test_inside_an_org_a_member_sees_that_orgs_members_only(database: Database) -> None:
    with database.admin.begin() as connection:
        me, colleague, stranger = add_user(connection), add_user(connection), add_user(connection)
        mine, theirs = add_org(connection, me), add_org(connection, stranger)
        add_member(connection, mine, me, "owner")
        add_member(connection, mine, colleague, "analyst")
        add_member(connection, theirs, stranger, "owner")

    with tenant_transaction(database.app_api, org_id=mine, user_id=me) as connection:
        assert visible_users(connection) == {me, colleague}


def test_a_non_member_sees_no_one_in_an_org(database: Database) -> None:
    with database.admin.begin() as connection:
        me, stranger = add_user(connection), add_user(connection)
        theirs = add_org(connection, stranger)
        add_member(connection, theirs, stranger, "owner")

    with tenant_transaction(database.app_api, org_id=theirs, user_id=me) as connection:
        assert visible_users(connection) == {me}


def test_without_settings_no_user_is_visible(database: Database) -> None:
    with database.admin.begin() as connection:
        add_user(connection)

    with tenant_transaction(database.app_api) as connection:
        assert visible_users(connection) == set()


def test_a_user_can_rename_only_themselves(database: Database) -> None:
    with database.admin.begin() as connection:
        me, colleague = add_user(connection), add_user(connection)
        org = add_org(connection, me)
        add_member(connection, org, me, "owner")
        add_member(connection, org, colleague, "viewer")

    with tenant_transaction(database.app_api, org_id=org, user_id=me) as connection:
        renamed = connection.execute(
            text("UPDATE users SET display_name = 'Taken over' WHERE id IN (:me, :other)"),
            {"me": me, "other": colleague},
        ).rowcount

    assert renamed == 1


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO users (id, cognito_sub, email) VALUES (gen_random_uuid(), 'x', 'x@y.z')",
        "UPDATE users SET email = 'x@y.z'",
        "UPDATE users SET disabled_at = NULL",
    ],
)
def test_the_api_role_changes_users_only_through_sign_in(
    database: Database, statement: str
) -> None:
    def run() -> None:
        with database.app_api.begin() as connection:
            connection.execute(text(statement))

    with pytest.raises(ProgrammingError, match="permission denied"):
        run()


def test_sign_in_works_when_the_owner_role_is_not_a_superuser(database: Database) -> None:
    """On Neon the migrations run as `neondb_owner`, which isn't a superuser. The function must
    still find users, which works because `users` doesn't FORCE row-level security."""
    sub = f"sub-{uuid4()}"
    existing = sign_in_user(database.app_api, sub=sub, email="owned@example.com")
    owner = f"owner_{uuid4().hex[:8]}"
    with database.admin.begin() as connection:
        connection.execute(text(f"CREATE ROLE {owner} NOSUPERUSER NOBYPASSRLS"))
        connection.execute(text(f"ALTER TABLE users OWNER TO {owner}"))
        connection.execute(text(f"ALTER FUNCTION sign_in_user(uuid, text, text) OWNER TO {owner}"))
    try:
        again = sign_in_user(database.app_api, sub=sub, email="owned@example.com")
    finally:
        with database.admin.begin() as connection:
            connection.execute(text("ALTER TABLE users OWNER TO CURRENT_USER"))
            connection.execute(
                text("ALTER FUNCTION sign_in_user(uuid, text, text) OWNER TO CURRENT_USER")
            )
            connection.execute(text(f"DROP ROLE {owner}"))

    assert (again.user_id, again.created) == (existing.user_id, False)
```

`backend/tests/integration/test_audit_log.py`:
```python
"""Writing audit events as the API's role (spec §9.4)."""

from uuid import uuid4

import pytest
from conftest import Database
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from tenantdata import add_tenant

from nettriage.adapters.audit_log import record
from nettriage.application.audit import AuditEvent


def test_an_event_is_stored_with_its_request_details(database: Database) -> None:
    actor = uuid4()
    record(
        database.app_api,
        AuditEvent(
            action="auth.session_created",
            outcome="success",
            actor_type="user",
            actor_user_id=actor,
            ip="203.0.113.7",
            user_agent="x" * 300,
            request_id="cf-request-1",
            trace_id="0" * 32,
            details={"new_user": True},
        ),
    )

    with database.admin.begin() as connection:
        row = connection.execute(
            text(
                "SELECT action, outcome, org_id, host(ip) AS ip, length(user_agent) AS ua, "
                "request_id, details FROM audit_log WHERE actor_user_id = :actor"
            ),
            {"actor": actor},
        ).one()
    assert (row.action, row.outcome, row.org_id, row.ip) == (
        "auth.session_created",
        "success",
        None,
        "203.0.113.7",
    )
    assert (row.ua, row.request_id, row.details) == (256, "cf-request-1", {"new_user": True})


def test_an_org_event_is_written_within_that_org(database: Database) -> None:
    tenant = add_tenant(database.admin)

    record(
        database.app_api,
        AuditEvent(
            action="org.renamed",
            outcome="success",
            actor_type="user",
            actor_user_id=tenant.owner_id,
            org_id=tenant.org_id,
        ),
    )

    with database.admin.begin() as connection:
        count: int = connection.execute(
            text("SELECT count(*) FROM audit_log WHERE org_id = :org"), {"org": tenant.org_id}
        ).scalar_one()
    assert count == 1


def test_the_owner_cannot_truncate_the_audit_log(database: Database) -> None:
    def truncate() -> None:
        with database.admin.begin() as connection:
            connection.execute(text("TRUNCATE audit_log"))

    with pytest.raises(DBAPIError, match="append-only"):
        truncate()
```

In `backend/tests/integration/test_tenant_isolation.py`, replace:
```python
from nettriage.adapters.postgres import tenant_transaction
```
with:
```python
from nettriage.adapters.postgres import tenant_transaction
from nettriage.adapters.users import sign_in_user
```

In `backend/tests/integration/test_tenant_isolation.py`, replace:
```python
    org, owner, member = uuid7(), uuid7(), uuid7()
    with database.app_api.begin() as connection:  # just-in-time users need no tenant
        for user in (owner, member):
            connection.execute(
                text("INSERT INTO users (id, cognito_sub, email) VALUES (:id, :sub, :email)"),
                {"id": user, "sub": f"sub-{user}", "email": f"{user}@example.com"},
            )
```
with:
```python
    org = uuid7()
    owner, member = (
        sign_in_user(database.app_api, sub=f"sub-{uuid7()}", email=f"{name}@example.com").user_id
        for name in ("owner", "member")
    )
```

In `backend/tests/integration/test_tenant_isolation.py`, replace:
```python
        "UPDATE invitations SET token_hash = repeat('0', 64)",
    ],
)
```
with:
```python
        "UPDATE invitations SET token_hash = repeat('0', 64)",
        "INSERT INTO organizations (id, name, slug, is_demo) "
        "VALUES (gen_random_uuid(), 'Demo', 'fake-demo', true)",
        "CREATE TEMP TABLE shadow (id int)",
        "CREATE TABLE sneaky (id int)",
    ],
)
```

In `backend/tests/unit/adapters/test_postgres.py`, replace:
```python
    assert connect_args(url) == {
        "prepare_threshold": None,
        "sslmode": "verify-full",
```
with:
```python
    assert connect_args(url) == {
        "prepare_threshold": None,
        "connect_timeout": 10,
        "sslmode": "verify-full",
```

In `backend/tests/unit/adapters/test_postgres.py`, replace:
```python
    assert connect_args(url) == {"prepare_threshold": None}
```
with:
```python
    assert connect_args(url) == {"prepare_threshold": None, "connect_timeout": 10}
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `just test`
Expected: FAIL. Collection stops on `ModuleNotFoundError: No module named 'nettriage.adapters.users'` (in `test_users.py` and `test_tenant_isolation.py`) and `No module named 'nettriage.adapters.audit_log'`.

Run: `cd backend && uv run python -m pytest tests/unit/adapters/test_postgres.py -q`
Expected: 2 failed. Each dict is missing `'connect_timeout': 10`.

- [ ] **Step 3: Write the migration, the adapters and the audit event**

`backend/migrations/versions/0003_sign_in_hardening.py`:
```python
"""Sign-in and hardening (Plan 3b): row-level security on `users`, the sign-in function, and the
Plan 3a review's hardening items.

- `users` gets row-level security, ENABLE without FORCE. `app_api` sees its own row, and inside
  an org's transaction the members of that org (when it is one of them). The table's owner
  bypasses it, so `sign_in_user`, a SECURITY DEFINER function owned by the owner, can find or
  create a user before anyone knows who the user is.
- `app_api` loses INSERT on `users` (sign-in goes through the function) and keeps UPDATE of
  `display_name`, on its own row only.
- INSERT grants become column-level, so `app_api` can't set `is_demo`, `accepted_at`,
  `created_at` and the like.
- `audit_log` also rejects TRUNCATE.
- PUBLIC loses TEMPORARY on this database, so `app_api` can't create a temporary table that
  shadows a real one.
- The migration stops if `app_api` already exists with rights it must not have.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT FROM pg_roles WHERE rolname = 'app_api'
                AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)
            ) THEN
                RAISE EXCEPTION 'app_api must not be a superuser or have BYPASSRLS, CREATEROLE, '
                    'CREATEDB or REPLICATION';
            END IF;
            IF EXISTS (
                SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
                WHERE r.rolname = 'app_api'
            ) THEN
                RAISE EXCEPTION 'app_api must not be a member of another role';
            END IF;
        END $$;

        ALTER TABLE users ENABLE ROW LEVEL SECURITY;
        CREATE POLICY self_or_fellow_member ON users FOR SELECT
            USING (id = app_user_id()
                   OR (id IN (SELECT user_id FROM memberships WHERE org_id = app_org_id())
                       AND EXISTS (SELECT FROM memberships
                                   WHERE org_id = app_org_id() AND user_id = app_user_id())));
        CREATE POLICY self_update ON users FOR UPDATE
            USING (id = app_user_id())
            WITH CHECK (id = app_user_id());

        -- Sign-in: find the user by Cognito's `sub`, or create them. Returns whether the row is
        -- new and whether the account is disabled; a disabled account's last_login_at stays.
        CREATE FUNCTION sign_in_user(p_new_id uuid, p_sub text, p_email text)
            RETURNS TABLE (user_id uuid, created boolean, disabled boolean)
            LANGUAGE sql SECURITY DEFINER
            SET search_path = pg_catalog, public, pg_temp
        AS $$
            INSERT INTO users AS u (id, cognito_sub, email, last_login_at)
            VALUES (p_new_id, p_sub, p_email, now())
            ON CONFLICT (cognito_sub) DO UPDATE
                SET email = excluded.email,
                    last_login_at = CASE WHEN u.disabled_at IS NULL THEN now()
                                         ELSE u.last_login_at END
            RETURNING u.id, u.id = p_new_id, u.disabled_at IS NOT NULL
        $$;
        REVOKE ALL ON FUNCTION sign_in_user(uuid, text, text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION sign_in_user(uuid, text, text) TO app_api;

        REVOKE INSERT, UPDATE ON users FROM app_api;
        GRANT UPDATE (display_name) ON users TO app_api;
        REVOKE INSERT ON organizations, memberships, invitations, audit_log FROM app_api;
        GRANT INSERT (id, name, slug, created_by) ON organizations TO app_api;
        GRANT INSERT (org_id, user_id, role, invited_by) ON memberships TO app_api;
        GRANT INSERT (id, org_id, email, role, token_hash, expires_at, created_by)
            ON invitations TO app_api;
        GRANT INSERT (id, org_id, actor_user_id, actor_type, action, target_type, target_id,
                      outcome, ip, user_agent, request_id, trace_id, details)
            ON audit_log TO app_api;

        CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
            FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only();

        DO $$
        BEGIN
            EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            EXECUTE format('GRANT TEMPORARY ON DATABASE %I TO PUBLIC', current_database());
        END $$;
        DROP TRIGGER audit_log_no_truncate ON audit_log;

        REVOKE INSERT ON organizations, memberships, invitations, audit_log FROM app_api;
        GRANT INSERT ON organizations, memberships, invitations, audit_log TO app_api;
        REVOKE UPDATE ON users FROM app_api;
        GRANT INSERT ON users TO app_api;
        GRANT UPDATE (email, display_name, last_login_at) ON users TO app_api;

        DROP FUNCTION sign_in_user(uuid, text, text);
        DROP POLICY self_update ON users;
        DROP POLICY self_or_fellow_member ON users;
        ALTER TABLE users DISABLE ROW LEVEL SECURITY;
        """
    )
```

`backend/src/nettriage/application/__init__.py`:
```python
"""Use cases and the rules around them: sessions, sign-in, rate limits, audit events. No AWS or
database imports."""
```

`backend/src/nettriage/application/audit.py`:
```python
"""Audit events (spec §9.4): who did what, to what, with what outcome."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

Outcome = Literal["success", "denied", "error"]
ActorType = Literal["user", "system", "anonymous"]


@dataclass(frozen=True)
class AuditEvent:
    action: str
    outcome: Outcome
    actor_type: ActorType
    actor_user_id: UUID | None = None
    org_id: UUID | None = None
    target_type: str | None = None
    target_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    details: Mapping[str, object] = field(default_factory=dict)
```

`backend/src/nettriage/adapters/users.py`:
```python
"""Users in Postgres (spec §6.3): just-in-time sign-in, and the signed-in user's own view."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid7

from sqlalchemy import Engine, text

from nettriage.adapters.postgres import tenant_transaction


@dataclass(frozen=True)
class SignedInUser:
    user_id: UUID
    created: bool
    disabled: bool


@dataclass(frozen=True)
class Membership:
    org_id: UUID
    name: str
    slug: str
    role: str


@dataclass(frozen=True)
class Me:
    user_id: UUID
    email: str
    display_name: str | None
    memberships: tuple[Membership, ...]


def sign_in_user(engine: Engine, *, sub: str, email: str) -> SignedInUser:
    """Find the user by Cognito's `sub` or create them, and keep their email current. Runs the
    `sign_in_user` database function: the API's role can't read or insert users directly."""
    with tenant_transaction(engine) as connection:
        row = connection.execute(
            text("SELECT user_id, created, disabled FROM sign_in_user(:id, :sub, :email)"),
            {"id": uuid7(), "sub": sub, "email": email},
        ).one()
    return SignedInUser(user_id=row.user_id, created=row.created, disabled=row.disabled)


def load_me(engine: Engine, user_id: UUID) -> Me | None:
    """The user and their memberships, read in a user-only transaction: row-level security
    shows only their own row and memberships, and the orgs they belong to."""
    with tenant_transaction(engine, user_id=user_id) as connection:
        user = connection.execute(
            text("SELECT id, email, display_name FROM users WHERE id = :id"), {"id": user_id}
        ).one_or_none()
        if user is None:
            return None
        rows = connection.execute(
            text(
                "SELECT o.id, o.name, o.slug, m.role FROM memberships m "
                "JOIN organizations o ON o.id = m.org_id "
                "WHERE m.user_id = :id ORDER BY o.name, o.id"
            ),
            {"id": user_id},
        ).all()
    return Me(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        memberships=tuple(
            Membership(org_id=row.id, name=row.name, slug=row.slug, role=row.role) for row in rows
        ),
    )
```

`backend/src/nettriage/adapters/audit_log.py`:
```python
"""The append-only audit log in Postgres (spec §5.2, §9.4)."""

from __future__ import annotations

import json
from uuid import uuid7

from sqlalchemy import Engine, text

from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.audit import AuditEvent

MAX_USER_AGENT = 256

_INSERT = text(
    "INSERT INTO audit_log (id, org_id, actor_user_id, actor_type, action, target_type, "
    "target_id, outcome, ip, user_agent, request_id, trace_id, details) VALUES (:id, :org_id, "
    ":actor_user_id, :actor_type, :action, :target_type, :target_id, :outcome, "
    "CAST(:ip AS inet), :user_agent, :request_id, :trace_id, CAST(:details AS jsonb))"
)


def record(engine: Engine, event: AuditEvent) -> None:
    """Append one event, in its own transaction scoped to the event's org and actor."""
    with tenant_transaction(engine, org_id=event.org_id, user_id=event.actor_user_id) as connection:
        connection.execute(
            _INSERT,
            {
                "id": uuid7(),
                "org_id": event.org_id,
                "actor_user_id": event.actor_user_id,
                "actor_type": event.actor_type,
                "action": event.action,
                "target_type": event.target_type,
                "target_id": event.target_id,
                "outcome": event.outcome,
                "ip": event.ip,
                "user_agent": event.user_agent[:MAX_USER_AGENT] if event.user_agent else None,
                "request_id": event.request_id,
                "trace_id": event.trace_id,
                "details": json.dumps(dict(event.details)),
            },
        )
```

In `backend/src/nettriage/adapters/postgres.py`, replace:
```python
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
```
with:
```python
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
CONNECT_TIMEOUT_SECONDS = 10
```

In `backend/src/nettriage/adapters/postgres.py`, replace:
```python
    """Remote hosts always use `sslmode=verify-full` against certifi's CA bundle, whatever the URL
    says; only a local test server may skip TLS."""
    args: dict[str, Any] = {"prepare_threshold": None}
```
with:
```python
    """Remote hosts always use `sslmode=verify-full` against certifi's CA bundle, whatever the URL
    says; only a local test server may skip TLS. A connection attempt gives up after 10 seconds,
    well inside the API's 29-second timeout, instead of psycopg's default of about two minutes."""
    args: dict[str, Any] = {"prepare_threshold": None, "connect_timeout": CONNECT_TIMEOUT_SECONDS}
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
A dedicated test runs a query with no org filter and must receive zero rows from other organizations.
```
with:
```markdown
A dedicated test runs a query with no org filter and must receive zero rows from other organizations.

`users` also has row-level security (the owner's decision, 2026-09-28, Plan 3b). A user sees their own row, and in an organization's transaction the members of that organization. Sign-in finds or creates the user through a `SECURITY DEFINER` function, because the API's role can't read or insert other users' rows.
```

- [ ] **Step 4: Run the checks**

Run: `just lint test`
Expected:
- lint ends with `All checks passed!`, `… files already formatted` and `Success: no issues found in … source files`;
- the tests end with `225 passed`, and the coverage line `Required test coverage of 85% reached.`

The new tests cover the following:
- sign-in creates the user once and then finds them, and keeps the email current;
- a disabled account is reported, and its `last_login_at` doesn't change;
- outside an org a user sees only their own row; inside an org, a member sees that org's members and a non-member sees only themselves;
- `app_api` can rename only itself, and can't insert users or change emails;
- the function still works when its owner isn't a superuser;
- audit events are written with their request details;
- the owner can't `TRUNCATE` the audit log;
- `app_api` can't create temporary tables, create tables, or insert `is_demo`.

`test_migrations.py` also applies 0003, rolls everything back and applies it again.

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/versions/0003_sign_in_hardening.py backend/src/nettriage/application backend/src/nettriage/adapters backend/tests docs/superpowers/specs/2026-09-26-nettriage-m1-design.md
git commit -m "feat(data): row-level security on users, a sign-in function, and the audit log's last gaps" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Sessions and sign-in state in the DynamoDB `runtime` table

**Files:**
- Modify: `backend/pyproject.toml`, `backend/uv.lock` (via `uv add`), `justfile`, `.github/workflows/ci.yml`
- Create: `backend/src/nettriage/application/clock.py`, `backend/src/nettriage/application/sessions.py`, `backend/src/nettriage/application/sign_in.py`, `backend/src/nettriage/adapters/runtime_table.py`, `backend/src/nettriage/adapters/sessions.py`, `backend/src/nettriage/adapters/login_states.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/unit/application/test_session_rules.py`, `backend/tests/unit/application/test_sign_in_rules.py`, `backend/tests/unit/adapters/test_session_store.py`, `backend/tests/unit/adapters/test_login_state_store.py`

**Interfaces:**
- Produces:
  - In `nettriage.application.clock`: `Clock = Callable[[], datetime]` and `system_clock() -> datetime` (UTC).
  - In `nettriage.application.sessions`:
    - the constants `COOKIE_NAME = "__Host-session"`, `IDLE_TIMEOUT` (60 min), `ABSOLUTE_TIMEOUT` (12 h) and `TOUCH_INTERVAL` (5 min);
    - `new_secret() -> str` (32 random bytes, base64url) and `session_key(session_id) -> str` (SHA-256 hex);
    - `Session(key, user_id: UUID, csrf_token, created_at, last_seen_at)`, with `.expires_at`, `.is_expired(now)` and `.needs_touch(now)`.
  - In `nettriage.application.sign_in`:
    - `DEFAULT_RETURN_TO = "/app"` and `LoginState(code_verifier, nonce, return_to)`;
    - `safe_return_to(value: str | None) -> str`, `new_code_verifier() -> str` and `code_challenge(verifier) -> str` (S256).
  - In `nettriage.adapters.runtime_table`: `epoch_seconds(dt) -> int`, `epoch_millis(dt) -> int`, `from_millis(str) -> datetime` and `is_condition_failure(ClientError) -> bool`.
  - In `nettriage.adapters.sessions`: `SessionStore(client, table)`, with these methods:
    - `.create(*, user_id, now, ip, user_agent) -> tuple[str, Session]`;
    - `.get(session_id) -> Session | None`;
    - `.touch(session, now) -> Session | None`;
    - `.delete(session)` and `.delete_all(user_id) -> int`.
  - In `nettriage.adapters.login_states`: `LoginStateStore(client, table)` with `.put(state, login, now)` and `.take(state, now) -> LoginState | None`, where a state can be taken only once, within 5 minutes.
  - In the test harness (`backend/tests/conftest.py`):
    - `RuntimeTable(client, name)` and `create_runtime_table(client, name)`;
    - the `runtime_table` fixture (moto) and the `clock` fixture (`FakeClock`, starting at 2026-09-28 12:00 UTC, with `.advance(delta)`).
  - `just test` and CI measure coverage of `nettriage.application` as well as `nettriage.domain`.

- [ ] **Step 1: Add the dependencies**

```bash
cd backend
uv add boto3
uv add --dev "moto[dynamodb,ssm]" "types-boto3[dynamodb,ssm]"
```

- [ ] **Step 2: Write the failing tests**

`backend/tests/conftest.py`:
```python
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import boto3
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL

from nettriage.adapters.postgres import create_database_engine, engine_url
from nettriage.entrypoints.api.app import create_app
from nettriage.platform.config import Settings

if TYPE_CHECKING:
    from types_boto3_dynamodb.client import DynamoDBClient

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


# The DynamoDB `runtime` table, mocked in-process by moto (spec §11.4). The concurrency test in
# tests/security uses a real DynamoDB Local instead, because moto's writes aren't atomic.

RUNTIME_TABLE = "nettriage-test-runtime"
REGION = "eu-north-1"


@dataclass(frozen=True)
class RuntimeTable:
    client: DynamoDBClient
    name: str


def create_runtime_table(client: DynamoDBClient, name: str) -> None:
    """The same key schema as infra/modules/data."""
    client.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def runtime_table() -> Iterator[RuntimeTable]:
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        create_runtime_table(client, RUNTIME_TABLE)
        yield RuntimeTable(client=client, name=RUNTIME_TABLE)


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
```

`backend/tests/unit/application/test_session_rules.py`:
```python
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from nettriage.application.sessions import Session, new_secret, session_key

START = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def session(created: datetime = START, seen: datetime = START) -> Session:
    return Session(
        key="k", user_id=uuid4(), csrf_token=new_secret(), created_at=created, last_seen_at=seen
    )


def test_a_session_expires_after_sixty_idle_minutes() -> None:
    active = session()

    assert not active.is_expired(START + timedelta(minutes=59, seconds=59))
    assert active.is_expired(START + timedelta(minutes=60))


def test_activity_can_not_keep_a_session_alive_past_twelve_hours() -> None:
    busy = session(seen=START + timedelta(hours=11, minutes=59))

    assert not busy.is_expired(START + timedelta(hours=11, minutes=59, seconds=59))
    assert busy.is_expired(START + timedelta(hours=12))


def test_activity_is_recorded_at_most_every_five_minutes() -> None:
    active = session()

    assert not active.needs_touch(START + timedelta(minutes=4, seconds=59))
    assert active.needs_touch(START + timedelta(minutes=5))


def test_secrets_are_32_random_bytes_and_only_their_hash_is_a_key() -> None:
    secret = new_secret()

    assert len(secret) == 43  # 32 bytes, base64url without padding
    assert secret != new_secret()
    assert len(session_key(secret)) == 64
    assert secret not in session_key(secret)
```

`backend/tests/unit/application/test_sign_in_rules.py`:
```python
import base64
import hashlib

import pytest

from nettriage.application.sign_in import code_challenge, new_code_verifier, safe_return_to


@pytest.mark.parametrize("path", ["/app", "/app/orgs/acme/findings?status=open", "/invite"])
def test_a_path_inside_the_app_is_kept(path: str) -> None:
    assert safe_return_to(path) == path


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "https://evil.example/",
        "//evil.example/",
        "/\\evil.example/",
        "/app\\..\\evil",
        "javascript:alert(1)",
        "app",
        "/app\r\nSet-Cookie: x=y",
        "/" + "a" * 512,
    ],
)
def test_anything_else_returns_to_the_app(value: str | None) -> None:
    assert safe_return_to(value) == "/app"


def test_the_code_verifier_fits_rfc_7636() -> None:
    verifier = new_code_verifier()

    assert 43 <= len(verifier) <= 128
    assert verifier != new_code_verifier()


def test_the_code_challenge_is_the_unpadded_base64url_sha256_of_the_verifier() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"  # RFC 7636, appendix B

    assert code_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert code_challenge(verifier) == expected.rstrip(b"=").decode()
```

`backend/tests/unit/adapters/test_session_store.py`:
```python
from datetime import timedelta
from uuid import uuid4

from conftest import FakeClock, RuntimeTable

from nettriage.adapters.sessions import SessionStore
from nettriage.application.sessions import session_key


def store(table: RuntimeTable) -> SessionStore:
    return SessionStore(table.client, table.name)


def test_a_created_session_is_found_by_its_id(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    sessions = store(runtime_table)
    user = uuid4()

    session_id, created = sessions.create(
        user_id=user, now=clock(), ip="203.0.113.7", user_agent="Firefox"
    )

    assert sessions.get(session_id) == created
    assert (created.user_id, created.created_at, created.last_seen_at) == (user, clock(), clock())


def test_only_the_hash_of_the_session_id_is_stored(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    session_id, _ = store(runtime_table).create(
        user_id=uuid4(), now=clock(), ip=None, user_agent=None
    )

    items = runtime_table.client.scan(TableName=runtime_table.name)["Items"]
    assert session_id not in str(items)
    assert any(item["pk"]["S"] == f"SESSION#{session_key(session_id)}" for item in items)


def test_an_unknown_session_id_finds_nothing(runtime_table: RuntimeTable) -> None:
    assert store(runtime_table).get("not-a-session") is None


def test_touching_records_activity_and_pushes_the_ttl(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    sessions = store(runtime_table)
    session_id, session = sessions.create(user_id=uuid4(), now=clock(), ip=None, user_agent=None)
    clock.advance(timedelta(minutes=10))

    touched = sessions.touch(session, clock())

    assert touched is not None
    assert sessions.get(session_id) == touched
    assert touched.last_seen_at == clock()


def test_touching_a_deleted_session_reports_it_gone(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    sessions = store(runtime_table)
    _, session = sessions.create(user_id=uuid4(), now=clock(), ip=None, user_agent=None)
    sessions.delete(session)

    assert sessions.touch(session, clock()) is None


def test_signing_out_deletes_only_that_session(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    sessions = store(runtime_table)
    user = uuid4()
    first_id, first = sessions.create(user_id=user, now=clock(), ip=None, user_agent=None)
    second_id, _ = sessions.create(user_id=user, now=clock(), ip=None, user_agent=None)

    sessions.delete(first)

    assert sessions.get(first_id) is None
    assert sessions.get(second_id) is not None


def test_signing_out_everywhere_deletes_every_session_of_that_user_only(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    sessions = store(runtime_table)
    user, other = uuid4(), uuid4()
    mine = [
        sessions.create(user_id=user, now=clock(), ip=None, user_agent=None)[0] for _ in range(30)
    ]
    theirs, _ = sessions.create(user_id=other, now=clock(), ip=None, user_agent=None)

    deleted = sessions.delete_all(user)

    assert deleted == 30
    assert all(sessions.get(session_id) is None for session_id in mine)
    assert sessions.get(theirs) is not None
    assert sessions.delete_all(user) == 0
```

`backend/tests/unit/adapters/test_login_state_store.py`:
```python
from datetime import timedelta

from conftest import FakeClock, RuntimeTable

from nettriage.adapters.login_states import LoginStateStore
from nettriage.application.sign_in import LoginState

LOGIN = LoginState(code_verifier="verifier", nonce="nonce", return_to="/app/orgs")


def test_a_login_state_can_be_taken_once(runtime_table: RuntimeTable, clock: FakeClock) -> None:
    states = LoginStateStore(runtime_table.client, runtime_table.name)
    states.put("state-1", LOGIN, clock())

    assert states.take("state-1", clock()) == LOGIN
    assert states.take("state-1", clock()) is None


def test_an_unknown_state_is_refused(runtime_table: RuntimeTable, clock: FakeClock) -> None:
    states = LoginStateStore(runtime_table.client, runtime_table.name)

    assert states.take("never-issued", clock()) is None


def test_a_state_older_than_five_minutes_is_refused_even_before_dynamodb_expires_it(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    states = LoginStateStore(runtime_table.client, runtime_table.name)
    states.put("state-1", LOGIN, clock())
    clock.advance(timedelta(minutes=5))

    assert states.take("state-1", clock()) is None
```

- [ ] **Step 3: Run the tests and watch them fail**

Run: `cd backend && uv run python -m pytest tests/unit -q`
Expected: FAIL. Collection errors say `No module named 'nettriage.application.sessions'`, `No module named 'nettriage.application.sign_in'`, `No module named 'nettriage.adapters.sessions'` and `No module named 'nettriage.adapters.login_states'`.

- [ ] **Step 4: Write the session and sign-in rules and their stores**

`backend/src/nettriage/application/clock.py`:
```python
"""The current time, injected so tests can control it."""

from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]


def system_clock() -> datetime:
    return datetime.now(UTC)
```

`backend/src/nettriage/application/sessions.py`:
```python
"""Browser sessions (spec §6.2). The cookie holds 32 random bytes; the server keeps only their
SHA-256, so a leaked session table can't be replayed as cookies."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

COOKIE_NAME = "__Host-session"
IDLE_TIMEOUT = timedelta(minutes=60)
ABSOLUTE_TIMEOUT = timedelta(hours=12)
# last_seen_at is rewritten at most this often, to save DynamoDB writes (spec §5.5).
TOUCH_INTERVAL = timedelta(minutes=5)


def new_secret() -> str:
    """32 random bytes, base64url-encoded: a session ID or a CSRF token."""
    return secrets.token_urlsafe(32)


def session_key(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()


@dataclass(frozen=True)
class Session:
    key: str
    user_id: UUID
    csrf_token: str
    created_at: datetime
    last_seen_at: datetime

    @property
    def expires_at(self) -> datetime:
        return min(self.created_at + ABSOLUTE_TIMEOUT, self.last_seen_at + IDLE_TIMEOUT)

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def needs_touch(self, now: datetime) -> bool:
        return now - self.last_seen_at >= TOUCH_INTERVAL
```

`backend/src/nettriage/application/sign_in.py`:
```python
"""The sign-in flow's rules (spec §4.1): where the user may return to, and PKCE."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass

DEFAULT_RETURN_TO = "/app"
MAX_RETURN_TO = 512


@dataclass(frozen=True)
class LoginState:
    """What the callback needs from the login that started it; kept for 5 minutes."""

    code_verifier: str
    nonce: str
    return_to: str


def safe_return_to(value: str | None) -> str:
    """A path inside this app, or /app. It must start with a single `/`, so it has no scheme and
    no host; anything a browser could still read as another origin is refused too: `//host`,
    backslashes (browsers read `/\\host` as `//host`) and control characters. This is the
    open-redirect protection."""
    if not value or len(value) > MAX_RETURN_TO or not value.startswith("/"):
        return DEFAULT_RETURN_TO
    if value.startswith("//") or "\\" in value:
        return DEFAULT_RETURN_TO
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        return DEFAULT_RETURN_TO
    return value


def new_code_verifier() -> str:
    """64 random bytes, base64url-encoded: 86 characters, inside RFC 7636's 43 to 128."""
    return secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    """RFC 7636's S256 method."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
```

`backend/src/nettriage/adapters/runtime_table.py`:
```python
"""The DynamoDB `runtime` table (spec §5.5): a string partition key `pk`, and a TTL attribute
`expires_at` in epoch seconds. DynamoDB deletes expired items only eventually, so readers check
`expires_at` themselves."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from botocore.exceptions import ClientError


def epoch_seconds(moment: datetime) -> int:
    return math.ceil(moment.timestamp())


def epoch_millis(moment: datetime) -> int:
    return round(moment.timestamp() * 1000)


def from_millis(value: str) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, UTC)


def is_condition_failure(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"
```

`backend/src/nettriage/adapters/sessions.py`:
```python
"""Sessions in the DynamoDB `runtime` table (spec §5.5).

- `SESSION#<sha256(session id)>`: the user, the CSRF token, timestamps, IP and user agent.
- `USERSESS#<user id>`: the set of the user's session keys, for "sign out everywhere".

Reads are strongly consistent, so a signed-out session stops working at once.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from itertools import batched
from typing import TYPE_CHECKING
from uuid import UUID

from botocore.exceptions import ClientError

from nettriage.adapters.runtime_table import (
    epoch_millis,
    epoch_seconds,
    from_millis,
    is_condition_failure,
)
from nettriage.application.sessions import ABSOLUTE_TIMEOUT, Session, new_secret, session_key

if TYPE_CHECKING:
    from types_boto3_dynamodb.client import DynamoDBClient
    from types_boto3_dynamodb.type_defs import (
        AttributeValueTypeDef,
        WriteRequestOutputTypeDef,
        WriteRequestTypeDef,
    )

MAX_USER_AGENT = 256
BATCH_LIMIT = 25


def _session_pk(key: str) -> str:
    return f"SESSION#{key}"


def _user_pk(user_id: UUID) -> str:
    return f"USERSESS#{user_id}"


class SessionStore:
    def __init__(self, client: DynamoDBClient, table: str) -> None:
        self._client = client
        self._table = table

    def create(
        self, *, user_id: UUID, now: datetime, ip: str | None, user_agent: str | None
    ) -> tuple[str, Session]:
        """A new session. Returns the session ID for the cookie; only its hash is stored."""
        session_id = new_secret()
        session = Session(
            key=session_key(session_id),
            user_id=user_id,
            csrf_token=new_secret(),
            created_at=now,
            last_seen_at=now,
        )
        item: dict[str, AttributeValueTypeDef] = {
            "pk": {"S": _session_pk(session.key)},
            "user_id": {"S": str(user_id)},
            "csrf_token": {"S": session.csrf_token},
            "created_at": {"N": str(epoch_millis(now))},
            "last_seen_at": {"N": str(epoch_millis(now))},
            "expires_at": {"N": str(epoch_seconds(session.expires_at))},
        }
        if ip:
            item["ip"] = {"S": ip}
        if user_agent:
            item["user_agent"] = {"S": user_agent[:MAX_USER_AGENT]}
        self._client.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": self._table,
                        "Item": item,
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }
                },
                {
                    "Update": {
                        "TableName": self._table,
                        "Key": {"pk": {"S": _user_pk(user_id)}},
                        "UpdateExpression": "ADD sessions :key SET expires_at = :expires",
                        "ExpressionAttributeValues": {
                            ":key": {"SS": [session.key]},
                            ":expires": {"N": str(epoch_seconds(now + ABSOLUTE_TIMEOUT))},
                        },
                    }
                },
            ]
        )
        return session_id, session

    def get(self, session_id: str) -> Session | None:
        response = self._client.get_item(
            TableName=self._table,
            Key={"pk": {"S": _session_pk(session_key(session_id))}},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if item is None:
            return None
        return Session(
            key=session_key(session_id),
            user_id=UUID(item["user_id"]["S"]),
            csrf_token=item["csrf_token"]["S"],
            created_at=from_millis(item["created_at"]["N"]),
            last_seen_at=from_millis(item["last_seen_at"]["N"]),
        )

    def touch(self, session: Session, now: datetime) -> Session | None:
        """Record activity. None if the session was deleted in the meantime."""
        touched = replace(session, last_seen_at=now)
        try:
            self._client.update_item(
                TableName=self._table,
                Key={"pk": {"S": _session_pk(session.key)}},
                UpdateExpression="SET last_seen_at = :seen, expires_at = :expires",
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeValues={
                    ":seen": {"N": str(epoch_millis(now))},
                    ":expires": {"N": str(epoch_seconds(touched.expires_at))},
                },
            )
        except ClientError as error:
            if is_condition_failure(error):
                return None
            raise
        return touched

    def delete(self, session: Session) -> None:
        self._client.transact_write_items(
            TransactItems=[
                {
                    "Delete": {
                        "TableName": self._table,
                        "Key": {"pk": {"S": _session_pk(session.key)}},
                    }
                },
                {
                    "Update": {
                        "TableName": self._table,
                        "Key": {"pk": {"S": _user_pk(session.user_id)}},
                        "UpdateExpression": "DELETE sessions :key",
                        "ExpressionAttributeValues": {":key": {"SS": [session.key]}},
                    }
                },
            ]
        )

    def delete_all(self, user_id: UUID) -> int:
        """Delete every session the user has. Returns how many were listed."""
        response = self._client.get_item(
            TableName=self._table, Key={"pk": {"S": _user_pk(user_id)}}, ConsistentRead=True
        )
        keys = sorted(response.get("Item", {}).get("sessions", {}).get("SS", []))
        for chunk in batched(keys, BATCH_LIMIT, strict=False):
            requests: Sequence[WriteRequestTypeDef | WriteRequestOutputTypeDef] = [
                {"DeleteRequest": {"Key": {"pk": {"S": _session_pk(key)}}}} for key in chunk
            ]
            while requests:
                result = self._client.batch_write_item(RequestItems={self._table: requests})
                requests = result.get("UnprocessedItems", {}).get(self._table, [])
        self._client.delete_item(TableName=self._table, Key={"pk": {"S": _user_pk(user_id)}})
        return len(keys)
```

`backend/src/nettriage/adapters/login_states.py`:
```python
"""Sign-in state in the DynamoDB `runtime` table (spec §4.1, §5.5): `LOGIN#<state>` holds the
PKCE verifier, the nonce and where to return, for 5 minutes, and can be taken only once."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

from nettriage.adapters.runtime_table import epoch_seconds, is_condition_failure
from nettriage.application.sign_in import LoginState

if TYPE_CHECKING:
    from types_boto3_dynamodb.client import DynamoDBClient

LOGIN_STATE_TTL = timedelta(minutes=5)


class LoginStateStore:
    def __init__(self, client: DynamoDBClient, table: str) -> None:
        self._client = client
        self._table = table

    def put(self, state: str, login: LoginState, now: datetime) -> None:
        self._client.put_item(
            TableName=self._table,
            Item={
                "pk": {"S": f"LOGIN#{state}"},
                "code_verifier": {"S": login.code_verifier},
                "nonce": {"S": login.nonce},
                "return_to": {"S": login.return_to},
                "expires_at": {"N": str(epoch_seconds(now + LOGIN_STATE_TTL))},
            },
            ConditionExpression="attribute_not_exists(pk)",
        )

    def take(self, state: str, now: datetime) -> LoginState | None:
        """Delete and return the state. None if it never existed, was already used, or expired:
        the delete is conditional, so two callbacks with the same state can't both succeed."""
        try:
            response = self._client.delete_item(
                TableName=self._table,
                Key={"pk": {"S": f"LOGIN#{state}"}},
                ConditionExpression="attribute_exists(pk)",
                ReturnValues="ALL_OLD",
            )
        except ClientError as error:
            if is_condition_failure(error):
                return None
            raise
        item = response.get("Attributes", {})
        if int(item["expires_at"]["N"]) <= epoch_seconds(now):
            return None
        return LoginState(
            code_verifier=item["code_verifier"]["S"],
            nonce=item["nonce"]["S"],
            return_to=item["return_to"]["S"],
        )
```

In `justfile`, replace:
```
uv run python -m pytest --cov=nettriage.domain --cov-report=term-missing --cov-fail-under=85
```
with:
```
uv run python -m pytest --cov=nettriage.domain --cov=nettriage.application --cov-report=term-missing --cov-fail-under=85
```

In `.github/workflows/ci.yml`, replace:
```yaml
          uv run --locked pytest --cov=nettriage.domain --cov-report=term-missing --cov-fail-under=85
```
with:
```yaml
          uv run --locked pytest --cov=nettriage.domain --cov=nettriage.application --cov-report=term-missing --cov-fail-under=85
```

- [ ] **Step 5: Run the checks**

Run: `just lint test`
Expected:
- lint is clean;
- `254 passed`;
- the coverage table lists `src\nettriage\application\sessions.py` and `sign_in.py` at 100%, above the 85% floor.

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/src backend/tests justfile .github/workflows/ci.yml
git commit -m "feat(auth): sessions and sign-in state in DynamoDB" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: GCRA rate limiting on DynamoDB, exact under concurrency

**Files:**
- Create: `backend/src/nettriage/application/rate_limits.py`, `backend/src/nettriage/adapters/rate_limiter.py`
- Modify: `.github/workflows/ci.yml`, `CLAUDE.md`, `docs/adr/0006-gcra-rate-limiter.md`, `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (§6.5, §11.4)
- Test: `backend/tests/unit/application/test_rate_limit_rules.py`, `backend/tests/unit/adapters/test_rate_limiter.py`, `backend/tests/security/test_rate_limit_concurrency.py`

**Interfaces:**
- Consumes (Task 2): `RuntimeTable`, `create_runtime_table`, `FakeClock`, `epoch_millis`, `epoch_seconds` and `is_condition_failure`.
- Produces:
  - In `nettriage.application.rate_limits`:
    - `Policy(name, limit, period: timedelta, burst)`, with `.period_ms`, `.interval_ms` and `.tolerance_ms`;
    - `POLICIES: dict[str, Policy]`, holding the seven policies of §6.5;
    - `Decision(policy, allowed, remaining=0, reset_seconds=0, retry_after_seconds=0, degraded=False)`;
    - `allowed(policy, tat_ms, now_ms)`, `limited(policy, tat_ms, now_ms)` and `failed_open(policy)`;
    - `header_values(decisions) -> dict[str, str]`, which gives the `RateLimit-Policy` and `RateLimit` values;
    - `viewer_ip(header) -> str | None` and `ip_subject(header) -> str | None` (IPv6 per /64).
  - In `nettriage.adapters.rate_limiter`: `RateLimiter(client, table, clock)`, with these methods:
    - `.check(policy, subject) -> Decision`;
    - `.should_audit(policy, subject) -> bool`, true at most once a minute per subject and policy.
  - In CI, a DynamoDB Local service at `NETTRIAGE_TEST_DYNAMODB_URL=http://localhost:8000`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/unit/application/test_rate_limit_rules.py`:
```python
from datetime import timedelta

import pytest

from nettriage.application.rate_limits import (
    POLICIES,
    Policy,
    allowed,
    failed_open,
    header_values,
    ip_subject,
    limited,
    viewer_ip,
)

MINUTE = Policy("test", 60, timedelta(minutes=1), 3)  # T = 1 s, tau = 2 s


def test_the_spec_policies_have_whole_millisecond_intervals() -> None:
    assert set(POLICIES) == {
        "api.user",
        "api.mutation.user",
        "auth.ip",
        "public.ip",
        "uploads.org",
        "invites.org",
        "ai.rerun.user",
    }
    for policy in POLICIES.values():
        assert policy.interval_ms * policy.limit == policy.period_ms, policy.name


def test_a_fresh_key_leaves_the_rest_of_the_burst() -> None:
    decision = allowed(MINUTE, tat_ms=1_000, now_ms=0)

    assert (decision.allowed, decision.remaining, decision.reset_seconds) == (True, 2, 1)


def test_the_last_slot_of_the_burst_leaves_nothing() -> None:
    assert allowed(MINUTE, tat_ms=3_000, now_ms=0).remaining == 0


def test_a_limited_request_is_told_when_to_retry() -> None:
    decision = limited(MINUTE, tat_ms=3_000, now_ms=0)

    assert (decision.allowed, decision.retry_after_seconds, decision.reset_seconds) == (
        False,
        1,
        3,
    )


def test_headers_list_every_policy_checked() -> None:
    user = POLICIES["api.user"]
    mutation = POLICIES["api.mutation.user"]

    headers = header_values([allowed(user, 500, 0), allowed(mutation, 2_000, 0)])

    assert headers == {
        "RateLimit-Policy": '"api.user";q=120;w=60, "api.mutation.user";q=30;w=60',
        "RateLimit": '"api.user";r=29;t=1, "api.mutation.user";r=9;t=2',
    }


def test_a_fail_open_decision_reports_no_headers() -> None:
    assert header_values([failed_open(MINUTE)]) == {}


@pytest.mark.parametrize(
    ("header", "subject"),
    [
        ("198.51.100.10:46532", "198.51.100.10"),
        ("2001:db8:1:2:3:4:5:6:46532", "2001:db8:1:2::/64"),
        ("[2001:db8:1:2::7]:443", "2001:db8:1:2::/64"),
        (None, None),
        ("", None),
        ("not-an-address:1", None),
        ("198.51.100.10", None),
    ],
)
def test_the_ip_subject_comes_from_cloudfronts_viewer_address(
    header: str | None, subject: str | None
) -> None:
    assert ip_subject(header) == subject


def test_the_viewer_ip_keeps_the_whole_address() -> None:
    assert viewer_ip("2001:db8:1:2:3:4:5:6:46532") == "2001:db8:1:2:3:4:5:6"
    assert viewer_ip("198.51.100.10:46532") == "198.51.100.10"
```

`backend/tests/unit/adapters/test_rate_limiter.py`:
```python
from datetime import timedelta

from conftest import FakeClock, RuntimeTable

from nettriage.adapters.rate_limiter import RateLimiter
from nettriage.application.rate_limits import Policy

POLICY = Policy("test", 60, timedelta(minutes=1), 3)  # T = 1 s, burst 3


def limiter(table: RuntimeTable, clock: FakeClock) -> RateLimiter:
    return RateLimiter(table.client, table.name, clock)


def test_a_burst_is_allowed_then_the_next_request_is_limited(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    limits = limiter(runtime_table, clock)

    decisions = [limits.check(POLICY, "user-1") for _ in range(4)]

    assert [d.allowed for d in decisions] == [True, True, True, False]
    assert [d.remaining for d in decisions[:3]] == [2, 1, 0]
    assert decisions[3].retry_after_seconds == 1


def test_one_request_is_allowed_again_after_one_interval(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    limits = limiter(runtime_table, clock)
    for _ in range(3):
        limits.check(POLICY, "user-1")

    clock.advance(timedelta(seconds=1))

    assert limits.check(POLICY, "user-1").allowed
    assert not limits.check(POLICY, "user-1").allowed


def test_an_idle_key_gets_its_whole_burst_back(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    limits = limiter(runtime_table, clock)
    for _ in range(4):
        limits.check(POLICY, "user-1")

    clock.advance(timedelta(minutes=5))

    assert [limits.check(POLICY, "user-1").allowed for _ in range(4)] == [True, True, True, False]


def test_subjects_and_policies_are_counted_separately(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    limits = limiter(runtime_table, clock)
    other_policy = Policy("other", 60, timedelta(minutes=1), 3)
    for _ in range(3):
        limits.check(POLICY, "user-1")

    assert limits.check(POLICY, "user-2").allowed
    assert limits.check(other_policy, "user-1").allowed


def test_a_dynamodb_failure_lets_the_request_through_marked_degraded(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    broken = RateLimiter(runtime_table.client, "no-such-table", clock)

    decision = broken.check(POLICY, "user-1")

    assert (decision.allowed, decision.degraded) == (True, True)


def test_limits_are_audited_at_most_once_per_minute_per_subject(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    limits = limiter(runtime_table, clock)

    first = limits.should_audit(POLICY, "user-1")
    again = limits.should_audit(POLICY, "user-1")
    someone_else = limits.should_audit(POLICY, "user-2")
    clock.advance(timedelta(minutes=1))
    later = limits.should_audit(POLICY, "user-1")

    assert (first, again, someone_else, later) == (True, False, True, True)
```

`backend/tests/security/test_rate_limit_concurrency.py`:
```python
"""100 parallel requests for one key: exactly the allowed number pass (spec §11.4).

This needs a real DynamoDB, whose conditional writes are atomic; moto's aren't. CI runs DynamoDB
Local as a service container and sets NETTRIAGE_TEST_DYNAMODB_URL. Without it the test is skipped:
this machine can't run DynamoDB Local (no Docker, no Java).
"""

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import boto3
import pytest
from botocore.config import Config
from conftest import FakeClock, RuntimeTable, create_runtime_table

from nettriage.adapters.rate_limiter import RateLimiter
from nettriage.application.rate_limits import Policy

DYNAMODB_ENV = "NETTRIAGE_TEST_DYNAMODB_URL"
POLICY = Policy("concurrency", 5, timedelta(hours=1), 5)


@pytest.fixture
def dynamodb_local() -> Iterator[RuntimeTable]:
    endpoint = os.environ.get(DYNAMODB_ENV)
    if not endpoint:
        pytest.skip(f"{DYNAMODB_ENV} isn't set; CI runs this against DynamoDB Local")
    client = boto3.client(
        "dynamodb",
        endpoint_url=endpoint,
        region_name="eu-north-1",
        aws_access_key_id="local",  # DynamoDB Local accepts any credentials
        aws_secret_access_key="local",  # noqa: S106
        config=Config(max_pool_connections=100),
    )
    name = f"runtime-{uuid4().hex[:8]}"
    create_runtime_table(client, name)
    yield RuntimeTable(client=client, name=name)
    client.delete_table(TableName=name)


def test_exactly_the_burst_passes_when_100_requests_race(
    dynamodb_local: RuntimeTable, clock: FakeClock
) -> None:
    limiter = RateLimiter(dynamodb_local.client, dynamodb_local.name, clock)

    with ThreadPoolExecutor(max_workers=100) as pool:
        decisions = list(pool.map(lambda _: limiter.check(POLICY, "racer"), range(100)))

    assert not any(decision.degraded for decision in decisions)
    assert sum(decision.allowed for decision in decisions) == POLICY.burst
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `cd backend && uv run python -m pytest tests/unit tests/security -q`
Expected: FAIL. Collection errors say `No module named 'nettriage.application.rate_limits'` and `No module named 'nettriage.adapters.rate_limiter'`.

- [ ] **Step 3: Write the policies and the limiter**

`backend/src/nettriage/application/rate_limits.py`:
```python
"""Rate limiting with GCRA (spec §6.5).

For `limit` requests per `period`, the emission interval is T = period / limit and the burst
tolerance is tau = (burst - 1) * T. A request at `now` is allowed if tat - now <= tau, where
`tat` is the key's theoretical arrival time (`now` for a new or idle key); then
tat = max(tat, now) + T.
"""

from __future__ import annotations

import ipaddress
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class Policy:
    name: str
    limit: int
    period: timedelta
    burst: int

    @property
    def period_ms(self) -> int:
        return int(self.period.total_seconds() * 1000)

    @property
    def interval_ms(self) -> int:
        return self.period_ms // self.limit

    @property
    def tolerance_ms(self) -> int:
        return (self.burst - 1) * self.interval_ms


POLICIES: dict[str, Policy] = {
    policy.name: policy
    for policy in (
        Policy("api.user", 120, timedelta(minutes=1), 30),
        Policy("api.mutation.user", 30, timedelta(minutes=1), 10),
        Policy("auth.ip", 10, timedelta(minutes=1), 5),
        Policy("public.ip", 60, timedelta(minutes=1), 20),
        Policy("uploads.org", 20, timedelta(days=1), 5),
        Policy("invites.org", 20, timedelta(days=1), 5),
        Policy("ai.rerun.user", 10, timedelta(hours=1), 3),
    )
}


@dataclass(frozen=True)
class Decision:
    policy: Policy
    allowed: bool
    remaining: int = 0
    reset_seconds: int = 0
    retry_after_seconds: int = 0
    # True when the limiter couldn't decide and let the request through (fail open).
    degraded: bool = False


def allowed(policy: Policy, tat_ms: int, now_ms: int) -> Decision:
    """The decision after an allowed request moved the key's tat to `tat_ms`."""
    headroom = policy.tolerance_ms - (tat_ms - now_ms)
    remaining = headroom // policy.interval_ms + 1 if headroom >= 0 else 0
    return Decision(
        policy=policy,
        allowed=True,
        remaining=remaining,
        reset_seconds=math.ceil(max(0, tat_ms - now_ms) / 1000),
    )


def limited(policy: Policy, tat_ms: int, now_ms: int) -> Decision:
    """The decision for a request refused because the key's tat is `tat_ms`."""
    return Decision(
        policy=policy,
        allowed=False,
        reset_seconds=math.ceil(max(0, tat_ms - now_ms) / 1000),
        retry_after_seconds=max(1, math.ceil((tat_ms - policy.tolerance_ms - now_ms) / 1000)),
    )


def failed_open(policy: Policy) -> Decision:
    return Decision(policy=policy, allowed=True, degraded=True)


def header_values(decisions: Iterable[Decision]) -> dict[str, str]:
    """The IETF RateLimit-Policy and RateLimit fields (draft-ietf-httpapi-ratelimit-headers),
    one list member per policy that was checked. Fail-open decisions have nothing to report."""
    known = [decision for decision in decisions if not decision.degraded]
    if not known:
        return {}
    return {
        "RateLimit-Policy": ", ".join(
            f'"{d.policy.name}";q={d.policy.limit};w={d.policy.period_ms // 1000}' for d in known
        ),
        "RateLimit": ", ".join(
            f'"{d.policy.name}";r={d.remaining};t={d.reset_seconds}' for d in known
        ),
    }


def viewer_ip(viewer_address: str | None) -> str | None:
    """The client's IP from CloudFront's `CloudFront-Viewer-Address` header ("ip:port"). None
    if the header is missing or malformed."""
    if not viewer_address or ":" not in viewer_address:
        return None
    host = viewer_address.rsplit(":", 1)[0].strip("[]")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def ip_subject(viewer_address: str | None) -> str | None:
    """The rate-limit subject for a client IP. IPv6 clients are limited per /64, because one
    host can use a whole /64. None without a viewer address: requests that didn't come through
    CloudFront (local runs, the Lambda adapter's readiness check) aren't IP-limited."""
    ip = viewer_ip(viewer_address)
    if ip is None or ":" not in ip:
        return ip
    return str(ipaddress.ip_network(f"{ip}/64", strict=False))
```

`backend/src/nettriage/adapters/rate_limiter.py`:
```python
"""GCRA rate limits in the DynamoDB `runtime` table (spec §5.5, §6.5).

Each check is one or two conditional updates of `RL#<policy>#<subject>`, never a read followed
by a write, so parallel requests can't both take the last slot:

1. A new or idle key (no `tat`, or `tat` <= now) is set to now + T.
2. Otherwise `tat` grows by T, on condition that tat - now <= tau.

A failed condition returns the item as it was, which tells us whether the request is over the
limit. DynamoDB errors fail open: the request is allowed and the decision is marked `degraded`.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from botocore.exceptions import BotoCoreError, ClientError

from nettriage.adapters.runtime_table import epoch_millis, epoch_seconds, is_condition_failure
from nettriage.application.clock import Clock
from nettriage.application.rate_limits import Decision, Policy, allowed, failed_open, limited

if TYPE_CHECKING:
    from types_boto3_dynamodb.client import DynamoDBClient
    from types_boto3_dynamodb.type_defs import AttributeValueTypeDef

logger = logging.getLogger(__name__)

ATTEMPTS = 3
AUDIT_SAMPLE_WINDOW = timedelta(minutes=1)


class RateLimiter:
    def __init__(self, client: DynamoDBClient, table: str, clock: Clock) -> None:
        self._client = client
        self._table = table
        self._clock = clock

    def check(self, policy: Policy, subject: str) -> Decision:
        now_moment = self._clock()
        now = epoch_millis(now_moment)
        key: dict[str, AttributeValueTypeDef] = {"pk": {"S": f"RL#{policy.name}#{subject}"}}
        expires: AttributeValueTypeDef = {"N": str(epoch_seconds(now_moment + 2 * policy.period))}
        try:
            for _ in range(ATTEMPTS):
                fresh = now + policy.interval_ms
                succeeded, tat = self._update(
                    key,
                    "SET tat = :fresh, expires_at = :expires",
                    "attribute_not_exists(tat) OR tat <= :now",
                    {":fresh": {"N": str(fresh)}, ":now": {"N": str(now)}, ":expires": expires},
                )
                if succeeded:
                    return allowed(policy, fresh, now)
                if tat is not None and tat - now > policy.tolerance_ms:
                    return limited(policy, tat, now)
                succeeded, tat = self._update(
                    key,
                    "SET tat = tat + :interval, expires_at = :expires",
                    "tat > :now AND tat <= :latest",
                    {
                        ":interval": {"N": str(policy.interval_ms)},
                        ":now": {"N": str(now)},
                        ":latest": {"N": str(now + policy.tolerance_ms)},
                        ":expires": expires,
                    },
                )
                if succeeded and tat is not None:
                    return allowed(policy, tat, now)
                if tat is not None and tat - now > policy.tolerance_ms:
                    return limited(policy, tat, now)
        except BotoCoreError, ClientError:
            logger.warning("rate_limit_failed_open", extra={"policy": policy.name})
            return failed_open(policy)
        logger.warning("rate_limit_undecided", extra={"policy": policy.name})
        return failed_open(policy)

    def should_audit(self, policy: Policy, subject: str) -> bool:
        """True at most once per subject and policy per minute: the audit log samples
        `ratelimit.limited` (spec §9.4)."""
        now = self._clock()
        try:
            self._client.put_item(
                TableName=self._table,
                Item={
                    "pk": {"S": f"RLAUDIT#{policy.name}#{subject}"},
                    "expires_at": {"N": str(epoch_seconds(now + AUDIT_SAMPLE_WINDOW))},
                },
                ConditionExpression="attribute_not_exists(pk) OR expires_at <= :now",
                ExpressionAttributeValues={":now": {"N": str(epoch_seconds(now))}},
            )
        except ClientError as error:
            if is_condition_failure(error):
                return False
            logger.warning("rate_limit_audit_sample_failed", extra={"policy": policy.name})
            return False
        except BotoCoreError:
            logger.warning("rate_limit_audit_sample_failed", extra={"policy": policy.name})
            return False
        return True

    def _update(
        self,
        key: dict[str, AttributeValueTypeDef],
        update: str,
        condition: str,
        values: dict[str, AttributeValueTypeDef],
    ) -> tuple[bool, int | None]:
        """(True, new tat) on success; (False, current tat) on a failed condition, where the
        current tat is None if the item has none."""
        try:
            response = self._client.update_item(
                TableName=self._table,
                Key=key,
                UpdateExpression=update,
                ConditionExpression=condition,
                ExpressionAttributeValues=values,
                ReturnValues="UPDATED_NEW",
                ReturnValuesOnConditionCheckFailure="ALL_OLD",
            )
        except ClientError as error:
            if not is_condition_failure(error):
                raise
            item: dict[str, Any] | None = error.response.get("Item")  # type: ignore[assignment]
            if item is None:
                item = self._client.get_item(
                    TableName=self._table, Key=key, ConsistentRead=True
                ).get("Item")
            return False, _tat(item)
        return True, _tat(response.get("Attributes"))


def _tat(item: dict[str, Any] | None) -> int | None:
    if not item or "tat" not in item:
        return None
    return int(item["tat"]["N"])
```

- [ ] **Step 4: Give CI a DynamoDB Local, and record the design change**

In `.github/workflows/ci.yml`, replace:
```yaml
          --health-retries 10
    env:
      NETTRIAGE_TEST_DATABASE_URL: postgresql://postgres:postgres@localhost:5432/postgres
```
with:
```yaml
          --health-retries 10
      # Atomic conditional writes for the rate limiter's concurrency test (spec §11.4); moto's
      # in-process DynamoDB, which the other tests use, isn't atomic.
      dynamodb:
        image: amazon/dynamodb-local:3.3.1
        ports:
          - 8000:8000
    env:
      NETTRIAGE_TEST_DATABASE_URL: postgresql://postgres:postgres@localhost:5432/postgres
      NETTRIAGE_TEST_DYNAMODB_URL: http://localhost:8000
```

In `CLAUDE.md`, replace:
```markdown
Backend tests need Postgres: `just test` starts a local one first (`just db-up`, no Docker) and
`just db-down` stops it; CI uses a Postgres 17 service container.
```
with:
```markdown
Backend tests need Postgres: `just test` starts a local one first (`just db-up`, no Docker) and
`just db-down` stops it; CI uses a Postgres 17 service container. DynamoDB is mocked in-process
with moto; the rate limiter's concurrency test needs DynamoDB Local, so it runs only in CI and
is skipped locally.
```

In `docs/adr/0006-gcra-rate-limiter.md`, replace:
```markdown
- Status: Accepted
- Date: 2026-09-26
```
with:
```markdown
- Status: Accepted; storage amended 2026-09-28 (Plan 3b)
- Date: 2026-09-26
```

In `docs/adr/0006-gcra-rate-limiter.md`, replace:
```markdown
Rate limiting uses GCRA (Generic Cell Rate Algorithm) implemented on DynamoDB: each key
stores one theoretical arrival time (`tat`), updated with a conditional write on every check.
```
with:
```markdown
Rate limiting uses GCRA (Generic Cell Rate Algorithm) implemented on DynamoDB: each key
stores one theoretical arrival time (`tat`), changed only by conditional updates. A new or idle
key is set to now + T; otherwise `tat` grows by T on condition that `tat - now <= tau`. A failed
condition returns the stored item, which says whether the request is over the limit.
```

In `docs/adr/0006-gcra-rate-limiter.md`, replace:
```markdown
- GCRA is exact under concurrency: two Lambda instances checking the same key race on the
  same conditional write, so one of them always loses cleanly rather than both allowing a
  request that together exceeds the limit.
- Each check costs one read and one write against DynamoDB.
- On a write conflict, the check retries up to 3 times; if it still can't decide, it fails
  open: the request is allowed, and the failure is logged, counted and alerted on.
```
with:
```markdown
- GCRA is exact under concurrency: DynamoDB applies conditional updates of one item one at a
  time, so parallel requests can't both take the last slot. A test sends 100 parallel requests
  at DynamoDB Local in CI and expects exactly the burst to pass.
- Each check costs one or two writes and no reads: one for a new or idle key, two for an active
  key (the first condition fails, the second update succeeds), and one for a refused request.
- The first design (never built) read the item and then wrote it conditionally, retrying 3
  times on a conflict. Under contention it would run out of retries and fail open, letting more
  requests through than the limit allows, so Plan 3b replaced it before implementing it.
- If DynamoDB fails, the check fails open: the request is allowed, and the failure is logged
  and counted.
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
**Storage.** An eventually consistent read, followed by a conditional write (`attribute_not_exists(tat) OR tat = :old`). On a conflict it retries up to 3 times. If it still can't decide, it allows the request (fail open) and increments a metric.
```
with:
```markdown
**Storage.** Conditional updates, never a read followed by a write, so parallel requests can't both take the last slot (amended in Plan 3b: the original read-then-write design would fail open under exactly the contention the concurrency test creates):
1. A new or idle key (no `tat`, or `tat` ≤ now) is set to now + T.
2. Otherwise `tat` grows by T, on condition that `tat − now ≤ τ`.

A failed condition returns the item as it was, which tells whether the request is over the limit. If DynamoDB fails, the request is allowed (fail open) and a metric is incremented.
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
  - a rate-limiter concurrency test (100 parallel requests; exactly the allowed number pass),
```
with:
```markdown
  - a rate-limiter concurrency test (100 parallel requests; exactly the allowed number pass), run against DynamoDB Local in CI, because moto's in-process DynamoDB doesn't make conditional writes atomic,
```

- [ ] **Step 5: Run the checks**

Run: `just lint test pin-check cloud-check`
Expected:
- lint is clean;
- the tests end with `274 passed, 1 skipped`. `cd backend && uv run python -m pytest tests/security -rs` names the skip: `NETTRIAGE_TEST_DYNAMODB_URL isn't set; CI runs this against DynamoDB Local`;
- `All actions in .github\workflows are pinned.` and `CI holds no cloud access.`

- [ ] **Step 6: Commit**

```bash
git add backend/src backend/tests .github/workflows/ci.yml CLAUDE.md docs
git commit -m "feat(auth): GCRA rate limits on DynamoDB, exact under concurrency" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The Cognito OpenID Connect client

**Files:**
- Modify: `backend/pyproject.toml`, `backend/uv.lock` (via `uv add`)
- Create: `backend/src/nettriage/adapters/oidc.py`
- Test: `backend/tests/fake_idp.py` (a fake Cognito), `backend/tests/unit/adapters/test_oidc.py`

**Interfaces:**
- Consumes (Task 2): `Clock` and `FakeClock`.
- Produces:
  - In `nettriage.adapters.oidc`:
    - `OidcSettings(issuer, client_id, client_secret, domain, app_origin)`, with `.redirect_uri` (`<app_origin>/api/auth/callback`) and `.jwks_url`;
    - `Identity(sub, email)` and `OidcError`, whose messages never contain a token or a code;
    - `OidcClient(settings, http: httpx.Client, clock)`, with `.settings`, `.authorization_url(*, state, nonce, code_challenge)`, `.logout_url()`, `.identity(*, code, code_verifier, nonce) -> Identity` and `.verify(token, nonce) -> Identity`.
  - In `backend/tests/fake_idp.py`:
    - the constants `ISSUER`, `DOMAIN`, `CLIENT_ID`, `CLIENT_SECRET`, `APP_ORIGIN` and `KEY_ID`, and `settings() -> OidcSettings`;
    - `FakeIdentityProvider`, with `.issue_code(*, nonce, sub=…, email=…, **claims) -> str`, `.sign(claims, *, kid=…)`, `.transport()`, `.token_requests`, `.jwks_requests` and `.token_status`.

- [ ] **Step 1: Add the dependencies**

```bash
cd backend
uv add "pyjwt[crypto]" httpx
```

- [ ] **Step 2: Write the failing tests**

`backend/tests/fake_idp.py`:
```python
"""A fake Cognito for tests: its token endpoint and published keys, served through httpx's mock
transport. It signs real RS256 ID tokens, so the verifier under test is the production one."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from functools import cache
from typing import Any
from urllib.parse import parse_qs

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from nettriage.adapters.oidc import OidcSettings

ISSUER = "https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_Test"
DOMAIN = "https://nettriage-test.auth.eu-north-1.amazoncognito.com"
CLIENT_ID = "test-client-id"
CLIENT_SECRET = "test-client-secret"  # noqa: S105 - the fake provider's own secret
APP_ORIGIN = "https://app.test"
KEY_ID = "test-key"


@cache
def signing_key() -> rsa.RSAPrivateKey:
    """One RSA key for the whole test run: generating one takes a noticeable fraction of a
    second."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def settings() -> OidcSettings:
    return OidcSettings(
        issuer=ISSUER,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        domain=DOMAIN,
        app_origin=APP_ORIGIN,
    )


@dataclass
class FakeIdentityProvider:
    codes: dict[str, dict[str, Any]] = field(default_factory=dict)
    token_requests: list[dict[str, list[str]]] = field(default_factory=list)
    jwks_requests: int = 0
    token_status: int = 200
    issued: int = 0

    def issue_code(
        self, *, nonce: str, sub: str = "user-sub", email: str = "u@example.com", **claims: Any
    ) -> str:
        """A code the token endpoint will exchange for an ID token with these claims."""
        self.issued += 1
        code = f"code-{self.issued}"
        now = int(time.time())
        self.codes[code] = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": sub,
            "email": email,
            "email_verified": True,
            "token_use": "id",
            "nonce": nonce,
            "iat": now,
            "exp": now + 3600,
            **claims,
        }
        return code

    def sign(self, claims: dict[str, Any], *, kid: str = KEY_ID) -> str:
        return jwt.encode(claims, signing_key(), algorithm="RS256", headers={"kid": kid})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{ISSUER}/.well-known/jwks.json":
            self.jwks_requests += 1
            public = json.loads(RSAAlgorithm.to_jwk(signing_key().public_key()))
            return httpx.Response(
                200, json={"keys": [{**public, "kid": KEY_ID, "use": "sig", "alg": "RS256"}]}
            )
        if url == f"{DOMAIN}/oauth2/token" and request.method == "POST":
            form = parse_qs(request.content.decode())
            self.token_requests.append(form)
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            claims = self.codes.pop(form.get("code", [""])[0], None)
            if claims is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(
                200,
                json={
                    "id_token": self.sign(claims),
                    "access_token": "a",
                    "refresh_token": "r",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        return httpx.Response(404)
```

`backend/tests/unit/adapters/test_oidc.py`:
```python
import time
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from conftest import FakeClock
from fake_idp import CLIENT_ID, CLIENT_SECRET, FakeIdentityProvider, settings

from nettriage.adapters.oidc import OidcClient, OidcError


@pytest.fixture
def idp() -> FakeIdentityProvider:
    return FakeIdentityProvider()


@pytest.fixture
def oidc(idp: FakeIdentityProvider, clock: FakeClock) -> OidcClient:
    return OidcClient(settings(), httpx.Client(transport=idp.transport()), clock)


def query(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


def test_the_authorization_url_asks_for_a_code_with_pkce(oidc: OidcClient) -> None:
    url = oidc.authorization_url(state="s", nonce="n", code_challenge="c")

    assert url.startswith(
        "https://nettriage-test.auth.eu-north-1.amazoncognito.com/oauth2/authorize?"
    )
    assert query(url) == {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": "https://app.test/api/auth/callback",
        "scope": "openid email profile",
        "state": "s",
        "nonce": "n",
        "code_challenge": "c",
        "code_challenge_method": "S256",
    }


def test_the_logout_url_returns_to_the_app(oidc: OidcClient) -> None:
    url = oidc.logout_url()

    assert url.startswith("https://nettriage-test.auth.eu-north-1.amazoncognito.com/logout?")
    assert query(url) == {"client_id": CLIENT_ID, "logout_uri": "https://app.test/"}


def test_a_valid_code_gives_the_verified_identity(
    oidc: OidcClient, idp: FakeIdentityProvider
) -> None:
    code = idp.issue_code(nonce="n-1", sub="abc", email="Ada@Example.com")

    identity = oidc.identity(code=code, code_verifier="v-1", nonce="n-1")

    assert (identity.sub, identity.email) == ("abc", "Ada@Example.com")
    [form] = idp.token_requests
    assert form["code_verifier"] == ["v-1"]
    assert form["redirect_uri"] == ["https://app.test/api/auth/callback"]


def test_the_client_secret_is_sent_as_basic_auth_not_in_the_form(
    idp: FakeIdentityProvider, clock: FakeClock
) -> None:
    seen: list[httpx.Request] = []

    def spy(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return idp.transport().handle_request(request)

    client = OidcClient(settings(), httpx.Client(transport=httpx.MockTransport(spy)), clock)
    client.identity(code=idp.issue_code(nonce="n"), code_verifier="v", nonce="n")

    token_request = next(r for r in seen if r.url.path == "/oauth2/token")
    assert token_request.headers["authorization"].startswith("Basic ")
    assert CLIENT_SECRET not in token_request.content.decode()


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        ({"aud": "someone-else"}, "InvalidAudienceError"),
        ({"iss": "https://evil.example"}, "InvalidIssuerError"),
        ({"exp": int(time.time()) - 3600}, "ExpiredSignatureError"),
        ({"token_use": "access"}, "isn't an ID token"),
        ({"email_verified": False}, "isn't verified"),
        ({"email_verified": "false"}, "isn't verified"),
        ({"email": ""}, "no usable email"),
    ],
)
def test_a_bad_id_token_is_refused(
    oidc: OidcClient, idp: FakeIdentityProvider, claims: dict[str, Any], reason: str
) -> None:
    code = idp.issue_code(nonce="n-1", **claims)

    with pytest.raises(OidcError, match=reason):
        oidc.identity(code=code, code_verifier="v", nonce="n-1")


def test_an_id_token_for_another_sign_in_is_refused(
    oidc: OidcClient, idp: FakeIdentityProvider
) -> None:
    code = idp.issue_code(nonce="nonce-of-another-sign-in")

    with pytest.raises(OidcError, match="nonce doesn't match"):
        oidc.identity(code=code, code_verifier="v", nonce="n-1")


def test_a_token_signed_with_an_unknown_key_is_refused(
    oidc: OidcClient, idp: FakeIdentityProvider
) -> None:
    code = idp.issue_code(nonce="n")
    claims = idp.codes[code]
    forged = idp.sign(claims, kid="attacker-key")

    with pytest.raises(OidcError, match="signing key is unknown"):
        oidc.verify(forged, "n")


def test_an_unsigned_token_is_refused(oidc: OidcClient, idp: FakeIdentityProvider) -> None:
    claims = idp.codes[idp.issue_code(nonce="n")]
    unsigned = jwt.encode(claims, key="", algorithm="none")

    with pytest.raises(OidcError, match="RS256"):
        oidc.verify(unsigned, "n")


def test_a_rejected_code_is_an_error_without_the_code(
    oidc: OidcClient, idp: FakeIdentityProvider
) -> None:
    idp.token_status = 400

    with pytest.raises(OidcError, match="answered 400") as caught:
        oidc.identity(code="secret-code", code_verifier="v", nonce="n")

    assert "secret-code" not in str(caught.value)


def test_an_unreachable_cognito_is_an_error(clock: FakeClock) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    client = OidcClient(settings(), httpx.Client(transport=httpx.MockTransport(down)), clock)

    with pytest.raises(OidcError, match="couldn't be reached"):
        client.identity(code="c", code_verifier="v", nonce="n")


def test_keys_are_fetched_once_and_refetched_for_a_new_key_at_most_every_five_minutes(
    oidc: OidcClient, idp: FakeIdentityProvider, clock: FakeClock
) -> None:
    for _ in range(3):
        oidc.identity(code=idp.issue_code(nonce="n"), code_verifier="v", nonce="n")
    rotated = idp.sign(idp.codes[idp.issue_code(nonce="n")], kid="rotated")
    for _ in range(3):
        with pytest.raises(OidcError):
            oidc.verify(rotated, "n")
    assert idp.jwks_requests == 1

    clock.advance(timedelta(minutes=5))
    with pytest.raises(OidcError):
        oidc.verify(rotated, "n")

    assert idp.jwks_requests == 2
```

- [ ] **Step 3: Run the tests and watch them fail**

Run: `cd backend && uv run python -m pytest tests/unit/adapters/test_oidc.py -q`
Expected: FAIL: `No module named 'nettriage.adapters.oidc'`.

- [ ] **Step 4: Write the client**

`backend/src/nettriage/adapters/oidc.py`:
```python
"""Cognito as the OpenID Connect provider (spec §4.1, §6.1).

The backend runs the authorization-code flow with PKCE, exchanges the code with the client
secret, and verifies the ID token against Cognito's published keys: signature (RS256), issuer,
audience, expiry, nonce, `token_use` and `email_verified`. Cognito's tokens are then discarded.
Error messages never include a token or a code.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from nettriage.application.clock import Clock

SCOPES = "openid email profile"
JWKS_REFRESH_INTERVAL = timedelta(minutes=5)
CLOCK_LEEWAY = timedelta(seconds=30)
MAX_EMAIL = 320


@dataclass(frozen=True)
class OidcSettings:
    issuer: str  # https://cognito-idp.<region>.amazonaws.com/<user pool id>
    client_id: str
    client_secret: str
    domain: str  # https://<prefix>.auth.<region>.amazoncognito.com
    app_origin: str  # https://<cloudfront domain>

    @property
    def redirect_uri(self) -> str:
        return f"{self.app_origin}/api/auth/callback"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"


@dataclass(frozen=True)
class Identity:
    sub: str
    email: str


class OidcError(Exception):
    """Sign-in couldn't be completed. The message says why, without any token."""


class OidcClient:
    def __init__(self, settings: OidcSettings, http: httpx.Client, clock: Clock) -> None:
        self.settings = settings
        self._http = http
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: datetime | None = None

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.client_id,
                "redirect_uri": self.settings.redirect_uri,
                "scope": SCOPES,
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self.settings.domain}/oauth2/authorize?{query}"

    def logout_url(self) -> str:
        query = urlencode(
            {"client_id": self.settings.client_id, "logout_uri": f"{self.settings.app_origin}/"}
        )
        return f"{self.settings.domain}/logout?{query}"

    def identity(self, *, code: str, code_verifier: str, nonce: str) -> Identity:
        """Exchange the code and verify the ID token it returns."""
        return self.verify(self._exchange(code, code_verifier), nonce)

    def _exchange(self, code: str, code_verifier: str) -> str:
        try:
            response = self._http.post(
                f"{self.settings.domain}/oauth2/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": self.settings.client_id,
                    "code": code,
                    "redirect_uri": self.settings.redirect_uri,
                    "code_verifier": code_verifier,
                },
                auth=(self.settings.client_id, self.settings.client_secret),
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError:
            raise OidcError("the token endpoint couldn't be reached") from None
        if response.status_code != httpx.codes.OK:
            raise OidcError(f"the token endpoint answered {response.status_code}")
        try:
            id_token = response.json()["id_token"]
        except ValueError, KeyError, TypeError:
            raise OidcError("the token response had no ID token") from None
        if not isinstance(id_token, str):
            raise OidcError("the token response had no ID token")
        return id_token

    def verify(self, token: str, nonce: str) -> Identity:
        """Check an ID token's signature and claims, and return who it identifies."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise OidcError("the ID token is malformed") from None
        if header.get("alg") != "RS256":
            raise OidcError("the ID token isn't signed with RS256")
        key = self._key(str(header.get("kid", "")))
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self.settings.client_id,
                issuer=self.settings.issuer,
                leeway=CLOCK_LEEWAY,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "token_use", "nonce"]},
            )
        except jwt.PyJWTError as error:
            raise OidcError(f"the ID token was rejected ({type(error).__name__})") from None
        if claims["token_use"] != "id":  # noqa: S105 - a claim value, not a password
            raise OidcError("the token isn't an ID token")
        if not hmac.compare_digest(str(claims["nonce"]), nonce):
            raise OidcError("the ID token's nonce doesn't match")
        if claims.get("email_verified") not in (True, "true"):
            raise OidcError("the email address isn't verified")
        email = claims.get("email")
        if not isinstance(email, str) or not email or len(email) > MAX_EMAIL:
            raise OidcError("the ID token has no usable email address")
        return Identity(sub=str(claims["sub"]), email=email)

    def _key(self, kid: str) -> jwt.PyJWK:
        if kid not in self._keys and self._may_refresh():
            self._refresh()
        if kid not in self._keys:
            raise OidcError("the ID token's signing key is unknown")
        return self._keys[kid]

    def _may_refresh(self) -> bool:
        """Fetch the keys on first use, and again for an unknown key at most every 5 minutes,
        so tokens with made-up key IDs can't make us hammer Cognito."""
        return self._fetched_at is None or self._clock() - self._fetched_at >= JWKS_REFRESH_INTERVAL

    def _refresh(self) -> None:
        self._fetched_at = self._clock()
        try:
            response = self._http.get(self.settings.jwks_url)
            response.raise_for_status()
            keys = response.json()["keys"]
            self._keys = {
                key["kid"]: jwt.PyJWK(key)
                for key in keys
                if key.get("kty") == "RSA" and key.get("use", "sig") == "sig"
            }
        except httpx.HTTPError, ValueError, KeyError, TypeError, jwt.PyJWTError:
            raise OidcError("Cognito's signing keys couldn't be fetched") from None
```

- [ ] **Step 5: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `291 passed, 1 skipped`.

The OIDC tests cover the following:
- the authorize and logout URLs;
- a valid code gives the identity;
- the client secret goes only in the Basic auth header;
- eight bad ID tokens are refused: wrong audience, wrong issuer, expired, access token, email not verified (as `False` or `"false"`), and empty email;
- a nonce from another sign-in, an unknown signing key and an unsigned token are refused;
- a rejected code gives an error that doesn't repeat the code;
- an unreachable Cognito gives an error;
- the keys are fetched once, and fetched again for a new key at most every 5 minutes.

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/src backend/tests
git commit -m "feat(auth): Cognito code flow with PKCE and ID-token verification" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: The API: access rules, sign-in and sign-out, `/me`, and production wiring

**Files:**
- Create:
  - `backend/src/nettriage/platform/metrics.py`;
  - in `backend/src/nettriage/entrypoints/api/`: `services.py`, `cookies.py`, `auditing.py`, `access.py`, `wiring.py`, `routes/auth.py` and `routes/me.py`.
- Modify:
  - in `backend/src/nettriage/platform/`: `logging.py` and `config.py`;
  - in `backend/src/nettriage/entrypoints/api/`: `app.py`, `main.py` and `routes/health.py`;
  - the spec, §4.1.
- Move: `backend/tests/integration/tenantdata.py` to `backend/tests/tenantdata.py`, so the API tests can seed data too.
- Test:
  - Create `backend/tests/browser.py`.
  - Create, in `backend/tests/api/`: `test_sign_in_routes.py`, `test_sign_out_and_me_routes.py` and `test_rate_limited_routes.py`.
  - Create, in `backend/tests/security/`: `test_csrf.py`, `test_session_lifetime.py`, `test_route_access.py` and `test_sign_in_logs.py`.
  - Create `backend/tests/unit/api/test_wiring.py`.
  - Modify `backend/tests/conftest.py`, and in `backend/tests/unit/platform/`: `test_errors.py`, `test_telemetry.py` and `test_logging.py`.

**Interfaces:**
- Consumes:
  - from Task 1: `sign_in_user`, `load_me`, `record` and `AuditEvent`;
  - from Task 2: `SessionStore`, `LoginStateStore`, `Session`, the `session_key` and sign-in helpers, and `system_clock`;
  - from Task 3: `RateLimiter`, `POLICIES`, `ip_subject`, `viewer_ip` and `header_values`;
  - from Task 4: `OidcClient`, `OidcSettings` and `OidcError`.
- Produces:
  - `create_app(settings: Settings, services: Services, *, tracer_provider=None, meter_provider=None) -> FastAPI`. `services` is now required.
  - `Services(database, sessions, login_states, rate_limiter, oidc, clock, metrics)`, with `.app_origin`, and `get_services(request)`.
  - In `nettriage.entrypoints.api.access`:
    - `Access`, `Public(policy_name)`, `SignedIn`, `signed_in` and `CurrentSession = Annotated[Session, Depends(signed_in)]`;
    - `enforce(request, policy, subject, *, actor)`, `unauthorized()` and `unavailable(what)`.
    3c's permission checks build on these.
  - In `nettriage.entrypoints.api.auditing`: `audit(request, *, action, outcome, actor_user_id=None, details=None)`.
  - `AppMetrics(meter_provider=None)`, with the counters `signups`, `csrf_failed`, `rate_limited` and `rate_limit_errors`.
  - The `Settings` fields `runtime_table`, `oidc_parameter`, `oidc_secret_parameter` and `database_url_parameter` (from `NETTRIAGE_*`).
  - `build_services(settings, session=None) -> Services` and `MissingParameterError`, in `wiring.py`.
  - `QUIET_LOGGERS`, in `platform/logging.py`.
  - The routes:
    - `GET /api/auth/login?return_to=`, which answers 302 to Cognito and sets `__Host-sign-in`;
    - `GET /api/auth/callback?code&state`, which answers 302 to `return_to` and sets `__Host-session`, or 302 to `/?sign_in=expired|failed|disabled|unavailable`;
    - `POST /api/auth/logout` and `POST /api/auth/logout-all`, which answer `{"logout_url": …}`;
    - `GET /api/v1/me`, which answers `{"user": {id, email, display_name}, "memberships": [{org_id, name, slug, role}], "csrf_token"}`.
  - For tests:
    - the fixtures `idp`, `metric_reader`, `services`, `client` (no database) and `database_client`, plus `counter(reader, name)`;
    - `browser.start_sign_in`, `finish_sign_in`, `sign_in`, `csrf_headers`, `set_cookie`, `query` and `VIEWER`.

- [ ] **Step 1: Move the test data helpers**

```bash
git mv backend/tests/integration/tenantdata.py backend/tests/tenantdata.py
```

`tests/` itself is on the test path (as `conftest` is), so `from tenantdata import …` keeps working in the integration tests.

- [ ] **Step 2: Write the failing tests**

`backend/tests/conftest.py`:
```python
import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import boto3
import httpx
import pytest
from alembic import command
from alembic.config import Config
from fake_idp import FakeIdentityProvider
from fake_idp import settings as idp_settings
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, NumberDataPoint
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL

from nettriage.adapters.login_states import LoginStateStore
from nettriage.adapters.oidc import OidcClient
from nettriage.adapters.postgres import create_database_engine, engine_url
from nettriage.adapters.rate_limiter import RateLimiter
from nettriage.adapters.sessions import SessionStore
from nettriage.entrypoints.api.app import create_app
from nettriage.entrypoints.api.services import Services
from nettriage.platform.config import Settings
from nettriage.platform.metrics import AppMetrics

if TYPE_CHECKING:
    from types_boto3_dynamodb.client import DynamoDBClient

TEST_DATABASE_ENV = "NETTRIAGE_TEST_DATABASE_URL"
BACKEND = Path(__file__).resolve().parents[1]
APP_API_PASSWORD = "app-api-test-only"  # noqa: S105 - a throwaway password on a test server


@pytest.fixture
def settings() -> Settings:
    return Settings(stage="local", version="1.2.3-test")


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


# The DynamoDB `runtime` table, mocked in-process by moto (spec §11.4). The concurrency test in
# tests/security uses a real DynamoDB Local instead, because moto's writes aren't atomic.

RUNTIME_TABLE = "nettriage-test-runtime"
REGION = "eu-north-1"


@dataclass(frozen=True)
class RuntimeTable:
    client: DynamoDBClient
    name: str


def create_runtime_table(client: DynamoDBClient, name: str) -> None:
    """The same key schema as infra/modules/data."""
    client.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def runtime_table() -> Iterator[RuntimeTable]:
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        create_runtime_table(client, RUNTIME_TABLE)
        yield RuntimeTable(client=client, name=RUNTIME_TABLE)


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


# The API with everything it uses: moto for DynamoDB, a fake Cognito, and a database engine that
# never connects. `database_client` puts the test database behind the API instead.

APP_ORIGIN = "https://app.test"
NO_DATABASE = "postgresql://unused@db.nettriage.invalid/unused"  # fails fast if ever used


@pytest.fixture
def idp() -> FakeIdentityProvider:
    return FakeIdentityProvider()


@pytest.fixture
def metric_reader() -> InMemoryMetricReader:
    return InMemoryMetricReader()


@pytest.fixture
def services(
    runtime_table: RuntimeTable,
    clock: FakeClock,
    idp: FakeIdentityProvider,
    metric_reader: InMemoryMetricReader,
) -> Services:
    table, client = runtime_table.name, runtime_table.client
    return Services(
        database=create_database_engine(NO_DATABASE),
        sessions=SessionStore(client, table),
        login_states=LoginStateStore(client, table),
        rate_limiter=RateLimiter(client, table, clock),
        oidc=OidcClient(idp_settings(), httpx.Client(transport=idp.transport()), clock),
        clock=clock,
        metrics=AppMetrics(MeterProvider(metric_readers=[metric_reader])),
    )


@pytest.fixture
def client(settings: Settings, services: Services) -> TestClient:
    return TestClient(create_app(settings, services), base_url=APP_ORIGIN)


@pytest.fixture
def database_client(settings: Settings, services: Services, database: Database) -> TestClient:
    """The API with the test database behind it, as `app_api`."""
    return TestClient(
        create_app(settings, replace(services, database=database.app_api)), base_url=APP_ORIGIN
    )


def counter(reader: InMemoryMetricReader, name: str) -> int:
    """The total of a counter across all its attributes."""
    data = reader.get_metrics_data()
    if data is None:
        return 0
    return sum(
        int(point.value)
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
        if isinstance(point, NumberDataPoint)
    )
```

`backend/tests/browser.py`:
```python
"""Drive the sign-in flow the way a browser does: the API's login redirect, the fake Cognito's
code, and the callback. The test client keeps the session cookie like a browser would."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx2
from fake_idp import APP_ORIGIN, FakeIdentityProvider
from fastapi.testclient import TestClient

VIEWER = {"CloudFront-Viewer-Address": "203.0.113.7:4444"}


def query(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


def start_sign_in(
    client: TestClient, return_to: str | None = None, headers: dict[str, str] | None = None
) -> dict[str, str]:
    """GET /api/auth/login; returns the Cognito authorize URL's query (state, nonce, ...)."""
    params = {"return_to": return_to} if return_to else {}
    response = client.get("/api/auth/login", params=params, headers=headers, follow_redirects=False)
    assert response.status_code == 302, response.text
    return query(response.headers["location"])


def finish_sign_in(
    client: TestClient,
    idp: FakeIdentityProvider,
    login: dict[str, str],
    *,
    sub: str = "user-sub",
    email: str = "u@example.com",
) -> httpx2.Response:
    code = idp.issue_code(nonce=login["nonce"], sub=sub, email=email)
    return client.get(
        "/api/auth/callback",
        params={"code": code, "state": login["state"]},
        follow_redirects=False,
    )


def sign_in(
    client: TestClient,
    idp: FakeIdentityProvider,
    *,
    sub: str = "user-sub",
    email: str = "u@example.com",
) -> httpx2.Response:
    return finish_sign_in(client, idp, start_sign_in(client), sub=sub, email=email)


def csrf_headers(client: TestClient) -> dict[str, str]:
    """What the SPA sends on a state-changing request (spec Â§6.2, Â§7)."""
    token = client.get("/api/v1/me").json()["csrf_token"]
    return {"X-CSRF-Token": token, "Sec-Fetch-Site": "same-origin", "Origin": APP_ORIGIN}


def set_cookie(response: httpx2.Response, name: str) -> str | None:
    """The response's Set-Cookie header for `name`, if any."""
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header
    return None
```

`backend/tests/api/test_sign_in_routes.py`:
```python
"""Sign-in through Cognito (spec §4.1, §6.2, §6.3)."""

from uuid import uuid4

from browser import finish_sign_in, query, set_cookie, sign_in, start_sign_in
from conftest import Database, RuntimeTable, counter
from fake_idp import DOMAIN, FakeIdentityProvider
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from sqlalchemy import text

from nettriage.application.sessions import COOKIE_NAME
from nettriage.application.sign_in import code_challenge


def audit_rows(database: Database, action: str, email: str) -> list[tuple[str, dict[str, object]]]:
    with database.admin.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT a.outcome, a.details FROM audit_log a JOIN users u "
                "ON u.id = a.actor_user_id WHERE a.action = :action AND u.email = :email "
                "ORDER BY a.created_at"
            ),
            {"action": action, "email": email},
        ).all()
    return [(row.outcome, row.details) for row in rows]


def test_login_sends_the_browser_to_cognito_with_pkce(
    client: TestClient, runtime_table: RuntimeTable
) -> None:
    response = client.get(
        "/api/auth/login", params={"return_to": "/app/orgs"}, follow_redirects=False
    )

    assert response.status_code == 302
    assert response.headers["cache-control"] == "no-store"
    location = response.headers["location"]
    assert location.startswith(f"{DOMAIN}/oauth2/authorize?")
    login = query(location)
    stored = runtime_table.client.get_item(
        TableName=runtime_table.name, Key={"pk": {"S": f"LOGIN#{login['state']}"}}
    )["Item"]
    assert stored["return_to"]["S"] == "/app/orgs"
    assert code_challenge(stored["code_verifier"]["S"]) == login["code_challenge"]
    assert stored["nonce"]["S"] == login["nonce"]


def test_login_ignores_a_return_address_outside_the_app(
    client: TestClient, runtime_table: RuntimeTable
) -> None:
    login = start_sign_in(client, return_to="https://evil.example/")

    stored = runtime_table.client.get_item(
        TableName=runtime_table.name, Key={"pk": {"S": f"LOGIN#{login['state']}"}}
    )["Item"]
    assert stored["return_to"]["S"] == "/app"


def test_signing_in_creates_the_user_and_sets_the_session_cookie(
    database_client: TestClient,
    idp: FakeIdentityProvider,
    database: Database,
    metric_reader: InMemoryMetricReader,
) -> None:
    email = f"{uuid4().hex}@example.com"
    login = start_sign_in(database_client, return_to="/app/orgs")

    response = finish_sign_in(database_client, idp, login, sub=f"sub-{uuid4()}", email=email)

    assert response.status_code == 302
    assert response.headers["location"] == "/app/orgs"
    cookie = set_cookie(response, COOKIE_NAME)
    assert cookie is not None
    for attribute in ("Max-Age=43200", "Path=/", "Secure", "HttpOnly", "SameSite=lax"):
        assert attribute in cookie
    assert "Domain" not in cookie
    assert database_client.get("/api/v1/me").json()["user"]["email"] == email
    assert audit_rows(database, "auth.session_created", email) == [("success", {"new_user": True})]
    assert counter(metric_reader, "nettriage.signups") == 1


def test_signing_in_again_finds_the_same_user(
    database_client: TestClient, idp: FakeIdentityProvider, database: Database
) -> None:
    sub, email = f"sub-{uuid4()}", f"{uuid4().hex}@example.com"
    sign_in(database_client, idp, sub=sub, email=email)
    first = database_client.get("/api/v1/me").json()["user"]["id"]

    sign_in(database_client, idp, sub=sub, email=email)

    assert database_client.get("/api/v1/me").json()["user"]["id"] == first
    assert [details for _, details in audit_rows(database, "auth.session_created", email)] == [
        {"new_user": True},
        {"new_user": False},
    ]


def test_a_sign_in_state_works_only_once(
    database_client: TestClient, idp: FakeIdentityProvider
) -> None:
    login = start_sign_in(database_client)
    finish_sign_in(database_client, idp, login, sub=f"sub-{uuid4()}")

    replayed = finish_sign_in(database_client, idp, login, sub=f"sub-{uuid4()}")

    assert replayed.headers["location"] == "/?sign_in=expired"
    assert set_cookie(replayed, COOKIE_NAME) is None


def test_an_unknown_state_is_refused(client: TestClient) -> None:
    response = client.get(
        "/api/auth/callback", params={"code": "c", "state": "forged"}, follow_redirects=False
    )

    assert response.headers["location"] == "/?sign_in=expired"
    assert set_cookie(response, COOKIE_NAME) is None


def test_a_cancelled_sign_in_is_reported(client: TestClient) -> None:
    login = start_sign_in(client)

    response = client.get(
        "/api/auth/callback",
        params={"error": "access_denied", "state": login["state"]},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/?sign_in=failed"


def test_an_id_token_for_another_sign_in_is_refused(
    client: TestClient, idp: FakeIdentityProvider
) -> None:
    login = start_sign_in(client)
    code = idp.issue_code(nonce="nonce-from-another-sign-in")

    response = client.get(
        "/api/auth/callback",
        params={"code": code, "state": login["state"]},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/?sign_in=failed"
    assert set_cookie(response, COOKIE_NAME) is None


def test_a_disabled_account_cannot_sign_in(
    database_client: TestClient, idp: FakeIdentityProvider, database: Database
) -> None:
    sub, email = f"sub-{uuid4()}", f"{uuid4().hex}@example.com"
    sign_in(database_client, idp, sub=sub, email=email)
    with database.admin.begin() as connection:
        connection.execute(
            text("UPDATE users SET disabled_at = now() WHERE cognito_sub = :sub"), {"sub": sub}
        )
    database_client.cookies.clear()

    response = sign_in(database_client, idp, sub=sub, email=email)

    assert response.headers["location"] == "/?sign_in=disabled"
    assert set_cookie(response, COOKIE_NAME) is None
    assert audit_rows(database, "auth.session_created", email)[-1] == ("denied", {})


def test_every_sign_in_gets_a_new_session_and_ends_the_one_the_browser_had(
    database_client: TestClient, idp: FakeIdentityProvider
) -> None:
    """Session fixation: a session ID the browser held before signing in never survives it."""
    sub = f"sub-{uuid4()}"
    sign_in(database_client, idp, sub=sub)
    before = database_client.cookies["__Host-session"]

    sign_in(database_client, idp, sub=sub)
    after = database_client.cookies["__Host-session"]

    assert after != before
    database_client.cookies.set("__Host-session", before, domain="app.test")
    assert database_client.get("/api/v1/me").status_code == 401


def test_a_sign_in_finished_in_another_browser_is_refused(
    database_client: TestClient, idp: FakeIdentityProvider
) -> None:
    """Login CSRF: an attacker starts a sign-in, then sends the victim the callback link with the
    attacker's code. The victim's browser didn't start that sign-in, so it must not finish it."""
    attacker_login = start_sign_in(database_client)
    code = idp.issue_code(nonce=attacker_login["nonce"], sub=f"sub-{uuid4()}")
    victim = TestClient(database_client.app, base_url="https://app.test")

    response = victim.get(
        "/api/auth/callback",
        params={"code": code, "state": attacker_login["state"]},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/?sign_in=expired"
    assert COOKIE_NAME not in victim.cookies


def test_the_sign_in_cookie_lives_five_minutes_and_the_callback_clears_it(
    database_client: TestClient, idp: FakeIdentityProvider
) -> None:
    login = database_client.get("/api/auth/login", follow_redirects=False)
    cookie = set_cookie(login, "__Host-sign-in")
    assert cookie is not None
    for attribute in ("Max-Age=300", "Path=/", "Secure", "HttpOnly", "SameSite=lax"):
        assert attribute in cookie

    callback = finish_sign_in(
        database_client, idp, query(login.headers["location"]), sub=f"sub-{uuid4()}"
    )

    assert set_cookie(callback, "__Host-sign-in") == (
        "__Host-sign-in=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Lax"
    )
```

`backend/tests/api/test_sign_out_and_me_routes.py`:
```python
"""The signed-in user, and signing out (spec §6.2, §7)."""

from uuid import uuid4

from browser import csrf_headers, sign_in
from conftest import Database
from fake_idp import CLIENT_ID, DOMAIN, FakeIdentityProvider
from fastapi.testclient import TestClient
from sqlalchemy import text
from tenantdata import add_member, add_org

from nettriage.application.sessions import COOKIE_NAME


def signed_in(client: TestClient, idp: FakeIdentityProvider) -> str:
    """Sign in a new user; returns their email."""
    email = f"{uuid4().hex}@example.com"
    sign_in(client, idp, sub=f"sub-{uuid4()}", email=email)
    return email


def test_me_shows_the_user_their_memberships_and_the_csrf_token(
    database_client: TestClient, idp: FakeIdentityProvider, database: Database
) -> None:
    email = signed_in(database_client, idp)
    user_id = database_client.get("/api/v1/me").json()["user"]["id"]
    with database.admin.begin() as connection:
        org = add_org(connection, user_id)
        add_member(connection, org, user_id, "admin")

    response = database_client.get("/api/v1/me")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["user"] == {"id": user_id, "email": email, "display_name": None}
    assert body["memberships"] == [
        {"org_id": str(org), "name": "Org", "slug": f"org-{org.hex}", "role": "admin"}
    ]
    assert len(body["csrf_token"]) == 43


def test_me_without_a_session_is_401_problem_details(client: TestClient) -> None:
    response = client.get("/api/v1/me")

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"


def test_an_unknown_session_cookie_is_401_and_cleared(client: TestClient) -> None:
    client.cookies.set(COOKIE_NAME, "made-up", domain="app.test")

    response = client.get("/api/v1/me")

    assert response.status_code == 401
    assert response.headers["set-cookie"].startswith(f"{COOKIE_NAME}=; Max-Age=0")


def test_signing_out_ends_the_session_and_returns_cognitos_logout_url(
    database_client: TestClient, idp: FakeIdentityProvider, database: Database
) -> None:
    email = signed_in(database_client, idp)
    headers = csrf_headers(database_client)

    response = database_client.post("/api/auth/logout", headers=headers)

    assert response.status_code == 200
    assert response.json()["logout_url"].startswith(f"{DOMAIN}/logout?client_id={CLIENT_ID}")
    assert response.headers["set-cookie"].startswith(f"{COOKIE_NAME}=; Max-Age=0")
    assert database_client.get("/api/v1/me").status_code == 401
    with database.admin.begin() as connection:
        actions: list[str] = list(
            connection.execute(
                text(
                    "SELECT a.action FROM audit_log a JOIN users u ON u.id = a.actor_user_id "
                    "WHERE u.email = :email ORDER BY a.created_at"
                ),
                {"email": email},
            ).scalars()
        )
        assert actions == ["auth.session_created", "auth.logout"]


def test_signing_out_everywhere_ends_every_session_of_the_user(
    database_client: TestClient, idp: FakeIdentityProvider, database: Database
) -> None:
    sub = f"sub-{uuid4()}"
    sign_in(database_client, idp, sub=sub)
    laptop = database_client.cookies[COOKIE_NAME]
    database_client.cookies.clear()
    sign_in(database_client, idp, sub=sub)  # a second browser

    response = database_client.post("/api/auth/logout-all", headers=csrf_headers(database_client))

    assert response.status_code == 200
    database_client.cookies.set(COOKIE_NAME, laptop, domain="app.test")
    assert database_client.get("/api/v1/me").status_code == 401
    with database.admin.begin() as connection:
        details: dict[str, object] = connection.execute(
            text(
                "SELECT a.details FROM audit_log a JOIN users u ON u.id = a.actor_user_id "
                "WHERE u.cognito_sub = :sub AND a.action = 'auth.logout_all'"
            ),
            {"sub": sub},
        ).scalar_one()
    assert details == {"sessions": 2}
```

`backend/tests/api/test_rate_limited_routes.py`:
```python
"""Rate limits on the API's routes (spec §6.5, §9.4)."""

from uuid import uuid4

from browser import VIEWER, sign_in
from conftest import Database, RuntimeTable, counter
from fake_idp import FakeIdentityProvider
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from sqlalchemy import text


def test_public_routes_report_their_limit_per_client_ip(client: TestClient) -> None:
    response = client.get("/api/health", headers=VIEWER)

    assert response.headers["ratelimit-policy"] == '"public.ip";q=60;w=60'
    assert response.headers["ratelimit"] == '"public.ip";r=19;t=1'


def test_requests_that_bypassed_cloudfront_are_not_ip_limited(client: TestClient) -> None:
    """The Lambda adapter's readiness check calls /api/health from inside the function."""
    response = client.get("/api/health")

    assert response.status_code == 200
    assert "ratelimit" not in response.headers


def test_too_many_sign_in_attempts_get_429_with_retry_after(
    database_client: TestClient, database: Database, metric_reader: InMemoryMetricReader
) -> None:
    viewer = {"CloudFront-Viewer-Address": f"198.51.100.{uuid4().int % 250 + 1}:1234"}
    responses = [
        database_client.get("/api/auth/login", headers=viewer, follow_redirects=False)
        for _ in range(7)
    ]

    assert [r.status_code for r in responses] == [302] * 5 + [429, 429]
    limited = responses[5]
    assert limited.headers["content-type"] == "application/problem+json"
    assert limited.headers["retry-after"] == "6"
    assert limited.headers["ratelimit"] == '"auth.ip";r=0;t=30'
    assert counter(metric_reader, "nettriage.ratelimit.limited") == 2
    ip = viewer["CloudFront-Viewer-Address"].split(":")[0]
    with database.admin.begin() as connection:
        audited: int = connection.execute(
            text(
                "SELECT count(*) FROM audit_log WHERE action = 'ratelimit.limited' "
                "AND host(ip) = :ip AND actor_type = 'anonymous'"
            ),
            {"ip": ip},
        ).scalar_one()
    assert audited == 1  # sampled: at most once a minute per subject


def test_signed_in_requests_are_limited_per_user(
    database_client: TestClient, idp: FakeIdentityProvider
) -> None:
    sign_in(database_client, idp, sub=f"sub-{uuid4()}")

    response = database_client.get("/api/v1/me")

    assert response.headers["ratelimit-policy"] == '"api.user";q=120;w=60'


def test_a_broken_limiter_lets_requests_through_and_counts_it(
    client: TestClient, runtime_table: RuntimeTable, metric_reader: InMemoryMetricReader
) -> None:
    runtime_table.client.delete_table(TableName=runtime_table.name)

    response = client.get("/api/health", headers=VIEWER)

    assert response.status_code == 200
    assert counter(metric_reader, "nettriage.ratelimit.errors") == 1
```

`backend/tests/security/test_csrf.py`:
```python
"""CSRF protection on state-changing requests (spec §6.2): the session's token in X-CSRF-Token,
Sec-Fetch-Site same-origin or none, and Origin, when present, equal to the app's origin."""

from uuid import uuid4

import pytest
from browser import csrf_headers, sign_in
from conftest import counter
from fake_idp import FakeIdentityProvider
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


@pytest.fixture
def signed_in_client(database_client: TestClient, idp: FakeIdentityProvider) -> TestClient:
    sign_in(database_client, idp, sub=f"sub-{uuid4()}")
    return database_client


def test_the_right_token_from_the_app_itself_is_accepted(signed_in_client: TestClient) -> None:
    response = signed_in_client.post("/api/auth/logout", headers=csrf_headers(signed_in_client))

    assert response.status_code == 200


def test_a_request_the_user_typed_or_bookmarked_is_accepted(signed_in_client: TestClient) -> None:
    headers = {**csrf_headers(signed_in_client), "Sec-Fetch-Site": "none"}
    del headers["Origin"]

    assert signed_in_client.post("/api/auth/logout", headers=headers).status_code == 200


@pytest.mark.parametrize(
    "change",
    [
        {"X-CSRF-Token": None},
        {"X-CSRF-Token": "wrong"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Sec-Fetch-Site": None},
        {"Origin": "https://evil.example"},
    ],
)
def test_a_forged_request_is_refused(
    signed_in_client: TestClient,
    metric_reader: InMemoryMetricReader,
    change: dict[str, str | None],
) -> None:
    headers = csrf_headers(signed_in_client)
    for name, value in change.items():
        if value is None:
            del headers[name]
        else:
            headers[name] = value

    response = signed_in_client.post("/api/auth/logout", headers=headers)

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert signed_in_client.get("/api/v1/me").status_code == 200  # still signed in
    assert counter(metric_reader, "nettriage.csrf.failed") == 1
```

`backend/tests/security/test_session_lifetime.py`:
```python
"""Session expiry and failure handling (spec §5.5, §6.2)."""

from datetime import timedelta
from uuid import uuid4

from browser import sign_in
from conftest import FakeClock, RuntimeTable
from fake_idp import FakeIdentityProvider
from fastapi.testclient import TestClient

from nettriage.application.sessions import COOKIE_NAME, session_key


def stored_last_seen(table: RuntimeTable, session_id: str) -> str:
    item = table.client.get_item(
        TableName=table.name, Key={"pk": {"S": f"SESSION#{session_key(session_id)}"}}
    )["Item"]
    return item["last_seen_at"]["N"]


def test_a_session_idle_for_sixty_minutes_is_ended(
    database_client: TestClient, idp: FakeIdentityProvider, clock: FakeClock
) -> None:
    sign_in(database_client, idp, sub=f"sub-{uuid4()}")
    clock.advance(timedelta(minutes=60))

    assert database_client.get("/api/v1/me").status_code == 401

    clock.advance(timedelta(minutes=-30))  # the session was deleted, not just refused
    assert database_client.get("/api/v1/me").status_code == 401


def test_activity_keeps_a_session_alive_but_not_past_twelve_hours(
    database_client: TestClient, idp: FakeIdentityProvider, clock: FakeClock
) -> None:
    sign_in(database_client, idp, sub=f"sub-{uuid4()}")
    for _ in range(23):  # every 30 minutes for 11.5 hours
        clock.advance(timedelta(minutes=30))
        assert database_client.get("/api/v1/me").status_code == 200

    clock.advance(timedelta(minutes=30))

    assert database_client.get("/api/v1/me").status_code == 401


def test_activity_is_written_at_most_every_five_minutes(
    database_client: TestClient,
    idp: FakeIdentityProvider,
    clock: FakeClock,
    runtime_table: RuntimeTable,
) -> None:
    sign_in(database_client, idp, sub=f"sub-{uuid4()}")
    session_id = database_client.cookies[COOKIE_NAME]
    first = stored_last_seen(runtime_table, session_id)

    clock.advance(timedelta(minutes=4))
    database_client.get("/api/v1/me")
    unchanged = stored_last_seen(runtime_table, session_id)
    clock.advance(timedelta(minutes=1))
    database_client.get("/api/v1/me")

    assert unchanged == first
    assert stored_last_seen(runtime_table, session_id) != first


def test_an_unreadable_session_store_fails_closed(
    database_client: TestClient, idp: FakeIdentityProvider, runtime_table: RuntimeTable
) -> None:
    sign_in(database_client, idp, sub=f"sub-{uuid4()}")
    runtime_table.client.delete_table(TableName=runtime_table.name)

    response = database_client.get("/api/v1/me")

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
```

`backend/tests/security/test_route_access.py`:
```python
"""Deny by default (spec §6.4, OWASP API5): every route declares who may call it."""

from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient

from nettriage.entrypoints.api.access import Access, Public, SignedIn

# Every API route and its declared access. A new route fails the test until it is added here.
EXPECTED = {
    ("GET", "/api/health"): "public.ip",
    ("GET", "/api/auth/login"): "auth.ip",
    ("GET", "/api/auth/callback"): "auth.ip",
    ("POST", "/api/auth/logout"): "signed in",
    ("POST", "/api/auth/logout-all"): "signed in",
    ("GET", "/api/v1/me"): "signed in",
}
# FastAPI's interactive docs, which only `local` and `dev` serve (spec §7).
DOCS: set[str | None] = {"/api/docs", "/api/openapi.json"}


def declared_access(route: APIRoute) -> list[Access]:
    found: list[Access] = []
    pending = [route.dependant]
    while pending:
        dependant = pending.pop()
        if isinstance(dependant.call, Access):
            found.append(dependant.call)
        pending.extend(dependant.dependencies)
    return found


def describe(access: Access) -> str:
    if isinstance(access, Public):
        return access.policy.name
    assert isinstance(access, SignedIn)
    return "signed in"


def test_every_route_declares_exactly_one_access_rule(client: TestClient) -> None:
    app: FastAPI = client.app  # type: ignore[assignment]
    actual: dict[tuple[str, str], str] = {}
    others: set[str | None] = set()
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if not isinstance(route, APIRoute):
            others.add(context.path)
            continue
        access = {describe(rule) for rule in declared_access(route)}
        assert len(access) == 1, f"{context.path} declares {access or 'no access rule'}"
        for method in context.methods or ():
            actual[(method, str(context.path))] = next(iter(access))

    assert actual == EXPECTED
    assert others <= DOCS
```

`backend/tests/security/test_sign_in_logs.py`:
```python
"""Sign-in never writes a secret or an email to the logs (spec §9.3), even when it fails."""

import io
import logging
from collections.abc import Iterator
from uuid import uuid4

import pytest
from browser import csrf_headers, finish_sign_in, start_sign_in
from fake_idp import FakeIdentityProvider
from fastapi.testclient import TestClient

from nettriage.application.sessions import COOKIE_NAME
from nettriage.platform.config import Settings
from nettriage.platform.logging import QUIET_LOGGERS, configure_logging


@pytest.fixture
def logs() -> Iterator[io.StringIO]:
    """The production logging setup at DEBUG, the most a stage could ever log, written to a
    buffer instead of stdout."""
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    quiet = {name: logging.getLogger(name).level for name in QUIET_LOGGERS}
    configure_logging(Settings(), level=logging.DEBUG)
    buffer = io.StringIO()
    handler = root.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    handler.setStream(buffer)
    yield buffer
    root.handlers, root.level = saved[0], saved[1]
    for name, level in quiet.items():
        logging.getLogger(name).setLevel(level)


def test_a_sign_in_and_out_log_no_secret_and_no_email(
    database_client: TestClient,
    idp: FakeIdentityProvider,
    logs: io.StringIO,
) -> None:
    email = f"{uuid4().hex}@example.com"
    login = start_sign_in(database_client)
    finish_sign_in(database_client, idp, login, sub=f"sub-{uuid4()}", email=email)
    session_id = database_client.cookies[COOKIE_NAME]
    headers = csrf_headers(database_client)
    failed = start_sign_in(database_client)
    database_client.get(
        "/api/auth/callback",
        params={"code": "leaked-code", "state": failed["state"]},
        follow_redirects=False,
    )
    database_client.post("/api/auth/logout", headers=headers)

    lines = logs.getvalue().splitlines()
    secrets = [
        email,
        session_id,
        headers["X-CSRF-Token"],
        login["state"],
        login["nonce"],
        failed["state"],
        "leaked-code",
    ]
    assert any('"sign_in_failed"' in line for line in lines)
    leaked = [line for line in lines if any(secret in line for secret in secrets)]
    assert leaked == []
```

`backend/tests/unit/api/test_wiring.py`:
```python
import json
from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws

from nettriage.entrypoints.api.wiring import MissingParameterError, build_services
from nettriage.platform.config import Settings

SETTINGS = Settings(
    stage="dev",
    runtime_table="nettriage-dev-runtime",
    oidc_parameter="/nettriage/dev/api/oidc",
    oidc_secret_parameter="/nettriage/dev/api/oidc-client-secret",  # noqa: S106 - a parameter name
    database_url_parameter="/nettriage/dev/db/app-api-url",
)
OIDC = {
    "issuer": "https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_Abc",
    "client_id": "client-1",
    "domain": "https://nettriage-dev-1234.auth.eu-north-1.amazoncognito.com",
    "app_origin": "https://d111111abcdef8.cloudfront.net",
}


@pytest.fixture
def session() -> Iterator[boto3.session.Session]:
    with mock_aws():
        session = boto3.session.Session(region_name="eu-north-1")
        ssm = session.client("ssm")
        ssm.put_parameter(Name=SETTINGS.oidc_parameter, Value=json.dumps(OIDC), Type="String")
        ssm.put_parameter(
            Name=SETTINGS.oidc_secret_parameter, Value="client-secret", Type="SecureString"
        )
        yield session


def test_services_are_built_from_ssm(session: boto3.session.Session) -> None:
    session.client("ssm").put_parameter(
        Name=SETTINGS.database_url_parameter,
        Value="postgresql://app_api:pw@ep-x-pooler.eu-central-1.aws.neon.tech/neondb",
        Type="SecureString",
    )

    services = build_services(SETTINGS, session)

    oidc = services.oidc.settings
    assert (oidc.issuer, oidc.client_id, oidc.domain, oidc.app_origin) == tuple(OIDC.values())
    assert oidc.client_secret == "client-secret"  # noqa: S105 - the fake secret put above
    assert services.database.url.host == "ep-x-pooler.eu-central-1.aws.neon.tech"
    assert services.database.pool.size() == 1  # type: ignore[attr-defined]


def test_a_missing_parameter_is_named_without_any_value(session: boto3.session.Session) -> None:
    with pytest.raises(MissingParameterError, match="/nettriage/dev/db/app-api-url") as caught:
        build_services(SETTINGS, session)

    assert "client-secret" not in str(caught.value)
```

`backend/tests/unit/platform/test_errors.py`:
```python
import logging
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nettriage.entrypoints.api.app import create_app
from nettriage.entrypoints.api.services import Services
from nettriage.platform.config import Settings
from nettriage.platform.logging import JsonFormatter

PROBLEM_JSON = "application/problem+json"


def test_unknown_route_returns_problem_details(client: TestClient) -> None:
    response = client.get("/api/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON
    body = response.json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Not Found"
    assert body["status"] == 404
    assert body["instance"] == "/api/does-not-exist"
    assert "trace_id" in body


def test_wrong_method_returns_problem_details_with_allow_header(client: TestClient) -> None:
    response = client.post("/api/health")

    assert response.status_code == 405
    assert response.headers["content-type"] == PROBLEM_JSON
    assert "GET" in response.headers["allow"]
    assert response.json()["title"] == "Method Not Allowed"


def test_unhandled_error_hides_internals(
    settings: Settings, services: Services, capsys: pytest.CaptureFixture[str]
) -> None:
    app: FastAPI = create_app(settings, services)

    @app.get("/api/boom")
    def boom() -> None:
        message = "database password is hunter2"
        raise RuntimeError(message)

    logger = logging.getLogger("nettriage.platform.errors")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(settings.service_name, settings.stage))
    logger.addHandler(handler)
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/boom")
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 500
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["title"] == "Internal Server Error"
    assert "hunter2" not in response.text
    assert "Traceback" not in response.text

    log_output = capsys.readouterr().out
    assert "hunter2" not in log_output
    assert "RuntimeError" in log_output


def test_request_validation_errors_are_problem_details(
    settings: Settings, services: Services
) -> None:
    app: FastAPI = create_app(settings, services)

    @app.get("/api/test-validation")
    def typed_route(n: int) -> dict[str, int]:
        return {"n": n}

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/test-validation", params={"n": "not-a-number-secret"})

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    body = response.json()
    assert body["status"] == 422
    assert body["errors"][0]["loc"][-1] == "n"
    assert "not-a-number-secret" not in response.text


def test_api_docs_are_disabled_in_prod(services: Services) -> None:
    client = TestClient(create_app(Settings(stage="prod", version="1"), services))

    assert client.get("/api/docs").status_code == 404
    assert client.get("/api/openapi.json").status_code == 404
```

`backend/tests/unit/platform/test_telemetry.py`:
```python
from collections.abc import Sequence

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from nettriage.entrypoints.api.app import create_app
from nettriage.entrypoints.api.services import Services
from nettriage.platform.config import Settings
from nettriage.platform.telemetry import create_meter_provider, create_tracer_provider

SETTINGS = Settings(stage="local", version="9.9.9")


def _client(
    services: Services,
    tracer_provider: TracerProvider,
    reader: InMemoryMetricReader | None = None,
) -> TestClient:
    meter_provider = create_meter_provider(SETTINGS, reader or InMemoryMetricReader())
    app = create_app(
        SETTINGS, services, tracer_provider=tracer_provider, meter_provider=meter_provider
    )
    return TestClient(app)


def _server_spans(exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    return [s for s in exporter.get_finished_spans() if s.kind == SpanKind.SERVER]


def test_requests_produce_a_server_span_with_resource_attributes(services: Services) -> None:
    exporter = InMemorySpanExporter()
    tracer_provider = create_tracer_provider(SETTINGS, exporter)

    _client(services, tracer_provider).get("/api/health")
    tracer_provider.force_flush()

    [span] = _server_spans(exporter)
    assert span.attributes is not None
    assert span.attributes.get("http.route") == "/api/health"
    assert span.resource.attributes["service.name"] == "nettriage-api"
    assert span.resource.attributes["service.version"] == "9.9.9"
    assert span.resource.attributes["deployment.environment.name"] == "local"


def test_problem_details_carry_the_request_trace_id(services: Services) -> None:
    exporter = InMemorySpanExporter()
    tracer_provider = create_tracer_provider(SETTINGS, exporter)

    body = _client(services, tracer_provider).get("/api/nope").json()
    tracer_provider.force_flush()

    [span] = _server_spans(exporter)
    assert body["trace_id"] == format(span.context.trace_id, "032x")


def test_http_server_metrics_are_recorded(services: Services) -> None:
    reader = InMemoryMetricReader()

    tracer_provider = create_tracer_provider(SETTINGS, InMemorySpanExporter())
    _client(services, tracer_provider, reader).get("/api/health")

    data = reader.get_metrics_data()
    assert data is not None
    names = {m.name for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics}
    assert names & {"http.server.request.duration", "http.server.duration"}


class FailingExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        raise ConnectionError("telemetry backend unreachable")

    def shutdown(self) -> None:
        return None


def test_requests_succeed_when_the_telemetry_backend_is_down(
    monkeypatch: pytest.MonkeyPatch, services: Services
) -> None:
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "nettriage-test-api")  # synchronous export

    tracer_provider = create_tracer_provider(SETTINGS, FailingExporter())
    response = _client(services, tracer_provider).get("/api/health")

    assert response.status_code == 200
```

In `backend/tests/unit/platform/test_logging.py`, replace:
```python
from nettriage.platform.logging import REDACTED, JsonFormatter, _sanitize_message, configure_logging
```
with:
```python
from nettriage.platform.logging import (
    QUIET_LOGGERS,
    REDACTED,
    JsonFormatter,
    _sanitize_message,
    configure_logging,
)
```

Append to `backend/tests/unit/platform/test_logging.py`:
```python


def test_http_and_aws_clients_only_log_warnings_even_at_debug() -> None:
    """botocore logs every DynamoDB item it writes at DEBUG, sessions included."""
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    quiet = {name: logging.getLogger(name).level for name in QUIET_LOGGERS}
    try:
        configure_logging(Settings(stage="local", version="t"), level=logging.DEBUG)

        levels = {name: logging.getLogger(name).getEffectiveLevel() for name in QUIET_LOGGERS}
        assert set(levels) >= {"botocore", "httpx"}
        assert set(levels.values()) == {logging.WARNING}
        assert logging.getLogger("nettriage").getEffectiveLevel() == logging.DEBUG
    finally:
        root.handlers, root.level = saved
        for name, level in quiet.items():
            logging.getLogger(name).setLevel(level)
```

- [ ] **Step 3: Run the tests and watch them fail**

Run: `just test`
Expected: FAIL before any test runs: `ImportError while loading conftest …` with `No module named 'nettriage.entrypoints.api.services'`.

- [ ] **Step 4: Write the metrics, the settings and the quieter client loggers**

`backend/src/nettriage/platform/metrics.py`:
```python
"""The app's own counters (spec §9.2). Attributes never carry user IDs, org IDs, IPs or free
text, which keeps Grafana's free tier under its series limit."""

from opentelemetry.metrics import MeterProvider, get_meter_provider


class AppMetrics:
    def __init__(self, meter_provider: MeterProvider | None = None) -> None:
        meter = (meter_provider or get_meter_provider()).get_meter("nettriage")
        self.signups = meter.create_counter(
            "nettriage.signups", description="Users who signed in for the first time"
        )
        self.csrf_failed = meter.create_counter(
            "nettriage.csrf.failed", description="State-changing requests refused by CSRF checks"
        )
        self.rate_limited = meter.create_counter(
            "nettriage.ratelimit.limited", description="Requests refused with 429, by policy"
        )
        self.rate_limit_errors = meter.create_counter(
            "nettriage.ratelimit.errors",
            description="Rate-limit checks that failed and let the request through, by policy",
        )
```

`backend/src/nettriage/platform/config.py`:
```python
"""Runtime settings, read from NETTRIAGE_* environment variables."""

import os
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Stage = Literal["local", "dev", "prod"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NETTRIAGE_", frozen=True)

    stage: Stage = "local"
    version: str = "0.0.0-local"
    service_name: str = "nettriage-api"
    # Set by Terraform on the function (spec §5.5, §6.8). The parameters hold the Cognito app
    # client's settings (JSON), its client secret, and app_api's pooled database URL.
    runtime_table: str = ""
    oidc_parameter: str = ""
    oidc_secret_parameter: str = ""
    database_url_parameter: str = ""

    @property
    def running_in_lambda(self) -> bool:
        return "AWS_LAMBDA_FUNCTION_NAME" in os.environ

    @property
    def api_docs_enabled(self) -> bool:
        return self.stage in ("local", "dev")
```

In `backend/src/nettriage/platform/logging.py`, replace:
```python
# Attributes every LogRecord has; anything else arrived through `extra=`.
```
with:
```python
# HTTP and AWS clients log request URLs and parameters at DEBUG and INFO: botocore logs every
# DynamoDB item it writes, sessions and sign-in state included. Only their warnings reach our
# logs, whatever our own level.
QUIET_LOGGERS = ("botocore", "boto3", "s3transfer", "urllib3", "httpx", "httpcore")
# Attributes every LogRecord has; anything else arrived through `extra=`.
```

In `backend/src/nettriage/platform/logging.py`, replace:
```python
    root.handlers = [handler]
    root.setLevel(level)
```
with:
```python
    root.handlers = [handler]
    root.setLevel(level)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))
```

- [ ] **Step 5: Write the services, cookies, audit helper and access rules**

`backend/src/nettriage/entrypoints/api/services.py`:
```python
"""What the API's routes use, built once per process: in production from SSM at cold start
(`wiring.py`), in tests from moto, a fake Cognito and the test database."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from fastapi import Request
from sqlalchemy import Engine

from nettriage.adapters.login_states import LoginStateStore
from nettriage.adapters.oidc import OidcClient
from nettriage.adapters.rate_limiter import RateLimiter
from nettriage.adapters.sessions import SessionStore
from nettriage.application.clock import Clock
from nettriage.platform.metrics import AppMetrics


@dataclass(frozen=True)
class Services:
    database: Engine
    sessions: SessionStore
    login_states: LoginStateStore
    rate_limiter: RateLimiter
    oidc: OidcClient
    clock: Clock
    metrics: AppMetrics

    @property
    def app_origin(self) -> str:
        return self.oidc.settings.app_origin


def get_services(request: Request) -> Services:
    return cast(Services, request.app.state.services)
```

`backend/src/nettriage/entrypoints/api/cookies.py`:
```python
"""The cookies (spec §6.2): Secure, HttpOnly, SameSite=Lax, Path=/, no Domain.

- `__Host-session` holds the session ID, and lives at most as long as the session can.
- `__Host-sign-in` binds a sign-in to the browser that started it: it holds the sign-in's
  `state` for 5 minutes, and the callback accepts only a state equal to it. Without it, an
  attacker could send a victim the callback link of the attacker's own sign-in (login CSRF).
"""

from fastapi import Response

from nettriage.application.sessions import ABSOLUTE_TIMEOUT, COOKIE_NAME

SIGN_IN_COOKIE = "__Host-sign-in"
SIGN_IN_MAX_AGE = 300
EXPIRED_SESSION_COOKIE = f"{COOKIE_NAME}=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Lax"
EXPIRED_SIGN_IN_COOKIE = f"{SIGN_IN_COOKIE}=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Lax"


def set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        session_id,
        max_age=int(ABSOLUTE_TIMEOUT.total_seconds()),
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.headers.append("Set-Cookie", EXPIRED_SESSION_COOKIE)


def set_sign_in_cookie(response: Response, state: str) -> None:
    response.set_cookie(
        SIGN_IN_COOKIE,
        state,
        max_age=SIGN_IN_MAX_AGE,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


def clear_sign_in_cookie(response: Response) -> None:
    response.headers.append("Set-Cookie", EXPIRED_SIGN_IN_COOKIE)
```

`backend/src/nettriage/entrypoints/api/auditing.py`:
```python
"""Writing audit events from a request: who, from where, and which trace."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from uuid import UUID

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError

from nettriage.adapters.audit_log import record
from nettriage.application.audit import AuditEvent, Outcome
from nettriage.application.rate_limits import viewer_ip
from nettriage.entrypoints.api.services import get_services
from nettriage.platform.trace_context import current_trace_id

logger = logging.getLogger(__name__)

VIEWER_ADDRESS = "cloudfront-viewer-address"
# CloudFront's ID for the request; it also appears in CloudFront's own logs.
REQUEST_ID = "x-amz-cf-id"


def audit(
    request: Request,
    *,
    action: str,
    outcome: Outcome,
    actor_user_id: UUID | None = None,
    details: Mapping[str, object] | None = None,
) -> None:
    """Append an audit event. A failed write is logged, not raised: the request it describes
    has already happened."""
    event = AuditEvent(
        action=action,
        outcome=outcome,
        actor_type="user" if actor_user_id else "anonymous",
        actor_user_id=actor_user_id,
        ip=viewer_ip(request.headers.get(VIEWER_ADDRESS)),
        user_agent=request.headers.get("user-agent"),
        request_id=request.headers.get(REQUEST_ID),
        trace_id=current_trace_id(),
        details=details or {},
    )
    try:
        record(get_services(request).database, event)
    except SQLAlchemyError:
        logger.exception("audit_write_failed", extra={"action": action})
```

`backend/src/nettriage/entrypoints/api/access.py`:
```python
"""Who may call a route (spec §6.2, §6.4, §6.5). Deny by default: every route declares
`Public(policy)` or `SignedIn`, and tests/security/test_route_access.py fails on any route that
declares neither.

- `Public(policy)` rate-limits by client IP.
- `SignedIn` needs a valid session. It fails closed: an unreadable session store gives 503.
  State-changing methods also pass the CSRF checks. Requests are rate-limited per user, and
  state-changing ones also by `api.mutation.user`.
"""

from __future__ import annotations

import hmac
import logging
from typing import Annotated
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, HTTPException, Request

from nettriage.application.rate_limits import POLICIES, Policy, ip_subject
from nettriage.application.sessions import COOKIE_NAME, Session
from nettriage.entrypoints.api.auditing import VIEWER_ADDRESS, audit
from nettriage.entrypoints.api.cookies import EXPIRED_SESSION_COOKIE
from nettriage.entrypoints.api.services import Services, get_services

logger = logging.getLogger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
SAME_SITE = frozenset({"same-origin", "none"})


class Access:
    """A route's access declaration."""


class Public(Access):
    def __init__(self, policy: str) -> None:
        self.policy = POLICIES[policy]

    def __call__(self, request: Request) -> None:
        subject = ip_subject(request.headers.get(VIEWER_ADDRESS))
        if subject is not None:
            enforce(request, self.policy, subject, actor=None)


class SignedIn(Access):
    def __call__(self, request: Request) -> Session:
        services = get_services(request)
        session = _valid_session(request, services)
        if request.method not in SAFE_METHODS:
            _check_csrf(request, services, session)
        enforce(request, POLICIES["api.user"], str(session.user_id), actor=session.user_id)
        if request.method not in SAFE_METHODS:
            enforce(
                request, POLICIES["api.mutation.user"], str(session.user_id), actor=session.user_id
            )
        return _touched(services, session)


signed_in = SignedIn()
CurrentSession = Annotated[Session, Depends(signed_in)]


def unauthorized() -> HTTPException:
    """401 that also clears the browser's stale session cookie."""
    return HTTPException(401, headers={"Set-Cookie": EXPIRED_SESSION_COOKIE})


def unavailable(what: str) -> HTTPException:
    return HTTPException(503, detail=f"{what} is unavailable right now; try again shortly.")


def enforce(request: Request, policy: Policy, subject: str, *, actor: UUID | None) -> None:
    """Check one rate limit, and remember the decision for the RateLimit headers."""
    services = get_services(request)
    decision = services.rate_limiter.check(policy, subject)
    request.state.rate_limits = [*getattr(request.state, "rate_limits", []), decision]
    if decision.degraded:
        services.metrics.rate_limit_errors.add(1, {"policy": policy.name})
        return
    if decision.allowed:
        return
    services.metrics.rate_limited.add(1, {"policy": policy.name})
    if services.rate_limiter.should_audit(policy, subject):
        audit(
            request,
            action="ratelimit.limited",
            outcome="denied",
            actor_user_id=actor,
            details={"policy": policy.name},
        )
    raise HTTPException(
        429,
        detail="Too many requests; try again later.",
        headers={"Retry-After": str(decision.retry_after_seconds)},
    )


def _valid_session(request: Request, services: Services) -> Session:
    session_id = request.cookies.get(COOKIE_NAME)
    if not session_id:
        raise unauthorized()
    try:
        session = services.sessions.get(session_id)
    except BotoCoreError, ClientError:
        logger.exception("session_read_failed")
        raise unavailable("Signing in") from None
    if session is None:
        raise unauthorized()
    if session.is_expired(services.clock()):
        try:
            services.sessions.delete(session)
        except BotoCoreError, ClientError:
            logger.warning("expired_session_delete_failed")
        raise unauthorized()
    return session


def _check_csrf(request: Request, services: Services, session: Session) -> None:
    site = request.headers.get("sec-fetch-site")
    origin = request.headers.get("origin")
    token = request.headers.get("x-csrf-token", "")
    if (
        site in SAME_SITE
        and (origin is None or origin == services.app_origin)
        and hmac.compare_digest(token.encode(), session.csrf_token.encode())
    ):
        return
    services.metrics.csrf_failed.add(1)
    logger.warning("csrf_failed", extra={"route": request.url.path})
    raise HTTPException(403, detail="The request failed its CSRF check. Reload the page and retry.")


def _touched(services: Services, session: Session) -> Session:
    now = services.clock()
    if not session.needs_touch(now):
        return session
    try:
        touched = services.sessions.touch(session, now)
    except BotoCoreError, ClientError:
        logger.warning("session_touch_failed")
        return session
    if touched is None:
        raise unauthorized()
    return touched
```

- [ ] **Step 6: Write the routes, the app and the production wiring**

`backend/src/nettriage/entrypoints/api/routes/auth.py`:
```python
"""Sign-in and sign-out (spec §4.1, §6.2), the backend-for-frontend way: the browser only ever
holds an opaque session cookie.

`login` and `callback` are browser navigations, so their failures redirect to the landing page
with `?sign_in=<reason>` instead of returning JSON.
"""

from __future__ import annotations

import hmac
import logging

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from nettriage.adapters.oidc import OidcError
from nettriage.adapters.users import sign_in_user
from nettriage.application.rate_limits import viewer_ip
from nettriage.application.sessions import COOKIE_NAME, new_secret
from nettriage.application.sign_in import (
    LoginState,
    code_challenge,
    new_code_verifier,
    safe_return_to,
)
from nettriage.entrypoints.api.access import CurrentSession, Public, unavailable
from nettriage.entrypoints.api.auditing import VIEWER_ADDRESS, audit
from nettriage.entrypoints.api.cookies import (
    SIGN_IN_COOKIE,
    clear_session_cookie,
    clear_sign_in_cookie,
    set_session_cookie,
    set_sign_in_cookie,
)
from nettriage.entrypoints.api.services import Services, get_services

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth")


class LogoutResult(BaseModel):
    logout_url: str


def redirect(location: str) -> RedirectResponse:
    return RedirectResponse(location, status_code=302, headers={"Cache-Control": "no-store"})


def sign_in_failed(reason: str) -> RedirectResponse:
    return redirect(f"/?sign_in={reason}")


@router.get("/login", dependencies=[Depends(Public("auth.ip"))])
def login(request: Request, return_to: str | None = None) -> RedirectResponse:
    services = get_services(request)
    state, nonce, verifier = new_secret(), new_secret(), new_code_verifier()
    try:
        services.login_states.put(
            state, LoginState(verifier, nonce, safe_return_to(return_to)), services.clock()
        )
    except BotoCoreError, ClientError:
        logger.exception("login_state_write_failed")
        return sign_in_failed("unavailable")
    response = redirect(
        services.oidc.authorization_url(
            state=state, nonce=nonce, code_challenge=code_challenge(verifier)
        )
    )
    set_sign_in_cookie(response, state)
    return response


@router.get("/callback", dependencies=[Depends(Public("auth.ip"))])
def callback(
    request: Request, code: str | None = None, state: str | None = None
) -> RedirectResponse:
    response = _finish_sign_in(request, code, state)
    clear_sign_in_cookie(response)
    return response


def _finish_sign_in(request: Request, code: str | None, state: str | None) -> RedirectResponse:
    services = get_services(request)
    started_here = request.cookies.get(SIGN_IN_COOKIE, "")
    if not state or not hmac.compare_digest(started_here.encode(), state.encode()):
        return sign_in_failed("expired")  # not started in this browser, or over 5 minutes ago
    try:
        login = services.login_states.take(state, services.clock())
    except BotoCoreError, ClientError:
        logger.exception("login_state_read_failed")
        return sign_in_failed("unavailable")
    if login is None:
        return sign_in_failed("expired")
    if not code:
        return sign_in_failed("failed")  # Cognito sent an error instead, e.g. a cancelled sign-in
    try:
        identity = services.oidc.identity(
            code=code, code_verifier=login.code_verifier, nonce=login.nonce
        )
    except OidcError as error:
        logger.warning("sign_in_failed", extra={"reason": str(error)})
        return sign_in_failed("failed")
    try:
        user = sign_in_user(services.database, sub=identity.sub, email=identity.email)
    except SQLAlchemyError:
        logger.exception("sign_in_user_failed")
        return sign_in_failed("unavailable")
    if user.disabled:
        audit(request, action="auth.session_created", outcome="denied", actor_user_id=user.user_id)
        return sign_in_failed("disabled")
    try:
        _end_session(services, request.cookies.get(COOKIE_NAME))  # never reuse a session
        session_id, _ = services.sessions.create(
            user_id=user.user_id,
            now=services.clock(),
            ip=viewer_ip(request.headers.get(VIEWER_ADDRESS)),
            user_agent=request.headers.get("user-agent"),
        )
    except BotoCoreError, ClientError:
        logger.exception("session_create_failed")
        return sign_in_failed("unavailable")
    if user.created:
        services.metrics.signups.add(1)
    audit(
        request,
        action="auth.session_created",
        outcome="success",
        actor_user_id=user.user_id,
        details={"new_user": user.created},
    )
    response = redirect(login.return_to)
    set_session_cookie(response, session_id)
    return response


@router.post("/logout")
def logout(request: Request, response: Response, session: CurrentSession) -> LogoutResult:
    services = get_services(request)
    try:
        services.sessions.delete(session)
    except BotoCoreError, ClientError:
        logger.exception("session_delete_failed")
        raise unavailable("Signing out") from None
    audit(request, action="auth.logout", outcome="success", actor_user_id=session.user_id)
    clear_session_cookie(response)
    return LogoutResult(logout_url=services.oidc.logout_url())


@router.post("/logout-all")
def logout_all(request: Request, response: Response, session: CurrentSession) -> LogoutResult:
    services = get_services(request)
    try:
        count = services.sessions.delete_all(session.user_id)
    except BotoCoreError, ClientError:
        logger.exception("session_delete_failed")
        raise unavailable("Signing out") from None
    audit(
        request,
        action="auth.logout_all",
        outcome="success",
        actor_user_id=session.user_id,
        details={"sessions": count},
    )
    clear_session_cookie(response)
    return LogoutResult(logout_url=services.oidc.logout_url())


def _end_session(services: Services, session_id: str | None) -> None:
    """Delete the session a browser brought to the callback, so a cookie planted before sign-in
    (session fixation) can never become a signed-in session."""
    if not session_id:
        return
    old = services.sessions.get(session_id)
    if old is not None:
        services.sessions.delete(old)
```

`backend/src/nettriage/entrypoints/api/routes/me.py`:
```python
"""The signed-in user (spec §7): who they are, their memberships, and the CSRF token the SPA
sends back on state-changing requests."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from nettriage.adapters.users import load_me
from nettriage.entrypoints.api.access import CurrentSession, unauthorized, unavailable
from nettriage.entrypoints.api.services import get_services

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")


class UserOut(BaseModel):
    id: UUID
    email: str
    display_name: str | None


class MembershipOut(BaseModel):
    org_id: UUID
    name: str
    slug: str
    role: str


class MeOut(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]
    csrf_token: str


@router.get("/me")
def me(request: Request, response: Response, session: CurrentSession) -> MeOut:
    try:
        found = load_me(get_services(request).database, session.user_id)
    except SQLAlchemyError:
        logger.exception("load_me_failed")
        raise unavailable("Your account") from None
    if found is None:
        raise unauthorized()
    response.headers["Cache-Control"] = "no-store"
    return MeOut(
        user=UserOut(id=found.user_id, email=found.email, display_name=found.display_name),
        memberships=[
            MembershipOut(org_id=m.org_id, name=m.name, slug=m.slug, role=m.role)
            for m in found.memberships
        ],
        csrf_token=session.csrf_token,
    )
```

`backend/src/nettriage/entrypoints/api/routes/health.py`:
```python
"""Liveness check. It never touches the database, so probes don't wake Neon. It is public, and
rate-limited per client IP like every public route (spec §6.5)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from nettriage.entrypoints.api.access import Public
from nettriage.entrypoints.api.dependencies import get_settings
from nettriage.platform.config import Settings

router = APIRouter()


class Health(BaseModel):
    status: str
    version: str


@router.get("/health", dependencies=[Depends(Public("public.ip"))])
def health(response: Response, settings: Annotated[Settings, Depends(get_settings)]) -> Health:
    response.headers["Cache-Control"] = "no-store"
    return Health(status="ok", version=settings.version)
```

`backend/src/nettriage/entrypoints/api/app.py`:
```python
"""FastAPI application factory. Creating an app has no global side effects."""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from nettriage.application.rate_limits import header_values
from nettriage.entrypoints.api.routes import auth, health, me
from nettriage.entrypoints.api.services import Services
from nettriage.platform.config import Settings
from nettriage.platform.errors import register_error_handlers
from nettriage.platform.telemetry import instrument_app


def create_app(
    settings: Settings,
    services: Services,
    *,
    tracer_provider: TracerProvider | None = None,
    meter_provider: MeterProvider | None = None,
) -> FastAPI:
    docs = settings.api_docs_enabled
    app = FastAPI(
        title="NetTriage API",
        version=settings.version,
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        swagger_ui_oauth2_redirect_url=None,
        openapi_url="/api/openapi.json" if docs else None,
    )
    app.state.settings = settings
    app.state.services = services
    register_error_handlers(app)
    app.middleware("http")(add_rate_limit_headers)
    for module in (health, auth, me):
        app.include_router(module.router, prefix="/api")
    if tracer_provider is not None:
        instrument_app(app, tracer_provider, meter_provider)
    return app


async def add_rate_limit_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """RateLimit-Policy and RateLimit for every limit the request was checked against, on
    every response, redirects and 429s included."""
    response = await call_next(request)
    response.headers.update(header_values(getattr(request.state, "rate_limits", [])))
    return response
```

`backend/src/nettriage/entrypoints/api/wiring.py`:
```python
"""Building the API's services in Lambda, once per cold start (spec §6.8): settings and secrets
come from SSM Parameter Store, never from environment variables or the package."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING

import boto3
import httpx
from botocore.config import Config

from nettriage.adapters.login_states import LoginStateStore
from nettriage.adapters.oidc import OidcClient, OidcSettings
from nettriage.adapters.postgres import create_database_engine
from nettriage.adapters.rate_limiter import RateLimiter
from nettriage.adapters.sessions import SessionStore
from nettriage.application.clock import system_clock
from nettriage.entrypoints.api.services import Services
from nettriage.platform.config import Settings
from nettriage.platform.metrics import AppMetrics

if TYPE_CHECKING:
    from types_boto3_ssm.client import SSMClient

# Fail fast inside the API's 29-second timeout, with a few quick retries (spec §3.5).
AWS_CONFIG = Config(
    connect_timeout=2, read_timeout=5, retries={"mode": "standard", "max_attempts": 3}
)
COGNITO_TIMEOUT = httpx.Timeout(5.0)


class MissingParameterError(RuntimeError):
    """An SSM parameter the API needs doesn't exist. The message names it; it has no value."""


def read_parameters(ssm: SSMClient, names: Sequence[str]) -> dict[str, str]:
    response = ssm.get_parameters(Names=list(names), WithDecryption=True)
    missing = response.get("InvalidParameters", [])
    if missing:
        raise MissingParameterError(f"Missing SSM parameters: {', '.join(sorted(missing))}")
    return {parameter["Name"]: parameter["Value"] for parameter in response["Parameters"]}


def build_services(settings: Settings, session: boto3.session.Session | None = None) -> Services:
    session = session or boto3.session.Session()
    values = read_parameters(
        session.client("ssm", config=AWS_CONFIG),
        [settings.oidc_parameter, settings.oidc_secret_parameter, settings.database_url_parameter],
    )
    oidc = json.loads(values[settings.oidc_parameter])
    dynamodb = session.client("dynamodb", config=AWS_CONFIG)
    table = settings.runtime_table
    return Services(
        database=create_database_engine(values[settings.database_url_parameter], pool_size=1),
        sessions=SessionStore(dynamodb, table),
        login_states=LoginStateStore(dynamodb, table),
        rate_limiter=RateLimiter(dynamodb, table, system_clock),
        oidc=OidcClient(
            OidcSettings(
                issuer=oidc["issuer"],
                client_id=oidc["client_id"],
                client_secret=values[settings.oidc_secret_parameter],
                domain=oidc["domain"],
                app_origin=oidc["app_origin"],
            ),
            httpx.Client(timeout=COGNITO_TIMEOUT),
            system_clock,
        ),
        clock=system_clock,
        metrics=AppMetrics(),
    )
```

`backend/src/nettriage/entrypoints/api/main.py`:
```python
"""Production ASGI entrypoint, started by run.sh: nettriage.entrypoints.api.main:app"""

from nettriage.entrypoints.api.app import create_app
from nettriage.entrypoints.api.wiring import build_services
from nettriage.platform.config import Settings
from nettriage.platform.logging import configure_logging
from nettriage.platform.telemetry import (
    create_meter_provider,
    create_tracer_provider,
    install_global_providers,
)

settings = Settings()
configure_logging(settings)
tracer_provider = create_tracer_provider(settings)
meter_provider = create_meter_provider(settings)
install_global_providers(tracer_provider, meter_provider)
services = build_services(settings)
app = create_app(settings, services, tracer_provider=tracer_provider, meter_provider=meter_provider)
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
After the ID token is verified, Cognito's tokens are discarded. The app never calls anything on the user's behalf. `return_to` must be a relative path within the app (open-redirect protection).
```
with:
```markdown
After the ID token is verified, Cognito's tokens are discarded. The app never calls anything on the user's behalf. `return_to` must be a relative path within the app (open-redirect protection).

The login response also sets a 5-minute `__Host-sign-in` cookie holding `state`, and the callback accepts only a `state` equal to it (amended in Plan 3b). This binds each sign-in to the browser that started it: otherwise an attacker could send a victim the callback link of the attacker's own sign-in, and the victim would be signed in to the attacker's account (login CSRF).
```

- [ ] **Step 7: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `330 passed, 1 skipped`.

The new API and security tests cover the following:
- The login redirect carries PKCE, and the state is stored. A return address outside the app becomes `/app`.
- Signing in:
  - creates the user and sets a `__Host-session` cookie with `Max-Age=43200; Path=/; Secure; HttpOnly; SameSite=lax` and no `Domain`;
  - writes `auth.session_created` and counts `nettriage.signups`.
- A second sign-in finds the same user.
- These are refused:
  - a replayed or unknown state;
  - a cancelled sign-in;
  - a nonce from another sign-in;
  - a disabled account;
  - a callback finished in another browser.
- Every sign-in ends the session the browser had.
- `/me`:
  - shows the user, memberships and a 43-character CSRF token with `no-store`;
  - without a session it's 401 Problem Details, and an unknown cookie is cleared.
- Logout ends the session and returns Cognito's logout URL, and logout-all ends every session.
- Seven forged-request variants are refused with 403, each counting `nettriage.csrf.failed`.
- Sessions:
  - end after 60 idle minutes, and after 12 hours of activity;
  - are touched at most every 5 minutes;
  - fail closed (503) without DynamoDB.
- Rate limiting:
  - `/api/health` reports `public.ip`;
  - 7 logins from one IP give 5 redirects then two 429s with `Retry-After: 6`, with one sampled audit row;
  - signed-in calls report `api.user`;
  - a broken limiter lets requests through and counts it.
- Every route declares exactly one access rule.
- A sign-in and sign-out at DEBUG log no email, session ID, CSRF token, state, nonce or code.
- The wiring reads its three SSM parameters, and names a missing one without its value.

- [ ] **Step 8: Commit**

```bash
git add backend docs/superpowers/specs/2026-09-26-nettriage-m1-design.md
git commit -m "feat(api): sign-in with Cognito, sessions, CSRF checks, rate limits and /me" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Cognito and the runtime table in Terraform, deploy checks, and the runbook

**Files:**
- Create:
  - `infra/modules/data/` (`versions.tf`, `variables.tf`, `main.tf`, `outputs.tf`, `tests/data.tftest.hcl`);
  - `infra/modules/identity/` (`versions.tf`, `variables.tf`, `main.tf`, `outputs.tf`, `tests/identity.tftest.hcl`).
- Modify:
  - `infra/modules/app/main.tf`, `variables.tf` and `tests/app.tftest.hcl`;
  - `infra/envs/dev/main.tf` and `outputs.tf`;
  - `.checkov.yaml`, `justfile` and `.github/workflows/ci.yml`.
- Modify: `tools/deploy/preflight.py`, `tools/smoke.py` and `tools/build_lambda.py`, with their tests `tools/tests/test_deploy_preflight.py`, `test_smoke.py` and `test_build_lambda.py`.
- Modify: `docs/runbooks/setup-and-deploy.md` and `README.md`.

**Interfaces:**
- Consumes:
  - from Task 5, the environment variables `NETTRIAGE_RUNTIME_TABLE`, `NETTRIAGE_OIDC_PARAMETER`, `NETTRIAGE_OIDC_SECRET_PARAMETER` and `NETTRIAGE_DATABASE_URL_PARAMETER`;
  - the OIDC parameter's JSON keys `issuer`, `client_id`, `domain` and `app_origin`;
  - from Plan 3a, the deploy-written `/nettriage/<stage>/db/app-api-url`.
- Produces:
  - The Terraform modules:
    - `module.data`, which outputs `table_name` and `table_arn`;
    - `module.identity`, which takes `stage`, `app_domain`, `oidc_parameter` and `oidc_secret_parameter`, and outputs `user_pool_id` and `sign_in_domain`;
    - `module.app`'s new inputs `runtime_table_name`, `runtime_table_arn`, `oidc_parameter`, `oidc_secret_parameter` and `database_url_parameter`.
  - The dev stack's `sign_in_domain` output.
  - Preflight checks `DynamoDB in eu-north-1` and `Cognito in eu-north-1`.
  - Smoke checks `api limits requests per viewer ip`, `sign-in redirects to Cognito` and `Cognito sign-in page loads`.
  - In `tools/build_lambda.py`: `validate_zip(out, *, max_zipped=MAX_ZIPPED_BYTES, max_unzipped=MAX_UNZIPPED_BYTES)`.

- [ ] **Step 1: Write the failing tests**

`infra/modules/data/tests/data.tftest.hcl`:
```hcl
mock_provider "aws" {}

variables {
  stage = "dev"
}

run "runtime_table_matches_the_spec" {
  command = apply

  assert {
    condition     = aws_dynamodb_table.runtime.name == "nettriage-dev-runtime"
    error_message = "Names follow nettriage-<stage>-<name>."
  }
  assert {
    condition     = aws_dynamodb_table.runtime.hash_key == "pk" && one(aws_dynamodb_table.runtime.attribute).type == "S"
    error_message = "The partition key is the string pk (spec §5.5)."
  }
  assert {
    condition     = one(aws_dynamodb_table.runtime.ttl).attribute_name == "expires_at" && one(aws_dynamodb_table.runtime.ttl).enabled
    error_message = "Items expire through TTL on expires_at (spec §5.5)."
  }
}

run "dev_capacity_stays_inside_always_free" {
  command = plan

  assert {
    condition     = aws_dynamodb_table.runtime.billing_mode == "PROVISIONED" && aws_dynamodb_table.runtime.read_capacity == 3 && aws_dynamodb_table.runtime.write_capacity == 3
    error_message = "dev gets 3 RCU and 3 WCU, provisioned (spec §5.5)."
  }
  assert {
    condition     = !aws_dynamodb_table.runtime.deletion_protection_enabled
    error_message = "dev can be torn down."
  }
}

run "prod_gets_more_capacity_and_deletion_protection" {
  command = plan

  variables {
    stage = "prod"
  }

  assert {
    condition     = aws_dynamodb_table.runtime.read_capacity == 10 && aws_dynamodb_table.runtime.write_capacity == 10
    error_message = "prod gets 10 RCU and 10 WCU (spec §5.5)."
  }
  assert {
    condition     = aws_dynamodb_table.runtime.deletion_protection_enabled
    error_message = "prod's table is protected from deletion."
  }
}
```

`infra/modules/identity/tests/identity.tftest.hcl`:
```hcl
mock_provider "aws" {
  mock_data "aws_region" {
    defaults = {
      region = "eu-north-1"
    }
  }
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
}

variables {
  stage                 = "dev"
  app_domain            = "d111111abcdef8.cloudfront.net"
  oidc_parameter        = "/nettriage/dev/api/oidc"
  oidc_secret_parameter = "/nettriage/dev/api/oidc-client-secret"
}

run "mfa_is_mandatory_and_totp_only" {
  command = apply

  assert {
    condition     = aws_cognito_user_pool.main.mfa_configuration == "ON" && one(aws_cognito_user_pool.main.software_token_mfa_configuration).enabled
    error_message = "MFA is required, TOTP only (spec §6.1)."
  }
  assert {
    condition     = length(aws_cognito_user_pool.main.sms_configuration) == 0
    error_message = "There is no SMS option (spec §6.1)."
  }
  assert {
    condition     = aws_cognito_user_pool.main.user_pool_tier == "ESSENTIALS"
    error_message = "The pool is on the Essentials plan (spec §6.1)."
  }
  assert {
    condition     = aws_cognito_user_pool.main.username_attributes == toset(["email"]) && aws_cognito_user_pool.main.auto_verified_attributes == toset(["email"])
    error_message = "The username is the email address, and it is verified (spec §6.1)."
  }
  assert {
    condition     = one(aws_cognito_user_pool.main.password_policy).minimum_length == 12
    error_message = "Passwords are at least 12 characters (spec §6.1)."
  }
}

run "the_app_client_is_confidential_and_code_flow_only" {
  command = apply

  assert {
    condition     = aws_cognito_user_pool_client.web.generate_secret && aws_cognito_user_pool_client.web.allowed_oauth_flows == toset(["code"])
    error_message = "The client is confidential and uses the authorization-code grant only (spec §6.1)."
  }
  assert {
    condition     = aws_cognito_user_pool_client.web.allowed_oauth_scopes == toset(["openid", "email", "profile"])
    error_message = "The client asks for openid, email and profile (spec §6.1)."
  }
  assert {
    condition     = aws_cognito_user_pool_client.web.callback_urls == toset(["https://d111111abcdef8.cloudfront.net/api/auth/callback"]) && aws_cognito_user_pool_client.web.logout_urls == toset(["https://d111111abcdef8.cloudfront.net/"])
    error_message = "Only the stage's exact callback and logout URLs are allowed (spec §6.1)."
  }
  assert {
    condition     = aws_cognito_user_pool_client.web.prevent_user_existence_errors == "ENABLED"
    error_message = "PreventUserExistenceErrors is on (spec §6.1)."
  }
}

run "the_api_reads_the_clients_settings_and_secret_from_ssm" {
  command = apply

  assert {
    condition     = aws_ssm_parameter.oidc.type == "String" && aws_ssm_parameter.oidc_client_secret.type == "SecureString"
    error_message = "The settings are plain; the client secret is a SecureString (spec §3.2)."
  }
  assert {
    condition     = keys(jsondecode(aws_ssm_parameter.oidc.value)) == ["app_origin", "client_id", "domain", "issuer"]
    error_message = "The settings JSON has exactly the keys the API reads (nettriage.entrypoints.api.wiring)."
  }
  assert {
    condition     = jsondecode(aws_ssm_parameter.oidc.value).app_origin == "https://d111111abcdef8.cloudfront.net"
    error_message = "The app origin is the CloudFront domain."
  }
  assert {
    condition     = startswith(jsondecode(aws_ssm_parameter.oidc.value).issuer, "https://cognito-idp.eu-north-1.amazonaws.com/")
    error_message = "The issuer is the pool's Cognito URL in eu-north-1."
  }
}

run "the_sign_in_domain_does_not_expose_the_account_id" {
  command = apply

  assert {
    condition     = startswith(aws_cognito_user_pool_domain.main.domain, "nettriage-dev-") && !strcontains(aws_cognito_user_pool_domain.main.domain, "123456789012")
    error_message = "The prefix domain is nettriage-<stage>-<hash>, without the account ID."
  }
  assert {
    condition     = aws_cognito_user_pool_domain.main.managed_login_version == 2
    error_message = "Sign-in uses Cognito's managed login (spec §2.2)."
  }
}
```

In `infra/modules/app/tests/app.tftest.hcl`, replace:
```hcl
mock_provider "aws" {
  # The default mock for a computed "arn" attribute is a short random string, not
  # ARN-shaped. aws_lambda_function.role validates its value looks like an ARN,
  # so give aws_iam_role.api's computed arn a realistic value.
```
with:
```hcl
mock_provider "aws" {
  mock_data "aws_region" {
    defaults = {
      region = "eu-north-1"
    }
  }
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
  # The default mock for a computed "arn" attribute is a short random string, not
  # ARN-shaped. aws_lambda_function.role validates its value looks like an ARN,
  # so give aws_iam_role.api's computed arn a realistic value.
```

In `infra/modules/app/tests/app.tftest.hcl`, replace:
```hcl
  otel_collector_layer_arn = "arn:aws:lambda:eu-north-1:184161586896:layer:opentelemetry-collector-arm64-0_22_0:1"
  grafana_otlp_endpoint    = "https://otlp-gateway.example.grafana.net/otlp"
  grafana_otlp_auth        = "dGVzdDp0ZXN0"
}

run "function_is_arm64_python_behind_iam_auth" {
```
with:
```hcl
  otel_collector_layer_arn = "arn:aws:lambda:eu-north-1:184161586896:layer:opentelemetry-collector-arm64-0_22_0:1"
  grafana_otlp_endpoint    = "https://otlp-gateway.example.grafana.net/otlp"
  grafana_otlp_auth        = "dGVzdDp0ZXN0"
  runtime_table_name       = "nettriage-dev-runtime"
  runtime_table_arn        = "arn:aws:dynamodb:eu-north-1:123456789012:table/nettriage-dev-runtime"
  oidc_parameter           = "/nettriage/dev/api/oidc"
  oidc_secret_parameter    = "/nettriage/dev/api/oidc-client-secret"
  database_url_parameter   = "/nettriage/dev/db/app-api-url"
}

run "function_is_arm64_python_behind_iam_auth" {
```

In `infra/modules/app/tests/app.tftest.hcl`, replace:
```hcl
  expect_failures = [var.grafana_otlp_endpoint]
}

```
with:
```hcl
  expect_failures = [var.grafana_otlp_endpoint]
}

run "the_api_reads_only_its_own_table_and_parameters" {
  command = apply

  assert {
    condition     = jsondecode(aws_iam_role_policy.api_runtime_table.policy).Statement[0].Resource == "arn:aws:dynamodb:eu-north-1:123456789012:table/nettriage-dev-runtime"
    error_message = "DynamoDB access is limited to the runtime table (spec §6.8)."
  }
  assert {
    condition = toset(jsondecode(aws_iam_role_policy.api_parameters.policy).Statement[0].Resource) == toset([
      "arn:aws:ssm:eu-north-1:123456789012:parameter/nettriage/dev/api/oidc",
      "arn:aws:ssm:eu-north-1:123456789012:parameter/nettriage/dev/api/oidc-client-secret",
      "arn:aws:ssm:eu-north-1:123456789012:parameter/nettriage/dev/db/app-api-url",
    ])
    error_message = "The API reads only its own three parameters (spec §6.8)."
  }
  assert {
    condition     = aws_lambda_function.api.environment[0].variables["NETTRIAGE_DATABASE_URL_PARAMETER"] == "/nettriage/dev/db/app-api-url"
    error_message = "The function gets parameter names, never secret values."
  }
}

```

In `tools/tests/test_deploy_preflight.py`, replace:
```python
        "Lambda in eu-north-1", "IAM", "CloudFront", "Budgets", "SSM in eu-north-1",
    ]
```
with:
```python
        "Lambda in eu-north-1", "IAM", "CloudFront", "Budgets", "SSM in eu-north-1",
        "DynamoDB in eu-north-1", "Cognito in eu-north-1",
    ]
```

`tools/tests/test_smoke.py`:
```python
from collections.abc import Callable

import httpx

from tools.smoke import run_checks

SECURITY_HEADERS = {
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "content-security-policy": "default-src 'self'; frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
}


COGNITO = "https://nettriage-dev-1a2b3c4d.auth.eu-north-1.amazoncognito.com"
AUTHORIZE = f"{COGNITO}/oauth2/authorize?client_id=abc&code_challenge_method=S256&state=s"


def healthy(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.url.host == "fn.example":
        return httpx.Response(403, json={"Message": "Forbidden"})
    if request.url.host.endswith(".amazoncognito.com"):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>")
    if path == "/api/health":
        headers = {**SECURITY_HEADERS, "ratelimit-policy": '"public.ip";q=60;w=60'}
        return httpx.Response(200, json={"status": "ok", "version": "abc"}, headers=headers)
    if path == "/api/auth/login":
        return httpx.Response(302, headers={"location": AUTHORIZE})
    problem = {**SECURITY_HEADERS, "content-type": "application/problem+json"}
    if path == "/api/v1/me":
        return httpx.Response(401, headers=problem)
    if path.startswith("/api/"):
        return httpx.Response(404, headers=problem, content=b"{}")
    html = {**SECURITY_HEADERS, "content-type": "text/html"}
    return httpx.Response(200, headers=html, content=b"<!doctype html>")


def checks_for(handler: Callable[[httpx.Request], httpx.Response]) -> dict[str, bool]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return {c.name: c.ok for c in run_checks(client, "https://cdn.example", "https://fn.example/", "abc")}


def test_a_healthy_deployment_passes_every_check() -> None:
    results = checks_for(healthy)

    assert results
    assert all(results.values()), results


def test_spa_fallback_swallowing_api_errors_is_caught() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/does-not-exist":
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>")
        return healthy(request)

    assert checks_for(broken)["api 404 stays problem+json"] is False


def test_edge_401_without_problem_details_is_caught() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/me":
            return httpx.Response(401, headers={"content-type": "text/html"}, content=b"<html>")
        return healthy(request)

    assert checks_for(broken)["edge rejects api call without session"] is False


def test_function_url_reachable_directly_is_caught() -> None:
    def open_url(request: httpx.Request) -> httpx.Response:
        if request.url.host == "fn.example":
            return httpx.Response(200, json={"status": "ok"})
        return healthy(request)

    assert checks_for(open_url)["function url rejects direct calls"] is False


def test_missing_security_headers_are_caught() -> None:
    def bare(request: httpx.Request) -> httpx.Response:
        response = healthy(request)
        if request.url.path == "/":
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>")
        return response

    assert checks_for(bare)["web security headers"] is False


def test_wrong_deployed_version_is_caught() -> None:
    def old(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"status": "ok", "version": "old"}, headers=SECURITY_HEADERS)
        return healthy(request)

    assert checks_for(old)["api health"] is False


def test_a_login_that_does_not_reach_cognito_is_caught() -> None:
    def local(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(302, headers={"location": "/?sign_in=unavailable"})
        return healthy(request)

    results = checks_for(local)

    assert results["sign-in redirects to Cognito"] is False
    assert results["Cognito sign-in page loads"] is False


def test_a_login_without_pkce_is_caught() -> None:
    def no_pkce(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(302, headers={"location": f"{COGNITO}/oauth2/authorize?client_id=abc"})
        return healthy(request)

    assert checks_for(no_pkce)["sign-in redirects to Cognito"] is False


def test_a_broken_cognito_page_is_caught() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.host.endswith(".amazoncognito.com"):
            return httpx.Response(400, content=b"Login pages unavailable")
        return healthy(request)

    assert checks_for(broken)["Cognito sign-in page loads"] is False


def test_an_api_that_cannot_see_the_viewer_ip_is_caught() -> None:
    def no_viewer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"status": "ok", "version": "abc"}, headers=SECURITY_HEADERS)
        return healthy(request)

    assert checks_for(no_viewer)["api limits requests per viewer ip"] is False
```

In `tools/tests/test_build_lambda.py`, replace:
```python
    assert (package / "fastapi-1.0.dist-info/WHEEL").exists()

```
with:
```python
    assert (package / "fastapi-1.0.dist-info/WHEEL").exists()


def test_a_zip_over_lambdas_upload_limit_is_rejected(tmp_path: Path) -> None:
    out = build(tmp_path)

    with pytest.raises(PackageError, match="Lambda takes at most 10"):
        validate_zip(out, max_zipped=10)


def test_a_package_over_lambdas_unzipped_limit_is_rejected(tmp_path: Path) -> None:
    out = build(tmp_path)

    with pytest.raises(PackageError, match="unzipped it is"):
        validate_zip(out, max_unzipped=10)

```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `just tools-test`
Expected: FAIL.
- The preflight test is missing `DynamoDB in eu-north-1` and `Cognito in eu-north-1`.
- The smoke tests hit `KeyError: 'sign-in redirects to Cognito'` (and the viewer-IP check).
- The build tests hit `TypeError: validate_zip() got an unexpected keyword argument 'max_zipped'`.

Run: `cd infra/modules/app && export TF_DATA_DIR=.terraform-check && terraform init -backend=false -input=false > /dev/null && terraform test`
Expected: FAIL. The new run `the_api_reads_only_its_own_table_and_parameters` fails with `Reference to undeclared resource` for the two new IAM policies, and `Invalid index` for the new environment variables.

- [ ] **Step 3: Write the data and identity modules**

`infra/modules/data/versions.tf`:
```hcl
terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
```

`infra/modules/data/variables.tf`:
```hcl
variable "stage" {
  type = string

  validation {
    condition     = contains(["dev", "prod"], var.stage)
    error_message = "stage must be dev or prod."
  }
}
```

`infra/modules/data/main.tf`:
```hcl
# The DynamoDB `runtime` table (spec §5.5): sessions, sign-in state and rate-limit keys, all
# expiring through TTL. Provisioned capacity stays inside the Always Free 25 read and 25 write
# units: prod gets 10 of each, dev 3.
locals {
  capacity = var.stage == "prod" ? 10 : 3
}

resource "aws_dynamodb_table" "runtime" {
  name                        = "nettriage-${var.stage}-runtime"
  billing_mode                = "PROVISIONED"
  read_capacity               = local.capacity
  write_capacity              = local.capacity
  hash_key                    = "pk"
  deletion_protection_enabled = var.stage == "prod"

  attribute {
    name = "pk"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}
```

`infra/modules/data/outputs.tf`:
```hcl
output "table_name" {
  value = aws_dynamodb_table.runtime.name
}

output "table_arn" {
  value = aws_dynamodb_table.runtime.arn
}
```

`infra/modules/identity/versions.tf`:
```hcl
terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
```

`infra/modules/identity/variables.tf`:
```hcl
variable "stage" {
  type = string

  validation {
    condition     = contains(["dev", "prod"], var.stage)
    error_message = "stage must be dev or prod."
  }
}

variable "app_domain" {
  type        = string
  description = "The app's CloudFront domain; sign-in returns to https://<app_domain>/api/auth/callback."
}

variable "oidc_parameter" {
  type        = string
  description = "SSM parameter for the app client's settings (JSON, not secret), which the API reads."
}

variable "oidc_secret_parameter" {
  type        = string
  description = "SSM SecureString parameter for the app client's secret, which the API reads."
}
```

`infra/modules/identity/main.tf`:
```hcl
# Cognito (spec §6.1): one user pool per stage on the Essentials plan, self-service sign-up with
# a verified email, mandatory TOTP MFA, and a confidential app client for the API's
# authorization-code flow with PKCE. The API reads the client's settings and secret from SSM.
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  name       = "nettriage-${var.stage}"
  app_origin = "https://${var.app_domain}"
  region     = data.aws_region.current.region
  # Prefix domains are unique per Region across all of AWS. A hash of the account ID keeps ours
  # unique without putting the account ID in a public URL.
  domain_prefix = "${local.name}-${substr(sha256(data.aws_caller_identity.current.account_id), 0, 8)}"
}

resource "aws_cognito_user_pool" "main" {
  name                     = local.name
  user_pool_tier           = "ESSENTIALS"
  deletion_protection      = var.stage == "prod" ? "ACTIVE" : "INACTIVE"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "ON"

  username_configuration {
    case_sensitive = false
  }

  software_token_mfa_configuration {
    enabled = true
  }

  # At least 12 characters and no composition rules (NIST SP 800-63B); TOTP is mandatory.
  password_policy {
    minimum_length                   = 12
    require_lowercase                = false
    require_uppercase                = false
    require_numbers                  = false
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  admin_create_user_config {
    allow_admin_create_user_only = false
  }

  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }

  verification_message_template {
    default_email_option = "CONFIRM_WITH_CODE"
  }
}

resource "aws_cognito_user_pool_domain" "main" {
  domain                = local.domain_prefix
  user_pool_id          = aws_cognito_user_pool.main.id
  managed_login_version = 2
}

resource "aws_cognito_user_pool_client" "web" {
  name                                 = "${local.name}-web"
  user_pool_id                         = aws_cognito_user_pool.main.id
  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = ["${local.app_origin}/api/auth/callback"]
  logout_urls                          = ["${local.app_origin}/"]
  explicit_auth_flows                  = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  # The API reads the ID token once and discards every token, so each lives as briefly as
  # Cognito allows. The sign-in session itself gets Cognito's maximum, 15 minutes, because a
  # first sign-up also verifies the email and sets up the authenticator app.
  auth_session_validity  = 15
  id_token_validity      = 5
  access_token_validity  = 5
  refresh_token_validity = 60

  token_validity_units {
    id_token      = "minutes"
    access_token  = "minutes"
    refresh_token = "minutes"
  }
}

resource "aws_cognito_managed_login_branding" "web" {
  user_pool_id                = aws_cognito_user_pool.main.id
  client_id                   = aws_cognito_user_pool_client.web.id
  use_cognito_provided_values = true
}

resource "aws_ssm_parameter" "oidc" {
  #checkov:skip=CKV2_AWS_34:Not secret: the issuer, client ID, sign-in domain and app origin all appear in the browser during sign-in. The client secret is the SecureString below.
  name = var.oidc_parameter
  type = "String"
  value = jsonencode({
    issuer     = "https://cognito-idp.${local.region}.amazonaws.com/${aws_cognito_user_pool.main.id}"
    client_id  = aws_cognito_user_pool_client.web.id
    domain     = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${local.region}.amazoncognito.com"
    app_origin = local.app_origin
  })
}

resource "aws_ssm_parameter" "oidc_client_secret" {
  name  = var.oidc_secret_parameter
  type  = "SecureString"
  value = aws_cognito_user_pool_client.web.client_secret
}
```

`infra/modules/identity/outputs.tf`:
```hcl
output "user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "sign_in_domain" {
  value = "${aws_cognito_user_pool_domain.main.domain}.auth.${local.region}.amazoncognito.com"
}
```

- [ ] **Step 4: Give the API its table, parameters and settings, and wire the dev stage**

In `infra/modules/app/variables.tf`, replace:
```hcl
  default = "python3.14"
}

```
with:
```hcl
  default = "python3.14"
}

variable "runtime_table_name" {
  type        = string
  description = "The DynamoDB runtime table (infra/modules/data)."
}

variable "runtime_table_arn" {
  type = string
}

variable "oidc_parameter" {
  type        = string
  description = "SSM parameter with the Cognito app client's settings (infra/modules/identity)."
}

variable "oidc_secret_parameter" {
  type        = string
  description = "SSM SecureString with the Cognito app client's secret (infra/modules/identity)."
}

variable "database_url_parameter" {
  type        = string
  description = "SSM SecureString with app_api's pooled Neon URL, written by the deploy (tools/deploy)."
}

```

In `infra/modules/app/main.tf`, replace:
```hcl
locals {
  name = "nettriage-${var.stage}-api"
}
```
with:
```hcl
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  name = "nettriage-${var.stage}-api"
  parameter_arns = [
    for name in [var.oidc_parameter, var.oidc_secret_parameter, var.database_url_parameter] :
    "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter${name}"
  ]
}
```

In `infra/modules/app/main.tf`, replace:
```hcl

resource "aws_lambda_function" "api" {
```
with:
```hcl

# Sessions, sign-in state and rate limits in the runtime table (spec §6.8). TransactWriteItems
# and BatchWriteItem are authorized by the item-level actions they perform.
resource "aws_iam_role_policy" "api_runtime_table" {
  name = "runtime-table"
  role = aws_iam_role.api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:BatchWriteItem",
      ]
      Resource = var.runtime_table_arn
    }]
  })
}

# Its own settings and secrets, read once at cold start. SecureStrings use the AWS-managed
# aws/ssm key, whose key policy already lets this account's roles decrypt through SSM.
resource "aws_iam_role_policy" "api_parameters" {
  name = "read-own-parameters"
  role = aws_iam_role.api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "ssm:GetParameters"
      Resource = local.parameter_arns
    }]
  })
}

resource "aws_lambda_function" "api" {
```

In `infra/modules/app/main.tf`, replace:
```hcl
      NETTRIAGE_SERVICE_NAME             = "nettriage-api"
    }
```
with:
```hcl
      NETTRIAGE_SERVICE_NAME             = "nettriage-api"
      NETTRIAGE_RUNTIME_TABLE            = var.runtime_table_name
      NETTRIAGE_OIDC_PARAMETER           = var.oidc_parameter
      NETTRIAGE_OIDC_SECRET_PARAMETER    = var.oidc_secret_parameter
      NETTRIAGE_DATABASE_URL_PARAMETER   = var.database_url_parameter
    }
```

In `infra/modules/app/main.tf`, replace:
```hcl

  depends_on = [aws_cloudwatch_log_group.api, aws_iam_role_policy.api_logs]
}
```
with:
```hcl

  depends_on = [
    aws_cloudwatch_log_group.api,
    aws_iam_role_policy.api_logs,
    aws_iam_role_policy.api_runtime_table,
    aws_iam_role_policy.api_parameters,
  ]
}
```

In `infra/envs/dev/main.tf`, replace:
```hcl
module "app" {
```
with:
```hcl
# The API's SSM parameters. identity writes the first two; the deploy writes the database URL
# (tools/deploy/config.py, db_role_url_parameter). The function gets only these names.
locals {
  oidc_parameter         = "/nettriage/dev/api/oidc"
  oidc_secret_parameter  = "/nettriage/dev/api/oidc-client-secret"
  database_url_parameter = "/nettriage/dev/db/app-api-url"
}

module "data" {
  source = "../../modules/data"
  stage  = "dev"
}

module "app" {
```

In `infra/envs/dev/main.tf`, replace:
```hcl
  grafana_otlp_auth        = var.grafana_otlp_auth
}
```
with:
```hcl
  grafana_otlp_auth        = var.grafana_otlp_auth
  runtime_table_name       = module.data.table_name
  runtime_table_arn        = module.data.table_arn
  oidc_parameter           = local.oidc_parameter
  oidc_secret_parameter    = local.oidc_secret_parameter
  database_url_parameter   = local.database_url_parameter
}

module "identity" {
  source                = "../../modules/identity"
  stage                 = "dev"
  app_domain            = module.edge.distribution_domain
  oidc_parameter        = local.oidc_parameter
  oidc_secret_parameter = local.oidc_secret_parameter
}
```

In `infra/envs/dev/outputs.tf`, replace:
```hcl
  value = module.app.function_url
}

```
with:
```hcl
  value = module.app.function_url
}

output "sign_in_domain" {
  value = module.identity.sign_in_domain
}

```

In `.checkov.yaml`, replace:
```yaml
  - CKV_AWS_374    # CloudFront geo restriction: the demo is public worldwide

```
with:
```yaml
  - CKV_AWS_374    # CloudFront geo restriction: the demo is public worldwide
  - CKV_AWS_28     # DynamoDB point-in-time recovery: the runtime table holds only data that expires within 24 hours (spec §5.5)
  - CKV_AWS_119    # DynamoDB KMS customer key: AWS-owned encryption; customer keys cost money (spec §6.8)
  - CKV2_AWS_16    # DynamoDB auto scaling: fixed capacity inside the Always Free units (spec §5.5); scaling could leave them
  - CKV_AWS_337    # SSM KMS customer key: SecureStrings use the AWS-managed key (spec §3.2); customer keys cost money

```

In `justfile`, replace:
```
    for d in infra/bootstrap infra/modules/app infra/modules/edge; do
```
with:
```
    for d in infra/bootstrap infra/modules/app infra/modules/data infra/modules/edge infra/modules/identity; do
```

In `.github/workflows/ci.yml`, replace:
```yaml
          for dir in infra/bootstrap infra/modules/app infra/modules/edge; do
```
with:
```yaml
          for dir in infra/bootstrap infra/modules/app infra/modules/data infra/modules/edge infra/modules/identity; do
```

- [ ] **Step 5: Check the account, the deployed sign-in and the package size**

In `tools/deploy/preflight.py`, replace:
```python
        _denial_probe(run, env, f"SSM in {REGION}", ["aws", "ssm", "describe-parameters", "--region", REGION, "--max-results", "1"]),
    ]
```
with:
```python
        _denial_probe(run, env, f"SSM in {REGION}", ["aws", "ssm", "describe-parameters", "--region", REGION, "--max-results", "1"]),
        _denial_probe(run, env, f"DynamoDB in {REGION}", ["aws", "dynamodb", "list-tables", "--region", REGION, "--max-items", "1"]),
        _denial_probe(run, env, f"Cognito in {REGION}", ["aws", "cognito-idp", "list-user-pools", "--region", REGION, "--max-results", "1"]),
    ]
```

In `tools/smoke.py`, replace:
```python
from dataclasses import dataclass

```
with:
```python
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

```

In `tools/smoke.py`, replace:
```python

def run_checks(client: httpx.Client, base_url: str, function_url: str, version: str) -> list[Check]:
```
with:
```python

def _is_cognito_authorize(response: httpx.Response) -> bool:
    """A redirect to Cognito's authorize endpoint for the code flow with PKCE (spec §4.1)."""
    location = urlsplit(response.headers.get("location", ""))
    query = parse_qs(location.query)
    return (
        response.status_code == 302
        and location.scheme == "https"
        and (location.hostname or "").endswith(".amazoncognito.com")
        and location.path == "/oauth2/authorize"
        and query.get("code_challenge_method") == ["S256"]
        and bool(query.get("client_id"))
    )


def _sign_in_checks(client: httpx.Client, base_url: str) -> list[Check]:
    login = client.get(f"{base_url}/api/auth/login")
    redirects = _is_cognito_authorize(login)
    checks = [
        Check("sign-in redirects to Cognito", redirects,
              f"{login.status_code} {urlsplit(login.headers.get('location', '')).hostname}"),
    ]
    if redirects:
        page = client.get(login.headers["location"], follow_redirects=True)
        checks.append(Check("Cognito sign-in page loads", _is_html(page), str(page.status_code)))
    else:
        checks.append(Check("Cognito sign-in page loads", False, "no redirect to Cognito"))
    return checks


def run_checks(client: httpx.Client, base_url: str, function_url: str, version: str) -> list[Check]:
```

In `tools/smoke.py`, replace:
```python
              str(direct.status_code)),
    ]
```
with:
```python
              str(direct.status_code)),
        Check("api limits requests per viewer ip",
              '"public.ip"' in health.headers.get("ratelimit-policy", ""),
              health.headers.get("ratelimit-policy", "no RateLimit-Policy header")),
        *_sign_in_checks(client, base_url),
    ]
```

In `tools/build_lambda.py`, replace:
```python
FIXED_DATE = (2020, 1, 1, 0, 0, 0)

```
with:
```python
FIXED_DATE = (2020, 1, 1, 0, 0, 0)
# Lambda's limits for a zip uploaded directly, as Terraform does (not through S3).
MAX_ZIPPED_BYTES = 50 * 1024 * 1024
MAX_UNZIPPED_BYTES = 250 * 1024 * 1024

```

In `tools/build_lambda.py`, replace:
```python

def validate_zip(out: Path) -> None:
    problems: list[str] = []
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
```
with:
```python

def validate_zip(
    out: Path, *, max_zipped: int = MAX_ZIPPED_BYTES, max_unzipped: int = MAX_UNZIPPED_BYTES
) -> None:
    problems: list[str] = []
    if out.stat().st_size > max_zipped:
        problems.append(f"the zip is {out.stat().st_size} bytes; Lambda takes at most {max_zipped}")
    with zipfile.ZipFile(out) as zf:
        unzipped = sum(info.file_size for info in zf.infolist())
        if unzipped > max_unzipped:
            problems.append(f"unzipped it is {unzipped} bytes; Lambda takes at most {max_unzipped}")
        names = set(zf.namelist())
```

- [ ] **Step 6: Update the runbook and the README**

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown

Success looks like eight `PASS` lines:
- `api health`
```
with:
```markdown

Success looks like eleven `PASS` lines:
- `api health`
```

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
- `function url rejects direct calls`

```
with:
```markdown
- `function url rejects direct calls`
- `api limits requests per viewer ip`
- `sign-in redirects to Cognito`
- `Cognito sign-in page loads`

```

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
3. In AWS Settings → **Billing**, the amount due is still $0.

```
with:
```markdown
3. In AWS Settings → **Billing**, the amount due is still $0.

### B4. Sign in to dev
Sign-in uses Cognito's managed login page with a password and a one-time code from an
authenticator app (TOTP). The app's own pages come in Plan 6, so for now you check the result
with the API directly.
1. Install an authenticator app on your phone: Microsoft Authenticator or Google Authenticator.
2. In your browser, open `https://<id>.cloudfront.net/api/auth/login` (the address from B2's last
   line, plus `/api/auth/login`). You land on Cognito's sign-in page.
3. Choose **Create an account**. Enter your email address and a password of at least 12
   characters, then choose **Sign up**.
4. Cognito emails you a verification code from `no-reply@verificationemail.com` (check spam).
   Enter it and confirm.
5. Set up MFA: in the authenticator app, add an account and scan the QR code on the page. Type
   the 6-digit code the app shows, give the device a name if asked, and confirm.
6. You land on `https://<id>.cloudfront.net/app`, which is still Plan 1's placeholder page.
7. Open `https://<id>.cloudfront.net/api/v1/me`. It shows your email, `"memberships": []` and a
   `csrf_token`: you're signed in, and your user exists in the database.
8. To sign in again later, repeat step 2; Cognito asks for your password and a fresh code from
   the app. A session lasts up to 12 hours, and ends after 60 minutes without activity.

Do steps 2 to 5 in one go: the API gives a sign-in 5 minutes. If it takes longer, you land on
`/?sign_in=expired`; your account is kept, so start again at step 2 and just sign in. If you land
on another `/?sign_in=...` address, see Part C.

```

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
| `STOP: Smoke tests failed …` | Read the `FAIL` lines. Send them to Claude, or roll back |
| `Error acquiring the state lock` | Another plan or deploy is running, or one was interrupted. Wait a minute and retry; if it persists, send the lock ID to Claude |
```
with:
```markdown
| `STOP: Smoke tests failed …` | Read the `FAIL` lines. Send them to Claude, or roll back |
| `FAIL  Cognito sign-in page loads` right after the first Plan 3b deploy | A new Cognito domain can take a few minutes to start answering. Wait 5 minutes, then run `just deploy-dev` again (Terraform has nothing left to change). If it still fails, send the output to Claude |
| `FAIL  sign-in redirects to Cognito` or `FAIL  api limits requests per viewer ip` | Send the output to Claude. Sign-in, or rate limiting by IP, isn't working on the deployed stage |
| The browser lands on `/?sign_in=expired` | The sign-in took longer than 5 minutes, was finished in a different browser from the one that started it, or a page was reloaded or opened twice. Start again from `/api/auth/login` |
| The browser lands on `/?sign_in=failed` | The sign-in was cancelled, or Cognito's answer was refused. Start again; if it keeps happening, send Claude the time it happened (the logs record why, as `sign_in_failed`) |
| The browser lands on `/?sign_in=unavailable` | DynamoDB, Neon or Cognito didn't answer. Wait a minute and start again; if it keeps happening, tell Claude |
| The browser lands on `/?sign_in=disabled` | This account is disabled in the database. Tell Claude if that's unexpected |
| `Too Many Requests` with `"status": 429` | Too many sign-in attempts or requests from your IP or account. Wait the number of seconds in the `Retry-After` header (a minute at most for sign-in), then retry |
| Cognito's verification email never arrives | Check spam. Cognito's built-in sender allows about 50 emails a day per account; wait until tomorrow if many sign-ups ran today |
| `Error acquiring the state lock` | Another plan or deploy is running, or one was interrupted. Wait a minute and retry; if it persists, send the lock ID to Claude |
```

In `README.md`, replace:
```markdown

> Status: Milestone 1 in progress. Plan 1 (walking skeleton) delivers the deployed,
> observable foundation: CI, owner-run deploys, infrastructure as code and telemetry.

```
with:
```markdown

> Status: Milestone 1 in progress. Deployed so far: the walking skeleton (CI, owner-run
> deploys, infrastructure as code, telemetry), the detection engine, the Postgres data
> foundation, and sign-in with mandatory MFA.

```

In `README.md`, replace:
```markdown
- **OpenTelemetry** traces and metrics in Grafana Cloud; RFC 9457 errors that carry the trace ID.
- **Supply chain**: SHA-pinned actions, CodeQL, dependency review, Dependabot, Checkov and tflint.
```
with:
```markdown
- **OpenTelemetry** traces and metrics in Grafana Cloud; RFC 9457 errors that carry the trace ID.
- **Sign-in with mandatory TOTP MFA** through Cognito, the backend-for-frontend way: the browser only holds an opaque, HttpOnly session cookie, with CSRF checks on every state-changing request ([ADR 0004](docs/adr/0004-backend-for-frontend-sessions.md)).
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for the API.
- **Distributed rate limiting** with GCRA on DynamoDB, exact under concurrency ([ADR 0006](docs/adr/0006-gcra-rate-limiter.md)).
- **Supply chain**: SHA-pinned actions, CodeQL, dependency review, Dependabot, Checkov and tflint.
```

- [ ] **Step 7: Run every check**

Run: `just lint test tools-test tf-check pin-check cloud-check build-lambda`
Expected:
- lint is clean;
- `330 passed, 1 skipped`;
- tools `216 passed`;
- `terraform test` passes in all five stacks: bootstrap 3, app 6, data 3, edge 4 and identity 4 runs;
- `built dist\backend.zip (…KiB)`, under 50 MB (about 38 MB).

`tf-check`'s `terraform init` writes `.terraform.lock.hcl` into the two new modules. Commit those files, as the other modules do.

Run Checkov as CI does. It runs locally too, with Python 3.12:
```bash
uv run --no-project --python 3.12 --with checkov python -m checkov.main -d infra --framework terraform --config-file .checkov.yaml --quiet --compact
```
Expected: `Passed checks: 74, Failed checks: 0, Skipped checks: 2`. tflint runs in CI.

- [ ] **Step 8: Commit**

```bash
git add infra .checkov.yaml justfile .github/workflows/ci.yml tools docs/runbooks/setup-and-deploy.md README.md
git commit -m "feat(infra): Cognito with mandatory MFA, the runtime table, and sign-in smoke checks" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7 (Claude, then the owner): Pull request, deploy, and the first sign-in

- [ ] **Step 1 (Claude):**
  - Run `just lint test tools-test edge-test web-check tf-check pin-check cloud-check detection-report build-lambda`; everything must pass.
  - Get a final review of the whole branch, fix what it finds, then push `plan-3b/sign-in-and-sessions`.
  - Open the PR and watch CI. In the `backend` job, `test_exactly_the_burst_passes_when_100_requests_race` must **pass** against DynamoDB Local, not be skipped. Check the job log.
  - Request a Copilot review.
- [ ] **Step 2 (owner):** Review the PR. Optionally run `just plan-dev` (runbook B1). Expected: `Plan: 9 to add, 1 to change, 0 to destroy`.
  - The 9 additions are the DynamoDB table, the function's two new IAM policies, the user pool, its domain, the app client, the managed-login branding and the two SSM parameters.
  - The change is the API function's code and settings.
- [ ] **Step 3 (owner):** Squash-merge the PR.
- [ ] **Step 4 (owner):** Runbook B2 (`just deploy-dev`). Expected:
  - the preflight shows `PASS  DynamoDB in eu-north-1` and `PASS  Cognito in eu-north-1`;
  - `Database migrated.` (no new logins this time);
  - the Terraform plan above;
  - eleven smoke `PASS` lines.
- [ ] **Step 5 (owner):** Runbook B4: sign up with MFA, then open `/api/v1/me`. It shows your email, `"memberships": []` and a `csrf_token`.

## Plan 3b is done when

- [ ] `just lint test tools-test tf-check` passes locally. CI passes too, with the concurrency test run against DynamoDB Local.
- [ ] The owner has signed up on dev with TOTP MFA, and `/api/v1/me` shows their account.
- [ ] The PR is merged through review, with every thread resolved.

## Spec coverage of this plan

| Spec | Covered here |
|---|---|
| §4.1 sign-in flow: state, PKCE, nonce, single-use state, ID-token checks, just-in-time user, session, open-redirect protection | Tasks 2, 4 and 5; binding the state to the browser amends §4.1 (Task 5) |
| §5.3 row-level security for `users` (the owner's decision) | Task 1 |
| §5.4 `app_api` grants: column-level INSERT, no INSERT on `users`, the append-only audit log incl. TRUNCATE | Task 1 |
| §5.5 `runtime` table: sessions, `USERSESS`, `LOGIN`, `RL`, TTL, capacity, failure policy | Tasks 2, 3, 5 and 6 |
| §6.1 Cognito | Task 6 |
| §6.2 cookie, lifetime, new session per login, logout, sign out everywhere, CSRF | Tasks 2 and 5 |
| §6.3 first login (just-in-time, verified email) | Tasks 1, 4 and 5 |
| §6.4 deny by default: every route declares its access | Task 5, `Public` and `SignedIn`. The permission matrix is Plan 3c |
| §6.5 GCRA, subjects, 429, RateLimit headers, fail open | Tasks 3 and 5; the storage design amends §6.5 (Task 3) |
| §6.7 edge session check | unchanged (Plan 1): `/api/auth/*` and `/api/health` stay public at the edge |
| §6.8 secrets in SSM, loaded at cold start; `connect_timeout` | Tasks 1, 5 and 6 |
| §7 `/api/auth/login`, `/callback`, `/logout`, `/logout-all`, `/api/v1/me`, `/api/health` (`public.ip`) | Task 5 |
| §9.2 `nettriage.signups`, `csrf.failed`, `ratelimit.limited` (+ `ratelimit.errors`) | Task 5 |
| §9.3 log rules | Task 5 (`QUIET_LOGGERS`, the DEBUG sign-in test) |
| §9.4 `auth.session_created`, `auth.logout`, `auth.logout_all`, `ratelimit.limited` (sampled) | Task 5 |
| §11.4 CSRF, session fixation and expiry, rate-limiter concurrency, redaction suites | Tasks 3 and 5 |
| §7 org, member and invitation routes; §6.4 matrix and `authz.denied`; §6.7 64 KB body limit; `Idempotency-Key` | Not in 3b (Plan 3c) |
