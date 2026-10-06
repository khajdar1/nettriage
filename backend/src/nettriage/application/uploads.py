"""Upload rules (spec §5.6, §5.7, §7): how big a file may be, where it lives in S3, and how
long its presigned PUT stays valid."""

from __future__ import annotations

import base64
from datetime import timedelta
from typing import Literal, get_args
from uuid import UUID

UploadStatus = Literal["pending_upload", "processing", "analyzed", "failed", "expired"]
UPLOAD_STATUSES: tuple[UploadStatus, ...] = get_args(UploadStatus)

# "25 MB per file" (spec §5.7), binary like the parser's limits: 1 MB = 1,048,576 bytes.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
PRESIGNED_PUT_LIFETIME = timedelta(minutes=5)
MAX_FILENAME = 255
# Why an analysis failed for good, after the worker's last try or the daily cleanup's (Plan 7a).
GAVE_UP = "NetTriage couldn't analyze this file after three tries. Upload it again later."


def s3_key(org_id: UUID, upload_id: UUID) -> str:
    """The object's key (spec §5.6). It's built from IDs only: the file's name never reaches S3."""
    return f"orgs/{org_id}/uploads/{upload_id}/raw"


def checksum_header(sha256_hex: str) -> str:
    """The `x-amz-checksum-sha256` value S3 checks the body against: the digest in base64."""
    return base64.b64encode(bytes.fromhex(sha256_hex)).decode()
