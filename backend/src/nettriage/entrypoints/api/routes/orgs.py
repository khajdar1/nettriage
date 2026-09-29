"""Organizations (spec §7): create, read, rename, delete, and read the audit log."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from nettriage.adapters.audit_log import list_org_events
from nettriage.adapters.idempotency import StoredResponse
from nettriage.adapters.organizations import create_org, delete_org, get_org, rename_org
from nettriage.entrypoints.api.access import CurrentSession, OrgContext, OrgMember
from nettriage.entrypoints.api.auditing import audit
from nettriage.entrypoints.api.cursors import decode_cursor, encode_cursor
from nettriage.entrypoints.api.idempotent import create_once, created_response
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.schemas import (
    AuditEventOut,
    AuditLogOut,
    NameIn,
    OrgOut,
)
from nettriage.entrypoints.api.services import get_services

router = APIRouter(prefix="/v1/orgs")


@router.post("", status_code=201, response_model=OrgOut)
def create(request: Request, body: NameIn, session: CurrentSession) -> Response:
    """Create an org with the caller as its owner. An `Idempotency-Key` header makes a retry
    return the first response instead of creating a second org (spec §7)."""
    services = get_services(request)

    def work() -> StoredResponse:
        with org_rules(request, "Creating an organization"):
            org = create_org(services.database, user_id=session.user_id, name=body.name)
        audit(
            request,
            action="org.created",
            outcome="success",
            actor_user_id=session.user_id,
            org_id=org.id,
            target_type="organization",
            target_id=str(org.id),
        )
        return StoredResponse(status=201, body=OrgOut.of(org).model_dump(mode="json"))

    stored = create_once(request, session.user_id, body, "Creating an organization", work)
    return created_response(stored, f"/api/v1/orgs/{stored.body['id']}")


@router.get("/{org_id}")
def read(
    request: Request, org_id: UUID, org: Annotated[OrgContext, Depends(OrgMember("org:read"))]
) -> OrgOut:
    with org_rules(request, "This organization"):
        return OrgOut.of(get_org(get_services(request).database, org.org_id, org.user_id))


@router.patch("/{org_id}")
def rename(
    request: Request,
    org_id: UUID,
    body: NameIn,
    org: Annotated[OrgContext, Depends(OrgMember("org:update"))],
) -> OrgOut:
    with org_rules(request, "Renaming this organization", org=org, permission="org:update"):
        renamed = rename_org(get_services(request).database, org.org_id, org.user_id, body.name)
    audit(
        request,
        action="org.renamed",
        outcome="success",
        actor_user_id=org.user_id,
        org_id=org.org_id,
        target_type="organization",
        target_id=str(org.org_id),
        details={"name": body.name},
    )
    return OrgOut.of(renamed)


@router.delete("/{org_id}", status_code=204)
def delete(
    request: Request,
    org_id: UUID,
    confirm_name: Annotated[str, Query(max_length=100)],
    org: Annotated[OrgContext, Depends(OrgMember("org:delete"))],
) -> None:
    """Delete the org, its members, invitations and everything it owns. The caller must type
    the org's name (spec §7), sent as `?confirm_name=`: a DELETE can't carry a body through
    CloudFront. The audit log keeps its records."""
    with org_rules(request, "Deleting this organization", org=org, permission="org:delete"):
        delete_org(get_services(request).database, org.org_id, org.user_id, confirm_name)
    audit(
        request,
        action="org.deleted",
        outcome="success",
        actor_user_id=org.user_id,
        org_id=org.org_id,
        target_type="organization",
        target_id=str(org.org_id),
    )


@router.get("/{org_id}/audit-log")
def audit_log(
    request: Request,
    org_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("audit:read"))],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> AuditLogOut:
    """The org's audit events, newest first, a page at a time (spec §7's cursor pagination)."""
    before = decode_cursor(cursor) if cursor else None
    with org_rules(request, "The audit log"):
        entries = list_org_events(
            get_services(request).database,
            org.org_id,
            org.user_id,
            limit=limit + 1,
            before=before,
        )
    page = entries[:limit]
    more = len(entries) > limit
    return AuditLogOut(
        events=[AuditEventOut(**vars(entry)) for entry in page],
        next_cursor=encode_cursor(page[-1].created_at, page[-1].id) if more else None,
    )
