# NetTriage Plan 4c: Triage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let members triage findings:
- change a finding's status and assignee with optimistic concurrency: `If-Match` names the version the caller read, a stale one gets `412`, a missing one `428`;
- comment on a finding;
- keep every change and comment in the finding's history (`finding_events`) and the org's audit log;
- keep assignees able to act: only owners, admins and analysts can be assigned, and a member who leaves, is removed or becomes a viewer is unassigned.

The triage queue and AI explanations are Plan 5. The web app's triage controls are Plan 6.

**Architecture:**
- `PATCH /api/v1/orgs/{org}/findings/{id}` runs one transaction as `app_api`:
  - it re-reads the caller's role under the org's lock (as every change has since Plan 3c);
  - it locks the finding's row and compares its version with `If-Match`;
  - it validates the assignee's role, and updates status and assignee in one version bump;
  - it writes `status_changed` and `assigned` events, and returns the finding with its new `ETag`.
- `POST …/comments` adds a `commented` event. It doesn't change the finding's version, so a comment never makes someone's status change stale.
- A composite foreign key ties a finding's assignee to a membership in the finding's org. Leaving, removal and demotion to viewer unassign that member's findings in the same transaction, with an event each. The key's `ON DELETE SET NULL (assignee_id)` is the backstop.
- No infrastructure changes.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2 Core with psycopg 3 (Neon Postgres 17, row-level security), Alembic.

**Spec:** `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (revision 2). Read it alongside this plan; section numbers (§) refer to it. This plan implements:
- §2's triage (status, assignee, comments) with optimistic concurrency;
- §5.2's `findings.status`, `assignee_id`, `version` and `finding_events` (`status_changed`, `assigned`, `commented`; comments of at most 2,000 characters);
- §5.4's triage rights for `app_api`;
- §6.4's `findings:triage` and `findings:comment`, with object-level checks and the test matrix;
- §7's `PATCH …/findings/{id}` (`If-Match`, 412, 428) and `POST …/comments`;
- §9.4's `finding.status_changed`, `finding.assigned` and `finding.commented`.

**Plan series:** Plan 4 of 7 ("upload pipeline") is split in three, as the owner chose on 2026-09-29:
- **4a: upload intake** (merged and deployed);
- **4b: analysis** (merged and deployed at `ea19a67`). Its plan expected `Plan: 13 to add`; the items it listed add up to 12, which the deploy showed;
- **4c (this plan): triage.**

**Branch:** `plan-4c/triage`, from `main` at `ea19a67` or later.

## Global Constraints

- **Stack.** Python **3.14**. No new dependencies. mypy `--strict` and Ruff pass on `src` and `tests`. Work test-first.
- **Commands on this Windows machine.** Run Python tools as modules (`uv run python -m …`). Host policy blocks some uv launchers and every unsigned executable outside trusted tools. Backend tests need the local Postgres, which `just test` starts (Postgres 16.2 locally; 17 in CI and on Neon).
- **Optimistic concurrency (§7).** Findings return an `ETag`. `PATCH` requires `If-Match`: a stale version gets **412 Precondition Failed**, and a missing header gets **428**.
- **Comments (§5.2).** At most **2,000** characters.
- **Permissions (§6.4).** `findings:triage` and `findings:comment`: owner, admin and analyst; not viewer. An ID in another organization is **404**.
- **No mass assignment (§6.4).** Request bodies forbid undeclared fields.
- **Logs and audit (§9.3, §9.4).** Never log a comment's text; the `finding.commented` audit event doesn't hold it either.
- **Owner-only commands.** Claude never runs `aws login`, `just store-*`, `just pause-uploads`, `just resume-uploads` or `just deploy-*`.
- **Commits.** Use Conventional Commits. Every commit made by Claude ends with the trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Decisions this plan makes

1. **Assignees (the owner's decisions, 2026-09-30):**
   - only a member who can triage (owner, admin or analyst) can be assigned. Anyone else, a viewer, a member of another org or an unknown ID, gets **422**;
   - when a member leaves or is removed, their findings in that org are unassigned in the same transaction, and each finding's history records who did it and why (`member_left`, `member_removed`);
   - **this plan adds:** a member who becomes a viewer is unassigned the same way (`role_changed`), because a viewer can't act on the finding. A change between owner, admin and analyst keeps assignments.
2. **Any status can change to any other** (the owner's decision), so a resolved finding can be reopened. Every change is in the history.
3. **`If-Match` must name exactly one version, as the API sends it** (`"3"`).
   - A missing header, `*`, a weak ETag (`W/"3"`) or a list names no version the caller read, so each gets **428** (this amends §7).
   - A **412** carries the finding's current `ETag`, so a client can re-read and retry.
4. **One transaction per change.**
   - The caller's role is re-read under the org's lock, and the finding's row is locked (`FOR UPDATE`). The version is checked before anything changes.
   - A status and an assignee sent together are one version bump.
   - A request that changes nothing (the status and assignee it asks for are already there) bumps nothing and records nothing.
5. **Comments:**
   - a comment is a `commented` event with `{"text": …}`. It doesn't change the finding's version, so it never makes a colleague's status change stale;
   - 1 to 2,000 characters after trimming; line breaks and tabs are allowed, other control characters aren't;
   - append-only: no edits or deletes, like the rest of the history;
   - `Idempotency-Key` is supported, so a double-click posts once (this amends §7). Plan 6's app sends it on every create.
6. **Audit (§9.4):**
   - `finding.status_changed` and `finding.assigned` carry `{"from", "to"}` (statuses or user IDs);
   - `finding.commented` carries no details, never the text;
   - unassignments caused by membership changes aren't audited per finding: the membership change already is, and each finding's history has the event.
7. **Database (migration 0007):**
   - `app_api` may UPDATE a finding's `status`, `assignee_id` and `version` only, and INSERT into `finding_events` with its own columns (not `created_at`). It can't change or delete an event;
   - a composite foreign key `(org_id, assignee_id)` → `memberships (org_id, user_id)` with `ON DELETE SET NULL (assignee_id)` (Postgres 15 or later) keeps assignees inside the org, even if code misses a case;
   - a partial index on `(org_id, assignee_id)` makes a member's unassignment cheap.
8. **Separate, single-purpose files:**
   - `application/triage.py` (rules);
   - `adapters/triage.py` (the transaction);
   - `adapters/assignments.py` (releasing a member's findings; `adapters/organizations.py` calls it without an import cycle);
   - `entrypoints/api/routes/triage.py` and `triage_schemas.py` (HTTP).

   `adapters/findings.py` gains `read_finding(connection, …)`, so a change returns what it wrote from inside its own transaction.
9. **No infrastructure change.** The deploy applies migration 0007; Terraform only updates the two functions' code (the API and the analyze worker share `backend.zip`; corrected after the final review).

## Review Focus

1. **Two analysts change the same finding at once, both from version 1.** One change must win. The other must get 412 with the current `ETag`, and nothing of the winner's may be overwritten. Tests: Task 2 `test_two_members_triaging_the_same_version_at_once_one_wins`, which holds the org's lock until both requests wait on it; Task 4 `test_a_stale_etag_gets_412_with_the_current_one_and_changes_nothing`.
2. **A member demoted to viewer, or removed, while their change or comment is in flight.** Nothing may change, and the answer is 403. Tests: Task 4 `test_a_change_uses_the_role_the_caller_has_now[triage-analyst-viewer-403]` and `[comment-analyst-viewer-403]`; Task 2 `test_a_viewer_can_not_triage_or_comment`.
3. **Assigning someone who can't act:** a viewer, a member of another org or a made-up ID; and an assignee who later leaves or is demoted. The first gets 422 with nothing changed; the second leaves the finding unassigned, with the reason in its history. Tests: Task 2 `test_only_a_member_who_can_triage_can_be_assigned`; Task 1 `test_an_assignee_must_be_a_member_of_the_findings_org`; Task 3's five tests.
4. **A client that sends `If-Match: *`, a weak ETag, a bare number, or none at all,** for example a hand-written script. It must get 428, never an unconditional overwrite. Tests: Task 2 `test_anything_but_one_version_etag_names_no_version`; Task 4 `test_a_change_without_if_match_is_refused_with_428` and `test_an_if_match_that_names_no_version_is_refused_with_428`.
5. **A comment that's empty, over 2,000 characters or carries control characters; one submitted twice; and a PATCH that tries to set other fields such as `severity`.** The first gets 422, the second posts once, the third gets 422. A comment's text never reaches the audit log. Tests: Task 4 `test_a_comment_is_1_to_2000_characters_without_control_characters`, `test_a_comment_joins_the_history_once_per_idempotency_key`, `test_a_change_that_says_nothing_valid_is_a_422` and `test_triage_and_comments_are_audited_without_the_comments_text`; Task 1 `test_the_api_can_not_rewrite_a_finding_or_its_history`.

## Owner prerequisites

- **Nothing is needed to build or review this plan.** Tests use the local Postgres.
- **Nothing new is needed before the deploy.** It applies migration `0007`; Terraform changes only the two functions' code.
- **To try it afterwards** (runbook B8, which Task 5 adds), you only need to be signed in to dev.

## File map

| File | Responsibility | Task |
|---|---|---|
| `migrations/versions/0007_triage.py` | `app_api`'s triage rights; assignees tied to memberships | 1 |
| `src/nettriage/application/triage.py` | `If-Match` and `ETag`s, who can be assigned, the triage errors | 2 |
| `src/nettriage/adapters/triage.py` | a status/assignee change against a version; a comment | 2 |
| `src/nettriage/adapters/findings.py` | gains `read_finding(connection, …)` | 2 |
| `src/nettriage/adapters/assignments.py` | unassigning a member's findings, with history | 3 |
| `src/nettriage/adapters/organizations.py` | calls it on leave, removal and demotion to viewer | 3 |
| `src/nettriage/entrypoints/api/triage_schemas.py`, `routes/triage.py` | the two routes | 4 |
| `src/nettriage/entrypoints/api/org_errors.py` | `412` with the current `ETag`; `422` for an invalid assignee | 4 |

Paths under `src/` and `migrations/` are in `backend/`.

---

### Task 1: The triage schema

**Files:**
- Create: `backend/migrations/versions/0007_triage.py`
- Test: `backend/tests/integration/test_triage_schema.py`

**Interfaces:**
- Consumes (Plan 4b): the `findings` and `finding_events` tables with row-level security (`org_id = app_org_id()`), `memberships (org_id, user_id)` as primary key, and the test harness's `add_tenant(engine) -> Tenant` (an org with an owner and an analyzed upload with one open finding at version 1), `add_user` and `add_member`.
- Produces:
  - `app_api` may `UPDATE (status, assignee_id, version)` on `findings`, and `INSERT (id, org_id, finding_id, actor_id, type, payload)` on `finding_events`;
  - the constraint `findings_assignee_is_a_member`: `FOREIGN KEY (org_id, assignee_id) REFERENCES memberships (org_id, user_id) ON DELETE SET NULL (assignee_id)`;
  - the index `findings_org_assignee`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/integration/test_triage_schema.py`:
```python
"""Triage in Postgres (spec §5.2, §5.4): the API may change a finding's status and assignee and
add to its history, nothing more, and an assignee is always a member of the finding's org."""

from uuid import UUID, uuid7

import pytest
from conftest import Database
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from tenantdata import Tenant, add_member, add_tenant, add_user

from nettriage.adapters.postgres import tenant_transaction


def run_as_api(database: Database, tenant: Tenant, statement: str, **params: object) -> int:
    with tenant_transaction(database.app_api, org_id=tenant.org_id) as connection:
        return connection.execute(text(statement), params).rowcount


def finding(database: Database, finding_id: UUID) -> tuple[str, UUID | None, int]:
    with database.admin.begin() as connection:
        row = connection.execute(
            text("SELECT status, assignee_id, version FROM findings WHERE id = :id"),
            {"id": finding_id},
        ).one()
    return row.status, row.assignee_id, row.version


def test_the_api_can_change_a_findings_status_assignee_and_version(database: Database) -> None:
    tenant = add_tenant(database.admin)

    changed = run_as_api(
        database,
        tenant,
        "UPDATE findings SET status = 'investigating', assignee_id = :owner, "
        "version = version + 1 WHERE id = :id",
        owner=tenant.owner_id,
        id=tenant.finding_id,
    )

    assert changed == 1
    assert finding(database, tenant.finding_id) == ("investigating", tenant.owner_id, 2)


def test_the_api_records_history_as_itself(database: Database) -> None:
    tenant = add_tenant(database.admin)

    added = run_as_api(
        database,
        tenant,
        "INSERT INTO finding_events (id, org_id, finding_id, actor_id, type, payload) "
        "VALUES (:id, :org, :finding, :actor, 'commented', '{\"text\": \"Looks benign\"}')",
        id=uuid7(),
        org=tenant.org_id,
        finding=tenant.finding_id,
        actor=tenant.owner_id,
    )

    assert added == 1


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE findings SET title = 'Nothing to see'",
        "UPDATE findings SET severity = 'low'",
        "UPDATE findings SET org_id = gen_random_uuid()",
        "DELETE FROM findings",
        "UPDATE finding_events SET payload = '{}'",
        "DELETE FROM finding_events",
        "INSERT INTO finding_events (id, org_id, finding_id, type, created_at) "
        "SELECT gen_random_uuid(), org_id, id, 'commented', now() - interval '1 day' "
        "FROM findings",
        "DELETE FROM finding_evidence",
        "INSERT INTO finding_techniques (finding_id, technique_id, source, org_id) "
        "SELECT id, 'T1595', 'ai', org_id FROM findings",
    ],
)
def test_the_api_can_not_rewrite_a_finding_or_its_history(
    database: Database, statement: str
) -> None:
    tenant = add_tenant(database.admin)

    with pytest.raises(ProgrammingError, match="permission denied"):
        run_as_api(database, tenant, statement)


def test_an_update_without_an_org_filter_changes_only_its_own_org(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    changed = run_as_api(database, mine, "UPDATE findings SET status = 'resolved'")

    assert changed == 1
    assert finding(database, theirs.finding_id)[0] == "open"


def test_an_assignee_must_be_a_member_of_the_findings_org(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    with pytest.raises(IntegrityError, match="findings_assignee_is_a_member"):
        run_as_api(
            database,
            mine,
            "UPDATE findings SET assignee_id = :outsider WHERE id = :id",
            outsider=theirs.owner_id,
            id=mine.finding_id,
        )


def test_a_membership_can_always_end_and_its_findings_are_unassigned(database: Database) -> None:
    """The API unassigns a leaver's findings itself; the foreign key is the backstop."""
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        analyst = add_user(connection)
        add_member(connection, tenant.org_id, analyst, "analyst")
        connection.execute(
            text("UPDATE findings SET assignee_id = :user WHERE id = :id"),
            {"user": analyst, "id": tenant.finding_id},
        )
        connection.execute(
            text("DELETE FROM memberships WHERE org_id = :org AND user_id = :user"),
            {"org": tenant.org_id, "user": analyst},
        )

    assert finding(database, tenant.finding_id) == ("open", None, 1)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `just test`
Expected: `5 failed, 742 passed, 1 skipped`. The failures are the five tests in `test_triage_schema.py` that need migration 0007: `test_the_api_can_change_a_findings_status_assignee_and_version`, `test_the_api_records_history_as_itself`, `test_an_update_without_an_org_filter_changes_only_its_own_org`, `test_an_assignee_must_be_a_member_of_the_findings_org` and `test_a_membership_can_always_end_and_its_findings_are_unassigned`. The 9 refusal cases pass already: they guard what the migration must not grant.

- [ ] **Step 3: Write the migration**

`backend/migrations/versions/0007_triage.py`:
```python
"""Triage (spec §5.2, §5.4, §7): members change a finding's status and assignee, and comment on
it; every change is a row in the finding's history.

- `app_api` may update a finding's status, assignee and version, and nothing else about it. It
  may add events to a finding's history, as itself, but never change or remove one.
- An assignee is a member of the finding's org: a composite foreign key to `memberships`. When a
  membership ends, the API unassigns that member's findings and records it (Plan 4c); the
  foreign key's `SET NULL (assignee_id)` is the backstop, so a membership can always go.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE findings ADD CONSTRAINT findings_assignee_is_a_member
            FOREIGN KEY (org_id, assignee_id) REFERENCES memberships (org_id, user_id)
            ON DELETE SET NULL (assignee_id);
        CREATE INDEX findings_org_assignee ON findings (org_id, assignee_id)
            WHERE assignee_id IS NOT NULL;

        GRANT UPDATE (status, assignee_id, version) ON findings TO app_api;
        GRANT INSERT (id, org_id, finding_id, actor_id, type, payload)
            ON finding_events TO app_api;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        REVOKE INSERT ON finding_events FROM app_api;
        REVOKE UPDATE ON findings FROM app_api;
        DROP INDEX findings_org_assignee;
        ALTER TABLE findings DROP CONSTRAINT findings_assignee_is_a_member;
        """
    )
```

- [ ] **Step 4: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `747 passed, 1 skipped`.

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/versions/0007_triage.py backend/tests/integration/test_triage_schema.py
git commit -m "feat(triage): let the API change a finding's status and assignee and add to its history; assignees are members" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Triaging a finding against the version the caller read

**Files:**
- Create: `backend/src/nettriage/application/triage.py`, `backend/src/nettriage/adapters/triage.py`
- Modify: `backend/src/nettriage/adapters/findings.py`
- Test: `backend/tests/unit/application/test_triage_rules.py`, `backend/tests/integration/test_triage.py`

**Interfaces:**
- Consumes:
  - Task 1's grants and foreign key;
  - Plan 3c's `lock_org_for(connection, org_id, user_id) -> Role` and `require(role, permission)`, `NotFound`, `Forbidden`, `OrgRuleError` and `allows(role, permission)`;
  - Plan 4b's `FindingDetail`, `FindingEvent`, `FindingStatus` and `get_finding`, and the harness's `APP_API_PASSWORD` and `Database.url`.
- Produces:
  - In `nettriage.application.triage`:
    - `MAX_COMMENT = 2000`;
    - `StaleVersion(OrgRuleError)` with `.current: int`, and `InvalidAssignee(OrgRuleError)`;
    - `etag(version) -> str` (`'"3"'`);
    - `expected_version(if_match: str | None) -> int | None`;
    - `can_be_assigned(role: Role | None) -> bool`.
  - In `nettriage.adapters.findings`: `read_finding(connection, org_id, finding_id) -> FindingDetail`, which `get_finding` now wraps.
  - In `nettriage.adapters.triage`:
    - `Triage(status=None, assign=False, assignee_id=None)` and `Triaged(detail, status, assignee)`, where `status` and `assignee` are `(from, to)` pairs or None;
    - `triage_finding(engine, org_id, user_id, finding_id, *, expected_version, change) -> Triaged`, which raises `NotFound`, `Forbidden`, `StaleVersion` and `InvalidAssignee`;
    - `add_comment(engine, org_id, user_id, finding_id, comment) -> FindingEvent`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/unit/application/test_triage_rules.py`:
```python
"""Triage rules (spec §7): which `If-Match` headers name a version, and who can be assigned."""

import pytest

from nettriage.application.triage import can_be_assigned, etag, expected_version


def test_an_etag_is_the_version_in_quotes() -> None:
    assert etag(3) == '"3"'
    assert expected_version(etag(3)) == 3
    assert expected_version(' "12" ') == 12


@pytest.mark.parametrize(
    "header",
    [None, "", "*", "3", 'W/"3"', '"03"', '"0"', '"3", "4"', '"three"', '"12345678901"'],
)
def test_anything_but_one_version_etag_names_no_version(header: str | None) -> None:
    assert expected_version(header) is None


@pytest.mark.parametrize(
    ("role", "assignable"),
    [("owner", True), ("admin", True), ("analyst", True), ("viewer", False), (None, False)],
)
def test_only_a_member_who_can_triage_can_be_assigned(role: str | None, assignable: bool) -> None:
    assert can_be_assigned(role) is assignable  # type: ignore[arg-type]
```

`backend/tests/integration/test_triage.py`:
```python
"""Triaging findings (spec §7), as `app_api`: status and assignee changes against the version the
caller read, comments, and the history they leave."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid7

import pytest
from conftest import APP_API_PASSWORD, Database
from sqlalchemy import Engine, text
from tenantdata import Tenant, add_member, add_tenant, add_user

from nettriage.adapters.findings import FindingStatus, get_finding
from nettriage.adapters.postgres import create_database_engine
from nettriage.adapters.triage import Triage, Triaged, add_comment, triage_finding
from nettriage.application.organizations import Forbidden, NotFound
from nettriage.application.triage import InvalidAssignee, StaleVersion


def member(database: Database, tenant: Tenant, role: str) -> UUID:
    with database.admin.begin() as connection:
        user = add_user(connection)
        add_member(connection, tenant.org_id, user, role)
    return user


def triage(
    database: Database,
    tenant: Tenant,
    change: Triage,
    *,
    version: int = 1,
    user: UUID | None = None,
) -> Triaged:
    return triage_finding(
        database.app_api,
        tenant.org_id,
        user or tenant.owner_id,
        tenant.finding_id,
        expected_version=version,
        change=change,
    )


def history(database: Database, tenant: Tenant) -> list[tuple[str, UUID | None, dict[str, object]]]:
    detail = get_finding(database.app_api, tenant.org_id, tenant.owner_id, tenant.finding_id)
    return [(event.type, event.actor_id, event.payload) for event in detail.events]


def test_a_status_change_bumps_the_version_and_is_recorded(database: Database) -> None:
    tenant = add_tenant(database.admin)

    triaged = triage(database, tenant, Triage(status="investigating"))

    assert (triaged.detail.finding.status, triaged.detail.finding.version) == ("investigating", 2)
    assert (triaged.status, triaged.assignee) == (("open", "investigating"), None)
    assert history(database, tenant)[1:] == [
        ("status_changed", tenant.owner_id, {"from": "open", "to": "investigating"})
    ]


def test_a_finding_is_assigned_to_an_analyst_and_unassigned_again(database: Database) -> None:
    tenant = add_tenant(database.admin)
    analyst = member(database, tenant, "analyst")

    assigned = triage(database, tenant, Triage(assign=True, assignee_id=analyst))
    unassigned = triage(database, tenant, Triage(assign=True, assignee_id=None), version=2)

    assert assigned.detail.finding.assignee_id == analyst
    assert (unassigned.detail.finding.assignee_id, unassigned.detail.finding.version) == (None, 3)
    assert [payload for kind, _, payload in history(database, tenant) if kind == "assigned"] == [
        {"from": None, "to": str(analyst)},
        {"from": str(analyst), "to": None},
    ]


def test_status_and_assignee_change_together_in_one_version(database: Database) -> None:
    tenant = add_tenant(database.admin)

    triaged = triage(
        database, tenant, Triage(status="resolved", assign=True, assignee_id=tenant.owner_id)
    )

    assert triaged.detail.finding.version == 2
    assert [kind for kind, _, _ in history(database, tenant)] == [
        "created",
        "status_changed",
        "assigned",
    ]


def test_a_stale_version_is_refused_and_changes_nothing(database: Database) -> None:
    tenant = add_tenant(database.admin)
    triage(database, tenant, Triage(status="investigating"))

    with pytest.raises(StaleVersion) as stale:
        triage(database, tenant, Triage(status="false_positive"), version=1)

    assert stale.value.current == 2
    finding = get_finding(database.app_api, tenant.org_id, tenant.owner_id, tenant.finding_id)
    assert (finding.finding.status, finding.finding.version) == ("investigating", 2)


def test_two_members_triaging_the_same_version_at_once_one_wins(database: Database) -> None:
    """Both read version 1. The org's lock is held until both are waiting on it, so they
    overlap for certain: the first to get it wins, the second finds version 2."""
    tenant = add_tenant(database.admin)
    analyst = member(database, tenant, "analyst")
    second = create_database_engine(
        database.url.set(username="app_api", password=APP_API_PASSWORD).render_as_string(
            hide_password=False
        ),
        pool_size=1,
    )
    started = threading.Barrier(3)

    def attempt(engine: Engine, user: UUID, status: FindingStatus) -> str:
        started.wait()
        try:
            triage_finding(
                engine,
                tenant.org_id,
                user,
                tenant.finding_id,
                expected_version=1,
                change=Triage(status=status),
            )
        except StaleVersion:
            return "stale"
        return "won"

    with database.admin.begin() as holder:
        holder.execute(
            text("SELECT id FROM organizations WHERE id = :org FOR UPDATE"), {"org": tenant.org_id}
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(attempt, database.app_api, tenant.owner_id, "resolved")
            other = pool.submit(attempt, second, analyst, "false_positive")
            started.wait()
            time.sleep(0.5)  # both are now blocked on the org's lock
            holder.commit()
        outcomes = sorted([first.result(), other.result()])
    second.dispose()

    assert outcomes == ["stale", "won"]
    assert [kind for kind, _, _ in history(database, tenant)].count("status_changed") == 1


def test_asking_for_what_is_already_there_changes_nothing(database: Database) -> None:
    tenant = add_tenant(database.admin)

    triaged = triage(database, tenant, Triage(status="open", assign=True, assignee_id=None))

    assert (triaged.status, triaged.assignee, triaged.detail.finding.version) == (None, None, 1)
    assert [kind for kind, _, _ in history(database, tenant)] == ["created"]


@pytest.mark.parametrize("who", ["viewer", "outsider", "unknown"])
def test_only_a_member_who_can_triage_can_be_assigned(database: Database, who: str) -> None:
    tenant = add_tenant(database.admin)
    if who == "viewer":
        assignee = member(database, tenant, "viewer")
    elif who == "outsider":
        assignee = add_tenant(database.admin).owner_id
    else:
        assignee = uuid7()

    with pytest.raises(InvalidAssignee):
        triage(database, tenant, Triage(assign=True, assignee_id=assignee))

    assert [kind for kind, _, _ in history(database, tenant)] == ["created"]


def test_a_viewer_can_not_triage_or_comment(database: Database) -> None:
    tenant = add_tenant(database.admin)
    viewer = member(database, tenant, "viewer")

    with pytest.raises(Forbidden):
        triage(database, tenant, Triage(status="resolved"), user=viewer)
    with pytest.raises(Forbidden):
        add_comment(database.app_api, tenant.org_id, viewer, tenant.finding_id, "Benign")


def test_a_finding_of_another_org_is_not_found(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    with pytest.raises(NotFound):
        triage_finding(
            database.app_api,
            mine.org_id,
            mine.owner_id,
            theirs.finding_id,
            expected_version=1,
            change=Triage(status="resolved"),
        )
    with pytest.raises(NotFound):
        add_comment(database.app_api, mine.org_id, mine.owner_id, theirs.finding_id, "Mine now")


def test_a_comment_joins_the_history_without_changing_the_version(database: Database) -> None:
    tenant = add_tenant(database.admin)
    analyst = member(database, tenant, "analyst")

    event = add_comment(
        database.app_api, tenant.org_id, analyst, tenant.finding_id, "Scanner from our pentest."
    )

    assert (event.type, event.actor_id, event.payload) == (
        "commented",
        analyst,
        {"text": "Scanner from our pentest."},
    )
    detail = get_finding(database.app_api, tenant.org_id, tenant.owner_id, tenant.finding_id)
    assert detail.finding.version == 1
    assert detail.events[-1].id == event.id
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/unit/application/test_triage_rules.py tests/integration/test_triage.py`
Expected: FAIL. Collection stops with 2 errors: `No module named 'nettriage.application.triage'` and `No module named 'nettriage.adapters.triage'`.

- [ ] **Step 3: Write the rules**

`backend/src/nettriage/application/triage.py`:
```python
"""Triage rules (spec §7): a finding's status and assignee change only against the version the
caller last read (optimistic concurrency), and only a member who can triage (owner, admin or
analyst) can be its assignee (the owner's decision, Plan 4c)."""

from __future__ import annotations

import re

from nettriage.application.organizations import OrgRuleError
from nettriage.application.permissions import Role, allows

MAX_COMMENT = 2000
# The ETags this API sends: the finding's version in quotes, such as "3".
_ETAG = re.compile(r'"([1-9][0-9]{0,9})"')


class StaleVersion(OrgRuleError):
    """The finding changed since the caller read it: someone else triaged it first."""

    def __init__(self, current: int) -> None:
        super().__init__("This finding changed since you read it. Reload it and try again.")
        self.current = current


class InvalidAssignee(OrgRuleError):
    """Only a member who can triage the finding can be its assignee."""


def etag(version: int) -> str:
    return f'"{version}"'


def expected_version(if_match: str | None) -> int | None:
    """The version an `If-Match` header names: exactly one ETag as this API sends it. None for a
    missing header, `*`, a weak or malformed ETag, or a list: none of them says which version
    the caller read."""
    if if_match is None:
        return None
    match = _ETAG.fullmatch(if_match.strip())
    return int(match[1]) if match else None


def can_be_assigned(role: Role | None) -> bool:
    return role is not None and allows(role, "findings:triage")
```

- [ ] **Step 4: Read a finding inside a transaction, and write the triage adapter**

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        row = connection.execute(
            text(
                f"SELECT {_SUMMARY}, f.metrics FROM findings f "  # noqa: S608
                "WHERE f.org_id = :org AND f.id = :id"
            ),
            {"org": org_id, "id": finding_id},
        ).one_or_none()
        if row is None:
            raise NotFound("No such finding.")
        return FindingDetail(
            finding=_summary(row),
            metrics=row.metrics,
            evidence=_evidence(connection, finding_id),
            techniques=_techniques(connection, finding_id),
            events=_events(connection, finding_id),
        )

```
with:
```python
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        return read_finding(connection, org_id, finding_id)


def read_finding(connection: Connection, org_id: UUID, finding_id: UUID) -> FindingDetail:
    """One finding in full, inside the caller's transaction (triage returns what it wrote)."""
    row = connection.execute(
        text(
            f"SELECT {_SUMMARY}, f.metrics FROM findings f "  # noqa: S608
            "WHERE f.org_id = :org AND f.id = :id"
        ),
        {"org": org_id, "id": finding_id},
    ).one_or_none()
    if row is None:
        raise NotFound("No such finding.")
    return FindingDetail(
        finding=_summary(row),
        metrics=row.metrics,
        evidence=_evidence(connection, finding_id),
        techniques=_techniques(connection, finding_id),
        events=_events(connection, finding_id),
    )

```

`backend/src/nettriage/adapters/triage.py`:
```python
"""Triaging a finding (spec §7), as `app_api` in the org's transaction: change its status or
assignee against the version the caller read, or comment on it. Every change is an event in the
finding's history, with the caller as its actor.

Like every change since Plan 3c, the caller's role is re-read under the org's lock, and the
finding's row is locked too, so two members triaging at once can't both win: the second gets
`StaleVersion`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid7

from sqlalchemy import Connection, Engine, text

from nettriage.adapters.findings import FindingDetail, FindingEvent, FindingStatus, read_finding
from nettriage.adapters.organizations import lock_org_for
from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.organizations import NotFound, require
from nettriage.application.permissions import Role
from nettriage.application.triage import InvalidAssignee, StaleVersion, can_be_assigned


@dataclass(frozen=True)
class Triage:
    """What to change. `status` None leaves it as it is; `assign` sets the assignee to
    `assignee_id`, and `assignee_id` None then unassigns."""

    status: FindingStatus | None = None
    assign: bool = False
    assignee_id: UUID | None = None


@dataclass(frozen=True)
class Triaged:
    """The finding as it now is, and what changed: (from, to) pairs, or None."""

    detail: FindingDetail
    status: tuple[str, str] | None
    assignee: tuple[UUID | None, UUID | None] | None


def triage_finding(
    engine: Engine,
    org_id: UUID,
    user_id: UUID,
    finding_id: UUID,
    *,
    expected_version: int,
    change: Triage,
) -> Triaged:
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        require(lock_org_for(connection, org_id, user_id), "findings:triage")
        row = connection.execute(
            text(
                "SELECT status, assignee_id, version FROM findings "
                "WHERE org_id = :org AND id = :id FOR UPDATE"
            ),
            {"org": org_id, "id": finding_id},
        ).one_or_none()
        if row is None:
            raise NotFound("No such finding.")
        if row.version != expected_version:
            raise StaleVersion(row.version)
        status = None
        if change.status is not None and change.status != row.status:
            status = (row.status, change.status)
        assignee = None
        if change.assign and change.assignee_id != row.assignee_id:
            if change.assignee_id is not None and not can_be_assigned(
                _role(connection, org_id, change.assignee_id)
            ):
                raise InvalidAssignee(
                    "Assign the finding to an owner, admin or analyst of this organization."
                )
            assignee = (row.assignee_id, change.assignee_id)
        if status is not None or assignee is not None:
            connection.execute(
                text(
                    "UPDATE findings SET status = :status, assignee_id = :assignee, "
                    "version = version + 1 WHERE org_id = :org AND id = :id"
                ),
                {
                    "status": status[1] if status else row.status,
                    "assignee": assignee[1] if assignee else row.assignee_id,
                    "org": org_id,
                    "id": finding_id,
                },
            )
        if status is not None:
            _record(connection, org_id, finding_id, user_id, "status_changed", _pair(status))
        if assignee is not None:
            _record(connection, org_id, finding_id, user_id, "assigned", _pair(assignee))
        return Triaged(read_finding(connection, org_id, finding_id), status, assignee)


def add_comment(
    engine: Engine, org_id: UUID, user_id: UUID, finding_id: UUID, comment: str
) -> FindingEvent:
    """A comment is an event in the finding's history. It doesn't change the finding's version,
    so it never makes someone else's status change stale."""
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        require(lock_org_for(connection, org_id, user_id), "findings:comment")
        exists = connection.execute(
            text("SELECT 1 FROM findings WHERE org_id = :org AND id = :id"),
            {"org": org_id, "id": finding_id},
        ).scalar_one_or_none()
        if exists is None:
            raise NotFound("No such finding.")
        return _record(connection, org_id, finding_id, user_id, "commented", {"text": comment})


def _record(
    connection: Connection,
    org_id: UUID,
    finding_id: UUID,
    actor_id: UUID,
    event_type: str,
    payload: dict[str, Any],
) -> FindingEvent:
    row = connection.execute(
        text(
            "INSERT INTO finding_events (id, org_id, finding_id, actor_id, type, payload) "
            "VALUES (:id, :org, :finding, :actor, :type, CAST(:payload AS jsonb)) "
            "RETURNING id, type, actor_id, payload, created_at"
        ),
        {
            "id": uuid7(),
            "org": org_id,
            "finding": finding_id,
            "actor": actor_id,
            "type": event_type,
            "payload": json.dumps(payload),
        },
    ).one()
    return FindingEvent(
        id=row.id,
        type=row.type,
        actor_id=row.actor_id,
        payload=row.payload,
        created_at=row.created_at,
    )


def _pair(change: tuple[Any, Any]) -> dict[str, Any]:
    before, after = change
    return {
        "from": str(before) if before is not None else None,
        "to": str(after) if after is not None else None,
    }


def _role(connection: Connection, org_id: UUID, user_id: UUID) -> Role | None:
    role: Role | None = connection.execute(
        text("SELECT role FROM memberships WHERE org_id = :org AND user_id = :user"),
        {"org": org_id, "user": user_id},
    ).scalar_one_or_none()
    return role
```

- [ ] **Step 5: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `775 passed, 1 skipped`.

- [ ] **Step 6: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(triage): change a finding's status and assignee against the version read, and comment, with history" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Assignments follow membership

**Files:**
- Create: `backend/src/nettriage/adapters/assignments.py`
- Modify: `backend/src/nettriage/adapters/organizations.py`
- Test: `backend/tests/integration/test_assignments.py`

**Interfaces:**
- Consumes: Task 1's grants; Task 2's `Triage` and `triage_finding`; Plan 3c's `change_role(engine, org_id, *, actor_id, target_id, role)` and `remove_member(engine, org_id, *, actor_id, target_id)`, whose transactions run as `app_api` with the org set; `add_finding(connection, org_id, upload_id)`.
- Produces: `nettriage.adapters.assignments.release_assignments(connection, org_id, member_id, *, actor_id, reason) -> int`, where `reason` is `"member_left"`, `"member_removed"` or `"role_changed"`. Each released finding gets `assignee_id = NULL`, `version + 1` and an `assigned` event `{"from": <member>, "to": null, "reason": …}` with `actor_id` as its actor.

- [ ] **Step 1: Write the failing tests**

`backend/tests/integration/test_assignments.py`:
```python
"""A finding's assignee can always triage it (the owner's decisions, Plan 4c): a member who
leaves, is removed or becomes a viewer is unassigned from the org's findings, and each finding's
history records who did it and why."""

from uuid import UUID

from conftest import Database
from sqlalchemy import text
from tenantdata import Tenant, add_finding, add_member, add_tenant, add_user

from nettriage.adapters.findings import get_finding
from nettriage.adapters.organizations import change_role, remove_member
from nettriage.adapters.triage import Triage, triage_finding


def assigned_analyst(database: Database, tenant: Tenant) -> UUID:
    with database.admin.begin() as connection:
        analyst = add_user(connection)
        add_member(connection, tenant.org_id, analyst, "analyst")
    triage_finding(
        database.app_api,
        tenant.org_id,
        tenant.owner_id,
        tenant.finding_id,
        expected_version=1,
        change=Triage(assign=True, assignee_id=analyst),
    )
    return analyst


def state(database: Database, tenant: Tenant) -> tuple[UUID | None, int, dict[str, object], UUID]:
    """The finding's assignee and version, and its latest event's payload and actor."""
    detail = get_finding(database.app_api, tenant.org_id, tenant.owner_id, tenant.finding_id)
    last = detail.events[-1]
    assert last.actor_id is not None
    return detail.finding.assignee_id, detail.finding.version, last.payload, last.actor_id


def test_a_member_who_leaves_is_unassigned(database: Database) -> None:
    tenant = add_tenant(database.admin)
    analyst = assigned_analyst(database, tenant)

    remove_member(database.app_api, tenant.org_id, actor_id=analyst, target_id=analyst)

    assert state(database, tenant) == (
        None,
        3,
        {"from": str(analyst), "to": None, "reason": "member_left"},
        analyst,
    )


def test_a_removed_member_is_unassigned_by_whoever_removed_them(database: Database) -> None:
    tenant = add_tenant(database.admin)
    analyst = assigned_analyst(database, tenant)

    remove_member(database.app_api, tenant.org_id, actor_id=tenant.owner_id, target_id=analyst)

    assert state(database, tenant) == (
        None,
        3,
        {"from": str(analyst), "to": None, "reason": "member_removed"},
        tenant.owner_id,
    )


def test_a_member_who_becomes_a_viewer_is_unassigned(database: Database) -> None:
    tenant = add_tenant(database.admin)
    analyst = assigned_analyst(database, tenant)

    change_role(
        database.app_api, tenant.org_id, actor_id=tenant.owner_id, target_id=analyst, role="viewer"
    )

    assert state(database, tenant) == (
        None,
        3,
        {"from": str(analyst), "to": None, "reason": "role_changed"},
        tenant.owner_id,
    )


def test_a_role_that_can_still_triage_keeps_its_assignments(database: Database) -> None:
    tenant = add_tenant(database.admin)
    analyst = assigned_analyst(database, tenant)

    change_role(
        database.app_api, tenant.org_id, actor_id=tenant.owner_id, target_id=analyst, role="admin"
    )

    assert state(database, tenant)[:2] == (analyst, 2)


def test_leaving_one_org_keeps_assignments_in_another(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)
    analyst = assigned_analyst(database, mine)
    with database.admin.begin() as connection:
        add_member(connection, theirs.org_id, analyst, "analyst")
        other = add_finding(connection, theirs.org_id, theirs.upload_id)
        connection.execute(
            text("UPDATE findings SET assignee_id = :user WHERE id = :id"),
            {"user": analyst, "id": other},
        )

    remove_member(database.app_api, mine.org_id, actor_id=analyst, target_id=analyst)

    with database.admin.begin() as connection:
        kept: UUID = connection.execute(
            text("SELECT assignee_id FROM findings WHERE id = :id"), {"id": other}
        ).scalar_one()
    assert kept == analyst
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/integration/test_assignments.py`
Expected: `3 failed, 2 passed`: `test_a_member_who_leaves_is_unassigned`, `test_a_removed_member_is_unassigned_by_whoever_removed_them` and `test_a_member_who_becomes_a_viewer_is_unassigned`. Migration 0007's foreign key already unassigns a leaver, but without a new version or a history event; nothing unassigns a viewer yet. The two cases that keep assignments pass already.

- [ ] **Step 3: Release a member's findings**

`backend/src/nettriage/adapters/assignments.py`:
```python
"""A finding's assignee can always triage it (the owner's decisions, Plan 4c). When a member
leaves, is removed or becomes a viewer, their findings in that org are unassigned, inside the
same transaction as the membership change, and each finding's history records who did it and
why. The foreign key's `SET NULL` (migration 0007) is only the backstop."""

from __future__ import annotations

import json
from typing import Literal
from uuid import UUID, uuid7

from sqlalchemy import Connection, text

type Reason = Literal["member_left", "member_removed", "role_changed"]


def release_assignments(
    connection: Connection, org_id: UUID, member_id: UUID, *, actor_id: UUID, reason: Reason
) -> int:
    """Unassign the member's findings in the org. Returns how many there were."""
    released: list[UUID] = list(
        connection.execute(
            text(
                "UPDATE findings SET assignee_id = NULL, version = version + 1 "
                "WHERE org_id = :org AND assignee_id = :member RETURNING id"
            ),
            {"org": org_id, "member": member_id},
        ).scalars()
    )
    if released:
        payload = json.dumps({"from": str(member_id), "to": None, "reason": reason})
        connection.execute(
            text(
                "INSERT INTO finding_events (id, org_id, finding_id, actor_id, type, payload) "
                "VALUES (:id, :org, :finding, :actor, 'assigned', CAST(:payload AS jsonb))"
            ),
            [
                {
                    "id": uuid7(),
                    "org": org_id,
                    "finding": finding_id,
                    "actor": actor_id,
                    "payload": payload,
                }
                for finding_id in released
            ],
        )
    return len(released)
```

- [ ] **Step 4: Release them on leave, removal and demotion to viewer**

In `backend/src/nettriage/adapters/organizations.py`, replace:
```python
from sqlalchemy.exc import IntegrityError

from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.organizations import (
```
with:
```python
from sqlalchemy.exc import IntegrityError

from nettriage.adapters.assignments import release_assignments
from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.organizations import (
```

In `backend/src/nettriage/adapters/organizations.py`, replace:
```python
            {"role": role, "org": org_id, "user": target_id},
        )
        row = connection.execute(
            text(
```
with:
```python
            {"role": role, "org": org_id, "user": target_id},
        )
        if not allows(role, "findings:triage"):
            release_assignments(
                connection, org_id, target_id, actor_id=actor_id, reason="role_changed"
            )
        row = connection.execute(
            text(
```

In `backend/src/nettriage/adapters/organizations.py`, replace:
```python
        if current == "owner":
            _keep_an_owner(connection, org_id)
        connection.execute(
            text("DELETE FROM memberships WHERE org_id = :org AND user_id = :user"),
```
with:
```python
        if current == "owner":
            _keep_an_owner(connection, org_id)
        release_assignments(
            connection,
            org_id,
            target_id,
            actor_id=actor_id,
            reason="member_left" if leaving else "member_removed",
        )
        connection.execute(
            text("DELETE FROM memberships WHERE org_id = :org AND user_id = :user"),
```

- [ ] **Step 5: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `780 passed, 1 skipped`.

- [ ] **Step 6: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(triage): unassign a member's findings when they leave, are removed or become a viewer" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The triage routes

**Files:**
- Create: `backend/src/nettriage/entrypoints/api/triage_schemas.py`, `backend/src/nettriage/entrypoints/api/routes/triage.py`
- Modify: `backend/src/nettriage/entrypoints/api/org_errors.py`, `backend/src/nettriage/entrypoints/api/app.py`
- Test: `backend/tests/api/test_triage_routes.py`, `backend/tests/security/test_route_access.py`, `backend/tests/security/test_authorization_matrix.py`, `backend/tests/security/test_stale_roles.py`

**Interfaces:**
- Consumes:
  - Task 2's `triage_finding`, `add_comment`, `Triage`, `etag`, `expected_version`, `MAX_COMMENT`, `StaleVersion` and `InvalidAssignee`;
  - Plan 3c's `OrgMember`, `org_rules`, `audit`, `Strict`;
  - Plan 4a's `create_once` and `StoredResponse`;
  - Plan 4b's `FindingOut.of_detail` and `FindingEventOut.of`.
- Produces:
  - `PATCH /api/v1/orgs/{org_id}/findings/{finding_id}` (`findings:triage`): body `{"status"?, "assignee_id"?}` with `If-Match`; `200` with the finding and its new `ETag`; `412` with the current `ETag`; `428`; `422`.
  - `POST /api/v1/orgs/{org_id}/findings/{finding_id}/comments` (`findings:comment`): body `{"text"}`; `201` with the event; `Idempotency-Key` supported.
  - Audit events `finding.status_changed`, `finding.assigned` and `finding.commented`.
  - The authorization matrix gains `EXTRA_HEADERS`, headers an endpoint needs besides the session's.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/security/test_route_access.py`, replace:
```python
    ("GET", "/api/v1/orgs/{org_id}/findings/{finding_id}"): "findings:read",
    ("GET", "/api/v1/attack-techniques/{technique_id}"): "signed in",
```
with:
```python
    ("GET", "/api/v1/orgs/{org_id}/findings/{finding_id}"): "findings:read",
    ("PATCH", "/api/v1/orgs/{org_id}/findings/{finding_id}"): "findings:triage",
    ("POST", "/api/v1/orgs/{org_id}/findings/{finding_id}/comments"): "findings:comment",
    ("GET", "/api/v1/attack-techniques/{technique_id}"): "signed in",
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
    "read technique": ("GET", "/api/v1/attack-techniques/{technique}", None),
}

```
with:
```python
    "read technique": ("GET", "/api/v1/attack-techniques/{technique}", None),
    "triage finding": (
        "PATCH",
        "/api/v1/orgs/{org}/findings/{finding}",
        {"status": "investigating"},
    ),
    "comment on finding": (
        "POST",
        "/api/v1/orgs/{org}/findings/{finding}/comments",
        {"text": "Looks like our scanner."},
    ),
}
# Headers an endpoint needs besides the session's: a triage change names the version it read.
EXTRA_HEADERS: dict[str, dict[str, str]] = {"triage finding": {"If-Match": '"1"'}}

```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
    "read technique": (200, 200, 200, 200, 200, 401),
}
```
with:
```python
    "read technique": (200, 200, 200, 200, 200, 401),
    "triage finding": (200, 200, 200, 403, 404, 401),
    "comment on finding": (201, 201, 201, 403, 404, 401),
}
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
        headers = signed_in_as(database_client, services.sessions, world.people[caller], clock())

```
with:
```python
        headers = signed_in_as(database_client, services.sessions, world.people[caller], clock())
    headers |= EXTRA_HEADERS.get(endpoint, {})

```

A caller demoted to viewer while their change is in flight changes nothing:

In `backend/tests/security/test_stale_roles.py`, replace:
```python
from sqlalchemy import text
from tenantdata import add_invitation, add_member, add_org, add_user

```
with:
```python
from sqlalchemy import text
from tenantdata import add_finding, add_invitation, add_member, add_org, add_upload, add_user

```

In `backend/tests/security/test_stale_roles.py`, replace:
```python
    """Everything a change could touch: the name, the members and their roles, the pending
    invitations and the uploads."""
    with database.admin.begin() as connection:
```
with:
```python
    """Everything a change could touch: the name, the members and their roles, the pending
    invitations, the uploads, and the findings with their history."""
    with database.admin.begin() as connection:
```

In `backend/tests/security/test_stale_roles.py`, replace:
```python
        ).all()
    return name, tuple(members), tuple(invitations), tuple(uploads)

```
with:
```python
        ).all()
        findings = connection.execute(
            text(
                "SELECT id, status, assignee_id, version FROM findings WHERE org_id = :org "
                "ORDER BY id"
            ),
            {"org": org},
        ).all()
        events = connection.execute(
            text("SELECT id FROM finding_events WHERE org_id = :org ORDER BY id"), {"org": org}
        ).all()
    return (
        name,
        tuple(members),
        tuple(invitations),
        tuple(uploads),
        tuple(findings),
        tuple(events),
    )

```

In `backend/tests/security/test_stale_roles.py`, replace:
```python
        ("upload", "analyst", "viewer", 403),
    ],
```
with:
```python
        ("upload", "analyst", "viewer", 403),
        ("triage", "analyst", "viewer", 403),
        ("comment", "analyst", "viewer", 403),
    ],
```

In `backend/tests/security/test_stale_roles.py`, replace:
```python
        invitation = add_invitation(connection, org, owner, f"{uuid4().hex}@example.com")
    monkeypatch.setattr(access, "role_of", lambda engine, org_id, user_id: then)
```
with:
```python
        invitation = add_invitation(connection, org, owner, f"{uuid4().hex}@example.com")
        finding = add_finding(connection, org, add_upload(connection, org, owner, "analyzed"))
    monkeypatch.setattr(access, "role_of", lambda engine, org_id, user_id: then)
```

In `backend/tests/security/test_stale_roles.py`, replace:
```python
        ),
    }
    method, path, body = requests[change]
    before = snapshot(database, org)
```
with:
```python
        ),
        "triage": (
            "PATCH",
            f"/api/v1/orgs/{org}/findings/{finding}",
            {"status": "false_positive", "assignee_id": str(analyst)},
        ),
        "comment": ("POST", f"/api/v1/orgs/{org}/findings/{finding}/comments", {"text": "Mine"}),
    }
    method, path, body = requests[change]
    if change == "triage":
        headers = {**headers, "If-Match": '"1"'}
    before = snapshot(database, org)
```

`backend/tests/api/test_triage_routes.py`:
```python
"""Triage through the API (spec §7): a finding's status and assignee change only with `If-Match`
naming the version the caller read, comments join its history, and each is audited."""

from typing import Any
from uuid import UUID

import pytest
from browser import signed_in_as
from conftest import Database, FakeClock
from fastapi.testclient import TestClient
from sqlalchemy import text
from tenantdata import add_finding, add_member, add_org, add_upload, add_user

from nettriage.entrypoints.api.services import Services


@pytest.fixture
def world(database: Database) -> dict[str, UUID]:
    """An org with an owner, an analyst and a viewer, and one open finding."""
    with database.admin.begin() as connection:
        owner, analyst, viewer = (add_user(connection) for _ in range(3))
        org = add_org(connection, owner)
        add_member(connection, org, owner, "owner")
        add_member(connection, org, analyst, "analyst")
        add_member(connection, org, viewer, "viewer")
        finding = add_finding(connection, org, add_upload(connection, org, owner, "analyzed"))
    return {"org": org, "owner": owner, "analyst": analyst, "viewer": viewer, "finding": finding}


@pytest.fixture
def headers(
    database_client: TestClient, services: Services, clock: FakeClock, world: dict[str, UUID]
) -> dict[str, str]:
    return signed_in_as(database_client, services.sessions, world["analyst"], clock())


def url(world: dict[str, UUID], suffix: str = "") -> str:
    return f"/api/v1/orgs/{world['org']}/findings/{world['finding']}{suffix}"


def patch(
    client: TestClient,
    world: dict[str, UUID],
    headers: dict[str, str],
    body: dict[str, Any],
    if_match: str | None = '"1"',
) -> Any:
    sent = headers if if_match is None else {**headers, "If-Match": if_match}
    return client.patch(url(world), json=body, headers=sent)


def audited(database: Database, org: UUID) -> list[tuple[str, dict[str, Any]]]:
    with database.admin.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT action, details FROM audit_log WHERE org_id = :org AND action LIKE "
                "'finding.%' ORDER BY created_at, id"
            ),
            {"org": org},
        ).all()
    return [(row.action, row.details) for row in rows]


def test_a_status_change_returns_the_finding_with_its_new_etag(
    database_client: TestClient, headers: dict[str, str], world: dict[str, UUID]
) -> None:
    etag = database_client.get(url(world)).headers["etag"]

    response = patch(database_client, world, headers, {"status": "investigating"}, etag)

    assert response.status_code == 200, response.text
    assert response.headers["etag"] == '"2"'
    finding = response.json()
    assert (finding["status"], finding["version"]) == ("investigating", 2)
    assert [(event["type"], event["payload"]) for event in finding["events"]][-1] == (
        "status_changed",
        {"from": "open", "to": "investigating"},
    )


def test_a_change_without_if_match_is_refused_with_428(
    database_client: TestClient, headers: dict[str, str], world: dict[str, UUID]
) -> None:
    response = patch(database_client, world, headers, {"status": "resolved"}, if_match=None)

    assert response.status_code == 428
    assert response.headers["content-type"] == "application/problem+json"
    assert "If-Match" in response.json()["detail"]


@pytest.mark.parametrize("if_match", ["*", 'W/"1"', "1", '"1", "2"'])
def test_an_if_match_that_names_no_version_is_refused_with_428(
    database_client: TestClient, headers: dict[str, str], world: dict[str, UUID], if_match: str
) -> None:
    response = patch(database_client, world, headers, {"status": "resolved"}, if_match)

    assert response.status_code == 428


def test_a_stale_etag_gets_412_with_the_current_one_and_changes_nothing(
    database_client: TestClient, headers: dict[str, str], world: dict[str, UUID]
) -> None:
    patch(database_client, world, headers, {"status": "investigating"})

    stale = patch(database_client, world, headers, {"status": "false_positive"})

    assert stale.status_code == 412
    assert stale.headers["etag"] == '"2"'
    assert "changed since you read it" in stale.json()["detail"]
    assert database_client.get(url(world)).json()["status"] == "investigating"


def test_a_finding_is_assigned_and_unassigned(
    database_client: TestClient, headers: dict[str, str], world: dict[str, UUID]
) -> None:
    assigned = patch(database_client, world, headers, {"assignee_id": str(world["owner"])})
    unassigned = patch(database_client, world, headers, {"assignee_id": None}, '"2"')

    assert assigned.json()["assignee_id"] == str(world["owner"])
    assert (unassigned.json()["assignee_id"], unassigned.headers["etag"]) == (None, '"3"')


def test_a_viewer_can_not_be_the_assignee(
    database_client: TestClient, headers: dict[str, str], world: dict[str, UUID]
) -> None:
    response = patch(database_client, world, headers, {"assignee_id": str(world["viewer"])})

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Assign the finding to an owner, admin or analyst of this organization."
    )


@pytest.mark.parametrize(
    "body",
    [{}, {"status": None}, {"status": "closed"}, {"severity": "low"}, {"assignee_id": "someone"}],
)
def test_a_change_that_says_nothing_valid_is_a_422(
    database_client: TestClient,
    headers: dict[str, str],
    world: dict[str, UUID],
    body: dict[str, Any],
) -> None:
    response = patch(database_client, world, headers, body)

    assert response.status_code == 422


def test_a_comment_joins_the_history_once_per_idempotency_key(
    database_client: TestClient, headers: dict[str, str], world: dict[str, UUID]
) -> None:
    keyed = {**headers, "Idempotency-Key": "comment-1"}
    body = {"text": "  Our weekly scanner.\nSafe to close.  "}

    first = database_client.post(url(world, "/comments"), json=body, headers=keyed)
    retry = database_client.post(url(world, "/comments"), json=body, headers=keyed)

    assert first.status_code == 201, first.text
    assert retry.json() == first.json()
    comment = first.json()
    assert (comment["type"], comment["actor_id"]) == ("commented", str(world["analyst"]))
    assert comment["payload"] == {"text": "Our weekly scanner.\nSafe to close."}
    detail = database_client.get(url(world))
    assert [event["type"] for event in detail.json()["events"]] == ["created", "commented"]
    assert detail.headers["etag"] == '"1"'


@pytest.mark.parametrize("text_", ["", "   ", "x" * 2001, "a\x00b", "bell\x07"])
def test_a_comment_is_1_to_2000_characters_without_control_characters(
    database_client: TestClient, headers: dict[str, str], world: dict[str, UUID], text_: str
) -> None:
    response = database_client.post(url(world, "/comments"), json={"text": text_}, headers=headers)

    assert response.status_code == 422


def test_triage_and_comments_are_audited_without_the_comments_text(
    database_client: TestClient,
    headers: dict[str, str],
    world: dict[str, UUID],
    database: Database,
) -> None:
    patch(
        database_client,
        world,
        headers,
        {"status": "resolved", "assignee_id": str(world["analyst"])},
    )
    database_client.post(
        url(world, "/comments"), json={"text": "Private note 7f3a"}, headers=headers
    )

    assert audited(database, world["org"]) == [
        ("finding.status_changed", {"from": "open", "to": "resolved"}),
        ("finding.assigned", {"from": None, "to": str(world["analyst"])}),
        ("finding.commented", {}),
    ]
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `just test`
Expected: `35 failed, 780 passed, 1 skipped`. The failures are:
- all 21 tests in `test_triage_routes.py`;
- `test_every_route_declares_exactly_one_access_rule`;
- the two new stale-role cases. Without the routes, PATCH gets 405 and the comment URL 404;
- 11 of the matrix's 12 new cases. The non-member's comment passes already: it expects the 404 a missing route also gives.

- [ ] **Step 3: Write the request bodies and the routes**

`backend/src/nettriage/entrypoints/api/triage_schemas.py`:
```python
"""Request bodies for triage (spec §7). Like every request model, they forbid fields they don't
declare, so a triage change can't touch a finding's severity, title or anything else."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import StringConstraints, model_validator

from nettriage.adapters.findings import FindingStatus
from nettriage.adapters.triage import Triage
from nettriage.application.triage import MAX_COMMENT
from nettriage.entrypoints.api.schemas import Strict

# 1 to 2,000 characters after trimming (spec §5.2). Line breaks and tabs are fine in a comment;
# other control characters aren't.
CommentText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_COMMENT,
        pattern=r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+$",
    ),
]


class TriageIn(Strict):
    """A status, an assignee, or both. `"assignee_id": null` unassigns; leaving a field out
    leaves it as it is."""

    status: FindingStatus | None = None
    assignee_id: UUID | None = None

    @model_validator(mode="after")
    def says_what_to_change(self) -> TriageIn:
        if not self.model_fields_set:
            raise ValueError("Send a status, an assignee_id, or both.")
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status can't be null.")
        return self

    def change(self) -> Triage:
        return Triage(
            status=self.status,
            assign="assignee_id" in self.model_fields_set,
            assignee_id=self.assignee_id,
        )


class CommentIn(Strict):
    text: CommentText
```

`backend/src/nettriage/entrypoints/api/routes/triage.py`:
```python
"""Triage (spec §7): change a finding's status or assignee against the version the caller read,
and comment on it. Each change is in the finding's history and the org's audit log."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from nettriage.adapters.idempotency import StoredResponse
from nettriage.adapters.triage import add_comment, triage_finding
from nettriage.application.triage import etag, expected_version
from nettriage.entrypoints.api.access import OrgContext, OrgMember
from nettriage.entrypoints.api.auditing import audit
from nettriage.entrypoints.api.finding_schemas import FindingEventOut, FindingOut
from nettriage.entrypoints.api.idempotent import create_once
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.services import get_services
from nettriage.entrypoints.api.triage_schemas import CommentIn, TriageIn

router = APIRouter(prefix="/v1/orgs/{org_id}/findings")


@router.patch("/{finding_id}")
def triage(
    request: Request,
    response: Response,
    org_id: UUID,
    finding_id: UUID,
    body: TriageIn,
    org: Annotated[OrgContext, Depends(OrgMember("findings:triage"))],
    if_match: Annotated[str | None, Header()] = None,
) -> FindingOut:
    """Change the finding's status, its assignee, or both. `If-Match` must carry the `ETag` of
    the version the caller read: without it the answer is 428, and if someone changed the
    finding since, 412 with the current `ETag`."""
    expected = expected_version(if_match)
    if expected is None:
        raise HTTPException(
            428, detail='Send If-Match with the ETag of the finding you read, such as "1".'
        )
    with org_rules(request, "Triaging this finding", org=org, permission="findings:triage"):
        triaged = triage_finding(
            get_services(request).database,
            org.org_id,
            org.user_id,
            finding_id,
            expected_version=expected,
            change=body.change(),
        )
    for action, change in (
        ("finding.status_changed", triaged.status),
        ("finding.assigned", triaged.assignee),
    ):
        if change is not None:
            before, after = change
            audit(
                request,
                action=action,
                outcome="success",
                actor_user_id=org.user_id,
                org_id=org.org_id,
                target_type="finding",
                target_id=str(finding_id),
                details={
                    "from": str(before) if before is not None else None,
                    "to": str(after) if after is not None else None,
                },
            )
    response.headers["ETag"] = etag(triaged.detail.finding.version)
    return FindingOut.of_detail(triaged.detail)


@router.post("/{finding_id}/comments", status_code=201, response_model=FindingEventOut)
def comment(
    request: Request,
    org_id: UUID,
    finding_id: UUID,
    body: CommentIn,
    org: Annotated[OrgContext, Depends(OrgMember("findings:comment"))],
) -> Response:
    """Add a comment to the finding's history. An `Idempotency-Key` makes a retry return the
    same comment instead of posting it twice. The audit log records that a comment was made,
    never its text."""
    services = get_services(request)

    def work() -> StoredResponse:
        with org_rules(
            request, "Commenting on this finding", org=org, permission="findings:comment"
        ):
            event = add_comment(services.database, org.org_id, org.user_id, finding_id, body.text)
        audit(
            request,
            action="finding.commented",
            outcome="success",
            actor_user_id=org.user_id,
            org_id=org.org_id,
            target_type="finding",
            target_id=str(finding_id),
        )
        return StoredResponse(status=201, body=FindingEventOut.of(event).model_dump(mode="json"))

    stored = create_once(request, org.user_id, body, "Commenting on this finding", work)
    return JSONResponse(stored.body, status_code=stored.status)
```

- [ ] **Step 4: Answer stale versions and invalid assignees, and register the routes**

In `backend/src/nettriage/entrypoints/api/org_errors.py`, replace:
```python
)
from nettriage.entrypoints.api.access import OrgContext, deny, forbidden, unavailable
```
with:
```python
)
from nettriage.application.triage import InvalidAssignee, StaleVersion, etag
from nettriage.entrypoints.api.access import OrgContext, deny, forbidden, unavailable
```

In `backend/src/nettriage/entrypoints/api/org_errors.py`, replace:
```python
    - NotFound and InvitationInvalid are 404; WrongEmail is 403;
    - ConfirmationMismatch is 422; the rest (last owner, conflicts, quotas) are 409;
    - a database outage is 503."""
```
with:
```python
    - NotFound and InvitationInvalid are 404; WrongEmail is 403;
    - ConfirmationMismatch and InvalidAssignee are 422;
    - StaleVersion is 412, with the finding's current `ETag`;
    - the rest (last owner, conflicts, quotas) are 409;
    - a database outage is 503."""
```

In `backend/src/nettriage/entrypoints/api/org_errors.py`, replace:
```python
        raise HTTPException(403, detail=str(error)) from None
    except ConfirmationMismatch as error:
        raise HTTPException(422, detail=str(error)) from None
    except OrgRuleError as error:
```
with:
```python
        raise HTTPException(403, detail=str(error)) from None
    except (ConfirmationMismatch, InvalidAssignee) as error:
        raise HTTPException(422, detail=str(error)) from None
    except StaleVersion as error:
        raise HTTPException(412, detail=str(error), headers={"ETag": etag(error.current)}) from None
    except OrgRuleError as error:
```

In `backend/src/nettriage/entrypoints/api/app.py`, replace:
```python
    orgs,
    uploads,
```
with:
```python
    orgs,
    triage,
    uploads,
```

In `backend/src/nettriage/entrypoints/api/app.py`, replace:
```python
        findings,
        attack_techniques,
```
with:
```python
        findings,
        triage,
        attack_techniques,
```

- [ ] **Step 5: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `815 passed, 1 skipped`.

- [ ] **Step 6: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(triage): PATCH a finding with If-Match (412, 428) and comment on it, audited" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Spec amendments, runbook and README

**Files:**
- Modify: `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (§5.2, §5.4, §6.4, §7), `docs/runbooks/setup-and-deploy.md` (a new B8 and two Part C rows), `README.md`

- [ ] **Step 1: Amend the spec**

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `attack_techniques` | `id` text PK (for example `T1046`), `stix_id`, `name`, `tactics` text[], `description`, `url`, `attack_version`, `is_subtechnique`, `parent_id`, `deprecated`. Loaded from ATT&CK STIX v19.2: `tools/attack_subset.py` extracts the techniques the detectors can name, and their parents, into the backend package (Plan 4b). MITRE's copyright notice and license are kept |
| `findings` | `id`, `org_id`, `upload_id`, `detector_id` → detectors, `detector_version`, `fingerprint`, `severity` CHECK in (low, medium, high, critical), `status` CHECK in (open, investigating, resolved, false_positive), `title`, `src_ip` inet, `dst_ip` inet, `dst_port` int CHECK 0–65535, `protocol` smallint, `time_window` tstzrange, `metrics` jsonb, `assignee_id` → users, `version` int; UNIQUE `(org_id, upload_id, fingerprint)`; UNIQUE `(org_id, id)` (target of child composite FKs); FK `(org_id, upload_id)` → uploads |
| `finding_evidence` | `id`, `org_id`, `finding_id`, `src_ip`, `dst_ip`, `src_port`, `dst_port`, `protocol`, `packets` bigint, `bytes` bigint, `start_ts`, `end_ts`, `action`, `line_no`; FK `(org_id, finding_id)` |
```
with:
```markdown
| `attack_techniques` | `id` text PK (for example `T1046`), `stix_id`, `name`, `tactics` text[], `description`, `url`, `attack_version`, `is_subtechnique`, `parent_id`, `deprecated`. Loaded from ATT&CK STIX v19.2: `tools/attack_subset.py` extracts the techniques the detectors can name, and their parents, into the backend package (Plan 4b). MITRE's copyright notice and license are kept |
| `findings` | `id`, `org_id`, `upload_id`, `detector_id` → detectors, `detector_version`, `fingerprint`, `severity` CHECK in (low, medium, high, critical), `status` CHECK in (open, investigating, resolved, false_positive), `title`, `src_ip` inet, `dst_ip` inet, `dst_port` int CHECK 0–65535, `protocol` smallint, `time_window` tstzrange, `metrics` jsonb, `assignee_id` → a member of the finding's org (a composite foreign key to `memberships`; Plan 4c), `version` int; UNIQUE `(org_id, upload_id, fingerprint)`; UNIQUE `(org_id, id)` (target of child composite FKs); FK `(org_id, upload_id)` → uploads |
| `finding_evidence` | `id`, `org_id`, `finding_id`, `src_ip`, `dst_ip`, `src_port`, `dst_port`, `protocol`, `packets` bigint, `bytes` bigint, `start_ts`, `end_ts`, `action`, `line_no`; FK `(org_id, finding_id)` |
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `nettriage_owner` | Migrations (CI only) | Owns the schema; DDL |
| `app_api` | `api` Lambda | The SELECT/INSERT/UPDATE its endpoints need; INSERT only on `audit_log`; DELETE only on `memberships`, `invitations`, `organizations` |
| `app_analyze` | `analyze` Lambda | SELECT `uploads`, `detectors`, `attack_techniques`; UPDATE of `uploads` status and statistics columns only; INSERT `findings`, `finding_evidence`, `finding_techniques`, `finding_events`. No `audit_log` until the worker records an event worth auditing (Plan 4b) |
```
with:
```markdown
| `nettriage_owner` | Migrations (CI only) | Owns the schema; DDL |
| `app_api` | `api` Lambda | The SELECT/INSERT/UPDATE its endpoints need; INSERT only on `audit_log`; DELETE only on `memberships`, `invitations`, `organizations`. On findings it may UPDATE only `status`, `assignee_id` and `version`, and `finding_events` is insert-only (Plan 4c) |
| `app_analyze` | `analyze` Lambda | SELECT `uploads`, `detectors`, `attack_techniques`; UPDATE of `uploads` status and statistics columns only; INSERT `findings`, `finding_evidence`, `finding_techniques`, `finding_events`. No `audit_log` until the worker records an event worth auditing (Plan 4b) |
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown

**Rules:**
```
with:
```markdown

A finding's assignee can always triage it (the owner's decisions, Plan 4c): only an Owner, Admin or Analyst can be assigned, and when a member leaves, is removed or becomes a Viewer, their findings in that organization are unassigned in the same transaction, each with an `assigned` event in its history.

**Rules:**
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
- **Pagination:** cursor-based (`cursor`, `limit` ≤ 100). Lists that §5.7's quotas keep small (members, invitations) return every item; the audit log pages with a cursor.
- **Optimistic concurrency:** findings return an `ETag`. `PATCH` requires `If-Match`: a stale version gets **412 Precondition Failed**, and a missing header gets 428.
- **Idempotency:** `Idempotency-Key` is supported on `POST …/uploads` and `POST /orgs` and is kept for 24 hours. A key reused with a different request gets 422, and a retry while the first request still runs gets 409. A request is its method, path and body, so a key can't be reused across routes or orgs (Plan 4a).
- **SPA request headers:** `x-amz-content-sha256` on every request with a body (the OAC requirement), and `X-CSRF-Token` on state-changing requests.
```
with:
```markdown
- **Pagination:** cursor-based (`cursor`, `limit` ≤ 100). Lists that §5.7's quotas keep small (members, invitations) return every item; the audit log pages with a cursor.
- **Optimistic concurrency:** findings return an `ETag`. `PATCH` requires `If-Match`: a stale version gets **412 Precondition Failed**, and a missing header gets 428. The ETag is the finding's version in quotes (`"3"`); `*`, a weak ETag or a list names no version and also gets 428. A 412 carries the current `ETag` (Plan 4c).
- **Idempotency:** `Idempotency-Key` is supported on `POST …/uploads`, `POST /orgs` and `POST …/comments` (Plan 4c) and is kept for 24 hours. A key reused with a different request gets 422, and a retry while the first request still runs gets 409. A request is its method, path and body, so a key can't be reused across routes or orgs (Plan 4a).
- **SPA request headers:** `x-amz-content-sha256` on every request with a body (the OAC requirement), and `X-CSRF-Token` on state-changing requests.
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `GET /api/v1/orgs/{org}/findings/{id}` | `findings:read` | With evidence, techniques, latest AI analysis (Plan 5), events; the `ETag` is the finding's version |
| `PATCH /api/v1/orgs/{org}/findings/{id}` | `findings:triage` | Status, assignee; `If-Match` |
| `POST /api/v1/orgs/{org}/findings/{id}/comments` | `findings:comment` | |
| `POST /api/v1/orgs/{org}/findings/{id}/ai-analyses` | `ai:request` | Re-run, subject to budget and rate limits |
```
with:
```markdown
| `GET /api/v1/orgs/{org}/findings/{id}` | `findings:read` | With evidence, techniques, latest AI analysis (Plan 5), events; the `ETag` is the finding's version |
| `PATCH /api/v1/orgs/{org}/findings/{id}` | `findings:triage` | Body: `status`, `assignee_id` (`null` unassigns), or both; `If-Match`. Any status can change to any other, and the assignee must be an Owner, Admin or Analyst of the org (422 otherwise; the owner's decisions, Plan 4c). Each change is an event in the finding's history and an audit event |
| `POST /api/v1/orgs/{org}/findings/{id}/comments` | `findings:comment` | Body: `text`, 1 to 2,000 characters, line breaks allowed. A comment joins the history without changing the finding's version; the audit event never holds its text |
| `POST /api/v1/orgs/{org}/findings/{id}/ai-analyses` | `ai:request` | Re-run, subject to budget and rate limits |
```

- [ ] **Step 2: Add "Try triage" to the runbook**

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown

## Part C: when things go wrong
```
with:
````markdown

### B8. Try triage
A finding's status and assignee change only with `If-Match`: the `ETag` of the version you read.
If someone changed the finding since, the API refuses with `412` instead of overwriting their
change.
1. Do B7 steps 1 to 6 (sign in, define `api`, upload the port scan and list its finding). Don't
   delete the org yet.
2. Keep the finding's address, and define `triage(body, etag)`, which sends a change with an
   optional `If-Match`:
   ```js
   const f = "/orgs/" + org.id + "/findings/" + list.findings[0].id;
   async function triage(body, etag) {
     const text = JSON.stringify(body);
     const headers = { "X-CSRF-Token": me.csrf_token, "Content-Type": "application/json", "x-amz-content-sha256": hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text))) };
     if (etag) headers["If-Match"] = etag;
     const response = await fetch("/api/v1" + f, { method: "PATCH", headers, body: text });
     console.log(response.status, response.headers.get("etag"), await response.json());
   }
   ```
3. Change the status without `If-Match`:
   ```js
   await triage({ status: "investigating" });
   ```
   `428`, "Send If-Match with the ETag of the finding you read".
4. Change it with the ETag you read (`"1"`, a finding's first version):
   ```js
   await triage({ status: "investigating" }, '"1"');
   ```
   `200`, the new ETag `"2"`, and the finding with `status: "investigating"`.
5. Send the same old ETag again, as a second person who read version 1 would:
   ```js
   await triage({ status: "false_positive" }, '"1"');
   ```
   `412`, the current ETag `"2"`, and "This finding changed since you read it. Reload it and try
   again." The status stays `investigating`.
6. Assign the finding to yourself:
   ```js
   await triage({ assignee_id: me.user.id }, '"2"');
   ```
   `200`, ETag `"3"`, with your ID as `assignee_id`.
7. Comment on it:
   ```js
   await api("POST", f + "/comments", { text: "Our weekly scanner. Safe to close." });
   ```
   `201`, with `type: "commented"` and your text.
8. Read its history:
   ```js
   (await api("GET", f)).events.map((event) => event.type);
   ```
   `["created", "status_changed", "assigned", "commented"]`.
9. Read the audit log:
   ```js
   await api("GET", "/orgs/" + org.id + "/audit-log");
   ```
   `200`, with `finding.commented`, `finding.assigned` and `finding.status_changed` newest
   first. `finding.commented` doesn't hold the comment's text.
10. Delete the test org as in B7 step 10.

## Part C: when things go wrong
````

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
| `Too Many Requests` with `"status": 429` | Too many sign-in attempts or requests from your IP or account, or more than 5 uploads started at once in one org (20 a day). Wait the number of seconds in the `Retry-After` header (a minute at most for sign-in), then retry |
| `503` "Uploads are paused for now" | The uploads kill switch is off. Resume it as in "Pause uploads in an emergency" if that's not intended |
```
with:
```markdown
| `Too Many Requests` with `"status": 429` | Too many sign-in attempts or requests from your IP or account, or more than 5 uploads started at once in one org (20 a day). Wait the number of seconds in the `Retry-After` header (a minute at most for sign-in), then retry |
| `412` "This finding changed since you read it" | Someone changed the finding after you read it. Read it again (its `ETag` header is the new version), then repeat the change with that ETag |
| `428` "Send If-Match with the ETag of the finding you read" | A status or assignee change needs the finding's `ETag` in an `If-Match` header, as in B8 |
| `503` "Uploads are paused for now" | The uploads kill switch is off. Resume it as in "Pause uploads in an emergency" if that's not intended |
```

- [ ] **Step 3: Add the highlight to the README**

In `README.md`, replace:
```markdown
- **Event-driven analysis**: each upload queues a worker Lambda that streams and parses the file within size, row and decompression limits, runs three detectors, and stores each finding exactly once with its evidence and MITRE ATT&CK techniques, even when a message arrives twice.
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for each function.
```
with:
```markdown
- **Event-driven analysis**: each upload queues a worker Lambda that streams and parses the file within size, row and decompression limits, runs three detectors, and stores each finding exactly once with its evidence and MITRE ATT&CK techniques, even when a message arrives twice.
- **Triage with optimistic concurrency**: a finding's status and assignee change only with `If-Match` naming the version the caller read, so two analysts can't silently overwrite each other (412 instead); every change and comment is in the finding's history and the audit log.
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for each function.
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-09-26-nettriage-m1-design.md docs/runbooks/setup-and-deploy.md README.md
git commit -m "docs: triage in the spec, runbook and README" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6 (Claude, then the owner): Pull request, deploy, and a first triage

- [ ] **Step 1 (Claude):**
  - Run `just lint test tools-test edge-test web-check tf-check pin-check cloud-check detection-report build-lambda`; everything must pass.
  - Get a final review of the whole branch, fix what it finds, then push `plan-4c/triage`.
  - Open the PR and watch CI.
  - Request a Copilot review.
- [ ] **Step 2 (owner):** Review the PR. Optionally run `just plan-dev` (runbook B1). Expected: `Plan: 0 to add, 2 to change, 0 to destroy`: the API and analyze functions' new code, since both are built from `backend.zip` (corrected after the final review).
- [ ] **Step 3 (owner):** Squash-merge the PR.
- [ ] **Step 4 (owner):** Runbook B2 (`just deploy-dev`). Expected:
  - `Database migrated.` (migration `0007`, with no new logins), then `Reference data synced: 3 detectors, 12 ATT&CK techniques.`;
  - the Terraform plan above;
  - fifteen smoke `PASS` lines.
- [ ] **Step 5 (owner):** Runbook B8:
  - `428` without `If-Match`;
  - `200` and `ETag "2"` with it;
  - `412` for the stale `"1"`;
  - assign yourself, then comment;
  - see all of it in the finding's history and in the audit log.

## Plan 4c is done when

- [ ] `just lint test tools-test tf-check` passes locally and CI passes.
- [ ] On dev, a stale `If-Match` gets 412 and a change with the current one succeeds, and the history and audit log show it (runbook B8).
- [ ] The PR is merged through review, with every thread resolved.

## Spec coverage of this plan

| Spec | Covered here |
|---|---|
| §2 triage (status, assignee, comments) with optimistic concurrency | Tasks 2 and 4 |
| §5.2 `findings.status`, `assignee_id`, `version`; `finding_events` `status_changed`, `assigned`, `commented`; comments at most 2,000 characters | Tasks 1, 2 and 4; the assignee's foreign key to memberships amends §5.2 (Task 5) |
| §5.4 `app_api`'s rights | Task 1; amends §5.4's `app_api` row (Task 5) |
| §6.4 `findings:triage`, `findings:comment`; 404 for another org's ID; no escalation; the test matrix | Tasks 2 and 4; who can be assigned amends §6.4 (Task 5) |
| §7 `PATCH …/findings/{id}` with `If-Match`, 412, 428; `POST …/comments` | Task 4; the ETag rules and `Idempotency-Key` on comments amend §7 (Task 5) |
| §9.4 `finding.status_changed`, `finding.assigned`, `finding.commented` | Task 4 |
| §7 `POST …/ai-analyses`, `PUT …/feedback`; the triage queue | Plan 5 |
| §10 the finding page's triage controls and timeline | Plan 6 |
