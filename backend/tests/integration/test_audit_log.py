"""Writing audit events as the API's role (spec §9.4)."""

from uuid import uuid4

import pytest
from conftest import Database
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from tenantdata import add_tenant

from nettriage.adapters.audit_log import list_org_events, record
from nettriage.application.audit import AuditEvent


def test_an_event_is_stored_with_its_request_details(database: Database) -> None:
    actor = uuid4()
    record(
        database.app_api,
        AuditEvent(
            action="auth.session_created",
            outcome="success",
            actor_type="user",
            actor_user_id=actor,
            ip="203.0.113.7",
            user_agent="x" * 300,
            request_id="cf-request-1",
            trace_id="0" * 32,
            details={"new_user": True},
        ),
    )

    with database.admin.begin() as connection:
        row = connection.execute(
            text(
                "SELECT action, outcome, org_id, host(ip) AS ip, length(user_agent) AS ua, "
                "request_id, details FROM audit_log WHERE actor_user_id = :actor"
            ),
            {"actor": actor},
        ).one()
    assert (row.action, row.outcome, row.org_id, row.ip) == (
        "auth.session_created",
        "success",
        None,
        "203.0.113.7",
    )
    assert (row.ua, row.request_id, row.details) == (256, "cf-request-1", {"new_user": True})


def test_an_org_event_is_written_within_that_org(database: Database) -> None:
    tenant = add_tenant(database.admin)

    record(
        database.app_api,
        AuditEvent(
            action="org.renamed",
            outcome="success",
            actor_type="user",
            actor_user_id=tenant.owner_id,
            org_id=tenant.org_id,
        ),
    )

    with database.admin.begin() as connection:
        count: int = connection.execute(
            text("SELECT count(*) FROM audit_log WHERE org_id = :org"), {"org": tenant.org_id}
        ).scalar_one()
    assert count == 1


def test_the_owner_cannot_truncate_the_audit_log(database: Database) -> None:
    def truncate() -> None:
        with database.admin.begin() as connection:
            connection.execute(text("TRUNCATE audit_log"))

    with pytest.raises(DBAPIError, match="append-only"):
        truncate()


def test_an_orgs_events_are_listed_newest_first_page_by_page(database: Database) -> None:
    tenant, other = add_tenant(database.admin), add_tenant(database.admin)
    for number in range(5):
        record(
            database.app_api,
            AuditEvent(
                action=f"org.event{number}",
                outcome="success",
                actor_type="user",
                actor_user_id=tenant.owner_id,
                org_id=tenant.org_id,
                ip="203.0.113.9",
            ),
        )
    record(
        database.app_api,
        AuditEvent(action="other.org", outcome="success", actor_type="system", org_id=other.org_id),
    )

    first = list_org_events(database.app_api, tenant.org_id, tenant.owner_id, limit=3)
    rest = list_org_events(
        database.app_api,
        tenant.org_id,
        tenant.owner_id,
        limit=3,
        before=(first[-1].created_at, first[-1].id),
    )

    assert [e.action for e in first + rest] == [f"org.event{n}" for n in (4, 3, 2, 1, 0)]
    assert not hasattr(first[0], "ip")
