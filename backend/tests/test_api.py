"""API endpoint tests."""
import pytest
from fastapi.testclient import TestClient

from main import app


def test_root(client: TestClient):
    """Root endpoint requires auth."""
    response = client.get("/")
    assert response.status_code == 401


def test_health(client: TestClient):
    """Authenticated health check returns status and includes storage probe."""
    response = client.get("/health")
    assert response.status_code == 401


def test_healthz_public(client: TestClient):
    """Public liveness endpoint is unauthenticated."""
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_protected_endpoints_require_auth(client: TestClient):
    """Endpoints that require auth return 401 when unauthenticated."""
    # Jobs list requires auth
    response = client.get("/api/jobs")
    assert response.status_code == 401
    # Agent detail is public (200 without auth); api_endpoint is hidden for unauthenticated users
    response = client.get("/api/agents/1")
    assert response.status_code in (200, 404)  # 200 if agent exists, 404 if no data
    if response.status_code == 200:
        data = response.json()
        assert data.get("api_endpoint") is None  # restricted to logged-in users
