"""An organization's overview (Plan 6d): how its findings stand, for every member."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from nettriage.adapters.overview import org_overview
from nettriage.entrypoints.api.access import OrgContext, OrgMember
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.overview_schemas import OverviewOut
from nettriage.entrypoints.api.services import get_services

router = APIRouter(prefix="/v1/orgs/{org_id}")


@router.get("/overview")
def overview(
    request: Request,
    org_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("findings:read"))],
) -> OverviewOut:
    """Unresolved findings by severity, all findings by status, the unresolved ones unassigned
    and the caller's, those detected in the last 24 hours, the members and the last upload."""
    with org_rules(request, "The overview"):
        found = org_overview(get_services(request).database, org.org_id, org.user_id)
    return OverviewOut.of(found)
