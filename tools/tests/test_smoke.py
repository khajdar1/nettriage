from collections.abc import Callable

import httpx

from tools.smoke import run_checks

SECURITY_HEADERS = {
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "content-security-policy": "default-src 'self'; frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
}


def healthy(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.url.host == "fn.example":
        return httpx.Response(403, json={"Message": "Forbidden"})
    if path == "/api/health":
        return httpx.Response(200, json={"status": "ok", "version": "abc"}, headers=SECURITY_HEADERS)
    problem = {**SECURITY_HEADERS, "content-type": "application/problem+json"}
    if path == "/api/v1/me":
        return httpx.Response(401, headers=problem)
    if path.startswith("/api/"):
        return httpx.Response(404, headers=problem, content=b"{}")
    html = {**SECURITY_HEADERS, "content-type": "text/html"}
    return httpx.Response(200, headers=html, content=b"<!doctype html>")


def checks_for(handler: Callable[[httpx.Request], httpx.Response]) -> dict[str, bool]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return {c.name: c.ok for c in run_checks(client, "https://cdn.example", "https://fn.example/", "abc")}


def test_a_healthy_deployment_passes_every_check() -> None:
    results = checks_for(healthy)

    assert results
    assert all(results.values()), results


def test_spa_fallback_swallowing_api_errors_is_caught() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/does-not-exist":
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>")
        return healthy(request)

    assert checks_for(broken)["api 404 stays problem+json"] is False


def test_function_url_reachable_directly_is_caught() -> None:
    def open_url(request: httpx.Request) -> httpx.Response:
        if request.url.host == "fn.example":
            return httpx.Response(200, json={"status": "ok"})
        return healthy(request)

    assert checks_for(open_url)["function url rejects direct calls"] is False


def test_missing_security_headers_are_caught() -> None:
    def bare(request: httpx.Request) -> httpx.Response:
        response = healthy(request)
        if request.url.path == "/":
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>")
        return response

    assert checks_for(bare)["web security headers"] is False


def test_wrong_deployed_version_is_caught() -> None:
    def old(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"status": "ok", "version": "old"}, headers=SECURITY_HEADERS)
        return healthy(request)

    assert checks_for(old)["api health"] is False
