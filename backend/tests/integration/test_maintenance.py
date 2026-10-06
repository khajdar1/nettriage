"""The daily cleanup (Plan 7a §2.4), run as `app_ops` against real Postgres: uploads waiting or
analyzing for over 2 hours, lapsed invitations nobody accepted, and audit rows over 180 days."""

from __future__ import annotations

from uuid import UUID, uuid7

from conftest import Database
from sqlalchemy import Engine, text
from tenantdata import add_invitation, add_tenant, add_upload

from nettriage.adapters.maintenance import (
    clean_up,
    delete_lapsed_invitations,
    delete_old_audit_rows,
    expire_abandoned_uploads,
    fail_stuck_analyses,
)
from nettriage.application.uploads import GAVE_UP


def upload_aged(admin: Engine, status: str, minutes: int) -> UUID:
    tenant = add_tenant(admin)
    with admin.begin() as connection:
        upload = add_upload(connection, tenant.org_id, tenant.owner_id, status)
        connection.execute(
            text(
                "UPDATE uploads SET created_at = now() - make_interval(mins => :minutes) "
                "WHERE id = :id"
            ),
            {"minutes": minutes, "id": upload},
        )
    return upload


def upload_row(admin: Engine, upload: UUID) -> tuple[str, str | None, bool]:
    with admin.begin() as connection:
        status, reason, processed = connection.execute(
            text("SELECT status, failure_reason, processed_at FROM uploads WHERE id = :id"),
            {"id": upload},
        ).one()
    return str(status), reason, processed is not None


def test_uploads_still_waiting_for_their_file_after_two_hours_expire(database: Database) -> None:
    stale = upload_aged(database.admin, "pending_upload", 121)
    recent = upload_aged(database.admin, "pending_upload", 90)

    expired = expire_abandoned_uploads(database.app_ops)

    assert expired >= 1
    assert upload_row(database.admin, stale)[0] == "expired"
    assert upload_row(database.admin, recent)[0] == "pending_upload"


def test_analyses_stuck_for_two_hours_fail_with_the_workers_own_sentence(
    database: Database,
) -> None:
    stuck = upload_aged(database.admin, "processing", 125)
    running = upload_aged(database.admin, "processing", 10)

    failed = fail_stuck_analyses(database.app_ops)

    assert failed >= 1
    assert upload_row(database.admin, stuck) == ("failed", GAVE_UP, True)
    assert upload_row(database.admin, running) == ("processing", None, False)


def test_lapsed_invitations_nobody_accepted_are_deleted(database: Database) -> None:
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        lapsed = add_invitation(connection, tenant.org_id, tenant.owner_id, "late@example.com")
        connection.execute(
            text("UPDATE invitations SET expires_at = now() - interval '1 day' WHERE id = :id"),
            {"id": lapsed},
        )

    deleted = delete_lapsed_invitations(database.app_ops)

    assert deleted >= 1
    with database.admin.begin() as connection:
        remaining: set[UUID] = set(
            connection.execute(
                text("SELECT id FROM invitations WHERE id IN (:lapsed, :valid)"),
                {"lapsed": lapsed, "valid": tenant.invitation_id},
            ).scalars()
        )
    assert remaining == {tenant.invitation_id}


def test_audit_rows_older_than_180_days_are_deleted(database: Database) -> None:
    tenant = add_tenant(database.admin)
    old, kept = uuid7(), uuid7()
    with database.admin.begin() as connection:
        for row, days in ((old, 200), (kept, 10)):
            connection.execute(
                text(
                    "INSERT INTO audit_log (id, org_id, actor_type, action, outcome, created_at) "
                    "VALUES (:id, :org, 'system', 'test.event', 'success', "
                    "now() - make_interval(days => :days))"
                ),
                {"id": row, "org": tenant.org_id, "days": days},
            )

    deleted = delete_old_audit_rows(database.app_ops)

    assert deleted >= 1
    with database.admin.begin() as connection:
        remaining: set[UUID] = set(
            connection.execute(
                text("SELECT id FROM audit_log WHERE id IN (:old, :kept)"),
                {"old": old, "kept": kept},
            ).scalars()
        )
    assert remaining == {kept}


def test_the_cleanup_runs_every_rule_and_counts_what_each_changed(database: Database) -> None:
    upload_aged(database.admin, "pending_upload", 300)
    upload_aged(database.admin, "processing", 300)

    counts = clean_up(database.app_ops)

    assert counts.uploads_expired >= 1
    assert counts.analyses_failed >= 1
    assert counts.invitations_deleted >= 0
    assert counts.audit_rows_deleted >= 0
    assert clean_up(database.app_ops).uploads_expired == 0
