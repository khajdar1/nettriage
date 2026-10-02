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
    "statement",
    [
        "SELECT * FROM findings",
        "SELECT title FROM findings",
        "SELECT metrics FROM findings",
        "SELECT * FROM finding_evidence",
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
