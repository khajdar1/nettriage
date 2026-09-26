"""FastAPI application factory. Creating an app has no global side effects."""

from fastapi import FastAPI
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from nettriage.entrypoints.api.routes import health
from nettriage.platform.config import Settings
from nettriage.platform.errors import register_error_handlers
from nettriage.platform.telemetry import instrument_app


def create_app(
    settings: Settings | None = None,
    *,
    tracer_provider: TracerProvider | None = None,
    meter_provider: MeterProvider | None = None,
) -> FastAPI:
    settings = settings or Settings()
    docs = settings.api_docs_enabled
    app = FastAPI(
        title="NetTriage API",
        version=settings.version,
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
    )
    app.state.settings = settings
    register_error_handlers(app)
    app.include_router(health.router, prefix="/api")
    if tracer_provider is not None:
        instrument_app(app, tracer_provider, meter_provider)
    return app
