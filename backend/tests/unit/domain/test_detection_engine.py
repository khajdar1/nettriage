import io
import random
import time

from flowmaker import make_flow

from nettriage.domain.detection.engine import DETECTORS, detect
from nettriage.domain.detection.model import Severity
from nettriage.domain.flows import NetworkFlow
from nettriage.domain.parsing.vpc_flow_logs import parse_flow_log


def scan(src: str, ports: int) -> list[NetworkFlow]:
    return [make_flow(src, "10.0.1.20", 1 + n, at=n) for n in range(ports)]


def test_every_detector_is_registered_with_reference_data() -> None:
    assert [d.id for d in DETECTORS] == ["port_scan", "remote_access_bruteforce", "outbound_volume"]
    assert all(d.version == 1 and d.name and d.description for d in DETECTORS)
    assert "T1046" in DETECTORS[0].candidate_techniques


def test_findings_are_capped_at_fifty_highest_severity_first() -> None:
    external = [flow for n in range(60) for flow in scan(f"198.51.100.{n + 1}", 110)]
    internal = scan("10.0.9.9", 110)

    result = detect(external + internal)

    assert len(result.findings) == 50
    assert result.truncated == 11
    assert result.findings[0].severity is Severity.HIGH
    assert str(result.findings[0].src_ip) == "10.0.9.9"


def test_the_same_file_always_gives_the_same_findings() -> None:
    lines = [
        f"2 123456789012 eni-1 198.51.100.7 10.0.1.20 40000 {port} 6 1 60 "
        f"{1_790_000_000 + port} {1_790_000_001 + port} REJECT OK"
        for port in range(1, 200)
    ]
    data = ("\n".join(lines) + "\n").encode()

    first = detect(parse_flow_log(io.BytesIO(data)).flows)
    second = detect(parse_flow_log(io.BytesIO(data)).flows)

    assert first == second
    assert [f.fingerprint for f in first.findings] == [f.fingerprint for f in second.findings]


def test_a_hundred_thousand_flows_are_detected_in_seconds() -> None:
    rng = random.Random(7)
    flows = [
        make_flow(
            f"10.0.{rng.randrange(4)}.{rng.randrange(1, 250)}",
            f"198.51.{rng.randrange(100)}.{rng.randrange(1, 250)}",
            rng.choice([443, 443, 80, 53, 22, 3389, 5432]),
            at=rng.uniform(0, 3600),
            packets=rng.randrange(1, 50),
            bytes=rng.randrange(60, 200_000),
            action=rng.choice(["ACCEPT", "ACCEPT", "REJECT"]),
        )
        for _ in range(100_000)
    ]

    started = time.perf_counter()
    detect(flows)

    assert time.perf_counter() - started < 15
