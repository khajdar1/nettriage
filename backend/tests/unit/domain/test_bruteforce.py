import time
from ipaddress import ip_address

from flowmaker import make_flow

from nettriage.domain.detection.bruteforce import detect_bruteforce
from nettriage.domain.detection.model import Severity
from nettriage.domain.flows import NetworkFlow

ATTACKER = "198.51.100.7"
SERVER = "10.0.1.20"


def attempts(
    count: int, *, port: int = 22, host: str = SERVER, start: float = 0.0, spacing: float = 5.0
) -> list[NetworkFlow]:
    return [
        make_flow(
            ATTACKER,
            host,
            port,
            at=start + n * spacing,
            src_port=50_000 + n,
            packets=12,
            bytes=2_400,
            action="ACCEPT",
        )
        for n in range(count)
    ]


def session(
    *, at: float, bytes: int = 200_000, duration: float = 30.0, port: int = 22, host: str = SERVER
) -> NetworkFlow:
    return make_flow(
        ATTACKER,
        host,
        port,
        at=at,
        duration=duration,
        src_port=60_000,
        packets=400,
        bytes=bytes,
        action="ACCEPT",
    )


def test_thirty_short_ssh_attempts_in_five_minutes_is_a_medium_finding() -> None:
    [finding] = detect_bruteforce(attempts(30, spacing=9.0))

    assert finding.detector_id == "remote_access_bruteforce"
    assert finding.severity is Severity.MEDIUM
    assert finding.src_ip == ip_address(ATTACKER)
    assert finding.dst_ip == ip_address(SERVER)
    assert finding.dst_port == 22
    assert finding.metrics["variant"] == "single"
    assert finding.metrics["possible_success"] is False
    assert finding.candidate_techniques == ("T1110", "T1110.001")
    assert (
        finding.title
        == f"SSH brute force against {SERVER} from {ATTACKER}: 30 attempts in 5 minutes"
    )


def test_twenty_nine_attempts_is_not_brute_force() -> None:
    assert detect_bruteforce(attempts(29, spacing=9.0)) == []


def test_attempts_spread_over_more_than_five_minutes_do_not_count() -> None:
    assert detect_bruteforce(attempts(40, spacing=11.0)) == []


def test_full_sessions_are_not_attempts() -> None:
    sessions = [session(at=n * 5.0, bytes=5_000, duration=2.0) for n in range(40)]
    big = [
        make_flow(ATTACKER, SERVER, 22, at=n * 5.0, packets=21, action="ACCEPT") for n in range(40)
    ]

    assert detect_bruteforce(sessions + big) == []


def test_a_large_later_session_marks_a_possible_login_as_high() -> None:
    [finding] = detect_bruteforce([*attempts(40), session(at=40 * 5.0 + 60)])

    assert finding.severity is Severity.HIGH
    assert finding.metrics["possible_success"] is True
    assert finding.metrics["success_bytes"] == 200_000
    assert finding.candidate_techniques == ("T1110", "T1110.001", "T1021.004")
    assert finding.title.endswith("(possible successful login)")


def test_a_long_later_session_also_counts() -> None:
    [finding] = detect_bruteforce([*attempts(40), session(at=400.0, bytes=9_000, duration=600.0)])

    assert finding.metrics["possible_success"] is True


def test_a_session_more_than_thirty_minutes_later_does_not_count() -> None:
    last_attempt = 39 * 5.0

    [finding] = detect_bruteforce([*attempts(40), session(at=last_attempt + 30 * 60 + 1)])

    assert finding.severity is Severity.MEDIUM


def test_rdp_uses_its_own_login_technique() -> None:
    [finding] = detect_bruteforce([*attempts(35, port=3389), session(at=300.0, port=3389)])

    assert finding.candidate_techniques == ("T1110", "T1110.001", "T1021.001")
    assert finding.title.startswith("RDP brute force")


def test_spraying_ten_hosts_is_a_spray_finding() -> None:
    flows = [
        attempt
        for n in range(12)
        for attempt in attempts(2, port=3389, host=f"10.0.4.{n + 1}", start=n * 10.0)
    ]

    [finding] = detect_bruteforce(flows)

    assert finding.metrics["variant"] == "spray"
    assert finding.dst_ip is None
    assert finding.dst_port == 3389
    assert finding.metrics["hosts"] == 12
    assert finding.candidate_techniques == ("T1110", "T1110.003")


def test_nine_hosts_is_not_spraying() -> None:
    flows = [attempt for n in range(9) for attempt in attempts(2, host=f"10.0.4.{n + 1}")]

    assert detect_bruteforce(flows) == []


def test_server_replies_from_port_22_are_not_attempts() -> None:
    replies = [
        make_flow(
            SERVER, ATTACKER, 50_000 + n, at=n * 2.0, src_port=22, packets=10, action="ACCEPT"
        )
        for n in range(100)
    ]

    assert detect_bruteforce(replies) == []


def test_the_success_lookup_stays_linear_on_many_bursts() -> None:
    """2,000 separate bursts with 40,000 success-like flows to the same service: the lookup
    must not rescan every success candidate for every burst."""
    bursts = [
        make_flow(ATTACKER, SERVER, 22, at=burst * 3_600.0 + n, packets=12, action="ACCEPT")
        for burst in range(2_000)
        for n in range(30)
    ]
    sessions = [
        make_flow(
            ATTACKER,
            SERVER,
            22,
            at=n * 180.0 + 90,
            duration=2.0,
            packets=400,
            bytes=200_000,
            action="ACCEPT",
        )
        for n in range(40_000)
    ]

    started = time.perf_counter()
    findings = detect_bruteforce(bursts + sessions)

    assert len(findings) == 2_000
    assert time.perf_counter() - started < 1.5
