# NetTriage Plan 7a: Keeping It Running Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make NetTriage look after itself between deploys (the owner's choice, 2026-10-06):
- **a nightly backup** of the database to S3, kept 7 days, and **a weekly restore drill** that restores the newest backup into a throwaway Postgres and checks every table's rows against it, so the backups are known to work;
- **a daily cleanup:** uploads waiting or analyzing for over two hours expire or fail, and lapsed invitations and audit rows over 180 days old are deleted;
- **a probe** of the app every 5 minutes, and of the database and DynamoDB every hour, without keeping Neon awake;
- the owner's **`just restore-drill-dev`**, and the flaky test `test_a_limited_health_check_writes_no_audit_row` fixed.

**Architecture:**
- **One `ops` Lambda runs five jobs by name.** It is the shared backend package with a handler of its own (1024 MB, 600 s, 2 GB of `/tmp`). Five EventBridge Scheduler schedules invoke it with `{"job": "<name>"}`: `probe`, `check`, `backup`, `cleanup` and `restore_drill`. Each job answers with counts only, and records `nettriage.ops.runs` {job, outcome} and a span `ops.<job>`. The probe and the check record `nettriage.probe.success` {check} and never raise.
- **One migration adds two roles, and neither skips row-level security.**
  - `app_ops` has narrow column grants and one row policy per cleanup rule. The audit log's trigger lets it, and only it, delete.
  - `app_backup` may `SELECT` every table, and has a `USING (true)` read policy on every row-secured table, so `pg_dump --enable-row-security` reads every row.
- **The backup:** in one `REPEATABLE READ` transaction, as `app_backup` on Neon's direct endpoint, export a snapshot and count every table. `pg_dump --snapshot` dumps that same moment. The dump and then its manifest go to `pg/<day>.dump` and `pg/<day>.json`.
- **The drill:** a throwaway Postgres 17 in `/tmp`, on a Unix socket, with `mmap` shared memory (Lambda has no `/dev/shm`). The newest dump is restored as a non-superuser owner, the rows are counted as the server's superuser, and the counts are compared with the manifest.
- **Postgres 17.11 for Lambda is built in CI.** It is compiled from source in Amazon Linux 2023 on GitHub's arm64 runner and packed as a layer, the `pg-client-zip` artifact. CI then proves it inside AWS's public Lambda image, as a non-root user without `/dev/shm`. A second CI job runs the real handler there end to end, with moto's server standing in for S3, SSM and DynamoDB. CI still holds no cloud access.
- **Terraform** adds `infra/modules/ops`. **The deploy** gives the roles their logins, hands Terraform the layer, and adds `just restore-drill-dev`.

**Tech Stack:** Python 3.14 · SQLAlchemy Core with psycopg 3 on Postgres 17 · boto3 and moto (in process, and its server in CI) · httpx · OpenTelemetry · pytest · Postgres 17.11 from source (`pg_dump`, `pg_restore`, `initdb`, `pg_ctl`, `postgres`) · Terraform with the AWS provider 6 (Lambda layers, EventBridge Scheduler, S3) · GitHub Actions on `ubuntu-24.04-arm`, with AWS's public images `amazonlinux:2023` and `lambda/python:3.14`.

**Spec:** `docs/superpowers/specs/2026-10-06-nettriage-ops-design.md` (approved by the owner on 2026-10-06: "Looks good, go ahead and start implementing"). It amends `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (revision 2) §3.5, §5.4, §5.6, §9.2, §9.6 and §9.7, and Task 12 copies its decisions into them. Read both alongside this plan; "ops §" refers to the first, "§" to the second.

**Plan series:**
- Plan 7 ("operations") has three parts (the owner's decision, 2026-10-06): **7a (this plan): keeping it running**; 7b: seeing it (tracing of database and AWS calls, dashboards, SLO alerts, deploy annotations); 7c: shipping it (prod, promotion, CloudWatch alarms, browser smoke tests, SBOM and provenance, ZAP).
- Plan 5d (the evals) and 6c (the public demo) wait for AWS to lift the Bedrock limits.

**Branch:** `plan-7a/ops`, from `main` at `4bf5af1`; it already holds the ops spec (`a2a8245`).

## Global Constraints

- **Stack.** Python 3.14. mypy `--strict` checks `src` and `tests` (`uv run python -m mypy` with no arguments, as `just lint` runs it), and Ruff checks and formats (target py314). Work test-first.
- **Commands on this Windows machine.**
  - Run Python tools as modules (`uv run python -m …`). Never pipe a heredoc into `python -` or `uv run python -`: it hangs.
  - Backend tests that touch the database need the local Postgres. Run `just db-up` in the main checkout (if it gives up during crash recovery, run it again) and copy its `.localdb/url` into the worktree's `.localdb/`. Then prefix `uv run python -m pytest` in `backend/` with `NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)"`. Don't run `just db-up` or `just test` in the worktree: they would start a second server there.
  - The tools' tests run from the repository's root: `uv run --project backend python -m pytest tools/tests/…`.
  - A module's Terraform tests run in its folder with `TF_DATA_DIR=.terraform-check` (git-ignored), as `just tf-check` does.
- **Tests import helpers** as `from conftest import …` and `from tenantdata import …`, never `from tests.…`.
- **Linux-only work runs in CI.** The Postgres build and the checks inside the Lambda image need Docker on arm64. Locally only their Python parts are tested; the PR's CI run (Task 13) is their test.
- **No role skips row-level security (ops §3).** Neither new role is a superuser, owns the schema or has `BYPASSRLS`, and no `SECURITY DEFINER` function is added.
- **Nothing secret or private leaves a job.** Logs, spans, metrics, the function's answer and errors, and the owner's terminal never hold a connection string, a password, row contents, or a database or program error's message (§9.3, Plan 4b). Failures are named by their type. The drill's own failures name only a day and tables.
- **CI holds no cloud access (ADR 0013).** No AWS credential variable is named in a workflow: moto's throwaway key pair is written to a file named by `AWS_SHARED_CREDENTIALS_FILE`. `tools/check_no_cloud_access.py` must pass and must not be changed. Every action is pinned to a commit SHA.
- **Infrastructure only through Terraform**, in eu-north-1.
- **Owner-only commands.** Claude never runs `aws login`, `just bootstrap`, `just store-*`, `just pause-*`, `just resume-*`, `just restore-drill-*` or `just deploy-*`.
- **Commits.** Use Conventional Commits. Every commit made by Claude ends with the trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Decisions this plan makes

1. **The owner's decisions (2026-10-06):**
   - Plan 7 in three parts, starting with 7a;
   - the nightly backup runs in the `ops` function;
   - the restore drill runs inside AWS, weekly. If CI had shown Postgres can't run inside Lambda, the fallback was a scratch database in the Neon project; it proved unnecessary.
2. **Postgres is built from source.** The first spike found that the PostgreSQL project's EL9 packages need libraries Lambda's image lacks (LDAP, ICU, PAM, systemd). Postgres 17.11, pinned by its SHA-256, is configured `--without-readline --without-icu --with-openssl --with-zlib` into `/opt/pg`, which is where Lambda mounts a layer. The layer holds only the five programs, `libpq.so.5` under the name they load, the server's modules and `share/`. It is packed reproducibly (fixed dates, sorted names), with programs and libraries executable.
3. **Postgres in Lambda's sandbox:**
   - `dynamic_shared_memory_type=mmap`, since there is no `/dev/shm`;
   - a Unix socket in the server's folder, and no TCP;
   - no durability (`fsync`, `full_page_writes` and `synchronous_commit` off);
   - `CREATE ROLE` and `CREATE DATABASE` run one statement at a time outside a transaction.
   CI runs the image as `nobody`, a user with a passwd entry like Lambda's, because `initdb` refuses a user it can't name.
4. **The restore is as a recovery into Neon would be:** the owner `restorer` isn't a superuser, the roles the dump's policies name exist first (the manifest lists them, from `pg_policies`), and `pg_restore --no-owner --no-privileges --exit-on-error` runs. The counts are taken as the superuser `drill`, past row-level security.
5. **`app_ops`'s read policy on uploads also admits `expired` and `failed` uploads over 2 hours old.** Postgres checks the row an `UPDATE … WHERE` writes against the `SELECT` policies too, so a read policy of only `pending_upload` and `processing` would refuse the rule's own change.
6. **Pending uploads expire after 2 hours, not 1 (amends §9.7).** An analysis message can be delivered three times, 30 minutes apart, and each delivery may claim an upload that is still pending.
7. **`app_backup` connects to Neon's direct endpoint, and `app_ops` through the pooler.** `pg_dump` needs a session, and the pooler works by transaction.
8. **A bucket per stage,** `nettriage-<stage>-backups-<8 hex of the account ID's SHA-256>`, like the uploads bucket (amends §5.6). The dump is uploaded first and its manifest last, and the newest backup is the newest day that has both.
9. **The probe fetches `/api/health` only.** The demo page joins it in Plan 6c. The check runs at :17, off the hour. A failed probe or check is a 0 in `nettriage.probe.success`, not an error.
10. **Retries:** the probe and the check get no scheduler retry; the backup, the cleanup and the drill get two. Every schedule runs in UTC, with no flexible window and a maximum event age of 1 hour.
11. **Errors leave the function by type only.** Like the analyze worker's `AnalysisFailed`, any failure other than the drill's own (`DrillFailed`, `NoBackup`) leaves as `JobFailed(<type>)` raised `from None`, because Lambda logs what the handler raises. The span, which is closed before that, records the original type.
12. **A drill cut short by the timeout** leaves its folder in a warm function's `/tmp`. The next drill removes it first, or `initdb` would refuse.
13. **CI's end-to-end job** runs the CI-built package and layer in AWS's Lambda image, as `nobody` and `--ipc=none`, against a migrated Postgres 17. moto's server is reached through `AWS_ENDPOINT_URL`. It runs `check`, `cleanup`, `backup` and `restore_drill`, and checks that the drill restored exactly what the backup counted.
14. **`just restore-drill-dev`** invokes the function with the owner's session, waits up to 660 s, and never retries (`AWS_MAX_ATTEMPTS=1`), since a retry would start a second drill. It prints the drill's own failures as they are, and anything else by its type.
15. **The flaky test's cause:** the sign-in limit test drew its address at random from `198.51.100.1`–`250`. Once in 250 runs that was `.88`, the health check test's own, and the session's database kept the sign-in's sampled audit row. Each test now has its own fixed address.

## Review Focus

1. **A table added later without `app_backup`'s grant or policy** would silently drop out of the backups, with every drill still passing. Test: Task 2 `test_every_table_is_in_the_backup`.
2. **A backup cut short between its two uploads** must never be restored as the newest: a dump without its manifest isn't a backup. Test: Task 5 `test_the_latest_backup_is_the_newest_day_with_both_files`.
3. **A run cut short in a warm function** must not block or fill the next one's `/tmp`. Tests: Task 6 `test_a_drill_cut_short_by_the_timeout_leaves_nothing_in_the_next_ones_way` and `test_the_drill_leaves_nothing_behind`; Task 5 `test_the_local_dump_is_removed_once_stored` and `test_a_failed_dump_stores_nothing`.
4. **An error that quotes connection details or data** must not reach logs, spans, Lambda's error report or the owner's terminal. Tests: Task 5 `test_a_failed_dump_says_only_how_it_exited`; Task 6 `test_a_failed_restore_says_only_how_it_exited_and_still_stops_the_server`; Task 7 `test_an_error_that_could_quote_the_database_leaves_by_its_type_only` and `test_a_failed_backup_fails_the_run_and_reports_its_type_only`; Task 10 `test_any_other_failure_is_named_by_type_only`.
5. **Rows just inside a rule's boundary, or in a state no rule targets,** must be left alone, even by a mistaken statement. Tests: Task 2 `test_ops_expires_only_uploads_waiting_for_their_file_over_two_hours`, `test_ops_can_only_set_an_upload_expired_or_failed` and `test_ops_reads_nothing_beyond_its_rules`; Task 3 `test_uploads_still_waiting_for_their_file_after_two_hours_expire`.

## Owner prerequisites

- **None to build or review.** CI proves the layer and the function in AWS's own Lambda image.
- **After the merge:**
  - runbook B2: one migration, `New logins: app_ops, app_backup.`, and the new function, layer, schedules and bucket in the Terraform plan;
  - the next morning, runbook B14: see the night's backup, then run `just restore-drill-dev`. That is the restore drill Milestone 1 asks for.

## File map

| File | Responsibility | Task |
|---|---|---|
| `tools/pg_client/build.sh`, `verify.sh`, `tools/pack_pg_client.py`, `.github/workflows/ci.yml` (`pg-client`) | Postgres 17 for Lambda, built, packed and proven in CI | 1 |
| `backend/migrations/versions/0011_ops_roles.py`, `backend/tests/conftest.py` | `app_ops` and `app_backup` | 2 |
| `backend/src/nettriage/adapters/maintenance.py`, `application/uploads.py` (`GAVE_UP`) | the four cleanup rules | 3 |
| `backend/src/nettriage/adapters/probes.py` | the probe and the hourly check | 4 |
| `backend/src/nettriage/adapters/pg_client.py`, `backups.py`, `entrypoints/ops/backup.py` | the nightly backup | 5 |
| `backend/src/nettriage/adapters/pg_client.py` (`ThrowawayServer`), `entrypoints/ops/drill.py` | the restore drill | 6 |
| `backend/src/nettriage/entrypoints/ops/jobs.py`, `wiring.py`, `handler.py`, `platform/metrics.py`, `platform/config.py`, `tools/build_lambda.py` | the function: jobs by name, telemetry, wiring | 7 |
| `tools/ops_e2e.py`, `.github/workflows/ci.yml` (`ops-in-lambda`) | the jobs end to end in the Lambda image | 8 |
| `infra/modules/ops/`, `infra/envs/dev/`, `.checkov.yaml`, `justfile`, `ci.yml` (Terraform) | the function, layer, schedules and bucket | 9 |
| `tools/deploy/config.py`, `database.py`, `drill.py`, `__main__.py`, `justfile`, `CLAUDE.md` | logins, the layer for Terraform, `just restore-drill-dev` | 10 |
| `backend/tests/api/test_rate_limited_routes.py` | the flaky test | 11 |
| the M1 spec, the ops spec, `docs/runbooks/setup-and-deploy.md` (B2, B14, Part C), `README.md` | the decisions, and the owner's backups and drill | 12 |

Backend tests sit under `backend/tests/`, the tools' under `tools/tests/`, and Terraform's under the module's `tests/`. A new file is given in full after "Create `path`:". An edit to an existing file is given as "In `file`, replace: … with: …", and each quoted passage appears exactly once in the file when its step runs.

---

### Task 1: Postgres 17 for Lambda, built from source and proven in AWS's Lambda image

**Files:**
- Create: `tools/pg_client/build.sh`, `tools/pg_client/verify.sh`, `tools/pack_pg_client.py`
- Modify: `.github/workflows/ci.yml` (the `pg-client` job)
- Test: `tools/tests/test_pack_pg_client.py`

**Interfaces:**
- Consumes: nothing new. CI's `postgres:17` service, and AWS's public images `public.ecr.aws/amazonlinux/amazonlinux:2023` and `public.ecr.aws/lambda/python:3.14`.
- Produces:
  - `tools/pg_client/build.sh <out>`: builds Postgres 17.11 from its checksummed source into `<out>/pg` (installed for the prefix `/opt/pg`);
  - `tools/pack_pg_client.py --src <install> --out <zip>`, with `pack(install: Path, out: Path) -> None`, `PackError`, `PROGRAMS = ("initdb", "pg_ctl", "pg_dump", "pg_restore", "postgres")` and `LIBPQ = "libpq.so.5"`. The zip holds `pg/bin/…`, `pg/lib/…` and `pg/share/…`, so Lambda mounts the programs at `/opt/pg/bin`;
  - `tools/pg_client/verify.sh <zip>`: dumps CI's database inside the Lambda image, restores it into a throwaway server there, and fails unless the data read back is the same;
  - the CI job `pg-client` and its artifact `pg-client-zip` (`dist/pg-client.zip`), which Tasks 8 and 10 use.

- [ ] **Step 1: Write the failing tests**

Create `tools/tests/test_pack_pg_client.py`:
```python
import stat
import zipfile
from pathlib import Path

import pytest

from tools.pack_pg_client import PackError, pack

BINARIES = ("initdb", "pg_ctl", "pg_dump", "pg_restore", "postgres")


def make_install(root: Path, *, skip: str | None = None) -> Path:
    """A tree shaped like `make install` with prefix /opt/pg (spec Plan 7a §4)."""
    install = root / "pg"
    files = {
        **{f"bin/{name}": f"#{name}".encode() for name in BINARIES},
        "bin/psql": b"#psql",
        "bin/pgbench": b"#pgbench",
        "lib/libpq.so.5.17": b"libpq",
        "lib/libpq.a": b"static",
        "lib/libecpg.so.6.17": b"ecpg",
        "lib/postgresql/plpgsql.so": b"plpgsql",
        "lib/postgresql/pgxs/src/makefiles/pgxs.mk": b"pgxs",
        "include/libpq-fe.h": b"header",
        "share/postgresql/postgres.bki": b"bki",
        "share/postgresql/timezone/UTC": b"tz",
    }
    for name, content in files.items():
        if name == skip:
            continue
        path = install / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return install


def entries(out: Path) -> dict[str, zipfile.ZipInfo]:
    with zipfile.ZipFile(out) as zf:
        return {info.filename: info for info in zf.infolist()}


def test_the_layer_holds_only_what_the_ops_jobs_run(tmp_path: Path) -> None:
    out = tmp_path / "pg-client.zip"

    pack(make_install(tmp_path), out)

    assert sorted(entries(out)) == [
        "pg/bin/initdb",
        "pg/bin/pg_ctl",
        "pg/bin/pg_dump",
        "pg/bin/pg_restore",
        "pg/bin/postgres",
        "pg/lib/libpq.so.5",
        "pg/lib/postgresql/plpgsql.so",
        "pg/share/postgresql/postgres.bki",
        "pg/share/postgresql/timezone/UTC",
    ]


def test_libpq_is_stored_under_the_name_the_programs_load(tmp_path: Path) -> None:
    out = tmp_path / "pg-client.zip"

    pack(make_install(tmp_path), out)

    with zipfile.ZipFile(out) as zf:
        assert zf.read("pg/lib/libpq.so.5") == b"libpq"


def test_programs_and_libraries_are_executable_and_data_is_not(tmp_path: Path) -> None:
    out = tmp_path / "pg-client.zip"

    pack(make_install(tmp_path), out)

    modes = {name: stat.S_IMODE(info.external_attr >> 16) for name, info in entries(out).items()}
    assert modes["pg/bin/pg_dump"] == 0o755
    assert modes["pg/lib/libpq.so.5"] == 0o755
    assert modes["pg/share/postgresql/postgres.bki"] == 0o644


def test_the_same_install_always_packs_to_the_same_bytes(tmp_path: Path) -> None:
    install = make_install(tmp_path)
    first, second = tmp_path / "a.zip", tmp_path / "b.zip"

    pack(install, first)
    pack(install, second)

    assert first.read_bytes() == second.read_bytes()


@pytest.mark.parametrize(
    "missing",
    ["bin/pg_dump", "bin/postgres", "lib/libpq.so.5.17", "share/postgresql/postgres.bki"],
)
def test_an_install_missing_a_needed_file_is_refused(tmp_path: Path, missing: str) -> None:
    with pytest.raises(PackError, match="missing"):
        pack(make_install(tmp_path, skip=missing), tmp_path / "pg-client.zip")
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --project backend python -m pytest tools/tests/test_pack_pg_client.py`
Expected: FAIL: `1 error` during collection, `ModuleNotFoundError: No module named 'tools.pack_pg_client'`.

- [ ] **Step 3: Build, pack and verify the layer**

In `.github/workflows/ci.yml`, replace:
```yaml
          retention-days: 7

  frontend:
    runs-on: ubuntu-24.04
```
with:
```yaml
          retention-days: 7

  # The `ops` function's Postgres client and server (Plan 7a §4): built from source for Lambda's
  # arm64 Amazon Linux 2023, then proven inside AWS's own Lambda image, as a non-root user
  # without /dev/shm, against a real Postgres 17. The image is public, so CI still holds no
  # cloud access.
  pg-client:
    runs-on: ubuntu-24.04-arm
    services:
      postgres:
        image: postgres:17
        env:
          POSTGRES_PASSWORD: ci-only
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready --health-interval 5s --health-timeout 5s --health-retries 10
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - name: Build Postgres in Amazon Linux 2023
        run: >-
          docker run --rm -v "$PWD:/w" -w /w public.ecr.aws/amazonlinux/amazonlinux:2023
          bash tools/pg_client/build.sh build
      - name: Pack the layer
        run: python3 tools/pack_pg_client.py --src build/pg --out dist/pg-client.zip
      - name: Check the layer inside AWS's Lambda image
        env:
          PGHOST: localhost
          PGUSER: postgres
          PGPASSWORD: ci-only
        run: |
          sudo apt-get update -qq && sudo apt-get install -y -qq postgresql-client > /dev/null
          bash tools/pg_client/verify.sh dist/pg-client.zip
      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: pg-client-zip
          path: dist/pg-client.zip
          retention-days: 7

  frontend:
    runs-on: ubuntu-24.04
```

Create `tools/pack_pg_client.py`:
```python
"""Package the Postgres build from tools/pg_client/build.sh as the `ops` function's layer (Plan 7a).

Usage, from the repository root:
    python tools/pack_pg_client.py --src build/pg --out dist/pg-client.zip

A layer extracts to /opt, so every entry starts with `pg/`, the build's prefix (/opt/pg). Only
what the backup and the restore drill run is kept: five programs, libpq under the name they load
it by, the server's modules and its data files. The zip is deterministic, so an unchanged build
publishes no new layer version.
"""

from __future__ import annotations

import argparse
import stat
import sys
import zipfile
from pathlib import Path

PROGRAMS = ("initdb", "pg_ctl", "pg_dump", "pg_restore", "postgres")
LIBPQ = "libpq.so.5"
REQUIRED = (*(f"bin/{name}" for name in PROGRAMS), f"lib/{LIBPQ}", "share/postgresql/postgres.bki")
FIXED_DATE = (2020, 1, 1, 0, 0, 0)


class PackError(Exception):
    """The build is missing something the layer needs."""


def _libpq(lib: Path) -> Path | None:
    """The real file behind libpq's soname (libpq.so.5.17); the links to it aren't packed."""
    versions = sorted(lib.glob(f"{LIBPQ}.*"))
    return versions[-1] if versions else None


def _members(install: Path) -> dict[str, Path]:
    """Each kept file, by its path inside the layer's pg/ folder."""
    members: dict[str, Path] = {}
    for name in PROGRAMS:
        path = install / "bin" / name
        if path.is_file():
            members[f"bin/{name}"] = path
    libpq = _libpq(install / "lib")
    if libpq is not None:
        members[f"lib/{LIBPQ}"] = libpq
    for module in sorted((install / "lib" / "postgresql").glob("*.so")):
        members[f"lib/postgresql/{module.name}"] = module
    for path in sorted((install / "share").rglob("*")):
        if path.is_file():
            members[path.relative_to(install).as_posix()] = path
    return members


def pack(install: Path, out: Path) -> None:
    members = _members(install)
    missing = [name for name in REQUIRED if name not in members]
    if missing:
        raise PackError(f"the build is missing {', '.join(missing)}")
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(members):
            info = zipfile.ZipInfo(f"pg/{name}", date_time=FIXED_DATE)
            mode = 0o755 if name.startswith(("bin/", "lib/")) else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, members[name].read_bytes())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--src", type=Path, required=True, help="the install prefix, e.g. build/pg")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        pack(args.src, args.out)
    except PackError as error:
        print(f"pack_pg_client: {error}", file=sys.stderr)
        return 1
    print(f"wrote {args.out} ({args.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Create `tools/pg_client/build.sh`:
```bash
#!/usr/bin/env bash
# Builds Postgres for the `ops` function's layer (Plan 7a §4), inside Amazon Linux 2023, the OS
# of Lambda's python3.14 runtime, from the PostgreSQL project's source, checked against its
# published SHA-256. LDAP, ICU, PAM, systemd and readline are left out: the Lambda image has
# none of their libraries, and the backup and the drill don't need them.
#
# CI runs it on an arm64 runner:
#   docker run --rm -v "$PWD:/w" -w /w public.ecr.aws/amazonlinux/amazonlinux:2023 \
#     bash tools/pg_client/build.sh build
# and finds the install under build/pg, for tools/pack_pg_client.py.
set -euo pipefail

VERSION=17.11
SHA256=dd27f2b3c59e73ed14aa3324901242bf69a032a6347805f274e6260322d42979
OUT=${1:?usage: build.sh OUT_DIR}

dnf install -y -q gcc make tar bzip2 binutils openssl-devel zlib-devel perl bison flex > /dev/null

work=$(mktemp -d)
curl -fsSL -o "$work/postgresql.tar.bz2" \
  "https://ftp.postgresql.org/pub/source/v$VERSION/postgresql-$VERSION.tar.bz2"
echo "$SHA256  $work/postgresql.tar.bz2" | sha256sum -c -
tar -xjf "$work/postgresql.tar.bz2" -C "$work"

cd "$work/postgresql-$VERSION"
./configure --prefix=/opt/pg --without-readline --without-icu --with-openssl --with-zlib > /dev/null
make -j"$(nproc)" -s > /dev/null
make -s install > /dev/null
# Only the programs and shared libraries: pgxs also installs shell scripts strip can't read.
find /opt/pg/bin -type f -exec strip --strip-unneeded {} +
find /opt/pg/lib -type f -name '*.so*' -exec strip --strip-unneeded {} +

mkdir -p "/w/$OUT"
cp -a /opt/pg "/w/$OUT/"
echo "built Postgres $VERSION into $OUT/pg"
```

Create `tools/pg_client/verify.sh`:
```bash
#!/usr/bin/env bash
# Proves the layer runs where the `ops` function will (Plan 7a §8): inside AWS's Lambda image,
# as a non-root user and without /dev/shm, like Lambda. Using only what the layer holds, it
# checks every program and library finds what it links to, dumps the database PG* points at,
# restores the dump into a throwaway server as a non-superuser owner, and compares the restored
# data with the original's.
#
# CI runs it on the arm64 runner, against its Postgres service:
#   PGHOST=localhost PGUSER=postgres PGPASSWORD=… bash tools/pg_client/verify.sh dist/pg-client.zip
set -euo pipefail

LAYER=${1:?usage: verify.sh LAYER_ZIP}
work=$(mktemp -d)
unzip -q "$LAYER" -d "$work/opt"
mkdir -m 777 "$work/out"

# Something worth restoring: rows behind forced row-level security, a policy naming a role, and
# a PL/pgSQL trigger, as NetTriage's tables have.
psql -q -d postgres -c "DROP DATABASE IF EXISTS layer_check" -c "CREATE DATABASE layer_check"
psql -q -d layer_check -c "
  DO \$\$ BEGIN CREATE ROLE layer_reader NOLOGIN; EXCEPTION WHEN duplicate_object THEN END \$\$;
  CREATE TABLE notes (id int PRIMARY KEY, org text NOT NULL, body text NOT NULL);
  INSERT INTO notes SELECT g, 'org-' || (g % 3), md5(g::text) FROM generate_series(1, 2000) g;
  ALTER TABLE notes ENABLE ROW LEVEL SECURITY;
  ALTER TABLE notes FORCE ROW LEVEL SECURITY;
  CREATE POLICY one_org ON notes TO layer_reader USING (org = 'org-1');
  CREATE FUNCTION touch() RETURNS trigger LANGUAGE plpgsql AS \$f\$ BEGIN RETURN NEW; END \$f\$;
  CREATE TRIGGER notes_touch BEFORE UPDATE ON notes FOR EACH ROW EXECUTE FUNCTION touch();"

docker run --rm --network host --ipc=none --user nobody \
  -v "$work/opt/pg:/opt/pg:ro" -v "$work/out:/out" -e PGHOST -e PGUSER -e PGPASSWORD -e PGDATABASE=layer_check \
  --entrypoint /bin/bash public.ecr.aws/lambda/python:3.14 -c '
    set -euo pipefail
    B=/opt/pg/bin
    if ldd $B/* /opt/pg/lib/libpq.so.5 /opt/pg/lib/postgresql/*.so | grep "not found"; then
      echo "a library the layer needs is missing from the Lambda image"
      exit 1
    fi
    test ! -e /dev/shm
    $B/pg_dump --version
    $B/pg_dump -Fc -f /tmp/layer.dump
    $B/pg_dump -Fp --data-only -f /out/original.sql

    # The throwaway server: set up in single-user mode, then started on a Unix socket only.
    $B/initdb -D /tmp/data -U drill --auth=trust --no-sync -E UTF8 --locale=C > /dev/null
    echo "dynamic_shared_memory_type = mmap" >> /tmp/data/postgresql.conf
    for statement in "CREATE ROLE layer_reader NOLOGIN" "CREATE ROLE restorer LOGIN" \
                     "CREATE DATABASE layer_check OWNER restorer"; do
      echo "$statement" | $B/postgres --single -D /tmp/data postgres > /dev/null
    done
    $B/pg_ctl -D /tmp/data -l /tmp/server.log -w \
      -o "-c listen_addresses= -c unix_socket_directories=/tmp -p 5433" start > /dev/null
    export PGHOST=/tmp PGPORT=5433 PGUSER=restorer PGPASSWORD=
    $B/pg_restore -d layer_check --no-owner --no-privileges --exit-on-error /tmp/layer.dump
    # Read back as the superuser drill: the restored tables force row-level security.
    PGUSER=drill $B/pg_dump -d layer_check -Fp --data-only -f /out/restored.sql
    $B/pg_ctl -D /tmp/data -m fast -w stop > /dev/null
  '

# Compared here: the Lambda image has no diff. Plain dumps carry comments and a random \restrict
# key (Postgres 17.6 on); the data itself must match.
keep() { grep -vE '^(--|\\(un)?restrict )' "$1"; }
diff <(keep "$work/out/original.sql") <(keep "$work/out/restored.sql")
echo "the layer dumped, restored and matched the original inside the Lambda image"
```

- [ ] **Step 4: Run the tests again**

Run: `uv run --project backend python -m pytest tools/tests/test_pack_pg_client.py`
Expected: `8 passed`.

- [ ] **Step 5: Check the scripts and the workflow**

Run: `bash -n tools/pg_client/build.sh && bash -n tools/pg_client/verify.sh && uv run --project backend python tools/check_pinned_actions.py .github/workflows && uv run --project backend python tools/check_no_cloud_access.py .`
Expected: no output from `bash -n`, then `All actions in .github\workflows are pinned.` and `CI holds no cloud access.` The build and the Lambda-image check run in the PR's CI (Task 13), where the `pg-client` job must be green.

- [ ] **Step 6: Commit**

```bash
git add tools/pg_client tools/pack_pg_client.py tools/tests/test_pack_pg_client.py .github/workflows/ci.yml
git commit -m "build(ops): a Postgres 17 client and server for the ops function's layer, built from source for Lambda's Amazon Linux 2023 and proven inside AWS's Lambda image in CI" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 2: The roles `app_ops` and `app_backup`

**Files:**
- Create: `backend/migrations/versions/0011_ops_roles.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/integration/test_ops_schema.py`

**Interfaces:**
- Consumes: the tenant tables and their row-level security (Plan 3a), the `audit_log_append_only()` trigger function, and the migration helpers' role guard (no superuser, no `BYPASSRLS`, not the schema owner).
- Produces:
  - the `NOLOGIN` roles `app_ops` and `app_backup`, which the deploy gives logins (Task 10);
  - `app_ops`: `SELECT`, and `UPDATE (status, failure_reason, processed_at)`, on `uploads`; `SELECT` and `DELETE` on `invitations` and `audit_log`; the policies `ops_read`, `ops_update` and `ops_delete`; and the trigger's one exception, a `DELETE` by `app_ops`;
  - `app_backup`: `SELECT` on every table and sequence (with default privileges for future ones) and a `backup_read` policy `USING (true)` on every row-secured table;
  - in the tests: `Database.app_ops` and `Database.app_backup` engines, with the passwords `APP_OPS_PASSWORD` and `APP_BACKUP_PASSWORD`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/conftest.py`, replace:
```python
APP_ANALYZE_PASSWORD = "app-analyze-test-only"  # noqa: S105 - the same, for the worker's role
APP_TRIAGE_PASSWORD = "app-triage-test-only"  # noqa: S105 - the same, for the AI worker's


```
with:
```python
APP_ANALYZE_PASSWORD = "app-analyze-test-only"  # noqa: S105 - the same, for the worker's role
APP_TRIAGE_PASSWORD = "app-triage-test-only"  # noqa: S105 - the same, for the AI worker's
APP_OPS_PASSWORD = "app-ops-test-only"  # noqa: S105 - the same, for the ops job's cleanup
APP_BACKUP_PASSWORD = "app-backup-test-only"  # noqa: S105 - the same, for the nightly backup


```

In `backend/tests/conftest.py`, replace:
```python
    """A database with every migration applied and the reference data synced. `admin` is a
    superuser engine that seeds data past row-level security; `app_api` connects as the API's
    role, `app_analyze` as the analyze worker's and `app_triage` as the AI worker's."""

    url: URL
```
with:
```python
    """A database with every migration applied and the reference data synced. `admin` is a
    superuser engine that seeds data past row-level security; `app_api` connects as the API's
    role, `app_analyze` as the analyze worker's, `app_triage` as the AI worker's, `app_ops` as the
    ops job's cleanup and `app_backup` as its backup (Plan 7a)."""

    url: URL
```

In `backend/tests/conftest.py`, replace:
```python
    app_analyze: Engine
    app_triage: Engine


```
with:
```python
    app_analyze: Engine
    app_triage: Engine
    app_ops: Engine
    app_backup: Engine


```

In `backend/tests/conftest.py`, replace:
```python
            text(f"ALTER ROLE app_triage WITH LOGIN PASSWORD '{APP_TRIAGE_PASSWORD}'")
        )
    app_url = url.set(username="app_api", password=APP_API_PASSWORD)
    app_api = create_database_engine(app_url.render_as_string(hide_password=False), pool_size=1)
```
with:
```python
            text(f"ALTER ROLE app_triage WITH LOGIN PASSWORD '{APP_TRIAGE_PASSWORD}'")
        )
        connection.execute(text(f"ALTER ROLE app_ops WITH LOGIN PASSWORD '{APP_OPS_PASSWORD}'"))
        connection.execute(
            text(f"ALTER ROLE app_backup WITH LOGIN PASSWORD '{APP_BACKUP_PASSWORD}'")
        )
    app_url = url.set(username="app_api", password=APP_API_PASSWORD)
    app_api = create_database_engine(app_url.render_as_string(hide_password=False), pool_size=1)
```

In `backend/tests/conftest.py`, replace:
```python
        triage_url.render_as_string(hide_password=False), pool_size=1
    )
    yield Database(
        url=url, admin=admin, app_api=app_api, app_analyze=app_analyze, app_triage=app_triage
    )
    app_triage.dispose()
    app_analyze.dispose()
```
with:
```python
        triage_url.render_as_string(hide_password=False), pool_size=1
    )
    ops_url = url.set(username="app_ops", password=APP_OPS_PASSWORD)
    app_ops = create_database_engine(ops_url.render_as_string(hide_password=False), pool_size=1)
    backup_url = url.set(username="app_backup", password=APP_BACKUP_PASSWORD)
    app_backup = create_database_engine(
        backup_url.render_as_string(hide_password=False), pool_size=1
    )
    yield Database(
        url=url,
        admin=admin,
        app_api=app_api,
        app_analyze=app_analyze,
        app_triage=app_triage,
        app_ops=app_ops,
        app_backup=app_backup,
    )
    app_backup.dispose()
    app_ops.dispose()
    app_triage.dispose()
    app_analyze.dispose()
```

Create `backend/tests/integration/test_ops_schema.py`:
```python
"""The ops job's database roles (Plan 7a §3): `app_ops` may change only the rows the cleanup rules
target, and `app_backup` may read every row of every table and change nothing. Each test runs a
deliberately broad statement as the role, so it's the row policies that are tested, not the
cleanup's own WHERE clauses."""

from __future__ import annotations

from uuid import UUID, uuid7

import pytest
from conftest import Database
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from tenantdata import add_invitation, add_tenant, add_upload

ROLES = ("app_ops", "app_backup")


def aged(admin: Engine, table: str, row: UUID, column: str, minutes: int) -> None:
    """Moves one row's timestamp into the past, as the database owner."""
    with admin.begin() as connection:
        connection.execute(
            text(
                f"UPDATE {table} SET {column} = now() - make_interval(mins => :minutes) "  # noqa: S608
                "WHERE id = :id"
            ),
            {"minutes": minutes, "id": row},
        )


def status_of(admin: Engine, upload: UUID) -> str:
    with admin.begin() as connection:
        return str(
            connection.execute(
                text("SELECT status FROM uploads WHERE id = :id"), {"id": upload}
            ).scalar_one()
        )


def exists(admin: Engine, table: str, row: UUID) -> bool:
    with admin.begin() as connection:
        found: int = connection.execute(
            text(f"SELECT count(*) FROM {table} WHERE id = :id"),  # noqa: S608
            {"id": row},
        ).scalar_one()
    return bool(found)


def add_audit_row(admin: Engine, org: UUID, days_ago: int) -> UUID:
    row = uuid7()
    with admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO audit_log (id, org_id, actor_type, action, outcome, created_at) "
                "VALUES (:id, :org, 'system', 'test.event', 'success', "
                "now() - make_interval(days => :days))"
            ),
            {"id": row, "org": org, "days": days_ago},
        )
    return row


@pytest.mark.parametrize("role", ROLES)
def test_the_ops_roles_have_no_special_powers(database: Database, role: str) -> None:
    with database.admin.begin() as connection:
        attributes = connection.execute(
            text(
                "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication "
                "FROM pg_roles WHERE rolname = :role"
            ),
            {"role": role},
        ).one()

    assert tuple(attributes) == (False, False, False, False, False)


def test_ops_expires_only_uploads_waiting_for_their_file_over_two_hours(
    database: Database,
) -> None:
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        stale = add_upload(connection, tenant.org_id, tenant.owner_id, "pending_upload")
        recent = add_upload(connection, tenant.org_id, tenant.owner_id, "pending_upload")
        analyzed = add_upload(connection, tenant.org_id, tenant.owner_id, "analyzed")
    aged(database.admin, "uploads", stale, "created_at", 121)
    aged(database.admin, "uploads", recent, "created_at", 119)
    aged(database.admin, "uploads", analyzed, "created_at", 600)

    with database.app_ops.begin() as connection:
        connection.execute(
            text("UPDATE uploads SET status = 'expired' WHERE status = 'pending_upload'")
        )

    assert [status_of(database.admin, row) for row in (stale, recent, analyzed)] == [
        "expired",
        "pending_upload",
        "analyzed",
    ]


def test_ops_fails_only_analyses_stuck_over_two_hours(database: Database) -> None:
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        stuck = add_upload(connection, tenant.org_id, tenant.owner_id, "processing")
        running = add_upload(connection, tenant.org_id, tenant.owner_id, "processing")
    aged(database.admin, "uploads", stuck, "created_at", 121)
    aged(database.admin, "uploads", running, "created_at", 30)

    with database.app_ops.begin() as connection:
        connection.execute(
            text(
                "UPDATE uploads SET status = 'failed', failure_reason = 'x', processed_at = now() "
                "WHERE status = 'processing'"
            )
        )

    assert [status_of(database.admin, row) for row in (stuck, running)] == [
        "failed",
        "processing",
    ]


def test_ops_can_only_set_an_upload_expired_or_failed(database: Database) -> None:
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        stale = add_upload(connection, tenant.org_id, tenant.owner_id, "pending_upload")
    aged(database.admin, "uploads", stale, "created_at", 121)

    with (
        pytest.raises(ProgrammingError, match="row-level security"),
        database.app_ops.begin() as connection,
    ):
        connection.execute(
            text("UPDATE uploads SET status = 'analyzed' WHERE id = :id"), {"id": stale}
        )


def test_ops_can_change_only_an_uploads_status_and_failure(database: Database) -> None:
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        database.app_ops.begin() as connection,
    ):
        connection.execute(text("UPDATE uploads SET original_filename = 'x'"))


def test_ops_deletes_only_lapsed_invitations_nobody_accepted(database: Database) -> None:
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        lapsed = add_invitation(connection, tenant.org_id, tenant.owner_id, "a@example.com")
        accepted = add_invitation(connection, tenant.org_id, tenant.owner_id, "b@example.com")
        connection.execute(
            text(
                "UPDATE invitations SET expires_at = now() - interval '1 minute' "
                "WHERE id IN (:lapsed, :accepted)"
            ),
            {"lapsed": lapsed, "accepted": accepted},
        )
        connection.execute(
            text("UPDATE invitations SET accepted_at = now(), accepted_by = :owner WHERE id = :id"),
            {"owner": tenant.owner_id, "id": accepted},
        )

    with database.app_ops.begin() as connection:
        connection.execute(text("DELETE FROM invitations"))

    assert [
        exists(database.admin, "invitations", row)
        for row in (lapsed, accepted, tenant.invitation_id)
    ] == [False, True, True]


def test_ops_deletes_only_audit_rows_over_180_days_old(database: Database) -> None:
    tenant = add_tenant(database.admin)
    old = add_audit_row(database.admin, tenant.org_id, 181)
    kept = add_audit_row(database.admin, tenant.org_id, 179)

    with database.app_ops.begin() as connection:
        connection.execute(text("DELETE FROM audit_log"))

    assert [exists(database.admin, "audit_log", row) for row in (old, kept)] == [False, True]


@pytest.mark.parametrize("engine", ["admin", "app_api"])
def test_the_audit_log_stays_append_only_for_everyone_else(database: Database, engine: str) -> None:
    tenant = add_tenant(database.admin)
    old = add_audit_row(database.admin, tenant.org_id, 400)

    with (
        pytest.raises(DBAPIError, match=r"append-only|permission denied"),
        getattr(database, engine).begin() as connection,
    ):
        connection.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": old})

    assert exists(database.admin, "audit_log", old)


def test_ops_reads_nothing_beyond_its_rules(database: Database) -> None:
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        database.app_ops.begin() as connection,
    ):
        connection.execute(text("SELECT count(*) FROM findings"))


def public_tables(admin: Engine) -> list[str]:
    with admin.begin() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY c.relname"
                )
            ).scalars()
        )


def test_the_backup_reads_every_row_of_every_table(database: Database) -> None:
    add_tenant(database.admin)
    add_tenant(database.admin)
    tables = public_tables(database.admin)

    def counts(engine: Engine) -> dict[str, int]:
        with engine.begin() as connection:
            return {
                table: int(
                    connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()  # noqa: S608
                )
                for table in tables
            }

    owner_counts = counts(database.admin)
    assert counts(database.app_backup) == owner_counts
    assert owner_counts["organizations"] >= 2


def test_the_backup_changes_nothing(database: Database) -> None:
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        database.app_backup.begin() as connection,
    ):
        connection.execute(text("DELETE FROM uploads"))


def test_every_table_is_in_the_backup(database: Database) -> None:
    """A table added later must be granted to `app_backup` and, if it has row-level security,
    get its read-all policy, or it silently drops out of the backups."""
    with database.admin.begin() as connection:
        missing: list[str] = list(
            connection.execute(
                text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r' AND ("
                    "  NOT has_table_privilege('app_backup', c.oid, 'SELECT')"
                    "  OR (c.relrowsecurity AND NOT EXISTS ("
                    "    SELECT FROM pg_policies p WHERE p.schemaname = 'public' "
                    "    AND p.tablename = c.relname AND 'app_backup' = ANY (p.roles) "
                    "    AND p.cmd IN ('SELECT', 'ALL') AND p.qual = 'true'))) "
                    "ORDER BY c.relname"
                )
            ).scalars()
        )

    assert missing == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/integration/test_ops_schema.py`
Expected: FAIL. Roles belong to the whole Postgres server, not to one database, so the result depends on what earlier runs left there. Where `app_ops` and `app_backup` already exist (as on the owner's machine after the prototype), `7 failed, 7 passed`, with `permission denied for table uploads` (and `invitations`, `audit_log`, `ai_analyses`), and `test_every_table_is_in_the_backup` listing every table. On a fresh server, every test errors in the fixtures, because the roles don't exist yet.

- [ ] **Step 3: Write the migration**

Create `backend/migrations/versions/0011_ops_roles.py`:
```python
"""The ops job's roles (Plan 7a §3): `app_ops` for the daily cleanup and the hourly check, and
`app_backup` for the nightly dump.

Every tenant table forces row-level security, even for its owner (Plan 3a), so neither role gets
`BYPASSRLS` nor works through `SECURITY DEFINER` functions (amending spec §5.4). Instead:
- `app_ops` has column grants and one row policy per cleanup rule, so even a broad statement
  changes only the rows a rule targets: uploads waiting or analyzing for over 2 hours, which may
  only become expired or failed; invitations nobody accepted, past their date; audit rows over
  180 days old. The audit log's trigger lets `app_ops` delete; everyone else is still refused.
- `app_backup` may read every table, and every row-secured table has a read-all policy for it,
  so `pg_dump --enable-row-security` sees every row. A test fails if a later table misses either.

Revision ID: 0011
Revises: 0010
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

STALE_UPLOAD = (
    "status IN ('pending_upload', 'processing') AND created_at < now() - interval '2 hours'"
)
LAPSED_INVITATION = "accepted_at IS NULL AND expires_at < now()"
OLD_AUDIT_ROW = "created_at < now() - interval '180 days'"


def create_role(role: str) -> str:
    """Created without a login (the deploy gives it one), with the same guards as the others."""
    return f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                CREATE ROLE {role} NOLOGIN;
            END IF;
            IF EXISTS (
                SELECT FROM pg_roles WHERE rolname = '{role}'
                AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)
            ) THEN
                RAISE EXCEPTION '{role} must not be a superuser or have BYPASSRLS, '
                    'CREATEROLE, CREATEDB or REPLICATION';
            END IF;
            IF EXISTS (
                SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
                WHERE r.rolname = '{role}'
            ) THEN
                RAISE EXCEPTION '{role} must not be a member of another role';
            END IF;
        END $$;
    """  # noqa: S608 - fixed role names, not input


def upgrade() -> None:
    op.execute(create_role("app_ops") + create_role("app_backup"))
    op.execute(
        f"""
        GRANT USAGE ON SCHEMA public TO app_ops, app_backup;
        GRANT EXECUTE ON FUNCTION app_org_id(), app_user_id() TO app_ops, app_backup;

        GRANT SELECT (id, status, created_at), UPDATE (status, failure_reason, processed_at)
            ON uploads TO app_ops;
        -- An UPDATE with a WHERE clause checks the new row against the read policy too, so
        -- reading covers the states the cleanup sets as well as those it changes.
        CREATE POLICY ops_read ON uploads FOR SELECT TO app_ops USING (
            status IN ('pending_upload', 'processing', 'expired', 'failed')
            AND created_at < now() - interval '2 hours'
        );
        CREATE POLICY ops_update ON uploads FOR UPDATE TO app_ops
            USING ({STALE_UPLOAD}) WITH CHECK (status IN ('expired', 'failed'));

        GRANT SELECT (id, accepted_at, expires_at), DELETE ON invitations TO app_ops;
        CREATE POLICY ops_read ON invitations FOR SELECT TO app_ops USING ({LAPSED_INVITATION});
        CREATE POLICY ops_delete ON invitations FOR DELETE TO app_ops
            USING ({LAPSED_INVITATION});

        GRANT SELECT (id, created_at), DELETE ON audit_log TO app_ops;
        CREATE POLICY ops_read ON audit_log FOR SELECT TO app_ops USING ({OLD_AUDIT_ROW});
        CREATE POLICY ops_delete ON audit_log FOR DELETE TO app_ops USING ({OLD_AUDIT_ROW});

        -- Still append-only for everyone but the cleanup, whose policy limits it to old rows.
        CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger
            LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND current_user = 'app_ops' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'audit_log is append-only';
        END $$;

        GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_backup;
        GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO app_backup;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO app_backup;
        DO $$
        DECLARE
            secured text;
        BEGIN
            FOR secured IN
                SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity
            LOOP
                EXECUTE format(
                    'CREATE POLICY backup_read ON %I FOR SELECT TO app_backup USING (true)',
                    secured
                );
            END LOOP;
        END $$;
        """  # noqa: S608 - fixed policy predicates, not input
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            secured text;
        BEGIN
            FOR secured IN
                SELECT tablename FROM pg_policies
                WHERE schemaname = 'public' AND policyname = 'backup_read'
            LOOP
                EXECUTE format('DROP POLICY backup_read ON %I', secured);
            END LOOP;
        END $$;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM app_backup;
        REVOKE ALL ON ALL TABLES IN SCHEMA public FROM app_backup;
        REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM app_backup;

        CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger
            LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
        END $$;
        DROP POLICY ops_read ON uploads;
        DROP POLICY ops_update ON uploads;
        DROP POLICY ops_read ON invitations;
        DROP POLICY ops_delete ON invitations;
        DROP POLICY ops_read ON audit_log;
        DROP POLICY ops_delete ON audit_log;
        REVOKE ALL ON uploads, invitations, audit_log FROM app_ops;

        REVOKE ALL ON FUNCTION app_org_id(), app_user_id() FROM app_ops, app_backup;
        REVOKE USAGE ON SCHEMA public FROM app_ops, app_backup;
        """
    )
```

- [ ] **Step 4: Run the tests again**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/integration/test_ops_schema.py`
Expected: `14 passed`.

- [ ] **Step 5: Check the migration both ways, and the backend**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/integration/test_migrations.py && uv run python -m ruff check . && uv run python -m ruff format --check . && uv run python -m mypy`
Expected: `2 passed` (every migration applies, rolls back and applies again, now with `0011`), Ruff passes and finds every file formatted, and mypy reports `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add backend/migrations/versions/0011_ops_roles.py backend/tests/conftest.py backend/tests/integration/test_ops_schema.py
git commit -m "feat(db): the ops job's roles: app_ops may change only the rows the cleanup rules target, and app_backup reads every row of every table, without BYPASSRLS" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 3: The daily cleanup

**Files:**
- Create: `backend/src/nettriage/adapters/maintenance.py`
- Modify: `backend/src/nettriage/application/uploads.py`, `backend/src/nettriage/entrypoints/analyze/worker.py`
- Test: `backend/tests/integration/test_maintenance.py`, `backend/tests/worker/test_analyze_worker.py`

**Interfaces:**
- Consumes: `Database.app_ops` (Task 2); the analyze worker's sentence for an upload it gave up on.
- Produces:
  - `GAVE_UP` moves from the analyze worker to `nettriage.application.uploads`, so the worker and the cleanup share one sentence;
  - `STALE`, the 2-hour condition, and `CleanupCounts(uploads_expired: int, analyses_failed: int, invitations_deleted: int, audit_rows_deleted: int)`;
  - `expire_abandoned_uploads`, `fail_stuck_analyses`, `delete_lapsed_invitations` and `delete_old_audit_rows`, each `(engine: Engine) -> int` in its own transaction;
  - `clean_up(engine: Engine) -> CleanupCounts`, which Task 7's `cleanup` job runs as `app_ops`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/integration/test_maintenance.py`:
```python
"""The daily cleanup (Plan 7a §2.4), run as `app_ops` against real Postgres: uploads waiting or
analyzing for over 2 hours, lapsed invitations nobody accepted, and audit rows over 180 days."""

from __future__ import annotations

from uuid import UUID, uuid7

from conftest import Database
from sqlalchemy import Engine, text
from tenantdata import add_invitation, add_tenant, add_upload

from nettriage.adapters.maintenance import (
    clean_up,
    delete_lapsed_invitations,
    delete_old_audit_rows,
    expire_abandoned_uploads,
    fail_stuck_analyses,
)
from nettriage.application.uploads import GAVE_UP


def upload_aged(admin: Engine, status: str, minutes: int) -> UUID:
    tenant = add_tenant(admin)
    with admin.begin() as connection:
        upload = add_upload(connection, tenant.org_id, tenant.owner_id, status)
        connection.execute(
            text(
                "UPDATE uploads SET created_at = now() - make_interval(mins => :minutes) "
                "WHERE id = :id"
            ),
            {"minutes": minutes, "id": upload},
        )
    return upload


def upload_row(admin: Engine, upload: UUID) -> tuple[str, str | None, bool]:
    with admin.begin() as connection:
        status, reason, processed = connection.execute(
            text("SELECT status, failure_reason, processed_at FROM uploads WHERE id = :id"),
            {"id": upload},
        ).one()
    return str(status), reason, processed is not None


def test_uploads_still_waiting_for_their_file_after_two_hours_expire(database: Database) -> None:
    stale = upload_aged(database.admin, "pending_upload", 121)
    recent = upload_aged(database.admin, "pending_upload", 90)

    expired = expire_abandoned_uploads(database.app_ops)

    assert expired >= 1
    assert upload_row(database.admin, stale)[0] == "expired"
    assert upload_row(database.admin, recent)[0] == "pending_upload"


def test_analyses_stuck_for_two_hours_fail_with_the_workers_own_sentence(
    database: Database,
) -> None:
    stuck = upload_aged(database.admin, "processing", 125)
    running = upload_aged(database.admin, "processing", 10)

    failed = fail_stuck_analyses(database.app_ops)

    assert failed >= 1
    assert upload_row(database.admin, stuck) == ("failed", GAVE_UP, True)
    assert upload_row(database.admin, running) == ("processing", None, False)


def test_lapsed_invitations_nobody_accepted_are_deleted(database: Database) -> None:
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        lapsed = add_invitation(connection, tenant.org_id, tenant.owner_id, "late@example.com")
        connection.execute(
            text("UPDATE invitations SET expires_at = now() - interval '1 day' WHERE id = :id"),
            {"id": lapsed},
        )

    deleted = delete_lapsed_invitations(database.app_ops)

    assert deleted >= 1
    with database.admin.begin() as connection:
        remaining: set[UUID] = set(
            connection.execute(
                text("SELECT id FROM invitations WHERE id IN (:lapsed, :valid)"),
                {"lapsed": lapsed, "valid": tenant.invitation_id},
            ).scalars()
        )
    assert remaining == {tenant.invitation_id}


def test_audit_rows_older_than_180_days_are_deleted(database: Database) -> None:
    tenant = add_tenant(database.admin)
    old, kept = uuid7(), uuid7()
    with database.admin.begin() as connection:
        for row, days in ((old, 200), (kept, 10)):
            connection.execute(
                text(
                    "INSERT INTO audit_log (id, org_id, actor_type, action, outcome, created_at) "
                    "VALUES (:id, :org, 'system', 'test.event', 'success', "
                    "now() - make_interval(days => :days))"
                ),
                {"id": row, "org": tenant.org_id, "days": days},
            )

    deleted = delete_old_audit_rows(database.app_ops)

    assert deleted >= 1
    with database.admin.begin() as connection:
        remaining: set[UUID] = set(
            connection.execute(
                text("SELECT id FROM audit_log WHERE id IN (:old, :kept)"),
                {"old": old, "kept": kept},
            ).scalars()
        )
    assert remaining == {kept}


def test_the_cleanup_runs_every_rule_and_counts_what_each_changed(database: Database) -> None:
    upload_aged(database.admin, "pending_upload", 300)
    upload_aged(database.admin, "processing", 300)

    counts = clean_up(database.app_ops)

    assert counts.uploads_expired >= 1
    assert counts.analyses_failed >= 1
    assert counts.invitations_deleted >= 0
    assert counts.audit_rows_deleted >= 0
    assert clean_up(database.app_ops).uploads_expired == 0
```

In `backend/tests/worker/test_analyze_worker.py`, replace:
```python
from nettriage.adapters.upload_objects import UploadObjects
from nettriage.application.analysis import UploadKey
from nettriage.application.uploads import s3_key
from nettriage.domain.parsing.vpc_flow_logs import ParseLimits
from nettriage.entrypoints.analyze import handler
from nettriage.entrypoints.analyze.worker import GAVE_UP, SIZE_MISMATCH, AnalysisFailed, Worker
from nettriage.platform.metrics import AnalyzeMetrics

```
with:
```python
from nettriage.adapters.upload_objects import UploadObjects
from nettriage.application.analysis import UploadKey
from nettriage.application.uploads import GAVE_UP, s3_key
from nettriage.domain.parsing.vpc_flow_logs import ParseLimits
from nettriage.entrypoints.analyze import handler
from nettriage.entrypoints.analyze.worker import SIZE_MISMATCH, AnalysisFailed, Worker
from nettriage.platform.metrics import AnalyzeMetrics

```

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/integration/test_maintenance.py tests/worker/test_analyze_worker.py`
Expected: FAIL: `2 errors` during collection: `No module named 'nettriage.adapters.maintenance'`, and `cannot import name 'GAVE_UP' from 'nettriage.application.uploads'`.

- [ ] **Step 3: Write the four rules**

Create `backend/src/nettriage/adapters/maintenance.py`:
```python
"""The daily cleanup (Plan 7a §2.4), as `app_ops`. Each rule runs in its own transaction and
repeats its row policy's predicate (migration 0011), so it changes exactly the rows the policy
allows; it returns how many, never which."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, text

from nettriage.application.uploads import GAVE_UP

# An upload's file arrives within its signed URL's 5 minutes, and its analysis message is
# delivered at most three times, 30 minutes apart: after 2 hours, every delivery is over.
STALE = "created_at < now() - interval '2 hours'"


@dataclass(frozen=True)
class CleanupCounts:
    uploads_expired: int
    analyses_failed: int
    invitations_deleted: int
    audit_rows_deleted: int


def _changed(engine: Engine, statement: str, params: dict[str, object] | None = None) -> int:
    with engine.begin() as connection:
        return connection.execute(text(statement), params or {}).rowcount


def expire_abandoned_uploads(engine: Engine) -> int:
    """Uploads whose file never came."""
    return _changed(
        engine,
        f"UPDATE uploads SET status = 'expired' WHERE status = 'pending_upload' AND {STALE}",  # noqa: S608
    )


def fail_stuck_analyses(engine: Engine) -> int:
    """Uploads whose analysis crashed or timed out on its last delivery."""
    return _changed(
        engine,
        "UPDATE uploads SET status = 'failed', failure_reason = :reason, processed_at = now() "  # noqa: S608
        f"WHERE status = 'processing' AND {STALE}",
        {"reason": GAVE_UP},
    )


def delete_lapsed_invitations(engine: Engine) -> int:
    """Invitations nobody accepted, past their date; accepted ones stay as history."""
    return _changed(
        engine, "DELETE FROM invitations WHERE accepted_at IS NULL AND expires_at < now()"
    )


def delete_old_audit_rows(engine: Engine) -> int:
    """The audit log keeps 180 days (spec §5.7)."""
    return _changed(engine, "DELETE FROM audit_log WHERE created_at < now() - interval '180 days'")


def clean_up(engine: Engine) -> CleanupCounts:
    return CleanupCounts(
        uploads_expired=expire_abandoned_uploads(engine),
        analyses_failed=fail_stuck_analyses(engine),
        invitations_deleted=delete_lapsed_invitations(engine),
        audit_rows_deleted=delete_old_audit_rows(engine),
    )
```

In `backend/src/nettriage/application/uploads.py`, replace:
```python
PRESIGNED_PUT_LIFETIME = timedelta(minutes=5)
MAX_FILENAME = 255


```
with:
```python
PRESIGNED_PUT_LIFETIME = timedelta(minutes=5)
MAX_FILENAME = 255
# Why an analysis failed for good, after the worker's last try or the daily cleanup's (Plan 7a).
GAVE_UP = "NetTriage couldn't analyze this file after three tries. Upload it again later."


```

In `backend/src/nettriage/entrypoints/analyze/worker.py`, replace:
```python
from nettriage.application.analysis import UploadKey, analyze_parsed, parse_upload_key
from nettriage.application.clock import Clock
from nettriage.domain.parsing.vpc_flow_logs import FlowLogError, ParseLimits, parse_flow_log
from nettriage.platform.metrics import AnalyzeMetrics
```
with:
```python
from nettriage.application.analysis import UploadKey, analyze_parsed, parse_upload_key
from nettriage.application.clock import Clock
from nettriage.application.uploads import GAVE_UP
from nettriage.domain.parsing.vpc_flow_logs import FlowLogError, ParseLimits, parse_flow_log
from nettriage.platform.metrics import AnalyzeMetrics
```

In `backend/src/nettriage/entrypoints/analyze/worker.py`, replace:
```python

SIZE_MISMATCH = "The file's size doesn't match the size declared when it was uploaded."
GAVE_UP = "NetTriage couldn't analyze this file after three tries. Upload it again later."
# Neon waking up or restarting takes seconds, and SQS would deliver the message again only after
# its 30-minute visibility timeout, so the worker first retries after these pauses (spec §8.6).
```
with:
```python

SIZE_MISMATCH = "The file's size doesn't match the size declared when it was uploaded."
# Neon waking up or restarting takes seconds, and SQS would deliver the message again only after
# its 30-minute visibility timeout, so the worker first retries after these pauses (spec §8.6).
```

- [ ] **Step 4: Run the tests again**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/integration/test_maintenance.py tests/worker/test_analyze_worker.py`
Expected: `23 passed`.

- [ ] **Step 5: Check the backend**

Run: `cd backend && uv run python -m ruff check . && uv run python -m ruff format --check . && uv run python -m mypy`
Expected: Ruff passes and finds every file formatted, and mypy reports `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/nettriage/adapters/maintenance.py backend/src/nettriage/application/uploads.py backend/src/nettriage/entrypoints/analyze/worker.py backend/tests/integration/test_maintenance.py backend/tests/worker/test_analyze_worker.py
git commit -m "feat(ops): the daily cleanup: uploads waiting or analyzing for over two hours expire or fail, and lapsed invitations and audit rows over 180 days are deleted" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 4: The probe and the hourly check

**Files:**
- Create: `backend/src/nettriage/adapters/probes.py`
- Test: `backend/tests/integration/test_probes.py`

**Interfaces:**
- Consumes: `Database.app_ops` (Task 2); the `runtime_table` fixture; `create_database_engine`.
- Produces: `health_answers(http: httpx.Client, app_url: str) -> bool` (a `GET <app_url>/api/health` within `HEALTH_TIMEOUT_SECONDS = 10`, true only for a 200 whose JSON is an object with `"status": "ok"`), `database_answers(engine: Engine) -> bool` (`SELECT 1`) and `dynamodb_answers(client: DynamoDBClient, table: str) -> bool` (a `GetItem` of `PROBE_KEY = "ops#probe"`; a missing item still answers). None of them raises.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/integration/test_probes.py`:
```python
"""The ops job's probe and hourly check (Plan 7a §2.1, §2.2): each says only whether the thing
answered, so nothing they report can carry data."""

from __future__ import annotations

import json

import httpx
import pytest
from botocore.exceptions import ClientError
from conftest import Database, RuntimeTable

from nettriage.adapters.postgres import create_database_engine
from nettriage.adapters.probes import database_answers, dynamodb_answers, health_answers

APP = "https://d1234.cloudfront.net"


def http(
    reply: httpx.Response | Exception, seen: list[httpx.Request] | None = None
) -> httpx.Client:
    def respond(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if isinstance(reply, Exception):
            raise reply
        return reply

    return httpx.Client(transport=httpx.MockTransport(respond))


def test_a_healthy_app_answers_ok_through_cloudfront() -> None:
    seen: list[httpx.Request] = []
    client = http(httpx.Response(200, json={"status": "ok", "version": "abc123"}), seen)

    assert health_answers(client, APP)
    assert [(request.method, str(request.url)) for request in seen] == [
        ("GET", f"{APP}/api/health")
    ]


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(503, json={"status": "ok"}),
        httpx.Response(200, content=b"<html>error</html>"),
        httpx.Response(200, content=json.dumps({"status": "starting"}).encode()),
        httpx.Response(200, json=["ok"]),
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
    ],
)
def test_anything_but_an_ok_answer_is_a_failed_probe(reply: httpx.Response | Exception) -> None:
    assert not health_answers(http(reply), APP)


def test_the_database_answers_as_the_ops_role(database: Database) -> None:
    assert database_answers(database.app_ops)


def test_a_database_that_refuses_is_a_failed_check() -> None:
    unreachable = create_database_engine("postgresql://app_ops:x@127.0.0.1:9/neondb", pool_size=1)

    assert not database_answers(unreachable)


def test_dynamodb_answers_even_without_the_item(runtime_table: RuntimeTable) -> None:
    assert dynamodb_answers(runtime_table.client, runtime_table.name)


def test_a_missing_table_is_a_failed_check(runtime_table: RuntimeTable) -> None:
    with pytest.raises(ClientError):
        runtime_table.client.describe_table(TableName="nettriage-test-gone")

    assert not dynamodb_answers(runtime_table.client, "nettriage-test-gone")
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/integration/test_probes.py`
Expected: FAIL: `1 error` during collection, `No module named 'nettriage.adapters.probes'`.

- [ ] **Step 3: Write the probes**

Create `backend/src/nettriage/adapters/probes.py`:
```python
"""The ops job's probe and hourly check (Plan 7a §2.1, §2.2). Each answers only whether the thing
answered; a failure is a False, never an exception or a message, so nothing reported can carry
data, and the next run, minutes away, tries again."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from types_boto3_dynamodb.client import DynamoDBClient

HEALTH_TIMEOUT_SECONDS = 10
# Any key: a missing item still proves the table answered.
PROBE_KEY = "ops#probe"


def health_answers(http: httpx.Client, app_url: str) -> bool:
    """`/api/health` through CloudFront answers 200 with `{"status": "ok"}`. It never touches the
    database (Plan 4a), so probing every 5 minutes doesn't keep Neon awake."""
    try:
        response = http.get(f"{app_url}/api/health", timeout=HEALTH_TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return False
    if response.status_code != httpx.codes.OK:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("status") == "ok"


def database_answers(engine: Engine) -> bool:
    """`SELECT 1`, within the engine's 10-second connect timeout (Neon wakes in seconds)."""
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT 1")).scalar_one() == 1
    except SQLAlchemyError:
        return False


def dynamodb_answers(client: DynamoDBClient, table: str) -> bool:
    try:
        client.get_item(TableName=table, Key={"pk": {"S": PROBE_KEY}})
    except ClientError, BotoCoreError:
        return False
    return True
```

- [ ] **Step 4: Run the tests again**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/integration/test_probes.py`
Expected: `11 passed` (the refused connection to port 9 can take several seconds on Windows).

- [ ] **Step 5: Check the backend**

Run: `cd backend && uv run python -m ruff check . && uv run python -m ruff format --check . && uv run python -m mypy`
Expected: Ruff passes and finds every file formatted, and mypy reports `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/nettriage/adapters/probes.py backend/tests/integration/test_probes.py
git commit -m "feat(ops): the probe and the hourly check: the app's health through CloudFront, the database as app_ops and the runtime table, each answering only whether it answered" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 5: The nightly backup

**Files:**
- Create: `backend/src/nettriage/adapters/pg_client.py`, `backend/src/nettriage/adapters/backups.py`, `backend/src/nettriage/entrypoints/ops/__init__.py`, `backend/src/nettriage/entrypoints/ops/backup.py`
- Test: `backend/tests/unit/adapters/test_pg_client.py`, `backend/tests/integration/test_backups.py`, `backend/tests/worker/test_ops_backup.py`

**Interfaces:**
- Consumes: `Database.app_backup` (Task 2); the layer's programs at `/opt/pg/bin` (Task 1); certifi's CA bundle, as `adapters/postgres.py` uses it.
- Produces:
  - in `pg_client.py`: `Target(host, port, database, user, password)` with `Target.from_url(url) -> Target` and `environment() -> dict[str, str]` (`PG*` variables, so the password is never an argument; a remote host gets `PGSSLMODE=verify-full` with certifi's bundle); `PgClient(bin_dir: Path = LAYER_BIN, run=subprocess.run)` with `program(name, args, env)`, `checked(name, args, env) -> None`, `version() -> str` and `dump(target: Target, snapshot: str, out: Path) -> None`; `ProgramFailed`, and `DumpFailed(ProgramFailed)`, whose message says only how the program exited;
  - in `backups.py`: `PREFIX = "pg/"`; `Snapshot(id, tables, roles, server_version)`; `Manifest(created_at, server_version, client_version, tables, roles)` with `to_json()` and `Manifest.from_json(body)`; the context manager `exported_snapshot(engine) -> Iterator[Snapshot]`; `BackupStore(client: S3Client, bucket: str)` with `put(day, dump, manifest)` (the dump first, the manifest last), `latest() -> str | None` (the newest day with both files) and `fetch(day, dump: Path) -> Manifest`;
  - in `entrypoints/ops/backup.py`: `DUMP_NAME = "nettriage.dump"`, the `Dumper` protocol (`version()`, `dump(target, snapshot, out)`) and `run_backup(engine, pg_dump: Dumper, store, now: datetime, workdir: Path) -> Manifest`, which removes its local dump whatever happens.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/integration/test_backups.py`:
```python
"""The nightly backup's database and storage sides (Plan 7a §2.3): a snapshot whose row counts the
dump will match, and the dump and its manifest in S3."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from conftest import REGION, Database
from sqlalchemy import text
from tenantdata import add_tenant

from nettriage.adapters.backups import BackupStore, Manifest, exported_snapshot
from nettriage.adapters.postgres import create_database_engine

BUCKET = "nettriage-test-backups"


def owner_counts(database: Database) -> dict[str, int]:
    with database.admin.begin() as connection:
        tables: list[str] = list(
            connection.execute(
                text(
                    "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
                    "ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r'"
                )
            ).scalars()
        )
        return {
            table: int(connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one())  # noqa: S608
            for table in tables
        }


def test_the_snapshot_counts_every_row_of_every_table_as_the_backup_role(
    database: Database,
) -> None:
    add_tenant(database.admin)
    add_tenant(database.admin)

    with exported_snapshot(database.app_backup) as snapshot:
        assert snapshot.tables == owner_counts(database)
        assert snapshot.tables["organizations"] >= 2
        assert snapshot.server_version.startswith(("16.", "17."))


def test_the_snapshot_names_the_roles_the_policies_need(database: Database) -> None:
    with exported_snapshot(database.app_backup) as snapshot:
        assert {"app_triage", "app_ops", "app_backup"} <= set(snapshot.roles)
        assert "public" not in snapshot.roles


def test_the_dump_sees_the_same_moment_as_the_counts(database: Database) -> None:
    add_tenant(database.admin)
    with exported_snapshot(database.app_backup) as snapshot:
        before = snapshot.tables["organizations"]
        add_tenant(database.admin)  # written after the snapshot: the dump mustn't see it

        second = create_database_engine(
            database.app_backup.url.render_as_string(hide_password=False), pool_size=1
        )
        with second.connect() as other:
            other.execution_options(isolation_level="REPEATABLE READ")
            with other.begin():
                other.execute(text(f"SET TRANSACTION SNAPSHOT '{snapshot.id}'"))
                seen: int = other.execute(text("SELECT count(*) FROM organizations")).scalar_one()
        second.dispose()

    assert seen == before


@pytest.fixture
def store() -> Iterator[BackupStore]:
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-north-1"}
        )
        yield BackupStore(client, BUCKET)


def manifest(day: str) -> Manifest:
    return Manifest(
        created_at=f"{day}T02:00:03+00:00",
        server_version="17.5",
        client_version="17.11",
        tables={"organizations": 2, "uploads": 5},
        roles=["app_api", "app_backup"],
    )


def test_a_backup_is_stored_as_its_day_dump_and_manifest(
    store: BackupStore, tmp_path: Path
) -> None:
    dump = tmp_path / "db.dump"
    dump.write_bytes(b"PGDMP custom archive")

    store.put("2026-10-07", dump, manifest("2026-10-07"))

    objects = store.client.list_objects_v2(Bucket=BUCKET)["Contents"]
    assert sorted(item["Key"] for item in objects) == ["pg/2026-10-07.dump", "pg/2026-10-07.json"]
    body = store.client.get_object(Bucket=BUCKET, Key="pg/2026-10-07.json")["Body"].read()
    assert json.loads(body)["tables"] == {"organizations": 2, "uploads": 5}


def test_the_latest_backup_is_the_newest_day_with_both_files(
    store: BackupStore, tmp_path: Path
) -> None:
    dump = tmp_path / "db.dump"
    dump.write_bytes(b"PGDMP")
    for day in ("2026-10-05", "2026-10-07", "2026-10-06"):
        store.put(day, dump, manifest(day))
    store.client.put_object(Bucket=BUCKET, Key="pg/2026-10-08.dump", Body=b"no manifest yet")

    assert store.latest() == "2026-10-07"


def test_no_backup_yet_has_no_latest(store: BackupStore) -> None:
    assert store.latest() is None


def test_a_backup_is_fetched_with_its_manifest(store: BackupStore, tmp_path: Path) -> None:
    dump = tmp_path / "db.dump"
    dump.write_bytes(b"PGDMP archive bytes")
    store.put("2026-10-07", dump, manifest("2026-10-07"))
    fetched = tmp_path / "fetched.dump"

    got = store.fetch("2026-10-07", fetched)

    assert got == manifest("2026-10-07")
    assert fetched.read_bytes() == b"PGDMP archive bytes"
```

Create `backend/tests/unit/adapters/test_pg_client.py`:
```python
"""Running the layer's Postgres programs (Plan 7a §2.3): the password travels in the environment,
never on the command line, TLS is verified for remote hosts, and a failure says only how the
program exited, never what it printed (it could quote data)."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import certifi
import pytest

from nettriage.adapters.pg_client import DumpFailed, PgClient, Target

NEON = "postgresql://app_backup:s3cret@ep-x.eu-central-1.aws.neon.tech/neondb"


class FakeRun:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.result = (returncode, stdout, stderr)

    def __call__(
        self, args: Sequence[str], *, env: dict[str, str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(args), env))
        returncode, stdout, stderr = self.result
        return subprocess.CompletedProcess(list(args), returncode, stdout, stderr)


def test_a_target_reads_its_connection_from_a_url() -> None:
    target = Target.from_url(NEON)

    assert (target.host, target.port, target.database, target.user, target.password) == (
        "ep-x.eu-central-1.aws.neon.tech",
        5432,
        "neondb",
        "app_backup",
        "s3cret",
    )


def test_the_dump_uses_the_snapshot_and_row_security_with_the_password_out_of_sight(
    tmp_path: Path,
) -> None:
    run = FakeRun()
    client = PgClient(Path("/opt/pg/bin"), run=run)

    client.dump(Target.from_url(NEON), "00000003-0000001B-1", tmp_path / "db.dump")

    [(args, env)] = run.calls
    assert args == [
        str(Path("/opt/pg/bin/pg_dump")),
        "--format=custom",
        "--enable-row-security",
        "--snapshot=00000003-0000001B-1",
        "--no-password",
        f"--file={tmp_path / 'db.dump'}",
    ]
    assert "s3cret" not in " ".join(args)
    assert {key: env[key] for key in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER")} == {
        "PGHOST": "ep-x.eu-central-1.aws.neon.tech",
        "PGPORT": "5432",
        "PGDATABASE": "neondb",
        "PGUSER": "app_backup",
    }
    assert env["PGPASSWORD"] == "s3cret"
    assert (env["PGSSLMODE"], env["PGSSLROOTCERT"]) == ("verify-full", certifi.where())
    assert env["PGCONNECT_TIMEOUT"] == "10"


def test_a_local_server_is_dumped_without_tls(tmp_path: Path) -> None:
    run = FakeRun()

    PgClient(Path("/opt/pg/bin"), run=run).dump(
        Target.from_url("postgresql://app_backup:pw@127.0.0.1:5432/nettriage"),
        "snap",
        tmp_path / "db.dump",
    )

    [(_, env)] = run.calls
    assert "PGSSLMODE" not in env


def test_a_failed_dump_says_only_how_it_exited(tmp_path: Path) -> None:
    run = FakeRun(returncode=1, stderr='pg_dump: error: query failed: ERROR: value "secret row"')

    with pytest.raises(DumpFailed) as failure:
        PgClient(Path("/opt/pg/bin"), run=run).dump(Target.from_url(NEON), "snap", tmp_path / "x")

    assert str(failure.value) == "pg_dump exited with status 1"
    assert "secret" not in str(failure.value)


def test_the_client_says_its_version() -> None:
    run = FakeRun(stdout="pg_dump (PostgreSQL) 17.11\n")

    assert PgClient(Path("/opt/pg/bin"), run=run).version() == "17.11"
```

Create `backend/tests/worker/test_ops_backup.py`:
```python
"""The nightly backup job (Plan 7a §2.3), against real Postgres as `app_backup`, with S3 in moto
and `pg_dump` stood in for: it dumps the snapshot it counted, then stores the dump and a manifest
of those counts under the day's name, and leaves nothing behind locally."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import boto3
import pytest
from conftest import REGION, Database
from tenantdata import add_tenant

from nettriage.adapters.backups import BackupStore
from nettriage.adapters.pg_client import DumpFailed, Target
from nettriage.entrypoints.ops.backup import run_backup

BUCKET = "nettriage-test-backups"
NIGHT = datetime(2026, 10, 7, 2, 0, 4, tzinfo=UTC)


class FakePgDump:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[Target, str, Path]] = []

    def version(self) -> str:
        return "17.11"

    def dump(self, target: Target, snapshot: str, out: Path) -> None:
        self.calls.append((target, snapshot, out))
        if self.fail:
            raise DumpFailed("pg_dump exited with status 1")
        out.write_bytes(b"PGDMP custom archive")


@pytest.fixture
def store() -> Iterator[BackupStore]:
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-north-1"}
        )
        yield BackupStore(client, BUCKET)


def test_a_backup_dumps_the_counted_snapshot_and_stores_it_under_the_day(
    database: Database, store: BackupStore, tmp_path: Path
) -> None:
    add_tenant(database.admin)
    pg_dump = FakePgDump()

    manifest = run_backup(database.app_backup, pg_dump, store, NIGHT, tmp_path)

    [(target, snapshot, _)] = pg_dump.calls
    assert target.user == "app_backup"
    assert snapshot
    assert store.latest() == "2026-10-07"
    fetched = store.fetch("2026-10-07", tmp_path / "check.dump")
    assert fetched == manifest
    assert (manifest.created_at, manifest.client_version) == (NIGHT.isoformat(), "17.11")
    assert manifest.tables["organizations"] >= 1
    assert "app_backup" in manifest.roles
    assert (tmp_path / "check.dump").read_bytes() == b"PGDMP custom archive"


def test_the_local_dump_is_removed_once_stored(
    database: Database, store: BackupStore, tmp_path: Path
) -> None:
    run_backup(database.app_backup, FakePgDump(), store, NIGHT, tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_a_failed_dump_stores_nothing(
    database: Database, store: BackupStore, tmp_path: Path
) -> None:
    with pytest.raises(DumpFailed):
        run_backup(database.app_backup, FakePgDump(fail=True), store, NIGHT, tmp_path)

    assert store.latest() is None
    assert list(tmp_path.iterdir()) == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/integration/test_backups.py tests/unit/adapters/test_pg_client.py tests/worker/test_ops_backup.py`
Expected: FAIL: `3 errors` during collection: `No module named 'nettriage.adapters.backups'` (twice) and `No module named 'nettriage.adapters.pg_client'`.

- [ ] **Step 3: Write the client, the store and the backup**

Create `backend/src/nettriage/adapters/backups.py`:
```python
"""The nightly backup's database and storage sides (Plan 7a §2.3, §2.5).

`exported_snapshot` opens a read-only REPEATABLE READ transaction as `app_backup`, exports its
snapshot and counts every table's rows in it; while it stays open, `pg_dump --snapshot` dumps
exactly the same moment, so the counts are what a restore must find. It also lists the roles the
row policies name, which a restore needs to exist first.

`BackupStore` keeps each night's dump and manifest under `pg/<day>.dump` and `pg/<day>.json`.
The manifest is written last, so a day counts as backed up only once both are there."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Connection, Engine, text

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client

PREFIX = "pg/"


@dataclass(frozen=True)
class Snapshot:
    id: str
    tables: dict[str, int]
    roles: list[str]
    server_version: str


@dataclass(frozen=True)
class Manifest:
    created_at: str
    server_version: str
    client_version: str
    tables: dict[str, int]
    roles: list[str]

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, body: str | bytes) -> Manifest:
        return cls(**json.loads(body))


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _count_tables(connection: Connection) -> dict[str, int]:
    tables: list[str] = list(
        connection.execute(
            text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY c.relname"
            )
        ).scalars()
    )
    return {
        table: int(
            connection.execute(text(f"SELECT count(*) FROM {_quoted(table)}")).scalar_one()  # noqa: S608
        )
        for table in tables
    }


def _policy_roles(connection: Connection) -> list[str]:
    """Only the roles a policy names: the others are only granted privileges, which a restore
    with `--no-privileges` doesn't recreate."""
    roles: list[str] = list(
        connection.execute(
            text("SELECT DISTINCT unnest(roles) FROM pg_policies WHERE schemaname = 'public'")
        ).scalars()
    )
    return sorted(role for role in roles if role != "public")


@contextmanager
def exported_snapshot(engine: Engine) -> Iterator[Snapshot]:
    with engine.connect() as connection:
        connection.execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)
        with connection.begin():
            snapshot_id = str(connection.execute(text("SELECT pg_export_snapshot()")).scalar_one())
            server_version = str(connection.execute(text("SHOW server_version")).scalar_one())
            yield Snapshot(
                id=snapshot_id,
                tables=_count_tables(connection),
                roles=_policy_roles(connection),
                server_version=server_version.split()[0],
            )


class BackupStore:
    def __init__(self, client: S3Client, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    def put(self, day: str, dump: Path, manifest: Manifest) -> None:
        self.client.upload_file(str(dump), self.bucket, f"{PREFIX}{day}.dump")
        self.client.put_object(
            Bucket=self.bucket,
            Key=f"{PREFIX}{day}.json",
            Body=manifest.to_json().encode(),
            ContentType="application/json",
        )

    def latest(self) -> str | None:
        """The newest day with both its dump and its manifest."""
        names: set[str] = set()
        for page in self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=PREFIX
        ):
            names.update(item["Key"].removeprefix(PREFIX) for item in page.get("Contents", []))
        days = {
            name.removesuffix(".dump")
            for name in names
            if name.endswith(".dump") and f"{name.removesuffix('.dump')}.json" in names
        }
        return max(days) if days else None

    def fetch(self, day: str, dump: Path) -> Manifest:
        self.client.download_file(self.bucket, f"{PREFIX}{day}.dump", str(dump))
        body = self.client.get_object(Bucket=self.bucket, Key=f"{PREFIX}{day}.json")["Body"]
        return Manifest.from_json(body.read())
```

Create `backend/src/nettriage/adapters/pg_client.py`:
```python
"""Running the Postgres programs the `ops` function's layer holds (Plan 7a §2.3, §2.5). The
password travels in the program's environment, never on its command line; remote hosts get the
same verified TLS as the app's own connections; and a failure says only how the program exited,
never what it printed, which could quote data (spec §9.3)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import certifi
from sqlalchemy.engine import make_url

from nettriage.adapters.postgres import CONNECT_TIMEOUT_SECONDS, LOCAL_HOSTS

# Where Lambda extracts the layer (tools/pack_pg_client.py).
LAYER_BIN = Path("/opt/pg/bin")

type Run = Callable[..., subprocess.CompletedProcess[str]]


class ProgramFailed(Exception):
    """A Postgres program exited with an error; the message holds its status only."""


class DumpFailed(ProgramFailed):
    pass


@dataclass(frozen=True)
class Target:
    """Where a program connects, and as whom."""

    host: str
    port: int
    database: str
    user: str
    password: str

    @classmethod
    def from_url(cls, url: str) -> Target:
        parsed = make_url(url)
        return cls(
            host=parsed.host or "",
            port=parsed.port or 5432,
            database=parsed.database or "",
            user=parsed.username or "",
            password=parsed.password or "",
        )

    def environment(self) -> dict[str, str]:
        env = {
            "PGHOST": self.host,
            "PGPORT": str(self.port),
            "PGDATABASE": self.database,
            "PGUSER": self.user,
            "PGPASSWORD": self.password,
            "PGCONNECT_TIMEOUT": str(CONNECT_TIMEOUT_SECONDS),
        }
        if self.host not in LOCAL_HOSTS:
            env |= {"PGSSLMODE": "verify-full", "PGSSLROOTCERT": certifi.where()}
        return env


class PgClient:
    def __init__(self, bin_dir: Path = LAYER_BIN, run: Run = subprocess.run) -> None:
        self.bin_dir = bin_dir
        self.run = run

    def _run(
        self, program: str, args: Sequence[str], env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        # Only what the program needs: nothing else from this function's environment.
        return self.run(
            [str(self.bin_dir / program), *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def version(self) -> str:
        """The client's version, such as "17.11"."""
        return self._run("pg_dump", ["--version"], {}).stdout.split()[-1]

    def dump(self, target: Target, snapshot: str, out: Path) -> None:
        """A custom-format dump of the target's database as of an exported snapshot, reading
        through row-level security (the backup role's policies let it see every row)."""
        result = self._run(
            "pg_dump",
            [
                "--format=custom",
                "--enable-row-security",
                f"--snapshot={snapshot}",
                "--no-password",
                f"--file={out}",
            ],
            target.environment(),
        )
        if result.returncode != 0:
            raise DumpFailed(f"pg_dump exited with status {result.returncode}")
```

Create `backend/src/nettriage/entrypoints/ops/__init__.py`:
```python
"""The ops function (Plan 7a; spec §9.6, §9.7): five scheduled jobs that keep NetTriage running,
from the 5-minute probe to the weekly restore drill."""
```

Create `backend/src/nettriage/entrypoints/ops/backup.py`:
```python
"""The nightly backup (Plan 7a §2.3): count every table in an exported snapshot, dump that same
snapshot, then store the dump and a manifest of the counts under the day's name. The local dump
is removed whatever happens, and a dump that fails stores nothing."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import Engine

from nettriage.adapters.backups import BackupStore, Manifest, exported_snapshot
from nettriage.adapters.pg_client import Target

DUMP_NAME = "nettriage.dump"


class Dumper(Protocol):
    def version(self) -> str: ...

    def dump(self, target: Target, snapshot: str, out: Path) -> None: ...


def run_backup(
    engine: Engine, pg_dump: Dumper, store: BackupStore, now: datetime, workdir: Path
) -> Manifest:
    """`engine` connects as `app_backup` to the database's direct endpoint: `pg_dump` needs a
    session, which Neon's pooler doesn't keep."""
    dump = workdir / DUMP_NAME
    target = Target.from_url(engine.url.render_as_string(hide_password=False))
    try:
        with exported_snapshot(engine) as snapshot:
            pg_dump.dump(target, snapshot.id, dump)
        manifest = Manifest(
            created_at=now.isoformat(),
            server_version=snapshot.server_version,
            client_version=pg_dump.version(),
            tables=snapshot.tables,
            roles=snapshot.roles,
        )
        store.put(now.date().isoformat(), dump, manifest)
    finally:
        dump.unlink(missing_ok=True)
    return manifest
```

- [ ] **Step 4: Run the tests again**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/integration/test_backups.py tests/unit/adapters/test_pg_client.py tests/worker/test_ops_backup.py`
Expected: `15 passed`. `test_the_dump_sees_the_same_moment_as_the_counts` joins the exported snapshot from a second session, as `pg_dump --snapshot` does; CI's end-to-end job (Task 8) runs the real `pg_dump`.

- [ ] **Step 5: Check the backend**

Run: `cd backend && uv run python -m ruff check . && uv run python -m ruff format --check . && uv run python -m mypy`
Expected: Ruff passes and finds every file formatted, and mypy reports `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/nettriage/adapters/pg_client.py backend/src/nettriage/adapters/backups.py backend/src/nettriage/entrypoints/ops backend/tests/integration/test_backups.py backend/tests/unit/adapters/test_pg_client.py backend/tests/worker/test_ops_backup.py
git commit -m "feat(ops): the nightly backup: pg_dump of an exported snapshot as app_backup, stored in S3 under the day with a manifest of every table's rows in that snapshot" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 6: The weekly restore drill

**Files:**
- Modify: `backend/src/nettriage/adapters/pg_client.py`
- Create: `backend/src/nettriage/entrypoints/ops/drill.py`
- Test: `backend/tests/unit/adapters/test_throwaway_server.py`, `backend/tests/integration/test_drill_sql.py`, `backend/tests/worker/test_ops_drill.py`

**Interfaces:**
- Consumes: `PgClient`, `ProgramFailed` (Task 5); `BackupStore` and `Manifest` (Task 5).
- Produces:
  - in `pg_client.py`: `quote_identifier(name) -> str`; the `SqlRunner` protocol (`execute(conninfo, statements)`, one statement at a time outside a transaction, and `count_tables(conninfo) -> dict[str, int]`) and its psycopg implementation `PsycopgSql`; `RestoreFailed(ProgramFailed)`; `SUPERUSER = "drill"`, `RESTORER = "restorer"`, `RESTORED = "restored"`, `PORT = 5433`; and `ThrowawayServer(client: PgClient, home: Path, sql: SqlRunner | None = None)`, a context manager with `prepare(roles)`, `restore(dump)` and `counts()`, which stops the server on leaving if it started;
  - in `entrypoints/ops/drill.py`: `NoBackup`, `DrillFailed` ("The restored backup of {day} differs from its manifest in {tables}."), `DrillResult(day: str, tables: int, rows: int)`, the `Server` protocol, and `run_restore_drill(store, throwaway: Callable[[Path], Server], workdir: Path) -> DrillResult`, which first removes a folder a drill cut short left behind, and removes the dump and the server's folder whatever happens.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/integration/test_drill_sql.py`:
```python
"""The restore drill's SQL against a real server (Plan 7a §2.5): setup statements run outside a
transaction, as CREATE DATABASE needs, and the restored tables are counted like the backup's."""

from __future__ import annotations

from uuid import uuid4

from conftest import Database
from psycopg.conninfo import make_conninfo
from sqlalchemy import text

from nettriage.adapters.pg_client import PsycopgSql


def conninfo(database: Database) -> str:
    url = database.admin.url
    return make_conninfo(
        host=url.host or "",
        port=str(url.port or 5432),
        user=url.username or "",
        password=url.password or "",
        dbname=url.database or "",
    )


def test_the_restored_tables_are_counted_like_the_backup_counted_them(database: Database) -> None:
    counts = PsycopgSql().count_tables(conninfo(database))

    with database.admin.begin() as connection:
        organizations: int = connection.execute(
            text("SELECT count(*) FROM organizations")
        ).scalar_one()
    assert counts["organizations"] == organizations
    assert {"uploads", "findings", "audit_log", "alembic_version"} <= set(counts)


def test_setup_statements_run_outside_a_transaction(database: Database) -> None:
    name = f"drill_check_{uuid4().hex[:8]}"

    PsycopgSql().execute(
        conninfo(database), [f'CREATE DATABASE "{name}"', f'DROP DATABASE "{name}"']
    )

    with database.admin.begin() as connection:
        left: int = connection.execute(
            text("SELECT count(*) FROM pg_database WHERE datname = :name"), {"name": name}
        ).scalar_one()
    assert left == 0
```

Create `backend/tests/unit/adapters/test_throwaway_server.py`:
```python
"""The restore drill's throwaway server (Plan 7a §2.5): initialized and started in its own
folder, reachable only on a Unix socket, with `mmap` shared memory (Lambda has no /dev/shm); the
dump's policy roles and a non-superuser owner exist before the restore, which runs as that
owner, as a real recovery into Neon would; and the server stops whatever happens."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from psycopg.conninfo import conninfo_to_dict

from nettriage.adapters.pg_client import PgClient, ProgramFailed, RestoreFailed, ThrowawayServer

BIN = Path("/opt/pg/bin")


class FakeRun:
    def __init__(self, fail: str | None = None) -> None:
        self.fail = fail
        self.calls: list[tuple[str, list[str], dict[str, str]]] = []

    def __call__(
        self, args: Sequence[str], *, env: dict[str, str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        program = Path(args[0]).name
        self.calls.append((program, list(args[1:]), env))
        returncode = 1 if program == self.fail else 0
        return subprocess.CompletedProcess(list(args), returncode, "", "secret row in stderr")

    def programs(self) -> list[str]:
        return [program for program, _, _ in self.calls]


class FakeSql:
    def __init__(self, counts: dict[str, int] | None = None) -> None:
        self.statements: list[tuple[str, str]] = []
        self.counts = counts or {}

    def execute(self, conninfo: str, statements: list[str]) -> None:
        self.statements.extend((conninfo, statement) for statement in statements)

    def count_tables(self, conninfo: str) -> dict[str, int]:
        self.statements.append((conninfo, "<count>"))
        return self.counts


def server(tmp_path: Path, run: FakeRun, sql: FakeSql) -> ThrowawayServer:
    return ThrowawayServer(PgClient(BIN, run=run), tmp_path / "drill", sql=sql)


def test_the_server_starts_in_its_folder_on_a_socket_only(tmp_path: Path) -> None:
    run, sql = FakeRun(), FakeSql()

    with server(tmp_path, run, sql) as drill:
        drill.prepare(["app_ops", "app_backup"])

    initdb, start = run.calls[0], run.calls[1]
    data = str(tmp_path / "drill" / "data")
    assert initdb[0] == "initdb"
    assert initdb[1] == [
        f"--pgdata={data}",
        "--username=drill",
        "--auth=trust",
        "--no-sync",
        "--encoding=UTF8",
        "--locale=C",
    ]
    assert start[0] == "pg_ctl"
    options = start[1][start[1].index("-o") + 1]
    assert "-c dynamic_shared_memory_type=mmap" in options
    assert "-c listen_addresses=" in options
    assert f"-c unix_socket_directories={tmp_path / 'drill'}" in options
    assert "-c fsync=off" in options
    assert start[1][-1] == "start"
    assert "-w" in start[1]


def test_the_policy_roles_and_a_non_superuser_owner_exist_before_the_restore(
    tmp_path: Path,
) -> None:
    run, sql = FakeRun(), FakeSql()

    with server(tmp_path, run, sql) as drill:
        drill.prepare(["app_ops", 'we"ird'])

    assert [statement for _, statement in sql.statements] == [
        'CREATE ROLE "app_ops" NOLOGIN',
        'CREATE ROLE "we""ird" NOLOGIN',
        'CREATE ROLE "restorer" LOGIN NOSUPERUSER',
        'CREATE DATABASE "restored" OWNER "restorer"',
    ]
    conninfo = sql.statements[0][0]
    assert {key: conninfo_to_dict(conninfo)[key] for key in ("host", "user")} == {
        "host": str(tmp_path / "drill"),
        "user": "drill",
    }


def test_the_dump_is_restored_as_the_owner_without_owners_or_privileges(tmp_path: Path) -> None:
    run, sql = FakeRun(), FakeSql()
    dump = tmp_path / "latest.dump"

    with server(tmp_path, run, sql) as drill:
        drill.prepare([])
        drill.restore(dump)

    [(_, args, env)] = [call for call in run.calls if call[0] == "pg_restore"]
    assert args == [
        "--dbname=restored",
        "--no-owner",
        "--no-privileges",
        "--exit-on-error",
        str(dump),
    ]
    assert (env["PGHOST"], env["PGUSER"]) == (str(tmp_path / "drill"), "restorer")
    assert "PGPASSWORD" not in env


def test_the_restored_rows_are_counted_as_the_servers_superuser(tmp_path: Path) -> None:
    run, sql = FakeRun(), FakeSql(counts={"organizations": 3})

    with server(tmp_path, run, sql) as drill:
        drill.prepare([])
        assert drill.counts() == {"organizations": 3}

    conninfo = conninfo_to_dict(sql.statements[-1][0])
    assert (conninfo["user"], conninfo["dbname"]) == ("drill", "restored")


def drill(throwaway: ThrowawayServer, dump: Path) -> None:
    with throwaway as running:
        running.prepare([])
        running.restore(dump)


def test_a_failed_restore_says_only_how_it_exited_and_still_stops_the_server(
    tmp_path: Path,
) -> None:
    run, sql = FakeRun(fail="pg_restore"), FakeSql()

    with pytest.raises(RestoreFailed) as failure:
        drill(server(tmp_path, run, sql), tmp_path / "latest.dump")

    assert str(failure.value) == "pg_restore exited with status 1"
    assert run.programs()[-1] == "pg_ctl"
    assert run.calls[-1][1][-1] == "stop"


def test_a_server_that_wont_initialize_fails_the_drill(tmp_path: Path) -> None:
    run, sql = FakeRun(fail="initdb"), FakeSql()

    with pytest.raises(ProgramFailed, match="initdb exited with status 1"):
        drill(server(tmp_path, run, sql), tmp_path / "latest.dump")

    assert sql.statements == []
    assert run.programs() == ["initdb"]
```

Create `backend/tests/worker/test_ops_drill.py`:
```python
"""The weekly restore drill (Plan 7a §2.5), with S3 in moto and the throwaway server stood in
for: it restores the newest backup and checks every table's rows against the manifest, fails
naming the tables that differ (never their rows), and leaves nothing behind."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from conftest import REGION

from nettriage.adapters.backups import BackupStore, Manifest
from nettriage.entrypoints.ops.drill import DrillFailed, NoBackup, run_restore_drill

BUCKET = "nettriage-test-backups"
TABLES = {"organizations": 2, "uploads": 7, "audit_log": 40}


class FakeServer:
    def __init__(self, home: Path, restored: dict[str, int]) -> None:
        self.home = home
        self.restored = restored
        self.prepared: list[str] | None = None
        self.dump: Path | None = None
        self.stopped = False

    def __enter__(self) -> FakeServer:
        self.home.mkdir(parents=True)
        (self.home / "data").mkdir()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stopped = True

    def prepare(self, roles: list[str]) -> None:
        self.prepared = roles

    def restore(self, dump: Path) -> None:
        self.dump = dump
        assert dump.read_bytes() == b"PGDMP night two"

    def counts(self) -> dict[str, int]:
        return self.restored


@pytest.fixture
def store() -> Iterator[BackupStore]:
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-north-1"}
        )
        yield BackupStore(client, BUCKET)


def backed_up(store: BackupStore, tmp_path: Path) -> None:
    for day, body in (("2026-10-06", b"PGDMP night one"), ("2026-10-07", b"PGDMP night two")):
        dump = tmp_path / f"{day}.dump"
        dump.write_bytes(body)
        store.put(
            day,
            dump,
            Manifest(
                created_at=f"{day}T02:00:00+00:00",
                server_version="17.5",
                client_version="17.11",
                tables=TABLES,
                roles=["app_backup", "app_ops"],
            ),
        )
        dump.unlink()


def test_the_newest_backup_is_restored_and_matches_its_manifest(
    store: BackupStore, tmp_path: Path
) -> None:
    backed_up(store, tmp_path)
    servers: list[FakeServer] = []

    def throwaway(home: Path) -> FakeServer:
        servers.append(FakeServer(home, dict(TABLES)))
        return servers[-1]

    result = run_restore_drill(store, throwaway, tmp_path)

    assert (result.day, result.tables, result.rows) == ("2026-10-07", 3, 49)
    [server] = servers
    assert server.prepared == ["app_backup", "app_ops"]
    assert server.stopped


@pytest.mark.parametrize(
    "restored",
    [
        {"organizations": 2, "uploads": 6, "audit_log": 40},
        {"organizations": 2, "audit_log": 40},
        {**TABLES, "stray": 1},
    ],
)
def test_a_restore_that_differs_from_its_manifest_fails_naming_the_tables(
    store: BackupStore, tmp_path: Path, restored: dict[str, int]
) -> None:
    backed_up(store, tmp_path)

    with pytest.raises(DrillFailed) as failure:
        run_restore_drill(store, lambda home: FakeServer(home, restored), tmp_path)

    message = str(failure.value)
    assert message.startswith("The restored backup of 2026-10-07 differs from its manifest in ")
    assert ("uploads" in message) or ("stray" in message)


def test_without_any_backup_the_drill_fails(store: BackupStore, tmp_path: Path) -> None:
    with pytest.raises(NoBackup):
        run_restore_drill(store, lambda home: FakeServer(home, {}), tmp_path)


def test_the_drill_leaves_nothing_behind(store: BackupStore, tmp_path: Path) -> None:
    backed_up(store, tmp_path)

    run_restore_drill(store, lambda home: FakeServer(home, dict(TABLES)), tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_a_drill_cut_short_by_the_timeout_leaves_nothing_in_the_next_ones_way(
    store: BackupStore, tmp_path: Path
) -> None:
    """The timeout stops a drill before it cleans up, and a warm function keeps its /tmp."""
    backed_up(store, tmp_path)
    leftover = tmp_path / "drill" / "data"
    leftover.mkdir(parents=True)
    (leftover / "PG_VERSION").write_text("17\n")

    result = run_restore_drill(store, lambda home: FakeServer(home, dict(TABLES)), tmp_path)

    assert result.day == "2026-10-07"
    assert list(tmp_path.iterdir()) == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/unit/adapters/test_throwaway_server.py tests/integration/test_drill_sql.py tests/worker/test_ops_drill.py`
Expected: FAIL: `3 errors` during collection: `cannot import name 'RestoreFailed' from 'nettriage.adapters.pg_client'`, `cannot import name 'PsycopgSql'`, and `No module named 'nettriage.entrypoints.ops.drill'`.

- [ ] **Step 3: Write the throwaway server and the drill**

In `backend/src/nettriage/adapters/pg_client.py`, replace:
```python
from dataclasses import dataclass
from pathlib import Path

import certifi
from sqlalchemy.engine import make_url

```
with:
```python
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import certifi
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from sqlalchemy.engine import make_url

```

In `backend/src/nettriage/adapters/pg_client.py`, replace:
```python
        self.run = run

    def _run(
        self, program: str, args: Sequence[str], env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
```
with:
```python
        self.run = run

    def program(
        self, program: str, args: Sequence[str], env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
```

In `backend/src/nettriage/adapters/pg_client.py`, replace:
```python
        )

    def version(self) -> str:
        """The client's version, such as "17.11"."""
        return self._run("pg_dump", ["--version"], {}).stdout.split()[-1]

    def dump(self, target: Target, snapshot: str, out: Path) -> None:
        """A custom-format dump of the target's database as of an exported snapshot, reading
        through row-level security (the backup role's policies let it see every row)."""
        result = self._run(
            "pg_dump",
            [
```
with:
```python
        )

    def checked(
        self,
        program: str,
        args: Sequence[str],
        env: dict[str, str],
        failure: type[ProgramFailed] = ProgramFailed,
    ) -> None:
        result = self.program(program, args, env)
        if result.returncode != 0:
            raise failure(f"{program} exited with status {result.returncode}")

    def version(self) -> str:
        """The client's version, such as "17.11"."""
        return self.program("pg_dump", ["--version"], {}).stdout.split()[-1]

    def dump(self, target: Target, snapshot: str, out: Path) -> None:
        """A custom-format dump of the target's database as of an exported snapshot, reading
        through row-level security (the backup role's policies let it see every row)."""
        self.checked(
            "pg_dump",
            [
```

In `backend/src/nettriage/adapters/pg_client.py`, replace:
```python
            ],
            target.environment(),
        )
        if result.returncode != 0:
            raise DumpFailed(f"pg_dump exited with status {result.returncode}")
```
with:
```python
            ],
            target.environment(),
            DumpFailed,
        )


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class SqlRunner(Protocol):
    def execute(self, conninfo: str, statements: list[str]) -> None: ...

    def count_tables(self, conninfo: str) -> dict[str, int]: ...


class PsycopgSql:
    """The drill's SQL, through psycopg (which brings its own libpq)."""

    def execute(self, conninfo: str, statements: list[str]) -> None:
        """Outside a transaction, as CREATE DATABASE needs. The statements are built by the
        drill from quoted identifiers, never from input."""
        with psycopg.connect(conninfo, autocommit=True) as connection:
            for statement in statements:
                connection.execute(statement.encode())

    def count_tables(self, conninfo: str) -> dict[str, int]:
        with psycopg.connect(conninfo) as connection:
            tables = [
                str(row[0])
                for row in connection.execute(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY c.relname"
                )
            ]
            counts: dict[str, int] = {}
            for table in tables:
                row = connection.execute(
                    sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
                ).fetchone()
                counts[table] = int(row[0]) if row else 0
            return counts


class RestoreFailed(ProgramFailed):
    pass


# The throwaway server's names (Plan 7a §2.5).
SUPERUSER = "drill"
RESTORER = "restorer"
RESTORED = "restored"
PORT = 5433


class ThrowawayServer:
    """A Postgres server in its own folder for one restore drill, reachable only on a Unix
    socket there, with `mmap` shared memory (Lambda has no /dev/shm) and no durability (it's
    thrown away). The restore runs as a non-superuser owner, as a recovery into Neon would; the
    rows are counted as the server's superuser, past row-level security. Leaving the `with`
    block stops the server, whatever happened."""

    def __init__(self, client: PgClient, home: Path, sql: SqlRunner | None = None) -> None:
        self.client = client
        self.home = home
        self.data = home / "data"
        self.sql: SqlRunner = sql or PsycopgSql()
        self.started = False

    def __enter__(self) -> ThrowawayServer:
        return self

    def __exit__(self, *exc: object) -> None:
        if self.started:
            # Best effort: a stop that fails mustn't hide what failed the drill.
            self.client.program("pg_ctl", [f"--pgdata={self.data}", "-m", "fast", "-w", "stop"], {})
            self.started = False

    def _conninfo(self, user: str, database: str) -> str:
        return make_conninfo(host=str(self.home), port=str(PORT), user=user, dbname=database)

    def prepare(self, roles: list[str]) -> None:
        """Initialize and start the server, then create the dump's policy roles and the owner
        the restore runs as."""
        self.home.mkdir(parents=True, exist_ok=True)
        self.client.checked(
            "initdb",
            [
                f"--pgdata={self.data}",
                f"--username={SUPERUSER}",
                "--auth=trust",
                "--no-sync",
                "--encoding=UTF8",
                "--locale=C",
            ],
            {},
        )
        options = " ".join(
            [
                "-c dynamic_shared_memory_type=mmap",
                "-c listen_addresses=",
                f"-c unix_socket_directories={self.home}",
                f"-c port={PORT}",
                "-c fsync=off",
                "-c full_page_writes=off",
                "-c synchronous_commit=off",
            ]
        )
        self.client.checked(
            "pg_ctl",
            [
                f"--pgdata={self.data}",
                f"--log={self.home / 'server.log'}",
                "-w",
                "-o",
                options,
                "start",
            ],
            {},
        )
        self.started = True
        self.sql.execute(
            self._conninfo(SUPERUSER, "postgres"),
            [f"CREATE ROLE {quote_identifier(role)} NOLOGIN" for role in roles]
            + [
                f"CREATE ROLE {quote_identifier(RESTORER)} LOGIN NOSUPERUSER",
                f"CREATE DATABASE {quote_identifier(RESTORED)} OWNER {quote_identifier(RESTORER)}",
            ],
        )

    def restore(self, dump: Path) -> None:
        self.client.checked(
            "pg_restore",
            [f"--dbname={RESTORED}", "--no-owner", "--no-privileges", "--exit-on-error", str(dump)],
            {"PGHOST": str(self.home), "PGPORT": str(PORT), "PGUSER": RESTORER},
            RestoreFailed,
        )

    def counts(self) -> dict[str, int]:
        return self.sql.count_tables(self._conninfo(SUPERUSER, RESTORED))
```

Create `backend/src/nettriage/entrypoints/ops/drill.py`:
```python
"""The weekly restore drill (Plan 7a §2.5): restore the newest backup into a throwaway server
and check every table's rows against its manifest. A difference fails the drill, naming the
tables (never their rows); the dump and the server's folder are removed whatever happens."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from nettriage.adapters.backups import BackupStore


class NoBackup(Exception):
    pass


class DrillFailed(Exception):
    pass


@dataclass(frozen=True)
class DrillResult:
    day: str
    tables: int
    rows: int


class Server(Protocol):
    def __enter__(self) -> Server: ...

    def __exit__(self, *exc: object) -> None: ...

    def prepare(self, roles: list[str]) -> None: ...

    def restore(self, dump: Path) -> None: ...

    def counts(self) -> dict[str, int]: ...


def run_restore_drill(
    store: BackupStore, throwaway: Callable[[Path], Server], workdir: Path
) -> DrillResult:
    day = store.latest()
    if day is None:
        raise NoBackup("There's no backup to restore yet.")
    dump = workdir / "latest.dump"
    home = workdir / "drill"
    # The timeout stops a drill before it cleans up, and a warm function keeps its /tmp.
    shutil.rmtree(home, ignore_errors=True)
    try:
        manifest = store.fetch(day, dump)
        with throwaway(home) as server:
            server.prepare(manifest.roles)
            server.restore(dump)
            restored = server.counts()
    finally:
        dump.unlink(missing_ok=True)
        shutil.rmtree(home, ignore_errors=True)
    differing = sorted(
        table
        for table in manifest.tables.keys() | restored.keys()
        if manifest.tables.get(table) != restored.get(table)
    )
    if differing:
        raise DrillFailed(
            f"The restored backup of {day} differs from its manifest in {', '.join(differing)}."
        )
    return DrillResult(day=day, tables=len(manifest.tables), rows=sum(manifest.tables.values()))
```

- [ ] **Step 4: Run the tests again**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/unit/adapters/test_throwaway_server.py tests/integration/test_drill_sql.py tests/worker/test_ops_drill.py`
Expected: `15 passed`.

- [ ] **Step 5: Check the backend**

Run: `cd backend && uv run python -m ruff check . && uv run python -m ruff format --check . && uv run python -m mypy`
Expected: Ruff passes and finds every file formatted, and mypy reports `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/nettriage/adapters/pg_client.py backend/src/nettriage/entrypoints/ops/drill.py backend/tests/unit/adapters/test_throwaway_server.py backend/tests/integration/test_drill_sql.py backend/tests/worker/test_ops_drill.py
git commit -m "feat(ops): the weekly restore drill: the newest backup restored into a throwaway Postgres as a non-superuser owner, every table's rows checked against its manifest" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 7: The `ops` function: jobs by name, telemetry and wiring

**Files:**
- Create: `backend/src/nettriage/entrypoints/ops/jobs.py`, `backend/src/nettriage/entrypoints/ops/wiring.py`, `backend/src/nettriage/entrypoints/ops/handler.py`
- Modify: `backend/src/nettriage/platform/metrics.py`, `backend/src/nettriage/platform/config.py`, `tools/build_lambda.py`
- Test: `backend/tests/worker/test_ops_jobs.py`, `backend/tests/unit/ops/test_ops_wiring.py`, `tools/tests/test_build_lambda.py`

**Interfaces:**
- Consumes: `clean_up` (Task 3); `health_answers`, `database_answers`, `dynamodb_answers` (Task 4); `run_backup`, `Dumper`, `BackupStore`, `PgClient` (Task 5); `run_restore_drill`, `Server`, `ThrowawayServer`, `DrillFailed`, `NoBackup` (Task 6); `read_parameters`, `create_database_engine`, the telemetry factories and `Settings`.
- Produces:
  - `Settings.backup_database_url_parameter`, `Settings.backups_bucket` and `Settings.app_url`, from `NETTRIAGE_BACKUP_DATABASE_URL_PARAMETER`, `NETTRIAGE_BACKUPS_BUCKET` and `NETTRIAGE_APP_URL` (the `ops` connection string is the existing `NETTRIAGE_DATABASE_URL_PARAMETER`);
  - `OpsMetrics(meter_provider=None)` with the gauge `probe_success` (`nettriage.probe.success`) and the counter `runs` (`nettriage.ops.runs`);
  - `JobFailed`, and the dataclass `Ops(http, app_url, ops_database, backup_database, dynamodb, runtime_table, store, pg_dump, throwaway, clock, metrics, tracer, workdir=Path("/tmp"), flush)` with `run(job: str) -> dict[str, str | int | bool]`, answering `{"job": job, …counts}`. An unknown job raises `ValueError`; a failure other than `DrillFailed` or `NoBackup` leaves as `JobFailed(<type>)`;
  - `build_ops(settings, tracer_provider, meter_provider, session=None) -> Ops`;
  - the Lambda handler `nettriage.entrypoints.ops.handler.handle(event, context)`, which Task 9's function names, and which flushes telemetry whatever happens;
  - `tools/build_lambda.py` refuses a package without the ops handler.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/ops/test_ops_wiring.py`:
```python
"""Building the `ops` function in Lambda (Plan 7a §2, §4): its two connection strings from SSM
(`app_ops` through the pooler, `app_backup` direct), the backups bucket, the runtime table, the
app's URL for the probe, and the layer's Postgres programs. The handler runs the job the
schedule names and flushes telemetry whatever happens."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from nettriage.adapters.parameters import MissingParameterError
from nettriage.adapters.pg_client import LAYER_BIN, PgClient
from nettriage.entrypoints.ops import handler
from nettriage.entrypoints.ops.wiring import build_ops
from nettriage.platform.config import Settings

SETTINGS = Settings(
    stage="dev",
    service_name="nettriage-ops",
    database_url_parameter="/nettriage/dev/db/app-ops-url",
    backup_database_url_parameter="/nettriage/dev/db/app-backup-url",
    runtime_table="nettriage-dev-runtime",
    backups_bucket="nettriage-dev-backups-1a2b3c4d",
    app_url="https://d1234.cloudfront.net",
)


@pytest.fixture
def session() -> Iterator[boto3.session.Session]:
    with mock_aws():
        yield boto3.session.Session(region_name="eu-north-1")


def store_urls(session: boto3.session.Session) -> None:
    ssm = session.client("ssm")
    ssm.put_parameter(
        Name="/nettriage/dev/db/app-ops-url",
        Value="postgresql://app_ops:pw@ep-x-pooler.eu-central-1.aws.neon.tech/neondb",
        Type="SecureString",
    )
    ssm.put_parameter(
        Name="/nettriage/dev/db/app-backup-url",
        Value="postgresql://app_backup:pw@ep-x.eu-central-1.aws.neon.tech/neondb",
        Type="SecureString",
    )


def test_the_jobs_run_as_their_own_roles_against_the_stages_resources(
    session: boto3.session.Session,
) -> None:
    store_urls(session)

    ops = build_ops(SETTINGS, TracerProvider(), MeterProvider(), session)

    assert ops.ops_database.url.username == "app_ops"
    assert ops.backup_database.url.username == "app_backup"
    assert ops.backup_database.url.host == "ep-x.eu-central-1.aws.neon.tech"
    assert ops.store.bucket == "nettriage-dev-backups-1a2b3c4d"
    assert ops.runtime_table == "nettriage-dev-runtime"
    assert ops.app_url == "https://d1234.cloudfront.net"
    assert isinstance(ops.pg_dump, PgClient)
    assert ops.pg_dump.bin_dir == LAYER_BIN
    assert ops.workdir == Path("/tmp")  # noqa: S108 - Lambda's scratch space
    ops.flush()


def test_a_missing_connection_string_is_named(session: boto3.session.Session) -> None:
    with pytest.raises(MissingParameterError, match="app-backup-url"):
        build_ops(SETTINGS, TracerProvider(), MeterProvider(), session)


class FakeOps:
    def __init__(self) -> None:
        self.jobs: list[str] = []
        self.flushed = 0

    def run(self, job: str) -> dict[str, str | int | bool]:
        self.jobs.append(job)
        if job == "backup":
            raise RuntimeError("dump failed")
        return {"job": job, "ok": True}

    def flush(self) -> None:
        self.flushed += 1


def test_the_handler_runs_the_scheduled_job_and_answers_with_its_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeOps()
    monkeypatch.setattr(handler, "ops", lambda: fake)

    response = handler.handle({"job": "probe"}, object())

    assert response == {"job": "probe", "ok": True}
    assert (fake.jobs, fake.flushed) == (["probe"], 1)


def test_a_failed_job_still_flushes_its_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeOps()
    monkeypatch.setattr(handler, "ops", lambda: fake)

    with pytest.raises(RuntimeError):
        handler.handle({"job": "backup"}, object())

    assert fake.flushed == 1


def test_an_event_without_a_job_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeOps()
    monkeypatch.setattr(handler, "ops", lambda: fake)

    with pytest.raises(ValueError, match="job"):
        handler.handle({}, object())
```

Create `backend/tests/worker/test_ops_jobs.py`:
```python
"""The `ops` function's jobs (Plan 7a §2, §6), with real Postgres, DynamoDB and S3 in moto, and the
Postgres programs stood in for. Each job answers with counts only, records
`nettriage.ops.runs` {job, outcome} and one span, and a failure is logged and traced by its type,
never its message."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path

import boto3
import httpx
import pytest
from conftest import REGION, Database, FakeClock, RuntimeTable
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, NumberDataPoint
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from tenantdata import add_tenant

from nettriage.adapters.backups import BackupStore
from nettriage.adapters.pg_client import DumpFailed, Target
from nettriage.adapters.postgres import create_database_engine
from nettriage.entrypoints.ops.drill import DrillFailed
from nettriage.entrypoints.ops.jobs import JobFailed, Ops
from nettriage.platform.metrics import OpsMetrics

APP = "https://d1234.cloudfront.net"
BUCKET = "nettriage-test-backups"


class FakePgDump:
    def __init__(self) -> None:
        self.fail = False

    def version(self) -> str:
        return "17.11"

    def dump(self, target: Target, snapshot: str, out: Path) -> None:
        if self.fail:
            raise DumpFailed("pg_dump exited with status 1")
        out.write_bytes(b"PGDMP")


class FakeServer:
    """Restores exactly what the manifest counted, unless told to lose a row."""

    def __init__(self, home: Path, lose_a_row: bool) -> None:
        self.home = home
        self.lose_a_row = lose_a_row
        self.dump: Path | None = None

    def __enter__(self) -> FakeServer:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def prepare(self, roles: list[str]) -> None:
        return None

    def restore(self, dump: Path) -> None:
        self.dump = dump

    def counts(self) -> dict[str, int]:
        manifest = json.loads((self.home.parent / "expected.json").read_text(encoding="utf-8"))
        counts: dict[str, int] = dict(manifest)
        if self.lose_a_row:
            counts["organizations"] -= 1
        return counts


@dataclass
class Rig:
    ops: Ops
    health: list[httpx.Response]
    pg_dump: FakePgDump
    spans: InMemorySpanExporter
    metrics: InMemoryMetricReader
    store: BackupStore
    workdir: Path
    lose_a_row: list[bool]


@pytest.fixture
def rig(database: Database, runtime_table: RuntimeTable, clock: FakeClock, tmp_path: Path) -> Rig:
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-north-1"})
    store = BackupStore(s3, BUCKET)
    health = [httpx.Response(200, json={"status": "ok", "version": "abc"})]
    spans = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    reader = InMemoryMetricReader()
    pg_dump = FakePgDump()
    lose_a_row = [False]
    workdir = tmp_path / "tmp"
    workdir.mkdir()

    def throwaway(home: Path) -> FakeServer:
        latest = store.latest()
        assert latest is not None
        manifest = store.fetch(latest, tmp_path / "peek.dump")
        (home.parent / "expected.json").write_text(json.dumps(manifest.tables), encoding="utf-8")
        return FakeServer(home, lose_a_row[0])

    ops = Ops(
        http=httpx.Client(transport=httpx.MockTransport(lambda request: health[0])),
        app_url=APP,
        ops_database=database.app_ops,
        backup_database=database.app_backup,
        dynamodb=runtime_table.client,
        runtime_table=runtime_table.name,
        store=store,
        pg_dump=pg_dump,
        throwaway=throwaway,
        clock=clock,
        metrics=OpsMetrics(MeterProvider(metric_readers=[reader])),
        tracer=tracer_provider.get_tracer("test"),
        workdir=workdir,
    )
    return Rig(ops, health, pg_dump, spans, reader, store, workdir, lose_a_row)


def points(reader: InMemoryMetricReader, name: str) -> dict[tuple[tuple[str, str], ...], float]:
    data = reader.get_metrics_data()
    found: dict[tuple[tuple[str, str], ...], float] = {}
    for resource in data.resource_metrics if data else []:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name != name:
                    continue
                for point in metric.data.data_points:
                    if isinstance(point, NumberDataPoint):
                        key = tuple(
                            sorted((k, str(v)) for k, v in (point.attributes or {}).items())
                        )
                        found[key] = point.value
    return found


def run_counted(rig: Rig, job: str, outcome: str) -> float:
    return points(rig.metrics, "nettriage.ops.runs").get((("job", job), ("outcome", outcome)), 0)


def test_a_healthy_app_is_probed_as_a_success(rig: Rig) -> None:
    result = rig.ops.run("probe")

    assert result == {"job": "probe", "ok": True}
    assert points(rig.metrics, "nettriage.probe.success") == {(("check", "health"),): 1}
    assert run_counted(rig, "probe", "success") == 1


def test_an_unhealthy_app_is_a_failed_probe_without_an_error(rig: Rig) -> None:
    rig.health[0] = httpx.Response(503)

    result = rig.ops.run("probe")

    assert result == {"job": "probe", "ok": False}
    assert points(rig.metrics, "nettriage.probe.success") == {(("check", "health"),): 0}
    assert run_counted(rig, "probe", "failure") == 1


def test_the_hourly_check_reaches_the_database_and_the_runtime_table(rig: Rig) -> None:
    result = rig.ops.run("check")

    assert result == {"job": "check", "database": True, "dynamodb": True}
    assert points(rig.metrics, "nettriage.probe.success") == {
        (("check", "database"),): 1,
        (("check", "dynamodb"),): 1,
    }


def test_the_cleanup_answers_with_its_counts(rig: Rig) -> None:
    result = rig.ops.run("cleanup")

    assert set(result) == {
        "job",
        "uploads_expired",
        "analyses_failed",
        "invitations_deleted",
        "audit_rows_deleted",
    }
    assert run_counted(rig, "cleanup", "success") == 1


def test_the_backup_answers_with_its_day_tables_and_rows(rig: Rig, database: Database) -> None:
    add_tenant(database.admin)

    result = rig.ops.run("backup")

    assert result["job"] == "backup"
    assert result["day"] == "2026-09-28"
    assert int(result["tables"]) >= 14
    assert int(result["rows"]) >= 1
    [span] = rig.spans.get_finished_spans()
    assert span.name == "ops.backup"
    assert span.attributes is not None
    assert span.attributes["ops.tables"] == result["tables"]
    assert run_counted(rig, "backup", "success") == 1


def test_a_failed_backup_fails_the_run_and_reports_its_type_only(
    rig: Rig, logs: io.StringIO
) -> None:
    rig.pg_dump.fail = True

    with pytest.raises(JobFailed) as failure:
        rig.ops.run("backup")

    assert str(failure.value) == "DumpFailed"
    assert run_counted(rig, "backup", "failure") == 1
    [span] = rig.spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description == "DumpFailed"
    [line] = [json.loads(line) for line in logs.getvalue().splitlines() if "ops_job_failed" in line]
    assert (line["job"], line["error_code"]) == ("backup", "DumpFailed")
    assert "status 1" not in logs.getvalue()


def test_the_drill_restores_the_newest_backup_and_answers_with_its_counts(
    rig: Rig, database: Database
) -> None:
    add_tenant(database.admin)
    backup = rig.ops.run("backup")

    result = rig.ops.run("restore_drill")

    assert result == {
        "job": "restore_drill",
        "day": backup["day"],
        "tables": backup["tables"],
        "rows": backup["rows"],
    }
    assert run_counted(rig, "restore_drill", "success") == 1


def test_a_drill_that_finds_a_difference_fails_the_run(rig: Rig, database: Database) -> None:
    add_tenant(database.admin)
    rig.ops.run("backup")
    rig.lose_a_row[0] = True

    with pytest.raises(DrillFailed):
        rig.ops.run("restore_drill")

    assert run_counted(rig, "restore_drill", "failure") == 1


def test_an_error_that_could_quote_the_database_leaves_by_its_type_only(
    rig: Rig, database: Database
) -> None:
    """Lambda logs what the handler raises, and a database error can quote connection details."""
    gone = database.app_ops.url.set(database="nettriage_gone")
    rig.ops.ops_database = create_database_engine(
        gone.render_as_string(hide_password=False), pool_size=1
    )

    with pytest.raises(JobFailed) as failure:
        rig.ops.run("cleanup")

    assert str(failure.value) == "OperationalError"
    assert failure.value.__cause__ is None
    assert failure.value.__suppress_context__
    assert run_counted(rig, "cleanup", "failure") == 1


def test_an_unknown_job_is_refused(rig: Rig) -> None:
    with pytest.raises(ValueError, match="unknown job"):
        rig.ops.run("defrag")
```

In `tools/tests/test_build_lambda.py`, replace:
```python
        "nettriage/entrypoints/analyze/handler.py": b"def handle(event, context): pass\n",
        "nettriage/entrypoints/triage/handler.py": b"def handle(event, context): pass\n",
        "nettriage/prompts/triage/v1.md": b"You explain findings.\n",
        "fastapi-1.0.dist-info/WHEEL": f"Wheel-Version: 1.0\nTag: {wheel_tag}\n".encode(),
```
with:
```python
        "nettriage/entrypoints/analyze/handler.py": b"def handle(event, context): pass\n",
        "nettriage/entrypoints/triage/handler.py": b"def handle(event, context): pass\n",
        "nettriage/entrypoints/ops/handler.py": b"def handle(event, context): pass\n",
        "nettriage/prompts/triage/v1.md": b"You explain findings.\n",
        "fastapi-1.0.dist-info/WHEEL": f"Wheel-Version: 1.0\nTag: {wheel_tag}\n".encode(),
```

In `tools/tests/test_build_lambda.py`, replace:
```python
    with pytest.raises(PackageError, match=f"missing {name}"):
        validate_zip(out)
```
with:
```python
    with pytest.raises(PackageError, match=f"missing {name}"):
        validate_zip(out)


def test_a_package_without_the_ops_function_is_rejected(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    (package / "nettriage/entrypoints/ops/handler.py").unlink()
    out = tmp_path / "backend.zip"
    write_zip(package, out)

    with pytest.raises(PackageError, match="missing nettriage/entrypoints/ops/handler.py"):
        validate_zip(out)
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/worker/test_ops_jobs.py tests/unit/ops/test_ops_wiring.py ../tools/tests/test_build_lambda.py`
Expected: FAIL: `2 errors` during collection: `No module named 'nettriage.entrypoints.ops.jobs'`, and `cannot import name 'handler' from 'nettriage.entrypoints.ops'`.

- [ ] **Step 3: Write the jobs, their telemetry, the wiring and the handler**

Create `backend/src/nettriage/entrypoints/ops/handler.py`:
```python
"""The ops Lambda's entry point (Plan 7a §2): `nettriage.entrypoints.ops.handler.handle`.
EventBridge Scheduler invokes it with `{"job": "<name>"}`, and the owner's
`just restore-drill-<stage>` with `{"job": "restore_drill"}`. It answers with the job's counts;
an exception fails the invocation, so Lambda retries an asynchronous one."""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from typing import Any, Protocol

from nettriage.entrypoints.ops.wiring import build_ops
from nettriage.platform.config import Settings
from nettriage.platform.logging import configure_logging
from nettriage.platform.telemetry import (
    create_meter_provider,
    create_tracer_provider,
    install_global_providers,
)


class Runner(Protocol):
    def run(self, job: str) -> dict[str, str | int | bool]: ...

    def flush(self) -> None: ...


@cache
def ops() -> Runner:
    """Built on the first invocation of each Lambda instance, then reused."""
    settings = Settings()
    configure_logging(settings)
    tracer_provider = create_tracer_provider(settings)
    meter_provider = create_meter_provider(settings)
    install_global_providers(tracer_provider, meter_provider)
    return build_ops(settings, tracer_provider, meter_provider)


def handle(event: Mapping[str, Any], context: object) -> dict[str, str | int | bool]:
    job = event.get("job")
    if not isinstance(job, str):
        raise ValueError("the event names no job")
    current = ops()
    try:
        return current.run(job)
    finally:
        current.flush()
```

Create `backend/src/nettriage/entrypoints/ops/jobs.py`:
```python
"""The `ops` function's jobs (Plan 7a §2): `probe` every 5 minutes, `check` hourly, `backup`
nightly, `cleanup` daily and `restore_drill` weekly, each named by the schedule that invokes it.

Every job answers with counts only, records `nettriage.ops.runs` {job, outcome} and one span
`ops.<job>`. A probe or check that fails is a "failure" outcome but not an error: the next run is
minutes away. A backup, cleanup or drill that fails raises, so Lambda retries it, and is logged,
traced and raised by its type only (spec §9.3), except the drill's own failures."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from opentelemetry.trace import Span, Status, StatusCode, Tracer
from sqlalchemy import Engine

from nettriage.adapters.backups import BackupStore
from nettriage.adapters.maintenance import clean_up
from nettriage.adapters.probes import database_answers, dynamodb_answers, health_answers
from nettriage.application.clock import Clock
from nettriage.entrypoints.ops.backup import Dumper, run_backup
from nettriage.entrypoints.ops.drill import DrillFailed, NoBackup, Server, run_restore_drill
from nettriage.platform.metrics import OpsMetrics

if TYPE_CHECKING:
    from types_boto3_dynamodb.client import DynamoDBClient

logger = logging.getLogger(__name__)

type Result = dict[str, str | int | bool]
# The drill's own failures, whose messages name only a day and tables, so the owner's
# `just restore-drill-<stage>` can tell them as they are.
TOLD = (DrillFailed, NoBackup)


class JobFailed(Exception):
    """Raised in place of any other error, with only its type: Lambda logs what the handler
    raises, and a database error can quote connection details or the values it was given."""


@dataclass
class Ops:
    http: httpx.Client
    app_url: str
    ops_database: Engine
    backup_database: Engine
    dynamodb: DynamoDBClient
    runtime_table: str
    store: BackupStore
    pg_dump: Dumper
    throwaway: Callable[[Path], Server]
    clock: Clock
    metrics: OpsMetrics
    tracer: Tracer
    workdir: Path = Path("/tmp")  # noqa: S108 - Lambda's own scratch space
    flush: Callable[[], None] = field(default=lambda: None)

    def run(self, job: str) -> Result:
        jobs: dict[str, Callable[[], tuple[Result, bool]]] = {
            "probe": self._probe,
            "check": self._check,
            "backup": self._backup,
            "cleanup": self._cleanup,
            "restore_drill": self._restore_drill,
        }
        if job not in jobs:
            raise ValueError(f"unknown job {job!r}")
        try:
            with self._span(f"ops.{job}") as span:
                try:
                    answer, ok = jobs[job]()
                except Exception as error:
                    self.metrics.runs.add(1, {"job": job, "outcome": "failure"})
                    logger.warning(
                        "ops_job_failed", extra={"job": job, "error_code": type(error).__name__}
                    )
                    raise
                outcome = "success" if ok else "failure"
                for key, value in answer.items():
                    span.set_attribute(f"ops.{key}", value)
                self.metrics.runs.add(1, {"job": job, "outcome": outcome})
                logger.info("ops_job_done", extra={"job": job, "outcome": outcome, **answer})
        except TOLD:
            raise
        except Exception as error:
            # Outside the span, which records the error's own type.
            raise JobFailed(type(error).__name__) from None
        return {"job": job, **answer}

    def _probe(self) -> tuple[Result, bool]:
        ok = health_answers(self.http, self.app_url)
        self.metrics.probe_success.set(int(ok), {"check": "health"})
        return {"ok": ok}, ok

    def _check(self) -> tuple[Result, bool]:
        database = database_answers(self.ops_database)
        dynamodb = dynamodb_answers(self.dynamodb, self.runtime_table)
        self.metrics.probe_success.set(int(database), {"check": "database"})
        self.metrics.probe_success.set(int(dynamodb), {"check": "dynamodb"})
        return {"database": database, "dynamodb": dynamodb}, database and dynamodb

    def _backup(self) -> tuple[Result, bool]:
        now = self.clock()
        manifest = run_backup(self.backup_database, self.pg_dump, self.store, now, self.workdir)
        return {
            "day": now.date().isoformat(),
            "tables": len(manifest.tables),
            "rows": sum(manifest.tables.values()),
        }, True

    def _cleanup(self) -> tuple[Result, bool]:
        counts = clean_up(self.ops_database)
        return {
            "uploads_expired": counts.uploads_expired,
            "analyses_failed": counts.analyses_failed,
            "invitations_deleted": counts.invitations_deleted,
            "audit_rows_deleted": counts.audit_rows_deleted,
        }, True

    def _restore_drill(self) -> tuple[Result, bool]:
        result = run_restore_drill(self.store, self.throwaway, self.workdir)
        return {"day": result.day, "tables": result.tables, "rows": result.rows}, True

    @contextmanager
    def _span(self, name: str) -> Iterator[Span]:
        """A failure is recorded by the error's type only: OpenTelemetry would otherwise copy
        its message, which could quote data, into the trace."""
        with self.tracer.start_as_current_span(
            name, record_exception=False, set_status_on_exception=False
        ) as span:
            try:
                yield span
            except Exception as error:
                span.set_status(Status(StatusCode.ERROR, type(error).__name__))
                raise
```

Create `backend/src/nettriage/entrypoints/ops/wiring.py`:
```python
"""Building the `ops` function in Lambda, once per cold start (Plan 7a §2, §4): `app_ops`'s
pooled URL and `app_backup`'s direct one come from SSM, the backups land in the stage's bucket,
the hourly check reads the runtime table, and the Postgres programs come from the layer."""

from __future__ import annotations

import boto3
import httpx
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from nettriage.adapters.backups import BackupStore
from nettriage.adapters.parameters import AWS_CONFIG, read_parameters
from nettriage.adapters.pg_client import PgClient, ThrowawayServer
from nettriage.adapters.postgres import create_database_engine
from nettriage.application.clock import system_clock
from nettriage.entrypoints.ops.jobs import Ops
from nettriage.platform.config import Settings
from nettriage.platform.metrics import OpsMetrics


def build_ops(
    settings: Settings,
    tracer_provider: TracerProvider,
    meter_provider: MeterProvider,
    session: boto3.session.Session | None = None,
) -> Ops:
    session = session or boto3.session.Session()
    names = [settings.database_url_parameter, settings.backup_database_url_parameter]
    values = read_parameters(session.client("ssm", config=AWS_CONFIG), names)
    pg = PgClient()

    def flush() -> None:
        # Lambda may freeze the environment right after the handler returns.
        tracer_provider.force_flush()
        meter_provider.force_flush()

    return Ops(
        http=httpx.Client(),
        app_url=settings.app_url,
        ops_database=create_database_engine(values[settings.database_url_parameter], pool_size=1),
        backup_database=create_database_engine(
            values[settings.backup_database_url_parameter], pool_size=1
        ),
        dynamodb=session.client("dynamodb", config=AWS_CONFIG),
        runtime_table=settings.runtime_table,
        store=BackupStore(session.client("s3", config=AWS_CONFIG), settings.backups_bucket),
        pg_dump=pg,
        throwaway=lambda home: ThrowawayServer(pg, home),
        clock=system_clock,
        metrics=OpsMetrics(meter_provider),
        tracer=tracer_provider.get_tracer("nettriage.ops"),
        flush=flush,
    )
```

In `backend/src/nettriage/platform/config.py`, replace:
```python
    # Where the analyze worker queues findings for an AI explanation (spec §4.2).
    triage_queue_url: str = ""

    @property
```
with:
```python
    # Where the analyze worker queues findings for an AI explanation (spec §4.2).
    triage_queue_url: str = ""
    # The ops function (Plan 7a): app_backup's direct database URL in SSM (its own
    # `database_url_parameter` holds app_ops's pooled one), the backups bucket, and the app's
    # CloudFront URL, which the probe fetches /api/health through.
    backup_database_url_parameter: str = ""
    backups_bucket: str = ""
    app_url: str = ""

    @property
```

In `backend/src/nettriage/platform/metrics.py`, replace:
```python
            "nettriage.ai.cache.hits", description="Explanations served from a stored analysis"
        )
        self.queue_message_age = meter.create_histogram(
            "nettriage.queue.message.age",
            unit="s",
            description="How long a message waited in its queue, by queue",
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


class OpsMetrics:
    """The `ops` function's metrics (Plan 7a §6): whether each probe and check answered, and every
    job's runs by outcome, so 7b's alerts can see a backup or a drill that failed or never ran."""

    def __init__(self, meter_provider: MeterProvider | None = None) -> None:
        meter = (meter_provider or get_meter_provider()).get_meter("nettriage")
        self.probe_success = meter.create_gauge(
            "nettriage.probe.success",
            description="1 when a probe or check answered, 0 when it didn't, by check",
        )
        self.runs = meter.create_counter(
            "nettriage.ops.runs", description="Ops jobs run, by job and outcome"
        )
```

In `tools/build_lambda.py`, replace:
```python
    "nettriage/entrypoints/analyze/handler.py",
    "nettriage/entrypoints/triage/handler.py",
    "nettriage/prompts/triage/v1.md",
)
```
with:
```python
    "nettriage/entrypoints/analyze/handler.py",
    "nettriage/entrypoints/triage/handler.py",
    "nettriage/entrypoints/ops/handler.py",
    "nettriage/prompts/triage/v1.md",
)
```

- [ ] **Step 4: Run the tests again**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/worker/test_ops_jobs.py tests/unit/ops/test_ops_wiring.py ../tools/tests/test_build_lambda.py`
Expected: `32 passed`.

- [ ] **Step 5: Run the whole backend suite and its checks**

Run: `just lint && cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest --cov=nettriage.domain --cov=nettriage.application --cov-fail-under=85`
Expected: Ruff passes and finds every file formatted, mypy reports `Success: no issues found`, and `1154 passed, 1 skipped` (the rate limiter's concurrency test, which needs DynamoDB Local), with coverage at 85% or more.

- [ ] **Step 6: Commit**

```bash
git add backend/src/nettriage/entrypoints/ops backend/src/nettriage/platform/metrics.py backend/src/nettriage/platform/config.py tools/build_lambda.py backend/tests/worker/test_ops_jobs.py backend/tests/unit/ops tools/tests/test_build_lambda.py
git commit -m "feat(ops): the ops function's entry point: the scheduled job by name, its counts as the answer, nettriage.ops.runs and nettriage.probe.success, and failures reported by type only" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 8: The jobs end to end, inside AWS's Lambda image in CI

**Files:**
- Create: `tools/ops_e2e.py`
- Modify: `.github/workflows/ci.yml` (the `ops-in-lambda` job)
- Test: `tools/tests/test_ops_e2e.py`

**Interfaces:**
- Consumes: the `backend-zip` artifact (the `package` job), `pg-client-zip` (Task 1), the handler (Task 7), and migration `0011` (Task 2).
- Produces:
  - `python tools/ops_e2e.py prepare --database-url <url>`: seeds two organizations, each with an upload waiting 3 hours and an audit row 200 days old; gives `app_ops` and `app_backup` the password `ci-only`; creates the bucket `nettriage-ci-backups`, the SSM parameters `/nettriage/ci/db/app-ops-url` and `/nettriage/ci/db/app-backup-url`, and the table `nettriage-ci-runtime` in moto's server; and writes moto's throwaway key pair to `aws/credentials`;
  - `python tools/ops_e2e.py verify <results.jsonl>`: fails unless the check reached both stores, the cleanup changed at least the seeded rows, and the drill restored the backup's day, tables and rows;
  - the CI job `ops-in-lambda` (`needs: [package, pg-client]`), which runs `check`, `cleanup`, `backup` and `restore_drill` through `handle` in AWS's Lambda image.

- [ ] **Step 1: Write the failing tests**

Create `tools/tests/test_ops_e2e.py`:
```python
import json
from pathlib import Path

from tools.ops_e2e import role_url, verify, write_stand_in_credentials

GOOD = [
    {"job": "check", "database": True, "dynamodb": True},
    {"job": "cleanup", "uploads_expired": 2, "analyses_failed": 0, "invitations_deleted": 0,
     "audit_rows_deleted": 2},
    {"job": "backup", "day": "2026-10-07", "tables": 15, "rows": 40},
    {"job": "restore_drill", "day": "2026-10-07", "tables": 15, "rows": 40},
]


def results(tmp_path: Path, answers: list[dict[str, object]]) -> Path:
    path = tmp_path / "results.jsonl"
    path.write_text("\n".join(json.dumps(answer) for answer in answers) + "\n")
    return path


def test_jobs_that_did_their_work_pass(tmp_path: Path) -> None:
    assert verify(results(tmp_path, GOOD)) == []


def test_a_drill_that_restored_other_counts_fails(tmp_path: Path) -> None:
    answers = [*GOOD[:3], {"job": "restore_drill", "day": "2026-10-07", "tables": 15, "rows": 39}]

    [problem] = verify(results(tmp_path, answers))

    assert problem.startswith("the drill restored")


def test_a_cleanup_that_missed_the_seeded_rows_fails(tmp_path: Path) -> None:
    answers = [GOOD[0], {**GOOD[1], "uploads_expired": 0}, *GOOD[2:]]

    [problem] = verify(results(tmp_path, answers))

    assert problem.startswith("the cleanup missed")


def test_a_missing_job_fails(tmp_path: Path) -> None:
    assert verify(results(tmp_path, GOOD[1:])) != []


def test_each_role_gets_its_own_url_on_the_same_database() -> None:
    url = role_url("postgresql://postgres:secret@localhost:5432/postgres", "app_ops")

    assert url == "postgresql://app_ops:ci-only@localhost:5432/postgres"


def test_the_stand_in_aws_gets_a_throwaway_key_pair_in_a_file(tmp_path: Path) -> None:
    path = tmp_path / "aws" / "credentials"

    write_stand_in_credentials(path)

    assert path.read_text().splitlines() == [
        "[default]",
        "aws_access_key_id = moto",
        "aws_secret_access_key = moto",
    ]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --project backend python -m pytest tools/tests/test_ops_e2e.py`
Expected: FAIL: `1 error` during collection, `ModuleNotFoundError: No module named 'tools.ops_e2e'`.

- [ ] **Step 3: Write the seeding, the verdict and the CI job**

In `.github/workflows/ci.yml`, replace:
```yaml
          retention-days: 7

  frontend:
    runs-on: ubuntu-24.04
```
with:
```yaml
          retention-days: 7

  # The ops function end to end (Plan 7a §8): the CI-built Lambda package and Postgres layer,
  # inside AWS's Lambda image as a non-root user without /dev/shm, against a migrated Postgres 17,
  # with AWS stood in for by moto's server. The drill must restore what the backup counted.
  ops-in-lambda:
    needs: [package, pg-client]
    runs-on: ubuntu-24.04-arm
    services:
      postgres:
        image: postgres:17
        env:
          POSTGRES_PASSWORD: ci-only
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready --health-interval 5s --health-timeout 5s --health-retries 10
    env:
      DATABASE_URL: postgresql://postgres:ci-only@localhost:5432/postgres
      # moto's server stands in for AWS. Its throwaway key pair is written to this file by
      # tools/ops_e2e.py, so no AWS credential is ever named here (ADR 0013).
      AWS_ENDPOINT_URL: http://localhost:5000
      AWS_SHARED_CREDENTIALS_FILE: ${{ github.workspace }}/aws/credentials
      AWS_DEFAULT_REGION: eu-north-1
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
      - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          name: backend-zip
          path: dist
      - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          name: pg-client-zip
          path: dist
      - name: Migrate the database, as a deploy would
        env:
          NETTRIAGE_MIGRATION_DATABASE_URL: ${{ env.DATABASE_URL }}
        run: uv run --locked --project backend python -m alembic -c backend/alembic.ini upgrade head
      - name: Stand in for AWS with moto
        run: |
          uvx --from "moto[server]==5.2.3" moto_server -p 5000 > moto.log 2>&1 &
          for attempt in $(seq 30); do curl -s localhost:5000 > /dev/null && break; sleep 1; done
      - name: Seed the database and the stand-in AWS
        run: uv run --locked --project backend python tools/ops_e2e.py prepare --database-url "$DATABASE_URL"
      - name: Run the ops jobs inside AWS's Lambda image
        run: |
          mkdir -p task opt out && chmod 777 out
          unzip -q dist/backend.zip -d task && unzip -q dist/pg-client.zip -d opt
          for job in check cleanup backup restore_drill; do
            docker run --rm --network host --ipc=none --user nobody \
              -v "$PWD/task:/var/task:ro" -v "$PWD/opt:/opt:ro" -v "$PWD/out:/out" \
              -v "$PWD/aws:/aws:ro" -e AWS_SHARED_CREDENTIALS_FILE=/aws/credentials \
              -e AWS_ENDPOINT_URL -e AWS_DEFAULT_REGION \
              -e PYTHONPATH=/var/task -e OTEL_EXPORTER_OTLP_TIMEOUT=1 \
              -e NETTRIAGE_STAGE=local -e NETTRIAGE_SERVICE_NAME=nettriage-ops \
              -e NETTRIAGE_DATABASE_URL_PARAMETER=/nettriage/ci/db/app-ops-url \
              -e NETTRIAGE_BACKUP_DATABASE_URL_PARAMETER=/nettriage/ci/db/app-backup-url \
              -e NETTRIAGE_BACKUPS_BUCKET=nettriage-ci-backups \
              -e NETTRIAGE_RUNTIME_TABLE=nettriage-ci-runtime \
              --entrypoint python public.ecr.aws/lambda/python:3.14 -c "
          import json, sys
          from nettriage.entrypoints.ops.handler import handle
          answer = handle({'job': sys.argv[1]}, None)
          open(f'/out/{sys.argv[1]}.json', 'w').write(json.dumps(answer))
          " "$job"
            cat "out/$job.json" >> results.jsonl && echo >> results.jsonl
          done
          cat results.jsonl
      - name: Check what the jobs did
        run: uv run --locked --project backend python tools/ops_e2e.py verify results.jsonl

  frontend:
    runs-on: ubuntu-24.04
```

Create `tools/ops_e2e.py`:
```python
"""The `ops` function end to end in CI (Plan 7a §8): its real code and the Postgres layer, inside
AWS's Lambda image, against a real Postgres 17, with AWS stood in for by moto's server.

    python tools/ops_e2e.py prepare --database-url postgresql://postgres:…@localhost:5432/postgres
    (run the jobs in the Lambda image; see the ci.yml job `ops-in-lambda`)
    python tools/ops_e2e.py verify results.jsonl

`prepare` seeds two organizations (with an upload abandoned 3 hours ago and an audit row 200
days old for the cleanup), gives `app_ops` and `app_backup` logins, and creates the bucket, the
connection strings in SSM and the runtime table in moto (AWS_ENDPOINT_URL). `verify` checks
the jobs' answers: the drill restored exactly what the backup counted.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import uuid7

import boto3
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

BUCKET = "nettriage-ci-backups"
RUNTIME_TABLE = "nettriage-ci-runtime"
OPS_URL_PARAMETER = "/nettriage/ci/db/app-ops-url"
BACKUP_URL_PARAMETER = "/nettriage/ci/db/app-backup-url"
ROLE_PASSWORD = "ci-only"  # noqa: S105 - a throwaway database in CI


def seed(database_url: str) -> None:
    engine = create_engine(make_url(database_url).set(drivername="postgresql+psycopg"))
    with engine.begin() as connection:
        for name in ("Acme", "Globex"):
            user, org = uuid7(), uuid7()
            connection.execute(
                text("INSERT INTO users (id, cognito_sub, email) VALUES (:id, :sub, :email)"),
                {"id": user, "sub": f"sub-{user}", "email": f"{name.lower()}@example.com"},
            )
            connection.execute(
                text(
                    "INSERT INTO organizations (id, name, slug, created_by) "
                    "VALUES (:id, :name, :slug, :by)"
                ),
                {"id": org, "name": name, "slug": f"{name.lower()}-{org.hex[:8]}", "by": user},
            )
            connection.execute(
                text("INSERT INTO memberships (org_id, user_id, role) VALUES (:org, :user, 'owner')"),
                {"org": org, "user": user},
            )
            upload = uuid7()
            connection.execute(
                text(
                    "INSERT INTO uploads (id, org_id, uploaded_by, original_filename, s3_key, "
                    "size_bytes, sha256, status, created_at) VALUES (:id, :org, :by, 'flows.log', "
                    ":key, 1024, :sha, 'pending_upload', now() - interval '3 hours')"
                ),
                {
                    "id": upload,
                    "org": org,
                    "by": user,
                    "key": f"orgs/{org}/uploads/{upload}/raw",
                    "sha": "0" * 64,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO audit_log (id, org_id, actor_type, action, outcome, created_at) "
                    "VALUES (:id, :org, 'system', 'ci.event', 'success', "
                    "now() - interval '200 days')"
                ),
                {"id": uuid7(), "org": org},
            )
        for role in ("app_ops", "app_backup"):
            connection.execute(text(f"ALTER ROLE {role} WITH LOGIN PASSWORD '{ROLE_PASSWORD}'"))
    engine.dispose()


def role_url(database_url: str, role: str) -> str:
    return (
        make_url(database_url)
        .set(username=role, password=ROLE_PASSWORD)
        .render_as_string(hide_password=False)
    )


def write_stand_in_credentials(path: Path) -> None:
    """moto needs a key pair, and any will do. It goes in a file that AWS_SHARED_CREDENTIALS_FILE
    names, so the workflow never names an AWS credential and its guard stays strict (ADR 0013)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[default]\naws_access_key_id = moto\naws_secret_access_key = moto\n", encoding="utf-8"
    )


def create_aws(database_url: str) -> None:
    session = boto3.session.Session(region_name="eu-north-1")
    session.client("s3").create_bucket(
        Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-north-1"}
    )
    ssm = session.client("ssm")
    for name, role in ((OPS_URL_PARAMETER, "app_ops"), (BACKUP_URL_PARAMETER, "app_backup")):
        ssm.put_parameter(Name=name, Value=role_url(database_url, role), Type="SecureString")
    session.client("dynamodb").create_table(
        TableName=RUNTIME_TABLE,
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


def verify(results: Path) -> list[str]:
    answers = {
        answer["job"]: answer
        for answer in (json.loads(line) for line in results.read_text().splitlines() if line)
    }
    problems = []
    if answers.get("check") != {"job": "check", "database": True, "dynamodb": True}:
        problems.append(f"the hourly check didn't reach everything: {answers.get('check')}")
    cleanup = answers.get("cleanup", {})
    if cleanup.get("uploads_expired", 0) < 2 or cleanup.get("audit_rows_deleted", 0) < 2:
        problems.append(f"the cleanup missed the seeded rows: {cleanup}")
    backup, drill = answers.get("backup", {}), answers.get("restore_drill", {})
    if not backup.get("tables") or not backup.get("rows"):
        problems.append(f"the backup counted nothing: {backup}")
    if {key: drill.get(key) for key in ("day", "tables", "rows")} != {
        key: backup.get(key) for key in ("day", "tables", "rows")
    }:
        problems.append(f"the drill restored {drill}, but the backup counted {backup}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--database-url", required=True)
    check = commands.add_parser("verify")
    check.add_argument("results", type=Path)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        write_stand_in_credentials(Path(os.environ["AWS_SHARED_CREDENTIALS_FILE"]))
        seed(args.database_url)
        create_aws(args.database_url)
        print("seeded two organizations and created the bucket, parameters and table")
        return 0
    problems = verify(args.results)
    for problem in problems:
        print(f"ops_e2e: {problem}", file=sys.stderr)
    if not problems:
        print("the ops jobs ran in the Lambda image, and the drill restored what the backup counted")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests again**

Run: `uv run --project backend python -m pytest tools/tests/test_ops_e2e.py`
Expected: `6 passed`.

- [ ] **Step 5: Check the workflow**

Run: `uv run --project backend python tools/check_pinned_actions.py .github/workflows && uv run --project backend python tools/check_no_cloud_access.py .`
Expected: `All actions in .github\workflows are pinned.` and `CI holds no cloud access.` The job itself runs in the PR's CI (Task 13). There, its last step prints the four answers, such as `{"job": "restore_drill", "day": "2026-10-06", "tables": 15, "rows": 9}`, with the drill's day, tables and rows equal to the backup's.

- [ ] **Step 6: Commit**

```bash
git add tools/ops_e2e.py tools/tests/test_ops_e2e.py .github/workflows/ci.yml
git commit -m "test(ops): the ops jobs end to end in CI, inside AWS's Lambda image with the Postgres layer, against Postgres 17 and moto: the drill restores what the backup counted" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 9: Terraform: the function, its layer, five schedules and the backups bucket

**Files:**
- Create: `infra/modules/ops/versions.tf`, `variables.tf`, `main.tf`, `outputs.tf`, and `.terraform.lock.hcl` (written by `terraform init`)
- Modify: `infra/envs/dev/main.tf`, `infra/envs/dev/variables.tf`, `infra/envs/dev/outputs.tf`, `.checkov.yaml`, `justfile` (`tf-check`), `.github/workflows/ci.yml` (the Terraform loop)
- Test: `infra/modules/ops/tests/ops.tftest.hcl`

**Interfaces:**
- Consumes: the package (`var.lambda_zip_path`) and the layer's zip (`var.pg_client_zip_path`, Task 10 passes it); the handler `nettriage.entrypoints.ops.handler.handle` and the settings' variables (Task 7); the runtime table's name and ARN (`module.data`); CloudFront's domain (`module.edge`).
- Produces:
  - the module `ops`, with the outputs `function_name` and `backups_bucket`;
  - in dev: `module "ops"`, the parameter names `/nettriage/dev/db/app-ops-url` and `/nettriage/dev/db/app-backup-url`, the variable `pg_client_zip_path`, and the outputs `ops_function` and `backups_bucket`;
  - the function `nettriage-<stage>-ops`, which Task 10's `just restore-drill-<stage>` invokes by that name.

- [ ] **Step 1: Write the failing tests**

Create `infra/modules/ops/tests/ops.tftest.hcl`:
```hcl
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
  mock_data "aws_region" {
    defaults = {
      region = "eu-north-1"
    }
  }
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/nettriage-dev-ops"
    }
  }
  mock_resource "aws_s3_bucket" {
    defaults = {
      arn = "arn:aws:s3:::nettriage-dev-backups-12345678"
    }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:eu-north-1:123456789012:log-group:/aws/lambda/nettriage-dev-ops"
    }
  }
  mock_resource "aws_lambda_function" {
    defaults = {
      arn = "arn:aws:lambda:eu-north-1:123456789012:function:nettriage-dev-ops"
    }
  }
  mock_resource "aws_lambda_layer_version" {
    defaults = {
      arn = "arn:aws:lambda:eu-north-1:123456789012:layer:nettriage-dev-pg-client:1"
    }
  }
}

variables {
  stage                         = "dev"
  lambda_zip_path               = "../app/tests/fixtures/app.zip"
  pg_client_zip_path            = "../app/tests/fixtures/app.zip"
  app_version                   = "test-sha"
  otel_collector_layer_arn      = "arn:aws:lambda:eu-north-1:184161586896:layer:opentelemetry-collector-arm64-0_22_0:1"
  grafana_otlp_endpoint         = "https://otlp-gateway.example.grafana.net/otlp"
  grafana_otlp_auth             = "dGVzdDp0ZXN0"
  database_url_parameter        = "/nettriage/dev/db/app-ops-url"
  backup_database_url_parameter = "/nettriage/dev/db/app-backup-url"
  runtime_table_name            = "nettriage-dev-runtime"
  runtime_table_arn             = "arn:aws:dynamodb:eu-north-1:123456789012:table/nettriage-dev-runtime"
  app_url                       = "https://d111111abcdef8.cloudfront.net"
}

run "the_function_has_room_for_a_dump_and_its_restore" {
  command = apply

  assert {
    condition     = aws_lambda_function.ops.handler == "nettriage.entrypoints.ops.handler.handle"
    error_message = "The ops function runs the ops handler from the shared package."
  }
  assert {
    condition = (
      aws_lambda_function.ops.memory_size == 1024 &&
      aws_lambda_function.ops.timeout == 600 &&
      one(aws_lambda_function.ops.ephemeral_storage).size == 2048
    )
    error_message = "1024 MB, 600 s and 2 GB of /tmp hold a dump and its restore (Plan 7a §4)."
  }
  assert {
    condition     = aws_lambda_function.ops.architectures == tolist(["arm64"])
    error_message = "The layer's Postgres is built for arm64."
  }
  assert {
    condition = (
      contains(aws_lambda_function.ops.layers, aws_lambda_layer_version.pg_client.arn) &&
      contains(aws_lambda_function.ops.layers, var.otel_collector_layer_arn)
    )
    error_message = "The function has the Postgres layer and the telemetry collector."
  }
  assert {
    condition     = aws_lambda_layer_version.pg_client.compatible_architectures == toset(["arm64"])
    error_message = "The Postgres layer is for arm64 only."
  }
}

run "the_function_knows_its_parameters_bucket_and_the_apps_url" {
  command = apply

  assert {
    condition = (
      aws_lambda_function.ops.environment[0].variables.NETTRIAGE_SERVICE_NAME == "nettriage-ops" &&
      aws_lambda_function.ops.environment[0].variables.NETTRIAGE_DATABASE_URL_PARAMETER == "/nettriage/dev/db/app-ops-url" &&
      aws_lambda_function.ops.environment[0].variables.NETTRIAGE_BACKUP_DATABASE_URL_PARAMETER == "/nettriage/dev/db/app-backup-url" &&
      aws_lambda_function.ops.environment[0].variables.NETTRIAGE_BACKUPS_BUCKET == aws_s3_bucket.backups.bucket &&
      aws_lambda_function.ops.environment[0].variables.NETTRIAGE_RUNTIME_TABLE == "nettriage-dev-runtime" &&
      aws_lambda_function.ops.environment[0].variables.NETTRIAGE_APP_URL == "https://d111111abcdef8.cloudfront.net"
    )
    error_message = "The function is told its two connection strings, its bucket, the runtime table and the app's URL."
  }
}

run "five_schedules_name_their_jobs" {
  command = apply

  assert {
    condition = {
      for job, schedule in aws_scheduler_schedule.ops : job => schedule.schedule_expression
      } == {
      probe         = "rate(5 minutes)"
      check         = "cron(17 * * * ? *)"
      backup        = "cron(0 2 * * ? *)"
      cleanup       = "cron(0 3 * * ? *)"
      restore_drill = "cron(0 4 ? * SUN *)"
    }
    error_message = "Probe every 5 minutes, check hourly, back up nightly, clean up daily, drill weekly (Plan 7a §2)."
  }
  assert {
    condition = alltrue([
      for job, schedule in aws_scheduler_schedule.ops :
      jsondecode(one(schedule.target).input).job == job && schedule.schedule_expression_timezone == "UTC"
    ])
    error_message = "Each schedule invokes the ops function with its own job's name, in UTC."
  }
  assert {
    condition = (
      one(one(aws_scheduler_schedule.ops["probe"].target).retry_policy).maximum_retry_attempts == 0 &&
      one(one(aws_scheduler_schedule.ops["check"].target).retry_policy).maximum_retry_attempts == 0
    )
    error_message = "A probe or check isn't retried: the next one comes in minutes."
  }
}

run "the_scheduler_may_only_invoke_the_ops_function" {
  command = apply

  assert {
    condition = (
      jsondecode(aws_iam_role_policy.scheduler_invoke.policy).Statement[0].Action == "lambda:InvokeFunction" &&
      jsondecode(aws_iam_role_policy.scheduler_invoke.policy).Statement[0].Resource == aws_lambda_function.ops.arn
    )
    error_message = "The schedules' role may invoke the ops function and nothing else."
  }
  assert {
    condition     = jsondecode(aws_iam_role.scheduler.assume_role_policy).Statement[0].Condition.StringEquals["aws:SourceAccount"] == "123456789012"
    error_message = "Only this account's schedules may assume the role."
  }
}

run "the_backups_bucket_is_private_encrypted_tls_only_and_keeps_7_days" {
  command = apply

  assert {
    condition     = aws_s3_bucket.backups.bucket == "nettriage-dev-backups-${substr(sha256("123456789012"), 0, 8)}"
    error_message = "The bucket is nettriage-<stage>-backups-<8 hex of the account ID's hash> (Plan 7a §4)."
  }
  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.backups.block_public_acls,
      aws_s3_bucket_public_access_block.backups.block_public_policy,
      aws_s3_bucket_public_access_block.backups.ignore_public_acls,
      aws_s3_bucket_public_access_block.backups.restrict_public_buckets,
    ])
    error_message = "Public access is blocked."
  }
  assert {
    condition     = one(one(aws_s3_bucket_server_side_encryption_configuration.backups.rule).apply_server_side_encryption_by_default).sse_algorithm == "AES256"
    error_message = "Backups are encrypted with SSE-S3."
  }
  assert {
    condition     = jsondecode(aws_s3_bucket_policy.backups.policy).Statement[0].Effect == "Deny" && jsondecode(aws_s3_bucket_policy.backups.policy).Statement[0].Condition.Bool["aws:SecureTransport"] == "false"
    error_message = "Requests without TLS are denied."
  }
  assert {
    condition     = one(one(aws_s3_bucket_lifecycle_configuration.backups.rule).expiration).days == 7
    error_message = "Backups are kept for 7 days (spec §5.7)."
  }
}

run "the_function_may_only_do_its_jobs" {
  command = apply

  assert {
    condition = toset(jsondecode(aws_iam_role_policy.ops_parameters.policy).Statement[0].Resource) == toset([
      "arn:aws:ssm:eu-north-1:123456789012:parameter/nettriage/dev/db/app-ops-url",
      "arn:aws:ssm:eu-north-1:123456789012:parameter/nettriage/dev/db/app-backup-url",
    ])
    error_message = "The function reads its own two connection strings and no other parameter."
  }
  assert {
    condition = (
      jsondecode(aws_iam_role_policy.ops_runtime_table.policy).Statement[0].Action == "dynamodb:GetItem" &&
      jsondecode(aws_iam_role_policy.ops_runtime_table.policy).Statement[0].Condition["ForAllValues:StringEquals"]["dynamodb:LeadingKeys"] == ["ops#probe"]
    )
    error_message = "The hourly check may read its own probe key in the runtime table, and nothing else."
  }
  assert {
    condition     = toset(jsondecode(aws_iam_role_policy.ops_backups.policy).Statement[0].Action) == toset(["s3:PutObject", "s3:GetObject"]) && jsondecode(aws_iam_role_policy.ops_backups.policy).Statement[0].Resource == "${aws_s3_bucket.backups.arn}/pg/*"
    error_message = "The function may put and get backups under pg/ only."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.ops_backups.policy).Statement[1].Action == "s3:ListBucket" && jsondecode(aws_iam_role_policy.ops_backups.policy).Statement[1].Condition.StringLike["s3:prefix"] == ["pg/*"]
    error_message = "The function may list the backups under pg/ to find the newest."
  }
}
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd infra/modules/ops && export TF_DATA_DIR=.terraform-check && terraform init -backend=false -input=false > /dev/null; terraform test -no-color`
Expected: FAIL: `Error: unknown provider registry.terraform.io/hashicorp/aws`, then `Failure! 0 passed, 0 failed.` The module has no configuration yet.

- [ ] **Step 3: Write the module and wire it into dev**

In `.checkov.yaml`, replace:
```yaml
  - CKV_AWS_144    # S3 cross-region replication: cost; state is versioned (spec §5.6)
  - CKV_AWS_145    # S3 KMS encryption: SSE-S3 by design; KMS customer keys cost money (spec §6.8)
  - CKV2_AWS_62    # S3 event notifications: not needed for the state or web buckets; the uploads bucket notifies the analyze queue
  - CKV2_AWS_61    # S3 lifecycle on the web bucket: deploys replace its content
  - CKV_AWS_21     # S3 versioning: the web bucket is rebuilt from Git on every deploy; raw uploads are never changed and are deleted after 30 days (spec §5.7)
  - CKV_AWS_300    # S3 abort-multipart rule on the web bucket: the owner's deploy uploads small files only
  - CKV_AWS_115    # Lambda reserved concurrency: new accounts may have too low a quota (spec §13.2)
  - CKV_AWS_116    # Lambda DLQ: the API is synchronous, and the analyze and triage workers are fed by SQS, whose redrive policies are their DLQs (spec §8.6)
  - CKV_AWS_117    # Lambda in a VPC: no VPC by design (ADR 0009)
  - CKV_AWS_173    # Lambda env var CMK encryption: AWS-managed encryption; CMKs cost money
```
with:
```yaml
  - CKV_AWS_144    # S3 cross-region replication: cost; state is versioned (spec §5.6)
  - CKV_AWS_145    # S3 KMS encryption: SSE-S3 by design; KMS customer keys cost money (spec §6.8)
  - CKV2_AWS_62    # S3 event notifications: not needed for the state, web or backups buckets; the uploads bucket notifies the analyze queue
  - CKV2_AWS_61    # S3 lifecycle on the web bucket: deploys replace its content
  - CKV_AWS_21     # S3 versioning: the web bucket is rebuilt from Git on every deploy; raw uploads are never changed and are deleted after 30 days, and backups are replaced nightly and kept 7 days (spec §5.7)
  - CKV_AWS_300    # S3 abort-multipart rule on the web bucket: the owner's deploy uploads small files only
  - CKV_AWS_115    # Lambda reserved concurrency: new accounts may have too low a quota (spec §13.2)
  - CKV_AWS_116    # Lambda DLQ: the API is synchronous, the analyze and triage workers are fed by SQS, whose redrive policies are their DLQs (spec §8.6), and the ops jobs record their failures in nettriage.ops.runs (Plan 7a)
  - CKV_AWS_117    # Lambda in a VPC: no VPC by design (ADR 0009)
  - CKV_AWS_173    # Lambda env var CMK encryption: AWS-managed encryption; CMKs cost money
```

In `.github/workflows/ci.yml`, replace:
```yaml
        run: |
          terraform fmt -check -recursive infra
          for dir in infra/bootstrap infra/modules/app infra/modules/data infra/modules/edge infra/modules/identity infra/modules/pipeline; do
            (cd "$dir" && terraform init -backend=false -input=false && terraform validate && terraform test)
          done
```
with:
```yaml
        run: |
          terraform fmt -check -recursive infra
          for dir in infra/bootstrap infra/modules/app infra/modules/data infra/modules/edge infra/modules/identity infra/modules/ops infra/modules/pipeline; do
            (cd "$dir" && terraform init -backend=false -input=false && terraform validate && terraform test)
          done
```

In `infra/envs/dev/main.tf`, replace:
```hcl
  analyze_database_url_parameter = "/nettriage/dev/db/app-analyze-url"
  triage_database_url_parameter  = "/nettriage/dev/db/app-triage-url"
}

```
with:
```hcl
  analyze_database_url_parameter = "/nettriage/dev/db/app-analyze-url"
  triage_database_url_parameter  = "/nettriage/dev/db/app-triage-url"
  ops_database_url_parameter     = "/nettriage/dev/db/app-ops-url"
  backup_database_url_parameter  = "/nettriage/dev/db/app-backup-url"
}

```

In `infra/envs/dev/main.tf`, replace:
```hcl
}

module "identity" {
  source                = "../../modules/identity"
```
with:
```hcl
}

# Keeping it running (Plan 7a): the probe, the hourly check, the nightly backup, the daily
# cleanup and the weekly restore drill.
module "ops" {
  source                        = "../../modules/ops"
  stage                         = "dev"
  lambda_zip_path               = var.lambda_zip_path
  pg_client_zip_path            = var.pg_client_zip_path
  app_version                   = var.app_version
  otel_collector_layer_arn      = var.otel_collector_layer_arn
  grafana_otlp_endpoint         = var.grafana_otlp_endpoint
  grafana_otlp_auth             = var.grafana_otlp_auth
  database_url_parameter        = local.ops_database_url_parameter
  backup_database_url_parameter = local.backup_database_url_parameter
  runtime_table_name            = module.data.table_name
  runtime_table_arn             = module.data.table_arn
  app_url                       = "https://${module.edge.distribution_domain}"
}

module "identity" {
  source                = "../../modules/identity"
```

In `infra/envs/dev/outputs.tf`, replace:
```hcl
  value = module.identity.sign_in_domain
}
```
with:
```hcl
  value = module.identity.sign_in_domain
}

output "ops_function" {
  value = module.ops.function_name
}

output "backups_bucket" {
  value = module.ops.backups_bucket
}
```

In `infra/envs/dev/variables.tf`, replace:
```hcl
variable "bedrock_model_id" {
  type = string
}
```
with:
```hcl
variable "bedrock_model_id" {
  type = string
}

variable "pg_client_zip_path" {
  type        = string
  description = "Path to the CI-built dist/pg-client.zip: Postgres 17 for the ops function's layer."
}
```

Create `infra/modules/ops/main.tf`:
```hcl
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  name = "nettriage-${var.stage}"
  ops  = "${local.name}-ops"
  # Global names; a hash of the account ID keeps ours unique without exposing it, as for uploads.
  bucket = "${local.name}-backups-${substr(sha256(data.aws_caller_identity.current.account_id), 0, 8)}"
  parameter_arns = [
    for name in [var.database_url_parameter, var.backup_database_url_parameter] :
    "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter${name}"
  ]
  # The jobs (Plan 7a §2), in UTC. A probe or check isn't retried: the next one comes in minutes.
  schedules = {
    probe         = { expression = "rate(5 minutes)", retries = 0 }
    check         = { expression = "cron(17 * * * ? *)", retries = 0 }
    backup        = { expression = "cron(0 2 * * ? *)", retries = 2 }
    cleanup       = { expression = "cron(0 3 * * ? *)", retries = 2 }
    restore_drill = { expression = "cron(0 4 ? * SUN *)", retries = 2 }
  }
}

# Nightly database dumps and their manifests (Plan 7a §2.3): private, TLS-only, SSE-S3, kept for
# 7 days (spec §5.7). Only the ops function writes them; the owner's session can read them.
resource "aws_s3_bucket" "backups" {
  bucket = local.bucket
}

resource "aws_s3_bucket_ownership_controls" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket                  = aws_s3_bucket.backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_policy" "backups" {
  bucket = aws_s3_bucket.backups.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "TlsOnly"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.backups.arn, "${aws_s3_bucket.backups.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.backups]
}

resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    id     = "keep-7-days"
    status = "Enabled"

    filter {}

    expiration {
      days = 7
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# Postgres 17 for the backup and the drill, built from source for Lambda (tools/pg_client).
resource "aws_lambda_layer_version" "pg_client" {
  layer_name               = "${local.name}-pg-client"
  filename                 = var.pg_client_zip_path
  source_code_hash         = filebase64sha256(var.pg_client_zip_path)
  compatible_runtimes      = [var.runtime]
  compatible_architectures = ["arm64"]
}

resource "aws_cloudwatch_log_group" "ops" {
  name              = "/aws/lambda/${local.ops}"
  retention_in_days = 7
}

resource "aws_iam_role" "ops" {
  name = local.ops
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "ops_logs" {
  name = "write-own-logs"
  role = aws_iam_role.ops.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.ops.arn}:*"
    }]
  })
}

# app_ops's and app_backup's connection strings, which the deploy writes.
resource "aws_iam_role_policy" "ops_parameters" {
  name = "read-own-parameters"
  role = aws_iam_role.ops.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "ssm:GetParameters"
      Resource = local.parameter_arns
    }]
  })
}

# The hourly check reads one key of its own; nothing else in the runtime table.
resource "aws_iam_role_policy" "ops_runtime_table" {
  name = "read-probe-key"
  role = aws_iam_role.ops.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "dynamodb:GetItem"
      Resource  = var.runtime_table_arn
      Condition = { "ForAllValues:StringEquals" = { "dynamodb:LeadingKeys" = ["ops#probe"] } }
    }]
  })
}

resource "aws_iam_role_policy" "ops_backups" {
  name = "keep-backups"
  role = aws_iam_role.ops.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = "${aws_s3_bucket.backups.arn}/pg/*"
      },
      {
        Effect    = "Allow"
        Action    = "s3:ListBucket"
        Resource  = aws_s3_bucket.backups.arn
        Condition = { StringLike = { "s3:prefix" = ["pg/*"] } }
      },
    ]
  })
}

# The same package as the other functions, with the Postgres layer (Plan 7a §4).
resource "aws_lambda_function" "ops" {
  function_name    = local.ops
  role             = aws_iam_role.ops.arn
  runtime          = var.runtime
  architectures    = ["arm64"]
  handler          = "nettriage.entrypoints.ops.handler.handle"
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  memory_size      = 1024
  timeout          = 600
  layers           = [var.otel_collector_layer_arn, aws_lambda_layer_version.pg_client.arn]

  ephemeral_storage {
    size = 2048
  }

  environment {
    variables = {
      OPENTELEMETRY_COLLECTOR_CONFIG_URI      = "/var/task/collector.yaml"
      OTEL_EXPORTER_OTLP_ENDPOINT             = "http://localhost:4318"
      OTEL_EXPORTER_OTLP_PROTOCOL             = "http/protobuf"
      GRAFANA_OTLP_ENDPOINT                   = var.grafana_otlp_endpoint
      GRAFANA_OTLP_AUTH                       = var.grafana_otlp_auth
      NETTRIAGE_STAGE                         = var.stage
      NETTRIAGE_VERSION                       = var.app_version
      NETTRIAGE_SERVICE_NAME                  = "nettriage-ops"
      NETTRIAGE_DATABASE_URL_PARAMETER        = var.database_url_parameter
      NETTRIAGE_BACKUP_DATABASE_URL_PARAMETER = var.backup_database_url_parameter
      NETTRIAGE_RUNTIME_TABLE                 = var.runtime_table_name
      NETTRIAGE_BACKUPS_BUCKET                = aws_s3_bucket.backups.bucket
      NETTRIAGE_APP_URL                       = var.app_url
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.ops,
    aws_iam_role_policy.ops_logs,
    aws_iam_role_policy.ops_parameters,
    aws_iam_role_policy.ops_runtime_table,
    aws_iam_role_policy.ops_backups,
  ]
}

# EventBridge Scheduler invokes the function, with this role and no other right.
resource "aws_iam_role" "scheduler" {
  name = "${local.ops}-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name = "invoke-ops"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.ops.arn
    }]
  })
}

resource "aws_scheduler_schedule" "ops" {
  for_each = local.schedules

  name                         = "${local.ops}-${replace(each.key, "_", "-")}"
  schedule_expression          = each.value.expression
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.ops.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ job = each.key })

    retry_policy {
      maximum_retry_attempts       = each.value.retries
      maximum_event_age_in_seconds = 3600
    }
  }
}
```

Create `infra/modules/ops/outputs.tf`:
```hcl
output "function_name" {
  value       = aws_lambda_function.ops.function_name
  description = "The ops function, which `just restore-drill-<stage>` invokes."
}

output "backups_bucket" {
  value = aws_s3_bucket.backups.bucket
}
```

Create `infra/modules/ops/variables.tf`:
```hcl
variable "stage" {
  type = string
}

variable "lambda_zip_path" {
  type        = string
  description = "Path to dist/backend.zip built by tools/build_lambda.py; every function ships it."
}

variable "pg_client_zip_path" {
  type        = string
  description = "Path to dist/pg-client.zip built by tools/pack_pg_client.py: Postgres 17 for the layer."
}

variable "app_version" {
  type        = string
  description = "Git SHA the function reports in its telemetry."
}

variable "runtime" {
  type    = string
  default = "python3.14"
}

variable "otel_collector_layer_arn" {
  type = string

  validation {
    condition     = can(regex("^arn:aws:lambda:eu-north-1:[0-9]{12}:layer:opentelemetry-collector-arm64-[0-9a-z_-]+:[0-9]+$", var.otel_collector_layer_arn))
    error_message = "Use the eu-north-1 arm64 OpenTelemetry collector layer."
  }
}

variable "grafana_otlp_endpoint" {
  type        = string
  description = "Grafana Cloud OTLP endpoint (not secret)."

  validation {
    condition     = can(regex("^https://", var.grafana_otlp_endpoint))
    error_message = "Use the Grafana Cloud OTLP https endpoint."
  }
}

variable "grafana_otlp_auth" {
  type        = string
  sensitive   = true
  description = "base64(instanceID:token) for a write-only telemetry token."
}

variable "database_url_parameter" {
  type        = string
  description = "SSM parameter holding app_ops's pooled database URL, which the deploy writes."
}

variable "backup_database_url_parameter" {
  type        = string
  description = "SSM parameter holding app_backup's direct database URL, which the deploy writes."
}

variable "runtime_table_name" {
  type = string
}

variable "runtime_table_arn" {
  type = string
}

variable "app_url" {
  type        = string
  description = "The app's CloudFront URL, which the probe fetches /api/health through."

  validation {
    condition     = can(regex("^https://", var.app_url))
    error_message = "The app's URL is https."
  }
}
```

Create `infra/modules/ops/versions.tf`:
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

In `justfile`, replace:
```
tf-check:
    terraform fmt -check -recursive infra
    for d in infra/bootstrap infra/modules/app infra/modules/data infra/modules/edge infra/modules/identity infra/modules/pipeline; do (cd "$d" && export TF_DATA_DIR=.terraform-check && terraform init -backend=false -input=false >/dev/null && terraform validate && terraform test) || exit 1; done

# CI hygiene: every workflow action pinned to a SHA
```
with:
```
tf-check:
    terraform fmt -check -recursive infra
    for d in infra/bootstrap infra/modules/app infra/modules/data infra/modules/edge infra/modules/identity infra/modules/ops infra/modules/pipeline; do (cd "$d" && export TF_DATA_DIR=.terraform-check && terraform init -backend=false -input=false >/dev/null && terraform validate && terraform test) || exit 1; done

# CI hygiene: every workflow action pinned to a SHA
```

- [ ] **Step 4: Run the tests again**

Run: `cd infra/modules/ops && export TF_DATA_DIR=.terraform-check && terraform init -backend=false -input=false > /dev/null && terraform test -no-color`
Expected: `Success! 6 passed, 0 failed.` `terraform init` wrote `infra/modules/ops/.terraform.lock.hcl`, which is committed like every module's.

- [ ] **Step 5: Check every module, and validate dev**

Run: `just tf-check && cd infra/envs/dev && TF_DATA_DIR=.terraform-check terraform init -backend=false -input=false > /dev/null && TF_DATA_DIR=.terraform-check terraform validate -no-color`
Expected: `terraform fmt` passes, and each module's tests end with `Success!`, ops's with `6 passed, 0 failed.`; then `Success! The configuration is valid.` tflint and Checkov run in the PR's CI (Task 13), where the `terraform` job must be green.

- [ ] **Step 6: Commit**

`.terraform-check` folders are git-ignored, so `git add infra/modules/ops` stages only the module, its tests and its lock file.

```bash
git add infra/modules/ops infra/envs/dev/main.tf infra/envs/dev/variables.tf infra/envs/dev/outputs.tf .checkov.yaml justfile .github/workflows/ci.yml
git commit -m "feat(infra): the ops function, its Postgres layer, five schedules and the backups bucket, with least-privilege IAM" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 10: The deploy: two logins, the layer for Terraform, and `just restore-drill-dev`

**Files:**
- Create: `tools/deploy/drill.py`
- Modify: `tools/deploy/config.py`, `tools/deploy/database.py`, `tools/deploy/__main__.py`, `justfile`, `CLAUDE.md`
- Test: `tools/tests/test_deploy_database.py`, `tools/tests/test_deploy_cli.py`, `tools/tests/test_deploy_drill.py`

**Interfaces:**
- Consumes: the roles (Task 2); the `pg-client-zip` artifact (Task 1); `JobFailed`, `DrillFailed` and `NoBackup` as Lambda reports them (Tasks 6 and 7); the function's name and the variable `pg_client_zip_path` (Task 9).
- Produces:
  - `config.APP_DB_ROLES` with `app_ops` and `app_backup`, `config.DIRECT_DB_ROLES = ("app_backup",)`, `config.PG_CLIENT_ARTIFACT = "pg-client-zip"` and `config.ops_function(stage) -> str`;
  - `database.direct_url(owner_url, role, password) -> str`, which `ensure_role_logins` uses for the direct roles;
  - `download_packages(run, run_id, dest) -> Path`, and `stage_env(run, env, stage, sha, dist)`, which passes `TF_VAR_lambda_zip_path` and `TF_VAR_pg_client_zip_path`;
  - `drill.restore_drill(run, env, stage) -> str`, the command `python -m tools.deploy restore-drill [--stage dev]`, and the recipe `just restore-drill-dev`.

- [ ] **Step 1: Write the failing tests**

In `tools/tests/test_deploy_cli.py`, replace:
```python
    assert workflows == ["ci.yml", "codeql.yml"]
    assert [call.args[call.args.index("--name") + 1] for call in run.called("gh", "run", "download")] == [
        "backend-zip", "web-dist",
    ]
    assert run.first("gh", "run", "download") < run.first("terraform", "apply") < run.first("aws", "s3", "sync")
```
with:
```python
    assert workflows == ["ci.yml", "codeql.yml"]
    assert [call.args[call.args.index("--name") + 1] for call in run.called("gh", "run", "download")] == [
        "backend-zip", "pg-client-zip", "web-dist",
    ]
    assert run.first("gh", "run", "download") < run.first("terraform", "apply") < run.first("aws", "s3", "sync")
```

In `tools/tests/test_deploy_cli.py`, replace:
```python
    assert apply.env["TF_VAR_grafana_otlp_auth"] == "dG9rZW4="
    assert apply.env["TF_VAR_lambda_zip_path"].endswith("backend.zip")
    assert smoke_argv == [[
        "--base-url", "https://d111.cloudfront.net",
```
with:
```python
    assert apply.env["TF_VAR_grafana_otlp_auth"] == "dG9rZW4="
    assert apply.env["TF_VAR_lambda_zip_path"].endswith("backend.zip")
    assert apply.env["TF_VAR_pg_client_zip_path"].endswith("pg-client.zip")
    assert smoke_argv == [[
        "--base-url", "https://d111.cloudfront.net",
```

In `tools/tests/test_deploy_cli.py`, replace:
```python
    assert "### Terraform plan: dev (ddddddd)" in out
    assert "dG9rZW4=" not in out


```
with:
```python
    assert "### Terraform plan: dev (ddddddd)" in out
    assert "dG9rZW4=" not in out


def test_plan_gives_terraform_the_ci_built_postgres_layer(stage_dir: Path) -> None:
    run = planning(runs((5, "completed", "success", "pull_request")))

    cli.plan(run, {}, "dev", post_comment=False)

    assert [call.args[call.args.index("--name") + 1] for call in run.called("gh", "run", "download")] == [
        "backend-zip", "pg-client-zip",
    ]
    [plan_call] = run.called("terraform", "plan")
    assert plan_call.env is not None
    assert plan_call.env["TF_VAR_pg_client_zip_path"].endswith("pg-client.zip")


```

In `tools/tests/test_deploy_database.py`, replace:
```python
    assert put.args[put.args.index("--name") + 1] == missing
    assert urlsplit(put.args[put.args.index("--value") + 1]).username == "app_triage"
```
with:
```python
    assert put.args[put.args.index("--name") + 1] == missing
    assert urlsplit(put.args[put.args.index("--value") + 1]).username == "app_triage"


def test_the_ops_job_gets_its_own_login_through_the_pooler() -> None:
    missing = "/nettriage/dev/db/app-ops-url"
    run = FakeRun().on("aws", "ssm", "describe-parameters", returns=ssm_names(missing=[missing]))
    run.on("aws", "ssm", "put-parameter")

    created = database.ensure_role_logins(run, {}, "dev", OWNER_URL, set_password=lambda *a: None)

    assert created == ["app_ops"]
    [put] = run.called("aws", "ssm", "put-parameter")
    stored = urlsplit(put.args[put.args.index("--value") + 1])
    assert put.args[put.args.index("--name") + 1] == missing
    assert stored.username == "app_ops"
    assert stored.hostname == "ep-quiet-sun-123456-pooler.eu-central-1.aws.neon.tech"


def test_the_backup_role_connects_directly_because_pg_dump_needs_a_session() -> None:
    missing = "/nettriage/dev/db/app-backup-url"
    run = FakeRun().on("aws", "ssm", "describe-parameters", returns=ssm_names(missing=[missing]))
    run.on("aws", "ssm", "put-parameter")
    given: list[tuple[str, str, str]] = []

    created = database.ensure_role_logins(
        run, {}, "dev", OWNER_URL, set_password=lambda *args: given.append(args)
    )

    assert created == ["app_backup"]
    [(_, _, password)] = given
    [put] = run.called("aws", "ssm", "put-parameter")
    assert put.args[put.args.index("--name") + 1] == missing
    assert put.args[put.args.index("--value") + 1] == database.direct_url(
        OWNER_URL, "app_backup", password
    )


def test_a_direct_connection_keeps_the_owners_endpoint_with_an_escaped_password_and_verified_tls() -> None:
    url = database.direct_url(OWNER_URL, "app_backup", "p/w+1")

    assert url == (
        "postgresql://app_backup:p%2Fw%2B1@ep-quiet-sun-123456.eu-central-1.aws.neon.tech/"
        "neondb?sslmode=verify-full"
    )
```

Create `tools/tests/test_deploy_drill.py`:
```python
"""The restore drill on demand (Plan 7a §2.5): `just restore-drill-<stage>` invokes the ops
function once, with the owner's session, and says what it restored."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from tools.deploy import __main__ as cli
from tools.deploy.runner import CommandError
from tools.tests.deploy_fakes import Call, signed_in

RESTORED = {"job": "restore_drill", "day": "2026-10-07", "tables": 15, "rows": 1234}


def invoked(answer: dict[str, object], function_error: str = "") -> Callable[[Call], str]:
    """`aws lambda invoke`: the function's answer goes to the file named last, and the call's
    metadata to stdout, with FunctionError when the function raised."""

    def respond(call: Call) -> str:
        Path(call.args[-1]).write_text(json.dumps(answer), encoding="utf-8")
        metadata: dict[str, object] = {"StatusCode": 200, "ExecutedVersion": "$LATEST"}
        if function_error:
            metadata["FunctionError"] = function_error
        return json.dumps(metadata)

    return respond


def test_the_drill_invokes_the_ops_function_once_and_says_what_it_restored(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run = signed_in().on("aws", "lambda", "invoke", returns=invoked(RESTORED))

    code = cli.main(["restore-drill", "--stage", "dev"], run=run)

    assert code == 0
    [invoke] = run.called("aws", "lambda", "invoke")
    assert invoke.args[invoke.args.index("--function-name") + 1] == "nettriage-dev-ops"
    assert json.loads(invoke.args[invoke.args.index("--payload") + 1]) == {"job": "restore_drill"}
    assert invoke.args[invoke.args.index("--cli-binary-format") + 1] == "raw-in-base64-out"
    # The drill may use the function's whole 600 s, and a retry would start a second drill.
    assert int(invoke.args[invoke.args.index("--cli-read-timeout") + 1]) > 600
    assert invoke.env is not None
    assert invoke.env["AWS_MAX_ATTEMPTS"] == "1"
    assert (
        "The restore drill restored the backup of 2026-10-07: 15 tables and 1234 rows, "
        "every table's count matching its manifest."
    ) in capsys.readouterr().out


@pytest.mark.parametrize(
    ("error_type", "message"),
    [
        ("DrillFailed", "The restored backup of 2026-10-07 differs from its manifest in findings, uploads."),
        ("NoBackup", "There's no backup to restore yet."),
    ],
)
def test_the_drills_own_failures_are_told_as_they_are(
    error_type: str, message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    failure = {"errorType": error_type, "errorMessage": message, "stackTrace": ["  File ..."]}
    run = signed_in().on("aws", "lambda", "invoke", returns=invoked(failure, "Unhandled"))

    code = cli.main(["restore-drill"], run=run)

    assert code == 1
    assert f"STOP: {message}" in capsys.readouterr().err


def test_any_other_failure_is_named_by_type_only(capsys: pytest.CaptureFixture[str]) -> None:
    failure = {
        "errorType": "OperationalError",
        "errorMessage": 'connection to server at "ep-quiet-sun-123456.eu-central-1.aws.neon.tech" '
        'failed: password authentication failed for user "app_backup"',
    }
    run = signed_in().on("aws", "lambda", "invoke", returns=invoked(failure, "Unhandled"))

    code = cli.main(["restore-drill"], run=run)

    err = capsys.readouterr().err
    assert code == 1
    assert "STOP: The restore drill failed with OperationalError." in err
    assert "/aws/lambda/nettriage-dev-ops" in err
    assert "ep-quiet-sun" not in err
    assert "app_backup" not in err


def test_a_failure_the_function_names_by_its_type_is_told_by_that_type(
    capsys: pytest.CaptureFixture[str],
) -> None:
    failure = {"errorType": "JobFailed", "errorMessage": "RestoreFailed"}
    run = signed_in().on("aws", "lambda", "invoke", returns=invoked(failure, "Unhandled"))

    code = cli.main(["restore-drill"], run=run)

    assert code == 1
    assert "STOP: The restore drill failed with RestoreFailed." in capsys.readouterr().err


def test_a_stage_without_the_ops_function_says_to_deploy_it_first(
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = CommandError("`aws lambda` failed with exit code 254.")
    run = signed_in().on("aws", "lambda", "invoke", returns=missing)

    code = cli.main(["restore-drill"], run=run)

    err = capsys.readouterr().err
    assert code == 1
    assert "Couldn't invoke nettriage-dev-ops" in err
    assert "just deploy-dev" in err
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --project backend python -m pytest tools/tests/test_deploy_database.py tools/tests/test_deploy_cli.py tools/tests/test_deploy_drill.py`
Expected: FAIL: `11 failed, 50 passed`. The new roles get no login, plan and deploy download only `backend-zip` (and the site), and `argument command: invalid choice: 'restore-drill'`.

- [ ] **Step 3: Give the roles their logins, hand Terraform the layer, and add the drill command**

In `CLAUDE.md`, replace:
```markdown
and `just plan-dev` when the owner asks, but never runs `aws login`, `just bootstrap`,
`just store-grafana-token`, `just store-database-url`, `just pause-uploads`, `just resume-uploads`,
`just pause-ai`, `just resume-ai` or `just deploy-*`.

## Working with the owner
```
with:
```markdown
and `just plan-dev` when the owner asks, but never runs `aws login`, `just bootstrap`,
`just store-grafana-token`, `just store-database-url`, `just pause-uploads`, `just resume-uploads`,
`just pause-ai`, `just resume-ai`, `just restore-drill-*` or `just deploy-*`.

## Working with the owner
```

In `justfile`, replace:
```
deploy-dev:
    uv run --project backend python -m tools.deploy deploy --stage dev
```
with:
```
deploy-dev:
    uv run --project backend python -m tools.deploy deploy --stage dev

# AWS: restore dev's newest backup into a throwaway database and check its counts (Plan 7a §2.5)
restore-drill-dev:
    uv run --project backend python -m tools.deploy restore-drill --stage dev
```

In `tools/deploy/__main__.py`, replace:
```python
  python -m tools.deploy uploads on|off [--stage dev]
  python -m tools.deploy ai on|off [--stage dev]

Every command uses the owner's short-lived `aws login` session: profile "nettriage", or
```
with:
```python
  python -m tools.deploy uploads on|off [--stage dev]
  python -m tools.deploy ai on|off [--stage dev]
  python -m tools.deploy restore-drill [--stage dev]

Every command uses the owner's short-lived `aws login` session: profile "nettriage", or
```

In `tools/deploy/__main__.py`, replace:
```python

from tools import smoke
from tools.deploy import database, config, github, gitguards, preflight, publish, secrets, session, terraform
from tools.deploy import runner
from tools.deploy.runner import CommandError, Runner
```
with:
```python

from tools import smoke
from tools.deploy import database, config, drill, github, gitguards, preflight, publish, secrets, session, terraform
from tools.deploy import runner
from tools.deploy.runner import CommandError, Runner
```

In `tools/deploy/__main__.py`, replace:
```python


def stage_env(run: Runner, env: Mapping[str, str], stage: str, sha: str, lambda_zip: Path) -> dict[str, str]:
    """Terraform's inputs for a stage, passed as environment variables, never as arguments."""
    return {
        **env,
        "TF_VAR_lambda_zip_path": str(lambda_zip),
        "TF_VAR_app_version": sha,
        "TF_VAR_grafana_otlp_auth": secrets.read_otlp_auth(run, env, stage),
```
with:
```python


def download_packages(run: Runner, run_id: int, dest: Path) -> Path:
    """The CI run's Lambda package and Postgres layer, side by side in dest."""
    github.download(run, run_id, config.BACKEND_ARTIFACT, dest)
    return github.download(run, run_id, config.PG_CLIENT_ARTIFACT, dest)


def stage_env(run: Runner, env: Mapping[str, str], stage: str, sha: str, dist: Path) -> dict[str, str]:
    """Terraform's inputs for a stage, passed as environment variables, never as arguments."""
    return {
        **env,
        "TF_VAR_lambda_zip_path": str(dist / "backend.zip"),
        "TF_VAR_pg_client_zip_path": str(dist / "pg-client.zip"),
        "TF_VAR_app_version": sha,
        "TF_VAR_grafana_otlp_auth": secrets.read_otlp_auth(run, env, stage),
```

In `tools/deploy/__main__.py`, replace:
```python
    workdir = config.stage_dir(stage)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        dist = github.download(run, ci.run_id, config.BACKEND_ARTIFACT, Path(scratch) / "dist")
        tf_env = stage_env(run, env, stage, sha, dist / "backend.zip")
        gitguards.require_unchanged_since(run, sha, "plan")
        terraform.init(run, tf_env, workdir, bucket, config.state_key(stage))
```
with:
```python
    workdir = config.stage_dir(stage)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        dist = download_packages(run, ci.run_id, Path(scratch) / "dist")
        tf_env = stage_env(run, env, stage, sha, dist)
        gitguards.require_unchanged_since(run, sha, "plan")
        terraform.init(run, tf_env, workdir, bucket, config.state_key(stage))
```

In `tools/deploy/__main__.py`, replace:
```python
    workdir = config.stage_dir(stage)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        dist = github.download(run, ci.run_id, config.BACKEND_ARTIFACT, Path(scratch) / "dist")
        web = github.download(run, ci.run_id, config.WEB_ARTIFACT, Path(scratch) / "web")
        tf_env = stage_env(run, env, stage, sha, dist / "backend.zip")
        gitguards.require_unchanged_since(run, sha, "deploy")
        migrate_database(run, env, stage)
```
with:
```python
    workdir = config.stage_dir(stage)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        dist = download_packages(run, ci.run_id, Path(scratch) / "dist")
        web = github.download(run, ci.run_id, config.WEB_ARTIFACT, Path(scratch) / "web")
        tf_env = stage_env(run, env, stage, sha, dist)
        gitguards.require_unchanged_since(run, sha, "deploy")
        migrate_database(run, env, stage)
```

In `tools/deploy/__main__.py`, replace:
```python
    parser.add_argument("--profile", default=os.environ.get("NETTRIAGE_AWS_PROFILE", config.DEFAULT_PROFILE))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "store-grafana-token", "store-database-url", "plan", "deploy"):
        command = commands.add_parser(name)
        command.add_argument("--stage", choices=config.STAGES, default="dev")
```
with:
```python
    parser.add_argument("--profile", default=os.environ.get("NETTRIAGE_AWS_PROFILE", config.DEFAULT_PROFILE))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "store-grafana-token", "store-database-url", "plan", "deploy", "restore-drill"):
        command = commands.add_parser(name)
        command.add_argument("--stage", choices=config.STAGES, default="dev")
```

In `tools/deploy/__main__.py`, replace:
```python
        elif args.command == "ai":
            switch_ai(run, env, args.stage, on=args.state == "on")
        elif args.command == "plan":
            plan(run, env, args.stage, post_comment=not args.no_comment)
```
with:
```python
        elif args.command == "ai":
            switch_ai(run, env, args.stage, on=args.state == "on")
        elif args.command == "restore-drill":
            print(drill.restore_drill(run, env, args.stage))
        elif args.command == "plan":
            plan(run, env, args.stage, post_comment=not args.no_comment)
```

In `tools/deploy/config.py`, replace:
```python
CODEQL_WORKFLOW = "codeql.yml"
BACKEND_ARTIFACT = "backend-zip"
WEB_ARTIFACT = "web-dist"
BOOTSTRAP_DIR = REPO / "infra" / "bootstrap"
```
with:
```python
CODEQL_WORKFLOW = "codeql.yml"
BACKEND_ARTIFACT = "backend-zip"
# The ops function's Postgres client and server, built in CI (Plan 7a §4).
PG_CLIENT_ARTIFACT = "pg-client-zip"
WEB_ARTIFACT = "web-dist"
BOOTSTRAP_DIR = REPO / "infra" / "bootstrap"
```

In `tools/deploy/config.py`, replace:
```python
# blocks unsigned executables.
NEON_HOST_SUFFIX = ".eu-central-1.aws.neon.tech"
# Database roles that get a login from the deploy. Plan 7 adds the ops job's.
APP_DB_ROLES = ("app_api", "app_analyze", "app_triage")
# Where the account may invoke Bedrock (spec Revision 2, R1).
BEDROCK_REGIONS = ("eu-north-1", "us-east-1", "us-west-2")
```
with:
```python
# blocks unsigned executables.
NEON_HOST_SUFFIX = ".eu-central-1.aws.neon.tech"
# Database roles that get a login from the deploy: one per function, and the ops function's two
# (Plan 7a §5). The backup role connects directly: pg_dump needs a session, and the pooler
# works by transaction.
APP_DB_ROLES = ("app_api", "app_analyze", "app_triage", "app_ops", "app_backup")
DIRECT_DB_ROLES = ("app_backup",)
# Where the account may invoke Bedrock (spec Revision 2, R1).
BEDROCK_REGIONS = ("eu-north-1", "us-east-1", "us-west-2")
```

In `tools/deploy/config.py`, replace:
```python


def db_owner_url_parameter(stage: str) -> str:
    return f"/nettriage/{stage}/db/owner-url"
```
with:
```python


def ops_function(stage: str) -> str:
    """The function that runs the scheduled jobs (infra/modules/ops)."""
    return f"nettriage-{stage}-ops"


def db_owner_url_parameter(stage: str) -> str:
    return f"/nettriage/{stage}/db/owner-url"
```

In `tools/deploy/database.py`, replace:
```python
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{quote(role, safe='')}:{quote(password, safe='')}@{endpoint}-pooler.{domain}{port}"
    return urlunsplit(("postgresql", netloc, parts.path, "sslmode=verify-full", ""))

```
with:
```python
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{quote(role, safe='')}:{quote(password, safe='')}@{endpoint}-pooler.{domain}{port}"
    return urlunsplit(("postgresql", netloc, parts.path, "sslmode=verify-full", ""))


def direct_url(owner_url: str, role: str, password: str) -> str:
    """The connection string for a role that needs a session (`app_backup`'s pg_dump): its
    role, on the owner's direct endpoint, verifying TLS."""
    parts = urlsplit(owner_url)
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{quote(role, safe='')}:{quote(password, safe='')}@{parts.hostname}{port}"
    return urlunsplit(("postgresql", netloc, parts.path, "sslmode=verify-full", ""))

```

In `tools/deploy/database.py`, replace:
```python
                "Check the connection string with: just store-database-url " + stage
            ) from None
        ssm.store_parameter(run, env, name, pooled_url(owner_url, role, password))
        created.append(role)
    return created
```
with:
```python
                "Check the connection string with: just store-database-url " + stage
            ) from None
        url = direct_url if role in config.DIRECT_DB_ROLES else pooled_url
        ssm.store_parameter(run, env, name, url(owner_url, role, password))
        created.append(role)
    return created
```

Create `tools/deploy/drill.py`:
```python
"""The restore drill on demand (Plan 7a §2.5): the owner's session invokes the stage's ops
function once with `{"job": "restore_drill"}` and waits for its answer. The drill's own
failures are told as they are, since they name only a day and tables; any other failure is
named by its type, because its message could quote connection details: the function sends
`JobFailed` with only the type, and anything else (a timeout) is named by its own type."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from pathlib import Path

from tools.deploy import config
from tools.deploy.runner import CommandError, Runner

# The function may run for 600 s; the CLI waits a minute longer, and never retries, since a
# retry would start a second drill beside the first.
READ_TIMEOUT_SECONDS = 660
TOLD = ("DrillFailed", "NoBackup")


def restore_drill(run: Runner, env: Mapping[str, str], stage: str) -> str:
    function = config.ops_function(stage)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        answer_file = Path(scratch) / "answer.json"
        try:
            metadata = run(
                ["aws", "lambda", "invoke", "--function-name", function,
                 "--payload", json.dumps({"job": "restore_drill"}),
                 "--cli-binary-format", "raw-in-base64-out",
                 "--cli-read-timeout", str(READ_TIMEOUT_SECONDS),
                 "--output", "json", str(answer_file)],
                env={**env, "AWS_MAX_ATTEMPTS": "1"},
            ).stdout
        except CommandError as exc:
            raise CommandError(
                f"Couldn't invoke {function}: {exc} If this stage hasn't been deployed since "
                f"Plan 7a, run just deploy-{stage} first."
            ) from exc
        answer = json.loads(answer_file.read_text(encoding="utf-8"))
    if json.loads(metadata).get("FunctionError"):
        error_type = str(answer.get("errorType", "an unknown error"))
        if error_type in TOLD:
            raise CommandError(str(answer.get("errorMessage", error_type)))
        if error_type == "JobFailed":  # the function's stand-in, whose message is the type
            error_type = str(answer.get("errorMessage", error_type))
        raise CommandError(
            f"The restore drill failed with {error_type}. Its logs are in CloudWatch, in "
            f"/aws/lambda/{function}, and in Grafana (service nettriage-ops)."
        )
    return (
        f"The restore drill restored the backup of {answer['day']}: {answer['tables']} tables "
        f"and {answer['rows']} rows, every table's count matching its manifest."
    )
```

- [ ] **Step 4: Run the tests again**

Run: `uv run --project backend python -m pytest tools/tests/test_deploy_database.py tools/tests/test_deploy_cli.py tools/tests/test_deploy_drill.py`
Expected: `61 passed`.

- [ ] **Step 5: Run every tool test, and the CI hygiene checks**

Run: `uv run --project backend python -m pytest tools/tests && just --list | grep restore-drill && uv run --project backend python tools/check_no_cloud_access.py .`
Expected: `272 passed`; the recipe `restore-drill-dev` is listed; `CI holds no cloud access.`

- [ ] **Step 6: Commit**

```bash
git add tools/deploy tools/tests/test_deploy_database.py tools/tests/test_deploy_cli.py tools/tests/test_deploy_drill.py justfile CLAUDE.md
git commit -m "feat(deploy): logins for app_ops and app_backup (direct, for pg_dump's session), the CI-built Postgres layer for Terraform, and the owner's just restore-drill-dev" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 11: The flaky test

**Files:**
- Test: `backend/tests/api/test_rate_limited_routes.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new. Each test in the file owns its viewer address.

**The cause (ops §7).** `test_too_many_sign_ins_from_one_ip_land_on_the_limited_page` draws its address from `198.51.100.1`–`250`, and its limited sign-in writes a sampled `ratelimit.limited` audit row for it. The test database lives for the whole session, and the file's last test, `test_a_limited_health_check_writes_no_audit_row`, counts the audit rows for `198.51.100.88`. Once in 250 runs, the draw is `.88`.

- [ ] **Step 1: Reproduce it**

For this step only, make the sign-in test draw the address that collides.

In `backend/tests/api/test_rate_limited_routes.py`, replace:
```python
    viewer = {"CloudFront-Viewer-Address": f"198.51.100.{uuid4().int % 250 + 1}:1234"}
```
with:
```python
    viewer = {"CloudFront-Viewer-Address": "198.51.100.88:1234"}
```

- [ ] **Step 2: Run the file to see the flake every time**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/api/test_rate_limited_routes.py`
Expected: FAIL: `1 failed, 6 passed`; `test_a_limited_health_check_writes_no_audit_row` fails with `assert 1 == 0`.

- [ ] **Step 3: Give the sign-in test its own fixed address**

Step 1's line goes, and the test gets an address of its own.

In `backend/tests/api/test_rate_limited_routes.py`, replace:
```python
    viewer = {"CloudFront-Viewer-Address": "198.51.100.88:1234"}
    responses = [
```
with:
```python
    # Each test here owns its address: the session's database keeps every test's audit rows, so
    # a shared one would be counted twice (a random .88 once broke the health check's test).
    viewer = {"CloudFront-Viewer-Address": "198.51.100.66:1234"}
    responses = [
```
`uuid4` stays imported: the file's other tests still use it.

- [ ] **Step 4: Run the file again**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/api/test_rate_limited_routes.py && grep -rn "198\.51\.100\.66" tests --include=*.py`
Expected: `7 passed`, and `.66` appears only in this file.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/api/test_rate_limited_routes.py
git commit -m "test(api): give the sign-in limit test its own viewer address, so its sampled audit row can't land on the health check's" -m "Its address was drawn at random from 198.51.100.1-250, so once in 250 runs it was .88, the health check test's own, and that test found the sign-in's ratelimit.limited row (Plan 7a §7)." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 12: The decisions in the M1 spec, the owner's backups and drill in the runbook, and the README

**Files:**
- Modify: `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (§3.5, §5.4, §5.6, §9.2, §9.6, §9.7), `docs/superpowers/specs/2026-10-06-nettriage-ops-design.md` (§4, the layer), `docs/runbooks/setup-and-deploy.md` (B2, B14, Part C), `README.md`

**Interfaces:**
- Consumes: every decision above, and the messages of Task 10's command.
- Produces: runbook B14, "Backups and the restore drill", which the owner follows the morning after the deploy; its **Drills performed** line, which Claude fills in from the owner's first drill.

- [ ] **Step 1: Write the docs**

In `README.md`, replace:
```markdown
> uploads, findings, triage, the AI's explanations, the audit log and usage are built, with
> a home of your work and each organization's findings at a glance; the public demo is next.

## Architecture
```
with:
```markdown
> uploads, findings, triage, the AI's explanations, the audit log and usage are built, with
> a home of your work and each organization's findings at a glance; the public demo is next.
> Operations has begun: nightly backups checked by a weekly restore drill, a daily cleanup,
> and probes.

## Architecture
```

In `README.md`, replace:
```markdown
- **A typed web app**: React and TypeScript, with an API client generated from the API's OpenAPI document, so CI fails when the two drift. It sends CloudFront's body hash, the CSRF token and idempotency keys on its own, and shows every API error with its reference. Files go from the browser straight to S3 with their fingerprint and progress; findings open most severe first; a port scan's evidence is drawn on a map of the host's ports; and triage names the version it read, so a newer change is shown, never overwritten. The home lists what's assigned to you across organizations, and every count on it, or above an organization's findings, links to exactly the findings it counts.
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for each function.
- **Distributed rate limiting** with GCRA on DynamoDB, exact under concurrency ([ADR 0006](docs/adr/0006-gcra-rate-limiter.md)).
- **Supply chain**: SHA-pinned actions, CodeQL, dependency review, Dependabot, Checkov and tflint.
```
with:
```markdown
- **A typed web app**: React and TypeScript, with an API client generated from the API's OpenAPI document, so CI fails when the two drift. It sends CloudFront's body hash, the CSRF token and idempotency keys on its own, and shows every API error with its reference. Files go from the browser straight to S3 with their fingerprint and progress; findings open most severe first; a port scan's evidence is drawn on a map of the host's ports; and triage names the version it read, so a newer change is shown, never overwritten. The home lists what's assigned to you across organizations, and every count on it, or above an organization's findings, links to exactly the findings it counts.
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for each function.
- **Backups known to work**: every night a Lambda dumps the database from a consistent snapshot to S3, and every week it restores the newest dump into a throwaway Postgres inside the function and checks every table's rows against the backup's manifest. Its Postgres is built from source in CI and proven inside AWS's own Lambda image, so CI still needs no cloud access. No role, the backup's included, skips row-level security.
- **Distributed rate limiting** with GCRA on DynamoDB, exact under concurrency ([ADR 0006](docs/adr/0006-gcra-rate-limiter.md)).
- **Supply chain**: SHA-pinned actions, CodeQL, dependency review, Dependabot, Checkov and tflint.
```

In `README.md`, replace:
````markdown
just plan-dev                   # on a PR: plan and post the changes
just deploy-dev                 # on main: deploy CI's artifacts, then smoke tests
```

````
with:
````markdown
just plan-dev                   # on a PR: plan and post the changes
just deploy-dev                 # on main: deploy CI's artifacts, then smoke tests
just restore-drill-dev          # restore the newest backup and check its counts
```

````

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
1. refuses anything but a clean `main` that matches GitHub and whose CI and CodeQL passed;
2. runs the preflight;
3. downloads that commit's CI-built artifacts;
4. migrates the database and prints `Database migrated.`, then
   `Reference data synced: 3 detectors, 12 ATT&CK techniques.` When a function's database role
   is new, it also gives it a login and adds `New logins: <role>.` to the first line (Plan 4b
   added `app_analyze`, Plan 5b adds `app_triage`);
5. shows the Terraform plan, and you type `yes`;
6. publishes the site;
7. runs the smoke tests.
```
with:
```markdown
1. refuses anything but a clean `main` that matches GitHub and whose CI and CodeQL passed;
2. runs the preflight;
3. downloads that commit's CI-built artifacts: the functions' package, the `ops` function's
   Postgres layer and the site;
4. migrates the database and prints `Database migrated.`, then
   `Reference data synced: 3 detectors, 12 ATT&CK techniques.` When a function's database role
   is new, it also gives it a login and adds `New logins: <role>.` to the first line (Plan 4b
   added `app_analyze`, Plan 5b `app_triage`, and Plan 7a's first deploy prints
   `Database migrated. New logins: app_ops, app_backup.`);
5. shows the Terraform plan, and you type `yes`. Plan 7a's first deploy adds `module.ops`: the
   `ops` function, its `pg-client` layer, five schedules and their role, and the backups bucket;
6. publishes the site;
7. runs the smoke tests.
```

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
7. Delete the organization as in B11 steps 6 and 7, typing `Glance Test`.

## Part C: when things go wrong

```
with:
````markdown
7. Delete the organization as in B11 steps 6 and 7, typing `Glance Test`.

### B14. Backups and the restore drill
Every night at 02:00 UTC the `ops` function dumps the database into the backups bucket, and
every Sunday at 04:00 UTC it restores the newest dump into a throwaway database and checks every
table's rows (Plan 7a). Do this once, the morning after Plan 7a's deploy: it is the restore
drill Milestone 1 asks for.
1. See last night's backup. Open https://console.aws.amazon.com/s3/ and click **Buckets**.
   Click `nettriage-dev-backups-` followed by 8 characters, then the `pg/` folder. It holds two
   objects named for today's date (UTC), such as `2026-10-08.dump` and `2026-10-08.json`. Each
   night adds two more, and objects older than 7 days disappear on their own.
2. In a terminal at the repository's root, sign in and start the drill:
   ```bash
   aws login --profile nettriage
   just restore-drill-dev
   ```
   It takes a minute or two. Success is one line:
   `The restore drill restored the backup of <date>: 15 tables and <n> rows, every table's count matching its manifest.`
   `<date>` is today's (UTC), or yesterday's before 02:00 UTC. Anything starting with `STOP:`
   is in Part C's table.
3. Send Claude that line. Claude adds it to **Drills performed** below, which is the record
   Milestone 1 asks for.
4. Sign out: `aws logout --profile nettriage`.

From then on the drill repeats every Sunday by itself.

**Drills performed:** none yet.

## Part C: when things go wrong

````

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
| `STOP: Can't read /nettriage/dev/db/owner-url from SSM. …` | Run A7 |
| `STOP: Database migrations failed; nothing was deployed. …` | Send the output to Claude. Nothing in AWS changed |
| `STOP: Couldn't give the database role app_api a login …` (or `app_analyze`, `app_triage`) | Check the stored string (A7), then send the output to Claude |
| `STOP: Syncing reference data failed; nothing in AWS changed. …` | The migrations ran, but the detectors and ATT&CK techniques weren't loaded. Send the output to Claude |
| `FAIL  Grafana OTLP endpoint in terraform.tfvars` | Put your endpoint in `terraform.tfvars` (A3) |
```
with:
```markdown
| `STOP: Can't read /nettriage/dev/db/owner-url from SSM. …` | Run A7 |
| `STOP: Database migrations failed; nothing was deployed. …` | Send the output to Claude. Nothing in AWS changed |
| `STOP: Couldn't give the database role app_api a login …` (or `app_analyze`, `app_triage`, `app_ops`, `app_backup`) | Check the stored string (A7), then send the output to Claude |
| `STOP: There's no backup to restore yet.` | The first backup runs at 02:00 UTC after Plan 7a's deploy. Run `just restore-drill-dev` again after that |
| `STOP: The restored backup of <date> differs from its manifest in <tables>.` | The backup or its restore lost rows. Don't delete anything; send the line to Claude. The bucket keeps 7 nights of backups, and Neon's point-in-time restore covers the last 6 hours |
| `STOP: The restore drill failed with <error>. …` | Send the line to Claude. The function's logs (CloudWatch, `/aws/lambda/nettriage-dev-ops`) say more, without data |
| `STOP: Couldn't invoke nettriage-dev-ops: …` | Deploy Plan 7a first (B2). If it was deployed, send the output to Claude |
| A backup object's date is days old, or the newest is missing | The nightly backup failed. Send Claude the date of the newest backup; the logs in `/aws/lambda/nettriage-dev-ops` say why. A new Neon major version (Postgres 18) stops `pg_dump` until its client is updated |
| `STOP: Syncing reference data failed; nothing in AWS changed. …` | The migrations ran, but the detectors and ATT&CK techniques weren't loaded. Send the output to Claude |
| `FAIL  Grafana OTLP endpoint in terraform.tfvars` | Put your endpoint in `terraform.tfvars` (A3) |
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `analyze` | 2048 MB | 300 s | SQS `analyze`, batch size 1 | Event source mapping maximum concurrency 2 |
| `triage` | 512 MB | 240 s | SQS `triage`, batch size 1, partial batch responses | Event source mapping maximum concurrency 2 |
| `ops` | 256 MB | 60 s | EventBridge Scheduler | None needed |

- The SQS visibility timeout is 6 × the function timeout.
- The triage worker explains one finding per run, and 240 s fits its worst case: an answer and its repair, each a 20-second call with 3 retries (the owner's decision in Plan 5b; batch size 5 and 60 s couldn't fit one slow call).
- Worker concurrency is capped through the event source mapping's maximum concurrency, not reserved concurrency, because new accounts can have a low Lambda concurrency quota (see 13.2).
```
with:
```markdown
| `analyze` | 2048 MB | 300 s | SQS `analyze`, batch size 1 | Event source mapping maximum concurrency 2 |
| `triage` | 512 MB | 240 s | SQS `triage`, batch size 1, partial batch responses | Event source mapping maximum concurrency 2 |
| `ops` | 1024 MB, 2 GB of `/tmp` | 600 s | EventBridge Scheduler, five schedules (9.7) | None needed |

- The SQS visibility timeout is 6 × the function timeout.
- `ops` holds a database dump and its restore drill, which 256 MB and 60 s couldn't (Plan 7a). Its Postgres 17 client and server are a Lambda layer of their own, so the other functions' packages don't grow.
- The triage worker explains one finding per run, and 240 s fits its worst case: an answer and its repair, each a 20-second call with 3 retries (the owner's decision in Plan 5b; batch size 5 and 60 s couldn't fit one slow call).
- Worker concurrency is capped through the event source mapping's maximum concurrency, not reserved concurrency, because new accounts can have a low Lambda concurrency quota (see 13.2).
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `app_analyze` | `analyze` Lambda | SELECT `uploads`, `detectors`, `attack_techniques`; UPDATE of `uploads` status and statistics columns only; INSERT `findings`, `finding_evidence`, `finding_techniques`, `finding_events`; SELECT of a finding's `id`, `org_id`, `upload_id` and `severity`, to queue the most severe for AI triage (Plan 5b). No `audit_log` until the worker records an event worth auditing (Plan 4b) |
| `app_triage` | `triage` Lambda | SELECT `findings`, `finding_evidence`, `finding_techniques`, `attack_techniques`, `detectors`, `ai_analyses`; INSERT/UPDATE `ai_analyses` (column grants: never `feedback`); INSERT `finding_techniques`, UPDATE of their `rationale`, and DELETE of the AI's own (`source = 'ai'`, a restrictive row policy); INSERT `finding_events` without an actor; INSERT `audit_log`, for `budget.exhausted` (Plan 5b); INSERT and UPDATE of `ai_usage`'s counts (Plan 5c) |
| `app_ops` | `ops` Lambda | `SELECT 1` health checks; retention through `SECURITY DEFINER` functions only (purge `audit_log` rows older than 180 days, expire invitations, expire stale pending uploads) |
| `app_backup` | Nightly backup | Read-only with `BYPASSRLS` (needed for a complete dump); used only by the backup workflow |

- Column-level grants limit UPDATEs to the columns each role needs.
- Apart from the read-only backup role, no application role is a superuser, the schema owner, or has `BYPASSRLS`.
- A trigger on `audit_log` rejects UPDATE and DELETE except through the retention function.

### 5.5 DynamoDB `runtime` table
```
with:
```markdown
| `app_analyze` | `analyze` Lambda | SELECT `uploads`, `detectors`, `attack_techniques`; UPDATE of `uploads` status and statistics columns only; INSERT `findings`, `finding_evidence`, `finding_techniques`, `finding_events`; SELECT of a finding's `id`, `org_id`, `upload_id` and `severity`, to queue the most severe for AI triage (Plan 5b). No `audit_log` until the worker records an event worth auditing (Plan 4b) |
| `app_triage` | `triage` Lambda | SELECT `findings`, `finding_evidence`, `finding_techniques`, `attack_techniques`, `detectors`, `ai_analyses`; INSERT/UPDATE `ai_analyses` (column grants: never `feedback`); INSERT `finding_techniques`, UPDATE of their `rationale`, and DELETE of the AI's own (`source = 'ai'`, a restrictive row policy); INSERT `finding_events` without an actor; INSERT `audit_log`, for `budget.exhausted` (Plan 5b); INSERT and UPDATE of `ai_usage`'s counts (Plan 5c) |
| `app_ops` | `ops` Lambda | `SELECT 1` health checks, and the maintenance rules (9.7) through narrow grants and a row policy per rule: SELECT and UPDATE of `status`, `failure_reason` and `processed_at` on `uploads`, only of uploads still `pending_upload` or `processing` after 2 hours and only to `expired` or `failed`; SELECT and DELETE of `invitations` never accepted and past their date, and of `audit_log` rows over 180 days old. Not `SECURITY DEFINER` functions: every tenant table forces row-level security on its owner too, so a function running as the owner would see no rows (Plan 7a) |
| `app_backup` | `ops` Lambda's nightly backup | SELECT on every table and sequence, and a SELECT policy `USING (true)` on every row-secured table, so `pg_dump --enable-row-security` reads every row. Not `BYPASSRLS`: no role skips row-level security, and Neon's owner may not be able to grant it. A test fails if a table lacks the grant or the policy, so a new table can't drop out of the backups (Plan 7a) |

- Column-level grants limit UPDATEs to the columns each role needs.
- No application role, the backup's included, is a superuser, the schema owner, or has `BYPASSRLS` (Plan 7a).
- A trigger on `audit_log` rejects UPDATE and DELETE, except DELETE by `app_ops`, whose row policy limits it to rows over 180 days old (Plan 7a).

### 5.5 DynamoDB `runtime` table
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `nettriage-<stage>-web` | SPA assets; `demo/*.json` | Private; read only by CloudFront through OAC |
| `nettriage-<stage>-uploads-<suffix>` | `orgs/{org_id}/uploads/{upload_id}/raw` | Private, TLS-only, SSE-S3, public access blocked. CORS allows `PUT` from the app origin only. Lifecycle: delete after 30 days, abort incomplete multipart uploads after 1 day. Event notification → SQS `analyze` |
| `nettriage-backups-<suffix>` | `pg/<stage>/<date>.dump` | Private, SSE-S3, deleted after 7 days |
| `nettriage-tfstate-<suffix>` | Terraform state | Versioned, encrypted, TLS-only, native locking |

The uploads bucket's `<suffix>` is the first 8 hex characters of the account ID's SHA-256, like the sign-in domain's (Plan 4a): bucket names are global, and this one appears in every page's CSP header, so it can't hold the account ID.

### 5.7 Retention, quotas and storage budget
```
with:
```markdown
| `nettriage-<stage>-web` | SPA assets; `demo/*.json` | Private; read only by CloudFront through OAC |
| `nettriage-<stage>-uploads-<suffix>` | `orgs/{org_id}/uploads/{upload_id}/raw` | Private, TLS-only, SSE-S3, public access blocked. CORS allows `PUT` from the app origin only. Lifecycle: delete after 30 days, abort incomplete multipart uploads after 1 day. Event notification → SQS `analyze` |
| `nettriage-<stage>-backups-<suffix>` | `pg/<date>.dump`, and `pg/<date>.json`, its manifest | Private, TLS-only, SSE-S3, public access blocked. Lifecycle: delete after 7 days, abort incomplete multipart uploads after 1 day. One per stage, made by the stage's deploy, so the bootstrap doesn't change; only `ops` writes to it (Plan 7a) |
| `nettriage-tfstate-<suffix>` | Terraform state | Versioned, encrypted, TLS-only, native locking |

The uploads bucket's `<suffix>` is the first 8 hex characters of the account ID's SHA-256, like the sign-in domain's (Plan 4a): bucket names are global, and this one appears in every page's CSP header, so it can't hold the account ID. The backups bucket's `<suffix>` is the same (Plan 7a).

### 5.7 Retention, quotas and storage budget
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
- `nettriage.authz.denied` {permission}, `nettriage.ratelimit.limited` {policy}, `nettriage.csrf.failed` and `nettriage.upload.rejected` {reason}
- `nettriage.signups`
- `nettriage.probe.success` {check}

**Cardinality rule:** no user IDs, org IDs, IPs or free text as metric attributes. Grafana's free tier allows 10k active series.
```
with:
```markdown
- `nettriage.authz.denied` {permission}, `nettriage.ratelimit.limited` {policy}, `nettriage.csrf.failed` and `nettriage.upload.rejected` {reason}
- `nettriage.signups`
- `nettriage.probe.success` {check}, where `check` is `health`, `database` or `dynamodb` (Plan 7a)
- `nettriage.ops.runs` {job, outcome}, one per run of each `ops` job, so a backup or drill that failed, or didn't run, can be alerted on (Plan 7a)

**Cardinality rule:** no user IDs, org IDs, IPs or free text as metric attributes. Grafana's free tier allows 10k active series.
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
- **Budgets** alerts as in 6.7.
- **Runbooks:** every alert links to `docs/runbooks/<alert>.md`.
- **Probe:** the `ops` Lambda runs every 5 minutes and fetches the demo page and `/api/health` through CloudFront. Hourly, it also runs `SELECT 1` against Neon and a DynamoDB read. Frequent database checks would keep Neon awake and use up its free compute hours.

### 9.7 Operations

- **Kill switches:** `ai_enabled` and `uploads_enabled` live in SSM (`/nettriage/<stage>/kill/ai-enabled` and `…/uploads-enabled`, `true` or anything else) and are re-read every 60 seconds. The owner flips them with `just pause-ai` / `just resume-ai` and `just pause-uploads` / `just resume-uploads`. A switch that can't be read keeps its last value, and counts as off until it has been read once (Plan 4a). Terraform only creates them, so a deploy never turns a paused switch back on.
- **Maintenance:** the `ops` Lambda runs daily. It expires `pending_upload` rows older than 1 hour and invitations past their date, fails uploads left `processing` for more than 2 hours (a crash or timeout on the last delivery; Plan 4b), and purges audit rows older than 180 days.
- **Backups:**
  - A nightly `pg_dump -Fc` to S3, kept for 7 days. Plan 7 decides the runner: the `ops` Lambda, or an owner-run command if packaging `pg_dump` for Lambda proves impractical (Revision 2, D7).
  - Neon's 6-hour point-in-time restore on top of that.
  - Targets: RPO ≤ 24 h (≤ 6 h with point-in-time restore) and RTO ≤ 1 h.
  - One restore drill is performed and documented in M1.
- **Cost watch:**
  - Every resource is tagged.
```
with:
```markdown
- **Budgets** alerts as in 6.7.
- **Runbooks:** every alert links to `docs/runbooks/<alert>.md`.
- **Probe:** the `ops` Lambda fetches `/api/health` through CloudFront every 5 minutes; the health route never touches the database, so the probe never wakes Neon. The demo page joins the probe once it exists (Plan 6c). Hourly, at :17, it also runs `SELECT 1` against Neon as `app_ops` and reads one DynamoDB key. Frequent database checks would keep Neon awake and use up its free compute hours. A failed probe or check is a 0 in `nettriage.probe.success`, not an error, and isn't retried: the next one comes soon (Plan 7a).

### 9.7 Operations

- **Kill switches:** `ai_enabled` and `uploads_enabled` live in SSM (`/nettriage/<stage>/kill/ai-enabled` and `…/uploads-enabled`, `true` or anything else) and are re-read every 60 seconds. The owner flips them with `just pause-ai` / `just resume-ai` and `just pause-uploads` / `just resume-uploads`. A switch that can't be read keeps its last value, and counts as off until it has been read once (Plan 4a). Terraform only creates them, so a deploy never turns a paused switch back on.
- **Schedules:** EventBridge Scheduler invokes `ops` with the job's name, in UTC, through a role that may only invoke it (Plan 7a): `probe` every 5 minutes, `check` hourly at :17 (9.6), `backup` at 02:00, `cleanup` at 03:00, and `restore_drill` on Sundays at 04:00. `backup`, `cleanup` and `restore_drill` are idempotent and keep Lambda's two retries.
- **Maintenance:** the daily `cleanup`, as `app_ops`, each rule in its own transaction, recording counts only (Plan 7a). It expires `pending_upload` rows older than 2 hours, fails uploads left `processing` for more than 2 hours with the analyze worker's own sentence (a crash or timeout on the last delivery; Plan 4b), deletes invitations never accepted and past their date (accepted ones stay as history), and purges audit rows older than 180 days. Pending uploads wait 2 hours, not 1: an analysis message can be delivered three times, 30 minutes apart, and each delivery may claim an upload that is still pending, so at 1 hour a valid file could be refused on its third try (Plan 7a).
- **Backups:**
  - A nightly `pg_dump -Fc` to S3 by the `ops` Lambda, kept for 7 days (Revision 2, D7; Plan 7a). It connects as `app_backup` to Neon's direct endpoint, since `pg_dump` needs a session and the pooler works by transaction. In one `REPEATABLE READ` transaction it exports a snapshot and counts every table's rows, then dumps that same snapshot (`--snapshot`, `--enable-row-security`), so the dump and the counts see the same moment. It writes `pg/<date>.dump`, then `pg/<date>.json`, the manifest: `created_at`, the server's and `pg_dump`'s versions, and each table's count. A rerun the same day replaces both.
  - Its Postgres 17 client and server are built from the PostgreSQL project's source, pinned by checksum, in Amazon Linux 2023 for arm64, and proven in CI inside AWS's own Lambda image (Plan 7a). If Neon's server passes the client's major version, `pg_dump` refuses, the backup fails loudly, and the client is updated.
  - Neon's 6-hour point-in-time restore on top of that.
  - Targets: RPO ≤ 24 h (≤ 6 h with point-in-time restore) and RTO ≤ 1 h.
  - **Restore drill:** weekly, and on demand with the owner's `just restore-drill-<stage>`. The `ops` Lambda restores the newest dump into a throwaway Postgres 17 inside the function (in `/tmp`, on a Unix socket, with `mmap` shared memory, since Lambda has no `/dev/shm`), as a non-superuser owner with `pg_restore --no-owner --no-privileges --exit-on-error`, as a recovery into Neon would be. It then compares every table's count with the manifest. A difference or any error fails the drill, naming the tables, never their rows; nothing leaves AWS (Plan 7a).
  - One restore drill is performed and documented in M1: the owner's first `just restore-drill-dev` (runbook B14).
- **Cost watch:**
  - Every resource is tagged.
```

In `docs/superpowers/specs/2026-10-06-nettriage-ops-design.md`, replace:
```markdown
- **The Postgres client:**
  - a Lambda layer used only by `ops`, so the other functions' packages don't grow;
  - it holds Postgres 17's `pg_dump`, `pg_restore`, `initdb` and server, from the PostgreSQL project's own packages for EL9 arm64, with the libraries they need;
  - it's downloaded at build time with pinned SHA-256 checksums.
- **Schedules:** five EventBridge Scheduler schedules, through a role that may only invoke `ops`. `probe` and `check` aren't retried, because the next run comes soon. `backup`, `cleanup` and `restore_drill` keep Lambda's two retries; they're idempotent.
- **The backups bucket:**
```
with:
```markdown
- **The Postgres client:**
  - a Lambda layer used only by `ops`, so the other functions' packages don't grow;
  - it holds Postgres 17's `pg_dump`, `pg_restore`, `initdb`, `pg_ctl` and server, with `libpq`;
  - CI builds them from the PostgreSQL project's source, pinned by its SHA-256 checksum, in Amazon Linux 2023 for arm64, with only OpenSSL and zlib. The first task's spike found that the project's EL9 packages need libraries Lambda's image lacks (LDAP, ICU, PAM, systemd).
- **Schedules:** five EventBridge Scheduler schedules, through a role that may only invoke `ops`. `probe` and `check` aren't retried, because the next run comes soon. `backup`, `cleanup` and `restore_drill` keep Lambda's two retries; they're idempotent.
- **The backups bucket:**
```

- [ ] **Step 2: Check the docs**

Run: `grep -c "Plan 7a" docs/superpowers/specs/2026-09-26-nettriage-m1-design.md && grep -c "restore-drill-dev" docs/runbooks/setup-and-deploy.md README.md && ! grep -n "BYPASSRLS (needed\|pg/<stage>\|256 MB | 60 s" docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`
Expected: `15`, then `docs/runbooks/setup-and-deploy.md:2` and `README.md:1`, and nothing more: the old backup role, bucket layout and function size are gone, so the last `grep` finds nothing and the command succeeds.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-09-26-nettriage-m1-design.md docs/superpowers/specs/2026-10-06-nettriage-ops-design.md docs/runbooks/setup-and-deploy.md README.md
git commit -m "docs: the ops function in the M1 spec (§3.5, §5.4, §5.6, §9.2, §9.6, §9.7), the runbook's backups and restore drill (B14), and the README" -m "Also corrects the ops spec's layer: built from source, since the spike found the EL9 packages need libraries Lambda's image lacks." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 13: The whole branch, the PR and its CI

**Files:** none.

- [ ] **Step 1: Run every local check**

Run: `just lint tf-check pin-check cloud-check && uv run --project backend python -m pytest tools/tests && cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest --cov=nettriage.domain --cov=nettriage.application --cov-fail-under=85`
Expected: every check passes: `Success!` for every Terraform module, the actions pinned, no cloud access, `272 passed` for the tools, and `1154 passed, 1 skipped` for the backend.

- [ ] **Step 2: Push and open the PR**

Push `plan-7a/ops` and open a PR to `main` titled "Plan 7a: keeping it running (nightly backups checked by a weekly restore drill, a daily cleanup, probes)". The description lists the owner's steps after the merge (runbook B2, then B14 the next morning) and ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

- [ ] **Step 3: Watch CI**

Expected: every job is green, including:
- `pg-client`: the build, the layer's size (about 6 MB), and `verify.sh`'s round trip inside the Lambda image;
- `ops-in-lambda`: `check` answers `{"database": true, "dynamodb": true}`, `cleanup` counts at least the two seeded uploads and audit rows, and the drill's day, tables and rows equal the backup's;
- `terraform`: `fmt`, `validate`, the tests, tflint and Checkov.

A red job is a finding: find its cause, fix it test-first, and push again.

- [ ] **Step 4: Ask Copilot for a review**

Run: `gh pr edit <number> --add-reviewer @copilot`, then follow the review loop (`CLAUDE.md`): act only on Copilot's or the owner's comments, verify each against the code, fix the valid ones test-first, and never merge.
