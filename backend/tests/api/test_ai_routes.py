"""The AI routes (spec §7, §8.3): re-run a finding's explanation, rate it, and read the org's AI
usage. A re-run is queued for the triage worker unless the finding's latest analysis for the
current model, prompt and input already succeeded (the owner's decision, Plan 5c)."""

import json
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID, uuid7

import pytest
from browser import signed_in_as
from conftest import APP_ORIGIN, Database, FakeClock
from fastapi.testclient import TestClient
from sqlalchemy import text
from tenantdata import add_finding, add_member, add_org, add_upload, add_user

from nettriage.adapters.ai_subjects import load_subject
from nettriage.adapters.triage_queue import TriageQueue
from nettriage.application.ai_input import input_hash, user_content
from nettriage.entrypoints.api.app import create_app
from nettriage.entrypoints.api.services import Services
from nettriage.platform.config import Settings


@pytest.fixture
def org(database: Database) -> tuple[UUID, UUID, UUID]:
    """An org, its owner, and a finding of an analyzed upload in it."""
    with database.admin.begin() as connection:
        owner = add_user(connection)
        org_id = add_org(connection, owner)
        add_member(connection, org_id, owner, "owner")
        upload = add_upload(connection, org_id, owner, status="analyzed")
        finding = add_finding(connection, org_id, upload)
    return org_id, owner, finding


@dataclass
class Owner:
    """The API, signed in as the org's owner, with the headers a state-changing request needs."""

    client: TestClient
    headers: dict[str, str]

    def post(self, path: str) -> Any:
        return self.client.post(path, headers=self.headers)

    def put(self, path: str, body: dict[str, Any]) -> Any:
        return self.client.put(path, json=body, headers=self.headers)


@pytest.fixture
def signed_in(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID, UUID],
) -> Owner:
    return Owner(database_client, signed_in_as(database_client, services.sessions, org[1], clock()))


def rerun_path(org: tuple[UUID, UUID, UUID]) -> str:
    return f"/api/v1/orgs/{org[0]}/findings/{org[2]}/ai-analyses"


def add_ai_analysis(
    database: Database, org: tuple[UUID, UUID, UUID], *, status: str, model_id: str
) -> UUID:
    """An analysis of the finding's current input, by `model_id` with prompt v1."""
    subject = load_subject(database.admin, org[0], org[2])
    analysis_id = uuid7()
    with database.admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ai_analyses (id, org_id, finding_id, status, provider, model_id, "
                "prompt_version, output_schema_version, input_hash, output, error_code) VALUES "
                "(:id, :org, :finding, :status, 'fake', :model, 'v1', 'v1', :hash, "
                "CAST(:output AS jsonb), :error)"
            ),
            {
                "id": analysis_id,
                "org": org[0],
                "finding": org[2],
                "status": status,
                "model": model_id,
                "hash": input_hash(user_content(subject)),
                "output": '{"summary": "A scan."}' if status == "succeeded" else None,
                "error": None if status == "succeeded" else "provider_throttled",
            },
        )
    return analysis_id


def queued(services: Services) -> list[dict[str, Any]]:
    """The bodies of the triage messages waiting in the queue, taken off it."""
    client, url = services.triage.client, services.triage.url
    bodies: list[dict[str, Any]] = []
    while batch := client.receive_message(QueueUrl=url, MaxNumberOfMessages=10).get("Messages"):
        for message in batch:
            bodies.append(json.loads(message["Body"]))
            client.delete_message(QueueUrl=url, ReceiptHandle=message["ReceiptHandle"])
    return bodies


def audited(database: Database, org_id: UUID) -> list[str]:
    with database.admin.begin() as connection:
        return list(
            connection.execute(
                text("SELECT action FROM audit_log WHERE org_id = :org AND action LIKE 'ai.%'"),
                {"org": org_id},
            ).scalars()
        )


def test_an_unexplained_finding_is_queued_for_the_worker(
    signed_in: Owner,
    services: Services,
    database: Database,
    org: tuple[UUID, UUID, UUID],
) -> None:
    response = signed_in.post(rerun_path(org))

    assert response.status_code == 202, response.text
    assert response.json() == {"status": "queued", "ai_analysis": None}
    assert queued(services) == [{"org_id": str(org[0]), "finding_id": str(org[2])}]
    assert audited(database, org[0]) == ["ai.rerun_requested"]


def test_a_failed_explanation_is_queued_again(
    signed_in: Owner,
    services: Services,
    database: Database,
    org: tuple[UUID, UUID, UUID],
) -> None:
    add_ai_analysis(database, org, status="failed", model_id=services.ai_model_id)

    response = signed_in.post(rerun_path(org))

    assert response.status_code == 202, response.text
    assert len(queued(services)) == 1


def test_an_explained_finding_returns_its_answer_at_no_cost(
    signed_in: Owner,
    services: Services,
    database: Database,
    org: tuple[UUID, UUID, UUID],
) -> None:
    explained = add_ai_analysis(database, org, status="succeeded", model_id=services.ai_model_id)

    response = signed_in.post(rerun_path(org))

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["ai_analysis"]["id"]) == ("explained", str(explained))
    assert body["ai_analysis"]["output"] == {"summary": "A scan."}
    assert queued(services) == []
    assert audited(database, org[0]) == []


def test_an_answer_by_another_model_is_explained_again(
    signed_in: Owner,
    services: Services,
    database: Database,
    org: tuple[UUID, UUID, UUID],
) -> None:
    add_ai_analysis(database, org, status="succeeded", model_id="an-older-model")

    response = signed_in.post(rerun_path(org))

    assert response.status_code == 202, response.text
    assert len(queued(services)) == 1


def test_re_runs_are_limited_per_user(signed_in: Owner, org: tuple[UUID, UUID, UUID]) -> None:
    answers = [signed_in.post(rerun_path(org)).status_code for _ in range(4)]

    assert answers == [202, 202, 202, 429]


def test_a_finding_of_another_org_is_not_found(
    signed_in: Owner, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    with database.admin.begin() as connection:
        stranger = add_user(connection)
        other_org = add_org(connection, stranger)
        theirs = add_finding(
            connection, other_org, add_upload(connection, other_org, stranger, "analyzed")
        )

    response = signed_in.post(f"/api/v1/orgs/{org[0]}/findings/{theirs}/ai-analyses")

    assert response.status_code == 404


def test_a_queue_that_refuses_is_a_503_and_nothing_is_audited(
    settings: Settings,
    services: Services,
    database: Database,
    org: tuple[UUID, UUID, UUID],
    clock: FakeClock,
) -> None:
    gone = TriageQueue(services.triage.client, services.triage.url.replace("triage", "gone"))
    broken = replace(services, database=database.app_api, triage=gone)
    client = TestClient(create_app(settings, broken), base_url=APP_ORIGIN)
    headers = signed_in_as(client, broken.sessions, org[1], clock())

    response = client.post(rerun_path(org), headers=headers)

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert audited(database, org[0]) == []


def feedback_path(org: tuple[UUID, UUID, UUID], analysis_id: UUID) -> str:
    return f"/api/v1/orgs/{org[0]}/findings/{org[2]}/ai-analyses/{analysis_id}/feedback"


def test_an_explanation_is_rated_by_its_reader(
    signed_in: Owner, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    explained = add_ai_analysis(database, org, status="succeeded", model_id="fake-triage")

    response = signed_in.put(feedback_path(org, explained), {"feedback": "up"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["id"], body["feedback"], body["feedback_by"]) == (
        str(explained),
        "up",
        str(org[1]),
    )


def test_a_rating_can_change_and_shows_on_the_finding(
    signed_in: Owner, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    explained = add_ai_analysis(database, org, status="succeeded", model_id="fake-triage")
    signed_in.put(feedback_path(org, explained), {"feedback": "up"})

    signed_in.put(feedback_path(org, explained), {"feedback": "down"})

    finding = signed_in.client.get(f"/api/v1/orgs/{org[0]}/findings/{org[2]}").json()
    assert finding["ai_analysis"]["feedback"] == "down"


def test_only_an_explanation_that_succeeded_can_be_rated(
    signed_in: Owner, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    failed = add_ai_analysis(database, org, status="failed", model_id="fake-triage")

    response = signed_in.put(feedback_path(org, failed), {"feedback": "down"})

    assert response.status_code == 409


def test_an_analysis_of_another_finding_is_not_found(
    signed_in: Owner, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    with database.admin.begin() as connection:
        other = add_finding(
            connection, org[0], add_upload(connection, org[0], org[1], status="analyzed")
        )
    theirs = add_ai_analysis(
        database, (org[0], org[1], other), status="succeeded", model_id="fake-triage"
    )

    response = signed_in.put(feedback_path(org, theirs), {"feedback": "up"})

    assert response.status_code == 404


@pytest.mark.parametrize(
    "body", [{"feedback": "meh"}, {"feedback": None}, {"feedback": "up", "by": "someone"}, {}]
)
def test_a_rating_is_up_or_down_and_nothing_else(
    signed_in: Owner, database: Database, org: tuple[UUID, UUID, UUID], body: dict[str, Any]
) -> None:
    explained = add_ai_analysis(database, org, status="succeeded", model_id="fake-triage")

    response = signed_in.put(feedback_path(org, explained), body)

    assert response.status_code == 422
