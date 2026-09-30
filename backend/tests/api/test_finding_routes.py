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
