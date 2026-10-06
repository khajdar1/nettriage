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
