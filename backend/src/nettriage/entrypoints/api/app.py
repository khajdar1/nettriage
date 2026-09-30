"""FastAPI application factory. Creating an app has no global side effects."""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from nettriage.application.rate_limits import header_values
from nettriage.entrypoints.api.access import RedirectInstead
from nettriage.entrypoints.api.routes import (
    attack_techniques,
    auth,
    findings,
    health,
    invitations,
    me,
    members,
    orgs,
    uploads,
)
from nettriage.entrypoints.api.services import Services
from nettriage.platform.body_limit import BodySizeLimit
from nettriage.platform.config import Settings
from nettriage.platform.errors import register_error_handlers
from nettriage.platform.telemetry import instrument_app


def create_app(
    settings: Settings,
    services: Services,
    *,
    tracer_provider: TracerProvider | None = None,
    meter_provider: MeterProvider | None = None,
) -> FastAPI:
    docs = settings.api_docs_enabled
    app = FastAPI(
        title="NetTriage API",
        version=settings.version,
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        swagger_ui_oauth2_redirect_url=None,
        openapi_url="/api/openapi.json" if docs else None,
    )
    app.state.settings = settings
    app.state.services = services
    register_error_handlers(app)
    app.add_exception_handler(RedirectInstead, redirect_instead)
    # Innermost, so a body counted over the limit reaches FastAPI as the HTTPException it is;
    # the header middleware wraps the request stream in a task group, which would regroup it.
    app.add_middleware(BodySizeLimit)
    app.middleware("http")(add_rate_limit_headers)
    for module in (
        health,
        auth,
        me,
        orgs,
        members,
        invitations,
        uploads,
        findings,
        attack_techniques,
    ):
        app.include_router(module.router, prefix="/api")
    if tracer_provider is not None:
        instrument_app(app, tracer_provider, meter_provider)
    return app


async def add_rate_limit_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """RateLimit-Policy and RateLimit for every limit the request was checked against, on
    every response, redirects and 429s included."""
    response = await call_next(request)
    response.headers.update(header_values(getattr(request.state, "rate_limits", [])))
    return response


async def redirect_instead(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, RedirectInstead):
        raise TypeError(type(exc))
    return RedirectResponse(
        exc.location, status_code=302, headers={"Cache-Control": "no-store", **exc.headers}
    )
