"""Pytest configuration and shared fixtures."""
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _load_repo_dotenv_all() -> None:
    """
    Merge the repository root `.env` into `os.environ` before `Settings` and `db.database`
    are imported.

    Does not override variables already set in the shell or CI (same rule as python-dotenv).
    Supports optional `export KEY=...` and single/double-quoted values.
    """
    repo_root = Path(__file__).resolve().parents[2]
    env_path = repo_root / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key:
            continue
        if key in os.environ:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        os.environ[key] = val


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


_load_repo_dotenv_all()

# Isolate harness from typical dev `.env`: `db.database` and storage clients initialize at import.
# Opt out of overrides (use merged shell + `.env` values) with PYTEST_USE_DOTENV_DATABASE=1 and/or
# PYTEST_USE_DOTENV_STORAGE=1. The default `client` fixture still uses in-memory SQLite sessions.
if not _env_truthy("PYTEST_USE_DOTENV_DATABASE"):
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
else:
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

if not _env_truthy("PYTEST_USE_DOTENV_STORAGE"):
    os.environ["OBJECT_STORAGE_BACKEND"] = "local"
else:
    os.environ.setdefault("OBJECT_STORAGE_BACKEND", "local")

os.environ["DISABLE_SCHEDULER"] = "true"

# Avoid real planner LLM calls and planner-heavy code paths when `.env` enables the agent planner.
if not _env_truthy("PYTEST_USE_DOTENV_PLANNER"):
    os.environ["AGENT_PLANNER_ENABLED"] = "false"
else:
    os.environ.setdefault("AGENT_PLANNER_ENABLED", "false")

# Other keys (Redis cache URL, API keys for non-planner paths, Celery URLs, etc.) still apply.

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
