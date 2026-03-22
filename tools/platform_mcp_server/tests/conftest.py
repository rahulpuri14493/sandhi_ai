"""Shared fixtures for platform MCP server tests."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Parent directory is the package root (app, execution).
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import app as app_module  # noqa: E402


@pytest.fixture
def mcp_client() -> TestClient:
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture
def mcp_headers() -> dict:
    return {"X-MCP-Business-Id": "1"}
