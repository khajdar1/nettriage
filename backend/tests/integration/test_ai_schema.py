"""AI analyses in Postgres (spec §5.2, §5.4): one row per finding, model, prompt and input, kept in
its finding's org, and the triage worker's role with only the rights it needs."""

from uuid import UUID, uuid7

import pytest
from conftest import Database
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from tenantdata import add_analysis, add_finding, add_tenant

from nettriage.adapters.audit_log import record
from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.audit import AuditEvent


def run_as_triage(database: Database, org: UUID, statement: str, /, **params: object) -> int:
    with tenant_transaction(database.app_triage, org_id=org) as connection:
        return connection.execute(text(statement), params).rowcount


INSERT_ANALYSIS = (
    "INSERT INTO ai_analyses (id, org_id, finding_id, status, provider, model_id, prompt_version, "
    "output_schema_version, input_hash, output, input_tokens, output_tokens, cost_usd, "
    "latency_ms) VALUES (:id, :org, :finding, 'succeeded', 'fake', 'fake-triage', 'v1', 'v1', "
    ':hash, \'{"summary": "x"}\', 900, 300, 0.000123, 850)'
)


def test_the_worker_can_read_a_finding_and_store_an_analysis_of_it(database: Database) -> None:
    tenant = add_tenant(database.admin)

    with tenant_transaction(database.app_triage, org_id=tenant.org_id) as connection:
        detector: str = connection.execute(
            text(
                "SELECT d.name FROM findings f JOIN detectors d ON d.id = f.detector_id "
                "WHERE f.id = :id"
            ),
            {"id": tenant.finding_id},
        ).scalar_one()
        stored = connection.execute(
            text(INSERT_ANALYSIS),
            {"id": uuid7(), "org": tenant.org_id, "finding": tenant.finding_id, "hash": "a" * 64},
        ).rowcount

    assert detector
    assert stored == 1


def test_one_finding_model_prompt_and_input_is_one_row(database: Database) -> None:
    tenant = add_tenant(database.admin)
    params = {"org": tenant.org_id, "finding": tenant.finding_id, "hash": "b" * 64}
    run_as_triage(database, tenant.org_id, INSERT_ANALYSIS, id=uuid7(), **params)

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


def test_only_a_succeeded_analysis_has_an_output(database: Database) -> None:
    tenant = add_tenant(database.admin)

    with pytest.raises(IntegrityError, match="ai_analyses_check"):
        run_as_triage(
            database,
            tenant.org_id,
            "UPDATE ai_analyses SET status = 'failed', error_code = 'provider_unavailable' "
            "WHERE id = :id",
            id=tenant.analysis_id,
        )


def test_an_analysis_can_not_point_at_another_orgs_finding(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    with pytest.raises(IntegrityError, match="foreign key"), database.admin.begin() as connection:
        add_analysis(connection, mine.org_id, theirs.finding_id)


def test_deleting_a_finding_or_its_org_removes_its_analyses(database: Database) -> None:
    tenant = add_tenant(database.admin)

    with database.admin.begin() as connection:
        connection.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": tenant.org_id})
        left: int = connection.execute(
            text("SELECT count(*) FROM ai_analyses WHERE org_id = :org"), {"org": tenant.org_id}
        ).scalar_one()
    assert left == 0


def test_the_worker_adds_the_ais_techniques_and_an_event(database: Database) -> None:
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        other = add_finding(connection, tenant.org_id, tenant.upload_id)

    added = run_as_triage(
        database,
        tenant.org_id,
        "INSERT INTO finding_techniques (finding_id, technique_id, source, org_id, rationale) "
        "VALUES (:finding, 'T1046', 'ai', :org, 'Many ports on one host.') "
        "ON CONFLICT (finding_id, technique_id, source) DO UPDATE SET rationale = "
        "EXCLUDED.rationale",
        finding=other,
        org=tenant.org_id,
    )
    explained = run_as_triage(
        database,
        tenant.org_id,
        "INSERT INTO finding_events (id, org_id, finding_id, type, payload) "
        "VALUES (:id, :org, :finding, 'ai_explained', '{}')",
        id=uuid7(),
        org=tenant.org_id,
        finding=other,
    )

    assert (added, explained) == (1, 1)


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
    [
        "SELECT * FROM memberships",
        "SELECT * FROM users",
        "SELECT * FROM uploads",
        "SELECT * FROM audit_log",
        "SELECT * FROM finding_events",
        "UPDATE findings SET status = 'resolved'",
        "UPDATE ai_analyses SET feedback = 'up'",
        "UPDATE ai_analyses SET finding_id = gen_random_uuid()",
        "DELETE FROM ai_analyses",
        "DELETE FROM finding_techniques",
        "INSERT INTO finding_events (id, org_id, finding_id, actor_id, type) "
        "SELECT gen_random_uuid(), org_id, finding_id, feedback_by, 'commented' FROM ai_analyses",
        "UPDATE detectors SET version = 99",
        "CREATE TEMP TABLE shadow (id int)",
    ],
)
def test_the_worker_role_has_only_the_rights_it_needs(database: Database, statement: str) -> None:
    tenant = add_tenant(database.admin)

    with pytest.raises(ProgrammingError, match="permission denied"):
        run_as_triage(database, tenant.org_id, statement)


def test_the_worker_role_can_not_bypass_row_level_security(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    with tenant_transaction(database.app_triage, org_id=mine.org_id) as connection:
        seen: set[UUID] = set(connection.execute(text("SELECT org_id FROM ai_analyses")).scalars())
        flags = connection.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()

    assert seen == {mine.org_id}
    assert theirs.org_id not in seen
    assert tuple(flags) == (False, False)
