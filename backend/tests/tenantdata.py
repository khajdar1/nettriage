"""Seed users, organizations, memberships and invitations for integration tests. Seeding uses
the superuser engine, which row-level security doesn't apply to."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

from sqlalchemy import Connection, Engine, text


@dataclass(frozen=True)
class Tenant:
    org_id: UUID
    owner_id: UUID
    invitation_id: UUID


def add_user(connection: Connection, email: str | None = None) -> UUID:
    user_id = uuid7()
    connection.execute(
        text("INSERT INTO users (id, cognito_sub, email) VALUES (:id, :sub, :email)"),
        {"id": user_id, "sub": f"sub-{user_id}", "email": email or f"{user_id}@example.com"},
    )
    return user_id


def add_org(connection: Connection, created_by: UUID) -> UUID:
    org_id = uuid7()
    connection.execute(
        text(
            "INSERT INTO organizations (id, name, slug, created_by) "
            "VALUES (:id, :name, :slug, :created_by)"
        ),
        {"id": org_id, "name": "Org", "slug": f"org-{org_id.hex}", "created_by": created_by},
    )
    return org_id


def add_member(connection: Connection, org_id: UUID, user_id: UUID, role: str) -> None:
    connection.execute(
        text("INSERT INTO memberships (org_id, user_id, role) VALUES (:org, :user, :role)"),
        {"org": org_id, "user": user_id, "role": role},
    )


def add_invitation(
    connection: Connection, org_id: UUID, created_by: UUID, email: str, role: str = "viewer"
) -> UUID:
    invitation_id = uuid7()
    connection.execute(
        text(
            "INSERT INTO invitations (id, org_id, email, role, token_hash, expires_at, created_by) "
            "VALUES (:id, :org, :email, :role, :hash, :expires, :created_by)"
        ),
        {
            "id": invitation_id,
            "org": org_id,
            "email": email,
            "role": role,
            "hash": hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
            "expires": datetime.now(UTC) + timedelta(days=7),
            "created_by": created_by,
        },
    )
    return invitation_id


def add_tenant(admin: Engine) -> Tenant:
    """An org with an owner, and a pending invitation."""
    with admin.begin() as connection:
        owner = add_user(connection)
        org = add_org(connection, owner)
        add_member(connection, org, owner, "owner")
        invitation = add_invitation(connection, org, owner, f"invitee-{org.hex}@example.com")
    return Tenant(org_id=org, owner_id=owner, invitation_id=invitation)
