"""Analyzing one upload (spec §4.2, §8.1, §8.2): which keys belong to uploads, and what a file
yields."""

import gzip
import io
import tracemalloc
from datetime import UTC, datetime
from uuid import UUID

import pytest
from flowlogs import busy_network, port_scan, quiet

from nettriage.application.analysis import analyze_flow_log, parse_upload_key
from nettriage.domain.parsing.vpc_flow_logs import FlowLogError, ParseLimits

ORG = UUID("01a0ec4d-060a-7266-a427-fce3ccd0d827")
UPLOAD = UUID("01a0ec4d-374f-740e-85e7-4ac6ee451cd5")


def test_a_key_the_api_made_names_its_org_and_upload() -> None:
    key = f"orgs/{ORG}/uploads/{UPLOAD}/raw"

    parsed = parse_upload_key(key)

    assert parsed is not None
    assert (parsed.org_id, parsed.upload_id, parsed.key) == (ORG, UPLOAD, key)


@pytest.mark.parametrize(
    "key",
    [
        f"orgs/{ORG}/uploads/{UPLOAD}/raw.gz",
        f"orgs/{ORG}/uploads/{UPLOAD}",
        f"orgs/{str(ORG).upper()}/uploads/{UPLOAD}/raw",
        f"orgs/{ORG}/uploads/{UPLOAD}/raw/../../other",
        f"prefix/orgs/{ORG}/uploads/{UPLOAD}/raw",
        "orgs/not-a-uuid/uploads/also-not/raw",
        "",
    ],
)
def test_any_other_key_is_not_an_upload(key: str) -> None:
    assert parse_upload_key(key) is None


def test_a_port_scan_yields_one_finding_and_the_files_statistics() -> None:
    analysis = analyze_flow_log(io.BytesIO(port_scan()))

    assert (analysis.rows_parsed, analysis.rows_rejected, analysis.rejected_samples) == (150, 0, [])
    [finding] = analysis.findings
    assert finding.detector_id == "port_scan"
    assert str(finding.src_ip) == "203.0.113.9"
    assert analysis.findings_truncated == 0
    assert analysis.flow_start == datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
    assert analysis.flow_end == datetime(2026, 9, 28, 12, 0, 59, tzinfo=UTC)


def test_a_gzipped_file_is_read_the_same() -> None:
    analysis = analyze_flow_log(io.BytesIO(gzip.compress(port_scan())))

    assert (analysis.rows_parsed, len(analysis.findings)) == (150, 1)


def test_an_interface_with_no_traffic_yields_no_findings_and_no_time_range() -> None:
    analysis = analyze_flow_log(io.BytesIO(quiet()))

    assert (analysis.rows_parsed, analysis.findings) == (0, [])
    assert (analysis.flow_start, analysis.flow_end) == (None, None)


def test_a_file_that_is_not_a_flow_log_fails_with_a_reason() -> None:
    with pytest.raises(FlowLogError) as failed:
        analyze_flow_log(io.BytesIO(b"<html>\n" * 50))

    assert failed.value.code == "not_a_flow_log"


def test_a_file_that_inflates_past_the_limit_fails_early() -> None:
    bomb = gzip.compress(port_scan() * 1_000)  # about 11 MB of flows, 40 KB compressed

    with pytest.raises(FlowLogError) as failed:
        analyze_flow_log(io.BytesIO(bomb), ParseLimits(max_decompressed_bytes=1_000_000))

    assert failed.value.code == "limit_exceeded"


# Of the worker's 2,048 MB (spec §3.5), the Python runtime, the libraries and the telemetry
# collector take about 300 MB. Parsing and detection get 1,280 MB, leaving room for files more
# varied than this one: a 2,000,000-row file measured about 30% more per row than it does.
ANALYSIS_MEMORY_BUDGET = 1280 * 1024 * 1024


def test_a_file_at_the_row_limit_fits_the_workers_memory() -> None:
    rows = 20_000
    data = busy_network(rows)

    tracemalloc.start()
    try:
        analyze_flow_log(io.BytesIO(data))
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert peak / rows * ParseLimits().max_rows < ANALYSIS_MEMORY_BUDGET
