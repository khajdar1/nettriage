"""`outbound_volume` v1 (spec §8.2): an internal source sends at least 50 MB to one external
destination within the file, and that volume's robust z-score against every internal host's
largest outbound volume is at least 5. With fewer than 5 internal hosts, or a MAD of 0, only
the absolute threshold applies. Replies (a server answering a download, see `is_reply`) aren't
outbound transfers and are ignored."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from statistics import median

from nettriage.domain.addresses import is_internal
from nettriage.domain.detection.model import (
    DetectorInfo,
    Finding,
    Severity,
    fingerprint,
    sample_evidence,
)
from nettriage.domain.flows import IPAddress, NetworkFlow, is_reply, protocol_name

DETECTOR_ID = "outbound_volume"
VERSION = 1
MB = 1024 * 1024
MIN_BYTES = 50 * MB
HIGH_BYTES = 500 * MB
MIN_ROBUST_Z = 5.0
MIN_HOSTS_FOR_Z = 5
# Scales the MAD so it estimates the standard deviation of normally distributed data.
MAD_TO_SIGMA = 1.4826
WEB_PORTS = frozenset({80, 443})
TECHNIQUES = ("T1048", "T1041", "T1567")


def detect_outbound_volume(flows: Sequence[NetworkFlow]) -> list[Finding]:
    pairs: defaultdict[tuple[IPAddress, IPAddress], list[NetworkFlow]] = defaultdict(list)
    for flow in flows:
        if (
            flow.action == "ACCEPT"
            and not is_reply(flow)
            and is_internal(flow.src_ip)
            and not is_internal(flow.dst_ip)
        ):
            pairs[(flow.src_ip, flow.dst_ip)].append(flow)
    totals = {pair: sum(flow.bytes for flow in group) for pair, group in pairs.items()}
    host_max: dict[IPAddress, int] = {}
    for (src, _), total in totals.items():
        host_max[src] = max(total, host_max.get(src, 0))
    baseline = _Baseline(list(host_max.values()))

    findings: list[Finding] = []
    for (src, dst), total in totals.items():
        if total < MIN_BYTES:
            continue
        z = baseline.robust_z(total)
        if z is not None and z < MIN_ROBUST_Z:
            continue
        findings.append(_finding(src, dst, pairs[(src, dst)], total, z, len(host_max)))
    return findings


class _Baseline:
    """Median and MAD of the per-host maximum outbound volumes."""

    def __init__(self, volumes: list[int]) -> None:
        self._usable = len(volumes) >= MIN_HOSTS_FOR_Z
        self._median = median(volumes) if volumes else 0.0
        self._mad = median(abs(v - self._median) for v in volumes) if volumes else 0.0

    def robust_z(self, volume: int) -> float | None:
        """None when the absolute threshold alone applies."""
        if not self._usable or self._mad == 0:
            return None
        return (volume - self._median) / (MAD_TO_SIGMA * self._mad)


def _finding(
    src: IPAddress,
    dst: IPAddress,
    flows: list[NetworkFlow],
    total: int,
    z: float | None,
    hosts: int,
) -> Finding:
    by_service: Counter[tuple[int, int]] = Counter()
    for flow in flows:
        by_service[(flow.dst_port, flow.protocol)] += flow.bytes
    (port, protocol), _ = min(by_service.items(), key=lambda item: (-item[1], item[0]))
    severity = Severity.HIGH if total >= HIGH_BYTES or port not in WEB_PORTS else Severity.MEDIUM
    window_start = min(flow.start for flow in flows)
    metrics: dict[str, int | float | bool | str] = {
        "bytes": total,
        "megabytes": round(total / MB, 1),
        "flows": len(flows),
        "dominant_port": port,
        "internal_hosts": hosts,
        "robust_z_applied": z is not None,
    }
    if z is not None:
        metrics["robust_z"] = round(z, 2)
    return Finding(
        detector_id=DETECTOR_ID,
        detector_version=VERSION,
        fingerprint=fingerprint(DETECTOR_ID, VERSION, [str(src), str(dst)], window_start),
        severity=severity,
        title=(
            f"{total / MB:,.0f} MB sent from {src} to {dst} "
            f"(mostly {protocol_name(protocol)} port {port})"
        ),
        src_ip=src,
        dst_ip=dst,
        dst_port=port,
        protocol=protocol,
        window_start=window_start,
        window_end=max(flow.end for flow in flows),
        metrics=metrics,
        candidate_techniques=TECHNIQUES,
        evidence=sample_evidence(flows),
    )


INFO = DetectorInfo(
    id=DETECTOR_ID,
    version=VERSION,
    name="Unusual outbound volume",
    description=(
        "An internal host sending at least 50 MB to one external destination, far more than "
        "the other hosts in the file send (robust z-score of 5 or more)."
    ),
    candidate_techniques=TECHNIQUES,
    run=detect_outbound_volume,
)
