# NetTriage Plan 3c: Organizations and Access Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let signed-in people create organizations, invite others by a one-time link, and manage members under §6.4's four roles:
- the org, member and invitation API of §7, with `Idempotency-Key` on `POST /api/v1/orgs`;
- deny by default: every org route declares a permission, outsiders get 404, and a test calls every endpoint as each role, a non-member and an anonymous caller;
- §5.7's quotas, the 64 KB request-body limit, the org audit log, and the audit events of §9.4;
- the sign-in follow-ups from Plan 3b's final review, and the owner's decision to ask for the password and code on every sign-in.

**Architecture:**
- `application/permissions.py` holds §6.4's table as data. `OrgMember(permission)` is a route dependency: it reads the caller's role in the path's `{org_id}` and returns 404 to a non-member or 403 to a member whose role lacks the permission. It records every denial.
- `adapters/organizations.py` and `adapters/invitations.py` run each change as one `app_api` transaction under row-level security.
  - Quotas and the last-owner rule lock first: a per-user advisory lock for the 3-orgs limit, and the org's row (`SELECT … FOR UPDATE`) for members and owners.
- Accepting an invitation finds it by the SHA-256 of its token through a new RLS policy, so only the holder of the link can see the row.
- The routes translate the domain's refusals into Problem Details: 403, 404, 409 or 422, and 503 for an outage.
- Idempotency keys live in the DynamoDB `runtime` table, next to sessions.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2 Core with psycopg 3, PostgreSQL 17 row-level security (Neon in AWS Europe Central 1), boto3 on DynamoDB, Alembic · moto for DynamoDB tests · no new dependencies and no infrastructure changes.

**Spec:** `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (revision 2). Read it alongside this plan; section numbers (§) refer to it. This plan implements:
- §4.1's `prompt=login` (the owner's decision) and the rest of §6.5 for sign-in;
- §5.3's invitation lookup and §5.7's organization quotas;
- §6.3 (invitations), §6.4 (authorization, all of it), §6.5's `invites.org` and §6.7's 64 KB request limit;
- §7's organization, member, invitation and audit-log routes, pagination and `Idempotency-Key` for `POST /orgs`;
- §9.2's `authz.denied` counter and §9.4's `org.*`, `member.*`, `invitation.revoked` and `authz.denied` events;
- §11.4's authorization matrix.

**Plan series:** Plan 3 of 7 ("data, identity and access") is split in three:
- **3a: the data foundation** (merged);
- **3b: sign-in and sessions** (merged);
- **3c (this plan): organizations and access control.**

The web pages for all of this come in Plan 6. Until then, runbook B5 (added by Task 6) shows how to try the API from the browser console.

**Branch:** `plan-3c/organizations-and-access-control`, from `main` at `1e51add` or later.

## Global Constraints

- **Stack.** Python **3.14**, with no new dependencies. mypy `--strict` and Ruff pass on `src` and `tests`. Work test-first.
- **Commands on this Windows machine.** Run Python tools as modules (`uv run python -m …`). Host policy blocks some uv launchers and every unsigned executable outside trusted tools. Backend tests need the local Postgres, which `just test` starts.
- **Roles and permissions (§6.4).** Exactly this table; every permission not listed for a role is denied:

  | Permission | Owner | Admin | Analyst | Viewer |
  |---|:-:|:-:|:-:|:-:|
  | `org:read`, `members:read`, `uploads:read`, `findings:read` | ✓ | ✓ | ✓ | ✓ |
  | `uploads:create`, `findings:triage`, `findings:comment`, `ai:request`, `ai:feedback` | ✓ | ✓ | ✓ | – |
  | `members:invite`, `members:role`, `members:remove` | ✓ any role | ✓ Analyst/Viewer only | – | – |
  | `org:update`, `audit:read`, `usage:read` | ✓ | ✓ | – | – |
  | `org:delete` | ✓ | – | – | – |

- **Rules (§6.4).**
  - Every member may leave an org except its last Owner.
  - A non-member, or an ID from another org, gets **404**. A member whose role lacks the permission gets **403**.
  - Nobody grants a role above their own or changes their own role. An org always keeps at least one Owner.
  - Every request model forbids fields it doesn't declare.
- **Quotas (§5.7).** At most **10** members per org, **3** orgs per user and **20** pending invitations per org. Members plus pending invitations stay within 10.
- **Invitations (§6.3).**
  - The token is 32 random bytes (base64url), shown once as `https://<app>/invite#<token>`.
  - Only its SHA-256 is stored. It expires after **7 days** and works once.
  - Accepting needs a signed-in user whose email matches the invitation's, ignoring case.
- **Rate limits (§6.5).** `POST …/invitations` counts against `invites.org` (20 a day, burst 5) per org. `GET /api/auth/login` and `/callback` each use their own `auth.ip` bucket.
- **Idempotency (§7).** `POST /api/v1/orgs` accepts `Idempotency-Key` (1 to 100 of `A-Z a-z 0-9 _ -`), kept for **24 hours** per user. The same key with another body gets **422**, and a retry while the first request runs gets **409**.
- **Request bodies (§6.7).** At most **64 KB**, or **413**.
- **Errors.** RFC 9457 Problem Details. `GET /api/auth/login` and `/callback` are browser navigations, so their failures, including rate limits, redirect.
- **Logs and audit (§9.3, §9.4).**
  - Logs never contain emails, invitation tokens, sign-in `state` or codes. Audit details carry roles and permissions, never emails.
  - `authz.denied` is written at most once a minute per caller and permission. The `nettriage.authz.denied` counter counts every denial.
- **Owner-only commands.** Claude never runs `aws login`, `just store-*` or `just deploy-*`.
- **Commits.** Use Conventional Commits. Every commit made by Claude ends with the trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Decisions this plan makes

1. **Every sign-in asks for the password and the code** (the owner's decision, 2026-09-29).
   - The authorize URL carries `prompt=login`, so Cognito never reuses its own session.
   - "Sign out everywhere" ends the app's sessions but can't end Cognito's session on other devices. Without `prompt=login`, a browser that still holds that Cognito session would sign back in with neither the password nor the code.
   - The cost is typing the password and a fresh code at every sign-in.
2. **An invitation is found by its token's hash through a row-level security policy, not a `SECURITY DEFINER` function.** This replaces Plan 3b's Decision 13.
   - `invitations` has `FORCE ROW LEVEL SECURITY`. On Neon the migration owner isn't a superuser, so a `SECURITY DEFINER` function owned by it would still be filtered and would see nothing.
   - Migration `0004` adds `app_invitation_token_hash()` and a `by_token` SELECT policy. A row is visible when its `token_hash` equals the transaction's `app.invitation_token_hash` setting.
   - Only the holder of the link can compute that hash. The token is 32 random bytes, so it can't be guessed.
3. **Quotas and the last-owner rule are safe under concurrent requests.**
   - Creating an org or accepting an invitation takes a per-user transaction advisory lock, so a user's orgs are counted by one transaction at a time.
   - Every change to an org's members locks the org's row first (`SELECT … FOR UPDATE`), so two owners demoting each other at once leave one owner, and two acceptances can't both take the tenth seat.
   - Threaded integration tests prove both against the local Postgres.
4. **Denials are sampled in the audit log** (this amends §6.4 and §9.4).
   - The metric counts every denial, but the `authz.denied` row is written at most once a minute per caller and permission. Anyone can sign up, so one row per denial would let a single account fill the database.
   - A non-member's denial isn't written into the org's log (`org_id` is empty and the org is the target), so outsiders can't write into it either.
5. **`OrgMember` decides 404 or 403 before the route runs.**
   - It reads the caller's role in a user-only transaction, where row-level security shows only the caller's own memberships, so no path parameter can make it read someone else's.
   - `members:remove` also allows `or_self`: any member may delete their own membership, which is how they leave.
6. **Refusals from the rules map to fixed statuses:**
   - escalation → 403, recorded as `authz.denied` with reason `escalation`;
   - unknown member or invitation → 404;
   - an invitation for another email → 403;
   - a wrong confirmation name → 422;
   - last owner, quotas and duplicates → 409;
   - a database outage → 503.
7. **`Idempotency-Key` is optional, and stored as `IDEMP#<user>#<key>`** in the `runtime` table.
   - The request hash is the SHA-256 of the canonical JSON body.
   - A request in progress holds its key for 1 minute, well past the API's 29-second timeout. A finished one holds its response for 24 hours.
   - A failed request forgets its key, so the client can retry with it.
   - If DynamoDB can't be read, the create is refused with 503 rather than risk a duplicate.
8. **Small lists return every item** (this amends §7's pagination).
   - Members and invitations are capped at 10 and 20 by §5.7, so they have no cursor.
   - The audit log pages newest first, with an opaque cursor on `(created_at, id)` and `limit` ≤ 100 (default 50). IPs and user agents stay out of it.
9. **Slugs come from the name.**
   - A slug is lowercase ASCII words joined by dashes, at most 50 characters, or `org` if nothing is left. A taken slug gets a random 6-hex suffix.
   - Renaming keeps the slug, so links keep working.
10. **Deleting an org needs `confirm_name` equal to its current name** (422 otherwise). Its memberships and invitations go with it; the audit log keeps its records.
11. **Inviting an address:**
    - The address must not already be a member's or have a pending invitation (409).
    - An expired invitation for it is revoked first (Plan 3a, Decision 6).
    - A new invitation is refused (409) if the org has 20 pending, or if members plus pending would exceed 10.
12. **Accepting refuses before touching the database** when the token isn't 43 base64url characters.
    - Unknown, used, revoked and expired invitations all get the same 404 message, so a caller learns nothing about which it was.
    - An invitation for another email gets 403.
13. **The 64 KB limit is an ASGI middleware, registered innermost.**
    - A declared `Content-Length` over the limit gets 413 before anything reads the body. A body without one is counted as it streams.
    - Innermost matters: the rate-limit header middleware runs the request stream in a task group, which would turn the 413 into a 400.
14. **Plan 3b's deferred review items land here:**
    - a rate-limited `login` or `callback` redirects to `/?sign_in=limited` with `Retry-After`;
    - `login` and `callback` stop sharing one `auth.ip` bucket, so a full sign-in costs one slot of each;
    - a `state` that isn't 43 base64url characters is refused before DynamoDB;
    - Cognito outages (unreachable, 5xx, keys unreadable) land on `/?sign_in=unavailable` instead of `failed`;
    - traces drop the query string of `/api/auth/*` URLs, which holds the code and the state;
    - a rate-limited health check writes no audit row, so the probe never touches the database;
    - signing out no longer races with "sign out everywhere": the `USERSESS` item is never left without an expiry, and a session created meanwhile stays listed;
    - a mojibake character in `tests/browser.py` is fixed.

## Review Focus

1. **After "sign out everywhere", someone opens a browser where the user signed in before.** The next sign-in must ask for the password and a fresh code, not reuse Cognito's session. Test: Task 1 `test_every_sign_in_asks_for_the_password_and_the_code_again`.
2. **Two owners demote each other at the same moment.** Exactly one owner must remain. Test: Task 3 `test_two_owners_demoting_each_other_at_once_leave_one_owner`.
3. **A double-click or a network retry on "Create organization".** One org must be created, and the retry must get the same answer. Tests: Task 5 `test_a_retry_with_the_same_idempotency_key_returns_the_same_org`, and Task 3 `test_three_parallel_creations_by_one_user_still_stop_at_three`.
4. **An invitation link forwarded to someone with another email address.** They must be refused, and the invitation must stay usable for the right person. Test: Task 5 `test_an_invitation_for_someone_else_is_refused`.
5. **An outsider probing org IDs, or one account hammering routes it may not use.** The outsider gets the same 404 as for an org that doesn't exist and writes nothing into that org's log, and the audit log gets at most one row a minute. Tests: Task 4 `test_an_outsider_gets_the_same_answer_as_for_an_org_that_does_not_exist`, `test_denials_are_counted_and_audited_without_writing_into_an_outsiders_target` and `test_repeated_denials_by_one_caller_write_one_audit_row_a_minute`.

## Owner prerequisites

- **Nothing is needed to build or review this plan.** Tests use the local Postgres, moto and a fake Cognito.
- **Nothing new is needed before the deploy.** The deploy applies migration `0004` and updates the API function; no AWS resources are added.
- **To try it afterwards** (runbook B5, which Task 6 adds), you only need to be signed in to dev as in B4.

## File map

| File | Responsibility | Task |
|---|---|---|
| `application/permissions.py` | §6.4's roles and permissions as data; who may manage whom | 2 |
| `application/organizations.py` | quotas, slugs, invitation tokens, email shape, the rules' errors and the role-change rule | 2, 3 |
| `migrations/versions/0004_invitation_lookup.py` | the `by_token` policy for accepting an invitation | 3 |
| `adapters/organizations.py` | orgs and members in Postgres, with the locks | 3 |
| `adapters/invitations.py` | invitations in Postgres: create, list, revoke, accept | 3 |
| `adapters/audit_log.py` | adds reading an org's events a page at a time | 3 |
| `adapters/idempotency.py` | `Idempotency-Key` in the `runtime` table | 4 |
| `platform/body_limit.py` | the 64 KB request-body limit | 4 |
| `entrypoints/api/access.py` | adds `OrgMember`, `OrgContext` and `deny` | 1, 4 |
| `entrypoints/api/schemas.py` | the org API's request and response models | 5 |
| `entrypoints/api/org_errors.py` | the rules' refusals as HTTP errors | 5 |
| `entrypoints/api/routes/orgs.py`, `members.py`, `invitations.py` | the routes | 5 |

All paths above are under `backend/src/nettriage/` (the migration under `backend/`).

---

### Task 1: Sign-in follow-ups from Plan 3b

**Files:**
- Modify: `backend/src/nettriage/application/sessions.py`, `backend/src/nettriage/adapters/oidc.py`, `backend/src/nettriage/adapters/sessions.py`, `backend/src/nettriage/entrypoints/api/access.py`, `backend/src/nettriage/entrypoints/api/app.py`, `backend/src/nettriage/entrypoints/api/routes/auth.py`, `backend/src/nettriage/entrypoints/api/routes/health.py`, `backend/src/nettriage/platform/telemetry.py`
- Modify (test helper): `backend/tests/browser.py`
- Test: `backend/tests/api/test_sign_in_routes.py`, `backend/tests/api/test_rate_limited_routes.py`, `backend/tests/unit/adapters/test_oidc.py`, `backend/tests/unit/adapters/test_session_store.py`, `backend/tests/unit/platform/test_telemetry.py`

**Interfaces:**
- Consumes (Plan 3b, on `main`): `Public`, `SignedIn`, `enforce`, `SessionStore`, `OidcClient`, `RateLimiter`, `POLICIES`, and the test harness in `backend/tests/conftest.py`, `browser.py` and `fake_idp.py`.
- Produces:
  - In `nettriage.application.sessions`: `SECRET` (the pattern of 32 random bytes in base64url, 43 characters) and `is_secret(value: str) -> bool`.
  - In `nettriage.adapters.oidc`: `OidcUnavailableError(OidcError)`, raised when Cognito can't be reached, answers 5xx, or its keys can't be read. `authorization_url` adds `prompt=login`.
  - In `nettriage.entrypoints.api.access`:
    - `RedirectInstead(location: str, headers: dict[str, str] | None = None)`, an exception the app answers with a `302` and `Cache-Control: no-store`;
    - `Public(policy: str, *, scope: str | None = None, limited_redirect: str | None = None, audit_limits: bool = True)`, whose subject is `f"{scope}:{ip}"` when a scope is given;
    - `enforce(request, policy, subject, *, actor, audit_limits=True, limited_redirect=None)`.
  - In `nettriage.entrypoints.api.routes.auth`: `SIGN_IN_LIMITED = "/?sign_in=limited"`.
  - In `nettriage.platform.telemetry`: `hide_sign_in_query(span, scope)`, the server request hook that drops the query of `/api/auth/*` URLs from spans.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/browser.py`, replace:
```python
def csrf_headers(client: TestClient) -> dict[str, str]:
    """What the SPA sends on a state-changing request (spec Â§6.2, Â§7)."""
    token = client.get("/api/v1/me").json()["csrf_token"]
```
with:
```python
def csrf_headers(client: TestClient) -> dict[str, str]:
    """What the SPA sends on a state-changing request (spec §6.2, §7)."""
    token = client.get("/api/v1/me").json()["csrf_token"]
```

In `backend/tests/unit/adapters/test_oidc.py`, replace:
```python
from fake_idp import CLIENT_ID, CLIENT_SECRET, FakeIdentityProvider, settings

from nettriage.adapters.oidc import OidcClient, OidcError


```
with:
```python
from fake_idp import CLIENT_ID, CLIENT_SECRET, FakeIdentityProvider, settings

from nettriage.adapters.oidc import OidcClient, OidcError, OidcUnavailableError


```

In `backend/tests/unit/adapters/test_oidc.py`, replace:
```python
        "code_challenge": "c",
        "code_challenge_method": "S256",
    }

```
with:
```python
        "code_challenge": "c",
        "code_challenge_method": "S256",
        "prompt": "login",
    }

```

In `backend/tests/unit/adapters/test_oidc.py`, replace:
```python
    client = OidcClient(settings(), httpx.Client(transport=httpx.MockTransport(down)), clock)

    with pytest.raises(OidcError, match="couldn't be reached"):
        client.identity(code="c", code_verifier="v", nonce="n")

```
with:
```python
    client = OidcClient(settings(), httpx.Client(transport=httpx.MockTransport(down)), clock)

    with pytest.raises(OidcUnavailableError, match="couldn't be reached"):
        client.identity(code="c", code_verifier="v", nonce="n")

```

In `backend/tests/unit/adapters/test_oidc.py`, replace:
```python

    client = OidcClient(settings(), httpx.Client(transport=httpx.MockTransport(flaky)), clock)
    with pytest.raises(OidcError, match="signing keys couldn't be fetched"):
        client.identity(code=idp.issue_code(nonce="n"), code_verifier="v", nonce="n")

```
with:
```python

    client = OidcClient(settings(), httpx.Client(transport=httpx.MockTransport(flaky)), clock)
    with pytest.raises(OidcUnavailableError, match="signing keys couldn't be fetched"):
        client.identity(code=idp.issue_code(nonce="n"), code_verifier="v", nonce="n")

```

In `backend/tests/unit/adapters/test_oidc.py`, replace:
```python
    assert identity.sub == "abc"

```
with:
```python
    assert identity.sub == "abc"


def test_a_cognito_server_error_is_unavailable_but_a_refused_code_is_not(
    oidc: OidcClient, idp: FakeIdentityProvider
) -> None:
    idp.token_status = 503
    with pytest.raises(OidcUnavailableError, match="answered 503"):
        oidc.identity(code=idp.issue_code(nonce="n"), code_verifier="v", nonce="n")

    idp.token_status = 400
    with pytest.raises(OidcError, match="answered 400") as refused:
        oidc.identity(code=idp.issue_code(nonce="n"), code_verifier="v", nonce="n")
    assert not isinstance(refused.value, OidcUnavailableError)

```

In `backend/tests/unit/adapters/test_session_store.py`, replace:
```python
from datetime import timedelta
from uuid import uuid4

from conftest import FakeClock, RuntimeTable

from nettriage.adapters.sessions import SessionStore
```
with:
```python
from datetime import timedelta
from typing import Any
from uuid import uuid4

from conftest import CountingClient, FakeClock, RuntimeTable

from nettriage.adapters.sessions import SessionStore
```

In `backend/tests/unit/adapters/test_session_store.py`, replace:
```python
    assert sessions.delete_all(user) == 0

```
with:
```python
    assert sessions.delete_all(user) == 0


def test_signing_out_after_signing_out_everywhere_leaves_nothing_that_never_expires(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    sessions = store(runtime_table)
    user = uuid4()
    _, session = sessions.create(user_id=user, now=clock(), ip=None, user_agent=None)
    sessions.delete_all(user)

    sessions.delete(session)

    items = runtime_table.client.scan(TableName=runtime_table.name)["Items"]
    assert all("expires_at" in item for item in items)


def test_a_session_created_while_signing_out_everywhere_stays_listed(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    """A sign-in that lands between reading the user's sessions and deleting them must survive,
    and still be found by the next "sign out everywhere"."""
    user = uuid4()
    late: list[str] = []
    other = SessionStore(runtime_table.client, runtime_table.name)

    class Racing(CountingClient):
        def batch_write_item(self, **kwargs: Any) -> Any:
            if not late:
                late.append(other.create(user_id=user, now=clock(), ip=None, user_agent=None)[0])
            return runtime_table.client.batch_write_item(**kwargs)

    sessions = SessionStore(Racing(runtime_table.client), runtime_table.name)  # type: ignore[arg-type]
    sessions.create(user_id=user, now=clock(), ip=None, user_agent=None)

    sessions.delete_all(user)

    assert sessions.get(late[0]) is not None
    assert sessions.delete_all(user) == 1
    assert sessions.get(late[0]) is None

```

In `backend/tests/unit/platform/test_telemetry.py`, replace:
```python
    assert response.status_code == 200

```
with:
```python
    assert response.status_code == 200


def test_sign_in_urls_in_traces_carry_no_code_or_state(services: Services) -> None:
    exporter = InMemorySpanExporter()
    tracer_provider = create_tracer_provider(SETTINGS, exporter)

    _client(services, tracer_provider).get(
        "/api/auth/callback?code=SECRETCODE&state=SECRETSTATE", follow_redirects=False
    )
    tracer_provider.force_flush()

    recorded = [
        f"{key}={value}"
        for span in exporter.get_finished_spans()
        for key, value in (span.attributes or {}).items()
    ]
    assert recorded
    assert [item for item in recorded if "SECRET" in item] == []

```

In `backend/tests/api/test_sign_in_routes.py`, replace:
```python
"""Sign-in through Cognito (spec §4.1, §6.2, §6.3)."""

from uuid import uuid4

from browser import finish_sign_in, query, set_cookie, sign_in, start_sign_in
from conftest import Database, RuntimeTable, counter
from fake_idp import DOMAIN, FakeIdentityProvider
```
with:
```python
"""Sign-in through Cognito (spec §4.1, §6.2, §6.3)."""

from datetime import datetime
from uuid import uuid4

import pytest
from browser import finish_sign_in, query, set_cookie, sign_in, start_sign_in
from conftest import Database, RuntimeTable, counter
from fake_idp import DOMAIN, FakeIdentityProvider
```

In `backend/tests/api/test_sign_in_routes.py`, replace:
```python

from nettriage.application.sessions import COOKIE_NAME
from nettriage.application.sign_in import code_challenge


def audit_rows(database: Database, action: str, email: str) -> list[tuple[str, dict[str, object]]]:
```
with:
```python

from nettriage.application.sessions import COOKIE_NAME
from nettriage.application.sign_in import code_challenge
from nettriage.entrypoints.api.services import Services


def audit_rows(database: Database, action: str, email: str) -> list[tuple[str, dict[str, object]]]:
```

In `backend/tests/api/test_sign_in_routes.py`, replace:
```python
        "__Host-sign-in=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Lax"
    )

```
with:
```python
        "__Host-sign-in=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Lax"
    )


def test_every_sign_in_asks_for_the_password_and_the_code_again(client: TestClient) -> None:
    """Cognito's own session survives "sign out everywhere" on other devices, so it must never
    let a sign-in skip the password and the authenticator code (the owner's decision)."""
    assert start_sign_in(client)["prompt"] == "login"


def test_a_state_can_not_be_replayed_even_with_its_sign_in_cookie(
    database_client: TestClient, idp: FakeIdentityProvider
) -> None:
    login = start_sign_in(database_client)
    finish_sign_in(database_client, idp, login, sub=f"sub-{uuid4()}")
    database_client.cookies.set("__Host-sign-in", login["state"], domain="app.test")

    replayed = finish_sign_in(database_client, idp, login, sub=f"sub-{uuid4()}")

    assert replayed.headers["location"] == "/?sign_in=expired"
    assert set_cookie(replayed, COOKIE_NAME) is None


def test_a_state_the_api_never_issued_is_refused_before_dynamodb(
    client: TestClient, services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    def untouched(state: str, now: datetime) -> None:
        raise AssertionError("the sign-in store was read")

    monkeypatch.setattr(services.login_states, "take", untouched)
    client.cookies.set("__Host-sign-in", "x" * 3000, domain="app.test")

    response = client.get(
        "/api/auth/callback", params={"code": "c", "state": "x" * 3000}, follow_redirects=False
    )

    assert response.headers["location"] == "/?sign_in=expired"


def test_an_unreachable_cognito_sends_the_browser_to_the_unavailable_page(
    client: TestClient, idp: FakeIdentityProvider
) -> None:
    idp.token_status = 503

    response = finish_sign_in(client, idp, start_sign_in(client))

    assert response.headers["location"] == "/?sign_in=unavailable"

```

In `backend/tests/api/test_rate_limited_routes.py`, replace:
```python
from uuid import uuid4

from browser import VIEWER, sign_in
from conftest import Database, RuntimeTable, counter
from fake_idp import FakeIdentityProvider
```
with:
```python
from uuid import uuid4

from browser import VIEWER, sign_in, start_sign_in
from conftest import Database, RuntimeTable, counter
from fake_idp import FakeIdentityProvider
```

In `backend/tests/api/test_rate_limited_routes.py`, replace:
```python


def test_too_many_sign_in_attempts_get_429_with_retry_after(
    database_client: TestClient, database: Database, metric_reader: InMemoryMetricReader
) -> None:
    viewer = {"CloudFront-Viewer-Address": f"198.51.100.{uuid4().int % 250 + 1}:1234"}
    responses = [
```
with:
```python


def test_too_many_sign_ins_from_one_ip_land_on_the_limited_page(
    database_client: TestClient, database: Database, metric_reader: InMemoryMetricReader
) -> None:
    """Sign-in is a browser navigation, so a limited one lands on a page, not on JSON."""
    viewer = {"CloudFront-Viewer-Address": f"198.51.100.{uuid4().int % 250 + 1}:1234"}
    responses = [
```

In `backend/tests/api/test_rate_limited_routes.py`, replace:
```python
    ]

    assert [r.status_code for r in responses] == [302] * 5 + [429, 429]
    limited = responses[5]
    assert limited.headers["content-type"] == "application/problem+json"
    assert limited.headers["retry-after"] == "6"
    assert limited.headers["ratelimit"] == '"auth.ip";r=0;t=30'
```
with:
```python
    ]

    assert [r.status_code for r in responses] == [302] * 7
    assert all("amazoncognito.com" in r.headers["location"] for r in responses[:5])
    limited = responses[5]
    assert limited.headers["location"] == "/?sign_in=limited"
    assert limited.headers["retry-after"] == "6"
    assert limited.headers["ratelimit"] == '"auth.ip";r=0;t=30'
```

In `backend/tests/api/test_rate_limited_routes.py`, replace:
```python
    assert counter(metric_reader, "nettriage.ratelimit.errors") == 1

```
with:
```python
    assert counter(metric_reader, "nettriage.ratelimit.errors") == 1


def test_a_sign_in_costs_one_slot_of_the_login_bucket_and_one_of_the_callback_bucket(
    database_client: TestClient, idp: FakeIdentityProvider
) -> None:
    """Five people behind one office address can all sign in within the burst."""
    viewer = {"CloudFront-Viewer-Address": "198.51.100.77:1234"}
    locations = []
    for _ in range(5):
        database_client.cookies.clear()
        login = start_sign_in(database_client, headers=viewer)
        code = idp.issue_code(nonce=login["nonce"], sub=f"sub-{uuid4()}")
        callback = database_client.get(
            "/api/auth/callback",
            params={"code": code, "state": login["state"]},
            headers=viewer,
            follow_redirects=False,
        )
        locations.append(callback.headers["location"])

    assert locations == ["/app"] * 5


def test_a_limited_health_check_writes_no_audit_row(
    database_client: TestClient, database: Database, metric_reader: InMemoryMetricReader
) -> None:
    """The health check must never touch the database, even when it is rate-limited."""
    viewer = {"CloudFront-Viewer-Address": "198.51.100.88:1234"}

    statuses = [database_client.get("/api/health", headers=viewer).status_code for _ in range(21)]

    assert statuses == [200] * 20 + [429]
    assert counter(metric_reader, "nettriage.ratelimit.limited") == 1
    with database.admin.begin() as connection:
        audited: int = connection.execute(
            text("SELECT count(*) FROM audit_log WHERE host(ip) = '198.51.100.88'")
        ).scalar_one()
    assert audited == 0

```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `just test`
Expected: FAIL. Collection stops on `ImportError: cannot import name 'OidcUnavailableError' from 'nettriage.adapters.oidc'`.

To see the other failures, run past that error:
```bash
cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest --continue-on-collection-errors
```
Expected: `9 failed, 319 passed, 1 skipped, 1 error`. The nine failures are:
- `test_too_many_sign_ins_from_one_ip_land_on_the_limited_page`, `test_a_sign_in_costs_one_slot_of_the_login_bucket_and_one_of_the_callback_bucket` and `test_a_limited_health_check_writes_no_audit_row`;
- `test_every_sign_in_asks_for_the_password_and_the_code_again`;
- `test_a_state_the_api_never_issued_is_refused_before_dynamodb` (the sign-in store is read, which the test forbids);
- `test_an_unreachable_cognito_sends_the_browser_to_the_unavailable_page` (`'/?sign_in=failed' == '/?sign_in=unavailable'`);
- `test_signing_out_after_signing_out_everywhere_leaves_nothing_that_never_expires` and `test_a_session_created_while_signing_out_everywhere_stays_listed`;
- `test_sign_in_urls_in_traces_carry_no_code_or_state`.

`test_a_state_can_not_be_replayed_even_with_its_sign_in_cookie` already passes: it pins, from the route, the single-use state that Plan 3b built.

- [ ] **Step 3: Ask for the password every time, and refuse bad state early**

In `backend/src/nettriage/application/sessions.py`, replace:
```python
import hashlib
import secrets
```
with:
```python
import hashlib
import re
import secrets
```

In `backend/src/nettriage/application/sessions.py`, replace:
```python

def new_secret() -> str:
    """32 random bytes, base64url-encoded: a session ID or a CSRF token."""
    return secrets.token_urlsafe(32)

```
with:
```python

SECRET = re.compile(r"[A-Za-z0-9_-]{43}")  # 32 bytes, base64url without padding


def new_secret() -> str:
    """32 random bytes, base64url-encoded: a session ID, a CSRF token or a sign-in state."""
    return secrets.token_urlsafe(32)


def is_secret(value: str) -> bool:
    """Whether a value could be one of our secrets; anything else is refused unread."""
    return SECRET.fullmatch(value) is not None

```

In `backend/src/nettriage/adapters/oidc.py`, replace:
```python

class OidcClient:
```
with:
```python

class OidcUnavailableError(OidcError):
    """Cognito couldn't be reached or failed on its side; trying again later may work."""


class OidcClient:
```

In `backend/src/nettriage/adapters/oidc.py`, replace:
```python
                "code_challenge_method": "S256",
            }
```
with:
```python
                "code_challenge_method": "S256",
                # Always ask for the password and the authenticator code, even while Cognito's
                # own session is alive: "sign out everywhere" can't end that session on other
                # devices (the owner's decision, Plan 3c).
                "prompt": "login",
            }
```

In `backend/src/nettriage/adapters/oidc.py`, replace:
```python
        except httpx.HTTPError:
            raise OidcError("the token endpoint couldn't be reached") from None
        if response.status_code != httpx.codes.OK:
```
with:
```python
        except httpx.HTTPError:
            raise OidcUnavailableError("the token endpoint couldn't be reached") from None
        if response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
            raise OidcUnavailableError(f"the token endpoint answered {response.status_code}")
        if response.status_code != httpx.codes.OK:
```

In `backend/src/nettriage/adapters/oidc.py`, replace:
```python
        except httpx.HTTPError, ValueError, KeyError, TypeError, jwt.PyJWTError:
            raise OidcError("Cognito's signing keys couldn't be fetched") from None
        self._fetched_at = self._clock()
```
with:
```python
        except httpx.HTTPError, ValueError, KeyError, TypeError, jwt.PyJWTError:
            raise OidcUnavailableError("Cognito's signing keys couldn't be fetched") from None
        self._fetched_at = self._clock()
```

- [ ] **Step 4: Redirect a limited sign-in, split the sign-in buckets, and spare the health check the audit row**

In `backend/src/nettriage/entrypoints/api/access.py`, replace:
```python

- `Public(policy)` rate-limits by client IP.
- `SignedIn` needs a valid session. It fails closed: an unreadable session store gives 503.
```
with:
```python

- `Public(policy)` rate-limits by client IP. A `scope` gives a route its own bucket under the
  policy; `limited_redirect` sends a limited browser navigation to a page instead of JSON; and
  `audit_limits=False` keeps a limited route from writing to the audit log (and so to Postgres).
- `SignedIn` needs a valid session. It fails closed: an unreadable session store gives 503.
```

In `backend/src/nettriage/entrypoints/api/access.py`, replace:
```python
import logging
import re
from typing import Annotated, NoReturn
```
with:
```python
import logging
from typing import Annotated, NoReturn
```

In `backend/src/nettriage/entrypoints/api/access.py`, replace:
```python
from nettriage.application.rate_limits import POLICIES, Policy, ip_subject
from nettriage.application.sessions import COOKIE_NAME, Session
from nettriage.entrypoints.api.auditing import VIEWER_ADDRESS, audit
```
with:
```python
from nettriage.application.rate_limits import POLICIES, Policy, ip_subject
from nettriage.application.sessions import COOKIE_NAME, Session, is_secret
from nettriage.entrypoints.api.auditing import VIEWER_ADDRESS, audit
```

In `backend/src/nettriage/entrypoints/api/access.py`, replace:
```python
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{43}")  # 32 bytes, base64url without padding
SAME_SITE = frozenset({"same-origin", "none"})
```
with:
```python
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
SAME_SITE = frozenset({"same-origin", "none"})
```

In `backend/src/nettriage/entrypoints/api/access.py`, replace:
```python

class Public(Access):
    def __init__(self, policy: str) -> None:
        self.policy = POLICIES[policy]

```
with:
```python

class RedirectInstead(Exception):
    """Answer with a redirect to `location` instead of Problem Details; the app turns it into a
    302 (see app.py)."""

    def __init__(self, location: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(location)
        self.location = location
        self.headers = headers or {}


class Public(Access):
    def __init__(
        self,
        policy: str,
        *,
        scope: str | None = None,
        limited_redirect: str | None = None,
        audit_limits: bool = True,
    ) -> None:
        self.policy = POLICIES[policy]
        self.scope = scope
        self.limited_redirect = limited_redirect
        self.audit_limits = audit_limits

```

In `backend/src/nettriage/entrypoints/api/access.py`, replace:
```python
        subject = ip_subject(request.headers.get(VIEWER_ADDRESS))
        if subject is not None:
            enforce(request, self.policy, subject, actor=None)

```
with:
```python
        subject = ip_subject(request.headers.get(VIEWER_ADDRESS))
        if subject is None:
            return
        enforce(
            request,
            self.policy,
            f"{self.scope}:{subject}" if self.scope else subject,
            actor=None,
            audit_limits=self.audit_limits,
            limited_redirect=self.limited_redirect,
        )

```

In `backend/src/nettriage/entrypoints/api/access.py`, replace:
```python

def enforce(request: Request, policy: Policy, subject: str, *, actor: UUID | None) -> None:
    """Check one rate limit, and remember the decision for the RateLimit headers."""
```
with:
```python

def enforce(
    request: Request,
    policy: Policy,
    subject: str,
    *,
    actor: UUID | None,
    audit_limits: bool = True,
    limited_redirect: str | None = None,
) -> None:
    """Check one rate limit, and remember the decision for the RateLimit headers."""
```

In `backend/src/nettriage/entrypoints/api/access.py`, replace:
```python
    services.metrics.rate_limited.add(1, {"policy": policy.name})
    if services.rate_limiter.should_audit(policy, subject):
        audit(
```
with:
```python
    services.metrics.rate_limited.add(1, {"policy": policy.name})
    if audit_limits and services.rate_limiter.should_audit(policy, subject):
        audit(
```

In `backend/src/nettriage/entrypoints/api/access.py`, replace:
```python
        )
    raise HTTPException(
        429,
        detail="Too many requests; try again later.",
        headers={"Retry-After": str(decision.retry_after_seconds)},
    )

```
with:
```python
        )
    retry_after = {"Retry-After": str(decision.retry_after_seconds)}
    if limited_redirect:
        raise RedirectInstead(limited_redirect, retry_after)
    raise HTTPException(429, detail="Too many requests; try again later.", headers=retry_after)

```

In `backend/src/nettriage/entrypoints/api/access.py`, replace:
```python
        raise unauthorized()
    if not SESSION_ID.fullmatch(session_id):
        _unknown_session(services, subject, now)
```
with:
```python
        raise unauthorized()
    if not is_secret(session_id):
        _unknown_session(services, subject, now)
```

In `backend/src/nettriage/entrypoints/api/app.py`, replace:
```python

from fastapi import FastAPI, Request, Response
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from nettriage.application.rate_limits import header_values
from nettriage.entrypoints.api.routes import auth, health, me
from nettriage.entrypoints.api.services import Services
```
with:
```python

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from nettriage.application.rate_limits import header_values
from nettriage.entrypoints.api.access import RedirectInstead
from nettriage.entrypoints.api.routes import auth, health, me
from nettriage.entrypoints.api.services import Services
```

In `backend/src/nettriage/entrypoints/api/app.py`, replace:
```python
    app.state.services = services
    register_error_handlers(app)
    app.middleware("http")(add_rate_limit_headers)
    for module in (health, auth, me):
```
with:
```python
    app.state.services = services
    register_error_handlers(app)
    app.add_exception_handler(RedirectInstead, redirect_instead)
    app.middleware("http")(add_rate_limit_headers)
    for module in (health, auth, me):
```

In `backend/src/nettriage/entrypoints/api/app.py`, replace:
```python
    return response

```
with:
```python
    return response


async def redirect_instead(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, RedirectInstead):
        raise TypeError(type(exc))
    return RedirectResponse(
        exc.location, status_code=302, headers={"Cache-Control": "no-store", **exc.headers}
    )

```

In `backend/src/nettriage/entrypoints/api/routes/auth.py`, replace:
```python

from nettriage.adapters.oidc import OidcError
from nettriage.adapters.users import sign_in_user
from nettriage.application.rate_limits import viewer_ip
from nettriage.application.sessions import COOKIE_NAME, new_secret
from nettriage.application.sign_in import (
```
with:
```python

from nettriage.adapters.oidc import OidcError, OidcUnavailableError
from nettriage.adapters.users import sign_in_user
from nettriage.application.rate_limits import viewer_ip
from nettriage.application.sessions import COOKIE_NAME, is_secret, new_secret
from nettriage.application.sign_in import (
```

In `backend/src/nettriage/entrypoints/api/routes/auth.py`, replace:
```python

@router.get("/login", dependencies=[Depends(Public("auth.ip"))])
def login(request: Request, return_to: str | None = None) -> RedirectResponse:
```
with:
```python

# A sign-in is one login and one callback; each has its own auth.ip bucket, so a sign-in costs
# one slot in each, and a limited one lands on the landing page instead of a JSON error.
SIGN_IN_LIMITED = "/?sign_in=limited"


@router.get(
    "/login",
    dependencies=[Depends(Public("auth.ip", scope="login", limited_redirect=SIGN_IN_LIMITED))],
)
def login(request: Request, return_to: str | None = None) -> RedirectResponse:
```

In `backend/src/nettriage/entrypoints/api/routes/auth.py`, replace:
```python

@router.get("/callback", dependencies=[Depends(Public("auth.ip"))])
def callback(
```
with:
```python

@router.get(
    "/callback",
    dependencies=[Depends(Public("auth.ip", scope="callback", limited_redirect=SIGN_IN_LIMITED))],
)
def callback(
```

In `backend/src/nettriage/entrypoints/api/routes/auth.py`, replace:
```python
    started_here = request.cookies.get(SIGN_IN_COOKIE, "")
    if not state or not hmac.compare_digest(started_here.encode(), state.encode()):
        return sign_in_failed("expired")  # not started in this browser, or over 15 minutes ago
```
with:
```python
    started_here = request.cookies.get(SIGN_IN_COOKIE, "")
    if not state or not is_secret(state):
        return sign_in_failed("expired")  # not a state this API issued; DynamoDB isn't asked
    if not hmac.compare_digest(started_here.encode(), state.encode()):
        return sign_in_failed("expired")  # not started in this browser, or over 15 minutes ago
```

In `backend/src/nettriage/entrypoints/api/routes/auth.py`, replace:
```python
        )
    except OidcError as error:
```
with:
```python
        )
    except OidcUnavailableError as error:
        logger.warning("sign_in_unavailable", extra={"reason": str(error)})
        return sign_in_failed("unavailable")
    except OidcError as error:
```

In `backend/src/nettriage/entrypoints/api/routes/health.py`, replace:
```python

@router.get("/health", dependencies=[Depends(Public("public.ip"))])
def health(response: Response, settings: Annotated[Settings, Depends(get_settings)]) -> Health:
```
with:
```python

# A limited health check only counts a metric: an audit row would wake Neon, and the probe
# must never touch the database.
@router.get("/health", dependencies=[Depends(Public("public.ip", audit_limits=False))])
def health(response: Response, settings: Annotated[Settings, Depends(get_settings)]) -> Health:
```

- [ ] **Step 5: Keep the code and state out of traces, and close the sign-out race**

In `backend/src/nettriage/platform/telemetry.py`, replace:
```python
"""

```
with:
```python
"""

from typing import Any

```

In `backend/src/nettriage/platform/telemetry.py`, replace:
```python
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter

```
with:
```python
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import Span

```

In `backend/src/nettriage/platform/telemetry.py`, replace:
```python

def instrument_app(
```
with:
```python

def hide_sign_in_query(span: Span, scope: dict[str, Any]) -> None:
    """The sign-in callback's query holds Cognito's authorization code and the sign-in state.
    Spans of /api/auth/* keep their URL without it."""
    if not span.is_recording() or not str(scope.get("path", "")).startswith("/api/auth/"):
        return
    attributes = getattr(span, "attributes", None) or {}
    for name in ("http.url", "http.target", "url.full"):
        value = attributes.get(name)
        if isinstance(value, str) and "?" in value:
            span.set_attribute(name, value.split("?", 1)[0])
    if "url.query" in attributes:
        span.set_attribute("url.query", "")


def instrument_app(
```

In `backend/src/nettriage/platform/telemetry.py`, replace:
```python
    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=tracer_provider, meter_provider=meter_provider
    )
```
with:
```python
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        server_request_hook=hide_sign_in_query,
    )
```

In `backend/src/nettriage/adapters/sessions.py`, replace:
```python
                        "Key": {"pk": {"S": _user_pk(session.user_id)}},
                        "UpdateExpression": "DELETE sessions :key",
                        "ExpressionAttributeValues": {":key": {"SS": [session.key]}},
                    }
```
with:
```python
                        "Key": {"pk": {"S": _user_pk(session.user_id)}},
                        # If "sign out everywhere" already removed the set, this recreates only
                        # its key; give it an expiry so TTL still cleans it up.
                        "UpdateExpression": (
                            "DELETE sessions :key SET expires_at = if_not_exists(expires_at, :exp)"
                        ),
                        "ExpressionAttributeValues": {
                            ":key": {"SS": [session.key]},
                            ":exp": {"N": str(epoch_seconds(session.expires_at))},
                        },
                    }
```

In `backend/src/nettriage/adapters/sessions.py`, replace:
```python
    def delete_all(self, user_id: UUID) -> int:
        """Delete every session the user has. Returns how many were listed."""
        response = self._client.get_item(
```
with:
```python
    def delete_all(self, user_id: UUID) -> int:
        """Delete every session the user has. Returns how many were listed. A session created
        while this runs survives, and stays listed for the next call."""
        response = self._client.get_item(
```

In `backend/src/nettriage/adapters/sessions.py`, replace:
```python
                requests = result.get("UnprocessedItems", {}).get(self._table, [])
        self._client.delete_item(TableName=self._table, Key={"pk": {"S": _user_pk(user_id)}})
        return len(keys)
```
with:
```python
                requests = result.get("UnprocessedItems", {}).get(self._table, [])
        if keys:
            # Remove only the keys read above: a session created meanwhile stays listed, so the
            # next "sign out everywhere" still finds it.
            self._client.update_item(
                TableName=self._table,
                Key={"pk": {"S": _user_pk(user_id)}},
                UpdateExpression="DELETE sessions :keys",
                ExpressionAttributeValues={":keys": {"SS": keys}},
            )
        return len(keys)
```

- [ ] **Step 6: Run the checks**

Run: `just lint test`
Expected:
- lint ends with `All checks passed!`, `… files already formatted` and `Success: no issues found in … source files`;
- the tests end with `347 passed, 1 skipped` and `Required test coverage of 85% reached.` The skipped test is the rate limiter's concurrency test, which needs DynamoDB Local (CI only).

The new tests cover the following:
- The authorize URL carries `prompt=login`.
- A state can't be replayed, even with its sign-in cookie. A state of the wrong shape (here 3,000 characters) is refused before DynamoDB is read.
- An unreachable Cognito lands on `/?sign_in=unavailable`. A Cognito 5xx is unavailable, but a refused code (400) is a failed sign-in.
- From one IP, five `login`s go to Cognito, and the sixth lands on `/?sign_in=limited` with `Retry-After: 6`. Five complete sign-ins from one IP all reach `/app`.
- A limited health check writes no audit row.
- Signing out after "sign out everywhere" leaves no `USERSESS` item without an expiry, and a session created while signing out everywhere stays listed.
- A sign-in's spans carry no code and no state.

- [ ] **Step 7: Commit**

```bash
git add backend/src backend/tests
git commit -m "fix(auth): ask for the password on every sign-in, and close Plan 3b's review items" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Roles, permissions and the organization rules

**Files:**
- Create: `backend/src/nettriage/application/permissions.py`, `backend/src/nettriage/application/organizations.py`
- Test: `backend/tests/unit/application/test_permissions.py`, `backend/tests/unit/application/test_organization_rules.py`

**Interfaces:**
- Produces:
  - In `nettriage.application.permissions`:
    - `Role = Literal["owner", "admin", "analyst", "viewer"]` and `ROLES: tuple[Role, ...]`;
    - `PERMISSIONS: dict[str, frozenset[Role]]`, §6.4's table;
    - `allows(role: Role, permission: str) -> bool`, which raises `KeyError` for a permission that isn't in the table;
    - `can_manage(actor: Role, target: Role) -> bool`: an owner manages every role, an admin only analysts and viewers.
  - In `nettriage.application.organizations`:
    - `MAX_MEMBERS_PER_ORG = 10`, `MAX_ORGS_PER_USER = 3`, `MAX_PENDING_INVITATIONS = 20`, `INVITATION_LIFETIME = timedelta(days=7)`, `MAX_SLUG = 60` and `MAX_EMAIL = 320`;
    - `slugify(name) -> str`, lowercase ASCII words joined by `-`, at most 50 characters, or `"org"`;
    - `with_suffix(slug) -> str`, which adds `-` and 6 random hex characters;
    - `new_invitation_token() -> str` (32 random bytes, base64url), `token_hash(token) -> str` (SHA-256 hex) and `invitation_url(app_origin, token) -> str`, which gives `f"{app_origin}/invite#{token}"`;
    - `normalize_email(value) -> str | None`, which trims the value and returns `None` if it isn't shaped like an address or is longer than 320 characters.

- [ ] **Step 1: Write the failing tests**

`backend/tests/unit/application/test_permissions.py`:
```python
"""The permission table and the no-escalation rule (spec §6.4)."""

import pytest

from nettriage.application.permissions import PERMISSIONS, ROLES, Role, allows, can_manage

# Spec §6.4's table, written out by hand: who has each permission.
SPEC = {
    "org:read": "owner admin analyst viewer",
    "members:read": "owner admin analyst viewer",
    "uploads:read": "owner admin analyst viewer",
    "findings:read": "owner admin analyst viewer",
    "uploads:create": "owner admin analyst",
    "findings:triage": "owner admin analyst",
    "findings:comment": "owner admin analyst",
    "ai:request": "owner admin analyst",
    "ai:feedback": "owner admin analyst",
    "members:invite": "owner admin",
    "members:role": "owner admin",
    "members:remove": "owner admin",
    "org:update": "owner admin",
    "audit:read": "owner admin",
    "usage:read": "owner admin",
    "org:delete": "owner",
}


def test_the_table_is_exactly_the_spec() -> None:
    assert set(PERMISSIONS) == set(SPEC)
    for permission, holders in SPEC.items():
        assert {role for role in ROLES if allows(role, permission)} == set(holders.split())


def test_an_unknown_permission_is_an_error_not_a_silent_no() -> None:
    with pytest.raises(KeyError):
        allows("owner", "org:destroy")


@pytest.mark.parametrize(
    ("actor", "manages"),
    [
        ("owner", {"owner", "admin", "analyst", "viewer"}),
        ("admin", {"analyst", "viewer"}),
        ("analyst", set()),
        ("viewer", set()),
    ],
)
def test_owners_manage_anyone_and_admins_only_analysts_and_viewers(
    actor: Role, manages: set[str]
) -> None:
    assert {target for target in ROLES if can_manage(actor, target)} == manages
```

`backend/tests/unit/application/test_organization_rules.py`:
```python
import hashlib
import re

import pytest

from nettriage.application.organizations import (
    MAX_SLUG,
    invitation_url,
    new_invitation_token,
    normalize_email,
    slugify,
    token_hash,
    with_suffix,
)

SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")  # the database's CHECK


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Acme Security", "acme-security"),
        ("  Blue   Team!! ", "blue-team"),
        ("Café Zürich", "cafe-zurich"),
        ("SOC-2 / Ops", "soc-2-ops"),
        ("!!!", "org"),
        ("日本", "org"),
    ],
)
def test_slugs_are_lowercase_ascii_words_joined_by_dashes(name: str, slug: str) -> None:
    assert slugify(name) == slug


def test_a_long_name_leaves_room_for_a_suffix_and_the_result_fits_the_database() -> None:
    slug = slugify("word " * 40)
    suffixed = with_suffix(slug)

    assert len(slug) <= 50
    assert len(suffixed) <= MAX_SLUG
    assert SLUG.match(slug)
    assert SLUG.match(suffixed)
    assert suffixed != with_suffix(slug)


def test_invitation_tokens_are_random_hashed_and_travel_in_the_fragment() -> None:
    token = new_invitation_token()

    assert len(token) == 43
    assert token != new_invitation_token()
    assert token_hash(token) == hashlib.sha256(token.encode()).hexdigest()
    assert invitation_url("https://app.test", token) == f"https://app.test/invite#{token}"


@pytest.mark.parametrize(
    ("value", "email"),
    [
        (" Ada@Example.com ", "Ada@Example.com"),
        ("a@b.co", "a@b.co"),
        ("no-at-sign", None),
        ("two@@example.com", None),
        ("spaces in@example.com", None),
        ("nodot@example", None),
        ("x" * 310 + "@example.com", None),
    ],
)
def test_emails_are_trimmed_and_checked_for_shape(value: str, email: str | None) -> None:
    assert normalize_email(value) == email
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `cd backend && uv run python -m pytest tests/unit/application`
Expected: FAIL. Collection stops with 2 errors: `No module named 'nettriage.application.organizations'` and `No module named 'nettriage.application.permissions'`.

- [ ] **Step 3: Write the permissions and the rules**

`backend/src/nettriage/application/permissions.py`:
```python
"""Who may do what in an organization (spec §6.4). Deny by default: a permission not in the
table is an error, and a role not listed for a permission doesn't have it."""

from __future__ import annotations

from typing import Literal, get_args

Role = Literal["owner", "admin", "analyst", "viewer"]
ROLES: tuple[Role, ...] = get_args(Role)

_EVERYONE: frozenset[Role] = frozenset(ROLES)
_CONTRIBUTORS: frozenset[Role] = frozenset({"owner", "admin", "analyst"})
_MANAGERS: frozenset[Role] = frozenset({"owner", "admin"})
_OWNERS: frozenset[Role] = frozenset({"owner"})

PERMISSIONS: dict[str, frozenset[Role]] = {
    "org:read": _EVERYONE,
    "members:read": _EVERYONE,
    "uploads:read": _EVERYONE,
    "findings:read": _EVERYONE,
    "uploads:create": _CONTRIBUTORS,
    "findings:triage": _CONTRIBUTORS,
    "findings:comment": _CONTRIBUTORS,
    "ai:request": _CONTRIBUTORS,
    "ai:feedback": _CONTRIBUTORS,
    "members:invite": _MANAGERS,
    "members:role": _MANAGERS,
    "members:remove": _MANAGERS,
    "org:update": _MANAGERS,
    "audit:read": _MANAGERS,
    "usage:read": _MANAGERS,
    "org:delete": _OWNERS,
}

# Whose membership each role may grant, change or remove: owners anyone, admins only analysts
# and viewers (spec §6.4). Nobody can grant a role above their own.
_MANAGEABLE: dict[Role, frozenset[Role]] = {
    "owner": _EVERYONE,
    "admin": frozenset({"analyst", "viewer"}),
    "analyst": frozenset(),
    "viewer": frozenset(),
}


def allows(role: Role, permission: str) -> bool:
    return role in PERMISSIONS[permission]


def can_manage(actor: Role, target: Role) -> bool:
    """Whether `actor` may invite someone as `target`, or change or remove a `target` member."""
    return target in _MANAGEABLE[actor]
```

`backend/src/nettriage/application/organizations.py`:
```python
"""Organization rules (spec §5.2, §5.7, §6.3): quotas, slugs, invitation tokens and emails."""

from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata
from datetime import timedelta

MAX_MEMBERS_PER_ORG = 10
MAX_ORGS_PER_USER = 3
MAX_PENDING_INVITATIONS = 20
INVITATION_LIFETIME = timedelta(days=7)
MAX_SLUG = 60
MAX_EMAIL = 320
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def slugify(name: str) -> str:
    """A slug from an org's name: lowercase ASCII words joined by dashes, at most 50 characters
    (leaving room for a suffix), or "org" if nothing is left."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", ascii_name.lower())
    slug = "-".join(words)[:50].strip("-")
    return slug or "org"


def with_suffix(slug: str) -> str:
    """The slug with a random suffix, for when the plain one is taken."""
    return f"{slug[: MAX_SLUG - 7]}-{secrets.token_hex(3)}"


def new_invitation_token() -> str:
    """32 random bytes, base64url-encoded; shown once in the invitation link (spec §6.3)."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def invitation_url(app_origin: str, token: str) -> str:
    """The token goes in the fragment, which browsers never send to servers (spec §6.3)."""
    return f"{app_origin}/invite#{token}"


def normalize_email(value: str) -> str | None:
    """The address as typed, trimmed; None if it can't be an email address."""
    email = value.strip()
    if len(email) > MAX_EMAIL or not _EMAIL.fullmatch(email):
        return None
    return email
```

- [ ] **Step 4: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `368 passed, 1 skipped`. The coverage table lists `permissions.py` and `organizations.py` at 100%.

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(orgs): roles, permissions and the organization rules" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Organizations, members and invitations in Postgres

**Files:**
- Create: `backend/migrations/versions/0004_invitation_lookup.py`, `backend/src/nettriage/adapters/organizations.py`, `backend/src/nettriage/adapters/invitations.py`
- Modify: `backend/src/nettriage/application/organizations.py`, `backend/src/nettriage/adapters/audit_log.py`
- Test: `backend/tests/integration/test_organizations.py`, `backend/tests/integration/test_invitations.py`, `backend/tests/integration/test_audit_log.py`

**Interfaces:**
- Consumes:
  - Task 2: `Role`, `allows`, `can_manage`, the quotas, `slugify`, `with_suffix`, `new_invitation_token`, `token_hash` and `INVITATION_LIFETIME`.
  - Plan 3a: `tenant_transaction(engine, *, org_id=None, user_id)`, `create_database_engine`, the `database` fixture (`.admin`, `.app_api`) with `APP_API_PASSWORD`, and `tenantdata.add_user`, `add_org`, `add_member` and `add_invitation`.
- Produces:
  - In `nettriage.application.organizations`:
    - the errors `OrgRuleError` and its subclasses `NotFound`, `Forbidden`, `LastOwner`, `QuotaExceeded`, `Conflict`, `InvitationInvalid`, `WrongEmail` and `ConfirmationMismatch`;
    - `check_role_change(*, actor_id, actor_role, target_id, current, new) -> None`, which raises `Forbidden` for a change of one's own role or a role the actor can't manage.
  - In `nettriage.adapters.organizations`:
    - `Organization(id, name, slug, role, member_count, created_at)`, where `role` is the caller's; `Member(user_id, email, display_name, role, joined_at)`; `Removal(role, left)`;
    - `role_of(engine, org_id, user_id) -> Role | None`, read in a user-only transaction;
    - `create_org(engine, *, user_id, name) -> Organization`;
    - `get_org(engine, org_id, user_id) -> Organization`, `rename_org(engine, org_id, user_id, name) -> Organization` and `delete_org(engine, org_id, user_id, confirm_name) -> None`;
    - `list_members(engine, org_id, user_id) -> list[Member]`;
    - `change_role(engine, org_id, *, actor_id, actor_role, target_id, role) -> tuple[Role, Member]`, which returns the previous role;
    - `remove_member(engine, org_id, *, actor_id, actor_role, target_id) -> Removal`;
    - the transaction helpers `set_org`, `lock_user`, `lock_org`, `count_user_orgs`, `count_members` and `read_org`, shared with the invitations adapter.
  - In `nettriage.adapters.invitations`:
    - `Invitation(id, email, role, expires_at, created_at, created_by)` and `INVALID`, the one message for every unusable invitation;
    - `list_invitations(engine, org_id, user_id, now) -> list[Invitation]`, the pending ones;
    - `create_invitation(engine, org_id, *, actor_id, actor_role, email, role, now) -> tuple[Invitation, str]`, where the `str` is the token, returned once;
    - `revoke_invitation(engine, org_id, *, actor_id, invitation_id, now) -> bool`;
    - `accept_invitation(engine, *, user_id, token, now) -> Organization`.
  - In `nettriage.adapters.audit_log`: `AuditEntry(id, created_at, actor_user_id, actor_type, action, target_type, target_id, outcome, details)` and `list_org_events(engine, org_id, user_id, *, limit, before: tuple[datetime, UUID] | None = None) -> list[AuditEntry]`, newest first.

- [ ] **Step 1: Write the failing tests**

`backend/tests/integration/test_organizations.py`:
```python
"""Organizations and members as the API's role (spec §5.2, §5.7, §6.4)."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from conftest import APP_API_PASSWORD, Database
from sqlalchemy import text
from tenantdata import add_member, add_user

from nettriage.adapters.organizations import (
    change_role,
    create_org,
    delete_org,
    get_org,
    list_members,
    remove_member,
    rename_org,
    role_of,
)
from nettriage.adapters.postgres import create_database_engine
from nettriage.application.organizations import (
    ConfirmationMismatch,
    Forbidden,
    LastOwner,
    NotFound,
    QuotaExceeded,
    slugify,
)
from nettriage.application.permissions import Role


def new_user(database: Database) -> UUID:
    with database.admin.begin() as connection:
        return add_user(connection)


def org_with(database: Database, **roles: Role) -> tuple[UUID, dict[str, UUID]]:
    """An org created by a new owner, plus members with the given roles, keyed by name."""
    owner = new_user(database)
    org = create_org(database.app_api, user_id=owner, name=f"Org {uuid4().hex[:8]}").id
    people = {"owner": owner}
    with database.admin.begin() as connection:
        for name, role in roles.items():
            people[name] = add_user(connection)
            add_member(connection, org, people[name], role)
    return org, people


def test_creating_an_org_makes_the_user_its_owner(database: Database) -> None:
    user = new_user(database)
    name = f"Acme {uuid4().hex[:8]}"

    org = create_org(database.app_api, user_id=user, name=name)

    assert (org.name, org.slug, org.role, org.member_count) == (name, slugify(name), "owner", 1)
    assert role_of(database.app_api, org.id, user) == "owner"


def test_a_taken_slug_gets_a_random_suffix(database: Database) -> None:
    name = f"Twin {uuid4().hex[:8]}"
    first = create_org(database.app_api, user_id=new_user(database), name=name)

    second = create_org(database.app_api, user_id=new_user(database), name=name)

    assert second.slug.startswith(f"{first.slug}-")


def test_a_user_can_belong_to_at_most_three_orgs(database: Database) -> None:
    user = new_user(database)
    for number in range(3):
        create_org(database.app_api, user_id=user, name=f"Mine {number}")

    with pytest.raises(QuotaExceeded, match="at most 3"):
        create_org(database.app_api, user_id=user, name="One too many")


def test_three_parallel_creations_by_one_user_still_stop_at_three(database: Database) -> None:
    """The per-user advisory lock serializes the quota check."""
    user = new_user(database)
    url = database.url.set(username="app_api", password=APP_API_PASSWORD)
    engine = create_database_engine(url.render_as_string(hide_password=False), pool_size=5)

    def attempt(number: int) -> str:
        try:
            create_org(engine, user_id=user, name=f"Race {number}")
        except QuotaExceeded:
            return "refused"
        return "created"

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(attempt, range(5)))
    engine.dispose()

    assert sorted(results) == ["created"] * 3 + ["refused"] * 2


def test_the_org_shows_the_callers_role_and_member_count(database: Database) -> None:
    org, people = org_with(database, viewer="viewer")

    seen = get_org(database.app_api, org, people["viewer"])

    assert (seen.role, seen.member_count) == ("viewer", 2)


def test_renaming_keeps_the_slug(database: Database) -> None:
    org, people = org_with(database)
    before = get_org(database.app_api, org, people["owner"])

    after = rename_org(database.app_api, org, people["owner"], "New name")

    assert (after.name, after.slug) == ("New name", before.slug)


def test_deleting_needs_the_exact_name_and_takes_the_members_along(database: Database) -> None:
    org, people = org_with(database, viewer="viewer")
    name = get_org(database.app_api, org, people["owner"]).name

    with pytest.raises(ConfirmationMismatch):
        delete_org(database.app_api, org, people["owner"], name.upper())
    delete_org(database.app_api, org, people["owner"], name)

    with database.admin.begin() as connection:
        left: int = connection.execute(
            text("SELECT count(*) FROM memberships WHERE org_id = :org"), {"org": org}
        ).scalar_one()
    assert left == 0
    assert role_of(database.app_api, org, people["viewer"]) is None


def test_members_are_listed_with_their_email_and_role(database: Database) -> None:
    org, people = org_with(database, analyst="analyst")

    members = list_members(database.app_api, org, people["analyst"])

    assert {(m.user_id, m.role) for m in members} == {
        (people["owner"], "owner"),
        (people["analyst"], "analyst"),
    }
    assert all(member.email.endswith("@example.com") for member in members)


def test_an_owner_can_give_any_role(database: Database) -> None:
    org, people = org_with(database, viewer="viewer")

    previous, member = change_role(
        database.app_api,
        org,
        actor_id=people["owner"],
        actor_role="owner",
        target_id=people["viewer"],
        role="admin",
    )

    assert (previous, member.role) == ("viewer", "admin")


@pytest.mark.parametrize(
    ("target", "role", "allowed"),
    [("viewer", "analyst", True), ("viewer", "admin", False), ("other_admin", "viewer", False)],
)
def test_an_admin_manages_only_analysts_and_viewers(
    database: Database, target: str, role: Role, allowed: bool
) -> None:
    org, people = org_with(database, admin="admin", other_admin="admin", viewer="viewer")

    def change() -> None:
        change_role(
            database.app_api,
            org,
            actor_id=people["admin"],
            actor_role="admin",
            target_id=people[target],
            role=role,
        )

    if allowed:
        change()
    else:
        with pytest.raises(Forbidden):
            change()


def test_nobody_changes_their_own_role(database: Database) -> None:
    org, people = org_with(database)

    with pytest.raises(Forbidden, match="your own role"):
        change_role(
            database.app_api,
            org,
            actor_id=people["owner"],
            actor_role="owner",
            target_id=people["owner"],
            role="admin",
        )


def test_the_last_owner_can_not_be_demoted_but_one_of_two_can(database: Database) -> None:
    org, people = org_with(database, second="owner")
    change_role(
        database.app_api,
        org,
        actor_id=people["owner"],
        actor_role="owner",
        target_id=people["second"],
        role="admin",
    )

    with pytest.raises(LastOwner):
        change_role(
            database.app_api,
            org,
            actor_id=people["second"],
            actor_role="owner",
            target_id=people["owner"],
            role="viewer",
        )


def test_two_owners_demoting_each_other_at_once_leave_one_owner(database: Database) -> None:
    """Changes lock the org's row, so the second demotion sees the first."""
    org, people = org_with(database, second="owner")
    url = database.url.set(username="app_api", password=APP_API_PASSWORD)
    engine = create_database_engine(url.render_as_string(hide_password=False), pool_size=2)

    def demote(pair: tuple[str, str]) -> str:
        actor, target = pair
        try:
            change_role(
                engine,
                org,
                actor_id=people[actor],
                actor_role="owner",
                target_id=people[target],
                role="viewer",
            )
        except LastOwner:
            return "refused"
        return "demoted"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(demote, [("owner", "second"), ("second", "owner")]))
    engine.dispose()

    assert sorted(results) == ["demoted", "refused"]


def test_changing_someone_who_isnt_a_member_is_not_found(database: Database) -> None:
    org, people = org_with(database)

    with pytest.raises(NotFound):
        change_role(
            database.app_api,
            org,
            actor_id=people["owner"],
            actor_role="owner",
            target_id=new_user(database),
            role="viewer",
        )


def test_any_member_may_leave_but_not_the_last_owner(database: Database) -> None:
    org, people = org_with(database, viewer="viewer")

    left = remove_member(
        database.app_api,
        org,
        actor_id=people["viewer"],
        actor_role="viewer",
        target_id=people["viewer"],
    )

    assert (left.role, left.left) == ("viewer", True)
    with pytest.raises(LastOwner):
        remove_member(
            database.app_api,
            org,
            actor_id=people["owner"],
            actor_role="owner",
            target_id=people["owner"],
        )


@pytest.mark.parametrize(
    ("actor", "target", "allowed"),
    [
        ("admin", "analyst", True),
        ("admin", "other_admin", False),
        ("admin", "owner", False),
        ("analyst", "viewer", False),
        ("owner", "admin", True),
    ],
)
def test_removing_someone_else_needs_the_right_to_manage_them(
    database: Database, actor: str, target: str, allowed: bool
) -> None:
    org, people = org_with(
        database, admin="admin", other_admin="admin", analyst="analyst", viewer="viewer"
    )
    actor_role = role_of(database.app_api, org, people[actor])
    assert actor_role is not None

    def remove() -> None:
        remove_member(
            database.app_api,
            org,
            actor_id=people[actor],
            actor_role=actor_role,
            target_id=people[target],
        )

    if allowed:
        remove()
        assert role_of(database.app_api, org, people[target]) is None
    else:
        with pytest.raises(Forbidden):
            remove()


def test_a_non_member_has_no_role(database: Database) -> None:
    org, _ = org_with(database)

    assert role_of(database.app_api, org, new_user(database)) is None
```

`backend/tests/integration/test_invitations.py`:
```python
"""Invitations as the API's role (spec §5.7, §6.3)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from conftest import Database
from sqlalchemy import text
from tenantdata import add_invitation, add_member, add_user

from nettriage.adapters.invitations import (
    accept_invitation,
    create_invitation,
    list_invitations,
    revoke_invitation,
)
from nettriage.adapters.organizations import create_org, get_org, role_of
from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.organizations import (
    Conflict,
    Forbidden,
    InvitationInvalid,
    QuotaExceeded,
    WrongEmail,
    token_hash,
)
from nettriage.application.permissions import Role

NOW = datetime.now(UTC)


def new_user(database: Database, email: str | None = None) -> UUID:
    with database.admin.begin() as connection:
        return add_user(connection, email)


def new_org(database: Database) -> tuple[UUID, UUID]:
    owner = new_user(database)
    return create_org(database.app_api, user_id=owner, name=f"Org {uuid4().hex[:8]}").id, owner


def invite(
    database: Database,
    org: UUID,
    owner: UUID,
    email: str,
    role: Role = "viewer",
    now: datetime = NOW,
) -> str:
    _, token = create_invitation(
        database.app_api, org, actor_id=owner, actor_role="owner", email=email, role=role, now=now
    )
    return token


def test_an_invitation_is_pending_and_only_its_tokens_hash_is_stored(database: Database) -> None:
    org, owner = new_org(database)
    email = f"{uuid4().hex}@example.com"

    invitation, token = create_invitation(
        database.app_api,
        org,
        actor_id=owner,
        actor_role="owner",
        email=email,
        role="analyst",
        now=NOW,
    )

    assert [i.id for i in list_invitations(database.app_api, org, owner, NOW)] == [invitation.id]
    assert (invitation.role, invitation.expires_at) == ("analyst", NOW + timedelta(days=7))
    with database.admin.begin() as connection:
        stored: str = connection.execute(
            text("SELECT token_hash FROM invitations WHERE id = :id"), {"id": invitation.id}
        ).scalar_one()
    assert stored == token_hash(token)
    assert token not in stored


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_an_admin_can_invite_only_analysts_and_viewers(database: Database, role: Role) -> None:
    org, _ = new_org(database)
    admin = new_user(database)
    with database.admin.begin() as connection:
        add_member(connection, org, admin, "admin")

    with pytest.raises(Forbidden):
        create_invitation(
            database.app_api,
            org,
            actor_id=admin,
            actor_role="admin",
            email=f"{uuid4().hex}@example.com",
            role=role,
            now=NOW,
        )


def test_a_member_or_a_pending_address_can_not_be_invited_again(database: Database) -> None:
    org, owner = new_org(database)
    email = f"{uuid4().hex}@example.com"
    member = new_user(database, email.upper())
    with database.admin.begin() as connection:
        add_member(connection, org, member, "viewer")
    pending = f"{uuid4().hex}@example.com"
    invite(database, org, owner, pending)

    with pytest.raises(Conflict, match="already a member"):
        invite(database, org, owner, email)
    with pytest.raises(Conflict, match="pending invitation"):
        invite(database, org, owner, pending.upper())


def test_an_expired_invitation_is_replaced_by_a_new_one(database: Database) -> None:
    org, owner = new_org(database)
    email = f"{uuid4().hex}@example.com"
    invite(database, org, owner, email)
    later = NOW + timedelta(days=8)

    invite(database, org, owner, email, now=later)

    assert len(list_invitations(database.app_api, org, owner, later)) == 1


def test_an_org_has_at_most_twenty_pending_invitations(database: Database) -> None:
    org, owner = new_org(database)
    with database.admin.begin() as connection:
        for _ in range(20):
            add_invitation(connection, org, owner, f"{uuid4().hex}@example.com")

    with pytest.raises(QuotaExceeded, match="20 pending"):
        invite(database, org, owner, f"{uuid4().hex}@example.com")


def test_members_and_pending_invitations_together_stay_within_ten(database: Database) -> None:
    org, owner = new_org(database)
    with database.admin.begin() as connection:
        for _ in range(8):
            add_member(connection, org, add_user(connection), "viewer")
    invite(database, org, owner, f"{uuid4().hex}@example.com")  # 9 members + 1 pending

    with pytest.raises(QuotaExceeded, match="10 members"):
        invite(database, org, owner, f"{uuid4().hex}@example.com")


def test_a_revoked_invitation_is_gone_and_can_not_be_revoked_twice(database: Database) -> None:
    org, owner = new_org(database)
    invitation, _ = create_invitation(
        database.app_api,
        org,
        actor_id=owner,
        actor_role="owner",
        email=f"{uuid4().hex}@example.com",
        role="viewer",
        now=NOW,
    )

    first = revoke_invitation(
        database.app_api, org, actor_id=owner, invitation_id=invitation.id, now=NOW
    )
    again = revoke_invitation(
        database.app_api, org, actor_id=owner, invitation_id=invitation.id, now=NOW
    )

    assert (first, again) == (True, False)
    assert list_invitations(database.app_api, org, owner, NOW) == []


def test_accepting_joins_the_org_with_the_invited_role(database: Database) -> None:
    org, owner = new_org(database)
    email = f"{uuid4().hex}@example.com"
    token = invite(database, org, owner, email, role="analyst")
    invitee = new_user(database, email.upper())

    joined = accept_invitation(database.app_api, user_id=invitee, token=token, now=NOW)

    assert (joined.id, joined.role) == (org, "analyst")
    assert role_of(database.app_api, org, invitee) == "analyst"
    assert list_invitations(database.app_api, org, owner, NOW) == []


def test_an_invitation_is_for_its_email_address_only(database: Database) -> None:
    org, owner = new_org(database)
    token = invite(database, org, owner, f"{uuid4().hex}@example.com")

    with pytest.raises(WrongEmail):
        accept_invitation(database.app_api, user_id=new_user(database), token=token, now=NOW)


def test_an_invitation_works_once(database: Database) -> None:
    org, owner = new_org(database)
    email = f"{uuid4().hex}@example.com"
    token = invite(database, org, owner, email)
    invitee = new_user(database, email)
    accept_invitation(database.app_api, user_id=invitee, token=token, now=NOW)

    with pytest.raises(InvitationInvalid):
        accept_invitation(database.app_api, user_id=invitee, token=token, now=NOW)


@pytest.mark.parametrize("state", ["revoked", "expired", "unknown"])
def test_a_revoked_expired_or_unknown_invitation_is_refused(database: Database, state: str) -> None:
    org, owner = new_org(database)
    email = f"{uuid4().hex}@example.com"
    invitation, token = create_invitation(
        database.app_api,
        org,
        actor_id=owner,
        actor_role="owner",
        email=email,
        role="viewer",
        now=NOW,
    )
    when = NOW
    if state == "revoked":
        revoke_invitation(
            database.app_api, org, actor_id=owner, invitation_id=invitation.id, now=NOW
        )
    elif state == "expired":
        when = NOW + timedelta(days=7)
    else:
        token = "never-issued"  # noqa: S105 - a made-up token, not a password

    with pytest.raises(InvitationInvalid):
        accept_invitation(
            database.app_api, user_id=new_user(database, email), token=token, now=when
        )


def test_a_user_in_three_orgs_can_not_join_a_fourth(database: Database) -> None:
    org, owner = new_org(database)
    email = f"{uuid4().hex}@example.com"
    token = invite(database, org, owner, email)
    invitee = new_user(database, email)
    for number in range(3):
        create_org(database.app_api, user_id=invitee, name=f"Theirs {number}")

    with pytest.raises(QuotaExceeded, match="at most 3"):
        accept_invitation(database.app_api, user_id=invitee, token=token, now=NOW)


def test_a_full_org_takes_no_one_else(database: Database) -> None:
    org, owner = new_org(database)
    email = f"{uuid4().hex}@example.com"
    token = invite(database, org, owner, email)
    with database.admin.begin() as connection:
        for _ in range(9):
            add_member(connection, org, add_user(connection), "viewer")

    with pytest.raises(QuotaExceeded, match="already has 10"):
        accept_invitation(database.app_api, user_id=new_user(database, email), token=token, now=NOW)
    assert get_org(database.app_api, org, owner).member_count == 10


def test_only_the_tokens_hash_reveals_an_invitation_to_a_non_member(database: Database) -> None:
    org, owner = new_org(database)
    token = invite(database, org, owner, f"{uuid4().hex}@example.com")
    stranger = new_user(database)

    def visible(hash_setting: str | None) -> int:
        with tenant_transaction(database.app_api, user_id=stranger) as connection:
            if hash_setting is not None:
                connection.execute(
                    text("SELECT set_config('app.invitation_token_hash', :h, true)"),
                    {"h": hash_setting},
                )
            count: int = connection.execute(text("SELECT count(*) FROM invitations")).scalar_one()
        return count

    assert (visible(None), visible(token_hash("guess")), visible(token_hash(token))) == (0, 0, 1)
```

In `backend/tests/integration/test_audit_log.py`, replace:
```python
from tenantdata import add_tenant

from nettriage.adapters.audit_log import record
from nettriage.application.audit import AuditEvent

```
with:
```python
from tenantdata import add_tenant

from nettriage.adapters.audit_log import list_org_events, record
from nettriage.application.audit import AuditEvent

```

In `backend/tests/integration/test_audit_log.py`, replace:
```python
        truncate()

```
with:
```python
        truncate()


def test_an_orgs_events_are_listed_newest_first_page_by_page(database: Database) -> None:
    tenant, other = add_tenant(database.admin), add_tenant(database.admin)
    for number in range(5):
        record(
            database.app_api,
            AuditEvent(
                action=f"org.event{number}",
                outcome="success",
                actor_type="user",
                actor_user_id=tenant.owner_id,
                org_id=tenant.org_id,
                ip="203.0.113.9",
            ),
        )
    record(
        database.app_api,
        AuditEvent(action="other.org", outcome="success", actor_type="system", org_id=other.org_id),
    )

    first = list_org_events(database.app_api, tenant.org_id, tenant.owner_id, limit=3)
    rest = list_org_events(
        database.app_api,
        tenant.org_id,
        tenant.owner_id,
        limit=3,
        before=(first[-1].created_at, first[-1].id),
    )

    assert [e.action for e in first + rest] == [f"org.event{n}" for n in (4, 3, 2, 1, 0)]
    assert not hasattr(first[0], "ip")

```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `just test`
Expected: FAIL. Collection stops with 3 errors: `cannot import name 'list_org_events' from 'nettriage.adapters.audit_log'`, `No module named 'nettriage.adapters.invitations'` and `No module named 'nettriage.adapters.organizations'`.

- [ ] **Step 3: Add the invitation lookup policy**

`backend/migrations/versions/0004_invitation_lookup.py`:
```python
"""Accepting an invitation (Plan 3c, spec §6.3): the invitee isn't a member of the org yet, so
row-level security hides the invitation from them. A transaction that sets
`app.invitation_token_hash` may read the one invitation with that hash.

A SECURITY DEFINER function can't do this lookup instead: `invitations` has FORCE ROW LEVEL
SECURITY, which applies to its owner too, and on Neon the owner isn't a superuser. Knowing the
hash already means knowing the invitation (the link holds the token), so the policy grants
nothing more than the link does.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION app_invitation_token_hash() RETURNS text LANGUAGE sql STABLE
            AS $$ SELECT NULLIF(current_setting('app.invitation_token_hash', true), '') $$;
        GRANT EXECUTE ON FUNCTION app_invitation_token_hash() TO app_api;

        CREATE POLICY by_token ON invitations FOR SELECT
            USING (token_hash = app_invitation_token_hash());
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP POLICY by_token ON invitations;
        DROP FUNCTION app_invitation_token_hash();
        """
    )
```

- [ ] **Step 4: Add the rules' errors and the role-change rule**

In `backend/src/nettriage/application/organizations.py`, replace:
```python
import unicodedata
from datetime import timedelta

MAX_MEMBERS_PER_ORG = 10
```
with:
```python
import unicodedata
from datetime import timedelta

from nettriage.application.permissions import Role, can_manage

MAX_MEMBERS_PER_ORG = 10
```

In `backend/src/nettriage/application/organizations.py`, replace:
```python
    return email

```
with:
```python
    return email


class OrgRuleError(Exception):
    """A request the organization rules refuse. The message is safe to show to the caller."""


class NotFound(OrgRuleError):
    """No such member or invitation in this organization."""


class Forbidden(OrgRuleError):
    """The caller's role doesn't allow this (spec §6.4's no-escalation rules)."""


class LastOwner(OrgRuleError):
    """An organization always keeps at least one owner (spec §6.4)."""


class QuotaExceeded(OrgRuleError):
    """One of §5.7's limits: 3 orgs per user, 10 members per org, 20 pending invitations."""


class Conflict(OrgRuleError):
    """The request clashes with what exists: already a member, already invited."""


class InvitationInvalid(OrgRuleError):
    """The invitation doesn't exist, was used or revoked, or has expired."""


class WrongEmail(OrgRuleError):
    """The invitation is for a different email address."""


class ConfirmationMismatch(OrgRuleError):
    """Deleting an org needs its exact name as confirmation (spec §7)."""


def check_role_change(
    *, actor_id: object, actor_role: Role, target_id: object, current: Role, new: Role
) -> None:
    """No escalation (spec §6.4): nobody changes their own role, and a manager changes only
    roles they may manage, to roles they may grant."""
    if actor_id == target_id:
        raise Forbidden("You can't change your own role.")
    if not can_manage(actor_role, current) or not can_manage(actor_role, new):
        raise Forbidden("Your role can't grant or change that role.")

```

- [ ] **Step 5: Write the organization and invitation adapters, and the audit log reader**

`backend/src/nettriage/adapters/organizations.py`:
```python
"""Organizations and their members in Postgres (spec §5.2, §5.7, §6.4).

Each function is one transaction as `app_api`, so row-level security applies throughout.
Changes to an org's members lock the org's row first (`SELECT … FOR UPDATE`), so two requests
can't both remove the last owner or both take the tenth seat.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid7

from psycopg import errors
from sqlalchemy import Connection, Engine, Row, text
from sqlalchemy.exc import IntegrityError

from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.organizations import (
    MAX_ORGS_PER_USER,
    ConfirmationMismatch,
    Forbidden,
    LastOwner,
    NotFound,
    OrgRuleError,
    QuotaExceeded,
    check_role_change,
    slugify,
    with_suffix,
)
from nettriage.application.permissions import Role, allows, can_manage


@dataclass(frozen=True)
class Organization:
    id: UUID
    name: str
    slug: str
    role: Role  # the caller's
    member_count: int
    created_at: datetime


@dataclass(frozen=True)
class Member:
    user_id: UUID
    email: str
    display_name: str | None
    role: Role
    joined_at: datetime


@dataclass(frozen=True)
class Removal:
    role: Role  # the removed member's
    left: bool  # they removed themselves


def role_of(engine: Engine, org_id: UUID, user_id: UUID) -> Role | None:
    """The user's role in the org, read in a user-only transaction: row-level security shows
    only the user's own memberships, so this can't be fooled into another user's row."""
    with tenant_transaction(engine, user_id=user_id) as connection:
        role = connection.execute(
            text("SELECT role FROM memberships WHERE org_id = :org AND user_id = :user"),
            {"org": org_id, "user": user_id},
        ).scalar_one_or_none()
    return cast(Role | None, role)


def create_org(engine: Engine, *, user_id: UUID, name: str) -> Organization:
    """A new org with the user as its owner. Its slug comes from the name, with a random
    suffix if the plain one is taken."""
    org_id = uuid7()
    base = slugify(name)
    for slug in (base, with_suffix(base), with_suffix(base)):
        try:
            with tenant_transaction(engine, user_id=user_id) as connection:
                lock_user(connection, user_id)
                if count_user_orgs(connection, user_id) >= MAX_ORGS_PER_USER:
                    raise QuotaExceeded(f"You can belong to at most {MAX_ORGS_PER_USER} orgs.")
                set_org(connection, org_id)
                connection.execute(
                    text(
                        "INSERT INTO organizations (id, name, slug, created_by) "
                        "VALUES (:id, :name, :slug, :user)"
                    ),
                    {"id": org_id, "name": name, "slug": slug, "user": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO memberships (org_id, user_id, role) "
                        "VALUES (:org, :user, 'owner')"
                    ),
                    {"org": org_id, "user": user_id},
                )
                return read_org(connection, org_id, user_id)
        except IntegrityError as error:
            if not _slug_taken(error):
                raise
    raise OrgRuleError("Couldn't find a free address for this org; try another name.")


def get_org(engine: Engine, org_id: UUID, user_id: UUID) -> Organization:
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        return read_org(connection, org_id, user_id)


def rename_org(engine: Engine, org_id: UUID, user_id: UUID, name: str) -> Organization:
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        connection.execute(
            text("UPDATE organizations SET name = :name WHERE id = :org"),
            {"name": name, "org": org_id},
        )
        return read_org(connection, org_id, user_id)


def delete_org(engine: Engine, org_id: UUID, user_id: UUID, confirm_name: str) -> None:
    """Delete the org and, through cascades, everything it owns. The audit log keeps its rows."""
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        name = connection.execute(
            text("SELECT name FROM organizations WHERE id = :org FOR UPDATE"), {"org": org_id}
        ).scalar_one_or_none()
        if name is None:
            raise NotFound("No such organization.")
        if confirm_name != name:
            raise ConfirmationMismatch("Type the organization's exact name to delete it.")
        connection.execute(text("DELETE FROM organizations WHERE id = :org"), {"org": org_id})


def list_members(engine: Engine, org_id: UUID, user_id: UUID) -> list[Member]:
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        rows = connection.execute(
            text(
                "SELECT m.user_id, u.email, u.display_name, m.role, m.created_at "
                "FROM memberships m JOIN users u ON u.id = m.user_id "
                "WHERE m.org_id = :org ORDER BY m.created_at, m.user_id"
            ),
            {"org": org_id},
        ).all()
    return [_member(row) for row in rows]


def change_role(
    engine: Engine,
    org_id: UUID,
    *,
    actor_id: UUID,
    actor_role: Role,
    target_id: UUID,
    role: Role,
) -> tuple[Role, Member]:
    """Change a member's role. Returns their previous role and the member as they are now."""
    with tenant_transaction(engine, org_id=org_id, user_id=actor_id) as connection:
        lock_org(connection, org_id)
        current = _role(connection, org_id, target_id)
        check_role_change(
            actor_id=actor_id, actor_role=actor_role, target_id=target_id, current=current, new=role
        )
        if current == "owner" and role != "owner":
            _keep_an_owner(connection, org_id)
        connection.execute(
            text("UPDATE memberships SET role = :role WHERE org_id = :org AND user_id = :user"),
            {"role": role, "org": org_id, "user": target_id},
        )
        row = connection.execute(
            text(
                "SELECT m.user_id, u.email, u.display_name, m.role, m.created_at "
                "FROM memberships m JOIN users u ON u.id = m.user_id "
                "WHERE m.org_id = :org AND m.user_id = :user"
            ),
            {"org": org_id, "user": target_id},
        ).one()
    return current, _member(row)


def remove_member(
    engine: Engine, org_id: UUID, *, actor_id: UUID, actor_role: Role, target_id: UUID
) -> Removal:
    """Remove a member, or let the caller leave (spec §6.4): anyone may leave except the last
    owner; removing someone else needs `members:remove` and a role the caller may manage."""
    with tenant_transaction(engine, org_id=org_id, user_id=actor_id) as connection:
        lock_org(connection, org_id)
        current = _role(connection, org_id, target_id)
        leaving = target_id == actor_id
        if not leaving and not (
            allows(actor_role, "members:remove") and can_manage(actor_role, current)
        ):
            raise Forbidden("Your role can't remove that member.")
        if current == "owner":
            _keep_an_owner(connection, org_id)
        connection.execute(
            text("DELETE FROM memberships WHERE org_id = :org AND user_id = :user"),
            {"org": org_id, "user": target_id},
        )
    return Removal(role=current, left=leaving)


# Shared with the invitations adapter.


def set_org(connection: Connection, org_id: UUID | None) -> None:
    """Switch the transaction's org, for work that starts user-only (quotas) and then writes
    inside one org."""
    connection.execute(
        text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id or "")}
    )


def lock_user(connection: Connection, user_id: UUID) -> None:
    """Serialize a user's org-count checks, so two requests can't both take the third seat."""
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:user, 0))"), {"user": str(user_id)}
    )


def lock_org(connection: Connection, org_id: UUID) -> None:
    locked = connection.execute(
        text("SELECT id FROM organizations WHERE id = :org FOR UPDATE"), {"org": org_id}
    ).scalar_one_or_none()
    if locked is None:
        raise NotFound("No such organization.")


def count_user_orgs(connection: Connection, user_id: UUID) -> int:
    """The user's memberships. Needs a transaction with no org set, where row-level security
    shows all of the user's own memberships."""
    count: int = connection.execute(
        text("SELECT count(*) FROM memberships WHERE user_id = :user"), {"user": user_id}
    ).scalar_one()
    return count


def count_members(connection: Connection, org_id: UUID) -> int:
    count: int = connection.execute(
        text("SELECT count(*) FROM memberships WHERE org_id = :org"), {"org": org_id}
    ).scalar_one()
    return count


def read_org(connection: Connection, org_id: UUID, user_id: UUID) -> Organization:
    row = connection.execute(
        text(
            "SELECT o.id, o.name, o.slug, o.created_at, m.role, "
            "(SELECT count(*) FROM memberships c WHERE c.org_id = o.id) AS member_count "
            "FROM organizations o JOIN memberships m ON m.org_id = o.id AND m.user_id = :user "
            "WHERE o.id = :org"
        ),
        {"org": org_id, "user": user_id},
    ).one_or_none()
    if row is None:
        raise NotFound("No such organization.")
    return Organization(
        id=row.id,
        name=row.name,
        slug=row.slug,
        role=row.role,
        member_count=row.member_count,
        created_at=row.created_at,
    )


def _role(connection: Connection, org_id: UUID, user_id: UUID) -> Role:
    role = connection.execute(
        text("SELECT role FROM memberships WHERE org_id = :org AND user_id = :user"),
        {"org": org_id, "user": user_id},
    ).scalar_one_or_none()
    if role is None:
        raise NotFound("No such member in this organization.")
    return cast(Role, role)


def _keep_an_owner(connection: Connection, org_id: UUID) -> None:
    owners: int = connection.execute(
        text("SELECT count(*) FROM memberships WHERE org_id = :org AND role = 'owner'"),
        {"org": org_id},
    ).scalar_one()
    if owners <= 1:
        raise LastOwner("An organization needs at least one owner. Make someone else owner first.")


def _member(row: Row[Any]) -> Member:
    return Member(
        user_id=row.user_id,
        email=row.email,
        display_name=row.display_name,
        role=row.role,
        joined_at=row.created_at,
    )


def _slug_taken(error: IntegrityError) -> bool:
    return (
        isinstance(error.orig, errors.UniqueViolation)
        and error.orig.diag.constraint_name == "organizations_slug_key"
    )
```

`backend/src/nettriage/adapters/invitations.py`:
```python
"""Invitations in Postgres (spec §5.2, §5.7, §6.3): one-time links, stored as a SHA-256 hash,
valid for 7 days and only for the invited email address. "Pending" means neither accepted nor
revoked, and not expired."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid7

from sqlalchemy import Engine, Row, text

from nettriage.adapters.organizations import (
    Organization,
    count_members,
    count_user_orgs,
    lock_org,
    lock_user,
    read_org,
    set_org,
)
from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.organizations import (
    INVITATION_LIFETIME,
    MAX_MEMBERS_PER_ORG,
    MAX_ORGS_PER_USER,
    MAX_PENDING_INVITATIONS,
    Conflict,
    Forbidden,
    InvitationInvalid,
    QuotaExceeded,
    WrongEmail,
    new_invitation_token,
    token_hash,
)
from nettriage.application.permissions import Role, can_manage

INVALID = "This invitation isn't valid anymore. Ask for a new one."


@dataclass(frozen=True)
class Invitation:
    id: UUID
    email: str
    role: Role
    expires_at: datetime
    created_at: datetime
    created_by: UUID


def list_invitations(
    engine: Engine, org_id: UUID, user_id: UUID, now: datetime
) -> list[Invitation]:
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        rows = connection.execute(
            text(
                "SELECT id, email, role, expires_at, created_at, created_by FROM invitations "
                "WHERE org_id = :org AND accepted_at IS NULL AND revoked_at IS NULL "
                "AND expires_at > :now ORDER BY created_at, id"
            ),
            {"org": org_id, "now": now},
        ).all()
    return [_invitation(row) for row in rows]


def create_invitation(
    engine: Engine,
    org_id: UUID,
    *,
    actor_id: UUID,
    actor_role: Role,
    email: str,
    role: Role,
    now: datetime,
) -> tuple[Invitation, str]:
    """A new invitation, and its token, which is shown once and never stored. An expired
    invitation for the same address is revoked first (Plan 3a, Decision 6)."""
    if not can_manage(actor_role, role):
        raise Forbidden("Your role can't invite someone with that role.")
    token = new_invitation_token()
    with tenant_transaction(engine, org_id=org_id, user_id=actor_id) as connection:
        lock_org(connection, org_id)
        member = connection.execute(
            text(
                "SELECT 1 FROM memberships m JOIN users u ON u.id = m.user_id "
                "WHERE m.org_id = :org AND lower(u.email) = lower(:email)"
            ),
            {"org": org_id, "email": email},
        ).first()
        if member is not None:
            raise Conflict("That person is already a member.")
        earlier = connection.execute(
            text(
                "SELECT id, expires_at FROM invitations WHERE org_id = :org "
                "AND lower(email) = lower(:email) AND accepted_at IS NULL AND revoked_at IS NULL"
            ),
            {"org": org_id, "email": email},
        ).one_or_none()
        if earlier is not None and earlier.expires_at > now:
            raise Conflict("That address already has a pending invitation.")
        if earlier is not None:
            connection.execute(
                text("UPDATE invitations SET revoked_at = :now WHERE id = :id"),
                {"now": now, "id": earlier.id},
            )
        pending: int = connection.execute(
            text(
                "SELECT count(*) FROM invitations WHERE org_id = :org "
                "AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > :now"
            ),
            {"org": org_id, "now": now},
        ).scalar_one()
        if pending >= MAX_PENDING_INVITATIONS:
            raise QuotaExceeded(
                f"An org can have at most {MAX_PENDING_INVITATIONS} pending invitations."
            )
        if count_members(connection, org_id) + pending >= MAX_MEMBERS_PER_ORG:
            raise QuotaExceeded(
                f"An org can have at most {MAX_MEMBERS_PER_ORG} members, "
                "counting pending invitations."
            )
        row = connection.execute(
            text(
                "INSERT INTO invitations "
                "(id, org_id, email, role, token_hash, expires_at, created_by) "
                "VALUES (:id, :org, :email, :role, :hash, :expires, :actor) "
                "RETURNING id, email, role, expires_at, created_at, created_by"
            ),
            {
                "id": uuid7(),
                "org": org_id,
                "email": email,
                "role": role,
                "hash": token_hash(token),
                "expires": now + INVITATION_LIFETIME,
                "actor": actor_id,
            },
        ).one()
    return _invitation(row), token


def revoke_invitation(
    engine: Engine, org_id: UUID, *, actor_id: UUID, invitation_id: UUID, now: datetime
) -> bool:
    """Revoke a pending invitation. False if there's no pending invitation with that ID."""
    with tenant_transaction(engine, org_id=org_id, user_id=actor_id) as connection:
        revoked = connection.execute(
            text(
                "UPDATE invitations SET revoked_at = :now WHERE org_id = :org AND id = :id "
                "AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > :now"
            ),
            {"now": now, "org": org_id, "id": invitation_id},
        ).rowcount
    return revoked == 1


def accept_invitation(engine: Engine, *, user_id: UUID, token: str, now: datetime) -> Organization:
    """Join the org the invitation is for, with its role (spec §6.3). The signed-in user's
    verified email must match the invitation's, ignoring case.

    The transaction starts user-only: row-level security then shows the user's own memberships
    (for the 3-orgs quota) and, through `app.invitation_token_hash`, only the invitation this
    token is for. It then switches to the invitation's org to join it."""
    with tenant_transaction(engine, user_id=user_id) as connection:
        connection.execute(
            text("SELECT set_config('app.invitation_token_hash', :hash, true)"),
            {"hash": token_hash(token)},
        )
        lock_user(connection, user_id)
        invitation = connection.execute(
            text(
                "SELECT id, org_id, email, role, created_by FROM invitations "
                "WHERE token_hash = app_invitation_token_hash() "
                "AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > :now"
            ),
            {"now": now},
        ).one_or_none()
        if invitation is None:
            raise InvitationInvalid(INVALID)
        email: str = connection.execute(
            text("SELECT email FROM users WHERE id = :user"), {"user": user_id}
        ).scalar_one()
        if email.lower() != invitation.email.lower():
            raise WrongEmail("This invitation is for a different email address.")
        if count_user_orgs(connection, user_id) >= MAX_ORGS_PER_USER:
            raise QuotaExceeded(f"You can belong to at most {MAX_ORGS_PER_USER} orgs.")
        set_org(connection, invitation.org_id)
        lock_org(connection, invitation.org_id)
        already = connection.execute(
            text("SELECT 1 FROM memberships WHERE org_id = :org AND user_id = :user"),
            {"org": invitation.org_id, "user": user_id},
        ).first()
        if already is not None:
            raise Conflict("You're already a member of this organization.")
        if count_members(connection, invitation.org_id) >= MAX_MEMBERS_PER_ORG:
            raise QuotaExceeded(f"This org already has {MAX_MEMBERS_PER_ORG} members.")
        accepted = connection.execute(
            text(
                "UPDATE invitations SET accepted_at = :now, accepted_by = :user "
                "WHERE id = :id AND accepted_at IS NULL AND revoked_at IS NULL"
            ),
            {"now": now, "user": user_id, "id": invitation.id},
        ).rowcount
        if accepted != 1:
            raise InvitationInvalid(INVALID)
        connection.execute(
            text(
                "INSERT INTO memberships (org_id, user_id, role, invited_by) "
                "VALUES (:org, :user, :role, :by)"
            ),
            {
                "org": invitation.org_id,
                "user": user_id,
                "role": invitation.role,
                "by": invitation.created_by,
            },
        )
        return read_org(connection, invitation.org_id, user_id)


def _invitation(row: Row[Any]) -> Invitation:
    return Invitation(
        id=row.id,
        email=row.email,
        role=row.role,
        expires_at=row.expires_at,
        created_at=row.created_at,
        created_by=row.created_by,
    )
```

In `backend/src/nettriage/adapters/audit_log.py`, replace:
```python

import json
from uuid import uuid7

from sqlalchemy import Engine, text
```
with:
```python

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import Engine, text
```

In `backend/src/nettriage/adapters/audit_log.py`, replace:
```python
        )

```
with:
```python
        )


@dataclass(frozen=True)
class AuditEntry:
    id: UUID
    created_at: datetime
    actor_user_id: UUID | None
    actor_type: str
    action: str
    target_type: str | None
    target_id: str | None
    outcome: str
    details: dict[str, object]


def list_org_events(
    engine: Engine,
    org_id: UUID,
    user_id: UUID,
    *,
    limit: int,
    before: tuple[datetime, UUID] | None = None,
) -> list[AuditEntry]:
    """The org's events, newest first; `before` continues after the last entry of a page.
    IPs and user agents stay out: they're for investigations, not the org's view."""
    before_at, before_id = before or (None, None)
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        rows = connection.execute(
            text(
                "SELECT id, created_at, actor_user_id, actor_type, action, target_type, "
                "target_id, outcome, details FROM audit_log WHERE org_id = :org "
                "AND (CAST(:before_at AS timestamptz) IS NULL OR (created_at, id) < "
                "(CAST(:before_at AS timestamptz), CAST(:before_id AS uuid))) "
                "ORDER BY created_at DESC, id DESC LIMIT :limit"
            ),
            {"org": org_id, "before_at": before_at, "before_id": before_id, "limit": limit},
        ).all()
    return [
        AuditEntry(
            id=row.id,
            created_at=row.created_at,
            actor_user_id=row.actor_user_id,
            actor_type=row.actor_type,
            action=row.action,
            target_type=row.target_type,
            target_id=row.target_id,
            outcome=row.outcome,
            details=row.details,
        )
        for row in rows
    ]

```

- [ ] **Step 6: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `409 passed, 1 skipped`.

The threaded tests (`test_three_parallel_creations_by_one_user_still_stop_at_three` and `test_two_owners_demoting_each_other_at_once_leave_one_owner`) run their transactions on separate `app_api` connections at the same moment. Run them five times to be sure they aren't flaky:
```bash
cd backend && for i in 1 2 3 4 5; do NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/integration/test_organizations.py -k "parallel or at_once" | tail -1; done
```
Expected: `2 passed, 21 deselected` five times.

- [ ] **Step 7: Commit**

```bash
git add backend/migrations backend/src backend/tests
git commit -m "feat(orgs): organizations, members and invitations in Postgres, safe under concurrency" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Org access, idempotency keys and the request-body limit

**Files:**
- Create: `backend/src/nettriage/adapters/idempotency.py`, `backend/src/nettriage/platform/body_limit.py`
- Modify: `backend/src/nettriage/entrypoints/api/access.py`, `backend/src/nettriage/entrypoints/api/auditing.py`, `backend/src/nettriage/entrypoints/api/app.py`, `backend/src/nettriage/entrypoints/api/services.py`, `backend/src/nettriage/entrypoints/api/wiring.py`, `backend/src/nettriage/adapters/rate_limiter.py`, `backend/src/nettriage/platform/metrics.py`
- Modify (test harness): `backend/tests/conftest.py`, `backend/tests/browser.py`
- Test: `backend/tests/unit/adapters/test_idempotency_store.py`, `backend/tests/unit/platform/test_body_limit.py`, `backend/tests/security/test_org_access.py`, `backend/tests/unit/adapters/test_rate_limiter.py`

**Interfaces:**
- Consumes:
  - Task 1: `enforce` and `Public`.
  - Task 2: `PERMISSIONS`, `allows` and `Role`.
  - Task 3: `role_of`.
  - Plan 3b: `Services`, `RuntimeTable`, `FakeClock`, `counter`, `SessionStore`, `epoch_seconds` and `is_condition_failure`.
- Produces:
  - In `nettriage.adapters.idempotency`:
    - `IDEMPOTENCY_TTL` (24 h), `IN_PROGRESS_TTL` (1 min) and `StoredResponse(status: int, body: dict[str, Any])`;
    - the errors `IdempotencyMismatch` and `IdempotencyInProgress`;
    - `IdempotencyStore(client, table)`, with `.begin(user_id, key, request_hash, now) -> StoredResponse | None`, `.finish(user_id, key, response, now)` and `.abandon(user_id, key)`.
  - `Services.idempotency: IdempotencyStore`, wired in production and in the `services` fixture.
  - In `nettriage.platform.body_limit`: `MAX_BODY_BYTES = 64 * 1024`, `BodyTooLarge` (a 413 `HTTPException`) and the ASGI middleware `BodySizeLimit(app, max_bytes=MAX_BODY_BYTES)`, registered innermost in `create_app`.
  - In `nettriage.entrypoints.api.access`:
    - `OrgContext(session, org_id, role)`, with `.user_id`;
    - `OrgMember(permission: str, *, or_self: bool = False)`, a route dependency that returns `OrgContext`, answers 404 to a non-member and 403 to a member whose role lacks the permission, and raises `ValueError` at import time for a permission not in the table;
    - `deny(request, actor, permission, *, org_id, target, reason)`, `not_found()` and `forbidden(detail=…)`.
  - `audit(…)` takes `org_id`, `target_type` and `target_id`.
  - `AppMetrics.authz_denied`, the `nettriage.authz.denied` counter, by permission.
  - `RateLimiter.should_audit(event: str, subject: str) -> bool`, which now takes an event name instead of a `Policy`, so `authz.denied` can be sampled too.
  - In `backend/tests/browser.py`: `signed_in_as(client, sessions, user_id, now) -> dict[str, str]`, which gives the client a session without the Cognito round trip and returns the SPA's CSRF headers.

- [ ] **Step 1: Write the failing idempotency tests**

`backend/tests/unit/adapters/test_idempotency_store.py`:
```python
from datetime import timedelta
from uuid import uuid4

import pytest
from conftest import FakeClock, RuntimeTable

from nettriage.adapters.idempotency import (
    IdempotencyInProgress,
    IdempotencyMismatch,
    IdempotencyStore,
    StoredResponse,
)

DONE = StoredResponse(status=201, body={"id": "org-1", "name": "Acme"})


def test_a_new_key_lets_the_request_run(runtime_table: RuntimeTable, clock: FakeClock) -> None:
    store = IdempotencyStore(runtime_table.client, runtime_table.name)

    assert store.begin(uuid4(), "key-1", "hash-a", clock()) is None


def test_a_finished_request_is_answered_again_from_the_store(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    store = IdempotencyStore(runtime_table.client, runtime_table.name)
    user = uuid4()
    store.begin(user, "key-1", "hash-a", clock())
    store.finish(user, "key-1", DONE, clock())

    assert store.begin(user, "key-1", "hash-a", clock()) == DONE


def test_the_same_key_with_another_request_is_refused(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    store = IdempotencyStore(runtime_table.client, runtime_table.name)
    user = uuid4()
    store.begin(user, "key-1", "hash-a", clock())
    store.finish(user, "key-1", DONE, clock())

    with pytest.raises(IdempotencyMismatch):
        store.begin(user, "key-1", "hash-b", clock())


def test_a_retry_while_the_first_request_runs_is_refused(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    store = IdempotencyStore(runtime_table.client, runtime_table.name)
    user = uuid4()
    store.begin(user, "key-1", "hash-a", clock())

    with pytest.raises(IdempotencyInProgress):
        store.begin(user, "key-1", "hash-a", clock())


def test_keys_belong_to_one_user_abandoned_ones_can_be_retried_and_they_last_a_day(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    store = IdempotencyStore(runtime_table.client, runtime_table.name)
    user = uuid4()
    store.begin(user, "key-1", "hash-a", clock())

    assert store.begin(uuid4(), "key-1", "hash-a", clock()) is None  # another user's key
    store.abandon(user, "key-1")
    assert store.begin(user, "key-1", "hash-b", clock()) is None
    store.finish(user, "key-1", DONE, clock())
    clock.advance(timedelta(hours=24))
    assert store.begin(user, "key-1", "hash-c", clock()) is None


def test_a_key_left_unfinished_frees_itself_after_a_minute(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    """If the work succeeded but storing its response failed, a retry mustn't be refused as
    "still running" for a whole day."""
    store = IdempotencyStore(runtime_table.client, runtime_table.name)
    user = uuid4()
    store.begin(user, "key-1", "hash-a", clock())
    clock.advance(timedelta(minutes=1))

    assert store.begin(user, "key-1", "hash-a", clock()) is None
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && uv run python -m pytest tests/unit/adapters/test_idempotency_store.py`
Expected: FAIL. Collection stops on `ModuleNotFoundError: No module named 'nettriage.adapters.idempotency'`.

- [ ] **Step 3: Write the idempotency store and wire it**

`backend/src/nettriage/adapters/idempotency.py`:
```python
"""Idempotency keys in the DynamoDB `runtime` table (spec §5.5, §7): `IDEMP#<user>#<key>`
remembers a request's hash and, once it's done, its response, for 24 hours. A retry with the
same key and request gets the same response instead of doing the work twice."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from botocore.exceptions import ClientError

from nettriage.adapters.runtime_table import epoch_seconds, is_condition_failure

if TYPE_CHECKING:
    from types_boto3_dynamodb.client import DynamoDBClient

IDEMPOTENCY_TTL = timedelta(hours=24)
# A request still running holds its key only this long, well past the API's 29-second
# timeout: if it succeeded but storing its response failed, a retry isn't refused for a day.
IN_PROGRESS_TTL = timedelta(minutes=1)


@dataclass(frozen=True)
class StoredResponse:
    status: int
    body: dict[str, Any]


class IdempotencyMismatch(Exception):
    """The key was used before with a different request."""


class IdempotencyInProgress(Exception):
    """The first request with this key hasn't finished yet."""


class IdempotencyStore:
    def __init__(self, client: DynamoDBClient, table: str) -> None:
        self._client = client
        self._table = table

    def begin(
        self, user_id: UUID, key: str, request_hash: str, now: datetime
    ) -> StoredResponse | None:
        """None if this request should run now; the stored response if it already ran."""
        try:
            self._client.put_item(
                TableName=self._table,
                Item={
                    "pk": {"S": _pk(user_id, key)},
                    "request_hash": {"S": request_hash},
                    "state": {"S": "in_progress"},
                    "expires_at": {"N": str(epoch_seconds(now + IN_PROGRESS_TTL))},
                },
                ConditionExpression="attribute_not_exists(pk) OR expires_at <= :now",
                ExpressionAttributeValues={":now": {"N": str(epoch_seconds(now))}},
                ReturnValuesOnConditionCheckFailure="ALL_OLD",
            )
        except ClientError as error:
            if not is_condition_failure(error):
                raise
            item: dict[str, Any] = error.response.get("Item") or self._client.get_item(  # type: ignore[assignment]
                TableName=self._table, Key={"pk": {"S": _pk(user_id, key)}}, ConsistentRead=True
            ).get("Item", {})
            if item.get("request_hash", {}).get("S") != request_hash:
                raise IdempotencyMismatch from None
            if item.get("state", {}).get("S") != "done":
                raise IdempotencyInProgress from None
            return StoredResponse(
                status=int(item["status"]["N"]), body=json.loads(item["response"]["S"])
            )
        return None

    def finish(self, user_id: UUID, key: str, response: StoredResponse, now: datetime) -> None:
        """Store the response for retries, for 24 hours."""
        self._client.update_item(
            TableName=self._table,
            Key={"pk": {"S": _pk(user_id, key)}},
            UpdateExpression=(
                "SET #state = :done, #status = :status, #response = :response, "
                "expires_at = :expires"
            ),
            ExpressionAttributeNames={
                "#state": "state",
                "#status": "status",
                "#response": "response",
            },
            ExpressionAttributeValues={
                ":done": {"S": "done"},
                ":status": {"N": str(response.status)},
                ":response": {"S": json.dumps(response.body)},
                ":expires": {"N": str(epoch_seconds(now + IDEMPOTENCY_TTL))},
            },
        )

    def abandon(self, user_id: UUID, key: str) -> None:
        """Forget a request that failed, so the client can retry it with the same key."""
        self._client.delete_item(TableName=self._table, Key={"pk": {"S": _pk(user_id, key)}})


def _pk(user_id: UUID, key: str) -> str:
    return f"IDEMP#{user_id}#{key}"
```

In `backend/src/nettriage/entrypoints/api/services.py`, replace:
```python

from nettriage.adapters.login_states import LoginStateStore
```
with:
```python

from nettriage.adapters.idempotency import IdempotencyStore
from nettriage.adapters.login_states import LoginStateStore
```

In `backend/src/nettriage/entrypoints/api/services.py`, replace:
```python
    rate_limiter: RateLimiter
    oidc: OidcClient
```
with:
```python
    rate_limiter: RateLimiter
    idempotency: IdempotencyStore
    oidc: OidcClient
```

In `backend/src/nettriage/entrypoints/api/wiring.py`, replace:
```python

from nettriage.adapters.login_states import LoginStateStore
```
with:
```python

from nettriage.adapters.idempotency import IdempotencyStore
from nettriage.adapters.login_states import LoginStateStore
```

In `backend/src/nettriage/entrypoints/api/wiring.py`, replace:
```python
        rate_limiter=RateLimiter(dynamodb, table, system_clock),
        oidc=OidcClient(
```
with:
```python
        rate_limiter=RateLimiter(dynamodb, table, system_clock),
        idempotency=IdempotencyStore(dynamodb, table),
        oidc=OidcClient(
```

In `backend/tests/conftest.py`, replace:
```python

from nettriage.adapters.login_states import LoginStateStore
```
with:
```python

from nettriage.adapters.idempotency import IdempotencyStore
from nettriage.adapters.login_states import LoginStateStore
```

In `backend/tests/conftest.py`, replace:
```python
        rate_limiter=RateLimiter(client, table, clock),
        oidc=OidcClient(idp_settings(), httpx.Client(transport=idp.transport()), clock),
```
with:
```python
        rate_limiter=RateLimiter(client, table, clock),
        idempotency=IdempotencyStore(client, table),
        oidc=OidcClient(idp_settings(), httpx.Client(transport=idp.transport()), clock),
```

Run: `cd backend && uv run python -m pytest tests/unit/adapters/test_idempotency_store.py`
Expected: `6 passed`.

- [ ] **Step 4: Write the failing body-limit tests**

`backend/tests/unit/platform/test_body_limit.py`:
```python
"""Request bodies are at most 64 KB (spec §6.7)."""

from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from nettriage.entrypoints.api.app import create_app
from nettriage.entrypoints.api.services import Services
from nettriage.platform.body_limit import MAX_BODY_BYTES
from nettriage.platform.config import Settings


def app_with_a_body_route(settings: Settings, services: Services) -> TestClient:
    app: FastAPI = create_app(settings, services)

    @app.post("/api/test-body")
    def take(payload: dict[str, Any]) -> dict[str, int]:
        return {"keys": len(payload)}

    return TestClient(app, base_url="https://app.test")


def json_of(size: int) -> bytes:
    """A JSON object exactly `size` bytes long."""
    return b'{"k":"' + b"x" * (size - 8) + b'"}'


def test_a_body_at_the_limit_is_accepted(settings: Settings, services: Services) -> None:
    client = app_with_a_body_route(settings, services)

    response = client.post(
        "/api/test-body",
        content=json_of(MAX_BODY_BYTES),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200


def test_a_declared_body_over_the_limit_is_refused_before_anything_runs(
    settings: Settings, services: Services
) -> None:
    client = app_with_a_body_route(settings, services)

    response = client.post(
        "/api/auth/logout",
        content=json_of(MAX_BODY_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["detail"] == "Request bodies are limited to 64 KB."


def test_a_body_sent_in_pieces_without_a_length_is_counted(
    settings: Settings, services: Services
) -> None:
    client = app_with_a_body_route(settings, services)

    def pieces() -> Iterator[bytes]:
        body = json_of(MAX_BODY_BYTES * 2)
        for start in range(0, len(body), 8192):
            yield body[start : start + 8192]

    response = client.post(
        "/api/test-body", content=pieces(), headers={"Content-Type": "application/json"}
    )

    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
```

Run: `cd backend && uv run python -m pytest tests/unit/platform/test_body_limit.py`
Expected: FAIL. Collection stops on `ModuleNotFoundError: No module named 'nettriage.platform.body_limit'`.

- [ ] **Step 5: Write the body limit**

`backend/src/nettriage/platform/body_limit.py`:
```python
"""Request bodies are at most 64 KB (spec §6.7): the API takes small JSON, and files go
straight to S3. A declared Content-Length over the limit is refused before anything else runs;
a body that arrives without one is counted as it's read."""

from __future__ import annotations

from http import HTTPStatus

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nettriage.platform.errors import problem_response

MAX_BODY_BYTES = 64 * 1024
DETAIL = f"Request bodies are limited to {MAX_BODY_BYTES // 1024} KB."


class BodyTooLarge(HTTPException):
    """Raised while a route reads its body. It's an HTTPException, so FastAPI passes it on to
    the Problem Details handler instead of turning it into "error parsing the body"."""

    def __init__(self) -> None:
        super().__init__(HTTPStatus.CONTENT_TOO_LARGE, detail=DETAIL)


class BodySizeLimit:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self.max_bytes):
            response = problem_response(Request(scope), HTTPStatus.CONTENT_TOO_LARGE, DETAIL)
            await response(scope, receive, send)
            return
        received = 0

        async def counted() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLarge
            return message

        await self.app(scope, counted, send)
```

In `backend/src/nettriage/entrypoints/api/app.py`, replace:
```python
from nettriage.entrypoints.api.services import Services
from nettriage.platform.config import Settings
```
with:
```python
from nettriage.entrypoints.api.services import Services
from nettriage.platform.body_limit import BodySizeLimit
from nettriage.platform.config import Settings
```

In `backend/src/nettriage/entrypoints/api/app.py`, replace:
```python
    app.add_exception_handler(RedirectInstead, redirect_instead)
    app.middleware("http")(add_rate_limit_headers)
```
with:
```python
    app.add_exception_handler(RedirectInstead, redirect_instead)
    # Innermost, so a body counted over the limit reaches FastAPI as the HTTPException it is;
    # the header middleware wraps the request stream in a task group, which would regroup it.
    app.add_middleware(BodySizeLimit)
    app.middleware("http")(add_rate_limit_headers)
```

Run: `cd backend && uv run python -m pytest tests/unit/platform/test_body_limit.py`
Expected: `3 passed`.

- [ ] **Step 6: Write the failing org access tests**

In `backend/tests/browser.py`, replace:
```python
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx2
from fake_idp import APP_ORIGIN, FakeIdentityProvider
from fastapi.testclient import TestClient

VIEWER = {"CloudFront-Viewer-Address": "203.0.113.7:4444"}
```
with:
```python
from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx2
from fake_idp import APP_ORIGIN, FakeIdentityProvider
from fastapi.testclient import TestClient

from nettriage.adapters.sessions import SessionStore
from nettriage.application.sessions import COOKIE_NAME

VIEWER = {"CloudFront-Viewer-Address": "203.0.113.7:4444"}
```

In `backend/tests/browser.py`, replace:
```python
    return None

```
with:
```python
    return None


def signed_in_as(
    client: TestClient, sessions: SessionStore, user_id: UUID, now: datetime
) -> dict[str, str]:
    """Give the client a session for the user, without the Cognito round trip. Returns the
    headers the SPA sends on state-changing requests."""
    session_id, session = sessions.create(user_id=user_id, now=now, ip=None, user_agent=None)
    client.cookies.set(COOKIE_NAME, session_id, domain="app.test")
    return {
        "X-CSRF-Token": session.csrf_token,
        "Sec-Fetch-Site": "same-origin",
        "Origin": APP_ORIGIN,
    }

```

In `backend/tests/unit/adapters/test_rate_limiter.py`, replace:
```python

    first = limits.should_audit(POLICY, "user-1")
    again = limits.should_audit(POLICY, "user-1")
    someone_else = limits.should_audit(POLICY, "user-2")
    clock.advance(timedelta(minutes=1))
    later = limits.should_audit(POLICY, "user-1")

```
with:
```python

    first = limits.should_audit(POLICY.name, "user-1")
    again = limits.should_audit(POLICY.name, "user-1")
    someone_else = limits.should_audit(POLICY.name, "user-2")
    clock.advance(timedelta(minutes=1))
    later = limits.should_audit(POLICY.name, "user-1")

```

In `backend/tests/unit/adapters/test_rate_limiter.py`, replace:
```python

    sampled = [limits.should_audit(POLICY, "user-1") for _ in range(20)]

```
with:
```python

    sampled = [limits.should_audit(POLICY.name, "user-1") for _ in range(20)]

```

`backend/tests/security/test_org_access.py`:
```python
"""Org-scoped access (spec §6.4): membership plus permission, 404 for outsiders, 403 for
members without the permission, and every denial recorded."""

from datetime import timedelta
from typing import Annotated
from uuid import UUID, uuid4

import httpx2
import pytest
from browser import signed_in_as
from conftest import Database, FakeClock, counter
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from sqlalchemy import text
from tenantdata import add_member, add_org, add_user

from nettriage.entrypoints.api.access import OrgContext, OrgMember
from nettriage.entrypoints.api.services import Services

ROLES = ("owner", "admin", "analyst", "viewer")


@pytest.fixture
def probe(database_client: TestClient) -> TestClient:
    """The API with two probe routes: one needing org:update, one members:remove or self."""
    app: FastAPI = database_client.app  # type: ignore[assignment]

    @app.get("/api/v1/orgs/{org_id}/probe")
    def update_probe(
        org: Annotated[OrgContext, Depends(OrgMember("org:update"))],
    ) -> dict[str, str]:
        return {"role": org.role}

    @app.delete("/api/v1/orgs/{org_id}/probe/{user_id}", status_code=204)
    def remove_probe(
        org: Annotated[OrgContext, Depends(OrgMember("members:remove", or_self=True))],
    ) -> None:
        return None

    return database_client


@pytest.fixture
def org(database: Database) -> tuple[UUID, dict[str, UUID]]:
    with database.admin.begin() as connection:
        people = {role: add_user(connection) for role in (*ROLES, "stranger")}
        org_id = add_org(connection, people["owner"])
        for role in ROLES:
            add_member(connection, org_id, people[role], role)
    return org_id, people


@pytest.mark.parametrize(
    ("who", "status"),
    [("owner", 200), ("admin", 200), ("analyst", 403), ("viewer", 403), ("stranger", 404)],
)
def test_a_route_needs_a_membership_whose_role_has_the_permission(
    probe: TestClient,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, dict[str, UUID]],
    who: str,
    status: int,
) -> None:
    org_id, people = org
    signed_in_as(probe, services.sessions, people[who], clock())

    response = probe.get(f"/api/v1/orgs/{org_id}/probe")

    assert response.status_code == status
    if status != 200:
        assert response.headers["content-type"] == "application/problem+json"


def test_an_org_id_that_isnt_a_uuid_is_simply_not_found(
    probe: TestClient, services: Services, clock: FakeClock, org: tuple[UUID, dict[str, UUID]]
) -> None:
    signed_in_as(probe, services.sessions, org[1]["owner"], clock())

    assert probe.get("/api/v1/orgs/not-an-id/probe").status_code == 404


def test_an_outsider_gets_the_same_answer_as_for_an_org_that_does_not_exist(
    probe: TestClient, services: Services, clock: FakeClock, org: tuple[UUID, dict[str, UUID]]
) -> None:
    org_id, people = org
    signed_in_as(probe, services.sessions, people["stranger"], clock())

    existing = probe.get(f"/api/v1/orgs/{org_id}/probe")
    missing = probe.get(f"/api/v1/orgs/{uuid4()}/probe")

    def answer(response: httpx2.Response) -> tuple[int, object, object, object]:
        body = response.json()
        return response.status_code, body["type"], body["title"], body.get("detail")

    assert answer(existing) == answer(missing)
    assert existing.status_code == 404


def test_without_a_session_it_is_401(probe: TestClient, org: tuple[UUID, dict[str, UUID]]) -> None:
    assert probe.get(f"/api/v1/orgs/{org[0]}/probe").status_code == 401


def test_denials_are_counted_and_audited_without_writing_into_an_outsiders_target(
    probe: TestClient,
    services: Services,
    clock: FakeClock,
    database: Database,
    metric_reader: InMemoryMetricReader,
    org: tuple[UUID, dict[str, UUID]],
) -> None:
    org_id, people = org
    for who in ("viewer", "stranger"):
        signed_in_as(probe, services.sessions, people[who], clock())
        probe.get(f"/api/v1/orgs/{org_id}/probe")

    assert counter(metric_reader, "nettriage.authz.denied") == 2
    with database.admin.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT actor_user_id, org_id, target_id, details FROM audit_log "
                "WHERE action = 'authz.denied' AND target_id = :org ORDER BY created_at"
            ),
            {"org": str(org_id)},
        ).all()
    assert [(row.actor_user_id, row.org_id) for row in rows] == [
        (people["viewer"], org_id),
        (people["stranger"], None),
    ]
    assert rows[0].details == {
        "permission": "org:update",
        "route": "/api/v1/orgs/{org_id}/probe",
        "reason": "role",
    }
    assert rows[1].details["reason"] == "not_a_member"


def test_a_member_may_act_on_themselves_where_a_route_allows_it(
    probe: TestClient,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, dict[str, UUID]],
) -> None:
    org_id, people = org
    headers = signed_in_as(probe, services.sessions, people["viewer"], clock())

    own = probe.delete(f"/api/v1/orgs/{org_id}/probe/{people['viewer']}", headers=headers)
    other = probe.delete(f"/api/v1/orgs/{org_id}/probe/{people['analyst']}", headers=headers)
    anyone = probe.delete(f"/api/v1/orgs/{org_id}/probe/{uuid4()}", headers=headers)

    assert (own.status_code, other.status_code, anyone.status_code) == (204, 403, 403)


def test_repeated_denials_by_one_caller_write_one_audit_row_a_minute(
    probe: TestClient,
    services: Services,
    clock: FakeClock,
    database: Database,
    metric_reader: InMemoryMetricReader,
    org: tuple[UUID, dict[str, UUID]],
) -> None:
    """Anyone can sign up, so an audit row for every denial would let one account fill the
    database; the metric still counts each one."""
    org_id, people = org
    signed_in_as(probe, services.sessions, people["stranger"], clock())
    for _ in range(10):
        probe.get(f"/api/v1/orgs/{uuid4()}/probe")
    clock.advance(timedelta(minutes=1))
    probe.get(f"/api/v1/orgs/{org_id}/probe")

    assert counter(metric_reader, "nettriage.authz.denied") == 11
    with database.admin.begin() as connection:
        rows: int = connection.execute(
            text(
                "SELECT count(*) FROM audit_log "
                "WHERE action = 'authz.denied' AND actor_user_id = :actor"
            ),
            {"actor": people["stranger"]},
        ).scalar_one()
    assert rows == 2
```

Run: `just test`
Expected: FAIL. Collection stops on `ImportError: cannot import name 'OrgContext' from 'nettriage.entrypoints.api.access'`.

Run: `cd backend && uv run python -m pytest tests/unit/adapters/test_rate_limiter.py`
Expected: `2 failed, 7 passed`. Both `should_audit` tests fail with `AttributeError: 'str' object has no attribute 'name'`: they now pass the event name, where the limiter still expects a `Policy`.

- [ ] **Step 7: Write `OrgMember`, the denials and their metric**

In `backend/src/nettriage/platform/metrics.py`, replace:
```python
        )
        self.rate_limit_errors = meter.create_counter(
```
with:
```python
        )
        self.authz_denied = meter.create_counter(
            "nettriage.authz.denied", description="Requests refused by authorization, by permission"
        )
        self.rate_limit_errors = meter.create_counter(
```

In `backend/src/nettriage/adapters/rate_limiter.py`, replace:
```python

    def should_audit(self, policy: Policy, subject: str) -> bool:
        """True at most once per subject and policy per minute: the audit log samples
        `ratelimit.limited` (spec §9.4)."""
        now = self._clock()
        name = f"RLAUDIT#{policy.name}#{subject}"
        if epoch_millis(now) < self._audited_until.get(name, 0):
```
with:
```python

    def should_audit(self, event: str, subject: str) -> bool:
        """True at most once per subject and event kind per minute: the audit log samples
        `ratelimit.limited` (spec §9.4) and `authz.denied`, which anyone can cause."""
        now = self._clock()
        name = f"RLAUDIT#{event}#{subject}"
        if epoch_millis(now) < self._audited_until.get(name, 0):
```

In `backend/src/nettriage/adapters/rate_limiter.py`, replace:
```python
                return False
            logger.warning("rate_limit_audit_sample_failed", extra={"policy": policy.name})
            return False
        except BotoCoreError:
            logger.warning("rate_limit_audit_sample_failed", extra={"policy": policy.name})
            return False
```
with:
```python
                return False
            logger.warning("audit_sample_failed", extra={"event": event})
            return False
        except BotoCoreError:
            logger.warning("audit_sample_failed", extra={"event": event})
            return False
```

In `backend/src/nettriage/entrypoints/api/auditing.py`, replace:
```python
    actor_user_id: UUID | None = None,
    details: Mapping[str, object] | None = None,
```
with:
```python
    actor_user_id: UUID | None = None,
    org_id: UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    details: Mapping[str, object] | None = None,
```

In `backend/src/nettriage/entrypoints/api/auditing.py`, replace:
```python
        actor_user_id=actor_user_id,
        ip=viewer_ip(request.headers.get(VIEWER_ADDRESS)),
```
with:
```python
        actor_user_id=actor_user_id,
        org_id=org_id,
        target_type=target_type,
        target_id=target_id,
        ip=viewer_ip(request.headers.get(VIEWER_ADDRESS)),
```

In `backend/src/nettriage/entrypoints/api/access.py`, replace:
```python
"""Who may call a route (spec §6.2, §6.4, §6.5). Deny by default: every route declares
`Public(policy)` or `SignedIn`, and tests/security/test_route_access.py fails on any route that
declares neither.

```
with:
```python
"""Who may call a route (spec §6.2, §6.4, §6.5). Deny by default: every route declares
`Public(policy)`, `SignedIn` or `OrgMember(permission)`, and tests/security/test_route_access.py
fails on any route that declares none of them.

```

In `backend/src/nettriage/entrypoints/api/access.py`, replace:
```python
  `audit_limits=False` keeps a limited route from writing to the audit log (and so to Postgres).
- `SignedIn` needs a valid session. It fails closed: an unreadable session store gives 503.
```
with:
```python
  `audit_limits=False` keeps a limited route from writing to the audit log (and so to Postgres).
- `OrgMember(permission)` needs a session and a membership in the path's `{org_id}` whose role
  has the permission (spec §6.4). A non-member gets 404, as if the org didn't exist (OWASP API1);
  a member without the permission gets 403. Both are recorded as `authz.denied`.
- `SignedIn` needs a valid session. It fails closed: an unreadable session store gives 503.
```

In `backend/src/nettriage/entrypoints/api/access.py`, replace:
```python
import logging
from typing import Annotated, NoReturn
```
with:
```python
import logging
from dataclasses import dataclass
from typing import Annotated, NoReturn
```

In `backend/src/nettriage/entrypoints/api/access.py`, replace:
```python
from fastapi import Depends, HTTPException, Request

from nettriage.adapters.runtime_table import epoch_millis
from nettriage.application.rate_limits import POLICIES, Policy, ip_subject
```
with:
```python
from fastapi import Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from nettriage.adapters.organizations import role_of
from nettriage.adapters.runtime_table import epoch_millis
from nettriage.application.permissions import PERMISSIONS, Role, allows
from nettriage.application.rate_limits import POLICIES, Policy, ip_subject
```

In `backend/src/nettriage/entrypoints/api/access.py`, replace:
```python

def unauthorized() -> HTTPException:
```
with:
```python

@dataclass(frozen=True)
class OrgContext:
    """The caller inside one org: their session, the org and their role in it."""

    session: Session
    org_id: UUID
    role: Role

    @property
    def user_id(self) -> UUID:
        return self.session.user_id


class OrgMember(Access):
    """A member of the path's `{org_id}` whose role has `permission`. With `or_self`, a member
    may also act on themselves, when the path's `{user_id}` is theirs (leaving an org)."""

    def __init__(self, permission: str, *, or_self: bool = False) -> None:
        if permission not in PERMISSIONS:
            raise ValueError(f"unknown permission {permission!r}")
        self.permission = permission
        self.or_self = or_self

    def __call__(self, request: Request, session: CurrentSession) -> OrgContext:
        org_id = _path_uuid(request, "org_id")
        if org_id is None:
            raise not_found()
        try:
            role = role_of(get_services(request).database, org_id, session.user_id)
        except SQLAlchemyError:
            logger.exception("membership_read_failed")
            raise unavailable("This organization") from None
        if role is None:
            deny(
                request,
                session.user_id,
                self.permission,
                org_id=None,
                target=org_id,
                reason="not_a_member",
            )
            raise not_found()
        is_self = self.or_self and _path_uuid(request, "user_id") == session.user_id
        if allows(role, self.permission) or is_self:
            return OrgContext(session=session, org_id=org_id, role=role)
        deny(request, session.user_id, self.permission, org_id=org_id, target=org_id, reason="role")
        raise forbidden()


def deny(
    request: Request,
    actor: UUID,
    permission: str,
    *,
    org_id: UUID | None,
    target: UUID,
    reason: str,
) -> None:
    """Record a denial (spec §6.4): a metric by permission, and an `authz.denied` audit event,
    sampled at most once a minute per caller and permission: anyone can sign up, so one row per
    denial would let a single account fill the database. A non-member's attempt isn't written
    into that org's log (`org_id` None), so outsiders can't fill it either; the org's ID is kept
    as the target."""
    services = get_services(request)
    services.metrics.authz_denied.add(1, {"permission": permission})
    if not services.rate_limiter.should_audit("authz.denied", f"{actor}:{permission}"):
        return
    route = getattr(request.scope.get("route"), "path", request.url.path)
    audit(
        request,
        action="authz.denied",
        outcome="denied",
        actor_user_id=actor,
        org_id=org_id,
        target_type="organization",
        target_id=str(target),
        details={"permission": permission, "route": route, "reason": reason},
    )


def not_found() -> HTTPException:
    return HTTPException(404)


def forbidden(detail: str = "Your role in this organization doesn't allow that.") -> HTTPException:
    return HTTPException(403, detail=detail)


def _path_uuid(request: Request, name: str) -> UUID | None:
    value = request.path_params.get(name)
    try:
        return UUID(str(value)) if value is not None else None
    except ValueError:
        return None


def unauthorized() -> HTTPException:
```

In `backend/src/nettriage/entrypoints/api/access.py`, replace:
```python
    services.metrics.rate_limited.add(1, {"policy": policy.name})
    if audit_limits and services.rate_limiter.should_audit(policy, subject):
        audit(
```
with:
```python
    services.metrics.rate_limited.add(1, {"policy": policy.name})
    if audit_limits and services.rate_limiter.should_audit(policy.name, subject):
        audit(
```

- [ ] **Step 8: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `429 passed, 1 skipped`.

- [ ] **Step 9: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(orgs): org-scoped access with recorded denials, idempotency keys and the 64 KB body limit" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: The organization, member and invitation routes

**Files:**
- Create: `backend/src/nettriage/entrypoints/api/schemas.py`, `backend/src/nettriage/entrypoints/api/org_errors.py`, `backend/src/nettriage/entrypoints/api/routes/orgs.py`, `backend/src/nettriage/entrypoints/api/routes/members.py`, `backend/src/nettriage/entrypoints/api/routes/invitations.py`
- Modify: `backend/src/nettriage/entrypoints/api/app.py`
- Modify (test harness): `backend/tests/conftest.py` (the `logs` fixture moves here from `test_sign_in_logs.py`, so both log tests share it)
- Test: `backend/tests/api/test_org_routes.py`, `backend/tests/api/test_member_routes.py`, `backend/tests/api/test_invitation_routes.py`, `backend/tests/security/test_authorization_matrix.py`, `backend/tests/security/test_route_access.py`, `backend/tests/security/test_org_logs.py`, `backend/tests/security/test_sign_in_logs.py`

**Interfaces:**
- Consumes:
  - Task 2: `Role`, `invitation_url`, `normalize_email` and `token_hash`.
  - Task 3: every adapter function, `INVALID`, `AuditEntry`, `list_org_events` and the rules' errors.
  - Task 4: `OrgMember`, `OrgContext`, `deny`, `forbidden`, `IdempotencyStore`, `StoredResponse`, the idempotency errors and `signed_in_as`.
  - Task 1: `is_secret` and `enforce`.
  - Plan 3b: `CurrentSession`, `audit`, `unavailable` and `POLICIES["invites.org"]`.
- Produces the routes of §7, all under `/api`:

  | Route | Access | Success |
  |---|---|---|
  | `POST /v1/orgs` | signed in | 201 with `Location`; `Idempotency-Key` optional |
  | `GET /v1/orgs/{org_id}` | `org:read` | 200 |
  | `PATCH /v1/orgs/{org_id}` | `org:update` | 200 |
  | `DELETE /v1/orgs/{org_id}` | `org:delete` | 204; body `{"confirm_name": …}` |
  | `GET /v1/orgs/{org_id}/audit-log` | `audit:read` | 200; `limit` 1–100 (default 50), `cursor` |
  | `GET /v1/orgs/{org_id}/members` | `members:read` | 200 |
  | `PATCH /v1/orgs/{org_id}/members/{user_id}` | `members:role` | 200 |
  | `DELETE /v1/orgs/{org_id}/members/{user_id}` | `members:remove` or self | 204 |
  | `GET /v1/orgs/{org_id}/invitations` | `members:invite` | 200 |
  | `POST /v1/orgs/{org_id}/invitations` | `members:invite`, plus `invites.org` | 201 with `invite_url` |
  | `DELETE /v1/orgs/{org_id}/invitations/{invitation_id}` | `members:invite` | 204 |
  | `POST /v1/invitations/accept` | signed in | 200 with the org |

  Their audit events are `org.created`, `org.renamed`, `org.deleted`, `member.role_changed` (from and to), `member.left` or `member.removed`, `member.invited`, `invitation.revoked` and `member.joined`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/api/test_org_routes.py`:
```python
"""Organizations through the API (spec §7): create, read, rename, delete, audit log."""

from uuid import UUID, uuid4

import pytest
from browser import signed_in_as
from conftest import Database, FakeClock
from fastapi.testclient import TestClient
from sqlalchemy import text
from tenantdata import add_member, add_user

from nettriage.entrypoints.api.services import Services


@pytest.fixture
def owner(database: Database) -> UUID:
    with database.admin.begin() as connection:
        return add_user(connection)


@pytest.fixture
def headers(
    database_client: TestClient, services: Services, clock: FakeClock, owner: UUID
) -> dict[str, str]:
    return signed_in_as(database_client, services.sessions, owner, clock())


def create(
    client: TestClient, headers: dict[str, str], name: str, **extra: str
) -> dict[str, object]:
    response = client.post("/api/v1/orgs", json={"name": name}, headers={**headers, **extra})
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def actions(database: Database, org: object) -> list[str]:
    with database.admin.begin() as connection:
        return list(
            connection.execute(
                text("SELECT action FROM audit_log WHERE org_id = :org ORDER BY created_at, id"),
                {"org": org},
            ).scalars()
        )


def test_creating_an_org_makes_the_caller_its_owner(
    database_client: TestClient, headers: dict[str, str], database: Database
) -> None:
    response = database_client.post(
        "/api/v1/orgs", json={"name": "  Acme Security  "}, headers=headers
    )

    assert response.status_code == 201
    org = response.json()
    assert response.headers["location"] == f"/api/v1/orgs/{org['id']}"
    assert (org["name"], org["role"], org["member_count"]) == ("Acme Security", "owner", 1)
    assert org["slug"].startswith("acme-security")
    assert actions(database, org["id"]) == ["org.created"]
    me = database_client.get("/api/v1/me").json()
    assert [m["org_id"] for m in me["memberships"]] == [org["id"]]


def test_a_retry_with_the_same_idempotency_key_returns_the_same_org(
    database_client: TestClient, headers: dict[str, str]
) -> None:
    key = {"Idempotency-Key": f"create-{uuid4().hex}"}

    first = create(database_client, headers, "Retried", **key)
    again = create(database_client, headers, "Retried", **key)

    assert again == first
    assert len(database_client.get("/api/v1/me").json()["memberships"]) == 1


def test_an_idempotency_key_reused_for_another_org_is_refused(
    database_client: TestClient, headers: dict[str, str]
) -> None:
    key = {"Idempotency-Key": f"create-{uuid4().hex}"}
    create(database_client, headers, "First", **key)

    response = database_client.post(
        "/api/v1/orgs", json={"name": "Second"}, headers={**headers, **key}
    )

    assert response.status_code == 422
    assert "different request" in response.json()["detail"]


def test_a_malformed_idempotency_key_is_refused(
    database_client: TestClient, headers: dict[str, str]
) -> None:
    response = database_client.post(
        "/api/v1/orgs", json={"name": "Org"}, headers={**headers, "Idempotency-Key": "a b"}
    )

    assert response.status_code == 422


def test_a_fourth_org_is_refused(database_client: TestClient, headers: dict[str, str]) -> None:
    for number in range(3):
        create(database_client, headers, f"Mine {number}")

    response = database_client.post("/api/v1/orgs", json={"name": "Fourth"}, headers=headers)

    assert response.status_code == 409
    assert "at most 3" in response.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {"name": ""},
        {"name": "x" * 101},
        {"name": "Org", "slug": "chosen"},
        {"name": "Org", "is_demo": True},
        {"name": "Line\nbreak"},
    ],
)
def test_an_org_body_takes_a_name_and_nothing_else(
    database_client: TestClient, headers: dict[str, str], body: dict[str, object]
) -> None:
    """No mass assignment (OWASP API3): unknown fields are refused, not ignored."""
    response = database_client.post("/api/v1/orgs", json=body, headers=headers)

    assert response.status_code == 422


def test_members_read_the_org_and_managers_rename_it(
    database_client: TestClient,
    headers: dict[str, str],
    services: Services,
    clock: FakeClock,
    database: Database,
) -> None:
    org = create(database_client, headers, "Before")
    with database.admin.begin() as connection:
        viewer = add_user(connection)
        add_member(connection, UUID(str(org["id"])), viewer, "viewer")

    renamed = database_client.patch(
        f"/api/v1/orgs/{org['id']}", json={"name": "After"}, headers=headers
    )
    viewer_headers = signed_in_as(database_client, services.sessions, viewer, clock())
    seen = database_client.get(f"/api/v1/orgs/{org['id']}")
    refused = database_client.patch(
        f"/api/v1/orgs/{org['id']}", json={"name": "Taken over"}, headers=viewer_headers
    )

    assert (renamed.status_code, renamed.json()["slug"]) == (200, org["slug"])
    assert (seen.json()["name"], seen.json()["role"], seen.json()["member_count"]) == (
        "After",
        "viewer",
        2,
    )
    assert refused.status_code == 403
    assert actions(database, org["id"]) == ["org.created", "org.renamed", "authz.denied"]


def test_deleting_an_org_needs_its_exact_name(
    database_client: TestClient, headers: dict[str, str], database: Database
) -> None:
    org = create(database_client, headers, "Doomed Org")

    wrong = database_client.request(
        "DELETE", f"/api/v1/orgs/{org['id']}", json={"confirm_name": "doomed org"}, headers=headers
    )
    deleted = database_client.request(
        "DELETE", f"/api/v1/orgs/{org['id']}", json={"confirm_name": "Doomed Org"}, headers=headers
    )
    after = database_client.get(f"/api/v1/orgs/{org['id']}")

    assert (wrong.status_code, deleted.status_code, after.status_code) == (422, 204, 404)
    assert actions(database, org["id"])[-1] == "org.deleted"


def test_the_audit_log_pages_newest_first(
    database_client: TestClient, headers: dict[str, str]
) -> None:
    org = create(database_client, headers, "Busy")
    for number in range(4):
        database_client.patch(
            f"/api/v1/orgs/{org['id']}", json={"name": f"Busy {number}"}, headers=headers
        )

    first = database_client.get(f"/api/v1/orgs/{org['id']}/audit-log", params={"limit": 3}).json()
    rest = database_client.get(
        f"/api/v1/orgs/{org['id']}/audit-log", params={"limit": 3, "cursor": first["next_cursor"]}
    ).json()

    names = [e["details"].get("name") for e in first["events"] + rest["events"]]
    assert names == ["Busy 3", "Busy 2", "Busy 1", "Busy 0", None]
    assert rest["next_cursor"] is None
    assert "ip" not in first["events"][0]


def test_a_broken_audit_cursor_is_refused(
    database_client: TestClient, headers: dict[str, str]
) -> None:
    org = create(database_client, headers, "Cursor")

    response = database_client.get(
        f"/api/v1/orgs/{org['id']}/audit-log", params={"cursor": "not-a-cursor"}
    )

    assert response.status_code == 422
```

`backend/tests/api/test_member_routes.py`:
```python
"""Members through the API (spec §6.4, §7): list, change roles, remove, leave."""

from dataclasses import dataclass
from uuid import UUID

import pytest
from browser import signed_in_as
from conftest import Database, FakeClock
from fastapi.testclient import TestClient
from sqlalchemy import text
from tenantdata import add_member, add_org, add_user

from nettriage.entrypoints.api.services import Services


@dataclass
class Team:
    org: UUID
    people: dict[str, UUID]


@pytest.fixture
def team(database: Database) -> Team:
    with database.admin.begin() as connection:
        people = {name: add_user(connection) for name in ("owner", "admin", "analyst", "viewer")}
        org = add_org(connection, people["owner"])
        for name, user in people.items():
            add_member(connection, org, user, name)
    return Team(org=org, people=people)


def act_as(client: TestClient, services: Services, clock: FakeClock, user: UUID) -> dict[str, str]:
    return signed_in_as(client, services.sessions, user, clock())


def last_audit(database: Database, org: UUID) -> tuple[str, dict[str, object]]:
    with database.admin.begin() as connection:
        row = connection.execute(
            text(
                "SELECT action, details FROM audit_log WHERE org_id = :org "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"org": org},
        ).one()
    return row.action, row.details


def test_members_are_listed_with_email_and_role(
    database_client: TestClient, services: Services, clock: FakeClock, team: Team
) -> None:
    act_as(database_client, services, clock, team.people["viewer"])

    response = database_client.get(f"/api/v1/orgs/{team.org}/members")

    assert response.status_code == 200
    roles = {m["user_id"]: m["role"] for m in response.json()["members"]}
    assert roles == {str(user): name for name, user in team.people.items()}


def test_an_owner_changes_a_role_and_it_is_audited(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    team: Team,
    database: Database,
) -> None:
    headers = act_as(database_client, services, clock, team.people["owner"])

    response = database_client.patch(
        f"/api/v1/orgs/{team.org}/members/{team.people['viewer']}",
        json={"role": "admin"},
        headers=headers,
    )

    assert (response.status_code, response.json()["role"]) == (200, "admin")
    assert last_audit(database, team.org) == (
        "member.role_changed",
        {"from": "viewer", "to": "admin"},
    )


def test_an_admin_granting_admin_is_an_audited_denial(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    team: Team,
    database: Database,
) -> None:
    headers = act_as(database_client, services, clock, team.people["admin"])

    response = database_client.patch(
        f"/api/v1/orgs/{team.org}/members/{team.people['viewer']}",
        json={"role": "admin"},
        headers=headers,
    )

    assert response.status_code == 403
    action, details = last_audit(database, team.org)
    assert (action, details["reason"], details["permission"]) == (
        "authz.denied",
        "escalation",
        "members:role",
    )


def test_nobody_changes_their_own_role_and_the_last_owner_stays(
    database_client: TestClient, services: Services, clock: FakeClock, team: Team
) -> None:
    headers = act_as(database_client, services, clock, team.people["owner"])

    own = database_client.patch(
        f"/api/v1/orgs/{team.org}/members/{team.people['owner']}",
        json={"role": "viewer"},
        headers=headers,
    )
    leave = database_client.delete(
        f"/api/v1/orgs/{team.org}/members/{team.people['owner']}", headers=headers
    )

    assert own.status_code == 403
    assert leave.status_code == 409
    assert "at least one owner" in leave.json()["detail"]


def test_a_viewer_can_leave(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    team: Team,
    database: Database,
) -> None:
    headers = act_as(database_client, services, clock, team.people["viewer"])

    response = database_client.delete(
        f"/api/v1/orgs/{team.org}/members/{team.people['viewer']}", headers=headers
    )

    assert response.status_code == 204
    assert last_audit(database, team.org) == ("member.left", {"role": "viewer"})
    assert database_client.get(f"/api/v1/orgs/{team.org}").status_code == 404


def test_an_admin_removes_an_analyst_but_not_the_owner(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    team: Team,
    database: Database,
) -> None:
    headers = act_as(database_client, services, clock, team.people["admin"])

    removed = database_client.delete(
        f"/api/v1/orgs/{team.org}/members/{team.people['analyst']}", headers=headers
    )
    audited = last_audit(database, team.org)
    refused = database_client.delete(
        f"/api/v1/orgs/{team.org}/members/{team.people['owner']}", headers=headers
    )

    assert (removed.status_code, refused.status_code) == (204, 403)
    assert audited == ("member.removed", {"role": "analyst"})


def test_changing_someone_who_isnt_a_member_is_not_found(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    team: Team,
    database: Database,
) -> None:
    headers = act_as(database_client, services, clock, team.people["owner"])
    with database.admin.begin() as connection:
        stranger = add_user(connection)

    response = database_client.patch(
        f"/api/v1/orgs/{team.org}/members/{stranger}", json={"role": "viewer"}, headers=headers
    )

    assert response.status_code == 404
```

`backend/tests/api/test_invitation_routes.py`:
```python
"""Invitations through the API (spec §6.3, §6.5, §7)."""

from uuid import UUID, uuid4

import pytest
from browser import signed_in_as
from conftest import Database, FakeClock
from fake_idp import APP_ORIGIN
from fastapi.testclient import TestClient
from sqlalchemy import text
from tenantdata import add_member, add_org, add_user

from nettriage.application.organizations import token_hash
from nettriage.entrypoints.api.services import Services


@pytest.fixture
def org(database: Database) -> tuple[UUID, UUID]:
    with database.admin.begin() as connection:
        owner = add_user(connection)
        org_id = add_org(connection, owner)
        add_member(connection, org_id, owner, "owner")
    return org_id, owner


def invite(
    client: TestClient, headers: dict[str, str], org: UUID, email: str, role: str = "viewer"
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/orgs/{org}/invitations", json={"email": email, "role": role}, headers=headers
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def test_an_invitation_link_carries_the_token_in_its_fragment_only_once(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID],
    database: Database,
) -> None:
    headers = signed_in_as(database_client, services.sessions, org[1], clock())

    created = invite(database_client, headers, org[0], "Invitee@Example.com", "analyst")
    listed = database_client.get(f"/api/v1/orgs/{org[0]}/invitations").json()["invitations"]

    url = str(created["invite_url"])
    token = url.removeprefix(f"{APP_ORIGIN}/invite#")
    assert url.startswith(f"{APP_ORIGIN}/invite#")
    assert len(token) == 43
    assert [i["email"] for i in listed] == ["Invitee@Example.com"]
    assert token not in str(listed)
    with database.admin.begin() as connection:
        stored: str = connection.execute(
            text("SELECT token_hash FROM invitations WHERE org_id = :org"), {"org": org[0]}
        ).scalar_one()
    assert stored == token_hash(token)


@pytest.mark.parametrize("email", ["not-an-address", "a b@example.com", ""])
def test_an_invitation_needs_an_email_address(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID],
    email: str,
) -> None:
    headers = signed_in_as(database_client, services.sessions, org[1], clock())

    response = database_client.post(
        f"/api/v1/orgs/{org[0]}/invitations",
        json={"email": email, "role": "viewer"},
        headers=headers,
    )

    assert response.status_code == 422


def test_inviting_the_same_address_twice_is_a_conflict(
    database_client: TestClient, services: Services, clock: FakeClock, org: tuple[UUID, UUID]
) -> None:
    headers = signed_in_as(database_client, services.sessions, org[1], clock())
    invite(database_client, headers, org[0], "twice@example.com")

    again = database_client.post(
        f"/api/v1/orgs/{org[0]}/invitations",
        json={"email": "TWICE@example.com", "role": "viewer"},
        headers=headers,
    )

    assert again.status_code == 409


def test_invitations_are_rate_limited_per_org(
    database_client: TestClient, services: Services, clock: FakeClock, org: tuple[UUID, UUID]
) -> None:
    headers = signed_in_as(database_client, services.sessions, org[1], clock())
    statuses = [
        database_client.post(
            f"/api/v1/orgs/{org[0]}/invitations",
            json={"email": f"{uuid4().hex}@example.com", "role": "viewer"},
            headers=headers,
        ).status_code
        for _ in range(6)
    ]

    assert statuses == [201] * 5 + [429]


def test_a_revoked_invitation_disappears_and_can_not_be_revoked_again(
    database_client: TestClient, services: Services, clock: FakeClock, org: tuple[UUID, UUID]
) -> None:
    headers = signed_in_as(database_client, services.sessions, org[1], clock())
    created = invite(database_client, headers, org[0], "gone@example.com")
    invitation_id = created["invitation"]["id"]  # type: ignore[index]

    first = database_client.delete(
        f"/api/v1/orgs/{org[0]}/invitations/{invitation_id}", headers=headers
    )
    again = database_client.delete(
        f"/api/v1/orgs/{org[0]}/invitations/{invitation_id}", headers=headers
    )

    assert (first.status_code, again.status_code) == (204, 404)
    assert database_client.get(f"/api/v1/orgs/{org[0]}/invitations").json()["invitations"] == []


def test_accepting_joins_the_org_once(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID],
    database: Database,
) -> None:
    headers = signed_in_as(database_client, services.sessions, org[1], clock())
    email = f"{uuid4().hex}@example.com"
    token = str(invite(database_client, headers, org[0], email, "analyst")["invite_url"]).split(
        "#"
    )[1]
    with database.admin.begin() as connection:
        invitee = add_user(connection, email.upper())
    invitee_headers = signed_in_as(database_client, services.sessions, invitee, clock())

    joined = database_client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=invitee_headers
    )
    again = database_client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=invitee_headers
    )

    assert (joined.status_code, joined.json()["role"], joined.json()["id"]) == (
        200,
        "analyst",
        str(org[0]),
    )
    assert again.status_code == 404
    with database.admin.begin() as connection:
        joins: int = connection.execute(
            text("SELECT count(*) FROM audit_log WHERE org_id = :org AND action = 'member.joined'"),
            {"org": org[0]},
        ).scalar_one()
    assert joins == 1


def test_an_invitation_for_someone_else_is_refused(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID],
    database: Database,
) -> None:
    headers = signed_in_as(database_client, services.sessions, org[1], clock())
    email = f"{uuid4().hex}@example.com"
    token = str(invite(database_client, headers, org[0], email)["invite_url"]).split("#")[1]
    with database.admin.begin() as connection:
        other = add_user(connection)
        invitee = add_user(connection, email)
    other_headers = signed_in_as(database_client, services.sessions, other, clock())

    refused = database_client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=other_headers
    )
    invitee_headers = signed_in_as(database_client, services.sessions, invitee, clock())
    joined = database_client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=invitee_headers
    )

    assert refused.status_code == 403
    assert joined.status_code == 200


@pytest.mark.parametrize("token", ["", "short", "x" * 43, "é" * 43])
def test_a_token_that_was_never_issued_is_not_found(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID],
    token: str,
) -> None:
    headers = signed_in_as(database_client, services.sessions, org[1], clock())

    response = database_client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=headers
    )

    assert response.status_code == 404
```

`backend/tests/security/test_authorization_matrix.py`:
```python
"""The authorization matrix (spec §6.4): every endpoint, called by an owner, an admin, an
analyst, a viewer, a non-member and an anonymous caller, against a hand-written table of
expected outcomes. Each case gets a fresh org, so destructive calls don't interfere."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from browser import signed_in_as
from conftest import Database, FakeClock
from fastapi.testclient import TestClient
from tenantdata import add_invitation, add_member, add_org, add_user

from nettriage.application.organizations import new_invitation_token, token_hash
from nettriage.entrypoints.api.services import Services

CALLERS = ("owner", "admin", "analyst", "viewer", "stranger", "anonymous")

# method, path, body; {org}, {target}, {invitation} and {org_name} are filled in per case.
ENDPOINTS: dict[str, tuple[str, str, dict[str, Any] | None]] = {
    "me": ("GET", "/api/v1/me", None),
    "create org": ("POST", "/api/v1/orgs", {"name": "New org"}),
    "read org": ("GET", "/api/v1/orgs/{org}", None),
    "rename org": ("PATCH", "/api/v1/orgs/{org}", {"name": "Renamed"}),
    "delete org": ("DELETE", "/api/v1/orgs/{org}", {"confirm_name": "{org_name}"}),
    "audit log": ("GET", "/api/v1/orgs/{org}/audit-log", None),
    "list members": ("GET", "/api/v1/orgs/{org}/members", None),
    "change role": ("PATCH", "/api/v1/orgs/{org}/members/{target}", {"role": "analyst"}),
    "remove member": ("DELETE", "/api/v1/orgs/{org}/members/{target}", None),
    "list invitations": ("GET", "/api/v1/orgs/{org}/invitations", None),
    "invite": (
        "POST",
        "/api/v1/orgs/{org}/invitations",
        {"email": "{new_email}", "role": "viewer"},
    ),
    "revoke invitation": ("DELETE", "/api/v1/orgs/{org}/invitations/{invitation}", None),
    "accept invitation": ("POST", "/api/v1/invitations/accept", {"token": "{token}"}),
}

# Expected status per caller: owner, admin, analyst, viewer, non-member, anonymous. The target
# member is another viewer; the pending invitation to accept is for the non-member's address.
MATRIX: dict[str, tuple[int, int, int, int, int, int]] = {
    "me": (200, 200, 200, 200, 200, 401),
    "create org": (201, 201, 201, 201, 201, 401),
    "read org": (200, 200, 200, 200, 404, 401),
    "rename org": (200, 200, 403, 403, 404, 401),
    "delete org": (204, 403, 403, 403, 404, 401),
    "audit log": (200, 200, 403, 403, 404, 401),
    "list members": (200, 200, 200, 200, 404, 401),
    "change role": (200, 200, 403, 403, 404, 401),
    "remove member": (204, 204, 403, 403, 404, 401),
    "list invitations": (200, 200, 403, 403, 404, 401),
    "invite": (201, 201, 403, 403, 404, 401),
    "revoke invitation": (204, 204, 403, 403, 404, 401),
    "accept invitation": (403, 403, 403, 403, 200, 401),
}


@dataclass
class World:
    values: dict[str, str]
    people: dict[str, UUID]


@pytest.fixture
def world(database: Database) -> World:
    stranger_email = f"{uuid4().hex}@example.com"
    token = new_invitation_token()
    with database.admin.begin() as connection:
        people = {
            name: add_user(connection) for name in ("owner", "admin", "analyst", "viewer", "target")
        }
        people["stranger"] = add_user(connection, stranger_email)
        org = add_org(connection, people["owner"])
        for name in ("owner", "admin", "analyst", "viewer"):
            add_member(connection, org, people[name], name)
        add_member(connection, org, people["target"], "viewer")
        invitation = add_invitation(connection, org, people["owner"], f"{uuid4().hex}@example.com")
        add_invitation(connection, org, people["owner"], stranger_email)
        connection.exec_driver_sql(
            "UPDATE invitations SET token_hash = %s WHERE lower(email) = lower(%s)",
            (token_hash(token), stranger_email),
        )
    return World(
        values={
            "org": str(org),
            "org_name": "Org",
            "target": str(people["target"]),
            "invitation": str(invitation),
            "token": token,
            "new_email": f"{uuid4().hex}@example.com",
        },
        people=people,
    )


def fill(value: Any, values: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format(**values)
    if isinstance(value, dict):
        return {key: fill(item, values) for key, item in value.items()}
    return value


@pytest.mark.parametrize("caller", CALLERS)
@pytest.mark.parametrize("endpoint", list(ENDPOINTS))
def test_every_endpoint_for_every_kind_of_caller(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    world: World,
    endpoint: str,
    caller: str,
) -> None:
    method, path, body = ENDPOINTS[endpoint]
    headers = {}
    if caller != "anonymous":
        headers = signed_in_as(database_client, services.sessions, world.people[caller], clock())

    response = database_client.request(
        method,
        fill(path, world.values),
        json=fill(body, world.values),
        headers=headers,
        follow_redirects=False,
    )

    expected = MATRIX[endpoint][CALLERS.index(caller)]
    assert response.status_code == expected, response.text
    if expected >= 400:
        assert response.headers["content-type"] == "application/problem+json"


def test_the_matrix_covers_every_org_route(client: TestClient) -> None:
    from test_route_access import EXPECTED

    covered = {
        (
            method,
            fill(path, {"org": "{org_id}", "target": "{user_id}", "invitation": "{invitation_id}"}),
        )
        for method, path, _ in ENDPOINTS.values()
    }
    routes = {key for key, access in EXPECTED.items() if key[1].startswith("/api/v1")}
    assert routes <= covered
```

In `backend/tests/security/test_route_access.py`, replace:
```python

from nettriage.entrypoints.api.access import Access, Public, SignedIn

```
with:
```python

from nettriage.entrypoints.api.access import Access, OrgMember, Public, SignedIn

```

In `backend/tests/security/test_route_access.py`, replace:
```python
    ("GET", "/api/v1/me"): "signed in",
}
```
with:
```python
    ("GET", "/api/v1/me"): "signed in",
    ("POST", "/api/v1/orgs"): "signed in",
    ("GET", "/api/v1/orgs/{org_id}"): "org:read",
    ("PATCH", "/api/v1/orgs/{org_id}"): "org:update",
    ("DELETE", "/api/v1/orgs/{org_id}"): "org:delete",
    ("GET", "/api/v1/orgs/{org_id}/audit-log"): "audit:read",
    ("GET", "/api/v1/orgs/{org_id}/members"): "members:read",
    ("PATCH", "/api/v1/orgs/{org_id}/members/{user_id}"): "members:role",
    ("DELETE", "/api/v1/orgs/{org_id}/members/{user_id}"): "members:remove or self",
    ("GET", "/api/v1/orgs/{org_id}/invitations"): "members:invite",
    ("POST", "/api/v1/orgs/{org_id}/invitations"): "members:invite",
    ("DELETE", "/api/v1/orgs/{org_id}/invitations/{invitation_id}"): "members:invite",
    ("POST", "/api/v1/invitations/accept"): "signed in",
}
```

In `backend/tests/security/test_route_access.py`, replace:
```python

def describe(access: Access) -> str:
    if isinstance(access, Public):
        return access.policy.name
    assert isinstance(access, SignedIn)
    return "signed in"

```
with:
```python

def most_specific(rules: list[Access]) -> set[str]:
    """An org rule includes a session (OrgMember depends on SignedIn), so it speaks for both."""
    org_rules = {rule for rule in rules if isinstance(rule, OrgMember)}
    if org_rules:
        return {r.permission + (" or self" if r.or_self else "") for r in org_rules}
    if any(isinstance(rule, SignedIn) for rule in rules):
        return {"signed in"}
    return {rule.policy.name for rule in rules if isinstance(rule, Public)}

```

In `backend/tests/security/test_route_access.py`, replace:
```python
            continue
        access = {describe(rule) for rule in declared_access(route)}
        assert len(access) == 1, f"{context.path} declares {access or 'no access rule'}"
```
with:
```python
            continue
        access = most_specific(declared_access(route))
        assert len(access) == 1, f"{context.path} declares {access or 'no access rule'}"
```

In `backend/tests/conftest.py`, replace:
```python
import os
```
with:
```python
import io
import logging
import os
```

In `backend/tests/conftest.py`, replace:
```python
from nettriage.platform.config import Settings
from nettriage.platform.metrics import AppMetrics
```
with:
```python
from nettriage.platform.config import Settings
from nettriage.platform.logging import QUIET_LOGGERS, configure_logging
from nettriage.platform.metrics import AppMetrics
```

In `backend/tests/conftest.py`, replace:
```python
@pytest.fixture
def services(
```
with:
```python
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


@pytest.fixture
def services(
```

In `backend/tests/security/test_sign_in_logs.py`, replace:
```python
import io
import logging
from collections.abc import Iterator
from uuid import uuid4

import pytest
from browser import csrf_headers, finish_sign_in, start_sign_in
```
with:
```python
import io
from uuid import uuid4

from browser import csrf_headers, finish_sign_in, start_sign_in
```

In `backend/tests/security/test_sign_in_logs.py`, replace:
```python
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

```
with:
```python
from nettriage.application.sessions import COOKIE_NAME

```

`backend/tests/security/test_org_logs.py`:
```python
"""Inviting and accepting never write an email or an invitation token to the logs (spec §9.3),
even when a request is refused."""

import io
from uuid import uuid4

from browser import signed_in_as
from conftest import Database, FakeClock
from fastapi.testclient import TestClient
from tenantdata import add_member, add_org, add_user

from nettriage.entrypoints.api.services import Services


def test_inviting_and_accepting_log_no_email_and_no_token(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    database: Database,
    logs: io.StringIO,
) -> None:
    email = f"{uuid4().hex}@example.com"
    with database.admin.begin() as connection:
        owner = add_user(connection)
        org_id = add_org(connection, owner)
        add_member(connection, org_id, owner, "owner")
        stranger = add_user(connection)
        invitee = add_user(connection, email)
    invitations = f"/api/v1/orgs/{org_id}/invitations"
    headers = signed_in_as(database_client, services.sessions, owner, clock())
    body = {"email": email, "role": "viewer"}
    created = database_client.post(invitations, json=body, headers=headers).json()
    token = str(created["invite_url"]).split("#")[1]
    again = database_client.post(invitations, json=body, headers=headers)
    stranger_headers = signed_in_as(database_client, services.sessions, stranger, clock())
    refused = database_client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=stranger_headers
    )
    invitee_headers = signed_in_as(database_client, services.sessions, invitee, clock())
    joined = database_client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=invitee_headers
    )

    assert (again.status_code, refused.status_code, joined.status_code) == (409, 403, 200)
    lines = logs.getvalue().splitlines()
    assert lines
    leaked = [line for line in lines if email in line.lower() or token in line]
    assert leaked == []
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `just test`
Expected: `93 failed, 450 passed, 1 skipped`. The routes don't exist yet, so these fail:
- 14 tests in `test_org_routes.py`, 6 in `test_member_routes.py` and 9 in `test_invitation_routes.py`;
- 62 of the matrix's 78 cases (the 16 that pass are the 6 calls of `GET /api/v1/me`, which exists, and the 10 non-member calls that expect 404, which a missing route also gives);
- `test_inviting_and_accepting_log_no_email_and_no_token` and `test_every_route_declares_exactly_one_access_rule`.

- [ ] **Step 3: Write the request and response models, and the error mapping**

`backend/src/nettriage/entrypoints/api/schemas.py`:
```python
"""Request and response bodies for the organization API (spec §7). Every request model forbids
fields it doesn't declare, so a client can't set anything it wasn't offered (OWASP API3)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

from nettriage.adapters.invitations import Invitation
from nettriage.adapters.organizations import Member, Organization
from nettriage.application.permissions import Role

# 1 to 100 characters after trimming, and no control characters (line breaks, tabs, NUL).
OrgName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=100, pattern=r"^[^\x00-\x1f\x7f]+$"
    ),
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NameIn(Strict):
    name: OrgName


class DeleteOrgIn(Strict):
    confirm_name: Annotated[str, StringConstraints(max_length=100)]


class RoleIn(Strict):
    role: Role


class InvitationIn(Strict):
    email: Annotated[str, StringConstraints(max_length=320)]
    role: Role


class AcceptIn(Strict):
    token: Annotated[str, StringConstraints(max_length=100)]


class OrgOut(BaseModel):
    id: UUID
    name: str
    slug: str
    role: Role
    member_count: int
    created_at: datetime

    @classmethod
    def of(cls, org: Organization) -> OrgOut:
        return cls(
            id=org.id,
            name=org.name,
            slug=org.slug,
            role=org.role,
            member_count=org.member_count,
            created_at=org.created_at,
        )


class MemberOut(BaseModel):
    user_id: UUID
    email: str
    display_name: str | None
    role: Role
    joined_at: datetime

    @classmethod
    def of(cls, member: Member) -> MemberOut:
        return cls(
            user_id=member.user_id,
            email=member.email,
            display_name=member.display_name,
            role=member.role,
            joined_at=member.joined_at,
        )


class MembersOut(BaseModel):
    members: list[MemberOut]


class InvitationOut(BaseModel):
    id: UUID
    email: str
    role: Role
    expires_at: datetime
    created_at: datetime
    created_by: UUID

    @classmethod
    def of(cls, invitation: Invitation) -> InvitationOut:
        return cls(
            id=invitation.id,
            email=invitation.email,
            role=invitation.role,
            expires_at=invitation.expires_at,
            created_at=invitation.created_at,
            created_by=invitation.created_by,
        )


class InvitationsOut(BaseModel):
    invitations: list[InvitationOut]


class CreatedInvitationOut(BaseModel):
    invitation: InvitationOut
    # The only time the token is shown (spec §6.3). The fragment never reaches servers or logs.
    invite_url: str


class AuditEventOut(BaseModel):
    id: UUID
    created_at: datetime
    actor_user_id: UUID | None
    actor_type: str
    action: str
    target_type: str | None
    target_id: str | None
    outcome: str
    details: dict[str, object]


class AuditLogOut(BaseModel):
    events: list[AuditEventOut]
    next_cursor: str | None
```

`backend/src/nettriage/entrypoints/api/org_errors.py`:
```python
"""How the organization rules' refusals become HTTP answers (spec §6.4, §7), in one place.
An escalation attempt is a denial like any other, so it's recorded as `authz.denied`."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from nettriage.application.organizations import (
    ConfirmationMismatch,
    Forbidden,
    InvitationInvalid,
    NotFound,
    OrgRuleError,
    WrongEmail,
)
from nettriage.entrypoints.api.access import OrgContext, deny, forbidden, unavailable

logger = logging.getLogger(__name__)


@contextmanager
def org_rules(
    request: Request, what: str, *, org: OrgContext | None = None, permission: str = ""
) -> Iterator[None]:
    """Run organization work, answering its refusals and outages as HTTP errors:
    - Forbidden (no escalation) is 403, and recorded as `authz.denied` for `permission`;
    - NotFound and InvitationInvalid are 404; WrongEmail is 403;
    - ConfirmationMismatch is 422; the rest (last owner, conflicts, quotas) are 409;
    - a database outage is 503."""
    try:
        yield
    except Forbidden as error:
        if org is not None:
            deny(
                request,
                org.user_id,
                permission,
                org_id=org.org_id,
                target=org.org_id,
                reason="escalation",
            )
        raise forbidden(str(error)) from None
    except (NotFound, InvitationInvalid) as error:
        raise HTTPException(404, detail=str(error)) from None
    except WrongEmail as error:
        raise HTTPException(403, detail=str(error)) from None
    except ConfirmationMismatch as error:
        raise HTTPException(422, detail=str(error)) from None
    except OrgRuleError as error:
        raise HTTPException(409, detail=str(error)) from None
    except SQLAlchemyError:
        logger.exception("organization_work_failed", extra={"what": what})
        raise unavailable(what) from None
```

- [ ] **Step 4: Write the routes and register them**

`backend/src/nettriage/entrypoints/api/routes/orgs.py`:
```python
"""Organizations (spec §7): create, read, rename, delete, and read the audit log."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Annotated
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from nettriage.adapters.audit_log import list_org_events
from nettriage.adapters.idempotency import (
    IdempotencyInProgress,
    IdempotencyMismatch,
    StoredResponse,
)
from nettriage.adapters.organizations import create_org, delete_org, get_org, rename_org
from nettriage.entrypoints.api.access import CurrentSession, OrgContext, OrgMember, unavailable
from nettriage.entrypoints.api.auditing import audit
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.schemas import (
    AuditEventOut,
    AuditLogOut,
    DeleteOrgIn,
    NameIn,
    OrgOut,
)
from nettriage.entrypoints.api.services import get_services

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/orgs")

IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{1,100}")


@router.post("", status_code=201, response_model=OrgOut)
def create(request: Request, body: NameIn, session: CurrentSession) -> Response:
    """Create an org with the caller as its owner. An `Idempotency-Key` header makes a retry
    return the first response instead of creating a second org (spec §7)."""
    services = get_services(request)
    key = request.headers.get("idempotency-key")
    if key is not None and not IDEMPOTENCY_KEY.fullmatch(key):
        raise HTTPException(
            422, detail="Idempotency-Key must be 1 to 100 letters, digits, dashes or underscores."
        )
    if key is not None:
        request_hash = hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True).encode())
        try:
            stored = services.idempotency.begin(
                session.user_id, key, request_hash.hexdigest(), services.clock()
            )
        except IdempotencyMismatch:
            raise HTTPException(
                422, detail="This Idempotency-Key was already used for a different request."
            ) from None
        except IdempotencyInProgress:
            raise HTTPException(
                409, detail="A request with this Idempotency-Key is still running; retry shortly."
            ) from None
        except BotoCoreError, ClientError:
            logger.exception("idempotency_read_failed")
            raise unavailable("Creating an organization") from None
        if stored is not None:
            return _created(stored)
    try:
        with org_rules(request, "Creating an organization"):
            org = create_org(services.database, user_id=session.user_id, name=body.name)
    except Exception:
        if key is not None:
            _forget(request, session.user_id, key)
        raise
    created = StoredResponse(status=201, body=OrgOut.of(org).model_dump(mode="json"))
    if key is not None:
        try:
            services.idempotency.finish(session.user_id, key, created, services.clock())
        except BotoCoreError, ClientError:
            logger.warning("idempotency_write_failed")
    audit(
        request,
        action="org.created",
        outcome="success",
        actor_user_id=session.user_id,
        org_id=org.id,
        target_type="organization",
        target_id=str(org.id),
    )
    return _created(created)


@router.get("/{org_id}")
def read(
    request: Request, org_id: UUID, org: Annotated[OrgContext, Depends(OrgMember("org:read"))]
) -> OrgOut:
    with org_rules(request, "This organization"):
        return OrgOut.of(get_org(get_services(request).database, org.org_id, org.user_id))


@router.patch("/{org_id}")
def rename(
    request: Request,
    org_id: UUID,
    body: NameIn,
    org: Annotated[OrgContext, Depends(OrgMember("org:update"))],
) -> OrgOut:
    with org_rules(request, "Renaming this organization"):
        renamed = rename_org(get_services(request).database, org.org_id, org.user_id, body.name)
    audit(
        request,
        action="org.renamed",
        outcome="success",
        actor_user_id=org.user_id,
        org_id=org.org_id,
        target_type="organization",
        target_id=str(org.org_id),
        details={"name": body.name},
    )
    return OrgOut.of(renamed)


@router.delete("/{org_id}", status_code=204)
def delete(
    request: Request,
    org_id: UUID,
    body: DeleteOrgIn,
    org: Annotated[OrgContext, Depends(OrgMember("org:delete"))],
) -> None:
    """Delete the org, its members, invitations and everything it owns. The caller must type
    the org's name (spec §7). The audit log keeps its records."""
    with org_rules(request, "Deleting this organization"):
        delete_org(get_services(request).database, org.org_id, org.user_id, body.confirm_name)
    audit(
        request,
        action="org.deleted",
        outcome="success",
        actor_user_id=org.user_id,
        org_id=org.org_id,
        target_type="organization",
        target_id=str(org.org_id),
    )


@router.get("/{org_id}/audit-log")
def audit_log(
    request: Request,
    org_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("audit:read"))],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> AuditLogOut:
    """The org's audit events, newest first, a page at a time (spec §7's cursor pagination)."""
    before = _decode(cursor) if cursor else None
    with org_rules(request, "The audit log"):
        entries = list_org_events(
            get_services(request).database,
            org.org_id,
            org.user_id,
            limit=limit + 1,
            before=before,
        )
    page = entries[:limit]
    more = len(entries) > limit
    return AuditLogOut(
        events=[AuditEventOut(**vars(entry)) for entry in page],
        next_cursor=_encode(page[-1].created_at, page[-1].id) if more else None,
    )


def _created(stored: StoredResponse) -> Response:
    return JSONResponse(
        stored.body,
        status_code=stored.status,
        headers={"Location": f"/api/v1/orgs/{stored.body['id']}"},
    )


def _forget(request: Request, user_id: UUID, key: str) -> None:
    try:
        get_services(request).idempotency.abandon(user_id, key)
    except BotoCoreError, ClientError:
        logger.warning("idempotency_abandon_failed")


def _encode(created_at: datetime, event_id: UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(event_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        created_at, event_id = json.loads(raw)
        return datetime.fromisoformat(created_at), UUID(event_id)
    except (ValueError, TypeError) as error:
        raise HTTPException(
            422, detail="The cursor isn't valid; start from the first page."
        ) from error
```

`backend/src/nettriage/entrypoints/api/routes/members.py`:
```python
"""An organization's members (spec §7): list them, change a role, remove someone or leave."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from nettriage.adapters.organizations import change_role, list_members, remove_member
from nettriage.entrypoints.api.access import OrgContext, OrgMember
from nettriage.entrypoints.api.auditing import audit
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.schemas import MemberOut, MembersOut, RoleIn
from nettriage.entrypoints.api.services import get_services

router = APIRouter(prefix="/v1/orgs/{org_id}/members")


@router.get("")
def members(
    request: Request, org_id: UUID, org: Annotated[OrgContext, Depends(OrgMember("members:read"))]
) -> MembersOut:
    with org_rules(request, "The member list"):
        found = list_members(get_services(request).database, org.org_id, org.user_id)
    return MembersOut(members=[MemberOut.of(member) for member in found])


@router.patch("/{user_id}")
def set_role(
    request: Request,
    org_id: UUID,
    user_id: UUID,
    body: RoleIn,
    org: Annotated[OrgContext, Depends(OrgMember("members:role"))],
) -> MemberOut:
    with org_rules(request, "Changing a role", org=org, permission="members:role"):
        previous, member = change_role(
            get_services(request).database,
            org.org_id,
            actor_id=org.user_id,
            actor_role=org.role,
            target_id=user_id,
            role=body.role,
        )
    audit(
        request,
        action="member.role_changed",
        outcome="success",
        actor_user_id=org.user_id,
        org_id=org.org_id,
        target_type="user",
        target_id=str(user_id),
        details={"from": previous, "to": body.role},
    )
    return MemberOut.of(member)


@router.delete("/{user_id}", status_code=204)
def remove(
    request: Request,
    org_id: UUID,
    user_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("members:remove", or_self=True))],
) -> None:
    """Remove a member, or leave when `{user_id}` is the caller (spec §6.4)."""
    with org_rules(request, "Removing a member", org=org, permission="members:remove"):
        removal = remove_member(
            get_services(request).database,
            org.org_id,
            actor_id=org.user_id,
            actor_role=org.role,
            target_id=user_id,
        )
    audit(
        request,
        action="member.left" if removal.left else "member.removed",
        outcome="success",
        actor_user_id=org.user_id,
        org_id=org.org_id,
        target_type="user",
        target_id=str(user_id),
        details={"role": removal.role},
    )
```

`backend/src/nettriage/entrypoints/api/routes/invitations.py`:
```python
"""Invitations (spec §6.3, §7): invite by email, list and revoke them, and accept one."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from nettriage.adapters.invitations import (
    INVALID,
    accept_invitation,
    create_invitation,
    list_invitations,
    revoke_invitation,
)
from nettriage.application.organizations import invitation_url, normalize_email
from nettriage.application.rate_limits import POLICIES
from nettriage.application.sessions import is_secret
from nettriage.entrypoints.api.access import CurrentSession, OrgContext, OrgMember, enforce
from nettriage.entrypoints.api.auditing import audit
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.schemas import (
    AcceptIn,
    CreatedInvitationOut,
    InvitationIn,
    InvitationOut,
    InvitationsOut,
    OrgOut,
)
from nettriage.entrypoints.api.services import get_services

router = APIRouter(prefix="/v1")


@router.get("/orgs/{org_id}/invitations")
def invitations(
    request: Request,
    org_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("members:invite"))],
) -> InvitationsOut:
    services = get_services(request)
    with org_rules(request, "The invitation list"):
        found = list_invitations(services.database, org.org_id, org.user_id, services.clock())
    return InvitationsOut(invitations=[InvitationOut.of(invitation) for invitation in found])


@router.post("/orgs/{org_id}/invitations", status_code=201)
def invite(
    request: Request,
    org_id: UUID,
    body: InvitationIn,
    org: Annotated[OrgContext, Depends(OrgMember("members:invite"))],
) -> CreatedInvitationOut:
    services = get_services(request)
    email = normalize_email(body.email)
    if email is None:
        raise HTTPException(422, detail="That isn't an email address.")
    enforce(request, POLICIES["invites.org"], str(org.org_id), actor=org.user_id)
    with org_rules(request, "Inviting someone", org=org, permission="members:invite"):
        invitation, token = create_invitation(
            services.database,
            org.org_id,
            actor_id=org.user_id,
            actor_role=org.role,
            email=email,
            role=body.role,
            now=services.clock(),
        )
    audit(
        request,
        action="member.invited",
        outcome="success",
        actor_user_id=org.user_id,
        org_id=org.org_id,
        target_type="invitation",
        target_id=str(invitation.id),
        details={"role": invitation.role},
    )
    return CreatedInvitationOut(
        invitation=InvitationOut.of(invitation),
        invite_url=invitation_url(services.app_origin, token),
    )


@router.delete("/orgs/{org_id}/invitations/{invitation_id}", status_code=204)
def revoke(
    request: Request,
    org_id: UUID,
    invitation_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("members:invite"))],
) -> None:
    services = get_services(request)
    with org_rules(request, "Revoking an invitation"):
        revoked = revoke_invitation(
            services.database,
            org.org_id,
            actor_id=org.user_id,
            invitation_id=invitation_id,
            now=services.clock(),
        )
    if not revoked:
        raise HTTPException(404, detail="No pending invitation with that ID.")
    audit(
        request,
        action="invitation.revoked",
        outcome="success",
        actor_user_id=org.user_id,
        org_id=org.org_id,
        target_type="invitation",
        target_id=str(invitation_id),
    )


@router.post("/invitations/accept")
def accept(request: Request, body: AcceptIn, session: CurrentSession) -> OrgOut:
    """Join an org with an invitation's token, from the link's fragment (spec §6.3)."""
    if not is_secret(body.token):
        raise HTTPException(404, detail=INVALID)
    services = get_services(request)
    with org_rules(request, "Accepting the invitation"):
        org = accept_invitation(
            services.database, user_id=session.user_id, token=body.token, now=services.clock()
        )
    audit(
        request,
        action="member.joined",
        outcome="success",
        actor_user_id=session.user_id,
        org_id=org.id,
        target_type="user",
        target_id=str(session.user_id),
        details={"role": org.role},
    )
    return OrgOut.of(org)
```

In `backend/src/nettriage/entrypoints/api/app.py`, replace:
```python
from nettriage.entrypoints.api.access import RedirectInstead
from nettriage.entrypoints.api.routes import auth, health, me
from nettriage.entrypoints.api.services import Services
```
with:
```python
from nettriage.entrypoints.api.access import RedirectInstead
from nettriage.entrypoints.api.routes import auth, health, invitations, me, members, orgs
from nettriage.entrypoints.api.services import Services
```

In `backend/src/nettriage/entrypoints/api/app.py`, replace:
```python
    app.middleware("http")(add_rate_limit_headers)
    for module in (health, auth, me):
        app.include_router(module.router, prefix="/api")
```
with:
```python
    app.middleware("http")(add_rate_limit_headers)
    for module in (health, auth, me, orgs, members, invitations):
        app.include_router(module.router, prefix="/api")
```

- [ ] **Step 5: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `543 passed, 1 skipped`.

`test_inviting_and_accepting_log_no_email_and_no_token` runs the production logging at DEBUG through an invitation, a duplicate, a refused acceptance and a successful one, and finds neither the email nor the token in any line.

The matrix runs 78 cases (13 endpoints × 6 callers), each against a fresh org, so destructive calls don't interfere. `test_the_matrix_covers_every_org_route` fails if a new org route is added without a row.

- [ ] **Step 6: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(orgs): organization, member and invitation API with the authorization matrix" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Spec amendments, runbook and README

**Files:**
- Modify: `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (§4.1, §5.3, §6.4, §6.5, §7, §9.4), `docs/runbooks/setup-and-deploy.md` (a new B5, and a Part C row), `README.md`

- [ ] **Step 1: Amend the spec**

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown

### 4.2 Upload → findings → AI explanation
```
with:
```markdown

Every authorize request also sends `prompt=login` (the owner's decision, Plan 3c). "Sign out everywhere" can't end Cognito's own session on other devices, so that session must never let a sign-in skip the password and the authenticator code.

### 4.2 Upload → findings → AI explanation
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
`users` also has row-level security (the owner's decision, 2026-09-28, Plan 3b). A user sees their own row, and in an organization's transaction the members of that organization. Sign-in finds or creates the user through a `SECURITY DEFINER` function, because the API's role can't read or insert other users' rows.

```
with:
```markdown
`users` also has row-level security (the owner's decision, 2026-09-28, Plan 3b). A user sees their own row, and in an organization's transaction the members of that organization. Sign-in finds or creates the user through a `SECURITY DEFINER` function, because the API's role can't read or insert other users' rows.

`invitations` has one more read policy (Plan 3c): a transaction that sets `app.invitation_token_hash` sees the one invitation with that hash. The invitee isn't a member yet, and a `SECURITY DEFINER` function can't help, because FORCE applies to the table's owner too. Knowing the hash already means holding the link.

```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
- **No mass assignment.** Every endpoint has explicit request and response schemas (OWASP API3).
- **Denials are recorded.** Each denial writes `authz.denied` to the audit log and increments a metric, and a spike raises an alert.
- **Test matrix.** Every endpoint is tested for each of: owner, admin, analyst, viewer, non-member and anonymous, against a hand-written table of expected outcomes.
```
with:
```markdown
- **No mass assignment.** Every endpoint has explicit request and response schemas (OWASP API3).
- **Denials are recorded.** Each denial increments a metric, and a spike raises an alert. The audit log gets an `authz.denied` event at most once a minute per caller and permission (amended in Plan 3c): anyone can sign up, so one row per denial would let a single account fill the database.
- **Test matrix.** Every endpoint is tested for each of: owner, admin, analyst, viewer, non-member and anonymous, against a hand-written table of expected outcomes.
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `ai.rerun.user` | user | 10 / hour | 3 |

```
with:
```markdown
| `ai.rerun.user` | user | 10 / hour | 3 |

`GET /api/auth/login` and `/callback` each count against `auth.ip` in a bucket of their own, so a sign-in costs one slot in each. A limited sign-in redirects to `/?sign_in=limited` instead of answering JSON. A limited `GET /api/health` counts the metric but writes no audit row, so the probe never touches the database (Plan 3c).

```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
- **Errors:** RFC 9457 Problem Details (`type`, `title`, `status`, `detail`, `instance`, `trace_id`), never stack traces.
- **Pagination:** cursor-based (`cursor`, `limit` ≤ 100).
- **Optimistic concurrency:** findings return an `ETag`. `PATCH` requires `If-Match`: a stale version gets **412 Precondition Failed**, and a missing header gets 428.
- **Idempotency:** `Idempotency-Key` is supported on `POST …/uploads` and `POST /orgs` and is kept for 24 hours.
- **SPA request headers:** `x-amz-content-sha256` on every request with a body (the OAC requirement), and `X-CSRF-Token` on state-changing requests.
```
with:
```markdown
- **Errors:** RFC 9457 Problem Details (`type`, `title`, `status`, `detail`, `instance`, `trace_id`), never stack traces.
- **Pagination:** cursor-based (`cursor`, `limit` ≤ 100). Lists that §5.7's quotas keep small (members, invitations) return every item; the audit log pages with a cursor.
- **Optimistic concurrency:** findings return an `ETag`. `PATCH` requires `If-Match`: a stale version gets **412 Precondition Failed**, and a missing header gets 428.
- **Idempotency:** `Idempotency-Key` is supported on `POST …/uploads` and `POST /orgs` and is kept for 24 hours. A key reused with a different request gets 422, and a retry while the first request still runs gets 409.
- **SPA request headers:** `x-amz-content-sha256` on every request with a body (the OAC requirement), and `X-CSRF-Token` on state-changing requests.
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown

`auth.session_created`, `auth.logout`, `auth.logout_all`, `org.created`, `org.renamed`, `org.deleted`, `member.invited`, `member.joined`, `member.role_changed`, `member.removed`, `member.left`, `invitation.revoked`, `upload.created`, `finding.status_changed`, `finding.assigned`, `finding.commented`, `ai.rerun_requested`, `authz.denied`, `budget.exhausted`, and `ratelimit.limited` (sampled: at most one per subject and policy per minute).

```
with:
```markdown

`auth.session_created`, `auth.logout`, `auth.logout_all`, `org.created`, `org.renamed`, `org.deleted`, `member.invited`, `member.joined`, `member.role_changed`, `member.removed`, `member.left`, `invitation.revoked`, `upload.created`, `finding.status_changed`, `finding.assigned`, `finding.commented`, `ai.rerun_requested`, `authz.denied` (sampled: at most one per caller and permission per minute), `budget.exhausted`, and `ratelimit.limited` (sampled: at most one per subject and policy per minute).

```

- [ ] **Step 2: Add "Try organizations" and the limited sign-in to the runbook**

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown

## Part C: when things go wrong
```
with:
````markdown

### B5. Try organizations
The organization pages come in Plan 6. Until then, you can call the API from the browser's
developer console while signed in.
1. Sign in as in B4 (steps 2 to 6). You're on `https://<id>.cloudfront.net/app`.
2. Press **F12** and choose the **Console** tab.
3. The first time you paste into the console, the browser refuses and asks you to type
   `allow pasting`. Type it and press **Enter**.
4. Paste this and press **Enter**. It defines `api(method, path, body)`, which calls the API the
   way the app will: with your CSRF token, and with the body's SHA-256 in `x-amz-content-sha256`,
   which CloudFront needs before it passes a body on to the API.
   ```js
   const me = await (await fetch("/api/v1/me")).json();
   async function api(method, path, body) {
     const headers = { "X-CSRF-Token": me.csrf_token };
     const text = body === undefined ? undefined : JSON.stringify(body);
     if (text !== undefined) {
       const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
       headers["Content-Type"] = "application/json";
       headers["x-amz-content-sha256"] = [...new Uint8Array(hash)]
         .map((byte) => byte.toString(16).padStart(2, "0"))
         .join("");
     }
     const response = await fetch("/api/v1" + path, { method, headers, body: text });
     const answer = response.status === 204 ? null : await response.json();
     console.log(response.status, answer);
     return answer;
   }
   ```
5. Create an organization:
   ```js
   const org = await api("POST", "/orgs", { name: "Acme Security" });
   ```
   The console shows `201` and the org, with `slug: "acme-security"`, `role: "owner"` and
   `member_count: 1`.
6. Invite someone. Any address works; nothing is emailed yet:
   ```js
   await api("POST", "/orgs/" + org.id + "/invitations", { email: "colleague@example.com", role: "viewer" });
   ```
   `201`, with an `invite_url` ending in `/invite#` and a long token. The link is shown only this
   once. The page it opens comes in Plan 6.
7. Read the audit log:
   ```js
   await api("GET", "/orgs/" + org.id + "/audit-log");
   ```
   `200`, with `member.invited` and `org.created`, newest first.
8. Try a refusal:
   ```js
   await api("DELETE", "/orgs/" + org.id, { confirm_name: "not the name" });
   ```
   `422`: deleting an org needs its exact name.
9. Delete the test org (you can have at most 3):
   ```js
   await api("DELETE", "/orgs/" + org.id, { confirm_name: "Acme Security" });
   ```
   `204`.

If the console shows `401`, your session ended: sign in again and repeat from step 4.

## Part C: when things go wrong
````

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
| The browser lands on `/?sign_in=unavailable` | DynamoDB, Neon or Cognito didn't answer. Wait a minute and start again; if it keeps happening, tell Claude |
| The browser lands on `/?sign_in=disabled` | This account is disabled in the database. Tell Claude if that's unexpected |
```
with:
```markdown
| The browser lands on `/?sign_in=unavailable` | DynamoDB, Neon or Cognito didn't answer. Wait a minute and start again; if it keeps happening, tell Claude |
| The browser lands on `/?sign_in=limited` | Too many sign-ins from your network in a short time. Wait a minute, then start again |
| The browser lands on `/?sign_in=disabled` | This account is disabled in the database. Tell Claude if that's unexpected |
```

- [ ] **Step 3: Add the highlight to the README**

In `README.md`, replace:
```markdown
- **Sign-in with mandatory TOTP MFA** through Cognito, the backend-for-frontend way: the browser only holds an opaque, HttpOnly session cookie, with CSRF checks on every state-changing request ([ADR 0004](docs/adr/0004-backend-for-frontend-sessions.md)).
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for the API.
```
with:
```markdown
- **Sign-in with mandatory TOTP MFA** through Cognito, the backend-for-frontend way: the browser only holds an opaque, HttpOnly session cookie, with CSRF checks on every state-changing request ([ADR 0004](docs/adr/0004-backend-for-frontend-sessions.md)).
- **Organizations with roles** (owner, admin, analyst, viewer) and one-time invitation links; a test calls every endpoint as each role, a non-member and an anonymous caller.
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for the API.
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-09-26-nettriage-m1-design.md docs/runbooks/setup-and-deploy.md README.md
git commit -m "docs: organizations and access control in the spec, runbook and README" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7 (Claude, then the owner): Pull request, deploy, and a first organization

- [ ] **Step 1 (Claude):**
  - Run `just lint test tools-test edge-test web-check tf-check pin-check cloud-check detection-report build-lambda`; everything must pass.
  - Get a final review of the whole branch, fix what it finds, then push `plan-3c/organizations-and-access-control`.
  - Open the PR and watch CI.
  - Request a Copilot review.
- [ ] **Step 2 (owner):** Review the PR. Optionally run `just plan-dev` (runbook B1). Expected: `Plan: 0 to add, 1 to change, 0 to destroy`. The change is the API function's code.
- [ ] **Step 3 (owner):** Squash-merge the PR.
- [ ] **Step 4 (owner):** Runbook B2 (`just deploy-dev`). Expected:
  - the preflight passes;
  - `Database migrated.` (migration `0004` is applied here);
  - the Terraform plan above;
  - eleven smoke `PASS` lines.
- [ ] **Step 5 (owner):** Runbook B4 step 2: signing in now always asks for your password and a fresh authenticator code.
- [ ] **Step 6 (owner):** Runbook B5: create an organization, invite an address, read the audit log, and see a wrong confirmation name refused.

## Plan 3c is done when

- [ ] `just lint test` passes locally and CI passes.
- [ ] The owner has created an organization on dev and seen its audit log (runbook B5).
- [ ] The PR is merged through review, with every thread resolved.

## Spec coverage of this plan

| Spec | Covered here |
|---|---|
| §4.1 `prompt=login` on every sign-in (the owner's decision) | Task 1; amends §4.1 (Task 6) |
| §5.3 row-level security for accepting an invitation | Task 3 (migration `0004`); amends §5.3 (Task 6) |
| §5.7 quotas: 10 members per org, 3 orgs per user, 20 pending invitations per org | Tasks 2 and 3 |
| §6.3 invitations: 32-byte token in the fragment, SHA-256 stored, 7 days, once, email match ignoring case | Tasks 2, 3 and 5 |
| §6.4 the permission table, leaving, deny by default, 404 across orgs, no escalation, last owner, no mass assignment | Tasks 2 to 5 (`test_route_access`, `schemas.Strict`) |
| §6.4 denials recorded: metric and sampled `authz.denied` | Task 4; amends §6.4 (Task 6) |
| §6.4 test matrix: owner, admin, analyst, viewer, non-member, anonymous | Task 5 (`test_authorization_matrix`) |
| §6.5 `invites.org` per org; the sign-in buckets and the limited redirect; health without audit rows | Tasks 1 and 5; amends §6.5 (Task 6) |
| §6.7 JSON request bodies at most 64 KB | Task 4 |
| §7 org, member, invitation and audit-log routes | Task 5 |
| §7 pagination (small lists whole, the audit log by cursor) and `Idempotency-Key` on `POST /orgs` (24 h, 422, 409) | Tasks 4 and 5; amends §7 (Task 6) |
| §9.2 `nettriage.authz.denied` | Task 4 |
| §9.3 no emails, tokens, state or codes in logs and traces | Task 1 (traces) and Task 5 (`test_org_logs`) |
| §9.4 `org.created`, `org.renamed`, `org.deleted`, `member.invited`, `member.joined`, `member.role_changed`, `member.removed`, `member.left`, `invitation.revoked`, `authz.denied` (sampled) | Tasks 4 and 5; amends §9.4 (Task 6) |
| §11.4 authorization matrix | Task 5 |
| §6.4 permissions for uploads, findings, AI and usage | Their routes come in Plans 4 and 5; the table already holds them (Task 2) |
| §7 `POST …/uploads` with `Idempotency-Key` | Plan 4 (the store from Task 4 is reused) |
| §6.4 "a spike raises an alert" for denials; §9.5's security dashboard | Plan 7, from Task 4's `nettriage.authz.denied` counter |
