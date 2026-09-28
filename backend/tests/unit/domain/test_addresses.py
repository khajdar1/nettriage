from ipaddress import ip_address

import pytest

from nettriage.domain.addresses import is_internal


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "10.255.255.255",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.1.10",
        "100.64.0.1",
        "100.127.255.255",
        "fd12:3456:789a::1",
        "fc00::1",
        "fe80::1",
    ],
)
def test_internal_ranges(address: str) -> None:
    assert is_internal(ip_address(address))


@pytest.mark.parametrize(
    "address",
    [
        "8.8.8.8",
        "172.15.255.255",
        "172.32.0.1",
        "100.63.255.255",
        "100.128.0.0",
        "192.169.0.1",
        "2001:db8::1",
        "127.0.0.1",
        "169.254.169.254",
    ],
)
def test_everything_else_is_external(address: str) -> None:
    assert not is_internal(ip_address(address))
