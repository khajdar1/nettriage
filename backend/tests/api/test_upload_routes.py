"""Uploads through the API (spec §4.2, §7): a presigned PUT for exactly the declared file, the
org's daily quota, Idempotency-Key, the kill switch, and reading uploads back."""

import hashlib
from dataclasses import replace
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from browser import signed_in_as
from conftest import UPLOADS_BUCKET, Database, FakeClock
from fake_idp import APP_ORIGIN
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from sqlalchemy import text
from tenantdata import add_finding, add_member, add_org, add_upload, add_user

from nettriage.adapters.kill_switch import KillSwitch
from nettriage.application.uploads import MAX_UPLOAD_BYTES, checksum_header
from nettriage.entrypoints.api.app import create_app
from nettriage.entrypoints.api.services import Services
from nettriage.platform.config import Settings
from nettriage.platform.telemetry import create_tracer_provider

SHA256 = hashlib.sha256(b"flows").hexdigest()
FILE = {"filename": "vpc-flows.log.gz", "size_bytes": 5, "sha256": SHA256.upper()}


@pytest.fixture
def org(database: Database) -> tuple[UUID, UUID]:
    with database.admin.begin() as connection:
        owner = add_user(connection)
        org_id = add_org(connection, owner)
        add_member(connection, org_id, owner, "owner")
    return org_id, owner


@pytest.fixture
def headers(
    database_client: TestClient, services: Services, clock: FakeClock, org: tuple[UUID, UUID]
) -> dict[str, str]:
    return signed_in_as(database_client, services.sessions, org[1], clock())


def post(
    client: TestClient, org: UUID, headers: dict[str, str], body: dict[str, Any] | None = None
) -> Any:
    return client.post(f"/api/v1/orgs/{org}/uploads", json=body or FILE, headers=headers)


def uploads_in(database: Database, org: UUID) -> int:
    with database.admin.begin() as connection:
        count: int = connection.execute(
            text("SELECT count(*) FROM uploads WHERE org_id = :org"), {"org": org}
        ).scalar_one()
    return count


def test_an_upload_gets_a_put_for_exactly_the_declared_file(
    database_client: TestClient,
    headers: dict[str, str],
    org: tuple[UUID, UUID],
    database: Database,
    clock: FakeClock,
) -> None:
    response = post(database_client, org[0], headers)

    assert response.status_code == 201, response.text
    created = response.json()
    upload = created["upload"]
    assert (upload["status"], upload["size_bytes"], upload["sha256"]) == (
        "pending_upload",
        5,
        SHA256,
    )
    assert upload["original_filename"] == "vpc-flows.log.gz"
    assert response.headers["location"] == f"/api/v1/orgs/{org[0]}/uploads/{upload['id']}"
    url = urlsplit(created["upload_url"])
    query = parse_qs(url.query)
    assert url.hostname == f"{UPLOADS_BUCKET}.s3.eu-north-1.amazonaws.com"
    assert url.path == f"/orgs/{org[0]}/uploads/{upload['id']}/raw"
    assert query["X-Amz-SignedHeaders"] == ["content-length;host;x-amz-checksum-sha256"]
    assert created["upload_headers"] == {"x-amz-checksum-sha256": checksum_header(SHA256)}
    assert created["expires_at"] == (clock() + timedelta(minutes=5)).isoformat().replace(
        "+00:00", "Z"
    )
    with database.admin.begin() as connection:
        audited = connection.execute(
            text("SELECT action, target_id, details FROM audit_log WHERE org_id = :org"),
            {"org": org[0]},
        ).one()
    assert tuple(audited) == ("upload.created", upload["id"], {"size_bytes": 5})


@pytest.mark.parametrize(
    "body",
    [
        {**FILE, "size_bytes": 0},
        {**FILE, "size_bytes": MAX_UPLOAD_BYTES + 1},
        {**FILE, "filename": "   "},
        {**FILE, "filename": "flows\n.log"},
        {**FILE, "filename": "x" * 256},
        {**FILE, "sha256": "zz" * 32},
        {**FILE, "sha256": SHA256[:-1]},
        {**FILE, "status": "analyzed"},
        {"filename": "flows.log", "size_bytes": 5},
    ],
)
def test_a_file_needs_a_name_a_size_up_to_25_mb_and_a_sha256(
    database_client: TestClient,
    headers: dict[str, str],
    org: tuple[UUID, UUID],
    database: Database,
    body: dict[str, Any],
) -> None:
    response = post(database_client, org[0], headers, body)

    assert response.status_code == 422
    assert uploads_in(database, org[0]) == 0


def test_an_org_may_start_five_uploads_at_once_and_then_waits(
    database_client: TestClient, headers: dict[str, str], org: tuple[UUID, UUID]
) -> None:
    statuses = [post(database_client, org[0], headers).status_code for _ in range(6)]
    limited = post(database_client, org[0], headers)

    assert statuses == [201] * 5 + [429]
    assert int(limited.headers["retry-after"]) > 0


def test_a_retry_with_the_same_idempotency_key_returns_the_same_upload(
    database_client: TestClient, headers: dict[str, str], org: tuple[UUID, UUID], database: Database
) -> None:
    key = {"Idempotency-Key": f"upload-{uuid4().hex}"}

    first = post(database_client, org[0], {**headers, **key})
    again = post(database_client, org[0], {**headers, **key})

    assert (first.status_code, again.status_code) == (201, 201)
    assert again.json() == first.json()
    assert uploads_in(database, org[0]) == 1


def test_a_key_that_created_an_org_can_not_create_an_upload(
    database_client: TestClient, headers: dict[str, str], org: tuple[UUID, UUID]
) -> None:
    key = {"Idempotency-Key": f"shared-{uuid4().hex}"}
    created_org = database_client.post(
        "/api/v1/orgs", json={"name": "Another"}, headers={**headers, **key}
    )

    reused = post(database_client, org[0], {**headers, **key})

    assert (created_org.status_code, reused.status_code) == (201, 422)


def test_paused_uploads_are_refused_and_nothing_is_created(
    settings: Settings,
    services: Services,
    database: Database,
    clock: FakeClock,
    org: tuple[UUID, UUID],
) -> None:
    paused = replace(
        services, database=database.app_api, uploads_switch=KillSwitch(lambda: "false", clock)
    )
    client = TestClient(create_app(settings, paused), base_url=APP_ORIGIN)
    headers = signed_in_as(client, services.sessions, org[1], clock())

    response = post(client, org[0], headers)

    assert response.status_code == 503
    assert uploads_in(database, org[0]) == 0


def test_the_put_carries_the_requests_trace(
    services: Services, database: Database, clock: FakeClock, org: tuple[UUID, UUID]
) -> None:
    """The worker links its spans to this trace through the object's metadata (spec §9.1)."""
    exporter = InMemorySpanExporter()
    settings = Settings(stage="local", version="9.9.9")
    tracer_provider = create_tracer_provider(settings, exporter)
    app = create_app(
        settings, replace(services, database=database.app_api), tracer_provider=tracer_provider
    )
    client = TestClient(app, base_url=APP_ORIGIN)
    headers = signed_in_as(client, services.sessions, org[1], clock())

    created = post(client, org[0], headers).json()
    tracer_provider.force_flush()

    [server] = [s for s in exporter.get_finished_spans() if s.kind == SpanKind.SERVER]
    traceparent = created["upload_headers"]["x-amz-meta-traceparent"]
    assert traceparent.split("-")[1] == format(server.context.trace_id, "032x")
    signed = parse_qs(urlsplit(created["upload_url"]).query)["X-Amz-SignedHeaders"]
    assert signed == ["content-length;host;x-amz-checksum-sha256;x-amz-meta-traceparent"]


def test_uploads_are_listed_newest_first_page_by_page(
    database_client: TestClient, headers: dict[str, str], org: tuple[UUID, UUID]
) -> None:
    ids = [post(database_client, org[0], headers).json()["upload"]["id"] for _ in range(3)]

    first = database_client.get(f"/api/v1/orgs/{org[0]}/uploads", params={"limit": 2}).json()
    rest = database_client.get(
        f"/api/v1/orgs/{org[0]}/uploads", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()

    listed = [upload["id"] for upload in first["uploads"] + rest["uploads"]]
    assert listed == ids[::-1]
    assert rest["next_cursor"] is None


def test_an_upload_says_how_many_findings_it_has_and_the_most_severe(
    database_client: TestClient,
    headers: dict[str, str],
    org: tuple[UUID, UUID],
    database: Database,
) -> None:
    created = post(database_client, org[0], headers).json()["upload"]
    with database.admin.begin() as connection:
        analyzed = add_upload(connection, org[0], org[1], status="analyzed")
        for severity in ("low", "high", "medium"):
            add_finding(connection, org[0], analyzed, severity=severity)

    listed = database_client.get(f"/api/v1/orgs/{org[0]}/uploads").json()["uploads"]
    one = database_client.get(f"/api/v1/orgs/{org[0]}/uploads/{analyzed}").json()

    assert (created["findings"], created["worst_severity"]) == (0, None)
    assert {upload["id"]: (upload["findings"], upload["worst_severity"]) for upload in listed} == {
        created["id"]: (0, None),
        str(analyzed): (3, "high"),
    }
    assert (one["findings"], one["worst_severity"]) == (3, "high")


def test_an_upload_is_read_by_id_but_not_through_another_org(
    database_client: TestClient,
    headers: dict[str, str],
    org: tuple[UUID, UUID],
    database: Database,
) -> None:
    upload = post(database_client, org[0], headers).json()["upload"]
    with database.admin.begin() as connection:
        other = add_org(connection, org[1])
        add_member(connection, other, org[1], "owner")

    mine = database_client.get(f"/api/v1/orgs/{org[0]}/uploads/{upload['id']}")
    through_other = database_client.get(f"/api/v1/orgs/{other}/uploads/{upload['id']}")

    assert (mine.status_code, mine.json()) == (200, upload)
    assert through_other.status_code == 404
