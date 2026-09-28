"""Sliding 5-minute windows over flow start times (spec §8.2, "in any 5-minute window").

A window holds the flows that started less than 5 minutes after its first flow. Every window
that meets a detector's rule qualifies; qualifying windows that share flows merge into one
episode, so an attack lasting an hour is one finding, not sixty."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from datetime import timedelta

from nettriage.domain.flows import NetworkFlow

WINDOW = timedelta(minutes=5)


@dataclass(frozen=True)
class Episode:
    flows: Sequence[NetworkFlow]
    peak_distinct: int


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
    counts: Counter[Hashable] = Counter()
    marked = 0
    left = 0
    spans: list[list[int]] = []  # [first index, last index, peak distinct]
    for right, flow in enumerate(flows):
        counts[key(flow)] += 1
        marked += 1 if is_marked is None or is_marked(flow) else 0
        while flow.start - flows[left].start >= WINDOW:
            leaving = flows[left]
            leaving_key = key(leaving)
            counts[leaving_key] -= 1
            if counts[leaving_key] == 0:
                del counts[leaving_key]
            marked -= 1 if is_marked is None or is_marked(leaving) else 0
            left += 1
        size = right - left + 1
        if len(counts) >= min_distinct and marked * 100 >= min_marked_percent * size:
            if spans and left <= spans[-1][1]:
                spans[-1][1] = right
                spans[-1][2] = max(spans[-1][2], len(counts))
            else:
                spans.append([left, right, len(counts)])
    return [Episode(flows[first : last + 1], peak) for first, last, peak in spans]
