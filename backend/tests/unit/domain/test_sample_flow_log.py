"""The sample flow log runbook B12 uploads (docs/samples/port-scan.log) is analyzed into exactly
the finding the landing page shows: keep the three in step."""

from pathlib import Path

from nettriage.domain.detection.engine import detect
from nettriage.domain.detection.model import Severity
from nettriage.domain.parsing.vpc_flow_logs import parse_flow_log

SAMPLE = Path(__file__).parents[4] / "docs" / "samples" / "port-scan.log"


def test_the_sample_flow_log_is_the_landing_pages_port_scan() -> None:
    with SAMPLE.open("rb") as stream:
        parsed = parse_flow_log(stream)

    [finding] = detect(parsed.flows).findings

    assert (parsed.rows_parsed, parsed.rows_rejected) == (150, 0)
    assert finding.title == "Port scan of 10.0.0.5 from 10.0.3.17: 150 TCP ports in 5 minutes"
    assert finding.severity is Severity.HIGH
    assert finding.candidate_techniques == ("T1046",)
    assert len(finding.evidence) == 50
