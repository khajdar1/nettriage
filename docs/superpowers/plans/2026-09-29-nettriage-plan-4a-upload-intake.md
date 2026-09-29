# NetTriage Plan 4a: Upload Intake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let members upload a VPC Flow Logs file from the browser straight to S3:
- the `uploads` table;
- a private, TLS-only uploads bucket that accepts a browser `PUT` only from the app's pages;
- `POST`, `GET` and `GET /{id}` upload routes. `POST` hands out a presigned PUT that signs the file's size, its SHA-256 and the request's trace, so S3 accepts exactly the declared file;
- the org's daily upload quota, `Idempotency-Key`, and an `uploads_enabled` kill switch.

Analysis (the queue, the worker and findings) is Plan 4b; for now an upload stays `pending_upload`.

**Architecture:**
- `POST /api/v1/orgs/{org}/uploads` checks `uploads:create`, the kill switch and the `uploads.org` quota, then inserts a `pending_upload` row under the org's lock (re-reading the caller's role, as every change does since Plan 3c).
- It then presigns a PUT to `orgs/{org}/uploads/{upload}/raw` with the API's role, signing `Content-Length`, `x-amz-checksum-sha256` and `x-amz-meta-traceparent`, and returns the URL and the headers to send.
- The browser PUTs the file to S3 directly. S3 refuses a body of another length (the signature breaks) or another SHA-256 (the checksum breaks).
- A new Terraform module, `pipeline`, holds the bucket and the kill-switch parameter; Plan 4b adds the queue and the worker to it. The CSP's `connect-src` gains the bucket's origin.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2 Core with psycopg 3 (Neon Postgres, row-level security), boto3 (S3 presigning, SSM), OpenTelemetry's W3C propagator · moto for SSM in tests · Terraform (S3, SSM, IAM).

**Spec:** `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (revision 2). Read it alongside this plan; section numbers (§) refer to it. This plan implements:
- §4.2's first half (upload request, presigned PUT, direct upload to S3);
- §5.2's `uploads` table, with §5.3's row-level security and §5.4's grants for `app_api`;
- §5.6's uploads bucket and §5.7's upload limits (25 MB, 20 a day per org);
- §6.5's `uploads.org` and §6.7's CSP `connect-src`;
- §7's `POST`, `GET` and `GET /{id}` upload routes, with `Idempotency-Key`;
- §9.1's trace propagation into S3 metadata, §9.4's `upload.created` and §9.7's `uploads_enabled` kill switch;
- §13.2's check that S3 enforces the presigned PUT's signed headers from a browser (runbook B6).

**Plan series:** Plan 4 of 7 ("upload pipeline") is split in three, as the owner chose on 2026-09-29:
- **4a (this plan): upload intake;**
- **4b: analysis:** the `analyze` queue and worker (stream, parse, detect, store), findings and the findings read API;
- **4c: triage:** a finding's status and assignee with version checks (`ETag`/`If-Match`), comments and history.

**Branch:** `plan-4a/upload-intake`, from `main` at `2fcbf70` or later.

## Global Constraints

- **Stack.** Python **3.14**. New dev dependency: the `s3` extra of `types-boto3`. mypy `--strict` and Ruff pass on `src` and `tests`. Work test-first.
- **Commands on this Windows machine.** Run Python tools as modules (`uv run python -m …`). Host policy blocks some uv launchers and every unsigned executable outside trusted tools. Backend tests need the local Postgres, which `just test` starts.
- **Region.** The bucket and the SSM parameter live in **eu-north-1** (Revision 2, R1).
- **The uploads bucket (§5.6).**
  - Keys are `orgs/{org_id}/uploads/{upload_id}/raw`, built from IDs only.
  - Private (public access blocked), TLS-only, SSE-S3, ACLs disabled.
  - CORS allows `PUT` from the app's origin only.
  - Lifecycle: delete after **30 days**; abort incomplete multipart uploads after **1 day**.
- **Upload limits (§5.7, §6.5).** At most **25 MB** per file (26,214,400 bytes; binary, like the parser's limits), and `uploads.org`: **20 a day per org, burst 5**.
- **The presigned PUT (§7, §9.1).** It expires in **5 minutes** and signs `Content-Length`, `x-amz-checksum-sha256` (the declared SHA-256 in base64) and, when a trace is active, `x-amz-meta-traceparent`.
- **Idempotency (§7).** `Idempotency-Key` on `POST …/uploads`, kept for **24 hours** per user. The same key with another request gets **422**, and a retry while the first runs gets **409**.
- **Kill switch (§9.7).** `uploads_enabled` lives in SSM and is re-read every **60 seconds**. While it's off, `POST …/uploads` gets **503**.
- **Grants (§5.4).** `app_api` may read uploads and insert new ones, and nothing else on the table.
- **Logs and audit (§9.3, §9.4).** Never log a file's name or content. `upload.created` records the size, not the name.
- **Owner-only commands.** Claude never runs `aws login`, `just store-*` or `just deploy-*`.
- **Commits.** Use Conventional Commits. Every commit made by Claude ends with the trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Decisions this plan makes

1. **Plan 4 is split in three** (the owner's decision, 2026-09-29): 4a upload intake, 4b analysis, 4c triage. Each is a PR with its own deploy and something to try.
2. **The bucket is named `nettriage-<stage>-uploads-<8 hex of the account ID's SHA-256>`** (this amends §5.6 and §6.7's CSP example).
   - Bucket names are global, so a bare `nettriage-dev-uploads` could be taken.
   - Its origin appears in every page's CSP header, so the name can't hold the account ID itself. The sign-in domain uses the same suffix (Plan 3b).
3. **The presigned PUT signs the size, the checksum and the trace, and nothing else.**
   - `Content-Length`: the browser sets it from the body, so a longer or shorter file breaks the signature (403).
   - `x-amz-checksum-sha256`: S3 computes the body's SHA-256 and refuses a mismatch (400).
   - `x-amz-meta-traceparent`: the worker (Plan 4b) links its spans to the upload's trace.
   - The client sends the SHA-256 in hex; the API converts it to base64 for the header.
   - Checksums are calculated only when required (`request_checksum_calculation="when_required"`), so the SDK adds no header the browser would also have to send.
   - URLs are virtual-hosted and regional (`<bucket>.s3.eu-north-1.amazonaws.com`), the origin the CSP allows.
   - §13.2 asks whether S3 really enforces these from a browser. Runbook B6 checks it on dev. The fallback, if it doesn't, is a presigned POST with `content-length-range`.
4. **A file's name is display-only.**
   - It never becomes part of an S3 key, a log line or an audit event.
   - It is 1 to 255 characters after trimming, with no control characters.
5. **`app_api` may SELECT uploads and INSERT only a new upload's columns.**
   - It can't set a status, statistics or results, and can't update or delete a row.
   - The worker's role (Plan 4b) and `ops` (Plan 7) move uploads on.
   - Deleting an org cascades to its uploads. Their S3 objects expire with the 30-day lifecycle.
6. **Creating an upload re-reads the caller's role under the org's lock**, as every change has since Plan 3c's fix. A member demoted to viewer mid-request creates nothing.
7. **The daily quota is the `uploads.org` rate limit,** with the org as subject.
   - It counts only new uploads: an `Idempotency-Key` replay doesn't count.
   - `api.mutation.user` applies too, as on every state-changing request.
8. **One `Idempotency-Key` flow serves `POST /orgs` and `POST …/uploads`** (`entrypoints/api/idempotent.py`).
   - A request's hash now covers its method and path as well as its body. So a key can't be replayed across routes or orgs (the 3c review's note for Plan 4).
   - A key stored under the old, body-only hash within 24 hours of the deploy would get a 422 on retry; that's harmless.
9. **The kill switch fails closed until it's known.**
   - `true`, in any case, means on. Anything else, including a missing parameter, means off.
   - A failed read keeps the last value. Until one read succeeds, uploads are paused.
   - Terraform only creates the parameter (`ignore_changes = [value]`), so a deploy never switches uploads back on after the owner paused them.
   - The owner flips it with one `aws ssm put-parameter` command (runbook Part C).
10. **The trace travels as a W3C `traceparent` from OpenTelemetry's own propagator,** not a hand-built string. Newer SDKs also set the "random trace ID" flag (`-03`).
11. **The smoke test finds the bucket's origin in the CSP's `connect-src`,** with no new deploy wiring. It checks that:
    - the app may PUT (CORS preflight);
    - another origin may not;
    - an anonymous PUT is refused.

    That's four more `PASS` lines, fifteen in all.
12. **Uploads are listed a page at a time** (default 50, at most 100), newest first. The audit log's cursor encoding moves to a shared `entrypoints/api/cursors.py`.

## Review Focus

1. **A browser sends a different file than it declared: altered, truncated or longer.** S3 must refuse it, and nothing may be stored under the upload's key. Tests: Task 2 `test_a_presigned_put_pins_the_files_size_checksum_and_trace` (the URL signs them); S3's enforcement is checked on dev by runbook B6 step 5.
2. **A double-click or network retry on "Upload".** One upload row must be created, and the retry must get the same answer, URL included. Test: Task 4 `test_a_retry_with_the_same_idempotency_key_returns_the_same_upload`.
3. **An analyst demoted to viewer while their upload request is in flight.** No upload may be created. Test: Task 4 `test_a_change_uses_the_role_the_caller_has_now[upload-analyst-viewer-403]`.
4. **A file named `../../etc/passwd`, one with a line break, or one 300 characters long.** The API must refuse the last two (422), and no name may ever reach an S3 key. Tests: Task 1 `test_an_objects_key_is_built_from_ids_only`, Task 4 `test_a_file_needs_a_name_a_size_up_to_25_mb_and_a_sha256`.
5. **SSM unreadable when a new Lambda instance starts.** Uploads must be paused (503) until the switch has been read, and a later failed read must keep the last value. Tests: Task 2 `test_a_switch_that_was_never_read_is_off` and `test_a_failed_read_keeps_the_last_value`.

## Owner prerequisites

- **Nothing is needed to build or review this plan.** Tests use the local Postgres, moto and offline presigning.
- **Nothing new is needed before the deploy.** Terraform creates the bucket and the kill switch; the deploy applies migration `0005`.
- **To try it afterwards** (runbook B6, which Task 6 adds), you only need to be signed in to dev.

## File map

| File | Responsibility | Task |
|---|---|---|
| `migrations/versions/0005_uploads.py` | the `uploads` table, its RLS policy and `app_api`'s grants | 1 |
| `src/nettriage/application/uploads.py` | upload limits, statuses, the S3 key and the checksum header | 1 |
| `src/nettriage/adapters/uploads.py` | uploads in Postgres: create, read, list | 1 |
| `src/nettriage/adapters/upload_storage.py` | presigned PUTs into the uploads bucket | 2 |
| `src/nettriage/adapters/kill_switch.py` | on/off flags in SSM, re-read once a minute | 2 |
| `src/nettriage/platform/trace_context.py` | adds the W3C `traceparent` of the current span | 2 |
| `src/nettriage/entrypoints/api/idempotent.py` | the shared `Idempotency-Key` flow | 3 |
| `src/nettriage/entrypoints/api/cursors.py` | page cursors | 3 |
| `src/nettriage/entrypoints/api/upload_schemas.py`, `routes/uploads.py` | the upload routes | 4 |
| `infra/modules/pipeline/` | the uploads bucket and the kill switch | 5 |
| `tools/smoke.py` | adds the uploads bucket checks | 5 |

Paths under `src/` and `migrations/` are in `backend/`.

---

### Task 1: The `uploads` table

**Files:**
- Create: `backend/migrations/versions/0005_uploads.py`, `backend/src/nettriage/application/uploads.py`, `backend/src/nettriage/adapters/uploads.py`
- Modify (test harness): `backend/tests/tenantdata.py` (`add_upload`; every seeded tenant gets an upload)
- Test: `backend/tests/unit/application/test_upload_rules.py`, `backend/tests/integration/test_uploads.py`, `backend/tests/integration/test_tenant_isolation.py`, `backend/tests/integration/test_schema.py`, `backend/tests/integration/test_migrations.py`

**Interfaces:**
- Consumes (Plan 3c): `tenant_transaction`, `lock_org_for(connection, org_id, user_id) -> Role`, `require(role, permission)`, `NotFound`, `Forbidden`, and the `database` fixture.
- Produces:
  - In `nettriage.application.uploads`:
    - `UploadStatus = Literal["pending_upload", "processing", "analyzed", "failed", "expired"]` and `UPLOAD_STATUSES`;
    - `MAX_UPLOAD_BYTES = 26_214_400`, `PRESIGNED_PUT_LIFETIME = timedelta(minutes=5)` and `MAX_FILENAME = 255`;
    - `s3_key(org_id, upload_id) -> str`, which gives `orgs/{org}/uploads/{upload}/raw`;
    - `checksum_header(sha256_hex) -> str`, the digest in base64.
  - In `nettriage.adapters.uploads`:
    - `Upload(id, org_id, uploaded_by, original_filename, size_bytes, sha256, status, failure_reason, rows_parsed, rows_rejected, rejected_samples, findings_truncated, flow_start, flow_end, processed_at, created_at)`;
    - `create_upload(engine, org_id, *, user_id, upload_id, filename, size_bytes, sha256, s3_key) -> Upload`, which requires `uploads:create` under the org's lock;
    - `get_upload(engine, org_id, user_id, upload_id) -> Upload`, which raises `NotFound`;
    - `list_uploads(engine, org_id, user_id, *, limit, before: tuple[datetime, UUID] | None = None) -> list[Upload]`, newest first.
  - In `backend/tests/tenantdata.py`: `add_upload(connection, org_id, uploaded_by, status="pending_upload") -> UUID`, and `Tenant.upload_id`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/tenantdata.py`, replace:
```python
"""Seed users, organizations, memberships and invitations for integration tests. Seeding uses
the superuser engine, which row-level security doesn't apply to."""

```
with:
```python
"""Seed users, organizations, memberships, invitations and uploads for integration tests.
Seeding uses the superuser engine, which row-level security doesn't apply to."""

```

In `backend/tests/tenantdata.py`, replace:
```python
    invitation_id: UUID

```
with:
```python
    invitation_id: UUID
    upload_id: UUID

```

In `backend/tests/tenantdata.py`, replace:
```python

def add_tenant(admin: Engine) -> Tenant:
    """An org with an owner, and a pending invitation."""
    with admin.begin() as connection:
```
with:
```python

def add_upload(
    connection: Connection, org_id: UUID, uploaded_by: UUID, status: str = "pending_upload"
) -> UUID:
    upload_id = uuid7()
    connection.execute(
        text(
            "INSERT INTO uploads (id, org_id, uploaded_by, original_filename, s3_key, size_bytes, "
            "sha256, status) VALUES (:id, :org, :by, 'flows.log', :key, 1024, :sha256, :status)"
        ),
        {
            "id": upload_id,
            "org": org_id,
            "by": uploaded_by,
            "key": f"orgs/{org_id}/uploads/{upload_id}/raw",
            "sha256": "0" * 64,
            "status": status,
        },
    )
    return upload_id


def add_tenant(admin: Engine) -> Tenant:
    """An org with an owner, a pending invitation and an upload."""
    with admin.begin() as connection:
```

In `backend/tests/tenantdata.py`, replace:
```python
        invitation = add_invitation(connection, org, owner, f"invitee-{org.hex}@example.com")
    return Tenant(org_id=org, owner_id=owner, invitation_id=invitation)

```
with:
```python
        invitation = add_invitation(connection, org, owner, f"invitee-{org.hex}@example.com")
        upload = add_upload(connection, org, owner)
    return Tenant(org_id=org, owner_id=owner, invitation_id=invitation, upload_id=upload)

```

In `backend/tests/integration/test_tenant_isolation.py`, replace:
```python

TENANT_TABLES = ("organizations", "memberships", "invitations", "audit_log")
INSERT_AUDIT_EVENT = text(
```
with:
```python

TENANT_TABLES = ("organizations", "memberships", "invitations", "audit_log", "uploads")
INSERT_AUDIT_EVENT = text(
```

In `backend/tests/integration/test_tenant_isolation.py`, replace:
```python
            table: org_ids(connection, table)
            for table in ("organizations", "memberships", "invitations")
        }
```
with:
```python
            table: org_ids(connection, table)
            for table in ("organizations", "memberships", "invitations", "uploads")
        }
```

In `backend/tests/integration/test_tenant_isolation.py`, replace:
```python
        "VALUES (gen_random_uuid(), 'Demo', 'fake-demo', true)",
        "CREATE TEMP TABLE shadow (id int)",
```
with:
```python
        "VALUES (gen_random_uuid(), 'Demo', 'fake-demo', true)",
        "UPDATE uploads SET status = 'analyzed'",
        "DELETE FROM uploads",
        "INSERT INTO uploads (id, org_id, uploaded_by, original_filename, s3_key, size_bytes, "
        "sha256, status) SELECT gen_random_uuid(), org_id, uploaded_by, 'x', 'k', 1, sha256, "
        "'analyzed' FROM uploads",
        "CREATE TEMP TABLE shadow (id int)",
```

In `backend/tests/integration/test_schema.py`, replace:
```python

def test_deleting_an_org_removes_its_memberships_and_invitations_not_its_audit_log(
    database: Database,
```
with:
```python

def test_deleting_an_org_removes_its_members_invitations_and_uploads_not_its_audit_log(
    database: Database,
```

In `backend/tests/integration/test_schema.py`, replace:
```python
            ).scalar_one()
            for table in ("memberships", "invitations", "audit_log")
        }
    assert remaining == {"memberships": 0, "invitations": 0, "audit_log": 1}

```
with:
```python
            ).scalar_one()
            for table in ("memberships", "invitations", "uploads", "audit_log")
        }
    assert remaining == {"memberships": 0, "invitations": 0, "uploads": 0, "audit_log": 1}

```

In `backend/tests/integration/test_migrations.py`, replace:
```python

TABLES = {"users", "organizations", "memberships", "invitations", "audit_log"}

```
with:
```python

TABLES = {"users", "organizations", "memberships", "invitations", "audit_log", "uploads"}

```

`backend/tests/unit/application/test_upload_rules.py`:
```python
import base64
import hashlib
from uuid import UUID

from nettriage.application.uploads import (
    MAX_UPLOAD_BYTES,
    PRESIGNED_PUT_LIFETIME,
    checksum_header,
    s3_key,
)

ORG = UUID("01a0ec4d-060a-7266-a427-fce3ccd0d827")
UPLOAD = UUID("01a0ec4d-374f-740e-85e7-4ac6ee451cd5")


def test_the_limits_are_the_specs() -> None:
    assert MAX_UPLOAD_BYTES == 26_214_400
    assert PRESIGNED_PUT_LIFETIME.total_seconds() == 300


def test_an_objects_key_is_built_from_ids_only() -> None:
    assert s3_key(ORG, UPLOAD) == f"orgs/{ORG}/uploads/{UPLOAD}/raw"


def test_the_checksum_header_is_the_digest_in_base64() -> None:
    digest = hashlib.sha256(b"flows").digest()

    assert checksum_header(digest.hex()) == base64.b64encode(digest).decode()
```

`backend/tests/integration/test_uploads.py`:
```python
"""Uploads in Postgres (spec §5.2, §5.3): created as `pending_upload` by a member whose role may
upload, read only within their org."""

from uuid import UUID, uuid4, uuid7

import pytest
from conftest import Database
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from tenantdata import add_member, add_tenant, add_upload, add_user

from nettriage.adapters.uploads import Upload, create_upload, get_upload, list_uploads
from nettriage.application.organizations import Forbidden, NotFound
from nettriage.application.uploads import UPLOAD_STATUSES, s3_key

SHA256 = "ab" * 32


def upload_as(database: Database, org: UUID, user: UUID, filename: str = "flows.log") -> Upload:
    upload_id = uuid7()
    return create_upload(
        database.app_api,
        org,
        user_id=user,
        upload_id=upload_id,
        filename=filename,
        size_bytes=2048,
        sha256=SHA256,
        s3_key=s3_key(org, upload_id),
    )


def test_an_upload_starts_pending_with_what_the_member_declared(database: Database) -> None:
    tenant = add_tenant(database.admin)

    upload = upload_as(database, tenant.org_id, tenant.owner_id, "vpc flows.log.gz")

    assert (upload.org_id, upload.uploaded_by, upload.original_filename) == (
        tenant.org_id,
        tenant.owner_id,
        "vpc flows.log.gz",
    )
    assert (upload.size_bytes, upload.sha256, upload.status) == (2048, SHA256, "pending_upload")
    assert (upload.rows_parsed, upload.rejected_samples, upload.findings_truncated) == (
        None,
        [],
        0,
    )
    assert get_upload(database.app_api, tenant.org_id, tenant.owner_id, upload.id) == upload


def test_a_viewer_can_not_upload(database: Database) -> None:
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        viewer = add_user(connection)
        add_member(connection, tenant.org_id, viewer, "viewer")

    with pytest.raises(Forbidden):
        upload_as(database, tenant.org_id, viewer)


def test_someone_removed_from_the_org_can_not_upload(database: Database) -> None:
    tenant = add_tenant(database.admin)
    stranger = uuid4()

    with pytest.raises(NotFound):
        upload_as(database, tenant.org_id, stranger)


def test_uploads_are_listed_newest_first_page_by_page(database: Database) -> None:
    tenant = add_tenant(database.admin)
    created = [upload_as(database, tenant.org_id, tenant.owner_id) for _ in range(3)]
    newest_first = [tenant.upload_id, *[upload.id for upload in created]][::-1]

    first = list_uploads(database.app_api, tenant.org_id, tenant.owner_id, limit=2)
    rest = list_uploads(
        database.app_api,
        tenant.org_id,
        tenant.owner_id,
        limit=2,
        before=(first[-1].created_at, first[-1].id),
    )

    assert [upload.id for upload in first + rest] == newest_first


def test_another_orgs_upload_is_not_found(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    with pytest.raises(NotFound):
        get_upload(database.app_api, mine.org_id, mine.owner_id, theirs.upload_id)


@pytest.mark.parametrize("status", [*UPLOAD_STATUSES, "done"])
def test_the_database_accepts_exactly_the_statuses_the_code_knows(
    database: Database, status: str
) -> None:
    tenant = add_tenant(database.admin)

    def add() -> None:
        with database.admin.begin() as connection:
            add_upload(connection, tenant.org_id, tenant.owner_id, status)

    if status in UPLOAD_STATUSES:
        add()
    else:
        with pytest.raises(IntegrityError, match="uploads_status_check"):
            add()


def test_an_uploads_checksum_must_be_lowercase_hex(database: Database) -> None:
    tenant = add_tenant(database.admin)

    with pytest.raises(IntegrityError, match="uploads_sha256_check"), database.admin.begin() as c:
        c.execute(
            text("UPDATE uploads SET sha256 = :bad WHERE id = :id"),
            {"bad": "AB" * 32, "id": tenant.upload_id},
        )
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `just test`
Expected: FAIL. Collection stops with 2 errors: `No module named 'nettriage.application.uploads'` and `No module named 'nettriage.adapters.uploads'`.

To see the other failures, run past those errors:
```bash
cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest --continue-on-collection-errors
```
Expected: `33 failed, 522 passed, 1 skipped, 2 errors`. Every seeded tenant now gets an upload, and there's no `uploads` table yet: `psycopg.errors.UndefinedTable: relation "uploads" does not exist` in `test_tenant_isolation.py` (25), `test_schema.py` (5), `test_audit_log.py` (2) and `test_migrations.py` (1).

- [ ] **Step 3: Write the rules, the migration and the adapter**

`backend/src/nettriage/application/uploads.py`:
```python
"""Upload rules (spec §5.6, §5.7, §7): how big a file may be, where it lives in S3, and how
long its presigned PUT stays valid."""

from __future__ import annotations

import base64
from datetime import timedelta
from typing import Literal, get_args
from uuid import UUID

UploadStatus = Literal["pending_upload", "processing", "analyzed", "failed", "expired"]
UPLOAD_STATUSES: tuple[UploadStatus, ...] = get_args(UploadStatus)

# "25 MB per file" (spec §5.7), binary like the parser's limits: 1 MB = 1,048,576 bytes.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
PRESIGNED_PUT_LIFETIME = timedelta(minutes=5)
MAX_FILENAME = 255


def s3_key(org_id: UUID, upload_id: UUID) -> str:
    """The object's key (spec §5.6). It's built from IDs only: the file's name never reaches S3."""
    return f"orgs/{org_id}/uploads/{upload_id}/raw"


def checksum_header(sha256_hex: str) -> str:
    """The `x-amz-checksum-sha256` value S3 checks the body against: the digest in base64."""
    return base64.b64encode(bytes.fromhex(sha256_hex)).decode()
```

`backend/migrations/versions/0005_uploads.py`:
```python
"""Uploads (spec §5.2, §5.3, §5.4): one row per file a member uploads. The API creates it as
`pending_upload` with a presigned PUT; the analyze worker (Plan 4b) moves it on.

`app_api` may read uploads and insert new ones, but only the columns a new upload has: it can't
set a status, statistics or results, and it can't update or delete a row. Deleting the org
removes its uploads through the cascade.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

STATUSES = "('pending_upload', 'processing', 'analyzed', 'failed', 'expired')"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE uploads (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
            uploaded_by uuid NOT NULL REFERENCES users (id),
            original_filename text NOT NULL
                CHECK (length(original_filename) BETWEEN 1 AND 255),
            s3_key text NOT NULL UNIQUE CHECK (length(s3_key) <= 200),
            size_bytes bigint NOT NULL CHECK (size_bytes > 0),
            sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{{64}}$'),
            format text NOT NULL DEFAULT 'aws_vpc_flow_logs'
                CHECK (format IN ('aws_vpc_flow_logs')),
            status text NOT NULL DEFAULT 'pending_upload' CHECK (status IN {STATUSES}),
            failure_reason text CHECK (length(failure_reason) <= 500),
            rows_parsed integer CHECK (rows_parsed >= 0),
            rows_rejected integer CHECK (rows_rejected >= 0),
            rejected_samples jsonb NOT NULL DEFAULT '[]'::jsonb,
            findings_truncated integer NOT NULL DEFAULT 0 CHECK (findings_truncated >= 0),
            flow_time_range tstzrange,
            processed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, id)
        );
        CREATE INDEX uploads_org_created ON uploads (org_id, created_at DESC);
        CREATE TRIGGER uploads_updated_at BEFORE UPDATE ON uploads
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();

        ALTER TABLE uploads ENABLE ROW LEVEL SECURITY;
        ALTER TABLE uploads FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON uploads
            USING (org_id = app_org_id())
            WITH CHECK (org_id = app_org_id());

        GRANT SELECT ON uploads TO app_api;
        GRANT INSERT (id, org_id, uploaded_by, original_filename, s3_key, size_bytes, sha256)
            ON uploads TO app_api;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE uploads")
```

`backend/src/nettriage/adapters/uploads.py`:
```python
"""Uploads in Postgres (spec §5.2, §7): one row per file, created as `pending_upload` when a
member asks to upload one. Each function is one transaction as `app_api`, so row-level security
applies throughout."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, Row, text

from nettriage.adapters.organizations import lock_org_for
from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.organizations import NotFound, require
from nettriage.application.uploads import UploadStatus

_COLUMNS = (
    "id, org_id, uploaded_by, original_filename, size_bytes, sha256, status, failure_reason, "
    "rows_parsed, rows_rejected, rejected_samples, findings_truncated, "
    "lower(flow_time_range) AS flow_start, upper(flow_time_range) AS flow_end, processed_at, "
    "created_at"
)


@dataclass(frozen=True)
class Upload:
    id: UUID
    org_id: UUID
    uploaded_by: UUID
    original_filename: str
    size_bytes: int
    sha256: str
    status: UploadStatus
    failure_reason: str | None
    rows_parsed: int | None
    rows_rejected: int | None
    rejected_samples: list[dict[str, Any]]
    findings_truncated: int
    flow_start: datetime | None
    flow_end: datetime | None
    processed_at: datetime | None
    created_at: datetime


def create_upload(
    engine: Engine,
    org_id: UUID,
    *,
    user_id: UUID,
    upload_id: UUID,
    filename: str,
    size_bytes: int,
    sha256: str,
    s3_key: str,
) -> Upload:
    """A new `pending_upload` row. Like every change, it decides with the caller's role as it
    is under the org's lock (Plan 3c)."""
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        require(lock_org_for(connection, org_id, user_id), "uploads:create")
        row = connection.execute(
            text(
                "INSERT INTO uploads (id, org_id, uploaded_by, original_filename, s3_key, "  # noqa: S608
                "size_bytes, sha256) VALUES (:id, :org, :user, :filename, :key, :size, :sha256) "
                f"RETURNING {_COLUMNS}"
            ),
            {
                "id": upload_id,
                "org": org_id,
                "user": user_id,
                "filename": filename,
                "key": s3_key,
                "size": size_bytes,
                "sha256": sha256,
            },
        ).one()
    return _upload(row)


def get_upload(engine: Engine, org_id: UUID, user_id: UUID, upload_id: UUID) -> Upload:
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        row = connection.execute(
            text(f"SELECT {_COLUMNS} FROM uploads WHERE org_id = :org AND id = :id"),  # noqa: S608
            {"org": org_id, "id": upload_id},
        ).one_or_none()
    if row is None:
        raise NotFound("No such upload.")
    return _upload(row)


def list_uploads(
    engine: Engine,
    org_id: UUID,
    user_id: UUID,
    *,
    limit: int,
    before: tuple[datetime, UUID] | None = None,
) -> list[Upload]:
    """The org's uploads, newest first; `before` continues after the last upload of a page."""
    before_at, before_id = before or (None, None)
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        rows = connection.execute(
            text(
                f"SELECT {_COLUMNS} FROM uploads WHERE org_id = :org "  # noqa: S608
                "AND (CAST(:before_at AS timestamptz) IS NULL OR (created_at, id) < "
                "(CAST(:before_at AS timestamptz), CAST(:before_id AS uuid))) "
                "ORDER BY created_at DESC, id DESC LIMIT :limit"
            ),
            {"org": org_id, "before_at": before_at, "before_id": before_id, "limit": limit},
        ).all()
    return [_upload(row) for row in rows]


def _upload(row: Row[Any]) -> Upload:
    return Upload(
        id=row.id,
        org_id=row.org_id,
        uploaded_by=row.uploaded_by,
        original_filename=row.original_filename,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        status=row.status,
        failure_reason=row.failure_reason,
        rows_parsed=row.rows_parsed,
        rows_rejected=row.rows_rejected,
        rejected_samples=row.rejected_samples,
        findings_truncated=row.findings_truncated,
        flow_start=row.flow_start,
        flow_end=row.flow_end,
        processed_at=row.processed_at,
        created_at=row.created_at,
    )
```

- [ ] **Step 4: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `570 passed, 1 skipped`.

- [ ] **Step 5: Commit**

```bash
git add backend/migrations backend/src backend/tests
git commit -m "feat(uploads): the uploads table, with row-level security and insert-only grants" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Presigned PUTs, the kill switch and the trace

**Files:**
- Modify: `backend/pyproject.toml`, `backend/uv.lock` (via `uv add`), `backend/src/nettriage/platform/trace_context.py`
- Create: `backend/src/nettriage/adapters/upload_storage.py`, `backend/src/nettriage/adapters/kill_switch.py`
- Test: `backend/tests/unit/adapters/test_upload_storage.py`, `backend/tests/unit/adapters/test_kill_switch.py`, `backend/tests/unit/platform/test_trace_context.py`

**Interfaces:**
- Consumes (Task 1): `PRESIGNED_PUT_LIFETIME` and `checksum_header`. From Plan 3b: `Clock` and the `clock` fixture (`FakeClock`).
- Produces:
  - In `nettriage.adapters.upload_storage`:
    - `S3_CONFIG` and `uploads_client(session) -> S3Client`;
    - `PresignedPut(url: str, headers: dict[str, str], expires_at: datetime)`;
    - `UploadStorage(client, bucket)` with `.presign_put(*, key, size_bytes, sha256, traceparent: str | None, now) -> PresignedPut`.
  - In `nettriage.adapters.kill_switch`:
    - `REFRESH_INTERVAL = timedelta(seconds=60)`;
    - `KillSwitch(read: Callable[[], str], clock, refresh=REFRESH_INTERVAL)` with `.is_on() -> bool`;
    - `ssm_parameter(client, name) -> Callable[[], str]`, which reads a missing parameter as `""`.
  - In `nettriage.platform.trace_context`: `current_traceparent() -> str | None`.

- [ ] **Step 1: Add the S3 type stubs**

```bash
cd backend
uv add --dev "types-boto3[dynamodb,s3,ssm]"
```

- [ ] **Step 2: Write the failing tests**

`backend/tests/unit/adapters/test_upload_storage.py`:
```python
"""Presigned PUTs into the uploads bucket (spec §5.6, §9.1)."""

import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import boto3

from nettriage.adapters.upload_storage import UploadStorage, uploads_client
from nettriage.application.uploads import checksum_header

BUCKET = "nettriage-test-uploads-12345678"
KEY = "orgs/01a0ec4d-060a-7266-a427-fce3ccd0d827/uploads/01a0ec4d-374f-740e-85e7-4ac6ee451cd5/raw"
SHA256 = hashlib.sha256(b"flows").hexdigest()
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def storage() -> UploadStorage:
    session = boto3.session.Session(
        aws_access_key_id="AKIDEXAMPLE",
        aws_secret_access_key="not-a-secret",  # noqa: S106 - presigning is offline
        region_name="eu-north-1",
    )
    return UploadStorage(uploads_client(session), BUCKET)


def signed(url: str) -> tuple[str | None, str, dict[str, str]]:
    parts = urlsplit(url)
    return parts.hostname, parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}


def test_a_presigned_put_pins_the_files_size_checksum_and_trace() -> None:
    put = storage().presign_put(
        key=KEY, size_bytes=5, sha256=SHA256, traceparent=TRACEPARENT, now=NOW
    )

    host, path, query = signed(put.url)
    assert (host, path) == (f"{BUCKET}.s3.eu-north-1.amazonaws.com", f"/{KEY}")
    assert query["X-Amz-SignedHeaders"] == (
        "content-length;host;x-amz-checksum-sha256;x-amz-meta-traceparent"
    )
    assert query["X-Amz-Expires"] == "300"
    assert put.headers == {
        "x-amz-checksum-sha256": checksum_header(SHA256),
        "x-amz-meta-traceparent": TRACEPARENT,
    }
    assert put.expires_at == NOW + timedelta(minutes=5)


def test_without_a_trace_the_browser_sends_only_the_checksum() -> None:
    put = storage().presign_put(key=KEY, size_bytes=5, sha256=SHA256, traceparent=None, now=NOW)

    _, _, query = signed(put.url)
    assert query["X-Amz-SignedHeaders"] == "content-length;host;x-amz-checksum-sha256"
    assert put.headers == {"x-amz-checksum-sha256": checksum_header(SHA256)}
```

`backend/tests/unit/adapters/test_kill_switch.py`:
```python
"""Kill switches in SSM (spec §9.7): re-read once a minute, off until known."""

from datetime import timedelta

import boto3
import pytest
from botocore.exceptions import ClientError
from conftest import FakeClock
from moto import mock_aws

from nettriage.adapters.kill_switch import KillSwitch, ssm_parameter

FAILURE = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Get")


class Parameter:
    """A fake parameter: each read returns (or raises) the next value."""

    def __init__(self, *values: str | Exception) -> None:
        self.values = list(values)
        self.reads = 0

    def __call__(self) -> str:
        value = self.values[min(self.reads, len(self.values) - 1)]
        self.reads += 1
        if isinstance(value, Exception):
            raise value
        return value


@pytest.mark.parametrize(
    ("value", "on"),
    [("true", True), (" TRUE\n", True), ("false", False), ("", False), ("yes", False)],
)
def test_a_switch_is_on_only_while_its_parameter_reads_true(
    clock: FakeClock, value: str, on: bool
) -> None:
    assert KillSwitch(Parameter(value), clock).is_on() is on


def test_it_is_read_at_most_once_a_minute(clock: FakeClock) -> None:
    parameter = Parameter("true", "false")
    switch = KillSwitch(parameter, clock)

    first = switch.is_on()
    clock.advance(timedelta(seconds=59))
    cached = switch.is_on()
    clock.advance(timedelta(seconds=1))
    later = switch.is_on()

    assert (first, cached, later, parameter.reads) == (True, True, False, 2)


def test_a_failed_read_keeps_the_last_value(clock: FakeClock) -> None:
    switch = KillSwitch(Parameter("true", FAILURE), clock)

    first = switch.is_on()
    clock.advance(timedelta(minutes=1))

    assert (first, switch.is_on()) == (True, True)


def test_a_switch_that_was_never_read_is_off(clock: FakeClock) -> None:
    assert KillSwitch(Parameter(FAILURE), clock).is_on() is False


def test_a_parameter_that_does_not_exist_reads_as_off() -> None:
    with mock_aws():
        client = boto3.client("ssm", region_name="eu-north-1")
        client.put_parameter(Name="/nettriage/test/uploads-enabled", Value="true", Type="String")

        present = ssm_parameter(client, "/nettriage/test/uploads-enabled")()
        missing = ssm_parameter(client, "/nettriage/test/missing")()

    assert (present, missing) == ("true", "")
```

`backend/tests/unit/platform/test_trace_context.py`:
```python
"""The W3C traceparent that carries a trace across async hops (spec §9.1)."""

from opentelemetry.sdk.trace import TracerProvider

from nettriage.platform.trace_context import current_traceparent


def test_outside_a_span_there_is_no_traceparent() -> None:
    assert current_traceparent() is None


def test_a_spans_traceparent_names_its_trace_and_span() -> None:
    tracer = TracerProvider().get_tracer("test")

    with tracer.start_as_current_span("upload") as span:
        context = span.get_span_context()
        value = current_traceparent()

    flags = int(context.trace_flags)
    assert value == f"00-{context.trace_id:032x}-{context.span_id:016x}-{flags:02x}"
    assert flags & 0x01  # sampled
```

- [ ] **Step 3: Run them and watch them fail**

Run: `cd backend && uv run python -m pytest tests/unit/adapters/test_upload_storage.py tests/unit/adapters/test_kill_switch.py tests/unit/platform/test_trace_context.py`
Expected: FAIL. Collection stops with 3 errors: `No module named 'nettriage.adapters.upload_storage'`, `No module named 'nettriage.adapters.kill_switch'` and `cannot import name 'current_traceparent' from 'nettriage.platform.trace_context'`.

- [ ] **Step 4: Write the presigner, the kill switch and the traceparent**

`backend/src/nettriage/adapters/upload_storage.py`:
```python
"""The uploads bucket (spec §5.6, §9.1): a presigned PUT pins the file's size, its SHA-256 and
the trace it belongs to, so S3 accepts exactly the file the member declared.

The browser sends `Content-Length` itself; the other signed headers come back from the API in
`PresignedPut.headers`. S3 refuses a body of another length (the signature no longer matches)
and a body with another SHA-256 (the checksum no longer matches)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config

from nettriage.application.uploads import PRESIGNED_PUT_LIFETIME, checksum_header

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client

# Virtual-hosted, regional URLs (`<bucket>.s3.eu-north-1.amazonaws.com`), the origin the CSP
# allows. Checksums only when required, so the SDK adds no header the browser would have to send.
S3_CONFIG = Config(
    signature_version="s3v4",
    s3={"addressing_style": "virtual"},
    request_checksum_calculation="when_required",
)


@dataclass(frozen=True)
class PresignedPut:
    url: str
    headers: dict[str, str]
    expires_at: datetime


class UploadStorage:
    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def presign_put(
        self, *, key: str, size_bytes: int, sha256: str, traceparent: str | None, now: datetime
    ) -> PresignedPut:
        """A PUT URL valid for 5 minutes, and the headers the browser must send with it."""
        headers = {"x-amz-checksum-sha256": checksum_header(sha256)}
        metadata = {}
        if traceparent is not None:
            headers["x-amz-meta-traceparent"] = traceparent
            metadata["traceparent"] = traceparent
        url = self._client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ContentLength": size_bytes,
                "ChecksumSHA256": headers["x-amz-checksum-sha256"],
                "Metadata": metadata,
            },
            ExpiresIn=int(PRESIGNED_PUT_LIFETIME.total_seconds()),
        )
        return PresignedPut(url=url, headers=headers, expires_at=now + PRESIGNED_PUT_LIFETIME)


def uploads_client(session: boto3.session.Session) -> S3Client:
    return session.client("s3", config=S3_CONFIG)
```

`backend/src/nettriage/adapters/kill_switch.py`:
```python
"""Kill switches (spec §9.7): on/off flags in SSM Parameter Store that the owner can flip
without a deploy. Each is re-read at most once a minute."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from botocore.exceptions import BotoCoreError, ClientError

from nettriage.application.clock import Clock

if TYPE_CHECKING:
    from types_boto3_ssm.client import SSMClient

logger = logging.getLogger(__name__)

REFRESH_INTERVAL = timedelta(seconds=60)


class KillSwitch:
    """On only while its parameter reads `true`. A failed read keeps the last value; until one
    read has succeeded, the switch is off, so an unreadable switch never opens anything."""

    def __init__(
        self, read: Callable[[], str], clock: Clock, refresh: timedelta = REFRESH_INTERVAL
    ) -> None:
        self._read = read
        self._clock = clock
        self._refresh = refresh
        self._on = False
        self._read_at: datetime | None = None

    def is_on(self) -> bool:
        now = self._clock()
        if self._read_at is None or now - self._read_at >= self._refresh:
            self._read_at = now
            try:
                self._on = self._read().strip().lower() == "true"
            except BotoCoreError, ClientError:
                logger.warning("kill_switch_read_failed")
        return self._on


def ssm_parameter(client: SSMClient, name: str) -> Callable[[], str]:
    """Reads the parameter's value; a parameter that doesn't exist reads as empty (off)."""

    def read() -> str:
        found = client.get_parameters(Names=[name])["Parameters"]
        return found[0]["Value"] if found else ""

    return read
```

In `backend/src/nettriage/platform/trace_context.py`, replace:
```python
"""The current OpenTelemetry trace and span IDs as hex strings."""

from opentelemetry import trace


```
with:
```python
"""The current OpenTelemetry trace and span IDs as hex strings, and the W3C traceparent."""

from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


```

In `backend/src/nettriage/platform/trace_context.py`, replace:
```python
    return format(context.span_id, "016x") if context.is_valid else None

```
with:
```python
    return format(context.span_id, "016x") if context.is_valid else None


def current_traceparent() -> str | None:
    """The current span as a W3C `traceparent`, for work that continues after an async hop: the
    upload's S3 metadata, then the queue message (spec §9.1)."""
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    return carrier.get("traceparent")

```

- [ ] **Step 5: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `583 passed, 1 skipped`.

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/src backend/tests
git commit -m "feat(uploads): presigned PUTs that pin size, checksum and trace; the uploads kill switch" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: One `Idempotency-Key` flow, and shared page cursors

**Files:**
- Create: `backend/src/nettriage/entrypoints/api/idempotent.py`, `backend/src/nettriage/entrypoints/api/cursors.py`
- Modify: `backend/src/nettriage/entrypoints/api/routes/orgs.py` (uses both; its own copies go)
- Test: `backend/tests/unit/api/test_idempotent.py`, `backend/tests/unit/api/test_cursors.py`; the existing `backend/tests/api/test_org_routes.py` keeps passing unchanged

**Interfaces:**
- Consumes (Plan 3c): `IdempotencyStore`, `StoredResponse`, `IdempotencyMismatch`, `IdempotencyInProgress`, `unavailable` and `get_services`.
- Produces:
  - In `nettriage.entrypoints.api.idempotent`:
    - `IDEMPOTENCY_KEY` and `request_hash(method, path, body: BaseModel) -> str`;
    - `create_once(request, user_id, body, what, work: Callable[[], StoredResponse]) -> StoredResponse`;
    - `created_response(stored, location) -> JSONResponse`.
  - In `nettriage.entrypoints.api.cursors`:
    - `encode_cursor(created_at, item_id) -> str`;
    - `decode_cursor(cursor) -> tuple[datetime, UUID]`, which raises a 422 `HTTPException` for a broken cursor.

- [ ] **Step 1: Write the failing tests**

`backend/tests/unit/api/test_idempotent.py`:
```python
"""What an Idempotency-Key's request hash covers (spec §7)."""

from pydantic import BaseModel

from nettriage.entrypoints.api.idempotent import request_hash


class Body(BaseModel):
    name: str
    size: int


def test_the_same_request_hashes_the_same_whatever_its_field_order() -> None:
    first = Body(name="flows.log", size=1)
    again = Body.model_validate({"size": 1, "name": "flows.log"})

    path = "/api/v1/orgs"
    assert request_hash("POST", path, first) == request_hash("POST", path, again)


def test_another_method_path_or_body_is_another_request() -> None:
    body = Body(name="flows.log", size=1)

    hashes = {
        request_hash("POST", "/api/v1/orgs", body),
        request_hash("PUT", "/api/v1/orgs", body),
        request_hash("POST", "/api/v1/orgs/one/uploads", body),
        request_hash("POST", "/api/v1/orgs/two/uploads", body),
        request_hash("POST", "/api/v1/orgs", Body(name="flows.log", size=2)),
    }

    assert len(hashes) == 5
```

`backend/tests/unit/api/test_cursors.py`:
```python
"""Page cursors (spec §7): opaque to clients, exact for the server."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi import HTTPException

from nettriage.entrypoints.api.cursors import decode_cursor, encode_cursor

AT = datetime(2026, 9, 29, 8, 34, 26, 169892, tzinfo=UTC)
ITEM = UUID("01a0ec4d-060a-7266-a427-fce3ccd0d827")


def test_a_cursor_brings_back_the_exact_position() -> None:
    cursor = encode_cursor(AT, ITEM)

    assert decode_cursor(cursor) == (AT, ITEM)
    assert cursor.isascii()
    assert "=" not in cursor


@pytest.mark.parametrize("cursor", ["", "not-base64!", "WzFd", "WyJ4IiwgInkiXQ"])
def test_a_broken_cursor_is_a_422(cursor: str) -> None:
    with pytest.raises(HTTPException) as refused:
        decode_cursor(cursor)

    assert refused.value.status_code == 422
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && uv run python -m pytest tests/unit/api/test_idempotent.py tests/unit/api/test_cursors.py`
Expected: FAIL. Collection stops with 2 errors: `No module named 'nettriage.entrypoints.api.idempotent'` and `No module named 'nettriage.entrypoints.api.cursors'`.

- [ ] **Step 3: Write the shared flow and the cursors, and move the org routes onto them**

`backend/src/nettriage/entrypoints/api/idempotent.py`:
```python
"""`Idempotency-Key` on POSTs that create something (spec §7): a retry with the same key and the
same request gets the first response instead of creating a second one. Keys are kept for 24
hours per user (adapters/idempotency.py).

A request's hash covers its method and path as well as its body, so a key used to create an org
can't replay that org as an upload, and a key used in one org can't in another."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from nettriage.adapters.idempotency import (
    IdempotencyInProgress,
    IdempotencyMismatch,
    StoredResponse,
)
from nettriage.entrypoints.api.access import unavailable
from nettriage.entrypoints.api.services import get_services

logger = logging.getLogger(__name__)

IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{1,100}")


def request_hash(method: str, path: str, body: BaseModel) -> str:
    canonical = json.dumps(
        {"method": method, "path": path, "body": body.model_dump(mode="json")}, sort_keys=True
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def create_once(
    request: Request,
    user_id: UUID,
    body: BaseModel,
    what: str,
    work: Callable[[], StoredResponse],
) -> StoredResponse:
    """Run `work` once per `Idempotency-Key`. Without a key it simply runs. A failure forgets
    the key, so the client can retry with it; a key that can't be read refuses the request
    (503) rather than risk creating twice."""
    services = get_services(request)
    key = request.headers.get("idempotency-key")
    if key is None:
        return work()
    if not IDEMPOTENCY_KEY.fullmatch(key):
        raise HTTPException(
            422, detail="Idempotency-Key must be 1 to 100 letters, digits, dashes or underscores."
        )
    fingerprint = request_hash(request.method, request.url.path, body)
    try:
        stored = services.idempotency.begin(user_id, key, fingerprint, services.clock())
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
        raise unavailable(what) from None
    if stored is not None:
        return stored
    try:
        created = work()
    except Exception:
        _forget(request, user_id, key)
        raise
    try:
        services.idempotency.finish(user_id, key, created, services.clock())
    except BotoCoreError, ClientError:
        logger.warning("idempotency_write_failed")
    return created


def created_response(stored: StoredResponse, location: str) -> JSONResponse:
    return JSONResponse(stored.body, status_code=stored.status, headers={"Location": location})


def _forget(request: Request, user_id: UUID, key: str) -> None:
    try:
        get_services(request).idempotency.abandon(user_id, key)
    except BotoCoreError, ClientError:
        logger.warning("idempotency_abandon_failed")
```

`backend/src/nettriage/entrypoints/api/cursors.py`:
```python
"""Cursors for lists that page newest first (spec §7): an opaque base64url string holding the
last item's `(created_at, id)`, which the next page continues after."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException


def encode_cursor(created_at: datetime, item_id: UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(item_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        created_at, item_id = json.loads(raw)
        return datetime.fromisoformat(created_at), UUID(item_id)
    except (ValueError, TypeError) as error:
        raise HTTPException(
            422, detail="The cursor isn't valid; start from the first page."
        ) from error
```

In `backend/src/nettriage/entrypoints/api/routes/orgs.py`, replace:
```python

import base64
import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Annotated
```
with:
```python

from typing import Annotated
```

In `backend/src/nettriage/entrypoints/api/routes/orgs.py`, replace:
```python

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
```
with:
```python

from fastapi import APIRouter, Depends, Query, Request, Response

from nettriage.adapters.audit_log import list_org_events
from nettriage.adapters.idempotency import StoredResponse
from nettriage.adapters.organizations import create_org, delete_org, get_org, rename_org
from nettriage.entrypoints.api.access import CurrentSession, OrgContext, OrgMember
from nettriage.entrypoints.api.auditing import audit
from nettriage.entrypoints.api.cursors import decode_cursor, encode_cursor
from nettriage.entrypoints.api.idempotent import create_once, created_response
from nettriage.entrypoints.api.org_errors import org_rules
```

In `backend/src/nettriage/entrypoints/api/routes/orgs.py`, replace:
```python

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/orgs")

IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{1,100}")

```
with:
```python

router = APIRouter(prefix="/v1/orgs")

```

In `backend/src/nettriage/entrypoints/api/routes/orgs.py`, replace:
```python
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

```
with:
```python
    services = get_services(request)

    def work() -> StoredResponse:
        with org_rules(request, "Creating an organization"):
            org = create_org(services.database, user_id=session.user_id, name=body.name)
        audit(
            request,
            action="org.created",
            outcome="success",
            actor_user_id=session.user_id,
            org_id=org.id,
            target_type="organization",
            target_id=str(org.id),
        )
        return StoredResponse(status=201, body=OrgOut.of(org).model_dump(mode="json"))

    stored = create_once(request, session.user_id, body, "Creating an organization", work)
    return created_response(stored, f"/api/v1/orgs/{stored.body['id']}")

```

In `backend/src/nettriage/entrypoints/api/routes/orgs.py`, replace:
```python
    """The org's audit events, newest first, a page at a time (spec §7's cursor pagination)."""
    before = _decode(cursor) if cursor else None
    with org_rules(request, "The audit log"):
```
with:
```python
    """The org's audit events, newest first, a page at a time (spec §7's cursor pagination)."""
    before = decode_cursor(cursor) if cursor else None
    with org_rules(request, "The audit log"):
```

In `backend/src/nettriage/entrypoints/api/routes/orgs.py`, replace:
```python
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
with:
```python
        events=[AuditEventOut(**vars(entry)) for entry in page],
        next_cursor=encode_cursor(page[-1].created_at, page[-1].id) if more else None,
    )

```

- [ ] **Step 4: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `590 passed, 1 skipped`. The org route tests, including the three `Idempotency-Key` tests and the broken-cursor test, pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests
git commit -m "refactor(api): one Idempotency-Key flow whose hash covers method, path and body; shared page cursors" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The upload routes

**Files:**
- Create: `backend/src/nettriage/entrypoints/api/upload_schemas.py`, `backend/src/nettriage/entrypoints/api/routes/uploads.py`
- Modify: `backend/src/nettriage/platform/config.py`, `backend/src/nettriage/entrypoints/api/services.py`, `backend/src/nettriage/entrypoints/api/wiring.py`, `backend/src/nettriage/entrypoints/api/app.py`
- Modify (test harness): `backend/tests/conftest.py` (`UPLOADS_BUCKET`, `presigning_session()`, and the two new services)
- Test: `backend/tests/api/test_upload_routes.py`, `backend/tests/unit/api/test_wiring.py`, `backend/tests/security/test_route_access.py`, `backend/tests/security/test_authorization_matrix.py`, `backend/tests/security/test_stale_roles.py`

**Interfaces:**
- Consumes:
  - Task 1: `create_upload`, `get_upload`, `list_uploads`, `Upload`, `s3_key`, `MAX_UPLOAD_BYTES`, `MAX_FILENAME` and `add_upload`.
  - Task 2: `UploadStorage`, `uploads_client`, `KillSwitch`, `ssm_parameter` and `current_traceparent`.
  - Task 3: `create_once`, `created_response`, `encode_cursor` and `decode_cursor`.
  - Plan 3c: `OrgMember`, `OrgContext`, `enforce`, `org_rules`, `audit`, `Strict` and `POLICIES["uploads.org"]`.
- Produces:
  - `Settings.uploads_bucket` and `Settings.uploads_enabled_parameter` (`NETTRIAGE_UPLOADS_BUCKET`, `NETTRIAGE_UPLOADS_ENABLED_PARAMETER`);
  - `Services.upload_storage` and `Services.uploads_switch`;
  - the routes, all under `/api`:

    | Route | Access | Success |
    |---|---|---|
    | `POST /v1/orgs/{org_id}/uploads` | `uploads:create`, plus `uploads.org` | 201 with `Location`: `{upload, upload_url, upload_headers, expires_at}`; `Idempotency-Key` optional; 503 while paused |
    | `GET /v1/orgs/{org_id}/uploads` | `uploads:read` | 200, `{uploads, next_cursor}`; `limit` 1 to 100 (default 50), `cursor` |
    | `GET /v1/orgs/{org_id}/uploads/{upload_id}` | `uploads:read` | 200, the upload |

  - the audit event `upload.created`, whose details hold `{size_bytes}`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/api/test_upload_routes.py`:
```python
"""Uploads through the API (spec §4.2, §7): a presigned PUT for exactly the declared file, the
org's daily quota, Idempotency-Key, the kill switch, and reading uploads back."""

import hashlib
from dataclasses import replace
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from browser import signed_in_as
from conftest import UPLOADS_BUCKET, Database, FakeClock
from fake_idp import APP_ORIGIN
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from sqlalchemy import text
from tenantdata import add_member, add_org, add_user

from nettriage.adapters.kill_switch import KillSwitch
from nettriage.application.uploads import MAX_UPLOAD_BYTES, checksum_header
from nettriage.entrypoints.api.app import create_app
from nettriage.entrypoints.api.services import Services
from nettriage.platform.config import Settings
from nettriage.platform.telemetry import create_tracer_provider

SHA256 = hashlib.sha256(b"flows").hexdigest()
FILE = {"filename": "vpc-flows.log.gz", "size_bytes": 5, "sha256": SHA256.upper()}


@pytest.fixture
def org(database: Database) -> tuple[UUID, UUID]:
    with database.admin.begin() as connection:
        owner = add_user(connection)
        org_id = add_org(connection, owner)
        add_member(connection, org_id, owner, "owner")
    return org_id, owner


@pytest.fixture
def headers(
    database_client: TestClient, services: Services, clock: FakeClock, org: tuple[UUID, UUID]
) -> dict[str, str]:
    return signed_in_as(database_client, services.sessions, org[1], clock())


def post(
    client: TestClient, org: UUID, headers: dict[str, str], body: dict[str, Any] | None = None
) -> Any:
    return client.post(f"/api/v1/orgs/{org}/uploads", json=body or FILE, headers=headers)


def uploads_in(database: Database, org: UUID) -> int:
    with database.admin.begin() as connection:
        count: int = connection.execute(
            text("SELECT count(*) FROM uploads WHERE org_id = :org"), {"org": org}
        ).scalar_one()
    return count


def test_an_upload_gets_a_put_for_exactly_the_declared_file(
    database_client: TestClient,
    headers: dict[str, str],
    org: tuple[UUID, UUID],
    database: Database,
    clock: FakeClock,
) -> None:
    response = post(database_client, org[0], headers)

    assert response.status_code == 201, response.text
    created = response.json()
    upload = created["upload"]
    assert (upload["status"], upload["size_bytes"], upload["sha256"]) == (
        "pending_upload",
        5,
        SHA256,
    )
    assert upload["original_filename"] == "vpc-flows.log.gz"
    assert response.headers["location"] == f"/api/v1/orgs/{org[0]}/uploads/{upload['id']}"
    url = urlsplit(created["upload_url"])
    query = parse_qs(url.query)
    assert url.hostname == f"{UPLOADS_BUCKET}.s3.eu-north-1.amazonaws.com"
    assert url.path == f"/orgs/{org[0]}/uploads/{upload['id']}/raw"
    assert query["X-Amz-SignedHeaders"] == ["content-length;host;x-amz-checksum-sha256"]
    assert created["upload_headers"] == {"x-amz-checksum-sha256": checksum_header(SHA256)}
    assert created["expires_at"] == (clock() + timedelta(minutes=5)).isoformat().replace(
        "+00:00", "Z"
    )
    with database.admin.begin() as connection:
        audited = connection.execute(
            text("SELECT action, target_id, details FROM audit_log WHERE org_id = :org"),
            {"org": org[0]},
        ).one()
    assert tuple(audited) == ("upload.created", upload["id"], {"size_bytes": 5})


@pytest.mark.parametrize(
    "body",
    [
        {**FILE, "size_bytes": 0},
        {**FILE, "size_bytes": MAX_UPLOAD_BYTES + 1},
        {**FILE, "filename": "   "},
        {**FILE, "filename": "flows\n.log"},
        {**FILE, "filename": "x" * 256},
        {**FILE, "sha256": "zz" * 32},
        {**FILE, "sha256": SHA256[:-1]},
        {**FILE, "status": "analyzed"},
        {"filename": "flows.log", "size_bytes": 5},
    ],
)
def test_a_file_needs_a_name_a_size_up_to_25_mb_and_a_sha256(
    database_client: TestClient,
    headers: dict[str, str],
    org: tuple[UUID, UUID],
    database: Database,
    body: dict[str, Any],
) -> None:
    response = post(database_client, org[0], headers, body)

    assert response.status_code == 422
    assert uploads_in(database, org[0]) == 0


def test_an_org_may_start_five_uploads_at_once_and_then_waits(
    database_client: TestClient, headers: dict[str, str], org: tuple[UUID, UUID]
) -> None:
    statuses = [post(database_client, org[0], headers).status_code for _ in range(6)]
    limited = post(database_client, org[0], headers)

    assert statuses == [201] * 5 + [429]
    assert int(limited.headers["retry-after"]) > 0


def test_a_retry_with_the_same_idempotency_key_returns_the_same_upload(
    database_client: TestClient, headers: dict[str, str], org: tuple[UUID, UUID], database: Database
) -> None:
    key = {"Idempotency-Key": f"upload-{uuid4().hex}"}

    first = post(database_client, org[0], {**headers, **key})
    again = post(database_client, org[0], {**headers, **key})

    assert (first.status_code, again.status_code) == (201, 201)
    assert again.json() == first.json()
    assert uploads_in(database, org[0]) == 1


def test_a_key_that_created_an_org_can_not_create_an_upload(
    database_client: TestClient, headers: dict[str, str], org: tuple[UUID, UUID]
) -> None:
    key = {"Idempotency-Key": f"shared-{uuid4().hex}"}
    created_org = database_client.post(
        "/api/v1/orgs", json={"name": "Another"}, headers={**headers, **key}
    )

    reused = post(database_client, org[0], {**headers, **key})

    assert (created_org.status_code, reused.status_code) == (201, 422)


def test_paused_uploads_are_refused_and_nothing_is_created(
    settings: Settings,
    services: Services,
    database: Database,
    clock: FakeClock,
    org: tuple[UUID, UUID],
) -> None:
    paused = replace(
        services, database=database.app_api, uploads_switch=KillSwitch(lambda: "false", clock)
    )
    client = TestClient(create_app(settings, paused), base_url=APP_ORIGIN)
    headers = signed_in_as(client, services.sessions, org[1], clock())

    response = post(client, org[0], headers)

    assert response.status_code == 503
    assert uploads_in(database, org[0]) == 0


def test_the_put_carries_the_requests_trace(
    services: Services, database: Database, clock: FakeClock, org: tuple[UUID, UUID]
) -> None:
    """The worker links its spans to this trace through the object's metadata (spec §9.1)."""
    exporter = InMemorySpanExporter()
    settings = Settings(stage="local", version="9.9.9")
    tracer_provider = create_tracer_provider(settings, exporter)
    app = create_app(
        settings, replace(services, database=database.app_api), tracer_provider=tracer_provider
    )
    client = TestClient(app, base_url=APP_ORIGIN)
    headers = signed_in_as(client, services.sessions, org[1], clock())

    created = post(client, org[0], headers).json()
    tracer_provider.force_flush()

    [server] = [s for s in exporter.get_finished_spans() if s.kind == SpanKind.SERVER]
    traceparent = created["upload_headers"]["x-amz-meta-traceparent"]
    assert traceparent.split("-")[1] == format(server.context.trace_id, "032x")
    signed = parse_qs(urlsplit(created["upload_url"]).query)["X-Amz-SignedHeaders"]
    assert signed == ["content-length;host;x-amz-checksum-sha256;x-amz-meta-traceparent"]


def test_uploads_are_listed_newest_first_page_by_page(
    database_client: TestClient, headers: dict[str, str], org: tuple[UUID, UUID]
) -> None:
    ids = [post(database_client, org[0], headers).json()["upload"]["id"] for _ in range(3)]

    first = database_client.get(f"/api/v1/orgs/{org[0]}/uploads", params={"limit": 2}).json()
    rest = database_client.get(
        f"/api/v1/orgs/{org[0]}/uploads", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()

    listed = [upload["id"] for upload in first["uploads"] + rest["uploads"]]
    assert listed == ids[::-1]
    assert rest["next_cursor"] is None


def test_an_upload_is_read_by_id_but_not_through_another_org(
    database_client: TestClient,
    headers: dict[str, str],
    org: tuple[UUID, UUID],
    database: Database,
) -> None:
    upload = post(database_client, org[0], headers).json()["upload"]
    with database.admin.begin() as connection:
        other = add_org(connection, org[1])
        add_member(connection, other, org[1], "owner")

    mine = database_client.get(f"/api/v1/orgs/{org[0]}/uploads/{upload['id']}")
    through_other = database_client.get(f"/api/v1/orgs/{other}/uploads/{upload['id']}")

    assert (mine.status_code, mine.json()) == (200, upload)
    assert through_other.status_code == 404
```

In `backend/tests/unit/api/test_wiring.py`, replace:
```python
from collections.abc import Iterator

```
with:
```python
from collections.abc import Iterator
from datetime import UTC, datetime

```

In `backend/tests/unit/api/test_wiring.py`, replace:
```python
    database_url_parameter="/nettriage/dev/db/app-api-url",
)
OIDC = {
```
with:
```python
    database_url_parameter="/nettriage/dev/db/app-api-url",
    uploads_bucket="nettriage-dev-uploads-12345678",
    uploads_enabled_parameter="/nettriage/dev/kill/uploads-enabled",
)
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
OIDC = {
```

In `backend/tests/unit/api/test_wiring.py`, replace:
```python
        )
        yield session
```
with:
```python
        )
        ssm.put_parameter(Name=SETTINGS.uploads_enabled_parameter, Value="true", Type="String")
        yield session
```

In `backend/tests/unit/api/test_wiring.py`, replace:
```python
    assert services.database.pool.size() == 1  # type: ignore[attr-defined]

```
with:
```python
    assert services.database.pool.size() == 1  # type: ignore[attr-defined]
    assert services.uploads_switch.is_on()
    put = services.upload_storage.presign_put(
        key="orgs/o/uploads/u/raw", size_bytes=1, sha256="0" * 64, traceparent=None, now=NOW
    )
    assert put.url.startswith("https://nettriage-dev-uploads-12345678.s3.eu-north-1.amazonaws.com/")

```

In `backend/tests/security/test_route_access.py`, replace:
```python
    ("POST", "/api/v1/invitations/accept"): "signed in",
}
```
with:
```python
    ("POST", "/api/v1/invitations/accept"): "signed in",
    ("POST", "/api/v1/orgs/{org_id}/uploads"): "uploads:create",
    ("GET", "/api/v1/orgs/{org_id}/uploads"): "uploads:read",
    ("GET", "/api/v1/orgs/{org_id}/uploads/{upload_id}"): "uploads:read",
}
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
from fastapi.testclient import TestClient
from tenantdata import add_invitation, add_member, add_org, add_user

```
with:
```python
from fastapi.testclient import TestClient
from tenantdata import add_invitation, add_member, add_org, add_upload, add_user

```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
    "accept invitation": ("POST", "/api/v1/invitations/accept", {"token": "{token}"}),
}
```
with:
```python
    "accept invitation": ("POST", "/api/v1/invitations/accept", {"token": "{token}"}),
    "create upload": (
        "POST",
        "/api/v1/orgs/{org}/uploads",
        {"filename": "flows.log", "size_bytes": 1024, "sha256": "{sha256}"},
    ),
    "list uploads": ("GET", "/api/v1/orgs/{org}/uploads", None),
    "read upload": ("GET", "/api/v1/orgs/{org}/uploads/{upload}", None),
}
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
    "accept invitation": (403, 403, 403, 403, 200, 401),
}
```
with:
```python
    "accept invitation": (403, 403, 403, 403, 200, 401),
    "create upload": (201, 201, 201, 403, 404, 401),
    "list uploads": (200, 200, 200, 200, 404, 401),
    "read upload": (200, 200, 200, 200, 404, 401),
}
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
        add_invitation(connection, org, people["owner"], stranger_email)
        connection.exec_driver_sql(
```
with:
```python
        add_invitation(connection, org, people["owner"], stranger_email)
        upload = add_upload(connection, org, people["analyst"])
        connection.exec_driver_sql(
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
            "new_email": f"{uuid4().hex}@example.com",
        },
```
with:
```python
            "new_email": f"{uuid4().hex}@example.com",
            "upload": str(upload),
            "sha256": "ab" * 32,
        },
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
                path.split("?")[0],
                {"org": "{org_id}", "target": "{user_id}", "invitation": "{invitation_id}"},
            ),
```
with:
```python
                path.split("?")[0],
                {
                    "org": "{org_id}",
                    "target": "{user_id}",
                    "invitation": "{invitation_id}",
                    "upload": "{upload_id}",
                },
            ),
```

In `backend/tests/security/test_stale_roles.py`, replace:
```python
def snapshot(database: Database, org: UUID) -> tuple[Any, ...]:
    """Everything a change could touch: the name, the members and their roles, and the
    pending invitations."""
    with database.admin.begin() as connection:
```
with:
```python
def snapshot(database: Database, org: UUID) -> tuple[Any, ...]:
    """Everything a change could touch: the name, the members and their roles, the pending
    invitations and the uploads."""
    with database.admin.begin() as connection:
```

In `backend/tests/security/test_stale_roles.py`, replace:
```python
        ).all()
    return name, tuple(members), tuple(invitations)

```
with:
```python
        ).all()
        uploads = connection.execute(
            text("SELECT id FROM uploads WHERE org_id = :org ORDER BY id"), {"org": org}
        ).all()
    return name, tuple(members), tuple(invitations), tuple(uploads)

```

In `backend/tests/security/test_stale_roles.py`, replace:
```python
        ("revoke", "admin", None, 404),
    ],
```
with:
```python
        ("revoke", "admin", None, 404),
        ("upload", "analyst", "viewer", 403),
    ],
```

In `backend/tests/security/test_stale_roles.py`, replace:
```python
    headers = signed_in_as(database_client, services.sessions, actor, clock())
    requests: dict[str, tuple[str, str, dict[str, str] | None]] = {
        "rename": ("PATCH", f"/api/v1/orgs/{org}", {"name": "Renamed"}),
```
with:
```python
    headers = signed_in_as(database_client, services.sessions, actor, clock())
    requests: dict[str, tuple[str, str, dict[str, Any] | None]] = {
        "rename": ("PATCH", f"/api/v1/orgs/{org}", {"name": "Renamed"}),
```

In `backend/tests/security/test_stale_roles.py`, replace:
```python
        "revoke": ("DELETE", f"/api/v1/orgs/{org}/invitations/{invitation}", None),
    }
```
with:
```python
        "revoke": ("DELETE", f"/api/v1/orgs/{org}/invitations/{invitation}", None),
        "upload": (
            "POST",
            f"/api/v1/orgs/{org}/uploads",
            {"filename": "flows.log", "size_bytes": 1, "sha256": "0" * 64},
        ),
    }
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `just test`
Expected: FAIL. Collection stops with 2 errors:
- `ImportError: cannot import name 'UPLOADS_BUCKET' from 'conftest'` in `test_upload_routes.py`;
- a `ValidationError` for `Settings` in `test_wiring.py`, which doesn't know `uploads_bucket` or `uploads_enabled_parameter` yet.

Run past those errors:
```bash
cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest --continue-on-collection-errors
```
Expected: `17 failed, 590 passed, 1 skipped, 2 errors`. The failures are:
- the route declarations;
- the stale-role `upload` case;
- 15 of the matrix's 18 new cases. The 3 non-member calls pass already: they expect the 404 a missing route also gives.

- [ ] **Step 3: Give the API its bucket and kill switch**

In `backend/src/nettriage/platform/config.py`, replace:
```python
    database_url_parameter: str = ""

```
with:
```python
    database_url_parameter: str = ""
    # The uploads bucket, and the SSM parameter that switches uploads on and off (spec §9.7).
    uploads_bucket: str = ""
    uploads_enabled_parameter: str = ""

```

In `backend/src/nettriage/entrypoints/api/services.py`, replace:
```python
from nettriage.adapters.idempotency import IdempotencyStore
from nettriage.adapters.login_states import LoginStateStore
```
with:
```python
from nettriage.adapters.idempotency import IdempotencyStore
from nettriage.adapters.kill_switch import KillSwitch
from nettriage.adapters.login_states import LoginStateStore
```

In `backend/src/nettriage/entrypoints/api/services.py`, replace:
```python
from nettriage.adapters.sessions import SessionStore
from nettriage.application.clock import Clock
```
with:
```python
from nettriage.adapters.sessions import SessionStore
from nettriage.adapters.upload_storage import UploadStorage
from nettriage.application.clock import Clock
```

In `backend/src/nettriage/entrypoints/api/services.py`, replace:
```python
    idempotency: IdempotencyStore
    oidc: OidcClient
```
with:
```python
    idempotency: IdempotencyStore
    upload_storage: UploadStorage
    uploads_switch: KillSwitch
    oidc: OidcClient
```

In `backend/src/nettriage/entrypoints/api/wiring.py`, replace:
```python
from nettriage.adapters.idempotency import IdempotencyStore
from nettriage.adapters.login_states import LoginStateStore
```
with:
```python
from nettriage.adapters.idempotency import IdempotencyStore
from nettriage.adapters.kill_switch import KillSwitch, ssm_parameter
from nettriage.adapters.login_states import LoginStateStore
```

In `backend/src/nettriage/entrypoints/api/wiring.py`, replace:
```python
from nettriage.adapters.sessions import SessionStore
from nettriage.application.clock import system_clock
```
with:
```python
from nettriage.adapters.sessions import SessionStore
from nettriage.adapters.upload_storage import UploadStorage, uploads_client
from nettriage.application.clock import system_clock
```

In `backend/src/nettriage/entrypoints/api/wiring.py`, replace:
```python
    session = session or boto3.session.Session()
    values = read_parameters(
        session.client("ssm", config=AWS_CONFIG),
        [settings.oidc_parameter, settings.oidc_secret_parameter, settings.database_url_parameter],
```
with:
```python
    session = session or boto3.session.Session()
    ssm = session.client("ssm", config=AWS_CONFIG)
    values = read_parameters(
        ssm,
        [settings.oidc_parameter, settings.oidc_secret_parameter, settings.database_url_parameter],
```

In `backend/src/nettriage/entrypoints/api/wiring.py`, replace:
```python
        idempotency=IdempotencyStore(dynamodb, table),
        oidc=OidcClient(
```
with:
```python
        idempotency=IdempotencyStore(dynamodb, table),
        upload_storage=UploadStorage(uploads_client(session), settings.uploads_bucket),
        uploads_switch=KillSwitch(
            ssm_parameter(ssm, settings.uploads_enabled_parameter), system_clock
        ),
        oidc=OidcClient(
```

In `backend/tests/conftest.py`, replace:
```python
from nettriage.adapters.idempotency import IdempotencyStore
from nettriage.adapters.login_states import LoginStateStore
```
with:
```python
from nettriage.adapters.idempotency import IdempotencyStore
from nettriage.adapters.kill_switch import KillSwitch
from nettriage.adapters.login_states import LoginStateStore
```

In `backend/tests/conftest.py`, replace:
```python
from nettriage.adapters.sessions import SessionStore
from nettriage.entrypoints.api.app import create_app
```
with:
```python
from nettriage.adapters.sessions import SessionStore
from nettriage.adapters.upload_storage import UploadStorage, uploads_client
from nettriage.entrypoints.api.app import create_app
```

In `backend/tests/conftest.py`, replace:
```python
NO_DATABASE = "postgresql://unused@db.nettriage.invalid/unused"  # fails fast if ever used

```
with:
```python
NO_DATABASE = "postgresql://unused@db.nettriage.invalid/unused"  # fails fast if ever used
UPLOADS_BUCKET = "nettriage-test-uploads-00000000"


def presigning_session() -> boto3.session.Session:
    """Presigning is offline: dummy credentials sign URLs that nothing ever calls."""
    return boto3.session.Session(
        aws_access_key_id="testing",
        aws_secret_access_key="testing",  # noqa: S106 - not a secret
        region_name="eu-north-1",
    )

```

In `backend/tests/conftest.py`, replace:
```python
        idempotency=IdempotencyStore(client, table),
        oidc=OidcClient(idp_settings(), httpx.Client(transport=idp.transport()), clock),
```
with:
```python
        idempotency=IdempotencyStore(client, table),
        upload_storage=UploadStorage(uploads_client(presigning_session()), UPLOADS_BUCKET),
        uploads_switch=KillSwitch(lambda: "true", clock),
        oidc=OidcClient(idp_settings(), httpx.Client(transport=idp.transport()), clock),
```

- [ ] **Step 4: Write the upload schemas and routes, and register them**

`backend/src/nettriage/entrypoints/api/upload_schemas.py`:
```python
"""Request and response bodies for uploads (spec §7). The request model forbids fields it doesn't
declare, so a client can't set a status or results (OWASP API3)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from nettriage.adapters.uploads import Upload
from nettriage.application.uploads import MAX_FILENAME, MAX_UPLOAD_BYTES, UploadStatus
from nettriage.entrypoints.api.schemas import Strict

# Shown back to the org's members only; it never becomes part of an S3 key, a log or an audit
# event. No control characters (line breaks, tabs, NUL).
FileName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=MAX_FILENAME, pattern=r"^[^\x00-\x1f\x7f]+$"
    ),
]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-fA-F]{64}$", to_lower=True)]


class UploadIn(Strict):
    filename: FileName
    size_bytes: Annotated[int, Field(ge=1, le=MAX_UPLOAD_BYTES)]
    sha256: Sha256Hex


class UploadOut(BaseModel):
    id: UUID
    original_filename: str
    size_bytes: int
    sha256: str
    status: UploadStatus
    failure_reason: str | None
    rows_parsed: int | None
    rows_rejected: int | None
    rejected_samples: list[dict[str, Any]]
    findings_truncated: int
    flow_start: datetime | None
    flow_end: datetime | None
    uploaded_by: UUID
    created_at: datetime
    processed_at: datetime | None

    @classmethod
    def of(cls, upload: Upload) -> UploadOut:
        return cls(
            id=upload.id,
            original_filename=upload.original_filename,
            size_bytes=upload.size_bytes,
            sha256=upload.sha256,
            status=upload.status,
            failure_reason=upload.failure_reason,
            rows_parsed=upload.rows_parsed,
            rows_rejected=upload.rows_rejected,
            rejected_samples=upload.rejected_samples,
            findings_truncated=upload.findings_truncated,
            flow_start=upload.flow_start,
            flow_end=upload.flow_end,
            uploaded_by=upload.uploaded_by,
            created_at=upload.created_at,
            processed_at=upload.processed_at,
        )


class CreatedUploadOut(BaseModel):
    upload: UploadOut
    # PUT the file here within 5 minutes, sending these headers; the browser adds
    # Content-Length, which must equal size_bytes.
    upload_url: str
    upload_headers: dict[str, str]
    expires_at: datetime


class UploadsOut(BaseModel):
    uploads: list[UploadOut]
    next_cursor: str | None
```

`backend/src/nettriage/entrypoints/api/routes/uploads.py`:
```python
"""Uploads (spec §4.2, §7): register a file and get a presigned PUT for it, then list and read
the org's uploads. The file itself goes from the browser straight to S3."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid7

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from nettriage.adapters.idempotency import StoredResponse
from nettriage.adapters.uploads import create_upload, get_upload, list_uploads
from nettriage.application.rate_limits import POLICIES
from nettriage.application.uploads import s3_key
from nettriage.entrypoints.api.access import OrgContext, OrgMember, enforce
from nettriage.entrypoints.api.auditing import audit
from nettriage.entrypoints.api.cursors import decode_cursor, encode_cursor
from nettriage.entrypoints.api.idempotent import create_once, created_response
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.services import get_services
from nettriage.entrypoints.api.upload_schemas import (
    CreatedUploadOut,
    UploadIn,
    UploadOut,
    UploadsOut,
)
from nettriage.platform.trace_context import current_traceparent

router = APIRouter(prefix="/v1/orgs/{org_id}/uploads")


@router.post("", status_code=201, response_model=CreatedUploadOut)
def create(
    request: Request,
    org_id: UUID,
    body: UploadIn,
    org: Annotated[OrgContext, Depends(OrgMember("uploads:create"))],
) -> Response:
    """Register a file and get a presigned PUT for it, valid for 5 minutes. It counts against
    the org's 20 uploads a day; an `Idempotency-Key` makes a retry return the same upload."""
    services = get_services(request)
    if not services.uploads_switch.is_on():
        raise HTTPException(503, detail="Uploads are paused for now. Try again later.")

    def work() -> StoredResponse:
        enforce(request, POLICIES["uploads.org"], str(org.org_id), actor=org.user_id)
        upload_id = uuid7()
        key = s3_key(org.org_id, upload_id)
        with org_rules(request, "Creating an upload", org=org, permission="uploads:create"):
            upload = create_upload(
                services.database,
                org.org_id,
                user_id=org.user_id,
                upload_id=upload_id,
                filename=body.filename,
                size_bytes=body.size_bytes,
                sha256=body.sha256,
                s3_key=key,
            )
        put = services.upload_storage.presign_put(
            key=key,
            size_bytes=upload.size_bytes,
            sha256=upload.sha256,
            traceparent=current_traceparent(),
            now=services.clock(),
        )
        audit(
            request,
            action="upload.created",
            outcome="success",
            actor_user_id=org.user_id,
            org_id=org.org_id,
            target_type="upload",
            target_id=str(upload.id),
            details={"size_bytes": upload.size_bytes},
        )
        created = CreatedUploadOut(
            upload=UploadOut.of(upload),
            upload_url=put.url,
            upload_headers=put.headers,
            expires_at=put.expires_at,
        )
        return StoredResponse(status=201, body=created.model_dump(mode="json"))

    stored = create_once(request, org.user_id, body, "Creating an upload", work)
    upload_id = stored.body["upload"]["id"]
    return created_response(stored, f"/api/v1/orgs/{org.org_id}/uploads/{upload_id}")


@router.get("")
def uploads(
    request: Request,
    org_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("uploads:read"))],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> UploadsOut:
    """The org's uploads, newest first, a page at a time."""
    before = decode_cursor(cursor) if cursor else None
    with org_rules(request, "The upload list"):
        found = list_uploads(
            get_services(request).database, org.org_id, org.user_id, limit=limit + 1, before=before
        )
    page = found[:limit]
    more = len(found) > limit
    return UploadsOut(
        uploads=[UploadOut.of(upload) for upload in page],
        next_cursor=encode_cursor(page[-1].created_at, page[-1].id) if more else None,
    )


@router.get("/{upload_id}")
def upload(
    request: Request,
    org_id: UUID,
    upload_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("uploads:read"))],
) -> UploadOut:
    """One upload: its status and, once analyzed, its statistics."""
    with org_rules(request, "This upload"):
        return UploadOut.of(
            get_upload(get_services(request).database, org.org_id, org.user_id, upload_id)
        )
```

In `backend/src/nettriage/entrypoints/api/app.py`, replace:
```python
from nettriage.entrypoints.api.access import RedirectInstead
from nettriage.entrypoints.api.routes import auth, health, invitations, me, members, orgs
from nettriage.entrypoints.api.services import Services
```
with:
```python
from nettriage.entrypoints.api.access import RedirectInstead
from nettriage.entrypoints.api.routes import (
    auth,
    health,
    invitations,
    me,
    members,
    orgs,
    uploads,
)
from nettriage.entrypoints.api.services import Services
```

In `backend/src/nettriage/entrypoints/api/app.py`, replace:
```python
    app.middleware("http")(add_rate_limit_headers)
    for module in (health, auth, me, orgs, members, invitations):
        app.include_router(module.router, prefix="/api")
```
with:
```python
    app.middleware("http")(add_rate_limit_headers)
    for module in (health, auth, me, orgs, members, invitations, uploads):
        app.include_router(module.router, prefix="/api")
```

- [ ] **Step 5: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `626 passed, 1 skipped`.

The matrix grows to 16 endpoints × 6 callers (96 cases). A viewer can list and read uploads but not create one, and a non-member gets 404 everywhere.

- [ ] **Step 6: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(uploads): upload routes with presigned PUTs, the daily quota, Idempotency-Key and the kill switch" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: The uploads bucket, the kill switch, the API's access and the CSP

**Files:**
- Create: `infra/modules/pipeline/main.tf`, `variables.tf`, `outputs.tf`, `versions.tf`, `tests/pipeline.tftest.hcl` (and `.terraform.lock.hcl`, generated by `just tf-check`)
- Modify: `infra/modules/app/main.tf`, `infra/modules/app/variables.tf`, `infra/modules/app/tests/app.tftest.hcl`, `infra/envs/dev/main.tf`, `.checkov.yaml`, `justfile`, `.github/workflows/ci.yml`, `tools/smoke.py`
- Test: `infra/modules/pipeline/tests/pipeline.tftest.hcl`, `infra/modules/app/tests/app.tftest.hcl`, `tools/tests/test_smoke.py`

**Interfaces:**
- Consumes (Task 4): the API reads `NETTRIAGE_UPLOADS_BUCKET` and `NETTRIAGE_UPLOADS_ENABLED_PARAMETER`. From Plan 1: `module.edge`'s `csp_connect_src_extra` and `distribution_domain`.
- Produces:
  - `module.pipeline` (inputs: `stage`, `app_origin`) with the outputs `uploads_bucket`, `uploads_bucket_arn`, `uploads_origin` (`https://<bucket>.s3.eu-north-1.amazonaws.com`) and `uploads_enabled_parameter` (`/nettriage/<stage>/kill/uploads-enabled`);
  - the `app` module's new inputs `uploads_bucket`, `uploads_bucket_arn` and `uploads_enabled_parameter`, and the `api_uploads` IAM policy;
  - four smoke checks: `csp allows the uploads bucket`, `uploads bucket lets the app PUT`, `uploads bucket refuses other origins` and `uploads bucket refuses anonymous writes`.

- [ ] **Step 1: Write the failing tests**

`infra/modules/pipeline/tests/pipeline.tftest.hcl`:
```hcl
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
}

variables {
  stage      = "dev"
  app_origin = "https://d111111abcdef8.cloudfront.net"
}

run "the_bucket_name_does_not_expose_the_account_id" {
  command = apply

  assert {
    condition     = aws_s3_bucket.uploads.bucket == "nettriage-dev-uploads-${substr(sha256("123456789012"), 0, 8)}"
    error_message = "The bucket is nettriage-<stage>-uploads-<8 hex of the account ID's hash>."
  }
  assert {
    condition     = !strcontains(aws_s3_bucket.uploads.bucket, "123456789012")
    error_message = "The CSP shows the bucket's name to everyone, so it must not hold the account ID."
  }
}

run "the_bucket_is_private_encrypted_and_tls_only" {
  command = apply

  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.uploads.block_public_acls,
      aws_s3_bucket_public_access_block.uploads.block_public_policy,
      aws_s3_bucket_public_access_block.uploads.ignore_public_acls,
      aws_s3_bucket_public_access_block.uploads.restrict_public_buckets,
    ])
    error_message = "Public access is blocked (spec §5.6)."
  }
  assert {
    condition     = one(one(aws_s3_bucket_server_side_encryption_configuration.uploads.rule).apply_server_side_encryption_by_default).sse_algorithm == "AES256"
    error_message = "Uploads are encrypted with SSE-S3 (spec §5.6)."
  }
  assert {
    condition     = jsondecode(aws_s3_bucket_policy.uploads.policy).Statement[0].Condition.Bool["aws:SecureTransport"] == "false" && jsondecode(aws_s3_bucket_policy.uploads.policy).Statement[0].Effect == "Deny"
    error_message = "Requests without TLS are denied (spec §5.6)."
  }
  assert {
    condition     = one(aws_s3_bucket_ownership_controls.uploads.rule).object_ownership == "BucketOwnerEnforced"
    error_message = "ACLs are disabled."
  }
}

run "only_the_app_may_put_from_a_browser" {
  command = apply

  assert {
    condition     = one(aws_s3_bucket_cors_configuration.uploads.cors_rule).allowed_methods == toset(["PUT"])
    error_message = "Browsers may only PUT (spec §5.6)."
  }
  assert {
    condition     = one(aws_s3_bucket_cors_configuration.uploads.cors_rule).allowed_origins == toset(["https://d111111abcdef8.cloudfront.net"])
    error_message = "Only the app's origin may PUT (spec §5.6)."
  }
  assert {
    condition     = one(aws_s3_bucket_cors_configuration.uploads.cors_rule).allowed_headers == toset(["content-type", "x-amz-checksum-sha256", "x-amz-meta-traceparent"])
    error_message = "The browser may send the presigned PUT's signed headers and a content type."
  }
}

run "raw_uploads_are_deleted_after_30_days" {
  command = apply

  assert {
    condition     = one(one(aws_s3_bucket_lifecycle_configuration.uploads.rule).expiration).days == 30
    error_message = "Raw uploads are kept for 30 days (spec §5.7)."
  }
  assert {
    condition     = one(one(aws_s3_bucket_lifecycle_configuration.uploads.rule).abort_incomplete_multipart_upload).days_after_initiation == 1
    error_message = "Incomplete multipart uploads are aborted after 1 day (spec §5.6)."
  }
}

run "uploads_start_enabled" {
  command = apply

  assert {
    condition     = aws_ssm_parameter.uploads_enabled.name == "/nettriage/dev/kill/uploads-enabled" && aws_ssm_parameter.uploads_enabled.value == "true"
    error_message = "The uploads kill switch starts on (spec §9.7)."
  }
  assert {
    condition     = output.uploads_enabled_parameter == "/nettriage/dev/kill/uploads-enabled"
    error_message = "The API is told the switch's name."
  }
}
```

In `infra/modules/app/tests/app.tftest.hcl`, replace:
```hcl
  }
}

variables {
  stage                    = "dev"
  lambda_zip_path          = "tests/fixtures/app.zip"
  app_version              = "test-sha"
  lwa_layer_arn            = "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerArm64:30"
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
  command = apply
```
with:
```hcl
  }
}

variables {
  stage                     = "dev"
  lambda_zip_path           = "tests/fixtures/app.zip"
  app_version               = "test-sha"
  lwa_layer_arn             = "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerArm64:30"
  otel_collector_layer_arn  = "arn:aws:lambda:eu-north-1:184161586896:layer:opentelemetry-collector-arm64-0_22_0:1"
  grafana_otlp_endpoint     = "https://otlp-gateway.example.grafana.net/otlp"
  grafana_otlp_auth         = "dGVzdDp0ZXN0"
  runtime_table_name        = "nettriage-dev-runtime"
  runtime_table_arn         = "arn:aws:dynamodb:eu-north-1:123456789012:table/nettriage-dev-runtime"
  oidc_parameter            = "/nettriage/dev/api/oidc"
  oidc_secret_parameter     = "/nettriage/dev/api/oidc-client-secret"
  database_url_parameter    = "/nettriage/dev/db/app-api-url"
  uploads_bucket            = "nettriage-dev-uploads-12345678"
  uploads_bucket_arn        = "arn:aws:s3:::nettriage-dev-uploads-12345678"
  uploads_enabled_parameter = "/nettriage/dev/kill/uploads-enabled"
}

run "function_is_arm64_python_behind_iam_auth" {
  command = apply
```

In `infra/modules/app/tests/app.tftest.hcl`, replace:
```hcl
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
with:
```hcl
    condition = toset(jsondecode(aws_iam_role_policy.api_parameters.policy).Statement[0].Resource) == toset([
      "arn:aws:ssm:eu-north-1:123456789012:parameter/nettriage/dev/api/oidc",
      "arn:aws:ssm:eu-north-1:123456789012:parameter/nettriage/dev/api/oidc-client-secret",
      "arn:aws:ssm:eu-north-1:123456789012:parameter/nettriage/dev/db/app-api-url",
      "arn:aws:ssm:eu-north-1:123456789012:parameter/nettriage/dev/kill/uploads-enabled",
    ])
    error_message = "The API reads only its own parameters and the uploads kill switch (spec §6.8, §9.7)."
  }
  assert {
    condition     = aws_lambda_function.api.environment[0].variables["NETTRIAGE_DATABASE_URL_PARAMETER"] == "/nettriage/dev/db/app-api-url"
    error_message = "The function gets parameter names, never secret values."
  }
}

run "the_api_may_only_put_uploads_under_orgs" {
  command = apply

  assert {
    condition     = jsondecode(aws_iam_role_policy.api_uploads.policy).Statement[0].Action == "s3:PutObject"
    error_message = "The API only puts objects, for presigned PUTs; it never reads uploads."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.api_uploads.policy).Statement[0].Resource == "arn:aws:s3:::nettriage-dev-uploads-12345678/orgs/*"
    error_message = "Presigned PUTs may only write under orgs/ in the uploads bucket (spec §5.6)."
  }
  assert {
    condition     = aws_lambda_function.api.environment[0].variables["NETTRIAGE_UPLOADS_BUCKET"] == "nettriage-dev-uploads-12345678" && aws_lambda_function.api.environment[0].variables["NETTRIAGE_UPLOADS_ENABLED_PARAMETER"] == "/nettriage/dev/kill/uploads-enabled"
    error_message = "The API is told the bucket and the kill switch's name, never values."
  }
}

```

In `tools/tests/test_smoke.py`, replace:
```python
SECURITY_HEADERS = {
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "content-security-policy": "default-src 'self'; frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
}
```
with:
```python
SECURITY_HEADERS = {
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "content-security-policy": (
        "default-src 'self'; connect-src 'self' "
        "https://nettriage-dev-uploads-1a2b3c4d.s3.eu-north-1.amazonaws.com; "
        "frame-ancestors 'none'"
    ),
    "x-content-type-options": "nosniff",
}
```

In `tools/tests/test_smoke.py`, replace:
```python


def healthy(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.url.host == "fn.example":
        return httpx.Response(403, json={"Message": "Forbidden"})
```
with:
```python


def uploads_bucket(request: httpx.Request) -> httpx.Response:
    if request.method == "OPTIONS" and request.headers.get("origin") == "https://cdn.example":
        return httpx.Response(200, headers={"access-control-allow-origin": "https://cdn.example"})
    return httpx.Response(403, content=b"<Error><Code>AccessDenied</Code></Error>")


def healthy(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.url.host.endswith(".s3.eu-north-1.amazonaws.com"):
        return uploads_bucket(request)
    if request.url.host == "fn.example":
        return httpx.Response(403, json={"Message": "Forbidden"})
```

In `tools/tests/test_smoke.py`, replace:
```python
    assert checks_for(no_viewer)["api limits requests per viewer ip"] is False

```
with:
```python
    assert checks_for(no_viewer)["api limits requests per viewer ip"] is False


def test_a_csp_without_the_uploads_bucket_is_caught() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        response = healthy(request)
        if "content-security-policy" in response.headers:
            response.headers["content-security-policy"] = "default-src 'self'; connect-src 'self'"
        return response

    assert checks_for(broken)["csp allows the uploads bucket"] is False


def test_a_bucket_open_to_any_origin_is_caught() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        if request.method == "OPTIONS":
            origin = request.headers.get("origin", "")
            return httpx.Response(200, headers={"access-control-allow-origin": origin})
        return healthy(request)

    results = checks_for(broken)
    assert results["uploads bucket lets the app PUT"] is True
    assert results["uploads bucket refuses other origins"] is False


def test_a_bucket_that_takes_anonymous_writes_is_caught() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200)
        return healthy(request)

    assert checks_for(broken)["uploads bucket refuses anonymous writes"] is False

```

Add the new module to the Terraform checks.

In `justfile`, replace:
```
    terraform fmt -check -recursive infra
    for d in infra/bootstrap infra/modules/app infra/modules/data infra/modules/edge infra/modules/identity; do (cd "$d" && export TF_DATA_DIR=.terraform-check && terraform init -backend=false -input=false >/dev/null && terraform validate && terraform test) || exit 1; done

```
with:
```
    terraform fmt -check -recursive infra
    for d in infra/bootstrap infra/modules/app infra/modules/data infra/modules/edge infra/modules/identity infra/modules/pipeline; do (cd "$d" && export TF_DATA_DIR=.terraform-check && terraform init -backend=false -input=false >/dev/null && terraform validate && terraform test) || exit 1; done

```

In `.github/workflows/ci.yml`, replace:
```yaml
          terraform fmt -check -recursive infra
          for dir in infra/bootstrap infra/modules/app infra/modules/data infra/modules/edge infra/modules/identity; do
            (cd "$dir" && terraform init -backend=false -input=false && terraform validate && terraform test)
```
with:
```yaml
          terraform fmt -check -recursive infra
          for dir in infra/bootstrap infra/modules/app infra/modules/data infra/modules/edge infra/modules/identity infra/modules/pipeline; do
            (cd "$dir" && terraform init -backend=false -input=false && terraform validate && terraform test)
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `just tf-check`
Expected: FAIL in the app module: `Failure! 5 passed, 2 failed`.
- `the_api_reads_only_its_own_table_and_parameters` fails its assertion (the kill switch isn't in the list yet).
- `the_api_may_only_put_uploads_under_orgs` fails with `Reference to undeclared resource` (`aws_iam_role_policy.api_uploads`).

The loop stops there, before the pipeline module.

Run: `just tools-test`
Expected: `3 failed, 216 passed`: the three new bucket tests (`test_a_csp_without_the_uploads_bucket_is_caught`, `test_a_bucket_open_to_any_origin_is_caught` and `test_a_bucket_that_takes_anonymous_writes_is_caught`).

- [ ] **Step 3: Write the pipeline module**

`infra/modules/pipeline/versions.tf`:
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

`infra/modules/pipeline/variables.tf`:
```hcl
variable "stage" {
  type = string
}

variable "app_origin" {
  type        = string
  description = "The app's origin (https://<distribution domain>), the only one that may PUT uploads."
}
```

`infra/modules/pipeline/main.tf`:
```hcl
data "aws_caller_identity" "current" {}

locals {
  name = "nettriage-${var.stage}"
  # Bucket names are global. A hash of the account ID keeps ours unique without the account ID
  # itself, which would otherwise appear in every page's CSP header (as for the sign-in domain).
  bucket = "${local.name}-uploads-${substr(sha256(data.aws_caller_identity.current.account_id), 0, 8)}"
}

# Raw uploads (spec §5.6): private, TLS-only, SSE-S3, deleted after 30 days. Browsers PUT files
# here with a presigned URL from the API; the analyze worker (Plan 4b) reads them.
resource "aws_s3_bucket" "uploads" {
  bucket = local.bucket
}

resource "aws_s3_bucket_ownership_controls" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "uploads" {
  bucket                  = aws_s3_bucket.uploads.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_policy" "uploads" {
  bucket = aws_s3_bucket.uploads.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "TlsOnly"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.uploads.arn, "${aws_s3_bucket.uploads.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.uploads]
}

# Browsers PUT files straight from the app's pages, and from nowhere else. The browser adds
# Content-Length itself; the other signed headers are these.
resource "aws_s3_bucket_cors_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  cors_rule {
    allowed_methods = ["PUT"]
    allowed_origins = [var.app_origin]
    allowed_headers = ["content-type", "x-amz-checksum-sha256", "x-amz-meta-traceparent"]
    max_age_seconds = 3000
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    id     = "expire-raw-uploads"
    status = "Enabled"

    filter {}

    expiration {
      days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# The uploads kill switch (spec §9.7). The API re-reads it every minute: "true" lets members
# upload, anything else pauses uploads. Terraform only creates it, so a deploy never switches
# uploads back on after the owner paused them.
resource "aws_ssm_parameter" "uploads_enabled" {
  #checkov:skip=CKV2_AWS_34:Not secret: an on/off flag.
  name  = "/nettriage/${var.stage}/kill/uploads-enabled"
  type  = "String"
  value = "true"

  lifecycle {
    ignore_changes = [value]
  }
}
```

`infra/modules/pipeline/outputs.tf`:
```hcl
output "uploads_bucket" {
  value = aws_s3_bucket.uploads.bucket
}

output "uploads_bucket_arn" {
  value = aws_s3_bucket.uploads.arn
}

output "uploads_origin" {
  description = "The bucket's regional origin, which presigned PUTs use and the CSP allows."
  value       = "https://${aws_s3_bucket.uploads.bucket_regional_domain_name}"
}

output "uploads_enabled_parameter" {
  value = aws_ssm_parameter.uploads_enabled.name
}
```

- [ ] **Step 4: Give the API the bucket and the switch, and wire the dev stage**

In `infra/modules/app/variables.tf`, replace:
```hcl
  description = "SSM SecureString with app_api's pooled Neon URL, written by the deploy (tools/deploy)."
}

```
with:
```hcl
  description = "SSM SecureString with app_api's pooled Neon URL, written by the deploy (tools/deploy)."
}

variable "uploads_bucket" {
  type        = string
  description = "The uploads bucket, which presigned PUTs go to."
}

variable "uploads_bucket_arn" {
  type = string
}

variable "uploads_enabled_parameter" {
  type        = string
  description = "Name of the SSM parameter that switches uploads on and off (spec §9.7)."
}

```

In `infra/modules/app/main.tf`, replace:
```hcl
  parameter_arns = [
    for name in [var.oidc_parameter, var.oidc_secret_parameter, var.database_url_parameter] :
    "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter${name}"
```
with:
```hcl
  parameter_arns = [
    for name in [
      var.oidc_parameter,
      var.oidc_secret_parameter,
      var.database_url_parameter,
      var.uploads_enabled_parameter,
    ] :
    "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter${name}"
```

In `infra/modules/app/main.tf`, replace:
```hcl

resource "aws_lambda_function" "api" {
```
with:
```hcl

# Presigned PUTs are signed with the API's role, so the role may put objects, and only under
# orgs/ in the uploads bucket (spec §6.8). The API never reads an upload.
resource "aws_iam_role_policy" "api_uploads" {
  name = "presign-uploads"
  role = aws_iam_role.api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "s3:PutObject"
      Resource = "${var.uploads_bucket_arn}/orgs/*"
    }]
  })
}

resource "aws_lambda_function" "api" {
```

In `infra/modules/app/main.tf`, replace:
```hcl
    variables = {
      AWS_LAMBDA_EXEC_WRAPPER            = "/opt/bootstrap"
      AWS_LWA_PORT                       = "8080"
      AWS_LWA_READINESS_CHECK_PATH       = "/api/health"
      OPENTELEMETRY_COLLECTOR_CONFIG_URI = "/var/task/collector.yaml"
      OTEL_EXPORTER_OTLP_ENDPOINT        = "http://localhost:4318"
      OTEL_EXPORTER_OTLP_PROTOCOL        = "http/protobuf"
      GRAFANA_OTLP_ENDPOINT              = var.grafana_otlp_endpoint
      GRAFANA_OTLP_AUTH                  = var.grafana_otlp_auth
      NETTRIAGE_STAGE                    = var.stage
      NETTRIAGE_VERSION                  = var.app_version
      NETTRIAGE_SERVICE_NAME             = "nettriage-api"
      NETTRIAGE_RUNTIME_TABLE            = var.runtime_table_name
      NETTRIAGE_OIDC_PARAMETER           = var.oidc_parameter
      NETTRIAGE_OIDC_SECRET_PARAMETER    = var.oidc_secret_parameter
      NETTRIAGE_DATABASE_URL_PARAMETER   = var.database_url_parameter
    }
```
with:
```hcl
    variables = {
      AWS_LAMBDA_EXEC_WRAPPER             = "/opt/bootstrap"
      AWS_LWA_PORT                        = "8080"
      AWS_LWA_READINESS_CHECK_PATH        = "/api/health"
      OPENTELEMETRY_COLLECTOR_CONFIG_URI  = "/var/task/collector.yaml"
      OTEL_EXPORTER_OTLP_ENDPOINT         = "http://localhost:4318"
      OTEL_EXPORTER_OTLP_PROTOCOL         = "http/protobuf"
      GRAFANA_OTLP_ENDPOINT               = var.grafana_otlp_endpoint
      GRAFANA_OTLP_AUTH                   = var.grafana_otlp_auth
      NETTRIAGE_STAGE                     = var.stage
      NETTRIAGE_VERSION                   = var.app_version
      NETTRIAGE_SERVICE_NAME              = "nettriage-api"
      NETTRIAGE_RUNTIME_TABLE             = var.runtime_table_name
      NETTRIAGE_OIDC_PARAMETER            = var.oidc_parameter
      NETTRIAGE_OIDC_SECRET_PARAMETER     = var.oidc_secret_parameter
      NETTRIAGE_DATABASE_URL_PARAMETER    = var.database_url_parameter
      NETTRIAGE_UPLOADS_BUCKET            = var.uploads_bucket
      NETTRIAGE_UPLOADS_ENABLED_PARAMETER = var.uploads_enabled_parameter
    }
```

In `infra/modules/app/main.tf`, replace:
```hcl
    aws_iam_role_policy.api_parameters,
  ]
```
with:
```hcl
    aws_iam_role_policy.api_parameters,
    aws_iam_role_policy.api_uploads,
  ]
```

In `infra/envs/dev/main.tf`, replace:
```hcl

module "app" {
  source                   = "../../modules/app"
  stage                    = "dev"
  lambda_zip_path          = var.lambda_zip_path
  app_version              = var.app_version
  lwa_layer_arn            = var.lwa_layer_arn
  otel_collector_layer_arn = var.otel_collector_layer_arn
  grafana_otlp_endpoint    = var.grafana_otlp_endpoint
  grafana_otlp_auth        = var.grafana_otlp_auth
  runtime_table_name       = module.data.table_name
  runtime_table_arn        = module.data.table_arn
  oidc_parameter           = local.oidc_parameter
  oidc_secret_parameter    = local.oidc_secret_parameter
  database_url_parameter   = local.database_url_parameter
}
```
with:
```hcl

module "pipeline" {
  source     = "../../modules/pipeline"
  stage      = "dev"
  app_origin = "https://${module.edge.distribution_domain}"
}

module "app" {
  source                    = "../../modules/app"
  stage                     = "dev"
  lambda_zip_path           = var.lambda_zip_path
  app_version               = var.app_version
  lwa_layer_arn             = var.lwa_layer_arn
  otel_collector_layer_arn  = var.otel_collector_layer_arn
  grafana_otlp_endpoint     = var.grafana_otlp_endpoint
  grafana_otlp_auth         = var.grafana_otlp_auth
  runtime_table_name        = module.data.table_name
  runtime_table_arn         = module.data.table_arn
  oidc_parameter            = local.oidc_parameter
  oidc_secret_parameter     = local.oidc_secret_parameter
  database_url_parameter    = local.database_url_parameter
  uploads_bucket            = module.pipeline.uploads_bucket
  uploads_bucket_arn        = module.pipeline.uploads_bucket_arn
  uploads_enabled_parameter = module.pipeline.uploads_enabled_parameter
}
```

In `infra/envs/dev/main.tf`, replace:
```hcl
module "edge" {
  source            = "../../modules/edge"
  stage             = "dev"
  api_origin_domain = module.app.function_url_domain
}
```
with:
```hcl
module "edge" {
  source                = "../../modules/edge"
  stage                 = "dev"
  api_origin_domain     = module.app.function_url_domain
  csp_connect_src_extra = [module.pipeline.uploads_origin]
}
```

The two repo-wide Checkov skips that now also cover the uploads bucket state why:

In `.checkov.yaml`, replace:
```yaml
  - CKV_AWS_145    # S3 KMS encryption: SSE-S3 by design; KMS customer keys cost money (spec §6.8)
  - CKV2_AWS_62    # S3 event notifications: not needed for state or web buckets
  - CKV2_AWS_61    # S3 lifecycle on the web bucket: deploys replace its content
  - CKV_AWS_21     # S3 versioning on the web bucket: rebuilt from Git on every deploy
  - CKV_AWS_300    # S3 abort-multipart rule on the web bucket: the owner's deploy uploads small files only
```
with:
```yaml
  - CKV_AWS_145    # S3 KMS encryption: SSE-S3 by design; KMS customer keys cost money (spec §6.8)
  - CKV2_AWS_62    # S3 event notifications: not needed for state or web buckets; the uploads bucket gets its notification with the analyze queue (Plan 4b)
  - CKV2_AWS_61    # S3 lifecycle on the web bucket: deploys replace its content
  - CKV_AWS_21     # S3 versioning: the web bucket is rebuilt from Git on every deploy; raw uploads are never changed and are deleted after 30 days (spec §5.7)
  - CKV_AWS_300    # S3 abort-multipart rule on the web bucket: the owner's deploy uploads small files only
```

- [ ] **Step 5: Check the bucket from the smoke test**

In `tools/smoke.py`, replace:
```python

import argparse
import sys
import time
```
with:
```python

import argparse
import re
import sys
import time
```

In `tools/smoke.py`, replace:
```python
    "x-content-type-options": "nosniff",
}


```
with:
```python
    "x-content-type-options": "nosniff",
}


# The uploads bucket's origin: the one S3 origin in the CSP's connect-src (spec §6.7).
UPLOADS_ORIGIN = re.compile(r"https://[a-z0-9.-]+\.s3\.eu-north-1\.amazonaws\.com")
FOREIGN_ORIGIN = "https://smoke-test.example"


```

In `tools/smoke.py`, replace:
```python


def run_checks(client: httpx.Client, base_url: str, function_url: str, version: str) -> list[Check]:
    health = client.get(f"{base_url}/api/health")
```
with:
```python


def _uploads_origin(csp: str) -> str | None:
    for directive in csp.split(";"):
        parts = directive.split()
        if parts and parts[0] == "connect-src":
            found = [part for part in parts[1:] if UPLOADS_ORIGIN.fullmatch(part)]
            return found[0] if len(found) == 1 else None
    return None


def _uploads_checks(client: httpx.Client, base_url: str, csp: str) -> list[Check]:
    """Browsers PUT uploads straight to S3 (spec §4.2): the app's pages may, other sites and
    anonymous callers may not. Nothing is written: the preflights send no body, and the
    anonymous PUT is refused."""
    origin = _uploads_origin(csp)
    if origin is None:
        return [Check("csp allows the uploads bucket", False, "no single S3 origin in connect-src")]
    probe = f"{origin}/orgs/smoke/uploads/smoke/raw"
    signed_headers = "x-amz-checksum-sha256,x-amz-meta-traceparent"
    app = client.options(probe, headers={
        "Origin": base_url,
        "Access-Control-Request-Method": "PUT",
        "Access-Control-Request-Headers": signed_headers,
    })
    foreign = client.options(probe, headers={
        "Origin": FOREIGN_ORIGIN, "Access-Control-Request-Method": "PUT"
    })
    anonymous = client.put(probe, content=b"smoke")
    allowed = app.headers.get("access-control-allow-origin")
    return [
        Check("csp allows the uploads bucket", True, origin),
        Check("uploads bucket lets the app PUT", app.status_code == 200 and allowed == base_url,
              f"{app.status_code} {allowed}"),
        Check("uploads bucket refuses other origins", foreign.status_code == 403,
              str(foreign.status_code)),
        Check("uploads bucket refuses anonymous writes", anonymous.status_code == 403,
              str(anonymous.status_code)),
    ]


def run_checks(client: httpx.Client, base_url: str, function_url: str, version: str) -> list[Check]:
    health = client.get(f"{base_url}/api/health")
```

In `tools/smoke.py`, replace:
```python
              health.headers.get("ratelimit-policy", "no RateLimit-Policy header")),
        *_sign_in_checks(client, base_url),
    ]

```
with:
```python
              health.headers.get("ratelimit-policy", "no RateLimit-Policy header")),
        *_sign_in_checks(client, base_url),
        *_uploads_checks(client, base_url, root.headers.get("content-security-policy", "")),
    ]

```

- [ ] **Step 6: Run the checks**

Run: `just tf-check tools-test`
Expected:
- `terraform test` passes in all six stacks: bootstrap 3, app 7, data 3, edge 4, identity 4 and pipeline 5 runs;
- tools `219 passed`.

`terraform init` writes `infra/modules/pipeline/.terraform.lock.hcl`. Commit it, as the other modules do.

Validate the dev stage as CI does:
```bash
cd infra/envs/dev && export TF_DATA_DIR=.terraform-check && terraform init -backend=false -input=false >/dev/null && terraform validate
```
Expected: `Success! The configuration is valid.`

Run Checkov as CI does:
```bash
uv run --no-project --python 3.12 --with checkov python -m checkov.main -d infra --framework terraform --config-file .checkov.yaml --quiet --compact
```
Expected: `Passed checks: 93, Failed checks: 0, Skipped checks: 3`. tflint runs in CI.

- [ ] **Step 7: Commit**

```bash
git add infra .checkov.yaml justfile .github/workflows/ci.yml tools
git commit -m "feat(infra): the uploads bucket, its kill switch, presign access for the API, and CSP and smoke checks for it" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Spec amendments, runbook and README

**Files:**
- Modify: `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (§5.6, §6.7, §7, §9.7, §13.2), `docs/runbooks/setup-and-deploy.md` (a new B6, "Pause uploads in an emergency" and two Part C rows), `README.md`

- [ ] **Step 1: Amend the spec**

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `nettriage-<stage>-web` | SPA assets; `demo/*.json` | Private; read only by CloudFront through OAC |
| `nettriage-<stage>-uploads` | `orgs/{org_id}/uploads/{upload_id}/raw` | Private, TLS-only, SSE-S3, public access blocked. CORS allows `PUT` from the app origin only. Lifecycle: delete after 30 days, abort incomplete multipart uploads after 1 day. Event notification → SQS `analyze` |
| `nettriage-backups-<suffix>` | `pg/<stage>/<date>.dump` | Private, SSE-S3, deleted after 7 days |
| `nettriage-tfstate-<suffix>` | Terraform state | Versioned, encrypted, TLS-only, native locking |

```
with:
```markdown
| `nettriage-<stage>-web` | SPA assets; `demo/*.json` | Private; read only by CloudFront through OAC |
| `nettriage-<stage>-uploads-<suffix>` | `orgs/{org_id}/uploads/{upload_id}/raw` | Private, TLS-only, SSE-S3, public access blocked. CORS allows `PUT` from the app origin only. Lifecycle: delete after 30 days, abort incomplete multipart uploads after 1 day. Event notification → SQS `analyze` |
| `nettriage-backups-<suffix>` | `pg/<stage>/<date>.dump` | Private, SSE-S3, deleted after 7 days |
| `nettriage-tfstate-<suffix>` | Terraform state | Versioned, encrypted, TLS-only, native locking |

The uploads bucket's `<suffix>` is the first 8 hex characters of the account ID's SHA-256, like the sign-in domain's (Plan 4a): bucket names are global, and this one appears in every page's CSP header, so it can't hold the account ID.

```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
  - `Strict-Transport-Security: max-age=31536000; includeSubDomains`
  - `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self' https://nettriage-<stage>-uploads.s3.eu-north-1.amazonaws.com; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'; upgrade-insecure-requests`
  - `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy` (camera, microphone and geolocation disabled), `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-origin`
```
with:
```markdown
  - `Strict-Transport-Security: max-age=31536000; includeSubDomains`
  - `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self' https://nettriage-<stage>-uploads-<suffix>.s3.eu-north-1.amazonaws.com; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'; upgrade-insecure-requests`
  - `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy` (camera, microphone and geolocation disabled), `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-origin`
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
- **Optimistic concurrency:** findings return an `ETag`. `PATCH` requires `If-Match`: a stale version gets **412 Precondition Failed**, and a missing header gets 428.
- **Idempotency:** `Idempotency-Key` is supported on `POST …/uploads` and `POST /orgs` and is kept for 24 hours. A key reused with a different request gets 422, and a retry while the first request still runs gets 409.
- **SPA request headers:** `x-amz-content-sha256` on every request with a body (the OAC requirement), and `X-CSRF-Token` on state-changing requests.
```
with:
```markdown
- **Optimistic concurrency:** findings return an `ETag`. `PATCH` requires `If-Match`: a stale version gets **412 Precondition Failed**, and a missing header gets 428.
- **Idempotency:** `Idempotency-Key` is supported on `POST …/uploads` and `POST /orgs` and is kept for 24 hours. A key reused with a different request gets 422, and a retry while the first request still runs gets 409. A request is its method, path and body, so a key can't be reused across routes or orgs (Plan 4a).
- **SPA request headers:** `x-amz-content-sha256` on every request with a body (the OAC requirement), and `X-CSRF-Token` on state-changing requests.
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `POST /api/v1/invitations/accept` | session | Body: token |
| `POST /api/v1/orgs/{org}/uploads` | `uploads:create` | Returns a presigned PUT that expires in 5 minutes |
| `GET /api/v1/orgs/{org}/uploads` | `uploads:read` | |
```
with:
```markdown
| `POST /api/v1/invitations/accept` | session | Body: token |
| `POST /api/v1/orgs/{org}/uploads` | `uploads:create` | Body: `filename`, `size_bytes` (at most 25 MB), `sha256` (hex). Returns a presigned PUT that expires in 5 minutes. It signs `Content-Length`, `x-amz-checksum-sha256` and `x-amz-meta-traceparent`, so S3 accepts only the declared file; the response lists the headers to send. Counts against `uploads.org`; 503 while uploads are paused |
| `GET /api/v1/orgs/{org}/uploads` | `uploads:read` | |
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown

- **Kill switches:** `ai_enabled` and `uploads_enabled` live in SSM and are re-read every 60 seconds.
- **Maintenance:** the `ops` Lambda runs daily. It expires `pending_upload` rows older than 1 hour and invitations past their date, and purges audit rows older than 180 days.
```
with:
```markdown

- **Kill switches:** `ai_enabled` and `uploads_enabled` live in SSM (`/nettriage/<stage>/kill/<name>`, `true` or anything else) and are re-read every 60 seconds. A switch that can't be read keeps its last value, and counts as off until it has been read once (Plan 4a). Terraform only creates them, so a deploy never turns a paused switch back on.
- **Maintenance:** the `ops` Lambda runs daily. It expires `pending_upload` rows older than 1 hour and invitations past their date, and purges audit rows older than 180 days.
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| Current Lambda Function URL + OAC permission requirements (resource-policy actions, body-hash header) | Follow AWS's current docs; if needed, API Gateway HTTP API ($1 per million requests) |
| S3 presigned PUT enforcing the signed `content-length`, checksum and metadata headers from browsers | Presigned POST with a policy (`content-length-range`) |
| The OpenTelemetry Lambda collector layer for python3.14/arm64 | `force_flush` at the end of each invocation |
```
with:
```markdown
| Current Lambda Function URL + OAC permission requirements (resource-policy actions, body-hash header) | Follow AWS's current docs; if needed, API Gateway HTTP API ($1 per million requests) |
| S3 presigned PUT enforcing the signed `content-length`, checksum and metadata headers from browsers (Plan 4a signs them; runbook B6 checks S3 refuses a changed file) | Presigned POST with a policy (`content-length-range`) |
| The OpenTelemetry Lambda collector layer for python3.14/arm64 | `force_flush` at the end of each invocation |
```

- [ ] **Step 2: Add "Try an upload" and the kill switch to the runbook**

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown

## Part C: when things go wrong

```
with:
````markdown

### B6. Try an upload
Files go from the browser straight to S3, with a presigned PUT the API hands out. Analysis comes
in Plan 4b; for now an upload stays `pending_upload`. This also checks that S3 refuses any file
other than the one the API signed for (spec §13.2).
1. Do B5 steps 1 to 4 (sign in, open the console, define `api`).
2. Create an org to upload into:
   ```js
   const org = await api("POST", "/orgs", { name: "Upload Test" });
   ```
3. Prepare a small file and its SHA-256:
   ```js
   const file = new TextEncoder().encode("version srcaddr dstaddr srcport dstport protocol packets bytes start end action\n");
   const hex = (buffer) => [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
   const sha256 = hex(await crypto.subtle.digest("SHA-256", file));
   ```
4. Ask to upload it:
   ```js
   const created = await api("POST", "/orgs/" + org.id + "/uploads", { filename: "test.log", size_bytes: file.length, sha256 });
   ```
   `201`, with `upload.status: "pending_upload"`, an `upload_url` on
   `nettriage-dev-uploads-….s3.eu-north-1.amazonaws.com` and one `upload_headers` entry per
   signed header. The URL works for 5 minutes, so do steps 5 and 6 right away.
5. Check that S3 refuses a different file. Same length, different content:
   ```js
   (await fetch(created.upload_url, { method: "PUT", headers: created.upload_headers, body: new TextEncoder().encode("VERSION srcaddr dstaddr srcport dstport protocol packets bytes start end action\n") })).status;
   ```
   `400` (the checksum doesn't match). One byte longer:
   ```js
   (await fetch(created.upload_url, { method: "PUT", headers: created.upload_headers, body: new TextEncoder().encode("version srcaddr dstaddr srcport dstport protocol packets bytes start end action\n\n") })).status;
   ```
   `403` (the signed length doesn't match). If the console shows `TypeError: Failed to fetch`
   instead of a number, S3 refused the file too: open the **Network** tab to see the PUT's
   `400` or `403`.
6. Upload the real file:
   ```js
   (await fetch(created.upload_url, { method: "PUT", headers: created.upload_headers, body: file })).status;
   ```
   `200`.
7. List the org's uploads:
   ```js
   await api("GET", "/orgs/" + org.id + "/uploads");
   ```
   `200`, with your upload, still `pending_upload`.
8. Delete the test org when you're done (its file is deleted from S3 after 30 days):
   ```js
   await api("DELETE", "/orgs/" + org.id + "?confirm_name=" + encodeURIComponent("Upload Test"));
   ```

If step 5 gives `200`, S3 accepted a file it shouldn't have: stop and tell Claude (spec §13.2's
fallback is a presigned POST).

## Part C: when things go wrong

### Pause uploads in an emergency
Uploads have a kill switch in SSM (spec §9.7). Pausing them needs your AWS session
(`aws login --profile nettriage`).
- Pause: `aws ssm put-parameter --profile nettriage --region eu-north-1 --name /nettriage/dev/kill/uploads-enabled --value false --overwrite`
- Resume: the same command with `--value true`.

Within a minute, new uploads get `503` ("Uploads are paused for now"); files already uploaded are
kept. A deploy never switches uploads back on.

````

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
| The browser lands on `/?sign_in=disabled` | This account is disabled in the database. Tell Claude if that's unexpected |
| `Too Many Requests` with `"status": 429` | Too many sign-in attempts or requests from your IP or account. Wait the number of seconds in the `Retry-After` header (a minute at most for sign-in), then retry |
| Cognito's verification email never arrives | Check spam. Cognito's built-in sender allows about 50 emails a day per account; wait until tomorrow if many sign-ups ran today |
```
with:
```markdown
| The browser lands on `/?sign_in=disabled` | This account is disabled in the database. Tell Claude if that's unexpected |
| `Too Many Requests` with `"status": 429` | Too many sign-in attempts or requests from your IP or account, or more than 5 uploads started at once in one org (20 a day). Wait the number of seconds in the `Retry-After` header (a minute at most for sign-in), then retry |
| `503` "Uploads are paused for now" | The uploads kill switch is off. Resume it as in "Pause uploads in an emergency" if that's not intended |
| Cognito's verification email never arrives | Check spam. Cognito's built-in sender allows about 50 emails a day per account; wait until tomorrow if many sign-ups ran today |
```

- [ ] **Step 3: Add the highlight to the README**

In `README.md`, replace:
```markdown
- **Organizations with roles** (owner, admin, analyst, viewer) and one-time invitation links; a test calls every endpoint as each role, a non-member and an anonymous caller.
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for the API.
```
with:
```markdown
- **Organizations with roles** (owner, admin, analyst, viewer) and one-time invitation links; a test calls every endpoint as each role, a non-member and an anonymous caller.
- **Direct-to-S3 uploads**: the API hands out a presigned PUT that signs the file's size and SHA-256, so S3 accepts only the declared file and the API never handles it.
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for the API.
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-09-26-nettriage-m1-design.md docs/runbooks/setup-and-deploy.md README.md
git commit -m "docs: upload intake in the spec, runbook and README" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7 (Claude, then the owner): Pull request, deploy, and a first upload

- [ ] **Step 1 (Claude):**
  - Run `just lint test tools-test edge-test web-check tf-check pin-check cloud-check detection-report build-lambda`; everything must pass.
  - Get a final review of the whole branch, fix what it finds, then push `plan-4a/upload-intake`.
  - Open the PR and watch CI.
  - Request a Copilot review.
- [ ] **Step 2 (owner):** Review the PR. Optionally run `just plan-dev` (runbook B1). Expected: `Plan: 9 to add, 3 to change, 0 to destroy`.
  - The 9 additions are the uploads bucket and its six settings (ownership, public access block, encryption, policy, CORS, lifecycle), the kill-switch parameter and the API's presign policy.
  - The 3 changes are the API function (code and two settings), its parameters policy and the response headers policy (the CSP).
- [ ] **Step 3 (owner):** Squash-merge the PR.
- [ ] **Step 4 (owner):** Runbook B2 (`just deploy-dev`). Expected:
  - `Database migrated.` (migration `0005`);
  - the Terraform plan above;
  - fifteen smoke `PASS` lines. The new four are `csp allows the uploads bucket`, `uploads bucket lets the app PUT`, `uploads bucket refuses other origins` and `uploads bucket refuses anonymous writes`.
- [ ] **Step 5 (owner):** Runbook B6:
  - ask for an upload;
  - see S3 refuse a changed file (`400`) and a longer one (`403`);
  - upload the real file (`200`);
  - list it as `pending_upload`.

## Plan 4a is done when

- [ ] `just lint test tools-test tf-check` passes locally and CI passes.
- [ ] On dev, S3 refused a changed and a longer file and accepted the declared one (runbook B6), which settles §13.2's presigned-PUT question.
- [ ] The PR is merged through review, with every thread resolved.

## Spec coverage of this plan

| Spec | Covered here |
|---|---|
| §4.2 upload request, presigned PUT, direct PUT to S3 | Tasks 2, 4 and 5 |
| §5.2 `uploads` table; §5.3 row-level security; §5.4 `app_api`'s grants | Task 1 |
| §5.6 uploads bucket: private, TLS-only, SSE-S3, CORS from the app, 30-day lifecycle, abort multipart after 1 day | Task 5; the name's suffix amends §5.6 (Task 6) |
| §5.6 event notification to SQS `analyze` | Plan 4b |
| §5.7 25 MB per file, 20 uploads per org per day | Tasks 1 and 4 |
| §6.5 `uploads.org` | Task 4 |
| §6.7 CSP `connect-src` with the uploads bucket | Task 5; amends §6.7's example (Task 6) |
| §6.8 the API's role may put objects under `orgs/` only | Task 5 |
| §7 `POST`, `GET`, `GET /{id}` uploads; `Idempotency-Key` on `POST …/uploads` | Tasks 3 and 4; the request hash covering method and path amends §7 (Task 6) |
| §9.1 `traceparent` in S3 object metadata | Tasks 2 and 4; the worker's side is Plan 4b |
| §9.4 `upload.created` | Task 4 |
| §9.7 `uploads_enabled` kill switch | Tasks 2, 4 and 5; its failure rules amend §9.7 (Task 6) |
| §11.4 authorization matrix and route declarations for the new routes | Task 4 |
| §13.2 S3 enforcing the presigned PUT's signed headers from browsers | Runbook B6 (Task 6), run by the owner in Task 7 |
| §8, §9.2 pipeline metrics, the `analyze` worker, findings | Plan 4b |
| §9.7 expiring `pending_upload` rows older than 1 hour | Plan 7 (the `ops` Lambda) |
