"""
Service to communicate with MCP servers over HTTP (JSON-RPC).
Platform uses this to forward requests to user's MCP server with their stored credentials.
"""
import httpx
from typing import Optional, Dict, Any

# MCP uses JSON-RPC 2.0 over HTTP
JSONRPC_VERSION = "2.0"


def build_jsonrpc_body(method: str, params: Optional[dict] = None, request_id: Optional[int] = 1) -> dict:
    body = {
        "jsonrpc": JSONRPC_VERSION,
        "method": method,
        "id": request_id,
    }
    if params is not None:
        body["params"] = params
    return body


def _normalize_path(path: str) -> str:
    """Ensure path starts with / and has no trailing slash (for joining with base_url)."""
    path = (path or "/mcp").strip()
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


async def call_mcp_server(
    base_url: str,
    method: str,
    params: Optional[dict] = None,
    auth_type: str = "none",
    credentials: Optional[dict] = None,
    endpoint_path: str = "/mcp",
    timeout: float = 30.0,
    extra_headers: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Send a JSON-RPC request to an MCP server.
    base_url: e.g. https://mcp.example.com (no trailing slash).
    endpoint_path: path to JSON-RPC endpoint, e.g. /mcp, /message, or / (default /mcp).
    extra_headers: optional headers (e.g. X-MCP-Business-Id for platform MCP server).
    """
    base = base_url.rstrip("/")
    path = _normalize_path(endpoint_path)
    post_url = base + path

    headers = {"Content-Type": "application/json"}
    if auth_type == "bearer" and credentials and credentials.get("token"):
        headers["Authorization"] = f"Bearer {credentials['token']}"
    elif auth_type == "api_key" and credentials and credentials.get("api_key"):
        headers["Authorization"] = f"Bearer {credentials['api_key']}"
    elif auth_type == "basic" and credentials:
        import base64
        u = credentials.get("username") or ""
        p = credentials.get("password") or ""
        encoded = base64.b64encode(f"{u}:{p}".encode()).decode()
        headers["Authorization"] = f"Basic {encoded}"
    if extra_headers:
        headers.update(extra_headers)

    body = build_jsonrpc_body(method, params)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(post_url, json=body, headers=headers)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise RuntimeError(data["error"].get("message", str(data["error"])))
        return data


async def list_tools(
    base_url: str,
    endpoint_path: str = "/mcp",
    auth_type: str = "none",
    credentials: Optional[dict] = None,
    timeout: float = 30.0,
    extra_headers: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    MCP tools/list: discover available tools from an MCP server.
    Returns result with keys: tools (list of { name, description, inputSchema }), nextCursor.
    """
    result = await call_mcp_server(
        base_url=base_url,
        method="tools/list",
        params={},
        auth_type=auth_type,
        credentials=credentials,
        endpoint_path=endpoint_path,
        timeout=timeout,
        extra_headers=extra_headers,
    )
    return result.get("result", {})


async def call_tool(
    base_url: str,
    tool_name: str,
    arguments: dict,
    endpoint_path: str = "/mcp",
    auth_type: str = "none",
    credentials: Optional[dict] = None,
    timeout: float = 30.0,
    extra_headers: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    MCP tools/call: invoke a tool by name with arguments.
    Returns result with keys: content (list of { type, text }), isError.
    """
    result = await call_mcp_server(
        base_url=base_url,
        method="tools/call",
        params={"name": tool_name, "arguments": arguments},
        auth_type=auth_type,
        credentials=credentials,
        endpoint_path=endpoint_path,
        timeout=timeout,
        extra_headers=extra_headers,
    )
    return result.get("result", {})
