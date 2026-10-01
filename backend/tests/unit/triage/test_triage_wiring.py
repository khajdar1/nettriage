"""Building the triage worker in Lambda (spec §6.8, §8.3, §9.7): its own database URL from SSM,
Bedrock in the model's Region, the runtime table for budgets, and the `ai_enabled` switch."""

from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from nettriage.adapters.bedrock_llm import BedrockProvider
from nettriage.adapters.parameters import MissingParameterError
from nettriage.entrypoints.triage import handler
from nettriage.entrypoints.triage.explainer import Explainer
from nettriage.entrypoints.triage.wiring import build_worker
from nettriage.platform.config import Settings

SETTINGS = Settings(
    stage="dev",
    service_name="nettriage-triage",
    database_url_parameter="/nettriage/dev/db/app-triage-url",
    runtime_table="nettriage-dev-runtime",
    ai_enabled_parameter="/nettriage/dev/kill/ai-enabled",
    bedrock_region="eu-north-1",
    bedrock_model_id="openai.gpt-oss-20b-1:0",
)


@pytest.fixture
def session() -> Iterator[boto3.session.Session]:
    with mock_aws():
        yield boto3.session.Session(region_name="eu-north-1")


def store_database_url(session: boto3.session.Session) -> None:
    session.client("ssm").put_parameter(
        Name="/nettriage/dev/db/app-triage-url",
        Value="postgresql://app_triage:pw@ep-x-pooler.eu-central-1.aws.neon.tech/neondb",
        Type="SecureString",
    )


def test_the_worker_explains_with_bedrock_as_app_triage(session: boto3.session.Session) -> None:
    store_database_url(session)

    worker = build_worker(SETTINGS, TracerProvider(), MeterProvider(), session)

    explainer = worker.explainer
    assert isinstance(explainer, Explainer)
    assert explainer.database.url.username == "app_triage"
    assert isinstance(explainer.provider, BedrockProvider)
    assert explainer.provider.model_id == "openai.gpt-oss-20b-1:0"
    assert explainer.provider.client.meta.region_name == "eu-north-1"
    worker.flush()


@pytest.mark.parametrize(("value", "on"), [("true", True), ("false", False), (None, False)])
def test_the_ai_switch_is_read_from_ssm(
    session: boto3.session.Session, value: str | None, on: bool
) -> None:
    store_database_url(session)
    if value is not None:
        session.client("ssm").put_parameter(
            Name="/nettriage/dev/kill/ai-enabled", Value=value, Type="String"
        )

    worker = build_worker(SETTINGS, TracerProvider(), MeterProvider(), session)

    assert isinstance(worker.explainer, Explainer)
    assert worker.explainer.enabled() is on


def test_a_missing_database_url_is_named(session: boto3.session.Session) -> None:
    with pytest.raises(MissingParameterError, match="/nettriage/dev/db/app-triage-url"):
        build_worker(SETTINGS, TracerProvider(), MeterProvider(), session)


class _Worker:
    def __init__(self) -> None:
        self.flushed = False

    def handle_batch(self, records: list[dict[str, Any]]) -> list[str]:
        return [record["messageId"] for record in records if record["body"] == "fail"]

    def flush(self) -> None:
        self.flushed = True


def test_the_handler_reports_the_messages_to_deliver_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Worker()
    monkeypatch.setattr(handler, "worker", lambda: fake)
    event = {"Records": [{"messageId": "a", "body": "ok"}, {"messageId": "b", "body": "fail"}]}

    response = handler.handle(event, object())

    assert response == {"batchItemFailures": [{"itemIdentifier": "b"}]}
    assert fake.flushed
