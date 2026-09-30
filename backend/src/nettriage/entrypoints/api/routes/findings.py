"""Findings (spec §7): list an org's findings a page at a time, and read one with its evidence,
techniques and history. Triage (status, assignee, comments) is Plan 4c."""

from __future__ import annotations

from typing import Annotated
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
from nettriage.entrypoints.api.cursors import decode_cursor, encode_cursor
from nettriage.entrypoints.api.finding_schemas import FindingOut, FindingsOut, FindingSummaryOut
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.services import get_services

router = APIRouter(prefix="/v1/orgs/{org_id}/findings")


@router.get("")
def findings(
    request: Request,
    org_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("findings:read"))],
    status: FindingStatus | None = None,
    severity: FindingSeverity | None = None,
    detector: Annotated[str | None, Query(max_length=50)] = None,
    upload: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> FindingsOut:
    """The org's findings, newest first. Filters: status, severity, detector, upload."""
    before = decode_cursor(cursor) if cursor else None
    filters = FindingFilters(status=status, severity=severity, detector=detector, upload_id=upload)
    with org_rules(request, "The finding list"):
        found = list_findings(
            get_services(request).database,
            org.org_id,
            org.user_id,
            filters,
            limit=limit + 1,
            before=before,
        )
    page = found[:limit]
    more = len(found) > limit
    return FindingsOut(
        findings=[FindingSummaryOut.of(finding) for finding in page],
        next_cursor=encode_cursor(page[-1].created_at, page[-1].id) if more else None,
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
