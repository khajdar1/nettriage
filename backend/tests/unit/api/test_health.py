from fastapi.testclient import TestClient


def test_health_returns_ok_and_version(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "1.2.3-test"}


def test_health_is_never_cached(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.headers["cache-control"] == "no-store"
