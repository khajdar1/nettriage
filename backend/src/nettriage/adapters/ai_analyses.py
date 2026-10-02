"""The API's side of AI analyses (spec §7, §8.3), as `app_api` in the caller's org: whether a
finding is already explained for the current model and prompt, and a reader's rating of an
explanation."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Engine, text

from nettriage.adapters.ai_store import find_cached
from nettriage.adapters.ai_subjects import load_subject
from nettriage.adapters.findings import AiAnalysis, read_analysis
from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.ai_input import PROMPT_VERSION, input_hash, user_content
from nettriage.application.organizations import NotFound, OrgRuleError


class NotExplained(OrgRuleError):
    """Only an explanation that succeeded can be rated."""


def current_explanation(
    engine: Engine,
    org_id: UUID,
    finding_id: UUID,
    *,
    model_id: str,
    prompt_version: str = PROMPT_VERSION,
) -> AiAnalysis | None:
    """The finding's succeeded analysis for the current model, prompt and input, if there is one
    (the triage worker's cache). Raises NotFound for a finding outside the org."""
    subject = load_subject(engine, org_id, finding_id)
    cached = find_cached(
        engine,
        org_id,
        finding_id,
        model_id=model_id,
        prompt_version=prompt_version,
        input_hash=input_hash(user_content(subject)),
    )
    if cached is None:
        return None
    with tenant_transaction(engine, org_id=org_id) as connection:
        return read_analysis(connection, cached.id)


def rate_analysis(
    engine: Engine,
    org_id: UUID,
    user_id: UUID,
    finding_id: UUID,
    analysis_id: UUID,
    feedback: str,
) -> AiAnalysis:
    """Record the reader's rating of one of the finding's succeeded analyses; a new rating
    replaces the previous one. Rating doesn't make the analysis newer."""
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        rated = connection.execute(
            text(
                "UPDATE ai_analyses SET feedback = :feedback, feedback_by = :user "
                "WHERE id = :id AND finding_id = :finding AND status = 'succeeded' RETURNING id"
            ),
            {"feedback": feedback, "user": user_id, "id": analysis_id, "finding": finding_id},
        ).scalar_one_or_none()
        if rated is None:
            exists = connection.execute(
                text("SELECT 1 FROM ai_analyses WHERE id = :id AND finding_id = :finding"),
                {"id": analysis_id, "finding": finding_id},
            ).scalar_one_or_none()
            if exists is None:
                raise NotFound("No such AI analysis.")
            raise NotExplained("Only an AI explanation that succeeded can be rated.")
        return read_analysis(connection, analysis_id)
