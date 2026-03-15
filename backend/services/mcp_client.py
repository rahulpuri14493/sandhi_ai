"""
Service to communicate with MCP servers over HTTP (JSON-RPC).

Compatible with:
- MCP streamable HTTP (2025-11-25): single endpoint, POST, response as JSON or SSE.
- Legacy HTTP+SSE (2024-11-05): POST to endpoint, server responds with JSON or SSE.
- Servers that return Content-Type: application/json (e.g. many self-hosted).
- Servers that return Content-Type: text/event-stream (e.g. PageIndex).

Platform uses this to forward requests to user's MCP server with their stored credentials.
"""
import json
import httpx
from typing import Optional, Dict, Any

# MCP uses JSON-RPC 2.0 over HTTP
JSONRPC_VERSION = "2.0"


def _parse_sse_to_json(raw: str) -> dict:
    """
    Parse SSE body: extract first event's data and parse as JSON.
    SSE format: "event: message\\ndata: {...}\\n\\n". Multiple data lines in one event are joined.
    """
    data_parts = []
    for line in raw.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()  # after "data:"
            data_parts.append(payload)
        elif data_parts and line.strip() == "":
            # Empty line ends event; use collected data
            break
    if not data_parts:
        raise ValueError("No data field in SSE stream")
    payload = "\n".join(data_parts)
    return json.loads(payload)


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
    # Avoid double path when base_url already includes the endpoint (e.g. https://api.pageindex.ai/mcp + /mcp)
    post_url = base if base.endswith(path) else base + path

    # MCP streamable HTTP transport (spec 2025-11-25); compatible with 2024-11-05 HTTP+SSE.
    # Client MUST accept both application/json and text/event-stream (single JSON or SSE).
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "SandhiAI-MCP-Client/1.0",
        "MCP-Protocol-Version": "2024-11-05",  # Broad compatibility; servers may support 2025-11-25
    }
    if auth_type == "bearer" and credentials:
        # Accept "token" or "api_key" (PageIndex and others use API key as Bearer)
        raw = (credentials.get("token") or credentials.get("api_key") or "").strip()
        if raw:
            headers["Authorization"] = f"Bearer {raw}"
    elif auth_type == "api_key" and credentials:
        # API key sent as Bearer per common MCP practice; trim to avoid paste errors
        raw = (credentials.get("api_key") or "").strip()
        if raw:
            headers["Authorization"] = f"Bearer {raw}"
    elif auth_type == "basic" and credentials:
        import base64
        u = (credentials.get("username") or "").strip()
        p = (credentials.get("password") or "").strip()
        # Only set Basic if at least one credential present (avoid empty "Basic Og==")
        if u or p:
            encoded = base64.b64encode(f"{u}:{p}".encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"
    if extra_headers:
        headers.update(extra_headers)

    body = build_jsonrpc_body(method, params)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(post_url, json=body, headers=headers)
        response.raise_for_status()
        raw = response.text
        if not raw or not raw.strip():
            raise RuntimeError(
                "MCP server returned an empty response. "
                "The server may require a different endpoint or transport."
            )
        content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        try:
            if content_type == "text/event-stream":
                data = _parse_sse_to_json(raw)
            else:
                data = response.json()
        except ValueError as e:
            raise RuntimeError(
                f"MCP server returned non-JSON (Content-Type: {content_type or 'unknown'}). "
                f"Response body starts with: {raw[:200]!r}"
            ) from e
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
