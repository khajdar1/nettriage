"""`port_scan` v1 (spec §8.2): in any 5-minute window, one source reaches at least 100 distinct
destination ports on one host (vertical) or at least 50 distinct hosts on one port
(horizontal), with at least 80% of those flows REJECT or at most 3 packets."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from nettriage.domain.addresses import is_internal
from nettriage.domain.detection.model import (
    DetectorInfo,
    Finding,
    Severity,
    fingerprint,
    flow_order,
    sample_evidence,
)
from nettriage.domain.detection.windows import Episode, sliding_episodes
from nettriage.domain.flows import TCP, UDP, IPAddress, NetworkFlow, protocol_name

DETECTOR_ID = "port_scan"
VERSION = 1
VERTICAL_MIN_PORTS = 100
HORIZONTAL_MIN_HOSTS = 50
SCAN_LIKE_MIN_PERCENT = 80
SCAN_LIKE_MAX_PACKETS = 3
EXTERNAL_HIGH_MIN = 1_000
SCAN_PROTOCOLS = frozenset({TCP, UDP})
EXTERNAL_TECHNIQUES = ("T1595", "T1595.001")
INTERNAL_TECHNIQUES = ("T1046",)


def _scan_like(flow: NetworkFlow) -> bool:
    return flow.action == "REJECT" or flow.packets <= SCAN_LIKE_MAX_PACKETS


def detect_port_scans(flows: Sequence[NetworkFlow]) -> list[Finding]:
    vertical: defaultdict[tuple[IPAddress, IPAddress, int], list[NetworkFlow]] = defaultdict(list)
    horizontal: defaultdict[tuple[IPAddress, int, int], list[NetworkFlow]] = defaultdict(list)
    for flow in flows:
        if flow.protocol in SCAN_PROTOCOLS:
            vertical[(flow.src_ip, flow.dst_ip, flow.protocol)].append(flow)
            horizontal[(flow.src_ip, flow.dst_port, flow.protocol)].append(flow)

    findings: list[Finding] = []
    for (src, dst, protocol), group in vertical.items():
        if len(group) < VERTICAL_MIN_PORTS:
            continue
        group.sort(key=flow_order)
        for episode in sliding_episodes(
            group,
            key=lambda f: f.dst_port,
            min_distinct=VERTICAL_MIN_PORTS,
            is_marked=_scan_like,
            min_marked_percent=SCAN_LIKE_MIN_PERCENT,
        ):
            peak = episode.peak_distinct
            title = (
                f"Port scan of {dst} from {src}: {peak} {protocol_name(protocol)} ports "
                "in 5 minutes"
            )
            findings.append(
                _finding(
                    "vertical",
                    episode,
                    src=src,
                    dst_ip=dst,
                    dst_port=None,
                    protocol=protocol,
                    entities=[str(dst), str(protocol)],
                    distinct_total=len({f.dst_port for f in episode.flows}),
                    title=title,
                )
            )
    for (src, port, protocol), group in horizontal.items():
        if len(group) < HORIZONTAL_MIN_HOSTS:
            continue
        group.sort(key=flow_order)
        for episode in sliding_episodes(
            group,
            key=lambda f: f.dst_ip,
            min_distinct=HORIZONTAL_MIN_HOSTS,
            is_marked=_scan_like,
            min_marked_percent=SCAN_LIKE_MIN_PERCENT,
        ):
            peak = episode.peak_distinct
            title = (
                f"Port scan of {protocol_name(protocol)} port {port} across {peak} hosts "
                f"from {src} in 5 minutes"
            )
            findings.append(
                _finding(
                    "horizontal",
                    episode,
                    src=src,
                    dst_ip=None,
                    dst_port=port,
                    protocol=protocol,
                    entities=[str(port), str(protocol)],
                    distinct_total=len({f.dst_ip for f in episode.flows}),
                    title=title,
                )
            )
    return findings


def _finding(
    variant: str,
    episode: Episode,
    *,
    src: IPAddress,
    dst_ip: IPAddress | None,
    dst_port: int | None,
    protocol: int,
    entities: list[str],
    distinct_total: int,
    title: str,
) -> Finding:
    flows = episode.flows
    internal = is_internal(src)
    if internal or episode.peak_distinct >= EXTERNAL_HIGH_MIN:
        severity = Severity.HIGH
    else:
        severity = Severity.MEDIUM
    scan_like = sum(1 for flow in flows if _scan_like(flow))
    window_start = flows[0].start
    return Finding(
        detector_id=DETECTOR_ID,
        detector_version=VERSION,
        fingerprint=fingerprint(DETECTOR_ID, VERSION, [variant, str(src), *entities], window_start),
        severity=severity,
        title=title,
        src_ip=src,
        dst_ip=dst_ip,
        dst_port=dst_port,
        protocol=protocol,
        window_start=window_start,
        window_end=max(flow.end for flow in flows),
        metrics={
            "variant": variant,
            "peak_distinct": episode.peak_distinct,
            "distinct_total": distinct_total,
            "flows": len(flows),
            "rejected": sum(1 for flow in flows if flow.action == "REJECT"),
            "scan_like_percent": round(100 * scan_like / len(flows)),
            "source_internal": internal,
        },
        candidate_techniques=INTERNAL_TECHNIQUES if internal else EXTERNAL_TECHNIQUES,
        evidence=sample_evidence(flows),
    )


INFO = DetectorInfo(
    id=DETECTOR_ID,
    version=VERSION,
    name="Port scan",
    description=(
        "One source reaching at least 100 ports on one host, or one port on at least 50 hosts, "
        "within 5 minutes, mostly rejected or tiny flows."
    ),
    candidate_techniques=(*EXTERNAL_TECHNIQUES, *INTERNAL_TECHNIQUES),
    run=detect_port_scans,
)
