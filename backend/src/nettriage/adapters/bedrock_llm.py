"""Amazon Bedrock as the triage model (spec §8.3): the Converse API, with the answer constrained
to output schema v1 (structured output).

- The model is called on demand in its own Region, never through a cross-Region inference
  profile, which the account can't use (spec Revision 2, R4).
- Bedrock enforces only part of JSON Schema: no string lengths, patterns or array maximums.
  It gets the schema without them, and the Pydantic checks stay the gate (spec §8.3).
- A failed call raises `ProviderError` with a code that is safe to store and log, never
  Bedrock's message, which can quote the request.
- The client waits 20 seconds for an answer and retries throttling, server errors and timeouts
  3 times, with exponential backoff and full jitter (botocore's standard mode)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from nettriage.application.llm import Generation, ProviderError, Usage

if TYPE_CHECKING:
    from types_boto3_bedrock_runtime.client import BedrockRuntimeClient

GPT_OSS_20B = "openai.gpt-oss-20b-1:0"

# `total_max_attempts` counts the first call: 1 call and 3 retries.
BEDROCK_CONFIG = Config(
    connect_timeout=5,
    read_timeout=20,
    retries={"total_max_attempts": 4, "mode": "standard"},
)

# Model settings beyond Converse's own. gpt-oss reasons before it answers, and its reasoning
# counts toward the 700-token limit: keep it short.
MODEL_FIELDS: Mapping[str, Mapping[str, Any]] = {GPT_OSS_20B: {"reasoning_effort": "low"}}

# The JSON Schema keywords Bedrock's structured output enforces (and `minItems` only as 0 or 1).
_SUPPORTED = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "$defs",
        "$ref",
        "anyOf",
        "allOf",
        "format",
        "minItems",
    }
)

_CODES = {
    "ThrottlingException": "provider_throttled",
    "ServiceQuotaExceededException": "provider_throttled",
    "ModelTimeoutException": "provider_timeout",
    "InternalServerException": "provider_unavailable",
    "ServiceUnavailableException": "provider_unavailable",
    "ModelNotReadyException": "provider_unavailable",
    "ModelErrorException": "provider_unavailable",
    # The $5 budget action denies the call (spec §6.7), as would a missing permission.
    "AccessDeniedException": "provider_denied",
    "ValidationException": "provider_rejected",
    "ResourceNotFoundException": "provider_rejected",
}


def bedrock_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """The schema with only the keywords Bedrock supports. Property and definition names are kept;
    a `minItems` above 1 is dropped."""
    reduced: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _SUPPORTED or (key == "minItems" and value > 1):
            continue
        if key in ("properties", "$defs"):
            reduced[key] = {name: bedrock_schema(item) for name, item in value.items()}
        elif key == "items":
            reduced[key] = bedrock_schema(value)
        elif key in ("anyOf", "allOf"):
            reduced[key] = [bedrock_schema(item) for item in value]
        else:
            reduced[key] = value
    return reduced


@dataclass
class BedrockProvider:
    client: BedrockRuntimeClient
    model_id: str
    # OpenTelemetry's `gen_ai.provider.name` for Bedrock, also stored with each analysis.
    name: str = "aws.bedrock"
    fields: Mapping[str, Any] = field(init=False)

    def __post_init__(self) -> None:
        self.fields = MODEL_FIELDS.get(self.model_id, {})

    def generate_structured(
        self,
        system: str,
        user_json: str,
        schema: dict[str, Any],
        max_tokens: int,
        temperature: float,
    ) -> Generation:
        request: dict[str, Any] = {
            "modelId": self.model_id,
            "system": [{"text": system}],
            "messages": [{"role": "user", "content": [{"text": user_json}]}],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
            "outputConfig": {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {
                            "name": "triage_output",
                            "schema": json.dumps(bedrock_schema(schema)),
                        }
                    },
                }
            },
        }
        if self.fields:
            request["additionalModelRequestFields"] = dict(self.fields)
        try:
            response = self.client.converse(**request)
        except ClientError as error:
            raise ProviderError(
                _CODES.get(error.response["Error"]["Code"], "provider_error")
            ) from None
        except ReadTimeoutError, ConnectTimeoutError:
            raise ProviderError("provider_timeout") from None
        except EndpointConnectionError, ConnectionClosedError:
            raise ProviderError("provider_unavailable") from None
        except BotoCoreError:
            raise ProviderError("provider_error") from None
        # gpt-oss also returns its reasoning, as `reasoningContent` blocks: only the text answers.
        text = "".join(block.get("text", "") for block in response["output"]["message"]["content"])
        return Generation(
            output=_parsed(text),
            usage=Usage(response["usage"]["inputTokens"], response["usage"]["outputTokens"]),
            model_id=self.model_id,
            latency_ms=response["metrics"]["latencyMs"],
            finish_reason=response["stopReason"],
        )


def _parsed(text: str) -> Any:
    """The answer as JSON, or None when it isn't (cut off at the token limit, or prose)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
