"""Sign-in state in the DynamoDB `runtime` table (spec §4.1, §5.5): `LOGIN#<state>` holds the
PKCE verifier, the nonce and where to return, for 5 minutes, and can be taken only once."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

from nettriage.adapters.runtime_table import epoch_seconds, is_condition_failure
from nettriage.application.sign_in import LoginState

if TYPE_CHECKING:
    from types_boto3_dynamodb.client import DynamoDBClient

LOGIN_STATE_TTL = timedelta(minutes=5)


class LoginStateStore:
    def __init__(self, client: DynamoDBClient, table: str) -> None:
        self._client = client
        self._table = table

    def put(self, state: str, login: LoginState, now: datetime) -> None:
        self._client.put_item(
            TableName=self._table,
            Item={
                "pk": {"S": f"LOGIN#{state}"},
                "code_verifier": {"S": login.code_verifier},
                "nonce": {"S": login.nonce},
                "return_to": {"S": login.return_to},
                "expires_at": {"N": str(epoch_seconds(now + LOGIN_STATE_TTL))},
            },
            ConditionExpression="attribute_not_exists(pk)",
        )

    def take(self, state: str, now: datetime) -> LoginState | None:
        """Delete and return the state. None if it never existed, was already used, or expired:
        the delete is conditional, so two callbacks with the same state can't both succeed."""
        try:
            response = self._client.delete_item(
                TableName=self._table,
                Key={"pk": {"S": f"LOGIN#{state}"}},
                ConditionExpression="attribute_exists(pk)",
                ReturnValues="ALL_OLD",
            )
        except ClientError as error:
            if is_condition_failure(error):
                return None
            raise
        item = response.get("Attributes", {})
        if int(item["expires_at"]["N"]) <= epoch_seconds(now):
            return None
        return LoginState(
            code_verifier=item["code_verifier"]["S"],
            nonce=item["nonce"]["S"],
            return_to=item["return_to"]["S"],
        )
