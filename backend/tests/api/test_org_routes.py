"""Organizations through the API (spec §7): create, read, rename, delete, audit log."""

from uuid import UUID, uuid4

import pytest
from browser import signed_in_as
from conftest import Database, FakeClock
from fastapi.testclient import TestClient
from sqlalchemy import text
from tenantdata import add_member, add_user

from nettriage.entrypoints.api.services import Services


@pytest.fixture
def owner(database: Database) -> UUID:
    with database.admin.begin() as connection:
        return add_user(connection)


@pytest.fixture
def headers(
    database_client: TestClient, services: Services, clock: FakeClock, owner: UUID
) -> dict[str, str]:
    return signed_in_as(database_client, services.sessions, owner, clock())


def create(
    client: TestClient, headers: dict[str, str], name: str, **extra: str
) -> dict[str, object]:
    response = client.post("/api/v1/orgs", json={"name": name}, headers={**headers, **extra})
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def actions(database: Database, org: object) -> list[str]:
    with database.admin.begin() as connection:
        return list(
            connection.execute(
                text("SELECT action FROM audit_log WHERE org_id = :org ORDER BY created_at, id"),
                {"org": org},
            ).scalars()
        )


def test_creating_an_org_makes_the_caller_its_owner(
    database_client: TestClient, headers: dict[str, str], database: Database
) -> None:
    response = database_client.post(
        "/api/v1/orgs", json={"name": "  Acme Security  "}, headers=headers
    )

    assert response.status_code == 201
    org = response.json()
    assert response.headers["location"] == f"/api/v1/orgs/{org['id']}"
    assert (org["name"], org["role"], org["member_count"]) == ("Acme Security", "owner", 1)
    assert org["slug"].startswith("acme-security")
    assert actions(database, org["id"]) == ["org.created"]
    me = database_client.get("/api/v1/me").json()
    assert [m["org_id"] for m in me["memberships"]] == [org["id"]]


def test_a_retry_with_the_same_idempotency_key_returns_the_same_org(
    database_client: TestClient, headers: dict[str, str]
) -> None:
    key = {"Idempotency-Key": f"create-{uuid4().hex}"}

    first = create(database_client, headers, "Retried", **key)
    again = create(database_client, headers, "Retried", **key)

    assert again == first
    assert len(database_client.get("/api/v1/me").json()["memberships"]) == 1


def test_an_idempotency_key_reused_for_another_org_is_refused(
    database_client: TestClient, headers: dict[str, str]
) -> None:
    key = {"Idempotency-Key": f"create-{uuid4().hex}"}
    create(database_client, headers, "First", **key)

    response = database_client.post(
        "/api/v1/orgs", json={"name": "Second"}, headers={**headers, **key}
    )

    assert response.status_code == 422
    assert "different request" in response.json()["detail"]


def test_a_malformed_idempotency_key_is_refused(
    database_client: TestClient, headers: dict[str, str]
) -> None:
    response = database_client.post(
        "/api/v1/orgs", json={"name": "Org"}, headers={**headers, "Idempotency-Key": "a b"}
    )

    assert response.status_code == 422


def test_a_fourth_org_is_refused(database_client: TestClient, headers: dict[str, str]) -> None:
    for number in range(3):
        create(database_client, headers, f"Mine {number}")

    response = database_client.post("/api/v1/orgs", json={"name": "Fourth"}, headers=headers)

    assert response.status_code == 409
    assert "at most 3" in response.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {"name": ""},
        {"name": "x" * 101},
        {"name": "Org", "slug": "chosen"},
        {"name": "Org", "is_demo": True},
        {"name": "Line\nbreak"},
    ],
)
def test_an_org_body_takes_a_name_and_nothing_else(
    database_client: TestClient, headers: dict[str, str], body: dict[str, object]
) -> None:
    """No mass assignment (OWASP API3): unknown fields are refused, not ignored."""
    response = database_client.post("/api/v1/orgs", json=body, headers=headers)

    assert response.status_code == 422


def test_members_read_the_org_and_managers_rename_it(
    database_client: TestClient,
    headers: dict[str, str],
    services: Services,
    clock: FakeClock,
    database: Database,
) -> None:
    org = create(database_client, headers, "Before")
    with database.admin.begin() as connection:
        viewer = add_user(connection)
        add_member(connection, UUID(str(org["id"])), viewer, "viewer")

    renamed = database_client.patch(
        f"/api/v1/orgs/{org['id']}", json={"name": "After"}, headers=headers
    )
    viewer_headers = signed_in_as(database_client, services.sessions, viewer, clock())
    seen = database_client.get(f"/api/v1/orgs/{org['id']}")
    refused = database_client.patch(
        f"/api/v1/orgs/{org['id']}", json={"name": "Taken over"}, headers=viewer_headers
    )

    assert (renamed.status_code, renamed.json()["slug"]) == (200, org["slug"])
    assert (seen.json()["name"], seen.json()["role"], seen.json()["member_count"]) == (
        "After",
        "viewer",
        2,
    )
    assert refused.status_code == 403
    assert actions(database, org["id"]) == ["org.created", "org.renamed", "authz.denied"]


def test_deleting_an_org_needs_its_exact_name(
    database_client: TestClient, headers: dict[str, str], database: Database
) -> None:
    org = create(database_client, headers, "Doomed Org")

    path = f"/api/v1/orgs/{org['id']}"

    wrong = database_client.delete(path, params={"confirm_name": "doomed org"}, headers=headers)
    missing = database_client.delete(path, headers=headers)
    deleted = database_client.delete(path, params={"confirm_name": "Doomed Org"}, headers=headers)
    after = database_client.get(path)

    assert (wrong.status_code, missing.status_code, deleted.status_code, after.status_code) == (
        422,
        422,
        204,
        404,
    )
    assert actions(database, org["id"])[-1] == "org.deleted"


def test_the_audit_log_pages_newest_first(
    database_client: TestClient, headers: dict[str, str]
) -> None:
    org = create(database_client, headers, "Busy")
    for number in range(4):
        database_client.patch(
            f"/api/v1/orgs/{org['id']}", json={"name": f"Busy {number}"}, headers=headers
        )

    first = database_client.get(f"/api/v1/orgs/{org['id']}/audit-log", params={"limit": 3}).json()
    rest = database_client.get(
        f"/api/v1/orgs/{org['id']}/audit-log", params={"limit": 3, "cursor": first["next_cursor"]}
    ).json()

    names = [e["details"].get("name") for e in first["events"] + rest["events"]]
    assert names == ["Busy 3", "Busy 2", "Busy 1", "Busy 0", None]
    assert rest["next_cursor"] is None
    assert "ip" not in first["events"][0]


def test_a_broken_audit_cursor_is_refused(
    database_client: TestClient, headers: dict[str, str]
) -> None:
    org = create(database_client, headers, "Cursor")

    response = database_client.get(
        f"/api/v1/orgs/{org['id']}/audit-log", params={"cursor": "not-a-cursor"}
    )

    assert response.status_code == 422
