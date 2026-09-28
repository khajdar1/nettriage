import io
from ipaddress import ip_address

import pytest
from nettriage.domain.addresses import is_internal
from nettriage.domain.detection.engine import detect
from nettriage.domain.flows import NetworkFlow
from nettriage.domain.parsing.vpc_flow_logs import parse_flow_log

from tools.scenarios.catalog import FAMILIES, build
from tools.scenarios.traffic import Traffic

ATTACK_FAMILIES = {
    "external_vertical_scan",
    "internal_horizontal_scan",
    "scan_across_a_window_boundary",
    "udp_scan",
    "ssh_brute_force",
    "brute_force_then_login",
    "rdp_password_spraying",
    "exfiltration_over_https",
    "exfiltration_over_another_port",
    "everything_at_once",
}


def flows_of(text: str) -> list[NetworkFlow]:
    return parse_flow_log(io.BytesIO(text.encode())).flows


def test_the_same_seed_always_writes_the_same_file() -> None:
    assert build("udp_scan", 4).text() == build("udp_scan", 4).text()
    assert build("udp_scan", 4).text() != build("udp_scan", 5).text()


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_every_scenario_is_a_clean_flow_log(family: str) -> None:
    result = parse_flow_log(io.BytesIO(build(family, 1).text().encode()))

    assert result.has_header
    assert result.rows_rejected == 0
    assert result.rows_parsed > 1_000


def test_attack_families_are_labeled_and_near_misses_are_not() -> None:
    for family in FAMILIES:
        assert bool(build(family, 1).labels) is (family in ATTACK_FAMILIES), family


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_background_traffic_alone_produces_no_findings(seed: int) -> None:
    traffic = Traffic(seed)
    traffic.background()

    assert detect(flows_of(traffic.scenario("background").text())).findings == []


def test_external_addresses_are_never_internal() -> None:
    traffic = Traffic(9)

    assert not any(is_internal(ip_address(traffic.external_ip())) for _ in range(2_000))
