# NetTriage Plan 5b: AI Triage Live on Bedrock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run Plan 5a's AI engine live on dev:
- a Bedrock provider that calls OpenAI gpt-oss-20b on demand in Stockholm, with the answer constrained to output schema v1;
- the analyze worker queues each analyzed upload's 20 most severe findings, and a new `triage` worker (SQS → Lambda) explains them one at a time;
- the `ai_enabled` kill switch, Bedrock outages retried through SQS, and `budget.exhausted` audited once a day;
- a finding's details carry its latest AI analysis;
- the infrastructure: the triage queue, worker and switch, and in the bootstrap a budget that counts usage before credits and a $5 action that denies Bedrock to the triage worker;
- the deploy's side: `app_triage`'s login, a preflight check of the model, and `just pause-ai` / `just resume-ai`.

Plan 5c adds the API endpoints (re-run, feedback, usage) and the evals that choose the model.

**Architecture:**
- After storing an upload's findings, the analyze worker sends the 20 most severe to the `triage` queue, one message per finding, with its trace as a message attribute.
- A delivery that finds the upload already analyzed sends them again, in case a crash or a failed send lost them. The explanations are cached, so a repeat costs nothing.
- The triage worker takes one message per run.
- Plan 5a's `Explainer` checks the cache, then the kill switch, reserves the budget and calls Bedrock's Converse API with structured output.
- A passing Bedrock failure (throttled, timed out, unavailable) hands the message back to SQS until its last delivery, which stores `failed`.
- The handler never raises. It answers with a partial batch response, so Lambda never logs an error's message.
- A finding's `GET` returns its latest analysis.
- AWS Budgets denies `bedrock:InvokeModel*` to the triage role at $5 of usage in a month. The DynamoDB budgets stay the real-time limit.

**Tech Stack:** Python 3.14, boto3 (Bedrock Runtime Converse, SQS, DynamoDB, SSM), Pydantic 2, SQLAlchemy 2 Core with psycopg 3 (Neon Postgres, row-level security), OpenTelemetry (GenAI semantic conventions), Terraform (AWS provider: SQS, Lambda, IAM, SSM, Budgets actions) · botocore's Stubber and moto in tests, `terraform test` with a mocked provider.

**Spec:** `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (revision 2). Read it alongside this plan; section numbers (§) refer to it. This plan implements:
- §3.2's triage worker and Bedrock, and §3.5's `triage` function (with the owner's sizing);
- §4.2's analyze → triage queue → triage hop, and §5.7's automatic triage of up to 20 findings per upload;
- §5.4's `budget.exhausted` grant for `app_triage`, and the analyze worker's read of finding severity;
- §6.7's $5 Budgets action;
- §7's latest AI analysis on `GET …/findings/{id}`;
- §8.3's `BedrockProvider` (structured output, the 20-second timeout, retries with backoff and full jitter, model IDs and prices);
- §8.6's Bedrock failure row, plus the AI kill switch;
- §9.1's worker telemetry (`service.name` `nettriage-triage`, the `triage.explain` span linked to the upload, GenAI conventions);
- §9.2's queue age for the triage queue, §9.4's `budget.exhausted`, and §9.7's `ai_enabled` kill switch.

**Plan series:** Plan 5 of 7 ("AI triage") is split in three, as the owner chose on 2026-10-01:
- 5a: the AI engine, offline (merged as PR #12, `885dfbd`; not deployed);
- **5b (this plan): live on Bedrock;**
- 5c: the API and evals (re-run, feedback and usage endpoints; the ~30-finding eval set with its gates; model comparison, nightly evals, the quality baseline).

**Branch:** `plan-5b/ai-live`, from `main` at `885dfbd` or later.

## Global Constraints

- **Stack.** Python **3.14**. The only new packages are type stubs and a moto extra, both for development: `types-boto3[bedrock-runtime,…,sqs,…]` and `moto[…,sqs,…]`. mypy `--strict` and Ruff pass on `src` and `tests`. Work test-first.
- **Commands on this Windows machine.** Run Python tools as modules (`uv run python -m …`). Host policy blocks some uv launchers and every unsigned executable outside trusted tools. Backend tests need the local Postgres, which `just test` starts.
- **The live model (the owner's decision).** OpenAI gpt-oss-20b, `openai.gpt-oss-20b-1:0`, on demand in **eu-north-1**, at **$0.07 / $0.30** per million input / output tokens. No cross-Region inference profiles (Revision 2, R4).
- **The triage worker (the owner's decision).** **512 MB**, **240 s**, SQS batch size **1**, partial batch responses, event source mapping maximum concurrency **2**. The SQS visibility timeout is **6 ×** the function timeout; `maxReceiveCount` **3**, then a dead-letter queue kept **14 days**.
- **Call settings (§8.3).** Temperature **0.1**, `max_tokens` **700**, a **20-second** timeout, and up to **3** retries on throttling and server errors, with exponential backoff and full jitter.
- **Automatic triage (§5.7).** Up to **20** findings per upload, highest severity first.
- **Budgets (§6.6, §6.7).** Unchanged: **100,000** tokens per org per day and **$0.50** a day across all orgs, failing closed. At **$5** of usage in a month, a Budgets action denies `bedrock:InvokeModel*` to the triage role.
- **Kill switch (§9.7).** `/nettriage/<stage>/kill/ai-enabled`: `true` is on, anything else is off. It is re-read every **60 seconds**, an unreadable switch counts as off until it has been read once, and Terraform only creates it.
- **Logs (§9.3).** Never prompts, the finding's data or the model's answers. Errors are logged and traced by their type or code only.
- **Infrastructure.** Terraform only, reviewed in the PR, applied by the owner's deploy. Regional resources live in eu-north-1. CI holds no cloud access.
- **Owner-only commands.** Claude never runs `aws login`, `just bootstrap`, `just store-*`, `just pause-*`, `just resume-*` or `just deploy-*`.
- **Commits.** Use Conventional Commits. Every commit made by Claude ends with the trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Decisions this plan makes

1. **The live model is gpt-oss-20b in Stockholm** (the owner's decision, 2026-10-01). Plan 5c's evals may replace it. AWS's documentation and price list, checked on 2026-10-01:
   - gpt-oss-20b runs on demand in eu-north-1, us-east-1 and us-west-2, supports structured output, and isn't sold through AWS Marketplace, so the worker's role needs only `bedrock:InvokeModel`;
   - Ministral 3 8B runs on demand on Converse only in us-east-1 and us-west-2 ($0.15 / $0.15);
   - Claude Haiku 4.5 has no on-demand model ID for Converse in any Region, only cross-Region inference profiles, which the account can't use. It is dropped as a candidate; Plan 5c picks the quality baseline;
   - AWS retired the model-access page: models are enabled on first use, and no Anthropic form is needed.
2. **The Bedrock provider** (`adapters/bedrock_llm.py`):
   - **The request:** Converse with `outputConfig.textFormat` of type `json_schema`.
   - **The schema:** Bedrock enforces only part of JSON Schema: no string lengths, patterns or array maximums, and `minItems` only as 0 or 1. It gets the schema reduced to the keywords it supports, and the Pydantic checks stay the gate.
   - **Reasoning:** gpt-oss reasons before it answers, and the reasoning counts toward `max_tokens`, so `reasoning_effort` is `low`.
   - **The answer:** only the `text` content blocks; `reasoningContent` is skipped. Text that isn't JSON is no output, so the checks ask for the repair.
   - **The client:** a 5-second connect timeout, a 20-second read timeout, and botocore's standard retry mode with 4 attempts in all (3 retries, exponential backoff with full jitter).
   - **Error codes:**
     - `ThrottlingException` → `provider_throttled`;
     - `ModelTimeoutException` and client timeouts → `provider_timeout`;
     - server errors, `ModelNotReady`, `ModelError` and lost connections → `provider_unavailable`;
     - `AccessDeniedException` → `provider_denied`;
     - `ValidationException` and `ResourceNotFoundException` → `provider_rejected`;
     - anything else → `provider_error`.
   - **Passing codes:** the first three pass (`ProviderError.transient`).
   - **The provider's name** is OpenTelemetry's `gen_ai.provider.name`, `aws.bedrock`, both in traces and in `ai_analyses.provider`. Migration 0009 lets that column hold a dot.
3. **The triage worker is sized for its worst case** (the owner's decision): one finding per run and a 240-second timeout. An answer and its repair, each a 20-second call with 3 retries, fit. The spec's batch of 5 and 60 seconds couldn't fit even one slow call.
4. **Passing Bedrock failures are retried through SQS.**
   - Before a message's last (third) delivery, `explain(…, last_delivery=False)` releases the reservation, stores nothing and raises `ExplainLater`. The worker hands the message back, and SQS delivers it again after the visibility timeout (24 minutes).
   - The last delivery stores `failed` with the code.
   - Lasting failures (`provider_denied`, `provider_rejected`, `provider_error`) are stored at once.
   - The first use of a schema each day can take Bedrock minutes, so this also covers a first call that times out.
5. **The kill switch is checked after the cache and before the budget.**
   - A cached answer is still served.
   - Otherwise, while the switch is off, the analysis is `skipped_budget` with `error_code` `ai_disabled`. This reuses the existing statuses, so no CHECK changes.
   - The analyze worker keeps queueing while the AI is off.
6. **Database retries happen inside the `Explainer`.** Reading the finding and the cache, and storing the analysis, are each retried after 1 and 3 seconds on `OperationalError`, as the analyze worker does. A stored answer is never paid for twice, and a longer outage hands the message back.
7. **`budget.exhausted` is audited once a day per org and budget.**
   - The first refusal of a UTC day sets `refused_org` or `refused_global` on the org's `BUDGET#` item, with a conditional update. Only that refusal writes the audit event: actor `system`, outcome `denied`, target the finding, details `{"scope": …}`.
   - If DynamoDB can't note it, nothing is audited, so a failure can't flood the log.
   - `app_triage` gets INSERT on `audit_log` (migration 0009); row-level security keeps it in the finding's org.
8. **Automatic triage.**
   - `findings_to_explain` returns an analyzed upload's findings, most severe first and in the analysis's order within a severity, up to 20.
   - `app_analyze` gets SELECT on a finding's `id`, `org_id`, `upload_id` and `severity` only (migration 0009).
   - The analyze worker queues them after storing the findings, and again on any delivery that finds the upload already analyzed. A failed send fails the analyze message, and its next delivery queues them.
   - Messages go out ten at a time, carrying the `analyze.upload` span's `traceparent`.
   - The `pending` status stays unused; Plan 5c's re-run may use it.
9. **The triage worker** (`entrypoints/triage/worker.py`, `handler.py`, `wiring.py`):
   - A malformed message, or one whose finding is gone (its org was deleted), is let go.
   - Anything unexpected is logged and traced by its type and handed back.
   - Its `triage.explain` span links to the upload's trace, with `triage.generate` inside it. It records `nettriage.queue.message.age` for the `triage` queue.
10. **A finding's details carry its latest AI analysis** (the owner's decision; this moves it from Plan 5c).
    - `ai_analysis` is the finding's most recently updated row, or `null`.
    - It carries the status, provider, model, prompt and output schema versions, the output, `error_code`, tokens, `cost_usd` and latency.
    - The UI labels it AI-generated in Plan 6.
11. **The triage infrastructure** (`infra/modules/pipeline/triage.tf`):
    - The role may invoke one model ARN, update only `BUDGET#` and `GBUDGET#` items in the runtime table (`dynamodb:LeadingKeys`), read its database URL and the switch, and consume its queue.
    - The analyze role may send to the triage queue.
    - The function sets `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` and `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false`.
12. **Two fixes in the bootstrap's budget** (`infra/bootstrap`). Plan 1 left the budget's credit setting at its default, which counts credits. On the Free plan, usage is paid from credits, so the budget nets to $0, and the $1 and $3 alerts could never fire.
    - **The budget counts usage before credits** (`include_credit = false`), as spec §6.7 means: "the budget alerts above still report usage against the credits".
    - **The $5 action lives in the bootstrap,** next to the budget and the owner's email. It names the triage roles, so it needs them to exist: the owner re-runs `just bootstrap` once after this plan's deploy (runbook A8).
    - **The action lags:** Budgets refreshes up to three times a day, so it can act hours after the spending. The DynamoDB budgets remain the real-time limit.
13. **The deploy:**
    - `APP_DB_ROLES` adds `app_triage`, so the deploy prints `New logins: app_triage.`;
    - `terraform.tfvars` names the model and its Region, and `just preflight` checks that Bedrock offers it there on demand and active;
    - `just pause-ai` and `just resume-ai` flip the switch;
    - the Lambda package must contain the triage handler and prompt v1.

## Review Focus

1. **Bedrock's real answers.** The tests stub Converse, so a schema keyword Bedrock refuses, or an answer in an unexpected content block, would turn every finding into `provider_rejected` or `invalid_output`. The request follows AWS's documented shape, pinned by Task 2 `test_a_call_sends_the_prompt_the_data_and_the_schema_bedrock_supports` and `test_the_schema_bedrock_gets_keeps_only_the_keywords_it_supports`. The live check is runbook B9, whose step 2 expects `succeeded`.
2. **gpt-oss's reasoning eats the 700 tokens.** The JSON would be cut off and the finding would end as `invalid_output`. Task 2's request test pins `reasoning_effort: low`, and `test_an_answer_that_isnt_json_has_no_output` covers a cut-off answer. B9 shows the real `output_tokens` and `finish_reasons`.
3. **A Bedrock outage, or a first call that times out while Bedrock prepares the schema.** The finding must be tried again, not stored as failed at once and not lost. Tests:
   - Task 3: `test_a_passing_provider_failure_is_tried_again_before_the_last_delivery` and `test_a_passing_failure_on_the_last_delivery_is_stored`;
   - Task 4: `test_a_passing_bedrock_failure_is_handed_back_to_sqs` and `test_on_the_last_delivery_a_passing_failure_is_stored`.
4. **An error's message reaching Lambda's logs.** A database or Bedrock error can quote the finding. The handler never raises, and failures are logged by type. Tests: Task 4 `test_an_unexpected_error_is_handed_back_and_logged_by_type_only` and `test_the_handler_reports_the_messages_to_deliver_again`.
5. **The $5 action.** It must deny only Bedrock, only to the triage workers, and it must be able to fire on the Free plan. Tests: Task 7 `the_budget_counts_usage_before_credits`, `at_five_dollars_bedrock_is_denied_to_the_triage_workers` and `budgets_may_attach_only_the_deny_policy_to_only_the_triage_workers`.

## Owner prerequisites

- **Before the deploy, check that your account can call gpt-oss-20b in Stockholm** (5 minutes, a fraction of a cent):
  1. Open the Bedrock console in Europe (Stockholm).
  2. Open **Playgrounds → Chat / Text** and select **OpenAI → gpt-oss-20b**.
  3. Send a short prompt and expect a reply.
  If you get an error, send its exact text to Claude.
- **If the playground says `Too many tokens per day`,** AWS's starting limits for new accounts still refuse every call (seen on 2026-10-02). Service Quotas can't fix it: the account-wide daily quota was 150,000,000, and gpt-oss-20b had no quota of its own listed. Open an Account and billing case asking AWS to verify the account and lift the initial Bedrock limits, as in the runbook's Part C, "Bedrock refuses every call". Building, reviewing and merging this plan don't wait for it; only B9's live explanation does.
- **Nothing else is needed:** no Anthropic form, no Marketplace subscription, no model-access page.
- **After the merge:** runbook B2 (the deploy), then A8 (the bootstrap again, for the $5 cutoff), then B9 (try an explanation).

## File map

| File | Responsibility | Task |
|---|---|---|
| `migrations/versions/0009_live_triage.py` | `app_analyze` reads finding severity, `app_triage` audits, providers named with a dot | 1 |
| `src/nettriage/adapters/bedrock_llm.py`, `application/llm.py` | the Bedrock provider, error codes, gpt-oss-20b's price | 2 |
| `src/nettriage/entrypoints/triage/explainer.py`, `adapters/ai_budget.py` | kill switch, passing failures, database retries, `budget.exhausted` | 3 |
| `src/nettriage/entrypoints/triage/worker.py`, `handler.py`, `wiring.py` | the triage Lambda | 4 |
| `src/nettriage/adapters/triage_queue.py`, `adapters/analysis_store.py`, `entrypoints/analyze/` | queueing each upload's most severe findings | 5 |
| `src/nettriage/adapters/findings.py`, `entrypoints/api/finding_schemas.py` | the latest AI analysis in a finding's details | 6 |
| `infra/modules/pipeline/triage.tf`, `infra/bootstrap/bedrock_cutoff.tf` | the triage queue, worker and switch; the $5 action | 7 |
| `tools/deploy/`, `tools/build_lambda.py`, `justfile`, `infra/envs/dev/` | login, preflight, pause and resume, the package | 8 |
| `docs/…/spec`, `docs/runbooks/setup-and-deploy.md`, `CLAUDE.md`, `README.md` | the decisions, and the owner's steps | 9 |

Paths under `src/` and `migrations/` are in `backend/`.

---

### Task 1: Database grants for the live workers

**Files:**
- Create: `backend/migrations/versions/0009_live_triage.py`
- Test: `backend/tests/integration/test_findings_schema.py`, `backend/tests/integration/test_ai_schema.py`

**Interfaces:**
- Consumes: Plan 5a's `ai_analyses` and `app_triage` (migration 0008), Plan 4b's `app_analyze` (migration 0006), Plan 3a's `audit_log.record(engine, AuditEvent)`, and the harness's `database` fixture with `app_analyze` and `app_triage` engines.
- Produces:
  - `app_analyze` may `SELECT (id, org_id, upload_id, severity) ON findings`;
  - `app_triage` may `INSERT` an `audit_log` row (the same columns as `app_api`), in the finding's org only;
  - `ai_analyses.provider` matches `^[a-z][a-z0-9_.-]{0,29}$`, so `aws.bedrock` fits.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/integration/test_findings_schema.py`, replace:
```python

@pytest.mark.parametrize(
```
with:
```python

def test_the_worker_can_rank_the_findings_of_its_org(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    with tenant_transaction(database.app_analyze, org_id=mine.org_id) as connection:
        seen = connection.execute(
            text("SELECT id, org_id, upload_id, severity FROM findings")
        ).all()

    assert [(row.id, row.org_id, row.upload_id) for row in seen] == [
        (mine.finding_id, mine.org_id, mine.upload_id)
    ]
    assert theirs.finding_id not in {row.id for row in seen}


@pytest.mark.parametrize(
```

In `backend/tests/integration/test_findings_schema.py`, replace:
```python
        "SELECT * FROM findings",
        "SELECT * FROM memberships",
```
with:
```python
        "SELECT * FROM findings",
        "SELECT title FROM findings",
        "SELECT metrics FROM findings",
        "SELECT * FROM finding_evidence",
        "SELECT * FROM memberships",
```

In `backend/tests/integration/test_ai_schema.py`, replace:
```python
from tenantdata import add_analysis, add_finding, add_tenant

from nettriage.adapters.postgres import tenant_transaction


```
with:
```python
from tenantdata import add_analysis, add_finding, add_tenant

from nettriage.adapters.audit_log import record
from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.audit import AuditEvent


```

In `backend/tests/integration/test_ai_schema.py`, replace:
```python
    with pytest.raises(IntegrityError, match="ai_analyses_finding_id_model_id"):
        run_as_triage(database, tenant.org_id, INSERT_ANALYSIS, id=uuid7(), **params)


```
with:
```python
    with pytest.raises(IntegrityError, match="ai_analyses_finding_id_model_id"):
        run_as_triage(database, tenant.org_id, INSERT_ANALYSIS, id=uuid7(), **params)


@pytest.mark.parametrize(("provider", "stored"), [("aws.bedrock", 1), ("AWS Bedrock", 0)])
def test_a_provider_is_named_as_opentelemetry_names_it(
    database: Database, provider: str, stored: int
) -> None:
    tenant = add_tenant(database.admin)
    statement = INSERT_ANALYSIS.replace("'fake'", ":provider")
    params = {"org": tenant.org_id, "finding": tenant.finding_id, "hash": "c" * 64}

    if stored:
        assert (
            run_as_triage(
                database, tenant.org_id, statement, provider=provider, id=uuid7(), **params
            )
            == 1
        )
    else:
        with pytest.raises(IntegrityError, match="ai_analyses_provider_check"):
            run_as_triage(
                database, tenant.org_id, statement, provider=provider, id=uuid7(), **params
            )


```

In `backend/tests/integration/test_ai_schema.py`, replace:
```python


@pytest.mark.parametrize(
    "statement",
```
with:
```python


def test_the_worker_can_audit_in_the_findings_org(database: Database) -> None:
    tenant = add_tenant(database.admin)

    record(
        database.app_triage,
        AuditEvent(
            action="budget.exhausted",
            outcome="denied",
            actor_type="system",
            org_id=tenant.org_id,
            details={"scope": "org"},
        ),
    )

    with database.admin.connect() as connection:
        audited = connection.execute(
            text("SELECT action, details FROM audit_log WHERE org_id = :org"),
            {"org": tenant.org_id},
        ).all()
    assert [tuple(row) for row in audited] == [("budget.exhausted", {"scope": "org"})]


def test_the_worker_can_not_audit_into_another_org(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    with pytest.raises(ProgrammingError, match="row-level security"):
        run_as_triage(
            database,
            mine.org_id,
            "INSERT INTO audit_log (id, org_id, actor_type, action, outcome) "
            "VALUES (:id, :org, 'system', 'budget.exhausted', 'denied')",
            id=uuid7(),
            org=theirs.org_id,
        )


@pytest.mark.parametrize(
    "statement",
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/integration/test_findings_schema.py tests/integration/test_ai_schema.py`
Expected: `4 failed, 44 passed`.
- `test_the_worker_can_rank_the_findings_of_its_org`: `permission denied for table findings`.
- `test_a_provider_is_named_as_opentelemetry_names_it[aws.bedrock-1]`: `violates check constraint "ai_analyses_provider_check"`.
- `test_the_worker_can_audit_in_the_findings_org` and `test_the_worker_can_not_audit_into_another_org`: `permission denied for table audit_log`.

- [ ] **Step 3: Write the migration**

`backend/migrations/versions/0009_live_triage.py`:
```python
"""Live AI triage (spec §5.4, §9.4; Plan 5b).

- `app_analyze` may read a finding's ID, org, upload and severity, so it can queue each upload's
  most severe findings for an AI explanation, again when SQS delivers a message twice.
- `app_triage` may append to the audit log in the finding's org: the worker records
  `budget.exhausted` when an org's budget or the global cap first refuses a call that day.
- An analysis names its provider as OpenTelemetry does (`gen_ai.provider.name`), so Bedrock is
  `aws.bedrock`: the name may now contain a dot.

Revision ID: 0009
Revises: 0008
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE ai_analyses DROP CONSTRAINT ai_analyses_provider_check,
            ADD CONSTRAINT ai_analyses_provider_check
                CHECK (provider ~ '^[a-z][a-z0-9_.-]{0,29}$');
        GRANT SELECT (id, org_id, upload_id, severity) ON findings TO app_analyze;
        GRANT INSERT (id, org_id, actor_user_id, actor_type, action, target_type, target_id,
                      outcome, ip, user_agent, request_id, trace_id, details)
            ON audit_log TO app_triage;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        REVOKE ALL ON audit_log FROM app_triage;
        REVOKE SELECT (id, org_id, upload_id, severity) ON findings FROM app_analyze;
        ALTER TABLE ai_analyses DROP CONSTRAINT ai_analyses_provider_check,
            ADD CONSTRAINT ai_analyses_provider_check
                CHECK (provider ~ '^[a-z][a-z0-9_-]{0,29}$');
        """
    )
```

- [ ] **Step 4: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `933 passed, 1 skipped`.

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/versions/0009_live_triage.py backend/tests/integration
git commit -m "feat(db): let the analyze worker rank findings and the triage worker audit; name providers as OpenTelemetry does" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The Bedrock provider

**Files:**
- Create: `backend/src/nettriage/adapters/bedrock_llm.py`
- Modify: `backend/src/nettriage/application/llm.py`, `backend/pyproject.toml`, `backend/uv.lock`
- Test: `backend/tests/unit/adapters/test_bedrock_llm.py`

**Interfaces:**
- Consumes: Plan 5a's `Generation`, `Usage`, `ProviderError` and `PRICES` (`application/llm.py`), and `output_schema()` (`application/ai_output.py`).
- Produces:
  - in `nettriage.application.llm`: `TRANSIENT_CODES`, `ProviderError.transient` (a property), and the price of `openai.gpt-oss-20b-1:0`;
  - in `nettriage.adapters.bedrock_llm`:
    - `GPT_OSS_20B = "openai.gpt-oss-20b-1:0"`, `BEDROCK_CONFIG` (a botocore `Config`) and `MODEL_FIELDS`;
    - `bedrock_schema(schema) -> dict`;
    - `BedrockProvider(client, model_id, name="aws.bedrock")`, an `LlmProvider`.

- [ ] **Step 1: Add the type stubs for Bedrock and SQS, and moto's SQS**

In `backend/pyproject.toml`, replace:
```toml
    "hypothesis>=6.168.3",
    "moto[dynamodb,ssm]>=5.2.3",
    "mypy>=2.3.1",
```
with:
```toml
    "hypothesis>=6.168.3",
    "moto[dynamodb,sqs,ssm]>=5.2.3",
    "mypy>=2.3.1",
```

In `backend/pyproject.toml`, replace:
```toml
    "ruff>=0.16.9",
    "types-boto3[dynamodb,s3,ssm]>=1.43.103",
]
```
with:
```toml
    "ruff>=0.16.9",
    "types-boto3[bedrock-runtime,dynamodb,s3,sqs,ssm]>=1.43.103",
]
```

Run: `cd backend && uv lock && uv sync`
Expected: `uv.lock` adds `types-boto3-bedrock-runtime` and `types-boto3-sqs`; nothing else changes version.

- [ ] **Step 2: Write the failing tests**

`backend/tests/unit/adapters/test_bedrock_llm.py`:
```python
"""The Bedrock provider (spec §8.3): the Converse API with the answer constrained to output schema
v1, and Bedrock's errors as codes that are safe to store."""

import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import boto3
import pytest
from botocore.exceptions import ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError
from botocore.stub import Stubber

from nettriage.adapters.bedrock_llm import (
    BEDROCK_CONFIG,
    GPT_OSS_20B,
    BedrockProvider,
    bedrock_schema,
)
from nettriage.application.ai_output import output_schema
from nettriage.application.llm import PRICES, Price, ProviderError, Usage

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string", "maxLength": 600}},
    "required": ["summary"],
    "additionalProperties": False,
}


def response(content: list[dict[str, Any]], stop: str = "end_turn") -> dict[str, Any]:
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop,
        "usage": {"inputTokens": 1800, "outputTokens": 320, "totalTokens": 2120},
        "metrics": {"latencyMs": 1450},
    }


@pytest.fixture
def stubbed() -> Iterator[tuple[BedrockProvider, Stubber]]:
    client = boto3.client("bedrock-runtime", region_name="eu-north-1")
    with Stubber(client) as stubber:
        yield BedrockProvider(client, GPT_OSS_20B), stubber


def test_a_call_sends_the_prompt_the_data_and_the_schema_bedrock_supports(
    stubbed: tuple[BedrockProvider, Stubber],
) -> None:
    provider, stubber = stubbed
    stubber.add_response(
        "converse",
        response(
            [
                {"reasoningContent": {"reasoningText": {"text": "A port scan."}}},
                {"text": '{"summary": "A scan."}'},
            ]
        ),
        {
            "modelId": "openai.gpt-oss-20b-1:0",
            "system": [{"text": "You explain findings."}],
            "messages": [{"role": "user", "content": [{"text": '{"detector":"port_scan"}'}]}],
            "inferenceConfig": {"maxTokens": 700, "temperature": 0.1},
            "outputConfig": {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {
                            "name": "triage_output",
                            "schema": json.dumps(bedrock_schema(SCHEMA)),
                        }
                    },
                }
            },
            "additionalModelRequestFields": {"reasoning_effort": "low"},
        },
    )

    generation = provider.generate_structured(
        "You explain findings.", '{"detector":"port_scan"}', SCHEMA, 700, 0.1
    )

    assert generation.output == {"summary": "A scan."}
    assert generation.usage == Usage(1800, 320)
    assert (generation.model_id, generation.latency_ms, generation.finish_reason) == (
        "openai.gpt-oss-20b-1:0",
        1450,
        "end_turn",
    )
    stubber.assert_no_pending_responses()


@pytest.mark.parametrize("text", ["Sorry, I can't.", '{"summary": "cut o', ""])
def test_an_answer_that_isnt_json_has_no_output(
    stubbed: tuple[BedrockProvider, Stubber], text: str
) -> None:
    provider, stubber = stubbed
    stubber.add_response("converse", response([{"text": text}], stop="max_tokens"))

    generation = provider.generate_structured("s", "{}", SCHEMA, 700, 0.1)

    assert (generation.output, generation.finish_reason) == (None, "max_tokens")


@pytest.mark.parametrize(
    ("error", "code", "transient"),
    [
        ("ThrottlingException", "provider_throttled", True),
        ("ModelTimeoutException", "provider_timeout", True),
        ("ServiceUnavailableException", "provider_unavailable", True),
        ("InternalServerException", "provider_unavailable", True),
        ("ModelNotReadyException", "provider_unavailable", True),
        ("ModelErrorException", "provider_unavailable", True),
        ("AccessDeniedException", "provider_denied", False),
        ("ValidationException", "provider_rejected", False),
        ("ResourceNotFoundException", "provider_rejected", False),
        ("SomethingNewException", "provider_error", False),
    ],
)
def test_bedrock_errors_become_codes_that_can_be_stored(
    stubbed: tuple[BedrockProvider, Stubber], error: str, code: str, transient: bool
) -> None:
    provider, stubber = stubbed
    stubber.add_client_error("converse", error, "Input contains 203.0.113.9")

    with pytest.raises(ProviderError) as failed:
        provider.generate_structured("s", "{}", SCHEMA, 700, 0.1)

    assert (failed.value.code, failed.value.transient, str(failed.value)) == (code, transient, code)
    assert failed.value.__cause__ is None


class _Unreachable:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def converse(self, **_: Any) -> dict[str, Any]:
        raise self.error


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ReadTimeoutError(endpoint_url="https://bedrock-runtime"), "provider_timeout"),
        (ConnectTimeoutError(endpoint_url="https://bedrock-runtime"), "provider_timeout"),
        (EndpointConnectionError(endpoint_url="https://bedrock-runtime"), "provider_unavailable"),
    ],
)
def test_a_slow_or_unreachable_bedrock_is_a_transient_failure(error: Exception, code: str) -> None:
    provider = BedrockProvider(_Unreachable(error), GPT_OSS_20B)  # type: ignore[arg-type]

    with pytest.raises(ProviderError) as failed:
        provider.generate_structured("s", "{}", SCHEMA, 700, 0.1)

    assert (failed.value.code, failed.value.transient) == (code, True)


def _keywords(schema: Any) -> set[str]:
    """Every JSON Schema keyword used anywhere in a schema (not property or definition names)."""
    found: set[str] = set()
    if isinstance(schema, list):
        for item in schema:
            found |= _keywords(item)
    elif isinstance(schema, dict):
        for key, value in schema.items():
            found.add(key)
            for item in value.values() if key in ("properties", "$defs") else [value]:
                found |= _keywords(item)
    return found


def test_the_schema_bedrock_gets_keeps_only_the_keywords_it_supports() -> None:
    reduced = bedrock_schema(output_schema())

    assert _keywords(reduced) <= {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "$defs",
        "$ref",
        "minItems",
    }
    assert reduced["additionalProperties"] is False
    assert reduced["required"] == output_schema()["required"]
    assert reduced["properties"]["recommended_next_steps"]["minItems"] == 1
    assert reduced["properties"]["confidence"]["enum"] == ["low", "medium", "high"]


def test_gpt_oss_20b_is_priced_as_in_stockholm() -> None:
    assert PRICES[GPT_OSS_20B] == Price(Decimal("0.07"), Decimal("0.30"))


def test_the_client_waits_20_seconds_and_retries_3_times() -> None:
    settings = vars(BEDROCK_CONFIG)

    assert (settings["read_timeout"], settings["retries"]) == (
        20,
        {"total_max_attempts": 4, "mode": "standard"},
    )
```

- [ ] **Step 3: Run them and watch them fail**

Run: `cd backend && uv run python -m pytest tests/unit/adapters/test_bedrock_llm.py`
Expected: FAIL. Collection stops with 1 error: `No module named 'nettriage.adapters.bedrock_llm'`.

- [ ] **Step 4: Mark the passing codes and price gpt-oss-20b**

In `backend/src/nettriage/application/llm.py`, replace:
```python
# What `ai_analyses.error_code` can hold (its CHECK, migration 0008).
_CODE = re.compile(r"[a-z][a-z_]{0,49}")


```
with:
```python
# What `ai_analyses.error_code` can hold (its CHECK, migration 0008).
_CODE = re.compile(r"[a-z][a-z_]{0,49}")
# Failures that can pass: the worker lets SQS deliver the finding again before storing `failed`.
TRANSIENT_CODES = frozenset({"provider_throttled", "provider_timeout", "provider_unavailable"})


```

In `backend/src/nettriage/application/llm.py`, replace:
```python
        super().__init__(self.code)


class LlmProvider(Protocol):
```
with:
```python
        super().__init__(self.code)

    @property
    def transient(self) -> bool:
        """Throttled, timed out or unavailable: worth trying again later."""
        return self.code in TRANSIENT_CODES


class LlmProvider(Protocol):
```

In `backend/src/nettriage/application/llm.py`, replace:
```python


# Per-model prices (spec §8.3). Plan 5b adds the Bedrock models it can call.
PRICES: dict[str, Price] = {
    "fake-triage": Price(Decimal("1.00"), Decimal("4.00")),
}

```
with:
```python


# Per-model prices (spec §8.3): on-demand, in the Region the model is called in.
PRICES: dict[str, Price] = {
    "fake-triage": Price(Decimal("1.00"), Decimal("4.00")),
    # OpenAI gpt-oss-20b on Bedrock in eu-north-1 (AWS's price list, 2026-10-01).
    "openai.gpt-oss-20b-1:0": Price(Decimal("0.07"), Decimal("0.30")),
}

```

- [ ] **Step 5: Write the provider**

`backend/src/nettriage/adapters/bedrock_llm.py`:
```python
"""Amazon Bedrock as the triage model (spec §8.3): the Converse API, with the answer constrained
to output schema v1 (structured output).

- The model is called on demand in its own Region, never through a cross-Region inference
  profile, which the account can't use (spec Revision 2, R4).
- Bedrock enforces only part of JSON Schema: no string lengths, patterns or array maximums.
  It gets the schema without them, and the Pydantic checks stay the gate (spec §8.3).
- A failed call raises `ProviderError` with a code that is safe to store and log, never
  Bedrock's message, which can quote the request.
- The client waits 20 seconds for an answer and retries throttling, server errors and timeouts
  3 times, with exponential backoff and full jitter (botocore's standard mode)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from nettriage.application.llm import Generation, ProviderError, Usage

if TYPE_CHECKING:
    from types_boto3_bedrock_runtime.client import BedrockRuntimeClient

GPT_OSS_20B = "openai.gpt-oss-20b-1:0"

# `total_max_attempts` counts the first call: 1 call and 3 retries.
BEDROCK_CONFIG = Config(
    connect_timeout=5,
    read_timeout=20,
    retries={"total_max_attempts": 4, "mode": "standard"},
)

# Model settings beyond Converse's own. gpt-oss reasons before it answers, and its reasoning
# counts toward the 700-token limit: keep it short.
MODEL_FIELDS: Mapping[str, Mapping[str, Any]] = {GPT_OSS_20B: {"reasoning_effort": "low"}}

# The JSON Schema keywords Bedrock's structured output enforces (and `minItems` only as 0 or 1).
_SUPPORTED = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "$defs",
        "$ref",
        "anyOf",
        "allOf",
        "format",
        "minItems",
    }
)

_CODES = {
    "ThrottlingException": "provider_throttled",
    "ServiceQuotaExceededException": "provider_throttled",
    "ModelTimeoutException": "provider_timeout",
    "InternalServerException": "provider_unavailable",
    "ServiceUnavailableException": "provider_unavailable",
    "ModelNotReadyException": "provider_unavailable",
    "ModelErrorException": "provider_unavailable",
    # The $5 budget action denies the call (spec §6.7), as would a missing permission.
    "AccessDeniedException": "provider_denied",
    "ValidationException": "provider_rejected",
    "ResourceNotFoundException": "provider_rejected",
}


def bedrock_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """The schema with only the keywords Bedrock supports. Property and definition names are kept;
    a `minItems` above 1 is dropped."""
    reduced: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _SUPPORTED or (key == "minItems" and value > 1):
            continue
        if key in ("properties", "$defs"):
            reduced[key] = {name: bedrock_schema(item) for name, item in value.items()}
        elif key == "items":
            reduced[key] = bedrock_schema(value)
        elif key in ("anyOf", "allOf"):
            reduced[key] = [bedrock_schema(item) for item in value]
        else:
            reduced[key] = value
    return reduced


@dataclass
class BedrockProvider:
    client: BedrockRuntimeClient
    model_id: str
    # OpenTelemetry's `gen_ai.provider.name` for Bedrock, also stored with each analysis.
    name: str = "aws.bedrock"
    fields: Mapping[str, Any] = field(init=False)

    def __post_init__(self) -> None:
        self.fields = MODEL_FIELDS.get(self.model_id, {})

    def generate_structured(
        self,
        system: str,
        user_json: str,
        schema: dict[str, Any],
        max_tokens: int,
        temperature: float,
    ) -> Generation:
        request: dict[str, Any] = {
            "modelId": self.model_id,
            "system": [{"text": system}],
            "messages": [{"role": "user", "content": [{"text": user_json}]}],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
            "outputConfig": {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {
                            "name": "triage_output",
                            "schema": json.dumps(bedrock_schema(schema)),
                        }
                    },
                }
            },
        }
        if self.fields:
            request["additionalModelRequestFields"] = dict(self.fields)
        try:
            response = self.client.converse(**request)
        except ClientError as error:
            raise ProviderError(
                _CODES.get(error.response["Error"]["Code"], "provider_error")
            ) from None
        except ReadTimeoutError, ConnectTimeoutError:
            raise ProviderError("provider_timeout") from None
        except EndpointConnectionError, ConnectionClosedError:
            raise ProviderError("provider_unavailable") from None
        except BotoCoreError:
            raise ProviderError("provider_error") from None
        # gpt-oss also returns its reasoning, as `reasoningContent` blocks: only the text answers.
        text = "".join(block.get("text", "") for block in response["output"]["message"]["content"])
        return Generation(
            output=_parsed(text),
            usage=Usage(response["usage"]["inputTokens"], response["usage"]["outputTokens"]),
            model_id=self.model_id,
            latency_ms=response["metrics"]["latencyMs"],
            finish_reason=response["stopReason"],
        )


def _parsed(text: str) -> Any:
    """The answer as JSON, or None when it isn't (cut off at the token limit, or prose)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
```

- [ ] **Step 6: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `953 passed, 1 skipped`.

- [ ] **Step 7: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/src backend/tests/unit/adapters/test_bedrock_llm.py
git commit -m "feat(ai): the Bedrock provider: Converse with structured output, gpt-oss-20b in Stockholm, storable error codes" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: The explainer for the live worker

**Files:**
- Modify: `backend/src/nettriage/entrypoints/triage/explainer.py`, `backend/src/nettriage/adapters/ai_budget.py`
- Test: `backend/tests/worker/test_explainer.py`, `backend/tests/unit/adapters/test_ai_budget.py`

**Interfaces:**
- Consumes: Task 1's audit grant, Task 2's `ProviderError.transient`, and Plan 5a's `Explainer`, `AiBudget` and `save_analysis`.
- Produces:
  - `AiBudget.first_refusal(org_id, scope) -> bool`;
  - in `nettriage.entrypoints.triage.explainer`:
    - `RETRY_DELAYS = (1.0, 3.0)`;
    - `ExplainLater(code)` with `.code`;
    - `Explainer(…, enabled=lambda: True, sleep=time.sleep)`;
    - `Explainer.explain(org_id, finding_id, *, last_delivery=True) -> Explained`, which raises `ExplainLater` for a passing provider failure when `last_delivery` is False.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/unit/adapters/test_ai_budget.py`, replace:
```python
        budget.reserve(uuid4(), 1_000, Decimal("0.001"))

```
with:
```python
        budget.reserve(uuid4(), 1_000, Decimal("0.001"))


def test_a_refusal_is_noted_once_a_day_per_org_and_scope(
    budget: AiBudget, clock: FakeClock
) -> None:
    org, other = uuid4(), uuid4()

    firsts = [
        budget.first_refusal(org, "org"),
        budget.first_refusal(org, "org"),
        budget.first_refusal(org, "global"),
        budget.first_refusal(other, "org"),
    ]
    clock.advance(timedelta(days=1))

    assert firsts == [True, False, True, True]
    assert budget.first_refusal(org, "org") is True


def test_a_refusal_that_can_not_be_noted_counts_as_noted(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    blind = AiBudget(runtime_table.client, "no-such-table", clock)

    assert blind.first_refusal(uuid4(), "org") is False

```

In `backend/tests/worker/test_explainer.py`, replace:
```python
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from sqlalchemy import text
from tenantdata import Tenant, add_tenant

from nettriage.adapters.ai_budget import AiBudget
from nettriage.adapters.ai_subjects import load_subject
from nettriage.adapters.fake_llm import FakeProvider
```
with:
```python
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from tenantdata import Tenant, add_finding, add_tenant

from nettriage.adapters import ai_store
from nettriage.adapters.ai_budget import AiBudget
from nettriage.adapters.ai_subjects import load_subject
from nettriage.adapters.fake_llm import FakeProvider
```

In `backend/tests/worker/test_explainer.py`, replace:
```python
from nettriage.application.ai_output import output_schema
from nettriage.application.llm import ProviderError
from nettriage.application.organizations import NotFound
from nettriage.entrypoints.triage.explainer import Explainer
from nettriage.platform.metrics import AiMetrics
from nettriage.prompts import triage_prompt

```
with:
```python
from nettriage.application.ai_output import output_schema
from nettriage.application.llm import ProviderError
from nettriage.application.organizations import NotFound
from nettriage.entrypoints.triage import explainer as explainer_module
from nettriage.entrypoints.triage.explainer import Explained, Explainer, ExplainLater
from nettriage.platform.metrics import AiMetrics
from nettriage.prompts import triage_prompt

```

In `backend/tests/worker/test_explainer.py`, replace:
```python
        rig.explainer.explain(mine.org_id, theirs.finding_id)
    assert rig.model.calls == []

```
with:
```python
        rig.explainer.explain(mine.org_id, theirs.finding_id)
    assert rig.model.calls == []


def test_switched_off_ai_makes_no_call_and_says_why(database: Database, rig: Rig) -> None:
    tenant = add_tenant(database.admin)
    off = replace(rig.explainer, enabled=lambda: False)

    explained = off.explain(tenant.org_id, tenant.finding_id)

    assert explained.outcome == "skipped_budget"
    assert analysis(database, tenant)["error_code"] == "ai_disabled"
    assert rig.model.calls == []
    assert budget_item(rig, f"BUDGET#{tenant.org_id}#2026-09-28") == {}


def test_a_cached_answer_is_served_while_ai_is_switched_off(database: Database, rig: Rig) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [answer()]
    first = rig.explainer.explain(tenant.org_id, tenant.finding_id)
    off = replace(rig.explainer, enabled=lambda: False)

    assert off.explain(tenant.org_id, tenant.finding_id) == Explained("cached", first.analysis_id)


def stored_count(database: Database, tenant: Tenant) -> int:
    with database.admin.begin() as connection:
        found: int = connection.execute(
            text("SELECT count(*) FROM ai_analyses WHERE finding_id = :finding AND id <> :seeded"),
            {"finding": tenant.finding_id, "seeded": tenant.analysis_id},
        ).scalar_one()
    return found


def test_a_passing_provider_failure_is_tried_again_before_the_last_delivery(
    database: Database, rig: Rig
) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [ProviderError("provider_throttled")]

    with pytest.raises(ExplainLater) as later:
        rig.explainer.explain(tenant.org_id, tenant.finding_id, last_delivery=False)

    assert later.value.code == "provider_throttled"
    assert stored_count(database, tenant) == 0
    assert budget_item(rig, f"BUDGET#{tenant.org_id}#2026-09-28")["tokens_reserved"] == 0


def test_a_passing_failure_on_the_last_delivery_is_stored(database: Database, rig: Rig) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [ProviderError("provider_timeout")]

    explained = rig.explainer.explain(tenant.org_id, tenant.finding_id, last_delivery=True)

    assert (explained.outcome, analysis(database, tenant)["error_code"]) == (
        "failed",
        "provider_timeout",
    )


def test_a_lasting_provider_failure_is_stored_at_once(database: Database, rig: Rig) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [ProviderError("provider_denied")]

    explained = rig.explainer.explain(tenant.org_id, tenant.finding_id, last_delivery=False)

    assert (explained.outcome, analysis(database, tenant)["error_code"]) == (
        "failed",
        "provider_denied",
    )


def test_a_store_that_fails_briefly_is_retried_without_a_second_call(
    database: Database, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [answer()]
    real_save = ai_store.save_analysis
    failures = [OperationalError("INSERT", {}, Exception("server closed the connection"))]

    def flaky_save(engine: Any, record: Any) -> Any:
        if failures:
            raise failures.pop()
        return real_save(engine, record)

    monkeypatch.setattr(explainer_module, "save_analysis", flaky_save)
    pauses: list[float] = []
    patient = replace(rig.explainer, sleep=pauses.append)

    explained = patient.explain(tenant.org_id, tenant.finding_id)

    assert explained.outcome == "succeeded"
    assert (len(rig.model.calls), pauses) == (1, [1.0])


def test_a_spent_budget_is_audited_once_a_day_per_org(
    database: Database, rig: Rig, runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        second = add_finding(connection, tenant.org_id, tenant.upload_id)
    poor = replace(
        rig.explainer,
        budget=AiBudget(runtime_table.client, runtime_table.name, clock, org_daily_tokens=500),
    )

    poor.explain(tenant.org_id, tenant.finding_id)
    poor.explain(tenant.org_id, second)

    with database.admin.begin() as connection:
        audited = connection.execute(
            text(
                "SELECT action, outcome, actor_type, target_type, target_id, details "
                "FROM audit_log WHERE org_id = :org AND action = 'budget.exhausted'"
            ),
            {"org": tenant.org_id},
        ).all()
    assert [tuple(row) for row in audited] == [
        (
            "budget.exhausted",
            "denied",
            "system",
            "finding",
            str(tenant.finding_id),
            {"scope": "org"},
        )
    ]

```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/worker/test_explainer.py tests/unit/adapters/test_ai_budget.py`
Expected: FAIL. Collection stops with 1 error: `cannot import name 'ExplainLater' from 'nettriage.entrypoints.triage.explainer'`.

- [ ] **Step 3: Note a budget's first refusal of the day**

In `backend/src/nettriage/adapters/ai_budget.py`, replace:
```python

    def _take(
```
with:
```python

    def first_refusal(self, org_id: UUID, scope: Literal["org", "global"]) -> bool:
        """True the first time today that a budget refuses this org's calls, so the refusal is
        audited once a day per org and budget (spec §9.4), not once per finding. A refusal that
        can't be noted counts as noted: no audit flood when DynamoDB fails."""
        now = self._clock()
        try:
            self._client.update_item(
                TableName=self._table,
                Key={"pk": {"S": f"BUDGET#{org_id}#{now.date().isoformat()}"}},
                UpdateExpression=(
                    f"SET refused_{scope} = :yes, expires_at = if_not_exists(expires_at, :expires)"
                ),
                ConditionExpression=f"attribute_not_exists(refused_{scope})",
                ExpressionAttributeValues={
                    ":yes": {"BOOL": True},
                    ":expires": {"N": str(epoch_seconds(now + KEEP_FOR))},
                },
            )
        except ClientError as error:
            if not is_condition_failure(error):
                logger.warning("ai_budget_refusal_unnoted", extra={"error_code": "dynamodb_error"})
            return False
        except BotoCoreError:
            logger.warning("ai_budget_refusal_unnoted", extra={"error_code": "dynamodb_error"})
            return False
        return True

    def _take(
```

- [ ] **Step 4: Teach the explainer the switch, passing failures, retries and the audit**

In `backend/src/nettriage/entrypoints/triage/explainer.py`, replace:
```python
2. A succeeded analysis of the same input, model and prompt is the answer: no call (`cached`).
3. Reserve the call's estimate in the org's token budget and the global spend cap; if either is
   spent, or can't be read, store `skipped_budget` and make no call.
4. Call the model with prompt v1 and output schema v1, then settle the budget to the real usage.
   A provider failure releases the reservation and stores `failed`.
5. Check the answer. If it fails a check, ask once more with the errors (`validation_errors`);
   if that fails too, store `invalid_output`.
6. Store the analysis, with the AI's techniques and an `ai_explained` event when it succeeded.

```
with:
```python
2. A succeeded analysis of the same input, model and prompt is the answer: no call (`cached`).
3. While the `ai_enabled` kill switch is off, store `skipped_budget` (`ai_disabled`), no call.
4. Reserve the call's estimate in the org's token budget and the global spend cap; if either is
   spent, or can't be read, store `skipped_budget` and make no call. The first refusal of the day
   for an org and budget is audited (`budget.exhausted`).
5. Call the model with prompt v1 and output schema v1, then settle the budget to the real usage.
   A provider failure releases the reservation. A passing one (throttled, timed out,
   unavailable) before the message's last delivery stores nothing: SQS delivers the finding
   again (Plan 5b). Otherwise it stores `failed`.
6. Check the answer. If it fails a check, ask once more with the errors (`validation_errors`);
   if that fails too, store `invalid_output`.
7. Store the analysis, with the AI's techniques and an `ai_explained` event when it succeeded.

Neon waking up gets two quick retries, for the reads and for the store, so a stored answer is
never paid for twice (spec §8.6).

```

In `backend/src/nettriage/entrypoints/triage/explainer.py`, replace:
```python
import logging
from dataclasses import dataclass, field
```
with:
```python
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
```

In `backend/src/nettriage/entrypoints/triage/explainer.py`, replace:
```python
from sqlalchemy import Engine

```
with:
```python
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError

```

In `backend/src/nettriage/entrypoints/triage/explainer.py`, replace:
```python
from nettriage.adapters.ai_subjects import load_subject
from nettriage.application.ai_input import (
```
with:
```python
from nettriage.adapters.ai_subjects import load_subject
from nettriage.adapters.audit_log import record
from nettriage.application.ai_input import (
```

In `backend/src/nettriage/entrypoints/triage/explainer.py`, replace:
```python
)
from nettriage.application.llm import (
```
with:
```python
)
from nettriage.application.audit import AuditEvent
from nettriage.application.llm import (
```

In `backend/src/nettriage/entrypoints/triage/explainer.py`, replace:
```python
ATTEMPTS = 2

```
with:
```python
ATTEMPTS = 2
# Pauses before retrying a database that is waking up, as the analyze worker does (spec §8.6).
RETRY_DELAYS = (1.0, 3.0)


class ExplainLater(Exception):
    """A passing provider failure before the message's last delivery: nothing was stored, and SQS
    delivers the finding again. `code` is safe to log."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

```

In `backend/src/nettriage/entrypoints/triage/explainer.py`, replace:
```python
    prompt_version: str = PROMPT_VERSION
    price: Price = field(init=False)
```
with:
```python
    prompt_version: str = PROMPT_VERSION
    # The `ai_enabled` kill switch (spec §9.7). The worker reads it from SSM.
    enabled: Callable[[], bool] = lambda: True
    sleep: Callable[[float], None] = time.sleep
    price: Price = field(init=False)
```

In `backend/src/nettriage/entrypoints/triage/explainer.py`, replace:
```python

    def explain(self, org_id: UUID, finding_id: UUID) -> Explained:
        subject = load_subject(self.database, org_id, finding_id)
        content = user_content(subject)
        key = input_hash(content)
        cached = find_cached(
            self.database,
            org_id,
            finding_id,
            model_id=self.provider.model_id,
            prompt_version=self.prompt_version,
            input_hash=key,
        )
```
with:
```python

    def explain(self, org_id: UUID, finding_id: UUID, *, last_delivery: bool = True) -> Explained:
        subject = self._retrying(lambda: load_subject(self.database, org_id, finding_id))
        content = user_content(subject)
        key = input_hash(content)
        cached = self._retrying(
            lambda: find_cached(
                self.database,
                org_id,
                finding_id,
                model_id=self.provider.model_id,
                prompt_version=self.prompt_version,
                input_hash=key,
            )
        )
```

In `backend/src/nettriage/entrypoints/triage/explainer.py`, replace:
```python
            return self._done("cached", cached.id)
        system = triage_prompt(self.prompt_version)
```
with:
```python
            return self._done("cached", cached.id)
        if not self.enabled():
            return self._store(subject, key, "skipped_budget", _Spent(), error_code="ai_disabled")
        system = triage_prompt(self.prompt_version)
```

In `backend/src/nettriage/entrypoints/triage/explainer.py`, replace:
```python
            except BudgetExhausted as error:
                code = f"budget_exhausted_{error.scope}"
```
with:
```python
            except BudgetExhausted as error:
                self._audit_refusal(org_id, finding_id, error.scope)
                code = f"budget_exhausted_{error.scope}"
```

In `backend/src/nettriage/entrypoints/triage/explainer.py`, replace:
```python
            except ProviderError as error:
                return self._store(subject, key, "failed", spent, error_code=error.code)
```
with:
```python
            except ProviderError as error:
                if error.transient and not last_delivery:
                    logger.info("finding_explain_later", extra={"error_code": error.code})
                    raise ExplainLater(error.code) from None
                return self._store(subject, key, "failed", spent, error_code=error.code)
```

In `backend/src/nettriage/entrypoints/triage/explainer.py`, replace:
```python
        called = spent.calls > 0
        analysis_id = save_analysis(
            self.database,
            AnalysisRecord(
                org_id=subject.org_id,
                finding_id=subject.finding_id,
                status=status,
                provider=self.provider.name,
                model_id=self.provider.model_id,
                prompt_version=self.prompt_version,
                output_schema_version=OUTPUT_SCHEMA_VERSION,
                input_hash=key,
                output=None if output is None else output.model_dump(mode="json"),
                input_tokens=spent.input_tokens if called else None,
                output_tokens=spent.output_tokens if called else None,
                cost_usd=spent.cost_usd if called else None,
                latency_ms=spent.latency_ms if called else None,
                error_code=error_code,
                techniques=()
                if output is None
                else tuple((claim.id, claim.rationale) for claim in output.attack_techniques),
            ),
        )
        return self._done(status, analysis_id)

```
with:
```python
        called = spent.calls > 0
        stored = AnalysisRecord(
            org_id=subject.org_id,
            finding_id=subject.finding_id,
            status=status,
            provider=self.provider.name,
            model_id=self.provider.model_id,
            prompt_version=self.prompt_version,
            output_schema_version=OUTPUT_SCHEMA_VERSION,
            input_hash=key,
            output=None if output is None else output.model_dump(mode="json"),
            input_tokens=spent.input_tokens if called else None,
            output_tokens=spent.output_tokens if called else None,
            cost_usd=spent.cost_usd if called else None,
            latency_ms=spent.latency_ms if called else None,
            error_code=error_code,
            techniques=()
            if output is None
            else tuple((claim.id, claim.rationale) for claim in output.attack_techniques),
        )
        analysis_id = self._retrying(lambda: save_analysis(self.database, stored))
        return self._done(status, analysis_id)

    def _retrying[T](self, step: Callable[[], T]) -> T:
        for delay in RETRY_DELAYS:
            try:
                return step()
            except OperationalError:
                logger.warning("database_unavailable", extra={"retry_in_s": delay})
                self.sleep(delay)
        return step()

    def _audit_refusal(
        self, org_id: UUID, finding_id: UUID, scope: Literal["org", "global"]
    ) -> None:
        if not self.budget.first_refusal(org_id, scope):
            return
        try:
            record(
                self.database,
                AuditEvent(
                    action="budget.exhausted",
                    outcome="denied",
                    actor_type="system",
                    org_id=org_id,
                    target_type="finding",
                    target_id=str(finding_id),
                    details={"scope": scope},
                ),
            )
        except Exception as error:  # the skipped analysis is stored either way
            logger.warning("audit_failed", extra={"error_code": type(error).__name__})

```

- [ ] **Step 5: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `962 passed, 1 skipped`.

- [ ] **Step 6: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(ai): the explainer for the live worker: kill switch, passing failures retried by SQS, database retries, budget.exhausted audited once a day" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The triage worker

**Files:**
- Create: `backend/src/nettriage/entrypoints/triage/worker.py`, `backend/src/nettriage/entrypoints/triage/handler.py`, `backend/src/nettriage/entrypoints/triage/wiring.py`
- Modify: `backend/src/nettriage/platform/config.py`, `backend/src/nettriage/platform/metrics.py`
- Test: `backend/tests/worker/test_triage_worker.py`, `backend/tests/unit/triage/test_triage_wiring.py`

**Interfaces:**
- Consumes:
  - Task 2's `BedrockProvider` and `BEDROCK_CONFIG`;
  - Task 3's `Explainer` (with `enabled`), `Explained` and `ExplainLater`;
  - Plan 4a's `KillSwitch` and `ssm_parameter`;
  - Plan 4b's `read_parameters`, `AWS_CONFIG`, `create_database_engine` and `links_from`;
  - the harness's `logs`, `clock`, `runtime_table` and `database` fixtures.
- Produces:
  - `Settings.ai_enabled_parameter`, `Settings.bedrock_region` (default `eu-north-1`) and `Settings.bedrock_model_id`;
  - `AiMetrics.queue_message_age`;
  - in `nettriage.entrypoints.triage.worker`: `LAST_DELIVERY = 3`, and `TriageWorker(explainer, clock, metrics, tracer, flush)` with `.handle_batch(records) -> list[str]` (the message IDs to deliver again) and `.handle_message(record) -> bool`;
  - `nettriage.entrypoints.triage.wiring.build_worker(settings, tracer_provider, meter_provider, session=None) -> TriageWorker`;
  - `nettriage.entrypoints.triage.handler.handle(event, context) -> {"batchItemFailures": [...]}`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/worker/test_triage_worker.py`:
```python
"""The triage worker (spec §4.2, §8.6): one SQS message per finding, explained with the real
explainer (Postgres as `app_triage`, budgets in moto, a scripted model). A message that fails is
handed back to SQS, and nothing the worker raises reaches Lambda."""

import io
import json
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID, uuid7

import pytest
from aidata import good_output
from conftest import Database, FakeClock, RuntimeTable
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import HistogramDataPoint, InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from sqlalchemy import text
from tenantdata import Tenant, add_tenant

from nettriage.adapters.ai_budget import AiBudget
from nettriage.adapters.fake_llm import FakeProvider
from nettriage.application.llm import ProviderError
from nettriage.entrypoints.triage.explainer import Explainer
from nettriage.entrypoints.triage.worker import TriageWorker
from nettriage.platform.metrics import AiMetrics

ANSWER = good_output(summary="203.0.113.9 probed port 22 on 10.0.0.5, and it was rejected.")
UPLOAD_TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


@dataclass
class Rig:
    worker: TriageWorker
    model: FakeProvider
    spans: InMemorySpanExporter
    metrics: InMemoryMetricReader


@pytest.fixture
def rig(database: Database, runtime_table: RuntimeTable, clock: FakeClock) -> Rig:
    spans = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    reader = InMemoryMetricReader()
    metrics = AiMetrics(MeterProvider(metric_readers=[reader]))
    tracer = tracer_provider.get_tracer("test")
    model = FakeProvider()
    explainer = Explainer(
        database=database.app_triage,
        provider=model,
        budget=AiBudget(runtime_table.client, runtime_table.name, clock),
        metrics=metrics,
        tracer=tracer,
    )
    worker = TriageWorker(explainer=explainer, clock=clock, metrics=metrics, tracer=tracer)
    return Rig(worker, model, spans, reader)


def message(
    org_id: UUID | str,
    finding_id: UUID | str,
    *,
    receive_count: int = 1,
    message_id: str = "m-1",
    traceparent: str | None = UPLOAD_TRACEPARENT,
) -> dict[str, Any]:
    """An SQS record as Lambda delivers it, on its `receive_count`th delivery."""
    attributes = {"traceparent": {"stringValue": traceparent, "dataType": "String"}}
    return {
        "messageId": message_id,
        "body": json.dumps({"org_id": str(org_id), "finding_id": str(finding_id)}),
        "attributes": {
            "SentTimestamp": "1790596740000",
            "ApproximateReceiveCount": str(receive_count),
        },
        "messageAttributes": attributes if traceparent else {},
    }


def statuses(database: Database, tenant: Tenant) -> list[tuple[str, str | None]]:
    with database.admin.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT status, error_code FROM ai_analyses WHERE finding_id = :finding "
                "AND id <> :seeded"
            ),
            {"finding": tenant.finding_id, "seeded": tenant.analysis_id},
        ).all()
    return [tuple(row) for row in rows]


def test_a_queued_finding_is_explained_and_its_message_done(database: Database, rig: Rig) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [ANSWER]

    retry = rig.worker.handle_batch([message(tenant.org_id, tenant.finding_id)])

    assert retry == []
    assert statuses(database, tenant) == [("succeeded", None)]


def test_a_passing_bedrock_failure_is_handed_back_to_sqs(database: Database, rig: Rig) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [ProviderError("provider_throttled")]

    retry = rig.worker.handle_batch([message(tenant.org_id, tenant.finding_id, receive_count=2)])

    assert retry == ["m-1"]
    assert statuses(database, tenant) == []


def test_on_the_last_delivery_a_passing_failure_is_stored(database: Database, rig: Rig) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [ProviderError("provider_throttled")]

    retry = rig.worker.handle_batch([message(tenant.org_id, tenant.finding_id, receive_count=3)])

    assert retry == []
    assert statuses(database, tenant) == [("failed", "provider_throttled")]


def test_a_finding_that_is_gone_is_let_go(database: Database, rig: Rig, logs: io.StringIO) -> None:
    tenant = add_tenant(database.admin)

    retry = rig.worker.handle_batch([message(tenant.org_id, uuid7())])

    assert retry == []
    assert rig.model.calls == []
    assert '"reason": "finding_gone"' in logs.getvalue()


@pytest.mark.parametrize(
    "body", ["not json", "[]", '{"org_id": "x", "finding_id": "y"}', '{"org_id": null}']
)
def test_a_malformed_message_is_let_go(rig: Rig, logs: io.StringIO, body: str) -> None:
    record = message(uuid7(), uuid7()) | {"body": body}

    assert rig.worker.handle_batch([record]) == []
    assert '"reason": "malformed"' in logs.getvalue()


class _Broken:
    def explain(self, *_: Any, **__: Any) -> Any:
        raise RuntimeError("could not store 203.0.113.9")


def test_an_unexpected_error_is_handed_back_and_logged_by_type_only(
    rig: Rig, logs: io.StringIO
) -> None:
    broken = replace(rig.worker, explainer=_Broken())

    retry = broken.handle_batch([message(uuid7(), uuid7())])

    assert retry == ["m-1"]
    assert '"error_code": "RuntimeError"' in logs.getvalue()
    assert "203.0.113.9" not in logs.getvalue()
    [span] = rig.spans.get_finished_spans()
    assert (span.status.status_code, span.status.description) == (StatusCode.ERROR, "RuntimeError")


def test_each_message_of_a_batch_stands_alone(database: Database, rig: Rig) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [ANSWER]

    retry = rig.worker.handle_batch(
        [
            message(tenant.org_id, tenant.finding_id, message_id="good"),
            message("x", "y", message_id="malformed"),
        ]
    )

    assert retry == []
    assert statuses(database, tenant) == [("succeeded", None)]


def test_the_explain_span_links_to_the_upload_and_holds_the_model_call(
    database: Database, rig: Rig
) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [ANSWER]

    rig.worker.handle_batch([message(tenant.org_id, tenant.finding_id)])

    spans = {span.name: span for span in rig.spans.get_finished_spans()}
    explain, generate = spans["triage.explain"], spans["triage.generate"]
    assert [format(link.context.trace_id, "032x") for link in explain.links] == [
        "0af7651916cd43dd8448eb211c80319c"
    ]
    assert generate.parent is not None
    assert generate.parent.span_id == explain.context.span_id
    assert explain.attributes == {"nettriage.ai.outcome": "succeeded"}


def test_the_time_a_message_waited_is_recorded(database: Database, rig: Rig) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [ANSWER]

    rig.worker.handle_batch([message(tenant.org_id, tenant.finding_id)])

    data = rig.metrics.get_metrics_data()
    assert data is not None
    points = [
        point
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == "nettriage.queue.message.age"
        for point in metric.data.data_points
    ]
    assert len(points) == 1
    assert isinstance(points[0], HistogramDataPoint)
    assert (points[0].sum, dict(points[0].attributes or {})) == (60.0, {"queue": "triage"})
```

`backend/tests/unit/triage/test_triage_wiring.py`:
```python
"""Building the triage worker in Lambda (spec §6.8, §8.3, §9.7): its own database URL from SSM,
Bedrock in the model's Region, the runtime table for budgets, and the `ai_enabled` switch."""

from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from nettriage.adapters.bedrock_llm import BedrockProvider
from nettriage.adapters.parameters import MissingParameterError
from nettriage.entrypoints.triage import handler
from nettriage.entrypoints.triage.explainer import Explainer
from nettriage.entrypoints.triage.wiring import build_worker
from nettriage.platform.config import Settings

SETTINGS = Settings(
    stage="dev",
    service_name="nettriage-triage",
    database_url_parameter="/nettriage/dev/db/app-triage-url",
    runtime_table="nettriage-dev-runtime",
    ai_enabled_parameter="/nettriage/dev/kill/ai-enabled",
    bedrock_region="eu-north-1",
    bedrock_model_id="openai.gpt-oss-20b-1:0",
)


@pytest.fixture
def session() -> Iterator[boto3.session.Session]:
    with mock_aws():
        yield boto3.session.Session(region_name="eu-north-1")


def store_database_url(session: boto3.session.Session) -> None:
    session.client("ssm").put_parameter(
        Name="/nettriage/dev/db/app-triage-url",
        Value="postgresql://app_triage:pw@ep-x-pooler.eu-central-1.aws.neon.tech/neondb",
        Type="SecureString",
    )


def test_the_worker_explains_with_bedrock_as_app_triage(session: boto3.session.Session) -> None:
    store_database_url(session)

    worker = build_worker(SETTINGS, TracerProvider(), MeterProvider(), session)

    explainer = worker.explainer
    assert isinstance(explainer, Explainer)
    assert explainer.database.url.username == "app_triage"
    assert isinstance(explainer.provider, BedrockProvider)
    assert explainer.provider.model_id == "openai.gpt-oss-20b-1:0"
    assert explainer.provider.client.meta.region_name == "eu-north-1"
    worker.flush()


@pytest.mark.parametrize(("value", "on"), [("true", True), ("false", False), (None, False)])
def test_the_ai_switch_is_read_from_ssm(
    session: boto3.session.Session, value: str | None, on: bool
) -> None:
    store_database_url(session)
    if value is not None:
        session.client("ssm").put_parameter(
            Name="/nettriage/dev/kill/ai-enabled", Value=value, Type="String"
        )

    worker = build_worker(SETTINGS, TracerProvider(), MeterProvider(), session)

    assert isinstance(worker.explainer, Explainer)
    assert worker.explainer.enabled() is on


def test_a_missing_database_url_is_named(session: boto3.session.Session) -> None:
    with pytest.raises(MissingParameterError, match="/nettriage/dev/db/app-triage-url"):
        build_worker(SETTINGS, TracerProvider(), MeterProvider(), session)


class _Worker:
    def __init__(self) -> None:
        self.flushed = False

    def handle_batch(self, records: list[dict[str, Any]]) -> list[str]:
        return [record["messageId"] for record in records if record["body"] == "fail"]

    def flush(self) -> None:
        self.flushed = True


def test_the_handler_reports_the_messages_to_deliver_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Worker()
    monkeypatch.setattr(handler, "worker", lambda: fake)
    event = {"Records": [{"messageId": "a", "body": "ok"}, {"messageId": "b", "body": "fail"}]}

    response = handler.handle(event, object())

    assert response == {"batchItemFailures": [{"itemIdentifier": "b"}]}
    assert fake.flushed
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/worker/test_triage_worker.py tests/unit/triage`
Expected: FAIL. Collection stops with 2 errors: `No module named 'nettriage.entrypoints.triage.worker'` and `cannot import name 'handler' from 'nettriage.entrypoints.triage'`.

- [ ] **Step 3: Add the worker's settings and the triage queue's age**

In `backend/src/nettriage/platform/config.py`, replace:
```python
    uploads_enabled_parameter: str = ""

```
with:
```python
    uploads_enabled_parameter: str = ""
    # The triage worker (Plan 5b): the AI kill switch's SSM parameter, and the model it calls,
    # on demand in the model's own Region (spec §8.3, §9.7).
    ai_enabled_parameter: str = ""
    bedrock_region: str = "eu-north-1"
    bedrock_model_id: str = ""

```

In `backend/src/nettriage/platform/metrics.py`, replace:
```python
            "nettriage.ai.cache.hits", description="Explanations served from a stored analysis"
        )

```
with:
```python
            "nettriage.ai.cache.hits", description="Explanations served from a stored analysis"
        )
        self.queue_message_age = meter.create_histogram(
            "nettriage.queue.message.age",
            unit="s",
            description="How long a message waited in its queue, by queue",
        )

```

- [ ] **Step 4: Write the worker, its wiring and its handler**

`backend/src/nettriage/entrypoints/triage/worker.py`:
```python
"""The triage worker (spec §4.2, §8.6). Each SQS message names one finding to explain,
`{"org_id": …, "finding_id": …}`, with the upload's W3C traceparent as a message attribute.

A message that fails is handed back to SQS (a partial batch response): SQS delivers it again
after the visibility timeout, at most 3 times, then moves it to the dead-letter queue. A passing
Bedrock failure is handed back the same way until the last delivery, which stores `failed`.
A malformed message, or one whose finding is gone (its org was deleted), is let go.

Nothing the worker raises reaches Lambda, so failures are logged and traced by their type or
code only, never by an error's message, which can quote the finding (spec §9.3)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from opentelemetry.trace import Status, StatusCode, Tracer

from nettriage.application.clock import Clock
from nettriage.application.organizations import NotFound
from nettriage.entrypoints.triage.explainer import Explained, ExplainLater
from nettriage.platform.metrics import AiMetrics
from nettriage.platform.trace_context import links_from

logger = logging.getLogger(__name__)

# The triage queue's maxReceiveCount (infra/modules/pipeline/triage.tf).
LAST_DELIVERY = 3


class Explains(Protocol):
    def explain(
        self, org_id: UUID, finding_id: UUID, *, last_delivery: bool = True
    ) -> Explained: ...


@dataclass
class TriageWorker:
    explainer: Explains
    clock: Clock
    metrics: AiMetrics
    tracer: Tracer
    flush: Callable[[], None] = lambda: None

    def handle_batch(self, records: Iterable[Mapping[str, Any]]) -> list[str]:
        """The IDs of the messages SQS should deliver again."""
        return [record["messageId"] for record in records if not self.handle_message(record)]

    def handle_message(self, record: Mapping[str, Any]) -> bool:
        """True when the message is done with: explained, or not worth another delivery."""
        self._record_age(record)
        target = _target(record.get("body", ""))
        if target is None:
            logger.warning("triage_message_ignored", extra={"reason": "malformed"})
            return True
        deliveries = int(record.get("attributes", {}).get("ApproximateReceiveCount", "1"))
        traceparent = record.get("messageAttributes", {}).get("traceparent", {}).get("stringValue")
        with self.tracer.start_as_current_span(
            "triage.explain",
            links=links_from(traceparent),
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                explained = self.explainer.explain(
                    *target, last_delivery=deliveries >= LAST_DELIVERY
                )
            except NotFound:
                logger.info("triage_message_ignored", extra={"reason": "finding_gone"})
                return True
            except ExplainLater as later:
                span.set_status(Status(StatusCode.ERROR, later.code))
                return False
            except Exception as error:
                span.set_status(Status(StatusCode.ERROR, type(error).__name__))
                logger.error("triage_failed", extra={"error_code": type(error).__name__})
                return False
            span.set_attribute("nettriage.ai.outcome", explained.outcome)
        return True

    def _record_age(self, record: Mapping[str, Any]) -> None:
        sent = record.get("attributes", {}).get("SentTimestamp")
        if sent is not None:
            age = self.clock().timestamp() - int(sent) / 1000
            self.metrics.queue_message_age.record(max(age, 0.0), {"queue": "triage"})


def _target(body: str) -> tuple[UUID, UUID] | None:
    try:
        parsed = json.loads(body)
        return UUID(parsed["org_id"]), UUID(parsed["finding_id"])
    except ValueError, TypeError, KeyError, AttributeError:
        return None
```

`backend/src/nettriage/entrypoints/triage/wiring.py`:
```python
"""Building the triage worker in Lambda, once per cold start (spec §6.8): its database URL comes
from SSM, budgets live in the runtime table, the model is called through Bedrock in its own
Region, and the `ai_enabled` switch is re-read from SSM every minute (spec §9.7)."""

from __future__ import annotations

import boto3
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from nettriage.adapters.ai_budget import AiBudget
from nettriage.adapters.bedrock_llm import BEDROCK_CONFIG, BedrockProvider
from nettriage.adapters.kill_switch import KillSwitch, ssm_parameter
from nettriage.adapters.parameters import AWS_CONFIG, read_parameters
from nettriage.adapters.postgres import create_database_engine
from nettriage.application.clock import system_clock
from nettriage.entrypoints.triage.explainer import Explainer
from nettriage.entrypoints.triage.worker import TriageWorker
from nettriage.platform.config import Settings
from nettriage.platform.metrics import AiMetrics


def build_worker(
    settings: Settings,
    tracer_provider: TracerProvider,
    meter_provider: MeterProvider,
    session: boto3.session.Session | None = None,
) -> TriageWorker:
    session = session or boto3.session.Session()
    ssm = session.client("ssm", config=AWS_CONFIG)
    values = read_parameters(ssm, [settings.database_url_parameter])
    metrics = AiMetrics(meter_provider)
    tracer = tracer_provider.get_tracer("nettriage.triage")
    bedrock = session.client(
        "bedrock-runtime", region_name=settings.bedrock_region, config=BEDROCK_CONFIG
    )
    explainer = Explainer(
        database=create_database_engine(values[settings.database_url_parameter], pool_size=1),
        provider=BedrockProvider(bedrock, settings.bedrock_model_id),
        budget=AiBudget(
            session.client("dynamodb", config=AWS_CONFIG), settings.runtime_table, system_clock
        ),
        metrics=metrics,
        tracer=tracer,
        enabled=KillSwitch(ssm_parameter(ssm, settings.ai_enabled_parameter), system_clock).is_on,
    )

    def flush() -> None:
        # Lambda may freeze the environment right after the handler returns.
        tracer_provider.force_flush()
        meter_provider.force_flush()

    return TriageWorker(
        explainer=explainer, clock=system_clock, metrics=metrics, tracer=tracer, flush=flush
    )
```

`backend/src/nettriage/entrypoints/triage/handler.py`:
```python
"""The triage Lambda's entry point (spec §3.5): `nettriage.entrypoints.triage.handler.handle`.
SQS delivers one message per invocation. The handler answers with a partial batch response,
naming the messages SQS should deliver again; it never raises, so Lambda never logs an error's
message (spec §9.3)."""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from typing import Any

from nettriage.entrypoints.triage.wiring import build_worker
from nettriage.entrypoints.triage.worker import TriageWorker
from nettriage.platform.config import Settings
from nettriage.platform.logging import configure_logging
from nettriage.platform.telemetry import (
    create_meter_provider,
    create_tracer_provider,
    install_global_providers,
)


@cache
def worker() -> TriageWorker:
    """Built on the first invocation of each Lambda instance, then reused."""
    settings = Settings()
    configure_logging(settings)
    tracer_provider = create_tracer_provider(settings)
    meter_provider = create_meter_provider(settings)
    install_global_providers(tracer_provider, meter_provider)
    return build_worker(settings, tracer_provider, meter_provider)


def handle(event: Mapping[str, Any], context: object) -> dict[str, list[dict[str, str]]]:
    current = worker()
    try:
        retry = current.handle_batch(event.get("Records", []))
    finally:
        current.flush()
    return {"batchItemFailures": [{"itemIdentifier": message_id} for message_id in retry]}
```

- [ ] **Step 5: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `980 passed, 1 skipped`.

- [ ] **Step 6: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(ai): the triage worker: one finding per message, partial batch responses, the explain span linked to the upload" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Automatic triage from the analyze worker

**Files:**
- Create: `backend/src/nettriage/adapters/triage_queue.py`
- Modify: `backend/src/nettriage/adapters/analysis_store.py`, `backend/src/nettriage/entrypoints/analyze/worker.py`, `backend/src/nettriage/entrypoints/analyze/wiring.py`, `backend/src/nettriage/platform/config.py`
- Test: `backend/tests/unit/adapters/test_triage_queue.py`, `backend/tests/integration/test_analysis_store.py`, `backend/tests/worker/test_analyze_worker.py`, `backend/tests/unit/analyze/test_worker_wiring.py`

**Interfaces:**
- Consumes:
  - Task 1's SELECT grant for `app_analyze`;
  - Plan 4b's `Worker`, `claim_upload`, `store_analysis`, `UploadKey` and `current_traceparent`;
  - moto's SQS (Task 2's `moto[sqs]`).
- Produces:
  - in `nettriage.adapters.triage_queue`: `TriageQueue(client, url)` with `.send(org_id, finding_ids, traceparent)`, and `TriageQueueError`;
  - in `nettriage.adapters.analysis_store`: `AUTO_TRIAGE_LIMIT = 20` and `findings_to_explain(engine, key, limit=AUTO_TRIAGE_LIMIT) -> list[UUID]`;
  - `Worker(…, triage=TriageQueue, …)`, a new required field after `objects`;
  - `Settings.triage_queue_url`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/unit/adapters/test_triage_queue.py`:
```python
"""The triage queue (spec §4.2): one message per finding to explain, sent ten at a time, with
the trace it continues."""

import json
from collections.abc import Iterator
from typing import Any
from uuid import uuid7

import boto3
import pytest
from botocore.stub import Stubber
from moto import mock_aws

from nettriage.adapters.triage_queue import TriageQueue, TriageQueueError

TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


@pytest.fixture
def sqs() -> Iterator[Any]:
    with mock_aws():
        yield boto3.client("sqs", region_name="eu-north-1")


def received(sqs: Any, url: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    while batch := sqs.receive_message(
        QueueUrl=url, MaxNumberOfMessages=10, MessageAttributeNames=["All"]
    ).get("Messages"):
        messages += batch
        for message in batch:
            sqs.delete_message(QueueUrl=url, ReceiptHandle=message["ReceiptHandle"])
    return messages


def test_each_finding_becomes_one_message_with_the_trace(sqs: Any) -> None:
    url = sqs.create_queue(QueueName="nettriage-test-triage")["QueueUrl"]
    org, findings = uuid7(), [uuid7() for _ in range(23)]

    TriageQueue(sqs, url).send(org, findings, TRACEPARENT)

    messages = received(sqs, url)
    assert sorted(json.loads(message["Body"])["finding_id"] for message in messages) == sorted(
        str(finding) for finding in findings
    )
    assert {json.loads(message["Body"])["org_id"] for message in messages} == {str(org)}
    assert {message["MessageAttributes"]["traceparent"]["StringValue"] for message in messages} == {
        TRACEPARENT
    }


def test_without_a_trace_the_message_carries_no_attributes(sqs: Any) -> None:
    url = sqs.create_queue(QueueName="nettriage-test-triage")["QueueUrl"]

    TriageQueue(sqs, url).send(uuid7(), [uuid7()], None)

    [message] = received(sqs, url)
    assert "MessageAttributes" not in message


def test_nothing_to_explain_sends_nothing() -> None:
    client = boto3.client("sqs", region_name="eu-north-1")
    with Stubber(client):
        TriageQueue(client, "https://sqs.eu-north-1.amazonaws.com/1/q").send(uuid7(), [], None)


def test_a_message_sqs_refuses_fails_the_send() -> None:
    client = boto3.client("sqs", region_name="eu-north-1")
    url = "https://sqs.eu-north-1.amazonaws.com/123456789012/nettriage-test-triage"
    with Stubber(client) as stubber:
        stubber.add_response(
            "send_message_batch",
            {
                "Successful": [],
                "Failed": [{"Id": "0", "SenderFault": False, "Code": "InternalError"}],
            },
        )
        with pytest.raises(TriageQueueError, match="1 of 1"):
            TriageQueue(client, url).send(uuid7(), [uuid7()], None)
```

In `backend/tests/integration/test_analysis_store.py`, replace:
```python
from tenantdata import add_tenant, add_upload

from nettriage.adapters.analysis_store import (
    ClaimedUpload,
    claim_upload,
    fail_upload,
    store_analysis,
)
from nettriage.adapters.findings import FindingFilters, list_findings
```
with:
```python
from tenantdata import add_tenant, add_upload

from nettriage.adapters.analysis_store import (
    AUTO_TRIAGE_LIMIT,
    ClaimedUpload,
    claim_upload,
    fail_upload,
    findings_to_explain,
    store_analysis,
)
from nettriage.adapters.findings import FindingFilters, list_findings
```

In `backend/tests/integration/test_analysis_store.py`, replace:
```python
        ("medium", "203.0.113.9"),
    ]

```
with:
```python
        ("medium", "203.0.113.9"),
    ]


def analyzed_with_two_findings(database: Database) -> UploadKey:
    key = pending(database)
    claimed = claim_upload(database.app_analyze, key)
    assert claimed is not None
    both = port_scan() + port_scan(source="10.0.0.9")
    store_analysis(database.app_analyze, claimed, analyze_flow_log(io.BytesIO(both)), NOW)
    return key


def severities(database: Database, ids: list[UUID]) -> list[str]:
    with database.admin.begin() as connection:
        found: dict[UUID, str] = dict(
            connection.execute(
                text("SELECT id, severity FROM findings WHERE id = ANY(:ids)"), {"ids": ids}
            ).all()
        )
    return [found[finding] for finding in ids]


def test_an_uploads_findings_are_explained_most_severe_first(database: Database) -> None:
    key = analyzed_with_two_findings(database)

    explain = findings_to_explain(database.app_analyze, key)

    assert severities(database, explain) == ["high", "medium"]


def test_at_most_twenty_findings_of_an_upload_are_explained(database: Database) -> None:
    key = analyzed_with_two_findings(database)

    assert AUTO_TRIAGE_LIMIT == 20
    assert severities(database, findings_to_explain(database.app_analyze, key, limit=1)) == ["high"]


def test_an_upload_that_wasnt_analyzed_has_nothing_to_explain(database: Database) -> None:
    waiting = pending(database)
    failed = pending(database)
    claimed = claim_upload(database.app_analyze, failed)
    assert claimed is not None
    fail_upload(database.app_analyze, claimed, "Not a flow log.", NOW)

    assert findings_to_explain(database.app_analyze, waiting) == []
    assert findings_to_explain(database.app_analyze, failed) == []

```

In `backend/tests/worker/test_analyze_worker.py`, replace:
```python
from collections.abc import Iterator
from dataclasses import dataclass, replace
from urllib.parse import quote_plus
from uuid import UUID
```
with:
```python
from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import quote_plus
from uuid import UUID
```

In `backend/tests/worker/test_analyze_worker.py`, replace:
```python
from nettriage.adapters.analysis_store import ClaimedUpload, claim_upload
from nettriage.adapters.postgres import create_database_engine
from nettriage.adapters.upload_objects import UploadObjects
from nettriage.application.analysis import UploadKey
```
with:
```python
from nettriage.adapters.analysis_store import ClaimedUpload, claim_upload
from nettriage.adapters.postgres import create_database_engine
from nettriage.adapters.triage_queue import TriageQueue
from nettriage.adapters.upload_objects import UploadObjects
from nettriage.application.analysis import UploadKey
```

In `backend/tests/worker/test_analyze_worker.py`, replace:
```python
    worker: Worker
    s3: object
    spans: InMemorySpanExporter
    metrics: InMemoryMetricReader
```
with:
```python
    worker: Worker
    s3: object
    sqs: Any
    queue_url: str
    spans: InMemorySpanExporter
    metrics: InMemoryMetricReader
```

In `backend/tests/worker/test_analyze_worker.py`, replace:
```python
        tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
        reader = InMemoryMetricReader()
        worker = Worker(
            database=database.app_analyze,
            objects=UploadObjects(s3, BUCKET),
            clock=clock,
            metrics=AnalyzeMetrics(MeterProvider(metric_readers=[reader])),
```
with:
```python
        tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
        reader = InMemoryMetricReader()
        sqs = boto3.client("sqs", region_name="eu-north-1")
        queue_url = sqs.create_queue(QueueName="nettriage-test-triage")["QueueUrl"]
        worker = Worker(
            database=database.app_analyze,
            objects=UploadObjects(s3, BUCKET),
            triage=TriageQueue(sqs, queue_url),
            clock=clock,
            metrics=AnalyzeMetrics(MeterProvider(metric_readers=[reader])),
```

In `backend/tests/worker/test_analyze_worker.py`, replace:
```python
            sleep=lambda seconds: None,
        )
        yield Rig(worker=worker, s3=s3, spans=spans, metrics=reader)


```
with:
```python
            sleep=lambda seconds: None,
        )
        yield Rig(worker=worker, s3=s3, sqs=sqs, queue_url=queue_url, spans=spans, metrics=reader)


```

In `backend/tests/worker/test_analyze_worker.py`, replace:
```python
    assert upload_of(database, key) == ("processing", None, 0)

```
with:
```python
    assert upload_of(database, key) == ("processing", None, 0)


def queued(rig: Rig) -> list[dict[str, Any]]:
    """The triage messages waiting in the queue, taken off it."""
    messages: list[dict[str, Any]] = []
    while batch := rig.sqs.receive_message(
        QueueUrl=rig.queue_url, MaxNumberOfMessages=10, MessageAttributeNames=["All"]
    ).get("Messages"):
        messages += batch
        for found in batch:
            rig.sqs.delete_message(QueueUrl=rig.queue_url, ReceiptHandle=found["ReceiptHandle"])
    return messages


def finding_ids(database: Database, key: str) -> list[str]:
    with database.admin.begin() as connection:
        found: list[UUID] = list(
            connection.execute(
                text(
                    "SELECT f.id FROM findings f JOIN uploads u ON u.id = f.upload_id "
                    "WHERE u.s3_key = :key"
                ),
                {"key": key},
            ).scalars()
        )
    return [str(finding) for finding in found]


def test_an_analyzed_uploads_findings_are_queued_for_ai_with_the_workers_trace(
    database: Database, rig: Rig
) -> None:
    key = uploaded(database, rig, port_scan(), traceparent=TRACEPARENT)

    rig.worker.handle_message(message(key))

    [sent] = queued(rig)
    assert json.loads(sent["Body"])["finding_id"] == finding_ids(database, key)[0]
    upload_span = next(s for s in rig.spans.get_finished_spans() if s.name == "analyze.upload")
    traceparent = sent["MessageAttributes"]["traceparent"]["StringValue"]
    assert traceparent.split("-")[1] == format(upload_span.context.trace_id, "032x")


def test_a_second_delivery_queues_the_findings_again(database: Database, rig: Rig) -> None:
    key = uploaded(database, rig, port_scan())
    rig.worker.handle_message(message(key))

    again = rig.worker.process(key)

    assert again == "ignored"
    bodies = [json.loads(sent["Body"])["finding_id"] for sent in queued(rig)]
    assert bodies == finding_ids(database, key) * 2


def test_a_failed_send_fails_the_message_and_the_next_delivery_queues(
    database: Database, rig: Rig
) -> None:
    key = uploaded(database, rig, port_scan())
    unreachable = TriageQueue(rig.sqs, rig.queue_url.replace("triage", "missing"))
    broken = replace(rig.worker, triage=unreachable)

    with pytest.raises(AnalysisFailed):
        broken.handle_message(message(key))
    rig.worker.handle_message(message(key, receive_count=2))

    assert upload_of(database, key) == ("analyzed", None, 1)
    assert len(queued(rig)) == 1


def test_a_file_that_fails_queues_nothing(database: Database, rig: Rig) -> None:
    key = uploaded(database, rig, b"this is not a flow log\n")

    rig.worker.handle_message(message(key))

    assert queued(rig) == []

```

In `backend/tests/unit/analyze/test_worker_wiring.py`, replace:
```python
"""Building the analyze worker from SSM (spec §6.8): it gets the worker role's database URL and
the uploads bucket, never a value in its environment."""

```
with:
```python
"""Building the analyze worker from SSM (spec §6.8): it gets the worker role's database URL, the
uploads bucket and the triage queue, never a value in its environment."""

```

In `backend/tests/unit/analyze/test_worker_wiring.py`, replace:
```python
    uploads_bucket="nettriage-dev-uploads-12345678",
)
```
with:
```python
    uploads_bucket="nettriage-dev-uploads-12345678",
    triage_queue_url="https://sqs.eu-north-1.amazonaws.com/123456789012/nettriage-dev-triage",
)
```

In `backend/tests/unit/analyze/test_worker_wiring.py`, replace:
```python
    assert worker.database.pool.size() == 1  # type: ignore[attr-defined]
    worker.flush()
```
with:
```python
    assert worker.database.pool.size() == 1  # type: ignore[attr-defined]
    assert worker.triage.url.endswith("/nettriage-dev-triage")
    worker.flush()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/unit/adapters/test_triage_queue.py tests/integration/test_analysis_store.py tests/worker/test_analyze_worker.py tests/unit/analyze`
Expected: FAIL. Collection stops with 4 errors:
- `No module named 'nettriage.adapters.triage_queue'`, twice;
- `cannot import name 'AUTO_TRIAGE_LIMIT' from 'nettriage.adapters.analysis_store'`;
- a `ValidationError` for `Settings`, because `triage_queue_url` isn't a setting yet.

- [ ] **Step 3: Write the triage queue and the ranking**

`backend/src/nettriage/adapters/triage_queue.py`:
```python
"""The triage queue (spec §4.2): the analyze worker sends one message per finding to explain,
`{"org_id": …, "finding_id": …}`, with the W3C traceparent of its own span as a message
attribute, so the triage worker's span links back to the upload (spec §9.1)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from types_boto3_sqs.client import SQSClient

# SendMessageBatch takes at most 10 messages.
_BATCH = 10


class TriageQueueError(Exception):
    """SQS refused some of the messages. The analyze message fails, and its next delivery queues
    the findings again (explaining a finding twice costs nothing: the analysis is cached)."""


@dataclass(frozen=True)
class TriageQueue:
    client: SQSClient
    url: str

    def send(self, org_id: UUID, finding_ids: Sequence[UUID], traceparent: str | None) -> None:
        attributes: dict[str, Any] = (
            {"traceparent": {"DataType": "String", "StringValue": traceparent}}
            if traceparent
            else {}
        )
        for start in range(0, len(finding_ids), _BATCH):
            batch = finding_ids[start : start + _BATCH]
            entries: list[Any] = [
                {
                    "Id": str(number),
                    "MessageBody": json.dumps(
                        {"org_id": str(org_id), "finding_id": str(finding_id)}
                    ),
                }
                | ({"MessageAttributes": attributes} if attributes else {})
                for number, finding_id in enumerate(batch)
            ]
            response = self.client.send_message_batch(QueueUrl=self.url, Entries=entries)
            if response.get("Failed"):
                raise TriageQueueError(f"{len(response['Failed'])} of {len(batch)} not queued")
```

In `backend/src/nettriage/adapters/analysis_store.py`, replace:
```python
from nettriage.domain.detection.model import Finding

```
with:
```python
from nettriage.domain.detection.model import Finding

# Automatic AI triage: up to 20 findings per upload, highest severity first (spec §5.7).
AUTO_TRIAGE_LIMIT = 20

```

In `backend/src/nettriage/adapters/analysis_store.py`, replace:
```python
    return True

```
with:
```python
    return True


def findings_to_explain(
    engine: Engine, key: UploadKey, limit: int = AUTO_TRIAGE_LIMIT
) -> list[UUID]:
    """An analyzed upload's findings for automatic AI triage: the most severe first, in the
    analysis's order within a severity (it inserted them last first). Empty for an upload that
    isn't analyzed."""
    with tenant_transaction(engine, org_id=key.org_id) as connection:
        found: list[UUID] = list(
            connection.execute(
                text(
                    "SELECT f.id FROM findings f JOIN uploads u "
                    "ON u.org_id = f.org_id AND u.id = f.upload_id "
                    "WHERE u.org_id = :org AND u.id = :id AND u.s3_key = :key "
                    "AND u.status = 'analyzed' ORDER BY CASE f.severity "
                    "WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, "
                    "f.id DESC LIMIT :limit"
                ),
                {"org": key.org_id, "id": key.upload_id, "key": key.key, "limit": limit},
            ).scalars()
        )
    return found

```

- [ ] **Step 4: Queue the findings from the analyze worker**

In `backend/src/nettriage/entrypoints/analyze/worker.py`, replace:
```python
can't finish is failed, so it doesn't wait forever.

```
with:
```python
can't finish is failed, so it doesn't wait forever.

An analyzed upload's most severe findings (up to 20) go to the triage queue for an AI
explanation (spec §4.2). A delivery that finds the upload already analyzed queues them again,
in case a crash or a failed send lost them: the explanations are cached, so it costs nothing.

```

In `backend/src/nettriage/entrypoints/analyze/worker.py`, replace:
```python
    fail_upload,
    give_up_upload,
```
with:
```python
    fail_upload,
    findings_to_explain,
    give_up_upload,
```

In `backend/src/nettriage/entrypoints/analyze/worker.py`, replace:
```python
)
from nettriage.adapters.upload_objects import UploadObjects
from nettriage.application.analysis import analyze_parsed, parse_upload_key
from nettriage.application.clock import Clock
```
with:
```python
)
from nettriage.adapters.triage_queue import TriageQueue
from nettriage.adapters.upload_objects import UploadObjects
from nettriage.application.analysis import UploadKey, analyze_parsed, parse_upload_key
from nettriage.application.clock import Clock
```

In `backend/src/nettriage/entrypoints/analyze/worker.py`, replace:
```python
from nettriage.platform.metrics import AnalyzeMetrics
from nettriage.platform.trace_context import links_from

```
with:
```python
from nettriage.platform.metrics import AnalyzeMetrics
from nettriage.platform.trace_context import current_traceparent, links_from

```

In `backend/src/nettriage/entrypoints/analyze/worker.py`, replace:
```python
    objects: UploadObjects
    clock: Clock
```
with:
```python
    objects: UploadObjects
    triage: TriageQueue
    clock: Clock
```

In `backend/src/nettriage/entrypoints/analyze/worker.py`, replace:
```python
            logger.info("upload_object_ignored", extra={"reason": "not_pending"})
            return "ignored"
```
with:
```python
            logger.info("upload_object_ignored", extra={"reason": "not_pending"})
            self._queue_for_ai(upload_key)
            return "ignored"
```

In `backend/src/nettriage/entrypoints/analyze/worker.py`, replace:
```python
            with self._span("analyze.upload", links=links_from(upload.traceparent)):
                return self._analyze(claimed, upload.size, upload.body)
        finally:
```
with:
```python
            with self._span("analyze.upload", links=links_from(upload.traceparent)):
                outcome = self._analyze(claimed, upload.size, upload.body)
                if outcome in ("analyzed", "duplicate"):
                    self._queue_for_ai(upload_key)
                return outcome
        finally:
```

In `backend/src/nettriage/entrypoints/analyze/worker.py`, replace:
```python
        return "analyzed"

```
with:
```python
        return "analyzed"

    def _queue_for_ai(self, key: UploadKey) -> None:
        """Queue an analyzed upload's most severe findings for an AI explanation, with this span
        as the trace the triage worker continues. Nothing for an upload that isn't analyzed."""
        findings = findings_to_explain(self.database, key)
        if findings:
            self.triage.send(key.org_id, findings, current_traceparent())
            logger.info("findings_queued_for_ai", extra={"findings": len(findings)})

```

In `backend/src/nettriage/entrypoints/analyze/wiring.py`, replace:
```python
"""Building the analyze worker in Lambda, once per cold start (spec §6.8): its database URL
comes from SSM, and its telemetry goes to the providers the handler set up (spec §9.1)."""

```
with:
```python
"""Building the analyze worker in Lambda, once per cold start (spec §6.8): its database URL
comes from SSM, findings to explain go to the triage queue, and its telemetry goes to the
providers the handler set up (spec §9.1)."""

```

In `backend/src/nettriage/entrypoints/analyze/wiring.py`, replace:
```python
from nettriage.adapters.postgres import create_database_engine
from nettriage.adapters.upload_objects import UploadObjects
```
with:
```python
from nettriage.adapters.postgres import create_database_engine
from nettriage.adapters.triage_queue import TriageQueue
from nettriage.adapters.upload_objects import UploadObjects
```

In `backend/src/nettriage/entrypoints/analyze/wiring.py`, replace:
```python
        objects=UploadObjects(session.client("s3", config=AWS_CONFIG), settings.uploads_bucket),
        clock=system_clock,
```
with:
```python
        objects=UploadObjects(session.client("s3", config=AWS_CONFIG), settings.uploads_bucket),
        triage=TriageQueue(session.client("sqs", config=AWS_CONFIG), settings.triage_queue_url),
        clock=system_clock,
```

In `backend/src/nettriage/platform/config.py`, replace:
```python
    bedrock_model_id: str = ""

```
with:
```python
    bedrock_model_id: str = ""
    # Where the analyze worker queues findings for an AI explanation (spec §4.2).
    triage_queue_url: str = ""

```

- [ ] **Step 5: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `991 passed, 1 skipped`.

- [ ] **Step 6: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(ai): queue each analyzed upload's 20 most severe findings for an AI explanation, again on a redelivery" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The latest AI analysis in a finding's details

**Files:**
- Modify: `backend/src/nettriage/adapters/findings.py`, `backend/src/nettriage/entrypoints/api/finding_schemas.py`
- Test: `backend/tests/api/test_finding_routes.py`

**Interfaces:**
- Consumes: Plan 5a's `ai_analyses` and `app_api`'s SELECT on it; Plan 4b's `read_finding` and `FindingOut`.
- Produces:
  - `nettriage.adapters.findings.AiAnalysis`, and `FindingDetail.ai_analysis: AiAnalysis | None`;
  - `AiAnalysisOut`, and `FindingOut.ai_analysis`.
  Triage's `PATCH` returns the same detail, so it carries `ai_analysis` too.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/api/test_finding_routes.py`, replace:
```python
filters, one finding with its evidence, techniques and history, and the techniques themselves
with MITRE's notice."""

from uuid import UUID

import pytest
from browser import signed_in_as
```
with:
```python
filters, one finding with its evidence, techniques and history, and the techniques themselves
with MITRE's notice."""

from uuid import UUID, uuid7

import pytest
from browser import signed_in_as
```

In `backend/tests/api/test_finding_routes.py`, replace:
```python
        "The ATT&CK reference is unavailable right now; try again shortly."
    )

```
with:
```python
        "The ATT&CK reference is unavailable right now; try again shortly."
    )


def add_ai_analysis(
    database: Database,
    org_id: UUID,
    finding_id: str,
    *,
    status: str,
    at: str,
    output: str | None = None,
    error_code: str | None = None,
) -> str:
    """An analysis by gpt-oss-20b, last updated `at`."""
    analysis_id = uuid7()
    with database.admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ai_analyses (id, org_id, finding_id, status, provider, model_id, "
                "prompt_version, output_schema_version, input_hash, output, input_tokens, "
                "output_tokens, cost_usd, latency_ms, error_code, created_at, updated_at) "
                "VALUES (:id, :org, :finding, :status, 'aws.bedrock', 'openai.gpt-oss-20b-1:0', "
                "'v1', 'v1', :hash, CAST(:output AS jsonb), 1800, 320, 0.000222, 1450, :error, "
                ":at, :at)"
            ),
            {
                "id": analysis_id,
                "org": org_id,
                "finding": finding_id,
                "status": status,
                "hash": analysis_id.hex * 2,
                "output": output,
                "error": error_code,
                "at": at,
            },
        )
    return str(analysis_id)


def test_a_finding_without_an_ai_analysis_says_so(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    finding_id = add(database, org)

    response = signed_in.get(f"/api/v1/orgs/{org[0]}/findings/{finding_id}")

    assert response.json()["ai_analysis"] is None


def test_a_finding_shows_its_latest_ai_analysis_with_what_it_said_and_cost(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    finding_id = add(database, org)
    add_ai_analysis(
        database,
        org[0],
        finding_id,
        status="failed",
        at="2026-09-28T09:00:00Z",
        error_code="provider_throttled",
    )
    latest = add_ai_analysis(
        database,
        org[0],
        finding_id,
        status="succeeded",
        at="2026-09-28T10:00:00Z",
        output='{"summary": "A scan from outside."}',
    )

    response = signed_in.get(f"/api/v1/orgs/{org[0]}/findings/{finding_id}")

    assert response.json()["ai_analysis"] == {
        "id": latest,
        "status": "succeeded",
        "provider": "aws.bedrock",
        "model_id": "openai.gpt-oss-20b-1:0",
        "prompt_version": "v1",
        "output_schema_version": "v1",
        "output": {"summary": "A scan from outside."},
        "error_code": None,
        "input_tokens": 1800,
        "output_tokens": 320,
        "cost_usd": "0.000222",
        "latency_ms": 1450,
        "created_at": "2026-09-28T10:00:00Z",
        "updated_at": "2026-09-28T10:00:00Z",
    }

```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/api/test_finding_routes.py`
Expected: `2 failed, 15 passed`, both with `KeyError: 'ai_analysis'`.

- [ ] **Step 3: Read the latest analysis with the finding**

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
from datetime import datetime
from typing import Any, Literal
```
with:
```python
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
```

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
@dataclass(frozen=True)
class FindingDetail:
```
with:
```python
@dataclass(frozen=True)
class AiAnalysis:
    """The finding's latest AI analysis (spec §7): what the model said, when it succeeded, or
    why there's no explanation (`error_code`), and what it cost."""

    id: UUID
    status: str
    provider: str
    model_id: str
    prompt_version: str
    output_schema_version: str
    output: dict[str, Any] | None
    error_code: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Decimal | None
    latency_ms: int | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class FindingDetail:
```

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
    events_total: int

```
with:
```python
    events_total: int
    ai_analysis: AiAnalysis | None

```

In `backend/src/nettriage/adapters/findings.py`, replace:
```python
        events_total=_event_count(connection, finding_id),
    )

```
with:
```python
        events_total=_event_count(connection, finding_id),
        ai_analysis=_latest_analysis(connection, finding_id),
    )


def _latest_analysis(connection: Connection, finding_id: UUID) -> AiAnalysis | None:
    row = connection.execute(
        text(
            "SELECT id, status, provider, model_id, prompt_version, output_schema_version, "
            "output, error_code, input_tokens, output_tokens, cost_usd, latency_ms, created_at, "
            "updated_at FROM ai_analyses WHERE finding_id = :id "
            "ORDER BY updated_at DESC, id DESC LIMIT 1"
        ),
        {"id": finding_id},
    ).one_or_none()
    return None if row is None else AiAnalysis(**row._mapping)

```

In `backend/src/nettriage/entrypoints/api/finding_schemas.py`, replace:
```python
from datetime import datetime
from typing import Any
```
with:
```python
from datetime import datetime
from decimal import Decimal
from typing import Any
```

In `backend/src/nettriage/entrypoints/api/finding_schemas.py`, replace:
```python
from nettriage.adapters.findings import (
    Evidence,
```
with:
```python
from nettriage.adapters.findings import (
    AiAnalysis,
    Evidence,
```

In `backend/src/nettriage/entrypoints/api/finding_schemas.py`, replace:
```python

class FindingOut(FindingSummaryOut):
```
with:
```python

class AiAnalysisOut(BaseModel):
    """AI-generated: the UI labels it so, with the model and prompt version (spec §8.4)."""

    id: UUID
    status: str
    provider: str
    model_id: str
    prompt_version: str
    output_schema_version: str
    output: dict[str, Any] | None
    error_code: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Decimal | None
    latency_ms: int | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, analysis: AiAnalysis) -> AiAnalysisOut:
        return cls(**vars(analysis))


class FindingOut(FindingSummaryOut):
```

In `backend/src/nettriage/entrypoints/api/finding_schemas.py`, replace:
```python
    events_total: int

```
with:
```python
    events_total: int
    # The latest AI analysis, or null when the finding hasn't been explained (Plan 5b).
    ai_analysis: AiAnalysisOut | None

```

In `backend/src/nettriage/entrypoints/api/finding_schemas.py`, replace:
```python
            events_total=detail.events_total,
        )
```
with:
```python
            events_total=detail.events_total,
            ai_analysis=None
            if detail.ai_analysis is None
            else AiAnalysisOut.of(detail.ai_analysis),
        )
```

- [ ] **Step 4: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `993 passed, 1 skipped`.

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests/api/test_finding_routes.py
git commit -m "feat(api): a finding's details carry its latest AI analysis" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: The triage infrastructure and the $5 Bedrock cutoff

**Files:**
- Create: `infra/modules/pipeline/triage.tf`, `infra/bootstrap/bedrock_cutoff.tf`
- Modify:
  - `infra/modules/pipeline/analyze.tf`, `variables.tf` and `outputs.tf`;
  - `infra/envs/dev/main.tf`;
  - `infra/bootstrap/cost.tf` and `variables.tf`;
  - `.checkov.yaml`
- Test: `infra/modules/pipeline/tests/pipeline.tftest.hcl`, `infra/bootstrap/tests/bootstrap.tftest.hcl`

**Interfaces:**
- Consumes: the data module's `table_name` and `table_arn`; the pipeline module's analyze worker, `local.name` and `data.aws_region.current`; the bootstrap's `aws_budgets_budget.monthly`, `local.account_id` and `var.budget_email`.
- Produces:
  - the `nettriage-<stage>-triage` queue, its DLQ, role and function, and its event source mapping;
  - the `/nettriage/<stage>/kill/ai-enabled` parameter, also an output, `ai_enabled_parameter`;
  - the analyze function's `NETTRIAGE_TRIAGE_QUEUE_URL`;
  - the pipeline module's variables `triage_database_url_parameter`, `runtime_table_name`, `runtime_table_arn`, `bedrock_region` (default `eu-north-1`) and `bedrock_model_id` (default `openai.gpt-oss-20b-1:0`);
  - in the bootstrap: `cost_types { include_credit = false }`, the `nettriage-deny-bedrock` policy, the `nettriage-budget-action` role, and `aws_budgets_budget_action.deny_bedrock`, with `var.triage_roles` (default `["nettriage-dev-triage"]`).

- [ ] **Step 1: Write the failing tests**

In `infra/modules/pipeline/tests/pipeline.tftest.hcl`, replace:
```hcl
    arn = "arn:aws:sqs:eu-north-1:123456789012:nettriage-dev-analyze-dlq"
  }
}

variables {
  stage                    = "dev"
  app_origin               = "https://d111111abcdef8.cloudfront.net"
  lambda_zip_path          = "../app/tests/fixtures/app.zip"
```
with:
```hcl
    arn = "arn:aws:sqs:eu-north-1:123456789012:nettriage-dev-analyze-dlq"
  }
}

override_resource {
  target = aws_sqs_queue.triage
  values = {
    arn = "arn:aws:sqs:eu-north-1:123456789012:nettriage-dev-triage"
    id  = "https://sqs.eu-north-1.amazonaws.com/123456789012/nettriage-dev-triage"
  }
}

override_resource {
  target = aws_sqs_queue.triage_dlq
  values = {
    arn = "arn:aws:sqs:eu-north-1:123456789012:nettriage-dev-triage-dlq"
  }
}

variables {
  stage                    = "dev"
  app_origin               = "https://d111111abcdef8.cloudfront.net"
  lambda_zip_path          = "../app/tests/fixtures/app.zip"
```

In `infra/modules/pipeline/tests/pipeline.tftest.hcl`, replace:
```hcl
  otel_collector_layer_arn = "arn:aws:lambda:eu-north-1:184161586896:layer:opentelemetry-collector-arm64-0_22_0:1"
  grafana_otlp_endpoint    = "https://otlp-gateway.example.grafana.net/otlp"
  grafana_otlp_auth        = "dGVzdDp0ZXN0"
  database_url_parameter   = "/nettriage/dev/db/app-analyze-url"
}

run "the_bucket_name_does_not_expose_the_account_id" {
  command = apply
```
with:
```hcl
  otel_collector_layer_arn = "arn:aws:lambda:eu-north-1:184161586896:layer:opentelemetry-collector-arm64-0_22_0:1"
  grafana_otlp_endpoint    = "https://otlp-gateway.example.grafana.net/otlp"
  grafana_otlp_auth        = "dGVzdDp0ZXN0"
  database_url_parameter   = "/nettriage/dev/db/app-analyze-url"

  triage_database_url_parameter = "/nettriage/dev/db/app-triage-url"
  runtime_table_name            = "nettriage-dev-runtime"
  runtime_table_arn             = "arn:aws:dynamodb:eu-north-1:123456789012:table/nettriage-dev-runtime"
}

run "the_bucket_name_does_not_expose_the_account_id" {
  command = apply
```

In `infra/modules/pipeline/tests/pipeline.tftest.hcl`, replace:
```hcl
    error_message = "The worker may only read its own database URL (spec §6.8)."
  }
}

```
with:
```hcl
    error_message = "The worker may only read its own database URL (spec §6.8)."
  }
}

run "each_found_finding_can_reach_the_triage_queue" {
  command = apply

  assert {
    condition     = jsondecode(aws_iam_role_policy.analyze_triage_queue.policy).Statement[0].Action == "sqs:SendMessage" && jsondecode(aws_iam_role_policy.analyze_triage_queue.policy).Statement[0].Resource == "arn:aws:sqs:eu-north-1:123456789012:nettriage-dev-triage"
    error_message = "The analyze worker may only send to the triage queue (spec §4.2, §6.8)."
  }
  assert {
    condition     = aws_lambda_function.analyze.environment[0].variables["NETTRIAGE_TRIAGE_QUEUE_URL"] == "https://sqs.eu-north-1.amazonaws.com/123456789012/nettriage-dev-triage"
    error_message = "The analyze worker is told the triage queue's URL."
  }
}

run "the_triage_worker_explains_one_finding_at_a_time_within_4_minutes" {
  command = apply

  assert {
    condition     = aws_lambda_function.triage.function_name == "nettriage-dev-triage" && aws_lambda_function.triage.handler == "nettriage.entrypoints.triage.handler.handle"
    error_message = "The triage worker is nettriage-<stage>-triage, with the triage entry point."
  }
  assert {
    condition     = aws_lambda_function.triage.memory_size == 512 && aws_lambda_function.triage.timeout == 240
    error_message = "The triage worker uses 512 MB and 240 s: an answer and its repair, each with 3 retries of 20 s (the owner's decision, Plan 5b)."
  }
  assert {
    condition     = aws_lambda_event_source_mapping.triage.batch_size == 1 && one(aws_lambda_event_source_mapping.triage.scaling_config).maximum_concurrency == 2
    error_message = "One finding per run, at most 2 runs at once (Plan 5b)."
  }
  assert {
    condition     = aws_lambda_event_source_mapping.triage.function_response_types == toset(["ReportBatchItemFailures"])
    error_message = "The worker hands failed messages back with a partial batch response (spec §3.5)."
  }
  assert {
    condition     = aws_sqs_queue.triage.visibility_timeout_seconds == 6 * aws_lambda_function.triage.timeout
    error_message = "The visibility timeout is 6 times the worker's timeout (spec §3.5)."
  }
  assert {
    condition     = jsondecode(aws_sqs_queue.triage.redrive_policy).maxReceiveCount == 3 && jsondecode(aws_sqs_queue.triage.redrive_policy).deadLetterTargetArn == "arn:aws:sqs:eu-north-1:123456789012:nettriage-dev-triage-dlq"
    error_message = "After 3 receives a message moves to the triage DLQ (spec §8.6)."
  }
  assert {
    condition     = aws_sqs_queue.triage.sqs_managed_sse_enabled && aws_sqs_queue.triage_dlq.sqs_managed_sse_enabled && aws_sqs_queue.triage_dlq.message_retention_seconds == 1209600
    error_message = "Both queues are encrypted, and dead letters are kept 14 days."
  }
}

run "the_triage_worker_is_told_its_model_switch_and_budgets" {
  command = apply

  assert {
    condition = alltrue([
      aws_lambda_function.triage.environment[0].variables["NETTRIAGE_SERVICE_NAME"] == "nettriage-triage",
      aws_lambda_function.triage.environment[0].variables["NETTRIAGE_DATABASE_URL_PARAMETER"] == "/nettriage/dev/db/app-triage-url",
      aws_lambda_function.triage.environment[0].variables["NETTRIAGE_RUNTIME_TABLE"] == "nettriage-dev-runtime",
      aws_lambda_function.triage.environment[0].variables["NETTRIAGE_AI_ENABLED_PARAMETER"] == "/nettriage/dev/kill/ai-enabled",
      aws_lambda_function.triage.environment[0].variables["NETTRIAGE_BEDROCK_REGION"] == "eu-north-1",
      aws_lambda_function.triage.environment[0].variables["NETTRIAGE_BEDROCK_MODEL_ID"] == "openai.gpt-oss-20b-1:0",
    ])
    error_message = "The worker connects as app_triage, budgets in the runtime table, reads the AI switch, and calls gpt-oss-20b in Stockholm (Plan 5b)."
  }
  assert {
    condition     = aws_lambda_function.triage.environment[0].variables["OTEL_SEMCONV_STABILITY_OPT_IN"] == "gen_ai_latest_experimental" && aws_lambda_function.triage.environment[0].variables["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] == "false"
    error_message = "GenAI telemetry follows the latest conventions and never captures prompts or answers (spec §9.1, §9.3)."
  }
  assert {
    condition     = aws_ssm_parameter.ai_enabled.name == "/nettriage/dev/kill/ai-enabled" && aws_ssm_parameter.ai_enabled.value == "true"
    error_message = "The AI kill switch starts on (spec §9.7)."
  }
  assert {
    condition     = output.ai_enabled_parameter == "/nettriage/dev/kill/ai-enabled"
    error_message = "The switch's name is an output, for the deploy's pause-ai and resume-ai."
  }
}

run "the_triage_worker_may_call_only_its_model_and_touch_only_budgets" {
  command = apply

  assert {
    condition     = jsondecode(aws_iam_role_policy.triage_bedrock.policy).Statement[0].Action == "bedrock:InvokeModel" && jsondecode(aws_iam_role_policy.triage_bedrock.policy).Statement[0].Resource == "arn:aws:bedrock:eu-north-1::foundation-model/openai.gpt-oss-20b-1:0"
    error_message = "The worker may invoke its one model, on demand in its Region (spec Revision 2, R4)."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.triage_budgets.policy).Statement[0].Action == "dynamodb:UpdateItem" && jsondecode(aws_iam_role_policy.triage_budgets.policy).Statement[0].Resource == "arn:aws:dynamodb:eu-north-1:123456789012:table/nettriage-dev-runtime"
    error_message = "The worker may only update items in the runtime table."
  }
  assert {
    condition     = toset(jsondecode(aws_iam_role_policy.triage_budgets.policy).Statement[0].Condition["ForAllValues:StringLike"]["dynamodb:LeadingKeys"]) == toset(["BUDGET#*", "GBUDGET#*"])
    error_message = "The worker may only touch budget items, never sessions or rate limits (spec §6.8)."
  }
  assert {
    condition     = toset(jsondecode(aws_iam_role_policy.triage_parameters.policy).Statement[0].Resource) == toset(["arn:aws:ssm:eu-north-1:123456789012:parameter/nettriage/dev/db/app-triage-url", "arn:aws:ssm:eu-north-1:123456789012:parameter/nettriage/dev/kill/ai-enabled"])
    error_message = "The worker may read its database URL and the AI switch, nothing else (spec §6.8)."
  }
  assert {
    condition     = toset(jsondecode(aws_iam_role_policy.triage_queue.policy).Statement[0].Action) == toset(["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"]) && jsondecode(aws_iam_role_policy.triage_queue.policy).Statement[0].Resource == "arn:aws:sqs:eu-north-1:123456789012:nettriage-dev-triage"
    error_message = "The worker may only consume the triage queue."
  }
}

```

In `infra/bootstrap/tests/bootstrap.tftest.hcl`, replace:
```hcl
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
}

```
with:
```hcl
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
  # Computed ARNs are short random strings by default; the budget action checks the ones it is
  # given look like ARNs.
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/nettriage-budget-action"
    }
  }
  mock_resource "aws_iam_policy" {
    defaults = {
      arn = "arn:aws:iam::123456789012:policy/nettriage-deny-bedrock"
    }
  }
}

```

In `infra/bootstrap/tests/bootstrap.tftest.hcl`, replace:
```hcl
    error_message = "Expected alerts at $1 and $3, actual and forecast (spec §6.7)."
  }
}

```
with:
```hcl
    error_message = "Expected alerts at $1 and $3, actual and forecast (spec §6.7)."
  }
}

run "the_budget_counts_usage_before_credits" {
  command = apply

  assert {
    condition     = one(aws_budgets_budget.monthly.cost_types).include_credit == false
    error_message = "Credits would net the month's usage to $0, and no alert or action would ever fire on the Free plan (spec §6.7, Plan 5b)."
  }
}

run "at_five_dollars_bedrock_is_denied_to_the_triage_workers" {
  command = apply

  assert {
    condition     = aws_budgets_budget_action.deny_bedrock.budget_name == "nettriage-monthly" && aws_budgets_budget_action.deny_bedrock.notification_type == "ACTUAL"
    error_message = "The action watches the monthly budget's actual spend (spec §6.7)."
  }
  assert {
    condition     = one(aws_budgets_budget_action.deny_bedrock.action_threshold).action_threshold_type == "ABSOLUTE_VALUE" && one(aws_budgets_budget_action.deny_bedrock.action_threshold).action_threshold_value == 5
    error_message = "It acts at $5 (spec §6.7)."
  }
  assert {
    condition     = aws_budgets_budget_action.deny_bedrock.action_type == "APPLY_IAM_POLICY" && aws_budgets_budget_action.deny_bedrock.approval_model == "AUTOMATIC"
    error_message = "It attaches a policy, without waiting for approval."
  }
  assert {
    condition     = one(one(aws_budgets_budget_action.deny_bedrock.definition).iam_action_definition).roles == toset(["nettriage-dev-triage"])
    error_message = "It denies the dev triage worker (prod's joins when prod exists)."
  }
  assert {
    condition     = jsondecode(aws_iam_policy.deny_bedrock.policy).Statement[0].Effect == "Deny" && jsondecode(aws_iam_policy.deny_bedrock.policy).Statement[0].Action == "bedrock:InvokeModel*"
    error_message = "The policy denies every Bedrock invocation (spec §6.7)."
  }
  assert {
    condition     = one(aws_budgets_budget_action.deny_bedrock.subscriber).address == "owner@example.com"
    error_message = "The owner is told when the action runs."
  }
}

run "budgets_may_attach_only_the_deny_policy_to_only_the_triage_workers" {
  command = apply

  assert {
    condition     = jsondecode(aws_iam_role.budget_action.assume_role_policy).Statement[0].Principal.Service == "budgets.amazonaws.com" && jsondecode(aws_iam_role.budget_action.assume_role_policy).Statement[0].Condition.StringEquals["aws:SourceAccount"] == "123456789012"
    error_message = "Only AWS Budgets, for this account, may assume the action's role."
  }
  assert {
    condition     = toset(jsondecode(aws_iam_role_policy.budget_action.policy).Statement[0].Action) == toset(["iam:AttachRolePolicy", "iam:DetachRolePolicy"])
    error_message = "The role may attach and detach role policies, nothing else."
  }
  assert {
    condition     = toset(jsondecode(aws_iam_role_policy.budget_action.policy).Statement[0].Resource) == toset(["arn:aws:iam::123456789012:role/nettriage-dev-triage"])
    error_message = "Only to the triage workers' roles."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.budget_action.policy).Statement[0].Condition.ArnEquals["iam:PolicyARN"] == "arn:aws:iam::123456789012:policy/nettriage-deny-bedrock"
    error_message = "And only the deny-Bedrock policy."
  }
}

```

- [ ] **Step 2: Run them and watch them fail**

Run: `just tf-check`
Expected: The bootstrap's tests fail first, and the check stops there. You see `Error: Attempt to get attribute from null value` (the budget has no `cost_types` yet), then `Failure! 3 passed, 1 failed, 2 skipped.`

- [ ] **Step 3: Add the triage queue, worker and switch**

`infra/modules/pipeline/triage.tf`:
```hcl
locals {
  triage = "${local.name}-triage"
  # One finding per run (the owner's decision, Plan 5b): an answer and its repair, each with
  # a 20 s timeout and 3 retries, fit in 240 s. SQS hides a message for 6 times as long.
  triage_timeout = 240
  triage_parameter_arns = [
    for name in [var.triage_database_url_parameter, aws_ssm_parameter.ai_enabled.name] :
    "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter${name}"
  ]
}

# The AI kill switch (spec §9.7). The triage worker re-reads it every minute: "true" lets it
# call the model, anything else skips the call (`ai_disabled`). Terraform only creates it, so a
# deploy never switches the AI back on after the owner paused it.
resource "aws_ssm_parameter" "ai_enabled" {
  #checkov:skip=CKV2_AWS_34:Not secret: an on/off flag.
  name  = "/nettriage/${var.stage}/kill/ai-enabled"
  type  = "String"
  value = "true"

  lifecycle {
    ignore_changes = [value]
  }
}

# One message per finding to explain (spec §4.2). A message the worker hands back three times
# moves to the dead-letter queue, kept 14 days for a look and a redrive (spec §8.6).
resource "aws_sqs_queue" "triage_dlq" {
  name                      = "${local.triage}-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "triage" {
  name                       = local.triage
  visibility_timeout_seconds = local.triage_timeout * 6
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.triage_dlq.arn
    maxReceiveCount     = 3
  })
}

# The analyze worker queues each analyzed upload's most severe findings.
resource "aws_iam_role_policy" "analyze_triage_queue" {
  name = "send-to-triage-queue"
  role = aws_iam_role.analyze.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sqs:SendMessage"
      Resource = aws_sqs_queue.triage.arn
    }]
  })
}

resource "aws_cloudwatch_log_group" "triage" {
  name              = "/aws/lambda/${local.triage}"
  retention_in_days = 7
}

# The $5 budget action in the bootstrap denies this role Bedrock (spec §6.7), by name.
resource "aws_iam_role" "triage" {
  name = local.triage
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "triage_logs" {
  name = "write-own-logs"
  role = aws_iam_role.triage.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.triage.arn}:*"
    }]
  })
}

resource "aws_iam_role_policy" "triage_queue" {
  name = "consume-triage-queue"
  role = aws_iam_role.triage.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes",
        "sqs:ChangeMessageVisibility",
      ]
      Resource = aws_sqs_queue.triage.arn
    }]
  })
}

# Its database URL (app_triage), which the deploy writes, and the AI kill switch.
resource "aws_iam_role_policy" "triage_parameters" {
  name = "read-own-parameters"
  role = aws_iam_role.triage.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "ssm:GetParameters"
      Resource = local.triage_parameter_arns
    }]
  })
}

# AI budgets live in the runtime table (spec §5.5); the worker may touch their items and no
# others: not sessions, sign-in states, rate limits or idempotency keys.
resource "aws_iam_role_policy" "triage_budgets" {
  name = "update-ai-budgets"
  role = aws_iam_role.triage.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "dynamodb:UpdateItem"
      Resource = var.runtime_table_arn
      Condition = {
        "ForAllValues:StringLike" = { "dynamodb:LeadingKeys" = ["BUDGET#*", "GBUDGET#*"] }
      }
    }]
  })
}

# One model, on demand in its own Region: the account can't use cross-Region inference
# profiles (spec Revision 2, R4). Converse needs bedrock:InvokeModel.
resource "aws_iam_role_policy" "triage_bedrock" {
  name = "invoke-triage-model"
  role = aws_iam_role.triage.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "bedrock:InvokeModel"
      Resource = "arn:aws:bedrock:${var.bedrock_region}::foundation-model/${var.bedrock_model_id}"
    }]
  })
}

# The same package as the API, with a plain handler (spec §3.5).
resource "aws_lambda_function" "triage" {
  function_name    = local.triage
  role             = aws_iam_role.triage.arn
  runtime          = var.runtime
  architectures    = ["arm64"]
  handler          = "nettriage.entrypoints.triage.handler.handle"
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  memory_size      = 512
  timeout          = local.triage_timeout
  layers           = [var.otel_collector_layer_arn]

  environment {
    variables = {
      OPENTELEMETRY_COLLECTOR_CONFIG_URI                 = "/var/task/collector.yaml"
      OTEL_EXPORTER_OTLP_ENDPOINT                        = "http://localhost:4318"
      OTEL_EXPORTER_OTLP_PROTOCOL                        = "http/protobuf"
      OTEL_SEMCONV_STABILITY_OPT_IN                      = "gen_ai_latest_experimental"
      OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT = "false"
      GRAFANA_OTLP_ENDPOINT                              = var.grafana_otlp_endpoint
      GRAFANA_OTLP_AUTH                                  = var.grafana_otlp_auth
      NETTRIAGE_STAGE                                    = var.stage
      NETTRIAGE_VERSION                                  = var.app_version
      NETTRIAGE_SERVICE_NAME                             = "nettriage-triage"
      NETTRIAGE_DATABASE_URL_PARAMETER                   = var.triage_database_url_parameter
      NETTRIAGE_RUNTIME_TABLE                            = var.runtime_table_name
      NETTRIAGE_AI_ENABLED_PARAMETER                     = aws_ssm_parameter.ai_enabled.name
      NETTRIAGE_BEDROCK_REGION                           = var.bedrock_region
      NETTRIAGE_BEDROCK_MODEL_ID                         = var.bedrock_model_id
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.triage,
    aws_iam_role_policy.triage_logs,
    aws_iam_role_policy.triage_queue,
    aws_iam_role_policy.triage_parameters,
    aws_iam_role_policy.triage_budgets,
    aws_iam_role_policy.triage_bedrock,
  ]
}

# One finding per run, at most two at once (spec §3.5, the owner's decision in Plan 5b). The
# worker answers with a partial batch response, so a failed message alone is delivered again.
resource "aws_lambda_event_source_mapping" "triage" {
  event_source_arn        = aws_sqs_queue.triage.arn
  function_name           = aws_lambda_function.triage.arn
  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = 2
  }
}
```

In `infra/modules/pipeline/analyze.tf`, replace:
```hcl
      NETTRIAGE_UPLOADS_BUCKET           = aws_s3_bucket.uploads.bucket
    }
```
with:
```hcl
      NETTRIAGE_UPLOADS_BUCKET           = aws_s3_bucket.uploads.bucket
      NETTRIAGE_TRIAGE_QUEUE_URL         = aws_sqs_queue.triage.id
    }
```

In `infra/modules/pipeline/analyze.tf`, replace:
```hcl
    aws_iam_role_policy.analyze_parameters,
  ]
```
with:
```hcl
    aws_iam_role_policy.analyze_parameters,
    aws_iam_role_policy.analyze_triage_queue,
  ]
```

In `infra/modules/pipeline/variables.tf`, replace:
```hcl
  description = "SSM SecureString with app_analyze's pooled Neon URL, written by the deploy (tools/deploy)."
}

```
with:
```hcl
  description = "SSM SecureString with app_analyze's pooled Neon URL, written by the deploy (tools/deploy)."
}

variable "triage_database_url_parameter" {
  type        = string
  description = "SSM SecureString with app_triage's pooled Neon URL, written by the deploy (tools/deploy)."
}

variable "runtime_table_name" {
  type        = string
  description = "The DynamoDB runtime table, where the AI budgets live (spec §5.5)."
}

variable "runtime_table_arn" {
  type = string
}

variable "bedrock_region" {
  type        = string
  default     = "eu-north-1"
  description = "Where the triage model runs on demand (spec Revision 2, R4)."

  validation {
    condition     = contains(["eu-north-1", "us-east-1", "us-west-2"], var.bedrock_region)
    error_message = "The account can invoke Bedrock only in eu-north-1, us-east-1 or us-west-2 (spec Revision 2, R1)."
  }
}

variable "bedrock_model_id" {
  type        = string
  default     = "openai.gpt-oss-20b-1:0"
  description = "The triage model's on-demand ID; it needs a price in backend/src/nettriage/application/llm.py."
}

```

In `infra/modules/pipeline/outputs.tf`, replace:
```hcl
  value = aws_ssm_parameter.uploads_enabled.name
}

```
with:
```hcl
  value = aws_ssm_parameter.uploads_enabled.name
}

output "ai_enabled_parameter" {
  value = aws_ssm_parameter.ai_enabled.name
}

```

In `infra/envs/dev/main.tf`, replace:
```hcl
  analyze_database_url_parameter = "/nettriage/dev/db/app-analyze-url"
}
```
with:
```hcl
  analyze_database_url_parameter = "/nettriage/dev/db/app-analyze-url"
  triage_database_url_parameter  = "/nettriage/dev/db/app-triage-url"
}
```

In `infra/envs/dev/main.tf`, replace:
```hcl
  database_url_parameter   = local.analyze_database_url_parameter
}
```
with:
```hcl
  database_url_parameter   = local.analyze_database_url_parameter

  triage_database_url_parameter = local.triage_database_url_parameter
  runtime_table_name            = module.data.table_name
  runtime_table_arn             = module.data.table_arn
}
```

- [ ] **Step 4: Count usage before credits, and cut Bedrock off at $5**

In `infra/bootstrap/cost.tf`, replace:
```hcl
  time_unit    = "MONTHLY"

```
with:
```hcl
  time_unit    = "MONTHLY"

  # Count usage before credits: on the Free plan the credits would net it to $0, and no alert
  # or action would ever fire (spec §6.7, Plan 5b).
  cost_types {
    include_credit = false
  }

```

In `infra/bootstrap/variables.tf`, replace:
```hcl
  description = "ARN of the account's existing Cost Anomaly Detection services monitor. Empty = no subscription."
}

```
with:
```hcl
  description = "ARN of the account's existing Cost Anomaly Detection services monitor. Empty = no subscription."
}

variable "triage_roles" {
  type        = list(string)
  default     = ["nettriage-dev-triage"]
  description = "The triage workers' roles, which the $5 budget action denies Bedrock (spec §6.7). They must exist: deploy the stage first."
}

```

`infra/bootstrap/bedrock_cutoff.tf`:
```hcl
# The $5 backstop (spec §6.7): when the month's actual usage reaches $5, AWS Budgets attaches a
# policy that denies Bedrock to the triage workers. Budgets refreshes up to three times a day,
# so this lags by hours; the AI budgets in DynamoDB are the real-time limit (spec §6.6). To undo
# it, detach the policy from the role (runbook Part C, "Bedrock was cut off at $5").

resource "aws_iam_policy" "deny_bedrock" {
  name        = "nettriage-deny-bedrock"
  description = "Attached by the $5 budget action: no Bedrock calls until the owner detaches it."
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "MonthlyBudgetSpent"
      Effect   = "Deny"
      Action   = "bedrock:InvokeModel*"
      Resource = "*"
    }]
  })
}

resource "aws_iam_role" "budget_action" {
  name = "nettriage-budget-action"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "budgets.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "budget_action" {
  name = "attach-deny-bedrock"
  role = aws_iam_role.budget_action.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"]
      Resource  = [for role in var.triage_roles : "arn:aws:iam::${local.account_id}:role/${role}"]
      Condition = { ArnEquals = { "iam:PolicyARN" = aws_iam_policy.deny_bedrock.arn } }
    }]
  })
}

resource "aws_budgets_budget_action" "deny_bedrock" {
  budget_name        = aws_budgets_budget.monthly.name
  action_type        = "APPLY_IAM_POLICY"
  approval_model     = "AUTOMATIC"
  notification_type  = "ACTUAL"
  execution_role_arn = aws_iam_role.budget_action.arn

  action_threshold {
    action_threshold_type  = "ABSOLUTE_VALUE"
    action_threshold_value = 5
  }

  definition {
    iam_action_definition {
      policy_arn = aws_iam_policy.deny_bedrock.arn
      roles      = var.triage_roles
    }
  }

  subscriber {
    address           = var.budget_email
    subscription_type = "EMAIL"
  }

  depends_on = [aws_iam_role_policy.budget_action]
}
```

In `.checkov.yaml`, replace:
```yaml
  - CKV_AWS_115    # Lambda reserved concurrency: new accounts may have too low a quota (spec §13.2)
  - CKV_AWS_116    # Lambda DLQ: the API is synchronous, and the analyze worker is fed by SQS, whose redrive policy is its DLQ (spec §8.6)
  - CKV_AWS_117    # Lambda in a VPC: no VPC by design (ADR 0009)
```
with:
```yaml
  - CKV_AWS_115    # Lambda reserved concurrency: new accounts may have too low a quota (spec §13.2)
  - CKV_AWS_116    # Lambda DLQ: the API is synchronous, and the analyze and triage workers are fed by SQS, whose redrive policies are their DLQs (spec §8.6)
  - CKV_AWS_117    # Lambda in a VPC: no VPC by design (ADR 0009)
```

- [ ] **Step 5: Run the checks**

Run: `just tf-check && (cd infra/envs/dev && TF_DATA_DIR=.terraform-check terraform init -backend=false -input=false >/dev/null && TF_DATA_DIR=.terraform-check terraform validate)`
Expected: `Success! 6 passed, 0 failed.` for the bootstrap and `Success! 14 passed, 0 failed.` for the pipeline (with the other modules' lines unchanged), then `Success! The configuration is valid.` for `envs/dev`.

- [ ] **Step 6: Commit**

```bash
git add infra .checkov.yaml
git commit -m "feat(infra): the triage queue and worker, the AI kill switch, and the \$5 budget action that denies Bedrock" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: The deploy's side

**Files:**
- Modify:
  - `tools/deploy/config.py`, `preflight.py` and `__main__.py`;
  - `tools/build_lambda.py`;
  - `justfile`;
  - `infra/envs/dev/terraform.tfvars`, `variables.tf` and `main.tf`
- Test:
  - `tools/tests/test_deploy_database.py`, `test_deploy_preflight.py`, `test_deploy_uploads_switch.py` and `test_deploy_cli.py`;
  - `tools/tests/test_build_lambda.py`

**Interfaces:**
- Consumes: Task 7's `bedrock_region` and `bedrock_model_id` module variables and the `ai-enabled` parameter; Plan 3a's `ensure_role_logins`; Plan 4a's `switch_uploads`; the `FakeRun` test double.
- Produces:
  - in `tools.deploy.config`: `APP_DB_ROLES` with `app_triage`, `BEDROCK_REGIONS` and `ai_enabled_parameter(stage)`;
  - `tools.deploy.preflight.model_check(run, env, region, model_id) -> Check`, the stage check named `Triage model on Bedrock`;
  - in `tools.deploy.__main__`: `set_switch(run, env, name, on) -> str`, `switch_ai(run, env, stage, on)`, and the `ai on|off` command;
  - `just pause-ai` and `just resume-ai`;
  - `build_lambda.REQUIRED` with the triage handler and prompt v1;
  - `bedrock_region` and `bedrock_model_id` in `terraform.tfvars`.

- [ ] **Step 1: Write the failing tests**

In `tools/tests/test_deploy_database.py`, replace:
```python
        "Database migrations failed; nothing was deployed. Alembic stopped with exit code 1: "
        'Error: ProgrammingError: relation "users" already exists'
    )

```
with:
```python
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

```

In `tools/tests/test_deploy_preflight.py`, replace:
```python

LWA = "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerArm64:30"


```
with:
```python

LWA = "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerArm64:30"
MODEL = json.dumps(
    {
        "modelDetails": {
            "modelId": "openai.gpt-oss-20b-1:0",
            "inferenceTypesSupported": ["ON_DEMAND"],
            "modelLifecycle": {"status": "ACTIVE"},
        }
    }
)


```

In `tools/tests/test_deploy_preflight.py`, replace:
```python

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
```
with:
```python

def test_stage_checks_names_and_order() -> None:
    run = (
        FakeRun()
        .on("aws", "lambda", "get-layer-version-by-arn", returns='{"CompatibleArchitectures": ["arm64"]}')
        .on("aws", "bedrock", "get-foundation-model", returns=MODEL)
        .on("aws", "ssm", "describe-parameters", returns=ssm_names())
        .on("aws")
    )
    tfvars = {
        "lwa_layer_arn": "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerArm64:30",
        "otel_collector_layer_arn": "arn:aws:lambda:eu-north-1:753240598075:layer:OtelLayerArm64:1",
        "grafana_otlp_endpoint": "https://otlp-gateway.grafana.net/otlp",
        "bedrock_region": "eu-north-1",
        "bedrock_model_id": "openai.gpt-oss-20b-1:0",
    }
    checks = preflight.stage_checks(run, {}, "dev", "123456789012", tfvars)
```

In `tools/tests/test_deploy_preflight.py`, replace:
```python
        "Grafana OTLP endpoint in terraform.tfvars",
        "Lambda Web Adapter layer", "OpenTelemetry collector layer",
    ]
    assert all(check.ok for check in checks)
```
with:
```python
        "Grafana OTLP endpoint in terraform.tfvars",
        "Lambda Web Adapter layer", "OpenTelemetry collector layer",
        "Triage model on Bedrock",
    ]
    assert all(check.ok for check in checks)
```

In `tools/tests/test_deploy_preflight.py`, replace:
```python
    assert checks["Database connection in SSM"].detail == "missing; run: just store-database-url dev"

```
with:
```python
    assert checks["Database connection in SSM"].detail == "missing; run: just store-database-url dev"


def test_the_triage_model_runs_on_demand_in_its_region() -> None:
    run = FakeRun().on("aws", "bedrock", "get-foundation-model", returns=MODEL)

    check = preflight.model_check(run, {}, "eu-north-1", "openai.gpt-oss-20b-1:0")

    assert check.ok
    [call] = run.called("aws", "bedrock", "get-foundation-model")
    assert call.args[call.args.index("--model-identifier") + 1] == "openai.gpt-oss-20b-1:0"
    assert call.args[call.args.index("--region") + 1] == "eu-north-1"


def test_an_answer_that_isnt_json_fails() -> None:
    run = FakeRun().on("aws", "bedrock", "get-foundation-model", returns="")

    assert not preflight.model_check(run, {}, "eu-north-1", "openai.gpt-oss-20b-1:0").ok


@pytest.mark.parametrize(
    "details",
    [
        {"inferenceTypesSupported": ["PROVISIONED"], "modelLifecycle": {"status": "ACTIVE"}},
        {"inferenceTypesSupported": ["INFERENCE_PROFILE"], "modelLifecycle": {"status": "ACTIVE"}},
        {"inferenceTypesSupported": ["ON_DEMAND"], "modelLifecycle": {"status": "LEGACY"}},
    ],
)
def test_a_model_that_isnt_on_demand_and_active_fails(details: dict[str, object]) -> None:
    run = FakeRun().on(
        "aws", "bedrock", "get-foundation-model", returns=json.dumps({"modelDetails": details})
    )

    assert not preflight.model_check(run, {}, "eu-north-1", "openai.gpt-oss-20b-1:0").ok


def test_a_model_bedrock_doesnt_offer_there_fails_with_a_hint() -> None:
    run = FakeRun().on(
        "aws", "bedrock", "get-foundation-model", returns=CommandError("ResourceNotFoundException")
    )

    check = preflight.model_check(run, {}, "eu-north-1", "mistral.ministral-3-8b-instruct")

    assert not check.ok
    assert "terraform.tfvars" in check.detail


@pytest.mark.parametrize(("region", "model"), [("eu-west-1", "x"), ("eu-north-1", "")])
def test_a_region_the_account_cant_call_or_no_model_fails_without_calling_aws(
    region: str, model: str
) -> None:
    run = FakeRun()

    assert not preflight.model_check(run, {}, region, model).ok
    assert run.called("aws") == []

```

In `tools/tests/test_deploy_uploads_switch.py`, replace:
```python
"""Pausing and resuming uploads from the owner's machine (spec §9.7, runbook Part C)."""

import pytest
```
with:
```python
"""Pausing and resuming uploads and AI explanations from the owner's machine (spec §9.7,
runbook Part C)."""

import pytest
```

In `tools/tests/test_deploy_uploads_switch.py`, replace:
```python
    assert "STOP: " in capsys.readouterr().err

```
with:
```python
    assert "STOP: " in capsys.readouterr().err


@pytest.mark.parametrize(
    ("state", "value", "shown"), [("off", "false", "paused"), ("on", "true", "on")]
)
def test_the_ai_switch_is_set_then_read_back(
    state: str, value: str, shown: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run = (
        signed_in()
        .on("aws", "ssm", "put-parameter")
        .on("aws", "ssm", "get-parameter", returns=f"{value}\n")
    )

    code = cli.main(["ai", state], run=run)

    [put] = run.called("aws", "ssm", "put-parameter")
    assert code == 0
    assert put.args[put.args.index("--name") + 1] == "/nettriage/dev/kill/ai-enabled"
    assert put.args[put.args.index("--value") + 1] == value
    assert f"AI explanations in dev are {shown}" in capsys.readouterr().out

```

In `tools/tests/test_deploy_cli.py`, replace:
```python
        f'lwa_layer_arn = "{LWA}"\notel_collector_layer_arn = "{OTEL}"\n'
        'grafana_otlp_endpoint = "https://otlp.example/otlp"\n',
        encoding="utf-8",
```
with:
```python
        f'lwa_layer_arn = "{LWA}"\notel_collector_layer_arn = "{OTEL}"\n'
        'grafana_otlp_endpoint = "https://otlp.example/otlp"\n'
        'bedrock_region = "eu-north-1"\nbedrock_model_id = "openai.gpt-oss-20b-1:0"\n',
        encoding="utf-8",
```

In `tools/tests/test_deploy_cli.py`, replace:
```python
        run.on("aws", "lambda", "get-layer-version-by-arn", returns=json.dumps({"CompatibleArchitectures": ["arm64"]}))
        .on("aws", "ssm", "describe-parameters", returns=ssm_names())
```
with:
```python
        run.on("aws", "lambda", "get-layer-version-by-arn", returns=json.dumps({"CompatibleArchitectures": ["arm64"]}))
        .on("aws", "bedrock", "get-foundation-model", returns=json.dumps(
            {"modelDetails": {"inferenceTypesSupported": ["ON_DEMAND"], "modelLifecycle": {"status": "ACTIVE"}}}
        ))
        .on("aws", "ssm", "describe-parameters", returns=ssm_names())
```

In `tools/tests/test_build_lambda.py`, replace:
```python
        "nettriage/entrypoints/api/main.py": b"app = None\n",
        "nettriage/entrypoints/analyze/handler.py": b"def handle(event, context): pass\n",
        "fastapi-1.0.dist-info/WHEEL": f"Wheel-Version: 1.0\nTag: {wheel_tag}\n".encode(),
        **(extra or {}),
```
with:
```python
        "nettriage/entrypoints/api/main.py": b"app = None\n",
        "nettriage/entrypoints/analyze/handler.py": b"def handle(event, context): pass\n",
        "nettriage/entrypoints/triage/handler.py": b"def handle(event, context): pass\n",
        "nettriage/prompts/triage/v1.md": b"You explain findings.\n",
        "fastapi-1.0.dist-info/WHEEL": f"Wheel-Version: 1.0\nTag: {wheel_tag}\n".encode(),
        **(extra or {}),
```

In `tools/tests/test_build_lambda.py`, replace:
```python
        validate_zip(out, max_unzipped=10)

```
with:
```python
        validate_zip(out, max_unzipped=10)


@pytest.mark.parametrize(
    "name", ["nettriage/entrypoints/triage/handler.py", "nettriage/prompts/triage/v1.md"]
)
def test_a_package_without_the_triage_worker_or_its_prompt_is_rejected(
    tmp_path: Path, name: str
) -> None:
    package = make_package(tmp_path)
    (package / name).unlink()
    out = tmp_path / "backend.zip"
    write_zip(package, out)

    with pytest.raises(PackageError, match=f"missing {name}"):
        validate_zip(out)

```

- [ ] **Step 2: Run them and watch them fail**

Run: `just tools-test`
Expected: `14 failed, 232 passed`: the two package tests, the triage login, nine preflight tests and the two AI switch tests.

- [ ] **Step 3: Give app_triage a login, check the model, and flip the AI switch**

In `tools/deploy/config.py`, replace:
```python
NEON_HOST_SUFFIX = ".eu-central-1.aws.neon.tech"
# Database roles that get a login from the deploy. Plans 5 and 7 add theirs.
APP_DB_ROLES = ("app_api", "app_analyze")
MIGRATIONS_CONFIG = REPO / "backend" / "alembic.ini"
```
with:
```python
NEON_HOST_SUFFIX = ".eu-central-1.aws.neon.tech"
# Database roles that get a login from the deploy. Plan 7 adds the ops job's.
APP_DB_ROLES = ("app_api", "app_analyze", "app_triage")
# Where the account may invoke Bedrock (spec Revision 2, R1).
BEDROCK_REGIONS = ("eu-north-1", "us-east-1", "us-west-2")
MIGRATIONS_CONFIG = REPO / "backend" / "alembic.ini"
```

In `tools/deploy/config.py`, replace:
```python

def db_owner_url_parameter(stage: str) -> str:
```
with:
```python

def ai_enabled_parameter(stage: str) -> str:
    """The AI kill switch (spec §9.7), which Terraform creates as "true"."""
    return f"/nettriage/{stage}/kill/ai-enabled"


def db_owner_url_parameter(stage: str) -> str:
```

In `tools/deploy/preflight.py`, replace:
```python

from tools.deploy.config import REGION, db_owner_url_parameter, otlp_auth_parameter, state_bucket
from tools.deploy.runner import CommandError, Runner
```
with:
```python

from tools.deploy.config import (
    BEDROCK_REGIONS,
    REGION,
    db_owner_url_parameter,
    otlp_auth_parameter,
    state_bucket,
)
from tools.deploy.runner import CommandError, Runner
```

In `tools/deploy/preflight.py`, replace:
```python

def parameter_check(run: Runner, env: Mapping[str, str], stage: str) -> Check:
```
with:
```python

def model_check(run: Runner, env: Mapping[str, str], region: str, model_id: str) -> Check:
    """The triage model must run on demand in its Region: the account can't use cross-Region
    inference profiles (spec Revision 2, R4)."""
    name = "Triage model on Bedrock"
    if region not in BEDROCK_REGIONS or not model_id:
        return Check(name, False, f"set bedrock_region (one of {', '.join(BEDROCK_REGIONS)}) and bedrock_model_id in terraform.tfvars")
    try:
        details = json.loads(
            run(["aws", "bedrock", "get-foundation-model", "--model-identifier", model_id, "--region", region], env=env).stdout
        ).get("modelDetails", {})
    except CommandError as exc:
        return Check(name, False, f"{model_id} isn't offered in {region}; fix bedrock_model_id or bedrock_region in terraform.tfvars ({exc})")
    except ValueError:
        return Check(name, False, "Bedrock's answer wasn't JSON; run the preflight again")
    on_demand = "ON_DEMAND" in details.get("inferenceTypesSupported", [])
    active = details.get("modelLifecycle", {}).get("status") == "ACTIVE"
    if not (on_demand and active):
        return Check(name, False, f"{model_id} in {region} isn't active and on demand; pick another model")
    return Check(name, True, f"{model_id} in {region}")


def parameter_check(run: Runner, env: Mapping[str, str], stage: str) -> Check:
```

In `tools/deploy/preflight.py`, replace:
```python
        layer_check(run, env, "OpenTelemetry collector layer", tfvars.get("otel_collector_layer_arn", "")),
    ]
```
with:
```python
        layer_check(run, env, "OpenTelemetry collector layer", tfvars.get("otel_collector_layer_arn", "")),
        model_check(run, env, tfvars.get("bedrock_region", ""), tfvars.get("bedrock_model_id", "")),
    ]
```

In `tools/deploy/__main__.py`, replace:
```python
  python -m tools.deploy uploads on|off [--stage dev]

```
with:
```python
  python -m tools.deploy uploads on|off [--stage dev]
  python -m tools.deploy ai on|off [--stage dev]

```

In `tools/deploy/__main__.py`, replace:
```python

def switch_uploads(run: Runner, env: Mapping[str, str], stage: str, on: bool) -> None:
    """Pause or resume uploads (spec §9.7): the API re-reads the switch within a minute. The
    name goes to the AWS CLI as an argument list, so no shell can rewrite it."""
    name = config.uploads_enabled_parameter(stage)
    wanted = "true" if on else "false"
```
with:
```python

def set_switch(run: Runner, env: Mapping[str, str], name: str, on: bool) -> str:
    """Set a kill switch (spec §9.7) and read it back. The name goes to the AWS CLI as an
    argument list, so no shell can rewrite it."""
    wanted = "true" if on else "false"
```

In `tools/deploy/__main__.py`, replace:
```python
        raise CommandError(f"{name} reads {value!r} after setting it to {wanted!r}. Run the command again.")
    print(f"Uploads in {stage} are {'on' if on else 'paused'} ({name} = {value}). "
          "The API picks this up within a minute.")

```
with:
```python
        raise CommandError(f"{name} reads {value!r} after setting it to {wanted!r}. Run the command again.")
    return value


def switch_uploads(run: Runner, env: Mapping[str, str], stage: str, on: bool) -> None:
    """Pause or resume uploads: the API re-reads the switch within a minute."""
    name = config.uploads_enabled_parameter(stage)
    value = set_switch(run, env, name, on)
    print(f"Uploads in {stage} are {'on' if on else 'paused'} ({name} = {value}). "
          "The API picks this up within a minute.")


def switch_ai(run: Runner, env: Mapping[str, str], stage: str, on: bool) -> None:
    """Pause or resume AI explanations: the triage worker re-reads the switch within a minute.
    While paused, findings are stored as skipped (`ai_disabled`), with no Bedrock call."""
    name = config.ai_enabled_parameter(stage)
    value = set_switch(run, env, name, on)
    print(f"AI explanations in {stage} are {'on' if on else 'paused'} ({name} = {value}). "
          "The triage worker picks this up within a minute.")

```

In `tools/deploy/__main__.py`, replace:
```python
    commands.choices["plan"].add_argument("--no-comment", action="store_true")
    uploads = commands.add_parser("uploads")
    uploads.add_argument("state", choices=("on", "off"))
    uploads.add_argument("--stage", choices=config.STAGES, default="dev")
    boot = commands.add_parser("bootstrap")
```
with:
```python
    commands.choices["plan"].add_argument("--no-comment", action="store_true")
    for name in ("uploads", "ai"):
        switch = commands.add_parser(name)
        switch.add_argument("state", choices=("on", "off"))
        switch.add_argument("--stage", choices=config.STAGES, default="dev")
    boot = commands.add_parser("bootstrap")
```

In `tools/deploy/__main__.py`, replace:
```python
            switch_uploads(run, env, args.stage, on=args.state == "on")
        elif args.command == "plan":
```
with:
```python
            switch_uploads(run, env, args.stage, on=args.state == "on")
        elif args.command == "ai":
            switch_ai(run, env, args.stage, on=args.state == "on")
        elif args.command == "plan":
```

In `tools/build_lambda.py`, replace:
```python
    "nettriage/entrypoints/analyze/handler.py",
)
```
with:
```python
    "nettriage/entrypoints/analyze/handler.py",
    "nettriage/entrypoints/triage/handler.py",
    "nettriage/prompts/triage/v1.md",
)
```

In `justfile`, replace:
```

# Plan the dev stage for the checked-out, pushed commit and post the changes to its PR
```
with:
```

# AWS: pause or resume AI explanations in an emergency (no deploy needed)
pause-ai stage="dev":
    uv run --project backend python -m tools.deploy ai off --stage {{stage}}

resume-ai stage="dev":
    uv run --project backend python -m tools.deploy ai on --stage {{stage}}

# Plan the dev stage for the checked-out, pushed commit and post the changes to its PR
```

- [ ] **Step 4: Name the model in the stage's settings**

In `infra/envs/dev/terraform.tfvars`, replace:
```hcl
grafana_otlp_endpoint    = "https://otlp-gateway-prod-eu-central-0.grafana.net/otlp"

```
with:
```hcl
grafana_otlp_endpoint    = "https://otlp-gateway-prod-eu-central-0.grafana.net/otlp"

# The triage model (Plan 5b): gpt-oss-20b, on demand in Stockholm. `just preflight` checks
# Bedrock offers it there; it needs a price in backend/src/nettriage/application/llm.py.
bedrock_region   = "eu-north-1"
bedrock_model_id = "openai.gpt-oss-20b-1:0"

```

In `infra/envs/dev/variables.tf`, replace:
```hcl
  sensitive = true
}

```
with:
```hcl
  sensitive = true
}

variable "bedrock_region" {
  type = string
}

variable "bedrock_model_id" {
  type = string
}

```

In `infra/envs/dev/main.tf`, replace:
```hcl
  triage_database_url_parameter = local.triage_database_url_parameter
  runtime_table_name            = module.data.table_name
```
with:
```hcl
  triage_database_url_parameter = local.triage_database_url_parameter
  bedrock_region                = var.bedrock_region
  bedrock_model_id              = var.bedrock_model_id
  runtime_table_name            = module.data.table_name
```

- [ ] **Step 5: Run the checks**

Run: `just tools-test build-lambda && (cd infra/envs/dev && TF_DATA_DIR=.terraform-check terraform validate)`
Expected: `246 passed`, the Lambda package builds and validates, and `Success! The configuration is valid.`

- [ ] **Step 6: Commit**

```bash
git add tools justfile infra/envs/dev
git commit -m "feat(deploy): app_triage's login, the triage model in the preflight, pause-ai and resume-ai, and the prompt in the package" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: The decisions in the spec, and the owner's steps

**Files:**
- Modify: `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (§3.2, §3.5, §4.2, §5.2, §5.4, §5.5, §6.7, §7, §8.3, §8.6, §9.1, §9.7, §13.2, §14), `docs/runbooks/setup-and-deploy.md` (A5, A8, B2, B9, Part C), `CLAUDE.md`, `README.md`

- [ ] **Step 1: Amend the spec**

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| Identity | Cognito user pool, Essentials tier | Managed login; MFA required (TOTP) |
| LLM | Amazon Bedrock (Region chosen per model in Plan 5: eu-north-1, us-east-1 or us-west-2) | Structured outputs; model chosen by evals; on-demand models only, no cross-Region inference profiles (Revision 2, R4) |
| Secrets and config | SSM Parameter Store (SecureString, AWS-managed key) | Database passwords, Cognito client secret, OTLP token, kill switches |
```
with:
```markdown
| Identity | Cognito user pool, Essentials tier | Managed login; MFA required (TOTP) |
| LLM | Amazon Bedrock: OpenAI gpt-oss-20b on demand in eu-north-1 (the owner's choice in Plan 5b, until Plan 5c's evals choose) | Structured outputs; model chosen by evals; on-demand models only, no cross-Region inference profiles (Revision 2, R4) |
| Secrets and config | SSM Parameter Store (SecureString, AWS-managed key) | Database passwords, Cognito client secret, OTLP token, kill switches |
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `analyze` | 2048 MB | 300 s | SQS `analyze`, batch size 1 | Event source mapping maximum concurrency 2 |
| `triage` | 512 MB | 60 s | SQS `triage`, batch size 5, partial batch responses | Event source mapping maximum concurrency 2 |
| `ops` | 256 MB | 60 s | EventBridge Scheduler | None needed |
```
with:
```markdown
| `analyze` | 2048 MB | 300 s | SQS `analyze`, batch size 1 | Event source mapping maximum concurrency 2 |
| `triage` | 512 MB | 240 s | SQS `triage`, batch size 1, partial batch responses | Event source mapping maximum concurrency 2 |
| `ops` | 256 MB | 60 s | EventBridge Scheduler | None needed |
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
- The SQS visibility timeout is 6 × the function timeout.
- Worker concurrency is capped through the event source mapping's maximum concurrency, not reserved concurrency, because new accounts can have a low Lambda concurrency quota (see 13.2).
```
with:
```markdown
- The SQS visibility timeout is 6 × the function timeout.
- The triage worker explains one finding per run, and 240 s fits its worst case: an answer and its repair, each a 20-second call with 3 retries (the owner's decision in Plan 5b; batch size 5 and 60 s couldn't fit one slow call).
- Worker concurrency is capped through the event source mapping's maximum concurrency, not reserved concurrency, because new accounts can have a low Lambda concurrency quota (see 13.2).
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
  QT->>T: messages
  T->>T: reserve budget, check cache, call Bedrock, validate, store
  B->>A: GET upload status and findings (polling)
```
with:
```markdown
  QT->>T: messages
  T->>T: check cache and kill switch, reserve budget, call Bedrock, validate, store
  B->>A: GET upload status and findings (polling)
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `finding_techniques` | PK `(finding_id, technique_id, source)`; `org_id`; `technique_id` → attack_techniques; `source` CHECK in (detector, ai); `rationale`; FK `(org_id, finding_id)` → findings |
| `ai_analyses` | `id`, `org_id`, `finding_id`, `status` CHECK in (pending, succeeded, failed, skipped_budget, invalid_output), `provider`, `model_id`, `prompt_version`, `output_schema_version`, `input_hash`, `output` jsonb, `input_tokens`, `output_tokens`, `cost_usd` numeric(10,6), `latency_ms`, `error_code`, `feedback` CHECK in (up, down) or NULL, `feedback_by`; UNIQUE `(finding_id, model_id, prompt_version, input_hash)`; FK `(org_id, finding_id)` → findings. Only a succeeded row has an `output`. A new attempt for the same key updates the row, and a succeeded row is never overwritten: it is the cache (Plan 5a) |
| `finding_events` | `id`, `org_id`, `finding_id`, `actor_id` (NULL for system events), `type` CHECK in (created, status_changed, assigned, commented, ai_explained), `payload` jsonb. Comments are at most 2,000 characters |
```
with:
```markdown
| `finding_techniques` | PK `(finding_id, technique_id, source)`; `org_id`; `technique_id` → attack_techniques; `source` CHECK in (detector, ai); `rationale`; FK `(org_id, finding_id)` → findings |
| `ai_analyses` | `id`, `org_id`, `finding_id`, `status` CHECK in (pending, succeeded, failed, skipped_budget, invalid_output), `provider`, `model_id`, `prompt_version`, `output_schema_version`, `input_hash`, `output` jsonb, `input_tokens`, `output_tokens`, `cost_usd` numeric(10,6), `latency_ms`, `error_code`, `feedback` CHECK in (up, down) or NULL, `feedback_by`; UNIQUE `(finding_id, model_id, prompt_version, input_hash)`; FK `(org_id, finding_id)` → findings. Only a succeeded row has an `output`. A new attempt for the same key updates the row, and a succeeded row is never overwritten: it is the cache (Plan 5a). `provider` is OpenTelemetry's `gen_ai.provider.name`, such as `aws.bedrock` (Plan 5b) |
| `finding_events` | `id`, `org_id`, `finding_id`, `actor_id` (NULL for system events), `type` CHECK in (created, status_changed, assigned, commented, ai_explained), `payload` jsonb. Comments are at most 2,000 characters |
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `app_api` | `api` Lambda | The SELECT/INSERT/UPDATE its endpoints need; INSERT only on `audit_log`; DELETE only on `memberships`, `invitations`, `organizations`. On findings it may UPDATE only `status`, `assignee_id` and `version`, and `finding_events` is insert-only (Plan 4c) |
| `app_analyze` | `analyze` Lambda | SELECT `uploads`, `detectors`, `attack_techniques`; UPDATE of `uploads` status and statistics columns only; INSERT `findings`, `finding_evidence`, `finding_techniques`, `finding_events`. No `audit_log` until the worker records an event worth auditing (Plan 4b) |
| `app_triage` | `triage` Lambda | SELECT `findings`, `finding_evidence`, `finding_techniques`, `attack_techniques`, `detectors`, `ai_analyses`; INSERT/UPDATE `ai_analyses` (column grants: never `feedback`); INSERT `finding_techniques` and UPDATE of their `rationale`; INSERT `finding_events` without an actor. `audit_log` waits for an event the worker audits (Plan 5a) |
| `app_ops` | `ops` Lambda | `SELECT 1` health checks; retention through `SECURITY DEFINER` functions only (purge `audit_log` rows older than 180 days, expire invitations, expire stale pending uploads) |
```
with:
```markdown
| `app_api` | `api` Lambda | The SELECT/INSERT/UPDATE its endpoints need; INSERT only on `audit_log`; DELETE only on `memberships`, `invitations`, `organizations`. On findings it may UPDATE only `status`, `assignee_id` and `version`, and `finding_events` is insert-only (Plan 4c) |
| `app_analyze` | `analyze` Lambda | SELECT `uploads`, `detectors`, `attack_techniques`; UPDATE of `uploads` status and statistics columns only; INSERT `findings`, `finding_evidence`, `finding_techniques`, `finding_events`; SELECT of a finding's `id`, `org_id`, `upload_id` and `severity`, to queue the most severe for AI triage (Plan 5b). No `audit_log` until the worker records an event worth auditing (Plan 4b) |
| `app_triage` | `triage` Lambda | SELECT `findings`, `finding_evidence`, `finding_techniques`, `attack_techniques`, `detectors`, `ai_analyses`; INSERT/UPDATE `ai_analyses` (column grants: never `feedback`); INSERT `finding_techniques` and UPDATE of their `rationale`; INSERT `finding_events` without an actor; INSERT `audit_log`, for `budget.exhausted` (Plan 5b) |
| `app_ops` | `ops` Lambda | `SELECT 1` health checks; retention through `SECURITY DEFINER` functions only (purge `audit_log` rows older than 180 days, expire invitations, expire stale pending uploads) |
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| Rate-limit key | `RL#<policy>#<subject>` | `tat` (GCRA theoretical arrival time, ms) | 2 × the policy window |
| Org AI budget | `BUDGET#<org_id>#<yyyy-mm-dd>` (UTC day) | `tokens_reserved` (open reservations plus settled usage, because a condition can't add two attributes), `tokens_used` (settled usage) | 2 days |
| Global AI budget | `GBUDGET#<yyyy-mm-dd>` (UTC day) | `usd_reserved`, `usd_used`, counted the same way | 2 days |
```
with:
```markdown
| Rate-limit key | `RL#<policy>#<subject>` | `tat` (GCRA theoretical arrival time, ms) | 2 × the policy window |
| Org AI budget | `BUDGET#<org_id>#<yyyy-mm-dd>` (UTC day) | `tokens_reserved` (open reservations plus settled usage, because a condition can't add two attributes), `tokens_used` (settled usage), `refused_org` and `refused_global` (set by the day's first refusal, so `budget.exhausted` is audited once a day per org and budget; Plan 5b) | 2 days |
| Global AI budget | `GBUDGET#<yyyy-mm-dd>` (UTC day) | `usd_reserved`, `usd_used`, counted the same way | 2 days |
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
  - Alerts at $1 and $3, on both actual and forecast spend.
  - At $5, a Budgets **action** attaches a deny policy for `bedrock:InvokeModel*` to the triage role.
  - Cost Anomaly Detection runs with lowered thresholds.
```
with:
```markdown
  - Alerts at $1 and $3, on both actual and forecast spend.
  - At $5, a Budgets **action** attaches a deny policy for `bedrock:InvokeModel*` to the triage role. It lives in the bootstrap next to the budget, names the stages' triage roles, and needs them to exist: the owner re-runs the bootstrap after the stage's first deploy with a triage worker (Plan 5b).
  - The budget counts usage before credits: with credits included, the Free plan's usage nets to $0 and no alert or action would fire (Plan 5b).
  - Budgets refreshes up to three times a day, so the action lags spending by hours; the AI budgets in DynamoDB are the real-time limit (6.6).
  - Cost Anomaly Detection runs with lowered thresholds.
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `GET /api/v1/orgs/{org}/findings` | `findings:read` | Filters: status, severity, detector, upload. Newest first, a page at a time |
| `GET /api/v1/orgs/{org}/findings/{id}` | `findings:read` | With evidence, techniques, latest AI analysis (Plan 5), and the latest 100 events with `events_total` (Plan 4c); the `ETag` is the finding's version |
| `PATCH /api/v1/orgs/{org}/findings/{id}` | `findings:triage` | Body: `status`, `assignee_id` (`null` unassigns), or both; `If-Match`. Any status can change to any other, and the assignee must be an Owner, Admin or Analyst of the org (422 otherwise; the owner's decisions, Plan 4c). Each change is an event in the finding's history and an audit event |
```
with:
```markdown
| `GET /api/v1/orgs/{org}/findings` | `findings:read` | Filters: status, severity, detector, upload. Newest first, a page at a time |
| `GET /api/v1/orgs/{org}/findings/{id}` | `findings:read` | With evidence, techniques, the latest AI analysis as `ai_analysis` (null until there is one: status, provider, model, prompt and output schema versions, output, `error_code`, tokens, `cost_usd` and latency; Plan 5b), and the latest 100 events with `events_total` (Plan 4c); the `ETag` is the finding's version |
| `PATCH /api/v1/orgs/{org}/findings/{id}` | `findings:triage` | Body: `status`, `assignee_id` (`null` unassigns), or both; `If-Match`. Any status can change to any other, and the assignee must be an Owner, Admin or Analyst of the org (422 otherwise; the owner's decisions, Plan 4c). Each change is an event in the finding's history and an audit event |
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
  - Temperature 0.1, `max_tokens` 700, timeout 20 seconds.
  - On throttling and 5xx errors, up to 3 retries with exponential backoff and full jitter.
- **Cost:** computed from a per-model price table in config and stored per analysis.
- **Model candidates:** gpt-oss-20b, Ministral 3 8B and Claude Haiku 4.5 (quality baseline), all through Bedrock structured outputs. The exact Bedrock model IDs are taken from the Bedrock console/API at implementation time.
- **Selection:** the eval suite runs on every candidate, and the default is the cheapest model that meets all gates (8.5).
```
with:
```markdown
  - Temperature 0.1, `max_tokens` 700, timeout 20 seconds.
  - On throttling and 5xx errors, up to 3 retries with exponential backoff and full jitter (botocore's standard mode; Plan 5b).
- **Bedrock** (Plan 5b): the Converse API with `outputConfig.textFormat` set to the output schema. Bedrock enforces only part of JSON Schema (no string lengths, patterns or array maximums), so it gets the schema without those keywords and the Pydantic checks stay the gate. gpt-oss's reasoning is set to `low`, because it counts toward `max_tokens`. Bedrock's errors are stored as codes: `provider_throttled`, `provider_timeout` and `provider_unavailable` pass, so SQS delivers the finding again until its last delivery; `provider_denied` (the $5 action, or a missing permission) and `provider_rejected` don't.
- **Cost:** computed from a per-model price table in config and stored per analysis.
- **Model candidates:** gpt-oss-20b, Ministral 3 8B and Claude Haiku 4.5 (quality baseline), all through Bedrock structured outputs. As checked in Plan 5b (2026-10-01):
  - gpt-oss-20b (`openai.gpt-oss-20b-1:0`) runs on demand in eu-north-1, us-east-1 and us-west-2, at $0.07 / $0.30 per million input / output tokens in eu-north-1. It runs live from Plan 5b.
  - Ministral 3 8B (`mistral.ministral-3-8b-instruct`) runs on demand on Converse only in us-east-1 and us-west-2 ($0.15 / $0.15).
  - Claude Haiku 4.5 has no on-demand model ID for Converse in any Region, only cross-Region inference profiles, which the account can't use (Revision 2, R4): it can't be a candidate. Plan 5c picks the quality baseline.
- **Selection:** the eval suite runs on every candidate, and the default is the cheapest model that meets all gates (8.5).
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| Neon asleep or briefly unavailable | Two retries, after 1 and 3 seconds; then SQS delivers the message again after its visibility timeout (Plan 4b) |
| Bedrock throttled or down | Backoff and retries, then `failed`, shown as "AI unavailable" with a retry button |
| AI budget exhausted | `skipped_budget`, shown as "AI paused until tomorrow"; detection is unaffected |
```
with:
```markdown
| Neon asleep or briefly unavailable | Two retries, after 1 and 3 seconds; then SQS delivers the message again after its visibility timeout (Plan 4b) |
| Bedrock throttled or down | Backoff and retries; then SQS delivers the finding again, and the last delivery stores `failed`, shown as "AI unavailable" with a retry button (Plan 5b) |
| AI switched off (`ai_enabled` kill switch) | No call: `skipped_budget` with `error_code` `ai_disabled`. A cached answer is still served (Plan 5b) |
| AI budget exhausted | `skipped_budget`, shown as "AI paused until tomorrow"; detection is unaffected |
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
- **Auto-instrumentation:** FastAPI, psycopg and botocore.
- **Manual spans:** `analyze.upload` (the worker's span for one file, linked to the upload request's trace; Plan 4b), `analyze.parse`, `analyze.detect` and `triage.generate`.
  - `triage.generate` carries GenAI attributes: `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` and `gen_ai.response.finish_reasons`.
```
with:
```markdown
- **Auto-instrumentation:** FastAPI, psycopg and botocore.
- **Manual spans:** `analyze.upload` (the worker's span for one file, linked to the upload request's trace; Plan 4b), `analyze.parse`, `analyze.detect`, `triage.explain` (the triage worker's span for one finding, linked to the `analyze.upload` that queued it; Plan 5b) and `triage.generate`.
  - `triage.generate` carries GenAI attributes: `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` and `gen_ai.response.finish_reasons`.
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown

- **Kill switches:** `ai_enabled` and `uploads_enabled` live in SSM (`/nettriage/<stage>/kill/<name>`, `true` or anything else) and are re-read every 60 seconds. A switch that can't be read keeps its last value, and counts as off until it has been read once (Plan 4a). Terraform only creates them, so a deploy never turns a paused switch back on.
- **Maintenance:** the `ops` Lambda runs daily. It expires `pending_upload` rows older than 1 hour and invitations past their date, fails uploads left `processing` for more than 2 hours (a crash or timeout on the last delivery; Plan 4b), and purges audit rows older than 180 days.
```
with:
```markdown

- **Kill switches:** `ai_enabled` and `uploads_enabled` live in SSM (`/nettriage/<stage>/kill/ai-enabled` and `…/uploads-enabled`, `true` or anything else) and are re-read every 60 seconds. The owner flips them with `just pause-ai` / `just resume-ai` and `just pause-uploads` / `just resume-uploads`. A switch that can't be read keeps its last value, and counts as off until it has been read once (Plan 4a). Terraform only creates them, so a deploy never turns a paused switch back on.
- **Maintenance:** the `ops` Lambda runs daily. It expires `pending_upload` rows older than 1 hour and invitations past their date, fails uploads left `processing` for more than 2 hours (a crash or timeout on the last delivery; Plan 4b), and purges audit rows older than 180 days.
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| Terraform using the owner's `aws login` session through a `credential_process` helper profile (the default in `tools/deploy/`) | Export the session as environment variables for each command, keeping each run under the credentials' 15-minute lifetime |
| Bedrock model IDs, structured-output support and on-demand availability for the candidates in eu-north-1, us-east-1 or us-west-2, without cross-Region inference profiles (Revision 2, R4); whether credits cover Claude | Drop unavailable candidates; run Claude only in manual comparisons |
| Neon Terraform provider reliability | Create the projects by hand and document it (taken in Plan 3a: the provider isn't code-signed and the owner's machine blocks unsigned executables; see ADR 0002) |
```
with:
```markdown
| Terraform using the owner's `aws login` session through a `credential_process` helper profile (the default in `tools/deploy/`) | Export the session as environment variables for each command, keeping each run under the credentials' 15-minute lifetime |
| Bedrock model IDs, structured-output support and on-demand availability for the candidates in eu-north-1, us-east-1 or us-west-2, without cross-Region inference profiles (Revision 2, R4); whether credits cover Claude | Checked in Plan 5b (8.3): gpt-oss-20b runs on demand in Stockholm; Ministral 3 8B only in the US Regions; Claude Haiku 4.5 is dropped (cross-Region profiles only). `just preflight` checks the live model before each deploy |
| Neon Terraform provider reliability | Create the projects by hand and document it (taken in Plan 3a: the provider isn't code-signed and the owner's machine blocks unsigned executables; see ADR 0002) |
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
- [ ] Create the budgets and alerts (6.7) with the bootstrap before deploying anything.
- [ ] Bedrock: confirm access to the candidate models in their chosen Regions, and submit Anthropic's use-case form to test Claude.
- [ ] Create a Grafana Cloud free stack and a Neon account (projects in aws-eu-central-1, per 13.2). Store the Grafana OTLP token in SSM.
```
with:
```markdown
- [ ] Create the budgets and alerts (6.7) with the bootstrap before deploying anything.
- [ ] Bedrock: confirm gpt-oss-20b answers in the Stockholm playground (Plan 5b). AWS enables models on first use, and no Anthropic form is needed, because Claude can't be used on this account (8.3).
- [ ] Create a Grafana Cloud free stack and a Neon account (projects in aws-eu-central-1, per 13.2). Store the Grafana OTLP token in SSM.
```

- [ ] **Step 2: Add the owner's steps to the runbook**

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown

### A5. Run the preflight
```
with:
````markdown

### A8. Turn on the $5 Bedrock cutoff (once, after Plan 5b's deploy)
Plan 5b adds two things to the bootstrap (spec §6.7):
- the monthly budget counts usage before credits. With credits counted, the Free plan's usage nets
  to $0, so no alert would ever fire;
- at $5 of usage in a month, a Budgets action denies Bedrock to the `nettriage-dev-triage` worker.
  The action needs that worker's role, so run this after B2 has deployed Plan 5b.

1. `aws login --profile nettriage` (skip this if you're signed in).
2. On `main`, run the bootstrap again with the same email, and the anomaly monitor ARN if you used
   one in A4:
   ```bash
   just bootstrap you@example.com
   ```
3. Terraform shows the plan. Expect `Plan: 4 to add, 1 to change, 0 to destroy`: the
   `nettriage-deny-bedrock` policy, the `nettriage-budget-action` role and its policy, the budget
   action, and the budget's new credit setting.
   - If the plan says `1 to destroy` (the anomaly subscription), type `no`, then run step 2 again
     with your anomaly monitor ARN (A4 shows how to find it).
   - Otherwise type `yes`.
4. In the AWS console, open **Billing and Cost Management → Budgets → nettriage-monthly**. The
   **Actions** tab lists one action at $5.00 that applies the IAM policy `nettriage-deny-bedrock`.

### A5. Run the preflight
````

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
Every line must say `PASS`, including the Terraform state bucket, the Grafana token in SSM, the
database connection in SSM (after A7), the Grafana endpoint and both Lambda layers. If a layer line fails with "not found", its version
moved on. Ask Claude to update the ARN in `terraform.tfvars` from the layer's release notes.
```
with:
```markdown
Every line must say `PASS`, including the Terraform state bucket, the Grafana token in SSM, the
database connection in SSM (after A7), the Grafana endpoint, both Lambda layers and the triage model on Bedrock. If a layer line fails with "not found", its version
moved on. Ask Claude to update the ARN in `terraform.tfvars` from the layer's release notes.
```

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
   is new, it also gives it a login and adds `New logins: <role>.` to the first line (Plan 4b
   adds `app_analyze`);
5. shows the Terraform plan, and you type `yes`;
```
with:
```markdown
   is new, it also gives it a login and adds `New logins: <role>.` to the first line (Plan 4b
   added `app_analyze`, Plan 5b adds `app_triage`);
5. shows the Terraform plan, and you type `yes`;
```

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown

## Part C: when things go wrong
```
with:
````markdown

### B9. Try an AI explanation
Each analyzed upload's most severe findings (up to 20) go to the triage queue, and the `triage`
worker explains them with gpt-oss-20b on Bedrock in Stockholm. The findings pages come in Plan 6;
until then you read the explanation from the API.
1. Do B7 steps 1 to 5: sign in, create the "Analysis Test" org, make and upload the port scan, and
   wait until the upload says `analyzed`.
2. Wait 30 seconds (the triage worker's first run starts cold), then read the finding:
   ```js
   const list = await api("GET", "/orgs/" + org.id + "/findings");
   const finding = await api("GET", "/orgs/" + org.id + "/findings/" + list.findings[0].id);
   finding.ai_analysis;
   ```
   Expect `status: "succeeded"`, `provider: "aws.bedrock"`, `model_id: "openai.gpt-oss-20b-1:0"`,
   `prompt_version: "v1"`, the tokens and latency, and `cost_usd` below `"0.002"` (a fifth of a
   cent).
   - `null`: wait a minute and read it again. Still `null` after 5 minutes: see "An AI explanation
     is missing" in Part C.
   - `status` `"failed"` or `"skipped_budget"`: look up its `error_code` in the same section.
3. Read what the model wrote:
   ```js
   finding.ai_analysis.output;
   ```
   It has a `summary`, `why_it_matters`, `likely_benign_explanations`, `recommended_next_steps`,
   `attack_techniques`, a `severity_assessment`, a `confidence` and `insufficient_evidence`. It's
   AI-generated: the checks only let it name `203.0.113.9`, `10.0.0.5` and the ports in the file.
   Tell Claude how it reads, with its `output_tokens`: that helps Plan 5c's evals.
4. Read the finding's history:
   ```js
   finding.events.map((event) => event.type);
   ```
   It ends with `ai_explained`. If the model named a technique, `finding.techniques` lists it with
   `source: "ai"` and its rationale.
5. In Grafana, open **Explore → Tempo** and run
   `{ resource.service.name = "nettriage-triage" && resource.deployment.environment.name = "dev" }`.
   There is a `triage.explain` trace with `triage.generate` inside it. `triage.generate` shows
   `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` and
   `gen_ai.response.finish_reasons`, and `triage.explain` links to the `analyze.upload` trace.
6. Pause the AI and watch it skip:
   1. `aws login --profile nettriage` (skip this if you're signed in), then `just pause-ai`.
      Expected:
      `AI explanations in dev are paused (/nettriage/dev/kill/ai-enabled = false). The triage worker picks this up within a minute.`
   2. Wait a minute, repeat B7 steps 3 and 4 (a new upload in the same org), and wait 30 seconds.
   3. Read the newest finding as in step 2: `ai_analysis` has `status: "skipped_budget"` and
      `error_code: "ai_disabled"`.
   4. `just resume-ai`. Expected: `AI explanations in dev are on (… = true). …`
7. Delete the test org as in B7 step 10.

## Part C: when things go wrong
````

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
kept. A deploy never switches uploads back on.

```
with:
```markdown
kept. A deploy never switches uploads back on.

### Pause AI explanations in an emergency
AI explanations have a kill switch in SSM (spec §9.7). Flipping it needs your AWS session.
1. `aws login --profile nettriage`
2. Pause: `just pause-ai`. Expected:
   `AI explanations in dev are paused (/nettriage/dev/kill/ai-enabled = false). The triage worker picks this up within a minute.`
3. Resume later: `just resume-ai`. Expected: `AI explanations in dev are on (… = true). …`

Within a minute, the triage worker stops calling Bedrock: new findings get `skipped_budget` with
`error_code: "ai_disabled"`, and answers it already has are still shown. Uploads and analysis
carry on. A deploy never switches the AI back on.

### An AI explanation is missing
When Bedrock is throttled, slow or down, the triage worker hands the finding back, and SQS
delivers it again 24 minutes later, three times at most. The first call of a day can take
minutes while Bedrock prepares the answer's schema, so a first try may time out. After the
third failure the finding gets `status: "failed"`, and the message waits in
`nettriage-dev-triage-dlq` for 14 days.
1. In the AWS console, with the Region set to Europe (Stockholm), open **CloudWatch → Log groups
   → /aws/lambda/nettriage-dev-triage**, and open the log stream from around the upload's time.
   Look for `finding_explain_later` and `triage_failed` lines, and send Claude their
   `error_code` (the logs never hold the finding's data, the prompt or the answer).
2. A finding's `ai_analysis.error_code` says why it has no explanation:

   | `error_code` | What it means |
   |---|---|
   | `ai_disabled` | The AI switch is off: `just resume-ai` |
   | `budget_exhausted_org` | The org used its 100,000 tokens today; the budget resets at midnight UTC |
   | `budget_exhausted_global` | All orgs together spent $0.50 today; the cap resets at midnight UTC |
   | `budget_unavailable` | DynamoDB couldn't be read; send Claude the time |
   | `provider_denied` | Bedrock refused the worker: the $5 budget action ran (next section), or the role lacks a permission. Send Claude the time |
   | `provider_rejected` | Bedrock refused the request itself; send Claude the time |
   | `provider_throttled`, `provider_timeout`, `provider_unavailable` | Bedrock failed three times in a row; Plan 5c adds a retry button. If every finding fails with `provider_throttled`, see "Bedrock refuses every call" below |
   | `checks_failed` (with `status: "invalid_output"`) | The model's answer failed the checks twice, so nothing was added to the finding. Send Claude the finding's `id` |

### Bedrock refuses every call: "Too many tokens per day"
AWS starts new accounts with Bedrock's daily token quota at or near 0. The playground then answers
`ThrottlingException: Too many tokens per day, please wait before trying again.` on the first
prompt, and every finding ends as `failed` with `provider_throttled`. Waiting doesn't help; AWS
has to raise the quota.
1. In the AWS console, with the Region set to Europe (Stockholm), open **Service Quotas → AWS
   services → Amazon Bedrock**. In the search box, type `Cross-Model Max Tokens Per Day`, and note
   its **Applied account-level quota value** and whether it's **Adjustable**.
2. If it's adjustable: choose it, then **Request increase at account level**, enter `5000000`
   (the most the $0.50 daily cap could spend on gpt-oss-20b), and choose **Request**.
3. If it isn't adjustable, or the request is denied: open **Support Center → Create case →
   Account and billing**. Pick the closest category (for example, other account questions), and
   ask AWS to verify the account and lift the initial Bedrock limits. Say which model and Region
   failed (`openai.gpt-oss-20b-1:0`, on demand, eu-north-1), the quota's value, and the use: at
   most 5 million tokens a day. Account and billing cases are free on every support plan.
4. When AWS answers, send a short prompt in the Bedrock playground (Stockholm, gpt-oss-20b). A
   reply means the quota works; then run B9 again.

### Bedrock was cut off at $5
An email from AWS Budgets says the month's usage passed $5, and the `nettriage-deny-bedrock`
policy is now attached to `nettriage-dev-triage`: new findings get `provider_denied`. Decide
with Claude before undoing it, because something spent far more than the AI budgets allow. To
undo it, open **IAM → Roles → nettriage-dev-triage → Permissions**, select
`nettriage-deny-bedrock` and choose **Remove**.

```

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
| `STOP: Database migrations failed; nothing was deployed. …` | Send the output to Claude. Nothing in AWS changed |
| `STOP: Couldn't give the database role app_api a login …` (or `app_analyze`) | Check the stored string (A7), then send the output to Claude |
| `STOP: Syncing reference data failed; nothing in AWS changed. …` | The migrations ran, but the detectors and ATT&CK techniques weren't loaded. Send the output to Claude |
| `FAIL  Grafana OTLP endpoint in terraform.tfvars` | Put your endpoint in `terraform.tfvars` (A3) |
| `FAIL  Lambda Web Adapter layer` or `FAIL  OpenTelemetry collector layer` … `isn't a eu-north-1 layer ARN; fix it in terraform.tfvars` or `not found or not shared; check the layer's current version…` | Ask Claude to update the layer ARN to its current version |
```
with:
```markdown
| `STOP: Database migrations failed; nothing was deployed. …` | Send the output to Claude. Nothing in AWS changed |
| `STOP: Couldn't give the database role app_api a login …` (or `app_analyze`, `app_triage`) | Check the stored string (A7), then send the output to Claude |
| `STOP: Syncing reference data failed; nothing in AWS changed. …` | The migrations ran, but the detectors and ATT&CK techniques weren't loaded. Send the output to Claude |
| `FAIL  Grafana OTLP endpoint in terraform.tfvars` | Put your endpoint in `terraform.tfvars` (A3) |
| `FAIL  Triage model on Bedrock` … `isn't offered in eu-north-1` or `isn't active and on demand` | Bedrock no longer offers the model there. Send the output to Claude, who picks another model with you |
| `FAIL  Lambda Web Adapter layer` or `FAIL  OpenTelemetry collector layer` … `isn't a eu-north-1 layer ARN; fix it in terraform.tfvars` or `not found or not shared; check the layer's current version…` | Ask Claude to update the layer ARN to its current version |
```

- [ ] **Step 3: Name the new owner-only commands, and the highlight**

In `CLAUDE.md`, replace:
```markdown
and `just plan-dev` when the owner asks, but never runs `aws login`, `just bootstrap`,
`just store-grafana-token`, `just store-database-url`, `just pause-uploads`, `just resume-uploads`
or `just deploy-*`.

```
with:
```markdown
and `just plan-dev` when the owner asks, but never runs `aws login`, `just bootstrap`,
`just store-grafana-token`, `just store-database-url`, `just pause-uploads`, `just resume-uploads`,
`just pause-ai`, `just resume-ai` or `just deploy-*`.

```

In `README.md`, replace:
```markdown
- **Triage with optimistic concurrency**: a finding's status and assignee change only with `If-Match` naming the version the caller read, so two analysts can't silently overwrite each other (412 instead); every change and comment is in the finding's history and the audit log.
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for each function.
```
with:
```markdown
- **Triage with optimistic concurrency**: a finding's status and assignee change only with `If-Match` naming the version the caller read, so two analysts can't silently overwrite each other (412 instead); every change and comment is in the finding's history and the audit log.
- **AI explanations with guardrails**: each upload's 20 most severe findings are explained by gpt-oss-20b on Amazon Bedrock from the finding's typed fields only. An answer that names an address, port or technique outside the data is refused, and every call is paid for in advance from a daily token budget per org and a $0.50 daily cap across all orgs, which fail closed.
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for each function.
```

- [ ] **Step 4: Check and commit**

Run: `git diff --check`
Expected: no output.

```bash
git add docs CLAUDE.md README.md
git commit -m "docs: live AI triage in the spec, runbook B9 and A8, and Part C" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10 (Claude, then the owner): Pull request, deploy, cutoff and a first explanation

- [ ] **Step 1 (Claude):**
  - Run `just lint test tools-test edge-test web-check tf-check pin-check cloud-check detection-report build-lambda`; everything must pass.
  - Get a final review of the whole branch, fix what it finds, then push `plan-5b/ai-live`.
  - Open the PR, watch CI, and request a Copilot review.
- [ ] **Step 2 (owner):** In the Bedrock console in Stockholm, check that gpt-oss-20b answers in the playground (Owner prerequisites).
- [ ] **Step 3 (owner):** Review the PR, then squash-merge it.
- [ ] **Step 4 (owner):** Runbook B2 (`just deploy-dev`). Expected:
  - `PASS  Triage model on Bedrock  openai.gpt-oss-20b-1:0 in eu-north-1` in the preflight;
  - `Database migrated. New logins: app_triage.` (migrations `0008` and `0009`), then `Reference data synced: 3 detectors, 12 ATT&CK techniques.`;
  - `Plan: 13 to add, 2 to change, 0 to destroy`:
    - to add: the switch, the triage queue and its DLQ, the triage log group, role and its five policies, the function, its event source mapping, and the analyze role's send policy;
    - to change: the `api` and `analyze` functions;
  - fifteen smoke `PASS` lines.
- [ ] **Step 5 (owner):** Runbook A8 (`just bootstrap you@example.com`, with your anomaly monitor ARN if you used one). Expected: `Plan: 4 to add, 1 to change, 0 to destroy`.
- [ ] **Step 6 (owner):** Runbook B9. Expected: the finding's `ai_analysis` is `succeeded` with gpt-oss-20b's output and its cost. With the switch off, the next upload's finding is `skipped_budget` (`ai_disabled`).

## Plan 5b is done when

- [ ] `just lint test tools-test tf-check` passes locally and CI passes.
- [ ] The PR is merged through review, with every thread resolved.
- [ ] Runbook B9 shows a succeeded explanation on dev, and A8's budget action exists.

## Spec coverage of this plan

| Spec | Covered here |
|---|---|
| §3.2 Bedrock, the triage worker; §3.5 `triage` (sizing amended) | Tasks 4 and 7; Task 9 amends §3.2 and §3.5 |
| §4.2 analyze → triage queue → triage, with the traceparent | Tasks 4 and 5 |
| §5.4 `app_triage` audits, `app_analyze` reads severity | Task 1 |
| §5.5 `BUDGET#` refusal flags | Task 3; Task 9 amends §5.5 |
| §5.7 automatic triage of up to 20 findings per upload | Task 5 |
| §6.7 the $5 Budgets action | Task 7; Task 9 amends §6.7 (credits, lag, the bootstrap) |
| §7 the finding's latest AI analysis | Task 6 (moved from Plan 5c by the owner) |
| §7 `POST …/ai-analyses`, `PUT …/feedback`, `GET …/usage` | Plan 5c |
| §8.3 `BedrockProvider`, structured output, timeout, retries, model IDs and prices | Task 2; Task 9 amends §8.3 (candidates as checked) |
| §8.3 model selection by evals; §8.5 evals | Plan 5c |
| §8.6 Bedrock throttled or down; the AI kill switch | Tasks 3 and 4; Task 9 amends §8.6 |
| §9.1 the triage worker's telemetry, `triage.explain` | Tasks 4 and 7 |
| §9.2 `nettriage.queue.message.age` {queue=triage} | Task 4 |
| §9.4 `budget.exhausted` | Task 3 |
| §9.4 `ai.rerun_requested` | Plan 5c |
| §9.5, §9.6 the AI dashboard, the AI coverage SLO and the triage DLQ alarm | Plan 7 |
| §9.7 the `ai_enabled` kill switch | Tasks 3, 4, 7 and 8 |
| §13.2 Bedrock availability | Task 8 (the preflight); Task 9 records what was checked |
| §14 owner checklist: Bedrock | Owner prerequisites; Task 9 amends §14 |
