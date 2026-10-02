"""AI explanations through the API (spec §7, §8.3): re-run a finding's explanation, and rate one.

A re-run is queued for the triage worker, which explains the finding within the org's budget,
unless the finding's latest analysis for the current model, prompt and input already succeeded:
then that answer is returned and nothing is spent (the owner's decision, Plan 5c). Re-runs are
limited per user (`ai.rerun.user`: 10 an hour, 3 at once)."""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from nettriage.adapters.ai_analyses import current_explanation, rate_analysis
from nettriage.adapters.triage_queue import TriageQueueError
from nettriage.application.rate_limits import POLICIES
from nettriage.entrypoints.api.access import OrgContext, OrgMember, enforce, unavailable
from nettriage.entrypoints.api.auditing import audit
from nettriage.entrypoints.api.finding_schemas import AiAnalysisOut, FeedbackIn, RerunOut
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.services import get_services
from nettriage.platform.trace_context import current_traceparent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/orgs/{org_id}/findings/{finding_id}/ai-analyses")

RERUN = "Explaining this finding again"


@router.post(
    "",
    status_code=202,
    response_model=RerunOut,
    responses={200: {"model": RerunOut, "description": "Already explained: the answer"}},
)
def rerun(
    request: Request,
    org_id: UUID,
    finding_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("ai:request"))],
) -> JSONResponse:
    """Explain the finding again: 202 when it's queued, or 200 with the answer when it's already
    explained for the current model and prompt."""
    services = get_services(request)
    enforce(request, POLICIES["ai.rerun.user"], str(org.user_id), actor=org.user_id)
    with org_rules(request, RERUN, org=org, permission="ai:request"):
        explained = current_explanation(
            services.database, org.org_id, finding_id, model_id=services.ai_model_id
        )
    if explained is not None:
        body = RerunOut(status="explained", ai_analysis=AiAnalysisOut.of(explained))
        return JSONResponse(body.model_dump(mode="json"), status_code=200)
    try:
        services.triage.send(org.org_id, [finding_id], current_traceparent())
    except (BotoCoreError, ClientError, TriageQueueError) as error:
        logger.warning("rerun_not_queued", extra={"error_code": type(error).__name__})
        raise unavailable(RERUN) from None
    audit(
        request,
        action="ai.rerun_requested",
        outcome="success",
        actor_user_id=org.user_id,
        org_id=org.org_id,
        target_type="finding",
        target_id=str(finding_id),
    )
    return JSONResponse(
        RerunOut(status="queued", ai_analysis=None).model_dump(mode="json"), status_code=202
    )


@router.put("/{analysis_id}/feedback")
def rate(
    request: Request,
    org_id: UUID,
    finding_id: UUID,
    analysis_id: UUID,
    body: FeedbackIn,
    org: Annotated[OrgContext, Depends(OrgMember("ai:feedback"))],
) -> AiAnalysisOut:
    """Rate one of the finding's explanations up or down; a new rating replaces the last. Only an
    explanation that succeeded can be rated (409 otherwise)."""
    with org_rules(request, "Rating this explanation", org=org, permission="ai:feedback"):
        rated = rate_analysis(
            get_services(request).database,
            org.org_id,
            org.user_id,
            finding_id,
            analysis_id,
            body.feedback,
        )
    return AiAnalysisOut.of(rated)
