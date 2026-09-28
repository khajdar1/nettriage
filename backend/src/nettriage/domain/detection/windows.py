"""Sliding 5-minute windows over flow start times (spec §8.2, "in any 5-minute window").

A window holds the flows that start within 5 minutes of each other. Every flow is checked as
the first flow of a window (the flows starting less than 5 minutes after it) and as the last
(the flows starting less than 5 minutes before it), so ordinary traffic just before or just
after an attack can't dilute it. Qualifying windows that share flows merge into one episode,
so an attack lasting an hour is one finding, not sixty."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from datetime import timedelta

from nettriage.domain.flows import NetworkFlow

WINDOW = timedelta(minutes=5)

type Span = list[int]  # [first index, last index, peak distinct]


@dataclass(frozen=True)
class Episode:
    flows: Sequence[NetworkFlow]
    peak_distinct: int


@dataclass(frozen=True)
class _Rule:
    min_distinct: int
    min_marked_percent: int


class _Window:
    """Distinct keys and the marked-flow count of the flows currently in the window."""

    def __init__(self, keys: list[Hashable], marks: list[bool]) -> None:
        self._keys = keys
        self._marks = marks
        self._counts: Counter[Hashable] = Counter()
        self._marked = 0
        self._size = 0

    def add(self, index: int) -> None:
        self._counts[self._keys[index]] += 1
        self._marked += self._marks[index]
        self._size += 1

    def remove(self, index: int) -> None:
        key = self._keys[index]
        self._counts[key] -= 1
        if self._counts[key] == 0:
            del self._counts[key]
        self._marked -= self._marks[index]
        self._size -= 1

    @property
    def distinct(self) -> int:
        return len(self._counts)

    def qualifies(self, rule: _Rule) -> bool:
        return (
            len(self._counts) >= rule.min_distinct
            and self._marked * 100 >= rule.min_marked_percent * self._size
        )


def sliding_episodes(
    flows: Sequence[NetworkFlow],
    key: Callable[[NetworkFlow], Hashable],
    min_distinct: int,
    is_marked: Callable[[NetworkFlow], bool] | None = None,
    min_marked_percent: int = 0,
) -> list[Episode]:
    """`flows` must be sorted by `flow_order`. A window qualifies when it holds at least
    `min_distinct` distinct `key` values and at least `min_marked_percent` of its flows satisfy
    `is_marked`. `peak_distinct` is the most distinct values any qualifying window held."""
    if len(flows) < min_distinct:
        return []
    keys = [key(flow) for flow in flows]
    marks = [is_marked is None or is_marked(flow) for flow in flows]
    rule = _Rule(min_distinct, min_marked_percent)
    spans = _windows_by_last_flow(flows, keys, marks, rule)
    spans += _windows_by_first_flow(flows, keys, marks, rule)
    merged: list[Span] = []
    for first, last, distinct in sorted(spans):
        _extend(merged, first, last, distinct)
    return [Episode(flows[first : last + 1], peak) for first, last, peak in merged]


def _extend(spans: list[Span], first: int, last: int, distinct: int) -> None:
    """Add a qualifying window, merging it into the previous one when they share flows."""
    if spans and first <= spans[-1][1]:
        spans[-1][1] = max(spans[-1][1], last)
        spans[-1][2] = max(spans[-1][2], distinct)
    else:
        spans.append([first, last, distinct])


def _windows_by_last_flow(
    flows: Sequence[NetworkFlow], keys: list[Hashable], marks: list[bool], rule: _Rule
) -> list[Span]:
    window = _Window(keys, marks)
    spans: list[Span] = []
    left = 0
    for right, flow in enumerate(flows):
        window.add(right)
        while flow.start - flows[left].start >= WINDOW:
            window.remove(left)
            left += 1
        if window.qualifies(rule):
            _extend(spans, left, right, window.distinct)
    return spans


def _windows_by_first_flow(
    flows: Sequence[NetworkFlow], keys: list[Hashable], marks: list[bool], rule: _Rule
) -> list[Span]:
    window = _Window(keys, marks)
    spans: list[Span] = []
    right = 0
    for left, flow in enumerate(flows):
        while right < len(flows) and flows[right].start - flow.start < WINDOW:
            window.add(right)
            right += 1
        if window.qualifies(rule):
            _extend(spans, left, right - 1, window.distinct)
        window.remove(left)
    return spans
