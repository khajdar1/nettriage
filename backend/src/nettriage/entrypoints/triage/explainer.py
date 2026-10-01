"""Explaining one finding (spec §6.6, §8.3, §8.6).

1. Read the finding's typed fields as `app_triage`, and hash what the model would see.
2. A succeeded analysis of the same input, model and prompt is the answer: no call (`cached`).
3. Reserve the call's estimate in the org's token budget and the global spend cap; if either is
   spent, or can't be read, store `skipped_budget` and make no call.
4. Call the model with prompt v1 and output schema v1, then settle the budget to the real usage.
   A provider failure releases the reservation and stores `failed`.
5. Check the answer. If it fails a check, ask once more with the errors (`validation_errors`);
   if that fails too, store `invalid_output`.
6. Store the analysis, with the AI's techniques and an `ai_explained` event when it succeeded.

Logs and traces never hold the prompt, the finding's data or the model's answer (spec §9.3)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from opentelemetry.trace import Status, StatusCode, Tracer
from sqlalchemy import Engine

from nettriage.adapters.ai_budget import AiBudget, BudgetExhausted, BudgetUnavailable
from nettriage.adapters.ai_store import AnalysisRecord, AnalysisStatus, find_cached, save_analysis
from nettriage.adapters.ai_subjects import load_subject
from nettriage.application.ai_input import (
    PROMPT_VERSION,
    TriageSubject,
    canonical_json,
    input_hash,
    user_content,
)
from nettriage.application.ai_output import (
    OUTPUT_SCHEMA_VERSION,
    TriageOutput,
    check_output,
    output_schema,
)
from nettriage.application.llm import (
    MAX_TOKENS,
    PRICES,
    TEMPERATURE,
    Generation,
    LlmProvider,
    Price,
    ProviderError,
    Usage,
    cost,
    estimate_input_tokens,
)
from nettriage.platform.metrics import AiMetrics
from nettriage.prompts import triage_prompt

logger = logging.getLogger(__name__)

type Outcome = Literal["succeeded", "cached", "failed", "skipped_budget", "invalid_output"]

# One answer, then one repair with the errors (spec §8.3).
ATTEMPTS = 2


@dataclass(frozen=True)
class Explained:
    outcome: Outcome
    analysis_id: UUID


@dataclass
class _Spent:
    """What the calls for one analysis used, across the answer and its repair."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal = field(default_factory=Decimal)
    latency_ms: int = 0

    def add(self, generation: Generation, call_cost: Decimal) -> None:
        self.calls += 1
        self.input_tokens += generation.usage.input_tokens
        self.output_tokens += generation.usage.output_tokens
        self.cost_usd += call_cost
        self.latency_ms += generation.latency_ms


@dataclass
class Explainer:
    database: Engine
    provider: LlmProvider
    budget: AiBudget
    metrics: AiMetrics
    tracer: Tracer
    prompt_version: str = PROMPT_VERSION
    price: Price = field(init=False)

    def __post_init__(self) -> None:
        # A model without a price could never be budgeted: refuse it at startup.
        self.price = PRICES[self.provider.model_id]

    def explain(self, org_id: UUID, finding_id: UUID) -> Explained:
        subject = load_subject(self.database, org_id, finding_id)
        content = user_content(subject)
        key = input_hash(content)
        cached = find_cached(
            self.database,
            org_id,
            finding_id,
            model_id=self.provider.model_id,
            prompt_version=self.prompt_version,
            input_hash=key,
        )
        if cached is not None:
            self.metrics.cache_hits.add(1)
            return self._done("cached", cached.id)
        system = triage_prompt(self.prompt_version)
        schema = output_schema()
        request = canonical_json(content)
        spent = _Spent()
        for _ in range(ATTEMPTS):
            try:
                generation = self._generate(org_id, system, request, schema, spent)
            except BudgetExhausted as error:
                code = f"budget_exhausted_{error.scope}"
                return self._store(subject, key, "skipped_budget", spent, error_code=code)
            except BudgetUnavailable:
                code = "budget_unavailable"
                return self._store(subject, key, "skipped_budget", spent, error_code=code)
            except ProviderError as error:
                return self._store(subject, key, "failed", spent, error_code=error.code)
            checked = check_output(generation.output, subject)
            if checked.output is not None:
                return self._store(subject, key, "succeeded", spent, output=checked.output)
            request = canonical_json(content | {"validation_errors": list(checked.errors)})
        return self._store(subject, key, "invalid_output", spent, error_code="checks_failed")

    def _generate(
        self, org_id: UUID, system: str, request: str, schema: dict[str, Any], spent: _Spent
    ) -> Generation:
        estimate = estimate_input_tokens(system, request)
        reservation = self.budget.reserve(
            org_id, estimate + MAX_TOKENS, cost(self.price, Usage(estimate, MAX_TOKENS))
        )
        attributes = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": self.provider.name,
            "gen_ai.request.model": self.provider.model_id,
        }
        with self.tracer.start_as_current_span(
            "triage.generate",
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                generation = self.provider.generate_structured(
                    system, request, schema, MAX_TOKENS, TEMPERATURE
                )
            except Exception as error:
                self.budget.release(reservation)
                code = error.code if isinstance(error, ProviderError) else type(error).__name__
                span.set_status(Status(StatusCode.ERROR, code))
                raise
            call_cost = cost(self.price, generation.usage)
            self.budget.settle(
                reservation,
                generation.usage.input_tokens + generation.usage.output_tokens,
                call_cost,
            )
            span.set_attributes(
                {
                    "gen_ai.usage.input_tokens": generation.usage.input_tokens,
                    "gen_ai.usage.output_tokens": generation.usage.output_tokens,
                    "gen_ai.response.finish_reasons": [generation.finish_reason],
                }
            )
        spent.add(generation, call_cost)
        for token_type, tokens in (
            ("input", generation.usage.input_tokens),
            ("output", generation.usage.output_tokens),
        ):
            self.metrics.token_usage.record(tokens, attributes | {"gen_ai.token.type": token_type})
        self.metrics.operation_duration.record(generation.latency_ms / 1000, attributes)
        self.metrics.cost_usd.add(float(call_cost), attributes)
        return generation

    def _store(
        self,
        subject: TriageSubject,
        key: str,
        status: AnalysisStatus,
        spent: _Spent,
        *,
        output: TriageOutput | None = None,
        error_code: str | None = None,
    ) -> Explained:
        called = spent.calls > 0
        analysis_id = save_analysis(
            self.database,
            AnalysisRecord(
                org_id=subject.org_id,
                finding_id=subject.finding_id,
                status=status,
                provider=self.provider.name,
                model_id=self.provider.model_id,
                prompt_version=self.prompt_version,
                output_schema_version=OUTPUT_SCHEMA_VERSION,
                input_hash=key,
                output=None if output is None else output.model_dump(mode="json"),
                input_tokens=spent.input_tokens if called else None,
                output_tokens=spent.output_tokens if called else None,
                cost_usd=spent.cost_usd if called else None,
                latency_ms=spent.latency_ms if called else None,
                error_code=error_code,
                techniques=()
                if output is None
                else tuple((claim.id, claim.rationale) for claim in output.attack_techniques),
            ),
        )
        return self._done(status, analysis_id)

    def _done(self, outcome: Outcome, analysis_id: UUID) -> Explained:
        self.metrics.outcomes.add(1, {"outcome": outcome})
        logger.info("finding_explained", extra={"outcome": outcome})
        return Explained(outcome=outcome, analysis_id=analysis_id)
