"""The uploads bucket (spec §5.6, §9.1): a presigned PUT pins the file's size, its SHA-256 and
the trace it belongs to, so S3 accepts exactly the file the member declared.

The browser sends `Content-Length` itself; the other signed headers come back from the API in
`PresignedPut.headers`. S3 refuses a body of another length (the signature no longer matches)
and a body with another SHA-256 (the checksum no longer matches)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config

from nettriage.application.uploads import PRESIGNED_PUT_LIFETIME, checksum_header

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client

# Virtual-hosted, regional URLs (`<bucket>.s3.eu-north-1.amazonaws.com`), the origin the CSP
# allows. Checksums only when required, so the SDK adds no header the browser would have to send.
S3_CONFIG = Config(
    signature_version="s3v4",
    s3={"addressing_style": "virtual"},
    request_checksum_calculation="when_required",
)


@dataclass(frozen=True)
class PresignedPut:
    url: str
    headers: dict[str, str]
    expires_at: datetime


class UploadStorage:
    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def presign_put(
        self, *, key: str, size_bytes: int, sha256: str, traceparent: str | None, now: datetime
    ) -> PresignedPut:
        """A PUT URL valid for 5 minutes, and the headers the browser must send with it."""
        headers = {"x-amz-checksum-sha256": checksum_header(sha256)}
        metadata = {}
        if traceparent is not None:
            headers["x-amz-meta-traceparent"] = traceparent
            metadata["traceparent"] = traceparent
        url = self._client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ContentLength": size_bytes,
                "ChecksumSHA256": headers["x-amz-checksum-sha256"],
                "Metadata": metadata,
            },
            ExpiresIn=int(PRESIGNED_PUT_LIFETIME.total_seconds()),
        )
        return PresignedPut(url=url, headers=headers, expires_at=now + PRESIGNED_PUT_LIFETIME)


def uploads_client(session: boto3.session.Session) -> S3Client:
    return session.client("s3", config=S3_CONFIG)
