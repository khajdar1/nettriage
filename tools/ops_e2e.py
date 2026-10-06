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
