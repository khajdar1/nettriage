"""Presigned PUTs into the uploads bucket (spec §5.6, §9.1)."""

import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import boto3

from nettriage.adapters.upload_storage import UploadStorage, uploads_client
from nettriage.application.uploads import checksum_header

BUCKET = "nettriage-test-uploads-12345678"
KEY = "orgs/01a0ec4d-060a-7266-a427-fce3ccd0d827/uploads/01a0ec4d-374f-740e-85e7-4ac6ee451cd5/raw"
SHA256 = hashlib.sha256(b"flows").hexdigest()
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def storage() -> UploadStorage:
    session = boto3.session.Session(
        aws_access_key_id="AKIDEXAMPLE",
        aws_secret_access_key="not-a-secret",  # noqa: S106 - presigning is offline
        region_name="eu-north-1",
    )
    return UploadStorage(uploads_client(session), BUCKET)


def signed(url: str) -> tuple[str | None, str, dict[str, str]]:
    parts = urlsplit(url)
    return parts.hostname, parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}


def test_a_presigned_put_pins_the_files_size_checksum_and_trace() -> None:
    put = storage().presign_put(
        key=KEY, size_bytes=5, sha256=SHA256, traceparent=TRACEPARENT, now=NOW
    )

    host, path, query = signed(put.url)
    assert (host, path) == (f"{BUCKET}.s3.eu-north-1.amazonaws.com", f"/{KEY}")
    assert query["X-Amz-SignedHeaders"] == (
        "content-length;host;x-amz-checksum-sha256;x-amz-meta-traceparent"
    )
    assert query["X-Amz-Expires"] == "300"
    assert put.headers == {
        "x-amz-checksum-sha256": checksum_header(SHA256),
        "x-amz-meta-traceparent": TRACEPARENT,
    }
    assert put.expires_at == NOW + timedelta(minutes=5)


def test_without_a_trace_the_browser_sends_only_the_checksum() -> None:
    put = storage().presign_put(key=KEY, size_bytes=5, sha256=SHA256, traceparent=None, now=NOW)

    _, _, query = signed(put.url)
    assert query["X-Amz-SignedHeaders"] == "content-length;host;x-amz-checksum-sha256"
    assert put.headers == {"x-amz-checksum-sha256": checksum_header(SHA256)}
