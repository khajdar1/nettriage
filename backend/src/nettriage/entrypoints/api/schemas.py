"""Request and response bodies for the organization API (spec §7). Every request model forbids
fields it doesn't declare, so a client can't set anything it wasn't offered (OWASP API3)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

from nettriage.adapters.invitations import Invitation
from nettriage.adapters.organizations import Member, Organization
from nettriage.application.permissions import Role

# 1 to 100 characters after trimming, and no control characters (line breaks, tabs, NUL).
OrgName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=100, pattern=r"^[^\x00-\x1f\x7f]+$"
    ),
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NameIn(Strict):
    name: OrgName


class DeleteOrgIn(Strict):
    confirm_name: Annotated[str, StringConstraints(max_length=100)]


class RoleIn(Strict):
    role: Role


class InvitationIn(Strict):
    email: Annotated[str, StringConstraints(max_length=320)]
    role: Role


class AcceptIn(Strict):
    token: Annotated[str, StringConstraints(max_length=100)]


class OrgOut(BaseModel):
    id: UUID
    name: str
    slug: str
    role: Role
    member_count: int
    created_at: datetime

    @classmethod
    def of(cls, org: Organization) -> OrgOut:
        return cls(
            id=org.id,
            name=org.name,
            slug=org.slug,
            role=org.role,
            member_count=org.member_count,
            created_at=org.created_at,
        )


class MemberOut(BaseModel):
    user_id: UUID
    email: str
    display_name: str | None
    role: Role
    joined_at: datetime

    @classmethod
    def of(cls, member: Member) -> MemberOut:
        return cls(
            user_id=member.user_id,
            email=member.email,
            display_name=member.display_name,
            role=member.role,
            joined_at=member.joined_at,
        )


class MembersOut(BaseModel):
    members: list[MemberOut]


class InvitationOut(BaseModel):
    id: UUID
    email: str
    role: Role
    expires_at: datetime
    created_at: datetime
    created_by: UUID

    @classmethod
    def of(cls, invitation: Invitation) -> InvitationOut:
        return cls(
            id=invitation.id,
            email=invitation.email,
            role=invitation.role,
            expires_at=invitation.expires_at,
            created_at=invitation.created_at,
            created_by=invitation.created_by,
        )


class InvitationsOut(BaseModel):
    invitations: list[InvitationOut]


class CreatedInvitationOut(BaseModel):
    invitation: InvitationOut
    # The only time the token is shown (spec §6.3). The fragment never reaches servers or logs.
    invite_url: str


class AuditEventOut(BaseModel):
    id: UUID
    created_at: datetime
    actor_user_id: UUID | None
    actor_type: str
    action: str
    target_type: str | None
    target_id: str | None
    outcome: str
    details: dict[str, object]


class AuditLogOut(BaseModel):
    events: list[AuditEventOut]
    next_cursor: str | None
