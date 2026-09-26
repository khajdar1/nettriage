"""Post-deploy smoke checks for a NetTriage stage.

Usage: python tools/smoke.py --base-url https://dxxxx.cloudfront.net \
         --function-url https://xxxx.lambda-url.us-east-1.on.aws/ --version <git sha>
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass

import httpx

REQUIRED_HEADERS = {
    "strict-transport-security": "max-age=31536000",
    "content-security-policy": "frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def _missing_headers(response: httpx.Response) -> str:
    missing = [
        name for name, needle in REQUIRED_HEADERS.items()
        if needle not in response.headers.get(name, "")
    ]
    return ", ".join(missing)


def _is_html(response: httpx.Response) -> bool:
    return response.status_code == 200 and "text/html" in response.headers.get("content-type", "")


def _is_problem(response: httpx.Response, status: int) -> bool:
    content_type = response.headers.get("content-type", "")
    return response.status_code == status and content_type.startswith("application/problem+json")


def run_checks(client: httpx.Client, base_url: str, function_url: str, version: str) -> list[Check]:
    health = client.get(f"{base_url}/api/health")
    body = health.json() if "json" in health.headers.get("content-type", "") else {}
    root = client.get(f"{base_url}/")
    spa_route = client.get(f"{base_url}/app/some/route")
    no_session = client.get(f"{base_url}/api/v1/me")
    not_found = client.get(
        f"{base_url}/api/does-not-exist", headers={"Cookie": "__Host-session=smoke"}
    )
    direct = client.get(f"{function_url.rstrip('/')}/api/health")
    return [
        Check("api health", health.status_code == 200 and body == {"status": "ok", "version": version},
              f"{health.status_code} {body}"),
        Check("api security headers", not _missing_headers(health), _missing_headers(health)),
        Check("web root", _is_html(root), str(root.status_code)),
        Check("web security headers", not _missing_headers(root), _missing_headers(root)),
        Check("spa route serves index.html", _is_html(spa_route), str(spa_route.status_code)),
        Check("edge rejects api call without session", _is_problem(no_session, 401),
              f"{no_session.status_code} {no_session.headers.get('content-type')}"),
        Check("api 404 stays problem+json", _is_problem(not_found, 404),
              f"{not_found.status_code} {not_found.headers.get('content-type')}"),
        Check("function url rejects direct calls", direct.status_code == 403,
              str(direct.status_code)),
    ]


def wait_for_version(
    client: httpx.Client, base_url: str, version: str, attempts: int = 30, delay: float = 10.0
) -> bool:
    for _ in range(attempts):
        try:
            response = client.get(f"{base_url}/api/health")
            if response.status_code == 200 and response.json().get("version") == version:
                return True
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(delay)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--function-url", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    base_url = args.base_url.rstrip("/")
    with httpx.Client(timeout=20.0) as client:
        if not wait_for_version(client, base_url, args.version):
            print(f"FAIL: {base_url}/api/health never reported version {args.version}")
            return 1
        checks = run_checks(client, base_url, args.function_url, args.version)
    for check in checks:
        print(f"{'PASS' if check.ok else 'FAIL'}  {check.name}  {check.detail}")
    return 0 if all(check.ok for check in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
