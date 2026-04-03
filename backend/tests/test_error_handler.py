"""Tests for middleware error handlers (via app)."""
from fastapi.testclient import TestClient



def test_validation_error_returns_422(client: TestClient):
    """RequestValidationError returns 422 and detail."""
    # POST with invalid body (e.g. missing required field)
    response = client.post(
        "/api/auth/register",
        json={"email": "not-an-email", "password": "short"},
    )
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data


def test_http_exception_returns_correct_status(client: TestClient):
    """Starlette HTTPException is handled and returns correct status."""
    response = client.get("/api/jobs/99999999")
    # 404 or 401 depending on auth; both are HTTPException
    assert response.status_code in (401, 404)
