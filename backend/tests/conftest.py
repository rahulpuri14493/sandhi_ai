"""Pytest configuration and shared fixtures."""
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Use in-memory SQLite for tests to avoid DB setup
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
# Default tests to local file storage unless a specific suite enables S3.
# This prevents CI failures when OBJECT_STORAGE_BACKEND defaults to "s3"
# but S3_* credentials are intentionally not configured for unit/integration tests.
os.environ.setdefault("OBJECT_STORAGE_BACKEND", "local")

from db.database import Base, get_db
from main import app


@pytest.fixture(scope="session")
def engine():
    """Create test database engine."""
    return create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture(scope="session")
def test_db(engine):
    """Create test database tables."""
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture
def db_session(test_db):
    """Create a fresh database session for each test."""
    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_db
    )
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    """Create test client with overridden DB dependency."""

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def temp_txt_file():
    """Create a temporary .txt file with content."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write("Sample requirement: Add 2 and 3. Result should be 5.")
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def temp_json_file():
    """Create a temporary .json file."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        f.write('{"task": "add", "a": 2, "b": 3}')
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)
