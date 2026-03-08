"""Shared fixtures for integration tests."""
import json
import os
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.security import get_password_hash, create_access_token
from db.database import get_db
from main import app
from models.agent import Agent, AgentStatus
from models.user import User, UserRole


@pytest.fixture
def integration_db_session(db_session):
    """Use same db_session from conftest (in-memory SQLite)."""
    return db_session


@pytest.fixture
def business_user(integration_db_session):
    """Create a business user and return user + token."""
    u = User(
        email=f"business-e2e-{uuid.uuid4().hex[:8]}@test.com",
        password_hash=get_password_hash("pass123"),
        role=UserRole.BUSINESS,
    )
    integration_db_session.add(u)
    integration_db_session.commit()
    integration_db_session.refresh(u)
    token = create_access_token(data={"sub": u.id})
    return {"user": u, "token": token}


@pytest.fixture
def developer_user(integration_db_session):
    """Create a developer user and return user + token."""
    u = User(
        email=f"dev-e2e-{uuid.uuid4().hex[:8]}@test.com",
        password_hash=get_password_hash("pass123"),
        role=UserRole.DEVELOPER,
    )
    integration_db_session.add(u)
    integration_db_session.commit()
    integration_db_session.refresh(u)
    token = create_access_token(data={"sub": u.id})
    return {"user": u, "token": token}


@pytest.fixture
def sample_agent(integration_db_session, developer_user):
    """Create one agent owned by developer_user."""
    dev = developer_user["user"]
    a = Agent(
        developer_id=dev.id,
        name="E2E Test Agent",
        description="For integration tests",
        status=AgentStatus.ACTIVE,
        price_per_task=10.0,
        price_per_communication=1.0,
        api_endpoint="https://example.com/v1/chat/completions",
        api_key="sk-test",
        llm_model="gpt-4o-mini",
        a2a_enabled=False,
    )
    integration_db_session.add(a)
    integration_db_session.commit()
    integration_db_session.refresh(a)
    return a


@pytest.fixture
def integration_client(integration_db_session, business_user, developer_user, sample_agent):
    """Test client with DB override; business and developer users and one agent exist."""
    def override_get_db():
        try:
            yield integration_db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def temp_upload_file():
    """Create a temp .txt file for job upload; yield path. Clean up after."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write("Sample requirement for e2e test: Add 2 and 3. Result 5.")
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)
