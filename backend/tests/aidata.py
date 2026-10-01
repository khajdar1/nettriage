"""A finding as the triage worker reads it, for AI tests: an external host scanning three ports
of one internal host."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from nettriage.application.ai_input import Candidate, EvidenceRow, TriageSubject

ORG = UUID("01a0ec4d-060a-7266-a427-fce3ccd0d827")
FINDING = UUID("01a0ec4d-374f-740e-85e7-4ac6ee451cd5")
START = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def scan_subject(**changes: Any) -> TriageSubject:
    subject = TriageSubject(
        org_id=ORG,
        finding_id=FINDING,
        detector_id="port_scan",
        detector_version=1,
        detector_name="Port scan",
        severity="medium",
        metrics={"distinct_ports": 3, "reject_ratio": 1.0},
        src_ip="203.0.113.9",
        dst_ip="10.0.0.5",
        dst_port=None,
        protocol=6,
        window_start=START,
        window_end=START + timedelta(minutes=5),
        evidence=tuple(
            EvidenceRow(
                src_ip="203.0.113.9",
                dst_ip="10.0.0.5",
                src_port=40000,
                dst_port=port,
                protocol=6,
                packets=1,
                bytes=40,
                start=START + timedelta(seconds=n),
                end=START + timedelta(seconds=n),
                action="REJECT",
            )
            for n, port in enumerate((22, 80, 443))
        ),
        candidates=(
            Candidate(id="T1595", name="Active Scanning", description="Adversaries may scan."),
            Candidate(
                id="T1595.001", name="Scanning IP Blocks", description="Adversaries may scan IPs."
            ),
        ),
    )
    return replace(subject, **changes)


def good_output(**changes: Any) -> dict[str, Any]:
    """An answer to `scan_subject()` that passes every check."""
    output: dict[str, Any] = {
        "summary": "203.0.113.9 probed port 22, port 80 and port 443 on 10.0.0.5; all rejected.",
        "why_it_matters": "Scans often come before an attempt on whatever answers.",
        "likely_benign_explanations": ["A vulnerability scanner the organization runs."],
        "recommended_next_steps": ["Check whether 203.0.113.9 belongs to a known scanner."],
        "attack_techniques": [{"id": "T1595", "rationale": "Many ports probed from outside."}],
        "severity_assessment": {
            "agrees_with_detector": True,
            "suggested_severity": "medium",
            "reason": "Every probe was rejected.",
        },
        "confidence": "high",
        "insufficient_evidence": False,
    }
    return output | changes
