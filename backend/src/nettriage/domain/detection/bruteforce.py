"""`remote_access_bruteforce` v1 (spec §8.2): in any 5-minute window, one source makes at least
30 small flows (at most 20 packets each) to TCP 22 or 3389 on one host, or reaches at least 10
hosts on one of those ports (spraying). A later large or long flow from the same source to an
attacked host and port, within 30 minutes, marks a possible successful login."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta

from nettriage.domain.detection.model import (
    DetectorInfo,
    Finding,
    Severity,
    fingerprint,
    flow_order,
    sample_evidence,
)
from nettriage.domain.detection.windows import Episode, sliding_episodes
from nettriage.domain.flows import TCP, IPAddress, NetworkFlow

DETECTOR_ID = "remote_access_bruteforce"
VERSION = 1
SERVICES = {22: "SSH", 3389: "RDP"}
MIN_ATTEMPTS = 30
ATTEMPT_MAX_PACKETS = 20
SPRAY_MIN_HOSTS = 10
SUCCESS_MIN_BYTES = 100 * 1024
SUCCESS_MIN_DURATION = timedelta(minutes=5)
SUCCESS_WITHIN = timedelta(minutes=30)
GUESSING_TECHNIQUES = ("T1110", "T1110.001")
SPRAYING_TECHNIQUES = ("T1110", "T1110.003")
LOGIN_TECHNIQUES = {22: "T1021.004", 3389: "T1021.001"}

type Target = tuple[IPAddress, IPAddress, int]


def _is_attempt(flow: NetworkFlow) -> bool:
    return flow.packets <= ATTEMPT_MAX_PACKETS


def _is_success(flow: NetworkFlow) -> bool:
    return flow.bytes >= SUCCESS_MIN_BYTES or flow.end - flow.start >= SUCCESS_MIN_DURATION


def detect_bruteforce(flows: Sequence[NetworkFlow]) -> list[Finding]:
    to_service: defaultdict[Target, list[NetworkFlow]] = defaultdict(list)
    for flow in flows:
        if flow.protocol == TCP and flow.dst_port in SERVICES:
            to_service[(flow.src_ip, flow.dst_ip, flow.dst_port)].append(flow)
    spray_groups: defaultdict[tuple[IPAddress, int], list[NetworkFlow]] = defaultdict(list)
    for target, group in to_service.items():
        group.sort(key=flow_order)
        spray_groups[(target[0], target[2])].extend(f for f in group if _is_attempt(f))
    successes = _SuccessIndex(to_service)

    findings: list[Finding] = []
    for (src, dst, port), group in to_service.items():
        attempts = [flow for flow in group if _is_attempt(flow)]
        for episode in sliding_episodes(
            attempts, key=lambda f: f.line_no, min_distinct=MIN_ATTEMPTS
        ):
            success = successes.first_after(episode, [(src, dst, port)])
            service = SERVICES[port]
            title = (
                f"{service} brute force against {dst} from {src}: "
                f"{episode.peak_distinct} attempts in 5 minutes"
            )
            findings.append(
                _finding(
                    "single",
                    episode,
                    src=src,
                    dst_ip=dst,
                    port=port,
                    entities=[str(dst), str(port)],
                    techniques=GUESSING_TECHNIQUES,
                    success=success,
                    title=title,
                )
            )
    for (src, port), attempts in spray_groups.items():
        attempts.sort(key=flow_order)
        for episode in sliding_episodes(
            attempts, key=lambda f: f.dst_ip, min_distinct=SPRAY_MIN_HOSTS
        ):
            hosts = sorted({flow.dst_ip for flow in episode.flows})
            success = successes.first_after(episode, [(src, host, port) for host in hosts])
            service = SERVICES[port]
            title = (
                f"{service} password spraying across {episode.peak_distinct} hosts "
                f"from {src} in 5 minutes"
            )
            findings.append(
                _finding(
                    "spray",
                    episode,
                    src=src,
                    dst_ip=None,
                    port=port,
                    entities=[str(port)],
                    techniques=SPRAYING_TECHNIQUES,
                    success=success,
                    title=title,
                )
            )
    return findings


class _SuccessIndex:
    """Large or long flows per (source, host, port), sorted by start, for the success check."""

    def __init__(self, to_service: dict[Target, list[NetworkFlow]]) -> None:
        # Start times are computed once per target: rebuilding them per episode made the
        # lookup quadratic on files with many bursts.
        self._flows: dict[Target, tuple[list[NetworkFlow], list[datetime]]] = {}
        for target, group in to_service.items():
            candidates = [flow for flow in group if _is_success(flow)]
            if candidates:
                self._flows[target] = (candidates, [flow.start for flow in candidates])

    def first_after(self, episode: Episode, targets: list[Target]) -> NetworkFlow | None:
        """The earliest success-like flow that starts after the episode's first attempt and
        no later than 30 minutes after its last attempt."""
        earliest = episode.flows[0].start
        latest = episode.flows[-1].start + SUCCESS_WITHIN
        found: list[NetworkFlow] = []
        for target in targets:
            candidates, starts = self._flows.get(target, ([], []))
            found.extend(candidates[bisect_left(starts, earliest) : bisect_right(starts, latest)])
        return min(found, key=flow_order) if found else None


def _finding(
    variant: str,
    episode: Episode,
    *,
    src: IPAddress,
    dst_ip: IPAddress | None,
    port: int,
    entities: list[str],
    techniques: tuple[str, ...],
    success: NetworkFlow | None,
    title: str,
) -> Finding:
    flows = episode.flows
    window_start = flows[0].start
    metrics: dict[str, int | float | bool | str] = {
        "variant": variant,
        "peak_distinct": episode.peak_distinct,
        "attempts": len(flows),
        "hosts": len({flow.dst_ip for flow in flows}),
        "possible_success": success is not None,
    }
    evidence = list(flows)
    if success is not None:
        metrics["success_bytes"] = success.bytes
        metrics["success_seconds"] = int((success.end - success.start).total_seconds())
        techniques = (*techniques, LOGIN_TECHNIQUES[port])
        title += " (possible successful login)"
        evidence.append(success)
    return Finding(
        detector_id=DETECTOR_ID,
        detector_version=VERSION,
        fingerprint=fingerprint(DETECTOR_ID, VERSION, [variant, str(src), *entities], window_start),
        severity=Severity.HIGH if success is not None else Severity.MEDIUM,
        title=title,
        src_ip=src,
        dst_ip=dst_ip,
        dst_port=port,
        protocol=TCP,
        window_start=window_start,
        window_end=max(flow.end for flow in evidence),
        metrics=metrics,
        candidate_techniques=techniques,
        evidence=sample_evidence(evidence),
    )


INFO = DetectorInfo(
    id=DETECTOR_ID,
    version=VERSION,
    name="SSH/RDP brute force",
    description=(
        "Many short connections to SSH (22) or RDP (3389) on one host within 5 minutes, or the "
        "same port on many hosts (password spraying). A later large or long session from the "
        "same source marks a possible successful login."
    ),
    candidate_techniques=(
        "T1110",
        "T1110.001",
        "T1110.003",
        LOGIN_TECHNIQUES[22],
        LOGIN_TECHNIQUES[3389],
    ),
    run=detect_bruteforce,
)
