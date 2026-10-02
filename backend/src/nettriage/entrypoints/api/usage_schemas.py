"""The org's AI usage as the API returns it (spec §7): per day, and in total."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from nettriage.adapters.ai_usage import DayUsage


class UsageTotalsOut(BaseModel):
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


class DayUsageOut(UsageTotalsOut):
    day: date


class UsageOut(BaseModel):
    """Newest day first. Days without a model call aren't listed."""

    days: list[DayUsageOut]
    totals: UsageTotalsOut

    @classmethod
    def of(cls, days: list[DayUsage]) -> UsageOut:
        return cls(
            days=[DayUsageOut(**vars(entry)) for entry in days],
            totals=UsageTotalsOut(
                calls=sum(entry.calls for entry in days),
                input_tokens=sum(entry.input_tokens for entry in days),
                output_tokens=sum(entry.output_tokens for entry in days),
                cost_usd=sum((entry.cost_usd for entry in days), Decimal(0)),
            ),
        )
