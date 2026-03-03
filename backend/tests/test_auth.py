"""API tests for auth endpoints (register, login, /me)."""
import uuid
import pytest
from fastapi.testclient import TestClient

from db.database import get_db
from main import app
from models.user import User, UserRole
from core.security import get_password_hash, create_access_token


@pytest.fixture
def client_with_auth(db_session):
    """Client with a pre-created user for login tests."""
    unique = uuid.uuid4().hex[:8]
    user = User(
        email=f"auth-{unique}@test.com",
        password_hash=get_password_hash("testpass123"),
        role=UserRole.BUSINESS,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c, user
    app.dependency_overrides.clear()


def test_register_success(client: TestClient):
    """POST /api/auth/register creates a new user."""
    unique = uuid.uuid4().hex[:8]
    response = client.post(
        "/api/auth/register",
        json={
            "email": f"newuser-{unique}@test.com",
            "password": "securepass123",
            "role": "business",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["email"] == f"newuser-{unique}@test.com"
    assert data["role"] == "business"
    assert "password" not in data


def test_register_duplicate_email(client_with_auth):
    """POST /api/auth/register returns 400 when email already exists."""
    client, user = client_with_auth
    response = client.post(
        "/api/auth/register",
        json={
            "email": user.email,
            "password": "otherpass",
            "role": "developer",
        },
    )
    assert response.status_code == 400
    assert "already registered" in response.json().get("detail", "").lower()


def test_login_success(client_with_auth):
    """POST /api/auth/login returns token for valid credentials."""
    client, user = client_with_auth
    response = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "testpass123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert len(data["access_token"]) > 20


def test_login_wrong_password(client_with_auth):
    """POST /api/auth/login returns 401 for wrong password."""
    client, user = client_with_auth
    response = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "wrongpassword"},
    )
    assert response.status_code == 401


def test_login_nonexistent_user(client: TestClient):
    """POST /api/auth/login returns 401 for unknown email."""
    response = client.post(
        "/api/auth/login",
        json={"email": "nonexistent@test.com", "password": "any"},
    )
    assert response.status_code == 401


def test_me_requires_auth(client: TestClient):
    """GET /api/auth/me returns 401 without token."""
    response = client.get("/api/auth/me")
    assert response.status_code == 401


def test_me_returns_user(client_with_auth):
    """GET /api/auth/me returns current user when authenticated."""
    client, user = client_with_auth
    token = create_access_token(data={"sub": user.id})
    response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == user.id
    assert data["email"] == user.email
    assert data["role"] == user.role.value
