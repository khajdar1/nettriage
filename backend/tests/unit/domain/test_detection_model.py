from dataclasses import replace
from datetime import timedelta
from ipaddress import ip_address

from flowmaker import T0, make_flow

from nettriage.domain.detection.model import (
    Finding,
    Severity,
    fingerprint,
    rank_and_cap,
    sample_evidence,
)
from nettriage.domain.detection.windows import sliding_episodes
from nettriage.domain.flows import NetworkFlow


def test_fingerprints_ignore_where_the_window_starts_within_five_minutes() -> None:
    first = fingerprint("port_scan", 1, ["vertical", "1.2.3.4"], T0 + timedelta(seconds=10))
    same_bucket = fingerprint("port_scan", 1, ["vertical", "1.2.3.4"], T0 + timedelta(seconds=299))
    next_bucket = fingerprint("port_scan", 1, ["vertical", "1.2.3.4"], T0 + timedelta(seconds=300))

    assert first == same_bucket
    assert first != next_bucket
    assert len(first) == 64


def test_fingerprints_depend_on_detector_version_and_entities() -> None:
    base = fingerprint("port_scan", 1, ["vertical", "1.2.3.4"], T0)

    assert base != fingerprint("port_scan", 2, ["vertical", "1.2.3.4"], T0)
    assert base != fingerprint("outbound_volume", 1, ["vertical", "1.2.3.4"], T0)
    assert base != fingerprint("port_scan", 1, ["vertical", "1.2.3.5"], T0)
    # Entities are separated, so ("1.2", "3.4") can't collide with ("1.23", ".4").
    assert fingerprint("x", 1, ["1.2", "3.4"], T0) != fingerprint("x", 1, ["1.23", ".4"], T0)


def test_small_evidence_sets_are_kept_whole_in_time_order() -> None:
    flows = [make_flow("1.2.3.4", "10.0.0.1", 80 + n, at=40 - n) for n in range(40)]

    evidence = sample_evidence(flows)

    assert len(evidence) == 40
    assert [flow.start for flow in evidence] == sorted(flow.start for flow in flows)


def test_large_evidence_sets_keep_the_first_ten_last_ten_and_thirty_between() -> None:
    flows = [make_flow("1.2.3.4", "10.0.0.1", n, at=n) for n in range(1000)]

    evidence = sample_evidence(reversed(flows))

    assert len(evidence) == 50
    assert list(evidence[:10]) == flows[:10]
    assert list(evidence[-10:]) == flows[-10:]
    middle = evidence[10:40]
    assert len(set(middle)) == 30
    assert middle[0] == flows[10]
    assert middle[-1] == flows[989]
    assert [flow.start for flow in evidence] == sorted(flow.start for flow in evidence)


def finding(severity: Severity, at: int, fp: str) -> Finding:
    return Finding(
        detector_id="port_scan",
        detector_version=1,
        fingerprint=fp,
        severity=severity,
        title="t",
        src_ip=ip_address("1.2.3.4"),
        dst_ip=None,
        dst_port=None,
        protocol=None,
        window_start=T0 + timedelta(seconds=at),
        window_end=T0 + timedelta(seconds=at + 1),
        metrics={},
        candidate_techniques=(),
        evidence=(),
    )


def test_ranking_puts_high_severity_first_then_earliest_and_caps() -> None:
    findings = [
        finding(Severity.MEDIUM, 0, "a"),
        finding(Severity.HIGH, 50, "b"),
        finding(Severity.HIGH, 10, "c"),
        finding(Severity.LOW, 0, "d"),
    ]

    kept, truncated = rank_and_cap(findings, limit=3)

    assert [f.fingerprint for f in kept] == ["c", "b", "a"]
    assert truncated == 1


def test_ranking_keeps_one_finding_per_fingerprint() -> None:
    duplicate = finding(Severity.MEDIUM, 0, "a")

    kept, truncated = rank_and_cap([duplicate, replace(duplicate, title="again")])

    assert len(kept) == 1
    assert truncated == 0


def test_windows_slide_so_activity_across_a_five_minute_boundary_counts() -> None:
    flows = [make_flow("1.2.3.4", "10.0.0.1", port, at=240 + port) for port in range(100)]

    episodes = sliding_episodes(flows, key=lambda f: f.dst_port, min_distinct=100)

    assert len(episodes) == 1
    assert episodes[0].peak_distinct == 100


def test_activity_spread_wider_than_five_minutes_does_not_qualify() -> None:
    flows = [make_flow("1.2.3.4", "10.0.0.1", port, at=port * 4) for port in range(100)]

    assert sliding_episodes(flows, key=lambda f: f.dst_port, min_distinct=100) == []


def test_overlapping_qualifying_windows_merge_into_one_episode() -> None:
    flows = [make_flow("1.2.3.4", "10.0.0.1", n % 500, at=n) for n in range(3600)]

    episodes = sliding_episodes(flows, key=lambda f: f.dst_port, min_distinct=100)

    assert len(episodes) == 1
    assert len(episodes[0].flows) == 3600


def test_separate_bursts_are_separate_episodes() -> None:
    first = [make_flow("1.2.3.4", "10.0.0.1", port, at=port) for port in range(120)]
    second = [make_flow("1.2.3.4", "10.0.0.1", port, at=3600 + port) for port in range(120)]

    episodes = sliding_episodes(first + second, key=lambda f: f.dst_port, min_distinct=100)

    assert [len(episode.flows) for episode in episodes] == [120, 120]


def test_the_marked_share_must_hold_in_the_window() -> None:
    flows = [
        make_flow("1.2.3.4", "10.0.0.1", port, at=port, action="ACCEPT" if port < 30 else "REJECT")
        for port in range(100)
    ]

    def rejected(flow: NetworkFlow) -> bool:
        return flow.action == "REJECT"

    assert sliding_episodes(flows, lambda f: f.dst_port, 100, rejected, 80) == []
    assert len(sliding_episodes(flows, lambda f: f.dst_port, 100, rejected, 70)) == 1


def test_ordinary_flows_just_before_a_scan_do_not_hide_it() -> None:
    requests = [
        make_flow("198.51.100.7", "10.0.1.20", 443, at=n, packets=30, action="ACCEPT")
        for n in range(30)
    ]
    probes = [
        make_flow("198.51.100.7", "10.0.1.20", 1_000 + n, at=240 + n * 0.5) for n in range(110)
    ]

    def scan_like(flow: NetworkFlow) -> bool:
        return flow.action == "REJECT" or flow.packets <= 3

    episodes = sliding_episodes(requests + probes, lambda f: f.dst_port, 100, scan_like, 80)

    assert len(episodes) == 1
    # The window starting at the last request holds it and all 110 probes: 111 ports, 99% marked.
    assert episodes[0].peak_distinct == 111
