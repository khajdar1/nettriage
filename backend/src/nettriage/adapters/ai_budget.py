"""AI budgets (spec §5.5, §6.6) in the DynamoDB `runtime` table: a daily token budget per org,
`BUDGET#<org_id>#<day>`, and a daily spend cap across all orgs, `GBUDGET#<day>` (UTC days).

Before each model call the worker reserves its estimate in both, with one conditional update
each, so parallel calls can't both take the last of a budget. After the call it settles the
reservation to what the call really used; if the call never happened it releases it.

A condition can't add two attributes, so `tokens_reserved` and `usd_reserved` count everything
taken from the budget: open reservations plus settled usage. `tokens_used` and `usd_used` count
the settled usage alone, for reporting.

Budgets fail closed: if DynamoDB can't be read or written, nothing is reserved and no call is
made (`BudgetUnavailable`)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError

from nettriage.adapters.runtime_table import epoch_seconds, is_condition_failure
from nettriage.application.clock import Clock

if TYPE_CHECKING:
    from types_boto3_dynamodb.client import DynamoDBClient

logger = logging.getLogger(__name__)

ORG_DAILY_TOKENS = 100_000
GLOBAL_DAILY_USD = Decimal("0.50")
KEEP_FOR = timedelta(days=2)


class BudgetExhausted(Exception):
    """Today's budget can't cover this call: the org's tokens, or the spend across all orgs."""

    def __init__(self, scope: Literal["org", "global"]) -> None:
        super().__init__(scope)
        self.scope = scope


class BudgetUnavailable(Exception):
    """The budget couldn't be read or written, so no call may be made (fail closed)."""


@dataclass(frozen=True)
class Reservation:
    org_key: str
    global_key: str
    tokens: int
    usd: Decimal


class AiBudget:
    def __init__(
        self,
        client: DynamoDBClient,
        table: str,
        clock: Clock,
        *,
        org_daily_tokens: int = ORG_DAILY_TOKENS,
        global_daily_usd: Decimal = GLOBAL_DAILY_USD,
    ) -> None:
        self._client = client
        self._table = table
        self._clock = clock
        self._org_daily_tokens = org_daily_tokens
        self._global_daily_usd = global_daily_usd

    def reserve(self, org_id: UUID, tokens: int, usd: Decimal) -> Reservation:
        now = self._clock()
        day = now.date().isoformat()
        reservation = Reservation(
            org_key=f"BUDGET#{org_id}#{day}", global_key=f"GBUDGET#{day}", tokens=tokens, usd=usd
        )
        expires = str(epoch_seconds(now + KEEP_FOR))
        self._take(
            reservation.org_key,
            "tokens",
            str(tokens),
            str(self._org_daily_tokens - tokens),
            expires,
            "org",
        )
        try:
            self._take(
                reservation.global_key,
                "usd",
                str(usd),
                str(self._global_daily_usd - usd),
                expires,
                "global",
            )
        except BudgetExhausted, BudgetUnavailable:
            self._add(reservation.org_key, {"tokens_reserved": str(-tokens)})
            raise
        return reservation

    def settle(self, reservation: Reservation, tokens: int, usd: Decimal) -> None:
        """Replace the reservation with what the call really used."""
        self._add(
            reservation.org_key,
            {"tokens_reserved": str(tokens - reservation.tokens), "tokens_used": str(tokens)},
        )
        self._add(
            reservation.global_key,
            {"usd_reserved": str(usd - reservation.usd), "usd_used": str(usd)},
        )

    def release(self, reservation: Reservation) -> None:
        """Give back a reservation whose call never happened."""
        self._add(reservation.org_key, {"tokens_reserved": str(-reservation.tokens)})
        self._add(reservation.global_key, {"usd_reserved": str(-reservation.usd)})

    def _take(
        self,
        key: str,
        unit: str,
        amount: str,
        room: str,
        expires: str,
        scope: Literal["org", "global"],
    ) -> None:
        if Decimal(room) < 0:
            raise BudgetExhausted(scope)
        try:
            self._client.update_item(
                TableName=self._table,
                Key={"pk": {"S": key}},
                UpdateExpression=(
                    f"SET {unit}_reserved = if_not_exists({unit}_reserved, :zero) + :amount, "
                    f"{unit}_used = if_not_exists({unit}_used, :zero), expires_at = :expires"
                ),
                ConditionExpression=(
                    f"attribute_not_exists({unit}_reserved) OR {unit}_reserved <= :room"
                ),
                ExpressionAttributeValues={
                    ":zero": {"N": "0"},
                    ":amount": {"N": amount},
                    ":room": {"N": room},
                    ":expires": {"N": expires},
                },
            )
        except ClientError as error:
            if is_condition_failure(error):
                raise BudgetExhausted(scope) from None
            logger.warning("ai_budget_unavailable", extra={"error_code": "dynamodb_error"})
            raise BudgetUnavailable from None
        except BotoCoreError:
            logger.warning("ai_budget_unavailable", extra={"error_code": "dynamodb_error"})
            raise BudgetUnavailable from None

    def _add(self, key: str, amounts: dict[str, str]) -> None:
        """Best effort: a failed settlement or release leaves the budget counting too much,
        never too little."""
        names = list(amounts)
        additions = ", ".join(f"{name} :v{n}" for n, name in enumerate(names))
        try:
            self._client.update_item(
                TableName=self._table,
                Key={"pk": {"S": key}},
                UpdateExpression=f"ADD {additions}",
                ExpressionAttributeValues={
                    f":v{n}": {"N": amounts[name]} for n, name in enumerate(names)
                },
            )
        except ClientError, BotoCoreError:
            logger.warning("ai_budget_settle_failed", extra={"error_code": "dynamodb_error"})
