"""Rate limits on the API's routes (spec §6.5, §9.4)."""

from uuid import uuid4

from browser import VIEWER, sign_in, start_sign_in
from conftest import Database, RuntimeTable, counter
from fake_idp import FakeIdentityProvider
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from sqlalchemy import text


def test_public_routes_report_their_limit_per_client_ip(client: TestClient) -> None:
    response = client.get("/api/health", headers=VIEWER)

    assert response.headers["ratelimit-policy"] == '"public.ip";q=60;w=60'
    assert response.headers["ratelimit"] == '"public.ip";r=19;t=1'


def test_requests_that_bypassed_cloudfront_are_not_ip_limited(client: TestClient) -> None:
    """The Lambda adapter's readiness check calls /api/health from inside the function."""
    response = client.get("/api/health")

    assert response.status_code == 200
    assert "ratelimit" not in response.headers


def test_too_many_sign_ins_from_one_ip_land_on_the_limited_page(
    database_client: TestClient, database: Database, metric_reader: InMemoryMetricReader
) -> None:
    """Sign-in is a browser navigation, so a limited one lands on a page, not on JSON."""
    viewer = {"CloudFront-Viewer-Address": f"198.51.100.{uuid4().int % 250 + 1}:1234"}
    responses = [
        database_client.get("/api/auth/login", headers=viewer, follow_redirects=False)
        for _ in range(7)
    ]

    assert [r.status_code for r in responses] == [302] * 7
    assert all("amazoncognito.com" in r.headers["location"] for r in responses[:5])
    limited = responses[5]
    assert limited.headers["location"] == "/?sign_in=limited"
    assert limited.headers["retry-after"] == "6"
    assert limited.headers["ratelimit"] == '"auth.ip";r=0;t=30'
    assert counter(metric_reader, "nettriage.ratelimit.limited") == 2
    ip = viewer["CloudFront-Viewer-Address"].split(":")[0]
    with database.admin.begin() as connection:
        audited: int = connection.execute(
            text(
                "SELECT count(*) FROM audit_log WHERE action = 'ratelimit.limited' "
                "AND host(ip) = :ip AND actor_type = 'anonymous'"
            ),
            {"ip": ip},
        ).scalar_one()
    assert audited == 1  # sampled: at most once a minute per subject


def test_signed_in_requests_are_limited_per_user(
    database_client: TestClient, idp: FakeIdentityProvider
) -> None:
    sign_in(database_client, idp, sub=f"sub-{uuid4()}")

    response = database_client.get("/api/v1/me")

    assert response.headers["ratelimit-policy"] == '"api.user";q=120;w=60'


def test_a_broken_limiter_lets_requests_through_and_counts_it(
    client: TestClient, runtime_table: RuntimeTable, metric_reader: InMemoryMetricReader
) -> None:
    runtime_table.client.delete_table(TableName=runtime_table.name)

    response = client.get("/api/health", headers=VIEWER)

    assert response.status_code == 200
    assert counter(metric_reader, "nettriage.ratelimit.errors") == 1


def test_a_sign_in_costs_one_slot_of_the_login_bucket_and_one_of_the_callback_bucket(
    database_client: TestClient, idp: FakeIdentityProvider
) -> None:
    """Five people behind one office address can all sign in within the burst."""
    viewer = {"CloudFront-Viewer-Address": "198.51.100.77:1234"}
    locations = []
    for _ in range(5):
        database_client.cookies.clear()
        login = start_sign_in(database_client, headers=viewer)
        code = idp.issue_code(nonce=login["nonce"], sub=f"sub-{uuid4()}")
        callback = database_client.get(
            "/api/auth/callback",
            params={"code": code, "state": login["state"]},
            headers=viewer,
            follow_redirects=False,
        )
        locations.append(callback.headers["location"])

    assert locations == ["/app"] * 5


def test_a_limited_health_check_writes_no_audit_row(
    database_client: TestClient, database: Database, metric_reader: InMemoryMetricReader
) -> None:
    """The health check must never touch the database, even when it is rate-limited."""
    viewer = {"CloudFront-Viewer-Address": "198.51.100.88:1234"}

    statuses = [database_client.get("/api/health", headers=viewer).status_code for _ in range(21)]

    assert statuses == [200] * 20 + [429]
    assert counter(metric_reader, "nettriage.ratelimit.limited") == 1
    with database.admin.begin() as connection:
        audited: int = connection.execute(
            text("SELECT count(*) FROM audit_log WHERE host(ip) = '198.51.100.88'")
        ).scalar_one()
    assert audited == 0
