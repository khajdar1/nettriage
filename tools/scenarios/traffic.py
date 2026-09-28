"""Building blocks for synthetic VPC Flow Logs: one `Traffic` per scenario, seeded so the same
seed always writes the same file."""

from __future__ import annotations

import random
import zlib
from dataclasses import dataclass, field

# 2026-09-01T12:00:00Z; every scenario covers the hour after it.
EPOCH = 1_788_264_000
HOUR = 3_600
ACCOUNT = "123456789012"
HEADER = (
    "version account-id interface-id srcaddr dstaddr srcport dstport protocol packets bytes "
    "start end action log-status"
)
TCP = 6
UDP = 17
MB = 1024 * 1024
RESOLVER = "10.20.0.2"
WEB_SERVER = "10.20.2.10"
BACKUP_SERVICE = "52.95.110.1"


@dataclass(frozen=True)
class Label:
    """An attack the suite expects a finding for. `None` fields match anything."""

    detector_id: str
    src_ip: str
    dst_ip: str | None = None
    dst_port: int | None = None


@dataclass
class Scenario:
    name: str
    seed: int
    records: list[str]
    labels: list[Label]

    def text(self) -> str:
        return "\n".join([HEADER, *self.records]) + "\n"


@dataclass
class Traffic:
    """Collects one scenario's flow records; `rng` drives every random choice."""

    seed: int
    rng: random.Random = field(init=False)
    internal_hosts: list[str] = field(init=False)
    labels: list[Label] = field(default_factory=list)
    _records: list[tuple[int, int, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        count = self.rng.randint(15, 35)
        self.internal_hosts = [f"10.20.1.{n}" for n in range(10, 10 + count)]

    def flow(
        self,
        src: str,
        dst: str,
        src_port: int,
        dst_port: int,
        *,
        at: int,
        protocol: int = TCP,
        packets: int = 1,
        bytes_: int = 60,
        duration: int = 1,
        action: str = "ACCEPT",
    ) -> None:
        interface = f"eni-{zlib.crc32(src.encode()):08x}"
        record = (
            f"2 {ACCOUNT} {interface} {src} {dst} {src_port} {dst_port} {protocol} {packets} "
            f"{bytes_} {EPOCH + at} {EPOCH + at + duration} {action} OK"
        )
        self._records.append((at, len(self._records), record))

    def external_ip(self) -> str:
        """A random public address, never inside the internal ranges."""
        first = self.rng.choice([3, 13, 18, 23, 34, 44, 51, 54, 81, 91, 104, 142, 185, 203])
        second, third = self.rng.randrange(256), self.rng.randrange(256)
        return f"{first}.{second}.{third}.{self.rng.randrange(1, 255)}"

    def ephemeral_port(self) -> int:
        return self.rng.randint(32_768, 60_999)

    def scenario(self, name: str) -> Scenario:
        ordered = [record for _, _, record in sorted(self._records)]
        return Scenario(name=name, seed=self.seed, records=ordered, labels=list(self.labels))

    # Benign background ------------------------------------------------------------------

    def background(self) -> None:
        """An hour of ordinary traffic that must never produce a finding."""
        rng = self.rng
        for host in self.internal_hosts:
            for _ in range(rng.randint(20, 60)):
                self._web_request(host, self.external_ip(), at=rng.randrange(HOUR))
            for _ in range(rng.randint(10, 40)):
                at = rng.randrange(HOUR)
                port = self.ephemeral_port()
                self.flow(host, RESOLVER, port, 53, at=at, protocol=UDP, bytes_=70)
                self.flow(RESOLVER, host, 53, port, at=at, protocol=UDP, bytes_=180)
        for _ in range(rng.randint(150, 400)):
            self._web_request(self.external_ip(), WEB_SERVER, at=rng.randrange(HOUR))
        for _ in range(rng.randint(30, 80)):
            # Internet background noise: a few probes per address, all refused.
            prober = self.external_ip()
            at = rng.randrange(HOUR)
            for port in rng.sample([22, 23, 80, 443, 445, 3389, 8080], rng.randint(1, 4)):
                self.flow(prober, WEB_SERVER, self.ephemeral_port(), port, at=at, action="REJECT")
        admin = self.internal_hosts[0]
        for server in rng.sample(self.internal_hosts[1:], 5):
            at = rng.randrange(HOUR - 900)
            self.flow(
                admin,
                server,
                self.ephemeral_port(),
                22,
                at=at,
                packets=rng.randint(200, 900),
                bytes_=rng.randint(40_000, 900_000),
                duration=rng.randint(120, 900),
            )
        monitor = self.internal_hosts[1]
        for server in self.internal_hosts[2:10]:
            for minute in range(0, HOUR, 60):
                self.flow(
                    monitor,
                    server,
                    self.ephemeral_port(),
                    22,
                    at=minute + rng.randrange(5),
                    packets=6,
                    bytes_=900,
                )
        for host in rng.sample(self.internal_hosts, 3):
            self.upload(host, BACKUP_SERVICE, rng.uniform(15, 40), at=rng.randrange(HOUR - 1_200))

    def _web_request(self, client: str, server: str, *, at: int) -> None:
        rng = self.rng
        port = self.ephemeral_port()
        response = rng.randint(2_000, 2 * MB)
        self.flow(
            client,
            server,
            port,
            443,
            at=at,
            packets=rng.randint(4, 20),
            bytes_=rng.randint(500, 4_000),
            duration=rng.randint(1, 20),
        )
        self.flow(
            server,
            client,
            443,
            port,
            at=at,
            packets=response // 1_400 + 2,
            bytes_=response,
            duration=rng.randint(1, 20),
        )

    # Attacks and near misses --------------------------------------------------------------

    def vertical_scan(
        self,
        src: str,
        dst: str,
        ports: int,
        *,
        at: int,
        seconds: int,
        reject_percent: int = 95,
        protocol: int = TCP,
        completed: bool = False,
    ) -> None:
        for n, port in enumerate(sorted(self.rng.sample(range(1, 65_536), ports))):
            self._probe(
                src,
                dst,
                port,
                at=at + n * seconds // ports,
                protocol=protocol,
                reject_percent=reject_percent,
                completed=completed,
            )

    def horizontal_scan(
        self,
        src: str,
        hosts: list[str],
        port: int,
        *,
        at: int,
        seconds: int,
    ) -> None:
        for n, host in enumerate(hosts):
            self._probe(
                src,
                host,
                port,
                at=at + n * seconds // len(hosts),
                protocol=TCP,
                reject_percent=90,
                completed=False,
            )

    def _probe(
        self,
        src: str,
        dst: str,
        port: int,
        *,
        at: int,
        protocol: int,
        reject_percent: int,
        completed: bool,
    ) -> None:
        if completed:
            self.flow(
                src,
                dst,
                self.ephemeral_port(),
                port,
                at=at,
                protocol=protocol,
                packets=self.rng.randint(20, 60),
                bytes_=self.rng.randint(4_000, 40_000),
            )
        elif self.rng.randrange(100) < reject_percent:
            self.flow(
                src, dst, self.ephemeral_port(), port, at=at, protocol=protocol, action="REJECT"
            )
        else:
            self.flow(
                src,
                dst,
                self.ephemeral_port(),
                port,
                at=at,
                protocol=protocol,
                packets=self.rng.randint(1, 3),
                bytes_=120,
            )

    def login_attempts(
        self, src: str, dst: str, port: int, attempts: int, *, at: int, seconds: int
    ) -> None:
        for n in range(attempts):
            self.flow(
                src,
                dst,
                self.ephemeral_port(),
                port,
                at=at + n * seconds // attempts,
                packets=self.rng.randint(8, 20),
                bytes_=self.rng.randint(1_500, 5_000),
                duration=self.rng.randint(1, 4),
            )

    def login_session(self, src: str, dst: str, port: int, *, at: int) -> None:
        self.flow(
            src,
            dst,
            self.ephemeral_port(),
            port,
            at=at,
            packets=self.rng.randint(300, 2_000),
            bytes_=self.rng.randint(150_000, 3 * MB),
            duration=self.rng.randint(60, 900),
        )

    def upload(self, src: str, dst: str, megabytes: float, *, at: int, port: int = 443) -> None:
        chunks = self.rng.randint(8, 30)
        size = int(megabytes * MB / chunks)
        for n in range(chunks):
            self.flow(
                src,
                dst,
                self.ephemeral_port(),
                port,
                at=at + n * 40,
                packets=size // 1_400 + 1,
                bytes_=size,
                duration=35,
            )
