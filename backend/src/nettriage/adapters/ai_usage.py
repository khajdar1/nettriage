"""An org's AI usage per UTC day (spec §5.2, §7), as `app_api` in the caller's org. The triage
worker counts every model call in `ai_usage` (Plan 5c)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Engine, text

from nettriage.adapters.postgres import tenant_transaction


@dataclass(frozen=True)
class DayUsage:
    day: date
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


def daily_usage(engine: Engine, org_id: UUID, user_id: UUID, days: int) -> list[DayUsage]:
    """The org's usage for the last `days` UTC days, today included, newest first. Days without
    a model call have no row."""
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        rows = connection.execute(
            text(
                "SELECT day, calls, input_tokens, output_tokens, cost_usd FROM ai_usage "
                "WHERE org_id = :org AND day > (now() AT TIME ZONE 'UTC')::date - :days "
                "ORDER BY day DESC"
            ),
            {"org": org_id, "days": days},
        ).all()
    return [DayUsage(**row._mapping) for row in rows]
