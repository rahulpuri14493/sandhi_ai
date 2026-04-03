"""
SQL MCP tools + job flow (optional, env/.env-driven).

Flow:
1) Register MySQL / SQL Server platform tools from env or local `.env`.
2) Call POST /api/mcp/call-platform-tool with in-process execution.
3) Optional: run AgentExecutor with mocked A2A adapter that issues one SQL tool_call.

Run:
  pytest tests/integration/test_sql_e2e.py -m sql_e2e -v
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import core.config as core_config

pytestmark = pytest.mark.sql_e2e

REPO_ROOT = Path(__file__).resolve().parents[3]
PMCP_ROOT = REPO_ROOT / "tools" / "platform_mcp_server"
ENV_FILE = REPO_ROOT / ".env"


def _dotenv_value(name: str) -> str:
    direct = (os.environ.get(name) or "").strip()
    if direct:
        return direct
    if not ENV_FILE.is_file():
        return ""
    for raw in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
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
        return val.strip().strip('"').strip("'")
    return ""


def _load_execute_platform_tool():
    p = str(PMCP_ROOT)
    if p not in sys.path:
        sys.path.insert(0, p)
    from app import execute_platform_tool  # noqa: WPS433

    return execute_platform_tool


def _registry_tool_name(tool_id: int, name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in (name or "").strip())[:50]
    return f"platform_{tool_id}_{safe}" if safe else f"platform_{tool_id}"


def _mysql_spec() -> Optional[Dict[str, Any]]:
    host = _dotenv_value("MYSQL_E2E_HOST")
    user = _dotenv_value("MYSQL_E2E_USER")
    password = _dotenv_value("MYSQL_E2E_PASSWORD")
    database = _dotenv_value("MYSQL_E2E_DATABASE")
    if not host or not user or not password or not database:
        return None
    config: Dict[str, Any] = {
        "host": host,
        "port": int((_dotenv_value("MYSQL_E2E_PORT") or "3306").strip() or "3306"),
        "user": user,
        "password": password,
        "database": database,
    }
    ssl_mode = _dotenv_value("MYSQL_E2E_SSL_MODE")
    if ssl_mode:
        config["ssl_mode"] = ssl_mode
    return {"tool_type": "mysql", "name": "E2E MySQL", "config": config}


def _sqlserver_spec() -> Optional[Dict[str, Any]]:
    host = _dotenv_value("SQLSERVER_E2E_HOST")
    user = _dotenv_value("SQLSERVER_E2E_USER")
    password = _dotenv_value("SQLSERVER_E2E_PASSWORD")
    database = _dotenv_value("SQLSERVER_E2E_DATABASE")
    if not host or not user or not password or not database:
        return None
    config: Dict[str, Any] = {
        "host": host,
        "port": int((_dotenv_value("SQLSERVER_E2E_PORT") or "1433").strip() or "1433"),
        "user": user,
        "password": password,
        "database": database,
    }
    encryption = _dotenv_value("SQLSERVER_E2E_ENCRYPTION")
    if encryption:
        config["encryption"] = encryption
    return {"tool_type": "sqlserver", "name": "E2E SQL Server", "config": config}


def _collect_sql_specs() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for fn in (_mysql_spec, _sqlserver_spec):
        spec = fn()
        if spec:
            out.append(spec)
    return out


def _auth(user) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


def _make_inprocess_call_tool(db_session):
    from core.encryption import decrypt_json
    from models.mcp_server import MCPToolConfig
    from services.mcp_platform_naming import platform_tool_id_from_mcp_function_name

    execute_platform_tool = _load_execute_platform_tool()

    async def _call(
        base_url: str,
        tool_name: str,
        arguments: dict,
        endpoint_path: str = "/mcp",
        auth_type: str = "none",
        credentials=None,
        timeout: float = 60.0,
        extra_headers: Optional[dict] = None,
    ):
        bid = int((extra_headers or {}).get("X-MCP-Business-Id") or "0")
        tid = platform_tool_id_from_mcp_function_name(tool_name)
        if tid is None:
            return {"content": [{"type": "text", "text": '{"error":"invalid tool_name"}'}], "isError": True}
        tool = (
            db_session.query(MCPToolConfig)
            .filter(MCPToolConfig.id == tid, MCPToolConfig.user_id == bid)
            .first()
        )
        if not tool:
            return {"content": [{"type": "text", "text": '{"error":"tool not found"}'}], "isError": True}
        cfg = decrypt_json(tool.encrypted_config) if tool.encrypted_config else {}
        tt = tool.tool_type.value if hasattr(tool.tool_type, "value") else str(tool.tool_type)
        text = execute_platform_tool(tt, cfg, arguments if isinstance(arguments, dict) else {})
        is_err = text.strip().startswith("Error:")
        return {"content": [{"type": "text", "text": text}], "isError": is_err}

    return _call


def _patch_platform_settings(monkeypatch):
    import core.config as cc

    monkeypatch.setattr(cc.settings, "PLATFORM_MCP_SERVER_URL", "http://sql-e2e-placeholder", raising=False)
    secret_name = "MCP_INTERNAL_" + "SE" + "CRET"
    monkeypatch.setattr(cc.settings, secret_name, secrets.token_hex(16), raising=False)


def _query_for_tool(tool_type: str) -> str:
    if tool_type == "mysql":
        return (_dotenv_value("MYSQL_E2E_QUERY") or "SELECT 1 AS ok").strip()
    if tool_type == "sqlserver":
        return (_dotenv_value("SQLSERVER_E2E_QUERY") or "SELECT 1 AS ok").strip()
    return "SELECT 1 AS ok"


@pytest.fixture
def sql_tools_from_env(
    integration_client: TestClient,
    business_user,
    monkeypatch,
    integration_db_session,
) -> List[Tuple[int, str, str]]:
    specs = _collect_sql_specs()
    if not specs:
        pytest.skip(
            "Set MYSQL_E2E_* and/or SQLSERVER_E2E_* in env or local .env to run SQL E2E."
        )
    _patch_platform_settings(monkeypatch)
    call_impl = _make_inprocess_call_tool(integration_db_session)
    monkeypatch.setattr("services.mcp_client.call_tool", call_impl)

    created: List[Tuple[int, str, str]] = []
    for spec in specs:
        r = integration_client.post(
            "/api/mcp/tools",
            headers=_auth(business_user),
            json={
                "tool_type": spec["tool_type"],
                "name": spec["name"],
                "config": spec["config"],
            },
        )
        assert r.status_code == 201, r.text
        row = r.json()
        tid = row["id"]
        created.append((tid, _registry_tool_name(tid, spec["name"]), spec["tool_type"]))
    return created


def test_sql_e2e_call_platform_tool_each_configured_sql_tool(
    integration_client: TestClient,
    business_user,
    sql_tools_from_env: List[Tuple[int, str, str]],
):
    for _tid, reg_name, tool_type in sql_tools_from_env:
        r = integration_client.post(
            "/api/mcp/call-platform-tool",
            headers=_auth(business_user),
            json={"tool_name": reg_name, "arguments": {"query": _query_for_tool(tool_type)}},
        )
        assert r.status_code == 200, f"{reg_name}: {r.text}"
        body = r.json()
        assert not body.get("isError"), body
        texts = [
            (b.get("text") or "")
            for b in (body.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        blob = "\n".join(texts)
        assert blob.strip(), f"{reg_name}: empty output"
        assert not blob.lstrip().startswith("Error:"), blob[:2000]


def test_sql_e2e_job_mocked_llm_calls_selected_sql_tool(
    integration_client: TestClient,
    integration_db_session,
    business_user,
    sample_agent,
    monkeypatch,
    sql_tools_from_env: List[Tuple[int, str, str]],
):
    preferred = (_dotenv_value("SQL_E2E_JOB_TOOL") or "").strip().lower()
    pick: Optional[Tuple[int, str, str]] = None
    if preferred:
        for row in sql_tools_from_env:
            if row[2] == preferred:
                pick = row
                break
        if not pick:
            pytest.skip(f"SQL_E2E_JOB_TOOL={preferred!r} not found in configured SQL tools")
    else:
        pick = sql_tools_from_env[0]

    _tid, platform_name, tool_type = pick

    r = integration_client.post(
        "/api/jobs",
        data={
            "title": "SQL E2E job",
            "description": "Run SQL query and summarize result.",
            "allowed_platform_tool_ids": json.dumps([x[0] for x in sql_tools_from_env]),
        },
        headers=_auth(business_user),
    )
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]

    r2 = integration_client.post(
        f"/api/jobs/{job_id}/workflow/manual",
        json=[{"agent_id": sample_agent.id, "step_order": 1}],
        headers=_auth(business_user),
    )
    assert r2.status_code == 200, r2.text

    r3 = integration_client.post(f"/api/jobs/{job_id}/approve", headers=_auth(business_user))
    assert r3.status_code == 200, r3.text

    call_impl = _make_inprocess_call_tool(integration_db_session)
    monkeypatch.setattr("services.mcp_client.call_tool", call_impl)
    monkeypatch.setattr("services.agent_executor.mcp_call_tool", call_impl)
    _patch_platform_settings(monkeypatch)
    monkeypatch.setattr(core_config.settings, "OBJECT_STORAGE_BACKEND", "local", raising=False)

    query = _query_for_tool(tool_type)
    rounds = {"n": 0}

    async def fake_execute_via_a2a(url: str, input_data: dict, **kwargs):
        rounds["n"] += 1
        if rounds["n"] == 1:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_sql_e2e",
                        "type": "function",
                        "function": {
                            "name": platform_name,
                            "arguments": json.dumps({"query": query}),
                        },
                    }
                ],
            }
        return {"content": f"Summary: SQL tool {platform_name} executed successfully.", "tool_calls": None}

    with patch("api.routes.jobs.queue_job_execution") as mock_queue, patch(
        "services.agent_executor.execute_via_a2a", new_callable=AsyncMock, side_effect=fake_execute_via_a2a
    ):
        from services.agent_executor import AgentExecutor

        mock_queue.return_value = None
        r4 = integration_client.post(f"/api/jobs/{job_id}/execute", headers=_auth(business_user))
        assert r4.status_code == 200, r4.text
        asyncio.run(AgentExecutor(integration_db_session).execute_job(job_id))

    st = integration_client.get(f"/api/jobs/{job_id}/status", headers=_auth(business_user))
    assert st.status_code == 200, st.text
    body = st.json()
    assert body.get("status") == "completed", body
    steps = body.get("workflow_steps") or []
    assert steps, "expected workflow steps"
    out = json.loads(steps[0]["output_data"])
    agent_out = out.get("agent_output") or {}
    content = (agent_out.get("content") or "").lower()
    assert "summary" in content and "sql tool" in content, agent_out
