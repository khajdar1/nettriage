"""Run every detector over one upload's flows (spec §8.2, §5 per-upload limits)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from nettriage.domain.detection import bruteforce, outbound_volume, port_scan
from nettriage.domain.detection.model import (
    MAX_FINDINGS_PER_UPLOAD,
    DetectorInfo,
    Finding,
    rank_and_cap,
)
from nettriage.domain.flows import NetworkFlow

DETECTORS: tuple[DetectorInfo, ...] = (port_scan.INFO, bruteforce.INFO, outbound_volume.INFO)


@dataclass(frozen=True)
class DetectionResult:
    """At most 50 findings, highest severity first; `truncated` counts the rest."""

    findings: list[Finding]
    truncated: int


def detect(
    flows: Sequence[NetworkFlow], max_findings: int = MAX_FINDINGS_PER_UPLOAD
) -> DetectionResult:
    candidates = [finding for detector in DETECTORS for finding in detector.run(flows)]
    kept, truncated = rank_and_cap(candidates, max_findings)
    return DetectionResult(findings=kept, truncated=truncated)
