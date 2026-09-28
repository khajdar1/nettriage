"""Run the detectors on scenarios and score them against their labels."""

from __future__ import annotations

import io
from collections.abc import Iterable
from dataclasses import dataclass, field

from nettriage.domain.detection.engine import DETECTORS, detect
from nettriage.domain.detection.model import Finding
from nettriage.domain.parsing.vpc_flow_logs import parse_flow_log

from tools.scenarios.traffic import Label, Scenario

TARGET = 0.9


def matches(finding: Finding, label: Label) -> bool:
    return (
        finding.detector_id == label.detector_id
        and str(finding.src_ip) == label.src_ip
        and (label.dst_ip is None or str(finding.dst_ip) == label.dst_ip)
        and (label.dst_port is None or finding.dst_port == label.dst_port)
    )


@dataclass
class Score:
    """One detector's counts. Several findings for one attack are all true positives."""

    true_positives: int = 0
    false_positives: int = 0
    attacks: int = 0
    detected: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def precision(self) -> float:
        findings = self.true_positives + self.false_positives
        return self.true_positives / findings if findings else 1.0

    @property
    def recall(self) -> float:
        return self.detected / self.attacks if self.attacks else 1.0

    @property
    def passed(self) -> bool:
        return self.precision >= TARGET and self.recall >= TARGET


def score(scenarios: Iterable[Scenario]) -> dict[str, Score]:
    scores = {detector.id: Score() for detector in DETECTORS}
    for scenario in scenarios:
        flows = parse_flow_log(io.BytesIO(scenario.text().encode())).flows
        findings = detect(flows).findings
        where = f"{scenario.name} (seed {scenario.seed})"
        for finding in findings:
            detector = scores[finding.detector_id]
            if any(matches(finding, label) for label in scenario.labels):
                detector.true_positives += 1
            else:
                detector.false_positives += 1
                detector.notes.append(f"false positive in {where}: {finding.title}")
        for label in scenario.labels:
            detector = scores[label.detector_id]
            detector.attacks += 1
            if any(matches(finding, label) for finding in findings):
                detector.detected += 1
            else:
                detector.notes.append(f"missed in {where}: {label}")
    return scores


def report(scores: dict[str, Score], scenarios: int, flows: int) -> str:
    lines = [
        "# Detection quality report",
        "",
        f"Generated suite: {scenarios} scenarios, {flows:,} flows. "
        f"Target per detector: precision and recall of at least {TARGET:.2f}.",
        "Synthetic data guards against regressions; it doesn't measure real-world accuracy.",
        "",
        "| Detector | Findings | False positives | Attacks | Detected | Precision | Recall "
        "| Result |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for detector_id, s in scores.items():
        lines.append(
            f"| `{detector_id}` | {s.true_positives + s.false_positives} | {s.false_positives} "
            f"| {s.attacks} | {s.detected} | {s.precision:.2f} | {s.recall:.2f} "
            f"| {'PASS' if s.passed else 'FAIL'} |"
        )
    notes = [note for s in scores.values() for note in s.notes]
    if notes:
        lines += ["", "## False positives and misses", "", *(f"- {note}" for note in notes)]
    return "\n".join(lines) + "\n"
