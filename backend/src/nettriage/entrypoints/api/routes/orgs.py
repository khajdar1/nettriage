"""Organizations (spec §7): create, read, rename, delete, and read the audit log."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Annotated
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from nettriage.adapters.audit_log import list_org_events
from nettriage.adapters.idempotency import (
    IdempotencyInProgress,
    IdempotencyMismatch,
    StoredResponse,
)
from nettriage.adapters.organizations import create_org, delete_org, get_org, rename_org
from nettriage.entrypoints.api.access import CurrentSession, OrgContext, OrgMember, unavailable
from nettriage.entrypoints.api.auditing import audit
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.schemas import (
    AuditEventOut,
    AuditLogOut,
    NameIn,
    OrgOut,
)
from nettriage.entrypoints.api.services import get_services

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/orgs")

IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{1,100}")


@router.post("", status_code=201, response_model=OrgOut)
def create(request: Request, body: NameIn, session: CurrentSession) -> Response:
    """Create an org with the caller as its owner. An `Idempotency-Key` header makes a retry
    return the first response instead of creating a second org (spec §7)."""
    services = get_services(request)
    key = request.headers.get("idempotency-key")
    if key is not None and not IDEMPOTENCY_KEY.fullmatch(key):
        raise HTTPException(
            422, detail="Idempotency-Key must be 1 to 100 letters, digits, dashes or underscores."
        )
    if key is not None:
        request_hash = hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True).encode())
        try:
            stored = services.idempotency.begin(
                session.user_id, key, request_hash.hexdigest(), services.clock()
            )
        except IdempotencyMismatch:
            raise HTTPException(
                422, detail="This Idempotency-Key was already used for a different request."
            ) from None
        except IdempotencyInProgress:
            raise HTTPException(
                409, detail="A request with this Idempotency-Key is still running; retry shortly."
            ) from None
        except BotoCoreError, ClientError:
            logger.exception("idempotency_read_failed")
            raise unavailable("Creating an organization") from None
        if stored is not None:
            return _created(stored)
    try:
        with org_rules(request, "Creating an organization"):
            org = create_org(services.database, user_id=session.user_id, name=body.name)
    except Exception:
        if key is not None:
            _forget(request, session.user_id, key)
        raise
    created = StoredResponse(status=201, body=OrgOut.of(org).model_dump(mode="json"))
    if key is not None:
        try:
            services.idempotency.finish(session.user_id, key, created, services.clock())
        except BotoCoreError, ClientError:
            logger.warning("idempotency_write_failed")
    audit(
        request,
        action="org.created",
        outcome="success",
        actor_user_id=session.user_id,
        org_id=org.id,
        target_type="organization",
        target_id=str(org.id),
    )
    return _created(created)


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
    before = _decode(cursor) if cursor else None
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
        next_cursor=_encode(page[-1].created_at, page[-1].id) if more else None,
    )


def _created(stored: StoredResponse) -> Response:
    return JSONResponse(
        stored.body,
        status_code=stored.status,
        headers={"Location": f"/api/v1/orgs/{stored.body['id']}"},
    )


def _forget(request: Request, user_id: UUID, key: str) -> None:
    try:
        get_services(request).idempotency.abandon(user_id, key)
    except BotoCoreError, ClientError:
        logger.warning("idempotency_abandon_failed")


def _encode(created_at: datetime, event_id: UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(event_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        created_at, event_id = json.loads(raw)
        return datetime.fromisoformat(created_at), UUID(event_id)
    except (ValueError, TypeError) as error:
        raise HTTPException(
            422, detail="The cursor isn't valid; start from the first page."
        ) from error
