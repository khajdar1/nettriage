"""Invitations in Postgres (spec §5.2, §5.7, §6.3): one-time links, stored as a SHA-256 hash,
valid for 7 days and only for the invited email address. "Pending" means neither accepted nor
revoked, and not expired."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid7

from sqlalchemy import Engine, Row, text

from nettriage.adapters.organizations import (
    Organization,
    count_members,
    count_user_orgs,
    lock_org,
    lock_org_for,
    lock_user,
    read_org,
    set_org,
)
from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.organizations import (
    INVITATION_LIFETIME,
    MAX_MEMBERS_PER_ORG,
    MAX_ORGS_PER_USER,
    MAX_PENDING_INVITATIONS,
    Conflict,
    Forbidden,
    InvitationInvalid,
    QuotaExceeded,
    WrongEmail,
    new_invitation_token,
    require,
    token_hash,
)
from nettriage.application.permissions import Role, can_manage

INVALID = "This invitation isn't valid anymore. Ask for a new one."


@dataclass(frozen=True)
class Invitation:
    id: UUID
    email: str
    role: Role
    expires_at: datetime
    created_at: datetime
    created_by: UUID


def list_invitations(
    engine: Engine, org_id: UUID, user_id: UUID, now: datetime
) -> list[Invitation]:
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        rows = connection.execute(
            text(
                "SELECT id, email, role, expires_at, created_at, created_by FROM invitations "
                "WHERE org_id = :org AND accepted_at IS NULL AND revoked_at IS NULL "
                "AND expires_at > :now ORDER BY created_at, id"
            ),
            {"org": org_id, "now": now},
        ).all()
    return [_invitation(row) for row in rows]


def create_invitation(
    engine: Engine,
    org_id: UUID,
    *,
    actor_id: UUID,
    email: str,
    role: Role,
    now: datetime,
) -> tuple[Invitation, str]:
    """A new invitation, and its token, which is shown once and never stored. An expired
    invitation for the same address is revoked first (Plan 3a, Decision 6)."""
    token = new_invitation_token()
    with tenant_transaction(engine, org_id=org_id, user_id=actor_id) as connection:
        actor_role = lock_org_for(connection, org_id, actor_id)
        require(actor_role, "members:invite")
        if not can_manage(actor_role, role):
            raise Forbidden("Your role can't invite someone with that role.")
        member = connection.execute(
            text(
                "SELECT 1 FROM memberships m JOIN users u ON u.id = m.user_id "
                "WHERE m.org_id = :org AND lower(u.email) = lower(:email)"
            ),
            {"org": org_id, "email": email},
        ).first()
        if member is not None:
            raise Conflict("That person is already a member.")
        earlier = connection.execute(
            text(
                "SELECT id, expires_at FROM invitations WHERE org_id = :org "
                "AND lower(email) = lower(:email) AND accepted_at IS NULL AND revoked_at IS NULL"
            ),
            {"org": org_id, "email": email},
        ).one_or_none()
        if earlier is not None and earlier.expires_at > now:
            raise Conflict("That address already has a pending invitation.")
        if earlier is not None:
            connection.execute(
                text("UPDATE invitations SET revoked_at = :now WHERE id = :id"),
                {"now": now, "id": earlier.id},
            )
        pending: int = connection.execute(
            text(
                "SELECT count(*) FROM invitations WHERE org_id = :org "
                "AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > :now"
            ),
            {"org": org_id, "now": now},
        ).scalar_one()
        if pending >= MAX_PENDING_INVITATIONS:
            raise QuotaExceeded(
                f"An org can have at most {MAX_PENDING_INVITATIONS} pending invitations."
            )
        if count_members(connection, org_id) + pending >= MAX_MEMBERS_PER_ORG:
            raise QuotaExceeded(
                f"An org can have at most {MAX_MEMBERS_PER_ORG} members, "
                "counting pending invitations."
            )
        row = connection.execute(
            text(
                "INSERT INTO invitations "
                "(id, org_id, email, role, token_hash, expires_at, created_by) "
                "VALUES (:id, :org, :email, :role, :hash, :expires, :actor) "
                "RETURNING id, email, role, expires_at, created_at, created_by"
            ),
            {
                "id": uuid7(),
                "org": org_id,
                "email": email,
                "role": role,
                "hash": token_hash(token),
                "expires": now + INVITATION_LIFETIME,
                "actor": actor_id,
            },
        ).one()
    return _invitation(row), token


def revoke_invitation(
    engine: Engine, org_id: UUID, *, actor_id: UUID, invitation_id: UUID, now: datetime
) -> bool:
    """Revoke a pending invitation. False if there's no pending invitation with that ID. Like
    inviting, it's only for a role the caller may grant (spec §6.4)."""
    with tenant_transaction(engine, org_id=org_id, user_id=actor_id) as connection:
        actor_role = lock_org_for(connection, org_id, actor_id)
        require(actor_role, "members:invite")
        role = connection.execute(
            text(
                "SELECT role FROM invitations WHERE org_id = :org AND id = :id "
                "AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > :now"
            ),
            {"now": now, "org": org_id, "id": invitation_id},
        ).scalar_one_or_none()
        if role is None:
            return False
        if not can_manage(actor_role, role):
            raise Forbidden("Your role can't revoke an invitation for that role.")
        revoked = connection.execute(
            text(
                "UPDATE invitations SET revoked_at = :now WHERE org_id = :org AND id = :id "
                "AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > :now"
            ),
            {"now": now, "org": org_id, "id": invitation_id},
        ).rowcount
    return revoked == 1


def accept_invitation(engine: Engine, *, user_id: UUID, token: str, now: datetime) -> Organization:
    """Join the org the invitation is for, with its role (spec §6.3). The signed-in user's
    verified email must match the invitation's, ignoring case.

    The transaction starts user-only: row-level security then shows the user's own memberships
    (for the 3-orgs quota) and, through `app.invitation_token_hash`, only the invitation this
    token is for. It then switches to the invitation's org to join it."""
    with tenant_transaction(engine, user_id=user_id) as connection:
        connection.execute(
            text("SELECT set_config('app.invitation_token_hash', :hash, true)"),
            {"hash": token_hash(token)},
        )
        lock_user(connection, user_id)
        invitation = connection.execute(
            text(
                "SELECT id, org_id, email, role, created_by FROM invitations "
                "WHERE token_hash = app_invitation_token_hash() "
                "AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > :now"
            ),
            {"now": now},
        ).one_or_none()
        if invitation is None:
            raise InvitationInvalid(INVALID)
        email: str = connection.execute(
            text("SELECT email FROM users WHERE id = :user"), {"user": user_id}
        ).scalar_one()
        if email.lower() != invitation.email.lower():
            raise WrongEmail("This invitation is for a different email address.")
        if count_user_orgs(connection, user_id) >= MAX_ORGS_PER_USER:
            raise QuotaExceeded(f"You can belong to at most {MAX_ORGS_PER_USER} orgs.")
        set_org(connection, invitation.org_id)
        lock_org(connection, invitation.org_id)
        already = connection.execute(
            text("SELECT 1 FROM memberships WHERE org_id = :org AND user_id = :user"),
            {"org": invitation.org_id, "user": user_id},
        ).first()
        if already is not None:
            raise Conflict("You're already a member of this organization.")
        if count_members(connection, invitation.org_id) >= MAX_MEMBERS_PER_ORG:
            raise QuotaExceeded(f"This org already has {MAX_MEMBERS_PER_ORG} members.")
        accepted = connection.execute(
            text(
                "UPDATE invitations SET accepted_at = :now, accepted_by = :user "
                "WHERE id = :id AND accepted_at IS NULL AND revoked_at IS NULL"
            ),
            {"now": now, "user": user_id, "id": invitation.id},
        ).rowcount
        if accepted != 1:
            raise InvitationInvalid(INVALID)
        connection.execute(
            text(
                "INSERT INTO memberships (org_id, user_id, role, invited_by) "
                "VALUES (:org, :user, :role, :by)"
            ),
            {
                "org": invitation.org_id,
                "user": user_id,
                "role": invitation.role,
                "by": invitation.created_by,
            },
        )
        return read_org(connection, invitation.org_id, user_id)


def _invitation(row: Row[Any]) -> Invitation:
    return Invitation(
        id=row.id,
        email=row.email,
        role=row.role,
        expires_at=row.expires_at,
        created_at=row.created_at,
        created_by=row.created_by,
    )
