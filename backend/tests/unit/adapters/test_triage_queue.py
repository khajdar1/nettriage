"""The triage queue (spec §4.2): one message per finding to explain, sent ten at a time, with
the trace it continues."""

import json
from collections.abc import Iterator
from typing import Any
from uuid import uuid7

import boto3
import pytest
from botocore.stub import Stubber
from moto import mock_aws

from nettriage.adapters.triage_queue import TriageQueue, TriageQueueError

TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


@pytest.fixture
def sqs() -> Iterator[Any]:
    with mock_aws():
        yield boto3.client("sqs", region_name="eu-north-1")


def received(sqs: Any, url: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    while batch := sqs.receive_message(
        QueueUrl=url, MaxNumberOfMessages=10, MessageAttributeNames=["All"]
    ).get("Messages"):
        messages += batch
        for message in batch:
            sqs.delete_message(QueueUrl=url, ReceiptHandle=message["ReceiptHandle"])
    return messages


def test_each_finding_becomes_one_message_with_the_trace(sqs: Any) -> None:
    url = sqs.create_queue(QueueName="nettriage-test-triage")["QueueUrl"]
    org, findings = uuid7(), [uuid7() for _ in range(23)]

    TriageQueue(sqs, url).send(org, findings, TRACEPARENT)

    messages = received(sqs, url)
    assert sorted(json.loads(message["Body"])["finding_id"] for message in messages) == sorted(
        str(finding) for finding in findings
    )
    assert {json.loads(message["Body"])["org_id"] for message in messages} == {str(org)}
    assert {message["MessageAttributes"]["traceparent"]["StringValue"] for message in messages} == {
        TRACEPARENT
    }


def test_without_a_trace_the_message_carries_no_attributes(sqs: Any) -> None:
    url = sqs.create_queue(QueueName="nettriage-test-triage")["QueueUrl"]

    TriageQueue(sqs, url).send(uuid7(), [uuid7()], None)

    [message] = received(sqs, url)
    assert "MessageAttributes" not in message


def test_nothing_to_explain_sends_nothing() -> None:
    client = boto3.client("sqs", region_name="eu-north-1")
    with Stubber(client):
        TriageQueue(client, "https://sqs.eu-north-1.amazonaws.com/1/q").send(uuid7(), [], None)


def test_a_message_sqs_refuses_fails_the_send() -> None:
    client = boto3.client("sqs", region_name="eu-north-1")
    url = "https://sqs.eu-north-1.amazonaws.com/123456789012/nettriage-test-triage"
    with Stubber(client) as stubber:
        stubber.add_response(
            "send_message_batch",
            {
                "Successful": [],
                "Failed": [{"Id": "0", "SenderFault": False, "Code": "InternalError"}],
            },
        )
        with pytest.raises(TriageQueueError, match="1 of 1"):
            TriageQueue(client, url).send(uuid7(), [uuid7()], None)
