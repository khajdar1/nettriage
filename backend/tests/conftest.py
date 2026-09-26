import pytest
from fastapi.testclient import TestClient

from nettriage.entrypoints.api.app import create_app
from nettriage.platform.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(stage="local", version="1.2.3-test")


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))
