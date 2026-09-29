from collections.abc import Callable

import httpx

from tools.smoke import run_checks

SECURITY_HEADERS = {
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "content-security-policy": (
        "default-src 'self'; connect-src 'self' "
        "https://nettriage-dev-uploads-1a2b3c4d.s3.eu-north-1.amazonaws.com; "
        "frame-ancestors 'none'"
    ),
    "x-content-type-options": "nosniff",
}


COGNITO = "https://nettriage-dev-1a2b3c4d.auth.eu-north-1.amazoncognito.com"
AUTHORIZE = f"{COGNITO}/oauth2/authorize?client_id=abc&code_challenge_method=S256&state=s"


def uploads_bucket(request: httpx.Request) -> httpx.Response:
    if request.method == "OPTIONS" and request.headers.get("origin") == "https://cdn.example":
        return httpx.Response(200, headers={"access-control-allow-origin": "https://cdn.example"})
    return httpx.Response(403, content=b"<Error><Code>AccessDenied</Code></Error>")


def healthy(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.url.host.endswith(".s3.eu-north-1.amazonaws.com"):
        return uploads_bucket(request)
    if request.url.host == "fn.example":
        return httpx.Response(403, json={"Message": "Forbidden"})
    if request.url.host.endswith(".amazoncognito.com"):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>")
    if path == "/api/health":
        headers = {**SECURITY_HEADERS, "ratelimit-policy": '"public.ip";q=60;w=60'}
        return httpx.Response(200, json={"status": "ok", "version": "abc"}, headers=headers)
    if path == "/api/auth/login":
        return httpx.Response(302, headers={"location": AUTHORIZE})
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


def test_edge_401_without_problem_details_is_caught() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/me":
            return httpx.Response(401, headers={"content-type": "text/html"}, content=b"<html>")
        return healthy(request)

    assert checks_for(broken)["edge rejects api call without session"] is False


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


def test_a_login_that_does_not_reach_cognito_is_caught() -> None:
    def local(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(302, headers={"location": "/?sign_in=unavailable"})
        return healthy(request)

    results = checks_for(local)

    assert results["sign-in redirects to Cognito"] is False
    assert results["Cognito sign-in page loads"] is False


def test_a_login_without_pkce_is_caught() -> None:
    def no_pkce(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(302, headers={"location": f"{COGNITO}/oauth2/authorize?client_id=abc"})
        return healthy(request)

    assert checks_for(no_pkce)["sign-in redirects to Cognito"] is False


def test_a_broken_cognito_page_is_caught() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.host.endswith(".amazoncognito.com"):
            return httpx.Response(400, content=b"Login pages unavailable")
        return healthy(request)

    assert checks_for(broken)["Cognito sign-in page loads"] is False


def test_an_api_that_cannot_see_the_viewer_ip_is_caught() -> None:
    def no_viewer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"status": "ok", "version": "abc"}, headers=SECURITY_HEADERS)
        return healthy(request)

    assert checks_for(no_viewer)["api limits requests per viewer ip"] is False


def test_a_csp_without_the_uploads_bucket_is_caught() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        response = healthy(request)
        if "content-security-policy" in response.headers:
            response.headers["content-security-policy"] = "default-src 'self'; connect-src 'self'"
        return response

    assert checks_for(broken)["csp allows the uploads bucket"] is False


def test_a_bucket_open_to_any_origin_is_caught() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        if request.method == "OPTIONS":
            origin = request.headers.get("origin", "")
            return httpx.Response(200, headers={"access-control-allow-origin": origin})
        return healthy(request)

    results = checks_for(broken)
    assert results["uploads bucket lets the app PUT"] is True
    assert results["uploads bucket refuses other origins"] is False


def test_a_bucket_that_takes_anonymous_writes_is_caught() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200)
        return healthy(request)

    assert checks_for(broken)["uploads bucket refuses anonymous writes"] is False
