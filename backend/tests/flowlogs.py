"""Small VPC Flow Logs files for tests: AWS's default v2 format, no header."""

START = 1_790_596_800  # 2026-09-28 12:00:00 UTC


def port_scan(ports: int = 150, source: str = "203.0.113.9") -> bytes:
    """An external host probing `ports` ports on one internal host within a minute, all
    rejected: one `port_scan` finding."""
    lines = [
        f"2 123456789012 eni-1 {source} 10.0.0.5 40000 {port} 6 1 40 "
        f"{START + port % 60} {START + port % 60} REJECT OK"
        for port in range(1, ports + 1)
    ]
    return ("\n".join(lines) + "\n").encode()


def quiet(lines: int = 3) -> bytes:
    """An interface with no traffic: NODATA records only, so no flows and no findings."""
    return f"2 123456789012 eni-1 - - - - - - - {START} {START + 60} - NODATA\n".encode() * lines


def busy_network(rows: int) -> bytes:
    """Varied traffic, as a large real file has: 500 sources (half internal) talking to 200
    internal hosts on many ports over an hour, accepted and rejected."""
    lines = []
    for n in range(rows):
        source = f"10.1.{n % 250 // 50}.{n % 50 + 1}" if n % 2 else f"198.51.100.{n % 250 + 1}"
        target = f"10.0.{n % 200 // 100}.{n % 100 + 1}"
        port = (n * 7919) % 65536
        action = "REJECT" if n % 3 == 0 else "ACCEPT"
        start = START + n % 3600
        lines.append(
            f"2 123456789012 eni-1 {source} {target} {40000 + n % 20000} {port} "
            f"{6 if n % 5 else 17} {n % 40 + 1} {(n % 40 + 1) * 120} {start} {start + 5} "
            f"{action} OK"
        )
    return ("\n".join(lines) + "\n").encode()
