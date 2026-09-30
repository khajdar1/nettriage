"""Analyzing one upload (spec §4.2, §8.1, §8.2): parse the file as it streams, run every
detector, and collect what the upload row and its findings need. Parsing and detecting are
separate steps so the worker can trace each (`analyze.parse`, `analyze.detect`, spec §9.1)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO
from uuid import UUID

from nettriage.domain.detection.engine import detect
from nettriage.domain.detection.model import Finding
from nettriage.domain.parsing.vpc_flow_logs import (
    ParseLimits,
    ParseResult,
    RejectedSample,
    parse_flow_log,
)

# The only keys the API ever hands out (application/uploads.py `s3_key`): anything else in the
# bucket is ignored (spec §8.6).
_UPLOAD_KEY = re.compile(
    r"orgs/(?P<org>[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})"
    r"/uploads/(?P<upload>[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})/raw"
)


@dataclass(frozen=True)
class UploadKey:
    org_id: UUID
    upload_id: UUID
    key: str


def parse_upload_key(key: str) -> UploadKey | None:
    """The org and upload an S3 key belongs to, or None for a key the API didn't make."""
    match = _UPLOAD_KEY.fullmatch(key)
    if match is None:
        return None
    return UploadKey(org_id=UUID(match["org"]), upload_id=UUID(match["upload"]), key=key)


@dataclass(frozen=True)
class Analysis:
    rows_parsed: int
    rows_rejected: int
    rejected_samples: list[RejectedSample]
    findings: list[Finding]
    findings_truncated: int
    flow_start: datetime | None
    flow_end: datetime | None


def analyze_parsed(parsed: ParseResult) -> Analysis:
    """Run every detector over a parsed file and summarize it for the upload row."""
    detected = detect(parsed.flows)
    return Analysis(
        rows_parsed=parsed.rows_parsed,
        rows_rejected=parsed.rows_rejected,
        rejected_samples=parsed.rejected_samples,
        findings=detected.findings,
        findings_truncated=detected.truncated,
        flow_start=min((flow.start for flow in parsed.flows), default=None),
        flow_end=max((flow.end for flow in parsed.flows), default=None),
    )


def analyze_flow_log(stream: BinaryIO, limits: ParseLimits | None = None) -> Analysis:
    """Parse, then detect. Raises the parser's `FlowLogError` for a file that isn't a flow log
    or breaks a limit; the worker marks the upload failed with its reason."""
    return analyze_parsed(parse_flow_log(stream, limits))
