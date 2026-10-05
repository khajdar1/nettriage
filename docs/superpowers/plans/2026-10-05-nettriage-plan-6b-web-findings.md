# NetTriage Plan 6b: Uploads, Findings, Triage and the AI in the Web App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the web app the rest of Milestone 1's work, in the Sweep look:
- uploading a flow log from the browser, with its progress, and following its analysis;
- the findings list, where an organization now opens: most severe first, filtered by severity, status, detector and upload;
- a finding's page: its details, its evidence (a port scan drawn on the port map), its ATT&CK techniques, its AI explanation, its history and comments, and triage that never overwrites a newer change;
- the audit log and the AI usage, for Owners and Admins;
- the API sorting findings most severe first (`sort=severity`).

Plan 6c adds the public demo once Bedrock answers.

**Architecture:**
- **The API gains `sort`** on `GET …/findings`: `newest` (the default) or `severity`, which ranks `critical` to `low` in SQL and continues pages with a cursor that leads with the last finding's severity. A cursor works only with the order it was made for (422 otherwise). The OpenAPI document and the typed client are regenerated.
- **Each page is a few small files:** a data module per resource (`src/uploads/`, `src/findings/`, `src/audit/`, `src/usage/`) with its types, query keys, TanStack Query hooks and pure helpers, which unit tests cover; and the page with its parts under `src/pages/org/`. Lists page with the API's cursors through `useInfiniteQuery`.
- **Uploads go straight to S3.** The browser hashes the file, asks the API for a slot with an `Idempotency-Key`, then puts the file with an `XMLHttpRequest`, the one browser API that reports upload progress. The list checks back every few seconds while an upload is being analyzed.
- **Triage is optimistic concurrency end to end:** the page sends the version it read in `If-Match`; a 412 reloads the finding, shows the newer change, and says so.
- **The AI panel trusts nothing it reads:** output schema v1 is checked field by field before any of it is shown, every part is plain text, and a queued re-run is checked for every few seconds, for five minutes.

**Tech Stack:** React 19, TypeScript 6 (strict), Vite 8 · React Router 8, TanStack Query 5, openapi-fetch 0.17, openapi-typescript 7 · Vitest 5, Testing Library, user-event · FastAPI, SQLAlchemy Core and Postgres (the `sort` parameter).

**Spec:** `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (revision 2). Read it alongside this plan; section numbers (§) refer to it. This plan implements:
- §2.2 features 3 to 7 in the browser: uploads, findings, triage, AI explanations, the audit log and usage;
- §7's findings `sort` (the owner's decision, 2026-10-03), `If-Match` on triage, and `Idempotency-Key` on uploads and comments;
- §10's uploads, findings, finding, audit and usage pages, with its security rules, states, accessibility and look.

**Plan series:** Plan 6 of 7 ("frontend") is in three parts (the owner's decision, 2026-10-03): 6a, the foundation and organizations (merged as #15, then restyled in #23); **6b (this plan): uploads, findings, triage, the AI panel, the audit log and usage;** 6c, the public demo, written once AWS lifts the Bedrock limits. Plan 5d (the evals) also waits for Bedrock. Plan 7 adds the Playwright smoke tests.

**Branch:** `plan-6b/web-findings`, from `main` at `82fc8a4` or later.

## Global Constraints

- **Stack.** Node 24 with pnpm 12. TypeScript strict, with `noUncheckedIndexedAccess`. ESLint (typescript-eslint strict) and Prettier (print width 100) pass. Python 3.14 with mypy `--strict` and Ruff. Work test-first.
- **Commands on this Windows machine.** Run Python tools as modules (`uv run python -m …`). Frontend commands run in `frontend/` with `pnpm`. Backend tests that touch the database need the local Postgres: `just db-up` once, then `NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)"` before `uv run python -m pytest` in `backend/` (`just test` does both for the whole suite).
- **Type checks** use `pnpm exec tsc --noEmit`, never `tsc -b`, which writes a build cache (`tsconfig.tsbuildinfo`) that must not be committed.
- **CSP (§6.7, §10).** No inline scripts or styles (`pnpm check:csp`): no `style` props (lint forbids them) and no `dangerouslySetInnerHTML`. Charts and maps are SVG whose geometry is in attributes. Every style lives in `src/styles.css`.
- **Links (§10).** External links use `ExternalLink` (`target="_blank"`, `rel="noopener noreferrer"`).
- **Requests (§7).**
  - The client already adds `x-amz-content-sha256`, `X-CSRF-Token`, and `{}` for a POST with no inputs (Plan 6a).
  - `POST …/uploads` and `POST …/comments` send an `Idempotency-Key`.
  - `PATCH …/findings/{id}` sends `If-Match` with the finding's version in quotes (`"3"`).
- **States (§10).** Loading and empty states. A Problem Details error shows with its reference (`trace_id`), and a 429 with its `Retry-After` time (`ErrorNotice`, Plan 6a).
- **Accessibility (§10).** WCAG 2.2 AA:
  - every control has a label, and one `h1` per page with a title;
  - a chart or map has a caption in words and hides its drawing from screen readers;
  - a table too wide for its column scrolls in a focusable region;
  - tests find elements by role and name.
- **Permissions (§6.4).** Owners, Admins and Analysts upload, triage, comment, ask the AI and rate it; Viewers read. Only Owners and Admins see the audit log and the usage. The app hides what a role can't do, the API decides, and every refusal is shown.
- **The look (§10, "Sweep").** Amber (`--signal`) marks only what a detector found, and the logo; a test enforces it. The AI's label and links are teal; the usage chart is ink.
- **AI output (§8.4, §10)** is plain text, labeled "AI-generated" with its model and prompt version.
- **Owner-only commands.** Claude never runs `aws login`, `just bootstrap`, `just store-*`, `just pause-*`, `just resume-*` or `just deploy-*`.
- **Commits.** Use Conventional Commits. Every commit made by Claude ends with the trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Decisions this plan makes

1. **The owner's decisions (2026-10-05):**
   - an organization opens on **Findings**;
   - findings open **most severe first**, then newest; **Newest first** is one choice away;
   - a port scan's finding page draws **the evidence sample** on the port map, captioned as a sample (a finding keeps at most 50 flows);
   - the usage page draws **cost per day as a bar chart** above the table.
2. **The upload panel sits on the page**, not in a dialog: the app has no modal dialogs (Plan 6a). Task 10 amends §10, which said "dialog".
3. **Uploading:**
   - the browser refuses an empty file, or one over 25 MB, before asking the API;
   - each attempt gets its own `Idempotency-Key`: a signed URL lasts 5 minutes, so a replayed answer could no longer be used;
   - S3 gets the bytes that were hashed (an `ArrayBuffer`), so the browser adds no `Content-Type`;
   - the list checks back every 3 seconds while an upload is being analyzed, or waiting (for at most 10 minutes) for its file.
4. **The API's `sort`:**
   - `severity` ranks with `array_position(ARRAY['low','medium','high','critical'], severity)`, then `created_at` and `id`, all descending;
   - its cursor is `[severity, created_at, id]`, and the newest-first cursor stays `[created_at, id]`, so each refuses the other with 422;
   - no index is added: an organization holds at most a few thousand findings (§5.7), which Postgres sorts in memory.
5. **The findings list keeps its filters and order in the address** (`?severity=high&sort=newest`), so a view can be shared and the back button works. A value that isn't real is read as no filter. Only what differs from the defaults is written.
6. **Lists load a page at a time** with a "Show more" button, as the API pages with cursors.
7. **A finding's page:**
   - two columns: the AI explanation, the evidence and the activity, then triage, details and techniques beside them; one column on narrow screens;
   - its title and its tab name come from the detector and the source (`Port scan from 10.0.3.17`);
   - metrics are labeled per key, and a key a newer detector adds is still shown;
   - a technique the detector and the AI both name is listed once, saying so;
   - MITRE's notice comes from the API's reference data (`GET /api/v1/attack-techniques/{id}`), read once and kept;
   - the evidence table scrolls in place, under its column names, so fifty flows don't push the rest of the page down.
8. **Triage:**
   - status and assignee are chosen, then saved together, as the role is on the members page (Plan 6a): a select changes on every arrow key;
   - only what changed is sent; an empty assignee sends `null`;
   - only Owners, Admins and Analysts are offered as assignees (the owner's decision, Plan 4c);
   - a 412 reloads the finding and says someone changed it meanwhile; a newer version, from anywhere, replaces what was chosen.
9. **Comments** keep their idempotency key as long as their text, so resending one that failed never posts it twice.
10. **The AI panel:**
    - it reads output schema v1 field by field, and shows nothing of an answer in another shape;
    - each failure has its own sentence (budget used up, AI switched off, service busy, checks failed);
    - after a re-run that queued, it checks for the new explanation every 5 seconds, for 5 minutes; a 200 shows the stored answer, saying nothing was spent;
    - it reads the finding through the page's query, without fetching it again on mount, so opening a finding reads it once.
11. **Members are read once per organization** (`useMembers`), by the members page, the findings list, the finding's page and the audit log, under one query key.
12. **The audit log and the usage** are hidden from Analysts and Viewers: no tab, and their pages say who can see them without calling the API.
13. **Runbook B12 uploads a committed sample** (`docs/samples/port-scan.log`): the landing page's example scan. A backend test checks it is analyzed into exactly the finding the landing page shows.

## Review Focus

1. **A triage change racing someone else's.** The page must show the newer change and overwrite nothing. Tests: Task 6 `a change someone else made first is shown, and nothing is overwritten`; Task 1 `a cursor from one order is a 422 in the other`.
2. **A file S3 refuses, or one too big or empty.** The person must be told what happened, and be able to send it again. Tests: Task 3 `a file over 25 MB, or an empty one, is refused before anything is sent`, `storage refusing the file says so, and the same file can be sent again` and `an upload the API refuses says why`.
3. **An AI answer in an unexpected shape, or carrying markup.** Nothing of it may render as HTML, and a shape this app doesn't know mustn't half-render. Tests: Task 7 `an explanation says it's AI-generated, by which model and prompt, and is shown as plain text`, `an explanation in a newer format than this app knows is not shown`, and `an answer that failed, or in a shape this app doesn't know, is not read`.
4. **Checking back forever.** Polling must stop: an upload whose file never came, a re-run that never answers, a finding read twice per visit. Tests: Task 3 `an upload that finished, or whose file never came, is not watched`; Task 7 `a queued explanation is checked for every few seconds, for five minutes` and `opening a finding reads it once, though several parts of the page show it`.
5. **Controls or pages a role can't use.** Tests: Task 3 `a viewer sees the uploads, with nothing to upload`; Task 6 `a viewer sees the status and assignee, with nothing to change`; Task 7 `a viewer reads the explanation, with nothing to rate or ask`; Tasks 8 and 9 `only owners and admins see …, in the tabs and at its address`.

## Owner prerequisites

- **None to build or review.** The tests stand in for the API and for S3.
- **After the merge:** runbook B2 (the deploy publishes the new web build and the `api` function's `sort`; no migrations), then B12, the new walk through triage.

## File map

| File | Responsibility | Task |
|---|---|---|
| `backend/src/nettriage/adapters/findings.py`, `entrypoints/api/cursors.py`, `entrypoints/api/routes/findings.py` | `sort=severity`, and its cursor | 1 |
| `frontend/src/findings/vocabulary.ts`, `src/orgs/members.ts`, `src/ui/Severity.tsx`, `src/ui/format.ts`, `src/orgs/permissions.ts` | names, formats, role checks and members, shared by every page | 2 |
| `frontend/src/uploads/`, `src/pages/org/Uploads.tsx`, `UploadPanel.tsx`, `src/test/fakeStorage.ts` | the uploads page and sending a file | 3 |
| `frontend/src/findings/findings.ts`, `src/pages/org/Findings.tsx`, `FindingFilters.tsx` | the findings list, where an organization opens | 4 |
| `frontend/src/findings/finding.ts`, `metrics.ts`, `src/pages/org/finding/` (page, facts, evidence, techniques) | a finding's page | 5 |
| `frontend/src/pages/org/finding/Triage.tsx`, `Activity.tsx`, `CommentForm.tsx` | triage, history and comments | 6 |
| `frontend/src/findings/explanation.ts`, `src/pages/org/finding/AiPanel.tsx` | the AI explanation | 7 |
| `frontend/src/audit/auditLog.ts`, `src/pages/org/AuditLog.tsx` | the audit log | 8 |
| `frontend/src/usage/usage.ts`, `src/pages/org/Usage.tsx`, `UsageChart.tsx` | the AI usage | 9 |
| the spec (§10), `docs/runbooks/setup-and-deploy.md` (B11, B12), `docs/samples/port-scan.log`, `README.md` | the decisions, and the owner's walk through triage | 10 |

Tests sit next to the code they test (`*.test.ts`, `*.test.tsx`). Edits to existing files are given as "In `file`, replace: … with: …"; each quoted passage appears exactly once in the file when its step runs.

---

### Task 1: The API sorts findings most severe first

**Files:**
- Modify: `backend/src/nettriage/adapters/findings.py`, `backend/src/nettriage/entrypoints/api/cursors.py`, `backend/src/nettriage/entrypoints/api/routes/findings.py`, the spec's §7 table
- Regenerate: `frontend/openapi.json`, `frontend/src/api/schema.ts` (`just openapi`)
- Test: `backend/tests/api/test_finding_routes.py`

**Interfaces:**
- Consumes: the findings list route and `list_findings` (Plan 4b), `encode_cursor` and `decode_cursor` (Plan 4a).
- Produces:
  - `GET /api/v1/orgs/{org}/findings?sort=newest|severity` (default `newest`); `next_cursor` works only with the order it was made for;
  - in `cursors.py`: `SEVERITIES`, `encode_severity_cursor(severity, created_at, id)` and `decode_severity_cursor(cursor) -> (severity, created_at, id)`;
  - `list_findings(…, sort="newest" | "severity", before=…, before_severity=…)` and the type `FindingSort`;
  - in the generated client: the query parameter `sort?: "newest" | "severity"`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/api/test_finding_routes.py`, replace:
```python


@pytest.mark.parametrize(
    "params",
    [{"status": "closed"}, {"severity": "urgent"}, {"upload": "nope"}, {"limit": 101}],
)
def test_a_filter_outside_its_values_is_a_422(
```
with:
```python


def test_findings_are_listed_most_severe_first_and_newest_first_within_page_by_page(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    low = add(database, org, severity="low")
    older_high = add(database, org, severity="high")
    critical = add(database, org, severity="critical")
    medium = add(database, org, severity="medium")
    newer_high = add(database, org, severity="high")

    pages = []
    cursor = None
    for _ in range(3):
        params: dict[str, str | int] = {"sort": "severity", "limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        page = signed_in.get(f"/api/v1/orgs/{org[0]}/findings", params=params).json()
        pages.append([finding["id"] for finding in page["findings"]])
        cursor = page["next_cursor"]

    assert pages == [[critical, newer_high], [older_high, medium], [low]]
    assert cursor is None


def test_a_cursor_from_one_order_is_a_422_in_the_other(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    for _ in range(2):
        add(database, org)
    url = f"/api/v1/orgs/{org[0]}/findings"
    by_severity = signed_in.get(url, params={"sort": "severity", "limit": 1}).json()
    newest = signed_in.get(url, params={"limit": 1}).json()

    assert signed_in.get(url, params={"cursor": by_severity["next_cursor"]}).status_code == 422
    assert (
        signed_in.get(url, params={"sort": "severity", "cursor": newest["next_cursor"]}).status_code
        == 422
    )


@pytest.mark.parametrize(
    "params",
    [
        {"status": "closed"},
        {"severity": "urgent"},
        {"upload": "nope"},
        {"limit": 101},
        {"sort": "oldest"},
    ],
)
def test_a_filter_outside_its_values_is_a_422(
```

- [ ] **Step 2: Run them to see them fail**

Run: `just db-up`, then `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/api/test_finding_routes.py`
Expected: FAIL: `3 failed, 17 passed`. The severity order isn't there yet, both cursors are read as newest-first ones, and `sort=oldest` is still accepted.

- [ ] **Step 3: Sort by severity, with its own cursor**

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
type FindingStatus = Literal["open", "investigating", "resolved", "false_positive"]
type FindingSeverity = Literal["low", "medium", "high", "critical"]

# A finding's detail lists at most this many of its latest events.
```
with:
```python
type FindingStatus = Literal["open", "investigating", "resolved", "false_positive"]
type FindingSeverity = Literal["low", "medium", "high", "critical"]
type FindingSort = Literal["newest", "severity"]

# A finding's detail lists at most this many of its latest events.
```

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
    "f.assignee_id, f.version, f.created_at"
)


```
with:
```python
    "f.assignee_id, f.version, f.created_at"
)

# Severity as a number to sort by, most severe highest (Plan 6b).
_RANK = "array_position(ARRAY['low', 'medium', 'high', 'critical'], {})"
_ORDERS: dict[FindingSort, tuple[str, str]] = {
    # (the rows after the cursor, the sort order)
    "newest": (
        "(f.created_at, f.id) < (CAST(:before_at AS timestamptz), CAST(:before_id AS uuid))",
        "f.created_at DESC, f.id DESC",
    ),
    "severity": (
        f"({_RANK.format('f.severity')}, f.created_at, f.id) < "
        f"({_RANK.format('CAST(:before_severity AS text)')}, "
        "CAST(:before_at AS timestamptz), CAST(:before_id AS uuid))",
        f"{_RANK.format('f.severity')} DESC, f.created_at DESC, f.id DESC",
    ),
}


```

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
    *,
    limit: int,
    before: tuple[datetime, UUID] | None = None,
) -> list[FindingSummary]:
    """The org's findings, newest first, narrowed by the filters that are set."""
    before_at, before_id = before or (None, None)
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        rows = connection.execute(
```
with:
```python
    *,
    limit: int,
    sort: FindingSort = "newest",
    before: tuple[datetime, UUID] | None = None,
    before_severity: FindingSeverity | None = None,
) -> list[FindingSummary]:
    """The org's findings, newest first or most severe first (then newest), narrowed by the
    filters that are set. A page continues after `before`, and after `before_severity` too when
    sorted by severity."""
    before_at, before_id = before or (None, None)
    after_cursor, order = _ORDERS[sort]
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        rows = connection.execute(
```

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
                "AND (CAST(:detector AS text) IS NULL OR f.detector_id = :detector) "
                "AND (CAST(:upload AS uuid) IS NULL OR f.upload_id = :upload) "
                "AND (CAST(:before_at AS timestamptz) IS NULL OR (f.created_at, f.id) < "
                "(CAST(:before_at AS timestamptz), CAST(:before_id AS uuid))) "
                "ORDER BY f.created_at DESC, f.id DESC LIMIT :limit"
            ),
            {
```
with:
```python
                "AND (CAST(:detector AS text) IS NULL OR f.detector_id = :detector) "
                "AND (CAST(:upload AS uuid) IS NULL OR f.upload_id = :upload) "
                f"AND (CAST(:before_at AS timestamptz) IS NULL OR {after_cursor}) "
                f"ORDER BY {order} LIMIT :limit"
            ),
            {
```

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
                "before_at": before_at,
                "before_id": before_id,
                "limit": limit,
            },
```
with:
```python
                "before_at": before_at,
                "before_id": before_id,
                "before_severity": before_severity,
                "limit": limit,
            },
```

In `backend/src/nettriage/entrypoints/api/cursors.py`, replace:
```python
"""Cursors for lists that page newest first (spec §7): an opaque base64url string holding the
last item's `(created_at, id)`, which the next page continues after."""

from __future__ import annotations
```
with:
```python
"""Cursors for lists that page newest first (spec §7): an opaque base64url string holding the
last item's `(created_at, id)`, which the next page continues after. Findings sorted most severe
first (Plan 6b) lead with the last item's severity: `(severity, created_at, id)`."""

from __future__ import annotations
```

In `backend/src/nettriage/entrypoints/api/cursors.py`, replace:
```python
            422, detail="The cursor isn't valid; start from the first page."
        ) from error
```
with:
```python
            422, detail="The cursor isn't valid; start from the first page."
        ) from error


SEVERITIES = ("low", "medium", "high", "critical")


def encode_severity_cursor(severity: str, created_at: datetime, item_id: UUID) -> str:
    """A cursor for a list sorted most severe first: the last item's severity leads."""
    raw = json.dumps([severity, created_at.isoformat(), str(item_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_severity_cursor(cursor: str) -> tuple[str, datetime, UUID]:
    """A cursor made for the newest-first order has no severity, so it is refused here."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        severity, created_at, item_id = json.loads(raw)
        if severity not in SEVERITIES:
            raise ValueError(severity)
        return severity, datetime.fromisoformat(created_at), UUID(item_id)
    except (ValueError, TypeError) as error:
        raise HTTPException(
            422, detail="The cursor isn't valid; start from the first page."
        ) from error
```

In `backend/src/nettriage/entrypoints/api/routes/findings.py`, replace:
```python
from __future__ import annotations

from typing import Annotated
from uuid import UUID

```
with:
```python
from __future__ import annotations

from typing import Annotated, Literal, cast
from uuid import UUID

```

In `backend/src/nettriage/entrypoints/api/routes/findings.py`, replace:
```python
)
from nettriage.entrypoints.api.access import OrgContext, OrgMember
from nettriage.entrypoints.api.cursors import decode_cursor, encode_cursor
from nettriage.entrypoints.api.finding_schemas import FindingOut, FindingsOut, FindingSummaryOut
from nettriage.entrypoints.api.org_errors import org_rules
```
with:
```python
)
from nettriage.entrypoints.api.access import OrgContext, OrgMember
from nettriage.entrypoints.api.cursors import (
    decode_cursor,
    decode_severity_cursor,
    encode_cursor,
    encode_severity_cursor,
)
from nettriage.entrypoints.api.finding_schemas import FindingOut, FindingsOut, FindingSummaryOut
from nettriage.entrypoints.api.org_errors import org_rules
```

In `backend/src/nettriage/entrypoints/api/routes/findings.py`, replace:
```python
    detector: Annotated[str | None, Query(max_length=50)] = None,
    upload: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> FindingsOut:
    """The org's findings, newest first. Filters: status, severity, detector, upload."""
    before = decode_cursor(cursor) if cursor else None
    filters = FindingFilters(status=status, severity=severity, detector=detector, upload_id=upload)
    with org_rules(request, "The finding list"):
```
with:
```python
    detector: Annotated[str | None, Query(max_length=50)] = None,
    upload: UUID | None = None,
    sort: Literal["newest", "severity"] = "newest",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> FindingsOut:
    """The org's findings, newest first, or most severe first and then newest with
    `sort=severity` (Plan 6b). Filters: status, severity, detector, upload. A cursor works only
    with the order it was made for."""
    before_severity: FindingSeverity | None = None
    before = None
    if cursor and sort == "severity":
        last_severity, last_at, last_id = decode_severity_cursor(cursor)
        before_severity, before = cast(FindingSeverity, last_severity), (last_at, last_id)
    elif cursor:
        before = decode_cursor(cursor)
    filters = FindingFilters(status=status, severity=severity, detector=detector, upload_id=upload)
    with org_rules(request, "The finding list"):
```

In `backend/src/nettriage/entrypoints/api/routes/findings.py`, replace:
```python
            filters,
            limit=limit + 1,
            before=before,
        )
    page = found[:limit]
    more = len(found) > limit
    return FindingsOut(
        findings=[FindingSummaryOut.of(finding) for finding in page],
        next_cursor=encode_cursor(page[-1].created_at, page[-1].id) if more else None,
    )

```
with:
```python
            filters,
            limit=limit + 1,
            sort=sort,
            before=before,
            before_severity=before_severity,
        )
    page = found[:limit]
    next_cursor = None
    if len(found) > limit:
        last = page[-1]
        next_cursor = (
            encode_severity_cursor(last.severity, last.created_at, last.id)
            if sort == "severity"
            else encode_cursor(last.created_at, last.id)
        )
    return FindingsOut(
        findings=[FindingSummaryOut.of(finding) for finding in page], next_cursor=next_cursor
    )

```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `GET /api/v1/orgs/{org}/uploads` | `uploads:read` | |
| `GET /api/v1/orgs/{org}/uploads/{id}` | `uploads:read` | Status and statistics |
| `GET /api/v1/orgs/{org}/findings` | `findings:read` | Filters: status, severity, detector, upload. Newest first, a page at a time |
| `GET /api/v1/orgs/{org}/findings/{id}` | `findings:read` | With evidence, techniques, the latest AI analysis as `ai_analysis` (null until there is one: status, provider, model, prompt and output schema versions, output, `error_code`, tokens, `cost_usd`, latency, and the rating's `feedback` and `feedback_by`; Plans 5b and 5c), and the latest 100 events with `events_total` (Plan 4c); the `ETag` is the finding's version |
| `PATCH /api/v1/orgs/{org}/findings/{id}` | `findings:triage` | Body: `status`, `assignee_id` (`null` unassigns), or both; `If-Match`. Any status can change to any other, and the assignee must be an Owner, Admin or Analyst of the org (422 otherwise; the owner's decisions, Plan 4c). Each change is an event in the finding's history and an audit event |
```
with:
```markdown
| `GET /api/v1/orgs/{org}/uploads` | `uploads:read` | |
| `GET /api/v1/orgs/{org}/uploads/{id}` | `uploads:read` | Status and statistics |
| `GET /api/v1/orgs/{org}/findings` | `findings:read` | Filters: status, severity, detector, upload. `sort=newest` (the default) or `sort=severity`: most severe first, then newest (Plan 6b). A page at a time; a cursor works only with the order it was made for |
| `GET /api/v1/orgs/{org}/findings/{id}` | `findings:read` | With evidence, techniques, the latest AI analysis as `ai_analysis` (null until there is one: status, provider, model, prompt and output schema versions, output, `error_code`, tokens, `cost_usd`, latency, and the rating's `feedback` and `feedback_by`; Plans 5b and 5c), and the latest 100 events with `events_total` (Plan 4c); the `ETag` is the finding's version |
| `PATCH /api/v1/orgs/{org}/findings/{id}` | `findings:triage` | Body: `status`, `assignee_id` (`null` unassigns), or both; `If-Match`. Any status can change to any other, and the assignee must be an Owner, Admin or Analyst of the org (422 otherwise; the owner's decisions, Plan 4c). Each change is an event in the finding's history and an audit event |
```

- [ ] **Step 4: Run the tests again**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/api/test_finding_routes.py`
Expected: `20 passed`.

- [ ] **Step 5: Regenerate the API contract**

Run: `just openapi`
Expected: `wrote ..\frontend\openapi.json`, then `openapi.json → src/api/schema.ts`. `git diff frontend/src/api/schema.ts` shows the findings list's new query parameter `sort?: "newest" | "severity"`.

- [ ] **Step 6: Check the backend and the contract**

Run: `cd backend && uv run python -m ruff check src tests && uv run python -m ruff format --check src tests && uv run python -m mypy src && uv run python -m pytest tests/api/test_openapi_document.py && cd ../frontend && pnpm check:api`
Expected: Ruff passes and finds every file formatted, mypy reports `Success: no issues found`, `4 passed`, and `check:api` finds `schema.ts` up to date.

- [ ] **Step 7: Commit**

```bash
git add backend frontend/openapi.json frontend/src/api/schema.ts docs/superpowers/specs
git commit -m "feat(api): findings sorted most severe first with sort=severity" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 2: Names, formats, role checks and members, shared by every page

**Files:**
- Create: `frontend/src/findings/vocabulary.ts`, `frontend/src/orgs/members.ts`, `frontend/src/ui/Severity.tsx`
- Modify: `frontend/src/ui/format.ts`, `frontend/src/orgs/permissions.ts`, `frontend/src/pages/org/MemberRow.tsx`, `frontend/src/pages/org/Members.tsx`, `frontend/src/styles.css`, `frontend/src/test/fixtures.ts`, `frontend/src/test/orgApi.ts`, `frontend/src/pages/org/OrgSettings.test.tsx`
- Test: `frontend/src/findings/vocabulary.test.ts`, `frontend/src/orgs/members.test.ts`, `frontend/src/ui/Severity.test.tsx`, `frontend/src/ui/format.test.ts`, `frontend/src/orgs/permissions.test.ts`

**Interfaces:**
- Consumes: 6a's `api`, `unwrap`, `SeverityBars`, `formatDate`, and the fixtures `OWNER` and `ADMIN`.
- Produces:
  - in `src/findings/vocabulary.ts`: the types `Severity` and `Status`; `SEVERITIES` (most severe first), `STATUSES`, `DETECTORS` (ID to name); `severityLabel(s)`, `severityLevel(s)` (1 to 4), `statusLabel(s)`, `detectorName(id)`, `protocolName(n)`, and the guards `isSeverity(v)` and `isStatus(v)`;
  - `<Severity severity={…} />`: the bars and the word, in `src/ui/Severity.tsx`;
  - in `src/ui/format.ts`: `formatDateTime(iso)`, `formatNumber(n)` and `formatUsd(amount)`;
  - in `src/orgs/permissions.ts`: `canContribute(role)`, `canReadAudit(role)` and `canReadUsage(role)`;
  - in `src/orgs/members.ts`: `Member`, `membersKey(orgId)`, `useMembers(orgId)` (its data is the list itself) and `memberName(members, userId)`. `Member` and `membersKey` move here from `MemberRow.tsx`, and the members page reads through `useMembers`, so every page shares one query.

- [ ] **Step 1: Write the failing tests**

`frontend/src/findings/vocabulary.test.ts`:
```ts
import { expect, test } from "vitest";
import {
  DETECTORS,
  SEVERITIES,
  STATUSES,
  detectorName,
  isSeverity,
  isStatus,
  protocolName,
  severityLabel,
  severityLevel,
  statusLabel,
} from "./vocabulary";

test("severities run most severe first, each with a word and one to four bars", () => {
  expect(SEVERITIES.map(severityLabel)).toEqual(["Critical", "High", "Medium", "Low"]);
  expect(SEVERITIES.map(severityLevel)).toEqual([4, 3, 2, 1]);
});

test("statuses read as words", () => {
  expect(STATUSES.map(statusLabel)).toEqual([
    "Open",
    "Investigating",
    "Resolved",
    "False positive",
  ]);
});

test("detectors have names, and an unknown one shows its ID", () => {
  expect(Object.keys(DETECTORS)).toEqual([
    "port_scan",
    "remote_access_bruteforce",
    "outbound_volume",
  ]);
  expect(detectorName("remote_access_bruteforce")).toBe("SSH/RDP brute force");
  expect(detectorName("dns_tunnel")).toBe("dns_tunnel");
});

test("protocols are named by their number", () => {
  expect([6, 17, 1, 47].map(protocolName)).toEqual(["TCP", "UDP", "ICMP", "Protocol 47"]);
});

test("only real severities and statuses are taken from an address", () => {
  expect(["high", "urgent", null].map(isSeverity)).toEqual([true, false, false]);
  expect(["false_positive", "closed", null].map(isStatus)).toEqual([true, false, false]);
});
```

`frontend/src/orgs/members.test.ts`:
```ts
import { expect, test } from "vitest";
import { ADMIN, OWNER } from "../test/fixtures";
import { memberName } from "./members";

test("a member is named by their display name, else their email", () => {
  expect(memberName([OWNER, ADMIN], ADMIN.user_id)).toBe("Ben Admin");
  expect(memberName([OWNER, ADMIN], OWNER.user_id)).toBe("ana@example.com");
});

test("someone no longer in the organization is named plainly, and so is anyone still loading", () => {
  expect(memberName([OWNER], "01a0e9e9-0000-7000-8000-0000000000ff")).toBe("A former member");
  expect(memberName(undefined, OWNER.user_id)).toBe("A member");
});
```

In `frontend/src/orgs/permissions.test.ts`, replace:
```ts
import { expect, test } from "vitest";
import { assignableRoles, canDelete, canInvite, canManage, canRename } from "./permissions";
import type { Role } from "./roles";

```
with:
```ts
import { expect, test } from "vitest";
import {
  assignableRoles,
  canContribute,
  canDelete,
  canInvite,
  canManage,
  canReadAudit,
  canReadUsage,
  canRename,
} from "./permissions";
import type { Role } from "./roles";

```

In `frontend/src/orgs/permissions.test.ts`, replace:
```ts
  expect(ROLES.map(canDelete)).toEqual([true, false, false, false]);
});
```
with:
```ts
  expect(ROLES.map(canDelete)).toEqual([true, false, false, false]);
});

test("owners, admins and analysts do the work: upload, triage, comment and ask the AI", () => {
  expect(ROLES.map(canContribute)).toEqual([true, true, true, false]);
});

test("only owners and admins read the audit log and the AI usage", () => {
  expect(ROLES.map(canReadAudit)).toEqual([true, true, false, false]);
  expect(ROLES.map(canReadUsage)).toEqual([true, true, false, false]);
});
```

In `frontend/src/pages/org/OrgSettings.test.tsx`, replace:
```tsx
import { screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { Member } from "./MemberRow";
import { ACME, ADMIN, ORG_ID, OWNER, SIGNED_OUT, VIEWER, memberOf, org } from "../../test/fixtures";
import { ORG, signedInAs } from "../../test/orgApi";
```
with:
```tsx
import { screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { Member } from "../../orgs/members";
import { ACME, ADMIN, ORG_ID, OWNER, SIGNED_OUT, VIEWER, memberOf, org } from "../../test/fixtures";
import { ORG, signedInAs } from "../../test/orgApi";
```

In `frontend/src/test/fixtures.ts`, replace:
```ts
import type { Me, Membership } from "../auth/session";
import type { Org } from "../orgs/org";
import type { Member } from "../pages/org/MemberRow";
import type { Reply } from "./fakeApi";

```
with:
```ts
import type { Me, Membership } from "../auth/session";
import type { Org } from "../orgs/org";
import type { Member } from "../orgs/members";
import type { Reply } from "./fakeApi";

```

In `frontend/src/test/orgApi.ts`, replace:
```ts
/** The fake API for a person signed in to Acme Security as one of its members. */
import type { Me } from "../auth/session";
import type { Member } from "../pages/org/MemberRow";
import { type FakeApi, type Handler, fakeApi } from "./fakeApi";
import { ACME, ADMIN, ME, ORG_ID, OWNER, VIEWER, memberOf, org } from "./fixtures";
```
with:
```ts
/** The fake API for a person signed in to Acme Security as one of its members. */
import type { Me } from "../auth/session";
import type { Member } from "../orgs/members";
import { type FakeApi, type Handler, fakeApi } from "./fakeApi";
import { ACME, ADMIN, ME, ORG_ID, OWNER, VIEWER, memberOf, org } from "./fixtures";
```

`frontend/src/ui/Severity.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { Severity } from "./Severity";

test("a severity is its word, with its bars beside it for sighted readers", () => {
  const { container } = render(<Severity severity="high" />);

  expect(screen.getByText("High")).toBeVisible();
  expect(container.querySelectorAll(".sev i.on")).toHaveLength(3);
});
```

`frontend/src/ui/format.test.ts`:
```ts
import { expect, test } from "vitest";
import { formatDateTime, formatUsd } from "./format";

test("a moment is written with its date and its time", () => {
  const written = formatDateTime("2026-10-03T14:05:00Z");

  expect(written).toMatch(/2026/);
  expect(written).toMatch(/:05/);
});

test("AI costs keep the fractions of a cent they are made of", () => {
  expect(["0", "0.0123", "0.00012345", "12.5"].map(formatUsd)).toEqual([
    "$0.00",
    "$0.0123",
    "$0.0001",
    "$12.50",
  ]);
  expect(formatUsd("0.00004")).toBe("under $0.0001");
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && pnpm exec vitest run src/findings src/orgs src/ui/format.test.ts src/ui/Severity.test.tsx`
Expected: FAIL. Three files stop before running on `Failed to resolve import` (`./vocabulary`, `./members`, `./Severity`), and four tests fail: the two new role checks and the two new formats aren't functions yet.

- [ ] **Step 3: Write the shared pieces, and move members to them**

`frontend/src/findings/vocabulary.ts`:
```ts
/** How findings are named on screen (spec §8.2): severities, statuses, detectors, protocols. */
import type { components } from "../api/schema";

export type Severity = components["schemas"]["FindingSeverity"];
export type Status = components["schemas"]["FindingStatus"];

/** Most severe first, the order findings are triaged in. */
export const SEVERITIES: readonly Severity[] = ["critical", "high", "medium", "low"];

const SEVERITY_LABELS: Record<Severity, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
};

const SEVERITY_LEVELS: Record<Severity, number> = { critical: 4, high: 3, medium: 2, low: 1 };

export const STATUSES: readonly Status[] = ["open", "investigating", "resolved", "false_positive"];

const STATUS_LABELS: Record<Status, string> = {
  open: "Open",
  investigating: "Investigating",
  resolved: "Resolved",
  false_positive: "False positive",
};

/** Milestone 1's detectors (spec §8.2), by the ID the API uses. */
export const DETECTORS: Record<string, string> = {
  port_scan: "Port scan",
  remote_access_bruteforce: "SSH/RDP brute force",
  outbound_volume: "Unusual outbound volume",
};

const PROTOCOLS: Record<number, string> = { 1: "ICMP", 6: "TCP", 17: "UDP" };

export function severityLabel(severity: Severity): string {
  return SEVERITY_LABELS[severity];
}

/** One to four bars, Low to Critical. */
export function severityLevel(severity: Severity): number {
  return SEVERITY_LEVELS[severity];
}

export function statusLabel(status: Status): string {
  return STATUS_LABELS[status];
}

export function detectorName(id: string): string {
  return DETECTORS[id] ?? id;
}

export function protocolName(protocol: number): string {
  return PROTOCOLS[protocol] ?? `Protocol ${protocol}`;
}

export function isSeverity(value: string | null): value is Severity {
  return value !== null && value in SEVERITY_LABELS;
}

export function isStatus(value: string | null): value is Status {
  return value !== null && value in STATUS_LABELS;
}
```

`frontend/src/orgs/members.ts`:
```ts
/** An organization's members (spec §7), read once and shared by every page that names people. */
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";

export type Member = components["schemas"]["MemberOut"];

export function membersKey(orgId: string) {
  return ["members", orgId] as const;
}

export function useMembers(orgId: string) {
  return useQuery({
    queryKey: membersKey(orgId),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/members", { params: { path: { org_id: orgId } } }),
      ).members,
  });
}

/** Who someone is, as their teammates know them: display name, else email. */
export function memberName(members: Member[] | undefined, userId: string): string {
  if (members === undefined) {
    return "A member";
  }
  const member = members.find((candidate) => candidate.user_id === userId);
  return member === undefined ? "A former member" : (member.display_name ?? member.email);
}
```

In `frontend/src/orgs/permissions.ts`, replace:
```ts
  return role === "owner";
}
```
with:
```ts
  return role === "owner";
}

/** Owners, Admins and Analysts upload, triage, comment and ask the AI; Viewers only read. */
export function canContribute(role: Role): boolean {
  return role !== "viewer";
}

export function canReadAudit(role: Role): boolean {
  return role === "owner" || role === "admin";
}

export function canReadUsage(role: Role): boolean {
  return role === "owner" || role === "admin";
}
```

In `frontend/src/pages/org/MemberRow.tsx`, replace:
```tsx
import { api } from "../../api/client";
import { unwrap } from "../../api/problem";
import type { components } from "../../api/schema";
import type { Org } from "../../orgs/org";
import { assignableRoles, canManage } from "../../orgs/permissions";
```
with:
```tsx
import { api } from "../../api/client";
import { unwrap } from "../../api/problem";
import { type Member, membersKey } from "../../orgs/members";
import type { Org } from "../../orgs/org";
import { assignableRoles, canManage } from "../../orgs/permissions";
```

In `frontend/src/pages/org/MemberRow.tsx`, replace:
```tsx
import { ErrorNotice } from "../../ui/ErrorNotice";
import { formatDate } from "../../ui/format";

export type Member = components["schemas"]["MemberOut"];

export function membersKey(orgId: string) {
  return ["members", orgId] as const;
}

/**
```
with:
```tsx
import { ErrorNotice } from "../../ui/ErrorNotice";
import { formatDate } from "../../ui/format";

/**
```

In `frontend/src/pages/org/Members.tsx`, replace:
```tsx
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { unwrap } from "../../api/problem";
import { useMe } from "../../auth/session";
import { useOrg } from "../../orgs/org";
import { canInvite } from "../../orgs/permissions";
```
with:
```tsx
import { useMe } from "../../auth/session";
import { useMembers } from "../../orgs/members";
import { useOrg } from "../../orgs/org";
import { canInvite } from "../../orgs/permissions";
```

In `frontend/src/pages/org/Members.tsx`, replace:
```tsx
import { usePageTitle } from "../../ui/usePageTitle";
import { Invitations } from "./Invitations";
import { MemberRow, membersKey } from "./MemberRow";

/** `/app/orgs/:org/members`: members, their roles, and invitations (spec §10). */
```
with:
```tsx
import { usePageTitle } from "../../ui/usePageTitle";
import { Invitations } from "./Invitations";
import { MemberRow } from "./MemberRow";

/** `/app/orgs/:org/members`: members, their roles, and invitations (spec §10). */
```

In `frontend/src/pages/org/Members.tsx`, replace:
```tsx
  usePageTitle(`Members · ${org.name}`);
  const me = useMe();
  const members = useQuery({
    queryKey: membersKey(org.id),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/members", { params: { path: { org_id: org.id } } }),
      ),
  });
  return (
    <main id="main" className="page">
```
with:
```tsx
  usePageTitle(`Members · ${org.name}`);
  const me = useMe();
  const members = useMembers(org.id);
  return (
    <main id="main" className="page">
```

In `frontend/src/pages/org/Members.tsx`, replace:
```tsx
        {members.data !== undefined && (
          <span className="muted">
            {members.data.members.length === 1
              ? "1 member"
              : `${members.data.members.length} members`}
          </span>
        )}
```
with:
```tsx
        {members.data !== undefined && (
          <span className="muted">
            {members.data.length === 1 ? "1 member" : `${members.data.length} members`}
          </span>
        )}
```

In `frontend/src/pages/org/Members.tsx`, replace:
```tsx
          </thead>
          <tbody>
            {members.data.members.map((member) => (
              <MemberRow
                key={member.user_id}
```
with:
```tsx
          </thead>
          <tbody>
            {members.data.map((member) => (
              <MemberRow
                key={member.user_id}
```

In `frontend/src/styles.css`, replace:
```css
.sev i.on {
  background: var(--signal);
}

```
with:
```css
.sev i.on {
  background: var(--signal);
}

.severity {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  font-stretch: 75%;
  font-weight: 650;
  white-space: nowrap;
}

```

`frontend/src/ui/Severity.tsx`:
```tsx
import { type Severity as Level, severityLabel, severityLevel } from "../findings/vocabulary";
import { SeverityBars } from "./SeverityBars";

/** A finding's severity: its word, with its bars beside it (amber: a detector's verdict). */
export function Severity({ severity }: { severity: Level }) {
  return (
    <span className="severity">
      <SeverityBars level={severityLevel(severity)} />
      {severityLabel(severity)}
    </span>
  );
}
```

In `frontend/src/ui/format.ts`, replace:
```ts
const DATE = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

/** A date as the person's browser writes dates, such as "3 Oct 2026". */
```
with:
```ts
const DATE = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });
const DATE_TIME = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const NUMBER = new Intl.NumberFormat(undefined);

/** A date as the person's browser writes dates, such as "3 Oct 2026". */
```

In `frontend/src/ui/format.ts`, replace:
```ts
  return DATE.format(new Date(iso));
}
```
with:
```ts
  return DATE.format(new Date(iso));
}

/** A moment as the person's browser writes it, such as "3 Oct 2026, 14:05". */
export function formatDateTime(iso: string): string {
  return DATE_TIME.format(new Date(iso));
}

/** A count with the person's digit grouping, such as "1,204". */
export function formatNumber(value: number): string {
  return NUMBER.format(value);
}

/**
 * An AI cost in US dollars, from the API's decimal string. A call costs fractions of a cent, so
 * amounts under a dollar keep four decimals.
 */
export function formatUsd(amount: string): string {
  const value = Number(amount);
  if (!Number.isFinite(value) || value === 0) {
    return "$0.00";
  }
  if (value < 0.0001) {
    return "under $0.0001";
  }
  return `$${value.toFixed(value < 1 ? 4 : 2)}`;
}
```

- [ ] **Step 4: Run the tests again**

Run: `cd frontend && pnpm exec vitest run src/findings src/orgs src/ui/format.test.ts src/ui/Severity.test.tsx`
Expected: `16 passed`.

- [ ] **Step 5: Check everything**

Run: `cd frontend && pnpm lint && pnpm exec tsc --noEmit && pnpm test`
Expected: lint and types are clean, and `119 passed`: the members page works the same through `useMembers`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(web): findings vocabulary, formats, role checks and shared members" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 3: The uploads page

**Files:**
- Create: `frontend/src/uploads/uploads.ts`, `frontend/src/uploads/sendUpload.ts`, `frontend/src/pages/org/Uploads.tsx`, `frontend/src/pages/org/UploadPanel.tsx`, `frontend/src/test/fakeStorage.ts`
- Modify: `frontend/src/app/routes.tsx`, `frontend/src/pages/org/OrgLayout.tsx`, `frontend/src/styles.css`, `frontend/src/test/fixtures.ts`
- Test: `frontend/src/uploads/uploads.test.ts`, `frontend/src/pages/org/Uploads.test.tsx`

**Interfaces:**
- Consumes: Task 2's `canContribute`, `formatDateTime` and `formatNumber`; 6a's `api`, `idempotencyKey`, `sha256Hex`, `unwrap`, `errorMessage`, `useOrg`, `ErrorNotice`, `Loading` and `usePageTitle`.
- Produces:
  - in `src/uploads/uploads.ts`: `Upload`, `uploadsKey(orgId)`, `isBusy(upload, now)` and `useUploads(orgId)`, which pages and polls;
  - in `src/uploads/sendUpload.ts`: `MAX_UPLOAD_BYTES`, `UploadProblem`, `Progress` and `sendUpload(orgId, file, onProgress)`;
  - the route `uploads` and its tab;
  - for tests: `fakeStorage({ status })` in `src/test/fakeStorage.ts`, which stands in for S3's `XMLHttpRequest` and returns `{ puts }`; and the fixtures `UPLOAD_ID` and `upload(fields)`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/pages/org/Uploads.test.tsx`:
```tsx
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { sha256Hex } from "../../api/client";
import { fakeStorage } from "../../test/fakeStorage";
import { ORG_ID, OWNER, UPLOAD_ID, VIEWER, upload } from "../../test/fixtures";
import { ORG, signedInAs } from "../../test/orgApi";
import { renderAt } from "../../test/render";

const UPLOADS = `GET ${ORG}/uploads`;
const PAGE = `/app/orgs/${ORG_ID}/uploads`;
const SIGNED_URL =
  "https://nettriage-dev-uploads.s3.eu-north-1.amazonaws.com/o/u?X-Amz-Signature=1";
const SIGNED_HEADERS = { "x-amz-checksum-sha256": "c2hh", "x-amz-meta-traceparent": "00-ab-cd-01" };
const NONE = { body: { uploads: [], next_cursor: null } };

function created(filename = "port-scan.log") {
  return {
    status: 201,
    body: {
      upload: upload({
        original_filename: filename,
        status: "pending_upload",
        rows_parsed: null,
        rows_rejected: null,
      }),
      upload_url: SIGNED_URL,
      upload_headers: SIGNED_HEADERS,
      expires_at: "2026-10-04T09:35:00Z",
    },
  };
}

test("uploads are listed with their status, rows and a link to their findings", async () => {
  signedInAs(OWNER, {
    [UPLOADS]: {
      body: {
        uploads: [
          upload(),
          upload({
            id: "01a10500-0000-7000-8000-000000000002",
            original_filename: "notes.txt",
            status: "failed",
            failure_reason: "No line in this file is a VPC flow log record.",
            rows_parsed: 0,
          }),
        ],
        next_cursor: null,
      },
    },
  });

  renderAt(PAGE);

  const analyzed = await screen.findByRole("row", { name: /port-scan\.log/ });
  expect(within(analyzed).getByText("Analyzed")).toBeInTheDocument();
  expect(within(analyzed).getByText("3 rejected")).toBeInTheDocument();
  expect(
    within(analyzed).getByRole("link", { name: "Findings from port-scan.log" }),
  ).toHaveAttribute("href", `/app/orgs/${ORG_ID}/findings?upload=${UPLOAD_ID}`);
  const failed = screen.getByRole("row", { name: /notes\.txt/ });
  expect(within(failed).getByText("Failed")).toBeInTheDocument();
  expect(within(failed).getByText("No line in this file is a VPC flow log record.")).toBeVisible();
  expect(within(failed).queryByRole("link")).toBeNull();
  expect(document.title).toBe("Uploads · Acme Security · NetTriage");
});

test("a contributor uploads a file: it's fingerprinted, sent to storage as signed, and listed", async () => {
  let reads = 0;
  const fake = signedInAs(OWNER, {
    [UPLOADS]: () => ({
      body: { uploads: ++reads === 1 ? [] : [upload({ status: "processing" })], next_cursor: null },
    }),
    [`POST ${ORG}/uploads`]: created(),
  });
  const storage = fakeStorage();
  const { user } = renderAt(PAGE);
  const content = "2 123456789012 eni-1 10.0.3.17 10.0.0.5 40000 22 6 1 40 1 2 REJECT OK\n";

  await user.upload(
    await screen.findByLabelText("Flow log file"),
    new File([content], "port-scan.log"),
  );
  await user.click(screen.getByRole("button", { name: "Upload" }));

  expect(await screen.findByText(/Uploaded port-scan\.log/)).toBeInTheDocument();
  const post = fake.requests.find((request) => request.method === "POST");
  const bytes = new TextEncoder().encode(content);
  expect(await post?.json()).toEqual({
    filename: "port-scan.log",
    size_bytes: bytes.byteLength,
    sha256: await sha256Hex(bytes),
  });
  expect(post?.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
  expect(storage.puts).toHaveLength(1);
  expect(storage.puts[0]?.method).toBe("PUT");
  expect(storage.puts[0]?.url).toBe(SIGNED_URL);
  expect(storage.puts[0]?.headers).toEqual(SIGNED_HEADERS);
  expect(new TextDecoder().decode(storage.puts[0]?.body)).toBe(content);
  expect(await screen.findByText("Analyzing")).toBeInTheDocument();
});

test("a file over 25 MB, or an empty one, is refused before anything is sent", async () => {
  const fake = signedInAs(OWNER, { [UPLOADS]: NONE });
  const { user } = renderAt(PAGE);
  const input = await screen.findByLabelText("Flow log file");

  await user.upload(input, new File([new Uint8Array(25 * 1024 * 1024 + 1)], "huge.log"));
  await user.click(screen.getByRole("button", { name: "Upload" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Files can be at most 25 MB.");

  await user.upload(input, new File([], "empty.log"));
  await user.click(screen.getByRole("button", { name: "Upload" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("This file is empty.");
  expect(fake.requests.some((request) => request.method === "POST")).toBe(false);
});

test("storage refusing the file says so, and the same file can be sent again", async () => {
  const fake = signedInAs(OWNER, { [UPLOADS]: NONE, [`POST ${ORG}/uploads`]: created("a.log") });
  fakeStorage({ status: 403 });
  const { user } = renderAt(PAGE);

  await user.upload(await screen.findByLabelText("Flow log file"), new File(["x\n"], "a.log"));
  await user.click(screen.getByRole("button", { name: "Upload" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Storage refused the file (status 403). Upload it again.",
  );
  fakeStorage();
  await user.click(screen.getByRole("button", { name: "Upload" }));
  expect(await screen.findByText(/Uploaded a\.log/)).toBeInTheDocument();
  const keys = fake.requests
    .filter((request) => request.method === "POST")
    .map((request) => request.headers.get("Idempotency-Key"));
  expect(new Set(keys).size).toBe(2);
});

test("an upload the API refuses says why", async () => {
  signedInAs(OWNER, {
    [UPLOADS]: NONE,
    [`POST ${ORG}/uploads`]: {
      status: 503,
      body: { title: "Service Unavailable", detail: "Uploads are paused.", trace_id: "t5" },
    },
  });
  const { user } = renderAt(PAGE);

  await user.upload(await screen.findByLabelText("Flow log file"), new File(["x\n"], "a.log"));
  await user.click(screen.getByRole("button", { name: "Upload" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Uploads are paused. (reference t5)");
});

test("a viewer sees the uploads, with nothing to upload", async () => {
  signedInAs(VIEWER, { [UPLOADS]: { body: { uploads: [upload()], next_cursor: null } } });

  renderAt(PAGE);

  expect(await screen.findByRole("row", { name: /port-scan\.log/ })).toBeInTheDocument();
  expect(screen.queryByLabelText("Flow log file")).toBeNull();
});

test("no uploads yet says so", async () => {
  signedInAs(VIEWER, { [UPLOADS]: NONE });

  renderAt(PAGE);

  expect(await screen.findByText("No uploads yet.")).toBeInTheDocument();
});

test("older uploads load a page at a time", async () => {
  const older = upload({
    id: "01a10500-0000-7000-8000-000000000009",
    original_filename: "old.log",
  });
  signedInAs(OWNER, {
    [UPLOADS]: (request) =>
      new URL(request.url).searchParams.get("cursor") === "c2"
        ? { body: { uploads: [older], next_cursor: null } }
        : { body: { uploads: [upload()], next_cursor: "c2" } },
  });
  const { user } = renderAt(PAGE);

  await user.click(await screen.findByRole("button", { name: "Show older uploads" }));

  expect(await screen.findByRole("row", { name: /old\.log/ })).toBeInTheDocument();
  expect(screen.getByRole("row", { name: /port-scan\.log/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Show older uploads" })).toBeNull();
});
```

`frontend/src/test/fakeStorage.ts`:
```ts
/**
 * A stand-in for S3 in tests: answers the browser's PUT of a file (an `XMLHttpRequest`, for its
 * progress events) and keeps what was sent. Vitest restores the real one after each test.
 */
import { vi } from "vitest";

export interface StoredPut {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: ArrayBuffer;
}

/** `status: 0` stands for a network failure. */
export function fakeStorage({ status = 200 }: { status?: number } = {}): { puts: StoredPut[] } {
  const puts: StoredPut[] = [];
  class FakeRequest {
    upload: { onprogress: ((event: ProgressEvent) => void) | null } = { onprogress: null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    status = 0;
    private method = "";
    private url = "";
    private headers: Record<string, string> = {};

    open(method: string, url: string) {
      this.method = method;
      this.url = url;
    }

    setRequestHeader(name: string, value: string) {
      this.headers[name] = value;
    }

    send(body: ArrayBuffer) {
      puts.push({ method: this.method, url: this.url, headers: { ...this.headers }, body });
      queueMicrotask(() => {
        const total = body.byteLength;
        this.upload.onprogress?.({
          lengthComputable: true,
          loaded: total / 2,
          total,
        } as ProgressEvent);
        this.status = status;
        if (status === 0) {
          this.onerror?.();
        } else {
          this.onload?.();
        }
      });
    }
  }
  vi.stubGlobal("XMLHttpRequest", FakeRequest);
  return { puts };
}
```

In `frontend/src/test/fixtures.ts`, replace:
```ts
import type { Org } from "../orgs/org";
import type { Member } from "../orgs/members";
import type { Reply } from "./fakeApi";

```
with:
```ts
import type { Org } from "../orgs/org";
import type { Member } from "../orgs/members";
import type { Upload } from "../uploads/uploads";
import type { Reply } from "./fakeApi";

```

In `frontend/src/test/fixtures.ts`, replace:
```ts
  joined_at: "2026-10-02T10:00:00Z",
};
```
with:
```ts
  joined_at: "2026-10-02T10:00:00Z",
};

export const UPLOAD_ID = "01a10500-0000-7000-8000-000000000001";

export function upload(fields: Partial<Upload> = {}): Upload {
  return {
    id: UPLOAD_ID,
    original_filename: "port-scan.log",
    size_bytes: 13_312,
    sha256: "a".repeat(64),
    status: "analyzed",
    failure_reason: null,
    rows_parsed: 1204,
    rows_rejected: 3,
    rejected_samples: [],
    findings_truncated: 0,
    flow_start: "2026-10-01T12:00:00Z",
    flow_end: "2026-10-01T12:05:00Z",
    uploaded_by: ME.user.id,
    created_at: "2026-10-04T09:30:00Z",
    processed_at: "2026-10-04T09:31:00Z",
    ...fields,
  };
}
```

`frontend/src/uploads/uploads.test.ts`:
```ts
import { expect, test } from "vitest";
import { upload } from "../test/fixtures";
import { isBusy } from "./uploads";

const NOW = Date.parse("2026-10-04T10:00:00Z");

test("an upload being analyzed is busy, and so is one waiting a few minutes for its file", () => {
  expect(isBusy(upload({ status: "processing" }), NOW)).toBe(true);
  expect(
    isBusy(upload({ status: "pending_upload", created_at: "2026-10-04T09:58:00Z" }), NOW),
  ).toBe(true);
});

test("an upload that finished, or whose file never came, is not watched", () => {
  expect(isBusy(upload({ status: "analyzed" }), NOW)).toBe(false);
  expect(isBusy(upload({ status: "failed" }), NOW)).toBe(false);
  expect(
    isBusy(upload({ status: "pending_upload", created_at: "2026-10-04T09:40:00Z" }), NOW),
  ).toBe(false);
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && pnpm exec vitest run src/uploads src/pages/org/Uploads.test.tsx`
Expected: FAIL. `uploads.test.ts` stops on `Failed to resolve import "./uploads"`, and all 8 page tests fail: there's no uploads page, so they don't find what they look for, such as `Unable to find a label with the text of: Flow log file`.

- [ ] **Step 3: Write the uploads data, sending a file, and the page**

In `frontend/src/app/routes.tsx`, replace:
```tsx
import { OrgLayout } from "../pages/org/OrgLayout";
import { OrgSettings } from "../pages/org/OrgSettings";
import { Orgs } from "../pages/Orgs";
import { Settings } from "../pages/Settings";
```
with:
```tsx
import { OrgLayout } from "../pages/org/OrgLayout";
import { OrgSettings } from "../pages/org/OrgSettings";
import { Uploads } from "../pages/org/Uploads";
import { Orgs } from "../pages/Orgs";
import { Settings } from "../pages/Settings";
```

In `frontend/src/app/routes.tsx`, replace:
```tsx
        children: [
          { index: true, element: <Navigate to="members" replace /> },
          { path: "members", element: <Members /> },
          { path: "settings", element: <OrgSettings /> },
```
with:
```tsx
        children: [
          { index: true, element: <Navigate to="members" replace /> },
          { path: "uploads", element: <Uploads /> },
          { path: "members", element: <Members /> },
          { path: "settings", element: <OrgSettings /> },
```

In `frontend/src/pages/org/OrgLayout.tsx`, replace:
```tsx
          </p>
          <nav className="tabs" aria-label="Organization">
            <NavLink to="members">Members</NavLink>
            <NavLink to="settings">Settings</NavLink>
```
with:
```tsx
          </p>
          <nav className="tabs" aria-label="Organization">
            <NavLink to="uploads">Uploads</NavLink>
            <NavLink to="members">Members</NavLink>
            <NavLink to="settings">Settings</NavLink>
```

`frontend/src/pages/org/UploadPanel.tsx`:
```tsx
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { errorMessage } from "../../api/problem";
import type { Org } from "../../orgs/org";
import { type Progress, UploadProblem, sendUpload } from "../../uploads/sendUpload";
import { uploadsKey } from "../../uploads/uploads";

function problemText(error: unknown): string {
  return error instanceof UploadProblem ? error.message : errorMessage(error);
}

/**
 * Sending a flow log (spec §10): choose a file, then upload it, with its progress shown in place.
 * It sits on the page rather than in a dialog: Plan 6a's pages have no modal dialogs.
 */
export function UploadPanel({ org }: { org: Org }) {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const send = useMutation({
    mutationFn: (chosen: File) => sendUpload(org.id, chosen, setProgress),
    onSettled: () => setProgress(null),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: uploadsKey(org.id) }),
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    if (file !== null) {
      send.mutate(file);
    }
  }
  return (
    <section className="setting" aria-labelledby="upload-title">
      <div className="setting-what">
        <h2 id="upload-title">Upload a flow log</h2>
        <p>
          VPC Flow Logs in the default format, plain or gzipped, up to 25 MB. Analysis takes about a
          minute.
        </p>
      </div>
      <div className="setting-how">
        <form className="inline-form" onSubmit={submit}>
          <div className="field">
            <label htmlFor="upload-file">Flow log file</label>
            <input
              id="upload-file"
              type="file"
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null);
                send.reset();
              }}
            />
          </div>
          <button
            type="submit"
            className="button button-primary"
            disabled={file === null || send.isPending}
          >
            Upload
          </button>
        </form>
        <div role="status" className="upload-status">
          {progress?.phase === "reading" && <p className="muted">Reading the file…</p>}
          {progress?.phase === "sending" && (
            <p className="muted">
              <progress max={100} value={progress.percent} aria-label="Upload progress" /> Sending,{" "}
              {progress.percent}%
            </p>
          )}
          {send.isSuccess && (
            <p>
              Uploaded {send.data.original_filename}. It's being analyzed, and shows as Analyzed in
              about a minute.
            </p>
          )}
        </div>
        {send.isError && (
          <p role="alert" className="notice notice-error">
            {problemText(send.error)}
          </p>
        )}
      </div>
    </section>
  );
}
```

`frontend/src/pages/org/Uploads.tsx`:
```tsx
import { Link } from "react-router";
import { useOrg } from "../../orgs/org";
import { canContribute } from "../../orgs/permissions";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { formatDateTime, formatNumber } from "../../ui/format";
import { Loading } from "../../ui/Loading";
import { usePageTitle } from "../../ui/usePageTitle";
import { type Upload, useUploads } from "../../uploads/uploads";
import { UploadPanel } from "./UploadPanel";

const STATUS_LABELS: Record<Upload["status"], string> = {
  pending_upload: "Waiting for the file",
  processing: "Analyzing",
  analyzed: "Analyzed",
  failed: "Failed",
  expired: "Expired",
};

function UploadRow({ upload }: { upload: Upload }) {
  const rejected = upload.rows_rejected ?? 0;
  return (
    <tr>
      <td className="mono">{upload.original_filename}</td>
      <td>
        <span className="state">{STATUS_LABELS[upload.status]}</span>
        {upload.failure_reason !== null && <div className="muted">{upload.failure_reason}</div>}
        {upload.findings_truncated > 0 && (
          <div className="muted">
            {formatNumber(upload.findings_truncated)} lower-severity findings weren't kept
          </div>
        )}
      </td>
      <td>
        {upload.rows_parsed === null ? "—" : formatNumber(upload.rows_parsed)}
        {rejected > 0 && <div className="muted">{formatNumber(rejected)} rejected</div>}
      </td>
      <td className="date">{formatDateTime(upload.created_at)}</td>
      <td>
        {upload.status === "analyzed" && (
          <Link
            to={`../findings?upload=${upload.id}`}
            aria-label={`Findings from ${upload.original_filename}`}
          >
            Findings
          </Link>
        )}
      </td>
    </tr>
  );
}

/** `/app/orgs/:org/uploads`: the organization's flow logs, and sending a new one (spec §10). */
export function Uploads() {
  const org = useOrg();
  usePageTitle(`Uploads · ${org.name}`);
  const uploads = useUploads(org.id);
  const all = uploads.data?.pages.flatMap((page) => page.uploads) ?? [];
  return (
    <main id="main" className="page">
      <h1>Uploads</h1>
      {canContribute(org.role) && <UploadPanel org={org} />}
      {uploads.isPending && <Loading />}
      {uploads.isError && <ErrorNotice error={uploads.error} />}
      {uploads.isSuccess && all.length === 0 && <p className="muted">No uploads yet.</p>}
      {all.length > 0 && (
        <table className="table">
          <caption className="visually-hidden">Uploads to {org.name}</caption>
          <thead>
            <tr>
              <th scope="col">File</th>
              <th scope="col">Status</th>
              <th scope="col">Rows</th>
              <th scope="col">Uploaded</th>
              <th scope="col">
                <span className="visually-hidden">Findings</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {all.map((upload) => (
              <UploadRow key={upload.id} upload={upload} />
            ))}
          </tbody>
        </table>
      )}
      {uploads.hasNextPage && (
        <button
          type="button"
          className="button more"
          disabled={uploads.isFetchingNextPage}
          onClick={() => void uploads.fetchNextPage()}
        >
          Show older uploads
        </button>
      )}
    </main>
  );
}
```

In `frontend/src/styles.css`, replace:
```css
}

/* An organization: its name and your role, then its tabs */

```
with:
```css
}

/* Lists that load a page at a time */

.more {
  margin-top: 1rem;
}

/* Choosing a file: the browser's picker button, dressed as the app's buttons */

.field input[type="file"] {
  height: auto;
  padding: 0.3rem;
}

.field input[type="file"]::file-selector-button {
  margin-right: 0.75rem;
  padding: 0.35rem 0.9rem;
  border: 1px solid var(--grid);
  border-radius: 6px;
  background: var(--paper);
  color: var(--ink);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}

/* Sending a file: the browser's own progress bar, in ink */

.upload-status progress {
  width: 12rem;
  height: 0.6rem;
  margin-right: 0.5rem;
  vertical-align: middle;
  accent-color: var(--ink);
}

/* An organization: its name and your role, then its tabs */

```

`frontend/src/uploads/sendUpload.ts`:
```ts
/**
 * Sending a flow log (spec §4.2, §7): the browser fingerprints the file, asks the API for a slot,
 * then puts the file straight into S3 with the URL and headers the API signed. S3 accepts only a
 * file of that exact length and SHA-256.
 */
import { api, idempotencyKey, sha256Hex } from "../api/client";
import { unwrap } from "../api/problem";
import type { Upload } from "./uploads";

/** 25 MB, as the API and S3 enforce (spec §5.7). */
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

/** A problem with the file or with storage, said in words the person can act on. */
export class UploadProblem extends Error {
  override name = "UploadProblem";
}

export type Progress = { phase: "reading" } | { phase: "sending"; percent: number };

/** Puts the file with an `XMLHttpRequest`, the one browser API that reports upload progress. */
function putFile(
  url: string,
  headers: Record<string, string>,
  body: ArrayBuffer,
  onProgress: (percent: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("PUT", url);
    for (const [name, value] of Object.entries(headers)) {
      request.setRequestHeader(name, value);
    }
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.round((100 * event.loaded) / event.total));
      }
    };
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) {
        resolve();
      } else {
        reject(
          new UploadProblem(
            `Storage refused the file (status ${request.status}). Upload it again.`,
          ),
        );
      }
    };
    request.onerror = () => {
      reject(
        new UploadProblem(
          "The file didn't reach storage. Check your connection and upload it again.",
        ),
      );
    };
    request.send(body);
  });
}

/**
 * Uploads `file` to the organization and returns its upload, now waiting for analysis. Each call
 * is a new upload with its own idempotency key: a signed URL lasts only 5 minutes, so a replayed
 * answer could no longer be used.
 */
export async function sendUpload(
  orgId: string,
  file: File,
  onProgress: (progress: Progress) => void,
): Promise<Upload> {
  if (file.size === 0) {
    throw new UploadProblem("This file is empty.");
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    throw new UploadProblem("Files can be at most 25 MB.");
  }
  onProgress({ phase: "reading" });
  const bytes = await file.arrayBuffer();
  const created = unwrap(
    await api.POST("/api/v1/orgs/{org_id}/uploads", {
      params: { path: { org_id: orgId } },
      body: { filename: file.name, size_bytes: file.size, sha256: await sha256Hex(bytes) },
      headers: { "Idempotency-Key": idempotencyKey() },
    }),
  );
  onProgress({ phase: "sending", percent: 0 });
  await putFile(created.upload_url, created.upload_headers, bytes, (percent) =>
    onProgress({ phase: "sending", percent }),
  );
  return created.upload;
}
```

`frontend/src/uploads/uploads.ts`:
```ts
/** An organization's uploads (spec §7), newest first, a page at a time. */
import { useInfiniteQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";

export type Upload = components["schemas"]["UploadOut"];

/** A file the API signed for but never received is watched only this long. */
const WAIT_FOR_FILE_MS = 10 * 60_000;
const POLL_MS = 3000;

export function uploadsKey(orgId: string) {
  return ["uploads", orgId] as const;
}

/** Still changing: being analyzed, or waiting a few minutes for its file. */
export function isBusy(upload: Upload, now: number): boolean {
  if (upload.status === "processing") {
    return true;
  }
  return (
    upload.status === "pending_upload" && now - Date.parse(upload.created_at) < WAIT_FOR_FILE_MS
  );
}

/** The uploads, rechecked every few seconds while one of them is still changing. */
export function useUploads(orgId: string) {
  return useInfiniteQuery({
    queryKey: uploadsKey(orgId),
    queryFn: async ({ pageParam }) =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/uploads", {
          params: { path: { org_id: orgId }, query: pageParam ? { cursor: pageParam } : {} },
        }),
      ),
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchInterval: (query) => {
      const now = Date.now();
      const pages = query.state.data?.pages ?? [];
      return pages.some((page) => page.uploads.some((upload) => isBusy(upload, now)))
        ? POLL_MS
        : false;
    },
  });
}
```

- [ ] **Step 4: Run the tests again**

Run: `cd frontend && pnpm exec vitest run src/uploads src/pages/org/Uploads.test.tsx`
Expected: `10 passed`.

- [ ] **Step 5: Check everything**

Run: `cd frontend && pnpm lint && pnpm exec tsc --noEmit && pnpm test`
Expected: lint and types are clean, and `129 passed`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(web): the uploads page: send a flow log with its fingerprint and progress, and follow its analysis" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 4: The findings list, where an organization opens

**Files:**
- Create: `frontend/src/findings/findings.ts`, `frontend/src/pages/org/Findings.tsx`, `frontend/src/pages/org/FindingFilters.tsx`
- Modify: `frontend/src/app/routes.tsx`, `frontend/src/pages/org/OrgLayout.tsx`, `frontend/src/styles.css`, `frontend/src/test/fixtures.ts`, `frontend/src/pages/org/Members.test.tsx`
- Test: `frontend/src/findings/findings.test.ts`, `frontend/src/pages/org/Findings.test.tsx`

**Interfaces:**
- Consumes: Task 1's `sort`; Task 2's vocabulary, `Severity`, `useMembers`, `memberName`, `canContribute` and `formatDateTime`; Task 3's fixtures `UPLOAD_ID` and `upload(fields)`.
- Produces:
  - in `src/findings/findings.ts`: `FindingSummary`, `Sort`, `FindingFilters`, `findingsKey(orgId, filters?)` (without filters, the key every list shares), `filtersFrom(search)`, `searchFrom(filters)` and `useFindings(orgId, filters)`;
  - the route `findings`, where `/app/orgs/:org` now opens, and its tab, first;
  - for tests: the fixtures `FINDING_ID` and `findingSummary(fields)`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/findings/findings.test.ts`:
```ts
import { expect, test } from "vitest";
import { filtersFrom, searchFrom } from "./findings";

test("findings open most severe first, with no filter", () => {
  expect(filtersFrom(new URLSearchParams())).toEqual({ sort: "severity" });
});

test("filters and the order are read from the address", () => {
  const search = new URLSearchParams(
    "severity=high&status=investigating&detector=port_scan&upload=u1&sort=newest",
  );

  expect(filtersFrom(search)).toEqual({
    severity: "high",
    status: "investigating",
    detector: "port_scan",
    upload: "u1",
    sort: "newest",
  });
});

test("a value that isn't real is read as no filter", () => {
  expect(filtersFrom(new URLSearchParams("severity=urgent&status=closed&sort=oldest"))).toEqual({
    sort: "severity",
  });
});

test("the address keeps only what differs from the defaults", () => {
  expect(searchFrom({ sort: "severity" }).toString()).toBe("");
  expect(searchFrom({ severity: "low", sort: "newest" }).toString()).toBe(
    "severity=low&sort=newest",
  );
});
```

`frontend/src/pages/org/Findings.test.tsx`:
```tsx
import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import {
  ADMIN,
  FINDING_ID,
  ORG_ID,
  OWNER,
  UPLOAD_ID,
  VIEWER,
  findingSummary,
  upload,
} from "../../test/fixtures";
import { ORG, signedInAs } from "../../test/orgApi";
import { renderAt } from "../../test/render";

const FINDINGS = `GET ${ORG}/findings`;
const PAGE = `/app/orgs/${ORG_ID}/findings`;
const NONE = { body: { findings: [], next_cursor: null } };

function lastQuery(requests: Request[]): URLSearchParams {
  const reads = requests.filter((request) => new URL(request.url).pathname.endsWith("/findings"));
  return new URL(reads.at(-1)?.url ?? "http://x").searchParams;
}

test("an organization opens on its findings, most severe first", async () => {
  const fake = signedInAs(OWNER, {
    [FINDINGS]: {
      body: {
        findings: [findingSummary({ status: "investigating", assignee_id: ADMIN.user_id })],
        next_cursor: null,
      },
    },
  });

  renderAt(`/app/orgs/${ORG_ID}`);

  expect(await screen.findByRole("heading", { level: 1, name: "Findings" })).toBeInTheDocument();
  const row = await screen.findByRole("row", { name: /Port scan of 10\.0\.0\.5/ });
  expect(within(row).getByText("High")).toBeInTheDocument();
  expect(
    within(row).getByRole("link", {
      name: "Port scan of 10.0.0.5 from 10.0.3.17: 150 TCP ports in 5 minutes",
    }),
  ).toHaveAttribute("href", `${PAGE}/${FINDING_ID}`);
  expect(within(row).getByText("Investigating")).toBeInTheDocument();
  expect(await within(row).findByText("Ben Admin")).toBeInTheDocument();
  expect(lastQuery(fake.requests).get("sort")).toBe("severity");
  expect(screen.getByRole("link", { name: "Findings" })).toHaveAttribute("aria-current", "page");
  expect(document.title).toBe("Findings · Acme Security · NetTriage");
});

test("filters and the order are kept in the address and sent to the API", async () => {
  const fake = signedInAs(OWNER, {
    [FINDINGS]: { body: { findings: [findingSummary()], next_cursor: null } },
  });
  const { user, router } = renderAt(PAGE);

  await user.selectOptions(await screen.findByLabelText("Severity"), "High");
  await user.selectOptions(screen.getByLabelText("Status"), "Investigating");
  await user.selectOptions(screen.getByLabelText("Detector"), "SSH/RDP brute force");
  await user.selectOptions(screen.getByLabelText("Sort"), "Newest first");

  await vi.waitFor(() => expect(lastQuery(fake.requests).get("sort")).toBe("newest"));
  const sent = lastQuery(fake.requests);
  expect([sent.get("severity"), sent.get("status"), sent.get("detector")]).toEqual([
    "high",
    "investigating",
    "remote_access_bruteforce",
  ]);
  expect(router.state.location.search).toBe(
    "?severity=high&status=investigating&detector=remote_access_bruteforce&sort=newest",
  );
});

test("an address with values that aren't real is read as no filter", async () => {
  const fake = signedInAs(OWNER, { [FINDINGS]: NONE });

  renderAt(`${PAGE}?severity=urgent&sort=oldest`);

  await screen.findByRole("heading", { level: 1, name: "Findings" });
  await vi.waitFor(() => expect(lastQuery(fake.requests).get("sort")).toBe("severity"));
  expect(lastQuery(fake.requests).has("severity")).toBe(false);
  expect(screen.getByLabelText("Severity")).toHaveValue("");
});

test("findings from one upload name it, with a way back to every upload's findings", async () => {
  const fake = signedInAs(OWNER, {
    [FINDINGS]: { body: { findings: [findingSummary()], next_cursor: null } },
    [`GET ${ORG}/uploads/${UPLOAD_ID}`]: { body: upload() },
  });
  const { user, router } = renderAt(`${PAGE}?upload=${UPLOAD_ID}`);

  expect((await screen.findByText("port-scan.log")).closest("p")).toHaveTextContent(
    "Findings from port-scan.log.",
  );
  expect(lastQuery(fake.requests).get("upload")).toBe(UPLOAD_ID);
  await user.click(screen.getByRole("link", { name: "Show every upload's findings" }));

  expect(router.state.location.search).toBe("");
});

test("with no findings yet, a contributor is pointed to uploading", async () => {
  signedInAs(OWNER, { [FINDINGS]: NONE });

  renderAt(PAGE);

  expect(await screen.findByText("No findings yet.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Upload a flow log" })).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}/uploads`,
  );
});

test("with no findings yet, a viewer is told where they come from", async () => {
  signedInAs(VIEWER, { [FINDINGS]: NONE });

  renderAt(PAGE);

  expect(
    await screen.findByText("Findings appear here once a flow log has been analyzed."),
  ).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Upload a flow log" })).toBeNull();
});

test("no finding matching the filters offers to clear them", async () => {
  signedInAs(OWNER, { [FINDINGS]: NONE });
  const { user, router } = renderAt(`${PAGE}?severity=low&sort=newest`);

  expect(await screen.findByText("No findings match these filters.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Clear filters" }));

  expect(router.state.location.search).toBe("?sort=newest");
});

test("more findings load a page at a time", async () => {
  const older = findingSummary({ id: "01a10600-0000-7000-8000-000000000002", title: "Older scan" });
  signedInAs(OWNER, {
    [FINDINGS]: (request) =>
      new URL(request.url).searchParams.get("cursor") === "c2"
        ? { body: { findings: [older], next_cursor: null } }
        : { body: { findings: [findingSummary()], next_cursor: "c2" } },
  });
  const { user } = renderAt(PAGE);

  await user.click(await screen.findByRole("button", { name: "Show more findings" }));

  expect(await screen.findByRole("link", { name: "Older scan" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Show more findings" })).toBeNull();
});

test("a list the API refuses says why", async () => {
  signedInAs(OWNER, {
    [FINDINGS]: {
      status: 503,
      body: { title: "Service Unavailable", detail: "Try again.", trace_id: "t7" },
    },
  });

  renderAt(PAGE);

  expect(await screen.findByRole("alert")).toHaveTextContent("Try again. (reference t7)");
});
```

In `frontend/src/pages/org/Members.test.tsx`, replace:
```tsx
}

test("an organization opens on its members, under its name and the person's role", async () => {
  signedInAs(OWNER);

  renderAt(`/app/orgs/${ORG_ID}`);

  expect(await screen.findByRole("heading", { level: 1, name: "Members" })).toBeInTheDocument();
```
with:
```tsx
}

test("an organization's members sit under its name and the person's role", async () => {
  signedInAs(OWNER);

  renderAt(`/app/orgs/${ORG_ID}/members`);

  expect(await screen.findByRole("heading", { level: 1, name: "Members" })).toBeInTheDocument();
```

In `frontend/src/test/fixtures.ts`, replace:
```ts
import type { Org } from "../orgs/org";
import type { Member } from "../orgs/members";
import type { Upload } from "../uploads/uploads";
import type { Reply } from "./fakeApi";
```
with:
```ts
import type { Org } from "../orgs/org";
import type { Member } from "../orgs/members";
import type { FindingSummary } from "../findings/findings";
import type { Upload } from "../uploads/uploads";
import type { Reply } from "./fakeApi";
```

In `frontend/src/test/fixtures.ts`, replace:
```ts
    ...fields,
  };
}
```
with:
```ts
    ...fields,
  };
}

export const FINDING_ID = "01a10600-0000-7000-8000-000000000001";

export function findingSummary(fields: Partial<FindingSummary> = {}): FindingSummary {
  return {
    id: FINDING_ID,
    upload_id: UPLOAD_ID,
    detector_id: "port_scan",
    detector_version: 1,
    severity: "high",
    status: "open",
    title: "Port scan of 10.0.0.5 from 10.0.3.17: 150 TCP ports in 5 minutes",
    src_ip: "10.0.3.17",
    dst_ip: "10.0.0.5",
    dst_port: null,
    protocol: 6,
    window_start: "2026-10-01T12:00:00Z",
    window_end: "2026-10-01T12:05:00Z",
    assignee_id: null,
    version: 1,
    created_at: "2026-10-04T09:31:00Z",
    ...fields,
  };
}
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && pnpm exec vitest run src/findings/findings.test.ts src/pages/org/Findings.test.tsx src/pages/org/Members.test.tsx`
Expected: FAIL. `findings.test.ts` stops on `Failed to resolve import "./findings"`, and the 9 findings page tests fail, such as `Unable to find role="heading" and name "Findings"`. The members tests pass.

- [ ] **Step 3: Write the findings data, the filters and the page**

In `frontend/src/app/routes.tsx`, replace:
```tsx
import { Landing } from "../pages/Landing";
import { NotFound } from "../pages/NotFound";
import { Members } from "../pages/org/Members";
import { OrgLayout } from "../pages/org/OrgLayout";
```
with:
```tsx
import { Landing } from "../pages/Landing";
import { NotFound } from "../pages/NotFound";
import { Findings } from "../pages/org/Findings";
import { Members } from "../pages/org/Members";
import { OrgLayout } from "../pages/org/OrgLayout";
```

In `frontend/src/app/routes.tsx`, replace:
```tsx
        element: <OrgLayout />,
        children: [
          { index: true, element: <Navigate to="members" replace /> },
          { path: "uploads", element: <Uploads /> },
          { path: "members", element: <Members /> },
```
with:
```tsx
        element: <OrgLayout />,
        children: [
          { index: true, element: <Navigate to="findings" replace /> },
          { path: "findings", element: <Findings /> },
          { path: "uploads", element: <Uploads /> },
          { path: "members", element: <Members /> },
```

`frontend/src/findings/findings.ts`:
```ts
/** An organization's findings (spec §7, §10): filtered and sorted by the API, a page at a time. */
import { useInfiniteQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";
import { DETECTORS, type Severity, type Status, isSeverity, isStatus } from "./vocabulary";

export type FindingSummary = components["schemas"]["FindingSummaryOut"];
export type Sort = "severity" | "newest";

export interface FindingFilters {
  severity?: Severity;
  status?: Status;
  detector?: string;
  upload?: string;
  /** Most severe first unless the person picks newest (the owner's decision, Plan 6b). */
  sort: Sort;
}

export function findingsKey(orgId: string, filters?: FindingFilters) {
  return filters === undefined
    ? (["findings", orgId] as const)
    : (["findings", orgId, filters] as const);
}

/** The filters in an address such as `?severity=high&sort=newest`; anything unknown is no filter. */
export function filtersFrom(search: URLSearchParams): FindingFilters {
  const filters: FindingFilters = { sort: search.get("sort") === "newest" ? "newest" : "severity" };
  const severity = search.get("severity");
  const status = search.get("status");
  const detector = search.get("detector");
  const upload = search.get("upload");
  if (isSeverity(severity)) {
    filters.severity = severity;
  }
  if (isStatus(status)) {
    filters.status = status;
  }
  if (detector !== null && detector in DETECTORS) {
    filters.detector = detector;
  }
  if (upload !== null && upload !== "") {
    filters.upload = upload;
  }
  return filters;
}

/** The address for these filters, keeping only what differs from the defaults. */
export function searchFrom(filters: FindingFilters): URLSearchParams {
  const search = new URLSearchParams();
  for (const name of ["severity", "status", "detector", "upload"] as const) {
    const value = filters[name];
    if (value !== undefined) {
      search.set(name, value);
    }
  }
  if (filters.sort === "newest") {
    search.set("sort", "newest");
  }
  return search;
}

export function useFindings(orgId: string, filters: FindingFilters) {
  return useInfiniteQuery({
    queryKey: findingsKey(orgId, filters),
    queryFn: async ({ pageParam }) =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/findings", {
          params: {
            path: { org_id: orgId },
            query: { ...filters, ...(pageParam ? { cursor: pageParam } : {}) },
          },
        }),
      ),
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}
```

`frontend/src/pages/org/FindingFilters.tsx`:
```tsx
import type { FindingFilters as Filters } from "../../findings/findings";
import {
  DETECTORS,
  SEVERITIES,
  STATUSES,
  isSeverity,
  isStatus,
  severityLabel,
  statusLabel,
} from "../../findings/vocabulary";

/**
 * The findings list's filters and order (spec §10). Each choice applies at once: it narrows the
 * list on the page and changes nothing else.
 */
export function FindingFilters({
  filters,
  onChange,
}: {
  filters: Filters;
  onChange: (filters: Filters) => void;
}) {
  function set(name: "severity" | "status" | "detector", value: string) {
    const next: Filters = { ...filters };
    if (name === "severity") {
      next.severity = isSeverity(value) ? value : undefined;
    } else if (name === "status") {
      next.status = isStatus(value) ? value : undefined;
    } else {
      next.detector = value === "" ? undefined : value;
    }
    onChange(next);
  }
  return (
    <div className="filters" role="group" aria-label="Filter and sort findings">
      <div className="field">
        <label htmlFor="filter-severity">Severity</label>
        <select
          id="filter-severity"
          value={filters.severity ?? ""}
          onChange={(event) => set("severity", event.target.value)}
        >
          <option value="">Any severity</option>
          {SEVERITIES.map((severity) => (
            <option key={severity} value={severity}>
              {severityLabel(severity)}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="filter-status">Status</label>
        <select
          id="filter-status"
          value={filters.status ?? ""}
          onChange={(event) => set("status", event.target.value)}
        >
          <option value="">Any status</option>
          {STATUSES.map((status) => (
            <option key={status} value={status}>
              {statusLabel(status)}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="filter-detector">Detector</label>
        <select
          id="filter-detector"
          value={filters.detector ?? ""}
          onChange={(event) => set("detector", event.target.value)}
        >
          <option value="">Any detector</option>
          {Object.entries(DETECTORS).map(([id, name]) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="filter-sort">Sort</label>
        <select
          id="filter-sort"
          value={filters.sort}
          onChange={(event) =>
            onChange({ ...filters, sort: event.target.value === "newest" ? "newest" : "severity" })
          }
        >
          <option value="severity">Most severe first</option>
          <option value="newest">Newest first</option>
        </select>
      </div>
    </div>
  );
}
```

`frontend/src/pages/org/Findings.tsx`:
```tsx
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { api } from "../../api/client";
import { unwrap } from "../../api/problem";
import {
  type FindingFilters as Filters,
  type FindingSummary,
  filtersFrom,
  searchFrom,
  useFindings,
} from "../../findings/findings";
import { detectorName, statusLabel } from "../../findings/vocabulary";
import { type Member, memberName, useMembers } from "../../orgs/members";
import { useOrg } from "../../orgs/org";
import { canContribute } from "../../orgs/permissions";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { formatDateTime } from "../../ui/format";
import { Loading } from "../../ui/Loading";
import { Severity } from "../../ui/Severity";
import { usePageTitle } from "../../ui/usePageTitle";
import { FindingFilters } from "./FindingFilters";

/** Which upload the list is narrowed to, by its file name, and the way back to all of them. */
function UploadScope({ orgId, filters }: { orgId: string; filters: Filters & { upload: string } }) {
  const upload = useQuery({
    queryKey: ["upload", orgId, filters.upload],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/uploads/{upload_id}", {
          params: { path: { org_id: orgId, upload_id: filters.upload } },
        }),
      ),
  });
  const everyUpload = searchFrom({ ...filters, upload: undefined }).toString();
  return (
    <p className="scope">
      Findings from <span className="mono">{upload.data?.original_filename ?? "one upload"}</span>.{" "}
      <Link to={{ search: everyUpload }}>Show every upload's findings</Link>
    </p>
  );
}

function FindingRow({ finding, members }: { finding: FindingSummary; members?: Member[] }) {
  return (
    <tr>
      <td>
        <Severity severity={finding.severity} />
      </td>
      <td>
        <Link to={finding.id}>{finding.title}</Link>
        <div className="muted">{detectorName(finding.detector_id)}</div>
      </td>
      <td>
        <span className="state">{statusLabel(finding.status)}</span>
      </td>
      <td className="nowrap">
        {finding.assignee_id === null ? (
          <span className="muted">Unassigned</span>
        ) : (
          memberName(members, finding.assignee_id)
        )}
      </td>
      <td className="date">{formatDateTime(finding.window_start)}</td>
    </tr>
  );
}

function NoFindings({
  filtered,
  contributor,
  onClear,
}: {
  filtered: boolean;
  contributor: boolean;
  onClear: () => void;
}) {
  if (filtered) {
    return (
      <div className="empty">
        <p>No findings match these filters.</p>
        <button type="button" className="button" onClick={onClear}>
          Clear filters
        </button>
      </div>
    );
  }
  return (
    <div className="empty">
      <p>No findings yet.</p>
      {contributor ? (
        <p>
          <Link to="../uploads">Upload a flow log</Link> and NetTriage looks for scans, brute force
          and unusual outbound volume in it.
        </p>
      ) : (
        <p className="muted">Findings appear here once a flow log has been analyzed.</p>
      )}
    </div>
  );
}

/** `/app/orgs/:org/findings`, where an organization opens: its findings, worst first (spec §10). */
export function Findings() {
  const org = useOrg();
  usePageTitle(`Findings · ${org.name}`);
  const [search, setSearch] = useSearchParams();
  const filters = filtersFrom(search);
  const findings = useFindings(org.id, filters);
  const members = useMembers(org.id);
  const all = findings.data?.pages.flatMap((page) => page.findings) ?? [];
  const change = (next: Filters) => setSearch(searchFrom(next));
  const filtered = [filters.severity, filters.status, filters.detector, filters.upload].some(
    (value) => value !== undefined,
  );
  return (
    <main id="main" className="page">
      <h1>Findings</h1>
      <FindingFilters filters={filters} onChange={change} />
      {filters.upload !== undefined && (
        <UploadScope orgId={org.id} filters={{ ...filters, upload: filters.upload }} />
      )}
      {findings.isPending && <Loading />}
      {findings.isError && <ErrorNotice error={findings.error} />}
      {findings.isSuccess && all.length === 0 && (
        <NoFindings
          filtered={filtered}
          contributor={canContribute(org.role)}
          onClear={() => change({ sort: filters.sort })}
        />
      )}
      {all.length > 0 && (
        <table className="table">
          <caption className="visually-hidden">Findings in {org.name}</caption>
          <thead>
            <tr>
              <th scope="col">Severity</th>
              <th scope="col">Finding</th>
              <th scope="col">Status</th>
              <th scope="col">Assignee</th>
              <th scope="col">When</th>
            </tr>
          </thead>
          <tbody>
            {all.map((finding) => (
              <FindingRow key={finding.id} finding={finding} members={members.data} />
            ))}
          </tbody>
        </table>
      )}
      {findings.hasNextPage && (
        <button
          type="button"
          className="button more"
          disabled={findings.isFetchingNextPage}
          onClick={() => void findings.fetchNextPage()}
        >
          Show more findings
        </button>
      )}
    </main>
  );
}
```

In `frontend/src/pages/org/OrgLayout.tsx`, replace:
```tsx
          </p>
          <nav className="tabs" aria-label="Organization">
            <NavLink to="uploads">Uploads</NavLink>
            <NavLink to="members">Members</NavLink>
```
with:
```tsx
          </p>
          <nav className="tabs" aria-label="Organization">
            <NavLink to="findings">Findings</NavLink>
            <NavLink to="uploads">Uploads</NavLink>
            <NavLink to="members">Members</NavLink>
```

In `frontend/src/styles.css`, replace:
```css
.muted {
  color: var(--graphite);
}

```
with:
```css
.muted {
  color: var(--graphite);
}

.nowrap {
  white-space: nowrap;
}

```

In `frontend/src/styles.css`, replace:
```css
}

/* Lists that load a page at a time */

.more {
  margin-top: 1rem;
}

```
with:
```css
}

/* Lists that load a page at a time, narrowed by filters above them */

.more {
  margin-top: 1rem;
}

.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem 1rem;
  margin-bottom: 1.25rem;
}

.filters .field {
  margin-bottom: 0;
}

.filters .field select {
  min-width: 11rem;
}

.scope {
  margin: 0 0 1rem;
  color: var(--graphite);
}

.empty {
  padding: 2rem 0;
  border-top: 1px solid var(--grid);
}

.empty > p:first-child {
  margin-top: 0;
  font-weight: 650;
}

```

- [ ] **Step 4: Run the tests again**

Run: `cd frontend && pnpm exec vitest run src/findings/findings.test.ts src/pages/org/Findings.test.tsx src/pages/org/Members.test.tsx`
Expected: `30 passed`.

- [ ] **Step 5: Check everything**

Run: `cd frontend && pnpm lint && pnpm exec tsc --noEmit && pnpm test`
Expected: lint and types are clean, and `142 passed`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(web): the findings list, where an organization opens: worst first, filtered by severity, status, detector and upload" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 5: A finding's page: details, evidence and techniques

**Files:**
- Create: `frontend/src/findings/finding.ts`, `frontend/src/findings/metrics.ts`, and in `frontend/src/pages/org/finding/`: `FindingPage.tsx`, `FindingFacts.tsx`, `EvidenceMap.tsx`, `EvidenceTable.tsx`, `Techniques.tsx`
- Modify: `frontend/src/ui/PortMap.tsx` (a `tallyLabel`), `frontend/src/ui/format.ts`, `frontend/src/app/routes.tsx`, `frontend/src/styles.css`, `frontend/src/test/fixtures.ts`
- Test: `frontend/src/findings/metrics.test.ts`, `frontend/src/pages/org/finding/FindingPage.test.tsx`, `frontend/src/ui/PortMap.test.tsx`, `frontend/src/ui/format.test.ts`

**Interfaces:**
- Consumes: Task 2's vocabulary, `Severity` and `formatNumber`; Task 4's fixtures; 6a's `PortMap`, `ExternalLink`, `ApiError` and `useOrg`.
- Produces:
  - in `src/findings/finding.ts`: `Finding`, `Evidence`, `Technique`, `findingKey(orgId, findingId)` and `useFinding(orgId, findingId)`;
  - `metricRows(metrics) -> { label, value }[]` in `src/findings/metrics.ts`;
  - `formatTime(iso)` and `formatClock(iso)`;
  - `PortMap`'s optional `tallyLabel` (default `"probed"`);
  - `FindingPage`, with its two columns `.finding-main` and `.finding-side`, which Tasks 6 and 7 fill; the route `findings/:findingId`;
  - for tests: the fixtures `SCAN_METRICS` and `finding(fields)`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/findings/metrics.test.ts`:
```ts
import { expect, test } from "vitest";
import { SCAN_METRICS } from "../test/fixtures";
import { metricRows } from "./metrics";

test("a port scan's numbers read as words, with yes or no for what's true or false", () => {
  expect(metricRows(SCAN_METRICS)).toEqual([
    { label: "Pattern", value: "Many ports on one host" },
    { label: "Peak in any 5 minutes", value: "150" },
    { label: "Distinct in the window", value: "150" },
    { label: "Flows", value: "150" },
    { label: "Rejected flows", value: "150" },
    { label: "Rejected or tiny flows", value: "100%" },
    { label: "Source inside the network", value: "Yes" },
  ]);
});

test("outbound volume is shown in megabytes, not bytes", () => {
  expect(metricRows({ bytes: 524_288_000, megabytes: 500, dominant_port: 443 })).toEqual([
    { label: "Sent", value: "500 MB" },
    { label: "Main port", value: "443" },
  ]);
});

test("a number a newer detector adds is still shown, named from its key", () => {
  expect(metricRows({ beacon_interval: 60, nested: { a: 1 } })).toEqual([
    { label: "Beacon interval", value: "60" },
  ]);
});
```

`frontend/src/pages/org/finding/FindingPage.test.tsx`:
```tsx
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { FINDING_ID, ORG_ID, OWNER, finding } from "../../../test/fixtures";
import { ORG, signedInAs } from "../../../test/orgApi";
import { renderAt } from "../../../test/render";

const FINDING = `${ORG}/findings/${FINDING_ID}`;
const PAGE = `/app/orgs/${ORG_ID}/findings/${FINDING_ID}`;
const NOTICE =
  "Copyright 2015-2026, The MITRE Corporation. MITRE ATT&CK and ATT&CK are registered trademarks of The MITRE Corporation.";

function technique(id: string) {
  return {
    body: {
      id,
      name: "Network Service Discovery",
      tactics: ["discovery"],
      description: "",
      url: `https://attack.mitre.org/techniques/${id}/`,
      is_subtechnique: false,
      parent_id: null,
      deprecated: false,
      attack_version: "17.1",
      notice: NOTICE,
      license: "https://github.com/mitre/cti/blob/master/LICENSE.txt",
    },
  };
}

test("a finding shows its severity, title, details and numbers", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding() },
    "GET /api/v1/attack-techniques/T1046": technique("T1046"),
  });

  renderAt(PAGE);

  expect(
    await screen.findByRole("heading", {
      level: 1,
      name: "Port scan of 10.0.0.5 from 10.0.3.17: 150 TCP ports in 5 minutes",
    }),
  ).toBeInTheDocument();
  expect(screen.getByText("High")).toBeInTheDocument();
  const details = screen.getByRole("region", { name: "Details" });
  const fact = (name: string) => within(details).getByText(name).nextElementSibling;
  expect(fact("Source")).toHaveTextContent("10.0.3.17");
  expect(fact("Destination")).toHaveTextContent("10.0.0.5");
  expect(fact("Port")).toHaveTextContent("Several");
  expect(fact("Protocol")).toHaveTextContent("TCP");
  expect(fact("Detector")).toHaveTextContent("Port scan, version 1");
  expect(fact("Peak in any 5 minutes")).toHaveTextContent("150");
  expect(fact("Source inside the network")).toHaveTextContent("Yes");
  expect(
    within(details).getByRole("link", { name: "Other findings from this upload" }),
  ).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}/findings?upload=01a10500-0000-7000-8000-000000000001`,
  );
  expect(screen.getByRole("link", { name: "All findings" })).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}/findings`,
  );
  expect(document.title).toBe("Port scan from 10.0.3.17 · Acme Security · NetTriage");
});

test("a port scan's evidence is mapped as the sample it is, and listed flow by flow", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding() },
    "GET /api/v1/attack-techniques/T1046": technique("T1046"),
  });

  renderAt(PAGE);

  const map = await screen.findByRole("figure", {
    name: "Ports 0 to 1023 of 10.0.0.5. The scan probed 150 ports; the evidence keeps a sample.",
  });
  const lit = [...map.querySelectorAll("rect.cell.lit")].map((cell) =>
    cell.getAttribute("data-port"),
  );
  expect(lit).toEqual(["22", "80", "443"]);
  expect(within(map).getByText(/in the sample/)).toHaveTextContent("3 in the sample");
  const evidence = screen.getByRole("table", { name: /Evidence/ });
  const first = within(evidence).getAllByRole("row")[1] as HTMLElement;
  expect(within(first).getByText("10.0.3.17:40000")).toBeInTheDocument();
  expect(within(first).getByText("10.0.0.5:22")).toBeInTheDocument();
  expect(within(first).getByText("REJECT")).toBeInTheDocument();
});

test("findings other than a port scan of one host have no port map", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: {
      body: finding({
        detector_id: "remote_access_bruteforce",
        title: "SSH brute force against 10.0.0.5 from 203.0.113.9",
        metrics: { variant: "single", attempts: 400, hosts: 1, possible_success: false },
        techniques: [],
      }),
    },
  });

  renderAt(PAGE);

  expect(await screen.findByRole("table", { name: /Evidence/ })).toBeInTheDocument();
  expect(screen.queryByRole("figure")).toBeNull();
  expect(screen.getByText("No ATT&CK technique is linked to this finding.")).toBeInTheDocument();
});

test("ATT&CK techniques link to MITRE, say who linked them, and carry MITRE's notice", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: {
      body: finding({
        techniques: [
          {
            id: "T1046",
            name: "Network Service Discovery",
            url: "https://attack.mitre.org/techniques/T1046/",
            source: "detector",
            rationale: null,
          },
          {
            id: "T1595.001",
            name: "Scanning IP Blocks",
            url: "https://attack.mitre.org/techniques/T1595/001/",
            source: "ai",
            rationale: "One source tried many ports in minutes.",
          },
        ],
      }),
    },
    "GET /api/v1/attack-techniques/T1046": technique("T1046"),
  });

  renderAt(PAGE);

  const techniques = await screen.findByRole("region", { name: "ATT&CK techniques" });
  const link = within(techniques).getByRole("link", { name: "T1046 Network Service Discovery" });
  expect(link).toHaveAttribute("href", "https://attack.mitre.org/techniques/T1046/");
  expect(link).toHaveAttribute("rel", "noopener noreferrer");
  expect(within(techniques).getByText("From the detector")).toBeInTheDocument();
  expect(within(techniques).getByText("Suggested by the AI")).toBeInTheDocument();
  expect(within(techniques).getByText("One source tried many ports in minutes.")).toBeVisible();
  expect(await within(techniques).findByText(NOTICE)).toBeInTheDocument();
});

test("a technique both the detector and the AI name is listed once, with both", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: {
      body: finding({
        techniques: [
          {
            id: "T1046",
            name: "Network Service Discovery",
            url: "https://attack.mitre.org/techniques/T1046/",
            source: "ai",
            rationale: "One internal host tried 150 ports on another.",
          },
          {
            id: "T1046",
            name: "Network Service Discovery",
            url: "https://attack.mitre.org/techniques/T1046/",
            source: "detector",
            rationale: null,
          },
        ],
      }),
    },
    "GET /api/v1/attack-techniques/T1046": technique("T1046"),
  });

  renderAt(PAGE);

  const techniques = await screen.findByRole("region", { name: "ATT&CK techniques" });
  expect(
    within(techniques).getAllByRole("link", { name: "T1046 Network Service Discovery" }),
  ).toHaveLength(1);
  expect(within(techniques).getByText("From the detector and the AI")).toBeInTheDocument();
  expect(
    within(techniques).getByText("One internal host tried 150 ports on another."),
  ).toBeVisible();
});

test("a finding that isn't there, or isn't the organization's, says so", async () => {
  signedInAs(OWNER, { [`GET ${FINDING}`]: { status: 404, body: { title: "Not Found" } } });

  renderAt(PAGE);

  expect(await screen.findByRole("heading", { name: "Finding not found" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "All findings" })).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}/findings`,
  );
});
```

In `frontend/src/test/fixtures.ts`, replace:
```ts
import type { Org } from "../orgs/org";
import type { Member } from "../orgs/members";
import type { FindingSummary } from "../findings/findings";
import type { Upload } from "../uploads/uploads";
```
with:
```ts
import type { Org } from "../orgs/org";
import type { Member } from "../orgs/members";
import type { Finding } from "../findings/finding";
import type { FindingSummary } from "../findings/findings";
import type { Upload } from "../uploads/uploads";
```

In `frontend/src/test/fixtures.ts`, replace:
```ts
    created_at: "2026-10-04T09:31:00Z",
    ...fields,
  };
}
```
with:
```ts
    created_at: "2026-10-04T09:31:00Z",
    ...fields,
  };
}

export const SCAN_METRICS = {
  variant: "vertical",
  peak_distinct: 150,
  distinct_total: 150,
  flows: 150,
  rejected: 150,
  scan_like_percent: 100,
  source_internal: true,
};

export function finding(fields: Partial<Finding> = {}): Finding {
  return {
    ...findingSummary(),
    metrics: SCAN_METRICS,
    evidence: [22, 80, 443].map((port, index) => ({
      src_ip: "10.0.3.17",
      dst_ip: "10.0.0.5",
      src_port: 40000 + index,
      dst_port: port,
      protocol: 6,
      packets: 1,
      bytes: 40,
      start: `2026-10-01T12:00:0${index}Z`,
      end: `2026-10-01T12:00:0${index}Z`,
      action: "REJECT",
      line_no: index + 1,
    })),
    techniques: [
      {
        id: "T1046",
        name: "Network Service Discovery",
        url: "https://attack.mitre.org/techniques/T1046/",
        source: "detector",
        rationale: null,
      },
    ],
    events: [
      {
        id: "01a10700-0000-7000-8000-000000000001",
        type: "created",
        actor_id: null,
        payload: {},
        created_at: "2026-10-04T09:31:00Z",
      },
    ],
    events_total: 1,
    ai_analysis: null,
    ...fields,
  };
}
```

In `frontend/src/ui/PortMap.test.tsx`, replace:
```tsx
  expect(litPorts(container)).toEqual([22, 80]);
  expect(tally(container)).toBe("2");
});

```
with:
```tsx
  expect(litPorts(container)).toEqual([22, 80]);
  expect(tally(container)).toBe("2");
});

test("its count can say what it counts", () => {
  const { container } = render(
    <PortMap ports={[22, 80, 443]} caption={CAPTION} tallyLabel="in the sample" />,
  );

  expect(container.querySelector("figcaption")).toHaveTextContent("3 in the sample");
});

```

In `frontend/src/ui/format.test.ts`, replace:
```ts
import { expect, test } from "vitest";
import { formatDateTime, formatUsd } from "./format";

test("a moment is written with its date and its time", () => {
```
with:
```ts
import { expect, test } from "vitest";
import { formatClock, formatDateTime, formatTime, formatUsd } from "./format";

test("a moment is written with its date and its time", () => {
```

In `frontend/src/ui/format.test.ts`, replace:
```ts
  expect(formatUsd("0.00004")).toBe("under $0.0001");
});
```
with:
```ts
  expect(formatUsd("0.00004")).toBe("under $0.0001");
});

test("times of day are written to the minute, or to the second for flows", () => {
  expect(formatTime("2026-10-03T14:05:09Z")).toMatch(/:05/);
  expect(formatTime("2026-10-03T14:05:09Z")).not.toMatch(/:09/);
  expect(formatClock("2026-10-03T14:05:09Z")).toMatch(/:05:09/);
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && pnpm exec vitest run src/findings/metrics.test.ts src/pages/org/finding/ src/ui/PortMap.test.tsx src/ui/format.test.ts`
Expected: FAIL. `metrics.test.ts` stops on `Failed to resolve import "./metrics"`; the 6 page tests fail, as there's no finding page; the port map's count can't be named yet; and `formatTime` isn't a function.

- [ ] **Step 3: Write the finding's data and its page**

In `frontend/src/app/routes.tsx`, replace:
```tsx
import { Landing } from "../pages/Landing";
import { NotFound } from "../pages/NotFound";
import { Findings } from "../pages/org/Findings";
import { Members } from "../pages/org/Members";
```
with:
```tsx
import { Landing } from "../pages/Landing";
import { NotFound } from "../pages/NotFound";
import { FindingPage } from "../pages/org/finding/FindingPage";
import { Findings } from "../pages/org/Findings";
import { Members } from "../pages/org/Members";
```

In `frontend/src/app/routes.tsx`, replace:
```tsx
          { index: true, element: <Navigate to="findings" replace /> },
          { path: "findings", element: <Findings /> },
          { path: "uploads", element: <Uploads /> },
          { path: "members", element: <Members /> },
```
with:
```tsx
          { index: true, element: <Navigate to="findings" replace /> },
          { path: "findings", element: <Findings /> },
          { path: "findings/:findingId", element: <FindingPage /> },
          { path: "uploads", element: <Uploads /> },
          { path: "members", element: <Members /> },
```

`frontend/src/findings/finding.ts`:
```ts
/** One finding in full (spec §7): evidence, techniques, history and its latest AI analysis. */
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";

export type Finding = components["schemas"]["FindingOut"];
export type Evidence = components["schemas"]["EvidenceOut"];
export type Technique = components["schemas"]["FindingTechniqueOut"];

export function findingKey(orgId: string, findingId: string) {
  return ["finding", orgId, findingId] as const;
}

export function useFinding(orgId: string, findingId: string) {
  return useQuery({
    queryKey: findingKey(orgId, findingId),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/findings/{finding_id}", {
          params: { path: { org_id: orgId, finding_id: findingId } },
        }),
      ),
  });
}
```

`frontend/src/findings/metrics.ts`:
```ts
/**
 * A finding's numbers (spec §8.2), as words. Each detector stores its own; a key this app doesn't
 * know yet is still shown, named from the key.
 */
import { formatNumber } from "../ui/format";

export interface MetricRow {
  label: string;
  value: string;
}

const LABELS: Record<string, string> = {
  variant: "Pattern",
  peak_distinct: "Peak in any 5 minutes",
  distinct_total: "Distinct in the window",
  flows: "Flows",
  rejected: "Rejected flows",
  scan_like_percent: "Rejected or tiny flows",
  source_internal: "Source inside the network",
  attempts: "Attempts",
  hosts: "Hosts tried",
  possible_success: "A login may have succeeded",
  megabytes: "Sent",
  dominant_port: "Main port",
  internal_hosts: "Internal hosts sending",
  robust_z_applied: "Compared with this network's usual volume",
};

const PATTERNS: Record<string, string> = {
  vertical: "Many ports on one host",
  horizontal: "One port on many hosts",
  single: "Many attempts on one host",
  spray: "A few attempts on many hosts",
};

/** Shown another way (bytes as megabytes), so left out. */
const HIDDEN = new Set(["bytes"]);

function label(key: string): string {
  const words = key.replaceAll("_", " ");
  return LABELS[key] ?? words.charAt(0).toUpperCase() + words.slice(1);
}

function value(key: string, raw: unknown): string | null {
  if (typeof raw === "boolean") {
    return raw ? "Yes" : "No";
  }
  if (typeof raw === "number") {
    if (key === "scan_like_percent") {
      return `${formatNumber(raw)}%`;
    }
    return key === "megabytes" ? `${formatNumber(raw)} MB` : formatNumber(raw);
  }
  if (typeof raw === "string") {
    return key === "variant" ? (PATTERNS[raw] ?? raw) : raw;
  }
  return null;
}

export function metricRows(metrics: Record<string, unknown>): MetricRow[] {
  const rows: MetricRow[] = [];
  for (const [key, raw] of Object.entries(metrics)) {
    const shown = HIDDEN.has(key) ? null : value(key, raw);
    if (shown !== null) {
      rows.push({ label: label(key), value: shown });
    }
  }
  return rows;
}
```

`frontend/src/pages/org/finding/EvidenceMap.tsx`:
```tsx
import type { Finding } from "../../../findings/finding";
import { formatNumber } from "../../../ui/format";
import { PortMap } from "../../../ui/PortMap";

/**
 * A port scan of one host, drawn on the port map (the owner's decision, Plan 6b). It shows the
 * ports in the stored evidence, which keeps at most 50 flows, and says so.
 */
export function EvidenceMap({ finding }: { finding: Finding }) {
  const ports = finding.evidence.map((flow) => flow.dst_port);
  const oneHost = finding.detector_id === "port_scan" && finding.dst_ip !== null;
  if (!oneHost || finding.dst_port !== null || !ports.some((port) => port < 1024)) {
    return null;
  }
  const { distinct_total: total, flows } = finding.metrics;
  const probed = typeof total === "number" ? total : new Set(ports).size;
  const sampled = typeof flows === "number" && finding.evidence.length < flows;
  const caption = `Ports 0 to 1023 of ${finding.dst_ip}. The scan probed ${formatNumber(probed)} ports${
    sampled ? "; the evidence keeps a sample." : "."
  }`;
  return (
    <PortMap ports={ports} caption={caption} tallyLabel={sampled ? "in the sample" : "probed"} />
  );
}
```

`frontend/src/pages/org/finding/EvidenceTable.tsx`:
```tsx
import type { Finding } from "../../../findings/finding";
import { protocolName } from "../../../findings/vocabulary";
import { formatClock, formatNumber } from "../../../ui/format";

/** An address and port as written in logs; IPv6 addresses go in brackets. */
function endpoint(ip: string, port: number): string {
  return ip.includes(":") ? `[${ip}]:${port}` : `${ip}:${port}`;
}

/** The flows the detector kept as evidence (spec §10), at most 50, in time order. */
export function EvidenceTable({ finding }: { finding: Finding }) {
  const { flows } = finding.metrics;
  const sampledFrom =
    typeof flows === "number" && flows > finding.evidence.length
      ? ` sampled from ${formatNumber(flows)}`
      : "";
  return (
    // Wider than its column on most screens: it scrolls on its own, by keyboard too.
    <div className="table-scroll" role="region" aria-label="Evidence flows" tabIndex={0}>
      <table className="table evidence">
        <caption>
          Evidence: {finding.evidence.length} flows{sampledFrom}, in time order
        </caption>
        <thead>
          <tr>
            <th scope="col">Line</th>
            <th scope="col">Time</th>
            <th scope="col">Source</th>
            <th scope="col">Destination</th>
            <th scope="col">Action</th>
            <th scope="col">Protocol</th>
            <th scope="col">Packets</th>
            <th scope="col">Bytes</th>
          </tr>
        </thead>
        <tbody>
          {finding.evidence.map((flow) => (
            <tr key={flow.line_no}>
              <td className="mono">{flow.line_no}</td>
              <td className="date">{formatClock(flow.start)}</td>
              <td className="mono">{endpoint(flow.src_ip, flow.src_port)}</td>
              <td className="mono">{endpoint(flow.dst_ip, flow.dst_port)}</td>
              <td className="mono">{flow.action}</td>
              <td>{protocolName(flow.protocol)}</td>
              <td>{formatNumber(flow.packets)}</td>
              <td>{formatNumber(flow.bytes)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

`frontend/src/pages/org/finding/FindingFacts.tsx`:
```tsx
import { Fragment } from "react";
import { Link } from "react-router";
import type { Finding } from "../../../findings/finding";
import { metricRows } from "../../../findings/metrics";
import { detectorName, protocolName } from "../../../findings/vocabulary";
import { formatDateTime, formatTime } from "../../../ui/format";

/** What the detector saw (spec §10): who, where, when, and the detector's own numbers. */
export function FindingFacts({ finding }: { finding: Finding }) {
  return (
    <section className="facts" aria-labelledby="facts-title">
      <h2 id="facts-title">Details</h2>
      <dl>
        <dt>Source</dt>
        <dd className="mono">{finding.src_ip}</dd>
        <dt>Destination</dt>
        <dd className={finding.dst_ip === null ? undefined : "mono"}>
          {finding.dst_ip ?? "Several"}
        </dd>
        <dt>Port</dt>
        <dd>{finding.dst_port ?? "Several"}</dd>
        <dt>Protocol</dt>
        <dd>{finding.protocol === null ? "Several" : protocolName(finding.protocol)}</dd>
        <dt>Window</dt>
        <dd>
          {formatDateTime(finding.window_start)} to {formatTime(finding.window_end)}
        </dd>
        <dt>Detector</dt>
        <dd>
          {detectorName(finding.detector_id)}, version {finding.detector_version}
        </dd>
        {metricRows(finding.metrics).map((row) => (
          <Fragment key={row.label}>
            <dt>{row.label}</dt>
            <dd>{row.value}</dd>
          </Fragment>
        ))}
        <dt>Upload</dt>
        <dd>
          <Link to={`../findings?upload=${finding.upload_id}`}>
            Other findings from this upload
          </Link>
        </dd>
      </dl>
    </section>
  );
}
```

`frontend/src/pages/org/finding/FindingPage.tsx`:
```tsx
import { Link, useParams } from "react-router";
import { ApiError } from "../../../api/problem";
import { useFinding } from "../../../findings/finding";
import { detectorName } from "../../../findings/vocabulary";
import { useOrg } from "../../../orgs/org";
import { ErrorNotice } from "../../../ui/ErrorNotice";
import { Loading } from "../../../ui/Loading";
import { Severity } from "../../../ui/Severity";
import { usePageTitle } from "../../../ui/usePageTitle";
import { EvidenceMap } from "./EvidenceMap";
import { EvidenceTable } from "./EvidenceTable";
import { FindingFacts } from "./FindingFacts";
import { Techniques } from "./Techniques";

function FindingNotFound() {
  return (
    <main id="main" className="page narrow">
      <h1>Finding not found</h1>
      <p>The link may be wrong, or the finding may belong to another organization.</p>
      <Link to="../findings">All findings</Link>
    </main>
  );
}

/** `/app/orgs/:org/findings/:id`: one finding, its evidence and what's known about it (spec §10). */
export function FindingPage() {
  const org = useOrg();
  const { findingId = "" } = useParams();
  const finding = useFinding(org.id, findingId);
  const subject =
    finding.data === undefined
      ? "Finding"
      : `${detectorName(finding.data.detector_id)} from ${finding.data.src_ip}`;
  usePageTitle(`${subject} · ${org.name}`);
  if (finding.isPending) {
    return (
      <main id="main" className="page">
        <Loading />
      </main>
    );
  }
  if (finding.isError) {
    if (finding.error instanceof ApiError && finding.error.status === 404) {
      return <FindingNotFound />;
    }
    return (
      <main id="main" className="page">
        <ErrorNotice error={finding.error} />
      </main>
    );
  }
  const found = finding.data;
  return (
    <main id="main" className="page">
      <p className="back">
        <Link to="../findings">All findings</Link>
      </p>
      <p className="finding-kind">
        <Severity severity={found.severity} />
        <span>{detectorName(found.detector_id)}</span>
      </p>
      <h1 className="finding-title">{found.title}</h1>
      <div className="finding-grid">
        <div className="finding-main">
          <section aria-labelledby="evidence-title">
            <h2 id="evidence-title">Evidence</h2>
            <EvidenceMap finding={found} />
            <EvidenceTable finding={found} />
          </section>
        </div>
        <div className="finding-side">
          <FindingFacts finding={found} />
          <Techniques techniques={found.techniques} />
        </div>
      </div>
    </main>
  );
}
```

`frontend/src/pages/org/finding/Techniques.tsx`:
```tsx
import { useQuery } from "@tanstack/react-query";
import { api } from "../../../api/client";
import { unwrap } from "../../../api/problem";
import type { Technique } from "../../../findings/finding";
import { ExternalLink } from "../../../ui/ExternalLink";

/** MITRE's notice, carried by the API's reference data (spec §7). The same for every technique. */
function useMitreNotice(techniqueId: string | undefined) {
  return useQuery({
    queryKey: ["attack-technique", techniqueId],
    enabled: techniqueId !== undefined,
    staleTime: Infinity,
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/attack-techniques/{technique_id}", {
          params: { path: { technique_id: techniqueId ?? "" } },
        }),
      ).notice,
  });
}

interface Named {
  technique: Technique;
  byDetector: boolean;
  byAi: boolean;
  rationale: string | null;
}

/** Each technique once, with who named it: the detector, the AI, or both. */
function named(techniques: Technique[]): Named[] {
  const byId = new Map<string, Named>();
  for (const technique of techniques) {
    const entry = byId.get(technique.id) ?? {
      technique,
      byDetector: false,
      byAi: false,
      rationale: null,
    };
    if (technique.source === "ai") {
      entry.byAi = true;
      entry.rationale = technique.rationale;
    } else {
      entry.byDetector = true;
    }
    byId.set(technique.id, entry);
  }
  return [...byId.values()];
}

function namedBy(entry: Named): string {
  if (entry.byDetector && entry.byAi) {
    return "From the detector and the AI";
  }
  return entry.byAi ? "Suggested by the AI" : "From the detector";
}

/** The finding's ATT&CK techniques (spec §10), linking to attack.mitre.org. */
export function Techniques({ techniques }: { techniques: Technique[] }) {
  const notice = useMitreNotice(techniques[0]?.id);
  return (
    <section className="techniques" aria-labelledby="techniques-title">
      <h2 id="techniques-title">ATT&amp;CK techniques</h2>
      {techniques.length === 0 ? (
        <p className="muted">No ATT&amp;CK technique is linked to this finding.</p>
      ) : (
        <ul>
          {named(techniques).map((entry) => (
            <li key={entry.technique.id}>
              <ExternalLink href={entry.technique.url}>
                <span className="mono">{entry.technique.id}</span> {entry.technique.name}
              </ExternalLink>
              <div className="muted">{namedBy(entry)}</div>
              {entry.rationale !== null && <p>{entry.rationale}</p>}
            </li>
          ))}
        </ul>
      )}
      {notice.data !== undefined && <p className="mitre">{notice.data}</p>}
    </section>
  );
}
```

In `frontend/src/styles.css`, replace:
```css
}

/* Lists that load a page at a time, narrowed by filters above them */

```
with:
```css
}

/* A finding: its kind and title, then the evidence beside what's known about it */

.back {
  margin: 0 0 1.5rem;
}

.finding-title {
  max-width: 48rem;
  margin-bottom: 2rem;
}

.finding-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 21rem);
  gap: 2.5rem;
  align-items: start;
}

.finding-main > section + section,
.finding-side > section + section {
  margin-top: 2.5rem;
}

.finding-main .port-map {
  max-width: 34rem;
  margin: 0 0 1.5rem;
}

.facts dl {
  display: grid;
  grid-template-columns: minmax(0, 9.5rem) minmax(0, 1fr);
  gap: 0.45rem 1rem;
  margin: 0;
}

.facts dt {
  font-stretch: 75%;
  font-weight: 650;
  color: var(--graphite);
}

.facts dd {
  margin: 0;
  overflow-wrap: anywhere;
}

.techniques ul {
  margin: 0;
  padding: 0;
  list-style: none;
}

.techniques li + li {
  margin-top: 1rem;
}

.techniques li p {
  margin: 0.3rem 0 0;
}

.mitre {
  margin-top: 1.25rem;
  font-size: 0.82rem;
  color: var(--graphite);
}

.table caption {
  padding: 0 0 0.6rem;
  caption-side: top;
  text-align: left;
  font-stretch: 75%;
  font-weight: 650;
  color: var(--graphite);
}

/* Fifty flows would push the rest of the page far down: the table scrolls in place, under its
   own column names. */
.table-scroll {
  max-height: 30rem;
  overflow: auto;
  border-radius: var(--radius);
}

.evidence {
  font-size: 0.9rem;
}

.evidence thead th {
  position: sticky;
  top: 0;
  z-index: 1;
}

.evidence th,
.evidence td {
  padding: 0.5rem 0.6rem;
  white-space: nowrap;
}

/* Lists that load a page at a time, narrowed by filters above them */

```

In `frontend/src/styles.css`, replace:
```css

@media (max-width: 62rem) {
  .hero {
    grid-template-columns: minmax(0, 1fr);
```
with:
```css

@media (max-width: 62rem) {
  .finding-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .hero {
    grid-template-columns: minmax(0, 1fr);
```

In `frontend/src/ui/PortMap.tsx`, replace:
```tsx
  callouts = [],
  sweep = false,
}: {
  ports: readonly number[];
```
with:
```tsx
  callouts = [],
  sweep = false,
  tallyLabel = "probed",
}: {
  ports: readonly number[];
```

In `frontend/src/ui/PortMap.tsx`, replace:
```tsx
  callouts?: Callout[];
  sweep?: boolean;
}) {
  const grid = useRef<SVGSVGElement>(null);
```
with:
```tsx
  callouts?: Callout[];
  sweep?: boolean;
  /** What the count beside the caption counts: "150 probed", or "50 in the sample". */
  tallyLabel?: string;
}) {
  const grid = useRef<SVGSVGElement>(null);
```

In `frontend/src/ui/PortMap.tsx`, replace:
```tsx
        <span id={captionId}>{caption}</span>
        <span>
          <b ref={count} /> probed
        </span>
      </figcaption>
```
with:
```tsx
        <span id={captionId}>{caption}</span>
        <span>
          <b ref={count} /> {tallyLabel}
        </span>
      </figcaption>
```

In `frontend/src/ui/format.ts`, replace:
```ts
  return `$${value.toFixed(value < 1 ? 4 : 2)}`;
}
```
with:
```ts
  return `$${value.toFixed(value < 1 ? 4 : 2)}`;
}

const TIME = new Intl.DateTimeFormat(undefined, { timeStyle: "short" });
const CLOCK = new Intl.DateTimeFormat(undefined, { timeStyle: "medium" });

/** A time of day, such as "14:05", for the end of a window that starts the same day. */
export function formatTime(iso: string): string {
  return TIME.format(new Date(iso));
}

/** A time of day to the second, such as "14:05:09", for flows minutes apart. */
export function formatClock(iso: string): string {
  return CLOCK.format(new Date(iso));
}
```

- [ ] **Step 4: Run the tests again**

Run: `cd frontend && pnpm exec vitest run src/findings/metrics.test.ts src/pages/org/finding/ src/ui/PortMap.test.tsx src/ui/format.test.ts`
Expected: `25 passed`.

- [ ] **Step 5: Check everything**

Run: `cd frontend && pnpm lint && pnpm exec tsc --noEmit && pnpm test`
Expected: lint and types are clean, and `153 passed`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(web): a finding's page: its details and numbers, its evidence mapped and listed, and its ATT&CK techniques" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 6: Triage, history and comments

**Files:**
- Create: `frontend/src/pages/org/finding/Triage.tsx`, `Activity.tsx`, `CommentForm.tsx`
- Modify: `frontend/src/pages/org/finding/FindingPage.tsx`, `frontend/src/styles.css`
- Test: `frontend/src/pages/org/finding/Triage.test.tsx`

**Interfaces:**
- Consumes: Task 5's `Finding`, `findingKey` and `FindingPage`; Task 4's `findingsKey(orgId)`; Task 2's `STATUSES`, `statusLabel`, `isStatus`, `useMembers`, `memberName`, `canContribute` and `formatDateTime`; 6a's `api`, `idempotencyKey`, `ApiError` and `unwrap`.
- Produces: `Triage` (the side column's first section), `Activity` (the main column's last) and `CommentForm`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/pages/org/finding/Triage.test.tsx`:
```tsx
import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { ADMIN, FINDING_ID, ORG_ID, OWNER, VIEWER, finding } from "../../../test/fixtures";
import { ORG, signedInAs } from "../../../test/orgApi";
import { renderAt } from "../../../test/render";

const FINDING = `${ORG}/findings/${FINDING_ID}`;
const PAGE = `/app/orgs/${ORG_ID}/findings/${FINDING_ID}`;

function event(id: number, type: string, actor: string | null, payload: Record<string, unknown>) {
  return {
    id: `01a10700-0000-7000-8000-00000000000${id}`,
    type,
    actor_id: actor,
    payload,
    created_at: `2026-10-04T09:3${id}:00Z`,
  };
}

test("a contributor changes the status and assignee, naming the version they read", async () => {
  let patch: Request | undefined;
  const changed = finding({ status: "investigating", assignee_id: ADMIN.user_id, version: 2 });
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding() },
    [`PATCH ${FINDING}`]: (request) => {
      patch = request;
      return { body: changed, headers: { ETag: '"2"' } };
    },
  });
  const { user } = renderAt(PAGE);

  await user.selectOptions(
    await screen.findByRole("combobox", { name: "Status" }),
    "Investigating",
  );
  await user.selectOptions(screen.getByRole("combobox", { name: "Assignee" }), "Ben Admin");
  await user.click(screen.getByRole("button", { name: "Save changes" }));

  await vi.waitFor(() => expect(patch).toBeDefined());
  expect(patch?.headers.get("If-Match")).toBe('"1"');
  expect(await patch?.json()).toEqual({ status: "investigating", assignee_id: ADMIN.user_id });
  expect(await screen.findByRole("button", { name: "Save changes" })).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "Status" })).toHaveValue("investigating");
});

test("only what changed is sent, and unassigning sends no one", async () => {
  let body: unknown;
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding({ assignee_id: ADMIN.user_id }) },
    [`PATCH ${FINDING}`]: async (request) => {
      body = await request.json();
      return { body: finding({ version: 2 }) };
    },
  });
  const { user } = renderAt(PAGE);

  await user.selectOptions(await screen.findByRole("combobox", { name: "Assignee" }), "Unassigned");
  await user.click(screen.getByRole("button", { name: "Save changes" }));

  await vi.waitFor(() => expect(body).toEqual({ assignee_id: null }));
});

test("a change someone else made first is shown, and nothing is overwritten", async () => {
  let reads = 0;
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: () =>
      ++reads === 1 ? { body: finding() } : { body: finding({ status: "resolved", version: 2 }) },
    [`PATCH ${FINDING}`]: {
      status: 412,
      headers: { ETag: '"2"' },
      body: { title: "Precondition Failed", detail: "The finding changed.", trace_id: "t4" },
    },
  });
  const { user } = renderAt(PAGE);

  await user.selectOptions(
    await screen.findByRole("combobox", { name: "Status" }),
    "False positive",
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Someone changed this finding while you had it open. It now shows their change; make yours again if it still applies.",
  );
  expect(await screen.findByRole("combobox", { name: "Status" })).toHaveValue("resolved");
});

test("only owners, admins and analysts can be assigned", async () => {
  signedInAs(OWNER, { [`GET ${FINDING}`]: { body: finding() } });

  renderAt(PAGE);

  const assignee = await screen.findByRole("combobox", { name: "Assignee" });
  await vi.waitFor(() =>
    expect(
      within(assignee)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["Unassigned", "ana@example.com", "Ben Admin"]),
  );
});

test("a viewer sees the status and assignee, with nothing to change", async () => {
  signedInAs(VIEWER, {
    [`GET ${FINDING}`]: { body: finding({ status: "investigating", assignee_id: ADMIN.user_id }) },
  });

  renderAt(PAGE);

  const triage = await screen.findByRole("region", { name: "Triage" });
  expect(within(triage).getByText("Investigating")).toBeInTheDocument();
  expect(await within(triage).findByText("Ben Admin")).toBeInTheDocument();
  expect(screen.queryByRole("combobox")).toBeNull();
  expect(screen.queryByLabelText("Add a comment")).toBeNull();
});

test("the history reads as sentences, oldest first, with comments as they were written", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: {
      body: finding({
        events: [
          event(1, "created", null, {}),
          event(2, "status_changed", ADMIN.user_id, { from: "open", to: "investigating" }),
          event(3, "assigned", ADMIN.user_id, { from: null, to: ADMIN.user_id }),
          event(4, "commented", OWNER.user_id, { text: "Checked: our own scanner.\nClosing." }),
        ],
        events_total: 4,
      }),
    },
  });

  renderAt(PAGE);

  const activity = await screen.findByRole("region", { name: "Activity" });
  await vi.waitFor(() =>
    expect(
      within(activity)
        .getAllByRole("listitem")
        .map((item) => item.firstChild?.textContent),
    ).toEqual([
      "NetTriage found it",
      "Ben Admin changed the status from Open to Investigating",
      "Ben Admin took it on",
      "ana@example.com commented",
    ]),
  );
  expect(within(activity).getByText(/Checked: our own scanner\./)).toHaveTextContent(
    "Checked: our own scanner. Closing.",
  );
});

test("a comment joins the history, sent once", async () => {
  let reads = 0;
  const fake = signedInAs(OWNER, {
    [`GET ${FINDING}`]: () =>
      ++reads === 1
        ? { body: finding() }
        : {
            body: finding({
              events: [
                event(1, "created", null, {}),
                event(2, "commented", OWNER.user_id, { text: "Looking." }),
              ],
              events_total: 2,
            }),
          },
    [`POST ${FINDING}/comments`]: {
      status: 201,
      body: event(2, "commented", OWNER.user_id, { text: "Looking." }),
    },
  });
  const { user } = renderAt(PAGE);

  await user.type(await screen.findByLabelText("Add a comment"), "Looking.");
  await user.click(screen.getByRole("button", { name: "Comment" }));

  expect(await screen.findByText("Looking.")).toBeInTheDocument();
  expect(screen.getByLabelText("Add a comment")).toHaveValue("");
  const post = fake.requests.find((request) => request.method === "POST");
  expect(await post?.json()).toEqual({ text: "Looking." });
  expect(post?.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
});

test("a long history says how much of it is shown", async () => {
  signedInAs(OWNER, { [`GET ${FINDING}`]: { body: finding({ events_total: 140 }) } });

  renderAt(PAGE);

  expect(await screen.findByText("Showing the latest 1 of 140 events.")).toBeInTheDocument();
});

test("a comment the API refuses says why", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding() },
    [`POST ${FINDING}/comments`]: {
      status: 429,
      headers: { "Retry-After": "30" },
      body: { title: "Too Many Requests" },
    },
  });
  const { user } = renderAt(PAGE);

  await user.type(await screen.findByLabelText("Add a comment"), "Again.");
  await user.click(screen.getByRole("button", { name: "Comment" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Too many requests. Try again in 30 seconds.",
  );
  expect(screen.getByLabelText("Add a comment")).toHaveValue("Again.");
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && pnpm exec vitest run src/pages/org/finding/Triage.test.tsx`
Expected: FAIL: `9 failed`. The page has no triage, activity or comment form yet.

- [ ] **Step 3: Write triage, the history and comments, and add them to the page**

`frontend/src/pages/org/finding/Activity.tsx`:
```tsx
import type { components } from "../../../api/schema";
import type { Finding } from "../../../findings/finding";
import { isStatus, statusLabel } from "../../../findings/vocabulary";
import { type Member, memberName, useMembers } from "../../../orgs/members";
import type { Org } from "../../../orgs/org";
import { canContribute } from "../../../orgs/permissions";
import { formatDateTime } from "../../../ui/format";
import { CommentForm } from "./CommentForm";

type FindingEvent = components["schemas"]["FindingEventOut"];

function status(value: unknown): string {
  return typeof value === "string" && isStatus(value) ? statusLabel(value) : "another status";
}

/** One change in the finding's history, as a sentence. */
function sentence(event: FindingEvent, members: Member[] | undefined): string {
  const who = event.actor_id === null ? "NetTriage" : memberName(members, event.actor_id);
  const { from, to } = event.payload;
  switch (event.type) {
    case "created":
      return `${who} found it`;
    case "status_changed":
      return `${who} changed the status from ${status(from)} to ${status(to)}`;
    case "assigned":
      if (typeof to !== "string") {
        return `${who} unassigned it`;
      }
      return to === event.actor_id
        ? `${who} took it on`
        : `${who} assigned it to ${memberName(members, to)}`;
    case "commented":
      return `${who} commented`;
    case "ai_explained":
      return "The AI explained it";
    default:
      return `${who} changed it`;
  }
}

/** The finding's history, oldest first, and adding a comment to it (spec §10). */
export function Activity({ org, finding }: { org: Org; finding: Finding }) {
  const members = useMembers(org.id);
  return (
    <section className="activity" aria-labelledby="activity-title">
      <h2 id="activity-title">Activity</h2>
      {finding.events_total > finding.events.length && (
        <p className="muted">
          Showing the latest {finding.events.length} of {finding.events_total} events.
        </p>
      )}
      <ol className="timeline">
        {finding.events.map((event) => (
          <li key={event.id}>
            <span className="event-what">{sentence(event, members.data)}</span>{" "}
            <time className="muted" dateTime={event.created_at}>
              {formatDateTime(event.created_at)}
            </time>
            {event.type === "commented" && typeof event.payload.text === "string" && (
              <p className="comment">{event.payload.text}</p>
            )}
          </li>
        ))}
      </ol>
      {canContribute(org.role) && <CommentForm org={org} findingId={finding.id} />}
    </section>
  );
}
```

`frontend/src/pages/org/finding/CommentForm.tsx`:
```tsx
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { api, idempotencyKey } from "../../../api/client";
import { unwrap } from "../../../api/problem";
import { findingKey } from "../../../findings/finding";
import type { Org } from "../../../orgs/org";
import { ErrorNotice } from "../../../ui/ErrorNotice";

/**
 * A comment on the finding (spec §7): plain text, kept as written. Its idempotency key lasts as
 * long as its text, so resending a comment that failed never posts it twice.
 */
export function CommentForm({ org, findingId }: { org: Org; findingId: string }) {
  const queryClient = useQueryClient();
  const [text, setText] = useState("");
  const [key, setKey] = useState(idempotencyKey);
  const comment = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/orgs/{org_id}/findings/{finding_id}/comments", {
          params: { path: { org_id: org.id, finding_id: findingId } },
          body: { text: text.trim() },
          headers: { "Idempotency-Key": key },
        }),
      ),
    onSuccess: async () => {
      setText("");
      setKey(idempotencyKey());
      await queryClient.invalidateQueries({ queryKey: findingKey(org.id, findingId) });
    },
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    comment.mutate();
  }
  return (
    <form className="comment-form" onSubmit={submit}>
      <div className="field">
        <label htmlFor="comment-text">Add a comment</label>
        <textarea
          id="comment-text"
          rows={3}
          maxLength={2000}
          value={text}
          aria-describedby="comment-hint"
          onChange={(event) => {
            setText(event.target.value);
            setKey(idempotencyKey());
          }}
        />
        <p id="comment-hint" className="muted">
          Up to 2,000 characters. Everyone in the organization can read it.
        </p>
      </div>
      <button type="submit" className="button" disabled={text.trim() === "" || comment.isPending}>
        Comment
      </button>
      {comment.isError && <ErrorNotice error={comment.error} />}
    </form>
  );
}
```

In `frontend/src/pages/org/finding/FindingPage.tsx`, replace:
```tsx
import { Severity } from "../../../ui/Severity";
import { usePageTitle } from "../../../ui/usePageTitle";
import { EvidenceMap } from "./EvidenceMap";
import { EvidenceTable } from "./EvidenceTable";
import { FindingFacts } from "./FindingFacts";
import { Techniques } from "./Techniques";

function FindingNotFound() {
```
with:
```tsx
import { Severity } from "../../../ui/Severity";
import { usePageTitle } from "../../../ui/usePageTitle";
import { Activity } from "./Activity";
import { EvidenceMap } from "./EvidenceMap";
import { EvidenceTable } from "./EvidenceTable";
import { FindingFacts } from "./FindingFacts";
import { Techniques } from "./Techniques";
import { Triage } from "./Triage";

function FindingNotFound() {
```

In `frontend/src/pages/org/finding/FindingPage.tsx`, replace:
```tsx
            <EvidenceTable finding={found} />
          </section>
        </div>
        <div className="finding-side">
          <FindingFacts finding={found} />
          <Techniques techniques={found.techniques} />
```
with:
```tsx
            <EvidenceTable finding={found} />
          </section>
          <Activity org={org} finding={found} />
        </div>
        <div className="finding-side">
          <Triage org={org} finding={found} />
          <FindingFacts finding={found} />
          <Techniques techniques={found.techniques} />
```

`frontend/src/pages/org/finding/Triage.tsx`:
```tsx
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { api } from "../../../api/client";
import { ApiError, unwrap } from "../../../api/problem";
import { type Finding, findingKey } from "../../../findings/finding";
import { findingsKey } from "../../../findings/findings";
import { STATUSES, type Status, statusLabel } from "../../../findings/vocabulary";
import { type Member, memberName, useMembers } from "../../../orgs/members";
import type { Org } from "../../../orgs/org";
import { canContribute } from "../../../orgs/permissions";
import { ErrorNotice } from "../../../ui/ErrorNotice";

const STALE =
  "Someone changed this finding while you had it open. It now shows their change; make yours again if it still applies.";

type Change = { status?: Status; assignee_id?: string | null };

/**
 * Status and assignee, chosen and then saved together (spec §7): the change names the version it
 * was made on, so it never overwrites someone else's newer one. Only owners, admins and analysts
 * can be assigned (the owner's decision, Plan 4c).
 */
function TriageForm({ org, finding, members }: { org: Org; finding: Finding; members?: Member[] }) {
  const queryClient = useQueryClient();
  const saved = finding.assignee_id ?? "";
  const [status, setStatus] = useState<Status>(finding.status);
  const [assignee, setAssignee] = useState(saved);
  const [version, setVersion] = useState(finding.version);
  // A newer version, saved here or by someone else, replaces what was chosen.
  if (finding.version !== version) {
    setVersion(finding.version);
    setStatus(finding.status);
    setAssignee(saved);
  }
  const save = useMutation({
    mutationFn: async (change: Change) =>
      unwrap(
        await api.PATCH("/api/v1/orgs/{org_id}/findings/{finding_id}", {
          params: {
            path: { org_id: org.id, finding_id: finding.id },
            header: { "if-match": `"${finding.version}"` },
          },
          body: change,
        }),
      ),
    onSuccess: (updated) => {
      queryClient.setQueryData(findingKey(org.id, finding.id), updated);
      void queryClient.invalidateQueries({ queryKey: findingsKey(org.id) });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 412) {
        void queryClient.invalidateQueries({ queryKey: findingKey(org.id, finding.id) });
      }
    },
  });
  const choices = (members ?? []).filter((member) => member.role !== "viewer");
  const change: Change = {};
  if (status !== finding.status) {
    change.status = status;
  }
  if (assignee !== saved) {
    change.assignee_id = assignee === "" ? null : assignee;
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    save.mutate(change);
  }
  return (
    <form onSubmit={submit}>
      <div className="field">
        <label htmlFor="triage-status">Status</label>
        <select
          id="triage-status"
          value={status}
          onChange={(event) => setStatus(event.target.value as Status)}
        >
          {STATUSES.map((choice) => (
            <option key={choice} value={choice}>
              {statusLabel(choice)}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="triage-assignee">Assignee</label>
        <select
          id="triage-assignee"
          value={assignee}
          onChange={(event) => setAssignee(event.target.value)}
        >
          <option value="">Unassigned</option>
          {choices.map((member) => (
            <option key={member.user_id} value={member.user_id}>
              {member.display_name ?? member.email}
            </option>
          ))}
          {saved !== "" && !choices.some((member) => member.user_id === saved) && (
            <option value={saved}>{memberName(members, saved)}</option>
          )}
        </select>
      </div>
      <button
        type="submit"
        className="button button-primary"
        disabled={Object.keys(change).length === 0 || save.isPending}
      >
        Save changes
      </button>
      {save.isError &&
        (save.error instanceof ApiError && save.error.status === 412 ? (
          <p role="alert" className="notice notice-error">
            {STALE}
          </p>
        ) : (
          <ErrorNotice error={save.error} />
        ))}
    </form>
  );
}

/** Where the finding stands: changed by contributors, read by everyone else (spec §10). */
export function Triage({ org, finding }: { org: Org; finding: Finding }) {
  const members = useMembers(org.id);
  return (
    <section className="triage" aria-labelledby="triage-title">
      <h2 id="triage-title">Triage</h2>
      {canContribute(org.role) ? (
        <TriageForm org={org} finding={finding} members={members.data} />
      ) : (
        <dl className="facts-list">
          <dt>Status</dt>
          <dd>{statusLabel(finding.status)}</dd>
          <dt>Assignee</dt>
          <dd>
            {finding.assignee_id === null
              ? "Unassigned"
              : memberName(members.data, finding.assignee_id)}
          </dd>
        </dl>
      )}
    </section>
  );
}
```

In `frontend/src/styles.css`, replace:
```css
}

.facts dl {
  display: grid;
  grid-template-columns: minmax(0, 9.5rem) minmax(0, 1fr);
```
with:
```css
}

.triage .field select {
  min-width: 0;
  width: 100%;
}

.facts dl,
.facts-list {
  display: grid;
  grid-template-columns: minmax(0, 9.5rem) minmax(0, 1fr);
```

In `frontend/src/styles.css`, replace:
```css
}

.facts dt {
  font-stretch: 75%;
  font-weight: 650;
```
with:
```css
}

.facts dt,
.facts-list dt {
  font-stretch: 75%;
  font-weight: 650;
```

In `frontend/src/styles.css`, replace:
```css
}

.facts dd {
  margin: 0;
  overflow-wrap: anywhere;
```
with:
```css
}

.facts dd,
.facts-list dd {
  margin: 0;
  overflow-wrap: anywhere;
```

In `frontend/src/styles.css`, replace:
```css
.techniques li p {
  margin: 0.3rem 0 0;
}

```
with:
```css
.techniques li p {
  margin: 0.3rem 0 0;
}

.timeline {
  margin: 0 0 1.75rem;
  padding: 0;
  list-style: none;
  border-left: 2px solid var(--grid);
}

.timeline li {
  position: relative;
  padding: 0 0 1.1rem 1.1rem;
}

.timeline li::before {
  content: "";
  position: absolute;
  top: 0.5rem;
  left: -5px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--graphite);
}

.event-what {
  font-weight: 600;
}

.comment {
  max-width: 40rem;
  margin: 0.45rem 0 0;
  padding: 0.6rem 0.8rem;
  border: 1px solid var(--grid);
  border-radius: 6px;
  background: var(--plate);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.comment-form {
  max-width: 40rem;
}

.field textarea {
  width: 100%;
  padding: 0.55rem 0.65rem;
  border: 1px solid var(--field-line);
  border-radius: 6px;
  background: var(--plate);
  color: var(--ink);
  font: inherit;
  resize: vertical;
}

.field p {
  margin: 0;
  font-size: 0.88rem;
}

```

- [ ] **Step 4: Run the tests again**

Run: `cd frontend && pnpm exec vitest run src/pages/org/finding/`
Expected: `15 passed`.

- [ ] **Step 5: Check everything**

Run: `cd frontend && pnpm lint && pnpm exec tsc --noEmit && pnpm test`
Expected: lint and types are clean, and `162 passed`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(web): triage on a finding's page: status and assignee that never overwrite a newer change, its history, and comments" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 7: The AI explanation

**Files:**
- Create: `frontend/src/findings/explanation.ts`, `frontend/src/pages/org/finding/AiPanel.tsx`
- Modify: `frontend/src/findings/finding.ts` (`useFinding`'s `watch`), `frontend/src/pages/org/finding/FindingPage.tsx`, `frontend/src/styles.css`, `frontend/src/test/fixtures.ts`
- Test: `frontend/src/findings/explanation.test.ts`, `frontend/src/pages/org/finding/AiPanel.test.tsx`, `frontend/src/pages/org/finding/FindingPage.test.tsx`

**Interfaces:**
- Consumes: Task 5's `Finding`, `findingKey`, `useFinding` and `FindingPage`; Task 2's `Severity`, `severityLabel`, `isSeverity` and `canContribute`; 6a's `api`, `unwrap` and `ErrorNotice`.
- Produces:
  - in `src/findings/explanation.ts`: `AiAnalysis`, `Explanation`, `readExplanation(analysis)`, `failureMessage(analysis)` and `pollInterval(waitingSince, now)`;
  - `useFinding(orgId, findingId, watch?)`: with `watch: { refetchInterval }`, a second reader that shares the page's copy and doesn't fetch on mount;
  - `AiPanel`, the main column's first section;
  - for tests: the fixtures `EXPLANATION`, `ANALYSIS_ID` and `aiAnalysis(fields)`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/findings/explanation.test.ts`:
```ts
import { expect, test } from "vitest";
import { EXPLANATION, aiAnalysis } from "../test/fixtures";
import { failureMessage, pollInterval, readExplanation } from "./explanation";

test("a succeeded answer in output schema v1 is read as an explanation", () => {
  expect(readExplanation(aiAnalysis())).toEqual(EXPLANATION);
});

test("an answer that failed, or in a shape this app doesn't know, is not read", () => {
  expect(readExplanation(aiAnalysis({ status: "failed", output: null }))).toBeNull();
  expect(readExplanation(aiAnalysis({ output_schema_version: "v2" }))).toBeNull();
  expect(readExplanation(aiAnalysis({ output: { ...EXPLANATION, summary: 42 } }))).toBeNull();
  expect(
    readExplanation(aiAnalysis({ output: { ...EXPLANATION, recommended_next_steps: "none" } })),
  ).toBeNull();
});

test("each way an explanation can fail is said plainly", () => {
  expect(failureMessage(aiAnalysis({ status: "skipped_budget", output: null }))).toBe(
    "Not explained: the organization's AI budget for today is used up. It resets at midnight UTC.",
  );
  expect(failureMessage(aiAnalysis({ status: "invalid_output", output: null }))).toBe(
    "The AI's answer didn't pass NetTriage's checks, so it isn't shown.",
  );
  const failed = (code: string) =>
    failureMessage(aiAnalysis({ status: "failed", output: null, error_code: code }));
  expect(failed("ai_disabled")).toBe("AI explanations are switched off right now.");
  expect(failed("provider_throttled")).toBe("The AI service was busy. Try again later.");
  expect(failed("provider_timeout")).toBe("The AI service didn't answer. Try again later.");
});

test("a queued explanation is checked for every few seconds, for five minutes", () => {
  const since = Date.parse("2026-10-04T10:00:00Z");

  expect(pollInterval(since, since + 60_000)).toBe(5000);
  expect(pollInterval(since, since + 5 * 60_000 + 1)).toBe(false);
  expect(pollInterval(null, since)).toBe(false);
});
```

`frontend/src/pages/org/finding/AiPanel.test.tsx`:
```tsx
import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import {
  ANALYSIS_ID,
  EXPLANATION,
  FINDING_ID,
  ORG_ID,
  OWNER,
  VIEWER,
  aiAnalysis,
  finding,
} from "../../../test/fixtures";
import { ORG, signedInAs } from "../../../test/orgApi";
import { renderAt } from "../../../test/render";

const FINDING = `${ORG}/findings/${FINDING_ID}`;
const PAGE = `/app/orgs/${ORG_ID}/findings/${FINDING_ID}`;
const RERUN = `POST ${FINDING}/ai-analyses`;

async function panel() {
  return screen.findByRole("region", { name: "AI explanation" });
}

test("an explanation says it's AI-generated, by which model and prompt, and is shown as plain text", async () => {
  const injected = { ...EXPLANATION, summary: 'Scan. <img src="x" onerror="alert(1)">' };
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding({ ai_analysis: aiAnalysis({ output: injected }) }) },
  });

  renderAt(PAGE);

  const ai = await panel();
  expect(within(ai).getByText("AI-generated")).toBeInTheDocument();
  expect(within(ai).getByText("openai.gpt-oss-20b-1:0")).toBeInTheDocument();
  expect(within(ai).getByText(/prompt v1/)).toBeInTheDocument();
  expect(within(ai).getByText('Scan. <img src="x" onerror="alert(1)">')).toBeInTheDocument();
  expect(ai.querySelector("img")).toBeNull();
  expect(within(ai).getByRole("heading", { name: "Why it matters" })).toBeInTheDocument();
  expect(
    within(ai)
      .getAllByRole("listitem")
      .map((item) => item.textContent),
  ).toEqual([
    "An inventory or vulnerability scanner your team runs.",
    "Check what else 10.0.3.17 reached.",
    "Ask who owns 10.0.3.17.",
  ]);
  expect(within(ai).getByText(/agrees with the detector's High/)).toHaveTextContent(
    "It agrees with the detector's High. Confidence: medium.",
  );
});

test("a contributor rates an explanation, and their rating shows", async () => {
  let body: unknown;
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding({ ai_analysis: aiAnalysis() }) },
    [`PUT ${FINDING}/ai-analyses/${ANALYSIS_ID}/feedback`]: async (request) => {
      body = await request.json();
      return { body: aiAnalysis({ feedback: "up", feedback_by: OWNER.user_id }) };
    },
  });
  const { user } = renderAt(PAGE);

  await user.click(within(await panel()).getByRole("button", { name: "Useful" }));

  expect(await screen.findByRole("button", { name: "Useful" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(screen.getByRole("button", { name: "Not useful" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  expect(body).toEqual({ feedback: "up" });
});

test("asking again queues the finding, and the new explanation appears when it's ready", async () => {
  let reads = 0;
  const failed = aiAnalysis({ status: "failed", output: null, error_code: "provider_throttled" });
  const fresh = aiAnalysis({ id: "01a10800-0000-7000-8000-000000000002" });
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: () =>
      ++reads === 1
        ? { body: finding({ ai_analysis: failed }) }
        : { body: finding({ ai_analysis: fresh }) },
    [RERUN]: { status: 202, body: { status: "queued", ai_analysis: null } },
  });
  const { user } = renderAt(PAGE);

  expect(
    await within(await panel()).findByText("The AI service was busy. Try again later."),
  ).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Explain again" }));

  expect(await screen.findByText(EXPLANATION.summary)).toBeInTheDocument();
});

test("asking again when nothing changed shows the same answer, at no cost", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding({ ai_analysis: aiAnalysis() }) },
    [RERUN]: { status: 200, body: { status: "explained", ai_analysis: aiAnalysis() } },
  });
  const { user } = renderAt(PAGE);

  await user.click(within(await panel()).getByRole("button", { name: "Explain again" }));

  expect(
    await screen.findByText(
      "This explanation is current: nothing changed since it was made, so the AI wasn't asked again.",
    ),
  ).toBeInTheDocument();
});

test("a finding not explained yet offers to explain it", async () => {
  const fake = signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding() },
    [RERUN]: { status: 202, body: { status: "queued", ai_analysis: null } },
  });
  const { user } = renderAt(PAGE);

  expect(await within(await panel()).findByText("Not explained yet.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Explain this finding" }));

  expect(
    await screen.findByText("Queued. The explanation appears here when it's ready."),
  ).toBeInTheDocument();
  const post = fake.requests.find((request) => request.method === "POST");
  expect(await post?.text()).toBe("{}");
});

test("an explanation in a newer format than this app knows is not shown", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: {
      body: finding({ ai_analysis: aiAnalysis({ output_schema_version: "v2" }) }),
    },
  });

  renderAt(PAGE);

  expect(
    await within(await panel()).findByText(
      "This explanation is in a format this version of NetTriage can't show. Reload the page to update.",
    ),
  ).toBeInTheDocument();
});

test("a viewer reads the explanation, with nothing to rate or ask", async () => {
  signedInAs(VIEWER, { [`GET ${FINDING}`]: { body: finding({ ai_analysis: aiAnalysis() }) } });

  renderAt(PAGE);

  const ai = await panel();
  expect(within(ai).getByText(EXPLANATION.summary)).toBeInTheDocument();
  expect(within(ai).queryByRole("button")).toBeNull();
});

test("a request the API refuses says why", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding() },
    [RERUN]: {
      status: 429,
      headers: { "Retry-After": "60" },
      body: { title: "Too Many Requests" },
    },
  });
  const { user } = renderAt(PAGE);

  await user.click(await screen.findByRole("button", { name: "Explain this finding" }));

  await vi.waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Too many requests. Try again in 1 minute.",
    ),
  );
});
```

In `frontend/src/pages/org/finding/FindingPage.test.tsx`, replace:
```tsx
});

test("a finding that isn't there, or isn't the organization's, says so", async () => {
  signedInAs(OWNER, { [`GET ${FINDING}`]: { status: 404, body: { title: "Not Found" } } });
```
with:
```tsx
});

test("opening a finding reads it once, though several parts of the page show it", async () => {
  const fake = signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding() },
    "GET /api/v1/attack-techniques/T1046": technique("T1046"),
  });

  renderAt(PAGE);

  await screen.findByRole("region", { name: "AI explanation" });
  await screen.findByRole("region", { name: "Activity" });
  const reads = fake.requests.filter(
    (request) => request.method === "GET" && request.url.endsWith(`/findings/${FINDING_ID}`),
  );
  expect(reads).toHaveLength(1);
});

test("a finding that isn't there, or isn't the organization's, says so", async () => {
  signedInAs(OWNER, { [`GET ${FINDING}`]: { status: 404, body: { title: "Not Found" } } });
```

In `frontend/src/test/fixtures.ts`, replace:
```ts
import type { Org } from "../orgs/org";
import type { Member } from "../orgs/members";
import type { Finding } from "../findings/finding";
import type { FindingSummary } from "../findings/findings";
```
with:
```ts
import type { Org } from "../orgs/org";
import type { Member } from "../orgs/members";
import type { AiAnalysis } from "../findings/explanation";
import type { Finding } from "../findings/finding";
import type { FindingSummary } from "../findings/findings";
```

In `frontend/src/test/fixtures.ts`, replace:
```ts
    ai_analysis: null,
    ...fields,
  };
}
```
with:
```ts
    ai_analysis: null,
    ...fields,
  };
}

export const EXPLANATION = {
  summary: "10.0.3.17 tried 150 ports on 10.0.0.5 in five minutes, and every attempt was refused.",
  why_it_matters: "A host inside the network looking for services is often an attack's first step.",
  likely_benign_explanations: ["An inventory or vulnerability scanner your team runs."],
  recommended_next_steps: ["Check what else 10.0.3.17 reached.", "Ask who owns 10.0.3.17."],
  attack_techniques: [{ id: "T1046", rationale: "One source probed many ports on one host." }],
  severity_assessment: {
    agrees_with_detector: true,
    suggested_severity: "high",
    reason: "Internal and fast.",
  },
  confidence: "medium",
  insufficient_evidence: false,
};

export const ANALYSIS_ID = "01a10800-0000-7000-8000-000000000001";

export function aiAnalysis(fields: Partial<AiAnalysis> = {}): AiAnalysis {
  return {
    id: ANALYSIS_ID,
    status: "succeeded",
    provider: "bedrock",
    model_id: "openai.gpt-oss-20b-1:0",
    prompt_version: "v1",
    output_schema_version: "v1",
    output: EXPLANATION,
    error_code: null,
    input_tokens: 900,
    output_tokens: 300,
    cost_usd: "0.0003",
    latency_ms: 2100,
    feedback: null,
    feedback_by: null,
    created_at: "2026-10-04T09:32:00Z",
    updated_at: "2026-10-04T09:32:02Z",
    ...fields,
  };
}
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && pnpm exec vitest run src/findings/explanation.test.ts src/pages/org/finding/`
Expected: FAIL. `explanation.test.ts` stops on `Failed to resolve import "./explanation"`, the 8 AI panel tests fail as there's no panel, and `opening a finding reads it once` fails while it waits for the panel.

- [ ] **Step 3: Write the explanation reader and the panel**

`frontend/src/findings/explanation.ts`:
```ts
/**
 * A finding's AI explanation (spec §8.3): the model's answer in output schema v1, read field by
 * field so a shape this app doesn't know is never shown half right; what each failure means; and
 * how long a queued explanation is waited for.
 */
import type { components } from "../api/schema";
import { type Severity, isSeverity } from "./vocabulary";

export type AiAnalysis = components["schemas"]["AiAnalysisOut"];

export interface Explanation {
  summary: string;
  why_it_matters: string;
  likely_benign_explanations: string[];
  recommended_next_steps: string[];
  attack_techniques: { id: string; rationale: string }[];
  severity_assessment: {
    agrees_with_detector: boolean;
    suggested_severity: Severity;
    reason: string;
  };
  confidence: "low" | "medium" | "high";
  insufficient_evidence: boolean;
}

const POLL_MS = 5000;
const WAIT_MS = 5 * 60_000;

function isText(value: unknown): value is string {
  return typeof value === "string";
}

function isTextList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(isText);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The answer, if it succeeded and has exactly the shape of output schema v1; otherwise null. */
export function readExplanation(analysis: AiAnalysis): Explanation | null {
  const output = analysis.output;
  if (
    analysis.status !== "succeeded" ||
    analysis.output_schema_version !== "v1" ||
    output === null
  ) {
    return null;
  }
  const severity = output.severity_assessment;
  const techniques = output.attack_techniques;
  const valid =
    isText(output.summary) &&
    isText(output.why_it_matters) &&
    isTextList(output.likely_benign_explanations) &&
    isTextList(output.recommended_next_steps) &&
    Array.isArray(techniques) &&
    techniques.every((claim) => isRecord(claim) && isText(claim.id) && isText(claim.rationale)) &&
    isRecord(severity) &&
    typeof severity.agrees_with_detector === "boolean" &&
    isText(severity.suggested_severity) &&
    isSeverity(severity.suggested_severity) &&
    isText(severity.reason) &&
    (output.confidence === "low" ||
      output.confidence === "medium" ||
      output.confidence === "high") &&
    typeof output.insufficient_evidence === "boolean";
  return valid ? (output as unknown as Explanation) : null;
}

/** Why there's no explanation to show, in words the person can act on. */
export function failureMessage(analysis: AiAnalysis): string {
  if (analysis.status === "skipped_budget") {
    return "Not explained: the organization's AI budget for today is used up. It resets at midnight UTC.";
  }
  if (analysis.status === "invalid_output" || analysis.error_code === "checks_failed") {
    return "The AI's answer didn't pass NetTriage's checks, so it isn't shown.";
  }
  if (analysis.error_code === "ai_disabled") {
    return "AI explanations are switched off right now.";
  }
  if (analysis.error_code === "provider_throttled") {
    return "The AI service was busy. Try again later.";
  }
  return "The AI service didn't answer. Try again later.";
}

/** How often to check for a queued explanation: every few seconds for five minutes, then not. */
export function pollInterval(waitingSince: number | null, now: number): number | false {
  return waitingSince !== null && now - waitingSince <= WAIT_MS ? POLL_MS : false;
}
```

In `frontend/src/findings/finding.ts`, replace:
```ts
}

export function useFinding(orgId: string, findingId: string) {
  return useQuery({
    queryKey: findingKey(orgId, findingId),
    queryFn: async () =>
      unwrap(
```
with:
```ts
}

/**
 * The finding. A second reader on the same page, such as the AI panel checking back while an
 * explanation is queued, passes `watch`: it shares the page's copy instead of reading it again.
 */
export function useFinding(
  orgId: string,
  findingId: string,
  watch?: { refetchInterval: () => number | false },
) {
  return useQuery({
    queryKey: findingKey(orgId, findingId),
    refetchInterval: watch?.refetchInterval ?? false,
    refetchOnMount: watch === undefined,
    queryFn: async () =>
      unwrap(
```

`frontend/src/pages/org/finding/AiPanel.tsx`:
```tsx
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../../api/client";
import { unwrap } from "../../../api/problem";
import {
  type AiAnalysis,
  type Explanation,
  failureMessage,
  pollInterval,
  readExplanation,
} from "../../../findings/explanation";
import { type Finding, findingKey, useFinding } from "../../../findings/finding";
import { type Severity, severityLabel } from "../../../findings/vocabulary";
import type { Org } from "../../../orgs/org";
import { canContribute } from "../../../orgs/permissions";
import { ErrorNotice } from "../../../ui/ErrorNotice";

/** The model's answer, every part of it plain text (spec §8.4, §10). */
function ExplanationBody({
  explanation,
  detector,
}: {
  explanation: Explanation;
  detector: Severity;
}) {
  const assessment = explanation.severity_assessment;
  return (
    <>
      <p className="ai-summary">{explanation.summary}</p>
      <h3>Why it matters</h3>
      <p>{explanation.why_it_matters}</p>
      {explanation.likely_benign_explanations.length > 0 && (
        <>
          <h3>Harmless explanations to rule out</h3>
          <ul>
            {explanation.likely_benign_explanations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      )}
      <h3>Next steps</h3>
      <ol>
        {explanation.recommended_next_steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
      <p className="muted">
        {assessment.agrees_with_detector
          ? `It agrees with the detector's ${severityLabel(detector)}.`
          : `It suggests ${severityLabel(assessment.suggested_severity)} rather than the detector's ${severityLabel(detector)}: ${assessment.reason}`}{" "}
        Confidence: {explanation.confidence}.
      </p>
      {explanation.insufficient_evidence && (
        <p className="notice">The AI says the evidence isn't enough to be sure.</p>
      )}
    </>
  );
}

/** Rating an explanation, for the evals that choose the model (spec §8.5). */
function Feedback({
  org,
  finding,
  analysis,
}: {
  org: Org;
  finding: Finding;
  analysis: AiAnalysis;
}) {
  const queryClient = useQueryClient();
  const rate = useMutation({
    mutationFn: async (feedback: "up" | "down") =>
      unwrap(
        await api.PUT(
          "/api/v1/orgs/{org_id}/findings/{finding_id}/ai-analyses/{analysis_id}/feedback",
          {
            params: {
              path: { org_id: org.id, finding_id: finding.id, analysis_id: analysis.id },
            },
            body: { feedback },
          },
        ),
      ),
    onSuccess: (rated) =>
      queryClient.setQueryData<Finding>(
        findingKey(org.id, finding.id),
        (old) => old && { ...old, ai_analysis: rated },
      ),
  });
  return (
    <div className="ai-feedback" role="group" aria-label="Rate this explanation">
      <span className="muted">Was it useful?</span>
      <button
        type="button"
        className="button"
        aria-pressed={analysis.feedback === "up"}
        disabled={rate.isPending}
        onClick={() => rate.mutate("up")}
      >
        Useful
      </button>
      <button
        type="button"
        className="button"
        aria-pressed={analysis.feedback === "down"}
        disabled={rate.isPending}
        onClick={() => rate.mutate("down")}
      >
        Not useful
      </button>
      {rate.isError && <ErrorNotice error={rate.error} />}
    </div>
  );
}

/**
 * The AI explanation (spec §10): labeled AI-generated, with its model and prompt version. A
 * contributor rates it, or asks again; a queued request is checked for every few seconds.
 */
export function AiPanel({ org, finding }: { org: Org; finding: Finding }) {
  const queryClient = useQueryClient();
  const key = findingKey(org.id, finding.id);
  const contributor = canContribute(org.role);
  const analysis = finding.ai_analysis;
  const [openedAt] = useState(() => Date.now());
  const [waiting, setWaiting] = useState<{ after: string | null; since: number } | null>(null);
  const [current, setCurrent] = useState(false);
  const answered =
    waiting !== null &&
    analysis !== null &&
    analysis.id !== waiting.after &&
    analysis.status !== "pending";
  const queued = waiting !== null && !answered;
  const since = queued ? waiting.since : analysis?.status === "pending" ? openedAt : null;
  useFinding(org.id, finding.id, { refetchInterval: () => pollInterval(since, Date.now()) });
  const rerun = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/orgs/{org_id}/findings/{finding_id}/ai-analyses", {
          params: { path: { org_id: org.id, finding_id: finding.id } },
        }),
      ),
    onSuccess: async (result) => {
      if (result.status === "explained" && result.ai_analysis !== null) {
        const latest = result.ai_analysis;
        queryClient.setQueryData<Finding>(key, (old) => old && { ...old, ai_analysis: latest });
        setCurrent(true);
        return;
      }
      setCurrent(false);
      setWaiting({ after: analysis?.id ?? null, since: Date.now() });
      await queryClient.invalidateQueries({ queryKey: key });
    },
  });
  const explanation = analysis === null || queued ? null : readExplanation(analysis);
  let body;
  if (queued) {
    body = <p>Queued. The explanation appears here when it's ready.</p>;
  } else if (analysis === null) {
    body = (
      <p>
        Not explained yet.{" "}
        <span className="muted">
          NetTriage explains each upload's 20 most severe findings by itself.
        </span>
      </p>
    );
  } else if (analysis.status === "pending") {
    body = <p>The AI is working on it.</p>;
  } else if (explanation !== null) {
    body = <ExplanationBody explanation={explanation} detector={finding.severity} />;
  } else if (analysis.status === "succeeded") {
    body = (
      <p>
        This explanation is in a format this version of NetTriage can't show. Reload the page to
        update.
      </p>
    );
  } else {
    body = <p>{failureMessage(analysis)}</p>;
  }
  return (
    <section className="ai-panel" aria-labelledby="ai-title">
      <h2 id="ai-title">AI explanation</h2>
      {explanation !== null && analysis !== null && (
        <p className="ai-by">
          <span className="ai-label">AI-generated</span>{" "}
          <span className="muted">
            by <span className="mono">{analysis.model_id}</span>, prompt {analysis.prompt_version}
          </span>
        </p>
      )}
      {body}
      {current && (
        <p role="status" className="muted">
          This explanation is current: nothing changed since it was made, so the AI wasn't asked
          again.
        </p>
      )}
      {contributor && explanation !== null && analysis !== null && (
        <Feedback org={org} finding={finding} analysis={analysis} />
      )}
      {contributor && !queued && analysis?.status !== "pending" && (
        <button
          type="button"
          className="button"
          disabled={rerun.isPending}
          onClick={() => rerun.mutate()}
        >
          {analysis === null ? "Explain this finding" : "Explain again"}
        </button>
      )}
      {rerun.isError && <ErrorNotice error={rerun.error} />}
    </section>
  );
}
```

In `frontend/src/pages/org/finding/FindingPage.tsx`, replace:
```tsx
import { usePageTitle } from "../../../ui/usePageTitle";
import { Activity } from "./Activity";
import { EvidenceMap } from "./EvidenceMap";
import { EvidenceTable } from "./EvidenceTable";
```
with:
```tsx
import { usePageTitle } from "../../../ui/usePageTitle";
import { Activity } from "./Activity";
import { AiPanel } from "./AiPanel";
import { EvidenceMap } from "./EvidenceMap";
import { EvidenceTable } from "./EvidenceTable";
```

In `frontend/src/pages/org/finding/FindingPage.tsx`, replace:
```tsx
      <div className="finding-grid">
        <div className="finding-main">
          <section aria-labelledby="evidence-title">
            <h2 id="evidence-title">Evidence</h2>
```
with:
```tsx
      <div className="finding-grid">
        <div className="finding-main">
          <AiPanel org={org} finding={found} />
          <section aria-labelledby="evidence-title">
            <h2 id="evidence-title">Evidence</h2>
```

In `frontend/src/styles.css`, replace:
```css
}

.timeline {
  margin: 0 0 1.75rem;
```
with:
```css
}

.ai-panel {
  padding: 1.25rem 1.4rem;
  border: 1px solid var(--grid);
  border-left: 3px solid var(--teal);
  border-radius: 0 var(--radius) var(--radius) 0;
  background: var(--plate);
}

.ai-panel h3 {
  margin: 1.1rem 0 0.35rem;
  font-size: 1rem;
}

.ai-panel ul,
.ai-panel ol {
  margin: 0;
  padding-left: 1.25rem;
}

.ai-by .ai-label {
  display: inline;
  margin: 0;
}

.ai-by {
  margin: 0 0 0.75rem;
}

.ai-summary {
  margin: 0;
  font-size: 1.08rem;
}

.ai-feedback {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem 0.75rem;
  margin: 1.25rem 0 0.75rem;
}

.ai-feedback .button[aria-pressed="true"] {
  border-color: var(--ink);
  background: var(--ink);
  color: var(--plate);
}

.timeline {
  margin: 0 0 1.75rem;
```

- [ ] **Step 4: Run the tests again**

Run: `cd frontend && pnpm exec vitest run src/findings/explanation.test.ts src/pages/org/finding/`
Expected: `28 passed`.

- [ ] **Step 5: Check that the read-once test guards `watch`**

In `frontend/src/findings/finding.ts`, change `refetchOnMount: watch === undefined,` to `refetchOnMount: true,` and run `cd frontend && pnpm exec vitest run src/pages/org/finding/FindingPage.test.tsx -t "reads it once"`.
Expected: `1 failed`: the panel fetches the finding a second time. Change it back, and run it again: `1 passed`.

- [ ] **Step 6: Check everything**

Run: `cd frontend && pnpm lint && pnpm exec tsc --noEmit && pnpm test`
Expected: lint and types are clean, and `175 passed`.

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat(web): the AI panel: an explanation labeled with its model and prompt, rated, asked again, and checked for while queued" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 8: The audit log

**Files:**
- Create: `frontend/src/audit/auditLog.ts`, `frontend/src/pages/org/AuditLog.tsx`
- Modify: `frontend/src/app/routes.tsx`, `frontend/src/pages/org/OrgLayout.tsx`
- Test: `frontend/src/pages/org/AuditLog.test.tsx`

**Interfaces:**
- Consumes: Task 2's `canReadAudit`, `useMembers`, `memberName` and `formatDateTime`; Task 4's fixture `FINDING_ID`.
- Produces: in `src/audit/auditLog.ts`, `AuditEvent`, `actionLabel(action)`, `outcomeLabel(outcome)`, `detailsText(details)` and `useAuditLog(orgId, enabled)`; the route `audit` and its tab, for Owners and Admins.

- [ ] **Step 1: Write the failing tests**

`frontend/src/pages/org/AuditLog.test.tsx`:
```tsx
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { ADMIN, FINDING_ID, ORG_ID, OWNER, VIEWER } from "../../test/fixtures";
import { ORG, signedInAs } from "../../test/orgApi";
import { renderAt } from "../../test/render";

const AUDIT = `GET ${ORG}/audit-log`;
const PAGE = `/app/orgs/${ORG_ID}/audit`;

function auditEvent(id: number, fields: object) {
  return {
    id: `01a10900-0000-7000-8000-00000000000${id}`,
    created_at: `2026-10-04T10:0${id}:00Z`,
    actor_user_id: OWNER.user_id,
    actor_type: "user",
    action: "org.renamed",
    target_type: "organization",
    target_id: ORG_ID,
    outcome: "success",
    details: {},
    ...fields,
  };
}

test("an owner reads the audit log, newest first, with each action in words", async () => {
  signedInAs(OWNER, {
    [AUDIT]: {
      body: {
        events: [
          auditEvent(3, {
            actor_user_id: ADMIN.user_id,
            action: "finding.status_changed",
            target_type: "finding",
            target_id: FINDING_ID,
            details: { from: "open", to: "resolved" },
          }),
          auditEvent(2, { actor_user_id: null, actor_type: "system", action: "budget.exhausted" }),
          auditEvent(1, { action: "member.role_changed", outcome: "denied" }),
        ],
        next_cursor: null,
      },
    },
  });

  renderAt(PAGE);

  const rows = (await screen.findAllByRole("row")).slice(1);
  expect(rows).toHaveLength(3);
  const [triage, budget, refused] = rows as [HTMLElement, HTMLElement, HTMLElement];
  expect(await within(triage).findByText("Ben Admin")).toBeInTheDocument();
  expect(within(triage).getByText("Changed a finding's status")).toBeInTheDocument();
  expect(within(triage).getByText("finding.status_changed")).toBeInTheDocument();
  expect(within(triage).getByText("from: open, to: resolved")).toBeInTheDocument();
  expect(within(triage).getByRole("link", { name: "The finding" })).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}/findings/${FINDING_ID}`,
  );
  expect(within(budget).getByText("NetTriage")).toBeInTheDocument();
  expect(within(refused).getByText("Refused")).toBeInTheDocument();
  expect(screen.getByText("Events are kept for 180 days.")).toBeInTheDocument();
  expect(document.title).toBe("Audit log · Acme Security · NetTriage");
});

test("an action this version of the app doesn't know is shown by its code", async () => {
  signedInAs(OWNER, {
    [AUDIT]: {
      body: { events: [auditEvent(1, { action: "report.exported" })], next_cursor: null },
    },
  });

  renderAt(PAGE);

  expect(await screen.findByText("report.exported")).toBeInTheDocument();
});

test("older events load a page at a time", async () => {
  signedInAs(OWNER, {
    [AUDIT]: (request) =>
      new URL(request.url).searchParams.get("cursor") === "c2"
        ? { body: { events: [auditEvent(1, { action: "org.created" })], next_cursor: null } }
        : { body: { events: [auditEvent(2, {})], next_cursor: "c2" } },
  });
  const { user } = renderAt(PAGE);

  await user.click(await screen.findByRole("button", { name: "Show older events" }));

  expect(await screen.findByText("Created the organization")).toBeInTheDocument();
  expect(screen.getByText("Renamed the organization")).toBeInTheDocument();
});

test("only owners and admins see the audit log, in the tabs and at its address", async () => {
  const fake = signedInAs({ ...VIEWER, role: "analyst" });

  renderAt(PAGE);

  expect(
    await screen.findByText("Only Owners and Admins can see the audit log."),
  ).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Audit log" })).toBeNull();
  expect(fake.requests.some((request) => request.url.includes("/audit-log"))).toBe(false);
});

test("an audit log the API refuses says why", async () => {
  signedInAs(OWNER, {
    [AUDIT]: {
      status: 503,
      body: { title: "Service Unavailable", detail: "Try again.", trace_id: "t8" },
    },
  });

  renderAt(PAGE);

  expect(await screen.findByRole("alert")).toHaveTextContent("Try again. (reference t8)");
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && pnpm exec vitest run src/pages/org/AuditLog.test.tsx`
Expected: FAIL: `5 failed`. There's no audit log page.

- [ ] **Step 3: Write the audit log**

In `frontend/src/app/routes.tsx`, replace:
```tsx
import { Landing } from "../pages/Landing";
import { NotFound } from "../pages/NotFound";
import { FindingPage } from "../pages/org/finding/FindingPage";
import { Findings } from "../pages/org/Findings";
```
with:
```tsx
import { Landing } from "../pages/Landing";
import { NotFound } from "../pages/NotFound";
import { AuditLog } from "../pages/org/AuditLog";
import { FindingPage } from "../pages/org/finding/FindingPage";
import { Findings } from "../pages/org/Findings";
```

In `frontend/src/app/routes.tsx`, replace:
```tsx
          { path: "uploads", element: <Uploads /> },
          { path: "members", element: <Members /> },
          { path: "settings", element: <OrgSettings /> },
        ],
```
with:
```tsx
          { path: "uploads", element: <Uploads /> },
          { path: "members", element: <Members /> },
          { path: "audit", element: <AuditLog /> },
          { path: "settings", element: <OrgSettings /> },
        ],
```

`frontend/src/audit/auditLog.ts`:
```ts
/** An organization's audit log (spec §9.4): who did what, newest first, a page at a time. */
import { useInfiniteQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";

export type AuditEvent = components["schemas"]["AuditEventOut"];

/** The actions the API records, in words; an action added later is shown by its code. */
const ACTIONS: Record<string, string> = {
  "org.created": "Created the organization",
  "org.renamed": "Renamed the organization",
  "org.deleted": "Deleted the organization",
  "member.invited": "Invited someone",
  "member.joined": "Joined",
  "member.left": "Left",
  "member.removed": "Removed a member",
  "member.role_changed": "Changed a member's role",
  "invitation.revoked": "Revoked an invitation",
  "upload.created": "Uploaded a flow log",
  "finding.status_changed": "Changed a finding's status",
  "finding.assigned": "Assigned a finding",
  "finding.commented": "Commented on a finding",
  "ai.rerun_requested": "Asked the AI to explain a finding again",
  "budget.exhausted": "Used up an AI budget",
  "authz.denied": "Was refused an action",
  "ratelimit.limited": "Was slowed down for sending too many requests",
  "auth.session_created": "Signed in",
  "auth.logout": "Signed out",
  "auth.logout_all": "Signed out everywhere",
};

const OUTCOMES: Record<string, string> = { success: "Done", denied: "Refused", error: "Failed" };

export function actionLabel(action: string): string | null {
  return ACTIONS[action] ?? null;
}

export function outcomeLabel(outcome: string): string {
  return OUTCOMES[outcome] ?? outcome;
}

/** An event's details on one line, such as "from: open, to: resolved". */
export function detailsText(details: Record<string, unknown>): string {
  return Object.entries(details)
    .map(([key, value]) => {
      const shown =
        typeof value === "object" && value !== null ? JSON.stringify(value) : String(value);
      return `${key}: ${shown}`;
    })
    .join(", ");
}

export function useAuditLog(orgId: string, enabled: boolean) {
  return useInfiniteQuery({
    queryKey: ["audit-log", orgId],
    enabled,
    queryFn: async ({ pageParam }) =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/audit-log", {
          params: { path: { org_id: orgId }, query: pageParam ? { cursor: pageParam } : {} },
        }),
      ),
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}
```

`frontend/src/pages/org/AuditLog.tsx`:
```tsx
import { Link } from "react-router";
import {
  type AuditEvent,
  actionLabel,
  detailsText,
  outcomeLabel,
  useAuditLog,
} from "../../audit/auditLog";
import { type Member, memberName, useMembers } from "../../orgs/members";
import { useOrg } from "../../orgs/org";
import { canReadAudit } from "../../orgs/permissions";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { formatDateTime } from "../../ui/format";
import { Loading } from "../../ui/Loading";
import { usePageTitle } from "../../ui/usePageTitle";

function who(event: AuditEvent, members: Member[] | undefined): string {
  if (event.actor_type === "system") {
    return "NetTriage";
  }
  return event.actor_user_id === null
    ? "Someone not signed in"
    : memberName(members, event.actor_user_id);
}

function EventRow({ event, members }: { event: AuditEvent; members?: Member[] }) {
  const label = actionLabel(event.action);
  const details = detailsText(event.details);
  return (
    <tr>
      <td className="date">{formatDateTime(event.created_at)}</td>
      <td>{who(event, members)}</td>
      <td>
        {label !== null && <div>{label}</div>}
        <div className="mono muted">{event.action}</div>
        {details !== "" && <div className="mono muted">{details}</div>}
        {event.target_type === "finding" && event.target_id !== null && (
          <Link to={`../findings/${event.target_id}`}>The finding</Link>
        )}
      </td>
      <td>
        <span className="state">{outcomeLabel(event.outcome)}</span>
      </td>
    </tr>
  );
}

/** `/app/orgs/:org/audit`: every recorded action in the organization, for Owners and Admins. */
export function AuditLog() {
  const org = useOrg();
  usePageTitle(`Audit log · ${org.name}`);
  const allowed = canReadAudit(org.role);
  const log = useAuditLog(org.id, allowed);
  const members = useMembers(org.id);
  const events = log.data?.pages.flatMap((page) => page.events) ?? [];
  return (
    <main id="main" className="page">
      <div className="page-head">
        <h1>Audit log</h1>
        <span className="muted">Events are kept for 180 days.</span>
      </div>
      {!allowed && <p>Only Owners and Admins can see the audit log.</p>}
      {log.isLoading && <Loading />}
      {log.isError && <ErrorNotice error={log.error} />}
      {log.isSuccess && events.length === 0 && <p className="muted">Nothing recorded yet.</p>}
      {events.length > 0 && (
        <table className="table">
          <caption className="visually-hidden">Audit log of {org.name}</caption>
          <thead>
            <tr>
              <th scope="col">When</th>
              <th scope="col">Who</th>
              <th scope="col">What</th>
              <th scope="col">Outcome</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <EventRow key={event.id} event={event} members={members.data} />
            ))}
          </tbody>
        </table>
      )}
      {log.hasNextPage && (
        <button
          type="button"
          className="button more"
          disabled={log.isFetchingNextPage}
          onClick={() => void log.fetchNextPage()}
        >
          Show older events
        </button>
      )}
    </main>
  );
}
```

In `frontend/src/pages/org/OrgLayout.tsx`, replace:
```tsx
import { ApiError } from "../../api/problem";
import { useOrgQuery } from "../../orgs/org";
import { roleLabel } from "../../orgs/roles";
import { ErrorNotice } from "../../ui/ErrorNotice";
```
with:
```tsx
import { ApiError } from "../../api/problem";
import { useOrgQuery } from "../../orgs/org";
import { canReadAudit } from "../../orgs/permissions";
import { roleLabel } from "../../orgs/roles";
import { ErrorNotice } from "../../ui/ErrorNotice";
```

In `frontend/src/pages/org/OrgLayout.tsx`, replace:
```tsx
            <NavLink to="uploads">Uploads</NavLink>
            <NavLink to="members">Members</NavLink>
            <NavLink to="settings">Settings</NavLink>
          </nav>
```
with:
```tsx
            <NavLink to="uploads">Uploads</NavLink>
            <NavLink to="members">Members</NavLink>
            {canReadAudit(org.data.role) && <NavLink to="audit">Audit log</NavLink>}
            <NavLink to="settings">Settings</NavLink>
          </nav>
```

- [ ] **Step 4: Run the tests again**

Run: `cd frontend && pnpm exec vitest run src/pages/org/AuditLog.test.tsx`
Expected: `5 passed`.

- [ ] **Step 5: Check everything**

Run: `cd frontend && pnpm lint && pnpm exec tsc --noEmit && pnpm test`
Expected: lint and types are clean, and `180 passed`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(web): the audit log, for owners and admins: who did what, in words and by code" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 9: The AI usage

**Files:**
- Create: `frontend/src/usage/usage.ts`, `frontend/src/pages/org/Usage.tsx`, `frontend/src/pages/org/UsageChart.tsx`
- Modify: `frontend/src/ui/format.ts`, `frontend/src/app/routes.tsx`, `frontend/src/pages/org/OrgLayout.tsx`, `frontend/src/styles.css`
- Test: `frontend/src/usage/usage.test.ts`, `frontend/src/pages/org/Usage.test.tsx`, `frontend/src/ui/format.test.ts`

**Interfaces:**
- Consumes: Task 2's `canReadUsage`, `formatNumber` and `formatUsd`.
- Produces: in `src/usage/usage.ts`, `Usage`, `PERIODS`, `periodFrom(value)`, `lastDays(days, now)`, `dailyCost(usage, days, now)` and `useUsage(orgId, days, enabled)`; `formatDay(day)`; `UsageChart`; the route `usage` and its tab, for Owners and Admins.

- [ ] **Step 1: Write the failing tests**

`frontend/src/pages/org/Usage.test.tsx`:
```tsx
import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ORG_ID, OWNER, VIEWER } from "../../test/fixtures";
import { ORG, signedInAs } from "../../test/orgApi";
import { renderAt } from "../../test/render";

const USAGE = `GET ${ORG}/usage`;
const PAGE = `/app/orgs/${ORG_ID}/usage`;
const SOME = {
  body: {
    days: [
      { day: "2026-10-04", calls: 3, input_tokens: 2700, output_tokens: 900, cost_usd: "0.0009" },
      { day: "2026-10-02", calls: 1, input_tokens: 900, output_tokens: 300, cost_usd: "0.0003" },
    ],
    totals: { calls: 4, input_tokens: 3600, output_tokens: 1200, cost_usd: "0.0012" },
  },
};

function asked(requests: Request[]): string | null | undefined {
  const reads = requests.filter((request) => request.url.includes("/usage"));
  return reads.length === 0 ? undefined : new URL(reads.at(-1)?.url ?? "").searchParams.get("days");
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-04T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

test("an owner sees the AI's cost per day drawn, then listed, with totals", async () => {
  const fake = signedInAs(OWNER, { [USAGE]: SOME });

  renderAt(PAGE);

  const chart = await screen.findByRole("figure", { name: /AI cost per UTC day/ });
  expect(chart.querySelectorAll("rect.bar")).toHaveLength(2);
  const table = screen.getByRole("table", { name: /AI calls by day/ });
  const rows = within(table).getAllByRole("row");
  expect(rows).toHaveLength(4);
  expect(rows[1]).toHaveTextContent("3");
  expect(rows[3]).toHaveTextContent("Total");
  expect(rows[3]).toHaveTextContent("$0.0012");
  expect(asked(fake.requests)).toBe("30");
  expect(document.title).toBe("AI usage · Acme Security · NetTriage");
});

test("choosing a period asks for it, and keeps it in the address", async () => {
  const fake = signedInAs(OWNER, { [USAGE]: SOME });
  const { user, router } = renderAt(PAGE);

  await user.selectOptions(await screen.findByLabelText("Period"), "Last 7 days");

  await vi.waitFor(() => expect(asked(fake.requests)).toBe("7"));
  expect(router.state.location.search).toBe("?days=7");
});

test("a period without AI calls says so", async () => {
  signedInAs(OWNER, {
    [USAGE]: {
      body: { days: [], totals: { calls: 0, input_tokens: 0, output_tokens: 0, cost_usd: "0" } },
    },
  });

  renderAt(PAGE);

  expect(await screen.findByText("No AI calls in the last 30 days.")).toBeInTheDocument();
  expect(screen.queryByRole("figure")).toBeNull();
});

test("only owners and admins see the AI usage, in the tabs and at its address", async () => {
  const fake = signedInAs({ ...VIEWER, role: "analyst" });

  renderAt(PAGE);

  expect(
    await screen.findByText("Only Owners and Admins can see the AI usage."),
  ).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "AI usage" })).toBeNull();
  expect(asked(fake.requests)).toBeUndefined();
});
```

In `frontend/src/ui/format.test.ts`, replace:
```ts
import { expect, test } from "vitest";
import { formatClock, formatDateTime, formatTime, formatUsd } from "./format";

test("a moment is written with its date and its time", () => {
```
with:
```ts
import { expect, test } from "vitest";
import { formatClock, formatDateTime, formatDay, formatTime, formatUsd } from "./format";

test("a moment is written with its date and its time", () => {
```

In `frontend/src/ui/format.test.ts`, replace:
```ts
  expect(formatClock("2026-10-03T14:05:09Z")).toMatch(/:05:09/);
});
```
with:
```ts
  expect(formatClock("2026-10-03T14:05:09Z")).toMatch(/:05:09/);
});

test("a UTC day is written as that day, whatever the time zone", () => {
  expect(formatDay("2026-10-04")).toMatch(/4/);
  expect(formatDay("2026-10-04")).not.toMatch(/3/);
});
```

`frontend/src/usage/usage.test.ts`:
```ts
import { expect, test } from "vitest";
import { dailyCost, lastDays, periodFrom } from "./usage";

const NOW = Date.parse("2026-10-04T23:30:00Z");

test("the last days are UTC days, oldest first, ending today", () => {
  expect(lastDays(3, NOW)).toEqual(["2026-10-02", "2026-10-03", "2026-10-04"]);
});

test("each day of the period has its cost, and a day without calls costs nothing", () => {
  const usage = {
    days: [
      { day: "2026-10-04", calls: 3, input_tokens: 2700, output_tokens: 900, cost_usd: "0.0009" },
      { day: "2026-10-02", calls: 1, input_tokens: 900, output_tokens: 300, cost_usd: "0.0003" },
    ],
    totals: { calls: 4, input_tokens: 3600, output_tokens: 1200, cost_usd: "0.0012" },
  };

  expect(dailyCost(usage, 3, NOW)).toEqual([
    { day: "2026-10-02", cost: 0.0003 },
    { day: "2026-10-03", cost: 0 },
    { day: "2026-10-04", cost: 0.0009 },
  ]);
});

test("the period is 7, 30 or 90 days, and 30 unless the address says otherwise", () => {
  expect(["7", "90", "30", "45", null].map(periodFrom)).toEqual([7, 90, 30, 30, 30]);
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && pnpm exec vitest run src/usage src/pages/org/Usage.test.tsx src/ui/format.test.ts`
Expected: FAIL. `usage.test.ts` stops on `Failed to resolve import "./usage"`, the 4 page tests fail as there's no usage page, and `formatDay` isn't a function.

- [ ] **Step 3: Write the usage data, the chart and the page**

In `frontend/src/app/routes.tsx`, replace:
```tsx
import { OrgSettings } from "../pages/org/OrgSettings";
import { Uploads } from "../pages/org/Uploads";
import { Orgs } from "../pages/Orgs";
import { Settings } from "../pages/Settings";
```
with:
```tsx
import { OrgSettings } from "../pages/org/OrgSettings";
import { Uploads } from "../pages/org/Uploads";
import { Usage } from "../pages/org/Usage";
import { Orgs } from "../pages/Orgs";
import { Settings } from "../pages/Settings";
```

In `frontend/src/app/routes.tsx`, replace:
```tsx
          { path: "members", element: <Members /> },
          { path: "audit", element: <AuditLog /> },
          { path: "settings", element: <OrgSettings /> },
        ],
```
with:
```tsx
          { path: "members", element: <Members /> },
          { path: "audit", element: <AuditLog /> },
          { path: "usage", element: <Usage /> },
          { path: "settings", element: <OrgSettings /> },
        ],
```

In `frontend/src/pages/org/OrgLayout.tsx`, replace:
```tsx
import { ApiError } from "../../api/problem";
import { useOrgQuery } from "../../orgs/org";
import { canReadAudit } from "../../orgs/permissions";
import { roleLabel } from "../../orgs/roles";
import { ErrorNotice } from "../../ui/ErrorNotice";
```
with:
```tsx
import { ApiError } from "../../api/problem";
import { useOrgQuery } from "../../orgs/org";
import { canReadAudit, canReadUsage } from "../../orgs/permissions";
import { roleLabel } from "../../orgs/roles";
import { ErrorNotice } from "../../ui/ErrorNotice";
```

In `frontend/src/pages/org/OrgLayout.tsx`, replace:
```tsx
            <NavLink to="members">Members</NavLink>
            {canReadAudit(org.data.role) && <NavLink to="audit">Audit log</NavLink>}
            <NavLink to="settings">Settings</NavLink>
          </nav>
```
with:
```tsx
            <NavLink to="members">Members</NavLink>
            {canReadAudit(org.data.role) && <NavLink to="audit">Audit log</NavLink>}
            {canReadUsage(org.data.role) && <NavLink to="usage">AI usage</NavLink>}
            <NavLink to="settings">Settings</NavLink>
          </nav>
```

`frontend/src/pages/org/Usage.tsx`:
```tsx
import { useSearchParams } from "react-router";
import { useOrg } from "../../orgs/org";
import { canReadUsage } from "../../orgs/permissions";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { formatDay, formatNumber, formatUsd } from "../../ui/format";
import { Loading } from "../../ui/Loading";
import { usePageTitle } from "../../ui/usePageTitle";
import { PERIODS, dailyCost, periodFrom, useUsage } from "../../usage/usage";
import { UsageChart } from "./UsageChart";

/** `/app/orgs/:org/usage`: what the AI did and cost, by UTC day, for Owners and Admins. */
export function Usage() {
  const org = useOrg();
  usePageTitle(`AI usage · ${org.name}`);
  const [search, setSearch] = useSearchParams();
  const days = periodFrom(search.get("days"));
  const allowed = canReadUsage(org.role);
  const usage = useUsage(org.id, days, allowed);
  return (
    <main id="main" className="page">
      <h1>AI usage</h1>
      {!allowed && <p>Only Owners and Admins can see the AI usage.</p>}
      {allowed && (
        <div className="filters">
          <div className="field">
            <label htmlFor="usage-period">Period</label>
            <select
              id="usage-period"
              value={days}
              onChange={(event) => setSearch({ days: event.target.value })}
            >
              {PERIODS.map((period) => (
                <option key={period} value={period}>
                  Last {period} days
                </option>
              ))}
            </select>
          </div>
        </div>
      )}
      {usage.isLoading && <Loading />}
      {usage.isError && <ErrorNotice error={usage.error} />}
      {usage.data !== undefined && usage.data.totals.calls === 0 && (
        <p className="muted">No AI calls in the last {days} days.</p>
      )}
      {usage.data !== undefined && usage.data.totals.calls > 0 && (
        <>
          <UsageChart series={dailyCost(usage.data, days, Date.now())} />
          <table className="table">
            <caption className="visually-hidden">AI calls by day, newest first</caption>
            <thead>
              <tr>
                <th scope="col">Day (UTC)</th>
                <th scope="col">Calls</th>
                <th scope="col">Input tokens</th>
                <th scope="col">Output tokens</th>
                <th scope="col">Cost</th>
              </tr>
            </thead>
            <tbody>
              {usage.data.days.map((day) => (
                <tr key={day.day}>
                  <td className="date">{formatDay(day.day)}</td>
                  <td>{formatNumber(day.calls)}</td>
                  <td>{formatNumber(day.input_tokens)}</td>
                  <td>{formatNumber(day.output_tokens)}</td>
                  <td>{formatUsd(day.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th scope="row">Total</th>
                <td>{formatNumber(usage.data.totals.calls)}</td>
                <td>{formatNumber(usage.data.totals.input_tokens)}</td>
                <td>{formatNumber(usage.data.totals.output_tokens)}</td>
                <td>{formatUsd(usage.data.totals.cost_usd)}</td>
              </tr>
            </tfoot>
          </table>
        </>
      )}
    </main>
  );
}
```

`frontend/src/pages/org/UsageChart.tsx`:
```tsx
import { useId } from "react";
import { formatDay, formatUsd } from "../../ui/format";

const BAR = 10;
const STEP = 14;
const HEIGHT = 120;

/**
 * The AI's cost per UTC day as bars in ink (the owner's decision, Plan 6b); a day without calls is
 * a gap. The table below it holds the same numbers for screen readers.
 */
export function UsageChart({ series }: { series: { day: string; cost: number }[] }) {
  const captionId = useId();
  const highest = Math.max(...series.map((point) => point.cost));
  const width = series.length * STEP;
  const first = series[0]?.day ?? "";
  const last = series.at(-1)?.day ?? "";
  return (
    <figure className="usage-chart" aria-labelledby={captionId}>
      <svg viewBox={`0 0 ${width} ${HEIGHT + 2}`} aria-hidden="true" preserveAspectRatio="none">
        {series.map((point, index) =>
          point.cost > 0 ? (
            <rect
              key={point.day}
              className="bar"
              x={index * STEP + (STEP - BAR) / 2}
              y={HEIGHT - Math.max(2, (point.cost / highest) * HEIGHT)}
              width={BAR}
              height={Math.max(2, (point.cost / highest) * HEIGHT)}
            />
          ) : null,
        )}
        <line className="baseline" x1={0} y1={HEIGHT + 1} x2={width} y2={HEIGHT + 1} />
      </svg>
      <figcaption id={captionId}>
        AI cost per UTC day, {formatDay(first)} to {formatDay(last)}. The busiest day cost{" "}
        {formatUsd(String(highest))}.
      </figcaption>
    </figure>
  );
}
```

In `frontend/src/styles.css`, replace:
```css
}

/* Lists that load a page at a time, narrowed by filters above them */

```
with:
```css
}

/* AI usage: cost per day in ink (amber stays the detectors') */

.usage-chart {
  margin: 0 0 1.5rem;
}

.usage-chart svg {
  display: block;
  width: 100%;
  height: 8rem;
}

.usage-chart .bar {
  fill: var(--ink);
}

.usage-chart .baseline {
  stroke: var(--grid);
  stroke-width: 1;
}

.usage-chart figcaption {
  margin-top: 0.5rem;
  font-size: 0.92rem;
  color: var(--graphite);
}

.table tfoot th,
.table tfoot td {
  border-top: 1px solid var(--grid);
  font-weight: 650;
}

/* Lists that load a page at a time, narrowed by filters above them */

```

In `frontend/src/ui/format.ts`, replace:
```ts
  return CLOCK.format(new Date(iso));
}
```
with:
```ts
  return CLOCK.format(new Date(iso));
}

const UTC_DAY = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: "UTC" });

/** A UTC day such as "2026-10-04", written as a date that never shifts with the time zone. */
export function formatDay(day: string): string {
  return UTC_DAY.format(new Date(`${day}T00:00:00Z`));
}
```

`frontend/src/usage/usage.ts`:
```ts
/** An organization's AI usage by UTC day (spec §7): calls, tokens and cost, with totals. */
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";

export type Usage = components["schemas"]["UsageOut"];

export const PERIODS = [7, 30, 90] as const;
const DAY_MS = 86_400_000;

/** The period in an address such as `?days=7`: 7, 30 or 90 days, and 30 otherwise. */
export function periodFrom(value: string | null): number {
  const days = Number(value);
  return PERIODS.some((period) => period === days) ? days : 30;
}

/** The last `days` UTC days, oldest first, ending today, as `YYYY-MM-DD`. */
export function lastDays(days: number, now: number): string[] {
  const today = Math.floor(now / DAY_MS) * DAY_MS;
  return Array.from({ length: days }, (_, index) =>
    new Date(today - (days - 1 - index) * DAY_MS).toISOString().slice(0, 10),
  );
}

/** Every day of the period with its cost, oldest first; the API lists only days with calls. */
export function dailyCost(
  usage: Usage,
  days: number,
  now: number,
): { day: string; cost: number }[] {
  const costs = new Map(usage.days.map((day) => [day.day, Number(day.cost_usd)]));
  return lastDays(days, now).map((day) => ({ day, cost: costs.get(day) ?? 0 }));
}

export function useUsage(orgId: string, days: number, enabled: boolean) {
  return useQuery({
    queryKey: ["usage", orgId, days],
    enabled,
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/usage", {
          params: { path: { org_id: orgId }, query: { days } },
        }),
      ),
  });
}
```

- [ ] **Step 4: Run the tests again**

Run: `cd frontend && pnpm exec vitest run src/usage src/pages/org/Usage.test.tsx src/ui/format.test.ts`
Expected: `11 passed`.

- [ ] **Step 5: Check everything**

Run: `cd frontend && pnpm lint && pnpm exec tsc --noEmit && pnpm test && pnpm build && pnpm check:csp`
Expected: lint and types are clean, `188 passed`, and the build passes the CSP check.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(web): AI usage, for owners and admins: cost per UTC day drawn in ink, then listed with totals" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 10: The decisions in the spec, and the owner's walk through triage

**Files:**
- Create: `docs/samples/port-scan.log`, `backend/tests/unit/domain/test_sample_flow_log.py`
- Modify: `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (§10), `docs/runbooks/setup-and-deploy.md` (B11, B12, Part C), `README.md`

**Interfaces:**
- Consumes: the port scan detector (Plan 2), `parse_flow_log` (Plan 4b), and the landing page's sample scan (`frontend/src/landing/sampleScan.ts`).
- Produces: the sample flow log runbook B12 uploads, and a test that keeps it, the detector and the landing page in step.

- [ ] **Step 1: Write the sample flow log**

`docs/samples/port-scan.log` holds the landing page's example scan as a VPC flow log: 10.0.3.17 probing 150 TCP ports of 10.0.0.5 in four minutes, every attempt rejected, in the shuffled order of `SAMPLE_SCAN`. Write it with this one-off script, which repeats `sampleScan.ts`'s generator (save it outside the repository, for example as `sample.mjs` in your temp folder):

```js
import { writeFileSync } from "node:fs";

const WELL_KNOWN = [21, 22, 23, 25, 53, 80, 110, 111, 135, 139, 143, 389, 443, 445, 465, 587, 636, 873, 993, 995];

function seeded(seed) {
  let state = seed;
  return () => {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const random = seeded(2026);
const ports = new Set(WELL_KNOWN);
while (ports.size < 150) ports.add(1 + Math.floor(random() * 1023));
const order = [...ports];
for (let i = order.length - 1; i > 0; i -= 1) {
  const j = Math.floor(random() * (i + 1));
  [order[i], order[j]] = [order[j], order[i]];
}

const start = Date.parse("2026-10-01T12:00:00Z") / 1000;
const lines = order.map((port, i) => {
  const at = start + Math.floor((i * 240) / order.length);
  return `2 123456789012 eni-0f3a 10.0.3.17 10.0.0.5 ${41000 + i} ${port} 6 1 44 ${at} ${at + 1} REJECT OK`;
});
writeFileSync(process.argv[2], lines.join("\n") + "\n");
console.log(`${lines.length} lines; first ports ${order.slice(0, 5).join(", ")}`);
```

Run: `mkdir -p docs/samples && node <path to>/sample.mjs docs/samples/port-scan.log`
Expected: `150 lines; first ports 23, 466, 53, 615, 424`. The file is 13,780 bytes and starts with `2 123456789012 eni-0f3a 10.0.3.17 10.0.0.5 41000 23 6 1 44 1790856000 1790856001 REJECT OK`.

- [ ] **Step 2: Pin it to the detector**

`backend/tests/unit/domain/test_sample_flow_log.py`:
```python
"""The sample flow log runbook B12 uploads (docs/samples/port-scan.log) is analyzed into exactly
the finding the landing page shows: keep the three in step."""

from pathlib import Path

from nettriage.domain.detection.engine import detect
from nettriage.domain.detection.model import Severity
from nettriage.domain.parsing.vpc_flow_logs import parse_flow_log

SAMPLE = Path(__file__).parents[4] / "docs" / "samples" / "port-scan.log"


def test_the_sample_flow_log_is_the_landing_pages_port_scan() -> None:
    with SAMPLE.open("rb") as stream:
        parsed = parse_flow_log(stream)

    [finding] = detect(parsed.flows).findings

    assert (parsed.rows_parsed, parsed.rows_rejected) == (150, 0)
    assert finding.title == "Port scan of 10.0.0.5 from 10.0.3.17: 150 TCP ports in 5 minutes"
    assert finding.severity is Severity.HIGH
    assert finding.candidate_techniques == ("T1046",)
    assert len(finding.evidence) == 50
```

Run: `cd backend && uv run python -m pytest tests/unit/domain/test_sample_flow_log.py`
Expected: `1 passed`. (It checks data, not new code: it fails if the sample, the detector or the landing page's example drift apart.)

- [ ] **Step 3: Record the decisions, the runbook steps and the README**

In `README.md`, replace:
```markdown
> deploys, infrastructure as code, telemetry), the detection engine, the Postgres data
> foundation, sign-in with mandatory MFA, organizations, uploads and their analysis, triage,
> and AI explanations on Bedrock. In progress: the web app.

## Architecture
```
with:
```markdown
> deploys, infrastructure as code, telemetry), the detection engine, the Postgres data
> foundation, sign-in with mandatory MFA, organizations, uploads and their analysis, triage,
> and AI explanations on Bedrock. In progress: the web app, whose pages for organizations,
> uploads, findings, triage, the AI's explanations, the audit log and usage are built; the
> public demo is next.

## Architecture
```

In `README.md`, replace:
```markdown
- **Triage with optimistic concurrency**: a finding's status and assignee change only with `If-Match` naming the version the caller read, so two analysts can't silently overwrite each other (412 instead); every change and comment is in the finding's history and the audit log.
- **AI explanations with guardrails**: each upload's 20 most severe findings are explained by gpt-oss-20b on Amazon Bedrock from the finding's typed fields only. An answer that names an address, port or technique outside the data is refused, and every call is paid for in advance from a daily token budget per org and a $0.50 daily cap across all orgs, which fail closed. Analysts, Admins and Owners can re-run an explanation and rate it, and Owners and Admins see the AI's calls, tokens and cost per day.
- **A typed web app**: React and TypeScript, with an API client generated from the API's OpenAPI document, so CI fails when the two drift. It sends CloudFront's body hash, the CSRF token and idempotency keys on its own, and shows every API error with its reference.
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for each function.
- **Distributed rate limiting** with GCRA on DynamoDB, exact under concurrency ([ADR 0006](docs/adr/0006-gcra-rate-limiter.md)).
```
with:
```markdown
- **Triage with optimistic concurrency**: a finding's status and assignee change only with `If-Match` naming the version the caller read, so two analysts can't silently overwrite each other (412 instead); every change and comment is in the finding's history and the audit log.
- **AI explanations with guardrails**: each upload's 20 most severe findings are explained by gpt-oss-20b on Amazon Bedrock from the finding's typed fields only. An answer that names an address, port or technique outside the data is refused, and every call is paid for in advance from a daily token budget per org and a $0.50 daily cap across all orgs, which fail closed. Analysts, Admins and Owners can re-run an explanation and rate it, and Owners and Admins see the AI's calls, tokens and cost per day.
- **A typed web app**: React and TypeScript, with an API client generated from the API's OpenAPI document, so CI fails when the two drift. It sends CloudFront's body hash, the CSRF token and idempotency keys on its own, and shows every API error with its reference. Files go from the browser straight to S3 with their fingerprint and progress; findings open most severe first; a port scan's evidence is drawn on a map of the host's ports; and triage names the version it read, so a newer change is shown, never overwritten.
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for each function.
- **Distributed rate limiting** with GCRA on DynamoDB, exact under concurrency ([ADR 0006](docs/adr/0006-gcra-rate-limiter.md)).
```

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
   authenticator app. You land on **Your organizations**.
3. Under **Create an organization**, type `Web Test` and click **Create organization**. The
   organization opens on its **Members** page, with you as its Owner.
4. Under **Invitations**, type an email address that has no NetTriage account yet, keep
   **Viewer**, and click **Invite**. The invitation's link appears once; click **Copy link**.
5. Optional, to try accepting it: open a private window, paste the link, and sign up with that
   email address (it needs a password and an authenticator app of its own). You land on
   **Web Test**'s **Members** page as a Viewer, with nothing you can change. Close the private
   window. If you skip this, click **Revoke** next to the invitation.
6. Click the **Settings** tab. Rename the organization to `Web Test (2) & Bob's` and click
   **Save**: the new name shows at the top at once. (The punctuation checks that the name gets
```
with:
```markdown
   authenticator app. You land on **Your organizations**.
3. Under **Create an organization**, type `Web Test` and click **Create organization**. The
   organization opens on its **Findings** page, which has none yet, with you as its Owner. Click
   the **Members** tab.
4. Under **Invitations**, type an email address that has no NetTriage account yet, keep
   **Viewer**, and click **Invite**. The invitation's link appears once; click **Copy link**.
5. Optional, to try accepting it: open a private window, paste the link, and sign up with that
   email address (it needs a password and an authenticator app of its own). You land on
   **Web Test**'s **Findings** page as a Viewer; on **Members** there's nothing you can change.
   Close the private window. If you skip this, click **Revoke** next to the invitation.
6. Click the **Settings** tab. Rename the organization to `Web Test (2) & Bob's` and click
   **Save**: the new name shows at the top at once. (The punctuation checks that the name gets
```

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
8. Click **Account settings** at the top: your email address shows. Click **Sign out**: you're
   signed out of NetTriage and of Cognito, and the landing page offers **Sign in or sign up**.

## Part C: when things go wrong
```
with:
```markdown
8. Click **Account settings** at the top: your email address shows. Click **Sign out**: you're
   signed out of NetTriage and of Cognito, and the landing page offers **Sign in or sign up**.

### B12. Triage in the web app
Uploads, findings, triage, the AI explanation, the audit log and the AI usage (Plan 6b). Until AWS
lifts the new account's Bedrock limits, the AI panel says the AI service was busy, or that the AI
is working on it; that's expected.
1. Sign in as in B11 step 2, and create an organization named `Triage Test`. It opens on
   **Findings**, which says **No findings yet.** with a link to **Upload a flow log**.
2. Click **Upload a flow log**. Under **Flow log file**, click **Choose File** and pick
   `docs/samples/port-scan.log` from your copy of the repository: the landing page's example, 150
   rejected TCP probes from 10.0.3.17 to 10.0.0.5. Click **Upload**. You see **Reading the
   file…**, a progress bar, then **Uploaded port-scan.log.** The file is listed as **Analyzing**,
   and within about a minute it turns **Analyzed** with 150 rows, without reloading the page.
3. Click **Findings** on its row. One finding: **High**, `Port scan of 10.0.0.5 from 10.0.3.17:
   150 TCP ports in 5 minutes`, **Open**, **Unassigned**. Set **Severity** to **Low**: **No
   findings match these filters.** Click **Clear filters**.
4. Click the finding's title, and check its page:
   - **AI explanation**: an explanation labeled **AI-generated** with its model and prompt
     version, or, while Bedrock is limited, **The AI service was busy. Try again later.**
   - **Evidence**: the port map with 50 amber cells, captioned `Ports 0 to 1023 of 10.0.0.5. The
     scan probed 150 ports; the evidence keeps a sample.`, then the 50 flows, all `REJECT`.
   - **ATT&CK techniques**: **T1046 Network Service Discovery**, from the detector; the link
     opens attack.mitre.org in a new tab.
5. Under **Triage**, set **Status** to **Investigating** and **Assignee** to yourself, and click
   **Save changes**. **Activity** gains two lines: you changed the status from Open to
   Investigating, and you took it on.
6. Under **Add a comment**, type `Checking with the owner of 10.0.3.17.` and click **Comment**. It
   joins the activity, as written.
7. Optional, to see that a change never overwrites a newer one: open the same page in a second
   tab, set **Status** to **Resolved** there and save. Back in the first tab, choose **False
   positive** and save. The first tab says someone changed the finding while you had it open,
   and now shows **Resolved**.
8. Click **Audit log**: the upload, the status change, the assignment and the comment, newest
   first, each in words with its code beneath. Click **AI usage**: the AI's calls and cost per
   UTC day, or **No AI calls in the last 30 days.** until Bedrock answers.
9. Delete the organization as in B11 steps 6 and 7, typing `Triage Test`.

## Part C: when things go wrong
```

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
   | `provider_denied` | Bedrock refused the worker: the $5 budget action ran (next section), or the role lacks a permission. Send Claude the time |
   | `provider_rejected` | Bedrock refused the request itself; send Claude the time |
   | `provider_throttled`, `provider_timeout`, `provider_unavailable` | Bedrock failed three times in a row. Ask for the explanation again as in B10 step 2; the app's button comes in Plan 6. If every finding fails with `provider_throttled`, see "Bedrock refuses every call" below |
   | `checks_failed` (with `status: "invalid_output"`) | The model's answer failed the checks twice, so nothing was added to the finding. Send Claude the finding's `id` |

```
with:
```markdown
   | `provider_denied` | Bedrock refused the worker: the $5 budget action ran (next section), or the role lacks a permission. Send Claude the time |
   | `provider_rejected` | Bedrock refused the request itself; send Claude the time |
   | `provider_throttled`, `provider_timeout`, `provider_unavailable` | Bedrock failed three times in a row. Ask for the explanation again with **Explain again** on the finding's page (B12 step 4), or as in B10 step 2. If every finding fails with `provider_throttled`, see "Bedrock refuses every call" below |
   | `checks_failed` (with `status: "invalid_output"`) | The model's answer failed the checks twice, so nothing was added to the finding. Send Claude the finding's `id` |

```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
- `/invite`: reads the token from the URL fragment, signs the user in if needed, then accepts. The token leaves the address bar at once and waits in the tab's `sessionStorage` while the person signs in (Plan 6a).
- `/app`: an org switcher (the list of the person's organizations, which the product name in the top bar returns to), plus onboarding (create an org or accept an invitation).
- `/app/orgs/:org/uploads`: the uploads list and an upload dialog with progress. The browser computes the file's SHA-256 before requesting a slot.
- `/app/orgs/:org/findings`: a table with filters (severity, status, detector, upload) and sorting by severity or newest, done by the API (the owner's decision; Plan 6b adds `sort` to `GET …/findings`).
- `/app/orgs/:org/findings/:id`:
  - summary and metrics,
  - an evidence table,
  - ATT&CK techniques, linking to attack.mitre.org,
  - an AI explanation panel with the "AI-generated" label, model and prompt version, feedback and re-run,
  - an activity timeline with comments,
  - status and assignee controls.
- `/app/orgs/:org/members`: members, roles, invitations. An invitation's link is shown once, ready to copy.
- `/app/orgs/:org/settings`: rename (Owner, Admin), leave (anyone), and delete after typing the org's name (Owner) (Plan 6a).
- `/app/orgs/:org/audit` and `/app/orgs/:org/usage`: Owner and Admin only.
- `/app/settings`: account settings: who is signed in, and "sign out everywhere".

```
with:
```markdown
- `/invite`: reads the token from the URL fragment, signs the user in if needed, then accepts. The token leaves the address bar at once and waits in the tab's `sessionStorage` while the person signs in (Plan 6a).
- `/app`: an org switcher (the list of the person's organizations, which the product name in the top bar returns to), plus onboarding (create an org or accept an invitation).
- `/app/orgs/:org/uploads`: the uploads list, and an upload panel with progress. The panel sits on the page, since the app has no modal dialogs (Plan 6a). The browser computes the file's SHA-256 before requesting a slot, and puts the file in S3 with an `XMLHttpRequest`, the one browser API that reports upload progress. The list checks back every few seconds while an upload is being analyzed (Plan 6b).
- `/app/orgs/:org/findings`, where an organization opens (the owner's decision, Plan 6b): a table with filters (severity, status, detector, upload) and sorting, done by the API with `sort`. It opens most severe first, then newest (the owner's decision); newest first is one choice away. The filters and the order live in the address.
- `/app/orgs/:org/findings/:id`:
  - summary and metrics,
  - an evidence table; a port scan of one host also draws its evidence on the port map, captioned as the sample it is (at most 50 flows; the owner's decision, Plan 6b),
  - ATT&CK techniques, linking to attack.mitre.org, each listed once with who named it (the detector, the AI or both), and MITRE's notice,
  - an AI explanation panel with the "AI-generated" label, model and prompt version, feedback and re-run. After a re-run it checks back every few seconds for five minutes. An answer whose shape doesn't match output schema v1 isn't shown,
  - an activity timeline with comments,
  - status and assignee controls, saved together with `If-Match`. A 412 shows the newer version and says someone changed the finding meanwhile.
- `/app/orgs/:org/members`: members, roles, invitations. An invitation's link is shown once, ready to copy.
- `/app/orgs/:org/settings`: rename (Owner, Admin), leave (anyone), and delete after typing the org's name (Owner) (Plan 6a).
- `/app/orgs/:org/audit` and `/app/orgs/:org/usage`: Owner and Admin only. Usage draws the cost per UTC day as ink bars above the table of days and totals, for the last 7, 30 or 90 days (the owner's decision, Plan 6b).
- `/app/settings`: account settings: who is signed in, and "sign out everywhere".

```

- [ ] **Step 4: Check everything**

Run: `just lint test web-check`
Expected: lint passes, every backend test passes (the DynamoDB Local test is skipped locally), and `web-check` passes with `188 passed`.

- [ ] **Step 5: Commit**

```bash
git add docs README.md backend/tests/unit/domain/test_sample_flow_log.py
git commit -m "docs: Plan 6b in the spec, runbook B12 (triage in the web app) with a sample flow log, and the README" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 11 (Claude, then the owner): Pull request, deploy and a walk through triage

- [ ] **Step 1 (Claude):**
  - Run `just lint test tools-test edge-test web-check tf-check pin-check cloud-check detection-report build-lambda`; everything must pass.
  - Take screenshots of the new pages (findings, a finding, uploads, the audit log, the usage) at desktop and phone width against a stand-in API, and fix what looks wrong.
  - Get a final review of the whole branch, fix what it finds, then push `plan-6b/web-findings`.
  - Open the PR, watch CI, and request a Copilot review.
- [ ] **Step 2 (owner):** Review the PR, then squash-merge it.
- [ ] **Step 3 (owner):** Runbook B2 (`just deploy-dev`). Expected:
  - `Database migrated.`, with no new logins;
  - `Plan: 0 to add, 3 to change, 0 to destroy`: the `api`, `analyze` and `triage` functions get the new code;
  - the web build is published;
  - every smoke test `PASS`es.
- [ ] **Step 4 (owner):** Runbook B12. Expected: upload the sample, follow it to **Analyzed**, open its finding (**High**, the port map with 50 amber cells), triage it, comment, and read the audit log and the usage, all from the app's pages.

## Plan 6b is done when

- [ ] `just lint test web-check` passes locally and CI passes.
- [ ] The PR is merged through review, with every thread resolved.
- [ ] Runbook B12 works on dev.

## Spec coverage of this plan

| Spec | Covered here |
|---|---|
| §2.2 3: upload a flow log from the browser | Task 3 |
| §2.2 4: findings with filters and sorting | Tasks 1 and 4 |
| §2.2 5: a finding's evidence and ATT&CK techniques | Task 5 |
| §2.2 6: triage (status, assignee, comments, history) | Task 6 |
| §2.2 7: AI explanations, ratings and re-runs; the audit log and usage | Tasks 7 to 9 |
| §2.2 8, §4.3: the public demo | Plan 6c |
| §6.4 what each role may do, in the UI | Tasks 2, 3 and 6 to 9 |
| §7 the findings `sort` | Task 1 |
| §7 `If-Match` and 412, `Idempotency-Key` on uploads and comments | Tasks 3 and 6 |
| §8.4 AI output as plain text, checked before it's shown | Task 7 |
| §10 `/app/orgs/:org/uploads`, `/findings`, `/findings/:id`, `/audit`, `/usage` | Tasks 3 to 9; Task 10 amends §10 |
| §10 security, states, accessibility, the look | Tasks 2 to 9 |
| §11.4 Playwright smoke tests after each deploy | Plan 7 (the owner's decision) |
