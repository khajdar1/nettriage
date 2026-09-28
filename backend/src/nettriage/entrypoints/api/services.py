"""What the API's routes use, built once per process: in production from SSM at cold start
(`wiring.py`), in tests from moto, a fake Cognito and the test database."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from fastapi import Request
from sqlalchemy import Engine

from nettriage.adapters.login_states import LoginStateStore
from nettriage.adapters.oidc import OidcClient
from nettriage.adapters.rate_limiter import RateLimiter
from nettriage.adapters.sessions import SessionStore
from nettriage.application.clock import Clock
from nettriage.application.rate_limits import UNKNOWN_SESSIONS_PER_IP, LocalLimiter
from nettriage.platform.metrics import AppMetrics


@dataclass(frozen=True)
class Services:
    database: Engine
    sessions: SessionStore
    login_states: LoginStateStore
    rate_limiter: RateLimiter
    oidc: OidcClient
    clock: Clock
    metrics: AppMetrics
    unknown_sessions: LocalLimiter = field(
        default_factory=lambda: LocalLimiter(UNKNOWN_SESSIONS_PER_IP)
    )

    @property
    def app_origin(self) -> str:
        return self.oidc.settings.app_origin


def get_services(request: Request) -> Services:
    return cast(Services, request.app.state.services)
