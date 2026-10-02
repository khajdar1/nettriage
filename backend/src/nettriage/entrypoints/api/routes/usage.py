"""An org's AI usage (spec §7): calls, tokens and cost per UTC day, for its Owners and Admins."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from nettriage.adapters.ai_usage import daily_usage
from nettriage.entrypoints.api.access import OrgContext, OrgMember
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.services import get_services
from nettriage.entrypoints.api.usage_schemas import UsageOut

router = APIRouter(prefix="/v1/orgs/{org_id}")


@router.get("/usage")
def usage(
    request: Request,
    org_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("usage:read"))],
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> UsageOut:
    """The last `days` UTC days of AI use (30 unless asked; at most 90), newest first."""
    with org_rules(request, "Reading the AI usage", org=org, permission="usage:read"):
        found = daily_usage(get_services(request).database, org.org_id, org.user_id, days)
    return UsageOut.of(found)
