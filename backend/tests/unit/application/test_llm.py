"""The provider interface, the fake model, and what a call costs (spec §8.3)."""

from decimal import Decimal

import pytest

from nettriage.adapters.fake_llm import FakeProvider
from nettriage.application.llm import (
    PRICES,
    Price,
    ProviderError,
    Usage,
    cost,
    estimate_input_tokens,
)


def test_a_call_costs_its_tokens_at_the_models_prices() -> None:
    price = Price(Decimal("1.00"), Decimal("4.00"))

    assert cost(price, Usage(900, 300)) == Decimal("0.002100")
    assert cost(price, Usage(1, 0)) == Decimal("0.000001")  # rounded up, never down to zero
    assert cost(PRICES["fake-triage"], Usage(0, 0)) == Decimal("0.000000")


def test_the_input_estimate_is_one_token_per_three_characters_rounded_up() -> None:
    assert estimate_input_tokens("abc", "d") == 2
    assert estimate_input_tokens("") == 0


def test_the_fake_model_answers_from_its_script_and_records_each_call() -> None:
    fake = FakeProvider(script=[{"summary": "first"}, ProviderError("provider_unavailable")])

    generation = fake.generate_structured("system", '{"a":1}', {"type": "object"}, 700, 0.1)
    with pytest.raises(ProviderError) as failed:
        fake.generate_structured("system", '{"a":2}', {"type": "object"}, 700, 0.1)

    assert (generation.output, generation.usage, generation.model_id) == (
        {"summary": "first"},
        Usage(900, 300),
        "fake-triage",
    )
    assert failed.value.code == "provider_unavailable"
    assert [call.user_json for call in fake.calls] == ['{"a":1}', '{"a":2}']
    assert fake.calls[0].max_tokens == 700


def test_the_fake_model_says_when_its_script_ran_out() -> None:
    with pytest.raises(AssertionError, match="no scripted answer"):
        FakeProvider().generate_structured("s", "{}", {}, 700, 0.1)
