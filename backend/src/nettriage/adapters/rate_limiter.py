"""GCRA rate limits in the DynamoDB `runtime` table (spec §5.5, §6.5).

Each check is one or two conditional updates of `RL#<policy>#<subject>`, never a read followed
by a write, so parallel requests can't both take the last slot:

1. A new or idle key (no `tat`, or `tat` <= now) is set to now + T.
2. Otherwise `tat` grows by T, on condition that tat - now <= tau.

A failed condition returns the item as it was, which tells us whether the request is over the
limit. DynamoDB errors fail open: the request is allowed and the decision is marked `degraded`.

The table's capacity is small (10 writes a second), and a failed conditional update costs a
write too. So each process remembers the last `tat` it saw per key. `tat` never decreases, so:
- a key known to be over its limit is refused locally, without a write, until it may retry;
- an active key starts with update 2, and a new or idle one with update 1.
The audit-log sample is remembered the same way.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from botocore.exceptions import BotoCoreError, ClientError

from nettriage.adapters.runtime_table import epoch_millis, epoch_seconds, is_condition_failure
from nettriage.application.clock import Clock
from nettriage.application.rate_limits import Decision, Policy, allowed, failed_open, limited

if TYPE_CHECKING:
    from types_boto3_dynamodb.client import DynamoDBClient
    from types_boto3_dynamodb.type_defs import AttributeValueTypeDef

logger = logging.getLogger(__name__)

ATTEMPTS = 3
AUDIT_SAMPLE_WINDOW = timedelta(minutes=1)
# Each process remembers at most this many keys; beyond it, it starts again from nothing.
MAX_REMEMBERED = 10_000


class RateLimiter:
    def __init__(self, client: DynamoDBClient, table: str, clock: Clock) -> None:
        self._client = client
        self._table = table
        self._clock = clock
        self._known_tat: dict[str, int] = {}
        self._audited_until: dict[str, int] = {}

    def check(self, policy: Policy, subject: str) -> Decision:
        now_moment = self._clock()
        now = epoch_millis(now_moment)
        name = f"RL#{policy.name}#{subject}"
        known = self._known_tat.get(name)
        if known is not None and known - now > policy.tolerance_ms:
            return limited(policy, known, now)  # still over the limit: tat never decreases
        key: dict[str, AttributeValueTypeDef] = {"pk": {"S": name}}
        expires: AttributeValueTypeDef = {"N": str(epoch_seconds(now_moment + 2 * policy.period))}
        active = known is not None and known > now
        try:
            for _ in range(ATTEMPTS):
                if not active:
                    fresh = now + policy.interval_ms
                    succeeded, tat = self._update(
                        key,
                        "SET tat = :fresh, expires_at = :expires",
                        "attribute_not_exists(tat) OR tat <= :now",
                        {":fresh": {"N": str(fresh)}, ":now": {"N": str(now)}, ":expires": expires},
                    )
                    if succeeded:
                        return self._remember(name, fresh, allowed(policy, fresh, now))
                    if tat is not None and tat - now > policy.tolerance_ms:
                        return self._remember(name, tat, limited(policy, tat, now))
                active = False
                succeeded, tat = self._update(
                    key,
                    "SET tat = tat + :interval, expires_at = :expires",
                    "tat > :now AND tat <= :latest",
                    {
                        ":interval": {"N": str(policy.interval_ms)},
                        ":now": {"N": str(now)},
                        ":latest": {"N": str(now + policy.tolerance_ms)},
                        ":expires": expires,
                    },
                )
                if succeeded and tat is not None:
                    return self._remember(name, tat, allowed(policy, tat, now))
                if tat is not None and tat - now > policy.tolerance_ms:
                    return self._remember(name, tat, limited(policy, tat, now))
                # No tat, or tat <= now: the key was new or idle after all; start with update 1.
        except BotoCoreError, ClientError:
            logger.warning("rate_limit_failed_open", extra={"policy": policy.name})
            return failed_open(policy)
        logger.warning("rate_limit_undecided", extra={"policy": policy.name})
        return failed_open(policy)

    def should_audit(self, policy: Policy, subject: str) -> bool:
        """True at most once per subject and policy per minute: the audit log samples
        `ratelimit.limited` (spec §9.4)."""
        now = self._clock()
        name = f"RLAUDIT#{policy.name}#{subject}"
        if epoch_millis(now) < self._audited_until.get(name, 0):
            return False
        _bounded(self._audited_until)[name] = epoch_millis(now + AUDIT_SAMPLE_WINDOW)
        try:
            self._client.put_item(
                TableName=self._table,
                Item={
                    "pk": {"S": name},
                    "expires_at": {"N": str(epoch_seconds(now + AUDIT_SAMPLE_WINDOW))},
                },
                ConditionExpression="attribute_not_exists(pk) OR expires_at <= :now",
                ExpressionAttributeValues={":now": {"N": str(epoch_seconds(now))}},
            )
        except ClientError as error:
            if is_condition_failure(error):
                return False
            logger.warning("rate_limit_audit_sample_failed", extra={"policy": policy.name})
            return False
        except BotoCoreError:
            logger.warning("rate_limit_audit_sample_failed", extra={"policy": policy.name})
            return False
        return True

    def _remember(self, name: str, tat: int, decision: Decision) -> Decision:
        _bounded(self._known_tat)[name] = tat
        return decision

    def _update(
        self,
        key: dict[str, AttributeValueTypeDef],
        update: str,
        condition: str,
        values: dict[str, AttributeValueTypeDef],
    ) -> tuple[bool, int | None]:
        """(True, new tat) on success; (False, current tat) on a failed condition, where the
        current tat is None if the item has none."""
        try:
            response = self._client.update_item(
                TableName=self._table,
                Key=key,
                UpdateExpression=update,
                ConditionExpression=condition,
                ExpressionAttributeValues=values,
                ReturnValues="UPDATED_NEW",
                ReturnValuesOnConditionCheckFailure="ALL_OLD",
            )
        except ClientError as error:
            if not is_condition_failure(error):
                raise
            item: dict[str, Any] | None = error.response.get("Item")  # type: ignore[assignment]
            if item is None:
                item = self._client.get_item(
                    TableName=self._table, Key=key, ConsistentRead=True
                ).get("Item")
            return False, _tat(item)
        return True, _tat(response.get("Attributes"))


def _bounded(memory: dict[str, int]) -> dict[str, int]:
    if len(memory) >= MAX_REMEMBERED:
        memory.clear()
    return memory


def _tat(item: dict[str, Any] | None) -> int | None:
    if not item or "tat" not in item:
        return None
    return int(item["tat"]["N"])
