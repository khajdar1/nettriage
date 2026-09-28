"""Stream a VPC Flow Logs file, plain or gzip, into `NetworkFlow` records (spec §8.1, §8.6)."""

from __future__ import annotations

import gzip
import io
import zlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import BinaryIO, Literal

from nettriage.domain.flows import NetworkFlow
from nettriage.domain.parsing.vpc_records import (
    Layout,
    RecordError,
    SkippedRecord,
    layout_for,
    parse_record,
)

GZIP_MAGIC = b"\x1f\x8b"
UTF8_BOM = b"\xef\xbb\xbf"
CHUNK_BYTES = 64 * 1024
MAX_REJECTED_SAMPLES = 20
SAMPLE_CHARS = 120
MAX_INVALID_SHARE = 0.05

type FailureCode = Literal["not_a_flow_log", "limit_exceeded"]


@dataclass(frozen=True)
class ParseLimits:
    """Spec §8.1's streaming limits. Sizes are binary: 1 MB = 1,048,576 bytes."""

    max_decompressed_bytes: int = 250 * 1024 * 1024
    max_rows: int = 2_000_000
    max_line_bytes: int = 4 * 1024


@dataclass(frozen=True)
class RejectedSample:
    line_no: int
    reason: str
    text: str


@dataclass(frozen=True)
class ParseResult:
    flows: list[NetworkFlow]
    rows_parsed: int
    rows_rejected: int
    rows_skipped: int
    rejected_samples: list[RejectedSample]
    has_header: bool


class FlowLogError(Exception):
    """The upload fails with `code`; `detail` is a readable reason for the UI."""

    def __init__(self, code: FailureCode, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code: FailureCode = code
        self.detail = detail


class _LineTooLong(Exception):
    pass


class _TooLarge(Exception):
    pass


def parse_flow_log(stream: BinaryIO, limits: ParseLimits | None = None) -> ParseResult:
    """Parse `stream` completely or raise `FlowLogError`. Gzip is detected from the first two
    bytes, never from a file name. Memory stays bounded by the limits: nothing is read past
    them."""
    limits = limits or ParseLimits()
    parser = _Parser(limits)
    try:
        for raw in _lines(_decompressed(stream), limits):
            parser.feed(raw)
    except _LineTooLong:
        if parser.rows_parsed == 0:
            raise FlowLogError(
                "not_a_flow_log", "the file doesn't contain flow log lines"
            ) from None
        raise FlowLogError(
            "limit_exceeded", f"a line is longer than {limits.max_line_bytes} bytes"
        ) from None
    except _TooLarge:
        raise FlowLogError(
            "limit_exceeded",
            f"the file is larger than {limits.max_decompressed_bytes // (1024 * 1024)} MB "
            "uncompressed",
        ) from None
    except gzip.BadGzipFile, EOFError, zlib.error:
        raise FlowLogError(
            "not_a_flow_log", "the file looks like gzip but can't be decompressed"
        ) from None
    return parser.finish()


class _Rewound:
    """`stream` with the bytes already read put back in front, for `gzip.GzipFile`."""

    def __init__(self, head: bytes, stream: BinaryIO) -> None:
        self._head = head
        self._stream = stream

    def read(self, size: int = -1) -> bytes:
        if not self._head:
            return self._stream.read(size)
        if size < 0:
            data, self._head = self._head + self._stream.read(), b""
            return data
        data, self._head = self._head[:size], self._head[size:]
        return data

    def seek(self, offset: int, /) -> int:
        # GzipFile reads front to back and never seeks; uploads can't rewind anyway.
        raise io.UnsupportedOperation("a flow log stream is read once, front to back")


def _decompressed(stream: BinaryIO) -> Iterator[bytes]:
    head = stream.read(len(GZIP_MAGIC))
    if head == GZIP_MAGIC:
        with gzip.GzipFile(fileobj=_Rewound(head, stream), mode="rb") as unzipped:
            while chunk := unzipped.read(CHUNK_BYTES):
                yield chunk
        return
    if head:
        yield head
    while chunk := stream.read(CHUNK_BYTES):
        yield chunk


def _lines(chunks: Iterable[bytes], limits: ParseLimits) -> Iterator[bytes]:
    total = 0
    pending = b""
    for chunk in chunks:
        total += len(chunk)
        if total > limits.max_decompressed_bytes:
            raise _TooLarge
        *complete, pending = (pending + chunk).split(b"\n")
        for line in complete:
            if len(line) > limits.max_line_bytes:
                raise _LineTooLong
            yield line
        if len(pending) > limits.max_line_bytes:
            raise _LineTooLong
    if pending:
        yield pending


class _Parser:
    def __init__(self, limits: ParseLimits) -> None:
        self._limits = limits
        self._layout: Layout | None = None
        self._line_no = 0
        self._rows = 0
        self.rows_parsed = 0
        self._rows_rejected = 0
        self._rows_skipped = 0
        self._flows: list[NetworkFlow] = []
        self._samples: list[RejectedSample] = []

    def feed(self, raw: bytes) -> None:
        self._line_no += 1
        raw = raw.rstrip(b"\r")
        if self._line_no == 1 and raw.startswith(UTF8_BOM):
            raw = raw[len(UTF8_BOM) :]
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            self._count_row()
            self._reject(raw, "encoding")
            return
        if not text.strip():
            return
        if self._layout is None:
            try:
                self._layout = layout_for(text)
            except RecordError as exc:
                raise FlowLogError("not_a_flow_log", exc.reason) from None
            if self._layout.has_header:
                return
        elif self._layout.has_header and tuple(text.split()) == self._layout.fields:
            return  # Concatenated S3 objects each start with the same header.
        self._count_row()
        try:
            self._flows.append(parse_record(text, self._layout, self._line_no))
            self.rows_parsed += 1
        except SkippedRecord:
            self._rows_skipped += 1
        except RecordError as exc:
            self._reject(raw, exc.reason)

    def finish(self) -> ParseResult:
        records = self.rows_parsed + self._rows_rejected
        if records == 0 and self._rows_skipped == 0:
            raise FlowLogError("not_a_flow_log", "the file contains no flow records")
        if self._rows_rejected > MAX_INVALID_SHARE * records:
            raise FlowLogError(
                "not_a_flow_log",
                f"{self._rows_rejected} of {records} records aren't valid flow log records",
            )
        return ParseResult(
            flows=self._flows,
            rows_parsed=self.rows_parsed,
            rows_rejected=self._rows_rejected,
            rows_skipped=self._rows_skipped,
            rejected_samples=self._samples,
            has_header=self._layout is not None and self._layout.has_header,
        )

    def _count_row(self) -> None:
        self._rows += 1
        if self._rows > self._limits.max_rows:
            raise FlowLogError(
                "limit_exceeded", f"the file has more than {self._limits.max_rows:,} rows"
            )

    def _reject(self, raw: bytes, reason: str) -> None:
        self._rows_rejected += 1
        if len(self._samples) < MAX_REJECTED_SAMPLES:
            # Printable ASCII only: NUL breaks Postgres jsonb, and escape codes reach the UI.
            text = "".join(chr(b) if 0x20 <= b <= 0x7E else "�" for b in raw[:SAMPLE_CHARS])
            self._samples.append(RejectedSample(self._line_no, reason, text))
