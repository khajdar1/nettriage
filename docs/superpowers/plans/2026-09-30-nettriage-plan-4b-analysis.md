# NetTriage Plan 4b: Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn every uploaded file into findings:
- S3 announces each finished upload to an SQS `analyze` queue, with a dead-letter queue;
- an `analyze` worker Lambda streams the file, parses it, runs the detectors and stores their findings exactly once, then marks the upload `analyzed`, or `failed` with a readable reason;
- the `findings`, `finding_evidence`, `finding_techniques` and `finding_events` tables, the `detectors` and `attack_techniques` reference tables, and the worker's own database role, `app_analyze`;
- a read API: an org's findings with filters, one finding with its evidence, techniques and history, and ATT&CK techniques with MITRE's notice.

Triage (a finding's status and assignee with `If-Match`, and comments) is Plan 4c. The triage queue and AI explanations are Plan 5.

**Architecture:**
- The uploads bucket notifies the queue of every `ObjectCreated` under `orgs/…/raw`. The worker (batch size 1, at most 2 at once) reads the S3 event and claims the upload with one conditional update to `processing`. A key the API didn't make, or an upload that is already finished, is ignored.
- It streams the object from S3 into Plan 2's parser and detectors (`nettriage.domain`), then stores everything in one transaction with the upload row locked. A second delivery finds the upload finished and stores nothing.
- A file that isn't a flow log, breaks a limit or has another size than declared fails its upload. A database outage gets two quick retries. Anything else raises, so SQS delivers the message again: 3 times, then the dead-letter queue.
- The worker's span links to the upload request's trace through the object's `x-amz-meta-traceparent` (Plan 4a).
- Reference data (the detectors and a 12-technique subset of ATT&CK 19.2, committed as JSON) is upserted by the deploy right after the migrations.
- The API reads findings as `app_api` under row-level security.

**Tech Stack:** Python 3.14, SQLAlchemy 2 Core with psycopg 3 (Neon Postgres, row-level security), boto3 (S3, SSM), FastAPI, OpenTelemetry · moto for S3 in tests · Terraform (SQS, S3 notifications, Lambda, IAM).

**Spec:** `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (revision 2). Read it alongside this plan; section numbers (§) refer to it. This plan implements:
- §4.2's second half (S3 event → `analyze` → findings → upload `analyzed`);
- §3.2's `analyze` queue and DLQ, and §3.5's `analyze` Lambda defaults;
- §5.2's `detectors`, `attack_techniques`, `findings`, `finding_evidence`, `finding_techniques` and `finding_events` tables and their indexes, with §5.3's row-level security and §5.4's grants for `app_analyze` (and `app_api`'s reads);
- §5.6's event notification to SQS `analyze`;
- §6.8's `analyze` role and database URL;
- §7's findings list and detail, and `GET /attack-techniques/{id}`;
- §8.1's limits and §8.6's failure handling, in the worker;
- §9.1's worker spans and span links, and §9.2's pipeline metrics;
- §11.9's MITRE attribution.

**Plan series:** Plan 4 of 7 ("upload pipeline") is split in three, as the owner chose on 2026-09-29:
- **4a: upload intake** (merged and deployed);
- **4b (this plan): analysis;**
- **4c: triage:** a finding's status and assignee with version checks (`ETag`/`If-Match`), comments and history.

**Branch:** `plan-4b/analysis`, from `main` at `490a388` or later.

## Global Constraints

- **Stack.** Python **3.14**. No new dependencies. mypy `--strict` and Ruff pass on `src` and `tests`. Work test-first.
- **Commands on this Windows machine.** Run Python tools as modules (`uv run python -m …`). Host policy blocks some uv launchers and every unsigned executable outside trusted tools. Backend tests need the local Postgres, which `just test` starts.
- **Region.** The queues and the worker live in **eu-north-1** (Revision 2, R1).
- **The worker (§3.5).** `analyze`: **2048 MB**, **300 s**, triggered by SQS `analyze` with **batch size 1** and event source mapping **maximum concurrency 2**. The visibility timeout is **6 × the function timeout** (1,800 s). `maxReceiveCount` is **3** (§3.2).
- **Parsing limits (§8.1).** 250 MB decompressed, 2,000,000 rows, 4 KB per line; more than 5% invalid lines fails the upload with `not_a_flow_log`. Plan 2's parser enforces them; this plan passes its errors on.
- **Duplicates (§8.6).** A second delivery of an event is ignored through state checks and unique keys: `UNIQUE (org_id, upload_id, fingerprint)`.
- **Least privilege (§5.4, §6.8).** `app_analyze` has column-level grants and no `BYPASSRLS`. The worker's IAM role may get objects under `orgs/`, consume its queue and read its own database URL, and nothing else.
- **Logs (§9.3).** Never log a line of an uploaded file, or its name. The worker logs event codes and counts.
- **ATT&CK (§5.2, §11.9).** Version **19.2**; MITRE's copyright notice travels with the data.
- **Owner-only commands.** Claude never runs `aws login`, `just store-*`, `just pause-uploads`, `just resume-uploads` or `just deploy-*`.
- **Commits.** Use Conventional Commits. Every commit made by Claude ends with the trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Decisions this plan makes

1. **ATT&CK ships as a committed subset.**
   - `tools/attack_subset.py` extracts the techniques the detectors can name, plus their parents (12 in all), from MITRE's STIX bundle for 19.2 into `backend/src/nettriage/reference/attack_techniques.json`, with MITRE's notice.
   - Nothing downloads ATT&CK at build or deploy time. A later version is a reviewed change to the JSON.
   - Citation markers are stripped from descriptions, because the reference list they point into isn't kept.
2. **Reference data is synced by the deploy, right after the migrations,** as the schema owner (`python -m nettriage.adapters.reference_data`, all upserts). The deploy prints its one-line summary. The test database is synced the same way.
3. **`app_analyze` gets exactly what the worker does** (this amends §5.4):
   - SELECT `uploads`, `detectors` and `attack_techniques`;
   - UPDATE of the upload's status and statistics columns;
   - INSERT on findings, their evidence, techniques and events, with column grants where a table has columns the worker must not set (a finding's status, assignee and version; an event's actor).
   - It gets no SELECT on findings and no `audit_log` until the worker records an event worth auditing.
   - The deploy gives it a login like `app_api` (`APP_DB_ROLES`), stored at `/nettriage/<stage>/db/app-analyze-url`.
4. **The worker claims an upload with one conditional update.** The row must match the key's org, ID and S3 key, and be `pending_upload` or `processing`. `processing` is claimable again, so SQS can retry a delivery that crashed or timed out (this amends §8.6's row on unknown objects).
5. **An analysis is stored in one transaction, with the upload row locked.**
   - If the upload is no longer `processing`, another delivery finished it, and nothing is written (`duplicate`).
   - Each finding gets its evidence, its detector's techniques (`source = 'detector'`) and a `created` event with no actor.
6. **What fails an upload, and what SQS retries:**
   - A `FlowLogError` from the parser (not a flow log, a limit), or a file whose size differs from the declared size, marks the upload `failed` with a readable reason and counts `nettriage.upload.rejected` by code. S3 already verified the checksum at PUT time (Plan 4a), so the worker doesn't hash the file again.
   - A database outage gets two retries, after 1 and 3 seconds (Neon waking or restarting takes seconds; SQS would retry only after 30 minutes). This amends §8.6's "retry with backoff".
   - Anything else raises: SQS delivers the message again, 3 times, then the DLQ keeps it for 14 days.
7. **The worker's telemetry.**
   - Its span for one file, `analyze.upload`, *links* to the upload request's trace (read from the object's metadata) rather than continuing it: the request finished long before.
   - `analyze.parse` and `analyze.detect` sit inside it.
   - The metrics are §9.2's. The handler flushes telemetry at the end of every invocation, even a failed one.
8. **The worker is a plain Lambda handler in the API's package** (`nettriage.entrypoints.analyze.handler.handle`).
   - It has no web adapter, and only the OpenTelemetry collector layer.
   - Its infrastructure lives in the `pipeline` module (§11.7's module list), next to the bucket.
   - SSM reading moves to `nettriage.adapters.parameters`, shared by both entry points.
9. **Findings are read as `app_api` under row-level security.**
   - The list is newest first, a page at a time (default 50, at most 100). It filters by `status`, `severity`, `detector` and `upload`.
   - The detail adds the metrics, the evidence (by time), the techniques (with their names) and the events. Its `ETag` is `"<version>"`, for Plan 4c's `If-Match`.
   - `GET /attack-techniques/{id}` is open to anyone signed in. An ID that isn't `T` plus 4 digits (and optionally `.` plus 3) is a 404 without a database call.
10. **The bucket notifies only `ObjectCreated` under `orgs/` ending in `/raw`.**
    - The queue policy accepts only this bucket, in this account (`aws:SourceArn`, `aws:SourceAccount`).
    - S3's `s3:TestEvent`, sent when the notification is set up, is skipped.
    - Both queues use SQS-managed encryption.
11. **The preflight also probes SQS in eu-north-1**, like the other services a deploy needs.

## Review Focus

1. **The same upload event delivered twice, or two deliveries finishing at once.** Exactly one set of findings may be stored, and the upload's statistics must be written once. Tests: Task 4 `test_two_deliveries_finishing_at_once_store_one_set_of_findings`, Task 5 `test_a_second_delivery_of_the_same_event_changes_nothing`.
2. **A hostile or wrong file: a gzip bomb, an HTML page, or a file of another size than declared.** The upload must fail with a readable reason, memory must stay bounded, and nothing else may be stored. Tests: Task 3 `test_a_file_that_inflates_past_the_limit_fails_early`; Task 5 `test_a_zip_bomb_fails_early_instead_of_filling_memory`, `test_a_file_that_is_not_a_flow_log_fails_its_upload_with_a_reason` and `test_a_file_of_another_size_than_declared_fails`.
3. **Neon asleep, restarting or unreachable while a message is handled.** The upload must not be lost or stuck: a quick retry, then SQS delivers again, and a `processing` upload can be claimed again. Tests: Task 5 `test_a_brief_database_outage_is_retried` and `test_a_longer_database_outage_raises_so_sqs_delivers_the_message_again`; Task 4 `test_a_retry_after_a_crash_can_claim_it_again`.
4. **A finding read through another org's URL or by a stranger, or rows linked across orgs.** The API must answer 404 with no data, and the database must refuse a cross-org link. Tests: Task 6 `test_a_finding_is_not_found_through_another_org_or_by_a_stranger`; Task 2 `test_a_finding_can_not_point_at_another_orgs_upload`, `test_evidence_can_not_point_at_another_orgs_finding` and the tenant-isolation suite over the four new tables.
5. **An object in the bucket that the API didn't hand out,** such as a copy under another key, or a key naming one org with another org's upload ID. It must be ignored, never analyzed into any org. Tests: Task 3 `test_any_other_key_is_not_an_upload`; Task 4 `test_a_key_that_doesnt_match_its_upload_is_not_claimed`; Task 5 `test_objects_that_are_not_uploads_and_s3s_test_event_are_ignored`.

## Owner prerequisites

- **Nothing is needed to build or review this plan.** Tests use the local Postgres and moto. Task 1 downloads MITRE's public ATT&CK bundle once (53 MB, from GitHub).
- **Nothing new is needed before the deploy.** The deploy applies migration `0006`, syncs the reference data, gives `app_analyze` a login and stores its URL in SSM, then Terraform creates the queues and the worker.
- **To try it afterwards** (runbook B7, which Task 8 adds), you only need to be signed in to dev.

## File map

| File | Responsibility | Task |
|---|---|---|
| `tools/attack_subset.py` | extracts the detectors' ATT&CK techniques from MITRE's STIX bundle | 1 |
| `src/nettriage/reference/` | the committed ATT&CK subset, and its loader | 1 |
| `migrations/versions/0006_findings.py` | reference and finding tables, RLS, `app_analyze` and its grants | 2 |
| `src/nettriage/adapters/reference_data.py` | upserts the detectors and techniques; the deploy runs it | 2 |
| `tools/deploy/` | syncs reference data after migrating; `app_analyze`'s login; the SQS probe | 2, 7 |
| `src/nettriage/application/analysis.py` | S3 keys to uploads; parse and detect into an `Analysis` | 3 |
| `src/nettriage/adapters/analysis_store.py` | claims an upload, stores its analysis once, or fails it | 4 |
| `src/nettriage/adapters/upload_objects.py` | streams an upload from S3, with its size and traceparent | 5 |
| `src/nettriage/adapters/parameters.py` | SSM reads shared by the API and the worker (moved) | 5 |
| `src/nettriage/entrypoints/analyze/` | the worker, its wiring and its Lambda handler | 5 |
| `src/nettriage/adapters/findings.py`, `adapters/attack_techniques.py` | reading findings and techniques as `app_api` | 6 |
| `src/nettriage/entrypoints/api/finding_schemas.py`, `routes/findings.py`, `routes/attack_techniques.py` | the read API | 6 |
| `infra/modules/pipeline/queues.tf`, `analyze.tf` | the queues, the notification, and the worker's function, role and trigger | 7 |

Paths under `src/` and `migrations/` are in `backend/`.

---

### Task 1: ATT&CK reference data

**Files:**
- Create: `tools/attack_subset.py`, `backend/src/nettriage/reference/__init__.py`, `backend/src/nettriage/reference/attack_techniques.json` (generated by the tool)
- Test: `tools/tests/test_attack_subset.py`, `backend/tests/unit/application/test_attack_reference.py`

**Interfaces:**
- Consumes (Plan 2): `nettriage.domain.detection.engine.DETECTORS`; each detector has `id`, `name`, `description`, `version` and `candidate_techniques`.
- Produces:
  - In `tools.attack_subset`: `ATTACK_VERSION = "19.2"`, `SOURCE`, `OUT`, `SubsetError`, `wanted_ids() -> set[str]`, `extract(bundle, wanted, version=ATTACK_VERSION) -> dict` and `main(argv=None) -> int`.
  - In `nettriage.reference`:
    - `AttackTechnique(id, stix_id, name, tactics: tuple[str, ...], description, url, is_subtechnique, parent_id: str | None, deprecated)`;
    - `AttackReference(version, notice, techniques: tuple[AttackTechnique, ...])`;
    - `attack_reference() -> AttackReference`, cached, which reads the committed JSON.

- [ ] **Step 1: Write the failing tests**

`tools/tests/test_attack_subset.py`:
```python
"""Extracting the detectors' ATT&CK techniques from MITRE's STIX bundle (spec §5.2)."""

from typing import Any

import pytest

from tools.attack_subset import SubsetError, extract, wanted_ids

NOTICE = "Copyright 2015-2026, The MITRE Corporation."


def technique(external_id: str, name: str, **extra: Any) -> dict[str, Any]:
    return {
        "type": "attack-pattern",
        "id": f"attack-pattern--{external_id}",
        "name": name,
        "description": "Adversaries may scan.(Citation: Some Report)  More text.(Citation: Other)",
        "kill_chain_phases": [{"kill_chain_name": "mitre-attack", "phase_name": "reconnaissance"}],
        "external_references": [
            {
                "source_name": "mitre-attack",
                "external_id": external_id,
                "url": f"https://attack.mitre.org/techniques/{external_id.replace('.', '/')}",
            }
        ],
        "x_mitre_is_subtechnique": "." in external_id,
        **extra,
    }


def bundle(*objects: dict[str, Any], version: str = "19.2") -> dict[str, Any]:
    return {
        "objects": [
            {"type": "x-mitre-collection", "x_mitre_version": version},
            {"type": "marking-definition", "definition_type": "statement",
             "definition": {"statement": NOTICE}},
            *objects,
        ]
    }


def test_the_wanted_techniques_are_the_detectors_candidates_and_their_parents() -> None:
    wanted = wanted_ids()

    assert {"T1595", "T1595.001", "T1046", "T1110.003", "T1021", "T1021.004", "T1567"} <= wanted
    assert all(technique.split(".")[0] in wanted for technique in wanted)


def test_a_technique_keeps_its_names_tactics_and_link_without_citations() -> None:
    subset = extract(
        bundle(technique("T1595", "Active Scanning"), technique("T1595.001", "Scanning IP Blocks")),
        {"T1595", "T1595.001"},
    )

    assert (subset["attack_version"], subset["notice"]) == ("19.2", NOTICE)
    first, sub = subset["techniques"]
    assert (first["id"], first["name"], first["tactics"]) == (
        "T1595",
        "Active Scanning",
        ["reconnaissance"],
    )
    assert first["description"] == "Adversaries may scan. More text."
    assert (first["is_subtechnique"], first["parent_id"]) == (False, None)
    assert (sub["is_subtechnique"], sub["parent_id"]) == (True, "T1595")
    assert sub["url"] == "https://attack.mitre.org/techniques/T1595/001"


def test_another_attack_version_is_refused() -> None:
    with pytest.raises(SubsetError, match="expected ATT&CK 19.2"):
        extract(bundle(technique("T1595", "Active Scanning"), version="18.1"), {"T1595"})


def test_a_missing_or_revoked_technique_is_refused() -> None:
    revoked = technique("T1046", "Network Service Discovery", revoked=True)

    with pytest.raises(SubsetError, match="no current technique T1046"):
        extract(bundle(technique("T1595", "Active Scanning"), revoked), {"T1595", "T1046"})
```

`backend/tests/unit/application/test_attack_reference.py`:
```python
"""The shipped ATT&CK reference data (spec §5.2): every technique a detector can name, from
ATT&CK 19.2, with MITRE's notice."""

import re

from nettriage.domain.detection.engine import DETECTORS
from nettriage.reference import attack_reference

TECHNIQUE_ID = re.compile(r"T\d{4}(\.\d{3})?")


def test_it_is_attack_19_2_with_mitres_notice() -> None:
    reference = attack_reference()

    assert reference.version == "19.2"
    assert reference.notice.startswith("Copyright 2015-2026, The MITRE Corporation.")


def test_every_technique_a_detector_can_name_is_there_with_its_parent() -> None:
    known = {technique.id: technique for technique in attack_reference().techniques}
    named = {technique for detector in DETECTORS for technique in detector.candidate_techniques}

    assert named <= known.keys()
    for technique in known.values():
        assert TECHNIQUE_ID.fullmatch(technique.id)
        path = technique.id.replace(".", "/")
        assert technique.url == f"https://attack.mitre.org/techniques/{path}"
        assert technique.tactics
        assert "(Citation:" not in technique.description
        if technique.is_subtechnique:
            assert technique.parent_id == technique.id.split(".")[0]
            assert technique.parent_id in known
```

- [ ] **Step 2: Run them and watch them fail**

Run: `just tools-test`
Expected: FAIL. Collection stops with 1 error: `No module named 'tools.attack_subset'`.

Run: `cd backend && uv run python -m pytest tests/unit/application/test_attack_reference.py`
Expected: FAIL. Collection stops with 1 error: `No module named 'nettriage.reference'`.

- [ ] **Step 3: Write the extractor and the loader**

`tools/attack_subset.py`:
```python
"""Write the ATT&CK techniques NetTriage's detectors can name (spec §5.2) from MITRE's STIX
bundle into backend/src/nettriage/reference/attack_techniques.json, with MITRE's notice.

Download the bundle once, then run the tool on it:

  curl -fLO https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/enterprise-attack/enterprise-attack-19.2.json
  uv run --project backend python -m tools.attack_subset enterprise-attack-19.2.json

The output is committed; nothing downloads ATT&CK at build or deploy time.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ATTACK_VERSION = "19.2"
SOURCE = (
    "https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/"
    f"enterprise-attack/enterprise-attack-{ATTACK_VERSION}.json"
)
OUT = (
    Path(__file__).resolve().parents[1]
    / "backend" / "src" / "nettriage" / "reference" / "attack_techniques.json"
)
CITATION = re.compile(r"\(Citation: [^)]*\)")


class SubsetError(Exception):
    """The bundle isn't the expected ATT&CK version, or lacks a technique a detector names."""


def wanted_ids() -> set[str]:
    """Every technique a detector can name, and the parent of each sub-technique."""
    from nettriage.domain.detection.engine import DETECTORS

    ids = {technique for detector in DETECTORS for technique in detector.candidate_techniques}
    return ids | {technique.split(".")[0] for technique in ids}


def extract(bundle: dict[str, Any], wanted: set[str], version: str = ATTACK_VERSION) -> dict[str, Any]:
    objects = bundle["objects"]
    versions = [o.get("x_mitre_version") for o in objects if o["type"] == "x-mitre-collection"]
    if versions != [version]:
        raise SubsetError(f"expected ATT&CK {version}, the bundle is {versions or 'unversioned'}")
    notices = [
        o["definition"]["statement"]
        for o in objects
        if o["type"] == "marking-definition" and o.get("definition_type") == "statement"
    ]
    if len(notices) != 1:
        raise SubsetError(f"expected one copyright statement, found {len(notices)}")
    found: dict[str, dict[str, Any]] = {}
    for obj in objects:
        if obj["type"] != "attack-pattern" or obj.get("revoked"):
            continue
        reference = next(
            (r for r in obj.get("external_references", []) if r.get("source_name") == "mitre-attack"),
            None,
        )
        if reference is not None and reference["external_id"] in wanted:
            found[reference["external_id"]] = _technique(obj, reference)
    missing = sorted(wanted - found.keys())
    if missing:
        raise SubsetError(f"ATT&CK {version} has no current technique {', '.join(missing)}")
    return {
        "attack_version": version,
        "source": SOURCE,
        "notice": notices[0],
        "techniques": [found[technique] for technique in sorted(found)],
    }


def _technique(obj: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    technique_id: str = reference["external_id"]
    subtechnique = bool(obj.get("x_mitre_is_subtechnique"))
    return {
        "id": technique_id,
        "stix_id": obj["id"],
        "name": obj["name"],
        "tactics": [
            phase["phase_name"]
            for phase in obj.get("kill_chain_phases", [])
            if phase.get("kill_chain_name") == "mitre-attack"
        ],
        "description": _clean(obj.get("description", "")),
        "url": reference["url"],
        "is_subtechnique": subtechnique,
        "parent_id": technique_id.split(".")[0] if subtechnique else None,
        "deprecated": bool(obj.get("x_mitre_deprecated")),
    }


def _clean(description: str) -> str:
    """Without MITRE's citation markers, which point into a reference list we don't keep."""
    return re.sub(r"[ \t]+", " ", CITATION.sub("", description)).strip()


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print(__doc__)
        return 2
    bundle = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    try:
        subset = extract(bundle, wanted_ids())
    except SubsetError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 1
    OUT.write_text(
        json.dumps(subset, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"Wrote {len(subset['techniques'])} ATT&CK {subset['attack_version']} techniques to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`backend/src/nettriage/reference/__init__.py`:
```python
"""Reference data that ships with the code (spec §5.2): the ATT&CK techniques the detectors can
name, taken from MITRE's ATT&CK STIX bundle by tools/attack_subset.py, with MITRE's notice."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib.resources import files


@dataclass(frozen=True)
class AttackTechnique:
    id: str
    stix_id: str
    name: str
    tactics: tuple[str, ...]
    description: str
    url: str
    is_subtechnique: bool
    parent_id: str | None
    deprecated: bool


@dataclass(frozen=True)
class AttackReference:
    version: str
    notice: str
    techniques: tuple[AttackTechnique, ...]


@cache
def attack_reference() -> AttackReference:
    raw = json.loads(files(__name__).joinpath("attack_techniques.json").read_text(encoding="utf-8"))
    return AttackReference(
        version=raw["attack_version"],
        notice=raw["notice"],
        techniques=tuple(
            AttackTechnique(
                id=item["id"],
                stix_id=item["stix_id"],
                name=item["name"],
                tactics=tuple(item["tactics"]),
                description=item["description"],
                url=item["url"],
                is_subtechnique=item["is_subtechnique"],
                parent_id=item["parent_id"],
                deprecated=item["deprecated"],
            )
            for item in raw["techniques"]
        ),
    )
```

- [ ] **Step 4: Generate the subset from MITRE's bundle**

Download ATT&CK 19.2 to a temporary directory (outside the repository), check it, and run the tool on it:
```bash
tmp="$(mktemp -d)"
curl -fsSL -o "$tmp/enterprise-attack-19.2.json" https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/enterprise-attack/enterprise-attack-19.2.json
sha256sum "$tmp/enterprise-attack-19.2.json"
uv run --project backend python -m tools.attack_subset "$tmp/enterprise-attack-19.2.json"
sha256sum backend/src/nettriage/reference/attack_techniques.json
rm -r "$tmp"
```
Expected:
- the bundle's hash is `dc1639caa5501d720e280cf1cbd8fbe009884a0c9b3e6e9ed9d0c25166c3d8f4`;
- `Wrote 12 ATT&CK 19.2 techniques to …attack_techniques.json`;
- the subset's hash is `255a327aa3048598b73ba6fe6f157b916a46e1bd93d73f967780429d0f73b203`.

If the bundle's hash differs, MITRE changed the file: stop and report it rather than committing different data.

- [ ] **Step 5: Run the checks**

Run: `just lint test tools-test`
Expected: lint is clean; backend `628 passed, 1 skipped`; tools `228 passed`.

- [ ] **Step 6: Commit**

```bash
git add tools/attack_subset.py tools/tests/test_attack_subset.py backend/src/nettriage/reference backend/tests/unit/application/test_attack_reference.py
git commit -m "feat(detection): ATT&CK 19.2 techniques for the detectors, extracted from MITRE's bundle with its notice" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The findings schema, `app_analyze` and reference data at deploy

**Files:**
- Create: `backend/migrations/versions/0006_findings.py`, `backend/src/nettriage/adapters/reference_data.py`
- Modify: `tools/deploy/config.py`, `tools/deploy/database.py`, `tools/deploy/__main__.py`
- Modify (test harness): `backend/tests/conftest.py` (sync the reference data; an `app_analyze` engine), `backend/tests/tenantdata.py` (`add_finding`; every seeded tenant gets an analyzed upload with a finding)
- Test: `backend/tests/integration/test_findings_schema.py`, `backend/tests/integration/test_tenant_isolation.py`, `backend/tests/integration/test_migrations.py`, `tools/tests/test_deploy_cli.py`, `tools/tests/test_deploy_database.py`

**Interfaces:**
- Consumes: Task 1's `attack_reference()`; Plan 2's `DETECTORS`; Plan 4a's `uploads` table, `add_upload(connection, org_id, uploaded_by, status="pending_upload")` and the deploy's `ensure_role_logins`, which gives every role in `APP_DB_ROLES` a login and stores its URL at `/nettriage/<stage>/db/<role>-url`.
- Produces:
  - Tables `detectors`, `attack_techniques`, `findings`, `finding_evidence`, `finding_techniques` and `finding_events`, as in §5.2. The four finding tables have row-level security; `findings` has `UNIQUE (org_id, upload_id, fingerprint)` and `UNIQUE (org_id, id)`.
  - Role `app_analyze` (NOLOGIN until the deploy gives it one) with Decision 3's grants. `app_api` gets SELECT on all six tables.
  - `nettriage.adapters.reference_data.sync_reference_data(engine) -> tuple[int, int]` (detectors, techniques), and `python -m nettriage.adapters.reference_data`, which reads `NETTRIAGE_MIGRATION_DATABASE_URL` and prints `Reference data synced: 3 detectors, 12 ATT&CK techniques.`
  - In `tools.deploy`: `config.APP_DB_ROLES = ("app_api", "app_analyze")` and `database.sync_reference_data(run, env, owner_url) -> str`.
  - Test harness: `Database.app_analyze` (an engine as `app_analyze`); `add_finding(connection, org_id, upload_id, severity="high") -> UUID`, a port-scan finding with one evidence row, technique T1595 and a `created` event; `Tenant.finding_id`.

- [ ] **Step 1: Write the failing tests**

The harness syncs the reference data and connects as the worker's role:

In `backend/tests/conftest.py`, replace:
```python
from nettriage.adapters.rate_limiter import RateLimiter
from nettriage.adapters.sessions import SessionStore
```
with:
```python
from nettriage.adapters.rate_limiter import RateLimiter
from nettriage.adapters.reference_data import sync_reference_data
from nettriage.adapters.sessions import SessionStore
```

In `backend/tests/conftest.py`, replace:
```python
APP_API_PASSWORD = "app-api-test-only"  # noqa: S105 - a throwaway password on a test server

```
with:
```python
APP_API_PASSWORD = "app-api-test-only"  # noqa: S105 - a throwaway password on a test server
APP_ANALYZE_PASSWORD = "app-analyze-test-only"  # noqa: S105 - the same, for the worker's role

```

In `backend/tests/conftest.py`, replace:
```python
class Database:
    """A database with every migration applied. `admin` is a superuser engine that seeds data
    past row-level security; `app_api` connects as the API's role."""

```
with:
```python
class Database:
    """A database with every migration applied and the reference data synced. `admin` is a
    superuser engine that seeds data past row-level security; `app_api` connects as the API's
    role and `app_analyze` as the analyze worker's."""

```

In `backend/tests/conftest.py`, replace:
```python
    app_api: Engine

```
with:
```python
    app_api: Engine
    app_analyze: Engine

```

In `backend/tests/conftest.py`, replace:
```python
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f"ALTER ROLE app_api WITH LOGIN PASSWORD '{APP_API_PASSWORD}'"))
    app_url = url.set(username="app_api", password=APP_API_PASSWORD)
    app_api = create_database_engine(app_url.render_as_string(hide_password=False), pool_size=1)
    yield Database(url=url, admin=admin, app_api=app_api)
    app_api.dispose()
```
with:
```python
    admin = create_engine(url)
    sync_reference_data(admin)
    with admin.begin() as connection:
        connection.execute(text(f"ALTER ROLE app_api WITH LOGIN PASSWORD '{APP_API_PASSWORD}'"))
        connection.execute(
            text(f"ALTER ROLE app_analyze WITH LOGIN PASSWORD '{APP_ANALYZE_PASSWORD}'")
        )
    app_url = url.set(username="app_api", password=APP_API_PASSWORD)
    app_api = create_database_engine(app_url.render_as_string(hide_password=False), pool_size=1)
    analyze_url = url.set(username="app_analyze", password=APP_ANALYZE_PASSWORD)
    app_analyze = create_database_engine(
        analyze_url.render_as_string(hide_password=False), pool_size=1
    )
    yield Database(url=url, admin=admin, app_api=app_api, app_analyze=app_analyze)
    app_analyze.dispose()
    app_api.dispose()
```

Every seeded tenant now has an analyzed upload with a finding:

In `backend/tests/tenantdata.py`, replace:
```python
"""Seed users, organizations, memberships, invitations and uploads for integration tests.
Seeding uses the superuser engine, which row-level security doesn't apply to."""

```
with:
```python
"""Seed users, organizations, memberships, invitations, uploads and findings for integration
tests. Seeding uses the superuser engine, which row-level security doesn't apply to."""

```

In `backend/tests/tenantdata.py`, replace:
```python
    upload_id: UUID

```
with:
```python
    upload_id: UUID
    finding_id: UUID

```

In `backend/tests/tenantdata.py`, replace:
```python

def add_tenant(admin: Engine) -> Tenant:
    """An org with an owner, a pending invitation and an upload."""
    with admin.begin() as connection:
```
with:
```python

def add_finding(
    connection: Connection, org_id: UUID, upload_id: UUID, severity: str = "high"
) -> UUID:
    """A port-scan finding with one evidence row, its detector technique and its created event."""
    finding_id = uuid7()
    connection.execute(
        text(
            "INSERT INTO findings (id, org_id, upload_id, detector_id, detector_version, "
            "fingerprint, severity, title, src_ip, dst_ip, time_window) VALUES (:id, :org, "
            ":upload, 'port_scan', 1, :fingerprint, :severity, 'Port scan from 203.0.113.9', "
            "'203.0.113.9', '10.0.0.5', tstzrange('2026-09-28 12:00+00', '2026-09-28 12:05+00'))"
        ),
        {
            "id": finding_id,
            "org": org_id,
            "upload": upload_id,
            "fingerprint": finding_id.hex * 2,
            "severity": severity,
        },
    )
    connection.execute(
        text(
            "INSERT INTO finding_evidence (id, org_id, finding_id, src_ip, dst_ip, src_port, "
            "dst_port, protocol, packets, bytes, start_ts, end_ts, action, line_no) VALUES "
            "(:id, :org, :finding, '203.0.113.9', '10.0.0.5', 40000, 22, 6, 1, 40, "
            "'2026-09-28 12:00+00', '2026-09-28 12:00+00', 'REJECT', 2)"
        ),
        {"id": uuid7(), "org": org_id, "finding": finding_id},
    )
    connection.execute(
        text(
            "INSERT INTO finding_techniques (finding_id, technique_id, source, org_id) "
            "VALUES (:finding, 'T1595', 'detector', :org)"
        ),
        {"finding": finding_id, "org": org_id},
    )
    connection.execute(
        text(
            "INSERT INTO finding_events (id, org_id, finding_id, type) "
            "VALUES (:id, :org, :finding, 'created')"
        ),
        {"id": uuid7(), "org": org_id, "finding": finding_id},
    )
    return finding_id


def add_tenant(admin: Engine) -> Tenant:
    """An org with an owner, a pending invitation, and an analyzed upload with a finding."""
    with admin.begin() as connection:
```

In `backend/tests/tenantdata.py`, replace:
```python
        invitation = add_invitation(connection, org, owner, f"invitee-{org.hex}@example.com")
        upload = add_upload(connection, org, owner)
    return Tenant(org_id=org, owner_id=owner, invitation_id=invitation, upload_id=upload)

```
with:
```python
        invitation = add_invitation(connection, org, owner, f"invitee-{org.hex}@example.com")
        upload = add_upload(connection, org, owner, "analyzed")
        finding = add_finding(connection, org, upload)
    return Tenant(
        org_id=org, owner_id=owner, invitation_id=invitation, upload_id=upload, finding_id=finding
    )

```

The isolation suite covers the four finding tables:

In `backend/tests/integration/test_tenant_isolation.py`, replace:
```python

TENANT_TABLES = ("organizations", "memberships", "invitations", "audit_log", "uploads")
INSERT_AUDIT_EVENT = text(
```
with:
```python

TENANT_TABLES = (
    "organizations",
    "memberships",
    "invitations",
    "audit_log",
    "uploads",
    "findings",
    "finding_evidence",
    "finding_techniques",
    "finding_events",
)
INSERT_AUDIT_EVENT = text(
```

In `backend/tests/integration/test_tenant_isolation.py`, replace:
```python
            table: org_ids(connection, table)
            for table in ("organizations", "memberships", "invitations", "uploads")
        }
```
with:
```python
            table: org_ids(connection, table)
            for table in (
                "organizations",
                "memberships",
                "invitations",
                "uploads",
                "findings",
                "finding_evidence",
                "finding_techniques",
                "finding_events",
            )
        }
```

In `backend/tests/integration/test_tenant_isolation.py`, replace:
```python
        "'analyzed' FROM uploads",
        "CREATE TEMP TABLE shadow (id int)",
```
with:
```python
        "'analyzed' FROM uploads",
        "INSERT INTO findings (id, org_id, upload_id, detector_id, detector_version, "
        "fingerprint, severity, title, src_ip, time_window) SELECT gen_random_uuid(), org_id, "
        "upload_id, detector_id, 1, repeat('1', 64), severity, title, src_ip, time_window "
        "FROM findings",
        "UPDATE findings SET severity = 'low'",
        "DELETE FROM findings",
        "UPDATE detectors SET version = 99",
        "INSERT INTO attack_techniques (id, stix_id, name, tactics, description, url, "
        "attack_version, is_subtechnique) VALUES ('T9999', 'x', 'x', '{}', 'x', "
        "'https://attack.mitre.org/techniques/T9999', '19.2', false)",
        "CREATE TEMP TABLE shadow (id int)",
```

In `backend/tests/integration/test_migrations.py`, replace:
```python

TABLES = {"users", "organizations", "memberships", "invitations", "audit_log", "uploads"}

```
with:
```python

TABLES = {
    "users",
    "organizations",
    "memberships",
    "invitations",
    "audit_log",
    "uploads",
    "detectors",
    "attack_techniques",
    "findings",
    "finding_evidence",
    "finding_techniques",
    "finding_events",
}

```

`backend/tests/integration/test_findings_schema.py`:
```python
"""Findings in Postgres (spec §5.2 to §5.4): reference data synced from code, findings that stay in
their upload's org, and the analyze worker's role with only the rights it needs."""

from uuid import UUID, uuid7

import pytest
from conftest import Database
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from tenantdata import add_finding, add_tenant

from nettriage.adapters.postgres import tenant_transaction
from nettriage.adapters.reference_data import sync_reference_data
from nettriage.domain.detection.engine import DETECTORS


def test_the_detectors_and_their_techniques_are_synced_from_code(database: Database) -> None:
    with database.admin.begin() as connection:
        detectors = {
            row.id: (row.version, list(row.candidate_techniques))
            for row in connection.execute(
                text("SELECT id, version, candidate_techniques FROM detectors")
            )
        }
        techniques: set[str] = set(
            connection.execute(text("SELECT id FROM attack_techniques")).scalars()
        )

    assert detectors == {
        detector.id: (detector.version, list(detector.candidate_techniques))
        for detector in DETECTORS
    }
    assert {t for d in DETECTORS for t in d.candidate_techniques} <= techniques


def test_syncing_again_updates_what_changed_and_adds_nothing(database: Database) -> None:
    with database.admin.begin() as connection:
        connection.execute(text("UPDATE detectors SET version = 99 WHERE id = 'port_scan'"))

    counts = sync_reference_data(database.admin)

    with database.admin.begin() as connection:
        version: int = connection.execute(
            text("SELECT version FROM detectors WHERE id = 'port_scan'")
        ).scalar_one()
        rows: int = connection.execute(text("SELECT count(*) FROM attack_techniques")).scalar_one()
    assert version == next(d.version for d in DETECTORS if d.id == "port_scan")
    assert (counts, rows) == ((len(DETECTORS), rows), rows)


def test_a_finding_can_not_point_at_another_orgs_upload(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    with (
        pytest.raises(IntegrityError, match="findings_org_id_upload_id_fkey"),
        database.admin.begin() as connection,
    ):
        add_finding(connection, mine.org_id, theirs.upload_id)


def test_evidence_can_not_point_at_another_orgs_finding(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    with (
        pytest.raises(IntegrityError, match="finding_evidence_org_id_finding_id_fkey"),
        database.admin.begin() as connection,
    ):
        connection.execute(
            text(
                "INSERT INTO finding_evidence (id, org_id, finding_id, src_ip, dst_ip, "
                "src_port, dst_port, protocol, packets, bytes, start_ts, end_ts, action, "
                "line_no) VALUES (:id, :org, :finding, '1.1.1.1', '2.2.2.2', 1, 2, 6, 1, 1, "
                "now(), now(), 'ACCEPT', 1)"
            ),
            {"id": uuid7(), "org": mine.org_id, "finding": theirs.finding_id},
        )


def test_a_finding_names_only_known_techniques(database: Database) -> None:
    tenant = add_tenant(database.admin)

    with (
        pytest.raises(IntegrityError, match="finding_techniques_technique_id_fkey"),
        database.admin.begin() as connection,
    ):
        connection.execute(
            text(
                "INSERT INTO finding_techniques (finding_id, technique_id, source, org_id) "
                "VALUES (:finding, 'T9999', 'ai', :org)"
            ),
            {"finding": tenant.finding_id, "org": tenant.org_id},
        )


def test_deleting_an_org_removes_its_findings_and_everything_about_them(
    database: Database,
) -> None:
    tenant = add_tenant(database.admin)

    with database.admin.begin() as connection:
        connection.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": tenant.org_id})
        left: dict[str, int] = {
            table: connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE org_id = :org"),  # noqa: S608
                {"org": tenant.org_id},
            ).scalar_one()
            for table in ("findings", "finding_evidence", "finding_techniques", "finding_events")
        }
    assert set(left.values()) == {0}


def run_as_analyze(database: Database, org: UUID, statement: str) -> None:
    with tenant_transaction(database.app_analyze, org_id=org) as connection:
        connection.execute(text(statement))


def test_the_worker_can_move_an_upload_on_in_its_org(database: Database) -> None:
    tenant = add_tenant(database.admin)

    with tenant_transaction(database.app_analyze, org_id=tenant.org_id) as connection:
        moved = connection.execute(
            text("UPDATE uploads SET status = 'failed', failure_reason = 'x' WHERE id = :id"),
            {"id": tenant.upload_id},
        ).rowcount

    assert moved == 1


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM findings",
        "SELECT * FROM memberships",
        "SELECT * FROM users",
        "SELECT * FROM invitations",
        "SELECT * FROM audit_log",
        "UPDATE uploads SET sha256 = repeat('0', 64)",
        "UPDATE uploads SET org_id = gen_random_uuid()",
        "DELETE FROM uploads",
        "UPDATE findings SET status = 'resolved'",
        "INSERT INTO uploads (id, org_id, uploaded_by, original_filename, s3_key, size_bytes, "
        "sha256) SELECT gen_random_uuid(), org_id, uploaded_by, 'x', 'k', 1, sha256 FROM uploads",
        "UPDATE detectors SET version = 99",
        "CREATE TEMP TABLE shadow (id int)",
    ],
)
def test_the_worker_role_has_only_the_rights_it_needs(database: Database, statement: str) -> None:
    tenant = add_tenant(database.admin)

    with pytest.raises(ProgrammingError, match="permission denied"):
        run_as_analyze(database, tenant.org_id, statement)


def test_the_worker_role_can_not_bypass_row_level_security(database: Database) -> None:
    with database.app_analyze.connect() as connection:
        flags = connection.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
        owned: int = connection.execute(
            text("SELECT count(*) FROM pg_tables WHERE tableowner = current_user")
        ).scalar_one()

    assert tuple(flags) == (False, False)
    assert owned == 0
```

The deploy syncs the reference data after migrating, and gives `app_analyze` a login:

In `tools/tests/test_deploy_cli.py`, replace:
```python
SHA = "d" * 40
LWA = "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerArm64:30"
```
with:
```python
SHA = "d" * 40
SYNCED = "Reference data synced: 3 detectors, 12 ATT&CK techniques.\n"
LWA = "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerArm64:30"
```

In `tools/tests/test_deploy_cli.py`, replace:
```python
    run.on(sys.executable, "-m", "alembic")
    return healthy_account(run)
```
with:
```python
    run.on(sys.executable, "-m", "alembic")
    run.on(sys.executable, "-m", "nettriage.adapters.reference_data", returns=SYNCED)
    return healthy_account(run)
```

In `tools/tests/test_deploy_cli.py`, replace:
```python

def test_deploy_never_puts_the_database_owner_url_in_any_call_args(stage_dir: Path) -> None:
```
with:
```python

def test_deploy_syncs_reference_data_after_migrating_and_before_terraform(
    stage_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = deployable()

    cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)

    [sync] = run.called(sys.executable, "-m", "nettriage.adapters.reference_data")
    assert sync.env is not None
    assert sync.env["NETTRIAGE_MIGRATION_DATABASE_URL"] == OWNER_URL
    assert set(sync.redact) == {OWNER_URL, "owner-s3cret"}
    assert (
        run.first(sys.executable, "-m", "alembic")
        < run.first(sys.executable, "-m", "nettriage.adapters.reference_data")
        < run.first("terraform", "init")
    )
    assert SYNCED.strip() in capsys.readouterr().out


def test_a_failed_reference_sync_stops_the_deploy_before_terraform(stage_dir: Path) -> None:
    run = deployable()
    run.rules.insert(
        0,
        (
            (sys.executable, "-m", "nettriage.adapters.reference_data"),
            CommandError("`python -m` failed with exit code 1: Error: bad data"),
        ),
    )

    with pytest.raises(CommandError, match="Syncing reference data failed"):
        cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)

    assert run.called("terraform") == []


def test_deploy_never_puts_the_database_owner_url_in_any_call_args(stage_dir: Path) -> None:
```

In `tools/tests/test_deploy_database.py`, replace:
```python

def test_an_existing_login_is_kept() -> None:
```
with:
```python

def test_the_analyze_worker_gets_its_own_login() -> None:
    missing = "/nettriage/dev/db/app-analyze-url"
    run = FakeRun().on("aws", "ssm", "describe-parameters", returns=ssm_names(missing=[missing]))
    run.on("aws", "ssm", "put-parameter")

    created = database.ensure_role_logins(run, {}, "dev", OWNER_URL, set_password=lambda *a: None)

    assert created == ["app_analyze"]
    [put] = run.called("aws", "ssm", "put-parameter")
    assert put.args[put.args.index("--name") + 1] == missing
    assert urlsplit(put.args[put.args.index("--value") + 1]).username == "app_analyze"


def test_an_existing_login_is_kept() -> None:
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `just test`
Expected: FAIL before any test runs: `ImportError while loading conftest â€¦`, `No module named 'nettriage.adapters.reference_data'`. The harness now syncs the reference data for every test.

Run: `just tools-test`
Expected: `3 failed, 228 passed`: `test_deploy_syncs_reference_data_after_migrating_and_before_terraform`, `test_a_failed_reference_sync_stops_the_deploy_before_terraform` and `test_the_analyze_worker_gets_its_own_login`.

- [ ] **Step 3: Write the migration and the sync**

`backend/migrations/versions/0006_findings.py`:
```python
"""Findings (spec §5.2, §5.3, §5.4): what the analyze worker stores for an upload, and the
reference data it points to.

- `detectors` and `attack_techniques` are reference data, the same for every org; the deploy
  syncs them from code after migrating (nettriage.adapters.reference_data).
- `findings`, `finding_evidence`, `finding_techniques` and `finding_events` are tenant tables
  with row-level security. Composite foreign keys keep a finding in its upload's org, and its
  evidence, techniques and events in the finding's.
- `app_analyze` is the analyze worker's role: it reads uploads, moves them on (status and
  results only) and inserts findings. It can't read findings back or touch anything else.
- `app_api` reads findings; Plan 4c lets it triage them.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TENANT_TABLES = ("findings", "finding_evidence", "finding_techniques", "finding_events")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE detectors (
            id text PRIMARY KEY CHECK (id ~ '^[a-z][a-z_]*$'),
            name text NOT NULL CHECK (length(name) <= 100),
            description text NOT NULL,
            version integer NOT NULL CHECK (version > 0),
            candidate_techniques text[] NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE attack_techniques (
            id text PRIMARY KEY CHECK (id ~ '^T[0-9]{4}(\\.[0-9]{3})?$'),
            stix_id text NOT NULL UNIQUE,
            name text NOT NULL,
            tactics text[] NOT NULL,
            description text NOT NULL,
            url text NOT NULL CHECK (url LIKE 'https://attack.mitre.org/techniques/%'),
            attack_version text NOT NULL,
            is_subtechnique boolean NOT NULL,
            parent_id text REFERENCES attack_techniques (id),
            deprecated boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CHECK (is_subtechnique = (parent_id IS NOT NULL))
        );

        CREATE TABLE findings (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
            upload_id uuid NOT NULL,
            detector_id text NOT NULL REFERENCES detectors (id),
            detector_version integer NOT NULL CHECK (detector_version > 0),
            fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
            severity text NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
            status text NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'investigating', 'resolved', 'false_positive')),
            title text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
            src_ip inet NOT NULL,
            dst_ip inet,
            dst_port integer CHECK (dst_port BETWEEN 0 AND 65535),
            protocol smallint CHECK (protocol BETWEEN 0 AND 255),
            time_window tstzrange NOT NULL,
            metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
            assignee_id uuid REFERENCES users (id),
            version integer NOT NULL DEFAULT 1 CHECK (version > 0),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, upload_id, fingerprint),
            UNIQUE (org_id, id),
            FOREIGN KEY (org_id, upload_id) REFERENCES uploads (org_id, id) ON DELETE CASCADE
        );
        CREATE INDEX findings_org_triage ON findings (org_id, status, severity, created_at DESC);
        CREATE INDEX findings_org_upload ON findings (org_id, upload_id);
        CREATE INDEX findings_org_source ON findings (org_id, src_ip);

        CREATE TABLE finding_evidence (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL,
            finding_id uuid NOT NULL,
            src_ip inet NOT NULL,
            dst_ip inet NOT NULL,
            src_port integer NOT NULL CHECK (src_port BETWEEN 0 AND 65535),
            dst_port integer NOT NULL CHECK (dst_port BETWEEN 0 AND 65535),
            protocol smallint NOT NULL CHECK (protocol BETWEEN 0 AND 255),
            packets bigint NOT NULL CHECK (packets >= 0),
            bytes bigint NOT NULL CHECK (bytes >= 0),
            start_ts timestamptz NOT NULL,
            end_ts timestamptz NOT NULL,
            action text NOT NULL CHECK (action IN ('ACCEPT', 'REJECT')),
            line_no integer NOT NULL CHECK (line_no > 0),
            created_at timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (org_id, finding_id) REFERENCES findings (org_id, id) ON DELETE CASCADE
        );
        CREATE INDEX finding_evidence_finding ON finding_evidence (finding_id, start_ts);

        CREATE TABLE finding_techniques (
            finding_id uuid NOT NULL,
            technique_id text NOT NULL REFERENCES attack_techniques (id),
            source text NOT NULL CHECK (source IN ('detector', 'ai')),
            org_id uuid NOT NULL,
            rationale text CHECK (length(rationale) <= 1000),
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (finding_id, technique_id, source),
            FOREIGN KEY (org_id, finding_id) REFERENCES findings (org_id, id) ON DELETE CASCADE
        );

        CREATE TABLE finding_events (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL,
            finding_id uuid NOT NULL,
            actor_id uuid REFERENCES users (id),
            type text NOT NULL CHECK (type IN ('created', 'status_changed', 'assigned',
                                               'commented', 'ai_explained')),
            payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (org_id, finding_id) REFERENCES findings (org_id, id) ON DELETE CASCADE
        );
        CREATE INDEX finding_events_finding ON finding_events (finding_id, created_at);

        CREATE TRIGGER detectors_updated_at BEFORE UPDATE ON detectors
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        CREATE TRIGGER attack_techniques_updated_at BEFORE UPDATE ON attack_techniques
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        CREATE TRIGGER findings_updated_at BEFORE UPDATE ON findings
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant ON {table} "
            "USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id())"
        )
    op.execute(
        """
        -- Created without a login; the deploy gives it one (tools/deploy/database.py).
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_analyze') THEN
                CREATE ROLE app_analyze NOLOGIN;
            END IF;
            IF EXISTS (
                SELECT FROM pg_roles WHERE rolname = 'app_analyze'
                AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)
            ) THEN
                RAISE EXCEPTION 'app_analyze must not be a superuser or have BYPASSRLS, '
                    'CREATEROLE, CREATEDB or REPLICATION';
            END IF;
            IF EXISTS (
                SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
                WHERE r.rolname = 'app_analyze'
            ) THEN
                RAISE EXCEPTION 'app_analyze must not be a member of another role';
            END IF;
        END $$;

        GRANT SELECT ON detectors, attack_techniques TO app_api, app_analyze;
        GRANT SELECT ON findings, finding_evidence, finding_techniques, finding_events TO app_api;

        GRANT USAGE ON SCHEMA public TO app_analyze;
        GRANT EXECUTE ON FUNCTION app_org_id(), app_user_id() TO app_analyze;
        GRANT SELECT ON uploads TO app_analyze;
        GRANT UPDATE (status, failure_reason, rows_parsed, rows_rejected, rejected_samples,
                      findings_truncated, flow_time_range, processed_at)
            ON uploads TO app_analyze;
        GRANT INSERT (id, org_id, upload_id, detector_id, detector_version, fingerprint, severity,
                      title, src_ip, dst_ip, dst_port, protocol, time_window, metrics)
            ON findings TO app_analyze;
        GRANT INSERT ON finding_evidence, finding_techniques TO app_analyze;
        GRANT INSERT (id, org_id, finding_id, type, payload) ON finding_events TO app_analyze;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE finding_events, finding_techniques, finding_evidence, findings,
            attack_techniques, detectors;
        REVOKE ALL ON uploads FROM app_analyze;
        REVOKE ALL ON FUNCTION app_org_id(), app_user_id() FROM app_analyze;
        REVOKE USAGE ON SCHEMA public FROM app_analyze;
        """
    )
```

`backend/src/nettriage/adapters/reference_data.py`:
```python
"""Reference data in Postgres (spec §5.2): the detectors, synced from code, and the ATT&CK
techniques they can name, from the committed subset (nettriage.reference).

The deploy runs this as the schema owner right after migrating (tools/deploy/database.py), and
the test database runs it too. Every row is an upsert, so running it again changes only what
changed in code.

    NETTRIAGE_MIGRATION_DATABASE_URL=<owner url> python -m nettriage.adapters.reference_data
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import Engine, text

from nettriage.adapters.postgres import create_database_engine
from nettriage.domain.detection.engine import DETECTORS
from nettriage.reference import attack_reference

URL_ENV = "NETTRIAGE_MIGRATION_DATABASE_URL"

_UPSERT_TECHNIQUE = text(
    "INSERT INTO attack_techniques (id, stix_id, name, tactics, description, url, "
    "attack_version, is_subtechnique, parent_id, deprecated) VALUES (:id, :stix_id, :name, "
    ":tactics, :description, :url, :version, :sub, :parent, :deprecated) "
    "ON CONFLICT (id) DO UPDATE SET stix_id = EXCLUDED.stix_id, name = EXCLUDED.name, "
    "tactics = EXCLUDED.tactics, description = EXCLUDED.description, url = EXCLUDED.url, "
    "attack_version = EXCLUDED.attack_version, is_subtechnique = EXCLUDED.is_subtechnique, "
    "parent_id = EXCLUDED.parent_id, deprecated = EXCLUDED.deprecated"
)
_UPSERT_DETECTOR = text(
    "INSERT INTO detectors (id, name, description, version, candidate_techniques) "
    "VALUES (:id, :name, :description, :version, :techniques) "
    "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description, "
    "version = EXCLUDED.version, candidate_techniques = EXCLUDED.candidate_techniques"
)


def sync_reference_data(engine: Engine) -> tuple[int, int]:
    """Upsert the detectors and techniques in one transaction. Returns their counts."""
    reference = attack_reference()
    parents_first = sorted(reference.techniques, key=lambda technique: technique.is_subtechnique)
    with engine.begin() as connection:
        for technique in parents_first:
            connection.execute(
                _UPSERT_TECHNIQUE,
                {
                    "id": technique.id,
                    "stix_id": technique.stix_id,
                    "name": technique.name,
                    "tactics": list(technique.tactics),
                    "description": technique.description,
                    "url": technique.url,
                    "version": reference.version,
                    "sub": technique.is_subtechnique,
                    "parent": technique.parent_id,
                    "deprecated": technique.deprecated,
                },
            )
        for detector in DETECTORS:
            connection.execute(
                _UPSERT_DETECTOR,
                {
                    "id": detector.id,
                    "name": detector.name,
                    "description": detector.description,
                    "version": detector.version,
                    "techniques": list(detector.candidate_techniques),
                },
            )
    return len(DETECTORS), len(reference.techniques)


def main() -> int:
    url = os.environ.get(URL_ENV)
    if not url:
        print(f"{URL_ENV} isn't set.", file=sys.stderr)
        return 2
    engine = create_database_engine(url, pool_size=1)
    try:
        detectors, techniques = sync_reference_data(engine)
    finally:
        engine.dispose()
    print(f"Reference data synced: {detectors} detectors, {techniques} ATT&CK techniques.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Sync at deploy, and give `app_analyze` its login**

In `tools/deploy/config.py`, replace:
```python
NEON_HOST_SUFFIX = ".eu-central-1.aws.neon.tech"
# Database roles that get a login from the deploy. Plans 4, 5 and 7 add theirs.
APP_DB_ROLES = ("app_api",)
MIGRATIONS_CONFIG = REPO / "backend" / "alembic.ini"
```
with:
```python
NEON_HOST_SUFFIX = ".eu-central-1.aws.neon.tech"
# Database roles that get a login from the deploy. Plans 5 and 7 add theirs.
APP_DB_ROLES = ("app_api", "app_analyze")
MIGRATIONS_CONFIG = REPO / "backend" / "alembic.ini"
```

In `tools/deploy/database.py`, replace:
```python

def set_role_password(owner_url: str, role: str, password: str) -> None:
```
with:
```python

def sync_reference_data(run: Runner, env: Mapping[str, str], owner_url: str) -> str:
    """Upsert the detectors and ATT&CK techniques from this checkout (spec §5.2), as the
    database owner, right after migrating. Returns its one-line summary."""
    try:
        result = run(
            [sys.executable, "-m", "nettriage.adapters.reference_data"],
            env={**env, MIGRATION_URL_ENV: owner_url},
            redact=[owner_url, urlsplit(owner_url).password or owner_url],
        )
    except CommandError as exc:
        reason = str(exc).partition(" failed with ")[2] or str(exc)
        raise CommandError(
            f"Syncing reference data failed; nothing in AWS changed. It stopped with {reason}"
        ) from exc
    return result.stdout.strip()


def set_role_password(owner_url: str, role: str, password: str) -> None:
```

In `tools/deploy/__main__.py`, replace:
```python
    database.migrate(run, env, owner_url)
    created = database.ensure_role_logins(run, env, stage, owner_url)
    print("Database migrated." + (f" New logins: {', '.join(created)}." if created else ""))

```
with:
```python
    database.migrate(run, env, owner_url)
    synced = database.sync_reference_data(run, env, owner_url)
    created = database.ensure_role_logins(run, env, stage, owner_url)
    print("Database migrated." + (f" New logins: {', '.join(created)}." if created else ""))
    print(synced)

```

- [ ] **Step 5: Run the checks**

Run: `just lint test tools-test`
Expected: lint is clean; backend `657 passed, 1 skipped`; tools `231 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/migrations backend/src backend/tests tools
git commit -m "feat(findings): findings, evidence, techniques and events with row-level security; app_analyze; reference data synced at deploy" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: The analysis step

**Files:**
- Create: `backend/src/nettriage/application/analysis.py`, `backend/tests/flowlogs.py` (test files)
- Test: `backend/tests/unit/application/test_analysis.py`

**Interfaces:**
- Consumes (Plan 2): `parse_flow_log(stream, limits=None) -> ParseResult` (`rows_parsed`, `rows_rejected`, `rejected_samples`, `flows`), `ParseLimits`, `RejectedSample`, `FlowLogError` (`code`, `detail`), `detect(flows) -> DetectionResult` (`findings`, `truncated`) and `Finding`. From Plan 4a: `s3_key(org_id, upload_id)`.
- Produces:
  - In `nettriage.application.analysis`:
    - `UploadKey(org_id: UUID, upload_id: UUID, key: str)`;
    - `parse_upload_key(key) -> UploadKey | None`, which accepts only keys `s3_key` makes (lowercase UUIDs, `orgs/{org}/uploads/{upload}/raw`);
    - `Analysis(rows_parsed, rows_rejected, rejected_samples, findings, findings_truncated, flow_start: datetime | None, flow_end: datetime | None)`;
    - `analyze_parsed(parsed: ParseResult) -> Analysis`, and `analyze_flow_log(stream, limits=None) -> Analysis`, which raises the parser's `FlowLogError`.
  - In `backend/tests/flowlogs.py`: `START = 1_790_596_800` (2026-09-28 12:00 UTC); `port_scan(ports=150, source="203.0.113.9") -> bytes`, one external host probing `ports` ports of `10.0.0.5` within a minute, all rejected; `quiet(lines=3) -> bytes`, NODATA records only.

- [ ] **Step 1: Write the failing tests**

`backend/tests/flowlogs.py`:
```python
"""Small VPC Flow Logs files for tests: AWS's default v2 format, no header."""

START = 1_790_596_800  # 2026-09-28 12:00:00 UTC


def port_scan(ports: int = 150, source: str = "203.0.113.9") -> bytes:
    """An external host probing `ports` ports on one internal host within a minute, all
    rejected: one `port_scan` finding."""
    lines = [
        f"2 123456789012 eni-1 {source} 10.0.0.5 40000 {port} 6 1 40 "
        f"{START + port % 60} {START + port % 60} REJECT OK"
        for port in range(1, ports + 1)
    ]
    return ("\n".join(lines) + "\n").encode()


def quiet(lines: int = 3) -> bytes:
    """An interface with no traffic: NODATA records only, so no flows and no findings."""
    return f"2 123456789012 eni-1 - - - - - - - {START} {START + 60} - NODATA\n".encode() * lines
```

`backend/tests/unit/application/test_analysis.py`:
```python
"""Analyzing one upload (spec §4.2, §8.1, §8.2): which keys belong to uploads, and what a file
yields."""

import gzip
import io
from datetime import UTC, datetime
from uuid import UUID

import pytest
from flowlogs import port_scan, quiet

from nettriage.application.analysis import analyze_flow_log, parse_upload_key
from nettriage.domain.parsing.vpc_flow_logs import FlowLogError, ParseLimits

ORG = UUID("01a0ec4d-060a-7266-a427-fce3ccd0d827")
UPLOAD = UUID("01a0ec4d-374f-740e-85e7-4ac6ee451cd5")


def test_a_key_the_api_made_names_its_org_and_upload() -> None:
    key = f"orgs/{ORG}/uploads/{UPLOAD}/raw"

    parsed = parse_upload_key(key)

    assert parsed is not None
    assert (parsed.org_id, parsed.upload_id, parsed.key) == (ORG, UPLOAD, key)


@pytest.mark.parametrize(
    "key",
    [
        f"orgs/{ORG}/uploads/{UPLOAD}/raw.gz",
        f"orgs/{ORG}/uploads/{UPLOAD}",
        f"orgs/{str(ORG).upper()}/uploads/{UPLOAD}/raw",
        f"orgs/{ORG}/uploads/{UPLOAD}/raw/../../other",
        f"prefix/orgs/{ORG}/uploads/{UPLOAD}/raw",
        "orgs/not-a-uuid/uploads/also-not/raw",
        "",
    ],
)
def test_any_other_key_is_not_an_upload(key: str) -> None:
    assert parse_upload_key(key) is None


def test_a_port_scan_yields_one_finding_and_the_files_statistics() -> None:
    analysis = analyze_flow_log(io.BytesIO(port_scan()))

    assert (analysis.rows_parsed, analysis.rows_rejected, analysis.rejected_samples) == (150, 0, [])
    [finding] = analysis.findings
    assert finding.detector_id == "port_scan"
    assert str(finding.src_ip) == "203.0.113.9"
    assert analysis.findings_truncated == 0
    assert analysis.flow_start == datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
    assert analysis.flow_end == datetime(2026, 9, 28, 12, 0, 59, tzinfo=UTC)


def test_a_gzipped_file_is_read_the_same() -> None:
    analysis = analyze_flow_log(io.BytesIO(gzip.compress(port_scan())))

    assert (analysis.rows_parsed, len(analysis.findings)) == (150, 1)


def test_an_interface_with_no_traffic_yields_no_findings_and_no_time_range() -> None:
    analysis = analyze_flow_log(io.BytesIO(quiet()))

    assert (analysis.rows_parsed, analysis.findings) == (0, [])
    assert (analysis.flow_start, analysis.flow_end) == (None, None)


def test_a_file_that_is_not_a_flow_log_fails_with_a_reason() -> None:
    with pytest.raises(FlowLogError) as failed:
        analyze_flow_log(io.BytesIO(b"<html>\n" * 50))

    assert failed.value.code == "not_a_flow_log"


def test_a_file_that_inflates_past_the_limit_fails_early() -> None:
    bomb = gzip.compress(port_scan() * 1_000)  # about 11 MB of flows, 40 KB compressed

    with pytest.raises(FlowLogError) as failed:
        analyze_flow_log(io.BytesIO(bomb), ParseLimits(max_decompressed_bytes=1_000_000))

    assert failed.value.code == "limit_exceeded"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && uv run python -m pytest tests/unit/application/test_analysis.py`
Expected: FAIL. Collection stops with 1 error: `No module named 'nettriage.application.analysis'`.

- [ ] **Step 3: Write the analysis step**

`backend/src/nettriage/application/analysis.py`:
```python
"""Analyzing one upload (spec §4.2, §8.1, §8.2): parse the file as it streams, run every
detector, and collect what the upload row and its findings need. Parsing and detecting are
separate steps so the worker can trace each (`analyze.parse`, `analyze.detect`, spec §9.1)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO
from uuid import UUID

from nettriage.domain.detection.engine import detect
from nettriage.domain.detection.model import Finding
from nettriage.domain.parsing.vpc_flow_logs import (
    ParseLimits,
    ParseResult,
    RejectedSample,
    parse_flow_log,
)

# The only keys the API ever hands out (application/uploads.py `s3_key`): anything else in the
# bucket is ignored (spec §8.6).
_UPLOAD_KEY = re.compile(
    r"orgs/(?P<org>[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})"
    r"/uploads/(?P<upload>[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})/raw"
)


@dataclass(frozen=True)
class UploadKey:
    org_id: UUID
    upload_id: UUID
    key: str


def parse_upload_key(key: str) -> UploadKey | None:
    """The org and upload an S3 key belongs to, or None for a key the API didn't make."""
    match = _UPLOAD_KEY.fullmatch(key)
    if match is None:
        return None
    return UploadKey(org_id=UUID(match["org"]), upload_id=UUID(match["upload"]), key=key)


@dataclass(frozen=True)
class Analysis:
    rows_parsed: int
    rows_rejected: int
    rejected_samples: list[RejectedSample]
    findings: list[Finding]
    findings_truncated: int
    flow_start: datetime | None
    flow_end: datetime | None


def analyze_parsed(parsed: ParseResult) -> Analysis:
    """Run every detector over a parsed file and summarize it for the upload row."""
    detected = detect(parsed.flows)
    return Analysis(
        rows_parsed=parsed.rows_parsed,
        rows_rejected=parsed.rows_rejected,
        rejected_samples=parsed.rejected_samples,
        findings=detected.findings,
        findings_truncated=detected.truncated,
        flow_start=min((flow.start for flow in parsed.flows), default=None),
        flow_end=max((flow.end for flow in parsed.flows), default=None),
    )


def analyze_flow_log(stream: BinaryIO, limits: ParseLimits | None = None) -> Analysis:
    """Parse, then detect. Raises the parser's `FlowLogError` for a file that isn't a flow log
    or breaks a limit; the worker marks the upload failed with its reason."""
    return analyze_parsed(parse_flow_log(stream, limits))
```

- [ ] **Step 4: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `670 passed, 1 skipped`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/nettriage/application/analysis.py backend/tests/flowlogs.py backend/tests/unit/application/test_analysis.py
git commit -m "feat(analysis): map S3 keys to uploads, and parse and detect a file into one analysis" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Storing an analysis exactly once

**Files:**
- Create: `backend/src/nettriage/adapters/analysis_store.py`
- Test: `backend/tests/integration/test_analysis_store.py`

**Interfaces:**
- Consumes: Task 2's tables, grants and `Database.app_analyze`; Task 3's `UploadKey`, `Analysis`, `analyze_flow_log`, and `flowlogs.port_scan` and `quiet`; Plan 3a's `tenant_transaction(engine, *, org_id=None, user_id=None)`.
- Produces, in `nettriage.adapters.analysis_store`:
  - `ClaimedUpload(org_id, upload_id, size_bytes, sha256)`;
  - `claim_upload(engine, key: UploadKey) -> ClaimedUpload | None`, which moves a matching `pending_upload` or `processing` upload to `processing`;
  - `store_analysis(engine, upload, analysis, now) -> bool`, which stores the findings and marks the upload `analyzed`, or returns False, storing nothing, when the upload is no longer `processing`;
  - `fail_upload(engine, upload, reason, now) -> bool`, which marks it `failed` with `reason` (at most 500 characters).

- [ ] **Step 1: Write the failing tests**

`backend/tests/integration/test_analysis_store.py`:
```python
"""The analyze worker's writes (spec §4.2, §8.6), as `app_analyze`: claim an upload, store its
findings or its failure once, however often SQS delivers the event."""

import io
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID

import pytest
from conftest import APP_ANALYZE_PASSWORD, Database
from flowlogs import port_scan, quiet
from sqlalchemy import text
from tenantdata import add_tenant, add_upload

from nettriage.adapters.analysis_store import (
    ClaimedUpload,
    claim_upload,
    fail_upload,
    store_analysis,
)
from nettriage.adapters.postgres import create_database_engine
from nettriage.application.analysis import UploadKey, analyze_flow_log
from nettriage.application.uploads import s3_key

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def pending(database: Database, status: str = "pending_upload") -> UploadKey:
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        upload = add_upload(connection, tenant.org_id, tenant.owner_id, status)
    return UploadKey(org_id=tenant.org_id, upload_id=upload, key=s3_key(tenant.org_id, upload))


def upload_row(database: Database, key: UploadKey) -> dict[str, object]:
    with database.admin.begin() as connection:
        row = connection.execute(
            text(
                "SELECT status, failure_reason, rows_parsed, rows_rejected, rejected_samples, "
                "findings_truncated, lower(flow_time_range) AS start, "
                "upper(flow_time_range) AS end, processed_at FROM uploads WHERE id = :id"
            ),
            {"id": key.upload_id},
        ).one()
    return dict(row._mapping)


def counts(database: Database, upload: UUID) -> tuple[int, int, int, int]:
    with database.admin.begin() as connection:
        findings: list[UUID] = list(
            connection.execute(
                text("SELECT id FROM findings WHERE upload_id = :id"), {"id": upload}
            ).scalars()
        )
        per_table: list[int] = [
            connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE finding_id = ANY(:ids)"),  # noqa: S608
                {"ids": findings},
            ).scalar_one()
            for table in ("finding_evidence", "finding_techniques", "finding_events")
        ]
    evidence, techniques, events = per_table
    return len(findings), evidence, techniques, events


def test_claiming_moves_a_pending_upload_to_processing(database: Database) -> None:
    key = pending(database)

    claimed = claim_upload(database.app_analyze, key)

    assert claimed == ClaimedUpload(
        org_id=key.org_id, upload_id=key.upload_id, size_bytes=1024, sha256="0" * 64
    )
    assert upload_row(database, key)["status"] == "processing"


def test_a_retry_after_a_crash_can_claim_it_again(database: Database) -> None:
    key = pending(database, "processing")

    assert claim_upload(database.app_analyze, key) is not None


@pytest.mark.parametrize("status", ["analyzed", "failed", "expired"])
def test_a_finished_upload_is_not_claimed_again(database: Database, status: str) -> None:
    key = pending(database, status)

    assert claim_upload(database.app_analyze, key) is None
    assert upload_row(database, key)["status"] == status


def test_a_key_that_doesnt_match_its_upload_is_not_claimed(database: Database) -> None:
    key = pending(database)
    other = pending(database)

    wrong_key = UploadKey(org_id=key.org_id, upload_id=key.upload_id, key=other.key)
    wrong_org = UploadKey(org_id=other.org_id, upload_id=key.upload_id, key=key.key)

    assert claim_upload(database.app_analyze, wrong_key) is None
    assert claim_upload(database.app_analyze, wrong_org) is None
    assert upload_row(database, key)["status"] == "pending_upload"


def test_storing_an_analysis_saves_the_findings_and_the_files_statistics(
    database: Database,
) -> None:
    key = pending(database)
    claimed = claim_upload(database.app_analyze, key)
    assert claimed is not None
    analysis = analyze_flow_log(io.BytesIO(port_scan()))

    stored = store_analysis(database.app_analyze, claimed, analysis, NOW)

    row = upload_row(database, key)
    assert stored is True
    assert (row["status"], row["rows_parsed"], row["rows_rejected"]) == ("analyzed", 150, 0)
    assert (row["rejected_samples"], row["findings_truncated"], row["processed_at"]) == ([], 0, NOW)
    assert (row["start"], row["end"]) == (analysis.flow_start, analysis.flow_end)
    evidence = len(analysis.findings[0].evidence)
    techniques = len(analysis.findings[0].candidate_techniques)
    assert counts(database, key.upload_id) == (1, evidence, techniques, 1)


def test_a_file_without_flows_is_analyzed_with_no_time_range(database: Database) -> None:
    key = pending(database)
    claimed = claim_upload(database.app_analyze, key)
    assert claimed is not None

    store_analysis(database.app_analyze, claimed, analyze_flow_log(io.BytesIO(quiet())), NOW)

    row = upload_row(database, key)
    assert (row["status"], row["start"], row["end"]) == ("analyzed", None, None)


def test_a_second_delivery_stores_nothing(database: Database) -> None:
    key = pending(database)
    claimed = claim_upload(database.app_analyze, key)
    assert claimed is not None
    analysis = analyze_flow_log(io.BytesIO(port_scan()))
    store_analysis(database.app_analyze, claimed, analysis, NOW)
    before = counts(database, key.upload_id)

    again = store_analysis(database.app_analyze, claimed, analysis, NOW)

    assert again is False
    assert counts(database, key.upload_id) == before


def test_two_deliveries_finishing_at_once_store_one_set_of_findings(database: Database) -> None:
    """The upload's row lock makes the second store see `analyzed` and stop."""
    key = pending(database)
    claimed = claim_upload(database.app_analyze, key)
    assert claimed is not None
    analysis = analyze_flow_log(io.BytesIO(port_scan()))
    url = database.url.set(username="app_analyze", password=APP_ANALYZE_PASSWORD)
    engine = create_database_engine(url.render_as_string(hide_password=False), pool_size=2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store_analysis(engine, claimed, analysis, NOW), [1, 2]))
    engine.dispose()

    assert sorted(results) == [False, True]
    assert counts(database, key.upload_id)[0] == 1


def test_a_failed_analysis_keeps_its_reason(database: Database) -> None:
    key = pending(database)
    claimed = claim_upload(database.app_analyze, key)
    assert claimed is not None

    failed = fail_upload(database.app_analyze, claimed, "the file contains no flow records", NOW)
    too_late = fail_upload(database.app_analyze, claimed, "again", NOW)

    row = upload_row(database, key)
    assert (failed, too_late) == (True, False)
    assert (row["status"], row["failure_reason"], row["processed_at"]) == (
        "failed",
        "the file contains no flow records",
        NOW,
    )
    assert counts(database, key.upload_id) == (0, 0, 0, 0)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `just test`
Expected: FAIL. Collection stops with 1 error: `No module named 'nettriage.adapters.analysis_store'`.

- [ ] **Step 3: Write the store**

`backend/src/nettriage/adapters/analysis_store.py`:
```python
"""The analyze worker's side of Postgres (spec §4.2, §8.6), as `app_analyze`: claim an upload,
then store its results or its failure, each in one transaction.

An upload moves pending_upload → processing → analyzed or failed. SQS may deliver the same event
twice, or again after a crash, so:
- claiming accepts pending_upload or processing (a retry after a crash), and nothing else;
- storing locks the upload and stores nothing unless it's still processing, so a duplicate that
  finishes second changes nothing. The unique `(org_id, upload_id, fingerprint)` is the backstop.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import Connection, Engine, text

from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.analysis import Analysis, UploadKey
from nettriage.domain.detection.model import Finding


@dataclass(frozen=True)
class ClaimedUpload:
    org_id: UUID
    upload_id: UUID
    size_bytes: int
    sha256: str


def claim_upload(engine: Engine, key: UploadKey) -> ClaimedUpload | None:
    """Mark the upload `processing`. None if the key names no upload of that org, or its upload
    is already analyzed, failed or expired."""
    with tenant_transaction(engine, org_id=key.org_id) as connection:
        row = connection.execute(
            text(
                "UPDATE uploads SET status = 'processing' WHERE org_id = :org AND id = :id "
                "AND s3_key = :key AND status IN ('pending_upload', 'processing') "
                "RETURNING size_bytes, sha256"
            ),
            {"org": key.org_id, "id": key.upload_id, "key": key.key},
        ).one_or_none()
    if row is None:
        return None
    return ClaimedUpload(
        org_id=key.org_id, upload_id=key.upload_id, size_bytes=row.size_bytes, sha256=row.sha256
    )


def store_analysis(
    engine: Engine, upload: ClaimedUpload, analysis: Analysis, now: datetime
) -> bool:
    """Store the findings and mark the upload analyzed. False if another delivery already
    finished it."""
    with tenant_transaction(engine, org_id=upload.org_id) as connection:
        if not _still_processing(connection, upload):
            return False
        for finding in analysis.findings:
            _insert_finding(connection, upload, finding)
        connection.execute(
            text(
                "UPDATE uploads SET status = 'analyzed', failure_reason = NULL, "
                "rows_parsed = :parsed, rows_rejected = :rejected, "
                "rejected_samples = CAST(:samples AS jsonb), findings_truncated = :truncated, "
                "flow_time_range = CASE WHEN CAST(:start AS timestamptz) IS NULL THEN NULL "
                "ELSE tstzrange(CAST(:start AS timestamptz), CAST(:end AS timestamptz), '[]') END, "
                "processed_at = :now WHERE org_id = :org AND id = :id"
            ),
            {
                "parsed": analysis.rows_parsed,
                "rejected": analysis.rows_rejected,
                "samples": json.dumps([asdict(sample) for sample in analysis.rejected_samples]),
                "truncated": analysis.findings_truncated,
                "start": analysis.flow_start,
                "end": analysis.flow_end,
                "now": now,
                "org": upload.org_id,
                "id": upload.upload_id,
            },
        )
    return True


def fail_upload(engine: Engine, upload: ClaimedUpload, reason: str, now: datetime) -> bool:
    """Mark the upload failed with a readable reason. False if it's no longer processing."""
    with tenant_transaction(engine, org_id=upload.org_id) as connection:
        failed = connection.execute(
            text(
                "UPDATE uploads SET status = 'failed', failure_reason = :reason, "
                "processed_at = :now WHERE org_id = :org AND id = :id AND status = 'processing'"
            ),
            {"reason": reason[:500], "now": now, "org": upload.org_id, "id": upload.upload_id},
        ).rowcount
    return failed == 1


def _still_processing(connection: Connection, upload: ClaimedUpload) -> bool:
    status = connection.execute(
        text("SELECT status FROM uploads WHERE org_id = :org AND id = :id FOR UPDATE"),
        {"org": upload.org_id, "id": upload.upload_id},
    ).scalar_one_or_none()
    return bool(status == "processing")


def _insert_finding(connection: Connection, upload: ClaimedUpload, finding: Finding) -> None:
    finding_id = uuid7()
    connection.execute(
        text(
            "INSERT INTO findings (id, org_id, upload_id, detector_id, detector_version, "
            "fingerprint, severity, title, src_ip, dst_ip, dst_port, protocol, time_window, "
            "metrics) VALUES (:id, :org, :upload, :detector, :version, :fingerprint, :severity, "
            ":title, :src, :dst, :port, :protocol, "
            "tstzrange(CAST(:start AS timestamptz), CAST(:end AS timestamptz), '[]'), "
            "CAST(:metrics AS jsonb))"
        ),
        {
            "id": finding_id,
            "org": upload.org_id,
            "upload": upload.upload_id,
            "detector": finding.detector_id,
            "version": finding.detector_version,
            "fingerprint": finding.fingerprint,
            "severity": finding.severity.value,
            "title": finding.title,
            "src": str(finding.src_ip),
            "dst": str(finding.dst_ip) if finding.dst_ip is not None else None,
            "port": finding.dst_port,
            "protocol": finding.protocol,
            "start": finding.window_start,
            "end": finding.window_end,
            "metrics": json.dumps(dict(finding.metrics)),
        },
    )
    if finding.evidence:
        connection.execute(
            text(
                "INSERT INTO finding_evidence (id, org_id, finding_id, src_ip, dst_ip, src_port, "
                "dst_port, protocol, packets, bytes, start_ts, end_ts, action, line_no) VALUES "
                "(:id, :org, :finding, :src, :dst, :src_port, :dst_port, :protocol, :packets, "
                ":bytes, :start, :end, :action, :line)"
            ),
            [
                {
                    "id": uuid7(),
                    "org": upload.org_id,
                    "finding": finding_id,
                    "src": str(flow.src_ip),
                    "dst": str(flow.dst_ip),
                    "src_port": flow.src_port,
                    "dst_port": flow.dst_port,
                    "protocol": flow.protocol,
                    "packets": flow.packets,
                    "bytes": flow.bytes,
                    "start": flow.start,
                    "end": flow.end,
                    "action": flow.action,
                    "line": flow.line_no,
                }
                for flow in finding.evidence
            ],
        )
    if finding.candidate_techniques:
        connection.execute(
            text(
                "INSERT INTO finding_techniques (finding_id, technique_id, source, org_id) "
                "VALUES (:finding, :technique, 'detector', :org)"
            ),
            [
                {"finding": finding_id, "technique": technique, "org": upload.org_id}
                for technique in finding.candidate_techniques
            ],
        )
    connection.execute(
        text(
            "INSERT INTO finding_events (id, org_id, finding_id, type, payload) "
            "VALUES (:id, :org, :finding, 'created', CAST(:payload AS jsonb))"
        ),
        {
            "id": uuid7(),
            "org": upload.org_id,
            "finding": finding_id,
            "payload": json.dumps({"detector": finding.detector_id}),
        },
    )
```

- [ ] **Step 4: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `681 passed, 1 skipped`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/nettriage/adapters/analysis_store.py backend/tests/integration/test_analysis_store.py
git commit -m "feat(analysis): claim an upload, then store its findings once or fail it with a reason" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: The analyze worker

**Files:**
- Create: `backend/src/nettriage/adapters/parameters.py` (moved out of the API's wiring), `backend/src/nettriage/adapters/upload_objects.py`, `backend/src/nettriage/entrypoints/analyze/__init__.py`, `backend/src/nettriage/entrypoints/analyze/worker.py`, `backend/src/nettriage/entrypoints/analyze/wiring.py`, `backend/src/nettriage/entrypoints/analyze/handler.py`
- Modify: `backend/src/nettriage/entrypoints/api/wiring.py`, `backend/src/nettriage/platform/metrics.py`, `backend/src/nettriage/platform/trace_context.py`, `tools/build_lambda.py`
- Test: `backend/tests/worker/test_analyze_worker.py`, `backend/tests/unit/analyze/test_worker_wiring.py`, `backend/tests/unit/api/test_wiring.py`, `tools/tests/test_build_lambda.py`

**Interfaces:**
- Consumes: Task 3's `parse_upload_key`, `analyze_parsed` and `flowlogs.port_scan`; Task 4's `claim_upload`, `store_analysis`, `fail_upload` and `ClaimedUpload`; Plan 2's `parse_flow_log`, `ParseLimits` and `FlowLogError`; Plan 4a's `s3_key`. From Plan 1: `Settings` (`database_url_parameter`, `uploads_bucket`, `service_name`), `configure_logging`, `create_tracer_provider`, `create_meter_provider` and `install_global_providers`.
- Produces:
  - In `nettriage.adapters.parameters`: `AWS_CONFIG`, `MissingParameterError` and `read_parameters(ssm, names) -> dict[str, str]`, moved unchanged from `entrypoints/api/wiring.py`, which now imports them.
  - In `nettriage.adapters.upload_objects`: `UploadObject(body: BinaryIO, size: int, traceparent: str | None)` and `UploadObjects(client, bucket)` with `.open(key) -> UploadObject`.
  - In `nettriage.platform.metrics`: `AnalyzeMetrics(meter_provider=None)` with `uploads_processed`, `processing_duration`, `rows_parsed`, `rows_rejected`, `findings_created`, `upload_rejected` and `queue_message_age`.
  - In `nettriage.platform.trace_context`: `links_from(traceparent: str | None) -> list[Link]`.
  - In `nettriage.entrypoints.analyze.worker`:
    - `Worker(database, objects, clock, metrics, tracer, flush=…, limits=ParseLimits(), sleep=time.sleep)`;
    - `.handle_message(record)` for one SQS record;
    - `.process(key) -> Outcome`, where `Outcome = Literal["analyzed", "failed", "ignored", "duplicate"]`;
    - `SIZE_MISMATCH` and `RETRY_DELAYS = (1.0, 3.0)`.
  - `nettriage.entrypoints.analyze.wiring.build_worker(settings, tracer_provider, meter_provider, session=None) -> Worker`, and the Lambda handler `nettriage.entrypoints.analyze.handler.handle(event, context)`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/worker/test_analyze_worker.py`:
```python
"""The analyze worker end to end (spec §4.2, §8.6, §9.1 to §9.3): an S3 event from the queue, the
object from S3 (moto), and the findings in Postgres as `app_analyze`."""

import gzip
import hashlib
import io
import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from urllib.parse import quote_plus
from uuid import UUID

import boto3
import pytest
from conftest import NO_DATABASE, Database, FakeClock, counter
from flowlogs import port_scan
from moto import mock_aws
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError
from tenantdata import add_member, add_org, add_user

from nettriage.adapters.analysis_store import ClaimedUpload, claim_upload
from nettriage.adapters.postgres import create_database_engine
from nettriage.adapters.upload_objects import UploadObjects
from nettriage.application.analysis import UploadKey
from nettriage.application.uploads import s3_key
from nettriage.domain.parsing.vpc_flow_logs import ParseLimits
from nettriage.entrypoints.analyze import handler
from nettriage.entrypoints.analyze.worker import SIZE_MISMATCH, Worker
from nettriage.platform.metrics import AnalyzeMetrics

BUCKET = "nettriage-test-uploads-00000000"
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


@dataclass
class Rig:
    worker: Worker
    s3: object
    spans: InMemorySpanExporter
    metrics: InMemoryMetricReader


@pytest.fixture
def rig(database: Database, clock: FakeClock) -> Iterator[Rig]:
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-north-1")
        s3.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-north-1"}
        )
        spans = InMemorySpanExporter()
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
        reader = InMemoryMetricReader()
        worker = Worker(
            database=database.app_analyze,
            objects=UploadObjects(s3, BUCKET),
            clock=clock,
            metrics=AnalyzeMetrics(MeterProvider(metric_readers=[reader])),
            tracer=tracer_provider.get_tracer("test"),
            sleep=lambda seconds: None,
        )
        yield Rig(worker=worker, s3=s3, spans=spans, metrics=reader)


def uploaded(
    database: Database,
    rig: Rig,
    content: bytes,
    *,
    declared_size: int | None = None,
    traceparent: str | None = None,
) -> str:
    """A pending upload whose file is in the bucket, as the browser would have PUT it."""
    with database.admin.begin() as connection:
        owner = add_user(connection)
        org = add_org(connection, owner)
        add_member(connection, org, owner, "owner")
        upload = UUID(int=int(hashlib.sha256(content + org.bytes).hexdigest()[:32], 16))
        key = s3_key(org, upload)
        connection.execute(
            text(
                "INSERT INTO uploads (id, org_id, uploaded_by, original_filename, s3_key, "
                "size_bytes, sha256) VALUES (:id, :org, :by, 'flows.log', :key, :size, :sha)"
            ),
            {
                "id": upload,
                "org": org,
                "by": owner,
                "key": key,
                "size": declared_size or len(content),
                "sha": hashlib.sha256(content).hexdigest(),
            },
        )
    metadata = {"traceparent": traceparent} if traceparent else {}
    rig.s3.put_object(Bucket=BUCKET, Key=key, Body=content, Metadata=metadata)  # type: ignore[attr-defined]
    return key


def message(key: str, sent_millis: int = 1_790_683_200_000) -> dict[str, object]:
    """An SQS record carrying S3's ObjectCreated notification, with its URL-encoded key."""
    body = {
        "Records": [{"eventName": "ObjectCreated:Put", "s3": {"object": {"key": quote_plus(key)}}}]
    }
    return {"body": json.dumps(body), "attributes": {"SentTimestamp": str(sent_millis)}}


def upload_of(database: Database, key: str) -> tuple[str, str | None, int]:
    with database.admin.begin() as connection:
        row = connection.execute(
            text(
                "SELECT u.status, u.failure_reason, count(f.id) AS findings FROM uploads u "
                "LEFT JOIN findings f ON f.upload_id = u.id WHERE u.s3_key = :key "
                "GROUP BY u.status, u.failure_reason"
            ),
            {"key": key},
        ).one()
    return row.status, row.failure_reason, row.findings


def test_an_uploaded_port_scan_is_analyzed_and_its_finding_stored(
    database: Database, rig: Rig
) -> None:
    key = uploaded(database, rig, port_scan())

    rig.worker.handle_message(message(key))

    assert upload_of(database, key) == ("analyzed", None, 1)
    assert counter(rig.metrics, "nettriage.uploads.processed") == 1
    assert counter(rig.metrics, "nettriage.findings.created") == 1
    assert counter(rig.metrics, "nettriage.rows.parsed") == 150


def test_the_workers_span_links_to_the_upload_request_and_times_each_step(
    database: Database, rig: Rig
) -> None:
    key = uploaded(database, rig, port_scan(), traceparent=TRACEPARENT)

    rig.worker.handle_message(message(key))

    spans = {span.name: span for span in rig.spans.get_finished_spans()}
    upload = spans["analyze.upload"]
    [link] = upload.links
    assert format(link.context.trace_id, "032x") == TRACEPARENT.split("-")[1]
    assert upload.context is not None
    for step in ("analyze.parse", "analyze.detect"):
        parent = spans[step].parent
        assert parent is not None
        assert parent.span_id == upload.context.span_id


def test_a_file_that_is_not_a_flow_log_fails_its_upload_with_a_reason(
    database: Database, rig: Rig
) -> None:
    key = uploaded(database, rig, b"<html><body>not flows</body></html>\n" * 20)

    rig.worker.handle_message(message(key))

    status, reason, findings = upload_of(database, key)
    assert (status, findings) == ("failed", 0)
    assert reason
    assert counter(rig.metrics, "nettriage.upload.rejected") == 1


def test_a_file_of_another_size_than_declared_fails(database: Database, rig: Rig) -> None:
    key = uploaded(database, rig, port_scan(), declared_size=len(port_scan()) + 1)

    rig.worker.handle_message(message(key))

    assert upload_of(database, key) == ("failed", SIZE_MISMATCH, 0)


def test_a_zip_bomb_fails_early_instead_of_filling_memory(database: Database, rig: Rig) -> None:
    key = uploaded(database, rig, gzip.compress(port_scan() * 1_000))
    worker = replace(rig.worker, limits=ParseLimits(max_decompressed_bytes=1_000_000))

    worker.handle_message(message(key))

    status, reason, _ = upload_of(database, key)
    assert status == "failed"
    assert reason is not None
    assert "MB" in reason or "bytes" in reason


def test_a_second_delivery_of_the_same_event_changes_nothing(database: Database, rig: Rig) -> None:
    key = uploaded(database, rig, port_scan())
    rig.worker.handle_message(message(key))

    again = rig.worker.process(key)

    assert again == "ignored"
    assert upload_of(database, key) == ("analyzed", None, 1)


def test_objects_that_are_not_uploads_and_s3s_test_event_are_ignored(rig: Rig) -> None:
    rig.worker.handle_message({"body": json.dumps({"Event": "s3:TestEvent"})})

    outcome = rig.worker.process("somewhere/else.txt")

    assert outcome == "ignored"


def test_a_brief_database_outage_is_retried(
    database: Database, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = uploaded(database, rig, port_scan())
    calls: list[str] = []

    def flaky_claim(engine: Engine, upload_key: UploadKey) -> ClaimedUpload | None:
        calls.append(upload_key.key)
        if len(calls) == 1:
            raise OperationalError("SELECT", {}, Exception("server closed the connection"))
        return claim_upload(engine, upload_key)

    monkeypatch.setattr("nettriage.entrypoints.analyze.worker.claim_upload", flaky_claim)
    slept: list[float] = []
    worker = replace(rig.worker, sleep=slept.append)

    outcome = worker.process(key)

    assert outcome == "analyzed"
    assert slept == [1.0]
    assert upload_of(database, key) == ("analyzed", None, 1)


def test_a_longer_database_outage_raises_so_sqs_delivers_the_message_again(
    database: Database, rig: Rig
) -> None:
    key = uploaded(database, rig, port_scan())
    slept: list[float] = []
    offline = replace(rig.worker, database=create_database_engine(NO_DATABASE), sleep=slept.append)

    with pytest.raises(OperationalError):
        offline.handle_message(message(key))

    assert slept == [1.0, 3.0]
    assert upload_of(database, key) == ("pending_upload", None, 0)


def test_the_logs_never_contain_a_line_of_the_file(
    database: Database, rig: Rig, logs: io.StringIO
) -> None:
    secret = b"ACCOUNT-SECRET-7f3a not a flow record\n"
    key = uploaded(database, rig, port_scan() + secret * 40)

    rig.worker.handle_message(message(key))

    assert upload_of(database, key)[0] == "failed"
    assert "ACCOUNT-SECRET-7f3a" not in logs.getvalue()


def test_the_handler_flushes_telemetry_even_when_a_message_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flushed: list[bool] = []

    class Failing:
        def handle_message(self, record: object) -> None:
            raise RuntimeError("boom")

        def flush(self) -> None:
            flushed.append(True)

    monkeypatch.setattr(handler, "worker", lambda: Failing())

    with pytest.raises(RuntimeError):
        handler.handle({"Records": [{"body": "{}"}]}, None)

    assert flushed == [True]
```

`backend/tests/unit/analyze/test_worker_wiring.py`:
```python
"""Building the analyze worker from SSM (spec §6.8): it gets the worker role's database URL and
the uploads bucket, never a value in its environment."""

from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from nettriage.adapters.parameters import MissingParameterError
from nettriage.entrypoints.analyze.wiring import build_worker
from nettriage.platform.config import Settings

SETTINGS = Settings(
    stage="dev",
    service_name="nettriage-analyze",
    database_url_parameter="/nettriage/dev/db/app-analyze-url",
    uploads_bucket="nettriage-dev-uploads-12345678",
)


@pytest.fixture
def session() -> Iterator[boto3.session.Session]:
    with mock_aws():
        yield boto3.session.Session(region_name="eu-north-1")


def test_the_worker_is_built_from_its_own_database_url(session: boto3.session.Session) -> None:
    session.client("ssm").put_parameter(
        Name="/nettriage/dev/db/app-analyze-url",
        Value="postgresql://app_analyze:pw@ep-x-pooler.eu-central-1.aws.neon.tech/neondb",
        Type="SecureString",
    )

    worker = build_worker(SETTINGS, TracerProvider(), MeterProvider(), session)

    assert worker.database.url.username == "app_analyze"
    assert worker.database.url.host == "ep-x-pooler.eu-central-1.aws.neon.tech"
    assert worker.database.pool.size() == 1  # type: ignore[attr-defined]
    worker.flush()


def test_a_missing_database_url_is_named(session: boto3.session.Session) -> None:
    with pytest.raises(MissingParameterError, match="/nettriage/dev/db/app-analyze-url"):
        build_worker(SETTINGS, TracerProvider(), MeterProvider(), session)
```

The API's wiring test imports `MissingParameterError` from its new home:

In `backend/tests/unit/api/test_wiring.py`, replace:
```python

from nettriage.entrypoints.api.wiring import MissingParameterError, build_services
from nettriage.platform.config import Settings
```
with:
```python

from nettriage.adapters.parameters import MissingParameterError
from nettriage.entrypoints.api.wiring import build_services
from nettriage.platform.config import Settings
```

The Lambda package must carry the worker's handler:

In `tools/tests/test_build_lambda.py`, replace:
```python
        "nettriage/entrypoints/api/main.py": b"app = None\n",
        "fastapi-1.0.dist-info/WHEEL": f"Wheel-Version: 1.0\nTag: {wheel_tag}\n".encode(),
```
with:
```python
        "nettriage/entrypoints/api/main.py": b"app = None\n",
        "nettriage/entrypoints/analyze/handler.py": b"def handle(event, context): pass\n",
        "fastapi-1.0.dist-info/WHEEL": f"Wheel-Version: 1.0\nTag: {wheel_tag}\n".encode(),
```

In `tools/tests/test_build_lambda.py`, replace:
```python

def test_zip_is_reproducible(tmp_path: Path) -> None:
```
with:
```python

def test_a_package_without_the_analyze_handler_is_rejected(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    (package / "nettriage/entrypoints/analyze/handler.py").unlink()
    out = tmp_path / "backend.zip"
    write_zip(package, out)

    with pytest.raises(PackageError, match="missing nettriage/entrypoints/analyze/handler.py"):
        validate_zip(out)


def test_zip_is_reproducible(tmp_path: Path) -> None:
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && NETTRIAGE_TEST_DATABASE_URL="$(cat ../.localdb/url)" uv run python -m pytest tests/worker tests/unit/analyze tests/unit/api/test_wiring.py`
Expected: FAIL. Collection stops with 3 errors: `No module named 'nettriage.adapters.upload_objects'` in `test_analyze_worker.py`, and `No module named 'nettriage.adapters.parameters'` in `test_worker_wiring.py` and `test_wiring.py`.

Run: `just tools-test`
Expected: `1 failed, 231 passed`: `test_a_package_without_the_analyze_handler_is_rejected`.

- [ ] **Step 3: Move the SSM reads, and add the S3 reader, the metrics and the span links**

`backend/src/nettriage/adapters/parameters.py`:
```python
"""Settings and secrets from SSM Parameter Store, read once per cold start (spec §6.8): the
functions get parameter names through their environment, never values."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from botocore.config import Config

if TYPE_CHECKING:
    from types_boto3_ssm.client import SSMClient

# Fail fast inside the API's 29-second timeout, with a few quick retries (spec §3.5).
AWS_CONFIG = Config(
    connect_timeout=2, read_timeout=5, retries={"mode": "standard", "max_attempts": 3}
)


class MissingParameterError(RuntimeError):
    """An SSM parameter a function needs doesn't exist. The message names it, never a value."""


def read_parameters(ssm: SSMClient, names: Sequence[str]) -> dict[str, str]:
    response = ssm.get_parameters(Names=list(names), WithDecryption=True)
    missing = response.get("InvalidParameters", [])
    if missing:
        raise MissingParameterError(f"Missing SSM parameters: {', '.join(sorted(missing))}")
    return {parameter["Name"]: parameter["Value"] for parameter in response["Parameters"]}
```

In `backend/src/nettriage/entrypoints/api/wiring.py`, replace:
```python
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING

```
with:
```python
import json

```

In `backend/src/nettriage/entrypoints/api/wiring.py`, replace:
```python
import httpx
from botocore.config import Config

```
with:
```python
import httpx

```

In `backend/src/nettriage/entrypoints/api/wiring.py`, replace:
```python
from nettriage.adapters.oidc import OidcClient, OidcSettings
from nettriage.adapters.postgres import create_database_engine
```
with:
```python
from nettriage.adapters.oidc import OidcClient, OidcSettings
from nettriage.adapters.parameters import AWS_CONFIG, read_parameters
from nettriage.adapters.postgres import create_database_engine
```

In `backend/src/nettriage/entrypoints/api/wiring.py`, replace:
```python

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

```
with:
```python

COGNITO_TIMEOUT = httpx.Timeout(5.0)

```

`backend/src/nettriage/adapters/upload_objects.py`:
```python
"""Reading an uploaded file from the uploads bucket (spec §4.2, §9.1): its body as a stream, its
size, and the trace the upload request belonged to."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO, cast

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client


@dataclass(frozen=True)
class UploadObject:
    body: BinaryIO
    size: int
    traceparent: str | None


class UploadObjects:
    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def open(self, key: str) -> UploadObject:
        """The object's body streams: nothing is read until the parser asks for it."""
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        return UploadObject(
            body=cast(BinaryIO, response["Body"]),
            size=response["ContentLength"],
            traceparent=response.get("Metadata", {}).get("traceparent"),
        )
```

In `backend/src/nettriage/platform/metrics.py`, replace:
```python
            description="Rate-limit checks that failed and let the request through, by policy",
        )

```
with:
```python
            description="Rate-limit checks that failed and let the request through, by policy",
        )


class AnalyzeMetrics:
    """The analyze worker's metrics (spec §9.2)."""

    def __init__(self, meter_provider: MeterProvider | None = None) -> None:
        meter = (meter_provider or get_meter_provider()).get_meter("nettriage")
        self.uploads_processed = meter.create_counter(
            "nettriage.uploads.processed",
            description="Upload events handled, by outcome: analyzed, failed, ignored, duplicate",
        )
        self.processing_duration = meter.create_histogram(
            "nettriage.upload.processing.duration",
            unit="s",
            description="Time to parse, detect and store one upload",
        )
        self.rows_parsed = meter.create_counter(
            "nettriage.rows.parsed", description="Flow records parsed"
        )
        self.rows_rejected = meter.create_counter(
            "nettriage.rows.rejected", description="Lines that weren't valid flow records"
        )
        self.findings_created = meter.create_counter(
            "nettriage.findings.created", description="Findings stored, by detector and severity"
        )
        self.upload_rejected = meter.create_counter(
            "nettriage.upload.rejected", description="Uploads that failed analysis, by reason"
        )
        self.queue_message_age = meter.create_histogram(
            "nettriage.queue.message.age",
            unit="s",
            description="How long a message waited in its queue, by queue",
        )

```

In `backend/src/nettriage/platform/trace_context.py`, replace:
```python
"""The current OpenTelemetry trace and span IDs as hex strings, and the W3C traceparent."""

from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

```
with:
```python
"""The current OpenTelemetry trace and span IDs as hex strings, and W3C traceparents across
async hops (spec §9.1)."""

from opentelemetry import trace
from opentelemetry.trace import Link
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

```

In `backend/src/nettriage/platform/trace_context.py`, replace:
```python
    return carrier.get("traceparent")

```
with:
```python
    return carrier.get("traceparent")


def links_from(traceparent: str | None) -> list[Link]:
    """A link to the trace a `traceparent` names, for a span that continues it later: the
    worker's span points back at the upload request."""
    if not traceparent:
        return []
    context = TraceContextTextMapPropagator().extract({"traceparent": traceparent})
    span_context = trace.get_current_span(context).get_span_context()
    return [Link(span_context)] if span_context.is_valid else []

```

- [ ] **Step 4: Write the worker, its wiring and its handler**

`backend/src/nettriage/entrypoints/analyze/__init__.py`:
```python
"""The analyze worker (spec §3.2, §4.2): a Lambda fed by the SQS `analyze` queue."""
```

`backend/src/nettriage/entrypoints/analyze/worker.py`:
```python
"""The analyze worker (spec §4.2, §8.6). Each SQS message carries an S3 event for one uploaded
file: claim its upload, parse, detect and store the findings. A file that isn't a flow log, or
breaks a limit, fails its upload with a readable reason. A database outage gets two quick
retries; anything else unexpected raises, so SQS retries the message (3 times, then the
dead-letter queue); storing again changes nothing.

Logs never contain a line of the file (spec §9.3): failures are logged by their code."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Literal
from urllib.parse import unquote_plus

from opentelemetry.trace import Tracer
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError

from nettriage.adapters.analysis_store import (
    ClaimedUpload,
    claim_upload,
    fail_upload,
    store_analysis,
)
from nettriage.adapters.upload_objects import UploadObjects
from nettriage.application.analysis import analyze_parsed, parse_upload_key
from nettriage.application.clock import Clock
from nettriage.domain.parsing.vpc_flow_logs import FlowLogError, ParseLimits, parse_flow_log
from nettriage.platform.metrics import AnalyzeMetrics
from nettriage.platform.trace_context import links_from

logger = logging.getLogger(__name__)

type Outcome = Literal["analyzed", "failed", "ignored", "duplicate"]

SIZE_MISMATCH = "The file's size doesn't match the size declared when it was uploaded."
# Neon waking up or restarting takes seconds, and SQS would deliver the message again only after
# its 30-minute visibility timeout, so the worker first retries after these pauses (spec §8.6).
RETRY_DELAYS = (1.0, 3.0)


@dataclass
class Worker:
    database: Engine
    objects: UploadObjects
    clock: Clock
    metrics: AnalyzeMetrics
    tracer: Tracer
    flush: Callable[[], None] = lambda: None
    limits: ParseLimits = field(default_factory=ParseLimits)
    sleep: Callable[[float], None] = time.sleep

    def handle_message(self, record: Mapping[str, Any]) -> None:
        """One SQS record: an S3 notification, or S3's test event when the notification is set
        up."""
        self._record_age(record)
        body = json.loads(record["body"])
        if body.get("Event") == "s3:TestEvent":
            return
        for event in body.get("Records", []):
            if str(event.get("eventName", "")).startswith("ObjectCreated:"):
                # S3 notifications URL-encode keys, with `+` for spaces.
                self.process(unquote_plus(event["s3"]["object"]["key"]))

    def process(self, key: str) -> Outcome:
        outcome = self._with_retries(key)
        self.metrics.uploads_processed.add(1, {"outcome": outcome})
        return outcome

    def _with_retries(self, key: str) -> Outcome:
        for delay in RETRY_DELAYS:
            try:
                return self._process(key)
            except OperationalError:
                logger.warning("database_unavailable", extra={"retry_in_s": delay})
                self.sleep(delay)
        return self._process(key)

    def _process(self, key: str) -> Outcome:
        upload_key = parse_upload_key(key)
        if upload_key is None:
            logger.warning("upload_object_ignored", extra={"reason": "unknown_key"})
            return "ignored"
        claimed = claim_upload(self.database, upload_key)
        if claimed is None:
            logger.info("upload_object_ignored", extra={"reason": "not_pending"})
            return "ignored"
        upload = self.objects.open(key)
        started = time.monotonic()
        try:
            with self.tracer.start_as_current_span(
                "analyze.upload", links=links_from(upload.traceparent)
            ):
                return self._analyze(claimed, upload.size, upload.body)
        finally:
            upload.body.close()
            self.metrics.processing_duration.record(time.monotonic() - started)

    def _analyze(self, claimed: ClaimedUpload, size: int, body: BinaryIO) -> Outcome:
        if size != claimed.size_bytes:
            return self._fail(claimed, "size_mismatch", SIZE_MISMATCH)
        try:
            with self.tracer.start_as_current_span("analyze.parse"):
                parsed = parse_flow_log(body, self.limits)
        except FlowLogError as error:
            return self._fail(claimed, error.code, error.detail)
        with self.tracer.start_as_current_span("analyze.detect"):
            analysis = analyze_parsed(parsed)
        if not store_analysis(self.database, claimed, analysis, self.clock()):
            logger.info("upload_already_stored")
            return "duplicate"
        self.metrics.rows_parsed.add(analysis.rows_parsed)
        self.metrics.rows_rejected.add(analysis.rows_rejected)
        for finding in analysis.findings:
            self.metrics.findings_created.add(
                1, {"detector": finding.detector_id, "severity": finding.severity.value}
            )
        logger.info(
            "upload_analyzed",
            extra={"findings": len(analysis.findings), "rows_parsed": analysis.rows_parsed},
        )
        return "analyzed"

    def _fail(self, claimed: ClaimedUpload, code: str, reason: str) -> Outcome:
        fail_upload(self.database, claimed, reason, self.clock())
        self.metrics.upload_rejected.add(1, {"reason": code})
        logger.info("upload_failed", extra={"error_code": code})
        return "failed"

    def _record_age(self, record: Mapping[str, Any]) -> None:
        sent = record.get("attributes", {}).get("SentTimestamp")
        if sent is not None:
            age = self.clock().timestamp() - int(sent) / 1000
            self.metrics.queue_message_age.record(max(age, 0.0), {"queue": "analyze"})
```

`backend/src/nettriage/entrypoints/analyze/wiring.py`:
```python
"""Building the analyze worker in Lambda, once per cold start (spec §6.8): its database URL
comes from SSM, and its telemetry goes to the providers the handler set up (spec §9.1)."""

from __future__ import annotations

import boto3
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from nettriage.adapters.parameters import AWS_CONFIG, read_parameters
from nettriage.adapters.postgres import create_database_engine
from nettriage.adapters.upload_objects import UploadObjects
from nettriage.application.clock import system_clock
from nettriage.entrypoints.analyze.worker import Worker
from nettriage.platform.config import Settings
from nettriage.platform.metrics import AnalyzeMetrics


def build_worker(
    settings: Settings,
    tracer_provider: TracerProvider,
    meter_provider: MeterProvider,
    session: boto3.session.Session | None = None,
) -> Worker:
    session = session or boto3.session.Session()
    values = read_parameters(
        session.client("ssm", config=AWS_CONFIG), [settings.database_url_parameter]
    )

    def flush() -> None:
        # Lambda may freeze the environment right after the handler returns.
        tracer_provider.force_flush()
        meter_provider.force_flush()

    return Worker(
        database=create_database_engine(values[settings.database_url_parameter], pool_size=1),
        objects=UploadObjects(session.client("s3", config=AWS_CONFIG), settings.uploads_bucket),
        clock=system_clock,
        metrics=AnalyzeMetrics(meter_provider),
        tracer=tracer_provider.get_tracer("nettriage.analyze"),
        flush=flush,
    )
```

`backend/src/nettriage/entrypoints/analyze/handler.py`:
```python
"""The analyze Lambda's entry point (spec §3.5): `nettriage.entrypoints.analyze.handler.handle`.
SQS delivers one message per invocation (batch size 1); an exception fails the invocation, so
SQS delivers the message again."""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from typing import Any

from nettriage.entrypoints.analyze.wiring import build_worker
from nettriage.entrypoints.analyze.worker import Worker
from nettriage.platform.config import Settings
from nettriage.platform.logging import configure_logging
from nettriage.platform.telemetry import (
    create_meter_provider,
    create_tracer_provider,
    install_global_providers,
)


@cache
def worker() -> Worker:
    """Built on the first invocation of each Lambda instance, then reused."""
    settings = Settings()
    configure_logging(settings)
    tracer_provider = create_tracer_provider(settings)
    meter_provider = create_meter_provider(settings)
    install_global_providers(tracer_provider, meter_provider)
    return build_worker(settings, tracer_provider, meter_provider)


def handle(event: Mapping[str, Any], context: object) -> None:
    current = worker()
    try:
        for record in event.get("Records", []):
            current.handle_message(record)
    finally:
        current.flush()
```

- [ ] **Step 5: Package the handler**

In `tools/build_lambda.py`, replace:
```python
EXECUTABLES = frozenset({"run.sh"})
REQUIRED = ("run.sh", "collector.yaml", "nettriage/entrypoints/api/main.py")
ALLOWED_WHEEL_TAG = re.compile(
```
with:
```python
EXECUTABLES = frozenset({"run.sh"})
REQUIRED = (
    "run.sh",
    "collector.yaml",
    "nettriage/entrypoints/api/main.py",
    "nettriage/entrypoints/analyze/handler.py",
)
ALLOWED_WHEEL_TAG = re.compile(
```

- [ ] **Step 6: Run the checks**

Run: `just lint test tools-test`
Expected: lint is clean; backend `694 passed, 1 skipped`; tools `232 passed`.

- [ ] **Step 7: Commit**

```bash
git add backend/src backend/tests tools/build_lambda.py tools/tests/test_build_lambda.py
git commit -m "feat(analysis): the analyze worker: claim, stream, parse, detect and store, with span links and metrics" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The findings read API

**Files:**
- Create: `backend/src/nettriage/adapters/findings.py`, `backend/src/nettriage/adapters/attack_techniques.py`, `backend/src/nettriage/entrypoints/api/finding_schemas.py`, `backend/src/nettriage/entrypoints/api/routes/findings.py`, `backend/src/nettriage/entrypoints/api/routes/attack_techniques.py`
- Modify: `backend/src/nettriage/entrypoints/api/app.py`
- Modify (test harness): `backend/tests/tenantdata.py` (`add_finding` takes a detector)
- Test: `backend/tests/api/test_finding_routes.py`, `backend/tests/security/test_route_access.py`, `backend/tests/security/test_authorization_matrix.py`

**Interfaces:**
- Consumes:
  - Task 1's `attack_reference().notice`; Task 2's tables, `app_api`'s SELECT grants and `add_finding`.
  - Plan 3c's `OrgMember(permission)`, `OrgContext` (`org_id`, `user_id`), `CurrentSession`, `unavailable(what)`, `org_rules(request, what)` and `NotFound`; `findings:read` is already in the permission table, for every role.
  - Plan 4a's `encode_cursor(created_at, item_id)` and `decode_cursor(cursor)`.
- Produces:
  - In `nettriage.adapters.findings`:
    - `FindingStatus`, `FindingSeverity`, `FindingSummary`, `Evidence`, `FindingTechnique`, `FindingEvent`, `FindingDetail(finding, metrics, evidence, techniques, events)` and `FindingFilters(status=None, severity=None, detector=None, upload_id=None)`;
    - `list_findings(engine, org_id, user_id, filters, *, limit, before=None) -> list[FindingSummary]`, newest first;
    - `get_finding(engine, org_id, user_id, finding_id) -> FindingDetail`, which raises `NotFound`.
  - In `nettriage.adapters.attack_techniques`: `Technique(id, name, tactics, description, url, is_subtechnique, parent_id, deprecated, attack_version)` and `get_technique(engine, user_id, technique_id) -> Technique | None`.
  - `GET /api/v1/orgs/{org_id}/findings` (`findings:read`; query `status`, `severity`, `detector`, `upload`, `limit` ≤ 100, `cursor`) → `{findings, next_cursor}`.
  - `GET /api/v1/orgs/{org_id}/findings/{finding_id}` (`findings:read`) → the finding with `metrics`, `evidence`, `techniques` and `events`, and `ETag: "<version>"`.
  - `GET /api/v1/attack-techniques/{technique_id}` (signed in) → the technique with `attack_version` and MITRE's `notice`.
  - In `backend/tests/tenantdata.py`: `add_finding(connection, org_id, upload_id, severity="high", detector="port_scan") -> UUID`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/tenantdata.py`, replace:
```python
def add_finding(
    connection: Connection, org_id: UUID, upload_id: UUID, severity: str = "high"
) -> UUID:
    """A port-scan finding with one evidence row, its detector technique and its created event."""
    finding_id = uuid7()
```
with:
```python
def add_finding(
    connection: Connection,
    org_id: UUID,
    upload_id: UUID,
    severity: str = "high",
    detector: str = "port_scan",
) -> UUID:
    """A finding (a port scan unless told otherwise) with one evidence row, its detector
    technique and its created event."""
    finding_id = uuid7()
```

In `backend/tests/tenantdata.py`, replace:
```python
            "fingerprint, severity, title, src_ip, dst_ip, time_window) VALUES (:id, :org, "
            ":upload, 'port_scan', 1, :fingerprint, :severity, 'Port scan from 203.0.113.9', "
            "'203.0.113.9', '10.0.0.5', tstzrange('2026-09-28 12:00+00', '2026-09-28 12:05+00'))"
```
with:
```python
            "fingerprint, severity, title, src_ip, dst_ip, time_window) VALUES (:id, :org, "
            ":upload, :detector, 1, :fingerprint, :severity, 'Port scan from 203.0.113.9', "
            "'203.0.113.9', '10.0.0.5', tstzrange('2026-09-28 12:00+00', '2026-09-28 12:05+00'))"
```

In `backend/tests/tenantdata.py`, replace:
```python
            "severity": severity,
        },
```
with:
```python
            "severity": severity,
            "detector": detector,
        },
```

In `backend/tests/security/test_route_access.py`, replace:
```python
    ("GET", "/api/v1/orgs/{org_id}/uploads/{upload_id}"): "uploads:read",
}
```
with:
```python
    ("GET", "/api/v1/orgs/{org_id}/uploads/{upload_id}"): "uploads:read",
    ("GET", "/api/v1/orgs/{org_id}/findings"): "findings:read",
    ("GET", "/api/v1/orgs/{org_id}/findings/{finding_id}"): "findings:read",
    ("GET", "/api/v1/attack-techniques/{technique_id}"): "signed in",
}
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
from fastapi.testclient import TestClient
from tenantdata import add_invitation, add_member, add_org, add_upload, add_user

```
with:
```python
from fastapi.testclient import TestClient
from tenantdata import add_finding, add_invitation, add_member, add_org, add_upload, add_user

```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python

# method, path, body; {org}, {target}, {invitation} and {org_name} are filled in per case.
ENDPOINTS: dict[str, tuple[str, str, dict[str, Any] | None]] = {
```
with:
```python

# method, path, body; {org}, {target}, {invitation}, {org_name} and the rest are filled in per
# case.
ENDPOINTS: dict[str, tuple[str, str, dict[str, Any] | None]] = {
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
    "read upload": ("GET", "/api/v1/orgs/{org}/uploads/{upload}", None),
}
```
with:
```python
    "read upload": ("GET", "/api/v1/orgs/{org}/uploads/{upload}", None),
    "list findings": ("GET", "/api/v1/orgs/{org}/findings", None),
    "read finding": ("GET", "/api/v1/orgs/{org}/findings/{finding}", None),
    "read technique": ("GET", "/api/v1/attack-techniques/{technique}", None),
}
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
    "read upload": (200, 200, 200, 200, 404, 401),
}
```
with:
```python
    "read upload": (200, 200, 200, 200, 404, 401),
    "list findings": (200, 200, 200, 200, 404, 401),
    "read finding": (200, 200, 200, 200, 404, 401),
    "read technique": (200, 200, 200, 200, 200, 401),
}
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
        add_invitation(connection, org, people["owner"], stranger_email)
        upload = add_upload(connection, org, people["analyst"])
        connection.exec_driver_sql(
```
with:
```python
        add_invitation(connection, org, people["owner"], stranger_email)
        upload = add_upload(connection, org, people["analyst"], status="analyzed")
        finding = add_finding(connection, org, upload)
        connection.exec_driver_sql(
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
            "upload": str(upload),
            "sha256": "ab" * 32,
```
with:
```python
            "upload": str(upload),
            "finding": str(finding),
            "technique": "T1595",
            "sha256": "ab" * 32,
```

In `backend/tests/security/test_authorization_matrix.py`, replace:
```python
                    "upload": "{upload_id}",
                },
```
with:
```python
                    "upload": "{upload_id}",
                    "finding": "{finding_id}",
                    "technique": "{technique_id}",
                },
```

`backend/tests/api/test_finding_routes.py`:
```python
"""Findings and ATT&CK techniques through the API (spec §7): an org's findings newest first with
filters, one finding with its evidence, techniques and history, and the techniques themselves
with MITRE's notice."""

from uuid import UUID

import pytest
from browser import signed_in_as
from conftest import Database, FakeClock
from fastapi.testclient import TestClient
from sqlalchemy import text
from tenantdata import add_finding, add_member, add_org, add_upload, add_user

from nettriage.entrypoints.api.services import Services
from nettriage.reference import attack_reference


@pytest.fixture
def org(database: Database) -> tuple[UUID, UUID, UUID]:
    """An org, its owner, and an analyzed upload in it."""
    with database.admin.begin() as connection:
        owner = add_user(connection)
        org_id = add_org(connection, owner)
        add_member(connection, org_id, owner, "owner")
        upload = add_upload(connection, org_id, owner, status="analyzed")
    return org_id, owner, upload


@pytest.fixture
def signed_in(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID, UUID],
) -> TestClient:
    signed_in_as(database_client, services.sessions, org[1], clock())
    return database_client


def add(database: Database, org: tuple[UUID, UUID, UUID], **fields: str) -> str:
    with database.admin.begin() as connection:
        return str(add_finding(connection, org[0], org[2], **fields))


def listed(client: TestClient, org: UUID, **params: str | int) -> list[str]:
    response = client.get(f"/api/v1/orgs/{org}/findings", params=params)
    assert response.status_code == 200, response.text
    return [finding["id"] for finding in response.json()["findings"]]


def test_findings_are_listed_newest_first_page_by_page(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    ids = [add(database, org) for _ in range(3)]

    first = signed_in.get(f"/api/v1/orgs/{org[0]}/findings", params={"limit": 2}).json()
    rest = signed_in.get(
        f"/api/v1/orgs/{org[0]}/findings", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()

    assert [f["id"] for f in first["findings"] + rest["findings"]] == ids[::-1]
    assert rest["next_cursor"] is None
    summary = first["findings"][0]
    assert (summary["detector_id"], summary["severity"], summary["status"]) == (
        "port_scan",
        "high",
        "open",
    )
    assert (summary["src_ip"], summary["dst_ip"], summary["version"]) == (
        "203.0.113.9",
        "10.0.0.5",
        1,
    )
    assert summary["window_start"] == "2026-09-28T12:00:00Z"


def test_the_list_is_narrowed_by_status_severity_detector_and_upload(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    scan = add(database, org)
    low = add(database, org, severity="low")
    brute = add(database, org, detector="remote_access_bruteforce")
    with database.admin.begin() as connection:
        connection.execute(
            text("UPDATE findings SET status = 'resolved' WHERE id = :id"), {"id": scan}
        )
        other_upload = add_upload(connection, org[0], org[1], status="analyzed")
        elsewhere = str(add_finding(connection, org[0], other_upload))

    assert listed(signed_in, org[0], status="resolved") == [scan]
    assert listed(signed_in, org[0], severity="low") == [low]
    assert listed(signed_in, org[0], detector="remote_access_bruteforce") == [brute]
    assert listed(signed_in, org[0], upload=str(other_upload)) == [elsewhere]
    assert listed(signed_in, org[0], status="open", severity="high") == [elsewhere, brute]


@pytest.mark.parametrize(
    "params",
    [{"status": "closed"}, {"severity": "urgent"}, {"upload": "nope"}, {"limit": 101}],
)
def test_a_filter_outside_its_values_is_a_422(
    signed_in: TestClient, org: tuple[UUID, UUID, UUID], params: dict[str, str | int]
) -> None:
    response = signed_in.get(f"/api/v1/orgs/{org[0]}/findings", params=params)

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


def test_a_finding_is_read_with_its_evidence_techniques_and_history(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    finding_id = add(database, org)

    response = signed_in.get(f"/api/v1/orgs/{org[0]}/findings/{finding_id}")

    assert response.status_code == 200, response.text
    assert response.headers["etag"] == '"1"'
    finding = response.json()
    assert (finding["id"], finding["upload_id"], finding["metrics"]) == (
        finding_id,
        str(org[2]),
        {},
    )
    evidence = finding["evidence"]
    assert [(row["dst_port"], row["action"], row["line_no"]) for row in evidence] == [
        (22, "REJECT", 2)
    ]
    assert finding["techniques"] == [
        {
            "id": "T1595",
            "name": "Active Scanning",
            "url": "https://attack.mitre.org/techniques/T1595",
            "source": "detector",
            "rationale": None,
        }
    ]
    assert [(event["type"], event["actor_id"]) for event in finding["events"]] == [
        ("created", None)
    ]


def test_a_finding_is_not_found_through_another_org_or_by_a_stranger(
    signed_in: TestClient,
    services: Services,
    clock: FakeClock,
    database: Database,
    org: tuple[UUID, UUID, UUID],
) -> None:
    finding_id = add(database, org)
    with database.admin.begin() as connection:
        other = add_org(connection, org[1])
        add_member(connection, other, org[1], "owner")
        stranger = add_user(connection)

    through_other = signed_in.get(f"/api/v1/orgs/{other}/findings/{finding_id}")
    in_other_list = listed(signed_in, other)
    signed_in_as(signed_in, services.sessions, stranger, clock())
    by_stranger = signed_in.get(f"/api/v1/orgs/{org[0]}/findings/{finding_id}")

    assert through_other.status_code == 404
    assert in_other_list == []
    assert by_stranger.status_code == 404


def test_a_technique_is_read_with_mitres_notice(
    signed_in: TestClient,
) -> None:
    response = signed_in.get("/api/v1/attack-techniques/T1110.001")

    assert response.status_code == 200, response.text
    technique = response.json()
    assert (technique["id"], technique["name"], technique["parent_id"]) == (
        "T1110.001",
        "Password Guessing",
        "T1110",
    )
    assert technique["tactics"] == ["credential-access"]
    assert technique["is_subtechnique"] is True
    assert technique["attack_version"] == "19.2"
    assert technique["notice"] == attack_reference().notice
    assert "MITRE ATT&CK" in technique["notice"]


@pytest.mark.parametrize("technique_id", ["T9999", "T1595.999", "t1595", "T1595.1", "1595"])
def test_an_unknown_or_malformed_technique_is_a_404(
    signed_in: TestClient, technique_id: str
) -> None:
    response = signed_in.get(f"/api/v1/attack-techniques/{technique_id}")

    assert response.status_code == 404
    assert response.json()["detail"] == "No such ATT&CK technique."


def test_techniques_answer_503_while_the_database_is_down(
    client: TestClient, services: Services, clock: FakeClock
) -> None:
    signed_in_as(client, services.sessions, UUID(int=1), clock())

    response = client.get("/api/v1/attack-techniques/T1595")

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "The ATT&CK reference is unavailable right now; try again shortly."
    )
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `just test`
Expected: `32 failed, 695 passed, 1 skipped`. The failures are:
- all 15 tests in `test_finding_routes.py`;
- `test_every_route_declares_exactly_one_access_rule` (the three routes don't exist yet);
- 16 of the matrix's 18 new cases. The non-member's `list findings` and `read finding` pass already: they expect the 404 a missing route also gives.

- [ ] **Step 3: Read findings and techniques**

`backend/src/nettriage/adapters/findings.py`:
```python
"""Reading findings (spec §5.2, §7), as `app_api` in the org's transaction: lists a page at a
time, and one finding with its evidence, techniques and history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import Connection, Engine, Row, text

from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.organizations import NotFound

type FindingStatus = Literal["open", "investigating", "resolved", "false_positive"]
type FindingSeverity = Literal["low", "medium", "high", "critical"]

_SUMMARY = (
    "f.id, f.upload_id, f.detector_id, f.detector_version, f.severity, f.status, f.title, "
    "host(f.src_ip) AS src_ip, host(f.dst_ip) AS dst_ip, f.dst_port, f.protocol, "
    "lower(f.time_window) AS window_start, upper(f.time_window) AS window_end, "
    "f.assignee_id, f.version, f.created_at"
)


@dataclass(frozen=True)
class FindingSummary:
    id: UUID
    upload_id: UUID
    detector_id: str
    detector_version: int
    severity: FindingSeverity
    status: FindingStatus
    title: str
    src_ip: str
    dst_ip: str | None
    dst_port: int | None
    protocol: int | None
    window_start: datetime
    window_end: datetime
    assignee_id: UUID | None
    version: int
    created_at: datetime


@dataclass(frozen=True)
class Evidence:
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    protocol: int
    packets: int
    bytes: int
    start: datetime
    end: datetime
    action: str
    line_no: int


@dataclass(frozen=True)
class FindingTechnique:
    id: str
    name: str
    url: str
    source: str
    rationale: str | None


@dataclass(frozen=True)
class FindingEvent:
    id: UUID
    type: str
    actor_id: UUID | None
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class FindingDetail:
    finding: FindingSummary
    metrics: dict[str, Any]
    evidence: list[Evidence]
    techniques: list[FindingTechnique]
    events: list[FindingEvent]


@dataclass(frozen=True)
class FindingFilters:
    status: FindingStatus | None = None
    severity: FindingSeverity | None = None
    detector: str | None = None
    upload_id: UUID | None = None


def list_findings(
    engine: Engine,
    org_id: UUID,
    user_id: UUID,
    filters: FindingFilters,
    *,
    limit: int,
    before: tuple[datetime, UUID] | None = None,
) -> list[FindingSummary]:
    """The org's findings, newest first, narrowed by the filters that are set."""
    before_at, before_id = before or (None, None)
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        rows = connection.execute(
            text(
                f"SELECT {_SUMMARY} FROM findings f WHERE f.org_id = :org "  # noqa: S608
                "AND (CAST(:status AS text) IS NULL OR f.status = :status) "
                "AND (CAST(:severity AS text) IS NULL OR f.severity = :severity) "
                "AND (CAST(:detector AS text) IS NULL OR f.detector_id = :detector) "
                "AND (CAST(:upload AS uuid) IS NULL OR f.upload_id = :upload) "
                "AND (CAST(:before_at AS timestamptz) IS NULL OR (f.created_at, f.id) < "
                "(CAST(:before_at AS timestamptz), CAST(:before_id AS uuid))) "
                "ORDER BY f.created_at DESC, f.id DESC LIMIT :limit"
            ),
            {
                "org": org_id,
                "status": filters.status,
                "severity": filters.severity,
                "detector": filters.detector,
                "upload": filters.upload_id,
                "before_at": before_at,
                "before_id": before_id,
                "limit": limit,
            },
        ).all()
    return [_summary(row) for row in rows]


def get_finding(engine: Engine, org_id: UUID, user_id: UUID, finding_id: UUID) -> FindingDetail:
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


def _evidence(connection: Connection, finding_id: UUID) -> list[Evidence]:
    rows = connection.execute(
        text(
            "SELECT host(src_ip) AS src_ip, host(dst_ip) AS dst_ip, src_port, dst_port, "
            "protocol, packets, bytes, start_ts, end_ts, action, line_no FROM finding_evidence "
            "WHERE finding_id = :id ORDER BY start_ts, line_no"
        ),
        {"id": finding_id},
    ).all()
    return [
        Evidence(
            src_ip=row.src_ip,
            dst_ip=row.dst_ip,
            src_port=row.src_port,
            dst_port=row.dst_port,
            protocol=row.protocol,
            packets=row.packets,
            bytes=row.bytes,
            start=row.start_ts,
            end=row.end_ts,
            action=row.action,
            line_no=row.line_no,
        )
        for row in rows
    ]


def _techniques(connection: Connection, finding_id: UUID) -> list[FindingTechnique]:
    rows = connection.execute(
        text(
            "SELECT t.id, t.name, t.url, ft.source, ft.rationale FROM finding_techniques ft "
            "JOIN attack_techniques t ON t.id = ft.technique_id "
            "WHERE ft.finding_id = :id ORDER BY ft.source, t.id"
        ),
        {"id": finding_id},
    ).all()
    return [
        FindingTechnique(
            id=row.id, name=row.name, url=row.url, source=row.source, rationale=row.rationale
        )
        for row in rows
    ]


def _events(connection: Connection, finding_id: UUID) -> list[FindingEvent]:
    rows = connection.execute(
        text(
            "SELECT id, type, actor_id, payload, created_at FROM finding_events "
            "WHERE finding_id = :id ORDER BY created_at, id"
        ),
        {"id": finding_id},
    ).all()
    return [
        FindingEvent(
            id=row.id,
            type=row.type,
            actor_id=row.actor_id,
            payload=row.payload,
            created_at=row.created_at,
        )
        for row in rows
    ]


def _summary(row: Row[Any]) -> FindingSummary:
    return FindingSummary(
        id=row.id,
        upload_id=row.upload_id,
        detector_id=row.detector_id,
        detector_version=row.detector_version,
        severity=row.severity,
        status=row.status,
        title=row.title,
        src_ip=row.src_ip,
        dst_ip=row.dst_ip,
        dst_port=row.dst_port,
        protocol=row.protocol,
        window_start=row.window_start,
        window_end=row.window_end,
        assignee_id=row.assignee_id,
        version=row.version,
        created_at=row.created_at,
    )
```

`backend/src/nettriage/adapters/attack_techniques.py`:
```python
"""ATT&CK techniques from the reference table (spec §5.2, §7), the same for every org."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Engine, text

from nettriage.adapters.postgres import tenant_transaction


@dataclass(frozen=True)
class Technique:
    id: str
    name: str
    tactics: tuple[str, ...]
    description: str
    url: str
    is_subtechnique: bool
    parent_id: str | None
    deprecated: bool
    attack_version: str


def get_technique(engine: Engine, user_id: UUID, technique_id: str) -> Technique | None:
    with tenant_transaction(engine, user_id=user_id) as connection:
        row = connection.execute(
            text(
                "SELECT id, name, tactics, description, url, is_subtechnique, parent_id, "
                "deprecated, attack_version FROM attack_techniques WHERE id = :id"
            ),
            {"id": technique_id},
        ).one_or_none()
    if row is None:
        return None
    return Technique(
        id=row.id,
        name=row.name,
        tactics=tuple(row.tactics),
        description=row.description,
        url=row.url,
        is_subtechnique=row.is_subtechnique,
        parent_id=row.parent_id,
        deprecated=row.deprecated,
        attack_version=row.attack_version,
    )
```

- [ ] **Step 4: Add the routes**

`backend/src/nettriage/entrypoints/api/finding_schemas.py`:
```python
"""Response bodies for findings and ATT&CK techniques (spec §7). Findings are read-only here;
Plan 4c adds triage."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from nettriage.adapters.attack_techniques import Technique
from nettriage.adapters.findings import (
    Evidence,
    FindingDetail,
    FindingEvent,
    FindingSeverity,
    FindingStatus,
    FindingSummary,
    FindingTechnique,
)


class FindingSummaryOut(BaseModel):
    id: UUID
    upload_id: UUID
    detector_id: str
    detector_version: int
    severity: FindingSeverity
    status: FindingStatus
    title: str
    src_ip: str
    dst_ip: str | None
    dst_port: int | None
    protocol: int | None
    window_start: datetime
    window_end: datetime
    assignee_id: UUID | None
    version: int
    created_at: datetime

    @classmethod
    def of(cls, finding: FindingSummary) -> FindingSummaryOut:
        return cls(**vars(finding))


class FindingsOut(BaseModel):
    findings: list[FindingSummaryOut]
    next_cursor: str | None


class EvidenceOut(BaseModel):
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    protocol: int
    packets: int
    bytes: int
    start: datetime
    end: datetime
    action: str
    line_no: int

    @classmethod
    def of(cls, evidence: Evidence) -> EvidenceOut:
        return cls(**vars(evidence))


class FindingTechniqueOut(BaseModel):
    id: str
    name: str
    url: str
    source: str
    rationale: str | None

    @classmethod
    def of(cls, technique: FindingTechnique) -> FindingTechniqueOut:
        return cls(**vars(technique))


class FindingEventOut(BaseModel):
    id: UUID
    type: str
    actor_id: UUID | None
    payload: dict[str, Any]
    created_at: datetime

    @classmethod
    def of(cls, event: FindingEvent) -> FindingEventOut:
        return cls(**vars(event))


class FindingOut(FindingSummaryOut):
    metrics: dict[str, Any]
    evidence: list[EvidenceOut]
    techniques: list[FindingTechniqueOut]
    events: list[FindingEventOut]

    @classmethod
    def of_detail(cls, detail: FindingDetail) -> FindingOut:
        return cls(
            **vars(detail.finding),
            metrics=detail.metrics,
            evidence=[EvidenceOut.of(evidence) for evidence in detail.evidence],
            techniques=[FindingTechniqueOut.of(technique) for technique in detail.techniques],
            events=[FindingEventOut.of(event) for event in detail.events],
        )


class TechniqueOut(BaseModel):
    id: str
    name: str
    tactics: list[str]
    description: str
    url: str
    is_subtechnique: bool
    parent_id: str | None
    deprecated: bool
    attack_version: str
    # MITRE's terms of use: its notice travels with the data (spec §11.9).
    notice: str

    @classmethod
    def of(cls, technique: Technique, notice: str) -> TechniqueOut:
        return cls(
            id=technique.id,
            name=technique.name,
            tactics=list(technique.tactics),
            description=technique.description,
            url=technique.url,
            is_subtechnique=technique.is_subtechnique,
            parent_id=technique.parent_id,
            deprecated=technique.deprecated,
            attack_version=technique.attack_version,
            notice=notice,
        )
```

`backend/src/nettriage/entrypoints/api/routes/findings.py`:
```python
"""Findings (spec §7): list an org's findings a page at a time, and read one with its evidence,
techniques and history. Triage (status, assignee, comments) is Plan 4c."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from nettriage.adapters.findings import (
    FindingFilters,
    FindingSeverity,
    FindingStatus,
    get_finding,
    list_findings,
)
from nettriage.entrypoints.api.access import OrgContext, OrgMember
from nettriage.entrypoints.api.cursors import decode_cursor, encode_cursor
from nettriage.entrypoints.api.finding_schemas import FindingOut, FindingsOut, FindingSummaryOut
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.services import get_services

router = APIRouter(prefix="/v1/orgs/{org_id}/findings")


@router.get("")
def findings(
    request: Request,
    org_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("findings:read"))],
    status: FindingStatus | None = None,
    severity: FindingSeverity | None = None,
    detector: Annotated[str | None, Query(max_length=50)] = None,
    upload: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> FindingsOut:
    """The org's findings, newest first. Filters: status, severity, detector, upload."""
    before = decode_cursor(cursor) if cursor else None
    filters = FindingFilters(status=status, severity=severity, detector=detector, upload_id=upload)
    with org_rules(request, "The finding list"):
        found = list_findings(
            get_services(request).database,
            org.org_id,
            org.user_id,
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


@router.get("/{finding_id}")
def finding(
    request: Request,
    response: Response,
    org_id: UUID,
    finding_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("findings:read"))],
) -> FindingOut:
    """One finding with its evidence, techniques and events. Its `ETag` is the version that
    triage (Plan 4c) will require in `If-Match`."""
    with org_rules(request, "This finding"):
        detail = get_finding(get_services(request).database, org.org_id, org.user_id, finding_id)
    response.headers["ETag"] = f'"{detail.finding.version}"'
    return FindingOut.of_detail(detail)
```

`backend/src/nettriage/entrypoints/api/routes/attack_techniques.py`:
```python
"""ATT&CK techniques (spec §7): reference data for anyone signed in, with MITRE's notice."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from nettriage.adapters.attack_techniques import get_technique
from nettriage.entrypoints.api.access import CurrentSession, unavailable
from nettriage.entrypoints.api.finding_schemas import TechniqueOut
from nettriage.entrypoints.api.services import get_services
from nettriage.reference import attack_reference

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/attack-techniques")

TECHNIQUE_ID = re.compile(r"T\d{4}(\.\d{3})?")


@router.get("/{technique_id}")
def technique(request: Request, technique_id: str, session: CurrentSession) -> TechniqueOut:
    """One ATT&CK technique (`T1046`, `T1110.001`) from the version NetTriage ships."""
    if not TECHNIQUE_ID.fullmatch(technique_id):
        raise HTTPException(404, detail="No such ATT&CK technique.")
    try:
        found = get_technique(get_services(request).database, session.user_id, technique_id)
    except SQLAlchemyError:
        logger.exception("technique_read_failed")
        raise unavailable("The ATT&CK reference") from None
    if found is None:
        raise HTTPException(404, detail="No such ATT&CK technique.")
    return TechniqueOut.of(found, attack_reference().notice)
```

In `backend/src/nettriage/entrypoints/api/app.py`, replace:
```python
from nettriage.entrypoints.api.routes import (
    auth,
    health,
```
with:
```python
from nettriage.entrypoints.api.routes import (
    attack_techniques,
    auth,
    findings,
    health,
```

In `backend/src/nettriage/entrypoints/api/app.py`, replace:
```python
    app.middleware("http")(add_rate_limit_headers)
    for module in (health, auth, me, orgs, members, invitations, uploads):
        app.include_router(module.router, prefix="/api")
```
with:
```python
    app.middleware("http")(add_rate_limit_headers)
    for module in (
        health,
        auth,
        me,
        orgs,
        members,
        invitations,
        uploads,
        findings,
        attack_techniques,
    ):
        app.include_router(module.router, prefix="/api")
```

- [ ] **Step 5: Run the checks**

Run: `just lint test`
Expected: lint is clean, and `727 passed, 1 skipped`.

- [ ] **Step 6: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(findings): list and read findings with evidence, techniques and history; ATT&CK techniques with MITRE's notice" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: The queues, the notification and the worker's Lambda

**Files:**
- Create: `infra/modules/pipeline/queues.tf`, `infra/modules/pipeline/analyze.tf`
- Modify: `infra/modules/pipeline/variables.tf`, `infra/envs/dev/main.tf`, `.checkov.yaml`, `tools/deploy/preflight.py`
- Test: `infra/modules/pipeline/tests/pipeline.tftest.hcl`, `tools/tests/test_deploy_preflight.py`

**Interfaces:**
- Consumes:
  - Plan 4a's `pipeline` module: `aws_s3_bucket.uploads`, `local.name` (`nettriage-<stage>`) and `data.aws_caller_identity.current`.
  - The dev stage's variables `lambda_zip_path`, `app_version`, `otel_collector_layer_arn`, `grafana_otlp_endpoint` and `grafana_otlp_auth`, which the API already uses.
  - Task 2's database URL parameter for `app_analyze`, `/nettriage/dev/db/app-analyze-url`, which the deploy writes before Terraform runs.
  - Task 5's handler, `nettriage.entrypoints.analyze.handler.handle`, and the settings it reads: `NETTRIAGE_DATABASE_URL_PARAMETER`, `NETTRIAGE_UPLOADS_BUCKET`, `NETTRIAGE_SERVICE_NAME`, `NETTRIAGE_STAGE` and `NETTRIAGE_VERSION`.
- Produces:
  - `nettriage-<stage>-analyze` and `nettriage-<stage>-analyze-dlq` queues;
  - the bucket's notification;
  - the `nettriage-<stage>-analyze` function with its role, its log group and its SQS trigger.
  - `pipeline` takes the new variables `lambda_zip_path`, `app_version`, `runtime` (default `python3.14`), `otel_collector_layer_arn`, `grafana_otlp_endpoint`, `grafana_otlp_auth` and `database_url_parameter`.

- [ ] **Step 1: Write the failing tests**

In `infra/modules/pipeline/tests/pipeline.tftest.hcl`, replace:
```hcl
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
```
with:
```hcl
    defaults = {
      account_id = "123456789012"
    }
  }
  mock_data "aws_region" {
    defaults = {
      region = "eu-north-1"
    }
  }
  # Computed ARNs are short random strings by default; these resources check that the ARNs
  # they are given look like ARNs.
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/nettriage-dev-analyze"
    }
  }
  mock_resource "aws_s3_bucket" {
    defaults = {
      arn = "arn:aws:s3:::nettriage-dev-uploads-12345678"
    }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:eu-north-1:123456789012:log-group:/aws/lambda/nettriage-dev-analyze"
    }
  }
  mock_resource "aws_lambda_function" {
    defaults = {
      arn = "arn:aws:lambda:eu-north-1:123456789012:function:nettriage-dev-analyze"
    }
  }
  mock_resource "aws_sqs_queue" {
    defaults = {
      arn = "arn:aws:sqs:eu-north-1:123456789012:nettriage-dev-analyze"
      id  = "https://sqs.eu-north-1.amazonaws.com/123456789012/nettriage-dev-analyze"
    }
  }
}

override_resource {
  target = aws_sqs_queue.analyze_dlq
  values = {
    arn = "arn:aws:sqs:eu-north-1:123456789012:nettriage-dev-analyze-dlq"
  }
}

variables {
  stage                    = "dev"
  app_origin               = "https://d111111abcdef8.cloudfront.net"
  lambda_zip_path          = "../app/tests/fixtures/app.zip"
  app_version              = "test-sha"
  otel_collector_layer_arn = "arn:aws:lambda:eu-north-1:184161586896:layer:opentelemetry-collector-arm64-0_22_0:1"
  grafana_otlp_endpoint    = "https://otlp-gateway.example.grafana.net/otlp"
  grafana_otlp_auth        = "dGVzdDp0ZXN0"
  database_url_parameter   = "/nettriage/dev/db/app-analyze-url"
}

run "the_bucket_name_does_not_expose_the_account_id" {
  command = apply
```

In `infra/modules/pipeline/tests/pipeline.tftest.hcl`, replace:
```hcl
    error_message = "The API is told the switch's name."
  }
}

```
with:
```hcl
    error_message = "The API is told the switch's name."
  }
}

run "every_raw_upload_reaches_the_analyze_queue" {
  command = apply

  assert {
    condition     = one(aws_s3_bucket_notification.uploads.queue).queue_arn == aws_sqs_queue.analyze.arn
    error_message = "The uploads bucket announces uploads to the analyze queue (spec §4.2)."
  }
  assert {
    condition     = one(aws_s3_bucket_notification.uploads.queue).events == toset(["s3:ObjectCreated:*"])
    error_message = "Every finished upload is announced, however it was written."
  }
  assert {
    condition     = one(aws_s3_bucket_notification.uploads.queue).filter_prefix == "orgs/" && one(aws_s3_bucket_notification.uploads.queue).filter_suffix == "/raw"
    error_message = "Only raw uploads (orgs/{org_id}/uploads/{upload_id}/raw) are announced (spec §5.6)."
  }
  assert {
    condition     = jsondecode(aws_sqs_queue_policy.analyze.policy).Statement[0].Principal.Service == "s3.amazonaws.com" && jsondecode(aws_sqs_queue_policy.analyze.policy).Statement[0].Action == "sqs:SendMessage"
    error_message = "S3 may send to the queue, and do nothing else."
  }
  assert {
    condition     = jsondecode(aws_sqs_queue_policy.analyze.policy).Statement[0].Condition.ArnEquals["aws:SourceArn"] == aws_s3_bucket.uploads.arn && jsondecode(aws_sqs_queue_policy.analyze.policy).Statement[0].Condition.StringEquals["aws:SourceAccount"] == "123456789012"
    error_message = "Only this account's uploads bucket may send (confused-deputy protection)."
  }
}

run "a_message_failed_three_times_waits_in_the_dead_letter_queue" {
  command = apply

  assert {
    condition     = jsondecode(aws_sqs_queue.analyze.redrive_policy).maxReceiveCount == 3 && jsondecode(aws_sqs_queue.analyze.redrive_policy).deadLetterTargetArn == "arn:aws:sqs:eu-north-1:123456789012:nettriage-dev-analyze-dlq"
    error_message = "After 3 receives a message moves to the DLQ (spec §3.2, §8.6)."
  }
  assert {
    condition     = aws_sqs_queue.analyze.visibility_timeout_seconds == 6 * aws_lambda_function.analyze.timeout
    error_message = "The visibility timeout is 6 times the worker's timeout (spec §3.5)."
  }
  assert {
    condition     = aws_sqs_queue.analyze_dlq.message_retention_seconds == 1209600
    error_message = "Dead letters are kept for 14 days, SQS's maximum."
  }
  assert {
    condition     = aws_sqs_queue.analyze.sqs_managed_sse_enabled && aws_sqs_queue.analyze_dlq.sqs_managed_sse_enabled
    error_message = "Both queues are encrypted at rest with SQS-managed keys."
  }
}

run "the_worker_is_arm64_python_with_2048_mb_and_300_s" {
  command = apply

  assert {
    condition     = aws_lambda_function.analyze.function_name == "nettriage-dev-analyze"
    error_message = "Names follow nettriage-<stage>-<name>."
  }
  assert {
    condition     = aws_lambda_function.analyze.runtime == "python3.14" && aws_lambda_function.analyze.architectures == tolist(["arm64"])
    error_message = "The worker runs on python3.14, arm64."
  }
  assert {
    condition     = aws_lambda_function.analyze.memory_size == 2048 && aws_lambda_function.analyze.timeout == 300
    error_message = "The worker uses 2048 MB and a 300 s timeout (spec §3.5)."
  }
  assert {
    condition     = aws_lambda_function.analyze.handler == "nettriage.entrypoints.analyze.handler.handle"
    error_message = "The worker's handler is the analyze entry point, without the web adapter."
  }
  assert {
    condition     = aws_lambda_function.analyze.layers == tolist(["arn:aws:lambda:eu-north-1:184161586896:layer:opentelemetry-collector-arm64-0_22_0:1"])
    error_message = "The worker has the OpenTelemetry collector layer and no other."
  }
  assert {
    condition     = aws_lambda_function.analyze.environment[0].variables["NETTRIAGE_SERVICE_NAME"] == "nettriage-analyze"
    error_message = "The worker's telemetry is service.name nettriage-analyze (spec §9.1)."
  }
  assert {
    condition     = aws_lambda_function.analyze.environment[0].variables["NETTRIAGE_DATABASE_URL_PARAMETER"] == "/nettriage/dev/db/app-analyze-url"
    error_message = "The worker connects as app_analyze (spec §5.4)."
  }
  assert {
    condition     = aws_lambda_function.analyze.environment[0].variables["NETTRIAGE_UPLOADS_BUCKET"] == aws_s3_bucket.uploads.bucket
    error_message = "The worker reads from the uploads bucket."
  }
  assert {
    condition     = aws_cloudwatch_log_group.analyze.name == "/aws/lambda/nettriage-dev-analyze" && aws_cloudwatch_log_group.analyze.retention_in_days == 7
    error_message = "The worker's logs are kept 7 days (spec §9.3)."
  }
}

run "the_worker_takes_one_message_at_a_time_and_runs_at_most_twice_at_once" {
  command = apply

  assert {
    condition     = aws_lambda_event_source_mapping.analyze.event_source_arn == aws_sqs_queue.analyze.arn && aws_lambda_event_source_mapping.analyze.batch_size == 1
    error_message = "The worker reads the analyze queue one message at a time (spec §3.5)."
  }
  assert {
    condition     = one(aws_lambda_event_source_mapping.analyze.scaling_config).maximum_concurrency == 2
    error_message = "The event source mapping caps the worker at 2 concurrent runs (spec §3.5)."
  }
}

run "the_worker_may_read_uploads_its_queue_and_its_database_url_only" {
  command = apply

  assert {
    condition     = jsondecode(aws_iam_role_policy.analyze_uploads.policy).Statement[0].Action == "s3:GetObject" && jsondecode(aws_iam_role_policy.analyze_uploads.policy).Statement[0].Resource == "arn:aws:s3:::nettriage-dev-uploads-12345678/orgs/*"
    error_message = "The worker may only get objects under orgs/ (spec §6.8)."
  }
  assert {
    condition     = toset(jsondecode(aws_iam_role_policy.analyze_queue.policy).Statement[0].Action) == toset(["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"]) && jsondecode(aws_iam_role_policy.analyze_queue.policy).Statement[0].Resource == aws_sqs_queue.analyze.arn
    error_message = "The worker may only consume the analyze queue (spec §6.8)."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.analyze_parameters.policy).Statement[0].Resource == "arn:aws:ssm:eu-north-1:123456789012:parameter/nettriage/dev/db/app-analyze-url"
    error_message = "The worker may only read its own database URL (spec §6.8)."
  }
}

```

In `tools/tests/test_deploy_preflight.py`, replace:
```python
        "Lambda in eu-north-1", "IAM", "CloudFront", "Budgets", "SSM in eu-north-1",
        "DynamoDB in eu-north-1", "Cognito in eu-north-1",
    ]
```
with:
```python
        "Lambda in eu-north-1", "IAM", "CloudFront", "Budgets", "SSM in eu-north-1",
        "DynamoDB in eu-north-1", "Cognito in eu-north-1", "SQS in eu-north-1",
    ]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `just tf-check`
Expected: FAIL in the pipeline module, the last stack: `Failure! 5 passed, 1 failed, 4 skipped`.
- `every_raw_upload_reaches_the_analyze_queue` fails with `Reference to undeclared resource` (`aws_sqs_queue_policy.analyze` and the other new resources), and the four runs after it are skipped.
- A warning notes that the override target `aws_sqs_queue.analyze_dlq` doesn't exist yet.

The first five stacks pass.

Run: `just tools-test`
Expected: `1 failed, 231 passed`: `test_a_denied_service_fails_only_its_own_check`, whose list of checks now ends with `SQS in eu-north-1`.

- [ ] **Step 3: Write the queues and the worker's function**

`infra/modules/pipeline/queues.tf`:
```hcl
locals {
  # The analyze worker's timeout; SQS hides a message for 6 times as long (spec §3.5).
  analyze_timeout = 300
}

# Each finished upload becomes one message (spec §4.2). A message the worker fails three times
# moves to the dead-letter queue, kept 14 days for a look and a redrive (spec §8.6).
resource "aws_sqs_queue" "analyze_dlq" {
  name                      = "${local.name}-analyze-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "analyze" {
  name                       = "${local.name}-analyze"
  visibility_timeout_seconds = local.analyze_timeout * 6
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.analyze_dlq.arn
    maxReceiveCount     = 3
  })
}

# Only this account's uploads bucket may send to the queue.
resource "aws_sqs_queue_policy" "analyze" {
  queue_url = aws_sqs_queue.analyze.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "UploadsBucketSends"
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.analyze.arn
      Condition = {
        ArnEquals    = { "aws:SourceArn" = aws_s3_bucket.uploads.arn }
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
      }
    }]
  })
}

# Every raw upload (orgs/{org_id}/uploads/{upload_id}/raw) is announced to the queue. S3 checks
# that it may send there, so the queue policy comes first.
resource "aws_s3_bucket_notification" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  queue {
    queue_arn     = aws_sqs_queue.analyze.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "orgs/"
    filter_suffix = "/raw"
  }

  depends_on = [aws_sqs_queue_policy.analyze]
}
```

`infra/modules/pipeline/analyze.tf`:
```hcl
data "aws_region" "current" {}

locals {
  analyze = "${local.name}-analyze"
  analyze_parameter_arn = join("", [
    "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}",
    ":parameter${var.database_url_parameter}",
  ])
}

resource "aws_cloudwatch_log_group" "analyze" {
  name              = "/aws/lambda/${local.analyze}"
  retention_in_days = 7
}

resource "aws_iam_role" "analyze" {
  name = local.analyze
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "analyze_logs" {
  name = "write-own-logs"
  role = aws_iam_role.analyze.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.analyze.arn}:*"
    }]
  })
}

# The worker reads uploads and nothing else in the bucket (spec §6.8).
resource "aws_iam_role_policy" "analyze_uploads" {
  name = "read-uploads"
  role = aws_iam_role.analyze.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "s3:GetObject"
      Resource = "${aws_s3_bucket.uploads.arn}/orgs/*"
    }]
  })
}

# What the event source mapping does with the worker's role: receive, delete, and extend a
# message's visibility.
resource "aws_iam_role_policy" "analyze_queue" {
  name = "consume-analyze-queue"
  role = aws_iam_role.analyze.id
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
      Resource = aws_sqs_queue.analyze.arn
    }]
  })
}

# Its database URL (app_analyze), a SecureString the deploy writes, read once at cold start.
resource "aws_iam_role_policy" "analyze_parameters" {
  name = "read-own-parameters"
  role = aws_iam_role.analyze.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "ssm:GetParameters"
      Resource = local.analyze_parameter_arn
    }]
  })
}

# The same package as the API, with a plain handler: no web adapter (spec §3.5).
resource "aws_lambda_function" "analyze" {
  function_name    = local.analyze
  role             = aws_iam_role.analyze.arn
  runtime          = var.runtime
  architectures    = ["arm64"]
  handler          = "nettriage.entrypoints.analyze.handler.handle"
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  memory_size      = 2048
  timeout          = local.analyze_timeout
  layers           = [var.otel_collector_layer_arn]

  environment {
    variables = {
      OPENTELEMETRY_COLLECTOR_CONFIG_URI = "/var/task/collector.yaml"
      OTEL_EXPORTER_OTLP_ENDPOINT        = "http://localhost:4318"
      OTEL_EXPORTER_OTLP_PROTOCOL        = "http/protobuf"
      GRAFANA_OTLP_ENDPOINT              = var.grafana_otlp_endpoint
      GRAFANA_OTLP_AUTH                  = var.grafana_otlp_auth
      NETTRIAGE_STAGE                    = var.stage
      NETTRIAGE_VERSION                  = var.app_version
      NETTRIAGE_SERVICE_NAME             = "nettriage-analyze"
      NETTRIAGE_DATABASE_URL_PARAMETER   = var.database_url_parameter
      NETTRIAGE_UPLOADS_BUCKET           = aws_s3_bucket.uploads.bucket
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.analyze,
    aws_iam_role_policy.analyze_logs,
    aws_iam_role_policy.analyze_uploads,
    aws_iam_role_policy.analyze_queue,
    aws_iam_role_policy.analyze_parameters,
  ]
}

# One message per invocation, and at most two at once: Neon's pooler and the account's low
# Lambda concurrency quota both prefer a small cap (spec §3.5, §13.2).
resource "aws_lambda_event_source_mapping" "analyze" {
  event_source_arn = aws_sqs_queue.analyze.arn
  function_name    = aws_lambda_function.analyze.arn
  batch_size       = 1

  scaling_config {
    maximum_concurrency = 2
  }
}
```

In `infra/modules/pipeline/variables.tf`, replace:
```hcl
  description = "The app's origin (https://<distribution domain>), the only one that may PUT uploads."
}

```
with:
```hcl
  description = "The app's origin (https://<distribution domain>), the only one that may PUT uploads."
}

variable "lambda_zip_path" {
  type        = string
  description = "Path to dist/backend.zip built by tools/build_lambda.py; the API ships the same package."
}

variable "app_version" {
  type        = string
  description = "Git SHA the worker reports in its telemetry."
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
  description = "SSM SecureString with app_analyze's pooled Neon URL, written by the deploy (tools/deploy)."
}

```

- [ ] **Step 4: Wire the dev stage, update Checkov's reasons, and probe SQS in the preflight**

In `infra/envs/dev/main.tf`, replace:
```hcl
# The API's SSM parameters. identity writes the first two; the deploy writes the database URL
# (tools/deploy/config.py, db_role_url_parameter). The function gets only these names.
locals {
  oidc_parameter         = "/nettriage/dev/api/oidc"
  oidc_secret_parameter  = "/nettriage/dev/api/oidc-client-secret"
  database_url_parameter = "/nettriage/dev/db/app-api-url"
}
```
with:
```hcl
# The functions' SSM parameters. identity writes the first two; the deploy writes the database
# URLs (tools/deploy/config.py, db_role_url_parameter). Each function gets only its own names.
locals {
  oidc_parameter                 = "/nettriage/dev/api/oidc"
  oidc_secret_parameter          = "/nettriage/dev/api/oidc-client-secret"
  database_url_parameter         = "/nettriage/dev/db/app-api-url"
  analyze_database_url_parameter = "/nettriage/dev/db/app-analyze-url"
}
```

In `infra/envs/dev/main.tf`, replace:
```hcl
module "pipeline" {
  source     = "../../modules/pipeline"
  stage      = "dev"
  app_origin = "https://${module.edge.distribution_domain}"
}
```
with:
```hcl
module "pipeline" {
  source                   = "../../modules/pipeline"
  stage                    = "dev"
  app_origin               = "https://${module.edge.distribution_domain}"
  lambda_zip_path          = var.lambda_zip_path
  app_version              = var.app_version
  otel_collector_layer_arn = var.otel_collector_layer_arn
  grafana_otlp_endpoint    = var.grafana_otlp_endpoint
  grafana_otlp_auth        = var.grafana_otlp_auth
  database_url_parameter   = local.analyze_database_url_parameter
}
```

The two repo-wide Checkov skips that the worker and the notification touch state why:

In `.checkov.yaml`, replace:
```yaml
  - CKV_AWS_145    # S3 KMS encryption: SSE-S3 by design; KMS customer keys cost money (spec §6.8)
  - CKV2_AWS_62    # S3 event notifications: not needed for state or web buckets; the uploads bucket gets its notification with the analyze queue (Plan 4b)
  - CKV2_AWS_61    # S3 lifecycle on the web bucket: deploys replace its content
```
with:
```yaml
  - CKV_AWS_145    # S3 KMS encryption: SSE-S3 by design; KMS customer keys cost money (spec §6.8)
  - CKV2_AWS_62    # S3 event notifications: not needed for the state or web buckets; the uploads bucket notifies the analyze queue
  - CKV2_AWS_61    # S3 lifecycle on the web bucket: deploys replace its content
```

In `.checkov.yaml`, replace:
```yaml
  - CKV_AWS_115    # Lambda reserved concurrency: new accounts may have too low a quota (spec §13.2)
  - CKV_AWS_116    # Lambda DLQ: the API function is synchronous
  - CKV_AWS_117    # Lambda in a VPC: no VPC by design (ADR 0009)
```
with:
```yaml
  - CKV_AWS_115    # Lambda reserved concurrency: new accounts may have too low a quota (spec §13.2)
  - CKV_AWS_116    # Lambda DLQ: the API is synchronous, and the analyze worker is fed by SQS, whose redrive policy is its DLQ (spec §8.6)
  - CKV_AWS_117    # Lambda in a VPC: no VPC by design (ADR 0009)
```

In `tools/deploy/preflight.py`, replace:
```python
        _denial_probe(run, env, f"Cognito in {REGION}", ["aws", "cognito-idp", "list-user-pools", "--region", REGION, "--max-results", "1"]),
    ]
```
with:
```python
        _denial_probe(run, env, f"Cognito in {REGION}", ["aws", "cognito-idp", "list-user-pools", "--region", REGION, "--max-results", "1"]),
        _denial_probe(run, env, f"SQS in {REGION}", ["aws", "sqs", "list-queues", "--region", REGION, "--max-results", "1"]),
    ]
```

- [ ] **Step 5: Run the checks**

Run: `just tf-check tools-test`
Expected:
- `terraform test` passes in all six stacks: bootstrap 3, app 7, data 3, edge 4, identity 4 and pipeline 10 runs;
- tools `232 passed`.

Validate the dev stage as CI does:
```bash
cd infra/envs/dev && export TF_DATA_DIR=.terraform-check && terraform init -backend=false -input=false >/dev/null && terraform validate
```
Expected: `Success! The configuration is valid.`

Run Checkov as CI does:
```bash
uv run --no-project --python 3.12 --with checkov python -m checkov.main -d infra --framework terraform --config-file .checkov.yaml --quiet --compact
```
Expected: `Passed checks: 146, Failed checks: 0, Skipped checks: 3`. tflint runs in CI.

- [ ] **Step 6: Commit**

```bash
git add infra .checkov.yaml tools
git commit -m "feat(infra): the analyze queue and DLQ, the uploads bucket's notification, and the analyze worker's Lambda" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Spec amendments, runbook and README

**Files:**
- Modify: `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (§5.2, §5.4, §7, §8.6, §9.1), `docs/runbooks/setup-and-deploy.md` (B2's deploy output, B6's result, a new B7, "An upload isn't analyzed" and three Part C rows), `README.md`

- [ ] **Step 1: Amend the spec**

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `uploads` | `id`, `org_id`, `uploaded_by`, `original_filename`, `s3_key`, `size_bytes` CHECK > 0, `sha256`, `format`, `status` CHECK in (pending_upload, processing, analyzed, failed, expired), `failure_reason`, `rows_parsed`, `rows_rejected`, `rejected_samples` jsonb, `findings_truncated`, `flow_time_range` tstzrange, `processed_at`; UNIQUE `(org_id, id)` |
| `detectors` | `id` text PK, `name`, `description`, `version`, `candidate_techniques` text[]. Synced from code at deploy time |
| `attack_techniques` | `id` text PK (for example `T1046`), `stix_id`, `name`, `tactics` text[], `description`, `url`, `attack_version`, `is_subtechnique`, `parent_id`, `deprecated`. Loaded from ATT&CK STIX v19.2, with MITRE's copyright notice kept |
| `findings` | `id`, `org_id`, `upload_id`, `detector_id` → detectors, `detector_version`, `fingerprint`, `severity` CHECK in (low, medium, high, critical), `status` CHECK in (open, investigating, resolved, false_positive), `title`, `src_ip` inet, `dst_ip` inet, `dst_port` int CHECK 0–65535, `protocol` smallint, `time_window` tstzrange, `metrics` jsonb, `assignee_id` → users, `version` int; UNIQUE `(org_id, upload_id, fingerprint)`; UNIQUE `(org_id, id)` (target of child composite FKs); FK `(org_id, upload_id)` → uploads |
```
with:
```markdown
| `uploads` | `id`, `org_id`, `uploaded_by`, `original_filename`, `s3_key`, `size_bytes` CHECK > 0, `sha256`, `format`, `status` CHECK in (pending_upload, processing, analyzed, failed, expired), `failure_reason`, `rows_parsed`, `rows_rejected`, `rejected_samples` jsonb, `findings_truncated`, `flow_time_range` tstzrange, `processed_at`; UNIQUE `(org_id, id)` |
| `detectors` | `id` text PK, `name`, `description`, `version`, `candidate_techniques` text[]. Synced from code at deploy time, right after the migrations, together with the ATT&CK techniques (Plan 4b) |
| `attack_techniques` | `id` text PK (for example `T1046`), `stix_id`, `name`, `tactics` text[], `description`, `url`, `attack_version`, `is_subtechnique`, `parent_id`, `deprecated`. Loaded from ATT&CK STIX v19.2: `tools/attack_subset.py` extracts the techniques the detectors can name, and their parents, into the backend package (Plan 4b). MITRE's copyright notice is kept |
| `findings` | `id`, `org_id`, `upload_id`, `detector_id` → detectors, `detector_version`, `fingerprint`, `severity` CHECK in (low, medium, high, critical), `status` CHECK in (open, investigating, resolved, false_positive), `title`, `src_ip` inet, `dst_ip` inet, `dst_port` int CHECK 0–65535, `protocol` smallint, `time_window` tstzrange, `metrics` jsonb, `assignee_id` → users, `version` int; UNIQUE `(org_id, upload_id, fingerprint)`; UNIQUE `(org_id, id)` (target of child composite FKs); FK `(org_id, upload_id)` → uploads |
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `app_api` | `api` Lambda | The SELECT/INSERT/UPDATE its endpoints need; INSERT only on `audit_log`; DELETE only on `memberships`, `invitations`, `organizations` |
| `app_analyze` | `analyze` Lambda | SELECT `uploads`, `detectors`; UPDATE of `uploads` status columns only; INSERT `findings`, `finding_evidence`, `finding_techniques`, `finding_events`, `audit_log` |
| `app_triage` | `triage` Lambda | SELECT `findings`, `finding_evidence`, `finding_techniques`, `attack_techniques`; INSERT/UPDATE `ai_analyses`; INSERT `finding_techniques`, `finding_events`, `audit_log` |
```
with:
```markdown
| `app_api` | `api` Lambda | The SELECT/INSERT/UPDATE its endpoints need; INSERT only on `audit_log`; DELETE only on `memberships`, `invitations`, `organizations` |
| `app_analyze` | `analyze` Lambda | SELECT `uploads`, `detectors`, `attack_techniques`; UPDATE of `uploads` status and statistics columns only; INSERT `findings`, `finding_evidence`, `finding_techniques`, `finding_events`. No `audit_log` until the worker records an event worth auditing (Plan 4b) |
| `app_triage` | `triage` Lambda | SELECT `findings`, `finding_evidence`, `finding_techniques`, `attack_techniques`; INSERT/UPDATE `ai_analyses`; INSERT `finding_techniques`, `finding_events`, `audit_log` |
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `GET /api/v1/orgs/{org}/uploads/{id}` | `uploads:read` | Status and statistics |
| `GET /api/v1/orgs/{org}/findings` | `findings:read` | Filters: status, severity, detector, upload |
| `GET /api/v1/orgs/{org}/findings/{id}` | `findings:read` | With evidence, techniques, latest AI analysis, events; ETag |
| `PATCH /api/v1/orgs/{org}/findings/{id}` | `findings:triage` | Status, assignee; `If-Match` |
```
with:
```markdown
| `GET /api/v1/orgs/{org}/uploads/{id}` | `uploads:read` | Status and statistics |
| `GET /api/v1/orgs/{org}/findings` | `findings:read` | Filters: status, severity, detector, upload. Newest first, a page at a time |
| `GET /api/v1/orgs/{org}/findings/{id}` | `findings:read` | With evidence, techniques, latest AI analysis (Plan 5), events; the `ETag` is the finding's version |
| `PATCH /api/v1/orgs/{org}/findings/{id}` | `findings:triage` | Status, assignee; `If-Match` |
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| `GET /api/v1/orgs/{org}/usage` | `usage:read` | AI tokens and cost by day |
| `GET /api/v1/attack-techniques/{id}` | session | Reference data |

```
with:
```markdown
| `GET /api/v1/orgs/{org}/usage` | `usage:read` | AI tokens and cost by day |
| `GET /api/v1/attack-techniques/{id}` | session | Reference data, with MITRE's notice; 404 for an unknown or malformed ID |

```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| Worker crash or timeout | SQS retries 3 times, then the message goes to the DLQ and an alarm fires; reprocessing is idempotent |
| Neon asleep or briefly unavailable | Retry with backoff; SQS redelivers |
| Bedrock throttled or down | Backoff and retries, then `failed`, shown as "AI unavailable" with a retry button |
```
with:
```markdown
| Worker crash or timeout | SQS retries 3 times, then the message goes to the DLQ and an alarm fires; reprocessing is idempotent |
| Neon asleep or briefly unavailable | Two retries, after 1 and 3 seconds; then SQS delivers the message again after its visibility timeout (Plan 4b) |
| Bedrock throttled or down | Backoff and retries, then `failed`, shown as "AI unavailable" with a retry button |
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
| Duplicate event delivery | Ignored through state checks and unique keys |
| An S3 object that doesn't match an upload in `pending_upload` | Ignored and logged (only known uploads are processed) |

```
with:
```markdown
| Duplicate event delivery | Ignored through state checks and unique keys |
| An S3 object that doesn't match an upload in `pending_upload` (or `processing`, when SQS delivers a message again after a crash) | Ignored and logged (only known uploads are processed) |

```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
- **Auto-instrumentation:** FastAPI, psycopg and botocore.
- **Manual spans:** `analyze.parse`, `analyze.detect` and `triage.generate`.
  - `triage.generate` carries GenAI attributes: `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` and `gen_ai.response.finish_reasons`.
```
with:
```markdown
- **Auto-instrumentation:** FastAPI, psycopg and botocore.
- **Manual spans:** `analyze.upload` (the worker's span for one file, linked to the upload request's trace; Plan 4b), `analyze.parse`, `analyze.detect` and `triage.generate`.
  - `triage.generate` carries GenAI attributes: `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` and `gen_ai.response.finish_reasons`.
```

- [ ] **Step 2: Add "Try an analysis" and the dead-letter queue to the runbook**

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
3. downloads that commit's CI-built artifacts;
4. migrates the database and prints `Database migrated.` The first time, it also gives the
   app's database role a login and adds `New logins: app_api.`;
5. shows the Terraform plan, and you type `yes`;
```
with:
```markdown
3. downloads that commit's CI-built artifacts;
4. migrates the database and prints `Database migrated.`, then
   `Reference data synced: 3 detectors, 12 ATT&CK techniques.` When a function's database role
   is new, it also gives it a login and adds `New logins: <role>.` to the first line (Plan 4b
   adds `app_analyze`);
5. shows the Terraform plan, and you type `yes`;
```

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
### B6. Try an upload
Files go from the browser straight to S3, with a presigned PUT the API hands out. Analysis comes
in Plan 4b; for now an upload stays `pending_upload`. This also checks that S3 refuses any file
other than the one the API signed for (spec §13.2).
1. Do B5 steps 1 to 4 (sign in, open the console, define `api`).
```
with:
```markdown
### B6. Try an upload
Files go from the browser straight to S3, with a presigned PUT the API hands out. This checks
that S3 refuses any file other than the one the API signed for (spec §13.2). The file here isn't
a real flow log, so the worker marks the upload `failed` a few seconds after step 6; B7 uploads
one that is.
1. Do B5 steps 1 to 4 (sign in, open the console, define `api`).
```

In `docs/runbooks/setup-and-deploy.md`, replace:
````markdown
   ```
   `200`, with your upload, still `pending_upload`.
8. Delete the test org when you're done (its file is deleted from S3 after 30 days):
````
with:
````markdown
   ```
   `200`, with your upload. It shows `pending_upload` for a few seconds, then
   `status: "failed"` with `failure_reason: "the file contains no flow records"`: the file only has
   a header line.
8. Delete the test org when you're done (its file is deleted from S3 after 30 days):
````

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
fallback is a presigned POST).

```
with:
````markdown
fallback is a presigned POST).

### B7. Try an analysis
An upload is analyzed by the `analyze` worker as soon as S3 has it: the bucket notifies a queue,
and the worker parses the file, runs the detectors and stores their findings. The findings pages
come in Plan 6; until then you read them from the API.
1. Do B5 steps 1 to 4 (sign in, open the console, define `api`).
2. Create an org:
   ```js
   const org = await api("POST", "/orgs", { name: "Analysis Test" });
   ```
3. Make a small flow log: an outside address (`203.0.113.9`, reserved for examples) probing 150
   ports on one internal host within a minute, an hour ago, all rejected. Then its SHA-256:
   ```js
   const start = Math.floor(Date.now() / 1000) - 3600;
   const lines = [];
   for (let port = 1; port <= 150; port++) lines.push(`2 123456789012 eni-1 203.0.113.9 10.0.0.5 40000 ${port} 6 1 40 ${start + (port % 60)} ${start + (port % 60)} REJECT OK`);
   const file = new TextEncoder().encode(lines.join("\n") + "\n");
   const hex = (buffer) => [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
   const sha256 = hex(await crypto.subtle.digest("SHA-256", file));
   ```
4. Upload it:
   ```js
   const created = await api("POST", "/orgs/" + org.id + "/uploads", { filename: "scan.log", size_bytes: file.length, sha256 });
   (await fetch(created.upload_url, { method: "PUT", headers: created.upload_headers, body: file })).status;
   ```
   `201`, then `200`.
5. Wait 20 seconds (the worker's first run starts cold), then read the upload:
   ```js
   await api("GET", "/orgs/" + org.id + "/uploads/" + created.upload.id);
   ```
   `200`, with `status: "analyzed"`, `rows_parsed: 150`, `rows_rejected: 0`, and `flow_start`
   and `flow_end` an hour ago. If it still says `pending_upload` or `processing`, wait a minute
   and read it again; if it hasn't changed, see "An upload isn't analyzed" in Part C.
6. List the org's findings:
   ```js
   const list = await api("GET", "/orgs/" + org.id + "/findings");
   ```
   `200`, with one finding: `detector_id: "port_scan"`, `severity: "medium"`, `status: "open"`
   and the title `Port scan of 10.0.0.5 from 203.0.113.9: 150 TCP ports in 5 minutes`.
7. Read it:
   ```js
   await api("GET", "/orgs/" + org.id + "/findings/" + list.findings[0].id);
   ```
   `200`, with 50 `evidence` flows (the first 10, the last 10 and 30 between them),
   `techniques` T1595 "Active Scanning" and T1595.001 "Scanning IP Blocks", and one `created`
   event.
8. Read a technique:
   ```js
   await api("GET", "/attack-techniques/T1595");
   ```
   `200`, with `tactics: ["reconnaissance"]`, `attack_version: "19.2"` and MITRE's `notice`.
9. In Grafana, open **Explore → Tempo** and run
   `{ resource.service.name = "nettriage-analyze" && resource.deployment.environment.name = "dev" }`.
   Within a few minutes there is an `analyze.upload` trace, with `analyze.parse` and
   `analyze.detect` inside it and a link to the upload request's trace.
10. Delete the test org, which deletes its uploads and findings (the file itself is deleted from
    S3 after 30 days):
    ```js
    await api("DELETE", "/orgs/" + org.id + "?confirm_name=" + encodeURIComponent("Analysis Test"));
    ```
    `204`.

````

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
kept. A deploy never switches uploads back on.

```
with:
```markdown
kept. A deploy never switches uploads back on.

### An upload isn't analyzed
The worker gets each upload from the `nettriage-dev-analyze` queue. If it fails on one, SQS gives
it the upload again 30 minutes later, and after the third failure moves the message to
`nettriage-dev-analyze-dlq`, where it waits 14 days. The upload stays `processing` meanwhile.
1. In the AWS console, with the Region set to Europe (Stockholm), open **CloudWatch → Log groups
   → /aws/lambda/nettriage-dev-analyze**, and open the log stream from around the upload's time.
   Send Claude the errors you find (the logs never hold the file's lines).
2. After the fix is deployed, send the upload back through the worker: open **Simple Queue
   Service → nettriage-dev-analyze-dlq**, choose **Start DLQ redrive**, keep **Redrive to source
   queue(s)**, and choose **DLQ redrive**. The worker picks the upload up where it left off; no
   finding is stored twice.

An upload that stays `pending_upload` even though its PUT returned `200` never reached the worker:
send Claude the upload's `id`.

```

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
| `STOP: Database migrations failed; nothing was deployed. …` | Send the output to Claude. Nothing in AWS changed |
| `STOP: Couldn't give the database role app_api a login …` | Check the stored string (A7), then send the output to Claude |
| `FAIL  Grafana OTLP endpoint in terraform.tfvars` | Put your endpoint in `terraform.tfvars` (A3) |
```
with:
```markdown
| `STOP: Database migrations failed; nothing was deployed. …` | Send the output to Claude. Nothing in AWS changed |
| `STOP: Couldn't give the database role app_api a login …` (or `app_analyze`) | Check the stored string (A7), then send the output to Claude |
| `STOP: Syncing reference data failed; nothing in AWS changed. …` | The migrations ran, but the detectors and ATT&CK techniques weren't loaded. Send the output to Claude |
| `FAIL  Grafana OTLP endpoint in terraform.tfvars` | Put your endpoint in `terraform.tfvars` (A3) |
```

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown
| `` STOP: `terraform apply` failed with exit code 1. `` | Terraform's own error is printed above this line (`apply` shares the terminal), so scroll up and read it. If it's `Error acquiring the state lock`, see that row; otherwise send the output to Claude. Terraform may have made some changes before failing; the next plan or deploy shows what's left |
| `` STOP: `terraform init` failed with exit code 1: Error: … `` (or any other `` `<tool> <command>` failed … ``) | Read the `Error:` text. `Error acquiring the state lock` is covered by its own row; for anything else, send the output to Claude |
```
with:
```markdown
| `` STOP: `terraform apply` failed with exit code 1. `` | Terraform's own error is printed above this line (`apply` shares the terminal), so scroll up and read it. If it's `Error acquiring the state lock`, see that row; otherwise send the output to Claude. Terraform may have made some changes before failing; the next plan or deploy shows what's left |
| Terraform's `Error: … Unable to validate the following destination configurations` (the uploads bucket's notification) | S3 checked the analyze queue's new policy before it took effect. Run `just deploy-dev` again; Terraform creates what's left |
| `` STOP: `terraform init` failed with exit code 1: Error: … `` (or any other `` `<tool> <command>` failed … ``) | Read the `Error:` text. `Error acquiring the state lock` is covered by its own row; for anything else, send the output to Claude |
```

- [ ] **Step 3: Add the highlight to the README**

In `README.md`, replace:
```markdown
- **Direct-to-S3 uploads**: the API hands out a presigned PUT that signs the file's size and SHA-256, so S3 accepts only the declared file and the API never handles it.
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for the API.
- **Distributed rate limiting** with GCRA on DynamoDB, exact under concurrency ([ADR 0006](docs/adr/0006-gcra-rate-limiter.md)).
```
with:
```markdown
- **Direct-to-S3 uploads**: the API hands out a presigned PUT that signs the file's size and SHA-256, so S3 accepts only the declared file and the API never handles it.
- **Event-driven analysis**: each upload queues a worker Lambda that streams and parses the file within size, row and decompression limits, runs three detectors, and stores each finding exactly once with its evidence and MITRE ATT&CK techniques, even when a message arrives twice.
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for each function.
- **Distributed rate limiting** with GCRA on DynamoDB, exact under concurrency ([ADR 0006](docs/adr/0006-gcra-rate-limiter.md)).
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-09-26-nettriage-m1-design.md docs/runbooks/setup-and-deploy.md README.md
git commit -m "docs: analysis in the spec, runbook and README" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9 (Claude, then the owner): Pull request, deploy, and a first analysis

- [ ] **Step 1 (Claude):**
  - Run `just lint test tools-test edge-test web-check tf-check pin-check cloud-check detection-report build-lambda`; everything must pass.
  - Get a final review of the whole branch, fix what it finds, then push `plan-4b/analysis`.
  - Open the PR and watch CI.
  - Request a Copilot review.
- [ ] **Step 2 (owner):** Review the PR. Optionally run `just plan-dev` (runbook B1). Expected: `Plan: 13 to add, 1 to change, 0 to destroy`.
  - The 13 additions are the two queues, the queue policy, the bucket's notification, and the worker's log group, role, four role policies, function and SQS trigger.
  - The 1 change is the API function (its code and version).
- [ ] **Step 3 (owner):** Squash-merge the PR.
- [ ] **Step 4 (owner):** Runbook B2 (`just deploy-dev`). Expected:
  - `Database migrated. New logins: app_analyze.` (migration `0006`), then `Reference data synced: 3 detectors, 12 ATT&CK techniques.`;
  - the preflight's new `PASS  SQS in eu-north-1`;
  - the Terraform plan above;
  - fifteen smoke `PASS` lines, as before.
- [ ] **Step 5 (owner):** Runbook B7:
  - upload a generated port scan;
  - see the upload `analyzed` with 150 rows;
  - list its one `port_scan` finding and read it, with 50 evidence flows and T1595 and T1595.001;
  - read T1595 with MITRE's notice;
  - find the `analyze.upload` trace in Grafana.

## Plan 4b is done when

- [ ] `just lint test tools-test tf-check` passes locally and CI passes.
- [ ] On dev, an uploaded port scan becomes an analyzed upload with one finding, readable through the API with its evidence and techniques (runbook B7).
- [ ] The PR is merged through review, with every thread resolved.

## Spec coverage of this plan

| Spec | Covered here |
|---|---|
| §3.2 SQS `analyze` with a DLQ, `maxReceiveCount` 3 | Task 7 |
| §3.5 `analyze`: 2048 MB, 300 s, batch size 1, maximum concurrency 2, visibility 6 × timeout | Task 7 |
| §4.2 S3 event → `analyze` → findings → upload `analyzed` | Tasks 3, 4, 5 and 7 |
| §4.2 one triage message per auto-triaged finding | Plan 5 |
| §5.2 `detectors`, `attack_techniques`, `findings`, `finding_evidence`, `finding_techniques`, `finding_events` and their indexes | Task 2; how reference data is loaded amends §5.2 (Task 8) |
| §5.2 `ai_analyses` | Plan 5 |
| §5.3 row-level security on the new tenant tables | Task 2 |
| §5.4 `app_analyze` | Task 2; its exact grants amend §5.4 (Task 8) |
| §5.6 the uploads bucket's event notification to SQS `analyze` | Task 7 |
| §6.8 the `analyze` role: get uploads, consume its queue, read its SSM parameter | Task 7; sending to the triage queue is Plan 5 |
| §7 `GET …/findings` with filters, `GET …/findings/{id}` with an `ETag`, `GET /attack-techniques/{id}` | Task 6; the details amend §7 (Task 8) |
| §7 `PATCH …/findings/{id}` with `If-Match`, comments | Plan 4c |
| §8.1 parsing and its limits, §8.2 detectors | Plan 2's code, run by the worker (Tasks 3 and 5) |
| §8.6 not a flow log, limits, crash or timeout, Neon unavailable, duplicate delivery, unknown objects | Tasks 4 and 5; the retry and the `processing` claim amend §8.6 (Task 8) |
| §9.1 `analyze.parse` and `analyze.detect` spans; worker spans link to the originating trace | Task 5; `analyze.upload` amends §9.1 (Task 8) |
| §9.2 `nettriage.uploads.processed`, `upload.processing.duration`, `rows.parsed`, `rows.rejected`, `findings.created`, `queue.message.age`, `upload.rejected` | Task 5 |
| §9.3 no raw upload lines in logs | Task 5 |
| §9.6 an alarm on DLQ depth | Plan 7 (observability) |
| §11.4 authorization matrix and route declarations for the new routes | Task 6 |
| §11.9 MITRE's notice kept with the ATT&CK data | Tasks 1, 2 and 6 |
