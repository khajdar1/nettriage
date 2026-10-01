"""The model behind triage (spec §8.3): one interface, `generate_structured`, so the provider can
change (Bedrock in Plan 5b, a scripted fake in tests and local development) while the prompt,
the checks, the budget and the cost stay the same."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import ROUND_UP, Decimal
from typing import Any, Protocol

# Call settings (spec §8.3): short, factual answers.
MAX_TOKENS = 700
TEMPERATURE = 0.1
_MILLION = Decimal(1_000_000)
_MICRO_USD = Decimal("0.000001")
# What `ai_analyses.error_code` can hold (its CHECK, migration 0008).
_CODE = re.compile(r"[a-z][a-z_]{0,49}")


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class Generation:
    """What the provider returned. `output` is the parsed JSON answer, or None when the model's
    text wasn't JSON; the checks then ask for a repair."""

    output: Any
    usage: Usage
    model_id: str
    latency_ms: int
    finish_reason: str


class ProviderError(Exception):
    """The provider couldn't answer (throttled, down, timed out), after its own retries. `code` is
    safe to store and log: one the `error_code` column can't hold (such as `ThrottlingException`)
    becomes `provider_error`. The message is the code too, never the provider's text."""

    def __init__(self, code: str) -> None:
        self.code = code if _CODE.fullmatch(code) else "provider_error"
        super().__init__(self.code)


class LlmProvider(Protocol):
    name: str
    model_id: str

    def generate_structured(
        self,
        system: str,
        user_json: str,
        schema: dict[str, Any],
        max_tokens: int,
        temperature: float,
    ) -> Generation: ...


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input_per_million: Decimal
    output_per_million: Decimal


# Per-model prices (spec §8.3). Plan 5b adds the Bedrock models it can call.
PRICES: dict[str, Price] = {
    "fake-triage": Price(Decimal("1.00"), Decimal("4.00")),
}


def cost(price: Price, usage: Usage) -> Decimal:
    """What a call cost, rounded up to a millionth of a dollar (`cost_usd` is numeric(10,6))."""
    total = (
        Decimal(usage.input_tokens) * price.input_per_million
        + Decimal(usage.output_tokens) * price.output_per_million
    ) / _MILLION
    return total.quantize(_MICRO_USD, rounding=ROUND_UP)


def estimate_input_tokens(*texts: str) -> int:
    """A generous guess at the prompt's tokens before the call: one per three characters. The
    budget reserves the guess plus `max_tokens`, then settles to the real usage."""
    return math.ceil(sum(len(text) for text in texts) / 3)
