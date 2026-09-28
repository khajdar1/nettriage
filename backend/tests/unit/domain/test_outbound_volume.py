from ipaddress import ip_address

from flowmaker import make_flow

from nettriage.domain.detection.model import Severity
from nettriage.domain.detection.outbound_volume import MB, detect_outbound_volume
from nettriage.domain.flows import NetworkFlow

EXFIL_DST = "203.0.113.50"


def upload(
    src: str,
    dst: str,
    megabytes: float,
    *,
    port: int = 443,
    chunks: int = 10,
    action: str = "ACCEPT",
) -> list[NetworkFlow]:
    size = int(megabytes * MB / chunks)
    return [
        make_flow(
            src,
            dst,
            port,
            at=n * 30.0,
            duration=25.0,
            packets=size // 1400 + 1,
            bytes=size,
            action="ACCEPT" if action == "ACCEPT" else "REJECT",
        )
        for n in range(chunks)
    ]


def baseline(hosts: int = 20) -> list[NetworkFlow]:
    """Ordinary hosts, each sending 1-3 MB to a web service."""
    return [
        flow for n in range(hosts) for flow in upload(f"10.0.5.{n + 1}", "198.51.100.80", 1 + n % 3)
    ]


def test_a_large_outlier_upload_is_a_finding() -> None:
    [finding] = detect_outbound_volume(baseline() + upload("10.0.5.200", EXFIL_DST, 80))

    assert finding.detector_id == "outbound_volume"
    assert finding.severity is Severity.MEDIUM
    assert finding.src_ip == ip_address("10.0.5.200")
    assert finding.dst_ip == ip_address(EXFIL_DST)
    assert finding.dst_port == 443
    assert finding.metrics["robust_z_applied"] is True
    assert float(finding.metrics["robust_z"]) >= 5
    assert finding.candidate_techniques == ("T1048", "T1041", "T1567")
    assert finding.title == f"80 MB sent from 10.0.5.200 to {EXFIL_DST} (mostly TCP port 443)"


def test_below_fifty_megabytes_is_never_a_finding() -> None:
    assert detect_outbound_volume(baseline() + upload("10.0.5.200", EXFIL_DST, 49)) == []


def test_five_hundred_megabytes_is_high() -> None:
    [finding] = detect_outbound_volume(baseline() + upload("10.0.5.200", EXFIL_DST, 520))

    assert finding.severity is Severity.HIGH


def test_a_non_web_port_is_high() -> None:
    [finding] = detect_outbound_volume(baseline() + upload("10.0.5.200", EXFIL_DST, 60, port=21))

    assert finding.severity is Severity.HIGH
    assert finding.dst_port == 21


def test_when_many_hosts_send_as_much_nothing_stands_out() -> None:
    backups = [flow for n in range(8) for flow in upload(f"10.0.6.{n + 1}", "52.95.110.1", 60 + n)]

    assert detect_outbound_volume(baseline(4) + backups) == []


def test_with_fewer_than_five_hosts_only_the_absolute_threshold_applies() -> None:
    flows = upload("10.0.5.1", EXFIL_DST, 55) + upload("10.0.5.2", "198.51.100.80", 54)

    findings = detect_outbound_volume(flows)

    assert len(findings) == 2
    assert all(f.metrics["robust_z_applied"] is False for f in findings)


def test_inbound_internal_and_rejected_traffic_do_not_count() -> None:
    inbound = upload(EXFIL_DST, "10.0.5.200", 300)
    internal = upload("10.0.5.200", "10.0.7.7", 300)
    rejected = upload("10.0.5.200", EXFIL_DST, 300, action="REJECT")

    assert detect_outbound_volume(baseline() + inbound + internal + rejected) == []


def test_a_web_server_answering_a_large_download_is_not_exfiltration() -> None:
    response = [
        make_flow(
            "10.0.2.10",
            "203.0.113.77",
            51_000,
            at=n * 30.0,
            duration=25.0,
            src_port=443,
            packets=9_000,
            bytes=12 * MB,
            action="ACCEPT",
        )
        for n in range(10)
    ]

    assert detect_outbound_volume(baseline() + response) == []
