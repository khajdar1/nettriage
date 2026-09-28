import gzip
import io
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path

import pytest
from nettriage.domain.detection.model import Finding, Severity
from nettriage.domain.parsing.vpc_flow_logs import parse_flow_log

from tools.scenarios.__main__ import main
from tools.scenarios.evaluate import Score, matches, report
from tools.scenarios.traffic import Label

NOON = datetime(2026, 9, 1, 12, tzinfo=UTC)


def finding(detector_id: str, src: str, dst: str | None = None, port: int | None = None) -> Finding:
    return Finding(
        detector_id=detector_id,
        detector_version=1,
        fingerprint="f",
        severity=Severity.MEDIUM,
        title="t",
        src_ip=ip_address(src),
        dst_ip=ip_address(dst) if dst else None,
        dst_port=port,
        protocol=6,
        window_start=NOON,
        window_end=NOON,
        metrics={},
        candidate_techniques=(),
        evidence=(),
    )


def test_a_label_matches_on_detector_source_and_the_fields_it_sets() -> None:
    label = Label("port_scan", "1.2.3.4", dst_port=445)

    assert matches(finding("port_scan", "1.2.3.4", port=445), label)
    assert matches(finding("port_scan", "1.2.3.4", dst="10.0.0.1", port=445), label)
    assert not matches(finding("port_scan", "1.2.3.4", port=22), label)
    assert not matches(finding("port_scan", "1.2.3.5", port=445), label)
    assert not matches(finding("outbound_volume", "1.2.3.4", port=445), label)


def test_scores_turn_counts_into_precision_and_recall() -> None:
    score = Score(true_positives=9, false_positives=1, attacks=10, detected=9)

    assert score.precision == pytest.approx(0.9)
    assert score.recall == pytest.approx(0.9)
    assert score.passed
    assert not Score(true_positives=8, false_positives=2, attacks=10, detected=10).passed
    assert Score().precision == Score().recall == 1.0


def test_the_report_lists_every_detector_and_every_note() -> None:
    scores = {
        "port_scan": Score(true_positives=3, attacks=3, detected=3),
        "outbound_volume": Score(false_positives=1, notes=["false positive in x (seed 1): y"]),
    }

    text = report(scores, scenarios=2, flows=1_234)

    assert "2 scenarios, 1,234 flows" in text
    assert "| `port_scan` | 3 | 0 | 3 | 3 | 1.00 | 1.00 | PASS |" in text
    assert "| `outbound_volume` | 1 | 1 | 0 | 0 | 0.00 | 1.00 | FAIL |" in text
    assert "- false positive in x (seed 1): y" in text


def test_report_command_scores_the_suite_and_writes_the_file(tmp_path: Path) -> None:
    out = tmp_path / "report.md"

    assert main(["report", "--seeds", "1", "--out", str(out)]) == 0
    assert "| `remote_access_bruteforce` |" in out.read_text(encoding="utf-8")


def test_write_command_writes_a_gzip_flow_log(tmp_path: Path) -> None:
    out = tmp_path / "scan.log.gz"

    assert (
        main(["write", "external_vertical_scan", "--seed", "2", "--out", str(out), "--gzip"]) == 0
    )
    assert parse_flow_log(io.BytesIO(out.read_bytes())).rows_parsed > 1_000
    assert gzip.decompress(out.read_bytes()).decode().startswith("version account-id")
