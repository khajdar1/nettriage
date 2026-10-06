"""The ops job's database roles (Plan 7a §3): `app_ops` may change only the rows the cleanup rules
target, and `app_backup` may read every row of every table and change nothing. Each test runs a
deliberately broad statement as the role, so it's the row policies that are tested, not the
cleanup's own WHERE clauses."""

from __future__ import annotations

from uuid import UUID, uuid7

import pytest
from conftest import Database
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from tenantdata import add_invitation, add_tenant, add_upload

ROLES = ("app_ops", "app_backup")


def aged(admin: Engine, table: str, row: UUID, column: str, minutes: int) -> None:
    """Moves one row's timestamp into the past, as the database owner."""
    with admin.begin() as connection:
        connection.execute(
            text(
                f"UPDATE {table} SET {column} = now() - make_interval(mins => :minutes) "  # noqa: S608
                "WHERE id = :id"
            ),
            {"minutes": minutes, "id": row},
        )


def status_of(admin: Engine, upload: UUID) -> str:
    with admin.begin() as connection:
        return str(
            connection.execute(
                text("SELECT status FROM uploads WHERE id = :id"), {"id": upload}
            ).scalar_one()
        )


def exists(admin: Engine, table: str, row: UUID) -> bool:
    with admin.begin() as connection:
        found: int = connection.execute(
            text(f"SELECT count(*) FROM {table} WHERE id = :id"),  # noqa: S608
            {"id": row},
        ).scalar_one()
    return bool(found)


def add_audit_row(admin: Engine, org: UUID, days_ago: int) -> UUID:
    row = uuid7()
    with admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO audit_log (id, org_id, actor_type, action, outcome, created_at) "
                "VALUES (:id, :org, 'system', 'test.event', 'success', "
                "now() - make_interval(days => :days))"
            ),
            {"id": row, "org": org, "days": days_ago},
        )
    return row


@pytest.mark.parametrize("role", ROLES)
def test_the_ops_roles_have_no_special_powers(database: Database, role: str) -> None:
    with database.admin.begin() as connection:
        attributes = connection.execute(
            text(
                "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication "
                "FROM pg_roles WHERE rolname = :role"
            ),
            {"role": role},
        ).one()

    assert tuple(attributes) == (False, False, False, False, False)


def test_ops_expires_only_uploads_waiting_for_their_file_over_two_hours(
    database: Database,
) -> None:
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        stale = add_upload(connection, tenant.org_id, tenant.owner_id, "pending_upload")
        recent = add_upload(connection, tenant.org_id, tenant.owner_id, "pending_upload")
        analyzed = add_upload(connection, tenant.org_id, tenant.owner_id, "analyzed")
    aged(database.admin, "uploads", stale, "created_at", 121)
    aged(database.admin, "uploads", recent, "created_at", 119)
    aged(database.admin, "uploads", analyzed, "created_at", 600)

    with database.app_ops.begin() as connection:
        connection.execute(
            text("UPDATE uploads SET status = 'expired' WHERE status = 'pending_upload'")
        )

    assert [status_of(database.admin, row) for row in (stale, recent, analyzed)] == [
        "expired",
        "pending_upload",
        "analyzed",
    ]


def test_ops_fails_only_analyses_stuck_over_two_hours(database: Database) -> None:
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        stuck = add_upload(connection, tenant.org_id, tenant.owner_id, "processing")
        running = add_upload(connection, tenant.org_id, tenant.owner_id, "processing")
    aged(database.admin, "uploads", stuck, "created_at", 121)
    aged(database.admin, "uploads", running, "created_at", 30)

    with database.app_ops.begin() as connection:
        connection.execute(
            text(
                "UPDATE uploads SET status = 'failed', failure_reason = 'x', processed_at = now() "
                "WHERE status = 'processing'"
            )
        )

    assert [status_of(database.admin, row) for row in (stuck, running)] == [
        "failed",
        "processing",
    ]


def test_ops_can_only_set_an_upload_expired_or_failed(database: Database) -> None:
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        stale = add_upload(connection, tenant.org_id, tenant.owner_id, "pending_upload")
    aged(database.admin, "uploads", stale, "created_at", 121)

    with (
        pytest.raises(ProgrammingError, match="row-level security"),
        database.app_ops.begin() as connection,
    ):
        connection.execute(
            text("UPDATE uploads SET status = 'analyzed' WHERE id = :id"), {"id": stale}
        )


def test_ops_can_change_only_an_uploads_status_and_failure(database: Database) -> None:
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        database.app_ops.begin() as connection,
    ):
        connection.execute(text("UPDATE uploads SET original_filename = 'x'"))


def test_ops_deletes_only_lapsed_invitations_nobody_accepted(database: Database) -> None:
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        lapsed = add_invitation(connection, tenant.org_id, tenant.owner_id, "a@example.com")
        accepted = add_invitation(connection, tenant.org_id, tenant.owner_id, "b@example.com")
        connection.execute(
            text(
                "UPDATE invitations SET expires_at = now() - interval '1 minute' "
                "WHERE id IN (:lapsed, :accepted)"
            ),
            {"lapsed": lapsed, "accepted": accepted},
        )
        connection.execute(
            text("UPDATE invitations SET accepted_at = now(), accepted_by = :owner WHERE id = :id"),
            {"owner": tenant.owner_id, "id": accepted},
        )

    with database.app_ops.begin() as connection:
        connection.execute(text("DELETE FROM invitations"))

    assert [
        exists(database.admin, "invitations", row)
        for row in (lapsed, accepted, tenant.invitation_id)
    ] == [False, True, True]


def test_ops_deletes_only_audit_rows_over_180_days_old(database: Database) -> None:
    tenant = add_tenant(database.admin)
    old = add_audit_row(database.admin, tenant.org_id, 181)
    kept = add_audit_row(database.admin, tenant.org_id, 179)

    with database.app_ops.begin() as connection:
        connection.execute(text("DELETE FROM audit_log"))

    assert [exists(database.admin, "audit_log", row) for row in (old, kept)] == [False, True]


@pytest.mark.parametrize("engine", ["admin", "app_api"])
def test_the_audit_log_stays_append_only_for_everyone_else(database: Database, engine: str) -> None:
    tenant = add_tenant(database.admin)
    old = add_audit_row(database.admin, tenant.org_id, 400)

    with (
        pytest.raises(DBAPIError, match=r"append-only|permission denied"),
        getattr(database, engine).begin() as connection,
    ):
        connection.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": old})

    assert exists(database.admin, "audit_log", old)


def test_ops_reads_nothing_beyond_its_rules(database: Database) -> None:
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        database.app_ops.begin() as connection,
    ):
        connection.execute(text("SELECT count(*) FROM findings"))


def test_ops_stays_within_its_rules_inside_an_organizations_transaction(
    database: Database,
) -> None:
    """Once a transaction names its organization, the tenant policies, which apply to every role,
    would widen the rules' own: permissive policies add up. So each rule is restrictive too."""
    tenant = add_tenant(database.admin)
    aged(database.admin, "uploads", tenant.upload_id, "created_at", 600)
    recent = add_audit_row(database.admin, tenant.org_id, 10)

    with database.app_ops.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.org_id', :org, true)"), {"org": str(tenant.org_id)}
        )
        changed = connection.execute(
            text("UPDATE uploads SET status = 'expired' WHERE id = :id"), {"id": tenant.upload_id}
        ).rowcount
        deleted = connection.execute(
            text("DELETE FROM invitations WHERE id = :id"), {"id": tenant.invitation_id}
        ).rowcount
        seen: int = connection.execute(
            text("SELECT count(*) FROM audit_log WHERE id = :id"), {"id": recent}
        ).scalar_one()

    assert (changed, deleted, seen) == (0, 0, 0)
    assert status_of(database.admin, tenant.upload_id) == "analyzed"
    assert exists(database.admin, "invitations", tenant.invitation_id)


def public_tables(admin: Engine) -> list[str]:
    with admin.begin() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY c.relname"
                )
            ).scalars()
        )


def test_the_backup_reads_every_row_of_every_table(database: Database) -> None:
    add_tenant(database.admin)
    add_tenant(database.admin)
    tables = public_tables(database.admin)

    def counts(engine: Engine) -> dict[str, int]:
        with engine.begin() as connection:
            return {
                table: int(
                    connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()  # noqa: S608
                )
                for table in tables
            }

    owner_counts = counts(database.admin)
    assert counts(database.app_backup) == owner_counts
    assert owner_counts["organizations"] >= 2


def test_the_backup_changes_nothing(database: Database) -> None:
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        database.app_backup.begin() as connection,
    ):
        connection.execute(text("DELETE FROM uploads"))


def test_every_table_is_in_the_backup(database: Database) -> None:
    """A table added later must be granted to `app_backup` and, if it has row-level security,
    get its read-all policy, or it silently drops out of the backups."""
    with database.admin.begin() as connection:
        missing: list[str] = list(
            connection.execute(
                text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r' AND ("
                    "  NOT has_table_privilege('app_backup', c.oid, 'SELECT')"
                    "  OR (c.relrowsecurity AND NOT EXISTS ("
                    "    SELECT FROM pg_policies p WHERE p.schemaname = 'public' "
                    "    AND p.tablename = c.relname AND 'app_backup' = ANY (p.roles) "
                    "    AND p.cmd IN ('SELECT', 'ALL') AND p.qual = 'true'))) "
                    "ORDER BY c.relname"
                )
            ).scalars()
        )

    assert missing == []
