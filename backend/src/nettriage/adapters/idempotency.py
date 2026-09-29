"""Idempotency keys in the DynamoDB `runtime` table (spec §5.5, §7): `IDEMP#<user>#<key>`
remembers a request's hash and, once it's done, its response, for 24 hours. A retry with the
same key and request gets the same response instead of doing the work twice."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from botocore.exceptions import ClientError

from nettriage.adapters.runtime_table import epoch_seconds, is_condition_failure

if TYPE_CHECKING:
    from types_boto3_dynamodb.client import DynamoDBClient

IDEMPOTENCY_TTL = timedelta(hours=24)
# A request still running holds its key only this long, well past the API's 29-second
# timeout: if it succeeded but storing its response failed, a retry isn't refused for a day.
IN_PROGRESS_TTL = timedelta(minutes=1)


@dataclass(frozen=True)
class StoredResponse:
    status: int
    body: dict[str, Any]


class IdempotencyMismatch(Exception):
    """The key was used before with a different request."""


class IdempotencyInProgress(Exception):
    """The first request with this key hasn't finished yet."""


class IdempotencyStore:
    def __init__(self, client: DynamoDBClient, table: str) -> None:
        self._client = client
        self._table = table

    def begin(
        self, user_id: UUID, key: str, request_hash: str, now: datetime
    ) -> StoredResponse | None:
        """None if this request should run now; the stored response if it already ran."""
        try:
            self._client.put_item(
                TableName=self._table,
                Item={
                    "pk": {"S": _pk(user_id, key)},
                    "request_hash": {"S": request_hash},
                    "state": {"S": "in_progress"},
                    "expires_at": {"N": str(epoch_seconds(now + IN_PROGRESS_TTL))},
                },
                ConditionExpression="attribute_not_exists(pk) OR expires_at <= :now",
                ExpressionAttributeValues={":now": {"N": str(epoch_seconds(now))}},
                ReturnValuesOnConditionCheckFailure="ALL_OLD",
            )
        except ClientError as error:
            if not is_condition_failure(error):
                raise
            item: dict[str, Any] = error.response.get("Item") or self._client.get_item(  # type: ignore[assignment]
                TableName=self._table, Key={"pk": {"S": _pk(user_id, key)}}, ConsistentRead=True
            ).get("Item", {})
            if item.get("request_hash", {}).get("S") != request_hash:
                raise IdempotencyMismatch from None
            if item.get("state", {}).get("S") != "done":
                raise IdempotencyInProgress from None
            return StoredResponse(
                status=int(item["status"]["N"]), body=json.loads(item["response"]["S"])
            )
        return None

    def finish(self, user_id: UUID, key: str, response: StoredResponse, now: datetime) -> None:
        """Store the response for retries, for 24 hours."""
        self._client.update_item(
            TableName=self._table,
            Key={"pk": {"S": _pk(user_id, key)}},
            UpdateExpression=(
                "SET #state = :done, #status = :status, #response = :response, "
                "expires_at = :expires"
            ),
            ExpressionAttributeNames={
                "#state": "state",
                "#status": "status",
                "#response": "response",
            },
            ExpressionAttributeValues={
                ":done": {"S": "done"},
                ":status": {"N": str(response.status)},
                ":response": {"S": json.dumps(response.body)},
                ":expires": {"N": str(epoch_seconds(now + IDEMPOTENCY_TTL))},
            },
        )

    def abandon(self, user_id: UUID, key: str) -> None:
        """Forget a request that failed, so the client can retry it with the same key."""
        self._client.delete_item(TableName=self._table, Key={"pk": {"S": _pk(user_id, key)}})


def _pk(user_id: UUID, key: str) -> str:
    return f"IDEMP#{user_id}#{key}"
