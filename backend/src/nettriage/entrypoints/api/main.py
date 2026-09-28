"""Production ASGI entrypoint, started by run.sh: nettriage.entrypoints.api.main:app"""

from nettriage.entrypoints.api.app import create_app
from nettriage.platform.config import Settings
from nettriage.platform.logging import configure_logging
from nettriage.platform.telemetry import (
    create_meter_provider,
    create_tracer_provider,
    install_global_providers,
)

settings = Settings()
configure_logging(settings)
tracer_provider = create_tracer_provider(settings)
meter_provider = create_meter_provider(settings)
install_global_providers(tracer_provider, meter_provider)
app = create_app(settings, tracer_provider=tracer_provider, meter_provider=meter_provider)
