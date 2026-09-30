"""Triage (spec §7): change a finding's status or assignee against the version the caller read,
and comment on it. Each change is in the finding's history and the org's audit log, and counts
against the org's `triage.org` quota: 500 a day, 50 at once (the owner's decision, Plan 4c)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from nettriage.adapters.idempotency import StoredResponse
from nettriage.adapters.triage import add_comment, triage_finding
from nettriage.application.rate_limits import POLICIES
from nettriage.application.triage import etag, expected_version
from nettriage.entrypoints.api.access import OrgContext, OrgMember, enforce
from nettriage.entrypoints.api.auditing import audit
from nettriage.entrypoints.api.finding_schemas import FindingEventOut, FindingOut
from nettriage.entrypoints.api.idempotent import create_once
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.services import get_services
from nettriage.entrypoints.api.triage_schemas import CommentIn, TriageIn

router = APIRouter(prefix="/v1/orgs/{org_id}/findings")


@router.patch("/{finding_id}")
def triage(
    request: Request,
    response: Response,
    org_id: UUID,
    finding_id: UUID,
    body: TriageIn,
    org: Annotated[OrgContext, Depends(OrgMember("findings:triage"))],
    if_match: Annotated[str | None, Header()] = None,
) -> FindingOut:
    """Change the finding's status, its assignee, or both. `If-Match` must carry the `ETag` of
    the version the caller read: without it the answer is 428, and if someone changed the
    finding since, 412 with the current `ETag`."""
    expected = expected_version(if_match)
    if expected is None:
        raise HTTPException(
            428, detail='Send If-Match with the ETag of the finding you read, such as "1".'
        )
    enforce(request, POLICIES["triage.org"], str(org.org_id), actor=org.user_id)
    with org_rules(request, "Triaging this finding", org=org, permission="findings:triage"):
        triaged = triage_finding(
            get_services(request).database,
            org.org_id,
            org.user_id,
            finding_id,
            expected_version=expected,
            change=body.change(),
        )
    for action, change in (
        ("finding.status_changed", triaged.status),
        ("finding.assigned", triaged.assignee),
    ):
        if change is not None:
            before, after = change
            audit(
                request,
                action=action,
                outcome="success",
                actor_user_id=org.user_id,
                org_id=org.org_id,
                target_type="finding",
                target_id=str(finding_id),
                details={
                    "from": str(before) if before is not None else None,
                    "to": str(after) if after is not None else None,
                },
            )
    response.headers["ETag"] = etag(triaged.detail.finding.version)
    return FindingOut.of_detail(triaged.detail)


@router.post("/{finding_id}/comments", status_code=201, response_model=FindingEventOut)
def comment(
    request: Request,
    org_id: UUID,
    finding_id: UUID,
    body: CommentIn,
    org: Annotated[OrgContext, Depends(OrgMember("findings:comment"))],
) -> Response:
    """Add a comment to the finding's history. An `Idempotency-Key` makes a retry return the
    same comment instead of posting it twice. The audit log records that a comment was made,
    never its text."""
    services = get_services(request)

    def work() -> StoredResponse:
        enforce(request, POLICIES["triage.org"], str(org.org_id), actor=org.user_id)
        with org_rules(
            request, "Commenting on this finding", org=org, permission="findings:comment"
        ):
            event = add_comment(services.database, org.org_id, org.user_id, finding_id, body.text)
        audit(
            request,
            action="finding.commented",
            outcome="success",
            actor_user_id=org.user_id,
            org_id=org.org_id,
            target_type="finding",
            target_id=str(finding_id),
        )
        return StoredResponse(status=201, body=FindingEventOut.of(event).model_dump(mode="json"))

    stored = create_once(request, org.user_id, body, "Commenting on this finding", work)
    return JSONResponse(stored.body, status_code=stored.status)
