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
