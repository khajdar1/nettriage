"""The DynamoDB `runtime` table (spec §5.5): a string partition key `pk`, and a TTL attribute
`expires_at` in epoch seconds. DynamoDB deletes expired items only eventually, so readers check
`expires_at` themselves."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from botocore.exceptions import ClientError


def epoch_seconds(moment: datetime) -> int:
    return math.ceil(moment.timestamp())


def epoch_millis(moment: datetime) -> int:
    return round(moment.timestamp() * 1000)


def from_millis(value: str) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, UTC)


def is_condition_failure(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"
