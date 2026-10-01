"""Storing AI analyses (spec §5.2, §8.3), as `app_triage` in the finding's org.

An analysis is keyed by finding, model, prompt version and input hash. A succeeded one is the
cache: the same input is never sent to the same model with the same prompt twice. Any other
outcome may be tried again, and the new attempt updates the same row; a succeeded row is never
overwritten.

A succeeded analysis also adds the model's techniques to the finding (`source = 'ai'`) and an
`ai_explained` event to its history, in the same transaction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid7

from sqlalchemy import Connection, Engine, text

from nettriage.adapters.postgres import tenant_transaction

type AnalysisStatus = Literal["succeeded", "failed", "skipped_budget", "invalid_output"]


@dataclass(frozen=True)
class AnalysisRecord:
    org_id: UUID
    finding_id: UUID
    status: AnalysisStatus
    provider: str
    model_id: str
    prompt_version: str
    output_schema_version: str
    input_hash: str
    output: dict[str, Any] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal | None = None
    latency_ms: int | None = None
    error_code: str | None = None
    # (technique ID, rationale) pairs from a succeeded output.
    techniques: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class CachedAnalysis:
    id: UUID
    output: dict[str, Any]


def find_cached(
    engine: Engine,
    org_id: UUID,
    finding_id: UUID,
    *,
    model_id: str,
    prompt_version: str,
    input_hash: str,
) -> CachedAnalysis | None:
    with tenant_transaction(engine, org_id=org_id) as connection:
        row = connection.execute(
            text(
                "SELECT id, output FROM ai_analyses WHERE org_id = :org AND finding_id = :finding "
                "AND model_id = :model AND prompt_version = :prompt AND input_hash = :hash "
                "AND status = 'succeeded'"
            ),
            {
                "org": org_id,
                "finding": finding_id,
                "model": model_id,
                "prompt": prompt_version,
                "hash": input_hash,
            },
        ).one_or_none()
    return None if row is None else CachedAnalysis(id=row.id, output=row.output)


def save_analysis(engine: Engine, record: AnalysisRecord) -> UUID:
    """Store an attempt and return its analysis's ID. A succeeded analysis already stored for the
    same key wins: the attempt changes nothing."""
    with tenant_transaction(engine, org_id=record.org_id) as connection:
        saved = connection.execute(
            text(
                "INSERT INTO ai_analyses (id, org_id, finding_id, status, provider, model_id, "
                "prompt_version, output_schema_version, input_hash, output, input_tokens, "
                "output_tokens, cost_usd, latency_ms, error_code) VALUES (:id, :org, :finding, "
                ":status, :provider, :model, :prompt, :schema, :hash, CAST(:output AS jsonb), "
                ":input_tokens, :output_tokens, :cost, :latency, :error) "
                "ON CONFLICT (finding_id, model_id, prompt_version, input_hash) DO UPDATE SET "
                "status = EXCLUDED.status, output = EXCLUDED.output, "
                "input_tokens = EXCLUDED.input_tokens, output_tokens = EXCLUDED.output_tokens, "
                "cost_usd = EXCLUDED.cost_usd, latency_ms = EXCLUDED.latency_ms, "
                "error_code = EXCLUDED.error_code WHERE ai_analyses.status <> 'succeeded' "
                "RETURNING id"
            ),
            {
                "id": uuid7(),
                "org": record.org_id,
                "finding": record.finding_id,
                "status": record.status,
                "provider": record.provider,
                "model": record.model_id,
                "prompt": record.prompt_version,
                "schema": record.output_schema_version,
                "hash": record.input_hash,
                "output": None if record.output is None else json.dumps(record.output),
                "input_tokens": record.input_tokens,
                "output_tokens": record.output_tokens,
                "cost": record.cost_usd,
                "latency": record.latency_ms,
                "error": record.error_code,
            },
        ).scalar_one_or_none()
        if saved is None:
            existing: UUID = connection.execute(
                text(
                    "SELECT id FROM ai_analyses WHERE finding_id = :finding AND model_id = :model "
                    "AND prompt_version = :prompt AND input_hash = :hash"
                ),
                {
                    "finding": record.finding_id,
                    "model": record.model_id,
                    "prompt": record.prompt_version,
                    "hash": record.input_hash,
                },
            ).scalar_one()
            return existing
        analysis_id: UUID = saved
        if record.status == "succeeded":
            _add_techniques(connection, record)
            connection.execute(
                text(
                    "INSERT INTO finding_events (id, org_id, finding_id, type, payload) "
                    "VALUES (:id, :org, :finding, 'ai_explained', CAST(:payload AS jsonb))"
                ),
                {
                    "id": uuid7(),
                    "org": record.org_id,
                    "finding": record.finding_id,
                    "payload": json.dumps(
                        {
                            "analysis_id": str(analysis_id),
                            "model_id": record.model_id,
                            "prompt_version": record.prompt_version,
                        }
                    ),
                },
            )
    return analysis_id


def _add_techniques(connection: Connection, record: AnalysisRecord) -> None:
    if not record.techniques:
        return
    connection.execute(
        text(
            "INSERT INTO finding_techniques (finding_id, technique_id, source, org_id, rationale) "
            "VALUES (:finding, :technique, 'ai', :org, :rationale) "
            "ON CONFLICT (finding_id, technique_id, source) "
            "DO UPDATE SET rationale = EXCLUDED.rationale"
        ),
        [
            {
                "finding": record.finding_id,
                "technique": technique,
                "org": record.org_id,
                "rationale": rationale,
            }
            for technique, rationale in record.techniques
        ],
    )
