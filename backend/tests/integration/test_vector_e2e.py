"""
Vector MCP tools + job flow (optional, env-driven).

**Security:** put credentials in environment variables or a local untracked `.env`; never commit keys.
If keys were ever pasted into chat, rotate them at the provider.

Requires network access to Pinecone / Qdrant / Chroma Cloud / Weaviate as configured.

Flow:
1) Register platform MCP tools via API (same shapes as the UI).
2) Call POST /api/mcp/call-platform-tool with in-process execution (patches HTTP MCP — no running platform-mcp container needed for this test).
3) Optional: run AgentExecutor with a mocked A2A adapter that issues one vector tool_call, then a final answer; assert job completed and output references retrieval.

Run:
  pytest tests/integration/test_vector_e2e.py -m vector_e2e -v

Env — Pinecone:
  PINECONE_E2E_API_KEY, PINECONE_E2E_HOST (full https host URL)
Env — Qdrant:
  QDRANT_E2E_URL, QDRANT_E2E_COLLECTION, QDRANT_E2E_API_KEY,
  optional QDRANT_E2E_EMBEDDING_MODEL (default text-embedding-3-small)
Env — Chroma Cloud:
  CHROMA_E2E_URL, CHROMA_E2E_API_KEY, CHROMA_E2E_COLLECTION (collection name in Chroma; stored as index_name in MCP config),
  CHROMA_E2E_TENANT, CHROMA_E2E_DATABASE
  Aliases: CHROMA_E2E_COLLECTION_NAME, CHROMA_E2E_INDEX_NAME
Env — Weaviate:
  WEAVIATE_E2E_URL, WEAVIATE_E2E_CLASS, WEAVIATE_E2E_API_KEY (for WCD)
  optional WEAVIATE_E2E_CLUSTER_NAME

Shared for API tests:
  monkeypatch sets PLATFORM_MCP_SERVER_URL / MCP_INTERNAL_SECRET so the route is allowed (values unused when call_tool is patched).

Job subtest additionally requires one fully configured tool; set VECTOR_E2E_JOB_TOOL to pinecone|weaviate|qdrant|chroma (default: first available among env-complete tools).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import core.config as core_config

pytestmark = pytest.mark.vector_e2e

REPO_ROOT = Path(__file__).resolve().parents[3]
PMCP_ROOT = REPO_ROOT / "tools" / "platform_mcp_server"


def _load_execute_platform_tool():
    p = str(PMCP_ROOT)
    if p not in sys.path:
        sys.path.insert(0, p)
    from app import execute_platform_tool  # noqa: WPS433

    return execute_platform_tool


def _registry_tool_name(tool_id: int, name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in (name or "").strip())[:50]
    return f"platform_{tool_id}_{safe}" if safe else f"platform_{tool_id}"


def _pinecone_spec() -> Optional[Dict[str, Any]]:
    key = (os.environ.get("PINECONE_E2E_API_KEY") or "").strip()
    host = (os.environ.get("PINECONE_E2E_HOST") or "").strip()
    if not key or not host:
        return None
    cfg: Dict[str, Any] = {"api_key": key, "url": host}
    oa = (os.environ.get("PINECONE_E2E_OPENAI_API_KEY") or "").strip()
    em = (os.environ.get("PINECONE_E2E_EMBEDDING_MODEL") or "").strip()
    if oa:
        cfg["openai_api_key"] = oa
    if em:
        cfg["embedding_model"] = em
    return {"tool_type": "pinecone", "name": "E2E Pinecone", "config": cfg}


def _qdrant_spec() -> Optional[Dict[str, Any]]:
    url = (os.environ.get("QDRANT_E2E_URL") or "").strip()
    coll = (os.environ.get("QDRANT_E2E_COLLECTION") or "").strip()
    key = (os.environ.get("QDRANT_E2E_API_KEY") or "").strip()
    if not url or not coll or not key:
        return None
    em = (os.environ.get("QDRANT_E2E_EMBEDDING_MODEL") or "text-embedding-3-small").strip()
    cfg: Dict[str, Any] = {"url": url, "index_name": coll, "api_key": key, "embedding_model": em}
    oa = (os.environ.get("QDRANT_E2E_OPENAI_API_KEY") or "").strip()
    if oa:
        cfg["openai_api_key"] = oa
    return {"tool_type": "qdrant", "name": "E2E Qdrant", "config": cfg}


def _chroma_collection_from_env() -> str:
    return (
        (os.environ.get("CHROMA_E2E_COLLECTION") or "").strip()
        or (os.environ.get("CHROMA_E2E_COLLECTION_NAME") or "").strip()
        or (os.environ.get("CHROMA_E2E_INDEX_NAME") or "").strip()
    )


def _chroma_spec() -> Optional[Dict[str, Any]]:
    url = (os.environ.get("CHROMA_E2E_URL") or "").strip()
    key = (os.environ.get("CHROMA_E2E_API_KEY") or "").strip()
    collection = _chroma_collection_from_env()
    tenant = (os.environ.get("CHROMA_E2E_TENANT") or "").strip()
    database = (os.environ.get("CHROMA_E2E_DATABASE") or "").strip()
    if not url or not key or not collection or not tenant or not database:
        return None
    return {
        "tool_type": "chroma",
        "name": "E2E Chroma",
        "config": {
            "url": url,
            "api_key": key,
            "index_name": collection,
            "tenant": tenant,
            "database": database,
        },
    }


def _weaviate_spec() -> Optional[Dict[str, Any]]:
    url = (os.environ.get("WEAVIATE_E2E_URL") or "").strip()
    cls = (os.environ.get("WEAVIATE_E2E_CLASS") or "").strip()
    key = (os.environ.get("WEAVIATE_E2E_API_KEY") or "").strip()
    if not url or not cls:
        return None
    if ".weaviate.cloud" in url.lower() and not key:
        return None
    cfg: Dict[str, Any] = {"url": url, "index_name": cls}
    if key:
        cfg["api_key"] = key
    cn = (os.environ.get("WEAVIATE_E2E_CLUSTER_NAME") or "").strip()
    if cn:
        cfg["weaviate_cluster_name"] = cn
    return {"tool_type": "weaviate", "name": "E2E Weaviate", "config": cfg}


def _collect_tool_specs() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for fn in (_pinecone_spec, _qdrant_spec, _chroma_spec, _weaviate_spec):
        spec = fn()
        if spec:
            out.append(spec)
    return out


def _auth(business_user) -> dict:
    return {"Authorization": f"Bearer {business_user['token']}"}


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

    monkeypatch.setattr(cc.settings, "PLATFORM_MCP_SERVER_URL", "http://vector-e2e-placeholder", raising=False)
    monkeypatch.setattr(cc.settings, "MCP_INTERNAL_SECRET", "e2e-test-secret", raising=False)


@pytest.fixture
def vector_tools_from_env(
    integration_client: TestClient,
    business_user,
    monkeypatch,
    integration_db_session,
) -> List[Tuple[int, str, str]]:
    """Create MCP tools from env; return [(id, registry_name, tool_type), ...]."""
    specs = _collect_tool_specs()
    if not specs:
        pytest.skip(
            "Set env for at least one vector provider (see module docstring: PINECONE_E2E_*, QDRANT_E2E_*, ...)"
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
        name = _registry_tool_name(tid, spec["name"])
        created.append((tid, name, spec["tool_type"]))
    return created


def test_vector_e2e_call_platform_tool_each_vector(
    integration_client: TestClient,
    business_user,
    vector_tools_from_env: List[Tuple[int, str, str]],
):
    """POST /api/mcp/call-platform-tool for each configured vector tool."""
    q_default = (os.environ.get("VECTOR_E2E_QUERY") or "sample search").strip()
    top_k = int((os.environ.get("VECTOR_E2E_TOP_K") or "3").strip() or "3")

    for _tid, reg_name, _tt in vector_tools_from_env:
        r = integration_client.post(
            "/api/mcp/call-platform-tool",
            headers=_auth(business_user),
            json={"tool_name": reg_name, "arguments": {"query": q_default, "top_k": top_k}},
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
        assert blob.strip()
        assert not blob.lstrip().startswith("Error:"), blob[:2000]
        low = blob.lower()
        assert "matches" in low or "document" in low or "metadata" in low or "weaviate_collection" in low, (
            f"Unexpected body (no obvious retrieval payload): {blob[:800]}"
        )


def test_vector_e2e_job_mocked_llm_calls_vector_tool(
    integration_client: TestClient,
    integration_db_session,
    business_user,
    sample_agent,
    monkeypatch,
    vector_tools_from_env: List[Tuple[int, str, str]],
):
    """One workflow step: mocked adapter issues a tool_call; real in-process vector query runs."""
    preference = (os.environ.get("VECTOR_E2E_JOB_TOOL") or "").strip().lower()
    pick: Optional[Tuple[int, str, str]] = None
    if preference:
        for row in vector_tools_from_env:
            if row[2] == preference:
                pick = row
                break
        if not pick:
            pytest.skip(f"VECTOR_E2E_JOB_TOOL={preference!r} not in configured tools for this run")
    else:
        pick = vector_tools_from_env[0]

    _tid, platform_name, tool_type = pick
    job_title = "Vector E2E job"
    job_desc = "Run a vector search over the configured knowledge base and summarize findings."

    r = integration_client.post(
        "/api/jobs",
        data={
            "title": job_title,
            "description": job_desc,
            "allowed_platform_tool_ids": json.dumps([x[0] for x in vector_tools_from_env]),
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
    # .env may point S3 at docker hostname `minio`; host pytest cannot resolve it.
    monkeypatch.setattr(core_config.settings, "OBJECT_STORAGE_BACKEND", "local", raising=False)

    jq = (os.environ.get("VECTOR_E2E_JOB_QUERY") or os.environ.get("VECTOR_E2E_QUERY") or "sample").strip()
    top_k = int((os.environ.get("VECTOR_E2E_TOP_K") or "3").strip() or "3")

    rounds = {"n": 0}

    async def fake_execute_via_a2a(url: str, input_data: dict, **kwargs):
        rounds["n"] += 1
        if rounds["n"] == 1:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_vec_e2e",
                        "type": "function",
                        "function": {
                            "name": platform_name,
                            "arguments": json.dumps({"query": jq, "top_k": top_k}),
                        },
                    }
                ],
            }
        return {
            "content": (
                f"Summary for job {job_title!r}: vector tool {platform_name!r} returned ranked matches; "
                "output is suitable for the business user."
            ),
            "tool_calls": None,
        }

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
    assert "summary" in content or "vector" in content or "matches" in content, agent_out
    # Tool round should have produced JSON-ish retrieval in message history; final content references task.
    assert job_title.split()[0].lower() in content or "vector" in content
