import json
from collections.abc import Iterator
from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

from nettriage.entrypoints.api.wiring import MissingParameterError, build_services
from nettriage.platform.config import Settings

SETTINGS = Settings(
    stage="dev",
    runtime_table="nettriage-dev-runtime",
    oidc_parameter="/nettriage/dev/api/oidc",
    oidc_secret_parameter="/nettriage/dev/api/oidc-client-secret",  # noqa: S106 - a parameter name
    database_url_parameter="/nettriage/dev/db/app-api-url",
    uploads_bucket="nettriage-dev-uploads-12345678",
    uploads_enabled_parameter="/nettriage/dev/kill/uploads-enabled",
)
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
OIDC = {
    "issuer": "https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_Abc",
    "client_id": "client-1",
    "domain": "https://nettriage-dev-1234.auth.eu-north-1.amazoncognito.com",
    "app_origin": "https://d111111abcdef8.cloudfront.net",
}


@pytest.fixture
def session() -> Iterator[boto3.session.Session]:
    with mock_aws():
        session = boto3.session.Session(region_name="eu-north-1")
        ssm = session.client("ssm")
        ssm.put_parameter(Name=SETTINGS.oidc_parameter, Value=json.dumps(OIDC), Type="String")
        ssm.put_parameter(
            Name=SETTINGS.oidc_secret_parameter, Value="client-secret", Type="SecureString"
        )
        ssm.put_parameter(Name=SETTINGS.uploads_enabled_parameter, Value="true", Type="String")
        yield session


def test_services_are_built_from_ssm(session: boto3.session.Session) -> None:
    session.client("ssm").put_parameter(
        Name=SETTINGS.database_url_parameter,
        Value="postgresql://app_api:pw@ep-x-pooler.eu-central-1.aws.neon.tech/neondb",
        Type="SecureString",
    )

    services = build_services(SETTINGS, session)

    oidc = services.oidc.settings
    assert (oidc.issuer, oidc.client_id, oidc.domain, oidc.app_origin) == tuple(OIDC.values())
    assert oidc.client_secret == "client-secret"  # noqa: S105 - the fake secret put above
    assert services.database.url.host == "ep-x-pooler.eu-central-1.aws.neon.tech"
    assert services.database.pool.size() == 1  # type: ignore[attr-defined]
    assert services.uploads_switch.is_on()
    put = services.upload_storage.presign_put(
        key="orgs/o/uploads/u/raw", size_bytes=1, sha256="0" * 64, traceparent=None, now=NOW
    )
    assert put.url.startswith("https://nettriage-dev-uploads-12345678.s3.eu-north-1.amazonaws.com/")


def test_a_missing_parameter_is_named_without_any_value(session: boto3.session.Session) -> None:
    with pytest.raises(MissingParameterError, match="/nettriage/dev/db/app-api-url") as caught:
        build_services(SETTINGS, session)

    assert "client-secret" not in str(caught.value)
