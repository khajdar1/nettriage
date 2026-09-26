"""FastAPI application factory. Creating an app has no global side effects."""

from fastapi import FastAPI

from nettriage.entrypoints.api.routes import health
from nettriage.platform.config import Settings
from nettriage.platform.errors import register_error_handlers


def create_app(settings: Settings | None = None) -> FastAPI:
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
    return app
