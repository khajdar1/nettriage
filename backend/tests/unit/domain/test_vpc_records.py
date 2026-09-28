from datetime import UTC, datetime
from ipaddress import ip_address

import pytest

from nettriage.domain.flows import FlowSource
from nettriage.domain.parsing.vpc_records import (
    DEFAULT_V2_FIELDS,
    Layout,
    RecordError,
    SkippedRecord,
    layout_for,
    parse_record,
)

V2 = Layout(DEFAULT_V2_FIELDS, has_header=False)
GOOD = (
    "2 123456789012 eni-0a1b2c3d 10.0.1.5 203.0.113.9 49152 443 6 12 3400 "
    "1790000000 1790000060 ACCEPT OK"
)


def test_a_default_v2_record_becomes_a_network_flow() -> None:
    flow = parse_record(GOOD, V2, line_no=7)

    assert flow.src_ip == ip_address("10.0.1.5")
    assert flow.dst_ip == ip_address("203.0.113.9")
    assert (flow.src_port, flow.dst_port, flow.protocol) == (49152, 443, 6)
    assert (flow.packets, flow.bytes) == (12, 3400)
    assert flow.start == datetime(2026, 9, 21, 14, 13, 20, tzinfo=UTC)
    assert flow.end == datetime(2026, 9, 21, 14, 14, 20, tzinfo=UTC)
    assert flow.action == "ACCEPT"
    assert flow.source == FlowSource(provider="aws_vpc", interface_id="eni-0a1b2c3d")
    assert flow.line_no == 7
    assert flow.direction is None
    assert flow.tcp_flags is None


def test_a_first_line_of_field_names_is_a_header_in_any_order_with_unknown_fields() -> None:
    layout = layout_for(
        "version vpc-id srcaddr dstaddr srcport dstport protocol packets bytes start end "
        "action tcp-flags flow-direction traffic-path log-status"
    )

    flow = parse_record(
        "5 vpc-0abc 2001:db8::5 fd00::9 51000 22 6 4 240 1790000000 1790000001 REJECT 2 "
        "ingress 1 OK",
        layout,
        line_no=2,
    )

    assert layout.has_header
    assert flow.src_ip == ip_address("2001:db8::5")
    assert flow.dst_ip == ip_address("fd00::9")
    assert flow.source.vpc_id == "vpc-0abc"
    assert flow.source.interface_id is None
    assert flow.direction == "ingress"
    assert flow.tcp_flags == 2


def test_a_record_first_line_means_the_default_v2_format() -> None:
    assert layout_for(GOOD) == V2


def test_a_header_missing_required_fields_is_refused() -> None:
    with pytest.raises(RecordError, match="header lacks required fields: action, bytes"):
        layout_for("srcaddr dstaddr srcport dstport protocol packets start end")


def test_packet_level_addresses_replace_the_interface_addresses() -> None:
    layout = layout_for(
        "srcaddr dstaddr pkt-srcaddr pkt-dstaddr srcport dstport protocol packets bytes "
        "start end action"
    )

    flow = parse_record(
        "10.0.9.9 52.1.2.3 10.0.1.5 - 40000 443 6 3 120 1790000000 1790000002 ACCEPT",
        layout,
        line_no=2,
    )

    assert flow.src_ip == ip_address("10.0.1.5")
    assert flow.dst_ip == ip_address("52.1.2.3")


@pytest.mark.parametrize("status", ["NODATA", "SKIPDATA"])
def test_nodata_and_skipdata_records_are_skipped(status: str) -> None:
    line = f"2 123456789012 eni-0a1b2c3d - - - - - - - 1790000000 1790000060 - {status}"

    with pytest.raises(SkippedRecord):
        parse_record(line, V2, line_no=3)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({3: "10.0.1.999"}, "bad_address"),
        ({4: "-"}, "missing_field"),
        ({5: "65536"}, "bad_port"),
        ({6: "-1"}, "bad_port"),
        ({7: "256"}, "bad_protocol"),
        ({8: "+12"}, "bad_count"),
        ({9: "1_000"}, "bad_count"),
        ({8: "٣"}, "bad_count"),
        ({10: "soon"}, "bad_time"),
        ({10: "99999999999999"}, "bad_time"),
        ({10: "1790000061"}, "start_after_end"),
        ({12: "accept"}, "bad_action"),
    ],
)
def test_invalid_records_are_refused_with_a_reason(change: dict[int, str], reason: str) -> None:
    values = GOOD.split()
    for index, replacement in change.items():
        values[index] = replacement

    with pytest.raises(RecordError) as caught:
        parse_record(" ".join(values), V2, line_no=4)

    assert caught.value.reason == reason


def test_a_record_with_the_wrong_number_of_fields_is_refused() -> None:
    with pytest.raises(RecordError, match="field_count"):
        parse_record(GOOD + " extra", V2, line_no=5)


def test_optional_fields_that_do_not_parse_count_as_missing() -> None:
    layout = layout_for(
        "srcaddr dstaddr srcport dstport protocol packets bytes start end action "
        "tcp-flags flow-direction pkt-srcaddr"
    )

    flow = parse_record(
        "10.0.1.5 52.1.2.3 40000 443 6 3 120 1790000000 1790000002 ACCEPT x sideways nope",
        layout,
        line_no=2,
    )

    assert flow.tcp_flags is None
    assert flow.direction is None
    assert flow.src_ip == ip_address("10.0.1.5")
