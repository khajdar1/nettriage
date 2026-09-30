"""Organizations and their members in Postgres (spec §5.2, §5.7, §6.4).

Each function is one transaction as `app_api`, so row-level security applies throughout.
Every change locks the org's row first (`SELECT … FOR UPDATE`), so two requests can't both
remove the last owner or both take the tenth seat. It then reads the caller's role again and
decides with it: the route checked the role before the transaction, and a demotion or a removal
may have committed since.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid7

from psycopg import errors
from sqlalchemy import Connection, Engine, Row, text
from sqlalchemy.exc import IntegrityError

from nettriage.adapters.assignments import release_assignments
from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.organizations import (
    MAX_ORGS_PER_USER,
    ConfirmationMismatch,
    Forbidden,
    LastOwner,
    NotFound,
    OrgRuleError,
    QuotaExceeded,
    check_role_change,
    require,
    slugify,
    with_suffix,
)
from nettriage.application.permissions import Role, allows, can_manage


@dataclass(frozen=True)
class Organization:
    id: UUID
    name: str
    slug: str
    role: Role  # the caller's
    member_count: int
    created_at: datetime


@dataclass(frozen=True)
class Member:
    user_id: UUID
    email: str
    display_name: str | None
    role: Role
    joined_at: datetime


@dataclass(frozen=True)
class Removal:
    role: Role  # the removed member's
    left: bool  # they removed themselves


def role_of(engine: Engine, org_id: UUID, user_id: UUID) -> Role | None:
    """The user's role in the org, read in a user-only transaction: row-level security shows
    only the user's own memberships, so this can't be fooled into another user's row."""
    with tenant_transaction(engine, user_id=user_id) as connection:
        role = connection.execute(
            text("SELECT role FROM memberships WHERE org_id = :org AND user_id = :user"),
            {"org": org_id, "user": user_id},
        ).scalar_one_or_none()
    return cast(Role | None, role)


def create_org(engine: Engine, *, user_id: UUID, name: str) -> Organization:
    """A new org with the user as its owner. Its slug comes from the name, with a random
    suffix if the plain one is taken."""
    org_id = uuid7()
    base = slugify(name)
    for slug in (base, with_suffix(base), with_suffix(base)):
        try:
            with tenant_transaction(engine, user_id=user_id) as connection:
                lock_user(connection, user_id)
                if count_user_orgs(connection, user_id) >= MAX_ORGS_PER_USER:
                    raise QuotaExceeded(f"You can belong to at most {MAX_ORGS_PER_USER} orgs.")
                set_org(connection, org_id)
                connection.execute(
                    text(
                        "INSERT INTO organizations (id, name, slug, created_by) "
                        "VALUES (:id, :name, :slug, :user)"
                    ),
                    {"id": org_id, "name": name, "slug": slug, "user": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO memberships (org_id, user_id, role) "
                        "VALUES (:org, :user, 'owner')"
                    ),
                    {"org": org_id, "user": user_id},
                )
                return read_org(connection, org_id, user_id)
        except IntegrityError as error:
            if not _slug_taken(error):
                raise
    raise OrgRuleError("Couldn't find a free address for this org; try another name.")


def get_org(engine: Engine, org_id: UUID, user_id: UUID) -> Organization:
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        return read_org(connection, org_id, user_id)


def rename_org(engine: Engine, org_id: UUID, user_id: UUID, name: str) -> Organization:
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        require(lock_org_for(connection, org_id, user_id), "org:update")
        connection.execute(
            text("UPDATE organizations SET name = :name WHERE id = :org"),
            {"name": name, "org": org_id},
        )
        return read_org(connection, org_id, user_id)


def delete_org(engine: Engine, org_id: UUID, user_id: UUID, confirm_name: str) -> None:
    """Delete the org and, through cascades, everything it owns. The audit log keeps its rows."""
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        require(lock_org_for(connection, org_id, user_id), "org:delete")
        name: str = connection.execute(
            text("SELECT name FROM organizations WHERE id = :org"), {"org": org_id}
        ).scalar_one()
        if confirm_name != name:
            raise ConfirmationMismatch("Type the organization's exact name to delete it.")
        connection.execute(text("DELETE FROM organizations WHERE id = :org"), {"org": org_id})


def list_members(engine: Engine, org_id: UUID, user_id: UUID) -> list[Member]:
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        rows = connection.execute(
            text(
                "SELECT m.user_id, u.email, u.display_name, m.role, m.created_at "
                "FROM memberships m JOIN users u ON u.id = m.user_id "
                "WHERE m.org_id = :org ORDER BY m.created_at, m.user_id"
            ),
            {"org": org_id},
        ).all()
    return [_member(row) for row in rows]


def change_role(
    engine: Engine,
    org_id: UUID,
    *,
    actor_id: UUID,
    target_id: UUID,
    role: Role,
) -> tuple[Role, Member]:
    """Change a member's role. Returns their previous role and the member as they are now."""
    with tenant_transaction(engine, org_id=org_id, user_id=actor_id) as connection:
        actor_role = lock_org_for(connection, org_id, actor_id)
        require(actor_role, "members:role")
        current = _role(connection, org_id, target_id)
        check_role_change(
            actor_id=actor_id, actor_role=actor_role, target_id=target_id, current=current, new=role
        )
        if current == "owner" and role != "owner":
            _keep_an_owner(connection, org_id)
        connection.execute(
            text("UPDATE memberships SET role = :role WHERE org_id = :org AND user_id = :user"),
            {"role": role, "org": org_id, "user": target_id},
        )
        if not allows(role, "findings:triage"):
            release_assignments(
                connection, org_id, target_id, actor_id=actor_id, reason="role_changed"
            )
        row = connection.execute(
            text(
                "SELECT m.user_id, u.email, u.display_name, m.role, m.created_at "
                "FROM memberships m JOIN users u ON u.id = m.user_id "
                "WHERE m.org_id = :org AND m.user_id = :user"
            ),
            {"org": org_id, "user": target_id},
        ).one()
    return current, _member(row)


def remove_member(engine: Engine, org_id: UUID, *, actor_id: UUID, target_id: UUID) -> Removal:
    """Remove a member, or let the caller leave (spec §6.4): anyone may leave except the last
    owner; removing someone else needs `members:remove` and a role the caller may manage."""
    with tenant_transaction(engine, org_id=org_id, user_id=actor_id) as connection:
        actor_role = lock_org_for(connection, org_id, actor_id)
        current = _role(connection, org_id, target_id)
        leaving = target_id == actor_id
        if not leaving and not (
            allows(actor_role, "members:remove") and can_manage(actor_role, current)
        ):
            raise Forbidden("Your role can't remove that member.")
        if current == "owner":
            _keep_an_owner(connection, org_id)
        release_assignments(
            connection,
            org_id,
            target_id,
            actor_id=actor_id,
            reason="member_left" if leaving else "member_removed",
        )
        connection.execute(
            text("DELETE FROM memberships WHERE org_id = :org AND user_id = :user"),
            {"org": org_id, "user": target_id},
        )
    return Removal(role=current, left=leaving)


# Shared with the invitations adapter.


def set_org(connection: Connection, org_id: UUID | None) -> None:
    """Switch the transaction's org, for work that starts user-only (quotas) and then writes
    inside one org."""
    connection.execute(
        text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id or "")}
    )


def lock_user(connection: Connection, user_id: UUID) -> None:
    """Serialize a user's org-count checks, so two requests can't both take the third seat."""
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:user, 0))"), {"user": str(user_id)}
    )


def lock_org(connection: Connection, org_id: UUID) -> None:
    locked = connection.execute(
        text("SELECT id FROM organizations WHERE id = :org FOR UPDATE"), {"org": org_id}
    ).scalar_one_or_none()
    if locked is None:
        raise NotFound("No such organization.")


def lock_org_for(connection: Connection, org_id: UUID, user_id: UUID) -> Role:
    """Lock the org's row, then return the caller's role as it is now. A caller who has just
    been removed is refused like any non-member."""
    lock_org(connection, org_id)
    role = _membership(connection, org_id, user_id)
    if role is None:
        raise NotFound("No such organization.")
    return role


def count_user_orgs(connection: Connection, user_id: UUID) -> int:
    """The user's memberships. Needs a transaction with no org set, where row-level security
    shows all of the user's own memberships."""
    count: int = connection.execute(
        text("SELECT count(*) FROM memberships WHERE user_id = :user"), {"user": user_id}
    ).scalar_one()
    return count


def count_members(connection: Connection, org_id: UUID) -> int:
    count: int = connection.execute(
        text("SELECT count(*) FROM memberships WHERE org_id = :org"), {"org": org_id}
    ).scalar_one()
    return count


def read_org(connection: Connection, org_id: UUID, user_id: UUID) -> Organization:
    row = connection.execute(
        text(
            "SELECT o.id, o.name, o.slug, o.created_at, m.role, "
            "(SELECT count(*) FROM memberships c WHERE c.org_id = o.id) AS member_count "
            "FROM organizations o JOIN memberships m ON m.org_id = o.id AND m.user_id = :user "
            "WHERE o.id = :org"
        ),
        {"org": org_id, "user": user_id},
    ).one_or_none()
    if row is None:
        raise NotFound("No such organization.")
    return Organization(
        id=row.id,
        name=row.name,
        slug=row.slug,
        role=row.role,
        member_count=row.member_count,
        created_at=row.created_at,
    )


def _role(connection: Connection, org_id: UUID, user_id: UUID) -> Role:
    role = _membership(connection, org_id, user_id)
    if role is None:
        raise NotFound("No such member in this organization.")
    return role


def _membership(connection: Connection, org_id: UUID, user_id: UUID) -> Role | None:
    role = connection.execute(
        text("SELECT role FROM memberships WHERE org_id = :org AND user_id = :user"),
        {"org": org_id, "user": user_id},
    ).scalar_one_or_none()
    return cast(Role | None, role)


def _keep_an_owner(connection: Connection, org_id: UUID) -> None:
    owners: int = connection.execute(
        text("SELECT count(*) FROM memberships WHERE org_id = :org AND role = 'owner'"),
        {"org": org_id},
    ).scalar_one()
    if owners <= 1:
        raise LastOwner("An organization needs at least one owner. Make someone else owner first.")


def _member(row: Row[Any]) -> Member:
    return Member(
        user_id=row.user_id,
        email=row.email,
        display_name=row.display_name,
        role=row.role,
        joined_at=row.created_at,
    )


def _slug_taken(error: IntegrityError) -> bool:
    return (
        isinstance(error.orig, errors.UniqueViolation)
        and error.orig.diag.constraint_name == "organizations_slug_key"
    )
