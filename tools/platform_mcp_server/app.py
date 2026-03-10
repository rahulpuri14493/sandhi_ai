"""
Platform MCP Server for Sandhi AI.

Exposes MCP protocol (JSON-RPC 2.0): initialize, tools/list, tools/call.
Tools are resolved per business (tenant) via the Sandhi AI backend internal API.
Implements Vector DB, PostgreSQL, and File system tools using tenant-stored config.
"""
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Sandhi AI Platform MCP Server",
    description="MCP server exposing platform-configured tools (Vector DB, Postgres, File system) per tenant.",
    version="1.0.0",
)

# Backend internal API (same network as platform)
BACKEND_BASE = os.environ.get("BACKEND_INTERNAL_URL", "http://backend:8000").strip().rstrip("/")
MCP_INTERNAL_SECRET = os.environ.get("MCP_INTERNAL_SECRET", "").strip()
INTERNAL_HEADERS = {"Content-Type": "application/json"}
if MCP_INTERNAL_SECRET:
    INTERNAL_HEADERS["X-Internal-Secret"] = MCP_INTERNAL_SECRET

JSONRPC_VERSION = "2.0"
BUSINESS_ID_HEADER = "x-mcp-business-id"


def _get_business_id(request: Request) -> int:
    """Extract business_id from header (set by backend when calling this server)."""
    raw = request.headers.get(BUSINESS_ID_HEADER) or request.headers.get("X-MCP-Business-Id")
    if not raw:
        raise HTTPException(status_code=400, detail="Missing X-MCP-Business-Id header")
    try:
        return int(raw.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid X-MCP-Business-Id")


def _fetch_platform_tools(business_id: int) -> List[Dict[str, Any]]:
    """Fetch tool list from backend internal API."""
    url = f"{BACKEND_BASE}/api/internal/mcp/tools?business_id={business_id}"
    with httpx.Client(timeout=15.0) as client:
        r = client.get(url, headers=INTERNAL_HEADERS)
        r.raise_for_status()
        data = r.json()
    return data.get("tools", [])


def _fetch_tool_config(business_id: int, tool_id: int) -> Dict[str, Any]:
    """Fetch decrypted tool config from backend."""
    url = f"{BACKEND_BASE}/api/internal/mcp/tools/{tool_id}/config"
    with httpx.Client(timeout=15.0) as client:
        r = client.post(url, json={"business_id": business_id}, headers=INTERNAL_HEADERS)
        r.raise_for_status()
        return r.json()


def _parse_platform_tool_id(name: str) -> Optional[int]:
    """Parse platform_<id>_* to get tool id."""
    if not name or not name.startswith("platform_"):
        return None
    match = re.match(r"^platform_(\d+)(?:_|$)", name)
    if match:
        return int(match.group(1))
    return None


# --- Tool execution (vector_db, postgres, filesystem) ---

def _execute_postgres(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    """Run a read-only query against configured PostgreSQL."""
    import psycopg2
    conn_str = config.get("connection_string") or ""
    if not conn_str:
        return "Error: connection_string not configured"
    query = (arguments.get("query") or "").strip()
    if not query:
        return "Error: query is required"
    if not query.upper().startswith("SELECT"):
        return "Error: only SELECT queries are allowed"
    try:
        conn = psycopg2.connect(conn_str)
        conn.set_session(readonly=True)
        cur = conn.cursor()
        cur.execute(query)
        rows = cur.fetchall()
        colnames = [d[0] for d in cur.description] if cur.description else []
        cur.close()
        conn.close()
        if not rows:
            return "No rows returned."
        lines = ["\t".join(colnames)]
        for row in rows:
            lines.append("\t".join(str(c) for c in row))
        return "\n".join(lines)
    except Exception as e:
        return f"Query error: {e}"


def _execute_filesystem(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    """Read file or list directory under base_path."""
    base = (config.get("base_path") or "").strip()
    if not base:
        return "Error: base_path not configured"
    rel = (arguments.get("path") or "").strip()
    if not rel:
        return "Error: path is required"
    if ".." in rel or rel.startswith("/"):
        return "Error: path must be relative and not contain .."
    import pathlib
    full = pathlib.Path(base) / rel
    try:
        full = full.resolve()
        base_resolved = pathlib.Path(base).resolve()
        if not str(full).startswith(str(base_resolved)):
            return "Error: path escapes base_path"
    except Exception:
        return "Error: invalid path"
    action = (arguments.get("action") or "read").strip().lower()
    if action == "list":
        if not full.is_dir():
            return "Error: path is not a directory"
        try:
            entries = sorted(full.iterdir())
            return "\n".join(e.name for e in entries)
        except Exception as e:
            return f"List error: {e}"
    try:
        return full.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Read error: {e}"


def _execute_vector_db(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    """Query vector DB (generic: try Pinecone-style or return placeholder)."""
    # Optional: add pinecone-client / weaviate-client and implement
    api_key = config.get("api_key")
    url = config.get("url") or ""
    query = (arguments.get("query") or "").strip()
    top_k = int(arguments.get("top_k") or 5)
    if not query:
        return "Error: query is required"
    if url and api_key:
        try:
            # Generic HTTP vector API: POST with query and top_k
            with httpx.Client(timeout=10.0) as client:
                r = client.post(
                    url.rstrip("/") + "/query",
                    json={"query": query, "top_k": top_k},
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                )
                if r.status_code == 200:
                    return json.dumps(r.json(), indent=2)
        except Exception as e:
            return f"Vector query error: {e}"
    return "Vector DB tool is configured; add a compatible endpoint (e.g. Pinecone/Weaviate) for live queries."


def _execute_mysql(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    """Run read-only query against MySQL."""
    try:
        import pymysql
    except ImportError:
        return "Error: pymysql not installed. Add pymysql to platform_mcp_server requirements."
    query = (arguments.get("query") or "").strip()
    if not query:
        return "Error: query is required"
    if not query.upper().startswith("SELECT"):
        return "Error: only SELECT queries are allowed"
    try:
        conn = pymysql.connect(
            host=config.get("host", "localhost"),
            port=int(config.get("port", 3306)),
            user=config.get("user", ""),
            password=config.get("password", ""),
            database=config.get("database", ""),
        )
        cur = conn.cursor()
        cur.execute(query)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        cur.close()
        conn.close()
        if not rows:
            return "No rows returned."
        lines = ["\t".join(cols)] + ["\t".join(str(c) for c in row) for row in rows]
        return "\n".join(lines)
    except Exception as e:
        return f"Query error: {e}"


def execute_platform_tool(tool_type: str, config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    """Dispatch to the right tool implementation."""
    if tool_type == "postgres":
        return _execute_postgres(config, arguments)
    if tool_type == "mysql":
        return _execute_mysql(config, arguments)
    if tool_type == "filesystem":
        return _execute_filesystem(config, arguments)
    if tool_type in ("vector_db", "pinecone", "weaviate", "qdrant", "chroma"):
        return _execute_vector_db(config, arguments)
    # Stub implementations for integrations (extend with real SDKs as needed)
    if tool_type == "elasticsearch":
        url = (config.get("url") or config.get("host") or "").strip() or "http://localhost:9200"
        query = (arguments.get("query") or "").strip()
        if not query:
            return "Error: query is required"
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(f"{url.rstrip('/')}/_search", json={"query": {"query_string": {"query": query}}, "size": arguments.get("size", 10)}, headers={"Content-Type": "application/json"})
                if r.status_code == 200:
                    return json.dumps(r.json(), indent=2)
                return f"Elasticsearch error: {r.status_code} {r.text}"
        except Exception as e:
            return f"Elasticsearch error: {e}"
    if tool_type == "s3":
        return "S3 tool is configured. Add boto3 and implement get/list in platform MCP server to enable."
    if tool_type == "slack":
        return "Slack tool is configured. Add slack_sdk and implement send/list in platform MCP server to enable."
    if tool_type == "github":
        return "GitHub tool is configured. Add PyGithub and implement in platform MCP server to enable."
    if tool_type == "notion":
        return "Notion tool is configured. Add notion-client and implement in platform MCP server to enable."
    if tool_type == "rest_api":
        base = (config.get("base_url") or "").strip()
        path = (arguments.get("path") or "").strip()
        method = (arguments.get("method") or "GET").upper()
        if not path:
            return "Error: path is required"
        url = path if path.startswith("http") else (base.rstrip("/") + "/" + path.lstrip("/"))
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.request(method, url, json=arguments.get("body"), headers={"Authorization": f"Bearer {config.get('api_key', '')}"} if config.get("api_key") else {})
                return json.dumps({"status": r.status_code, "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text})
        except Exception as e:
            return f"REST API error: {e}"
    return f"Unknown tool type: {tool_type}"


# --- JSON-RPC handler ---

@app.get("/health")
def health():
    return {"status": "ok", "service": "platform-mcp-server"}


@app.post("/mcp")
@app.post("/")
async def jsonrpc(request: Request, x_mcp_business_id: Optional[str] = Header(None)):
    """Single JSON-RPC 2.0 endpoint for MCP (tools/list, tools/call, initialize)."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    req_id = body.get("id")
    method = (body.get("method") or "").strip()
    params = body.get("params") or {}
    if not method:
        return JSONResponse({"jsonrpc": JSONRPC_VERSION, "id": req_id, "error": {"code": -32600, "message": "Invalid method"}})
    business_id = _get_business_id(request)

    if method == "initialize":
        return JSONResponse({
            "jsonrpc": JSONRPC_VERSION,
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "sandhi-platform-mcp", "version": "1.0.0"},
            },
        })

    if method == "tools/list":
        try:
            tools = _fetch_platform_tools(business_id)
            return JSONResponse({
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "result": {"tools": tools, "nextCursor": None},
            })
        except httpx.HTTPStatusError as e:
            logger.exception("Backend tools/list failed")
            return JSONResponse({
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {"code": -32000, "message": str(e.response.text)},
            })
        except Exception as e:
            logger.exception("tools/list error")
            return JSONResponse({
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {"code": -32603, "message": str(e)},
            })

    if method == "tools/call":
        name = (params.get("name") or "").strip()
        arguments = params.get("arguments") or {}
        if not name:
            return JSONResponse({
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {"code": -32602, "message": "Missing tool name"},
            })
        tool_id = _parse_platform_tool_id(name)
        if tool_id is None:
            return JSONResponse({
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {"code": -32602, "message": "Unknown tool; only platform tools are supported"},
            })
        try:
            data = _fetch_tool_config(business_id, tool_id)
            config = data.get("config") or {}
            tool_type = data.get("tool_type") or "vector_db"
            result_text = execute_platform_tool(tool_type, config, arguments)
            return JSONResponse({
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": result_text}],
                    "isError": result_text.startswith("Error:"),
                },
            })
        except httpx.HTTPStatusError as e:
            logger.exception("Backend config fetch failed")
            return JSONResponse({
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {"code": -32000, "message": e.response.text or str(e)},
            })
        except Exception as e:
            logger.exception("tools/call error")
            return JSONResponse({
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {"code": -32603, "message": str(e)},
            })

    return JSONResponse({
        "jsonrpc": JSONRPC_VERSION,
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    })
