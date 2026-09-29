"""An organization's members (spec §7): list them, change a role, remove someone or leave."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from nettriage.adapters.organizations import change_role, list_members, remove_member
from nettriage.entrypoints.api.access import OrgContext, OrgMember
from nettriage.entrypoints.api.auditing import audit
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.schemas import MemberOut, MembersOut, RoleIn
from nettriage.entrypoints.api.services import get_services

router = APIRouter(prefix="/v1/orgs/{org_id}/members")


@router.get("")
def members(
    request: Request, org_id: UUID, org: Annotated[OrgContext, Depends(OrgMember("members:read"))]
) -> MembersOut:
    with org_rules(request, "The member list"):
        found = list_members(get_services(request).database, org.org_id, org.user_id)
    return MembersOut(members=[MemberOut.of(member) for member in found])


@router.patch("/{user_id}")
def set_role(
    request: Request,
    org_id: UUID,
    user_id: UUID,
    body: RoleIn,
    org: Annotated[OrgContext, Depends(OrgMember("members:role"))],
) -> MemberOut:
    with org_rules(request, "Changing a role", org=org, permission="members:role"):
        previous, member = change_role(
            get_services(request).database,
            org.org_id,
            actor_id=org.user_id,
            target_id=user_id,
            role=body.role,
        )
    audit(
        request,
        action="member.role_changed",
        outcome="success",
        actor_user_id=org.user_id,
        org_id=org.org_id,
        target_type="user",
        target_id=str(user_id),
        details={"from": previous, "to": body.role},
    )
    return MemberOut.of(member)


@router.delete("/{user_id}", status_code=204)
def remove(
    request: Request,
    org_id: UUID,
    user_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("members:remove", or_self=True))],
) -> None:
    """Remove a member, or leave when `{user_id}` is the caller (spec §6.4)."""
    with org_rules(request, "Removing a member", org=org, permission="members:remove"):
        removal = remove_member(
            get_services(request).database,
            org.org_id,
            actor_id=org.user_id,
            target_id=user_id,
        )
    audit(
        request,
        action="member.left" if removal.left else "member.removed",
        outcome="success",
        actor_user_id=org.user_id,
        org_id=org.org_id,
        target_type="user",
        target_id=str(user_id),
        details={"role": removal.role},
    )
