from ipaddress import ip_address

from flowmaker import make_flow

from nettriage.domain.detection.model import Severity
from nettriage.domain.detection.port_scan import detect_port_scans
from nettriage.domain.flows import UDP, NetworkFlow

ATTACKER = "198.51.100.7"
TARGET = "10.0.1.20"


def vertical(
    src: str, ports: int, *, start: float = 0.0, spacing: float = 1.0
) -> list[NetworkFlow]:
    return [make_flow(src, TARGET, 1 + n, at=start + n * spacing) for n in range(ports)]


def horizontal(src: str, hosts: int, port: int = 445) -> list[NetworkFlow]:
    return [make_flow(src, f"10.0.2.{1 + n}", port, at=n) for n in range(hosts)]


def test_an_external_vertical_scan_is_a_medium_finding() -> None:
    [finding] = detect_port_scans(vertical(ATTACKER, 150))

    assert finding.detector_id == "port_scan"
    assert finding.severity is Severity.MEDIUM
    assert finding.src_ip == ip_address(ATTACKER)
    assert finding.dst_ip == ip_address(TARGET)
    assert finding.dst_port is None
    assert finding.protocol == 6
    assert finding.metrics["variant"] == "vertical"
    assert finding.metrics["peak_distinct"] == 150
    assert finding.candidate_techniques == ("T1595", "T1595.001")
    assert finding.title == f"Port scan of {TARGET} from {ATTACKER}: 150 TCP ports in 5 minutes"
    assert len(finding.evidence) == 50


def test_an_external_scan_of_a_thousand_ports_is_high() -> None:
    [finding] = detect_port_scans(vertical(ATTACKER, 1_200, spacing=0.2))

    assert finding.severity is Severity.HIGH


def test_an_internal_source_is_always_high_with_network_service_discovery() -> None:
    [finding] = detect_port_scans(horizontal("10.0.9.9", 60))

    assert finding.severity is Severity.HIGH
    assert finding.metrics["variant"] == "horizontal"
    assert finding.dst_ip is None
    assert finding.dst_port == 445
    assert finding.candidate_techniques == ("T1046",)


def test_ninety_nine_ports_is_not_a_scan() -> None:
    assert detect_port_scans(vertical(ATTACKER, 99)) == []


def test_forty_nine_hosts_is_not_a_scan() -> None:
    assert detect_port_scans(horizontal(ATTACKER, 49)) == []


def test_a_slow_scan_never_reaching_100_ports_in_5_minutes_is_not_flagged() -> None:
    assert detect_port_scans(vertical(ATTACKER, 400, spacing=4.0)) == []


def test_a_scan_straddling_a_five_minute_boundary_is_detected() -> None:
    flows = vertical(ATTACKER, 120, start=240.0, spacing=0.9)

    assert len(detect_port_scans(flows)) == 1


def test_mostly_completed_connections_are_not_a_scan() -> None:
    flows = [
        make_flow(ATTACKER, TARGET, 1 + n, at=n, packets=40, action="ACCEPT") for n in range(150)
    ]

    assert detect_port_scans(flows) == []


def test_tiny_accepted_probes_still_count_as_scan_like() -> None:
    flows = [
        make_flow(ATTACKER, TARGET, 1 + n, at=n, packets=2, action="ACCEPT") for n in range(150)
    ]

    assert len(detect_port_scans(flows)) == 1


def test_a_long_scan_is_one_finding_and_separate_bursts_are_two() -> None:
    long_scan = [make_flow(ATTACKER, TARGET, 1 + n % 1000, at=n * 0.5) for n in range(7200)]
    bursts = vertical("198.51.100.8", 120) + vertical("198.51.100.8", 120, start=7200.0)

    findings = detect_port_scans(long_scan + bursts)

    assert sorted(str(f.src_ip) for f in findings) == [
        "198.51.100.7",
        "198.51.100.8",
        "198.51.100.8",
    ]
    assert len({f.fingerprint for f in findings}) == 3


def test_a_server_answering_many_clients_is_not_a_scan() -> None:
    responses = [
        make_flow(
            TARGET,
            f"203.0.113.{1 + n % 200}",
            30_000 + n,
            at=n * 0.2,
            src_port=443,
            packets=12,
            bytes=9_000,
            action="ACCEPT",
        )
        for n in range(1_000)
    ]

    assert detect_port_scans(responses) == []


def test_udp_is_scanned_separately_and_icmp_never() -> None:
    udp = [make_flow(ATTACKER, TARGET, 1 + n, at=n, protocol=UDP) for n in range(120)]
    icmp = [make_flow(ATTACKER, f"10.0.3.{n}", 0, at=n, protocol=1) for n in range(1, 120)]

    [finding] = detect_port_scans(udp + icmp)

    assert finding.protocol == UDP
    assert "UDP ports" in finding.title


def test_findings_are_identical_when_detection_runs_twice() -> None:
    flows = vertical(ATTACKER, 150) + horizontal("10.0.9.9", 60)

    assert detect_port_scans(flows) == detect_port_scans(list(flows))
