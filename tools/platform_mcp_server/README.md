# Sandhi AI Platform MCP Server

Platform-hosted MCP (Model Context Protocol) server that exposes **enterprise tools** (Vector DB, PostgreSQL, File system) to Sandhi AI agents. Tools are configured per business (tenant) in the Sandhi AI backend; this server resolves them via the backend internal API and executes tool calls.

## Features

- **MCP protocol**: JSON-RPC 2.0 `initialize`, `tools/list`, `tools/call`
- **Tool discovery**: Tools are fetched from the backend per `X-MCP-Business-Id` (tenant)
- **Tool execution**: Runs Vector DB queries, read-only PostgreSQL, and file read/list under configured base path
- **Secure**: No credentials stored here; decrypted config is fetched per request from the backend using `MCP_INTERNAL_SECRET`

## Configuration

| Variable | Description |
|----------|-------------|
| `BACKEND_INTERNAL_URL` | Backend base URL (e.g. `http://backend:8000`) |
| `MCP_INTERNAL_SECRET` | Must match backend `MCP_INTERNAL_SECRET` |

The backend sets `X-MCP-Business-Id` when calling this server (e.g. from the job executor) so tools are scoped to the correct tenant.

## Run locally

```bash
pip install -r requirements.txt
export BACKEND_INTERNAL_URL=http://localhost:8000
export MCP_INTERNAL_SECRET=your-secret
uvicorn app:app --host 0.0.0.0 --port 8081
```

## Docker

```bash
docker build -t platform-mcp-server .
docker run -p 8081:8081 -e BACKEND_INTERNAL_URL=http://host.docker.internal:8000 -e MCP_INTERNAL_SECRET=secret platform-mcp-server
```

## Endpoints

- `GET /health` — Health check
- `POST /mcp` or `POST /` — MCP JSON-RPC (requires header `X-MCP-Business-Id: <business_id>`)
