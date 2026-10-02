"""The triage queue (spec §4.2): the analyze worker sends one message per finding to explain,
`{"org_id": …, "finding_id": …}`, with the W3C traceparent of its own span as a message
attribute, so the triage worker's span links back to the upload (spec §9.1)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from types_boto3_sqs.client import SQSClient

# SendMessageBatch takes at most 10 messages.
_BATCH = 10


class TriageQueueError(Exception):
    """SQS refused some of the messages. The analyze message fails, and its next delivery queues
    the findings again (explaining a finding twice costs nothing: the analysis is cached)."""


@dataclass(frozen=True)
class TriageQueue:
    client: SQSClient
    url: str

    def send(self, org_id: UUID, finding_ids: Sequence[UUID], traceparent: str | None) -> None:
        attributes: dict[str, Any] = (
            {"traceparent": {"DataType": "String", "StringValue": traceparent}}
            if traceparent
            else {}
        )
        for start in range(0, len(finding_ids), _BATCH):
            batch = finding_ids[start : start + _BATCH]
            entries: list[Any] = [
                {
                    "Id": str(number),
                    "MessageBody": json.dumps(
                        {"org_id": str(org_id), "finding_id": str(finding_id)}
                    ),
                }
                | ({"MessageAttributes": attributes} if attributes else {})
                for number, finding_id in enumerate(batch)
            ]
            response = self.client.send_message_batch(QueueUrl=self.url, Entries=entries)
            if response.get("Failed"):
                raise TriageQueueError(f"{len(response['Failed'])} of {len(batch)} not queued")
