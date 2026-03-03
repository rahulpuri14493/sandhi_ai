"""API endpoint tests."""
import pytest
from fastapi.testclient import TestClient

from main import app


def test_root(client: TestClient):
    """Root endpoint returns API info."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "Sandhi AI" in data["message"]
    assert "version" in data


def test_health(client: TestClient):
    """Health check returns healthy status."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "healthy"


def test_protected_endpoints_require_auth(client: TestClient):
    """Endpoints that require auth return 401 when unauthenticated."""
    # Jobs list requires auth
    response = client.get("/api/jobs")
    assert response.status_code == 401
    # Agent detail (by id) requires auth; list is public
    response = client.get("/api/agents/1")
    assert response.status_code == 401
