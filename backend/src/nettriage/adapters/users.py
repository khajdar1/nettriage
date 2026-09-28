"""Users in Postgres (spec §6.3): just-in-time sign-in, and the signed-in user's own view."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid7

from sqlalchemy import Engine, text

from nettriage.adapters.postgres import tenant_transaction


@dataclass(frozen=True)
class SignedInUser:
    user_id: UUID
    created: bool
    disabled: bool


@dataclass(frozen=True)
class Membership:
    org_id: UUID
    name: str
    slug: str
    role: str


@dataclass(frozen=True)
class Me:
    user_id: UUID
    email: str
    display_name: str | None
    memberships: tuple[Membership, ...]


def sign_in_user(engine: Engine, *, sub: str, email: str) -> SignedInUser:
    """Find the user by Cognito's `sub` or create them, and keep their email current. Runs the
    `sign_in_user` database function: the API's role can't read or insert users directly."""
    with tenant_transaction(engine) as connection:
        row = connection.execute(
            text("SELECT user_id, created, disabled FROM sign_in_user(:id, :sub, :email)"),
            {"id": uuid7(), "sub": sub, "email": email},
        ).one()
    return SignedInUser(user_id=row.user_id, created=row.created, disabled=row.disabled)


def load_me(engine: Engine, user_id: UUID) -> Me | None:
    """The user and their memberships, read in a user-only transaction: row-level security
    shows only their own row and memberships, and the orgs they belong to."""
    with tenant_transaction(engine, user_id=user_id) as connection:
        user = connection.execute(
            text("SELECT id, email, display_name FROM users WHERE id = :id"), {"id": user_id}
        ).one_or_none()
        if user is None:
            return None
        rows = connection.execute(
            text(
                "SELECT o.id, o.name, o.slug, m.role FROM memberships m "
                "JOIN organizations o ON o.id = m.org_id "
                "WHERE m.user_id = :id ORDER BY o.name, o.id"
            ),
            {"id": user_id},
        ).all()
    return Me(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        memberships=tuple(
            Membership(org_id=row.id, name=row.name, slug=row.slug, role=row.role) for row in rows
        ),
    )
