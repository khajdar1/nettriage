# NetTriage Plan 6d: Findings at a Glance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the web app show, without opening anything, what is yours and where each organization's findings stand (the owner's request, 2026-10-05):
- **a home** that lists the unresolved findings assigned to you in all your organizations, worst first, then a card per organization;
- **findings at a glance** above an organization's list: the unresolved by severity, quick views, every finding by status and the last upload, each number linking to exactly the findings it counts. The list now defaults to **Unresolved** (Open or Investigating) and gains an **Assignee** filter;
- **a summary strip** under a finding's title: its status, assignee, when it was detected, its window, its upload and the AI's verdict;
- **an uploads column** of findings ("6 findings, worst High"), and times that read as how long ago they were.

**Architecture:**
- **The API gains three things, and no migration.**
  - The findings list takes several statuses, `assignee` (`me` or `none`) and `since`, and each finding carries `ai_status`, its latest analysis's status, from a correlated subquery.
  - Uploads carry `findings` and `worst_severity`, from subqueries.
  - `GET …/overview` counts an organization's findings with `count(*) FILTER (…)` in one tenant transaction, with its member count and latest upload.

  The OpenAPI document and the typed client are regenerated after each.
- **Small shared pieces come first:**
  - `<Ago>`: a time as "2 h ago", exact on hover;
  - `<Person>` and `<Unassigned>`: initials in a disc beside a name;
  - `<AiMark>`: whether the AI explained a finding;
  - `<FindingSubline>`: detector, flow and AI mark under a title;
  - `useOverview`.

  The home, the findings page, the finding's page and the uploads page are built from them.
- **The home reads at most three organizations' assigned findings** with `useQueries`, under each organization's findings query key, so triage refreshes them as it refreshes the list. It merges them worst first.
- **The glance reads the overview, whose numbers never change with the filters.** A count's link replaces the other filters with its own view (keeping the order), so the list shows exactly what was counted. The status track is SVG with its geometry in attributes, as the CSP allows no inline styles.
- **Unresolved is the absence of `status`** in the address. `apiQuery(filters, now)` turns the address's filters into the API's: Unresolved becomes `status=open&status=investigating`, Any status sends none, and `new=day` becomes `since` 24 hours ago.

**Tech Stack:** React 19, TypeScript 6 (strict), Vite 8 · React Router 8, TanStack Query 5, openapi-fetch 0.17, openapi-typescript 7 · Vitest 5, Testing Library, user-event · FastAPI, SQLAlchemy Core and Postgres 17.

**Spec:** `docs/superpowers/specs/2026-10-05-nettriage-glance-design.md` (approved by the owner on 2026-10-05). It amends `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (revision 2) §7 and §10, and Task 9 copies its decisions into them. Read both alongside this plan; "glance §" refers to the first, "§" to the second.

**Plan series:**
- Plan 6 ("frontend") had three parts (the owner's decision, 2026-10-03): 6a, the foundation and organizations (#15, restyled in #23); 6b, uploads, findings, triage and the AI (#24); 6c, the public demo, once AWS lifts the Bedrock limits.
- **6d (this plan) answers the owner's request after walking through B12:** a web app that is "more practical and prettier", with more detail.
- Plan 5d (the evals) and 6c wait for Bedrock. Plan 7 adds the Playwright smoke tests.

**Branch:** `plan-6d/glance`, from `main` at `bdfc757` or later; it already holds the glance spec (`048ffc3`).

## Global Constraints

- **Stack.** Node 24 with pnpm 12. TypeScript strict, with `noUncheckedIndexedAccess`. ESLint (typescript-eslint strict) and Prettier (print width 100) pass. Python 3.14 with mypy `--strict` and Ruff. Work test-first.
- **Commands on this Windows machine.**
  - Run Python tools as modules (`uv run python -m …`). Frontend commands run in `frontend/` with `pnpm`.
  - Backend tests that touch the database need the local Postgres: run `just db-up` once, then prefix `uv run python -m pytest` in `backend/` with `NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)"`. `just test` does both for the whole suite.
  - Vitest's path filters ignore case on Windows, so a folder is named with its trailing slash (`src/pages/org/finding/`, not `finding`, which also matches `Findings.test.tsx`).
- **Type checks** use `pnpm exec tsc --noEmit`, never `tsc -b`, which writes a build cache (`tsconfig.tsbuildinfo`) that must not be committed.
- **The contract.** After a task changes the API, `just openapi` regenerates `frontend/openapi.json` and `frontend/src/api/schema.ts`, and `pnpm check:api` must pass. Never edit either by hand.
- **No migration.** Every new number is a query over existing tables and indexes. An organization holds at most a few thousand findings (§5.7).
- **Words (glance §2).**
  - **Unresolved** means Open or Investigating.
  - **Yours** means assigned to the signed-in person.
  - **New in the last day** means detected (created) in the last 24 hours, whatever the status.
- **CSP (§6.7, §10).** No inline scripts or styles (`pnpm check:csp`): no `style` props (lint forbids them). Anything sized by data is SVG with its geometry in attributes. Every style lives in `src/styles.css`.
- **The look (§10, "Sweep"; glance §5).**
  - Amber (`--signal`) marks only what a detector found (the severity bars and tiles) and the logo; a test enforces it.
  - The status track, quick views, initials and buttons are ink. The AI's mark and verdict are teal.
  - Counts are tabular figures in expanded Archivo.
- **Accessibility (§10, glance §6).** WCAG 2.2 AA.
  - One `h1` per page; the home's sections are `h2`.
  - Each count's link is named by its meaning ("2 unresolved Critical findings").
  - The status track is hidden from screen readers; its legend carries the numbers.
  - The quick view in use has `aria-current="true"`.
  - Initials are hidden from screen readers beside the name.
  - A relative time keeps the exact time in `dateTime` and `title`.
  - Tests find elements by role and name.
- **Times (glance §3.5)** read as how long ago they were, through `<Ago>`, everywhere but the audit log, which keeps exact times.
- **Owner-only commands.** Claude never runs `aws login`, `just bootstrap`, `just store-*`, `just pause-*`, `just resume-*` or `just deploy-*`.
- **Commits.** Use Conventional Commits. Every commit made by Claude ends with the trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Decisions this plan makes

1. **The owner's decisions (2026-10-05):**
   - more detail where it matters most: **my work** and **findings at a glance**;
   - the design was left to Claude ("Do some thinking, don't depend on me for design"), and the mockup and the spec were approved.
2. **The list's filters (API):**
   - `status` is a list of at most 4 (`Query(max_length=4)`), so a single value stays valid;
   - `assignee=me` means the caller, and `assignee=none` means nobody;
   - `since` is a moment, and the findings detected at or after it are kept;
   - each finding's `ai_status` is its latest analysis's, newest by `updated_at`, then `id`.
3. **Uploads count their findings** as the findings kept (`findings`) and the most severe of them (`worst_severity`). The insert that creates an upload answers `0` and `null`, without querying.
4. **The overview (API) is the organization's whole**, whatever the list's filters:
   - one read per organization, under `findings:read`, in one tenant transaction;
   - `unresolved_mine` depends on who asks, so it is never shared between people.
5. **Every count links to exactly the findings it counts.** A severity tile, quick view or legend entry replaces the other filters with its own view and keeps the order. Every link to an upload's findings asks for any status, so its count matches the list: the uploads column, the glance's last upload, the strip and the Details link.
6. **Unresolved is the default** and is `status` absent from the address:
   - "Any status" is `status=any`, and Clear filters goes back to Unresolved;
   - an empty Unresolved list in an organization that has findings says **Nothing here is unresolved.** and offers every finding;
   - only an organization with none says **No findings yet.**
7. **`new=day` has no select.** A line above the list names it, with a way back to any time. `since` is worked out when each page is read, so a list kept open for long drops findings that turned a day old meanwhile.
8. **The home:**
   - at most 20 findings per organization, merged most severe first, then newest;
   - "N more in Acme" counts the overview's `unresolved_mine` minus those shown;
   - the queries sit under `["findings", orgId, …]`, so a triage change refreshes them;
   - the cards' **Open findings** is the primary button.
9. **People:**
   - initials come from the display name, or from the email before the @;
   - the signed-in person reads **You**;
   - nobody reads **Unassigned**, beside a dashed, empty disc.
10. **The status track** is SVG, its `x` and `width` set as percentages without a `viewBox`, so the false-positive hatching isn't stretched. An organization with no findings gets an empty track.
11. **The AI's verdict:**
    - "Agrees: High, medium confidence" or "Suggests Medium", from an answer that reads as output schema v1;
    - otherwise "Not explained yet" (no analysis), "Queued" (pending) or "No explanation";
    - teal only when the analysis succeeded.
12. **A finding's window** reads "14:00 to 14:05" in the person's local time, with the dates when it starts and ends on different days.
13. **Shared pieces move where several pages use them:**
    - the upload status labels, to `UPLOAD_STATUS_LABELS` in `src/uploads/uploads.ts`;
    - reading one upload, to `useUpload`, used by the list's upload line and the strip;
    - the unresolved and total counts, to `unresolvedCount` and `findingCount` in `src/orgs/overview.ts`.
14. **Triage marks the overview out of date**, as it already does the findings. Pages read the overview again when they open (no `staleTime`), so this keeps the rule simple: a change refreshes what it changes.
15. **The links that say "Your organizations" stay.** The home is still the organization switcher (§10).

## Review Focus

1. **Paging a filtered list.** "Show more" must keep the filters, or its second page lists findings the glance never counted. Tests: Task 6 `more findings load a page at a time, under the same filters`; Task 1 `test_filters_page_with_the_severity_order_and_its_cursor`.
2. **The overview failing while the list works.** The page must say why, and still list the findings. Test: Task 6 `an overview the API refuses says why, and the findings are still listed`.
3. **An organization with no findings at all.** The glance shows zeros and an empty track, not a drawing divided by zero. Tests: Task 6 `an organization with no findings shows its counts at zero, and an empty track`; Task 3 `test_a_new_organization_has_nothing_to_count`.
4. **A count's link while other filters are on.** It must list exactly what it counted, not the count narrowed again. Tests: Task 6 `a quick view with other filters beside it isn't the one in use` (its link drops the other filters) and `each severity's tile counts its unresolved findings and lists them`.
5. **Values that aren't real.** The page reads them as no filter. The API answers 422 to a bad status, assignee or moment, or to more than four statuses. Tests: Task 6 `an address with values that aren't real is read as no filter`; Task 1's new cases in `test_a_filter_outside_its_values_is_a_422`.

## Owner prerequisites

- **None to build or review.** The tests stand in for the API.
- **After the merge:**
  - runbook B2 (the deploy publishes the new web build and the `api` function's new reads; no migrations);
  - then B13, the new walk through the home and the glance.

## File map

| File | Responsibility | Task |
|---|---|---|
| `backend/src/nettriage/adapters/findings.py`, `entrypoints/api/routes/findings.py`, `finding_schemas.py` | several statuses, `assignee`, `since`, `ai_status` | 1 |
| `backend/src/nettriage/adapters/uploads.py`, `entrypoints/api/upload_schemas.py` | an upload's findings and worst severity | 2 |
| `backend/src/nettriage/adapters/overview.py`, `entrypoints/api/overview_schemas.py`, `routes/overview.py`, `app.py` | `GET …/overview` | 3 |
| `frontend/src/ui/Ago.tsx`, `Person.tsx`, `AiMark.tsx`, `format.ts`, `src/orgs/overview.ts` | times, people, the AI's mark and the overview query, shared by every page | 4 |
| `frontend/src/pages/Orgs.tsx`, `src/pages/home/`, `src/findings/assigned.ts`, `src/ui/FindingSubline.tsx` | the home | 5 |
| `frontend/src/findings/findings.ts`, `src/pages/org/Findings.tsx`, `FindingFilters.tsx`, `Glance.tsx` | findings at a glance, and the new filters | 6 |
| `frontend/src/pages/org/finding/SummaryStrip.tsx`, `FindingPage.tsx`, `Activity.tsx`, `Triage.tsx`, `FindingFacts.tsx` | a finding's summary strip | 7 |
| `frontend/src/uploads/uploads.ts`, `src/pages/org/Uploads.tsx` | uploads' findings and times | 8 |
| the M1 spec (§7, §10), `docs/runbooks/setup-and-deploy.md` (B11, B12, B13), `README.md` | the decisions, and the owner's walk through the glance | 9 |

Tests sit next to the code they test (`*.test.ts`, `*.test.tsx`), and backend tests under `backend/tests/`. A new file is given in full after "Create `path`:". An edit to an existing file is given as "In `file`, replace: … with: …", and each quoted passage appears exactly once in the file when its step runs.

---

### Task 1: The findings list takes several statuses, an assignee and a moment, and says how the AI did

**Files:**
- Modify: `backend/src/nettriage/adapters/findings.py`, `backend/src/nettriage/entrypoints/api/routes/findings.py`, `backend/src/nettriage/entrypoints/api/finding_schemas.py`, `frontend/src/findings/findings.ts`, `frontend/src/test/fixtures.ts`
- Regenerate: `frontend/openapi.json`, `frontend/src/api/schema.ts` (`just openapi`)
- Test: `backend/tests/api/test_finding_routes.py`

**Interfaces:**
- Consumes: the findings list route and `list_findings` (Plans 4b and 6b), `FindingSummary` and `FindingSummaryOut`.
- Produces:
  - `GET /api/v1/orgs/{org}/findings?status=…&status=…` (at most 4), `assignee=me|none`, `since=<moment>`, beside `severity`, `detector`, `upload`, `sort` and `cursor`;
  - `FindingFilters(statuses: tuple[FindingStatus, ...] = (), severity, detector, upload_id, assignee: Literal["me", "none"] | None, since: datetime | None)`;
  - the type `AiStatus` (`pending`, `succeeded`, `failed`, `skipped_budget`, `invalid_output`), and `ai_status: AiStatus | None` on `FindingSummary`, `FindingSummaryOut` and so `FindingOut`;
  - in the generated client: `status?: FindingStatus[]`, `assignee?: "me" | "none"`, `since?: string` and `ai_status`. Until Task 6, `useFindings` sends the one status it has as a list.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/api/test_finding_routes.py`, replace:
```python


def listed(client: TestClient, org: UUID, **params: str | int) -> list[str]:
    response = client.get(f"/api/v1/orgs/{org}/findings", params=params)
    assert response.status_code == 200, response.text
```
with:
```python


def listed(client: TestClient, org: UUID, **params: str | int | list[str]) -> list[str]:
    response = client.get(f"/api/v1/orgs/{org}/findings", params=params)
    assert response.status_code == 200, response.text
```

In `backend/tests/api/test_finding_routes.py`, replace:
```python
        {"limit": 101},
        {"sort": "oldest"},
    ],
)
```
with:
```python
        {"limit": 101},
        {"sort": "oldest"},
        {"status": ["open", "closed"]},
        {"status": ["open", "investigating", "resolved", "false_positive", "open"]},
        {"assignee": "bob"},
        {"since": "yesterday"},
    ],
)
```

In `backend/tests/api/test_finding_routes.py`, replace:
```python
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


```
with:
```python
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


def set_finding(database: Database, finding_id: str, **columns: object) -> None:
    """Changes a finding's columns as the database owner, outside any API rule."""
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    with database.admin.begin() as connection:
        connection.execute(
            text(f"UPDATE findings SET {assignments} WHERE id = :id"),  # noqa: S608
            {"id": finding_id, **columns},
        )


def test_the_list_is_narrowed_to_any_of_several_statuses(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    opened = add(database, org)
    investigating = add(database, org)
    resolved = add(database, org)
    set_finding(database, investigating, status="investigating")
    set_finding(database, resolved, status="resolved")

    assert listed(signed_in, org[0], status=["open", "investigating"]) == [investigating, opened]
    assert listed(signed_in, org[0], status="resolved") == [resolved]


def test_the_list_is_narrowed_to_the_callers_findings_or_to_unassigned_ones(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    mine = add(database, org)
    theirs = add(database, org)
    unassigned = add(database, org)
    with database.admin.begin() as connection:
        colleague = add_user(connection)
        add_member(connection, org[0], colleague, "analyst")
    set_finding(database, mine, assignee_id=org[1])
    set_finding(database, theirs, assignee_id=colleague)

    assert listed(signed_in, org[0], assignee="me") == [mine]
    assert listed(signed_in, org[0], assignee="none") == [unassigned]


def test_the_list_is_narrowed_to_findings_detected_since_a_moment(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    older = add(database, org)
    newer = add(database, org)
    set_finding(database, older, created_at="2026-09-01T00:00:00Z")

    assert listed(signed_in, org[0], since="2026-09-02T00:00:00Z") == [newer]


def test_filters_page_with_the_severity_order_and_its_cursor(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    low = add(database, org, severity="low")
    high = add(database, org, severity="high")
    resolved_critical = add(database, org, severity="critical")
    critical = add(database, org, severity="critical")
    set_finding(database, resolved_critical, status="resolved")

    pages = []
    cursor = None
    for _ in range(3):
        params: dict[str, str | int | list[str]] = {
            "sort": "severity",
            "status": ["open", "investigating"],
            "limit": 1,
        }
        if cursor is not None:
            params["cursor"] = cursor
        page = signed_in.get(f"/api/v1/orgs/{org[0]}/findings", params=params).json()
        pages.append([finding["id"] for finding in page["findings"]])
        cursor = page["next_cursor"]

    assert pages == [[critical], [high], [low]]
    assert cursor is None


def test_each_listed_finding_says_how_its_latest_ai_analysis_went(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    explained = add(database, org)
    unexplained = add(database, org)
    add_ai_analysis(
        database,
        org[0],
        explained,
        status="failed",
        at="2026-10-01T10:00:00Z",
        error_code="provider_throttled",
    )
    add_ai_analysis(
        database,
        org[0],
        explained,
        status="succeeded",
        at="2026-10-01T11:00:00Z",
        output='{"summary": "A scan from inside."}',
    )

    response = signed_in.get(f"/api/v1/orgs/{org[0]}/findings")

    statuses = {finding["id"]: finding["ai_status"] for finding in response.json()["findings"]}
    assert statuses == {explained: "succeeded", unexplained: None}
    detail = signed_in.get(f"/api/v1/orgs/{org[0]}/findings/{explained}").json()
    assert detail["ai_status"] == "succeeded"


```

- [ ] **Step 2: Run them to see them fail**

Run: `just db-up`, then `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/api/test_finding_routes.py`
Expected: FAIL: `8 failed, 21 passed`. The API takes one status (the last one sent), has no `assignee` or `since` (so they're ignored, not refused), and lists no `ai_status` (`KeyError: 'ai_status'`).

- [ ] **Step 3: Filter by several statuses, an assignee and a moment, and read the latest analysis's status**

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
type FindingSeverity = Literal["low", "medium", "high", "critical"]
type FindingSort = Literal["newest", "severity"]

# A finding's detail lists at most this many of its latest events.
```
with:
```python
type FindingSeverity = Literal["low", "medium", "high", "critical"]
type FindingSort = Literal["newest", "severity"]
type AiStatus = Literal["pending", "succeeded", "failed", "skipped_budget", "invalid_output"]

# A finding's detail lists at most this many of its latest events.
```

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
    "host(f.src_ip) AS src_ip, host(f.dst_ip) AS dst_ip, f.dst_port, f.protocol, "
    "lower(f.time_window) AS window_start, upper(f.time_window) AS window_end, "
    "f.assignee_id, f.version, f.created_at"
)

```
with:
```python
    "host(f.src_ip) AS src_ip, host(f.dst_ip) AS dst_ip, f.dst_port, f.protocol, "
    "lower(f.time_window) AS window_start, upper(f.time_window) AS window_end, "
    "f.assignee_id, f.version, f.created_at, "
    # How the finding's latest AI analysis went, picked as its detail picks it (Plan 6d).
    "(SELECT a.status FROM ai_analyses a WHERE a.finding_id = f.id "
    "ORDER BY a.updated_at DESC, a.id DESC LIMIT 1) AS ai_status"
)

```

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
    version: int
    created_at: datetime


```
with:
```python
    version: int
    created_at: datetime
    ai_status: AiStatus | None


```

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
@dataclass(frozen=True)
class FindingFilters:
    status: FindingStatus | None = None
    severity: FindingSeverity | None = None
    detector: str | None = None
    upload_id: UUID | None = None


```
with:
```python
@dataclass(frozen=True)
class FindingFilters:
    # Any of these statuses; none means any status (Plan 6d).
    statuses: tuple[FindingStatus, ...] = ()
    severity: FindingSeverity | None = None
    detector: str | None = None
    upload_id: UUID | None = None
    # `me`: assigned to the caller; `none`: unassigned (Plan 6d).
    assignee: Literal["me", "none"] | None = None
    # Created at or after this moment (Plan 6d).
    since: datetime | None = None


```

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
            text(
                f"SELECT {_SUMMARY} FROM findings f WHERE f.org_id = :org "  # noqa: S608
                "AND (CAST(:status AS text) IS NULL OR f.status = :status) "
                "AND (CAST(:severity AS text) IS NULL OR f.severity = :severity) "
                "AND (CAST(:detector AS text) IS NULL OR f.detector_id = :detector) "
                "AND (CAST(:upload AS uuid) IS NULL OR f.upload_id = :upload) "
                f"AND (CAST(:before_at AS timestamptz) IS NULL OR {after_cursor}) "
                f"ORDER BY {order} LIMIT :limit"
```
with:
```python
            text(
                f"SELECT {_SUMMARY} FROM findings f WHERE f.org_id = :org "  # noqa: S608
                "AND (CAST(:statuses AS text[]) IS NULL "
                "OR f.status = ANY(CAST(:statuses AS text[]))) "
                "AND (CAST(:severity AS text) IS NULL OR f.severity = :severity) "
                "AND (CAST(:detector AS text) IS NULL OR f.detector_id = :detector) "
                "AND (CAST(:upload AS uuid) IS NULL OR f.upload_id = :upload) "
                "AND (CAST(:assignee AS uuid) IS NULL OR f.assignee_id = :assignee) "
                "AND (NOT CAST(:unassigned AS boolean) OR f.assignee_id IS NULL) "
                "AND (CAST(:since AS timestamptz) IS NULL OR f.created_at >= :since) "
                f"AND (CAST(:before_at AS timestamptz) IS NULL OR {after_cursor}) "
                f"ORDER BY {order} LIMIT :limit"
```

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
            {
                "org": org_id,
                "status": filters.status,
                "severity": filters.severity,
                "detector": filters.detector,
                "upload": filters.upload_id,
                "before_at": before_at,
                "before_id": before_id,
```
with:
```python
            {
                "org": org_id,
                "statuses": list(filters.statuses) or None,
                "severity": filters.severity,
                "detector": filters.detector,
                "upload": filters.upload_id,
                "assignee": user_id if filters.assignee == "me" else None,
                "unassigned": filters.assignee == "none",
                "since": filters.since,
                "before_at": before_at,
                "before_id": before_id,
```

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
        version=row.version,
        created_at=row.created_at,
    )
```
with:
```python
        version=row.version,
        created_at=row.created_at,
        ai_status=row.ai_status,
    )
```

In `backend/src/nettriage/entrypoints/api/finding_schemas.py`, replace:
```python
from nettriage.adapters.findings import (
    AiAnalysis,
    Evidence,
    FindingDetail,
```
with:
```python
from nettriage.adapters.findings import (
    AiAnalysis,
    AiStatus,
    Evidence,
    FindingDetail,
```

In `backend/src/nettriage/entrypoints/api/finding_schemas.py`, replace:
```python
    version: int
    created_at: datetime

    @classmethod
```
with:
```python
    version: int
    created_at: datetime
    # How the latest AI analysis went, or null before the first one (Plan 6d).
    ai_status: AiStatus | None

    @classmethod
```

In `backend/src/nettriage/entrypoints/api/routes/findings.py`, replace:
```python
from __future__ import annotations

from typing import Annotated, Literal, cast
from uuid import UUID
```
with:
```python
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, cast
from uuid import UUID
```

In `backend/src/nettriage/entrypoints/api/routes/findings.py`, replace:
```python
    org_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("findings:read"))],
    status: FindingStatus | None = None,
    severity: FindingSeverity | None = None,
    detector: Annotated[str | None, Query(max_length=50)] = None,
    upload: UUID | None = None,
    sort: Literal["newest", "severity"] = "newest",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
```
with:
```python
    org_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("findings:read"))],
    status: Annotated[list[FindingStatus] | None, Query(max_length=4)] = None,
    severity: FindingSeverity | None = None,
    detector: Annotated[str | None, Query(max_length=50)] = None,
    upload: UUID | None = None,
    assignee: Literal["me", "none"] | None = None,
    since: datetime | None = None,
    sort: Literal["newest", "severity"] = "newest",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
```

In `backend/src/nettriage/entrypoints/api/routes/findings.py`, replace:
```python
) -> FindingsOut:
    """The org's findings, newest first, or most severe first and then newest with
    `sort=severity` (Plan 6b). Filters: status, severity, detector, upload. A cursor works only
    with the order it was made for."""
    before_severity: FindingSeverity | None = None
```
with:
```python
) -> FindingsOut:
    """The org's findings, newest first, or most severe first and then newest with
    `sort=severity` (Plan 6b). Filters, combined: any of the given statuses, severity, detector,
    upload, the assignee (`me` or `none`) and `since` a moment (Plan 6d). A cursor works only
    with the order it was made for."""
    before_severity: FindingSeverity | None = None
```

In `backend/src/nettriage/entrypoints/api/routes/findings.py`, replace:
```python
    elif cursor:
        before = decode_cursor(cursor)
    filters = FindingFilters(status=status, severity=severity, detector=detector, upload_id=upload)
    with org_rules(request, "The finding list"):
        found = list_findings(
```
with:
```python
    elif cursor:
        before = decode_cursor(cursor)
    filters = FindingFilters(
        statuses=tuple(status or ()),
        severity=severity,
        detector=detector,
        upload_id=upload,
        assignee=assignee,
        since=since,
    )
    with org_rules(request, "The finding list"):
        found = list_findings(
```

In `frontend/src/findings/findings.ts`, replace:
```ts

export function useFindings(orgId: string, filters: FindingFilters) {
  return useInfiniteQuery({
    queryKey: findingsKey(orgId, filters),
```
with:
```ts

export function useFindings(orgId: string, filters: FindingFilters) {
  // The API takes any of several statuses (Plan 6d).
  const { status, ...rest } = filters;
  return useInfiniteQuery({
    queryKey: findingsKey(orgId, filters),
```

In `frontend/src/findings/findings.ts`, replace:
```ts
          params: {
            path: { org_id: orgId },
            query: { ...filters, ...(pageParam ? { cursor: pageParam } : {}) },
          },
        }),
```
with:
```ts
          params: {
            path: { org_id: orgId },
            query: {
              ...rest,
              ...(status ? { status: [status] } : {}),
              ...(pageParam ? { cursor: pageParam } : {}),
            },
          },
        }),
```

In `frontend/src/test/fixtures.ts`, replace:
```ts
    version: 1,
    created_at: "2026-10-04T09:31:00Z",
    ...fields,
  };
```
with:
```ts
    version: 1,
    created_at: "2026-10-04T09:31:00Z",
    ai_status: null,
    ...fields,
  };
```

- [ ] **Step 4: Run the tests again**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/api/test_finding_routes.py`
Expected: `29 passed`.

- [ ] **Step 5: Regenerate the API contract**

Run: `just openapi`
Expected: `wrote ..\frontend\openapi.json`, then `openapi.json → src/api/schema.ts`. `git diff frontend/src/api/schema.ts` shows `status?: components["schemas"]["FindingStatus"][]`, `assignee`, `since` and `ai_status`.

- [ ] **Step 6: Check the backend, the contract and the web app**

Run: `cd backend && uv run python -m ruff check src tests && uv run python -m ruff format --check src tests && uv run python -m mypy src && cd ../frontend && pnpm check:api && pnpm exec tsc --noEmit && pnpm test`
Expected: Ruff passes and finds every file formatted, mypy reports `Success: no issues found`, `check:api` finds `schema.ts` up to date, types are clean, and `194 passed`.

- [ ] **Step 7: Commit**

```bash
git add backend frontend/openapi.json frontend/src
git commit -m "feat(api): findings filtered by any of several statuses, by assignee and since a moment, each with how its AI analysis went" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 2: Each upload says how many findings it has, and the most severe

**Files:**
- Modify: `backend/src/nettriage/adapters/uploads.py`, `backend/src/nettriage/entrypoints/api/upload_schemas.py`, `frontend/src/test/fixtures.ts`
- Regenerate: `frontend/openapi.json`, `frontend/src/api/schema.ts`
- Test: `backend/tests/api/test_upload_routes.py`

**Interfaces:**
- Consumes: `Upload`, `get_upload`, `list_uploads` and `UploadOut` (Plan 4a); `FindingSeverity` from `adapters/findings.py`; the test helpers `add_finding` and `add_upload`.
- Produces: `findings: int` and `worst_severity: FindingSeverity | None` on `Upload` and `UploadOut`, in the list and single reads. A new upload answers `0` and `null`.

- [ ] **Step 1: Write the failing test**

In `backend/tests/api/test_upload_routes.py`, replace:
```python
from opentelemetry.trace import SpanKind
from sqlalchemy import text
from tenantdata import add_member, add_org, add_user

from nettriage.adapters.kill_switch import KillSwitch
```
with:
```python
from opentelemetry.trace import SpanKind
from sqlalchemy import text
from tenantdata import add_finding, add_member, add_org, add_upload, add_user

from nettriage.adapters.kill_switch import KillSwitch
```

In `backend/tests/api/test_upload_routes.py`, replace:
```python


def test_an_upload_is_read_by_id_but_not_through_another_org(
    database_client: TestClient,
```
with:
```python


def test_an_upload_says_how_many_findings_it_has_and_the_most_severe(
    database_client: TestClient,
    headers: dict[str, str],
    org: tuple[UUID, UUID],
    database: Database,
) -> None:
    created = post(database_client, org[0], headers).json()["upload"]
    with database.admin.begin() as connection:
        analyzed = add_upload(connection, org[0], org[1], status="analyzed")
        for severity in ("low", "high", "medium"):
            add_finding(connection, org[0], analyzed, severity=severity)

    listed = database_client.get(f"/api/v1/orgs/{org[0]}/uploads").json()["uploads"]
    one = database_client.get(f"/api/v1/orgs/{org[0]}/uploads/{analyzed}").json()

    assert (created["findings"], created["worst_severity"]) == (0, None)
    assert {upload["id"]: (upload["findings"], upload["worst_severity"]) for upload in listed} == {
        created["id"]: (0, None),
        str(analyzed): (3, "high"),
    }
    assert (one["findings"], one["worst_severity"]) == (3, "high")


def test_an_upload_is_read_by_id_but_not_through_another_org(
    database_client: TestClient,
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/api/test_upload_routes.py`
Expected: FAIL: `1 failed, 17 passed`, on `KeyError: 'findings'`.

- [ ] **Step 3: Count an upload's findings and find the worst**

In `backend/src/nettriage/adapters/uploads.py`, replace:
```python
from sqlalchemy import Engine, Row, text

from nettriage.adapters.organizations import lock_org_for
from nettriage.adapters.postgres import tenant_transaction
```
with:
```python
from sqlalchemy import Engine, Row, text

from nettriage.adapters.findings import FindingSeverity
from nettriage.adapters.organizations import lock_org_for
from nettriage.adapters.postgres import tenant_transaction
```

In `backend/src/nettriage/adapters/uploads.py`, replace:
```python
    "lower(flow_time_range) AS flow_start, upper(flow_time_range) AS flow_end, processed_at, "
    "created_at"
)

```
with:
```python
    "lower(flow_time_range) AS flow_start, upper(flow_time_range) AS flow_end, processed_at, "
    "created_at"
)
# The findings stored for an upload, and the most severe of them (Plan 6d).
_FINDINGS = (
    "(SELECT count(*) FROM findings f WHERE f.org_id = uploads.org_id "
    "AND f.upload_id = uploads.id) AS findings, "
    "(SELECT f.severity FROM findings f WHERE f.org_id = uploads.org_id "
    "AND f.upload_id = uploads.id ORDER BY "
    "array_position(ARRAY['low', 'medium', 'high', 'critical'], f.severity) DESC "
    "LIMIT 1) AS worst_severity"
)

```

In `backend/src/nettriage/adapters/uploads.py`, replace:
```python
    processed_at: datetime | None
    created_at: datetime


```
with:
```python
    processed_at: datetime | None
    created_at: datetime
    findings: int
    worst_severity: FindingSeverity | None


```

In `backend/src/nettriage/adapters/uploads.py`, replace:
```python
                "INSERT INTO uploads (id, org_id, uploaded_by, original_filename, s3_key, "  # noqa: S608
                "size_bytes, sha256) VALUES (:id, :org, :user, :filename, :key, :size, :sha256) "
                f"RETURNING {_COLUMNS}"
            ),
            {
```
with:
```python
                "INSERT INTO uploads (id, org_id, uploaded_by, original_filename, s3_key, "  # noqa: S608
                "size_bytes, sha256) VALUES (:id, :org, :user, :filename, :key, :size, :sha256) "
                f"RETURNING {_COLUMNS}, 0 AS findings, NULL AS worst_severity"
            ),
            {
```

In `backend/src/nettriage/adapters/uploads.py`, replace:
```python
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        row = connection.execute(
            text(f"SELECT {_COLUMNS} FROM uploads WHERE org_id = :org AND id = :id"),  # noqa: S608
            {"org": org_id, "id": upload_id},
        ).one_or_none()
```
with:
```python
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        row = connection.execute(
            text(
                f"SELECT {_COLUMNS}, {_FINDINGS} FROM uploads "  # noqa: S608
                "WHERE org_id = :org AND id = :id"
            ),
            {"org": org_id, "id": upload_id},
        ).one_or_none()
```

In `backend/src/nettriage/adapters/uploads.py`, replace:
```python
        rows = connection.execute(
            text(
                f"SELECT {_COLUMNS} FROM uploads WHERE org_id = :org "  # noqa: S608
                "AND (CAST(:before_at AS timestamptz) IS NULL OR (created_at, id) < "
                "(CAST(:before_at AS timestamptz), CAST(:before_id AS uuid))) "
```
with:
```python
        rows = connection.execute(
            text(
                f"SELECT {_COLUMNS}, {_FINDINGS} FROM uploads WHERE org_id = :org "  # noqa: S608
                "AND (CAST(:before_at AS timestamptz) IS NULL OR (created_at, id) < "
                "(CAST(:before_at AS timestamptz), CAST(:before_id AS uuid))) "
```

In `backend/src/nettriage/adapters/uploads.py`, replace:
```python
        processed_at=row.processed_at,
        created_at=row.created_at,
    )
```
with:
```python
        processed_at=row.processed_at,
        created_at=row.created_at,
        findings=row.findings,
        worst_severity=row.worst_severity,
    )
```

In `backend/src/nettriage/entrypoints/api/upload_schemas.py`, replace:
```python
from pydantic import BaseModel, Field, StringConstraints

from nettriage.adapters.uploads import Upload
from nettriage.application.uploads import MAX_FILENAME, MAX_UPLOAD_BYTES, UploadStatus
```
with:
```python
from pydantic import BaseModel, Field, StringConstraints

from nettriage.adapters.findings import FindingSeverity
from nettriage.adapters.uploads import Upload
from nettriage.application.uploads import MAX_FILENAME, MAX_UPLOAD_BYTES, UploadStatus
```

In `backend/src/nettriage/entrypoints/api/upload_schemas.py`, replace:
```python
    created_at: datetime
    processed_at: datetime | None

    @classmethod
```
with:
```python
    created_at: datetime
    processed_at: datetime | None
    # The findings stored for it, and the most severe of them (Plan 6d).
    findings: int
    worst_severity: FindingSeverity | None

    @classmethod
```

In `backend/src/nettriage/entrypoints/api/upload_schemas.py`, replace:
```python
            created_at=upload.created_at,
            processed_at=upload.processed_at,
        )

```
with:
```python
            created_at=upload.created_at,
            processed_at=upload.processed_at,
            findings=upload.findings,
            worst_severity=upload.worst_severity,
        )

```

In `frontend/src/test/fixtures.ts`, replace:
```ts
    created_at: "2026-10-04T09:30:00Z",
    processed_at: "2026-10-04T09:31:00Z",
    ...fields,
  };
```
with:
```ts
    created_at: "2026-10-04T09:30:00Z",
    processed_at: "2026-10-04T09:31:00Z",
    findings: 0,
    worst_severity: null,
    ...fields,
  };
```

- [ ] **Step 4: Run the tests again, and regenerate the contract**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/api/test_upload_routes.py && cd .. && just openapi`
Expected: `18 passed`, then `wrote ..\frontend\openapi.json` and `openapi.json → src/api/schema.ts`.

- [ ] **Step 5: Check the backend, the contract and the web app**

Run: `cd backend && uv run python -m ruff check src tests && uv run python -m ruff format --check src tests && uv run python -m mypy src && cd ../frontend && pnpm check:api && pnpm exec tsc --noEmit && pnpm test`
Expected: all clean, and `194 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend frontend/openapi.json frontend/src
git commit -m "feat(api): each upload says how many findings it has and the most severe" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 3: An organization's overview

**Files:**
- Create: `backend/src/nettriage/adapters/overview.py`, `backend/src/nettriage/entrypoints/api/overview_schemas.py`, `backend/src/nettriage/entrypoints/api/routes/overview.py`
- Modify: `backend/src/nettriage/adapters/uploads.py`, `backend/src/nettriage/entrypoints/api/app.py`
- Regenerate: `frontend/openapi.json`, `frontend/src/api/schema.ts`
- Test: `backend/tests/api/test_overview_routes.py`, `backend/tests/security/test_route_access.py`, `backend/tests/security/test_authorization_matrix.py`

**Interfaces:**
- Consumes: `tenant_transaction`, `OrgMember`, `org_rules`, `get_services`, `Upload`, `UploadOut` (with Task 2's fields).
- Produces:
  - `GET /api/v1/orgs/{org}/overview` under `findings:read`, answering `OverviewOut`: `unresolved_by_severity` (`SeverityCounts`: `critical`, `high`, `medium`, `low`), `by_status` (`StatusCounts`: `open`, `investigating`, `resolved`, `false_positive`), `unresolved_unassigned`, `unresolved_mine`, `new_last_day`, `member_count` and `last_upload: UploadOut | None`;
  - `org_overview(engine, org_id, user_id) -> Overview` in `adapters/overview.py`;
  - `latest_upload(connection, org_id) -> Upload | None` in `adapters/uploads.py`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/api/test_overview_routes.py`:
```python
"""An organization's overview (Plan 6d): its findings by severity and status, what's unassigned
and what's the caller's, what's new, its members and its last upload."""

from __future__ import annotations

from uuid import UUID

import pytest
from browser import signed_in_as
from conftest import Database, FakeClock
from fastapi.testclient import TestClient
from sqlalchemy import text
from tenantdata import add_finding, add_member, add_org, add_upload, add_user

from nettriage.entrypoints.api.services import Services

ZEROS = {"critical": 0, "high": 0, "medium": 0, "low": 0}


@pytest.fixture
def org(database: Database) -> tuple[UUID, UUID, UUID]:
    """An org, its owner, and an analyst in it."""
    with database.admin.begin() as connection:
        owner = add_user(connection)
        analyst = add_user(connection)
        org_id = add_org(connection, owner)
        add_member(connection, org_id, owner, "owner")
        add_member(connection, org_id, analyst, "analyst")
    return org_id, owner, analyst


def finding(
    database: Database,
    org: UUID,
    upload: UUID,
    *,
    severity: str = "high",
    status: str = "open",
    assignee: UUID | None = None,
    created_at: str | None = None,
) -> None:
    with database.admin.begin() as connection:
        finding_id = add_finding(connection, org, upload, severity=severity)
        connection.execute(
            text(
                "UPDATE findings SET status = :status, assignee_id = :assignee, "
                "created_at = COALESCE(CAST(:created AS timestamptz), created_at) WHERE id = :id"
            ),
            {"status": status, "assignee": assignee, "created": created_at, "id": finding_id},
        )


def overview(
    client: TestClient, services: Services, clock: FakeClock, org: UUID, user: UUID
) -> dict[str, object]:
    signed_in_as(client, services.sessions, user, clock())
    response = client.get(f"/api/v1/orgs/{org}/overview")
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def test_an_organization_overview_counts_its_findings(
    database_client: TestClient,
    database: Database,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID, UUID],
) -> None:
    org_id, owner, analyst = org
    with database.admin.begin() as connection:
        older = add_upload(connection, org_id, owner, status="analyzed")
        newest = add_upload(connection, org_id, owner, status="analyzed")
    finding(database, org_id, older, severity="critical", assignee=owner)
    finding(database, org_id, older, severity="high", status="investigating", assignee=analyst)
    finding(database, org_id, older, severity="high", created_at="2026-09-01T00:00:00Z")
    finding(database, org_id, older, severity="low", status="resolved", assignee=owner)
    finding(database, org_id, newest, severity="medium", status="false_positive")

    seen = overview(database_client, services, clock, org_id, owner)

    assert seen["unresolved_by_severity"] == {**ZEROS, "critical": 1, "high": 2}
    assert seen["by_status"] == {
        "open": 2,
        "investigating": 1,
        "resolved": 1,
        "false_positive": 1,
    }
    assert (seen["unresolved_unassigned"], seen["unresolved_mine"]) == (1, 1)
    assert seen["new_last_day"] == 4
    assert seen["member_count"] == 2
    last = seen["last_upload"]
    assert isinstance(last, dict)
    assert (last["id"], last["findings"], last["worst_severity"]) == (str(newest), 1, "medium")


def test_what_is_mine_depends_on_who_asks(
    database_client: TestClient,
    database: Database,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID, UUID],
) -> None:
    org_id, owner, analyst = org
    with database.admin.begin() as connection:
        upload = add_upload(connection, org_id, owner, status="analyzed")
    finding(database, org_id, upload, assignee=analyst)
    finding(database, org_id, upload, status="investigating", assignee=analyst)

    assert overview(database_client, services, clock, org_id, owner)["unresolved_mine"] == 0
    assert overview(database_client, services, clock, org_id, analyst)["unresolved_mine"] == 2


def test_a_new_organization_has_nothing_to_count(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID, UUID],
) -> None:
    seen = overview(database_client, services, clock, org[0], org[1])

    assert seen == {
        "unresolved_by_severity": ZEROS,
        "by_status": {"open": 0, "investigating": 0, "resolved": 0, "false_positive": 0},
        "unresolved_unassigned": 0,
        "unresolved_mine": 0,
        "new_last_day": 0,
        "member_count": 2,
        "last_upload": None,
    }


def test_an_overview_is_only_for_the_organizations_members(
    database_client: TestClient,
    database: Database,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID, UUID],
) -> None:
    with database.admin.begin() as connection:
        stranger = add_user(connection)
    signed_in_as(database_client, services.sessions, stranger, clock())

    response = database_client.get(f"/api/v1/orgs/{org[0]}/overview")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
    ),
    "read AI usage": ("GET", "/api/v1/orgs/{org}/usage", None),
}
# Headers an endpoint needs besides the session's: a triage change names the version it read.
```
with:
```python
    ),
    "read AI usage": ("GET", "/api/v1/orgs/{org}/usage", None),
    "read the overview": ("GET", "/api/v1/orgs/{org}/overview", None),
}
# Headers an endpoint needs besides the session's: a triage change names the version it read.
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
    "rate AI explanation": (200, 200, 200, 403, 404, 401),
    "read AI usage": (200, 200, 403, 403, 404, 401),
}

```
with:
```python
    "rate AI explanation": (200, 200, 200, 403, 404, 401),
    "read AI usage": (200, 200, 403, 403, 404, 401),
    "read the overview": (200, 200, 200, 200, 404, 401),
}

```

In `backend/tests/security/test_route_access.py`, replace:
```python
    ): "ai:feedback",
    ("GET", "/api/v1/orgs/{org_id}/usage"): "usage:read",
    ("GET", "/api/v1/attack-techniques/{technique_id}"): "signed in",
}
```
with:
```python
    ): "ai:feedback",
    ("GET", "/api/v1/orgs/{org_id}/usage"): "usage:read",
    ("GET", "/api/v1/orgs/{org_id}/overview"): "findings:read",
    ("GET", "/api/v1/attack-techniques/{technique_id}"): "signed in",
}
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/api/test_overview_routes.py tests/security`
Expected: FAIL: `9 failed, 184 passed, 1 skipped`. Three overview tests get 404s, as there's no route (a stranger's 404 already passes). The route-access test finds the route missing, and the authorization matrix's overview case fails for the four members and the signed-out caller.

- [ ] **Step 3: Count the organization's findings in one read**

Create `backend/src/nettriage/adapters/overview.py`:
```python
"""An organization's overview (Plan 6d): its findings counted by severity and status, what's
unassigned and what's the caller's, what arrived in the last day, its members and its last upload.
One read in the org's transaction; every number is a count over existing tables."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Engine, text

from nettriage.adapters.findings import FindingSeverity, FindingStatus
from nettriage.adapters.postgres import tenant_transaction
from nettriage.adapters.uploads import Upload, latest_upload

SEVERITIES: tuple[FindingSeverity, ...] = ("critical", "high", "medium", "low")
STATUSES: tuple[FindingStatus, ...] = ("open", "investigating", "resolved", "false_positive")

# Open or Investigating: still to be dealt with.
_UNRESOLVED = "status IN ('open', 'investigating')"
_COUNTS = ", ".join(
    [
        *(
            f"count(*) FILTER (WHERE {_UNRESOLVED} AND severity = '{severity}') AS u_{severity}"
            for severity in SEVERITIES
        ),
        *(f"count(*) FILTER (WHERE status = '{status}') AS s_{status}" for status in STATUSES),
        f"count(*) FILTER (WHERE {_UNRESOLVED} AND assignee_id IS NULL) AS unassigned",
        f"count(*) FILTER (WHERE {_UNRESOLVED} AND assignee_id = :user) AS mine",
        "count(*) FILTER (WHERE created_at >= now() - interval '1 day') AS new_last_day",
    ]
)


@dataclass(frozen=True)
class Overview:
    unresolved_by_severity: dict[FindingSeverity, int]
    by_status: dict[FindingStatus, int]
    unresolved_unassigned: int
    unresolved_mine: int
    new_last_day: int
    member_count: int
    last_upload: Upload | None


def org_overview(engine: Engine, org_id: UUID, user_id: UUID) -> Overview:
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        counts = connection.execute(
            text(f"SELECT {_COUNTS} FROM findings WHERE org_id = :org"),  # noqa: S608
            {"org": org_id, "user": user_id},
        ).one()
        members: int = connection.execute(
            text("SELECT count(*) FROM memberships WHERE org_id = :org"), {"org": org_id}
        ).scalar_one()
        last = latest_upload(connection, org_id)
    return Overview(
        unresolved_by_severity={
            severity: getattr(counts, f"u_{severity}") for severity in SEVERITIES
        },
        by_status={status: getattr(counts, f"s_{status}") for status in STATUSES},
        unresolved_unassigned=counts.unassigned,
        unresolved_mine=counts.mine,
        new_last_day=counts.new_last_day,
        member_count=members,
        last_upload=last,
    )
```

In `backend/src/nettriage/adapters/uploads.py`, replace:
```python
from uuid import UUID

from sqlalchemy import Engine, Row, text

from nettriage.adapters.findings import FindingSeverity
```
with:
```python
from uuid import UUID

from sqlalchemy import Connection, Engine, Row, text

from nettriage.adapters.findings import FindingSeverity
```

In `backend/src/nettriage/adapters/uploads.py`, replace:
```python


def _upload(row: Row[Any]) -> Upload:
    return Upload(
```
with:
```python


def latest_upload(connection: Connection, org_id: UUID) -> Upload | None:
    """The org's newest upload, whatever its status, inside the caller's transaction (Plan 6d)."""
    row = connection.execute(
        text(
            f"SELECT {_COLUMNS}, {_FINDINGS} FROM uploads WHERE org_id = :org "  # noqa: S608
            "ORDER BY created_at DESC, id DESC LIMIT 1"
        ),
        {"org": org_id},
    ).one_or_none()
    return None if row is None else _upload(row)


def _upload(row: Row[Any]) -> Upload:
    return Upload(
```

In `backend/src/nettriage/entrypoints/api/app.py`, replace:
```python
    members,
    orgs,
    triage,
    uploads,
```
with:
```python
    members,
    orgs,
    overview,
    triage,
    uploads,
```

In `backend/src/nettriage/entrypoints/api/app.py`, replace:
```python
        ai,
        usage,
        attack_techniques,
    ):
```
with:
```python
        ai,
        usage,
        overview,
        attack_techniques,
    ):
```

Create `backend/src/nettriage/entrypoints/api/overview_schemas.py`:
```python
"""An organization's overview (Plan 6d), as the API returns it."""

from __future__ import annotations

from pydantic import BaseModel

from nettriage.adapters.overview import Overview
from nettriage.entrypoints.api.upload_schemas import UploadOut


class SeverityCounts(BaseModel):
    critical: int
    high: int
    medium: int
    low: int


class StatusCounts(BaseModel):
    open: int
    investigating: int
    resolved: int
    false_positive: int


class OverviewOut(BaseModel):
    """Unresolved means Open or Investigating; `new_last_day` counts the findings detected in the
    last 24 hours, whatever their status."""

    unresolved_by_severity: SeverityCounts
    by_status: StatusCounts
    unresolved_unassigned: int
    unresolved_mine: int
    new_last_day: int
    member_count: int
    last_upload: UploadOut | None

    @classmethod
    def of(cls, overview: Overview) -> OverviewOut:
        return cls(
            unresolved_by_severity=SeverityCounts(**overview.unresolved_by_severity),
            by_status=StatusCounts(**overview.by_status),
            unresolved_unassigned=overview.unresolved_unassigned,
            unresolved_mine=overview.unresolved_mine,
            new_last_day=overview.new_last_day,
            member_count=overview.member_count,
            last_upload=None
            if overview.last_upload is None
            else UploadOut.of(overview.last_upload),
        )
```

Create `backend/src/nettriage/entrypoints/api/routes/overview.py`:
```python
"""An organization's overview (Plan 6d): how its findings stand, for every member."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from nettriage.adapters.overview import org_overview
from nettriage.entrypoints.api.access import OrgContext, OrgMember
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.overview_schemas import OverviewOut
from nettriage.entrypoints.api.services import get_services

router = APIRouter(prefix="/v1/orgs/{org_id}")


@router.get("/overview")
def overview(
    request: Request,
    org_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("findings:read"))],
) -> OverviewOut:
    """Unresolved findings by severity, all findings by status, the unresolved ones unassigned
    and the caller's, those detected in the last 24 hours, the members and the last upload."""
    with org_rules(request, "The overview"):
        found = org_overview(get_services(request).database, org.org_id, org.user_id)
    return OverviewOut.of(found)
```

- [ ] **Step 4: Run the tests again, and regenerate the contract**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/api/test_overview_routes.py tests/security && cd .. && just openapi`
Expected: `193 passed, 1 skipped` (the rate limiter's DynamoDB Local test runs only in CI), then `wrote ..\frontend\openapi.json` and `openapi.json → src/api/schema.ts`.

- [ ] **Step 5: Check the backend and the contract**

Run: `cd backend && uv run python -m ruff check src tests && uv run python -m ruff format --check src tests && uv run python -m mypy src && cd ../frontend && pnpm check:api && pnpm exec tsc --noEmit`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add backend frontend/openapi.json frontend/src/api/schema.ts
git commit -m "feat(api): an organization's overview: unresolved by severity, by status, unassigned, yours, new in the last day, members and the last upload" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 4: Times, people and the AI's mark, shared by every page

**Files:**
- Create: `frontend/src/ui/Ago.tsx`, `frontend/src/ui/Person.tsx`, `frontend/src/ui/AiMark.tsx`, `frontend/src/orgs/overview.ts`
- Modify: `frontend/src/ui/format.ts`, `frontend/src/styles.css`
- Test: `frontend/src/ui/format.test.ts`, `frontend/src/ui/people.test.tsx`

**Interfaces:**
- Consumes: `formatDate` and `formatDateTime` (Plans 6a and 6b); Task 1's `ai_status` and Task 3's `OverviewOut` in the generated client.
- Produces:
  - `formatAgo(iso, now) -> string` in `src/ui/format.ts`: "just now", "N min ago", "N h ago", "yesterday", "N days ago", then the date;
  - `<Ago iso={…} />`: a `<time className="ago">` with `dateTime` and the exact time in `title`;
  - `initials(name)`, `<Person name={…} you? />` and `<Unassigned />` in `src/ui/Person.tsx`;
  - `<AiMark status={…} />`: "Explained", "Not explained yet" or "No explanation";
  - in `src/orgs/overview.ts`: the type `Overview`, `overviewKey(orgId)` and `useOverview(orgId)`.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/ui/format.test.ts`, replace:
```ts
import { expect, test } from "vitest";
import { formatClock, formatDateTime, formatDay, formatTime, formatUsd } from "./format";

test("a moment is written with its date and its time", () => {
```
with:
```ts
import { expect, test } from "vitest";
import {
  formatAgo,
  formatClock,
  formatDate,
  formatDateTime,
  formatDay,
  formatTime,
  formatUsd,
} from "./format";

test("a moment is written with its date and its time", () => {
```

In `frontend/src/ui/format.test.ts`, replace:
```ts
  expect(formatDay("2026-10-04")).not.toMatch(/3/);
});
```
with:
```ts
  expect(formatDay("2026-10-04")).not.toMatch(/3/);
});

test("a recent moment is said as how long ago it was, and an older one as its date", () => {
  const now = Date.parse("2026-10-05T12:00:00Z");
  const ago = (iso: string) => formatAgo(iso, now);

  expect(ago("2026-10-05T11:59:30Z")).toBe("just now");
  expect(ago("2026-10-05T12:05:00Z")).toBe("just now");
  expect(ago("2026-10-05T11:59:00Z")).toBe("1 min ago");
  expect(ago("2026-10-05T11:01:00Z")).toBe("59 min ago");
  expect(ago("2026-10-05T11:00:00Z")).toBe("1 h ago");
  expect(ago("2026-10-04T12:00:01Z")).toBe("23 h ago");
  expect(ago("2026-10-04T12:00:00Z")).toBe("yesterday");
  expect(ago("2026-10-03T12:00:00Z")).toBe("2 days ago");
  expect(ago("2026-09-28T12:00:01Z")).toBe("6 days ago");
  expect(ago("2026-09-28T12:00:00Z")).toBe(formatDate("2026-09-28T12:00:00Z"));
});
```

Create `frontend/src/ui/people.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AiMark } from "./AiMark";
import { Ago } from "./Ago";
import { formatDateTime } from "./format";
import { Person, Unassigned, initials } from "./Person";

afterEach(() => {
  vi.useRealTimers();
});

test("initials come from a display name, or from an email address before the @", () => {
  expect(["Ben Okafor", "ana@acme.example", "cleo.park@acme.example", "x"].map(initials)).toEqual([
    "BO",
    "AN",
    "CP",
    "X",
  ]);
});

test("a person is their initials beside their name, and the signed-in person is You", () => {
  const { container, rerender } = render(<Person name="Ben Okafor" />);

  expect(screen.getByText("Ben Okafor")).toBeInTheDocument();
  expect(container.querySelector(".initials")).toHaveTextContent("BO");
  expect(container.querySelector(".initials")).toHaveAttribute("aria-hidden", "true");

  rerender(<Person name="ana@acme.example" you />);
  expect(screen.getByText("You")).toBeInTheDocument();
  expect(screen.queryByText("ana@acme.example")).toBeNull();
});

test("nobody assigned reads as Unassigned", () => {
  render(<Unassigned />);

  expect(screen.getByText("Unassigned")).toBeInTheDocument();
});

test("a moment shows how long ago it was, with the exact time to hover", () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));

  render(<Ago iso="2026-10-05T10:00:00Z" />);

  const time = screen.getByText("2 h ago");
  expect(time.tagName).toBe("TIME");
  expect(time).toHaveAttribute("dateTime", "2026-10-05T10:00:00Z");
  expect(time).toHaveAttribute("title", formatDateTime("2026-10-05T10:00:00Z"));
});

test("a finding's AI mark says whether it's explained", () => {
  const { rerender } = render(<AiMark status="succeeded" />);
  expect(screen.getByText("Explained")).toBeInTheDocument();

  for (const status of [null, "pending"] as const) {
    rerender(<AiMark status={status} />);
    expect(screen.getByText("Not explained yet")).toBeInTheDocument();
  }
  for (const status of ["failed", "skipped_budget", "invalid_output"] as const) {
    rerender(<AiMark status={status} />);
    expect(screen.getByText("No explanation")).toBeInTheDocument();
  }
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && pnpm exec vitest run src/ui/format.test.ts src/ui/people.test.tsx`
Expected: FAIL: `1 failed | 4 passed`. `people.test.tsx` stops on `Failed to resolve import "./Person"`, and the new format test fails with `formatAgo is not a function`.

- [ ] **Step 3: Write the shared pieces and their look**

Create `frontend/src/orgs/overview.ts`:
```ts
/** An organization's overview (Plan 6d): how its findings stand, read in one call. */
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";

export type Overview = components["schemas"]["OverviewOut"];

export function overviewKey(orgId: string) {
  return ["overview", orgId] as const;
}

export function useOverview(orgId: string) {
  return useQuery({
    queryKey: overviewKey(orgId),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/overview", { params: { path: { org_id: orgId } } }),
      ),
  });
}
```

In `frontend/src/styles.css`, replace:
```css
  font-weight: 650;
  white-space: nowrap;
}

```
with:
```css
  font-weight: 650;
  white-space: nowrap;
}

/* People, moments and the AI's mark, as lists show them (Plan 6d) */

.person {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  white-space: nowrap;
}

.initials {
  display: inline-grid;
  flex: none;
  place-items: center;
  width: 1.6rem;
  height: 1.6rem;
  border-radius: 50%;
  background: var(--ink);
  color: var(--plate);
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.initials-none {
  border: 1px dashed var(--graphite);
  background: transparent;
}

.ago {
  white-space: nowrap;
}

.ai-mark {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.82rem;
  font-weight: 650;
  color: var(--teal);
  white-space: nowrap;
}

.ai-mark::before {
  content: "";
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--teal);
}

.ai-mark-none {
  color: var(--graphite);
}

.ai-mark-none::before {
  border: 1px solid var(--graphite);
  background: transparent;
}

```

Create `frontend/src/ui/Ago.tsx`:
```tsx
import { formatAgo, formatDateTime } from "./format";

/** A moment as how long ago it was, with the exact local time on hover (Plan 6d). */
export function Ago({ iso }: { iso: string }) {
  return (
    <time className="ago" dateTime={iso} title={formatDateTime(iso)}>
      {formatAgo(iso, Date.now())}
    </time>
  );
}
```

Create `frontend/src/ui/AiMark.tsx`:
```tsx
import type { components } from "../api/schema";

type AiStatus = components["schemas"]["FindingSummaryOut"]["ai_status"];

/** Whether the AI has explained a finding, beside it in a list (Plan 6d). Teal, as the AI's label. */
export function AiMark({ status }: { status: AiStatus }) {
  if (status === "succeeded") {
    return <span className="ai-mark">Explained</span>;
  }
  return (
    <span className="ai-mark ai-mark-none">
      {status === null || status === "pending" ? "Not explained yet" : "No explanation"}
    </span>
  );
}
```

Create `frontend/src/ui/Person.tsx`:
```tsx
/** Two letters for a person: from their display name, or from their email before the @. */
export function initials(name: string): string {
  const words =
    name
      .split("@")[0]
      ?.split(/[\s._-]+/)
      .filter((word) => word !== "") ?? [];
  const [first = "", second] = words;
  const letters = second === undefined ? first.slice(0, 2) : `${first[0] ?? ""}${second[0] ?? ""}`;
  return letters.toUpperCase();
}

/** Someone: a disc with their initials beside their name, or You for the signed-in person. */
export function Person({ name, you = false }: { name: string; you?: boolean }) {
  return (
    <span className="person">
      <span className="initials" aria-hidden="true">
        {initials(name)}
      </span>
      {you ? "You" : name}
    </span>
  );
}

/** Nobody assigned: an empty, dashed disc. */
export function Unassigned() {
  return (
    <span className="person">
      <span className="initials initials-none" aria-hidden="true" />
      <span className="muted">Unassigned</span>
    </span>
  );
}
```

In `frontend/src/ui/format.ts`, replace:
```ts
  return UTC_DAY.format(new Date(`${day}T00:00:00Z`));
}
```
with:
```ts
  return UTC_DAY.format(new Date(`${day}T00:00:00Z`));
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/**
 * How long ago a moment was (Plan 6d): "just now", "5 min ago", "2 h ago", "yesterday",
 * "3 days ago", then its date from a week on. A moment slightly ahead of this clock is "just now".
 */
export function formatAgo(iso: string, now: number): string {
  const elapsed = now - Date.parse(iso);
  if (elapsed < MINUTE) {
    return "just now";
  }
  if (elapsed < HOUR) {
    return `${Math.floor(elapsed / MINUTE)} min ago`;
  }
  if (elapsed < DAY) {
    return `${Math.floor(elapsed / HOUR)} h ago`;
  }
  if (elapsed < 2 * DAY) {
    return "yesterday";
  }
  if (elapsed < 7 * DAY) {
    return `${Math.floor(elapsed / DAY)} days ago`;
  }
  return formatDate(iso);
}
```

- [ ] **Step 4: Run the tests again**

Run: `cd frontend && pnpm exec vitest run src/ui/format.test.ts src/ui/people.test.tsx`
Expected: `10 passed`.

- [ ] **Step 5: Check everything**

Run: `cd frontend && pnpm lint && pnpm exec tsc --noEmit && pnpm test`
Expected: lint and types are clean, and `200 passed`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(web): moments said as how long ago, people as initials beside their names, the AI's mark, and the organization overview's query" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 5: The home

**Files:**
- Create: `frontend/src/findings/assigned.ts`, `frontend/src/pages/home/AssignedToYou.tsx`, `frontend/src/pages/home/OrgCard.tsx`, `frontend/src/ui/FindingSubline.tsx`
- Modify: `frontend/src/pages/Orgs.tsx`, `frontend/src/uploads/uploads.ts`, `frontend/src/pages/org/Uploads.tsx`, `frontend/src/styles.css`, `frontend/src/test/fixtures.ts`
- Test: `frontend/src/pages/home/Home.test.tsx`, `frontend/src/pages/Orgs.test.tsx`, `frontend/src/ui/FindingSubline.test.tsx`

**Interfaces:**
- Consumes: Task 4's `Ago`, `AiMark`, `useOverview` and `Overview`; `useMe`, `Membership`, `Severity`, `SeverityBars`, `roleLabel`, `formatNumber`, `ErrorNotice`, `Loading`, `usePageTitle`; the vocabulary's `SEVERITIES`, `severityLabel`, `severityLevel`, `statusLabel` and `detectorName`.
- Produces:
  - in `src/findings/assigned.ts`: `ASSIGNED_LIMIT` (20), `assignedKey(orgId)` (`["findings", orgId, "assigned-to-me"]`), `useAssignedToMe(memberships)` and `worstFirst(a, b)`;
  - `flowOf(finding)` and `<FindingSubline finding={…} />` in `src/ui/FindingSubline.tsx`;
  - `UPLOAD_STATUS_LABELS` in `src/uploads/uploads.ts`, moved from the uploads page;
  - the fixture `overview(fields)` in `src/test/fixtures.ts`;
  - the home at `/app`: a hidden `h1` "Home", the sections "Assigned to you" and "Organizations" (`h2.page-title`), and one `article.org-card` per membership.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/pages/Orgs.test.tsx`, replace:
```tsx
  renderAt("/app");

  expect(await screen.findByRole("heading", { name: "Your organizations" })).toBeInTheDocument();
  expect(
    screen.getByText(
```
with:
```tsx
  renderAt("/app");

  expect(await screen.findByRole("heading", { name: "Organizations" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Assigned to you" })).toBeNull();
  expect(
    screen.getByText(
```

Create `frontend/src/pages/home/Home.test.tsx`:
```tsx
import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fakeApi } from "../../test/fakeApi";
import { ACME, ORG_ID, findingSummary, memberOf, overview, upload } from "../../test/fixtures";
import { renderAt } from "../../test/render";

const LAB_ID = "01a10333-0000-7000-8000-000000000002";
const LAB = {
  ...ACME,
  org_id: LAB_ID,
  name: "Northwind Labs",
  slug: "northwind-labs",
  role: "analyst" as const,
};
const NONE = { body: { findings: [], next_cursor: null } };

function finding(id: number, fields: object) {
  return findingSummary({ id: `01a10600-0000-7000-8000-00000000000${id}`, ...fields });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

test("findings assigned to the person in every organization are listed together, worst first", async () => {
  const fake = fakeApi({
    "GET /api/v1/me": { body: memberOf(ACME, LAB) },
    [`GET /api/v1/orgs/${ORG_ID}/findings`]: {
      body: {
        findings: [
          finding(1, { severity: "high", title: "Port scan of 10.0.0.5" }),
          finding(2, {
            severity: "medium",
            title: "RDP attempts",
            created_at: "2026-10-05T09:00:00Z",
          }),
        ],
        next_cursor: null,
      },
    },
    [`GET /api/v1/orgs/${LAB_ID}/findings`]: {
      body: {
        findings: [
          finding(3, { severity: "critical", title: "SSH brute force", status: "investigating" }),
        ],
        next_cursor: null,
      },
    },
    [`GET /api/v1/orgs/${ORG_ID}/overview`]: { body: overview() },
    [`GET /api/v1/orgs/${LAB_ID}/overview`]: { body: overview() },
  });

  renderAt("/app");

  const assigned = await screen.findByRole("region", { name: "Assigned to you" });
  await vi.waitFor(() => expect(within(assigned).getAllByRole("row")).toHaveLength(4));
  const rows = within(assigned).getAllByRole("row").slice(1);
  expect(rows.map((row) => within(row).getByRole("link").textContent)).toEqual([
    "SSH brute force",
    "Port scan of 10.0.0.5",
    "RDP attempts",
  ]);
  expect(within(rows[0] as HTMLElement).getByText("Northwind Labs")).toBeInTheDocument();
  expect(within(rows[0] as HTMLElement).getByRole("link")).toHaveAttribute(
    "href",
    `/app/orgs/${LAB_ID}/findings/01a10600-0000-7000-8000-000000000003`,
  );
  expect(within(rows[2] as HTMLElement).getByText("3 h ago")).toBeInTheDocument();
  expect(within(assigned).getByText("3 unresolved, in 2 organizations")).toBeInTheDocument();
  const asked = new URL(
    fake.requests.find((request) => request.url.includes(`${ORG_ID}/findings`))?.url ?? "http://x",
  ).searchParams;
  expect(asked.getAll("status")).toEqual(["open", "investigating"]);
  expect([asked.get("assignee"), asked.get("sort"), asked.get("limit")]).toEqual([
    "me",
    "severity",
    "20",
  ]);
  expect(document.title).toBe("Home · NetTriage");
});

test("an organization with more assigned than shown links to the rest", async () => {
  fakeApi({
    "GET /api/v1/me": { body: memberOf(ACME) },
    [`GET /api/v1/orgs/${ORG_ID}/findings`]: {
      body: { findings: [finding(1, {})], next_cursor: "c2" },
    },
    [`GET /api/v1/orgs/${ORG_ID}/overview`]: { body: overview({ unresolved_mine: 6 }) },
  });

  renderAt("/app");

  expect(await screen.findByRole("link", { name: "5 more in Acme Security" })).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}/findings?assignee=me`,
  );
});

test("with nothing assigned, the person is told where to find work", async () => {
  fakeApi({
    "GET /api/v1/me": { body: memberOf(ACME) },
    [`GET /api/v1/orgs/${ORG_ID}/findings`]: NONE,
    [`GET /api/v1/orgs/${ORG_ID}/overview`]: { body: overview() },
  });

  renderAt("/app");

  expect(
    await screen.findByText(
      "Nothing is assigned to you. Pick up an unassigned finding in one of your organizations.",
    ),
  ).toBeInTheDocument();
});

test("each organization's card shows where its findings stand, and links to them", async () => {
  fakeApi({
    "GET /api/v1/me": { body: memberOf(ACME) },
    [`GET /api/v1/orgs/${ORG_ID}/findings`]: NONE,
    [`GET /api/v1/orgs/${ORG_ID}/overview`]: {
      body: overview({ last_upload: upload({ created_at: "2026-10-05T10:00:00Z" }) }),
    },
  });

  renderAt("/app");

  const card = await screen.findByRole("article", { name: "Acme Security" });
  expect(within(card).getByRole("link", { name: "Acme Security" })).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}`,
  );
  expect(within(card).getByText("Owner")).toBeInTheDocument();
  const critical = await within(card).findByRole("link", {
    name: "2 unresolved Critical findings",
  });
  expect(critical).toHaveAttribute("href", `/app/orgs/${ORG_ID}/findings?severity=critical`);
  expect(within(card).getByRole("link", { name: "0 unresolved Medium findings" })).toHaveClass(
    "zero",
  );
  const fact = (name: string) => within(card).getByText(name).nextElementSibling;
  expect(fact("Unassigned")).toHaveTextContent("7");
  expect(fact("Yours")).toHaveTextContent("2");
  expect(fact("Members")).toHaveTextContent("4");
  expect(fact("Last upload")).toHaveTextContent("2 h ago, analyzed");
  expect(within(card).getByText("31 resolved or false positive")).toBeInTheDocument();
  expect(within(card).getByRole("link", { name: "Open findings" })).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}/findings`,
  );
});

test("an organization with no uploads yet says so on its card", async () => {
  fakeApi({
    "GET /api/v1/me": { body: memberOf(ACME) },
    [`GET /api/v1/orgs/${ORG_ID}/findings`]: NONE,
    [`GET /api/v1/orgs/${ORG_ID}/overview`]: { body: overview({ last_upload: null }) },
  });

  renderAt("/app");

  const card = await screen.findByRole("article", { name: "Acme Security" });
  await vi.waitFor(() =>
    expect(within(card).getByText("Last upload").nextElementSibling).toHaveTextContent("None yet"),
  );
});
```

In `frontend/src/test/fixtures.ts`, replace:
```ts
import type { Org } from "../orgs/org";
import type { Member } from "../orgs/members";
import type { AiAnalysis } from "../findings/explanation";
import type { Finding } from "../findings/finding";
```
with:
```ts
import type { Org } from "../orgs/org";
import type { Member } from "../orgs/members";
import type { Overview } from "../orgs/overview";
import type { AiAnalysis } from "../findings/explanation";
import type { Finding } from "../findings/finding";
```

In `frontend/src/test/fixtures.ts`, replace:
```ts
    updated_at: "2026-10-04T09:32:02Z",
    ...fields,
  };
}
```
with:
```ts
    updated_at: "2026-10-04T09:32:02Z",
    ...fields,
  };
}

export function overview(fields: Partial<Overview> = {}): Overview {
  return {
    unresolved_by_severity: { critical: 2, high: 9, medium: 0, low: 3 },
    by_status: { open: 9, investigating: 5, resolved: 28, false_positive: 3 },
    unresolved_unassigned: 7,
    unresolved_mine: 2,
    new_last_day: 5,
    member_count: 4,
    last_upload: upload(),
    ...fields,
  };
}
```

Create `frontend/src/ui/FindingSubline.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { findingSummary } from "../test/fixtures";
import { FindingSubline, flowOf } from "./FindingSubline";

test("a flow reads from source to destination, with the port when there is one", () => {
  expect(flowOf(findingSummary({ dst_port: 22, dst_ip: "10.0.0.12", src_ip: "10.0.4.9" }))).toBe(
    "10.0.4.9 → 10.0.0.12:22",
  );
  expect(flowOf(findingSummary({ dst_port: null }))).toBe("10.0.3.17 → 10.0.0.5");
});

test("one port probed on many hosts has no single destination", () => {
  expect(flowOf(findingSummary({ dst_ip: null, dst_port: 445 }))).toBe(
    "10.0.3.17 → many hosts:445",
  );
});

test("IPv6 addresses are bracketed so the port stays readable", () => {
  expect(
    flowOf(findingSummary({ src_ip: "2001:db8::9", dst_ip: "2001:db8::5", dst_port: 22 })),
  ).toBe("[2001:db8::9] → [2001:db8::5]:22");
});

test("the subline names the detector and whether the AI explained the finding", () => {
  render(<FindingSubline finding={findingSummary({ ai_status: "succeeded" })} />);

  expect(screen.getByText("Port scan")).toBeInTheDocument();
  expect(screen.getByText("10.0.3.17 → 10.0.0.5")).toBeInTheDocument();
  expect(screen.getByText("Explained")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && pnpm exec vitest run src/pages/home/ src/pages/Orgs.test.tsx src/ui/FindingSubline.test.tsx`
Expected: FAIL: `6 failed | 7 passed`. `FindingSubline.test.tsx` stops on `Failed to resolve import "./FindingSubline"`, the 5 home tests don't find **Assigned to you** or the cards, and the onboarding test doesn't find the **Organizations** heading.

- [ ] **Step 3: Write the home**

Create `frontend/src/findings/assigned.ts`:
```ts
/** The unresolved findings assigned to the signed-in person, in each of their organizations. */
import { useQueries } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { Membership } from "../auth/session";
import type { FindingSummary } from "./findings";
import { severityLevel } from "./vocabulary";

/** At most this many per organization; the home links to the rest. */
export const ASSIGNED_LIMIT = 20;

/** Under the org's findings key, so a triage change anywhere in it reads them again. */
export function assignedKey(orgId: string) {
  return ["findings", orgId, "assigned-to-me"] as const;
}

export function useAssignedToMe(memberships: Membership[]) {
  return useQueries({
    queries: memberships.map((membership) => ({
      queryKey: assignedKey(membership.org_id),
      queryFn: async () =>
        unwrap(
          await api.GET("/api/v1/orgs/{org_id}/findings", {
            params: {
              path: { org_id: membership.org_id },
              query: {
                assignee: "me",
                status: ["open", "investigating"],
                sort: "severity",
                limit: ASSIGNED_LIMIT,
              },
            },
          }),
        ),
    })),
  });
}

/** Most severe first, then newest, across organizations. */
export function worstFirst(a: FindingSummary, b: FindingSummary): number {
  const bySeverity = severityLevel(b.severity) - severityLevel(a.severity);
  return bySeverity !== 0 ? bySeverity : Date.parse(b.created_at) - Date.parse(a.created_at);
}
```

In `frontend/src/pages/Orgs.tsx`, replace:
```tsx
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router";
import { api, idempotencyKey } from "../api/client";
import { unwrap } from "../api/problem";
import { ME_KEY, useMe } from "../auth/session";
import { roleLabel } from "../orgs/roles";
import { ErrorNotice } from "../ui/ErrorNotice";
import { usePageTitle } from "../ui/usePageTitle";

/** A person belongs to at most three organizations (spec §2.1, §7). */
```
with:
```tsx
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router";
import { api, idempotencyKey } from "../api/client";
import { unwrap } from "../api/problem";
import { ME_KEY, useMe } from "../auth/session";
import { ErrorNotice } from "../ui/ErrorNotice";
import { usePageTitle } from "../ui/usePageTitle";
import { AssignedToYou } from "./home/AssignedToYou";
import { OrgCard } from "./home/OrgCard";

/** A person belongs to at most three organizations (spec §2.1, §7). */
```

In `frontend/src/pages/Orgs.tsx`, replace:
```tsx
}

/** `/app`: the person's organizations, and creating one (spec §10). */
export function Orgs() {
  usePageTitle("Your organizations");
  const me = useMe();
  const memberships = me.data?.memberships ?? [];
  return (
    <main id="main" className="page">
      <h1>Your organizations</h1>
      {memberships.length === 0 ? (
        <p>Create your first organization, or open an invitation link someone sent you.</p>
      ) : (
        <ul className="org-list">
          {memberships.map((membership) => (
            <li key={membership.org_id}>
              <span className="org-who">
                <Link to={`/app/orgs/${membership.org_id}`}>{membership.name}</Link>
                <span className="mono muted">{membership.slug}</span>
              </span>
              <span className="badge">{roleLabel(membership.role)}</span>
            </li>
          ))}
        </ul>
      )}
      {memberships.length < MAX_ORGS ? (
        <CreateOrg />
```
with:
```tsx
}

/**
 * `/app`, the home: the findings assigned to the person across their organizations, then each
 * organization at a glance, and creating one (spec §10, Plan 6d).
 */
export function Orgs() {
  usePageTitle("Home");
  const me = useMe();
  const memberships = me.data?.memberships ?? [];
  return (
    <main id="main" className="page home">
      <h1 className="visually-hidden">Home</h1>
      {memberships.length > 0 && <AssignedToYou memberships={memberships} />}
      <section className="home-section" aria-labelledby="orgs-title">
        <div className="page-head">
          <h2 id="orgs-title" className="page-title">
            Organizations
          </h2>
        </div>
        {memberships.length === 0 ? (
          <p>Create your first organization, or open an invitation link someone sent you.</p>
        ) : (
          <div className="org-cards">
            {memberships.map((membership) => (
              <OrgCard key={membership.org_id} membership={membership} />
            ))}
          </div>
        )}
      </section>
      {memberships.length < MAX_ORGS ? (
        <CreateOrg />
```

Create `frontend/src/pages/home/AssignedToYou.tsx`:
```tsx
import { Link } from "react-router";
import type { Membership } from "../../auth/session";
import { useAssignedToMe, worstFirst } from "../../findings/assigned";
import { statusLabel } from "../../findings/vocabulary";
import { useOverview } from "../../orgs/overview";
import { Ago } from "../../ui/Ago";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { FindingSubline } from "../../ui/FindingSubline";
import { Loading } from "../../ui/Loading";
import { Severity } from "../../ui/Severity";

/** "N more in Acme": what the overview counts as yours, beyond what was fetched. */
function MoreIn({ membership, shown }: { membership: Membership; shown: number }) {
  const overview = useOverview(membership.org_id);
  const more = (overview.data?.unresolved_mine ?? 0) - shown;
  return (
    <Link to={`/app/orgs/${membership.org_id}/findings?assignee=me`}>
      {more > 0 ? `${more} more` : "More"} in {membership.name}
    </Link>
  );
}

/** The unresolved findings assigned to the person, in every organization, worst first (Plan 6d). */
export function AssignedToYou({ memberships }: { memberships: Membership[] }) {
  const results = useAssignedToMe(memberships);
  const rows = results.flatMap((result, index) =>
    (result.data?.findings ?? []).map((finding) => ({
      finding,
      membership: memberships[index] as Membership,
    })),
  );
  rows.sort((a, b) => worstFirst(a.finding, b.finding));
  const orgs = new Set(rows.map((row) => row.membership.org_id)).size;
  const loading = results.some((result) => result.isPending);
  const capped = results.flatMap((result, index) =>
    result.data?.next_cursor
      ? [{ membership: memberships[index] as Membership, shown: result.data.findings.length }]
      : [],
  );
  return (
    <section className="home-section" aria-labelledby="assigned-title">
      <div className="page-head">
        <h2 id="assigned-title" className="page-title">
          Assigned to you
        </h2>
        {!loading && rows.length > 0 && (
          <span className="muted">
            {rows.length} unresolved, in {orgs} {orgs === 1 ? "organization" : "organizations"}
          </span>
        )}
      </div>
      {results.map((result, index) =>
        result.isError ? (
          <ErrorNotice key={memberships[index]?.org_id} error={result.error} />
        ) : null,
      )}
      {loading && <Loading />}
      {!loading && rows.length === 0 && (
        <p className="muted">
          Nothing is assigned to you. Pick up an unassigned finding in one of your organizations.
        </p>
      )}
      {rows.length > 0 && (
        <table className="table">
          <caption className="visually-hidden">Findings assigned to you</caption>
          <thead>
            <tr>
              <th scope="col">Severity</th>
              <th scope="col">Finding</th>
              <th scope="col">Status</th>
              <th scope="col">Organization</th>
              <th scope="col">Detected</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ finding, membership }) => (
              <tr key={finding.id}>
                <td>
                  <Severity severity={finding.severity} />
                </td>
                <td>
                  <Link to={`/app/orgs/${membership.org_id}/findings/${finding.id}`}>
                    {finding.title}
                  </Link>
                  <FindingSubline finding={finding} />
                </td>
                <td>
                  <span className="state">{statusLabel(finding.status)}</span>
                </td>
                <td>{membership.name}</td>
                <td>
                  <Ago iso={finding.created_at} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {capped.map(({ membership, shown }) => (
        <p key={membership.org_id} className="more">
          <MoreIn membership={membership} shown={shown} />
        </p>
      ))}
    </section>
  );
}
```

Create `frontend/src/pages/home/OrgCard.tsx`:
```tsx
import { Link } from "react-router";
import type { Membership } from "../../auth/session";
import { SEVERITIES, severityLabel, severityLevel } from "../../findings/vocabulary";
import { type Overview, useOverview } from "../../orgs/overview";
import { roleLabel } from "../../orgs/roles";
import { Ago } from "../../ui/Ago";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { formatNumber } from "../../ui/format";
import { SeverityBars } from "../../ui/SeverityBars";
import { UPLOAD_STATUS_LABELS } from "../../uploads/uploads";

/** Unresolved findings by severity, each count a link to them; a zero is greyed, not hidden. */
function SeverityCounts({ base, overview }: { base: string; overview: Overview }) {
  return (
    <div className="mini-counts">
      {SEVERITIES.map((severity) => {
        const count = overview.unresolved_by_severity[severity];
        return (
          <Link
            key={severity}
            to={`${base}/findings?severity=${severity}`}
            className={count === 0 ? "zero" : undefined}
            aria-label={`${count} unresolved ${severityLabel(severity)} findings`}
          >
            <SeverityBars level={severityLevel(severity)} />
            <b>{formatNumber(count)}</b> {severityLabel(severity)}
          </Link>
        );
      })}
    </div>
  );
}

function Facts({ base, overview }: { base: string; overview: Overview }) {
  const upload = overview.last_upload;
  return (
    <dl className="org-facts">
      <div>
        <dt>Unassigned</dt>
        <dd>
          <Link to={`${base}/findings?assignee=none`}>
            {formatNumber(overview.unresolved_unassigned)}
          </Link>
        </dd>
      </div>
      <div>
        <dt>Yours</dt>
        <dd>
          <Link to={`${base}/findings?assignee=me`}>{formatNumber(overview.unresolved_mine)}</Link>
        </dd>
      </div>
      <div>
        <dt>Members</dt>
        <dd>{formatNumber(overview.member_count)}</dd>
      </div>
      <div>
        <dt>Last upload</dt>
        <dd>
          {upload === null ? (
            "None yet"
          ) : (
            <>
              <Ago iso={upload.created_at} />, {UPLOAD_STATUS_LABELS[upload.status].toLowerCase()}
            </>
          )}
        </dd>
      </div>
    </dl>
  );
}

/** One organization on the home page: how its findings stand, and the way into them (Plan 6d). */
export function OrgCard({ membership }: { membership: Membership }) {
  const overview = useOverview(membership.org_id);
  const base = `/app/orgs/${membership.org_id}`;
  const nameId = `org-${membership.org_id}`;
  const settled = overview.data
    ? overview.data.by_status.resolved + overview.data.by_status.false_positive
    : 0;
  return (
    <article className="org-card" aria-labelledby={nameId}>
      <div className="org-card-head">
        <h3 id={nameId}>
          <Link to={base}>{membership.name}</Link>
        </h3>
        <span className="muted">{roleLabel(membership.role)}</span>
      </div>
      {overview.isError && <ErrorNotice error={overview.error} />}
      {overview.data && (
        <>
          <SeverityCounts base={base} overview={overview.data} />
          <Facts base={base} overview={overview.data} />
        </>
      )}
      <div className="org-card-foot">
        <Link className="button button-primary" to={`${base}/findings`}>
          Open findings
        </Link>
        {overview.data && (
          <span className="muted">{formatNumber(settled)} resolved or false positive</span>
        )}
      </div>
    </article>
  );
}
```

In `frontend/src/pages/org/Uploads.tsx`, replace:
```tsx
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
```
with:
```tsx
import { Loading } from "../../ui/Loading";
import { usePageTitle } from "../../ui/usePageTitle";
import { type Upload, UPLOAD_STATUS_LABELS, useUploads } from "../../uploads/uploads";
import { UploadPanel } from "./UploadPanel";

function UploadRow({ upload }: { upload: Upload }) {
```

In `frontend/src/pages/org/Uploads.tsx`, replace:
```tsx
      <td className="mono">{upload.original_filename}</td>
      <td>
        <span className="state">{STATUS_LABELS[upload.status]}</span>
        {upload.failure_reason !== null && <div className="muted">{upload.failure_reason}</div>}
        {upload.findings_truncated > 0 && (
```
with:
```tsx
      <td className="mono">{upload.original_filename}</td>
      <td>
        <span className="state">{UPLOAD_STATUS_LABELS[upload.status]}</span>
        {upload.failure_reason !== null && <div className="muted">{upload.failure_reason}</div>}
        {upload.findings_truncated > 0 && (
```

In `frontend/src/styles.css`, replace:
```css
  border: 1px solid var(--graphite);
  background: transparent;
}

```
with:
```css
  border: 1px solid var(--graphite);
  background: transparent;
}

/* Under a finding's title in a list: its detector, its flow and the AI's mark */

.subline {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.15rem 0.9rem;
  margin-top: 0.2rem;
  font-size: 0.86rem;
  color: var(--graphite);
}

.subline .flow {
  font-family: var(--mono);
  font-stretch: 87.5%;
  font-size: 0.78rem;
  color: var(--ink);
}

```

In `frontend/src/styles.css`, replace:
```css
}

/* Your organizations */

.org-list {
  margin: 1.25rem 0 2rem;
  padding: 0;
  border: 1px solid var(--grid);
  border-radius: var(--radius);
  background: var(--plate);
  list-style: none;
}

.org-list li {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.9rem 1.1rem;
  border-bottom: 1px solid var(--row);
}

.org-list li:last-child {
  border-bottom: 0;
}

.org-who {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.25rem 0.9rem;
}

.org-list a {
  color: var(--ink);
  font-stretch: 108%;
  font-weight: 700;
  font-size: 1.1rem;
  text-decoration: none;
}

.org-list a:hover {
  text-decoration: underline;
}

```
with:
```css
}

/* Home: your work across organizations, then each organization at a glance (Plan 6d) */

.page-title {
  font-stretch: 112%;
  font-size: 1.95rem;
}

.home-section + .home-section,
.home-section + .setting {
  margin-top: 2.5rem;
}

.org-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(19rem, 1fr));
  gap: 1rem;
}

.org-card {
  display: grid;
  align-content: start;
  gap: 0.9rem;
  padding: 1.1rem 1.2rem;
  border: 1px solid var(--grid);
  border-radius: 10px;
  background: var(--plate);
}

.org-card-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.25rem 1rem;
}

.org-card-head h3 {
  font-stretch: 108%;
  font-weight: 750;
  font-size: 1.15rem;
}

.org-card-head a {
  color: var(--ink);
  text-decoration: none;
}

.org-card-head a:hover {
  text-decoration: underline;
}

.mini-counts {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem 1rem;
}

.mini-counts a {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  color: var(--ink);
  text-decoration: none;
  font-variant-numeric: tabular-nums;
}

.mini-counts a:hover {
  text-decoration: underline;
}

.mini-counts b {
  font-stretch: 112%;
  font-size: 1.15rem;
}

/* A severity with nothing unresolved stays in its place, greyed, so the four always line up. */
.mini-counts a.zero {
  color: var(--graphite);
}

.mini-counts a.zero .sev i.on {
  background: var(--signal-tint);
}

.org-facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.35rem 1rem;
  margin: 0;
  font-size: 0.92rem;
}

.org-facts div {
  display: flex;
  justify-content: space-between;
  gap: 0.5rem;
  padding-bottom: 0.3rem;
  border-bottom: 1px solid var(--row);
}

.org-facts dt {
  color: var(--graphite);
}

.org-facts dd {
  margin: 0;
  font-weight: 600;
  text-align: right;
}

.org-card-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem 1rem;
}

```

In `frontend/src/styles.css`, replace:
```css
    grid-template-columns: minmax(0, 1fr);
  }
}

```
with:
```css
    grid-template-columns: minmax(0, 1fr);
  }

  .org-facts {
    grid-template-columns: minmax(0, 1fr);
  }
}

```

Create `frontend/src/ui/FindingSubline.tsx`:
```tsx
import type { FindingSummary } from "../findings/findings";
import { detectorName } from "../findings/vocabulary";
import { AiMark } from "./AiMark";

/** An address and an optional port, IPv6 in brackets. */
function endpoint(ip: string, port: number | null): string {
  const host = ip.includes(":") ? `[${ip}]` : ip;
  return port === null ? host : `${host}:${port}`;
}

/** Where the traffic went: source → destination, with "many hosts" for one port on many. */
export function flowOf(finding: FindingSummary): string {
  const destination =
    finding.dst_ip === null
      ? `many hosts${finding.dst_port === null ? "" : `:${finding.dst_port}`}`
      : endpoint(finding.dst_ip, finding.dst_port);
  return `${endpoint(finding.src_ip, null)} → ${destination}`;
}

/** Under a finding's title in a list: its detector, its flow and whether the AI explained it. */
export function FindingSubline({ finding }: { finding: FindingSummary }) {
  return (
    <div className="subline">
      <span>{detectorName(finding.detector_id)}</span>
      <span className="flow">{flowOf(finding)}</span>
      <AiMark status={finding.ai_status} />
    </div>
  );
}
```

In `frontend/src/uploads/uploads.ts`, replace:
```ts
const ANALYSIS_MS = 2 * 60 * 60_000;
const POLL_MS = 3000;

export function uploadsKey(orgId: string) {
```
with:
```ts
const ANALYSIS_MS = 2 * 60 * 60_000;
const POLL_MS = 3000;

/** How an upload's state reads on screen. */
export const UPLOAD_STATUS_LABELS: Record<Upload["status"], string> = {
  pending_upload: "Waiting for the file",
  processing: "Analyzing",
  analyzed: "Analyzed",
  failed: "Failed",
  expired: "Expired",
};

export function uploadsKey(orgId: string) {
```

- [ ] **Step 4: Run the tests again**

Run: `cd frontend && pnpm exec vitest run src/pages/home/ src/pages/Orgs.test.tsx src/ui/FindingSubline.test.tsx`
Expected: `17 passed`.

- [ ] **Step 5: Check everything**

Run: `cd frontend && pnpm lint && pnpm exec tsc --noEmit && pnpm test`
Expected: lint and types are clean, and `209 passed`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(web): the home (findings assigned to you across organizations, worst first, and a card per organization with where its findings stand)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 6: Findings at a glance, and the new filters

**Files:**
- Create: `frontend/src/pages/org/Glance.tsx`
- Modify: `frontend/src/findings/findings.ts`, `frontend/src/findings/assigned.ts`, `frontend/src/orgs/overview.ts`, `frontend/src/pages/org/FindingFilters.tsx`, `frontend/src/pages/org/Findings.tsx`, `frontend/src/styles.css`
- Test: `frontend/src/findings/findings.test.ts`, `frontend/src/pages/org/Findings.test.tsx`, `frontend/src/pages/org/Glance.test.tsx`

**Interfaces:**
- Consumes: Task 4's `Ago`, `Person`, `Unassigned` and `useOverview`; Task 5's `FindingSubline` and `UPLOAD_STATUS_LABELS`; `useMe`, `useMembers`, `memberName`, `SeverityBars`, `formatNumber`.
- Produces:
  - in `src/findings/findings.ts`:
    - `UNRESOLVED` (`["open", "investigating"]`) and the type `Assignee`;
    - `FindingFilters` with `status?: Status | "any"`, `assignee?: "me" | "none"` and `new?: "day"`;
    - `filtersFrom` and `searchFrom` for `status=any`, `assignee` and `new`;
    - `apiQuery(filters, now)`, the API's query for those filters;
  - `unresolvedCount(overview)` and `findingCount(overview)` in `src/orgs/overview.ts`;
  - `<Glance filters={…} overview={…} />`: a `section` named "Findings at a glance".

- [ ] **Step 1: Write the failing tests**

In `frontend/src/findings/findings.test.ts`, replace:
```ts
import { expect, test } from "vitest";
import { filtersFrom, searchFrom } from "./findings";

test("findings open most severe first, with no filter", () => {
  expect(filtersFrom(new URLSearchParams())).toEqual({ sort: "severity" });
});
```
with:
```ts
import { expect, test } from "vitest";
import { apiQuery, filtersFrom, searchFrom } from "./findings";

test("findings open on the unresolved ones, most severe first, with no filter", () => {
  expect(filtersFrom(new URLSearchParams())).toEqual({ sort: "severity" });
});
```

In `frontend/src/findings/findings.test.ts`, replace:
```ts
test("filters and the order are read from the address", () => {
  const search = new URLSearchParams(
    "severity=high&status=investigating&detector=port_scan&upload=u1&sort=newest",
  );

```
with:
```ts
test("filters and the order are read from the address", () => {
  const search = new URLSearchParams(
    "severity=high&status=investigating&assignee=me&new=day&detector=port_scan&upload=u1&sort=newest",
  );

```

In `frontend/src/findings/findings.test.ts`, replace:
```ts
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

```
with:
```ts
    severity: "high",
    status: "investigating",
    assignee: "me",
    new: "day",
    detector: "port_scan",
    upload: "u1",
    sort: "newest",
  });
  expect(filtersFrom(new URLSearchParams("status=any&assignee=none"))).toEqual({
    status: "any",
    assignee: "none",
    sort: "severity",
  });
});

test("a value that isn't real is read as no filter", () => {
  expect(
    filtersFrom(
      new URLSearchParams("severity=urgent&status=closed&assignee=bob&new=week&sort=oldest"),
    ),
  ).toEqual({ sort: "severity" });
});

```

In `frontend/src/findings/findings.test.ts`, replace:
```ts
    "severity=low&sort=newest",
  );
});
```
with:
```ts
    "severity=low&sort=newest",
  );
  expect(
    searchFrom({ status: "any", new: "day", assignee: "none", sort: "severity" }).toString(),
  ).toBe("status=any&assignee=none&new=day");
});

const NOW = Date.parse("2026-10-05T12:00:00Z");

test("unresolved, the default, asks the API for Open and Investigating findings", () => {
  expect(apiQuery({ sort: "severity" }, NOW)).toEqual({
    sort: "severity",
    status: ["open", "investigating"],
  });
});

test("any status sends no status, and one status sends just that one", () => {
  expect(apiQuery({ status: "any", sort: "severity" }, NOW)).toEqual({ sort: "severity" });
  expect(apiQuery({ status: "resolved", sort: "newest" }, NOW)).toEqual({
    sort: "newest",
    status: ["resolved"],
  });
});

test("new in the last day asks for findings since 24 hours ago, beside the other filters", () => {
  expect(
    apiQuery(
      {
        status: "any",
        new: "day",
        assignee: "me",
        severity: "high",
        detector: "port_scan",
        upload: "u1",
        sort: "severity",
      },
      NOW,
    ),
  ).toEqual({
    sort: "severity",
    since: "2026-10-04T12:00:00.000Z",
    assignee: "me",
    severity: "high",
    detector: "port_scan",
    upload: "u1",
  });
});
```

In `frontend/src/pages/org/Findings.test.tsx`, replace:
```tsx
import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import {
  ADMIN,
```
with:
```tsx
import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Member } from "../../orgs/members";
import {
  ADMIN,
```

In `frontend/src/pages/org/Findings.test.tsx`, replace:
```tsx
  VIEWER,
  findingSummary,
  upload,
} from "../../test/fixtures";
import { ORG, signedInAs } from "../../test/orgApi";
import { renderAt } from "../../test/render";
```
with:
```tsx
  VIEWER,
  findingSummary,
  overview,
  upload,
} from "../../test/fixtures";
import type { Handler } from "../../test/fakeApi";
import { ORG, signedInAs } from "../../test/orgApi";
import { renderAt } from "../../test/render";
```

In `frontend/src/pages/org/Findings.test.tsx`, replace:
```tsx
const NONE = { body: { findings: [], next_cursor: null } };

function lastQuery(requests: Request[]): URLSearchParams {
  const reads = requests.filter((request) => new URL(request.url).pathname.endsWith("/findings"));
```
with:
```tsx
const NONE = { body: { findings: [], next_cursor: null } };

const NOTHING = overview({
  unresolved_by_severity: { critical: 0, high: 0, medium: 0, low: 0 },
  by_status: { open: 0, investigating: 0, resolved: 0, false_positive: 0 },
  unresolved_unassigned: 0,
  unresolved_mine: 0,
  new_last_day: 0,
  last_upload: null,
});

/** Signed in, with the organization's overview the glance reads (Plan 6d). */
function signedIn(self: Member, extra: Record<string, Handler> = {}) {
  return signedInAs(self, { [`GET ${ORG}/overview`]: { body: overview() }, ...extra });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

function lastQuery(requests: Request[]): URLSearchParams {
  const reads = requests.filter((request) => new URL(request.url).pathname.endsWith("/findings"));
```

In `frontend/src/pages/org/Findings.test.tsx`, replace:
```tsx
}

test("an organization opens on its findings, most severe first", async () => {
  const fake = signedInAs(OWNER, {
    [FINDINGS]: {
      body: {
        findings: [findingSummary({ status: "investigating", assignee_id: ADMIN.user_id })],
        next_cursor: null,
      },
```
with:
```tsx
}

test("an organization opens on its unresolved findings, most severe first", async () => {
  const fake = signedIn(OWNER, {
    [FINDINGS]: {
      body: {
        findings: [
          findingSummary({
            status: "investigating",
            assignee_id: ADMIN.user_id,
            ai_status: "succeeded",
          }),
        ],
        next_cursor: null,
      },
```

In `frontend/src/pages/org/Findings.test.tsx`, replace:
```tsx
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
```
with:
```tsx
  expect(within(row).getByText("Investigating")).toBeInTheDocument();
  expect(await within(row).findByText("Ben Admin")).toBeInTheDocument();
  expect(within(row).getByText("BA")).toHaveAttribute("aria-hidden", "true");
  expect(within(row).getByText("Port scan")).toBeInTheDocument();
  expect(within(row).getByText("10.0.3.17 → 10.0.0.5")).toBeInTheDocument();
  expect(within(row).getByText("Explained")).toBeInTheDocument();
  expect(within(row).getByText("yesterday")).toHaveAttribute("dateTime", "2026-10-04T09:31:00Z");
  expect(screen.getByRole("columnheader", { name: "Detected" })).toBeInTheDocument();
  expect(lastQuery(fake.requests).get("sort")).toBe("severity");
  expect(lastQuery(fake.requests).getAll("status")).toEqual(["open", "investigating"]);
  expect(screen.getByLabelText("Status")).toHaveDisplayValue("Unresolved");
  expect(screen.getByRole("link", { name: "Findings" })).toHaveAttribute("aria-current", "page");
  expect(document.title).toBe("Findings · Acme Security · NetTriage");
});

test("a finding assigned to the signed-in person says You, and one nobody has says Unassigned", async () => {
  signedIn(OWNER, {
    [FINDINGS]: {
      body: {
        findings: [
          findingSummary({ assignee_id: OWNER.user_id }),
          findingSummary({ id: "01a10600-0000-7000-8000-000000000002", title: "RDP attempts" }),
        ],
        next_cursor: null,
      },
    },
  });

  renderAt(PAGE);

  const mine = await screen.findByRole("row", { name: /Port scan of 10\.0\.0\.5/ });
  expect(await within(mine).findByText("You")).toBeInTheDocument();
  const nobody = screen.getByRole("row", { name: /RDP attempts/ });
  expect(within(nobody).getByText("Unassigned")).toBeInTheDocument();
});

test("filters and the order are kept in the address and sent to the API", async () => {
  const fake = signedIn(OWNER, {
    [FINDINGS]: { body: { findings: [findingSummary()], next_cursor: null } },
  });
```

In `frontend/src/pages/org/Findings.test.tsx`, replace:
```tsx
  await user.selectOptions(await screen.findByLabelText("Severity"), "High");
  await user.selectOptions(screen.getByLabelText("Status"), "Investigating");
  await user.selectOptions(screen.getByLabelText("Detector"), "SSH/RDP brute force");
  await user.selectOptions(screen.getByLabelText("Sort"), "Newest first");
```
with:
```tsx
  await user.selectOptions(await screen.findByLabelText("Severity"), "High");
  await user.selectOptions(screen.getByLabelText("Status"), "Investigating");
  await user.selectOptions(screen.getByLabelText("Assignee"), "Yours");
  await user.selectOptions(screen.getByLabelText("Detector"), "SSH/RDP brute force");
  await user.selectOptions(screen.getByLabelText("Sort"), "Newest first");
```

In `frontend/src/pages/org/Findings.test.tsx`, replace:
```tsx
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
```
with:
```tsx
  await vi.waitFor(() => expect(lastQuery(fake.requests).get("sort")).toBe("newest"));
  const sent = lastQuery(fake.requests);
  expect([sent.getAll("status"), sent.get("severity"), sent.get("assignee")]).toEqual([
    ["investigating"],
    "high",
    "me",
  ]);
  expect(sent.get("detector")).toBe("remote_access_bruteforce");
  expect(router.state.location.search).toBe(
    "?severity=high&status=investigating&assignee=me&detector=remote_access_bruteforce&sort=newest",
  );
});

test("any status asks for every finding, and the address says so", async () => {
  const fake = signedIn(OWNER, { [FINDINGS]: NONE });
  const { user, router } = renderAt(PAGE);

  await user.selectOptions(await screen.findByLabelText("Status"), "Any status");

  await vi.waitFor(() => expect(router.state.location.search).toBe("?status=any"));
  await vi.waitFor(() => expect(lastQuery(fake.requests).has("status")).toBe(false));
});

test("new in the last day asks the API for findings since 24 hours ago", async () => {
  const fake = signedIn(OWNER, { [FINDINGS]: NONE });

  const { user, router } = renderAt(`${PAGE}?status=any&new=day`);

  // vi.waitFor moves the faked clock on a little each time it looks.
  await vi.waitFor(() =>
    expect(lastQuery(fake.requests).get("since")).toMatch(/^2026-10-04T12:00:0/),
  );
  expect(lastQuery(fake.requests).has("status")).toBe(false);
  expect(screen.getByText(/Detected in the last day./)).toBeInTheDocument();
  await user.click(screen.getByRole("link", { name: "Show findings from any time" }));

  expect(router.state.location.search).toBe("?status=any");
});

test("an address with values that aren't real is read as no filter", async () => {
  const fake = signedIn(OWNER, { [FINDINGS]: NONE });

  renderAt(`${PAGE}?severity=urgent&status=closed&assignee=bob&new=week&sort=oldest`);

  await screen.findByRole("heading", { level: 1, name: "Findings" });
  await vi.waitFor(() => expect(lastQuery(fake.requests).get("sort")).toBe("severity"));
  const sent = lastQuery(fake.requests);
  expect(["severity", "assignee", "since"].filter((name) => sent.has(name))).toEqual([]);
  expect(sent.getAll("status")).toEqual(["open", "investigating"]);
  expect(screen.getByLabelText("Severity")).toHaveValue("");
  expect(screen.getByLabelText("Status")).toHaveDisplayValue("Unresolved");
  expect(screen.getByLabelText("Assignee")).toHaveDisplayValue("Anyone");
});

test("findings from one upload name it, with a way back to every upload's findings", async () => {
  const fake = signedIn(OWNER, {
    [FINDINGS]: { body: { findings: [findingSummary()], next_cursor: null } },
    [`GET ${ORG}/uploads/${UPLOAD_ID}`]: { body: upload() },
```

In `frontend/src/pages/org/Findings.test.tsx`, replace:
```tsx

test("with no findings yet, a contributor is pointed to uploading", async () => {
  signedInAs(OWNER, { [FINDINGS]: NONE });

  renderAt(PAGE);
```
with:
```tsx

test("with no findings yet, a contributor is pointed to uploading", async () => {
  signedIn(OWNER, { [FINDINGS]: NONE, [`GET ${ORG}/overview`]: { body: NOTHING } });

  renderAt(PAGE);
```

In `frontend/src/pages/org/Findings.test.tsx`, replace:
```tsx

test("with no findings yet, a viewer is told where they come from", async () => {
  signedInAs(VIEWER, { [FINDINGS]: NONE });

  renderAt(PAGE);
```
with:
```tsx

test("with no findings yet, a viewer is told where they come from", async () => {
  signedIn(VIEWER, { [FINDINGS]: NONE, [`GET ${ORG}/overview`]: { body: NOTHING } });

  renderAt(PAGE);
```

In `frontend/src/pages/org/Findings.test.tsx`, replace:
```tsx
});

test("no finding matching the filters offers to clear them", async () => {
  signedInAs(OWNER, { [FINDINGS]: NONE });
  const { user, router } = renderAt(`${PAGE}?severity=low&sort=newest`);

```
with:
```tsx
});

test("with nothing unresolved, the person is offered every finding", async () => {
  signedIn(OWNER, {
    [FINDINGS]: NONE,
    [`GET ${ORG}/overview`]: {
      body: overview({
        ...NOTHING,
        by_status: { open: 0, investigating: 0, resolved: 4, false_positive: 1 },
      }),
    },
  });
  const { user, router } = renderAt(PAGE);

  expect(await screen.findByText("Nothing here is unresolved.")).toBeInTheDocument();
  await user.click(screen.getByRole("link", { name: "Show every finding" }));

  expect(router.state.location.search).toBe("?status=any");
});

test("no finding matching the filters offers to clear them", async () => {
  signedIn(OWNER, { [FINDINGS]: NONE });
  const { user, router } = renderAt(`${PAGE}?severity=low&sort=newest`);

```

In `frontend/src/pages/org/Findings.test.tsx`, replace:
```tsx
});

test("more findings load a page at a time", async () => {
  const older = findingSummary({ id: "01a10600-0000-7000-8000-000000000002", title: "Older scan" });
  signedInAs(OWNER, {
    [FINDINGS]: (request) =>
      new URL(request.url).searchParams.get("cursor") === "c2"
```
with:
```tsx
});

test("more findings load a page at a time, under the same filters", async () => {
  const older = findingSummary({ id: "01a10600-0000-7000-8000-000000000002", title: "Older scan" });
  const fake = signedIn(OWNER, {
    [FINDINGS]: (request) =>
      new URL(request.url).searchParams.get("cursor") === "c2"
```

In `frontend/src/pages/org/Findings.test.tsx`, replace:
```tsx
        : { body: { findings: [findingSummary()], next_cursor: "c2" } },
  });
  const { user } = renderAt(PAGE);

  await user.click(await screen.findByRole("button", { name: "Show more findings" }));
```
with:
```tsx
        : { body: { findings: [findingSummary()], next_cursor: "c2" } },
  });
  const { user } = renderAt(`${PAGE}?assignee=none`);

  await user.click(await screen.findByRole("button", { name: "Show more findings" }));
```

In `frontend/src/pages/org/Findings.test.tsx`, replace:
```tsx
  expect(await screen.findByRole("link", { name: "Older scan" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Show more findings" })).toBeNull();
});

test("a list the API refuses says why", async () => {
  signedInAs(OWNER, {
    [FINDINGS]: {
      status: 503,
```
with:
```tsx
  expect(await screen.findByRole("link", { name: "Older scan" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Show more findings" })).toBeNull();
  const second = lastQuery(fake.requests);
  expect([second.get("cursor"), second.get("assignee")]).toEqual(["c2", "none"]);
  expect(second.getAll("status")).toEqual(["open", "investigating"]);
});

test("an overview the API refuses says why, and the findings are still listed", async () => {
  signedIn(OWNER, {
    [FINDINGS]: { body: { findings: [findingSummary()], next_cursor: null } },
    [`GET ${ORG}/overview`]: {
      status: 503,
      body: { title: "Service Unavailable", detail: "Try again.", trace_id: "t9" },
    },
  });

  renderAt(PAGE);

  expect(await screen.findByRole("alert")).toHaveTextContent("Try again. (reference t9)");
  expect(await screen.findByRole("row", { name: /Port scan of 10\.0\.0\.5/ })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Findings at a glance" })).toBeNull();
});

test("a list the API refuses says why", async () => {
  signedIn(OWNER, {
    [FINDINGS]: {
      status: 503,
```

Create `frontend/src/pages/org/Glance.test.tsx`:
```tsx
import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ORG_ID, OWNER, UPLOAD_ID, overview, upload } from "../../test/fixtures";
import { ORG, signedInAs } from "../../test/orgApi";
import { renderAt } from "../../test/render";

const PAGE = `/app/orgs/${ORG_ID}/findings`;
const NONE = { body: { findings: [], next_cursor: null } };

function showing(body = overview()) {
  return signedInAs(OWNER, {
    [`GET ${ORG}/findings`]: NONE,
    [`GET ${ORG}/overview`]: { body },
  });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

test("the heading counts the unresolved findings among all of them", async () => {
  showing();

  renderAt(PAGE);

  expect(await screen.findByText("14 unresolved of 45")).toBeInTheDocument();
});

test("each severity's tile counts its unresolved findings and lists them", async () => {
  showing();
  const { user, router } = renderAt(PAGE);

  const glance = await screen.findByRole("region", { name: "Findings at a glance" });
  const critical = await within(glance).findByRole("link", {
    name: "2 unresolved Critical findings",
  });
  expect(critical).toHaveClass("rung");
  expect(within(glance).getByRole("link", { name: "0 unresolved Medium findings" })).toHaveClass(
    "zero",
  );
  await user.click(critical);

  expect(router.state.location.search).toBe("?severity=critical");
});

test("quick views list yours, the unassigned and the new, and the one in use looks pressed", async () => {
  showing();

  renderAt(`${PAGE}?assignee=me`);

  const yours = await screen.findByRole("link", { name: "Yours 2" });
  expect(yours).toHaveAttribute("href", `${PAGE}?assignee=me`);
  expect(yours).toHaveAttribute("aria-current", "true");
  const unassigned = screen.getByRole("link", { name: "Unassigned 7" });
  expect(unassigned).toHaveAttribute("href", `${PAGE}?assignee=none`);
  expect(unassigned).not.toHaveAttribute("aria-current");
  expect(screen.getByRole("link", { name: "New in the last day 5" })).toHaveAttribute(
    "href",
    `${PAGE}?status=any&new=day`,
  );
});

test("a quick view with other filters beside it isn't the one in use", async () => {
  showing();

  renderAt(`${PAGE}?assignee=me&severity=high`);

  const yours = await screen.findByRole("link", { name: "Yours 2" });
  expect(yours).not.toHaveAttribute("aria-current");
  // A count lists exactly what it counted: its view replaces the other filters.
  expect(yours).toHaveAttribute("href", `${PAGE}?assignee=me`);
});

test("an organization with no findings shows its counts at zero, and an empty track", async () => {
  showing(
    overview({
      unresolved_by_severity: { critical: 0, high: 0, medium: 0, low: 0 },
      by_status: { open: 0, investigating: 0, resolved: 0, false_positive: 0 },
      unresolved_unassigned: 0,
      unresolved_mine: 0,
      new_last_day: 0,
      last_upload: null,
    }),
  );

  renderAt(PAGE);

  expect(await screen.findByText("All 0, by status")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "0 unresolved Critical findings" })).toHaveClass("zero");
  expect(document.querySelectorAll("svg.status-track > rect")).toHaveLength(0);
});

test("the status track splits every finding by status, and its legend links to each", async () => {
  showing();

  renderAt(PAGE);

  expect(await screen.findByText("All 45, by status")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "9 Open findings" })).toHaveAttribute(
    "href",
    `${PAGE}?status=open`,
  );
  expect(screen.getByRole("link", { name: "3 False positive findings" })).toHaveAttribute(
    "href",
    `${PAGE}?status=false_positive`,
  );
  const track = document.querySelector("svg.status-track");
  expect(track).toHaveAttribute("aria-hidden", "true");
  const widths = Array.from(track?.querySelectorAll(":scope > rect") ?? []).map((rect) => [
    rect.getAttribute("class"),
    rect.getAttribute("x"),
    rect.getAttribute("width"),
  ]);
  expect(widths).toEqual([
    ["track-open", "0%", "20%"],
    ["track-investigating", "20%", "11.111%"],
    ["track-resolved", "31.111%", "62.222%"],
    ["track-false-positive", "93.333%", "6.667%"],
  ]);
});

test("the last upload is named, with how long ago it came and how many findings it had", async () => {
  showing(
    overview({
      last_upload: upload({
        created_at: "2026-10-05T10:00:00Z",
        findings: 6,
        worst_severity: "high",
      }),
    }),
  );

  renderAt(PAGE);

  const link = await screen.findByRole("link", { name: "port-scan.log" });
  expect(link).toHaveAttribute("href", `${PAGE}?status=any&upload=${UPLOAD_ID}`);
  expect(link.closest("p")).toHaveTextContent("Last upload port-scan.log, 2 h ago: 6 findings.");
});

test("an upload still being analyzed says so instead of its findings", async () => {
  showing(
    overview({ last_upload: upload({ status: "processing", created_at: "2026-10-05T11:58:00Z" }) }),
  );

  renderAt(PAGE);

  expect(
    (await screen.findByRole("link", { name: "port-scan.log" })).closest("p"),
  ).toHaveTextContent("Last upload port-scan.log, 2 min ago: analyzing.");
});

test("with no uploads, the glance says so", async () => {
  showing(overview({ last_upload: null }));

  renderAt(PAGE);

  expect(await screen.findByText("No uploads yet.")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && pnpm exec vitest run src/findings/findings.test.ts src/pages/org/Findings.test.tsx src/pages/org/Glance.test.tsx`
Expected: FAIL: `23 failed | 7 passed`. In `findings.test.ts`, 5 fail on `apiQuery` and the new filters. In the page's tests, 9 fail on the missing Unresolved default, Assignee filter, row details and empty states. All 9 glance tests fail on the missing panel.

- [ ] **Step 3: Write the filters, the glance and the rows**

In `frontend/src/findings/assigned.ts`, replace:
```ts
import { unwrap } from "../api/problem";
import type { Membership } from "../auth/session";
import type { FindingSummary } from "./findings";
import { severityLevel } from "./vocabulary";

```
with:
```ts
import { unwrap } from "../api/problem";
import type { Membership } from "../auth/session";
import { type FindingSummary, UNRESOLVED } from "./findings";
import { severityLevel } from "./vocabulary";

```

In `frontend/src/findings/assigned.ts`, replace:
```ts
              query: {
                assignee: "me",
                status: ["open", "investigating"],
                sort: "severity",
                limit: ASSIGNED_LIMIT,
```
with:
```ts
              query: {
                assignee: "me",
                status: [...UNRESOLVED],
                sort: "severity",
                limit: ASSIGNED_LIMIT,
```

In `frontend/src/findings/findings.ts`, replace:
```ts
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
```
with:
```ts
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { components, operations } from "../api/schema";
import { DETECTORS, type Severity, type Status, isSeverity, isStatus } from "./vocabulary";

export type FindingSummary = components["schemas"]["FindingSummaryOut"];
export type Sort = "severity" | "newest";
export type Assignee = "me" | "none";
type Query = NonNullable<
  operations["findings_api_v1_orgs__org_id__findings_get"]["parameters"]["query"]
>;

/** Still to be dealt with (Plan 6d): what the list shows unless the person picks a status. */
export const UNRESOLVED: readonly Status[] = ["open", "investigating"];

const DAY_MS = 86_400_000;

export interface FindingFilters {
  severity?: Severity;
  /** Absent: Unresolved, the default (Plan 6d). `any`: every status. */
  status?: Status | "any";
  /** `me`: assigned to the signed-in person; `none`: to nobody. */
  assignee?: Assignee;
  /** `day`: detected in the last 24 hours. */
  new?: "day";
  detector?: string;
  upload?: string;
```

In `frontend/src/findings/findings.ts`, replace:
```ts
  const severity = search.get("severity");
  const status = search.get("status");
  const detector = search.get("detector");
  const upload = search.get("upload");
```
with:
```ts
  const severity = search.get("severity");
  const status = search.get("status");
  const assignee = search.get("assignee");
  const detector = search.get("detector");
  const upload = search.get("upload");
```

In `frontend/src/findings/findings.ts`, replace:
```ts
    filters.severity = severity;
  }
  if (isStatus(status)) {
    filters.status = status;
  }
  if (detector !== null && detector in DETECTORS) {
```
with:
```ts
    filters.severity = severity;
  }
  if (status === "any" || isStatus(status)) {
    filters.status = status;
  }
  if (assignee === "me" || assignee === "none") {
    filters.assignee = assignee;
  }
  if (search.get("new") === "day") {
    filters.new = "day";
  }
  if (detector !== null && detector in DETECTORS) {
```

In `frontend/src/findings/findings.ts`, replace:
```ts
export function searchFrom(filters: FindingFilters): URLSearchParams {
  const search = new URLSearchParams();
  for (const name of ["severity", "status", "detector", "upload"] as const) {
    const value = filters[name];
    if (value !== undefined) {
```
with:
```ts
export function searchFrom(filters: FindingFilters): URLSearchParams {
  const search = new URLSearchParams();
  for (const name of ["severity", "status", "assignee", "new", "detector", "upload"] as const) {
    const value = filters[name];
    if (value !== undefined) {
```

In `frontend/src/findings/findings.ts`, replace:
```ts
}

export function useFindings(orgId: string, filters: FindingFilters) {
  // The API takes any of several statuses (Plan 6d).
  const { status, ...rest } = filters;
  return useInfiniteQuery({
    queryKey: findingsKey(orgId, filters),
```
with:
```ts
}

/** What the API is asked for: Unresolved is two statuses, and "new" a moment a day before now. */
export function apiQuery(filters: FindingFilters, now: number): Query {
  const { status, new: recent, ...rest } = filters;
  const query: Query = { ...rest };
  if (status === undefined) {
    query.status = [...UNRESOLVED];
  } else if (status !== "any") {
    query.status = [status];
  }
  if (recent === "day") {
    query.since = new Date(now - DAY_MS).toISOString();
  }
  return query;
}

export function useFindings(orgId: string, filters: FindingFilters) {
  return useInfiniteQuery({
    queryKey: findingsKey(orgId, filters),
```

In `frontend/src/findings/findings.ts`, replace:
```ts
            path: { org_id: orgId },
            query: {
              ...rest,
              ...(status ? { status: [status] } : {}),
              ...(pageParam ? { cursor: pageParam } : {}),
            },
```
with:
```ts
            path: { org_id: orgId },
            query: {
              ...apiQuery(filters, Date.now()),
              ...(pageParam ? { cursor: pageParam } : {}),
            },
```

In `frontend/src/orgs/overview.ts`, replace:
```ts
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";

export type Overview = components["schemas"]["OverviewOut"];

export function overviewKey(orgId: string) {
```
with:
```ts
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";
import { SEVERITIES, STATUSES } from "../findings/vocabulary";

export type Overview = components["schemas"]["OverviewOut"];

/** Open or Investigating, of every severity. */
export function unresolvedCount(overview: Overview): number {
  return SEVERITIES.reduce((sum, severity) => sum + overview.unresolved_by_severity[severity], 0);
}

/** Every finding the organization keeps, whatever its status. */
export function findingCount(overview: Overview): number {
  return STATUSES.reduce((sum, status) => sum + overview.by_status[status], 0);
}

export function overviewKey(orgId: string) {
```

In `frontend/src/pages/org/FindingFilters.tsx`, replace:
```tsx
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
```
with:
```tsx
  onChange: (filters: Filters) => void;
}) {
  function set(name: "severity" | "status" | "assignee" | "detector", value: string) {
    const next: Filters = { ...filters };
    if (name === "severity") {
      next.severity = isSeverity(value) ? value : undefined;
    } else if (name === "status") {
      next.status = value === "any" || isStatus(value) ? value : undefined;
    } else if (name === "assignee") {
      next.assignee = value === "me" || value === "none" ? value : undefined;
    } else {
      next.detector = value === "" ? undefined : value;
```

In `frontend/src/pages/org/FindingFilters.tsx`, replace:
```tsx
          onChange={(event) => set("status", event.target.value)}
        >
          <option value="">Any status</option>
          {STATUSES.map((status) => (
            <option key={status} value={status}>
```
with:
```tsx
          onChange={(event) => set("status", event.target.value)}
        >
          <option value="">Unresolved</option>
          <option value="any">Any status</option>
          {STATUSES.map((status) => (
            <option key={status} value={status}>
```

In `frontend/src/pages/org/FindingFilters.tsx`, replace:
```tsx
              {statusLabel(status)}
            </option>
          ))}
        </select>
      </div>
```
with:
```tsx
              {statusLabel(status)}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="filter-assignee">Assignee</label>
        <select
          id="filter-assignee"
          value={filters.assignee ?? ""}
          onChange={(event) => set("assignee", event.target.value)}
        >
          <option value="">Anyone</option>
          <option value="me">Yours</option>
          <option value="none">Unassigned</option>
        </select>
      </div>
```

In `frontend/src/pages/org/Findings.tsx`, replace:
```tsx
import { api } from "../../api/client";
import { unwrap } from "../../api/problem";
import {
  type FindingFilters as Filters,
```
with:
```tsx
import { api } from "../../api/client";
import { unwrap } from "../../api/problem";
import { useMe } from "../../auth/session";
import {
  type FindingFilters as Filters,
```

In `frontend/src/pages/org/Findings.tsx`, replace:
```tsx
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
```
with:
```tsx
  useFindings,
} from "../../findings/findings";
import { statusLabel } from "../../findings/vocabulary";
import { type Member, memberName, useMembers } from "../../orgs/members";
import { useOrg } from "../../orgs/org";
import { type Overview, findingCount, unresolvedCount, useOverview } from "../../orgs/overview";
import { canContribute } from "../../orgs/permissions";
import { Ago } from "../../ui/Ago";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { FindingSubline } from "../../ui/FindingSubline";
import { formatNumber } from "../../ui/format";
import { Loading } from "../../ui/Loading";
import { Person, Unassigned } from "../../ui/Person";
import { Severity } from "../../ui/Severity";
import { usePageTitle } from "../../ui/usePageTitle";
import { FindingFilters } from "./FindingFilters";
import { Glance } from "./Glance";

/** Which upload the list is narrowed to, by its file name, and the way back to all of them. */
```

In `frontend/src/pages/org/Findings.tsx`, replace:
```tsx
}

function FindingRow({ finding, members }: { finding: FindingSummary; members?: Member[] }) {
  return (
    <tr>
```
with:
```tsx
}

/** A "new in the last day" list says so, with the way back to any time; no select shows it. */
function NewScope({ filters }: { filters: Filters }) {
  return (
    <p className="scope">
      Detected in the last day.{" "}
      <Link to={{ search: searchFrom({ ...filters, new: undefined }).toString() }}>
        Show findings from any time
      </Link>
    </p>
  );
}

function FindingRow({
  finding,
  members,
  self,
}: {
  finding: FindingSummary;
  members?: Member[];
  self?: string;
}) {
  return (
    <tr>
```

In `frontend/src/pages/org/Findings.tsx`, replace:
```tsx
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
```
with:
```tsx
      <td>
        <Link to={finding.id}>{finding.title}</Link>
        <FindingSubline finding={finding} />
      </td>
      <td>
        <span className="state">{statusLabel(finding.status)}</span>
      </td>
      <td>
        {finding.assignee_id === null ? (
          <Unassigned />
        ) : (
          <Person
            name={memberName(members, finding.assignee_id)}
            you={finding.assignee_id === self}
          />
        )}
      </td>
      <td>
        <Ago iso={finding.created_at} />
      </td>
    </tr>
  );
}

/** "14 unresolved of 45": the organization's, whatever the filters. */
function UnresolvedNote({ overview }: { overview: Overview }) {
  return (
    <span className="muted">
      {formatNumber(unresolvedCount(overview))} unresolved of {formatNumber(findingCount(overview))}
    </span>
  );
}
```

In `frontend/src/pages/org/Findings.tsx`, replace:
```tsx
function NoFindings({
  filtered,
  contributor,
  onClear,
}: {
  filtered: boolean;
  contributor: boolean;
  onClear: () => void;
```
with:
```tsx
function NoFindings({
  filtered,
  closedOnly,
  contributor,
  onClear,
}: {
  filtered: boolean;
  closedOnly: boolean;
  contributor: boolean;
  onClear: () => void;
```

In `frontend/src/pages/org/Findings.tsx`, replace:
```tsx
          Clear filters
        </button>
      </div>
    );
```
with:
```tsx
          Clear filters
        </button>
      </div>
    );
  }
  if (closedOnly) {
    return (
      <div className="empty">
        <p>Nothing here is unresolved.</p>
        <p>
          Every finding is resolved or a false positive.{" "}
          <Link to={{ search: searchFrom({ status: "any", sort: "severity" }).toString() }}>
            Show every finding
          </Link>
        </p>
      </div>
    );
```

In `frontend/src/pages/org/Findings.tsx`, replace:
```tsx
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
```
with:
```tsx
  const filters = filtersFrom(search);
  const findings = useFindings(org.id, filters);
  const overview = useOverview(org.id);
  const members = useMembers(org.id);
  const me = useMe();
  const all = findings.data?.pages.flatMap((page) => page.findings) ?? [];
  const change = (next: Filters) => setSearch(searchFrom(next));
  // Unresolved and Any status aren't narrowing filters: with either alone, an empty list is empty.
  const filtered =
    [filters.severity, filters.assignee, filters.new, filters.detector, filters.upload].some(
      (value) => value !== undefined,
    ) ||
    (filters.status !== undefined && filters.status !== "any");
  const total = overview.data ? findingCount(overview.data) : null;
  return (
    <main id="main" className="page">
      <div className="page-head">
        <h1>Findings</h1>
        {overview.data && <UnresolvedNote overview={overview.data} />}
      </div>
      {overview.isError && <ErrorNotice error={overview.error} />}
      {overview.data && <Glance filters={filters} overview={overview.data} />}
      <FindingFilters filters={filters} onChange={change} />
      {filters.upload !== undefined && (
        <UploadScope orgId={org.id} filters={{ ...filters, upload: filters.upload }} />
      )}
      {filters.new !== undefined && <NewScope filters={filters} />}
      {findings.isPending && <Loading />}
      {findings.isError && <ErrorNotice error={findings.error} />}
      {findings.isSuccess && !overview.isPending && all.length === 0 && (
        <NoFindings
          filtered={filtered}
          closedOnly={filters.status === undefined && total !== 0}
          contributor={canContribute(org.role)}
          onClear={() => change({ sort: filters.sort })}
```

In `frontend/src/pages/org/Findings.tsx`, replace:
```tsx
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
```
with:
```tsx
              <th scope="col">Status</th>
              <th scope="col">Assignee</th>
              <th scope="col">Detected</th>
            </tr>
          </thead>
          <tbody>
            {all.map((finding) => (
              <FindingRow
                key={finding.id}
                finding={finding}
                members={members.data}
                self={me.data?.user.id}
              />
            ))}
          </tbody>
```

Create `frontend/src/pages/org/Glance.tsx`:
```tsx
import { Link } from "react-router";
import { type FindingFilters as Filters, searchFrom } from "../../findings/findings";
import {
  SEVERITIES,
  STATUSES,
  type Status,
  severityLabel,
  severityLevel,
  statusLabel,
} from "../../findings/vocabulary";
import { type Overview, findingCount } from "../../orgs/overview";
import { Ago } from "../../ui/Ago";
import { formatNumber } from "../../ui/format";
import { SeverityBars } from "../../ui/SeverityBars";
import { UPLOAD_STATUS_LABELS, type Upload } from "../../uploads/uploads";

/** Each number links to exactly the findings it counts: its view replaces the other filters. */
function view(filters: Filters, preset: Omit<Filters, "sort">): string {
  return `?${searchFrom({ ...preset, sort: filters.sort }).toString()}`;
}

function sameView(filters: Filters, preset: Omit<Filters, "sort">): boolean {
  return view(filters, preset) === view(filters, { ...filters });
}

const TRACK_CLASSES: Record<Status, string> = {
  open: "track-open",
  investigating: "track-investigating",
  resolved: "track-resolved",
  false_positive: "track-false-positive",
};

/** A share of the track's width, to three decimals: the CSP allows no inline style, so SVG
 * attributes carry the geometry, and percentages keep the hatching unstretched. */
function percent(value: number): string {
  return `${Number(value.toFixed(3))}%`;
}

/** Every finding by status, as one bar: ink for the unresolved, lighter for the closed. */
function StatusTrack({ overview }: { overview: Overview }) {
  const total = findingCount(overview);
  let x = 0;
  const parts = STATUSES.map((status) => {
    const width = total === 0 ? 0 : (overview.by_status[status] / total) * 100;
    const part = { status, x: percent(x), width: percent(width) };
    x += width;
    return part;
  }).filter((part) => part.width !== "0%");
  return (
    <svg className="status-track" aria-hidden="true" focusable="false">
      <defs>
        <pattern id="track-hatch" width="4" height="4" patternUnits="userSpaceOnUse">
          <rect className="hatch-ground" width="4" height="4" />
          <path className="hatch-line" d="M-1,1 l2,-2 M0,4 l4,-4 M3,5 l2,-2" />
        </pattern>
      </defs>
      {parts.map((part) => (
        <rect
          key={part.status}
          className={TRACK_CLASSES[part.status]}
          x={part.x}
          y="0"
          width={part.width}
          height="100%"
        />
      ))}
    </svg>
  );
}

function plural(count: number, one: string, many: string): string {
  return `${formatNumber(count)} ${count === 1 ? one : many}`;
}

function LastUpload({ filters, upload }: { filters: Filters; upload: Upload | null }) {
  if (upload === null) {
    return <p className="glance-upload muted">No uploads yet.</p>;
  }
  const outcome =
    upload.status === "analyzed"
      ? plural(upload.findings, "finding", "findings")
      : UPLOAD_STATUS_LABELS[upload.status].toLowerCase();
  return (
    <p className="glance-upload muted">
      Last upload{" "}
      <Link className="mono" to={view(filters, { status: "any", upload: upload.id })}>
        {upload.original_filename}
      </Link>
      , <Ago iso={upload.created_at} />: {outcome}.
    </p>
  );
}

/**
 * Above an organization's findings: the unresolved by severity, the quick views, every finding
 * by status and the last upload (Plan 6d). The numbers are the organization's, whatever the
 * filters, and each one links to the findings it counts.
 */
export function Glance({ filters, overview }: { filters: Filters; overview: Overview }) {
  const total = findingCount(overview);
  const quickViews = [
    { label: "Yours", count: overview.unresolved_mine, preset: { assignee: "me" } },
    { label: "Unassigned", count: overview.unresolved_unassigned, preset: { assignee: "none" } },
    {
      label: "New in the last day",
      count: overview.new_last_day,
      preset: { status: "any", new: "day" },
    },
  ] as const;
  return (
    <section className="glance" aria-label="Findings at a glance">
      <div>
        <h2 className="glance-title">Unresolved, by severity</h2>
        <div className="ladder">
          {SEVERITIES.map((severity) => {
            const count = overview.unresolved_by_severity[severity];
            return (
              <Link
                key={severity}
                to={view(filters, { severity })}
                className={count === 0 ? "rung zero" : "rung"}
                aria-label={`${count} unresolved ${severityLabel(severity)} findings`}
              >
                <SeverityBars level={severityLevel(severity)} />
                <b>{formatNumber(count)}</b>
                <span>{severityLabel(severity)}</span>
              </Link>
            );
          })}
        </div>
        <div className="views">
          {quickViews.map(({ label, count, preset }) => (
            <Link
              key={label}
              to={view(filters, preset)}
              className="chip"
              aria-current={sameView(filters, preset) ? "true" : undefined}
            >
              {label} <b>{formatNumber(count)}</b>
            </Link>
          ))}
        </div>
      </div>
      <div>
        <h2 className="glance-title">All {formatNumber(total)}, by status</h2>
        <StatusTrack overview={overview} />
        <div className="legend">
          {STATUSES.map((status) => (
            <Link
              key={status}
              to={view(filters, { status })}
              aria-label={`${overview.by_status[status]} ${statusLabel(status)} findings`}
            >
              <span className="legend-key">
                <span className={`key ${TRACK_CLASSES[status]}`} aria-hidden="true" />
                {statusLabel(status)}
              </span>
              <b>{formatNumber(overview.by_status[status])}</b>
            </Link>
          ))}
        </div>
        <LastUpload filters={filters} upload={overview.last_upload} />
      </div>
    </section>
  );
}
```

In `frontend/src/styles.css`, replace:
```css
}

/* Choosing a file: the browser's picker button, dressed as the app's buttons */

```
with:
```css
}

/* Findings at a glance: the unresolved by severity, quick views, every finding by status
   (Plan 6d). Amber stays the detectors': only the severity bars carry it. */

.glance {
  display: grid;
  grid-template-columns: minmax(0, 1.45fr) minmax(0, 1fr);
  gap: 1.4rem;
  margin-bottom: 1.5rem;
  padding: 1.1rem 1.2rem 1.2rem;
  border: 1px solid var(--grid);
  border-radius: 10px;
  background: var(--plate-sunk);
}

.glance-title {
  margin-bottom: 0.55rem;
  font-stretch: 75%;
  font-weight: 650;
  font-size: 1rem;
  letter-spacing: 0;
  color: var(--graphite);
}

.ladder {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0.6rem;
}

.rung {
  display: grid;
  grid-template-columns: auto 1fr;
  align-items: end;
  gap: 0.15rem 0.7rem;
  padding: 0.8rem 0.9rem 0.75rem;
  border: 1px solid var(--grid);
  border-radius: var(--radius);
  background: var(--plate);
  color: var(--ink);
  text-decoration: none;
}

.rung:hover {
  border-color: var(--ink);
}

/* The severity's bars, drawn large beside its count and word. */
.rung .sev {
  grid-row: span 2;
  gap: 3px;
  align-items: end;
}

.rung .sev i {
  width: 7px;
  height: 30px;
}

.rung b {
  font-stretch: 118%;
  font-weight: 750;
  font-size: 1.9rem;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

.rung span:last-child {
  font-stretch: 75%;
  font-weight: 650;
  color: var(--graphite);
}

/* Nothing unresolved at this severity: the tile stays, dimmed. */
.rung.zero b,
.rung.zero span:last-child {
  color: var(--graphite);
  font-weight: 500;
}

.rung.zero .sev i.on {
  background: var(--signal-tint);
}

.views {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: 0.9rem;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.3rem 0.75rem;
  border: 1px solid var(--grid);
  border-radius: 999px;
  background: var(--plate);
  color: var(--ink);
  font-weight: 600;
  font-size: 0.9rem;
  text-decoration: none;
}

.chip:hover {
  border-color: var(--ink);
}

.chip b {
  min-width: 1.2rem;
  padding: 0 0.35rem;
  border-radius: 999px;
  background: var(--paper);
  font-size: 0.85rem;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

/* The view in use looks pressed. */
.chip[aria-current="true"] {
  border-color: var(--ink);
  background: var(--ink);
  color: var(--plate);
}

.chip[aria-current="true"] b {
  background: var(--ink-soft);
}

.status-track {
  display: block;
  width: 100%;
  height: 12px;
  margin-bottom: 0.6rem;
  border-radius: 6px;
  background: var(--grid);
}

/* One class colors a status in the track (fill) and in its legend (background). */
.track-open {
  fill: var(--ink);
  background: var(--ink);
}

.track-investigating {
  fill: #4a5a6a;
  background: #4a5a6a;
}

.track-resolved {
  fill: #a9b4be;
  background: #a9b4be;
}

.track-false-positive {
  fill: url(#track-hatch);
  background: repeating-linear-gradient(135deg, #a9b4be 0 1.5px, #e4e9ed 1.5px 4px);
}

.hatch-ground {
  fill: #e4e9ed;
}

.hatch-line {
  stroke: #a9b4be;
  stroke-width: 1.5;
}

.legend {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.25rem 1rem;
  font-size: 0.92rem;
}

.legend a {
  display: flex;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.15rem 0;
  border-bottom: 1px solid transparent;
  color: var(--ink);
  text-decoration: none;
}

.legend a:hover {
  border-bottom-color: var(--grid);
}

.legend b {
  font-variant-numeric: tabular-nums;
}

.legend-key {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
}

.legend .key {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
}

.glance-upload {
  margin: 0.8rem 0 0;
  font-size: 0.9rem;
}

/* Choosing a file: the browser's picker button, dressed as the app's buttons */

```

In `frontend/src/styles.css`, replace:
```css

@media (max-width: 62rem) {
  .finding-grid {
    grid-template-columns: minmax(0, 1fr);
  }
```
with:
```css

@media (max-width: 62rem) {
  .finding-grid,
  .glance {
    grid-template-columns: minmax(0, 1fr);
  }
```

In `frontend/src/styles.css`, replace:
```css
    grid-template-columns: minmax(0, 1fr);
  }
}

```
with:
```css
    grid-template-columns: minmax(0, 1fr);
  }

  .ladder {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

```

- [ ] **Step 4: Run the tests again**

Run: `cd frontend && pnpm exec vitest run src/findings/findings.test.ts src/pages/org/Findings.test.tsx src/pages/org/Glance.test.tsx`
Expected: `30 passed`.

- [ ] **Step 5: Check everything**

Run: `cd frontend && pnpm lint && pnpm exec tsc --noEmit && pnpm test`
Expected: lint and types are clean, and `226 passed`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(web): findings at a glance (the unresolved by severity, quick views, every finding by status, the last upload; Unresolved by default, an Assignee filter, and rows with the flow, the AI's mark, initials and how long ago)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 7: A finding's summary strip

**Files:**
- Create: `frontend/src/pages/org/finding/SummaryStrip.tsx`
- Modify: `frontend/src/ui/format.ts`, `frontend/src/uploads/uploads.ts`, `frontend/src/pages/org/Findings.tsx`, `frontend/src/pages/org/finding/FindingPage.tsx`, `frontend/src/pages/org/finding/FindingFacts.tsx`, `frontend/src/pages/org/finding/Activity.tsx`, `frontend/src/pages/org/finding/Triage.tsx`, `frontend/src/styles.css`
- Test: `frontend/src/pages/org/finding/SummaryStrip.test.tsx`, `frontend/src/ui/format.test.ts`, `frontend/src/pages/org/finding/Triage.test.tsx`, `frontend/src/pages/org/finding/FindingPage.test.tsx`

**Interfaces:**
- Consumes: Task 4's `Ago`, `Person`, `Unassigned` and `overviewKey`; Task 6's `searchFrom`; `readExplanation` and `AiAnalysis` (Plan 6b).
- Produces:
  - `formatWindow(start, end)` in `src/ui/format.ts`;
  - `useUpload(orgId, uploadId)` in `src/uploads/uploads.ts`, now also read by the findings list's upload line;
  - `aiVerdict(analysis, severity)` and `<SummaryStrip org={…} finding={…} />`;
  - Triage marks `overviewKey(orgId)` out of date after a save.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/pages/org/finding/FindingPage.test.tsx`, replace:
```tsx
  ).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}/findings?upload=01a10500-0000-7000-8000-000000000001`,
  );
  expect(screen.getByRole("link", { name: "All findings" })).toHaveAttribute(
```
with:
```tsx
  ).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}/findings?status=any&upload=01a10500-0000-7000-8000-000000000001`,
  );
  expect(screen.getByRole("link", { name: "All findings" })).toHaveAttribute(
```

Create `frontend/src/pages/org/finding/SummaryStrip.test.tsx`:
```tsx
import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import {
  ADMIN,
  EXPLANATION,
  FINDING_ID,
  ORG_ID,
  OWNER,
  UPLOAD_ID,
  aiAnalysis,
  finding,
  upload,
} from "../../../test/fixtures";
import type { Handler } from "../../../test/fakeApi";
import { ORG, signedInAs } from "../../../test/orgApi";
import { renderAt } from "../../../test/render";
import { formatTime } from "../../../ui/format";
import { aiVerdict } from "./SummaryStrip";

const FINDING = `GET ${ORG}/findings/${FINDING_ID}`;
const PAGE = `/app/orgs/${ORG_ID}/findings/${FINDING_ID}`;

function opening(extra: Record<string, Handler>) {
  return signedInAs(OWNER, {
    [`GET ${ORG}/uploads/${UPLOAD_ID}`]: { body: upload() },
    "GET /api/v1/attack-techniques/T1046": { status: 404, body: { title: "Not Found" } },
    ...extra,
  });
}

async function strip(): Promise<HTMLElement> {
  return (await screen.findByText("Detected")).closest("dl") as HTMLElement;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

test("under its title, a finding says where it stands in one line", async () => {
  opening({
    [FINDING]: {
      body: finding({
        status: "investigating",
        assignee_id: ADMIN.user_id,
        ai_analysis: aiAnalysis(),
      }),
    },
  });

  renderAt(PAGE);

  const summary = await strip();
  const term = (name: string) => within(summary).getByText(name).nextElementSibling;
  expect(term("Status")).toHaveTextContent("Investigating");
  expect(await within(summary).findByText("Ben Admin")).toBeInTheDocument();
  expect(term("Detected")).toHaveTextContent("yesterday");
  expect(term("Window")).toHaveTextContent(
    `${formatTime("2026-10-01T12:00:00Z")} to ${formatTime("2026-10-01T12:05:00Z")}`,
  );
  expect(await within(summary).findByRole("link", { name: "port-scan.log" })).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}/findings?status=any&upload=${UPLOAD_ID}`,
  );
  expect(term("AI")).toHaveTextContent("Agrees: High, medium confidence");
});

test("a finding nobody has taken on says Unassigned; one the person has says You", async () => {
  opening({ [FINDING]: { body: finding() } });
  renderAt(PAGE);
  expect(within(await strip()).getByText("Unassigned")).toBeInTheDocument();
});

test("the signed-in person's own finding says You", async () => {
  opening({ [FINDING]: { body: finding({ assignee_id: OWNER.user_id }) } });
  renderAt(PAGE);
  expect(await within(await strip()).findByText("You")).toBeInTheDocument();
});

test("an upload that can't be read leaves the strip without it", async () => {
  opening({
    [FINDING]: { body: finding() },
    [`GET ${ORG}/uploads/${UPLOAD_ID}`]: { status: 404, body: { title: "Not Found" } },
  });

  renderAt(PAGE);

  const summary = await strip();
  await vi.waitFor(() => expect(within(summary).getByText("AI")).toBeInTheDocument());
  expect(within(summary).queryByText("Upload")).toBeNull();
});

test("the AI's verdict reads from its latest analysis", () => {
  const disagrees = {
    ...EXPLANATION,
    severity_assessment: {
      agrees_with_detector: false,
      suggested_severity: "medium",
      reason: "External, slow.",
    },
  };

  expect(aiVerdict(null, "high")).toBe("Not explained yet");
  expect(aiVerdict(aiAnalysis({ status: "pending", output: null }), "high")).toBe("Queued");
  expect(aiVerdict(aiAnalysis(), "critical")).toBe("Agrees: Critical, medium confidence");
  expect(aiVerdict(aiAnalysis({ output: disagrees }), "high")).toBe("Suggests Medium");
  for (const status of ["failed", "skipped_budget", "invalid_output"] as const) {
    expect(aiVerdict(aiAnalysis({ status, output: null }), "high")).toBe("No explanation");
  }
  expect(aiVerdict(aiAnalysis({ output: { summary: "half an answer" } }), "high")).toBe(
    "No explanation",
  );
});
```

In `frontend/src/pages/org/finding/Triage.test.tsx`, replace:
```tsx
import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { ADMIN, FINDING_ID, ORG_ID, OWNER, VIEWER, finding } from "../../../test/fixtures";
import { ORG, signedInAs } from "../../../test/orgApi";
import { renderAt } from "../../../test/render";
```
with:
```tsx
import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { overviewKey } from "../../../orgs/overview";
import {
  ADMIN,
  FINDING_ID,
  ORG_ID,
  OWNER,
  VIEWER,
  finding,
  overview,
} from "../../../test/fixtures";
import { ORG, signedInAs } from "../../../test/orgApi";
import { renderAt } from "../../../test/render";
```

In `frontend/src/pages/org/finding/Triage.test.tsx`, replace:
```tsx
  expect(await screen.findByRole("button", { name: "Save changes" })).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "Status" })).toHaveValue("investigating");
});

```
with:
```tsx
  expect(await screen.findByRole("button", { name: "Save changes" })).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "Status" })).toHaveValue("investigating");
});

test("a saved change marks the organization's overview out of date", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding() },
    [`PATCH ${FINDING}`]: { body: finding({ status: "resolved", version: 2 }) },
  });
  const { user, client } = renderAt(PAGE);
  client.setQueryData(overviewKey(ORG_ID), overview());

  await user.selectOptions(await screen.findByRole("combobox", { name: "Status" }), "Resolved");
  await user.click(screen.getByRole("button", { name: "Save changes" }));

  await vi.waitFor(() =>
    expect(client.getQueryState(overviewKey(ORG_ID))?.isInvalidated).toBe(true),
  );
});

```

In `frontend/src/pages/org/finding/Triage.test.tsx`, replace:
```tsx
    "Checked: our own scanner. Closing.",
  );
});

```
with:
```tsx
    "Checked: our own scanner. Closing.",
  );
  const when = within(activity).getAllByRole("listitem")[0]?.querySelector("time");
  expect(when).toHaveClass("ago");
  expect(when).toHaveAttribute("dateTime", "2026-10-04T09:31:00Z");
});

```

In `frontend/src/ui/format.test.ts`, replace:
```ts
  formatTime,
  formatUsd,
} from "./format";

```
with:
```ts
  formatTime,
  formatUsd,
  formatWindow,
} from "./format";

```

In `frontend/src/ui/format.test.ts`, replace:
```ts
});

test("a recent moment is said as how long ago it was, and an older one as its date", () => {
  const now = Date.parse("2026-10-05T12:00:00Z");
```
with:
```ts
});

test("a window within one day is written as two times, and one across days with its dates", () => {
  const start = "2026-10-01T12:00:00Z";
  const end = "2026-10-01T12:05:00Z";
  expect(formatWindow(start, end)).toBe(`${formatTime(start)} to ${formatTime(end)}`);
  const later = "2026-10-03T12:05:00Z";
  expect(formatWindow(start, later)).toBe(`${formatDateTime(start)} to ${formatDateTime(later)}`);
});

test("a recent moment is said as how long ago it was, and an older one as its date", () => {
  const now = Date.parse("2026-10-05T12:00:00Z");
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && pnpm exec vitest run src/ui/format.test.ts src/pages/org/finding/`
Expected: FAIL: `4 failed | 30 passed`. `SummaryStrip.test.tsx` stops on `Failed to resolve import "./SummaryStrip"`, and `formatWindow` isn't a function yet. The Details link still drops the status. In the triage tests, Activity's times aren't relative, and a save leaves the overview alone.

- [ ] **Step 3: Write the strip, and the times and links around it**

In `frontend/src/pages/org/Findings.tsx`, replace:
```tsx
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { api } from "../../api/client";
import { unwrap } from "../../api/problem";
import { useMe } from "../../auth/session";
import {
```
with:
```tsx
import { Link, useSearchParams } from "react-router";
import { useMe } from "../../auth/session";
import {
```

In `frontend/src/pages/org/Findings.tsx`, replace:
```tsx
import { Severity } from "../../ui/Severity";
import { usePageTitle } from "../../ui/usePageTitle";
import { FindingFilters } from "./FindingFilters";
import { Glance } from "./Glance";
```
with:
```tsx
import { Severity } from "../../ui/Severity";
import { usePageTitle } from "../../ui/usePageTitle";
import { useUpload } from "../../uploads/uploads";
import { FindingFilters } from "./FindingFilters";
import { Glance } from "./Glance";
```

In `frontend/src/pages/org/Findings.tsx`, replace:
```tsx
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
```
with:
```tsx
/** Which upload the list is narrowed to, by its file name, and the way back to all of them. */
function UploadScope({ orgId, filters }: { orgId: string; filters: Filters & { upload: string } }) {
  const upload = useUpload(orgId, filters.upload);
  const everyUpload = searchFrom({ ...filters, upload: undefined }).toString();
  return (
```

In `frontend/src/pages/org/finding/Activity.tsx`, replace:
```tsx
import type { Org } from "../../../orgs/org";
import { canContribute } from "../../../orgs/permissions";
import { formatDateTime } from "../../../ui/format";
import { CommentForm } from "./CommentForm";

```
with:
```tsx
import type { Org } from "../../../orgs/org";
import { canContribute } from "../../../orgs/permissions";
import { Ago } from "../../../ui/Ago";
import { CommentForm } from "./CommentForm";

```

In `frontend/src/pages/org/finding/Activity.tsx`, replace:
```tsx
          <li key={event.id}>
            <span className="event-what">{sentence(event, members.data)}</span>{" "}
            <time className="muted" dateTime={event.created_at}>
              {formatDateTime(event.created_at)}
            </time>
            {event.type === "commented" && typeof event.payload.text === "string" && (
              <p className="comment">{event.payload.text}</p>
```
with:
```tsx
          <li key={event.id}>
            <span className="event-what">{sentence(event, members.data)}</span>{" "}
            <span className="muted">
              <Ago iso={event.created_at} />
            </span>
            {event.type === "commented" && typeof event.payload.text === "string" && (
              <p className="comment">{event.payload.text}</p>
```

In `frontend/src/pages/org/finding/FindingFacts.tsx`, replace:
```tsx
        <dt>Upload</dt>
        <dd>
          <Link to={`../findings?upload=${finding.upload_id}`}>
            Other findings from this upload
          </Link>
```
with:
```tsx
        <dt>Upload</dt>
        <dd>
          <Link to={`../findings?status=any&upload=${finding.upload_id}`}>
            Other findings from this upload
          </Link>
```

In `frontend/src/pages/org/finding/FindingPage.tsx`, replace:
```tsx
import { EvidenceTable } from "./EvidenceTable";
import { FindingFacts } from "./FindingFacts";
import { Techniques } from "./Techniques";
import { Triage } from "./Triage";
```
with:
```tsx
import { EvidenceTable } from "./EvidenceTable";
import { FindingFacts } from "./FindingFacts";
import { SummaryStrip } from "./SummaryStrip";
import { Techniques } from "./Techniques";
import { Triage } from "./Triage";
```

In `frontend/src/pages/org/finding/FindingPage.tsx`, replace:
```tsx
      </p>
      <h1 className="finding-title">{found.title}</h1>
      <div className="finding-grid">
        <div className="finding-main">
```
with:
```tsx
      </p>
      <h1 className="finding-title">{found.title}</h1>
      <SummaryStrip org={org} finding={found} />
      <div className="finding-grid">
        <div className="finding-main">
```

Create `frontend/src/pages/org/finding/SummaryStrip.tsx`:
```tsx
import { Link } from "react-router";
import { useMe } from "../../../auth/session";
import { type AiAnalysis, readExplanation } from "../../../findings/explanation";
import type { Finding } from "../../../findings/finding";
import { searchFrom } from "../../../findings/findings";
import { type Severity, severityLabel, statusLabel } from "../../../findings/vocabulary";
import { memberName, useMembers } from "../../../orgs/members";
import type { Org } from "../../../orgs/org";
import { Ago } from "../../../ui/Ago";
import { formatWindow } from "../../../ui/format";
import { Person, Unassigned } from "../../../ui/Person";
import { useUpload } from "../../../uploads/uploads";

/** The AI's verdict in a few words; the AI panel below says the rest, and why a failure failed. */
export function aiVerdict(analysis: AiAnalysis | null, severity: Severity): string {
  if (analysis === null) {
    return "Not explained yet";
  }
  if (analysis.status === "pending") {
    return "Queued";
  }
  const explanation = readExplanation(analysis);
  if (explanation === null) {
    return "No explanation";
  }
  const assessment = explanation.severity_assessment;
  return assessment.agrees_with_detector
    ? `Agrees: ${severityLabel(severity)}, ${explanation.confidence} confidence`
    : `Suggests ${severityLabel(assessment.suggested_severity)}`;
}

/** The upload's file name, linking to all its findings; shown once the upload has been read. */
function UploadTerm({ org, finding }: { org: Org; finding: Finding }) {
  const upload = useUpload(org.id, finding.upload_id);
  if (upload.data === undefined) {
    return null;
  }
  const search = searchFrom({ status: "any", upload: finding.upload_id, sort: "severity" });
  return (
    <div>
      <dt>Upload</dt>
      <dd>
        <Link
          className="mono strip-file"
          to={`../findings?${search.toString()}`}
          title={upload.data.original_filename}
        >
          {upload.data.original_filename}
        </Link>
      </dd>
    </div>
  );
}

/** Under a finding's title: where it stands, in one line (Plan 6d). */
export function SummaryStrip({ org, finding }: { org: Org; finding: Finding }) {
  const members = useMembers(org.id);
  const me = useMe();
  return (
    <dl className="strip">
      <div>
        <dt>Status</dt>
        <dd>
          <span className="state">{statusLabel(finding.status)}</span>
        </dd>
      </div>
      <div>
        <dt>Assignee</dt>
        <dd>
          {finding.assignee_id === null ? (
            <Unassigned />
          ) : (
            <Person
              name={memberName(members.data, finding.assignee_id)}
              you={finding.assignee_id === me.data?.user.id}
            />
          )}
        </dd>
      </div>
      <div>
        <dt>Detected</dt>
        <dd>
          <Ago iso={finding.created_at} />
        </dd>
      </div>
      <div>
        <dt>Window</dt>
        <dd>{formatWindow(finding.window_start, finding.window_end)}</dd>
      </div>
      <UploadTerm org={org} finding={finding} />
      <div>
        <dt>AI</dt>
        <dd className={finding.ai_analysis?.status === "succeeded" ? "verdict" : undefined}>
          {aiVerdict(finding.ai_analysis, finding.severity)}
        </dd>
      </div>
    </dl>
  );
}
```

In `frontend/src/pages/org/finding/Triage.tsx`, replace:
```tsx
import { type Member, memberName, useMembers } from "../../../orgs/members";
import type { Org } from "../../../orgs/org";
import { canContribute } from "../../../orgs/permissions";
import { ErrorNotice } from "../../../ui/ErrorNotice";
```
with:
```tsx
import { type Member, memberName, useMembers } from "../../../orgs/members";
import type { Org } from "../../../orgs/org";
import { overviewKey } from "../../../orgs/overview";
import { canContribute } from "../../../orgs/permissions";
import { ErrorNotice } from "../../../ui/ErrorNotice";
```

In `frontend/src/pages/org/finding/Triage.tsx`, replace:
```tsx
      queryClient.setQueryData(findingKey(org.id, finding.id), updated);
      void queryClient.invalidateQueries({ queryKey: findingsKey(org.id) });
    },
    onError: (error) => {
```
with:
```tsx
      queryClient.setQueryData(findingKey(org.id, finding.id), updated);
      void queryClient.invalidateQueries({ queryKey: findingsKey(org.id) });
      void queryClient.invalidateQueries({ queryKey: overviewKey(org.id) });
    },
    onError: (error) => {
```

In `frontend/src/styles.css`, replace:
```css
.finding-title {
  max-width: 48rem;
  margin-bottom: 2rem;
}

```
with:
```css
.finding-title {
  max-width: 48rem;
  margin-bottom: 1.2rem;
}

/* Under the title, where the finding stands: one line of terms (Plan 6d). */
.strip {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem 1.6rem;
  margin: 0 0 2rem;
  padding: 0.85rem 1.1rem;
  border-top: 1px solid var(--grid);
  border-bottom: 1px solid var(--grid);
  background: var(--plate);
}

.strip div {
  display: grid;
  gap: 0.15rem;
  min-width: 0;
}

.strip dt {
  font-stretch: 75%;
  font-weight: 650;
  font-size: 0.92rem;
  color: var(--graphite);
}

.strip dd {
  display: flex;
  align-items: center;
  min-height: 1.6rem;
  margin: 0;
  font-weight: 600;
  white-space: nowrap;
}

/* A long file name keeps the line: it is cut short, and its title holds the whole name. */
.strip-file {
  max-width: 10rem;
  overflow: hidden;
  text-overflow: ellipsis;
}

.verdict {
  color: var(--teal);
}

```

In `frontend/src/styles.css`, replace:
```css
  }

  .ladder {
    grid-template-columns: repeat(2, minmax(0, 1fr));
```
with:
```css
  }

  .strip {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .strip dd {
    white-space: normal;
  }

  .ladder {
    grid-template-columns: repeat(2, minmax(0, 1fr));
```

In `frontend/src/ui/format.ts`, replace:
```ts
}

const UTC_DAY = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: "UTC" });

```
with:
```ts
}

/** A finding's window: "14:00 to 14:05", with the dates when it starts and ends on different days. */
export function formatWindow(start: string, end: string): string {
  return formatDate(start) === formatDate(end)
    ? `${formatTime(start)} to ${formatTime(end)}`
    : `${formatDateTime(start)} to ${formatDateTime(end)}`;
}

const UTC_DAY = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: "UTC" });

```

In `frontend/src/uploads/uploads.ts`, replace:
```ts
/** An organization's uploads (spec §7), newest first, a page at a time. */
import { useInfiniteQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
```
with:
```ts
/** An organization's uploads (spec §7), newest first, a page at a time. */
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
```

In `frontend/src/uploads/uploads.ts`, replace:
```ts
}

/** The uploads, rechecked every few seconds while one of them is still changing. */
export function useUploads(orgId: string) {
```
with:
```ts
}

/** One upload, read on its own: a list scoped to it, or a finding's summary, names its file. */
export function useUpload(orgId: string, uploadId: string) {
  return useQuery({
    queryKey: ["upload", orgId, uploadId],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/uploads/{upload_id}", {
          params: { path: { org_id: orgId, upload_id: uploadId } },
        }),
      ),
  });
}

/** The uploads, rechecked every few seconds while one of them is still changing. */
export function useUploads(orgId: string) {
```

- [ ] **Step 4: Run the tests again**

Run: `cd frontend && pnpm exec vitest run src/ui/format.test.ts src/pages/org/finding/`
Expected: `39 passed`.

- [ ] **Step 5: Check everything**

Run: `cd frontend && pnpm lint && pnpm exec tsc --noEmit && pnpm test`
Expected: lint and types are clean, and `233 passed`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(web): a finding's summary strip (status, assignee, detected, window, upload and the AI's verdict under its title), activity said as how long ago, and triage refreshing the overview" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 8: Uploads say how many findings they had

**Files:**
- Modify: `frontend/src/uploads/uploads.ts`, `frontend/src/pages/org/Uploads.tsx`
- Test: `frontend/src/uploads/uploads.test.ts`, `frontend/src/pages/org/Uploads.test.tsx`

**Interfaces:**
- Consumes: Task 2's `findings` and `worst_severity`, Task 4's `Ago`, `severityLabel`, `formatNumber`.
- Produces: `findingsOf(upload) -> string | null` in `src/uploads/uploads.ts`: "No findings", "1 finding, Low" or "6 findings, worst High", and `null` for an upload that isn't analyzed.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/pages/org/Uploads.test.tsx`, replace:
```tsx
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { sha256Hex } from "../../api/client";
import { fakeStorage } from "../../test/fakeStorage";
```
with:
```tsx
import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { sha256Hex } from "../../api/client";
import { fakeStorage } from "../../test/fakeStorage";
```

In `frontend/src/pages/org/Uploads.test.tsx`, replace:
```tsx
const SIGNED_HEADERS = { "x-amz-checksum-sha256": "c2hh", "x-amz-meta-traceparent": "00-ab-cd-01" };
const NONE = { body: { uploads: [], next_cursor: null } };

function created(filename = "port-scan.log") {
```
with:
```tsx
const SIGNED_HEADERS = { "x-amz-checksum-sha256": "c2hh", "x-amz-meta-traceparent": "00-ab-cd-01" };
const NONE = { body: { uploads: [], next_cursor: null } };

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

function created(filename = "port-scan.log") {
```

In `frontend/src/pages/org/Uploads.test.tsx`, replace:
```tsx
}

test("uploads are listed with their status, rows and a link to their findings", async () => {
  signedInAs(OWNER, {
    [UPLOADS]: {
      body: {
        uploads: [
          upload(),
          upload({
            id: "01a10500-0000-7000-8000-000000000002",
```
with:
```tsx
}

test("uploads are listed with their status, rows, findings and how long ago they came", async () => {
  signedInAs(OWNER, {
    [UPLOADS]: {
      body: {
        uploads: [
          upload({ findings: 6, worst_severity: "high" }),
          upload({
            id: "01a10500-0000-7000-8000-000000000002",
```

In `frontend/src/pages/org/Uploads.test.tsx`, replace:
```tsx
  expect(within(analyzed).getByText("3 rejected")).toBeInTheDocument();
  expect(
    within(analyzed).getByRole("link", { name: "Findings from port-scan.log" }),
  ).toHaveAttribute("href", `/app/orgs/${ORG_ID}/findings?upload=${UPLOAD_ID}`);
  const failed = screen.getByRole("row", { name: /notes\.txt/ });
  expect(within(failed).getByText("Failed")).toBeInTheDocument();
```
with:
```tsx
  expect(within(analyzed).getByText("3 rejected")).toBeInTheDocument();
  expect(
    within(analyzed).getByRole("link", { name: "6 findings, worst High, from port-scan.log" }),
  ).toHaveAttribute("href", `/app/orgs/${ORG_ID}/findings?status=any&upload=${UPLOAD_ID}`);
  expect(within(analyzed).getByText("yesterday")).toHaveAttribute(
    "dateTime",
    "2026-10-04T09:30:00Z",
  );
  expect(screen.getByRole("columnheader", { name: "Findings" })).toBeInTheDocument();
  const failed = screen.getByRole("row", { name: /notes\.txt/ });
  expect(within(failed).getByText("Failed")).toBeInTheDocument();
```

In `frontend/src/pages/org/Uploads.test.tsx`, replace:
```tsx
  expect(within(failed).queryByRole("link")).toBeNull();
  expect(document.title).toBe("Uploads · Acme Security · NetTriage");
});

```
with:
```tsx
  expect(within(failed).queryByRole("link")).toBeNull();
  expect(document.title).toBe("Uploads · Acme Security · NetTriage");
});

test("an upload's findings are counted as they read: one, none, or many with the worst", async () => {
  signedInAs(OWNER, {
    [UPLOADS]: {
      body: {
        uploads: [
          upload({ original_filename: "one.log", findings: 1, worst_severity: "critical" }),
          upload({
            id: "01a10500-0000-7000-8000-000000000002",
            original_filename: "quiet.log",
          }),
        ],
        next_cursor: null,
      },
    },
  });

  renderAt(PAGE);

  const one = await screen.findByRole("row", { name: /one\.log/ });
  expect(
    within(one).getByRole("link", { name: "1 finding, Critical, from one.log" }),
  ).toBeVisible();
  const quiet = screen.getByRole("row", { name: /quiet\.log/ });
  expect(within(quiet).getByText("No findings")).toBeInTheDocument();
  expect(within(quiet).queryByRole("link")).toBeNull();
});

```

In `frontend/src/uploads/uploads.test.ts`, replace:
```ts
import { expect, test } from "vitest";
import { upload } from "../test/fixtures";
import { isBusy } from "./uploads";

const NOW = Date.parse("2026-10-04T10:00:00Z");
```
with:
```ts
import { expect, test } from "vitest";
import { upload } from "../test/fixtures";
import { findingsOf, isBusy } from "./uploads";

const NOW = Date.parse("2026-10-04T10:00:00Z");
```

In `frontend/src/uploads/uploads.test.ts`, replace:
```ts
  ).toBe(false);
});
```
with:
```ts
  ).toBe(false);
});

test("an analyzed upload's findings read as a count, with the worst when there are several", () => {
  expect(findingsOf(upload({ findings: 0 }))).toBe("No findings");
  expect(findingsOf(upload({ findings: 1, worst_severity: "low" }))).toBe("1 finding, Low");
  expect(findingsOf(upload({ findings: 1204, worst_severity: "high" }))).toBe(
    "1,204 findings, worst High",
  );
});

test("an upload that isn't analyzed has no findings to count", () => {
  expect(findingsOf(upload({ status: "processing" }))).toBeNull();
  expect(findingsOf(upload({ status: "failed" }))).toBeNull();
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && pnpm exec vitest run src/uploads/ src/pages/org/Uploads.test.tsx`
Expected: FAIL: `4 failed | 11 passed`: `findingsOf is not a function` twice, and the two page tests don't find the findings links.

- [ ] **Step 3: Count each upload's findings on its row**

In `frontend/src/pages/org/Uploads.tsx`, replace:
```tsx
import { canContribute } from "../../orgs/permissions";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { formatDateTime, formatNumber } from "../../ui/format";
import { Loading } from "../../ui/Loading";
import { usePageTitle } from "../../ui/usePageTitle";
import { type Upload, UPLOAD_STATUS_LABELS, useUploads } from "../../uploads/uploads";
import { UploadPanel } from "./UploadPanel";

function UploadRow({ upload }: { upload: Upload }) {
```
with:
```tsx
import { canContribute } from "../../orgs/permissions";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Ago } from "../../ui/Ago";
import { formatNumber } from "../../ui/format";
import { Loading } from "../../ui/Loading";
import { usePageTitle } from "../../ui/usePageTitle";
import { findingsOf, type Upload, UPLOAD_STATUS_LABELS, useUploads } from "../../uploads/uploads";
import { UploadPanel } from "./UploadPanel";

/** How many findings an analyzed upload had, linking to all of them whatever their status. */
function UploadFindings({ upload }: { upload: Upload }) {
  const found = findingsOf(upload);
  if (found === null) {
    return null;
  }
  if (upload.findings === 0) {
    return <span className="muted">{found}</span>;
  }
  return (
    <Link
      to={`../findings?status=any&upload=${upload.id}`}
      aria-label={`${found}, from ${upload.original_filename}`}
    >
      {found}
    </Link>
  );
}

function UploadRow({ upload }: { upload: Upload }) {
```

In `frontend/src/pages/org/Uploads.tsx`, replace:
```tsx
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
```
with:
```tsx
        {rejected > 0 && <div className="muted">{formatNumber(rejected)} rejected</div>}
      </td>
      <td>
        <UploadFindings upload={upload} />
      </td>
      <td>
        <Ago iso={upload.created_at} />
      </td>
    </tr>
```

In `frontend/src/pages/org/Uploads.tsx`, replace:
```tsx
              <th scope="col">Status</th>
              <th scope="col">Rows</th>
              <th scope="col">Uploaded</th>
              <th scope="col">
                <span className="visually-hidden">Findings</span>
              </th>
            </tr>
          </thead>
```
with:
```tsx
              <th scope="col">Status</th>
              <th scope="col">Rows</th>
              <th scope="col">Findings</th>
              <th scope="col">Uploaded</th>
            </tr>
          </thead>
```

In `frontend/src/uploads/uploads.ts`, replace:
```ts
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";

export type Upload = components["schemas"]["UploadOut"];
```
with:
```ts
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";
import { severityLabel } from "../findings/vocabulary";
import { formatNumber } from "../ui/format";

export type Upload = components["schemas"]["UploadOut"];
```

In `frontend/src/uploads/uploads.ts`, replace:
```ts
  expired: "Expired",
};

export function uploadsKey(orgId: string) {
```
with:
```ts
  expired: "Expired",
};

/** An analyzed upload's findings (Plan 6d): "No findings", "1 finding, Low", "6 findings, worst High". */
export function findingsOf(upload: Upload): string | null {
  if (upload.status !== "analyzed") {
    return null;
  }
  const worst = upload.worst_severity === null ? "" : severityLabel(upload.worst_severity);
  if (upload.findings === 0) {
    return "No findings";
  }
  return upload.findings === 1
    ? `1 finding, ${worst}`
    : `${formatNumber(upload.findings)} findings, worst ${worst}`;
}

export function uploadsKey(orgId: string) {
```

- [ ] **Step 4: Run the tests again**

Run: `cd frontend && pnpm exec vitest run src/uploads/ src/pages/org/Uploads.test.tsx`
Expected: `15 passed`.

- [ ] **Step 5: Check everything**

Run: `cd frontend && pnpm lint && pnpm exec tsc --noEmit && pnpm test`
Expected: lint and types are clean, and `236 passed`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(web): uploads say how many findings each had and the worst (\"6 findings, worst High\"), linking to all of them, and how long ago they came" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 9: The decisions in the M1 spec, and the owner's walk through the glance

**Files:**
- Modify: `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (§7, §10), `docs/runbooks/setup-and-deploy.md` (B11, B12, a new B13), `README.md`

**Interfaces:**
- Consumes: the decisions above, and the glance spec.
- Produces: §7's overview row and the list's and uploads' new fields; §10's home, glance, strip and times; runbook B13.

- [ ] **Step 1: Write the docs**

In `README.md`, replace:
```markdown
> foundation, sign-in with mandatory MFA, organizations, uploads and their analysis, triage,
> and AI explanations on Bedrock. In progress: the web app, whose pages for organizations,
> uploads, findings, triage, the AI's explanations, the audit log and usage are built; the
> public demo is next.

## Architecture
```
with:
```markdown
> foundation, sign-in with mandatory MFA, organizations, uploads and their analysis, triage,
> and AI explanations on Bedrock. In progress: the web app, whose pages for organizations,
> uploads, findings, triage, the AI's explanations, the audit log and usage are built, with
> a home of your work and each organization's findings at a glance; the public demo is next.

## Architecture
```

In `README.md`, replace:
```markdown
- **Triage with optimistic concurrency**: a finding's status and assignee change only with `If-Match` naming the version the caller read, so two analysts can't silently overwrite each other (412 instead); every change and comment is in the finding's history and the audit log.
- **AI explanations with guardrails**: each upload's 20 most severe findings are explained by gpt-oss-20b on Amazon Bedrock from the finding's typed fields only. An answer that names an address, port or technique outside the data is refused, and every call is paid for in advance from a daily token budget per org and a $0.50 daily cap across all orgs, which fail closed. Analysts, Admins and Owners can re-run an explanation and rate it, and Owners and Admins see the AI's calls, tokens and cost per day.
- **A typed web app**: React and TypeScript, with an API client generated from the API's OpenAPI document, so CI fails when the two drift. It sends CloudFront's body hash, the CSRF token and idempotency keys on its own, and shows every API error with its reference. Files go from the browser straight to S3 with their fingerprint and progress; findings open most severe first; a port scan's evidence is drawn on a map of the host's ports; and triage names the version it read, so a newer change is shown, never overwritten.
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for each function.
- **Distributed rate limiting** with GCRA on DynamoDB, exact under concurrency ([ADR 0006](docs/adr/0006-gcra-rate-limiter.md)).
```
with:
```markdown
- **Triage with optimistic concurrency**: a finding's status and assignee change only with `If-Match` naming the version the caller read, so two analysts can't silently overwrite each other (412 instead); every change and comment is in the finding's history and the audit log.
- **AI explanations with guardrails**: each upload's 20 most severe findings are explained by gpt-oss-20b on Amazon Bedrock from the finding's typed fields only. An answer that names an address, port or technique outside the data is refused, and every call is paid for in advance from a daily token budget per org and a $0.50 daily cap across all orgs, which fail closed. Analysts, Admins and Owners can re-run an explanation and rate it, and Owners and Admins see the AI's calls, tokens and cost per day.
- **A typed web app**: React and TypeScript, with an API client generated from the API's OpenAPI document, so CI fails when the two drift. It sends CloudFront's body hash, the CSRF token and idempotency keys on its own, and shows every API error with its reference. Files go from the browser straight to S3 with their fingerprint and progress; findings open most severe first; a port scan's evidence is drawn on a map of the host's ports; and triage names the version it read, so a newer change is shown, never overwritten. The home lists what's assigned to you across organizations, and every count on it, or above an organization's findings, links to exactly the findings it counts.
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for each function.
- **Distributed rate limiting** with GCRA on DynamoDB, exact under concurrency ([ADR 0006](docs/adr/0006-gcra-rate-limiter.md)).
```

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
   motion, the grid shows up already lit.) Below it, **How a finding is made** shows four steps.
2. Click **Sign in or sign up** and sign in with your password and a fresh code from the
   authenticator app. You land on **Your organizations**.
3. Under **Create an organization**, type `Web Test` and click **Create organization**. The
   organization opens on its **Findings** page, which has none yet, with you as its Owner. Click
```
with:
```markdown
   motion, the grid shows up already lit.) Below it, **How a finding is made** shows four steps.
2. Click **Sign in or sign up** and sign in with your password and a fresh code from the
   authenticator app. You land on the home: **Assigned to you**, then **Organizations**.
3. Under **Create an organization**, type `Web Test` and click **Create organization**. The
   organization opens on its **Findings** page, which has none yet, with you as its Owner. Click
```

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
   through CloudFront's signing when you delete.)
7. Under **Delete this organization**, type `Web Test (2) & Bob's` and click
   **Delete organization**. You're back on **Your organizations**, without it.
8. Click **Account settings** at the top: your email address shows. Click **Sign out**: you're
   signed out of NetTriage and of Cognito, and the landing page offers **Sign in or sign up**.
```
with:
```markdown
   through CloudFront's signing when you delete.)
7. Under **Delete this organization**, type `Web Test (2) & Bob's` and click
   **Delete organization**. You're back on the home, without it.
8. Click **Account settings** at the top: your email address shows. Click **Sign out**: you're
   signed out of NetTriage and of Cognito, and the landing page offers **Sign in or sign up**.
```

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
   the file** for a moment, then **Analyzing**, and within about a minute it turns **Analyzed**
   with 150 rows, without reloading the page.
3. Click **Findings** on its row. One finding: **High**, `Port scan of 10.0.0.5 from 10.0.3.17:
   150 TCP ports in 5 minutes`, **Open**, **Unassigned**. Set **Severity** to **Low**: **No
   findings match these filters.** Click **Clear filters**.
4. Click the finding's title, and check its page:
```
with:
```markdown
   the file** for a moment, then **Analyzing**, and within about a minute it turns **Analyzed**
   with 150 rows, without reloading the page.
3. Click **1 finding, High** on its row. One finding: **High**, `Port scan of 10.0.0.5 from
   10.0.3.17: 150 TCP ports in 5 minutes`, **Open**, **Unassigned**. Set **Severity** to **Low**: **No
   findings match these filters.** Click **Clear filters**.
4. Click the finding's title, and check its page:
```

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
   UTC day, or **No AI calls in the last 30 days.** until Bedrock answers.
9. Delete the organization as in B11 steps 6 and 7, typing `Triage Test`.

## Part C: when things go wrong
```
with:
```markdown
   UTC day, or **No AI calls in the last 30 days.** until Bedrock answers.
9. Delete the organization as in B11 steps 6 and 7, typing `Triage Test`.

### B13. Findings at a glance
What's yours, and where each organization's findings stand (Plan 6d).
1. Sign in as in B11 step 2. The home says **Nothing is assigned to you.** under
   **Assigned to you**. Create an organization named `Glance Test`. It opens on **Findings**: the
   heading reads **0 unresolved of 0**, every tile reads 0, and the panel says
   **No uploads yet.**
2. Upload `docs/samples/port-scan.log` as in B12 step 2. Once it's **Analyzed**, its
   **Findings** reads **1 finding, High**, and **Uploaded** reads **just now** (hover over it for
   the exact time).
3. Click the **Findings** tab. The heading reads **1 unresolved of 1**. The **High** tile reads 1,
   and the other three read 0, dimmed. **Unassigned** and **New in the last day** read 1, and
   the panel ends **Last upload port-scan.log, … ago: 1 finding.** The finding's row shows
   `Port scan`, `10.0.3.17 → 10.0.0.5`, **Unassigned** and when it was detected.
4. Click the finding's title. Under it, the strip reads **Open**, **Unassigned**, when it was
   detected, its five-minute window, **port-scan.log**, and **Not explained yet** while Bedrock is
   limited (once it answers, the AI's verdict, such as **Agrees: High, medium confidence**).
5. Under **Triage**, set **Assignee** to yourself and click **Save changes**: the strip shows
   **You**. Click **NetTriage** at the top left. **Assigned to you** lists the finding, with
   **Glance Test** beside it, and the organization's card shows **Yours** 1 and
   **Unassigned** 0.
6. Open the finding again, set **Status** to **Resolved** and save. Click **All findings**: the
   list says **Nothing here is unresolved.** Click **Show every finding**: the finding is
   listed as **Resolved**, and **Status** reads **Any status**.
7. Delete the organization as in B11 steps 6 and 7, typing `Glance Test`.

## Part C: when things go wrong
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `POST /api/v1/invitations/accept` | session | Body: token |
| `POST /api/v1/orgs/{org}/uploads` | `uploads:create` | Body: `filename`, `size_bytes` (at most 25 MB), `sha256` (hex). Returns a presigned PUT that expires in 5 minutes. It signs `Content-Length`, `x-amz-checksum-sha256` and `x-amz-meta-traceparent`, so S3 accepts only the declared file; the response lists the headers to send. Counts against `uploads.org`; 503 while uploads are paused |
| `GET /api/v1/orgs/{org}/uploads` | `uploads:read` | |
| `GET /api/v1/orgs/{org}/uploads/{id}` | `uploads:read` | Status and statistics |
| `GET /api/v1/orgs/{org}/findings` | `findings:read` | Filters: status, severity, detector, upload. `sort=newest` (the default) or `sort=severity`: most severe first, then newest (Plan 6b). A page at a time; a cursor works only with the order it was made for |
| `GET /api/v1/orgs/{org}/findings/{id}` | `findings:read` | With evidence, techniques, the latest AI analysis as `ai_analysis` (null until there is one: status, provider, model, prompt and output schema versions, output, `error_code`, tokens, `cost_usd`, latency, and the rating's `feedback` and `feedback_by`; Plans 5b and 5c), and the latest 100 events with `events_total` (Plan 4c); the `ETag` is the finding's version |
| `PATCH /api/v1/orgs/{org}/findings/{id}` | `findings:triage` | Body: `status`, `assignee_id` (`null` unassigns), or both; `If-Match`. Any status can change to any other, and the assignee must be an Owner, Admin or Analyst of the org (422 otherwise; the owner's decisions, Plan 4c). Each change is an event in the finding's history and an audit event |
```
with:
```markdown
| `POST /api/v1/invitations/accept` | session | Body: token |
| `POST /api/v1/orgs/{org}/uploads` | `uploads:create` | Body: `filename`, `size_bytes` (at most 25 MB), `sha256` (hex). Returns a presigned PUT that expires in 5 minutes. It signs `Content-Length`, `x-amz-checksum-sha256` and `x-amz-meta-traceparent`, so S3 accepts only the declared file; the response lists the headers to send. Counts against `uploads.org`; 503 while uploads are paused |
| `GET /api/v1/orgs/{org}/uploads` | `uploads:read` | Each upload with `findings`, how many it kept, and `worst_severity`, the most severe of them (null when none; Plan 6d) |
| `GET /api/v1/orgs/{org}/uploads/{id}` | `uploads:read` | Status and statistics, with `findings` and `worst_severity` |
| `GET /api/v1/orgs/{org}/overview` | `findings:read` | Where the org's findings stand, in one read (Plan 6d): `unresolved_by_severity` (Open or Investigating), `by_status`, `unresolved_unassigned`, `unresolved_mine` (assigned to the caller), `new_last_day` (detected in the last 24 hours, any status), `member_count` and `last_upload` (null when none). Counted by queries over existing tables; no new column |
| `GET /api/v1/orgs/{org}/findings` | `findings:read` | Filters: `status`, one or several (at most 4; Plan 6d), severity, detector, upload, `assignee` (`me` or `none`) and `since`, a moment the findings were detected at or after (Plan 6d). `sort=newest` (the default) or `sort=severity`: most severe first, then newest (Plan 6b). A page at a time; a cursor works only with the order it was made for. Each finding carries `ai_status`, the status of its latest AI analysis (null until there is one; Plan 6d) |
| `GET /api/v1/orgs/{org}/findings/{id}` | `findings:read` | With evidence, techniques, the latest AI analysis as `ai_analysis` (null until there is one: status, provider, model, prompt and output schema versions, output, `error_code`, tokens, `cost_usd`, latency, and the rating's `feedback` and `feedback_by`; Plans 5b and 5c), and the latest 100 events with `events_total` (Plan 4c); the `ETag` is the finding's version |
| `PATCH /api/v1/orgs/{org}/findings/{id}` | `findings:triage` | Body: `status`, `assignee_id` (`null` unassigns), or both; `If-Match`. Any status can change to any other, and the assignee must be an Owner, Admin or Analyst of the org (422 otherwise; the owner's decisions, Plan 4c). Each change is an event in the finding's history and an audit event |
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
- `/demo`: the read-only demo workspace, rendered from the static snapshot, with a clear "demo" banner (Plan 6c, written once Bedrock answers, so the demo's explanations are real; the owner's decision).
- `/invite`: reads the token from the URL fragment, signs the user in if needed, then accepts. The token leaves the address bar at once and waits in the tab's `sessionStorage` while the person signs in (Plan 6a).
- `/app`: an org switcher (the list of the person's organizations, which the product name in the top bar returns to), plus onboarding (create an org or accept an invitation).
- `/app/orgs/:org/uploads`: the uploads list, and an upload panel with progress. The panel sits on the page, since the app has no modal dialogs (Plan 6a). The browser computes the file's SHA-256 before requesting a slot, and puts the file in S3 with an `XMLHttpRequest`, the one browser API that reports upload progress. The list checks back every few seconds while an upload is being analyzed (Plan 6b).
- `/app/orgs/:org/findings`, where an organization opens (the owner's decision, Plan 6b): a table with filters (severity, status, detector, upload) and sorting, done by the API with `sort`. It opens most severe first, then newest (the owner's decision); newest first is one choice away. The filters and the order live in the address.
- `/app/orgs/:org/findings/:id`:
  - summary and metrics,
  - an evidence table; a port scan of one host also draws its evidence on the port map, captioned as the sample it is (at most 50 flows; the owner's decision, Plan 6b),
```
with:
```markdown
- `/demo`: the read-only demo workspace, rendered from the static snapshot, with a clear "demo" banner (Plan 6c, written once Bedrock answers, so the demo's explanations are real; the owner's decision).
- `/invite`: reads the token from the URL fragment, signs the user in if needed, then accepts. The token leaves the address bar at once and waits in the tab's `sessionStorage` while the person signs in (Plan 6a).
- `/app`, the home and the org switcher, which the product name in the top bar returns to (Plan 6d):
  - **Assigned to you:** the unresolved (Open or Investigating) findings assigned to the person in all their organizations, most severe first, then newest. At most 20 are read per organization, with a link to the rest.
  - **Organizations:** a card per membership with its unresolved findings by severity, its unassigned and yours, its members and its last upload, each count linking to the findings it counts.
  - Onboarding: create an org, or accept an invitation.
- `/app/orgs/:org/uploads`: the uploads list, and an upload panel with progress. The panel sits on the page, since the app has no modal dialogs (Plan 6a). The browser computes the file's SHA-256 before requesting a slot, and puts the file in S3 with an `XMLHttpRequest`, the one browser API that reports upload progress. The list checks back every few seconds while an upload is being analyzed (Plan 6b). Each analyzed upload says how many findings it had and the worst, such as "6 findings, worst High", linking to all of them (Plan 6d).
- `/app/orgs/:org/findings`, where an organization opens (the owner's decision, Plan 6b): a table with filters (severity, status, assignee, detector, upload) and sorting, done by the API with `sort`. It opens most severe first, then newest (the owner's decision); newest first is one choice away. The filters and the order live in the address. Plan 6d adds:
  - **Unresolved** (Open or Investigating) as the status filter's default, beside Any status and each status, and an **Assignee** filter (Anyone, Yours, Unassigned);
  - a glance above the filters, read from the overview: the unresolved by severity as four tiles, the quick views Yours, Unassigned and New in the last day, every finding by status as a track with its legend, and the last upload. Each number links to exactly the findings it counts;
  - on each row, the detector, the flow and whether the AI explained it under the title, the assignee with their initials, and when it was detected.
- `/app/orgs/:org/findings/:id`:
  - under the title, a summary strip: status, assignee, when it was detected, its window, its upload and the AI's verdict (Plan 6d),
  - summary and metrics,
  - an evidence table; a port scan of one host also draws its evidence on the port map, captioned as the sample it is (at most 50 flows; the owner's decision, Plan 6b),
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
- `/app/orgs/:org/audit` and `/app/orgs/:org/usage`: Owner and Admin only. Usage draws the cost per UTC day as ink bars above the table of days and totals, for the last 7, 30 or 90 days (the owner's decision, Plan 6b).
- `/app/settings`: account settings: who is signed in, and "sign out everywhere".

**Libraries:**
```
with:
```markdown
- `/app/orgs/:org/audit` and `/app/orgs/:org/usage`: Owner and Admin only. Usage draws the cost per UTC day as ink bars above the table of days and totals, for the last 7, 30 or 90 days (the owner's decision, Plan 6b).
- `/app/settings`: account settings: who is signed in, and "sign out everywhere".

Times read as how long ago they were ("2 h ago"), with the exact local time on hover; the audit log keeps exact times, as a record should (Plan 6d).

**Libraries:**
```

- [ ] **Step 2: Check the docs name what the app shows**

Run: `grep -c "Nothing here is unresolved" docs/runbooks/setup-and-deploy.md frontend/src/pages/org/Findings.tsx`
Expected: `docs/runbooks/setup-and-deploy.md:1` and `frontend/src/pages/org/Findings.tsx:1`.

- [ ] **Step 3: Commit**

```bash
git add README.md docs
git commit -m "docs: the home and findings at a glance in the M1 spec (§7 the overview, the list's new filters and ai_status, uploads' findings; §10 the pages), the runbook's B13, and the README" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 10 (Claude, then the owner): Pull request, deploy and a walk through the glance

- [ ] **Step 1 (Claude):**
  - Run `just lint test tools-test edge-test web-check tf-check pin-check cloud-check detection-report build-lambda`; everything must pass.
  - Take screenshots of the changed pages at desktop and phone width against a stand-in API, and fix what looks wrong: the home, findings with the glance, a finding, and uploads.
  - Get a final review of the whole branch, fix what it finds, then push `plan-6d/glance`.
  - Open the PR, watch CI, and request a Copilot review.
- [ ] **Step 2 (owner):** Review the PR, then squash-merge it.
- [ ] **Step 3 (owner):** Runbook B2 (`just deploy-dev`). Expected:
  - `Database migrated.`, with no new migrations;
  - `Plan: 0 to add, 3 to change, 0 to destroy`: the `api`, `analyze` and `triage` functions get the new code;
  - the web build is published;
  - every smoke test `PASS`es.
- [ ] **Step 4 (owner):** Runbook B13. Expected:
  - the home lists what's assigned to you, and a card per organization;
  - the findings page opens on the unresolved, under the glance, and each count lists what it counted;
  - a finding's strip reads its status, assignee, times, upload and the AI's verdict;
  - uploads count their findings.

## Plan 6d is done when

- [ ] `just lint test web-check` passes locally and CI passes.
- [ ] The PR is merged through review, with every thread resolved.
- [ ] Runbook B13 works on dev.

## Spec coverage of this plan

| Glance spec | Covered here |
|---|---|
| §2 words: Unresolved, Yours, New in the last day | Tasks 1, 3 and 6 |
| §3.1 the home: assigned to you, the cards, creating an organization | Task 5 |
| §3.2 the glance, the Assignee filter, Unresolved by default, the rows | Task 6 |
| §3.3 a finding's summary strip | Task 7 |
| §3.4 uploads' findings and times | Task 8 |
| §3.5 `<Ago>` everywhere but the audit log | Tasks 4 to 8 |
| §4.1 `GET …/overview` | Task 3 |
| §4.2 the list's statuses, assignee, `since` and `ai_status` | Task 1 |
| §4.3 uploads' `findings` and `worst_severity` | Task 2 |
| §4.4 compatibility, no migration | Tasks 1 to 3 |
| §5 the look, §6 accessibility | Tasks 4 to 8 |
| §7 testing, screenshots | Tasks 1 to 8, and Task 10 |
| The M1 spec's §7 and §10, amended | Task 9 |
