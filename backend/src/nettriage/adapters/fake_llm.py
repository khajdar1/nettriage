"""A scripted model (spec §8.3): the provider for tests and local development. Each call returns
the next scripted answer, or raises it when it's an exception, and every call is recorded."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nettriage.application.llm import Generation, Usage


@dataclass(frozen=True)
class Call:
    system: str
    user_json: str
    schema: dict[str, Any]
    max_tokens: int
    temperature: float


@dataclass
class FakeProvider:
    script: list[Any] = field(default_factory=list)
    name: str = "fake"
    model_id: str = "fake-triage"
    usage: Usage = field(default_factory=lambda: Usage(input_tokens=900, output_tokens=300))
    latency_ms: int = 850
    calls: list[Call] = field(default_factory=list)

    def generate_structured(
        self,
        system: str,
        user_json: str,
        schema: dict[str, Any],
        max_tokens: int,
        temperature: float,
    ) -> Generation:
        self.calls.append(Call(system, user_json, schema, max_tokens, temperature))
        if not self.script:
            raise AssertionError("FakeProvider has no scripted answer left")
        answer = self.script.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Generation(
            output=answer,
            usage=self.usage,
            model_id=self.model_id,
            latency_ms=self.latency_ms,
            finish_reason="end_turn",
        )
