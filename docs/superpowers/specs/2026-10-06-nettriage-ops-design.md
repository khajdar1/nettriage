# NetTriage: Keeping It Running (Plan 7a design)

| | |
|---|---|
| **Status** | Design approved by the owner on 2026-10-06; this written spec awaits the owner's review |
| **Amends** | `2026-09-26-nettriage-m1-design.md` (revision 2): §3.5 (the `ops` function's size), §5.4 (`app_ops`, `app_backup`), §5.6 (the backups bucket), §9.2 (one metric), §9.6 (the probe), §9.7 (maintenance, backups, the restore drill). Plan 7a's docs task copies the decisions below into those sections |
| **Plan series** | Plan 7 ("operations") is in three parts (the owner's decision, 2026-10-06): **7a (this spec): keeping it running**; 7b: seeing it (tracing of database and AWS calls, dashboards, SLO alerts, deploy annotations); 7c: shipping it (prod, promotion, CloudWatch alarms, browser smoke tests, SBOM and provenance, ZAP) |

## 1. Why

NetTriage runs on dev, but nothing looks after it between deploys:
- an upload whose analysis crashed on its last try shows **Analyzing** for ever;
- an upload whose file never came stays **Waiting for the file**;
- expired invitations and audit rows pile up, and the audit log has no end;
- nothing checks that the app answers, and nothing but Neon's 6-hour restore window protects the data.

The owner chose this part of Plan 7 first, while Bedrock is blocked (2026-10-06).

**Success:**
- Uploads never stay waiting or analyzing for more than about two hours.
- Old invitations and audit rows (over 180 days) disappear on their own.
- The app is probed every 5 minutes, the database and DynamoDB every hour, without keeping Neon awake.
- Every night the database is dumped to S3 and kept for 7 days. Every week, the latest dump is restored and its row counts are checked, so the backups are known to work. Recovery point ≤ 24 h, recovery time ≤ 1 h.
- No role skips row-level security, and the audit log stays append-only for everyone but the cleanup.

**Out of scope** (later parts or plans):
- alerts on any of this, dashboards, database and AWS call tracing: 7b;
- prod, CloudWatch alarms, browser smoke tests: 7c;
- the demo page in the probe: 6c, once the demo exists.

## 2. The `ops` function

One new function, `ops`, in the same package and runtime as the others (Python 3.14, arm64). Amazon EventBridge Scheduler invokes it with the job's name.

| Schedule (UTC) | Job | What it does |
|---|---|---|
| Every 5 minutes | `probe` | Fetches `<app URL>/api/health` through CloudFront |
| Every hour, at :17 | `check` | `SELECT 1` on Neon as `app_ops`, and one DynamoDB read |
| Every night, 02:00 | `backup` | Dumps the database to S3, with a manifest |
| Every day, 03:00 | `cleanup` | The four maintenance rules (2.4) |
| Every Sunday, 04:00 | `restore_drill` | Restores the latest dump and checks it (2.5) |

The owner can also start the drill with `just restore-drill-<stage>`, which invokes `ops` with the owner's session.

### 2.1 `probe`

- **Request:** a `GET` with a 10-second timeout.
- **Success:** a 200 whose body is `{"status": "ok", …}`.
- **Result:** the metric `nettriage.probe.success` {check: `health`}, 1 or 0. A failed probe doesn't raise: the next one comes in 5 minutes.
- The health route never touches the database (Plan 4a), so the probe never wakes Neon.

### 2.2 `check`

- **Database:** `SELECT 1` through the pooler as `app_ops`, with a 10-second connect timeout (Neon wakes in a few seconds).
- **DynamoDB:** a `GetItem` of a fixed key in the `runtime` table. A missing item still counts as success.
- **Result:** `nettriage.probe.success` {check: `database` | `dynamodb`}, 1 or 0. Like the probe, a failed check doesn't raise: the next one comes in an hour.
- Running once an hour wakes Neon at most once an hour, as §9.6 intends.

### 2.3 `backup`

1. Connect as `app_backup` directly, not through Neon's pooler: `pg_dump` needs a session, and the pooler works by transaction.
2. In a `REPEATABLE READ` transaction, export a snapshot (`pg_export_snapshot()`) and count every table's rows.
3. Run `pg_dump -Fc --enable-row-security --snapshot=<that snapshot>` to `/tmp`, so the dump and the counts see the same moment.
4. Upload `pg/<YYYY-MM-DD>.dump` and `pg/<YYYY-MM-DD>.json` to the stage's backups bucket. The manifest holds `created_at`, the server and `pg_dump` versions, and each table's row count. A rerun on the same day replaces both.

The object names hold dates only, never tenant data. If Neon's server version passes the bundled client's (a future Postgres 18), `pg_dump` refuses. The backup fails loudly then, and the runbook says to update the client.

### 2.4 `cleanup`

Four rules. Each runs in its own transaction as `app_ops`, and records how many rows it changed (counts only):

| Rule | Rows | Change |
|---|---|---|
| **Abandoned uploads** | `status = 'pending_upload'` and `created_at` over 2 hours ago | `status = 'expired'` |
| **Stuck analyses** | `status = 'processing'` and `created_at` over 2 hours ago | `status = 'failed'`, `failure_reason` = "NetTriage couldn't analyze this file after three tries. Upload it again later." (the analyze worker's own sentence), `processed_at = now()` |
| **Old invitations** | never accepted (`accepted_at IS NULL`) and past `expires_at` | deleted. The members page already hides them; accepted invitations stay as history |
| **Old audit rows** | `created_at` over 180 days ago | deleted |

**Why 2 hours for uploads (amends §9.7's 1 hour):** an upload's file arrives within the signed URL's 5 minutes. Its analysis message can be delivered three times, 30 minutes apart (6 × the 5-minute function timeout), and each delivery claims an upload that is still `pending_upload` or `processing`. Expiring at 1 hour could refuse a valid file on its third try. At 2 hours, every delivery is over.

### 2.5 `restore_drill`

1. Download the newest dump and its manifest.
2. Start a throwaway Postgres 17 server inside the function, in `/tmp`, on a Unix socket. It uses `mmap` shared memory, because Lambda has no `/dev/shm`.
3. Create the application roles the dump's policies name, and a non-superuser owner. Restore as that owner with `pg_restore --no-owner --no-privileges --exit-on-error`, as a real recovery into Neon would (Neon's owner isn't a superuser).
4. Count every table's rows and compare them with the manifest.
5. Stop the server and delete `/tmp`.

A mismatch, or any error, fails the job. The result is the metric `nettriage.ops.runs` {job: `restore_drill`, outcome}, plus the function's own success or error. Nothing leaves AWS, and the owner installs nothing.

**Fallback (decided now):** CI might show that Postgres can't run inside the Lambda environment (Plan 7a's first task proves it). The drill then restores into a scratch database in the Neon project and drops it afterwards, through a role that may create only its own databases.

## 3. Database (one migration)

### 3.1 `app_ops`: the cleanup and the hourly check

- `NOLOGIN` in the migration; the deploy gives it its login, like every function's role.
- **Grants:**
  - `SELECT` and `UPDATE (status, failure_reason, processed_at)` on `uploads`;
  - `SELECT` and `DELETE` on `invitations` and `audit_log`.
- **Row policies, one per rule:** each permits only the rows its rule targets. For example, an `UPDATE` on `uploads` only `USING (status IN ('pending_upload', 'processing') AND created_at < now() - interval '2 hours')`, and `WITH CHECK (status IN ('expired', 'failed'))`. The role can't touch any other row, even by mistake.
- **The audit log's trigger** keeps refusing `UPDATE` and `DELETE`, with one exception: `DELETE` by `app_ops`. Its row policy limits that to rows over 180 days old.

**Amends §5.4**, which planned `SECURITY DEFINER` functions. Every tenant table forces row-level security, even for its owner (Plan 3a), so a function running as the owner would see no rows. Narrow grants and row policies give the same limits without exempting anyone.

### 3.2 `app_backup`: the nightly dump

- `NOLOGIN`. `SELECT` on every table and sequence, and a `SELECT` policy `USING (true)` on every row-secured table. `pg_dump --enable-row-security` then reads every row.
- **Amends §5.4**, which planned `BYPASSRLS`:
  - no role, not even the backup's, skips row-level security;
  - Neon's owner may not be able to grant `BYPASSRLS` at all.
- **Guards:** both new roles get the migration's checks (neither is a superuser, has `BYPASSRLS` or owns the schema). A test fails if any table is missing `app_backup`'s grant or policy, so a future table can't silently drop out of the backups.

## 4. Infrastructure (Terraform)

- **The function:**
  - `ops`: 1024 MB, a 600-second timeout, and 2 GB of ephemeral storage (room for a dump and its restore);
  - CloudWatch logs kept 7 days, and the OpenTelemetry collector layer like the others;
  - **amends §3.5** (256 MB, 60 s): the dump and the drill need more.
- **The Postgres client:**
  - a Lambda layer used only by `ops`, so the other functions' packages don't grow;
  - it holds Postgres 17's `pg_dump`, `pg_restore`, `initdb` and server, from the PostgreSQL project's own packages for EL9 arm64, with the libraries they need;
  - it's downloaded at build time with pinned SHA-256 checksums.
- **Schedules:** five EventBridge Scheduler schedules, through a role that may only invoke `ops`. `probe` and `check` aren't retried, because the next run comes soon. `backup`, `cleanup` and `restore_drill` keep Lambda's two retries; they're idempotent.
- **The backups bucket:**
  - `nettriage-<stage>-backups-<suffix>`, with the uploads bucket's suffix;
  - private, TLS-only, encrypted with SSE-S3, public access blocked, objects deleted after 7 days;
  - `ops` may put and get objects under `pg/` and list the bucket, and the owner's session can read it;
  - **amends §5.6**, which planned one bucket for all stages: a per-stage bucket is created by the stage's own deploy and needs no change to the bootstrap.
- **`ops`'s other rights:** read its two connection strings from SSM, `GetItem` on the `runtime` table, and nothing else.

## 5. Deploy

- The deploy gives `app_ops` and `app_backup` their logins and stores their URLs in SSM, like the other roles:
  - `app_ops` connects through the pooler;
  - `app_backup` connects to the direct endpoint.
- The first deploy after this plan:
  - migrates once;
  - prints `New logins: app_ops, app_backup.`;
  - adds the function, its layer, the schedules and the bucket.
- `just restore-drill-<stage>` is an owner command, listed in `CLAUDE.md` with the others Claude never runs.

## 6. Telemetry

- **Traces:** `service.name` `nettriage-ops`, one span per job (`ops.probe`, `ops.check`, `ops.backup`, `ops.cleanup`, `ops.restore_drill`), with counts and sizes as attributes, never data.
- **Metrics:**
  - `nettriage.probe.success` {check} (§9.2);
  - new: `nettriage.ops.runs` {job, outcome}, so 7b can alert on a backup or a drill that failed or didn't run.
- **Logs:** JSON, as everywhere. They never hold connection strings, row contents, or a database error's message: errors are logged by type only (Plan 4b).

## 7. The flaky test

`test_a_limited_health_check_writes_no_audit_row` once found an audit row it shouldn't have (Plan 6b's CI). Plan 7a finds the cause and fixes it test-first.

## 8. Testing

- **Each job against real Postgres:**
  - every cleanup rule, on both sides of its boundary;
  - every `app_ops` policy refusing the rows it must not touch;
  - the trigger refusing any other role's deletes;
  - `app_backup` reading every row of every table in every organization;
  - the dump's counts matching its snapshot.
- **The Postgres client, inside AWS's own Lambda image, in CI:**
  - on GitHub's arm64 runner (free for this public repository), using AWS's public `lambda/python:3.14` image, so CI still needs no cloud access;
  - it dumps CI's database, starts the throwaway server, restores the dump and compares the counts, just as the function will.
- **Other checks:**
  - Terraform: `validate`, `test`, tflint and Checkov, as for every module;
  - the deploy's role and URL rules.

## 9. The owner's steps

1. Deploy (runbook B2): one migration, two new logins, and the new function, layer, schedules and bucket.
2. The next morning, a new runbook section:
   - see the night's dump in the backups bucket;
   - run `just restore-drill-dev`, and see it say the counts match.

   This is the restore drill §12 asks for, and from then on it repeats every Sunday by itself.
