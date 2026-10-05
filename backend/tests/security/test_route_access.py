"""Deny by default (spec §6.4, OWASP API5): every route declares who may call it."""

from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient

from nettriage.entrypoints.api.access import Access, OrgMember, Public, SignedIn

# Every API route and its declared access. A new route fails the test until it is added here.
EXPECTED = {
    ("GET", "/api/health"): "public.ip",
    ("GET", "/api/auth/login"): "auth.ip",
    ("GET", "/api/auth/callback"): "auth.ip",
    ("POST", "/api/auth/logout"): "signed in",
    ("POST", "/api/auth/logout-all"): "signed in",
    ("GET", "/api/v1/me"): "signed in",
    ("POST", "/api/v1/orgs"): "signed in",
    ("GET", "/api/v1/orgs/{org_id}"): "org:read",
    ("PATCH", "/api/v1/orgs/{org_id}"): "org:update",
    ("DELETE", "/api/v1/orgs/{org_id}"): "org:delete",
    ("GET", "/api/v1/orgs/{org_id}/audit-log"): "audit:read",
    ("GET", "/api/v1/orgs/{org_id}/members"): "members:read",
    ("PATCH", "/api/v1/orgs/{org_id}/members/{user_id}"): "members:role",
    ("DELETE", "/api/v1/orgs/{org_id}/members/{user_id}"): "members:remove or self",
    ("GET", "/api/v1/orgs/{org_id}/invitations"): "members:invite",
    ("POST", "/api/v1/orgs/{org_id}/invitations"): "members:invite",
    ("DELETE", "/api/v1/orgs/{org_id}/invitations/{invitation_id}"): "members:invite",
    ("POST", "/api/v1/invitations/accept"): "signed in",
    ("POST", "/api/v1/orgs/{org_id}/uploads"): "uploads:create",
    ("GET", "/api/v1/orgs/{org_id}/uploads"): "uploads:read",
    ("GET", "/api/v1/orgs/{org_id}/uploads/{upload_id}"): "uploads:read",
    ("GET", "/api/v1/orgs/{org_id}/findings"): "findings:read",
    ("GET", "/api/v1/orgs/{org_id}/findings/{finding_id}"): "findings:read",
    ("PATCH", "/api/v1/orgs/{org_id}/findings/{finding_id}"): "findings:triage",
    ("POST", "/api/v1/orgs/{org_id}/findings/{finding_id}/comments"): "findings:comment",
    ("POST", "/api/v1/orgs/{org_id}/findings/{finding_id}/ai-analyses"): "ai:request",
    (
        "PUT",
        "/api/v1/orgs/{org_id}/findings/{finding_id}/ai-analyses/{analysis_id}/feedback",
    ): "ai:feedback",
    ("GET", "/api/v1/orgs/{org_id}/usage"): "usage:read",
    ("GET", "/api/v1/orgs/{org_id}/overview"): "findings:read",
    ("GET", "/api/v1/attack-techniques/{technique_id}"): "signed in",
}
# FastAPI's interactive docs, which only `local` and `dev` serve (spec §7).
DOCS: set[str | None] = {"/api/docs", "/api/openapi.json"}


def declared_access(route: APIRoute) -> list[Access]:
    found: list[Access] = []
    pending = [route.dependant]
    while pending:
        dependant = pending.pop()
        if isinstance(dependant.call, Access):
            found.append(dependant.call)
        pending.extend(dependant.dependencies)
    return found


def most_specific(rules: list[Access]) -> set[str]:
    """An org rule includes a session (OrgMember depends on SignedIn), so it speaks for both."""
    org_rules = {rule for rule in rules if isinstance(rule, OrgMember)}
    if org_rules:
        return {r.permission + (" or self" if r.or_self else "") for r in org_rules}
    if any(isinstance(rule, SignedIn) for rule in rules):
        return {"signed in"}
    return {rule.policy.name for rule in rules if isinstance(rule, Public)}


def test_every_route_declares_exactly_one_access_rule(client: TestClient) -> None:
    app: FastAPI = client.app  # type: ignore[assignment]
    actual: dict[tuple[str, str], str] = {}
    others: set[str | None] = set()
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if not isinstance(route, APIRoute):
            others.add(context.path)
            continue
        access = most_specific(declared_access(route))
        assert len(access) == 1, f"{context.path} declares {access or 'no access rule'}"
        for method in context.methods or ():
            actual[(method, str(context.path))] = next(iter(access))

    assert actual == EXPECTED
    assert others <= DOCS


def test_no_delete_route_takes_a_body(client: TestClient) -> None:
    """CloudFront signs every request to the Lambda function URL, and a DELETE that carries a
    body fails that signature (spec §7), so a DELETE takes its inputs from the path and query."""
    app: FastAPI = client.app  # type: ignore[assignment]
    with_body = [
        str(context.path)
        for context in iter_route_contexts(app.routes)
        if isinstance(context.original_route, APIRoute)
        and "DELETE" in (context.methods or ())
        and context.original_route.body_field is not None
    ]

    assert with_body == []
