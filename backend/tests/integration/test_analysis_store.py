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
from nettriage.adapters.findings import FindingFilters, list_findings
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


def test_an_uploads_findings_list_most_severe_first(database: Database) -> None:
    """The findings list is newest first, and an upload's findings are all equally new: they
    must list in the analysis's order, most severe first."""
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        upload = add_upload(connection, tenant.org_id, tenant.owner_id)
    key = UploadKey(org_id=tenant.org_id, upload_id=upload, key=s3_key(tenant.org_id, upload))
    claimed = claim_upload(database.app_analyze, key)
    assert claimed is not None
    both = port_scan() + port_scan(source="10.0.0.9")
    store_analysis(database.app_analyze, claimed, analyze_flow_log(io.BytesIO(both)), NOW)

    listed = list_findings(
        database.app_api, tenant.org_id, tenant.owner_id, FindingFilters(upload_id=upload), limit=10
    )

    assert [(f.severity, f.src_ip) for f in listed] == [
        ("high", "10.0.0.9"),
        ("medium", "203.0.113.9"),
    ]
