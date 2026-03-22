# Sandhi AI Platform MCP Server

Platform-hosted MCP (Model Context Protocol) server that exposes **enterprise tools** (Vector DB, PostgreSQL, File system) to Sandhi AI agents. Tools are configured per business (tenant) in the Sandhi AI backend; this server resolves them via the backend internal API and executes tool calls.

## Features

- **MCP protocol**: JSON-RPC 2.0 `initialize`, `tools/list`, `tools/call`
- **Tool discovery**: Tools are fetched from the backend per `X-MCP-Business-Id` (tenant)
- **Tool execution**: Runs Vector DB queries, PostgreSQL/MySQL reads and writes (per SQL), object store get/list/put, filesystem read/list/write under configured base path, and artifact-driven platform writes where implemented
- **Secure**: No credentials stored here; decrypted config is fetched per request from the backend using `MCP_INTERNAL_SECRET`

## Configuration

| Variable | Description |
|----------|-------------|
| `BACKEND_INTERNAL_URL` | Backend base URL (e.g. `http://backend:8000`) |
| `MCP_INTERNAL_SECRET` | Must match backend `MCP_INTERNAL_SECRET` |
| `MCP_POSTGRES_INTERACTIVE_READONLY` | If `1`/`true`, interactive Postgres tool allows only `SELECT` (and read-only `WITH`); use `output_contract` for INSERT/DDL. Optional per-tool: `interactive_readonly` in encrypted tool config. |
| `MCP_S3_WRITE_KEY_PREFIX` | If set (e.g. `reports`), interactive S3/MinIO `put`/`write` keys must be under that prefix. |

**Vector stores (no platform OpenAI key):** The platform does **not** provide an OpenAI API key. When query-by-text needs an embedding, the server uses the **end-user’s** OpenAI API key from the tool configuration (optional field “OpenAI API key for embedding”). If embedding is required and the user has not provided a key, the tool returns a clear message asking them to add it in the MCP Server tool config.

| Vector store | Native text search | User OpenAI key in config |
|--------------|--------------------|---------------------------|
| **Pinecone** | Yes (index with integrated embedding) | Optional; only if index has no integrated embedding |
| **Weaviate** | Yes (`near_text` when collection has vectorizer) | Optional; only if collection has no vectorizer |
| **Qdrant** | Yes (Qdrant Cloud: `Document` + model); self-hosted: no | Optional for Qdrant Cloud; required for self-hosted |
| **Chroma** | Yes (collection embedding function) | Optional; only if collection has no embedding function. **Chroma Cloud:** set URL to `https://api.trychroma.com`, and add **Chroma API key**, **Tenant ID**, and **Database name** from Chroma Cloud → Settings. |
| **Vector DB (generic)** | Depends on endpoint | N/A (uses POST `/query` or placeholder) |

**Qdrant Cloud models:** Set the **Model** field in the tool config to the same embedding model your collection uses. Qdrant offers many models (Dense and Sparse); common examples: `sentence-transformers/all-minilm-l6-v2` (All MiniLM L6 v2, 384 dim, free), `intfloat/multilingual-e5-small` (384 dim, free), or paid options like Embed Large v1 (1024 dim). The name must match exactly what you used when creating the collection. See your Qdrant Cloud console (e.g. Inference / Embedding models) for the full list and exact model IDs.

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
