"""The API's side of AI analyses (spec §7, §8.3), as `app_api` in the caller's org: whether a
finding is already explained for the current model and prompt."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Engine

from nettriage.adapters.ai_store import find_cached
from nettriage.adapters.ai_subjects import load_subject
from nettriage.adapters.findings import AiAnalysis, read_analysis
from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.ai_input import PROMPT_VERSION, input_hash, user_content


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
