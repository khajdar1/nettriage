"""Invitations (spec §6.3, §7): invite by email, list and revoke them, and accept one."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from nettriage.adapters.invitations import (
    INVALID,
    accept_invitation,
    create_invitation,
    list_invitations,
    revoke_invitation,
)
from nettriage.application.organizations import invitation_url, normalize_email
from nettriage.application.rate_limits import POLICIES
from nettriage.application.sessions import is_secret
from nettriage.entrypoints.api.access import CurrentSession, OrgContext, OrgMember, enforce
from nettriage.entrypoints.api.auditing import audit
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.schemas import (
    AcceptIn,
    CreatedInvitationOut,
    InvitationIn,
    InvitationOut,
    InvitationsOut,
    OrgOut,
)
from nettriage.entrypoints.api.services import get_services

router = APIRouter(prefix="/v1")


@router.get("/orgs/{org_id}/invitations")
def invitations(
    request: Request,
    org_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("members:invite"))],
) -> InvitationsOut:
    services = get_services(request)
    with org_rules(request, "The invitation list"):
        found = list_invitations(services.database, org.org_id, org.user_id, services.clock())
    return InvitationsOut(invitations=[InvitationOut.of(invitation) for invitation in found])


@router.post("/orgs/{org_id}/invitations", status_code=201)
def invite(
    request: Request,
    org_id: UUID,
    body: InvitationIn,
    org: Annotated[OrgContext, Depends(OrgMember("members:invite"))],
) -> CreatedInvitationOut:
    services = get_services(request)
    email = normalize_email(body.email)
    if email is None:
        raise HTTPException(422, detail="That isn't an email address.")
    enforce(request, POLICIES["invites.org"], str(org.org_id), actor=org.user_id)
    with org_rules(request, "Inviting someone", org=org, permission="members:invite"):
        invitation, token = create_invitation(
            services.database,
            org.org_id,
            actor_id=org.user_id,
            email=email,
            role=body.role,
            now=services.clock(),
        )
    audit(
        request,
        action="member.invited",
        outcome="success",
        actor_user_id=org.user_id,
        org_id=org.org_id,
        target_type="invitation",
        target_id=str(invitation.id),
        details={"role": invitation.role},
    )
    return CreatedInvitationOut(
        invitation=InvitationOut.of(invitation),
        invite_url=invitation_url(services.app_origin, token),
    )


@router.delete("/orgs/{org_id}/invitations/{invitation_id}", status_code=204)
def revoke(
    request: Request,
    org_id: UUID,
    invitation_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("members:invite"))],
) -> None:
    services = get_services(request)
    with org_rules(request, "Revoking an invitation", org=org, permission="members:invite"):
        revoked = revoke_invitation(
            services.database,
            org.org_id,
            actor_id=org.user_id,
            invitation_id=invitation_id,
            now=services.clock(),
        )
    if not revoked:
        raise HTTPException(404, detail="No pending invitation with that ID.")
    audit(
        request,
        action="invitation.revoked",
        outcome="success",
        actor_user_id=org.user_id,
        org_id=org.org_id,
        target_type="invitation",
        target_id=str(invitation_id),
    )


@router.post("/invitations/accept")
def accept(request: Request, body: AcceptIn, session: CurrentSession) -> OrgOut:
    """Join an org with an invitation's token, from the link's fragment (spec §6.3)."""
    if not is_secret(body.token):
        raise HTTPException(404, detail=INVALID)
    services = get_services(request)
    with org_rules(request, "Accepting the invitation"):
        org = accept_invitation(
            services.database, user_id=session.user_id, token=body.token, now=services.clock()
        )
    audit(
        request,
        action="member.joined",
        outcome="success",
        actor_user_id=session.user_id,
        org_id=org.id,
        target_type="user",
        target_id=str(session.user_id),
        details={"role": org.role},
    )
    return OrgOut.of(org)
