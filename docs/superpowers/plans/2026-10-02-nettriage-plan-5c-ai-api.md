# NetTriage Plan 5c: The AI API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give members the AI's controls and owners its bill, through the API:
- re-run a finding's AI explanation: queued for the triage worker, or the current answer at no cost;
- rate an explanation up or down;
- read the org's AI usage: calls, tokens and cost per day, counted on every model call;
- the latest successful analysis replaces the finding's AI techniques.

Plan 5d adds the evals that choose the model, once AWS lifts the Bedrock limits. Plan 6 adds the pages that use these endpoints.

**Architecture:**
- **Re-run:** `POST …/ai-analyses` reads the finding as `app_api` and hashes its typed fields as the worker does.
  - If a succeeded analysis exists for that input, the current model and prompt v1, it returns that answer (200).
  - Otherwise it sends one message to the triage queue (202), which the worker handles like any other.
- **Usage:** the worker counts every model call in `ai_usage`, one row per org and UTC day, added to with an upsert:
  - an attempt's calls in the same transaction as its analysis;
  - paid calls whose analysis isn't stored (a repair handed back to SQS) on their own.
  `GET …/usage` reads the rows.
- **Feedback:** `PUT …/feedback` sets the rating on a succeeded analysis. A rating no longer moves `updated_at`, so it never changes which analysis is the latest.
- **AI techniques:** when an analysis succeeds, the store deletes the finding's AI techniques and adds the new ones. The worker may delete only rows with `source = 'ai'` (a restrictive row policy).

**Tech Stack:** Python 3.14, FastAPI, Pydantic 2, SQLAlchemy 2 Core with psycopg 3 (Neon Postgres, row-level security), boto3 SQS · Terraform (IAM, Lambda) · moto for SQS in tests.

**Spec:** `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (revision 2). Read it alongside this plan; section numbers (§) refer to it. This plan implements:
- §2.2's re-run and thumbs-up/down feedback;
- §5.2's AI usage, and the AI techniques of a finding;
- §5.4's grants for both;
- §6.4's `ai:request`, `ai:feedback` and `usage:read`, and §6.5's `ai.rerun.user`;
- §7's `POST …/ai-analyses`, `PUT …/feedback` and `GET …/usage`;
- §9.4's `ai.rerun_requested`.

It also settles two of Plan 5a's deferred findings: AI techniques piling up across analyses, and a retry overwriting the earlier attempts' cost.

**Plan series:** Plan 5 of 7 ("AI triage") is now in four parts (the owner's decision, 2026-10-02):
- 5a: the AI engine, offline (PR #12);
- 5b: live on Bedrock (PR #13, deployed);
- **5c (this plan): the AI API;**
- 5d: the evals: the ~30-finding eval set with its gates, the runner, the model comparison and the quality baseline. It is written once AWS lifts the Bedrock limits, so it's tested live.

**Branch:** `plan-5c/ai-api`, from `main` at `13ea92d` or later.

## Global Constraints

- **Stack.** Python **3.14**. No new dependencies. mypy `--strict` and Ruff pass on `src` and `tests`. Work test-first.
- **Commands on this Windows machine.** Run Python tools as modules (`uv run python -m …`). Host policy blocks some uv launchers and every unsigned executable outside trusted tools. Backend tests need the local Postgres, which `just test` starts.
- **Permissions (§6.4):**
  - `ai:request` and `ai:feedback` are for Owners, Admins and Analysts;
  - `usage:read` is for Owners and Admins;
  - another org's finding or analysis is a **404**.
- **Rate limits (§6.5).** A re-run counts against `ai.rerun.user`: **10 an hour**, a burst of **3**. Every state-changing request also counts against `api.mutation.user`.
- **API errors (§7).** RFC 9457 Problem Details. Request bodies forbid undeclared fields (`Strict`).
- **Logs (§9.3).** Never prompts, the finding's data or the model's answers. Errors are logged by type only.
- **Infrastructure.** Terraform only, reviewed in the PR, applied by the owner's deploy. Regional resources live in eu-north-1. CI holds no cloud access.
- **Owner-only commands.** Claude never runs `aws login`, `just bootstrap`, `just store-*`, `just pause-*`, `just resume-*` or `just deploy-*`.
- **Commits.** Use Conventional Commits. Every commit made by Claude ends with the trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Decisions this plan makes

1. **Plan 5 is now in four parts** (the owner's decision, 2026-10-02). 5c is the API; 5d, the evals, waits for live Bedrock calls.
2. **A re-run retries unless the finding is already explained** (the owner's decision):
   - The API computes the finding's input hash as the worker does, with the worker's model (`NETTRIAGE_BEDROCK_MODEL_ID`) and prompt v1.
   - If that key has a succeeded analysis, it answers `200 {"status": "explained", "ai_analysis": …}` and nothing is spent.
   - Otherwise it queues the finding and answers `202 {"status": "queued", "ai_analysis": null}`. A failed, skipped or invalid analysis is retried, and a new model or prompt gets a fresh answer.
   - A queued re-run is audited as `ai.rerun_requested`.
   - If the queue can't be reached, the answer is 503 and nothing is audited.
   - While the AI is paused or a budget is spent, a re-run is still queued; the worker records why there's no answer.
3. **Usage is counted per org and UTC day** (the owner's decision):
   - `ai_usage` holds `calls`, `input_tokens`, `output_tokens` and `cost_usd` per org and day, the day being the database's UTC date.
   - `save_analysis` adds an attempt's calls in the same transaction as the analysis. This includes an attempt whose analysis another worker already stored, because its calls were still paid for.
   - Calls whose analysis isn't stored (a paid answer, then a repair handed back to SQS) are added on their own, best effort.
   - An analysis row's tokens and cost stay the final delivery's; `ai_usage` is the bill.
4. **The latest success replaces the AI's techniques** (the owner's decision):
   - storing a succeeded analysis deletes the finding's `source = 'ai'` rows, then adds its own;
   - `app_triage` gets DELETE on `finding_techniques`, narrowed by a restrictive row policy to `source = 'ai'`, so it can never remove a detector's technique.
5. **Feedback:**
   - only a succeeded analysis of the finding can be rated (409 otherwise; 404 for an analysis of another finding);
   - a new rating replaces the last, and `feedback_by` is the rater;
   - `app_api` may UPDATE only `feedback` and `feedback_by`;
   - the `updated_at` trigger now fires only when an attempt's own columns change, so a rating never makes an analysis "the latest";
   - no audit event: §9.4 lists none for feedback.
6. **Usage reading:**
   - `GET …/usage?days=` takes 1 to 90 days (30 by default);
   - UTC days are listed newest first; days without a call aren't listed;
   - `totals` sums the listed days, and costs are decimal strings.
7. **A finding's analysis now carries `feedback` and `feedback_by`,** in `GET …/findings/{id}` and in the rating's answer.
8. **Infrastructure:**
   - the API role may `sqs:SendMessage` to the triage queue;
   - the function gets `NETTRIAGE_TRIAGE_QUEUE_URL` and `NETTRIAGE_BEDROCK_MODEL_ID`;
   - the pipeline module outputs the queue's URL and ARN.

## Review Focus

1. **A re-run that pays for an answer that already exists, or that never refreshes an outdated one.** Tests: Task 3 `test_an_explained_finding_returns_its_answer_at_no_cost`, `test_an_answer_by_another_model_is_explained_again` and `test_a_failed_explanation_is_queued_again`.
2. **The usage page under-counting what was spent.** It must include an attempt whose success another worker stored first, and a paid call whose repair was handed back. Tests:
   - Task 2: `test_calls_are_counted_even_when_another_success_was_stored_first`, `test_calls_that_store_no_analysis_are_counted_on_their_own` and `test_a_paid_call_is_counted_even_when_the_repair_is_handed_back`.
3. **A rating that changes which explanation is shown.** The finding shows the latest analysis by `updated_at`. Test: Task 1 `test_the_api_may_rate_an_analysis_without_making_it_newer`.
4. **The worker deleting a detector's technique while replacing the AI's.** Tests: Task 1 `test_the_worker_may_take_back_only_the_ais_techniques`; Task 2 `test_a_later_success_replaces_the_ais_techniques`.
5. **Another org's finding, analysis or usage.** These must be a 404, or never counted. Tests:
   - Task 3: `test_a_finding_of_another_org_is_not_found`;
   - Task 4: `test_an_analysis_of_another_finding_is_not_found`;
   - Task 5: `test_another_orgs_usage_is_never_counted`;
   - Task 1: `ai_usage` in the tenant-isolation suite.

## Owner prerequisites

- **None to build or review.** Tests use the local Postgres and moto.
- **After the merge:** runbook B2 (the deploy, migration 0010), then B10. B10's re-run and usage steps work now. A succeeded explanation to rate, and today's usage, need AWS to have lifted the Bedrock limits.

## File map

| File | Responsibility | Task |
|---|---|---|
| `migrations/versions/0010_ai_usage_feedback.py` | `ai_usage`; the feedback grant and trigger; the worker's AI-only DELETE | 1 |
| `src/nettriage/adapters/ai_store.py`, `entrypoints/triage/explainer.py` | counting every call; replacing the AI's techniques | 2 |
| `src/nettriage/adapters/ai_analyses.py`, `entrypoints/api/routes/ai.py` | re-run and rating | 3, 4 |
| `src/nettriage/entrypoints/api/services.py`, `wiring.py` | the triage queue and the model, for the API | 3 |
| `src/nettriage/adapters/ai_usage.py`, `entrypoints/api/routes/usage.py`, `usage_schemas.py` | the usage endpoint | 5 |
| `infra/modules/app/`, `infra/modules/pipeline/outputs.tf`, `infra/envs/dev/main.tf` | the API's queue access and settings | 6 |
| `docs/…/spec`, `docs/runbooks/setup-and-deploy.md`, `README.md` | the decisions, and runbook B10 | 7 |

Paths under `src/` and `migrations/` are in `backend/`.

---

### Task 1: The usage table, the rating grant and the AI-only delete

**Files:**
- Create: `backend/migrations/versions/0010_ai_usage_feedback.py`
- Test: `backend/tests/tenantdata.py`, `backend/tests/integration/test_ai_schema.py`, `backend/tests/integration/test_tenant_isolation.py`, `backend/tests/integration/test_migrations.py`

**Interfaces:**
- Consumes: Plan 5a's `ai_analyses` (its `ai_analyses_updated_at` trigger and `feedback` columns) and `app_triage`, Plan 4b's `finding_techniques` and its `tenant` row policy, and the harness's `database` fixture.
- Produces:
  - the `ai_usage` table: PK `(org_id, day)`, with `calls`, `input_tokens`, `output_tokens` and `cost_usd`. RLS by org; `app_api` may SELECT; `app_triage` may SELECT, INSERT and UPDATE the counts;
  - `app_api` may `UPDATE (feedback, feedback_by) ON ai_analyses`;
  - `ai_analyses_updated_at` fires only `BEFORE UPDATE OF status, output, input_tokens, output_tokens, cost_usd, latency_ms, error_code`;
  - `app_triage` may DELETE `finding_techniques` rows, and the `ai_rows_only` restrictive policy limits that to `source = 'ai'`;
  - in the tests: `tenantdata.add_usage(connection, org_id)`, which `add_tenant` now calls.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/tenantdata.py`, replace:
```python

def add_tenant(admin: Engine) -> Tenant:
    """An org with an owner, a pending invitation, and an analyzed upload with a finding that
    has a succeeded AI analysis."""
    with admin.begin() as connection:
```
with:
```python

def add_usage(connection: Connection, org_id: UUID) -> None:
    """Today's AI usage for the org: one call (spec §5.2, Plan 5c)."""
    connection.execute(
        text(
            "INSERT INTO ai_usage (org_id, day, calls, input_tokens, output_tokens, cost_usd) "
            "VALUES (:org, (now() AT TIME ZONE 'UTC')::date, 1, 1800, 320, 0.000222)"
        ),
        {"org": org_id},
    )


def add_tenant(admin: Engine) -> Tenant:
    """An org with an owner, a pending invitation, an analyzed upload with a finding that has a
    succeeded AI analysis, and today's AI usage."""
    with admin.begin() as connection:
```

In `backend/tests/tenantdata.py`, replace:
```python
        analysis = add_analysis(connection, org, finding)
    return Tenant(
```
with:
```python
        analysis = add_analysis(connection, org, finding)
        add_usage(connection, org)
    return Tenant(
```

In `backend/tests/integration/test_tenant_isolation.py`, replace:
```python
    "ai_analyses",
)
```
with:
```python
    "ai_analyses",
    "ai_usage",
)
```

In `backend/tests/integration/test_tenant_isolation.py`, replace:
```python
                "ai_analyses",
            )
```
with:
```python
                "ai_analyses",
                "ai_usage",
            )
```

In `backend/tests/integration/test_tenant_isolation.py`, replace:
```python
        "DELETE FROM ai_analyses",
        "INSERT INTO attack_techniques (id, stix_id, name, tactics, description, url, "
```
with:
```python
        "DELETE FROM ai_analyses",
        "INSERT INTO ai_usage (org_id, day, calls) SELECT org_id, day + 1, 1 FROM ai_usage",
        "UPDATE ai_usage SET calls = 0",
        "DELETE FROM ai_usage",
        "INSERT INTO attack_techniques (id, stix_id, name, tactics, description, url, "
```

In `backend/tests/integration/test_migrations.py`, replace:
```python
    "ai_analyses",
}
```
with:
```python
    "ai_analyses",
    "ai_usage",
}
```

In `backend/tests/integration/test_ai_schema.py`, replace:
```python
its finding's org, and the triage worker's role with only the rights it needs."""

from uuid import UUID, uuid7

```
with:
```python
its finding's org, and the triage worker's role with only the rights it needs."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid7

```

In `backend/tests/integration/test_ai_schema.py`, replace:
```python
        "UPDATE ai_analyses SET finding_id = gen_random_uuid()",
        "DELETE FROM ai_analyses",
        "DELETE FROM finding_techniques",
        "INSERT INTO finding_events (id, org_id, finding_id, actor_id, type) "
        "SELECT gen_random_uuid(), org_id, finding_id, feedback_by, 'commented' FROM ai_analyses",
```
with:
```python
        "UPDATE ai_analyses SET finding_id = gen_random_uuid()",
        "DELETE FROM ai_analyses",
        "UPDATE ai_usage SET org_id = gen_random_uuid()",
        "DELETE FROM ai_usage",
        "INSERT INTO finding_events (id, org_id, finding_id, actor_id, type) "
        "SELECT gen_random_uuid(), org_id, finding_id, feedback_by, 'commented' FROM ai_analyses",
```

In `backend/tests/integration/test_ai_schema.py`, replace:
```python
    assert tuple(flags) == (False, False)

```
with:
```python
    assert tuple(flags) == (False, False)


COUNT_A_CALL = (
    "INSERT INTO ai_usage (org_id, day, calls, input_tokens, output_tokens, cost_usd) "
    "VALUES (:org, (now() AT TIME ZONE 'UTC')::date, 1, 100, 20, 0.000013) "
    "ON CONFLICT (org_id, day) DO UPDATE SET calls = ai_usage.calls + EXCLUDED.calls, "
    "input_tokens = ai_usage.input_tokens + EXCLUDED.input_tokens, "
    "output_tokens = ai_usage.output_tokens + EXCLUDED.output_tokens, "
    "cost_usd = ai_usage.cost_usd + EXCLUDED.cost_usd"
)


def test_the_worker_counts_every_call_per_org_and_day(database: Database) -> None:
    tenant = add_tenant(database.admin)

    run_as_triage(database, tenant.org_id, COUNT_A_CALL, org=tenant.org_id)

    with database.admin.connect() as connection:
        counted = connection.execute(
            text(
                "SELECT calls, input_tokens, output_tokens, cost_usd FROM ai_usage "
                "WHERE org_id = :org"
            ),
            {"org": tenant.org_id},
        ).one()
    assert tuple(counted) == (2, 1900, 340, Decimal("0.000235"))


def test_the_worker_can_not_count_calls_for_another_org(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    with pytest.raises(ProgrammingError, match="row-level security"):
        run_as_triage(database, mine.org_id, COUNT_A_CALL, org=theirs.org_id)


def test_the_worker_may_take_back_only_the_ais_techniques(database: Database) -> None:
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO finding_techniques (finding_id, technique_id, source, org_id, "
                "rationale) VALUES (:finding, 'T1046', 'ai', :org, 'Many ports.')"
            ),
            {"finding": tenant.finding_id, "org": tenant.org_id},
        )

    removed = run_as_triage(
        database,
        tenant.org_id,
        "DELETE FROM finding_techniques WHERE finding_id = :finding",
        finding=tenant.finding_id,
    )

    with database.admin.connect() as connection:
        left: set[str] = set(
            connection.execute(
                text("SELECT source FROM finding_techniques WHERE finding_id = :finding"),
                {"finding": tenant.finding_id},
            ).scalars()
        )
    assert (removed, left) == (1, {"detector"})


def updated_at(database: Database, analysis_id: UUID) -> datetime:
    with database.admin.connect() as connection:
        found: datetime = connection.execute(
            text("SELECT updated_at FROM ai_analyses WHERE id = :id"), {"id": analysis_id}
        ).scalar_one()
    return found


def test_the_api_may_rate_an_analysis_without_making_it_newer(database: Database) -> None:
    tenant = add_tenant(database.admin)
    before = updated_at(database, tenant.analysis_id)

    with tenant_transaction(
        database.app_api, org_id=tenant.org_id, user_id=tenant.owner_id
    ) as connection:
        rated = connection.execute(
            text("UPDATE ai_analyses SET feedback = 'up', feedback_by = :user WHERE id = :id"),
            {"user": tenant.owner_id, "id": tenant.analysis_id},
        ).rowcount

    assert rated == 1
    assert updated_at(database, tenant.analysis_id) == before


def test_a_retried_analysis_is_newer(database: Database) -> None:
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        failed = add_analysis(connection, tenant.org_id, tenant.finding_id, status="failed")
    before = updated_at(database, failed)

    run_as_triage(
        database,
        tenant.org_id,
        "UPDATE ai_analyses SET error_code = 'provider_throttled' WHERE id = :id",
        id=failed,
    )

    after = updated_at(database, failed)
    assert after > before

```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/integration/test_ai_schema.py tests/integration/test_tenant_isolation.py tests/integration/test_migrations.py`
Expected: `72 failed, 4 passed`.
- Almost every test fails on `relation "ai_usage" does not exist`: the test data now counts a day's AI usage for each org.
- `test_every_migration_applies_rolls_back_and_applies_again` fails because `ai_usage` isn't among the tables.

- [ ] **Step 3: Write the migration**

`backend/migrations/versions/0010_ai_usage_feedback.py`:
```python
"""AI usage, feedback and the AI's techniques (spec §5.2, §5.4, §7; Plan 5c).

- `ai_usage` counts every model call per org and UTC day: calls, tokens and cost, including
  retries and repairs that store no analysis (the owner's decision). The triage worker adds to
  it; the API reads it for `GET …/usage`.
- `app_api` may rate an analysis (`feedback`, `feedback_by`) and nothing else on it. A rating
  doesn't make the analysis newer: `updated_at` moves only when an attempt changes it.
- `app_triage` may delete the AI's own techniques of a finding, never the detector's, so the
  latest succeeded analysis replaces them (the owner's decision).

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ai_usage (
            org_id uuid NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
            day date NOT NULL,
            calls integer NOT NULL DEFAULT 0 CHECK (calls >= 0),
            input_tokens bigint NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
            output_tokens bigint NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
            cost_usd numeric(12, 6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
            PRIMARY KEY (org_id, day)
        );
        ALTER TABLE ai_usage ENABLE ROW LEVEL SECURITY;
        ALTER TABLE ai_usage FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON ai_usage
            USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id());
        GRANT SELECT ON ai_usage TO app_api;
        GRANT SELECT, INSERT (org_id, day, calls, input_tokens, output_tokens, cost_usd),
            UPDATE (calls, input_tokens, output_tokens, cost_usd)
            ON ai_usage TO app_triage;

        GRANT UPDATE (feedback, feedback_by) ON ai_analyses TO app_api;
        DROP TRIGGER ai_analyses_updated_at ON ai_analyses;
        CREATE TRIGGER ai_analyses_updated_at
            BEFORE UPDATE OF status, output, input_tokens, output_tokens, cost_usd, latency_ms,
                error_code
            ON ai_analyses FOR EACH ROW EXECUTE FUNCTION set_updated_at();

        GRANT DELETE ON finding_techniques TO app_triage;
        CREATE POLICY ai_rows_only ON finding_techniques AS RESTRICTIVE
            FOR DELETE TO app_triage USING (source = 'ai');
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP POLICY ai_rows_only ON finding_techniques;
        REVOKE DELETE ON finding_techniques FROM app_triage;
        DROP TRIGGER ai_analyses_updated_at ON ai_analyses;
        CREATE TRIGGER ai_analyses_updated_at BEFORE UPDATE ON ai_analyses
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        REVOKE UPDATE (feedback, feedback_by) ON ai_analyses FROM app_api;
        DROP TABLE ai_usage;
        """
    )
```

- [ ] **Step 4: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `1003 passed, 1 skipped`.

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/versions/0010_ai_usage_feedback.py backend/tests
git commit -m "feat(db): count AI usage per org and day, let the API rate an analysis, and let the worker replace the AI's techniques" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Count every call, and replace the AI's techniques

**Files:**
- Modify: `backend/src/nettriage/adapters/ai_store.py`, `backend/src/nettriage/entrypoints/triage/explainer.py`
- Test: `backend/tests/integration/test_ai_store.py`, `backend/tests/worker/test_explainer.py`

**Interfaces:**
- Consumes: Task 1's `ai_usage` and grants; Plan 5a's `AnalysisRecord`, `save_analysis` and `Explainer` (with its `_Spent`); Plan 5b's `ExplainLater` and `_retrying`.
- Produces:
  - `AnalysisRecord.calls: int = 0`, which `save_analysis` adds to `ai_usage` in its own transaction;
  - `record_usage(engine, org_id, *, calls, input_tokens, output_tokens, cost_usd)` in `nettriage.adapters.ai_store`;
  - a succeeded `save_analysis` replaces the finding's AI techniques;
  - the Explainer passes `calls`, and counts the paid calls of a repair it hands back.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/integration/test_ai_store.py`, replace:
```python

from nettriage.adapters.ai_store import AnalysisRecord, find_cached, save_analysis

```
with:
```python

from nettriage.adapters.ai_store import AnalysisRecord, find_cached, record_usage, save_analysis

```

In `backend/tests/integration/test_ai_store.py`, replace:
```python
        techniques=(("T1595", "Ports probed from outside."),),
    )
```
with:
```python
        techniques=(("T1595", "Ports probed from outside."),),
        calls=1,
    )
```

In `backend/tests/integration/test_ai_store.py`, replace:
```python

def test_a_later_analysis_updates_the_ais_rationale(database: Database) -> None:
    tenant = add_tenant(database.admin)
    save_analysis(database.app_triage, record(tenant))

```
with:
```python

def test_a_later_success_replaces_the_ais_techniques(database: Database) -> None:
    tenant = add_tenant(database.admin)
    first = (("T1595", "Ports probed from outside."), ("T1046", "Many ports on one host."))
    save_analysis(database.app_triage, record(tenant, techniques=first))

```

In `backend/tests/integration/test_ai_store.py`, replace:
```python
    assert ai_history(database, tenant)[0] == [("T1595", "A clearer reason.")]

```
with:
```python
    assert ai_history(database, tenant)[0] == [("T1595", "A clearer reason.")]
    assert detector_techniques(database, tenant) == ["T1595"]


def test_a_later_success_without_techniques_clears_the_ais(database: Database) -> None:
    tenant = add_tenant(database.admin)
    save_analysis(database.app_triage, record(tenant))

    save_analysis(database.app_triage, record(tenant, prompt_version="v2", techniques=()))

    assert ai_history(database, tenant)[0] == []
    assert detector_techniques(database, tenant) == ["T1595"]


def detector_techniques(database: Database, tenant: Tenant) -> list[str]:
    with database.admin.begin() as connection:
        found: list[str] = list(
            connection.execute(
                text(
                    "SELECT technique_id FROM finding_techniques "
                    "WHERE finding_id = :finding AND source = 'detector'"
                ),
                {"finding": tenant.finding_id},
            ).scalars()
        )
    return found


def usage(database: Database, tenant: Tenant) -> tuple[int, int, int, Decimal]:
    """Today's usage for the org; `add_tenant` seeds one call of 1800 + 320 tokens."""
    with database.admin.begin() as connection:
        row = connection.execute(
            text(
                "SELECT calls, input_tokens, output_tokens, cost_usd FROM ai_usage "
                "WHERE org_id = :org AND day = (now() AT TIME ZONE 'UTC')::date"
            ),
            {"org": tenant.org_id},
        ).one()
    return row.calls, row.input_tokens, row.output_tokens, row.cost_usd


def test_every_call_an_attempt_made_is_counted_for_the_day(database: Database) -> None:
    tenant = add_tenant(database.admin)

    save_analysis(database.app_triage, record(tenant, calls=2, input_tokens=1900))

    assert usage(database, tenant) == (3, 3700, 620, Decimal("0.002322"))


def test_calls_are_counted_even_when_another_success_was_stored_first(
    database: Database,
) -> None:
    tenant = add_tenant(database.admin)
    save_analysis(database.app_triage, record(tenant))

    save_analysis(
        database.app_triage,
        record(tenant, status="failed", output=None, error_code="provider_unavailable"),
    )

    assert usage(database, tenant) == (3, 3600, 920, Decimal("0.004422"))


def test_an_attempt_without_calls_counts_nothing(database: Database) -> None:
    tenant = add_tenant(database.admin)

    save_analysis(
        database.app_triage,
        record(
            tenant,
            status="skipped_budget",
            output=None,
            error_code="budget_exhausted_org",
            input_tokens=None,
            output_tokens=None,
            cost_usd=None,
            latency_ms=None,
            calls=0,
        ),
    )

    assert usage(database, tenant) == (1, 1800, 320, Decimal("0.000222"))


def test_calls_that_store_no_analysis_are_counted_on_their_own(database: Database) -> None:
    tenant = add_tenant(database.admin)

    record_usage(
        database.app_triage,
        tenant.org_id,
        calls=1,
        input_tokens=900,
        output_tokens=300,
        cost_usd=Decimal("0.0021"),
    )

    assert usage(database, tenant) == (2, 2700, 620, Decimal("0.002322"))

```

In `backend/tests/worker/test_explainer.py`, replace:
```python
    ]

```
with:
```python
    ]


def todays_usage(database: Database, tenant: Tenant) -> tuple[int, int, int]:
    with database.admin.begin() as connection:
        row = connection.execute(
            text(
                "SELECT calls, input_tokens, output_tokens FROM ai_usage "
                "WHERE org_id = :org AND day = (now() AT TIME ZONE 'UTC')::date"
            ),
            {"org": tenant.org_id},
        ).one()
    return row.calls, row.input_tokens, row.output_tokens


def test_every_model_call_is_counted_in_the_orgs_usage(database: Database, rig: Rig) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [UNGROUNDED, answer()]

    rig.explainer.explain(tenant.org_id, tenant.finding_id)

    assert todays_usage(database, tenant) == (3, 1800 + 2 * 900, 320 + 2 * 300)


def test_a_paid_call_is_counted_even_when_the_repair_is_handed_back(
    database: Database, rig: Rig
) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [UNGROUNDED, ProviderError("provider_throttled")]

    with pytest.raises(ExplainLater):
        rig.explainer.explain(tenant.org_id, tenant.finding_id, last_delivery=False)

    assert todays_usage(database, tenant) == (2, 1800 + 900, 320 + 300)

```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/integration/test_ai_store.py tests/worker/test_explainer.py`
Expected: FAIL. Collection stops with 1 error: `cannot import name 'record_usage' from 'nettriage.adapters.ai_store'`.

- [ ] **Step 3: Count calls and replace techniques in the store**

In `backend/src/nettriage/adapters/ai_store.py`, replace:
```python

A succeeded analysis also adds the model's techniques to the finding (`source = 'ai'`) and an
`ai_explained` event to its history, in the same transaction."""

```
with:
```python

A succeeded analysis also replaces the finding's AI techniques (`source = 'ai'`) with its own,
never touching the detector's, and adds an `ai_explained` event to its history, in the same
transaction (the owner's decision, Plan 5c).

Every model call is counted in `ai_usage`, per org and UTC day, whether or not its analysis is
stored: an attempt counts its calls with the analysis, and calls that store nothing (a passing
failure handed back to SQS after a paid call) are counted on their own."""

```

In `backend/src/nettriage/adapters/ai_store.py`, replace:
```python
    techniques: tuple[tuple[str, str], ...] = ()

```
with:
```python
    techniques: tuple[tuple[str, str], ...] = ()
    # The model calls this attempt made, counted in `ai_usage` with their tokens and cost.
    calls: int = 0

```

In `backend/src/nettriage/adapters/ai_store.py`, replace:
```python

def save_analysis(engine: Engine, record: AnalysisRecord) -> UUID:
    """Store an attempt and return its analysis's ID. A succeeded analysis already stored for the
    same key wins: the attempt changes nothing."""
    with tenant_transaction(engine, org_id=record.org_id) as connection:
        saved = connection.execute(
```
with:
```python

_COUNT_USAGE = text(
    "INSERT INTO ai_usage (org_id, day, calls, input_tokens, output_tokens, cost_usd) "
    "VALUES (:org, (now() AT TIME ZONE 'UTC')::date, :calls, :input_tokens, :output_tokens, "
    ":cost) ON CONFLICT (org_id, day) DO UPDATE SET calls = ai_usage.calls + EXCLUDED.calls, "
    "input_tokens = ai_usage.input_tokens + EXCLUDED.input_tokens, "
    "output_tokens = ai_usage.output_tokens + EXCLUDED.output_tokens, "
    "cost_usd = ai_usage.cost_usd + EXCLUDED.cost_usd"
)


def record_usage(
    engine: Engine,
    org_id: UUID,
    *,
    calls: int,
    input_tokens: int,
    output_tokens: int,
    cost_usd: Decimal,
) -> None:
    """Count model calls whose analysis isn't stored, in their own transaction."""
    with tenant_transaction(engine, org_id=org_id) as connection:
        connection.execute(
            _COUNT_USAGE,
            {
                "org": org_id,
                "calls": calls,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost": cost_usd,
            },
        )


def save_analysis(engine: Engine, record: AnalysisRecord) -> UUID:
    """Store an attempt and return its analysis's ID. A succeeded analysis already stored for the
    same key wins: the attempt changes nothing, but its calls are still counted."""
    with tenant_transaction(engine, org_id=record.org_id) as connection:
        if record.calls:
            connection.execute(
                _COUNT_USAGE,
                {
                    "org": record.org_id,
                    "calls": record.calls,
                    "input_tokens": record.input_tokens or 0,
                    "output_tokens": record.output_tokens or 0,
                    "cost": record.cost_usd or Decimal(0),
                },
            )
        saved = connection.execute(
```

In `backend/src/nettriage/adapters/ai_store.py`, replace:
```python
        if record.status == "succeeded":
            _add_techniques(connection, record)
            connection.execute(
```
with:
```python
        if record.status == "succeeded":
            _replace_techniques(connection, record)
            connection.execute(
```

In `backend/src/nettriage/adapters/ai_store.py`, replace:
```python

def _add_techniques(connection: Connection, record: AnalysisRecord) -> None:
    if not record.techniques:
```
with:
```python

def _replace_techniques(connection: Connection, record: AnalysisRecord) -> None:
    connection.execute(
        text(
            "DELETE FROM finding_techniques WHERE org_id = :org AND finding_id = :finding "
            "AND source = 'ai'"
        ),
        {"org": record.org_id, "finding": record.finding_id},
    )
    if not record.techniques:
```

- [ ] **Step 4: Pass the calls from the explainer**

In `backend/src/nettriage/entrypoints/triage/explainer.py`, replace:
```python

from nettriage.adapters.ai_budget import AiBudget, BudgetExhausted, BudgetUnavailable
from nettriage.adapters.ai_store import AnalysisRecord, AnalysisStatus, find_cached, save_analysis
from nettriage.adapters.ai_subjects import load_subject
from nettriage.adapters.audit_log import record
```
with:
```python

from nettriage.adapters.ai_budget import AiBudget, BudgetExhausted, BudgetUnavailable
from nettriage.adapters.ai_store import (
    AnalysisRecord,
    AnalysisStatus,
    find_cached,
    record_usage,
    save_analysis,
)
from nettriage.adapters.ai_subjects import load_subject
from nettriage.adapters.audit_log import record
```

In `backend/src/nettriage/entrypoints/triage/explainer.py`, replace:
```python
                if error.transient and not last_delivery:
                    logger.info("finding_explain_later", extra={"error_code": error.code})
                    raise ExplainLater(error.code) from None
                return self._store(subject, key, "failed", spent, error_code=error.code)
```
with:
```python
                if error.transient and not last_delivery:
                    logger.info("finding_explain_later", extra={"error_code": error.code})
                    self._count_unstored(org_id, spent)
                    raise ExplainLater(error.code) from None
                return self._store(subject, key, "failed", spent, error_code=error.code)
```

In `backend/src/nettriage/entrypoints/triage/explainer.py`, replace:
```python
            if output is None
            else tuple((claim.id, claim.rationale) for claim in output.attack_techniques),
        )
        analysis_id = self._retrying(lambda: save_analysis(self.database, stored))
```
with:
```python
            if output is None
            else tuple((claim.id, claim.rationale) for claim in output.attack_techniques),
            calls=spent.calls,
        )
        analysis_id = self._retrying(lambda: save_analysis(self.database, stored))
```

In `backend/src/nettriage/entrypoints/triage/explainer.py`, replace:
```python
                self.sleep(delay)
        return step()

    def _audit_refusal(
```
with:
```python
                self.sleep(delay)
        return step()

    def _count_unstored(self, org_id: UUID, spent: _Spent) -> None:
        """Paid calls whose analysis isn't stored (the repair was handed back to SQS) still
        count in the org's usage. Best effort: the message is handed back either way."""
        if not spent.calls:
            return
        try:
            self._retrying(
                lambda: record_usage(
                    self.database,
                    org_id,
                    calls=spent.calls,
                    input_tokens=spent.input_tokens,
                    output_tokens=spent.output_tokens,
                    cost_usd=spent.cost_usd,
                )
            )
        except Exception as error:
            logger.warning("usage_not_counted", extra={"error_code": type(error).__name__})

    def _audit_refusal(
```

- [ ] **Step 5: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `1010 passed, 1 skipped`.

- [ ] **Step 6: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(ai): count every model call in the org's daily usage, and let the latest success replace the AI's techniques" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Re-run a finding's explanation

**Files:**
- Create: `backend/src/nettriage/adapters/ai_analyses.py`, `backend/src/nettriage/entrypoints/api/routes/ai.py`
- Modify:
  - `backend/src/nettriage/adapters/findings.py`;
  - `backend/src/nettriage/entrypoints/api/finding_schemas.py`, `services.py`, `wiring.py` and `app.py`;
  - `backend/tests/conftest.py`
- Test: `backend/tests/api/test_ai_routes.py`, `backend/tests/security/test_route_access.py`, `backend/tests/security/test_authorization_matrix.py`

**Interfaces:**
- Consumes:
  - Plan 5a's `load_subject`, `user_content`, `input_hash`, `PROMPT_VERSION` and `find_cached`;
  - Plan 5b's `TriageQueue`, `TriageQueueError`, `Settings.triage_queue_url` and `Settings.bedrock_model_id`;
  - Plan 5b's `AiAnalysis` and `AiAnalysisOut`;
  - Plan 4c's `org_rules`, `enforce`, `audit` and `unavailable`;
  - the harness's `database_client`, `services` and `clock`.
- Produces:
  - `Services.triage: TriageQueue` and `Services.ai_model_id: str` (tests use `"fake-triage"` and a moto queue);
  - `read_analysis(connection, analysis_id) -> AiAnalysis` in `nettriage.adapters.findings`;
  - `current_explanation(engine, org_id, finding_id, *, model_id, prompt_version=PROMPT_VERSION) -> AiAnalysis | None` in `nettriage.adapters.ai_analyses`;
  - `RerunOut(status, ai_analysis)`;
  - `POST /api/v1/orgs/{org_id}/findings/{finding_id}/ai-analyses`, with the `ai:request` permission.

- [ ] **Step 1: Write the failing tests**

`backend/tests/api/test_ai_routes.py`:
```python
"""The AI routes (spec §7, §8.3): re-run a finding's explanation, rate it, and read the org's AI
usage. A re-run is queued for the triage worker unless the finding's latest analysis for the
current model, prompt and input already succeeded (the owner's decision, Plan 5c)."""

import json
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID, uuid7

import pytest
from browser import signed_in_as
from conftest import APP_ORIGIN, Database, FakeClock
from fastapi.testclient import TestClient
from sqlalchemy import text
from tenantdata import add_finding, add_member, add_org, add_upload, add_user

from nettriage.adapters.ai_subjects import load_subject
from nettriage.adapters.triage_queue import TriageQueue
from nettriage.application.ai_input import input_hash, user_content
from nettriage.entrypoints.api.app import create_app
from nettriage.entrypoints.api.services import Services
from nettriage.platform.config import Settings


@pytest.fixture
def org(database: Database) -> tuple[UUID, UUID, UUID]:
    """An org, its owner, and a finding of an analyzed upload in it."""
    with database.admin.begin() as connection:
        owner = add_user(connection)
        org_id = add_org(connection, owner)
        add_member(connection, org_id, owner, "owner")
        upload = add_upload(connection, org_id, owner, status="analyzed")
        finding = add_finding(connection, org_id, upload)
    return org_id, owner, finding


@dataclass
class Owner:
    """The API, signed in as the org's owner, with the headers a state-changing request needs."""

    client: TestClient
    headers: dict[str, str]

    def post(self, path: str) -> Any:
        return self.client.post(path, headers=self.headers)


@pytest.fixture
def signed_in(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID, UUID],
) -> Owner:
    return Owner(database_client, signed_in_as(database_client, services.sessions, org[1], clock()))


def rerun_path(org: tuple[UUID, UUID, UUID]) -> str:
    return f"/api/v1/orgs/{org[0]}/findings/{org[2]}/ai-analyses"


def add_ai_analysis(
    database: Database, org: tuple[UUID, UUID, UUID], *, status: str, model_id: str
) -> UUID:
    """An analysis of the finding's current input, by `model_id` with prompt v1."""
    subject = load_subject(database.admin, org[0], org[2])
    analysis_id = uuid7()
    with database.admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ai_analyses (id, org_id, finding_id, status, provider, model_id, "
                "prompt_version, output_schema_version, input_hash, output, error_code) VALUES "
                "(:id, :org, :finding, :status, 'fake', :model, 'v1', 'v1', :hash, "
                "CAST(:output AS jsonb), :error)"
            ),
            {
                "id": analysis_id,
                "org": org[0],
                "finding": org[2],
                "status": status,
                "model": model_id,
                "hash": input_hash(user_content(subject)),
                "output": '{"summary": "A scan."}' if status == "succeeded" else None,
                "error": None if status == "succeeded" else "provider_throttled",
            },
        )
    return analysis_id


def queued(services: Services) -> list[dict[str, Any]]:
    """The bodies of the triage messages waiting in the queue, taken off it."""
    client, url = services.triage.client, services.triage.url
    bodies: list[dict[str, Any]] = []
    while batch := client.receive_message(QueueUrl=url, MaxNumberOfMessages=10).get("Messages"):
        for message in batch:
            bodies.append(json.loads(message["Body"]))
            client.delete_message(QueueUrl=url, ReceiptHandle=message["ReceiptHandle"])
    return bodies


def audited(database: Database, org_id: UUID) -> list[str]:
    with database.admin.begin() as connection:
        return list(
            connection.execute(
                text("SELECT action FROM audit_log WHERE org_id = :org AND action LIKE 'ai.%'"),
                {"org": org_id},
            ).scalars()
        )


def test_an_unexplained_finding_is_queued_for_the_worker(
    signed_in: Owner,
    services: Services,
    database: Database,
    org: tuple[UUID, UUID, UUID],
) -> None:
    response = signed_in.post(rerun_path(org))

    assert response.status_code == 202, response.text
    assert response.json() == {"status": "queued", "ai_analysis": None}
    assert queued(services) == [{"org_id": str(org[0]), "finding_id": str(org[2])}]
    assert audited(database, org[0]) == ["ai.rerun_requested"]


def test_a_failed_explanation_is_queued_again(
    signed_in: Owner,
    services: Services,
    database: Database,
    org: tuple[UUID, UUID, UUID],
) -> None:
    add_ai_analysis(database, org, status="failed", model_id=services.ai_model_id)

    response = signed_in.post(rerun_path(org))

    assert response.status_code == 202, response.text
    assert len(queued(services)) == 1


def test_an_explained_finding_returns_its_answer_at_no_cost(
    signed_in: Owner,
    services: Services,
    database: Database,
    org: tuple[UUID, UUID, UUID],
) -> None:
    explained = add_ai_analysis(database, org, status="succeeded", model_id=services.ai_model_id)

    response = signed_in.post(rerun_path(org))

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["ai_analysis"]["id"]) == ("explained", str(explained))
    assert body["ai_analysis"]["output"] == {"summary": "A scan."}
    assert queued(services) == []
    assert audited(database, org[0]) == []


def test_an_answer_by_another_model_is_explained_again(
    signed_in: Owner,
    services: Services,
    database: Database,
    org: tuple[UUID, UUID, UUID],
) -> None:
    add_ai_analysis(database, org, status="succeeded", model_id="an-older-model")

    response = signed_in.post(rerun_path(org))

    assert response.status_code == 202, response.text
    assert len(queued(services)) == 1


def test_re_runs_are_limited_per_user(signed_in: Owner, org: tuple[UUID, UUID, UUID]) -> None:
    answers = [signed_in.post(rerun_path(org)).status_code for _ in range(4)]

    assert answers == [202, 202, 202, 429]


def test_a_finding_of_another_org_is_not_found(
    signed_in: Owner, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    with database.admin.begin() as connection:
        stranger = add_user(connection)
        other_org = add_org(connection, stranger)
        theirs = add_finding(
            connection, other_org, add_upload(connection, other_org, stranger, "analyzed")
        )

    response = signed_in.post(f"/api/v1/orgs/{org[0]}/findings/{theirs}/ai-analyses")

    assert response.status_code == 404


def test_a_queue_that_refuses_is_a_503_and_nothing_is_audited(
    settings: Settings,
    services: Services,
    database: Database,
    org: tuple[UUID, UUID, UUID],
    clock: FakeClock,
) -> None:
    gone = TriageQueue(services.triage.client, services.triage.url.replace("triage", "gone"))
    broken = replace(services, database=database.app_api, triage=gone)
    client = TestClient(create_app(settings, broken), base_url=APP_ORIGIN)
    headers = signed_in_as(client, broken.sessions, org[1], clock())

    response = client.post(rerun_path(org), headers=headers)

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert audited(database, org[0]) == []
```

In `backend/tests/security/test_route_access.py`, replace:
```python
    ("POST", "/api/v1/orgs/{org_id}/findings/{finding_id}/comments"): "findings:comment",
    ("GET", "/api/v1/attack-techniques/{technique_id}"): "signed in",
```
with:
```python
    ("POST", "/api/v1/orgs/{org_id}/findings/{finding_id}/comments"): "findings:comment",
    ("POST", "/api/v1/orgs/{org_id}/findings/{finding_id}/ai-analyses"): "ai:request",
    ("GET", "/api/v1/attack-techniques/{technique_id}"): "signed in",
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
    ),
}
```
with:
```python
    ),
    "re-run AI explanation": (
        "POST",
        "/api/v1/orgs/{org}/findings/{finding}/ai-analyses",
        None,
    ),
}
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
    "comment on finding": (201, 201, 201, 403, 404, 401),
}
```
with:
```python
    "comment on finding": (201, 201, 201, 403, 404, 401),
    "re-run AI explanation": (202, 202, 202, 403, 404, 401),
}
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/api/test_ai_routes.py tests/security/test_route_access.py tests/security/test_authorization_matrix.py`
Expected: `12 failed, 130 passed`.
- Six of the new route tests, the matrix's re-run row for every caller but the outsider, and `test_every_route_declares_exactly_one_access_rule`. Most get `404` (there's no route yet), and two get `AttributeError: 'Services' object has no attribute 'ai_model_id'`.
- `test_a_finding_of_another_org_is_not_found` and the matrix's outsider row already pass, because a route that doesn't exist is a 404 too. They pass for the right reason once the route exists.

- [ ] **Step 3: Give the API the triage queue and the worker's model**

In `backend/src/nettriage/entrypoints/api/services.py`, replace:
```python
from nettriage.adapters.sessions import SessionStore
from nettriage.adapters.upload_storage import UploadStorage
```
with:
```python
from nettriage.adapters.sessions import SessionStore
from nettriage.adapters.triage_queue import TriageQueue
from nettriage.adapters.upload_storage import UploadStorage
```

In `backend/src/nettriage/entrypoints/api/services.py`, replace:
```python
    metrics: AppMetrics
    unknown_sessions: LocalLimiter = field(
```
with:
```python
    metrics: AppMetrics
    # Re-runs go to the triage worker's queue; an answer by this model is the current one.
    triage: TriageQueue
    ai_model_id: str
    unknown_sessions: LocalLimiter = field(
```

In `backend/src/nettriage/entrypoints/api/wiring.py`, replace:
```python
from nettriage.adapters.sessions import SessionStore
from nettriage.adapters.upload_storage import UploadStorage, uploads_client
```
with:
```python
from nettriage.adapters.sessions import SessionStore
from nettriage.adapters.triage_queue import TriageQueue
from nettriage.adapters.upload_storage import UploadStorage, uploads_client
```

In `backend/src/nettriage/entrypoints/api/wiring.py`, replace:
```python
        metrics=AppMetrics(),
    )
```
with:
```python
        metrics=AppMetrics(),
        triage=TriageQueue(session.client("sqs", config=AWS_CONFIG), settings.triage_queue_url),
        ai_model_id=settings.bedrock_model_id,
    )
```

In `backend/tests/conftest.py`, replace:
```python
from nettriage.adapters.sessions import SessionStore
from nettriage.adapters.upload_storage import UploadStorage, uploads_client
```
with:
```python
from nettriage.adapters.sessions import SessionStore
from nettriage.adapters.triage_queue import TriageQueue
from nettriage.adapters.upload_storage import UploadStorage, uploads_client
```

In `backend/tests/conftest.py`, replace:
```python
    table, client = runtime_table.name, runtime_table.client
    return Services(
```
with:
```python
    table, client = runtime_table.name, runtime_table.client
    sqs = boto3.client("sqs", region_name=REGION)
    queue_url = sqs.create_queue(QueueName="nettriage-test-triage")["QueueUrl"]
    return Services(
```

In `backend/tests/conftest.py`, replace:
```python
        metrics=AppMetrics(MeterProvider(metric_readers=[metric_reader])),
    )
```
with:
```python
        metrics=AppMetrics(MeterProvider(metric_readers=[metric_reader])),
        triage=TriageQueue(sqs, queue_url),
        ai_model_id="fake-triage",
    )
```

- [ ] **Step 4: Find the current explanation, and answer the re-run**

In `backend/src/nettriage/adapters/findings.py`, replace:
```python

def _latest_analysis(connection: Connection, finding_id: UUID) -> AiAnalysis | None:
```
with:
```python

_ANALYSIS = (
    "id, status, provider, model_id, prompt_version, output_schema_version, output, error_code, "
    "input_tokens, output_tokens, cost_usd, latency_ms, created_at, updated_at"
)


def _latest_analysis(connection: Connection, finding_id: UUID) -> AiAnalysis | None:
```

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
        text(
            "SELECT id, status, provider, model_id, prompt_version, output_schema_version, "
            "output, error_code, input_tokens, output_tokens, cost_usd, latency_ms, created_at, "
            "updated_at FROM ai_analyses WHERE finding_id = :id "
            "ORDER BY updated_at DESC, id DESC LIMIT 1"
```
with:
```python
        text(
            f"SELECT {_ANALYSIS} FROM ai_analyses WHERE finding_id = :id "  # noqa: S608
            "ORDER BY updated_at DESC, id DESC LIMIT 1"
```

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
    return None if row is None else AiAnalysis(**row._mapping)

```
with:
```python
    return None if row is None else AiAnalysis(**row._mapping)


def read_analysis(connection: Connection, analysis_id: UUID) -> AiAnalysis:
    """One analysis of the connection's org, by its ID."""
    row = connection.execute(
        text(f"SELECT {_ANALYSIS} FROM ai_analyses WHERE id = :id"),  # noqa: S608
        {"id": analysis_id},
    ).one()
    return AiAnalysis(**row._mapping)

```

`backend/src/nettriage/adapters/ai_analyses.py`:
```python
"""The API's side of AI analyses (spec §7, §8.3), as `app_api` in the caller's org: whether a
finding is already explained for the current model and prompt."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Engine

from nettriage.adapters.ai_store import find_cached
from nettriage.adapters.ai_subjects import load_subject
from nettriage.adapters.findings import AiAnalysis, read_analysis
from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.ai_input import PROMPT_VERSION, input_hash, user_content


def current_explanation(
    engine: Engine,
    org_id: UUID,
    finding_id: UUID,
    *,
    model_id: str,
    prompt_version: str = PROMPT_VERSION,
) -> AiAnalysis | None:
    """The finding's succeeded analysis for the current model, prompt and input, if there is one
    (the triage worker's cache). Raises NotFound for a finding outside the org."""
    subject = load_subject(engine, org_id, finding_id)
    cached = find_cached(
        engine,
        org_id,
        finding_id,
        model_id=model_id,
        prompt_version=prompt_version,
        input_hash=input_hash(user_content(subject)),
    )
    if cached is None:
        return None
    with tenant_transaction(engine, org_id=org_id) as connection:
        return read_analysis(connection, cached.id)
```

In `backend/src/nettriage/entrypoints/api/finding_schemas.py`, replace:
```python
from decimal import Decimal
from typing import Any
from uuid import UUID
```
with:
```python
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID
```

In `backend/src/nettriage/entrypoints/api/finding_schemas.py`, replace:
```python

class FindingOut(FindingSummaryOut):
```
with:
```python

class RerunOut(BaseModel):
    """`queued` (202): the triage worker explains the finding again. `explained` (200): its
    latest analysis for the current model, prompt and input already succeeded, so that answer is
    returned and nothing is spent."""

    status: Literal["queued", "explained"]
    ai_analysis: AiAnalysisOut | None


class FindingOut(FindingSummaryOut):
```

`backend/src/nettriage/entrypoints/api/routes/ai.py`:
```python
"""AI explanations through the API (spec §7, §8.3): re-run a finding's explanation.

A re-run is queued for the triage worker, which explains the finding within the org's budget,
unless the finding's latest analysis for the current model, prompt and input already succeeded:
then that answer is returned and nothing is spent (the owner's decision, Plan 5c). Re-runs are
limited per user (`ai.rerun.user`: 10 an hour, 3 at once)."""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from nettriage.adapters.ai_analyses import current_explanation
from nettriage.adapters.triage_queue import TriageQueueError
from nettriage.application.rate_limits import POLICIES
from nettriage.entrypoints.api.access import OrgContext, OrgMember, enforce, unavailable
from nettriage.entrypoints.api.auditing import audit
from nettriage.entrypoints.api.finding_schemas import AiAnalysisOut, RerunOut
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.services import get_services
from nettriage.platform.trace_context import current_traceparent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/orgs/{org_id}/findings/{finding_id}/ai-analyses")

RERUN = "Explaining this finding again"


@router.post(
    "",
    status_code=202,
    response_model=RerunOut,
    responses={200: {"model": RerunOut, "description": "Already explained: the answer"}},
)
def rerun(
    request: Request,
    org_id: UUID,
    finding_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("ai:request"))],
) -> JSONResponse:
    """Explain the finding again: 202 when it's queued, or 200 with the answer when it's already
    explained for the current model and prompt."""
    services = get_services(request)
    enforce(request, POLICIES["ai.rerun.user"], str(org.user_id), actor=org.user_id)
    with org_rules(request, RERUN, org=org, permission="ai:request"):
        explained = current_explanation(
            services.database, org.org_id, finding_id, model_id=services.ai_model_id
        )
    if explained is not None:
        body = RerunOut(status="explained", ai_analysis=AiAnalysisOut.of(explained))
        return JSONResponse(body.model_dump(mode="json"), status_code=200)
    try:
        services.triage.send(org.org_id, [finding_id], current_traceparent())
    except (BotoCoreError, ClientError, TriageQueueError) as error:
        logger.warning("rerun_not_queued", extra={"error_code": type(error).__name__})
        raise unavailable(RERUN) from None
    audit(
        request,
        action="ai.rerun_requested",
        outcome="success",
        actor_user_id=org.user_id,
        org_id=org.org_id,
        target_type="finding",
        target_id=str(finding_id),
    )
    return JSONResponse(
        RerunOut(status="queued", ai_analysis=None).model_dump(mode="json"), status_code=202
    )
```

In `backend/src/nettriage/entrypoints/api/app.py`, replace:
```python
from nettriage.entrypoints.api.routes import (
    attack_techniques,
```
with:
```python
from nettriage.entrypoints.api.routes import (
    ai,
    attack_techniques,
```

In `backend/src/nettriage/entrypoints/api/app.py`, replace:
```python
        triage,
        attack_techniques,
```
with:
```python
        triage,
        ai,
        attack_techniques,
```

- [ ] **Step 5: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `1023 passed, 1 skipped`.

- [ ] **Step 6: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(api): re-run a finding's AI explanation: queued for the worker, or the current answer at no cost" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Rate an explanation

**Files:**
- Modify:
  - `backend/src/nettriage/adapters/ai_analyses.py`, `backend/src/nettriage/adapters/findings.py`;
  - `backend/src/nettriage/entrypoints/api/finding_schemas.py`, `backend/src/nettriage/entrypoints/api/routes/ai.py`
- Test: `backend/tests/api/test_ai_routes.py`, `backend/tests/api/test_finding_routes.py`, `backend/tests/security/test_route_access.py`, `backend/tests/security/test_authorization_matrix.py`

**Interfaces:**
- Consumes: Task 1's feedback grant and narrowed trigger; Task 3's `ai_analyses.py`, `read_analysis`, `routes/ai.py` and test helpers (`Owner`, `add_ai_analysis`); Plan 3c's `OrgRuleError` and `NotFound`.
- Produces:
  - `AiAnalysis.feedback: str | None` and `AiAnalysis.feedback_by: UUID | None`, also in `AiAnalysisOut`;
  - `FeedbackIn(Strict)` with `feedback: Literal["up", "down"]`;
  - `NotExplained(OrgRuleError)` and `rate_analysis(engine, org_id, user_id, finding_id, analysis_id, feedback) -> AiAnalysis` in `nettriage.adapters.ai_analyses`;
  - `PUT /api/v1/orgs/{org_id}/findings/{finding_id}/ai-analyses/{analysis_id}/feedback`, with the `ai:feedback` permission, answering `AiAnalysisOut`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/api/test_ai_routes.py`, replace:
```python

    def post(self, path: str) -> Any:
        return self.client.post(path, headers=self.headers)


@pytest.fixture
```
with:
```python

    def post(self, path: str) -> Any:
        return self.client.post(path, headers=self.headers)

    def put(self, path: str, body: dict[str, Any]) -> Any:
        return self.client.put(path, json=body, headers=self.headers)


@pytest.fixture
```

In `backend/tests/api/test_ai_routes.py`, replace:
```python
    assert response.headers["content-type"] == "application/problem+json"
    assert audited(database, org[0]) == []

```
with:
```python
    assert response.headers["content-type"] == "application/problem+json"
    assert audited(database, org[0]) == []


def feedback_path(org: tuple[UUID, UUID, UUID], analysis_id: UUID) -> str:
    return f"/api/v1/orgs/{org[0]}/findings/{org[2]}/ai-analyses/{analysis_id}/feedback"


def test_an_explanation_is_rated_by_its_reader(
    signed_in: Owner, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    explained = add_ai_analysis(database, org, status="succeeded", model_id="fake-triage")

    response = signed_in.put(feedback_path(org, explained), {"feedback": "up"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["id"], body["feedback"], body["feedback_by"]) == (
        str(explained),
        "up",
        str(org[1]),
    )


def test_a_rating_can_change_and_shows_on_the_finding(
    signed_in: Owner, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    explained = add_ai_analysis(database, org, status="succeeded", model_id="fake-triage")
    signed_in.put(feedback_path(org, explained), {"feedback": "up"})

    signed_in.put(feedback_path(org, explained), {"feedback": "down"})

    finding = signed_in.client.get(f"/api/v1/orgs/{org[0]}/findings/{org[2]}").json()
    assert finding["ai_analysis"]["feedback"] == "down"


def test_only_an_explanation_that_succeeded_can_be_rated(
    signed_in: Owner, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    failed = add_ai_analysis(database, org, status="failed", model_id="fake-triage")

    response = signed_in.put(feedback_path(org, failed), {"feedback": "down"})

    assert response.status_code == 409


def test_an_analysis_of_another_finding_is_not_found(
    signed_in: Owner, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    with database.admin.begin() as connection:
        other = add_finding(
            connection, org[0], add_upload(connection, org[0], org[1], status="analyzed")
        )
    theirs = add_ai_analysis(
        database, (org[0], org[1], other), status="succeeded", model_id="fake-triage"
    )

    response = signed_in.put(feedback_path(org, theirs), {"feedback": "up"})

    assert response.status_code == 404


@pytest.mark.parametrize(
    "body", [{"feedback": "meh"}, {"feedback": None}, {"feedback": "up", "by": "someone"}, {}]
)
def test_a_rating_is_up_or_down_and_nothing_else(
    signed_in: Owner, database: Database, org: tuple[UUID, UUID, UUID], body: dict[str, Any]
) -> None:
    explained = add_ai_analysis(database, org, status="succeeded", model_id="fake-triage")

    response = signed_in.put(feedback_path(org, explained), body)

    assert response.status_code == 422

```

In `backend/tests/api/test_finding_routes.py`, replace:
```python
        "latency_ms": 1450,
        "created_at": "2026-09-28T10:00:00Z",
```
with:
```python
        "latency_ms": 1450,
        "feedback": None,
        "feedback_by": None,
        "created_at": "2026-09-28T10:00:00Z",
```

In `backend/tests/security/test_route_access.py`, replace:
```python
    ("POST", "/api/v1/orgs/{org_id}/findings/{finding_id}/ai-analyses"): "ai:request",
    ("GET", "/api/v1/attack-techniques/{technique_id}"): "signed in",
```
with:
```python
    ("POST", "/api/v1/orgs/{org_id}/findings/{finding_id}/ai-analyses"): "ai:request",
    (
        "PUT",
        "/api/v1/orgs/{org_id}/findings/{finding_id}/ai-analyses/{analysis_id}/feedback",
    ): "ai:feedback",
    ("GET", "/api/v1/attack-techniques/{technique_id}"): "signed in",
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
from fastapi.testclient import TestClient
from tenantdata import add_finding, add_invitation, add_member, add_org, add_upload, add_user

```
with:
```python
from fastapi.testclient import TestClient
from tenantdata import (
    add_analysis,
    add_finding,
    add_invitation,
    add_member,
    add_org,
    add_upload,
    add_user,
)

```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
    ),
}
```
with:
```python
    ),
    "rate AI explanation": (
        "PUT",
        "/api/v1/orgs/{org}/findings/{finding}/ai-analyses/{analysis}/feedback",
        {"feedback": "up"},
    ),
}
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
    "re-run AI explanation": (202, 202, 202, 403, 404, 401),
}
```
with:
```python
    "re-run AI explanation": (202, 202, 202, 403, 404, 401),
    "rate AI explanation": (200, 200, 200, 403, 404, 401),
}
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
        finding = add_finding(connection, org, upload)
        connection.exec_driver_sql(
```
with:
```python
        finding = add_finding(connection, org, upload)
        analysis = add_analysis(connection, org, finding)
        connection.exec_driver_sql(
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
            "finding": str(finding),
            "technique": "T1595",
```
with:
```python
            "finding": str(finding),
            "analysis": str(analysis),
            "technique": "T1595",
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
                    "finding": "{finding_id}",
                    "technique": "{technique_id}",
```
with:
```python
                    "finding": "{finding_id}",
                    "analysis": "{analysis_id}",
                    "technique": "{technique_id}",
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/api/test_ai_routes.py tests/api/test_finding_routes.py tests/security/test_route_access.py tests/security/test_authorization_matrix.py`
Expected: `14 failed, 159 passed`.
- Seven of the new rating cases and the matrix's rating row for every caller but the outsider, with `404` (there's no route yet).
- `test_every_route_declares_exactly_one_access_rule`.
- `test_a_finding_shows_its_latest_ai_analysis_with_what_it_said_and_cost`, because the analysis has no `feedback` or `feedback_by` yet.
- `test_an_analysis_of_another_finding_is_not_found` already passes, as in Task 3.

- [ ] **Step 3: Carry the rating in the analysis**

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
    latency_ms: int | None
    created_at: datetime
```
with:
```python
    latency_ms: int | None
    # A reader's rating of a succeeded explanation (`up` or `down`), and who gave it.
    feedback: str | None
    feedback_by: UUID | None
    created_at: datetime
```

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
    "id, status, provider, model_id, prompt_version, output_schema_version, output, error_code, "
    "input_tokens, output_tokens, cost_usd, latency_ms, created_at, updated_at"
)
```
with:
```python
    "id, status, provider, model_id, prompt_version, output_schema_version, output, error_code, "
    "input_tokens, output_tokens, cost_usd, latency_ms, feedback, feedback_by, created_at, "
    "updated_at"
)
```

In `backend/src/nettriage/entrypoints/api/finding_schemas.py`, replace:
```python
    FindingTechnique,
)


```
with:
```python
    FindingTechnique,
)
from nettriage.entrypoints.api.schemas import Strict


```

In `backend/src/nettriage/entrypoints/api/finding_schemas.py`, replace:
```python
    cost_usd: Decimal | None
    latency_ms: int | None
    created_at: datetime
    updated_at: datetime
```
with:
```python
    cost_usd: Decimal | None
    latency_ms: int | None
    feedback: Literal["up", "down"] | None
    feedback_by: UUID | None
    created_at: datetime
    updated_at: datetime
```

In `backend/src/nettriage/entrypoints/api/finding_schemas.py`, replace:
```python
    def of(cls, analysis: AiAnalysis) -> AiAnalysisOut:
        return cls(**vars(analysis))


```
with:
```python
    def of(cls, analysis: AiAnalysis) -> AiAnalysisOut:
        return cls(**vars(analysis))


class FeedbackIn(Strict):
    """A reader's rating of an explanation: `up` or `down` (spec §7)."""

    feedback: Literal["up", "down"]


```

- [ ] **Step 4: Rate a succeeded analysis**

In `backend/src/nettriage/adapters/ai_analyses.py`, replace:
```python
"""The API's side of AI analyses (spec §7, §8.3), as `app_api` in the caller's org: whether a
finding is already explained for the current model and prompt."""

from __future__ import annotations
```
with:
```python
"""The API's side of AI analyses (spec §7, §8.3), as `app_api` in the caller's org: whether a
finding is already explained for the current model and prompt, and a reader's rating of an
explanation."""

from __future__ import annotations
```

In `backend/src/nettriage/adapters/ai_analyses.py`, replace:
```python
from uuid import UUID

from sqlalchemy import Engine

from nettriage.adapters.ai_store import find_cached
```
with:
```python
from uuid import UUID

from sqlalchemy import Engine, text

from nettriage.adapters.ai_store import find_cached
```

In `backend/src/nettriage/adapters/ai_analyses.py`, replace:
```python
from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.ai_input import PROMPT_VERSION, input_hash, user_content


```
with:
```python
from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.ai_input import PROMPT_VERSION, input_hash, user_content
from nettriage.application.organizations import NotFound, OrgRuleError


class NotExplained(OrgRuleError):
    """Only an explanation that succeeded can be rated."""


```

In `backend/src/nettriage/adapters/ai_analyses.py`, replace:
```python
        return read_analysis(connection, cached.id)

```
with:
```python
        return read_analysis(connection, cached.id)


def rate_analysis(
    engine: Engine,
    org_id: UUID,
    user_id: UUID,
    finding_id: UUID,
    analysis_id: UUID,
    feedback: str,
) -> AiAnalysis:
    """Record the reader's rating of one of the finding's succeeded analyses; a new rating
    replaces the previous one. Rating doesn't make the analysis newer."""
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        rated = connection.execute(
            text(
                "UPDATE ai_analyses SET feedback = :feedback, feedback_by = :user "
                "WHERE id = :id AND finding_id = :finding AND status = 'succeeded' RETURNING id"
            ),
            {"feedback": feedback, "user": user_id, "id": analysis_id, "finding": finding_id},
        ).scalar_one_or_none()
        if rated is None:
            exists = connection.execute(
                text("SELECT 1 FROM ai_analyses WHERE id = :id AND finding_id = :finding"),
                {"id": analysis_id, "finding": finding_id},
            ).scalar_one_or_none()
            if exists is None:
                raise NotFound("No such AI analysis.")
            raise NotExplained("Only an AI explanation that succeeded can be rated.")
        return read_analysis(connection, analysis_id)

```

In `backend/src/nettriage/entrypoints/api/routes/ai.py`, replace:
```python
"""AI explanations through the API (spec §7, §8.3): re-run a finding's explanation.

A re-run is queued for the triage worker, which explains the finding within the org's budget,
unless the finding's latest analysis for the current model, prompt and input already succeeded:
```
with:
```python
"""AI explanations through the API (spec §7, §8.3): re-run a finding's explanation, and rate one.

A re-run is queued for the triage worker, which explains the finding within the org's budget,
unless the finding's latest analysis for the current model, prompt and input already succeeded:
```

In `backend/src/nettriage/entrypoints/api/routes/ai.py`, replace:
```python
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from nettriage.adapters.ai_analyses import current_explanation
from nettriage.adapters.triage_queue import TriageQueueError
from nettriage.application.rate_limits import POLICIES
from nettriage.entrypoints.api.access import OrgContext, OrgMember, enforce, unavailable
from nettriage.entrypoints.api.auditing import audit
from nettriage.entrypoints.api.finding_schemas import AiAnalysisOut, RerunOut
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.services import get_services
from nettriage.platform.trace_context import current_traceparent
```
with:
```python
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from nettriage.adapters.ai_analyses import current_explanation, rate_analysis
from nettriage.adapters.triage_queue import TriageQueueError
from nettriage.application.rate_limits import POLICIES
from nettriage.entrypoints.api.access import OrgContext, OrgMember, enforce, unavailable
from nettriage.entrypoints.api.auditing import audit
from nettriage.entrypoints.api.finding_schemas import AiAnalysisOut, FeedbackIn, RerunOut
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.services import get_services
from nettriage.platform.trace_context import current_traceparent
```

In `backend/src/nettriage/entrypoints/api/routes/ai.py`, replace:
```python
        RerunOut(status="queued", ai_analysis=None).model_dump(mode="json"), status_code=202
    )

```
with:
```python
        RerunOut(status="queued", ai_analysis=None).model_dump(mode="json"), status_code=202
    )


@router.put("/{analysis_id}/feedback")
def rate(
    request: Request,
    org_id: UUID,
    finding_id: UUID,
    analysis_id: UUID,
    body: FeedbackIn,
    org: Annotated[OrgContext, Depends(OrgMember("ai:feedback"))],
) -> AiAnalysisOut:
    """Rate one of the finding's explanations up or down; a new rating replaces the last. Only an
    explanation that succeeded can be rated (409 otherwise)."""
    with org_rules(request, "Rating this explanation", org=org, permission="ai:feedback"):
        rated = rate_analysis(
            get_services(request).database,
            org.org_id,
            org.user_id,
            finding_id,
            analysis_id,
            body.feedback,
        )
    return AiAnalysisOut.of(rated)

```

- [ ] **Step 5: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `1037 passed, 1 skipped`.

- [ ] **Step 6: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(api): rate an AI explanation up or down" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: The org's AI usage

**Files:**
- Create: `backend/src/nettriage/adapters/ai_usage.py`, `backend/src/nettriage/entrypoints/api/usage_schemas.py`, `backend/src/nettriage/entrypoints/api/routes/usage.py`
- Modify: `backend/src/nettriage/entrypoints/api/app.py`
- Test: `backend/tests/api/test_usage_routes.py`, `backend/tests/security/test_route_access.py`, `backend/tests/security/test_authorization_matrix.py`

**Interfaces:**
- Consumes: Task 1's `ai_usage` and `app_api`'s SELECT; Task 2's `record_usage` (the tests add usage with it); Plan 3c's `OrgMember` and `org_rules`.
- Produces:
  - `DayUsage(day, calls, input_tokens, output_tokens, cost_usd)` and `daily_usage(engine, org_id, user_id, days) -> list[DayUsage]` in `nettriage.adapters.ai_usage`, newest day first;
  - `UsageOut(days, totals)`, `DayUsageOut` and `UsageTotalsOut` in `nettriage.entrypoints.api.usage_schemas`;
  - `GET /api/v1/orgs/{org_id}/usage?days=1..90` (30 by default), with the `usage:read` permission.

- [ ] **Step 1: Write the failing tests**

`backend/tests/api/test_usage_routes.py`:
```python
"""An org's AI usage through the API (spec §7): calls, tokens and cost per UTC day, newest first,
for Owners and Admins. The triage worker counts every model call (Plan 5c)."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from browser import signed_in_as
from conftest import Database, FakeClock
from fastapi.testclient import TestClient
from sqlalchemy import text
from tenantdata import add_member, add_org, add_user

from nettriage.entrypoints.api.services import Services


@pytest.fixture
def org(database: Database) -> tuple[UUID, UUID]:
    """An org and its owner, with no AI use yet."""
    with database.admin.begin() as connection:
        owner = add_user(connection)
        org_id = add_org(connection, owner)
        add_member(connection, org_id, owner, "owner")
    return org_id, owner


@pytest.fixture
def signed_in(
    database_client: TestClient, services: Services, clock: FakeClock, org: tuple[UUID, UUID]
) -> TestClient:
    signed_in_as(database_client, services.sessions, org[1], clock())
    return database_client


def used(database: Database, org_id: UUID, days_ago: int, calls: int, cost: str) -> None:
    with database.admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ai_usage (org_id, day, calls, input_tokens, output_tokens, cost_usd) "
                "VALUES (:org, (now() AT TIME ZONE 'UTC')::date - :ago, :calls, :calls * 1800, "
                ":calls * 320, CAST(:cost AS numeric))"
            ),
            {"org": org_id, "ago": days_ago, "calls": calls, "cost": cost},
        )


def day(days_ago: int) -> str:
    return (datetime.now(UTC).date() - timedelta(days=days_ago)).isoformat()


def usage(client: TestClient, org: tuple[UUID, UUID], **params: Any) -> Any:
    return client.get(f"/api/v1/orgs/{org[0]}/usage", params=params)


def test_ai_spend_is_listed_by_day_newest_first_with_totals(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID]
) -> None:
    used(database, org[0], 3, calls=2, cost="0.000444")
    used(database, org[0], 0, calls=1, cost="0.000222")
    used(database, org[0], 40, calls=5, cost="0.001110")

    response = usage(signed_in, org)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "days": [
            {
                "day": day(0),
                "calls": 1,
                "input_tokens": 1800,
                "output_tokens": 320,
                "cost_usd": "0.000222",
            },
            {
                "day": day(3),
                "calls": 2,
                "input_tokens": 3600,
                "output_tokens": 640,
                "cost_usd": "0.000444",
            },
        ],
        "totals": {
            "calls": 3,
            "input_tokens": 5400,
            "output_tokens": 960,
            "cost_usd": "0.000666",
        },
    }


def test_days_narrows_the_window(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID]
) -> None:
    used(database, org[0], 0, calls=1, cost="0.000222")
    used(database, org[0], 1, calls=1, cost="0.000222")

    response = usage(signed_in, org, days=1)

    assert [entry["day"] for entry in response.json()["days"]] == [day(0)]


@pytest.mark.parametrize("days", [0, 91, "a week"])
def test_days_outside_1_to_90_is_a_422(
    signed_in: TestClient, org: tuple[UUID, UUID], days: Any
) -> None:
    assert usage(signed_in, org, days=days).status_code == 422


def test_an_org_without_ai_use_has_none(signed_in: TestClient, org: tuple[UUID, UUID]) -> None:
    assert usage(signed_in, org).json() == {
        "days": [],
        "totals": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": "0"},
    }


def test_another_orgs_usage_is_never_counted(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID]
) -> None:
    with database.admin.begin() as connection:
        stranger = add_user(connection)
        theirs = add_org(connection, stranger)
    used(database, theirs, 0, calls=9, cost="0.001998")

    assert usage(signed_in, org).json()["totals"]["calls"] == 0
```

In `backend/tests/security/test_route_access.py`, replace:
```python
    ): "ai:feedback",
    ("GET", "/api/v1/attack-techniques/{technique_id}"): "signed in",
```
with:
```python
    ): "ai:feedback",
    ("GET", "/api/v1/orgs/{org_id}/usage"): "usage:read",
    ("GET", "/api/v1/attack-techniques/{technique_id}"): "signed in",
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
    ),
}
```
with:
```python
    ),
    "read AI usage": ("GET", "/api/v1/orgs/{org}/usage", None),
}
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
    "rate AI explanation": (200, 200, 200, 403, 404, 401),
}
```
with:
```python
    "rate AI explanation": (200, 200, 200, 403, 404, 401),
    "read AI usage": (200, 200, 403, 403, 404, 401),
}
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/api/test_usage_routes.py tests/security/test_route_access.py tests/security/test_authorization_matrix.py`
Expected: `13 failed, 141 passed`: the seven new usage cases, the matrix's usage row for every caller but the outsider, and `test_every_route_declares_exactly_one_access_rule`, all because there's no route yet.

- [ ] **Step 3: Read the usage, and answer it**

`backend/src/nettriage/adapters/ai_usage.py`:
```python
"""An org's AI usage per UTC day (spec §5.2, §7), as `app_api` in the caller's org. The triage
worker counts every model call in `ai_usage` (Plan 5c)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Engine, text

from nettriage.adapters.postgres import tenant_transaction


@dataclass(frozen=True)
class DayUsage:
    day: date
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


def daily_usage(engine: Engine, org_id: UUID, user_id: UUID, days: int) -> list[DayUsage]:
    """The org's usage for the last `days` UTC days, today included, newest first. Days without
    a model call have no row."""
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        rows = connection.execute(
            text(
                "SELECT day, calls, input_tokens, output_tokens, cost_usd FROM ai_usage "
                "WHERE org_id = :org AND day > (now() AT TIME ZONE 'UTC')::date - :days "
                "ORDER BY day DESC"
            ),
            {"org": org_id, "days": days},
        ).all()
    return [DayUsage(**row._mapping) for row in rows]
```

`backend/src/nettriage/entrypoints/api/usage_schemas.py`:
```python
"""The org's AI usage as the API returns it (spec §7): per day, and in total."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from nettriage.adapters.ai_usage import DayUsage


class UsageTotalsOut(BaseModel):
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


class DayUsageOut(UsageTotalsOut):
    day: date


class UsageOut(BaseModel):
    """Newest day first. Days without a model call aren't listed."""

    days: list[DayUsageOut]
    totals: UsageTotalsOut

    @classmethod
    def of(cls, days: list[DayUsage]) -> UsageOut:
        return cls(
            days=[DayUsageOut(**vars(entry)) for entry in days],
            totals=UsageTotalsOut(
                calls=sum(entry.calls for entry in days),
                input_tokens=sum(entry.input_tokens for entry in days),
                output_tokens=sum(entry.output_tokens for entry in days),
                cost_usd=sum((entry.cost_usd for entry in days), Decimal(0)),
            ),
        )
```

`backend/src/nettriage/entrypoints/api/routes/usage.py`:
```python
"""An org's AI usage (spec §7): calls, tokens and cost per UTC day, for its Owners and Admins."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from nettriage.adapters.ai_usage import daily_usage
from nettriage.entrypoints.api.access import OrgContext, OrgMember
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.services import get_services
from nettriage.entrypoints.api.usage_schemas import UsageOut

router = APIRouter(prefix="/v1/orgs/{org_id}")


@router.get("/usage")
def usage(
    request: Request,
    org_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("usage:read"))],
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> UsageOut:
    """The last `days` UTC days of AI use (30 unless asked; at most 90), newest first."""
    with org_rules(request, "Reading the AI usage", org=org, permission="usage:read"):
        found = daily_usage(get_services(request).database, org.org_id, org.user_id, days)
    return UsageOut.of(found)
```

In `backend/src/nettriage/entrypoints/api/app.py`, replace:
```python
    uploads,
)
```
with:
```python
    uploads,
    usage,
)
```

In `backend/src/nettriage/entrypoints/api/app.py`, replace:
```python
        ai,
        attack_techniques,
```
with:
```python
        ai,
        usage,
        attack_techniques,
```

- [ ] **Step 4: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `1050 passed, 1 skipped`.

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(api): the org's AI calls, tokens and cost per day, for Owners and Admins" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The API's access to the triage queue

**Files:**
- Modify: `infra/modules/app/main.tf`, `infra/modules/app/variables.tf`, `infra/modules/pipeline/outputs.tf`, `infra/envs/dev/main.tf`
- Test: `infra/modules/app/tests/app.tftest.hcl`, `infra/modules/pipeline/tests/pipeline.tftest.hcl`

**Interfaces:**
- Consumes: Plan 5b's `aws_sqs_queue.triage` in the pipeline module and `var.bedrock_model_id` in `envs/dev`; the app module's `aws_iam_role.api` and `aws_lambda_function.api`.
- Produces:
  - the pipeline module's outputs `triage_queue_url` and `triage_queue_arn`;
  - the app module's variables `triage_queue_url`, `triage_queue_arn` and `bedrock_model_id`;
  - `aws_iam_role_policy.api_triage_queue` (`sqs:SendMessage` on the triage queue only);
  - the API function's `NETTRIAGE_TRIAGE_QUEUE_URL` and `NETTRIAGE_BEDROCK_MODEL_ID`, which Task 3's `wiring.py` reads as `settings.triage_queue_url` and `settings.bedrock_model_id`.

- [ ] **Step 1: Write the failing tests**

In `infra/modules/app/tests/app.tftest.hcl`, replace:
```hcl
  database_url_parameter    = "/nettriage/dev/db/app-api-url"
  uploads_bucket            = "nettriage-dev-uploads-12345678"
  uploads_bucket_arn        = "arn:aws:s3:::nettriage-dev-uploads-12345678"
  uploads_enabled_parameter = "/nettriage/dev/kill/uploads-enabled"
}

run "function_is_arm64_python_behind_iam_auth" {
  command = apply
```
with:
```hcl
  database_url_parameter    = "/nettriage/dev/db/app-api-url"
  uploads_bucket            = "nettriage-dev-uploads-12345678"
  uploads_bucket_arn        = "arn:aws:s3:::nettriage-dev-uploads-12345678"
  uploads_enabled_parameter = "/nettriage/dev/kill/uploads-enabled"
  triage_queue_url          = "https://sqs.eu-north-1.amazonaws.com/123456789012/nettriage-dev-triage"
  triage_queue_arn          = "arn:aws:sqs:eu-north-1:123456789012:nettriage-dev-triage"
  bedrock_model_id          = "openai.gpt-oss-20b-1:0"
}

run "function_is_arm64_python_behind_iam_auth" {
  command = apply
```

In `infra/modules/app/tests/app.tftest.hcl`, replace:
```hcl
    error_message = "The API is told the bucket and the kill switch's name, never values."
  }
}

```
with:
```hcl
    error_message = "The API is told the bucket and the kill switch's name, never values."
  }
}

run "the_api_may_queue_a_re_run_and_nothing_else" {
  command = apply

  assert {
    condition     = jsondecode(aws_iam_role_policy.api_triage_queue.policy).Statement[0].Action == "sqs:SendMessage" && jsondecode(aws_iam_role_policy.api_triage_queue.policy).Statement[0].Resource == "arn:aws:sqs:eu-north-1:123456789012:nettriage-dev-triage"
    error_message = "The API may only send to the triage queue, for re-runs (spec §7, Plan 5c)."
  }
  assert {
    condition     = aws_lambda_function.api.environment[0].variables["NETTRIAGE_TRIAGE_QUEUE_URL"] == "https://sqs.eu-north-1.amazonaws.com/123456789012/nettriage-dev-triage"
    error_message = "The API is told the triage queue's URL."
  }
  assert {
    condition     = aws_lambda_function.api.environment[0].variables["NETTRIAGE_BEDROCK_MODEL_ID"] == "openai.gpt-oss-20b-1:0"
    error_message = "The API knows the worker's model, so a re-run can find the current answer."
  }
}

```

In `infra/modules/pipeline/tests/pipeline.tftest.hcl`, replace:
```hcl
    error_message = "The switch's name is an output, for the deploy's pause-ai and resume-ai."
  }
}

```
with:
```hcl
    error_message = "The switch's name is an output, for the deploy's pause-ai and resume-ai."
  }
  assert {
    condition     = output.triage_queue_url == "https://sqs.eu-north-1.amazonaws.com/123456789012/nettriage-dev-triage" && output.triage_queue_arn == "arn:aws:sqs:eu-north-1:123456789012:nettriage-dev-triage"
    error_message = "The triage queue is an output, so the API can queue re-runs (Plan 5c)."
  }
}

```

- [ ] **Step 2: Run them and watch them fail**

Run: `just tf-check`
Expected: The bootstrap's tests pass, then the app module's new run fails and the check stops there. You see `A managed resource "aws_iam_role_policy" "api_triage_queue" has not been declared in the root module.` and `Invalid index` for `NETTRIAGE_TRIAGE_QUEUE_URL` and `NETTRIAGE_BEDROCK_MODEL_ID`, then `Failure! 7 passed, 1 failed.`

- [ ] **Step 3: Let the API send re-runs, and tell it the queue and the model**

In `infra/modules/app/main.tf`, replace:
```hcl

resource "aws_lambda_function" "api" {
```
with:
```hcl

# A re-run of a finding's AI explanation is a message to the triage worker (spec §7, Plan 5c).
resource "aws_iam_role_policy" "api_triage_queue" {
  name = "send-reruns-to-triage-queue"
  role = aws_iam_role.api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sqs:SendMessage"
      Resource = var.triage_queue_arn
    }]
  })
}

resource "aws_lambda_function" "api" {
```

In `infra/modules/app/main.tf`, replace:
```hcl
      NETTRIAGE_UPLOADS_ENABLED_PARAMETER = var.uploads_enabled_parameter
    }
```
with:
```hcl
      NETTRIAGE_UPLOADS_ENABLED_PARAMETER = var.uploads_enabled_parameter
      NETTRIAGE_TRIAGE_QUEUE_URL          = var.triage_queue_url
      NETTRIAGE_BEDROCK_MODEL_ID          = var.bedrock_model_id
    }
```

In `infra/modules/app/main.tf`, replace:
```hcl
    aws_iam_role_policy.api_uploads,
  ]
```
with:
```hcl
    aws_iam_role_policy.api_uploads,
    aws_iam_role_policy.api_triage_queue,
  ]
```

In `infra/modules/app/variables.tf`, replace:
```hcl
  description = "Name of the SSM parameter that switches uploads on and off (spec §9.7)."
}

```
with:
```hcl
  description = "Name of the SSM parameter that switches uploads on and off (spec §9.7)."
}

variable "triage_queue_url" {
  type        = string
  description = "The triage queue, where a re-run of a finding's AI explanation goes (Plan 5c)."
}

variable "triage_queue_arn" {
  type = string
}

variable "bedrock_model_id" {
  type        = string
  description = "The triage worker's model: an answer by it, for the current prompt and input, is the current one."
}

```

In `infra/modules/pipeline/outputs.tf`, replace:
```hcl
  value = aws_ssm_parameter.ai_enabled.name
}

```
with:
```hcl
  value = aws_ssm_parameter.ai_enabled.name
}

output "triage_queue_url" {
  value = aws_sqs_queue.triage.id
}

output "triage_queue_arn" {
  value = aws_sqs_queue.triage.arn
}

```

In `infra/envs/dev/main.tf`, replace:
```hcl
  uploads_enabled_parameter = module.pipeline.uploads_enabled_parameter
}
```
with:
```hcl
  uploads_enabled_parameter = module.pipeline.uploads_enabled_parameter
  triage_queue_url          = module.pipeline.triage_queue_url
  triage_queue_arn          = module.pipeline.triage_queue_arn
  bedrock_model_id          = var.bedrock_model_id
}
```

- [ ] **Step 4: Run the checks**

Run: `just tf-check && (cd infra/envs/dev && TF_DATA_DIR=.terraform-check terraform init -backend=false -input=false >/dev/null && TF_DATA_DIR=.terraform-check terraform validate)`
Expected: `Success! 8 passed, 0 failed.` for the app module and `Success! 14 passed, 0 failed.` for the pipeline (with the other modules' lines unchanged), then `Success! The configuration is valid.` for `envs/dev`.

- [ ] **Step 5: Commit**

```bash
git add infra
git commit -m "feat(infra): let the API queue a re-run on the triage queue, and tell it the worker's model" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: The decisions in the spec, and the owner's steps

**Files:**
- Modify: `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (§5.2, §5.4, §7, §9.4), `docs/runbooks/setup-and-deploy.md` (B10), `README.md`

- [ ] **Step 1: Amend the spec**

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `finding_evidence` | `id`, `org_id`, `finding_id`, `src_ip`, `dst_ip`, `src_port`, `dst_port`, `protocol`, `packets` bigint, `bytes` bigint, `start_ts`, `end_ts`, `action`, `line_no`; FK `(org_id, finding_id)` |
| `finding_techniques` | PK `(finding_id, technique_id, source)`; `org_id`; `technique_id` → attack_techniques; `source` CHECK in (detector, ai); `rationale`; FK `(org_id, finding_id)` → findings |
| `ai_analyses` | `id`, `org_id`, `finding_id`, `status` CHECK in (pending, succeeded, failed, skipped_budget, invalid_output), `provider`, `model_id`, `prompt_version`, `output_schema_version`, `input_hash`, `output` jsonb, `input_tokens`, `output_tokens`, `cost_usd` numeric(10,6), `latency_ms`, `error_code`, `feedback` CHECK in (up, down) or NULL, `feedback_by`; UNIQUE `(finding_id, model_id, prompt_version, input_hash)`; FK `(org_id, finding_id)` → findings. Only a succeeded row has an `output`. A new attempt for the same key updates the row, and a succeeded row is never overwritten: it is the cache (Plan 5a). `provider` is OpenTelemetry's `gen_ai.provider.name`, such as `aws.bedrock` (Plan 5b) |
| `finding_events` | `id`, `org_id`, `finding_id`, `actor_id` (NULL for system events), `type` CHECK in (created, status_changed, assigned, commented, ai_explained), `payload` jsonb. Comments are at most 2,000 characters |
```
with:
```markdown
| `finding_evidence` | `id`, `org_id`, `finding_id`, `src_ip`, `dst_ip`, `src_port`, `dst_port`, `protocol`, `packets` bigint, `bytes` bigint, `start_ts`, `end_ts`, `action`, `line_no`; FK `(org_id, finding_id)` |
| `finding_techniques` | PK `(finding_id, technique_id, source)`; `org_id`; `technique_id` → attack_techniques; `source` CHECK in (detector, ai); `rationale`; FK `(org_id, finding_id)` → findings. The AI's techniques are exactly the latest succeeded analysis's: a newer one replaces them, and never touches the detector's (the owner's decision, Plan 5c) |
| `ai_analyses` | `id`, `org_id`, `finding_id`, `status` CHECK in (pending, succeeded, failed, skipped_budget, invalid_output), `provider`, `model_id`, `prompt_version`, `output_schema_version`, `input_hash`, `output` jsonb, `input_tokens`, `output_tokens`, `cost_usd` numeric(10,6), `latency_ms`, `error_code`, `feedback` CHECK in (up, down) or NULL, `feedback_by`; UNIQUE `(finding_id, model_id, prompt_version, input_hash)`; FK `(org_id, finding_id)` → findings. Only a succeeded row has an `output`. A new attempt for the same key updates the row, and a succeeded row is never overwritten: it is the cache (Plan 5a). `provider` is OpenTelemetry's `gen_ai.provider.name`, such as `aws.bedrock` (Plan 5b). A rating (`feedback`) doesn't change `updated_at`, so it never changes which analysis is the latest (Plan 5c) |
| `ai_usage` | PK `(org_id, day)` (UTC day); `calls`, `input_tokens`, `output_tokens`, `cost_usd` numeric(12,6); FK `org_id` → organizations. The triage worker adds every model call, including retries and repairs whose analysis isn't stored (the owner's decision, Plan 5c) |
| `finding_events` | `id`, `org_id`, `finding_id`, `actor_id` (NULL for system events), `type` CHECK in (created, status_changed, assigned, commented, ai_explained), `payload` jsonb. Comments are at most 2,000 characters |
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `nettriage_owner` | Migrations (CI only) | Owns the schema; DDL |
| `app_api` | `api` Lambda | The SELECT/INSERT/UPDATE its endpoints need; INSERT only on `audit_log`; DELETE only on `memberships`, `invitations`, `organizations`. On findings it may UPDATE only `status`, `assignee_id` and `version`, and `finding_events` is insert-only (Plan 4c) |
| `app_analyze` | `analyze` Lambda | SELECT `uploads`, `detectors`, `attack_techniques`; UPDATE of `uploads` status and statistics columns only; INSERT `findings`, `finding_evidence`, `finding_techniques`, `finding_events`; SELECT of a finding's `id`, `org_id`, `upload_id` and `severity`, to queue the most severe for AI triage (Plan 5b). No `audit_log` until the worker records an event worth auditing (Plan 4b) |
| `app_triage` | `triage` Lambda | SELECT `findings`, `finding_evidence`, `finding_techniques`, `attack_techniques`, `detectors`, `ai_analyses`; INSERT/UPDATE `ai_analyses` (column grants: never `feedback`); INSERT `finding_techniques` and UPDATE of their `rationale`; INSERT `finding_events` without an actor; INSERT `audit_log`, for `budget.exhausted` (Plan 5b) |
| `app_ops` | `ops` Lambda | `SELECT 1` health checks; retention through `SECURITY DEFINER` functions only (purge `audit_log` rows older than 180 days, expire invitations, expire stale pending uploads) |
```
with:
```markdown
| `nettriage_owner` | Migrations (CI only) | Owns the schema; DDL |
| `app_api` | `api` Lambda | The SELECT/INSERT/UPDATE its endpoints need; INSERT only on `audit_log`; DELETE only on `memberships`, `invitations`, `organizations`. On findings it may UPDATE only `status`, `assignee_id` and `version`, and `finding_events` is insert-only (Plan 4c). On `ai_analyses` it may UPDATE only `feedback` and `feedback_by`, and it reads `ai_usage` (Plan 5c) |
| `app_analyze` | `analyze` Lambda | SELECT `uploads`, `detectors`, `attack_techniques`; UPDATE of `uploads` status and statistics columns only; INSERT `findings`, `finding_evidence`, `finding_techniques`, `finding_events`; SELECT of a finding's `id`, `org_id`, `upload_id` and `severity`, to queue the most severe for AI triage (Plan 5b). No `audit_log` until the worker records an event worth auditing (Plan 4b) |
| `app_triage` | `triage` Lambda | SELECT `findings`, `finding_evidence`, `finding_techniques`, `attack_techniques`, `detectors`, `ai_analyses`; INSERT/UPDATE `ai_analyses` (column grants: never `feedback`); INSERT `finding_techniques`, UPDATE of their `rationale`, and DELETE of the AI's own (`source = 'ai'`, a restrictive row policy); INSERT `finding_events` without an actor; INSERT `audit_log`, for `budget.exhausted` (Plan 5b); INSERT and UPDATE of `ai_usage`'s counts (Plan 5c) |
| `app_ops` | `ops` Lambda | `SELECT 1` health checks; retention through `SECURITY DEFINER` functions only (purge `audit_log` rows older than 180 days, expire invitations, expire stale pending uploads) |
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `POST /api/v1/orgs/{org}/findings/{id}/comments` | `findings:comment` | Body: `text`, 1 to 2,000 characters, line breaks allowed. A comment joins the history without changing the finding's version; the audit event never holds its text |
| `POST /api/v1/orgs/{org}/findings/{id}/ai-analyses` | `ai:request` | Re-run, subject to budget and rate limits |
| `PUT /api/v1/orgs/{org}/findings/{id}/ai-analyses/{aid}/feedback` | `ai:feedback` | `up` or `down` |
| `GET /api/v1/orgs/{org}/audit-log` | `audit:read` | |
| `GET /api/v1/orgs/{org}/usage` | `usage:read` | AI tokens and cost by day |
| `GET /api/v1/attack-techniques/{id}` | session | Reference data, with MITRE's notice; 404 for an unknown or malformed ID |
```
with:
```markdown
| `POST /api/v1/orgs/{org}/findings/{id}/comments` | `findings:comment` | Body: `text`, 1 to 2,000 characters, line breaks allowed. A comment joins the history without changing the finding's version; the audit event never holds its text |
| `POST /api/v1/orgs/{org}/findings/{id}/ai-analyses` | `ai:request` | Re-run, subject to budget and rate limits (`ai.rerun.user`). `202 {"status": "queued"}`: the finding goes to the triage worker. `200 {"status": "explained", "ai_analysis": …}`: its latest analysis for the current model, prompt and input already succeeded, so that answer is returned and nothing is spent (the owner's decision, Plan 5c). 503 if the queue can't be reached |
| `PUT /api/v1/orgs/{org}/findings/{id}/ai-analyses/{aid}/feedback` | `ai:feedback` | `{"feedback": "up"}` or `"down"`; a new rating replaces the last. Only a succeeded explanation can be rated (409 otherwise). Returns the analysis, with `feedback` and `feedback_by` (Plan 5c) |
| `GET /api/v1/orgs/{org}/audit-log` | `audit:read` | |
| `GET /api/v1/orgs/{org}/usage` | `usage:read` | AI tokens and cost by day: `?days=` 1 to 90 (30 by default), UTC days newest first, each with `calls`, `input_tokens`, `output_tokens` and `cost_usd`, and their `totals`. Days without a call aren't listed (Plan 5c) |
| `GET /api/v1/attack-techniques/{id}` | session | Reference data, with MITRE's notice; 404 for an unknown or malformed ID |
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown

`auth.session_created`, `auth.logout`, `auth.logout_all`, `org.created`, `org.renamed`, `org.deleted`, `member.invited`, `member.joined`, `member.role_changed`, `member.removed`, `member.left`, `invitation.revoked`, `upload.created`, `finding.status_changed`, `finding.assigned`, `finding.commented`, `ai.rerun_requested`, `authz.denied` (sampled: at most one per caller and permission per minute), `budget.exhausted`, and `ratelimit.limited` (sampled: at most one per subject and policy per minute).

```
with:
```markdown

`auth.session_created`, `auth.logout`, `auth.logout_all`, `org.created`, `org.renamed`, `org.deleted`, `member.invited`, `member.joined`, `member.role_changed`, `member.removed`, `member.left`, `invitation.revoked`, `upload.created`, `finding.status_changed`, `finding.assigned`, `finding.commented`, `ai.rerun_requested` (when a re-run is queued; Plan 5c), `authz.denied` (sampled: at most one per caller and permission per minute), `budget.exhausted`, and `ratelimit.limited` (sampled: at most one per subject and policy per minute).

```

- [ ] **Step 2: Add the owner's steps to the runbook**

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown

## Part C: when things go wrong
```
with:
````markdown

### B10. Try a re-run, a rating and the usage
1. Do B7 steps 1 to 5 (sign in, create the "Analysis Test" org, upload the port scan, and wait
   until it says `analyzed`), then read the finding as in B9 step 2:
   ```js
   const list = await api("GET", "/orgs/" + org.id + "/findings");
   const f = "/orgs/" + org.id + "/findings/" + list.findings[0].id;
   (await api("GET", f)).ai_analysis;
   ```
2. Ask for the explanation again:
   ```js
   await api("POST", f + "/ai-analyses");
   ```
   - If the analysis in step 1 had `status: "succeeded"`: `200` with `status: "explained"` and
     the same answer. Nothing is spent.
   - Otherwise (for example `failed` while AWS still limits Bedrock): `202` with
     `status: "queued"`. Read the finding again after a minute: the triage worker has tried again.
3. If it succeeded, rate it:
   ```js
   await api("PUT", f + "/ai-analyses/" + (await api("GET", f)).ai_analysis.id + "/feedback", { feedback: "up" });
   ```
   `200`, with `feedback: "up"` and your user ID in `feedback_by`. An analysis that didn't
   succeed answers `409`.
4. Read the org's AI usage:
   ```js
   await api("GET", "/orgs/" + org.id + "/usage");
   ```
   `200`, with today in `days` (its `calls`, tokens and `cost_usd`) and the `totals`, once Bedrock
   has answered at least once. Before that, `days` is empty and the totals are 0.
5. Read the audit log as in B8 step 9: a queued re-run is an `ai.rerun_requested` event.
6. Delete the test org as in B7 step 10.

## Part C: when things go wrong
````

- [ ] **Step 3: Extend the highlight**

In `README.md`, replace:
```markdown
- **Triage with optimistic concurrency**: a finding's status and assignee change only with `If-Match` naming the version the caller read, so two analysts can't silently overwrite each other (412 instead); every change and comment is in the finding's history and the audit log.
- **AI explanations with guardrails**: each upload's 20 most severe findings are explained by gpt-oss-20b on Amazon Bedrock from the finding's typed fields only. An answer that names an address, port or technique outside the data is refused, and every call is paid for in advance from a daily token budget per org and a $0.50 daily cap across all orgs, which fail closed.
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for each function.
```
with:
```markdown
- **Triage with optimistic concurrency**: a finding's status and assignee change only with `If-Match` naming the version the caller read, so two analysts can't silently overwrite each other (412 instead); every change and comment is in the finding's history and the audit log.
- **AI explanations with guardrails**: each upload's 20 most severe findings are explained by gpt-oss-20b on Amazon Bedrock from the finding's typed fields only. An answer that names an address, port or technique outside the data is refused, and every call is paid for in advance from a daily token budget per org and a $0.50 daily cap across all orgs, which fail closed. Members can re-run an explanation and rate it, and Owners and Admins see the AI's calls, tokens and cost per day.
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for each function.
```

- [ ] **Step 4: Check and commit**

Run: `git diff --check`
Expected: no output.

```bash
git add docs README.md
git commit -m "docs: the AI API in the spec, and runbook B10" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8 (Claude, then the owner): Pull request, deploy and a first re-run

- [ ] **Step 1 (Claude):**
  - Run `just lint test tools-test edge-test web-check tf-check pin-check cloud-check detection-report build-lambda`; everything must pass.
  - Get a final review of the whole branch, fix what it finds, then push `plan-5c/ai-api`.
  - Open the PR, watch CI, and request a Copilot review.
- [ ] **Step 2 (owner):** Review the PR, then squash-merge it.
- [ ] **Step 3 (owner):** Runbook B2 (`just deploy-dev`). Expected:
  - `Database migrated.` (migration `0010`; no new logins), then the reference data line as before;
  - `Plan: 1 to add, 3 to change, 0 to destroy`:
    - to add: the API role's policy to send to the triage queue;
    - to change: the `api` function (its two new settings) and the `analyze` and `triage` functions (their new code);
  - fifteen smoke `PASS` lines.
- [ ] **Step 4 (owner):** Runbook B10. Expected:
  - while AWS still limits Bedrock: the re-run answers `202 queued`, and the usage answers `200` with no days;
  - once the limits are lifted: the re-run of an explained finding answers `200 explained`, a rating answers `200` with `feedback: "up"`, and the usage lists today's calls and cost.

## Plan 5c is done when

- [ ] `just lint test tools-test tf-check` passes locally and CI passes.
- [ ] The PR is merged through review, with every thread resolved.
- [ ] Runbook B10 shows a queued re-run, and the usage endpoint answers, on dev.

## Spec coverage of this plan

| Spec | Covered here |
|---|---|
| §2.2 re-run and thumbs-up/down feedback | Tasks 3 and 4 |
| §5.2 `ai_analyses.feedback`, `feedback_by` | Tasks 1 and 4; Task 7 amends §5.2 (a rating doesn't change `updated_at`) |
| §5.2 AI usage | Tasks 1 and 2; Task 7 adds the `ai_usage` row to §5.2 |
| §5.2 `finding_techniques` from the AI | Tasks 1 and 2; Task 7 amends §5.2 (the latest success replaces them) |
| §5.4 `app_api` and `app_triage` grants | Task 1; Task 7 amends §5.4 |
| §6.4 `ai:request`, `ai:feedback`, `usage:read` | Tasks 3, 4 and 5 (the authorization matrix) |
| §6.5 `ai.rerun.user` | Task 3 |
| §7 `POST …/ai-analyses`, `PUT …/feedback`, `GET …/usage` | Tasks 3, 4 and 5; Task 7 amends §7 (the answers) |
| §8.3 model selection by evals; §8.5 evals | Plan 5d |
| §9.4 `ai.rerun_requested` | Task 3; Task 7 amends §9.4 (only when queued) |
| §9.5, §9.6 the AI dashboard, the AI coverage SLO and the triage DLQ alarm | Plan 7 |
| The pages for re-run, feedback and usage | Plan 6 |
