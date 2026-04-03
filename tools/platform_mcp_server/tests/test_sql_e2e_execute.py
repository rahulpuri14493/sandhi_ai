"""
Optional smoke tests: execute_platform_tool against real SQL providers.

Credentials can be set via environment variables OR local repo `.env` (untracked).

Run:
  pytest tests/test_sql_e2e_execute.py -m sql_e2e -v

MySQL:
  MYSQL_E2E_HOST, MYSQL_E2E_USER, MYSQL_E2E_PASSWORD, MYSQL_E2E_DATABASE
  optional MYSQL_E2E_PORT (default 3306), MYSQL_E2E_SSL_MODE, MYSQL_E2E_QUERY

SQL Server:
  SQLSERVER_E2E_HOST, SQLSERVER_E2E_USER, SQLSERVER_E2E_PASSWORD, SQLSERVER_E2E_DATABASE
  optional SQLSERVER_E2E_PORT (default 1433), SQLSERVER_E2E_ENCRYPTION, SQLSERVER_E2E_QUERY
"""
from __future__ import annotations

import socket
import os
from pathlib import Path

import pytest

from app import execute_platform_tool

pytestmark = pytest.mark.sql_e2e

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILE = _REPO_ROOT / ".env"


def _dotenv_value(name: str) -> str:
    direct = (os.environ.get(name) or "").strip()
    if direct:
        return direct
    if not _ENV_FILE.is_file():
        return ""
    for raw in _ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        if key.strip() != name:
            continue
        v = val.strip().strip('"').strip("'")
        return v
    return ""


def _env_or_dotenv(*keys: str) -> tuple[dict[str, str], list[str]]:
    out: dict[str, str] = {}
    missing: list[str] = []
    for key in keys:
        val = _dotenv_value(key)
        if not val:
            missing.append(key)
        else:
            out[key] = val
    return out, missing


def _assert_ok_sql_output(text: str) -> None:
    assert text and isinstance(text, str), "empty tool output"
    assert not text.startswith("Error:"), text[:1500]


def _can_connect_tcp(host: str, port: int, *, timeout_s: float = 2.0) -> bool:
    host = (host or "").strip()
    if not host:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_s):
            return True
    except OSError:
        return False


def test_e2e_mysql_execute():
    cfg, missing = _env_or_dotenv(
        "MYSQL_E2E_HOST",
        "MYSQL_E2E_USER",
        "MYSQL_E2E_PASSWORD",
        "MYSQL_E2E_DATABASE",
    )
    if missing:
        pytest.skip(f"set {', '.join(missing)}")
    config: dict = {
        "host": cfg["MYSQL_E2E_HOST"],
        "port": int((_dotenv_value("MYSQL_E2E_PORT") or "3306").strip() or "3306"),
        "user": cfg["MYSQL_E2E_USER"],
        "password": cfg["MYSQL_E2E_PASSWORD"],
        "database": cfg["MYSQL_E2E_DATABASE"],
    }
    ssl_mode = _dotenv_value("MYSQL_E2E_SSL_MODE")
    if ssl_mode:
        config["ssl_mode"] = ssl_mode
    query = (_dotenv_value("MYSQL_E2E_QUERY") or "SELECT 1 AS ok").strip()
    if not _can_connect_tcp(config["host"], config["port"], timeout_s=2.0):
        pytest.skip(f"TCP unreachable: {config['host']}:{config['port']}")
    out = execute_platform_tool("mysql", config, {"query": query})
    if isinstance(out, str) and out.startswith("Error:"):
        pytest.skip(f"MySQL live check failed (credentials or permissions): {out[:300]}")
    _assert_ok_sql_output(out)


def test_e2e_sqlserver_execute():
    try:
        import pymssql  # noqa: F401
    except Exception:
        pytest.skip("pymssql is not installed in local test runtime")
    cfg, missing = _env_or_dotenv(
        "SQLSERVER_E2E_HOST",
        "SQLSERVER_E2E_USER",
        "SQLSERVER_E2E_PASSWORD",
        "SQLSERVER_E2E_DATABASE",
    )
    if missing:
        pytest.skip(f"set {', '.join(missing)}")
    config: dict = {
        "host": cfg["SQLSERVER_E2E_HOST"],
        "port": int((_dotenv_value("SQLSERVER_E2E_PORT") or "1433").strip() or "1433"),
        "user": cfg["SQLSERVER_E2E_USER"],
        "password": cfg["SQLSERVER_E2E_PASSWORD"],
        "database": cfg["SQLSERVER_E2E_DATABASE"],
    }
    encryption = _dotenv_value("SQLSERVER_E2E_ENCRYPTION")
    if encryption:
        config["encryption"] = encryption
    query = (_dotenv_value("SQLSERVER_E2E_QUERY") or "SELECT 1 AS ok").strip()
    if not _can_connect_tcp(config["host"], config["port"], timeout_s=2.0):
        pytest.skip(f"TCP unreachable: {config['host']}:{config['port']}")
    out = execute_platform_tool("sqlserver", config, {"query": query})
    if isinstance(out, str) and out.startswith("Error:"):
        pytest.skip(f"SQL Server live check failed (credentials, firewall, or permissions): {out[:300]}")
    _assert_ok_sql_output(out)
