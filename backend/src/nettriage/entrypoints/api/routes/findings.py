"""Findings (spec §7): list an org's findings a page at a time, and read one with its evidence,
techniques and history. Triage (status, assignee, comments) is Plan 4c."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from nettriage.adapters.findings import (
    FindingFilters,
    FindingSeverity,
    FindingStatus,
    get_finding,
    list_findings,
)
from nettriage.entrypoints.api.access import OrgContext, OrgMember
from nettriage.entrypoints.api.cursors import (
    decode_cursor,
    decode_severity_cursor,
    encode_cursor,
    encode_severity_cursor,
)
from nettriage.entrypoints.api.finding_schemas import FindingOut, FindingsOut, FindingSummaryOut
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.services import get_services

router = APIRouter(prefix="/v1/orgs/{org_id}/findings")


@router.get("")
def findings(
    request: Request,
    org_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("findings:read"))],
    status: Annotated[list[FindingStatus] | None, Query(max_length=4)] = None,
    severity: FindingSeverity | None = None,
    detector: Annotated[str | None, Query(max_length=50)] = None,
    upload: UUID | None = None,
    assignee: Literal["me", "none"] | None = None,
    since: datetime | None = None,
    sort: Literal["newest", "severity"] = "newest",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> FindingsOut:
    """The org's findings, newest first, or most severe first and then newest with
    `sort=severity` (Plan 6b). Filters, combined: any of the given statuses, severity, detector,
    upload, the assignee (`me` or `none`) and `since` a moment (Plan 6d). A cursor works only
    with the order it was made for."""
    before_severity: FindingSeverity | None = None
    before = None
    if cursor and sort == "severity":
        last_severity, last_at, last_id = decode_severity_cursor(cursor)
        before_severity, before = cast(FindingSeverity, last_severity), (last_at, last_id)
    elif cursor:
        before = decode_cursor(cursor)
    filters = FindingFilters(
        statuses=tuple(status or ()),
        severity=severity,
        detector=detector,
        upload_id=upload,
        assignee=assignee,
        since=since,
    )
    with org_rules(request, "The finding list"):
        found = list_findings(
            get_services(request).database,
            org.org_id,
            org.user_id,
            filters,
            limit=limit + 1,
            sort=sort,
            before=before,
            before_severity=before_severity,
        )
    page = found[:limit]
    next_cursor = None
    if len(found) > limit:
        last = page[-1]
        next_cursor = (
            encode_severity_cursor(last.severity, last.created_at, last.id)
            if sort == "severity"
            else encode_cursor(last.created_at, last.id)
        )
    return FindingsOut(
        findings=[FindingSummaryOut.of(finding) for finding in page], next_cursor=next_cursor
    )


@router.get("/{finding_id}")
def finding(
    request: Request,
    response: Response,
    org_id: UUID,
    finding_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("findings:read"))],
) -> FindingOut:
    """One finding with its evidence, techniques and events. Its `ETag` is the version that
    triage (Plan 4c) will require in `If-Match`."""
    with org_rules(request, "This finding"):
        detail = get_finding(get_services(request).database, org.org_id, org.user_id, finding_id)
    response.headers["ETag"] = f'"{detail.finding.version}"'
    return FindingOut.of_detail(detail)
