"""Explaining a finding end to end (spec §6.6, §8.3, §8.6): the finding from Postgres as
`app_triage`, budgets in DynamoDB (moto), and a scripted model."""

import io
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

import pytest
from conftest import Database, FakeClock, RuntimeTable, counter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from tenantdata import Tenant, add_finding, add_tenant

from nettriage.adapters import ai_store
from nettriage.adapters.ai_budget import AiBudget
from nettriage.adapters.ai_subjects import load_subject
from nettriage.adapters.fake_llm import FakeProvider
from nettriage.application.ai_input import canonical_json, input_hash, user_content
from nettriage.application.ai_output import output_schema
from nettriage.application.llm import ProviderError
from nettriage.application.organizations import NotFound
from nettriage.entrypoints.triage import explainer as explainer_module
from nettriage.entrypoints.triage.explainer import Explained, Explainer, ExplainLater
from nettriage.platform.metrics import AiMetrics
from nettriage.prompts import triage_prompt


def answer(**changes: Any) -> dict[str, Any]:
    """A grounded answer for `add_tenant`'s finding: 203.0.113.9 to 10.0.0.5 on port 22."""
    output: dict[str, Any] = {
        "summary": "203.0.113.9 probed port 22 on 10.0.0.5, and the probe was rejected.",
        "why_it_matters": "Probes of SSH often come before password guessing.",
        "likely_benign_explanations": ["An internet-wide scanner."],
        "recommended_next_steps": ["Keep port 22 closed to the internet."],
        "attack_techniques": [{"id": "T1595", "rationale": "An outside host probed a port."}],
        "severity_assessment": {
            "agrees_with_detector": False,
            "suggested_severity": "medium",
            "reason": "One rejected probe.",
        },
        "confidence": "medium",
        "insufficient_evidence": False,
    }
    return output | changes


UNGROUNDED = answer(recommended_next_steps=["Block 198.51.100.7 now."])


@dataclass
class Rig:
    explainer: Explainer
    model: FakeProvider
    spans: InMemorySpanExporter
    metrics: InMemoryMetricReader
    runtime_table: RuntimeTable


@pytest.fixture
def rig(database: Database, runtime_table: RuntimeTable, clock: FakeClock) -> Rig:
    spans = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    reader = InMemoryMetricReader()
    model = FakeProvider()
    explainer = Explainer(
        database=database.app_triage,
        provider=model,
        budget=AiBudget(runtime_table.client, runtime_table.name, clock),
        metrics=AiMetrics(MeterProvider(metric_readers=[reader])),
        tracer=tracer_provider.get_tracer("test"),
    )
    return Rig(explainer, model, spans, reader, runtime_table)


def analysis(database: Database, tenant: Tenant) -> dict[str, Any]:
    """The explainer's analysis, not the one `add_tenant` seeds."""
    with database.admin.begin() as connection:
        row = connection.execute(
            text(
                "SELECT status, provider, model_id, prompt_version, output_schema_version, "
                "input_hash, output, input_tokens, output_tokens, cost_usd, latency_ms, error_code "
                "FROM ai_analyses WHERE finding_id = :finding AND id <> :seeded"
            ),
            {"finding": tenant.finding_id, "seeded": tenant.analysis_id},
        ).one()
    return dict(row._mapping)


def ai_parts(database: Database, tenant: Tenant) -> tuple[list[str], list[str]]:
    with database.admin.begin() as connection:
        techniques: list[str] = list(
            connection.execute(
                text(
                    "SELECT technique_id FROM finding_techniques "
                    "WHERE finding_id = :finding AND source = 'ai'"
                ),
                {"finding": tenant.finding_id},
            ).scalars()
        )
        events: list[str] = list(
            connection.execute(
                text(
                    "SELECT type FROM finding_events WHERE finding_id = :finding "
                    "AND type = 'ai_explained'"
                ),
                {"finding": tenant.finding_id},
            ).scalars()
        )
    return techniques, events


def budget_item(rig: Rig, key: str) -> dict[str, Decimal]:
    found = rig.runtime_table.client.get_item(
        TableName=rig.runtime_table.name, Key={"pk": {"S": key}}
    )
    attributes: dict[str, Any] = found.get("Item", {})
    return {name: Decimal(value["N"]) for name, value in attributes.items() if "N" in value}


def test_a_finding_is_explained_stored_and_paid_for(database: Database, rig: Rig) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [answer()]

    explained = rig.explainer.explain(tenant.org_id, tenant.finding_id)

    assert explained.outcome == "succeeded"
    content = user_content(load_subject(database.app_triage, tenant.org_id, tenant.finding_id))
    stored = analysis(database, tenant)
    assert {key: stored[key] for key in ("status", "provider", "prompt_version")} == {
        "status": "succeeded",
        "provider": "fake",
        "prompt_version": "v1",
    }
    assert stored["input_hash"] == input_hash(content)
    assert stored["output"]["summary"] == answer()["summary"]
    assert (stored["input_tokens"], stored["output_tokens"], stored["latency_ms"]) == (
        900,
        300,
        850,
    )
    assert stored["cost_usd"] == Decimal("0.002100")
    assert ai_parts(database, tenant) == (["T1595"], ["ai_explained"])
    org_budget = budget_item(rig, f"BUDGET#{tenant.org_id}#2026-09-28")
    assert org_budget["tokens_used"] == org_budget["tokens_reserved"] == 1_200
    assert budget_item(rig, "GBUDGET#2026-09-28")["usd_used"] == Decimal("0.0021")
    assert counter(rig.metrics, "nettriage.ai.outcome") == 1


def test_the_model_gets_prompt_v1_the_schema_and_only_the_typed_fields(
    database: Database, rig: Rig
) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [answer()]

    rig.explainer.explain(tenant.org_id, tenant.finding_id)

    [call] = rig.model.calls
    content = user_content(load_subject(database.app_triage, tenant.org_id, tenant.finding_id))
    assert call.system == triage_prompt("v1")
    assert call.user_json == canonical_json(content)
    assert call.schema == output_schema()
    assert (call.max_tokens, call.temperature) == (700, 0.1)


def test_the_same_input_is_never_sent_to_the_model_twice(database: Database, rig: Rig) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [answer()]
    first = rig.explainer.explain(tenant.org_id, tenant.finding_id)

    again = rig.explainer.explain(tenant.org_id, tenant.finding_id)

    assert (again.outcome, again.analysis_id) == ("cached", first.analysis_id)
    assert len(rig.model.calls) == 1
    assert counter(rig.metrics, "nettriage.ai.cache.hits") == 1


def test_an_answer_that_fails_a_check_gets_one_repair_with_the_errors(
    database: Database, rig: Rig
) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [UNGROUNDED, answer()]

    explained = rig.explainer.explain(tenant.org_id, tenant.finding_id)

    assert explained.outcome == "succeeded"
    repair = rig.model.calls[1].user_json
    assert '"validation_errors":["recommended_next_steps mentions 198.51.100.7' in repair
    stored = analysis(database, tenant)
    assert (stored["input_tokens"], stored["output_tokens"], stored["latency_ms"]) == (
        1_800,
        600,
        1_700,
    )


def test_two_failed_checks_store_invalid_output_and_nothing_else(
    database: Database, rig: Rig
) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [UNGROUNDED, {"summary": "not the schema"}]

    explained = rig.explainer.explain(tenant.org_id, tenant.finding_id)

    assert explained.outcome == "invalid_output"
    stored = analysis(database, tenant)
    assert (stored["status"], stored["error_code"], stored["output"]) == (
        "invalid_output",
        "checks_failed",
        None,
    )
    assert ai_parts(database, tenant) == ([], [])


def test_a_provider_code_the_column_cant_hold_is_still_stored_as_failed(
    database: Database, rig: Rig
) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [ProviderError("ThrottlingException")]

    explained = rig.explainer.explain(tenant.org_id, tenant.finding_id)

    assert explained.outcome == "failed"
    stored = analysis(database, tenant)
    assert (stored["status"], stored["error_code"]) == ("failed", "provider_error")


def test_a_provider_failure_is_stored_and_its_reservation_released(
    database: Database, rig: Rig
) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [ProviderError("provider_unavailable")]

    explained = rig.explainer.explain(tenant.org_id, tenant.finding_id)

    assert explained.outcome == "failed"
    stored = analysis(database, tenant)
    assert (stored["status"], stored["error_code"], stored["input_tokens"]) == (
        "failed",
        "provider_unavailable",
        None,
    )
    assert budget_item(rig, f"BUDGET#{tenant.org_id}#2026-09-28")["tokens_reserved"] == 0
    [span] = rig.spans.get_finished_spans()
    assert (span.status.status_code, span.status.description) == (
        StatusCode.ERROR,
        "provider_unavailable",
    )


def test_a_spent_budget_skips_the_call(
    database: Database, rig: Rig, runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    tenant = add_tenant(database.admin)
    poor = replace(
        rig.explainer,
        budget=AiBudget(runtime_table.client, runtime_table.name, clock, org_daily_tokens=500),
    )

    explained = poor.explain(tenant.org_id, tenant.finding_id)

    assert explained.outcome == "skipped_budget"
    assert analysis(database, tenant)["error_code"] == "budget_exhausted_org"
    assert rig.model.calls == []


def test_a_budget_that_can_not_be_read_fails_closed(
    database: Database, rig: Rig, runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    tenant = add_tenant(database.admin)
    blind = replace(rig.explainer, budget=AiBudget(runtime_table.client, "no-such-table", clock))

    explained = blind.explain(tenant.org_id, tenant.finding_id)

    assert (explained.outcome, analysis(database, tenant)["error_code"]) == (
        "skipped_budget",
        "budget_unavailable",
    )
    assert rig.model.calls == []


def test_a_skipped_explanation_succeeds_later_in_the_same_row(
    database: Database, rig: Rig, runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    tenant = add_tenant(database.admin)
    poor = replace(
        rig.explainer,
        budget=AiBudget(runtime_table.client, runtime_table.name, clock, org_daily_tokens=500),
    )
    skipped = poor.explain(tenant.org_id, tenant.finding_id)
    rig.model.script = [answer()]

    explained = rig.explainer.explain(tenant.org_id, tenant.finding_id)

    assert (explained.outcome, explained.analysis_id) == ("succeeded", skipped.analysis_id)


def test_the_span_carries_the_gen_ai_attributes(database: Database, rig: Rig) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [answer()]

    rig.explainer.explain(tenant.org_id, tenant.finding_id)

    [span] = rig.spans.get_finished_spans()
    assert span.name == "triage.generate"
    assert dict(span.attributes or {}) == {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "fake",
        "gen_ai.request.model": "fake-triage",
        "gen_ai.usage.input_tokens": 900,
        "gen_ai.usage.output_tokens": 300,
        "gen_ai.response.finish_reasons": ("end_turn",),
    }


def test_the_logs_hold_neither_the_prompt_nor_the_answer(
    database: Database, rig: Rig, logs: io.StringIO
) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [UNGROUNDED, answer()]

    rig.explainer.explain(tenant.org_id, tenant.finding_id)

    written = logs.getvalue()
    assert "finding_explained" in written
    for private in ("198.51.100.7", "probed port 22", "203.0.113.9", "analyst assistant"):
        assert private not in written


def test_a_finding_of_another_org_is_not_found(database: Database, rig: Rig) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    with pytest.raises(NotFound):
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


def todays_usage(database: Database, tenant: Tenant) -> tuple[int, int, int]:
    with database.admin.begin() as connection:
        row = connection.execute(
            text(
                "SELECT calls, input_tokens, output_tokens FROM ai_usage "
                "WHERE org_id = :org AND day = (now() AT TIME ZONE 'UTC')::date"
            ),
            {"org": tenant.org_id},
        ).one()
    return row.calls, row.input_tokens, row.output_tokens


def test_every_model_call_is_counted_in_the_orgs_usage(database: Database, rig: Rig) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [UNGROUNDED, answer()]

    rig.explainer.explain(tenant.org_id, tenant.finding_id)

    assert todays_usage(database, tenant) == (3, 1800 + 2 * 900, 320 + 2 * 300)


def test_a_paid_call_is_counted_even_when_the_repair_is_handed_back(
    database: Database, rig: Rig
) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [UNGROUNDED, ProviderError("provider_throttled")]

    with pytest.raises(ExplainLater):
        rig.explainer.explain(tenant.org_id, tenant.finding_id, last_delivery=False)

    assert todays_usage(database, tenant) == (2, 1800 + 900, 320 + 300)
