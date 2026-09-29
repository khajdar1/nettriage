"""Sessions in the DynamoDB `runtime` table (spec §5.5).

- `SESSION#<sha256(session id)>`: the user, the CSRF token, timestamps, IP and user agent.
- `USERSESS#<user id>`: the set of the user's session keys, for "sign out everywhere".

Reads are strongly consistent, so a signed-out session stops working at once.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from itertools import batched
from typing import TYPE_CHECKING
from uuid import UUID

from botocore.exceptions import ClientError

from nettriage.adapters.runtime_table import (
    epoch_millis,
    epoch_seconds,
    from_millis,
    is_condition_failure,
)
from nettriage.application.sessions import ABSOLUTE_TIMEOUT, Session, new_secret, session_key

if TYPE_CHECKING:
    from types_boto3_dynamodb.client import DynamoDBClient
    from types_boto3_dynamodb.type_defs import (
        AttributeValueTypeDef,
        WriteRequestOutputTypeDef,
        WriteRequestTypeDef,
    )

MAX_USER_AGENT = 256
BATCH_LIMIT = 25


def _session_pk(key: str) -> str:
    return f"SESSION#{key}"


def _user_pk(user_id: UUID) -> str:
    return f"USERSESS#{user_id}"


class SessionStore:
    def __init__(self, client: DynamoDBClient, table: str) -> None:
        self._client = client
        self._table = table

    def create(
        self, *, user_id: UUID, now: datetime, ip: str | None, user_agent: str | None
    ) -> tuple[str, Session]:
        """A new session. Returns the session ID for the cookie; only its hash is stored."""
        session_id = new_secret()
        session = Session(
            key=session_key(session_id),
            user_id=user_id,
            csrf_token=new_secret(),
            created_at=now,
            last_seen_at=now,
        )
        item: dict[str, AttributeValueTypeDef] = {
            "pk": {"S": _session_pk(session.key)},
            "user_id": {"S": str(user_id)},
            "csrf_token": {"S": session.csrf_token},
            "created_at": {"N": str(epoch_millis(now))},
            "last_seen_at": {"N": str(epoch_millis(now))},
            "expires_at": {"N": str(epoch_seconds(session.expires_at))},
        }
        if ip:
            item["ip"] = {"S": ip}
        if user_agent:
            item["user_agent"] = {"S": user_agent[:MAX_USER_AGENT]}
        self._client.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": self._table,
                        "Item": item,
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }
                },
                {
                    "Update": {
                        "TableName": self._table,
                        "Key": {"pk": {"S": _user_pk(user_id)}},
                        "UpdateExpression": "ADD sessions :key SET expires_at = :expires",
                        "ExpressionAttributeValues": {
                            ":key": {"SS": [session.key]},
                            ":expires": {"N": str(epoch_seconds(now + ABSOLUTE_TIMEOUT))},
                        },
                    }
                },
            ]
        )
        return session_id, session

    def get(self, session_id: str) -> Session | None:
        response = self._client.get_item(
            TableName=self._table,
            Key={"pk": {"S": _session_pk(session_key(session_id))}},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if item is None:
            return None
        return Session(
            key=session_key(session_id),
            user_id=UUID(item["user_id"]["S"]),
            csrf_token=item["csrf_token"]["S"],
            created_at=from_millis(item["created_at"]["N"]),
            last_seen_at=from_millis(item["last_seen_at"]["N"]),
        )

    def touch(self, session: Session, now: datetime) -> Session | None:
        """Record activity. None if the session was deleted in the meantime."""
        touched = replace(session, last_seen_at=now)
        try:
            self._client.update_item(
                TableName=self._table,
                Key={"pk": {"S": _session_pk(session.key)}},
                UpdateExpression="SET last_seen_at = :seen, expires_at = :expires",
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeValues={
                    ":seen": {"N": str(epoch_millis(now))},
                    ":expires": {"N": str(epoch_seconds(touched.expires_at))},
                },
            )
        except ClientError as error:
            if is_condition_failure(error):
                return None
            raise
        return touched

    def delete(self, session: Session) -> None:
        self._client.transact_write_items(
            TransactItems=[
                {
                    "Delete": {
                        "TableName": self._table,
                        "Key": {"pk": {"S": _session_pk(session.key)}},
                    }
                },
                {
                    "Update": {
                        "TableName": self._table,
                        "Key": {"pk": {"S": _user_pk(session.user_id)}},
                        # If "sign out everywhere" already removed the set, this recreates only
                        # its key; give it an expiry so TTL still cleans it up.
                        "UpdateExpression": (
                            "DELETE sessions :key SET expires_at = if_not_exists(expires_at, :exp)"
                        ),
                        "ExpressionAttributeValues": {
                            ":key": {"SS": [session.key]},
                            ":exp": {"N": str(epoch_seconds(session.expires_at))},
                        },
                    }
                },
            ]
        )

    def delete_all(self, user_id: UUID) -> int:
        """Delete every session the user has. Returns how many were listed. A session created
        while this runs survives, and stays listed for the next call."""
        response = self._client.get_item(
            TableName=self._table, Key={"pk": {"S": _user_pk(user_id)}}, ConsistentRead=True
        )
        keys = sorted(response.get("Item", {}).get("sessions", {}).get("SS", []))
        for chunk in batched(keys, BATCH_LIMIT, strict=False):
            requests: Sequence[WriteRequestTypeDef | WriteRequestOutputTypeDef] = [
                {"DeleteRequest": {"Key": {"pk": {"S": _session_pk(key)}}}} for key in chunk
            ]
            while requests:
                result = self._client.batch_write_item(RequestItems={self._table: requests})
                requests = result.get("UnprocessedItems", {}).get(self._table, [])
        if keys:
            # Remove only the keys read above: a session created meanwhile stays listed, so the
            # next "sign out everywhere" still finds it.
            self._client.update_item(
                TableName=self._table,
                Key={"pk": {"S": _user_pk(user_id)}},
                UpdateExpression="DELETE sessions :keys",
                ExpressionAttributeValues={":keys": {"SS": keys}},
            )
        return len(keys)
