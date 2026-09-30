"""Reading an uploaded file from the uploads bucket (spec §4.2, §9.1): its body as a stream, its
size, and the trace the upload request belonged to."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO, cast

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client


@dataclass(frozen=True)
class UploadObject:
    body: BinaryIO
    size: int
    traceparent: str | None


class UploadObjects:
    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def open(self, key: str) -> UploadObject:
        """The object's body streams: nothing is read until the parser asks for it."""
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        return UploadObject(
            body=cast(BinaryIO, response["Body"]),
            size=response["ContentLength"],
            traceparent=response.get("Metadata", {}).get("traceparent"),
        )
