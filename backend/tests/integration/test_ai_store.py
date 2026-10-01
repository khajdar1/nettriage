"""Storing AI analyses (spec §5.2, §8.3), as `app_triage`: one row per key, a succeeded row is the
cache and is never overwritten, and success adds the AI's techniques and an event."""

from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import UUID

from conftest import Database
from sqlalchemy import text
from tenantdata import Tenant, add_tenant

from nettriage.adapters.ai_store import AnalysisRecord, find_cached, save_analysis

HASH = "c" * 64


def record(tenant: Tenant, **changes: Any) -> AnalysisRecord:
    base = AnalysisRecord(
        org_id=tenant.org_id,
        finding_id=tenant.finding_id,
        status="succeeded",
        provider="fake",
        model_id="fake-triage",
        prompt_version="v1",
        output_schema_version="v1",
        input_hash=HASH,
        output={"summary": "A scan."},
        input_tokens=900,
        output_tokens=300,
        cost_usd=Decimal("0.0021"),
        latency_ms=850,
        techniques=(("T1595", "Ports probed from outside."),),
    )
    return replace(base, **changes)


def rows(database: Database, tenant: Tenant) -> list[tuple[Any, ...]]:
    with database.admin.begin() as connection:
        return [
            tuple(row)
            for row in connection.execute(
                text(
                    "SELECT id, status, error_code, input_tokens FROM ai_analyses "
                    "WHERE finding_id = :finding AND input_hash = :hash ORDER BY id"
                ),
                {"finding": tenant.finding_id, "hash": HASH},
            )
        ]


def ai_history(database: Database, tenant: Tenant) -> tuple[list[Any], list[Any]]:
    with database.admin.begin() as connection:
        techniques = connection.execute(
            text(
                "SELECT technique_id, rationale FROM finding_techniques "
                "WHERE finding_id = :finding AND source = 'ai'"
            ),
            {"finding": tenant.finding_id},
        ).all()
        events: list[Any] = list(
            connection.execute(
                text(
                    "SELECT payload FROM finding_events "
                    "WHERE finding_id = :finding AND type = 'ai_explained'"
                ),
                {"finding": tenant.finding_id},
            ).scalars()
        )
        return [tuple(row) for row in techniques], events


def test_a_succeeded_analysis_adds_the_ais_techniques_and_an_event(database: Database) -> None:
    tenant = add_tenant(database.admin)

    analysis_id = save_analysis(database.app_triage, record(tenant))

    assert rows(database, tenant) == [(analysis_id, "succeeded", None, 900)]
    techniques, events = ai_history(database, tenant)
    assert techniques == [("T1595", "Ports probed from outside.")]
    assert events == [
        {"analysis_id": str(analysis_id), "model_id": "fake-triage", "prompt_version": "v1"}
    ]


def test_a_succeeded_analysis_is_the_cache_for_its_key(database: Database) -> None:
    tenant = add_tenant(database.admin)
    analysis_id = save_analysis(database.app_triage, record(tenant))

    def cached(**key: str) -> UUID | None:
        found = find_cached(
            database.app_triage,
            tenant.org_id,
            tenant.finding_id,
            **({"model_id": "fake-triage", "prompt_version": "v1", "input_hash": HASH} | key),
        )
        return None if found is None else found.id

    assert cached() == analysis_id
    assert cached(prompt_version="v2") is None
    assert cached(model_id="another-model") is None
    assert cached(input_hash="d" * 64) is None


def test_a_failed_attempt_is_retried_in_the_same_row(database: Database) -> None:
    tenant = add_tenant(database.admin)
    failed = save_analysis(
        database.app_triage,
        record(tenant, status="failed", output=None, error_code="provider_unavailable"),
    )

    assert (
        find_cached(
            database.app_triage,
            tenant.org_id,
            tenant.finding_id,
            model_id="fake-triage",
            prompt_version="v1",
            input_hash=HASH,
        )
        is None
    )
    succeeded = save_analysis(database.app_triage, record(tenant))

    assert succeeded == failed
    assert rows(database, tenant) == [(failed, "succeeded", None, 900)]


def test_a_succeeded_analysis_is_never_overwritten(database: Database) -> None:
    tenant = add_tenant(database.admin)
    analysis_id = save_analysis(database.app_triage, record(tenant))

    again = save_analysis(
        database.app_triage,
        record(tenant, status="failed", output=None, error_code="provider_unavailable"),
    )

    assert again == analysis_id
    assert rows(database, tenant) == [(analysis_id, "succeeded", None, 900)]
    assert len(ai_history(database, tenant)[1]) == 1


def test_a_later_analysis_updates_the_ais_rationale(database: Database) -> None:
    tenant = add_tenant(database.admin)
    save_analysis(database.app_triage, record(tenant))

    save_analysis(
        database.app_triage,
        record(tenant, prompt_version="v2", techniques=(("T1595", "A clearer reason."),)),
    )

    assert ai_history(database, tenant)[0] == [("T1595", "A clearer reason.")]
