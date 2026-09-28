import gzip
import io
import zlib

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nettriage.domain.parsing.vpc_flow_logs import (
    FlowLogError,
    ParseLimits,
    ParseResult,
    parse_flow_log,
)

HEADER = (
    "version account-id interface-id srcaddr dstaddr srcport dstport protocol packets bytes "
    "start end action log-status"
)


def record(n: int, action: str = "ACCEPT") -> str:
    return (
        f"2 123456789012 eni-0a1b2c3d 10.0.1.{n % 250 + 1} 203.0.113.9 {40000 + n} 443 6 "
        f"12 3400 1790000000 1790000060 {action} OK"
    )


def parse(text: str | bytes, limits: ParseLimits | None = None) -> ParseResult:
    data = text.encode() if isinstance(text, str) else text
    return parse_flow_log(io.BytesIO(data), limits)


def test_plain_text_with_a_header_parses_every_record() -> None:
    result = parse("\n".join([HEADER, record(1), record(2), record(3)]) + "\n")

    assert result.has_header
    assert result.rows_parsed == 3
    assert [flow.line_no for flow in result.flows] == [2, 3, 4]
    assert result.rows_rejected == result.rows_skipped == 0


def test_gzip_is_detected_from_its_magic_bytes() -> None:
    data = gzip.compress(("\n".join([record(1), record(2)]) + "\n").encode())

    result = parse(data)

    assert not result.has_header
    assert result.rows_parsed == 2


def test_concatenated_gzip_members_are_all_read() -> None:
    data = gzip.compress((record(1) + "\n").encode()) + gzip.compress((record(2) + "\n").encode())

    assert parse(data).rows_parsed == 2


def test_windows_line_endings_blank_lines_and_no_final_newline_are_fine() -> None:
    text = f"{HEADER}\r\n\r\n{record(1)}\r\n   \r\n{record(2)}"

    result = parse(text)

    assert result.rows_parsed == 2
    assert [flow.line_no for flow in result.flows] == [3, 5]


def test_nodata_and_skipdata_records_are_counted_not_parsed() -> None:
    nodata = "2 123456789012 eni-0a1b2c3d - - - - - - - 1790000000 1790000060 - NODATA"
    skipdata = "2 123456789012 eni-0a1b2c3d - - - - - - - 1790000000 1790000060 - SKIPDATA"

    result = parse("\n".join([record(1), nodata, skipdata]))

    assert (result.rows_parsed, result.rows_skipped, result.rows_rejected) == (1, 2, 0)


def test_a_file_of_only_nodata_records_is_a_valid_empty_log() -> None:
    nodata = "2 123456789012 eni-0a1b2c3d - - - - - - - 1790000000 1790000060 - NODATA"

    result = parse(nodata)

    assert result.flows == []
    assert result.rows_skipped == 1


def test_up_to_five_percent_invalid_records_are_rejected_and_sampled() -> None:
    lines = [record(n) for n in range(95)] + ["not a record"] * 5

    result = parse("\n".join(lines))

    assert (result.rows_parsed, result.rows_rejected) == (95, 5)
    assert [sample.line_no for sample in result.rejected_samples] == [96, 97, 98, 99, 100]
    assert result.rejected_samples[0].reason == "field_count"
    assert result.rejected_samples[0].text == "not a record"


def test_more_than_five_percent_invalid_records_is_not_a_flow_log() -> None:
    lines = [record(n) for n in range(94)] + ["not a record"] * 6

    with pytest.raises(FlowLogError) as caught:
        parse("\n".join(lines))

    assert caught.value.code == "not_a_flow_log"
    assert "6 of 100 records" in caught.value.detail


def test_at_most_twenty_rejected_samples_of_at_most_120_characters_are_kept() -> None:
    lines = [record(n) for n in range(1000)] + ["x" * 500] * 40

    result = parse("\n".join(lines))

    assert result.rows_rejected == 40
    assert len(result.rejected_samples) == 20
    assert all(len(sample.text) == 120 for sample in result.rejected_samples)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"\n\n  \n",
        HEADER.encode() + b"\n",
        b"name,email,amount\nalice,a@example.com,10\nbob,b@example.com,20\n",
        b'{"flows": [{"src": "10.0.0.1"}]}',
        b"hello world\n",
        b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 64,
    ],
    ids=["empty", "blank", "header-only", "csv", "json", "prose", "png"],
)
def test_files_that_are_not_flow_logs_say_so(content: bytes) -> None:
    with pytest.raises(FlowLogError) as caught:
        parse(content)

    assert caught.value.code == "not_a_flow_log"
    assert caught.value.detail


def test_corrupt_gzip_is_not_a_flow_log() -> None:
    data = gzip.compress((record(1) + "\n").encode())

    with pytest.raises(FlowLogError) as caught:
        parse(data[:-8] + b"garbage!")

    assert caught.value.code == "not_a_flow_log"


def test_a_too_long_line_after_valid_records_exceeds_the_limit() -> None:
    limits = ParseLimits(max_line_bytes=200)

    with pytest.raises(FlowLogError) as caught:
        parse(record(1) + "\n" + "9" * 201 + "\n", limits)

    assert caught.value.code == "limit_exceeded"


def test_too_many_rows_exceed_the_limit() -> None:
    limits = ParseLimits(max_rows=3)

    with pytest.raises(FlowLogError) as caught:
        parse("\n".join(record(n) for n in range(4)), limits)

    assert caught.value.code == "limit_exceeded"


def test_a_gzip_bomb_stops_at_the_decompressed_size_limit() -> None:
    compressor = zlib.compressobj(wbits=31)
    line = (record(1) + "\n").encode()
    bomb = b"".join(compressor.compress(line * 1000) for _ in range(200)) + compressor.flush()
    limits = ParseLimits(max_decompressed_bytes=1024 * 1024)

    with pytest.raises(FlowLogError) as caught:
        parse(bomb, limits)

    assert len(bomb) < 200_000
    assert caught.value.code == "limit_exceeded"


def outcome(data: str | bytes, limits: ParseLimits) -> ParseResult | str:
    """The parse result, or the failure code: never any other exception."""
    try:
        return parse(data, limits)
    except FlowLogError as exc:
        return exc.code


@settings(max_examples=300, deadline=None)
@given(st.binary(max_size=4096))
def test_arbitrary_bytes_never_crash_the_parser(data: bytes) -> None:
    result = outcome(
        data, ParseLimits(max_decompressed_bytes=8192, max_rows=50, max_line_bytes=256)
    )

    if isinstance(result, str):
        assert result in ("not_a_flow_log", "limit_exceeded")
    else:
        assert len(result.flows) <= 50
        assert len(result.rejected_samples) <= 20


@settings(max_examples=200, deadline=None)
@given(
    st.lists(
        st.one_of(
            st.builds(record, st.integers(0, 10_000), st.sampled_from(["ACCEPT", "REJECT"])),
            st.text(alphabet=st.characters(codec="ascii"), max_size=160),
        ),
        max_size=60,
    )
)
def test_mixed_lines_respect_the_limits(lines: list[str]) -> None:
    result = outcome("\n".join(lines), ParseLimits(max_rows=40, max_line_bytes=200))

    if isinstance(result, str):
        assert result in ("not_a_flow_log", "limit_exceeded")
    else:
        assert result.rows_parsed + result.rows_rejected + result.rows_skipped <= 40
        assert all(len(sample.text) <= 120 for sample in result.rejected_samples)


def test_rejected_samples_carry_no_control_characters() -> None:
    lines = [record(n) for n in range(40)] + ["bad\x00line\x1b[31m red"]

    result = parse("\n".join(lines))

    assert result.rejected_samples[0].text == "bad�line�[31m red"


def test_concatenated_s3_objects_repeat_their_header_and_still_parse() -> None:
    objects = [
        gzip.compress(
            ("\n".join([HEADER, *(record(10 * part + n) for n in range(15))]) + "\n").encode()
        )
        for part in range(5)
    ]

    result = parse(b"".join(objects))

    assert result.rows_parsed == 75
    assert result.rows_rejected == 0


def test_a_utf8_byte_order_mark_before_the_header_is_ignored() -> None:
    text = "﻿" + "\n".join([HEADER, record(1), record(2)]) + "\n"

    result = parse(text.encode("utf-8"))

    assert result.has_header
    assert result.rows_parsed == 2
