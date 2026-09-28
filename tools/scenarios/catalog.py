"""The generated suite: attack families that must be found, and near misses that must not.
Every scenario runs on top of an hour of benign background traffic."""

from __future__ import annotations

from collections.abc import Callable

from tools.scenarios.traffic import HOUR, UDP, WEB_SERVER, Label, Scenario, Traffic

type Family = Callable[[Traffic], None]


# Attacks ----------------------------------------------------------------------------------


def external_vertical_scan(t: Traffic) -> None:
    attacker, target = t.external_ip(), t.rng.choice([WEB_SERVER, *t.internal_hosts])
    t.vertical_scan(
        attacker,
        target,
        t.rng.randint(150, 1_500),
        at=t.rng.randrange(HOUR - 300),
        seconds=t.rng.randint(30, 240),
    )
    t.labels.append(Label("port_scan", attacker, dst_ip=target))


def internal_horizontal_scan(t: Traffic) -> None:
    compromised = t.rng.choice(t.internal_hosts)
    hosts = [f"10.20.{3 + n // 250}.{1 + n % 250}" for n in range(t.rng.randint(60, 200))]
    port = t.rng.choice([445, 3306, 5985, 6379])
    t.horizontal_scan(
        compromised, hosts, port, at=t.rng.randrange(HOUR - 300), seconds=t.rng.randint(20, 240)
    )
    t.labels.append(Label("port_scan", compromised, dst_port=port))


def scan_across_a_window_boundary(t: Traffic) -> None:
    attacker, target = t.external_ip(), t.rng.choice(t.internal_hosts)
    boundary = 300 * t.rng.randint(1, 10)
    t.vertical_scan(attacker, target, t.rng.randint(110, 140), at=boundary - 100, seconds=190)
    t.labels.append(Label("port_scan", attacker, dst_ip=target))


def udp_scan(t: Traffic) -> None:
    attacker, target = t.external_ip(), t.rng.choice(t.internal_hosts)
    t.vertical_scan(
        attacker,
        target,
        t.rng.randint(120, 400),
        at=t.rng.randrange(HOUR - 300),
        seconds=t.rng.randint(60, 240),
        protocol=UDP,
    )
    t.labels.append(Label("port_scan", attacker, dst_ip=target))


def ssh_brute_force(t: Traffic) -> None:
    attacker, target = t.external_ip(), t.rng.choice([WEB_SERVER, *t.internal_hosts])
    t.login_attempts(
        attacker,
        target,
        22,
        t.rng.randint(40, 300),
        at=t.rng.randrange(HOUR - 300),
        seconds=t.rng.randint(60, 240),
    )
    t.labels.append(Label("remote_access_bruteforce", attacker, dst_ip=target, dst_port=22))


def brute_force_then_login(t: Traffic) -> None:
    attacker, target = t.external_ip(), t.rng.choice(t.internal_hosts)
    port = t.rng.choice([22, 3389])
    start = t.rng.randrange(HOUR - 1_200)
    t.login_attempts(attacker, target, port, t.rng.randint(40, 120), at=start, seconds=200)
    t.login_session(attacker, target, port, at=start + 200 + t.rng.randint(10, 1_200))
    t.labels.append(Label("remote_access_bruteforce", attacker, dst_ip=target, dst_port=port))


def rdp_password_spraying(t: Traffic) -> None:
    attacker = t.external_ip()
    start = t.rng.randrange(HOUR - 300)
    targets = t.rng.sample(t.internal_hosts, min(len(t.internal_hosts), t.rng.randint(12, 30)))
    for n, host in enumerate(targets):
        t.login_attempts(attacker, host, 3389, t.rng.randint(1, 3), at=start + n * 5, seconds=5)
    t.labels.append(Label("remote_access_bruteforce", attacker, dst_port=3389))


def exfiltration_over_https(t: Traffic) -> None:
    source, destination = t.rng.choice(t.internal_hosts), t.external_ip()
    t.upload(source, destination, t.rng.uniform(80, 700), at=t.rng.randrange(HOUR - 1_200))
    t.labels.append(Label("outbound_volume", source, dst_ip=destination))


def exfiltration_over_another_port(t: Traffic) -> None:
    source, destination = t.rng.choice(t.internal_hosts), t.external_ip()
    t.upload(
        source,
        destination,
        t.rng.uniform(55, 150),
        at=t.rng.randrange(HOUR - 1_200),
        port=t.rng.choice([21, 22, 8080, 53]),
    )
    t.labels.append(Label("outbound_volume", source, dst_ip=destination))


def everything_at_once(t: Traffic) -> None:
    external_vertical_scan(t)
    ssh_brute_force(t)
    exfiltration_over_https(t)


# Near misses: close to a rule, and must stay quiet ----------------------------------------


def slow_scan(t: Traffic) -> None:
    t.vertical_scan(
        t.external_ip(),
        t.rng.choice(t.internal_hosts),
        t.rng.randint(300, 600),
        at=0,
        seconds=HOUR - 60,
    )


def partial_scan(t: Traffic) -> None:
    t.vertical_scan(
        t.external_ip(),
        t.rng.choice(t.internal_hosts),
        t.rng.randint(60, 95),
        at=t.rng.randrange(HOUR - 300),
        seconds=120,
    )


def completed_service_checks(t: Traffic) -> None:
    t.vertical_scan(
        t.rng.choice(t.internal_hosts),
        t.rng.choice(t.internal_hosts),
        150,
        at=t.rng.randrange(HOUR - 300),
        seconds=200,
        completed=True,
    )


def a_few_failed_logins(t: Traffic) -> None:
    t.login_attempts(
        t.external_ip(),
        t.rng.choice(t.internal_hosts),
        22,
        t.rng.randint(10, 28),
        at=t.rng.randrange(HOUR - 300),
        seconds=240,
    )


def spraying_nine_hosts(t: Traffic) -> None:
    attacker, start = t.external_ip(), t.rng.randrange(HOUR - 300)
    for n, host in enumerate(t.rng.sample(t.internal_hosts, 9)):
        t.login_attempts(attacker, host, 3389, 2, at=start + n * 10, seconds=5)


def upload_below_the_threshold(t: Traffic) -> None:
    t.upload(
        t.rng.choice(t.internal_hosts),
        t.external_ip(),
        t.rng.uniform(30, 48),
        at=t.rng.randrange(HOUR - 1_200),
    )


def large_download(t: Traffic) -> None:
    t.upload(
        t.external_ip(),
        t.rng.choice(t.internal_hosts),
        t.rng.uniform(200, 600),
        at=t.rng.randrange(HOUR - 1_200),
    )


FAMILIES: dict[str, Family] = {
    family.__name__: family
    for family in (
        external_vertical_scan,
        internal_horizontal_scan,
        scan_across_a_window_boundary,
        udp_scan,
        ssh_brute_force,
        brute_force_then_login,
        rdp_password_spraying,
        exfiltration_over_https,
        exfiltration_over_another_port,
        everything_at_once,
        slow_scan,
        partial_scan,
        completed_service_checks,
        a_few_failed_logins,
        spraying_nine_hosts,
        upload_below_the_threshold,
        large_download,
    )
}


def build(name: str, seed: int) -> Scenario:
    traffic = Traffic(seed)
    traffic.background()
    FAMILIES[name](traffic)
    return traffic.scenario(name)


def suite(seeds: int) -> list[Scenario]:
    """Every family with seeds 1..`seeds`."""
    return [build(name, seed) for name in FAMILIES for seed in range(1, seeds + 1)]
