"""Triaging findings (spec §7), as `app_api`: status and assignee changes against the version the
caller read, comments, and the history they leave."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid7

import pytest
from conftest import APP_API_PASSWORD, Database
from sqlalchemy import Engine, text
from tenantdata import Tenant, add_member, add_tenant, add_user

from nettriage.adapters.findings import FindingStatus, get_finding
from nettriage.adapters.postgres import create_database_engine
from nettriage.adapters.triage import Triage, Triaged, add_comment, triage_finding
from nettriage.application.organizations import Forbidden, NotFound
from nettriage.application.triage import InvalidAssignee, StaleVersion


def member(database: Database, tenant: Tenant, role: str) -> UUID:
    with database.admin.begin() as connection:
        user = add_user(connection)
        add_member(connection, tenant.org_id, user, role)
    return user


def triage(
    database: Database,
    tenant: Tenant,
    change: Triage,
    *,
    version: int = 1,
    user: UUID | None = None,
) -> Triaged:
    return triage_finding(
        database.app_api,
        tenant.org_id,
        user or tenant.owner_id,
        tenant.finding_id,
        expected_version=version,
        change=change,
    )


def history(database: Database, tenant: Tenant) -> list[tuple[str, UUID | None, dict[str, object]]]:
    detail = get_finding(database.app_api, tenant.org_id, tenant.owner_id, tenant.finding_id)
    return [(event.type, event.actor_id, event.payload) for event in detail.events]


def test_a_status_change_bumps_the_version_and_is_recorded(database: Database) -> None:
    tenant = add_tenant(database.admin)

    triaged = triage(database, tenant, Triage(status="investigating"))

    assert (triaged.detail.finding.status, triaged.detail.finding.version) == ("investigating", 2)
    assert (triaged.status, triaged.assignee) == (("open", "investigating"), None)
    assert history(database, tenant)[1:] == [
        ("status_changed", tenant.owner_id, {"from": "open", "to": "investigating"})
    ]


def test_a_finding_is_assigned_to_an_analyst_and_unassigned_again(database: Database) -> None:
    tenant = add_tenant(database.admin)
    analyst = member(database, tenant, "analyst")

    assigned = triage(database, tenant, Triage(assign=True, assignee_id=analyst))
    unassigned = triage(database, tenant, Triage(assign=True, assignee_id=None), version=2)

    assert assigned.detail.finding.assignee_id == analyst
    assert (unassigned.detail.finding.assignee_id, unassigned.detail.finding.version) == (None, 3)
    assert [payload for kind, _, payload in history(database, tenant) if kind == "assigned"] == [
        {"from": None, "to": str(analyst)},
        {"from": str(analyst), "to": None},
    ]


def test_status_and_assignee_change_together_in_one_version(database: Database) -> None:
    tenant = add_tenant(database.admin)

    triaged = triage(
        database, tenant, Triage(status="resolved", assign=True, assignee_id=tenant.owner_id)
    )

    assert triaged.detail.finding.version == 2
    assert [kind for kind, _, _ in history(database, tenant)] == [
        "created",
        "status_changed",
        "assigned",
    ]


def test_a_stale_version_is_refused_and_changes_nothing(database: Database) -> None:
    tenant = add_tenant(database.admin)
    triage(database, tenant, Triage(status="investigating"))

    with pytest.raises(StaleVersion) as stale:
        triage(database, tenant, Triage(status="false_positive"), version=1)

    assert stale.value.current == 2
    finding = get_finding(database.app_api, tenant.org_id, tenant.owner_id, tenant.finding_id)
    assert (finding.finding.status, finding.finding.version) == ("investigating", 2)


def test_two_members_triaging_the_same_version_at_once_one_wins(database: Database) -> None:
    """Both read version 1. The org's lock is held until both are waiting on it, so they
    overlap for certain: the first to get it wins, the second finds version 2."""
    tenant = add_tenant(database.admin)
    analyst = member(database, tenant, "analyst")
    second = create_database_engine(
        database.url.set(username="app_api", password=APP_API_PASSWORD).render_as_string(
            hide_password=False
        ),
        pool_size=1,
    )
    started = threading.Barrier(3)

    def attempt(engine: Engine, user: UUID, status: FindingStatus) -> str:
        started.wait()
        try:
            triage_finding(
                engine,
                tenant.org_id,
                user,
                tenant.finding_id,
                expected_version=1,
                change=Triage(status=status),
            )
        except StaleVersion:
            return "stale"
        return "won"

    with database.admin.begin() as holder:
        holder.execute(
            text("SELECT id FROM organizations WHERE id = :org FOR UPDATE"), {"org": tenant.org_id}
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(attempt, database.app_api, tenant.owner_id, "resolved")
            other = pool.submit(attempt, second, analyst, "false_positive")
            started.wait()
            time.sleep(0.5)  # both are now blocked on the org's lock
            holder.commit()
        outcomes = sorted([first.result(), other.result()])
    second.dispose()

    assert outcomes == ["stale", "won"]
    assert [kind for kind, _, _ in history(database, tenant)].count("status_changed") == 1


def test_asking_for_what_is_already_there_changes_nothing(database: Database) -> None:
    tenant = add_tenant(database.admin)

    triaged = triage(database, tenant, Triage(status="open", assign=True, assignee_id=None))

    assert (triaged.status, triaged.assignee, triaged.detail.finding.version) == (None, None, 1)
    assert [kind for kind, _, _ in history(database, tenant)] == ["created"]


@pytest.mark.parametrize("who", ["viewer", "outsider", "unknown"])
def test_only_a_member_who_can_triage_can_be_assigned(database: Database, who: str) -> None:
    tenant = add_tenant(database.admin)
    if who == "viewer":
        assignee = member(database, tenant, "viewer")
    elif who == "outsider":
        assignee = add_tenant(database.admin).owner_id
    else:
        assignee = uuid7()

    with pytest.raises(InvalidAssignee):
        triage(database, tenant, Triage(assign=True, assignee_id=assignee))

    assert [kind for kind, _, _ in history(database, tenant)] == ["created"]


def test_a_viewer_can_not_triage_or_comment(database: Database) -> None:
    tenant = add_tenant(database.admin)
    viewer = member(database, tenant, "viewer")

    with pytest.raises(Forbidden):
        triage(database, tenant, Triage(status="resolved"), user=viewer)
    with pytest.raises(Forbidden):
        add_comment(database.app_api, tenant.org_id, viewer, tenant.finding_id, "Benign")


def test_a_finding_of_another_org_is_not_found(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    with pytest.raises(NotFound):
        triage_finding(
            database.app_api,
            mine.org_id,
            mine.owner_id,
            theirs.finding_id,
            expected_version=1,
            change=Triage(status="resolved"),
        )
    with pytest.raises(NotFound):
        add_comment(database.app_api, mine.org_id, mine.owner_id, theirs.finding_id, "Mine now")


def test_a_comment_joins_the_history_without_changing_the_version(database: Database) -> None:
    tenant = add_tenant(database.admin)
    analyst = member(database, tenant, "analyst")

    event = add_comment(
        database.app_api, tenant.org_id, analyst, tenant.finding_id, "Scanner from our pentest."
    )

    assert (event.type, event.actor_id, event.payload) == (
        "commented",
        analyst,
        {"text": "Scanner from our pentest."},
    )
    detail = get_finding(database.app_api, tenant.org_id, tenant.owner_id, tenant.finding_id)
    assert detail.finding.version == 1
    assert detail.events[-1].id == event.id


def test_the_detail_lists_the_latest_100_events_and_counts_them_all(database: Database) -> None:
    """A finding's history only grows; its detail stays inside the API's response limit."""
    tenant = add_tenant(database.admin)
    for n in range(105):
        add_comment(database.app_api, tenant.org_id, tenant.owner_id, tenant.finding_id, f"c{n}")

    detail = get_finding(database.app_api, tenant.org_id, tenant.owner_id, tenant.finding_id)

    assert detail.events_total == 106
    assert [event.payload["text"] for event in detail.events] == [f"c{n}" for n in range(5, 105)]
