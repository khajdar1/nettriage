"""Findings and ATT&CK techniques through the API (spec §7): an org's findings newest first with
filters, one finding with its evidence, techniques and history, and the techniques themselves
with MITRE's notice."""

from uuid import UUID, uuid7

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


def listed(client: TestClient, org: UUID, **params: str | int | list[str]) -> list[str]:
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


def test_findings_are_listed_most_severe_first_and_newest_first_within_page_by_page(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    low = add(database, org, severity="low")
    older_high = add(database, org, severity="high")
    critical = add(database, org, severity="critical")
    medium = add(database, org, severity="medium")
    newer_high = add(database, org, severity="high")

    pages = []
    cursor = None
    for _ in range(3):
        params: dict[str, str | int] = {"sort": "severity", "limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        page = signed_in.get(f"/api/v1/orgs/{org[0]}/findings", params=params).json()
        pages.append([finding["id"] for finding in page["findings"]])
        cursor = page["next_cursor"]

    assert pages == [[critical, newer_high], [older_high, medium], [low]]
    assert cursor is None


def test_a_cursor_from_one_order_is_a_422_in_the_other(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    for _ in range(2):
        add(database, org)
    url = f"/api/v1/orgs/{org[0]}/findings"
    by_severity = signed_in.get(url, params={"sort": "severity", "limit": 1}).json()
    newest = signed_in.get(url, params={"limit": 1}).json()

    assert signed_in.get(url, params={"cursor": by_severity["next_cursor"]}).status_code == 422
    assert (
        signed_in.get(url, params={"sort": "severity", "cursor": newest["next_cursor"]}).status_code
        == 422
    )


@pytest.mark.parametrize(
    "params",
    [
        {"status": "closed"},
        {"severity": "urgent"},
        {"upload": "nope"},
        {"limit": 101},
        {"sort": "oldest"},
        {"status": ["open", "closed"]},
        {"status": ["open", "investigating", "resolved", "false_positive", "open"]},
        {"assignee": "bob"},
        {"since": "yesterday"},
    ],
)
def test_a_filter_outside_its_values_is_a_422(
    signed_in: TestClient, org: tuple[UUID, UUID, UUID], params: dict[str, str | int]
) -> None:
    response = signed_in.get(f"/api/v1/orgs/{org[0]}/findings", params=params)

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


def set_finding(database: Database, finding_id: str, **columns: object) -> None:
    """Changes a finding's columns as the database owner, outside any API rule."""
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    with database.admin.begin() as connection:
        connection.execute(
            text(f"UPDATE findings SET {assignments} WHERE id = :id"),  # noqa: S608
            {"id": finding_id, **columns},
        )


def test_the_list_is_narrowed_to_any_of_several_statuses(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    opened = add(database, org)
    investigating = add(database, org)
    resolved = add(database, org)
    set_finding(database, investigating, status="investigating")
    set_finding(database, resolved, status="resolved")

    assert listed(signed_in, org[0], status=["open", "investigating"]) == [investigating, opened]
    assert listed(signed_in, org[0], status="resolved") == [resolved]


def test_the_list_is_narrowed_to_the_callers_findings_or_to_unassigned_ones(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    mine = add(database, org)
    theirs = add(database, org)
    unassigned = add(database, org)
    with database.admin.begin() as connection:
        colleague = add_user(connection)
        add_member(connection, org[0], colleague, "analyst")
    set_finding(database, mine, assignee_id=org[1])
    set_finding(database, theirs, assignee_id=colleague)

    assert listed(signed_in, org[0], assignee="me") == [mine]
    assert listed(signed_in, org[0], assignee="none") == [unassigned]


def test_the_list_is_narrowed_to_findings_detected_since_a_moment(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    older = add(database, org)
    newer = add(database, org)
    set_finding(database, older, created_at="2026-09-01T00:00:00Z")

    assert listed(signed_in, org[0], since="2026-09-02T00:00:00Z") == [newer]


def test_filters_page_with_the_severity_order_and_its_cursor(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    low = add(database, org, severity="low")
    high = add(database, org, severity="high")
    resolved_critical = add(database, org, severity="critical")
    critical = add(database, org, severity="critical")
    set_finding(database, resolved_critical, status="resolved")

    pages = []
    cursor = None
    for _ in range(3):
        params: dict[str, str | int | list[str]] = {
            "sort": "severity",
            "status": ["open", "investigating"],
            "limit": 1,
        }
        if cursor is not None:
            params["cursor"] = cursor
        page = signed_in.get(f"/api/v1/orgs/{org[0]}/findings", params=params).json()
        pages.append([finding["id"] for finding in page["findings"]])
        cursor = page["next_cursor"]

    assert pages == [[critical], [high], [low]]
    assert cursor is None


def test_each_listed_finding_says_how_its_latest_ai_analysis_went(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    explained = add(database, org)
    unexplained = add(database, org)
    add_ai_analysis(
        database,
        org[0],
        explained,
        status="failed",
        at="2026-10-01T10:00:00Z",
        error_code="provider_throttled",
    )
    add_ai_analysis(
        database,
        org[0],
        explained,
        status="succeeded",
        at="2026-10-01T11:00:00Z",
        output='{"summary": "A scan from inside."}',
    )

    response = signed_in.get(f"/api/v1/orgs/{org[0]}/findings")

    statuses = {finding["id"]: finding["ai_status"] for finding in response.json()["findings"]}
    assert statuses == {explained: "succeeded", unexplained: None}
    detail = signed_in.get(f"/api/v1/orgs/{org[0]}/findings/{explained}").json()
    assert detail["ai_status"] == "succeeded"


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


def test_a_technique_is_read_with_mitres_notice_and_license(
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
    assert technique["license"] == attack_reference().license
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


def add_ai_analysis(
    database: Database,
    org_id: UUID,
    finding_id: str,
    *,
    status: str,
    at: str,
    output: str | None = None,
    error_code: str | None = None,
) -> str:
    """An analysis by gpt-oss-20b, last updated `at`."""
    analysis_id = uuid7()
    with database.admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ai_analyses (id, org_id, finding_id, status, provider, model_id, "
                "prompt_version, output_schema_version, input_hash, output, input_tokens, "
                "output_tokens, cost_usd, latency_ms, error_code, created_at, updated_at) "
                "VALUES (:id, :org, :finding, :status, 'aws.bedrock', 'openai.gpt-oss-20b-1:0', "
                "'v1', 'v1', :hash, CAST(:output AS jsonb), 1800, 320, 0.000222, 1450, :error, "
                ":at, :at)"
            ),
            {
                "id": analysis_id,
                "org": org_id,
                "finding": finding_id,
                "status": status,
                "hash": analysis_id.hex * 2,
                "output": output,
                "error": error_code,
                "at": at,
            },
        )
    return str(analysis_id)


def test_a_finding_without_an_ai_analysis_says_so(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    finding_id = add(database, org)

    response = signed_in.get(f"/api/v1/orgs/{org[0]}/findings/{finding_id}")

    assert response.json()["ai_analysis"] is None


def test_a_finding_shows_its_latest_ai_analysis_with_what_it_said_and_cost(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID, UUID]
) -> None:
    finding_id = add(database, org)
    add_ai_analysis(
        database,
        org[0],
        finding_id,
        status="failed",
        at="2026-09-28T09:00:00Z",
        error_code="provider_throttled",
    )
    latest = add_ai_analysis(
        database,
        org[0],
        finding_id,
        status="succeeded",
        at="2026-09-28T10:00:00Z",
        output='{"summary": "A scan from outside."}',
    )

    response = signed_in.get(f"/api/v1/orgs/{org[0]}/findings/{finding_id}")

    assert response.json()["ai_analysis"] == {
        "id": latest,
        "status": "succeeded",
        "provider": "aws.bedrock",
        "model_id": "openai.gpt-oss-20b-1:0",
        "prompt_version": "v1",
        "output_schema_version": "v1",
        "output": {"summary": "A scan from outside."},
        "error_code": None,
        "input_tokens": 1800,
        "output_tokens": 320,
        "cost_usd": "0.000222",
        "latency_ms": 1450,
        "feedback": None,
        "feedback_by": None,
        "created_at": "2026-09-28T10:00:00Z",
        "updated_at": "2026-09-28T10:00:00Z",
    }
