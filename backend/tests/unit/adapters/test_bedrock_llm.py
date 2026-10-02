"""The Bedrock provider (spec §8.3): the Converse API with the answer constrained to output schema
v1, and Bedrock's errors as codes that are safe to store."""

import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import boto3
import pytest
from botocore.exceptions import ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError
from botocore.stub import Stubber

from nettriage.adapters.bedrock_llm import (
    BEDROCK_CONFIG,
    GPT_OSS_20B,
    BedrockProvider,
    bedrock_schema,
)
from nettriage.application.ai_output import output_schema
from nettriage.application.llm import PRICES, Price, ProviderError, Usage

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string", "maxLength": 600}},
    "required": ["summary"],
    "additionalProperties": False,
}


def response(content: list[dict[str, Any]], stop: str = "end_turn") -> dict[str, Any]:
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop,
        "usage": {"inputTokens": 1800, "outputTokens": 320, "totalTokens": 2120},
        "metrics": {"latencyMs": 1450},
    }


@pytest.fixture
def stubbed() -> Iterator[tuple[BedrockProvider, Stubber]]:
    client = boto3.client("bedrock-runtime", region_name="eu-north-1")
    with Stubber(client) as stubber:
        yield BedrockProvider(client, GPT_OSS_20B), stubber


def test_a_call_sends_the_prompt_the_data_and_the_schema_bedrock_supports(
    stubbed: tuple[BedrockProvider, Stubber],
) -> None:
    provider, stubber = stubbed
    stubber.add_response(
        "converse",
        response(
            [
                {"reasoningContent": {"reasoningText": {"text": "A port scan."}}},
                {"text": '{"summary": "A scan."}'},
            ]
        ),
        {
            "modelId": "openai.gpt-oss-20b-1:0",
            "system": [{"text": "You explain findings."}],
            "messages": [{"role": "user", "content": [{"text": '{"detector":"port_scan"}'}]}],
            "inferenceConfig": {"maxTokens": 700, "temperature": 0.1},
            "outputConfig": {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {
                            "name": "triage_output",
                            "schema": json.dumps(bedrock_schema(SCHEMA)),
                        }
                    },
                }
            },
            "additionalModelRequestFields": {"reasoning_effort": "low"},
        },
    )

    generation = provider.generate_structured(
        "You explain findings.", '{"detector":"port_scan"}', SCHEMA, 700, 0.1
    )

    assert generation.output == {"summary": "A scan."}
    assert generation.usage == Usage(1800, 320)
    assert (generation.model_id, generation.latency_ms, generation.finish_reason) == (
        "openai.gpt-oss-20b-1:0",
        1450,
        "end_turn",
    )
    stubber.assert_no_pending_responses()


@pytest.mark.parametrize("text", ["Sorry, I can't.", '{"summary": "cut o', ""])
def test_an_answer_that_isnt_json_has_no_output(
    stubbed: tuple[BedrockProvider, Stubber], text: str
) -> None:
    provider, stubber = stubbed
    stubber.add_response("converse", response([{"text": text}], stop="max_tokens"))

    generation = provider.generate_structured("s", "{}", SCHEMA, 700, 0.1)

    assert (generation.output, generation.finish_reason) == (None, "max_tokens")


@pytest.mark.parametrize(
    ("error", "code", "transient"),
    [
        ("ThrottlingException", "provider_throttled", True),
        ("ModelTimeoutException", "provider_timeout", True),
        ("ServiceUnavailableException", "provider_unavailable", True),
        ("InternalServerException", "provider_unavailable", True),
        ("ModelNotReadyException", "provider_unavailable", True),
        ("ModelErrorException", "provider_unavailable", True),
        ("AccessDeniedException", "provider_denied", False),
        ("ValidationException", "provider_rejected", False),
        ("ResourceNotFoundException", "provider_rejected", False),
        ("SomethingNewException", "provider_error", False),
    ],
)
def test_bedrock_errors_become_codes_that_can_be_stored(
    stubbed: tuple[BedrockProvider, Stubber], error: str, code: str, transient: bool
) -> None:
    provider, stubber = stubbed
    stubber.add_client_error("converse", error, "Input contains 203.0.113.9")

    with pytest.raises(ProviderError) as failed:
        provider.generate_structured("s", "{}", SCHEMA, 700, 0.1)

    assert (failed.value.code, failed.value.transient, str(failed.value)) == (code, transient, code)
    assert failed.value.__cause__ is None


class _Unreachable:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def converse(self, **_: Any) -> dict[str, Any]:
        raise self.error


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ReadTimeoutError(endpoint_url="https://bedrock-runtime"), "provider_timeout"),
        (ConnectTimeoutError(endpoint_url="https://bedrock-runtime"), "provider_timeout"),
        (EndpointConnectionError(endpoint_url="https://bedrock-runtime"), "provider_unavailable"),
    ],
)
def test_a_slow_or_unreachable_bedrock_is_a_transient_failure(error: Exception, code: str) -> None:
    provider = BedrockProvider(_Unreachable(error), GPT_OSS_20B)  # type: ignore[arg-type]

    with pytest.raises(ProviderError) as failed:
        provider.generate_structured("s", "{}", SCHEMA, 700, 0.1)

    assert (failed.value.code, failed.value.transient) == (code, True)


def _keywords(schema: Any) -> set[str]:
    """Every JSON Schema keyword used anywhere in a schema (not property or definition names)."""
    found: set[str] = set()
    if isinstance(schema, list):
        for item in schema:
            found |= _keywords(item)
    elif isinstance(schema, dict):
        for key, value in schema.items():
            found.add(key)
            for item in value.values() if key in ("properties", "$defs") else [value]:
                found |= _keywords(item)
    return found


def test_the_schema_bedrock_gets_keeps_only_the_keywords_it_supports() -> None:
    reduced = bedrock_schema(output_schema())

    assert _keywords(reduced) <= {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "$defs",
        "$ref",
        "minItems",
    }
    assert reduced["additionalProperties"] is False
    assert reduced["required"] == output_schema()["required"]
    assert reduced["properties"]["recommended_next_steps"]["minItems"] == 1
    assert reduced["properties"]["confidence"]["enum"] == ["low", "medium", "high"]


def test_gpt_oss_20b_is_priced_as_in_stockholm() -> None:
    assert PRICES[GPT_OSS_20B] == Price(Decimal("0.07"), Decimal("0.30"))


def test_the_client_waits_20_seconds_and_retries_3_times() -> None:
    settings = vars(BEDROCK_CONFIG)

    assert (settings["read_timeout"], settings["retries"]) == (
        20,
        {"total_max_attempts": 4, "mode": "standard"},
    )
