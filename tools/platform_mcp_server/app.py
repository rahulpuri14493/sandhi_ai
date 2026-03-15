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
import sys
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

# Log to stdout: <datetime>.<type>.<message>
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(levelname)s.%(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Sandhi AI Platform MCP Server",
    description="MCP server exposing platform-configured tools (Vector DB, Postgres, File system) per tenant.",
    version="1.0.0",
)


@app.on_event("startup")
def startup():
    logger.info("Platform MCP server started; BACKEND_INTERNAL_URL=%s", BACKEND_BASE)

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


def _to_json_safe(obj: Any) -> Any:
    """Convert object to JSON-serializable form (e.g. Chroma SparseVector in metadata)."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_json_safe(x) for x in obj]
    # Chroma SparseVector and similar types
    if hasattr(obj, "to_dict") and callable(getattr(obj, "to_dict")):
        return _to_json_safe(obj.to_dict())
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


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
    """Run a read-only query against configured PostgreSQL.

    The SQL query text must come from trusted configuration (config["query"]),
    while user-controlled values are passed separately via parameters
    (arguments["params"]) and bound using psycopg2's parameterized execution.
    """
    import psycopg2
    conn_str = config.get("connection_string") or ""
    if not conn_str:
        return "Error: connection_string not configured"
    query = (config.get("query") or "").strip()
    if not query:
        return "Error: query is not configured for this tool"
    if not query.upper().startswith("SELECT"):
        return "Error: only SELECT queries are allowed"
    params = arguments.get("params")
    try:
        conn = psycopg2.connect(conn_str)
        conn.set_session(readonly=True)
        cur = conn.cursor()
        if params is None:
            cur.execute(query)
        else:
            cur.execute(query, params)
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
        logger.exception("Postgres query error")
        return "Query error"


def _execute_filesystem(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    """Read file or list directory under base_path."""
    import pathlib
    base = (config.get("base_path") or "").strip()
    if not base:
        return "Error: base_path not configured"
    rel = (arguments.get("path") or "").strip()
    if not rel:
        return "Error: path is required"
    if ".." in rel or rel.startswith("/"):
        return "Error: path must be relative and not contain .."
    # Build path only from resolved base + sanitized segments (no user string in path expression)
    base_resolved = pathlib.Path(base).resolve()
    parts = [p for p in rel.replace("\\", "/").split("/") if p and p not in (".", "..")]
    # codeql[py/path-injection] path built from allowlisted segments only; resolved path checked under base_resolved
    full = base_resolved.joinpath(*parts) if parts else base_resolved
    try:
        full = full.resolve()
        try:
            if not full.is_relative_to(base_resolved):
                return "Error: path escapes base_path"
        except AttributeError:
            if full != base_resolved and base_resolved not in full.parents:
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
        except Exception:
            logger.exception("Filesystem list error")
            return "List error"
    try:
        return full.read_text(encoding="utf-8", errors="replace")
    except Exception:
        logger.exception("Filesystem read error")
        return "Read error"


def _embed_with_user_key(
    query_text: str, config: Dict[str, Any], dimensions: Optional[int] = None
) -> Optional[List[float]]:
    """Embed query text using the end-user's OpenAI API key from tool config. Platform does not provide a key.
    dimensions: optional output size for text-embedding-3-* (e.g. 1024 to match Chroma collection)."""
    api_key = (config.get("openai_api_key") or config.get("embedding_api_key") or "").strip()
    if not api_key:
        return None
    model = (config.get("embedding_model") or "text-embedding-3-small").strip()
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        kwargs: Dict[str, Any] = {"model": model, "input": query_text}
        if dimensions is not None and "text-embedding-3" in model:
            kwargs["dimensions"] = dimensions
        r = client.embeddings.create(**kwargs)
        if r.data and len(r.data) > 0:
            return r.data[0].embedding
    except Exception as e:
        logger.warning("OpenAI embedding (user key) failed: %s", e)
    return None


# Message when embedding is required but user has not provided a key in tool config
_MSG_ADD_OPENAI_IN_CONFIG = (
    "Query-by-text requires embedding. Add your own **OpenAI API key** in this tool's configuration "
    "(optional field 'OpenAI API key for embedding'). The platform does not provide an API key."
)


def _execute_pinecone(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    """Query Pinecone index. Prefer integrated text search (no OpenAI). Fall back to OpenAI embed + vector query if needed."""
    api_key = (config.get("api_key") or "").strip()
    host = (config.get("url") or config.get("host") or "").strip().rstrip("/")
    query_text = (arguments.get("query") or "").strip()
    top_k = min(max(int(arguments.get("top_k") or 5), 1), 10000)
    # Pinecone default namespace: API uses "__default__"; UI often shows "_default_"
    namespace = (arguments.get("namespace") or config.get("namespace") or "").strip() or "__default__"
    if namespace == "_default_":
        namespace = "__default__"

    if not query_text:
        return "Error: query is required"
    if not api_key:
        return "Error: Pinecone API key is not configured"
    if not host:
        return "Error: Pinecone host (URL) is not configured. Use the index host from Pinecone console."

    index_host = host.replace("https://", "").replace("http://", "").split("/")[0].strip()

    def _normalize_result(result: Any, ns: Optional[str]) -> str:
        """Build a consistent JSON response from Pinecone search/query result.
        search() returns result.result.hits with _id, _score, fields; query() returns matches with id, score, metadata."""
        matches = getattr(result, "matches", None) or getattr(result, "hits", None)
        if matches is None:
            inner = getattr(result, "result", None) or (result.get("result") if isinstance(result, dict) else None)
            if inner is not None:
                matches = getattr(inner, "hits", None) or (inner.get("hits") if isinstance(inner, dict) else None)
        if matches is None and isinstance(result, dict):
            matches = result.get("matches") or result.get("hits") or []
        matches = matches or []
        namespace_out = getattr(result, "namespace", None) or (getattr(result, "namespace", None) if hasattr(result, "namespace") else None) or ns
        out = {"matches": [], "namespace": namespace_out}
        for m in matches:
            mid = getattr(m, "id", None) or getattr(m, "_id", None)
            if mid is None and isinstance(m, dict):
                mid = m.get("id") or m.get("_id")
            score = getattr(m, "score", None) or getattr(m, "_score", None)
            if score is None and isinstance(m, dict):
                score = m.get("score") or m.get("_score")
            meta = getattr(m, "metadata", None) or getattr(m, "fields", None)
            if meta is None and isinstance(m, dict):
                meta = m.get("metadata") or m.get("fields") or {k: v for k, v in m.items() if k not in ("id", "_id", "score", "_score")}
            entry = {"id": mid, "score": score}
            if meta:
                entry["metadata"] = dict(meta) if hasattr(meta, "items") else meta
            out["matches"].append(entry)
        return json.dumps(out, indent=2)

    try:
        from pinecone import Pinecone
        pc = Pinecone(api_key=api_key)
        index = pc.Index(host=index_host)

        # 1) Try integrated text search first (no OpenAI key required)
        try:
            search_result = index.search(
                namespace=namespace,
                query={"inputs": {"text": query_text}, "top_k": top_k},
            )
            return _normalize_result(search_result, namespace)
        except Exception as text_err:
            err_msg = str(text_err).lower()
            fallback = (
                "integrated" in err_msg or "inputs" in err_msg or "not supported" in err_msg
                or "text search" in err_msg or "400" in err_msg or "invalid" in err_msg
                or isinstance(text_err, AttributeError)  # e.g. .search() not in this SDK
            )
            if fallback:
                logger.info("Pinecone integrated text search not available, falling back to vector query: %s", text_err)
            else:
                raise

        # 2) Fall back: embed with user's OpenAI key from config then vector query
        vector = _embed_with_user_key(query_text, config)
        if not vector:
            return (
                "This Pinecone index does not support query-by-text (integrated embedding). "
                + _MSG_ADD_OPENAI_IN_CONFIG
            )
        payload = {"vector": vector, "topK": top_k, "includeMetadata": True, "namespace": namespace}
        result = index.query(**payload)
        return _normalize_result(result, namespace)
    except Exception as e:
        if "OPENAI" not in str(e) and "embed" not in str(e).lower():
            logger.exception("Pinecone query error")
        return "Pinecone query error"


def _parse_url(url_str: str) -> tuple:
    """Return (host, port, secure) from URL."""
    from urllib.parse import urlparse
    u = urlparse(url_str.strip() or "http://localhost")
    host = u.hostname or "localhost"
    port = u.port or (443 if u.scheme == "https" else 8080)
    secure = u.scheme == "https"
    return host, port, secure


def _url_host(url_str: str) -> str:
    """Return lowercase hostname from URL for safe domain checks (avoids substring-in-URL)."""
    from urllib.parse import urlparse
    u = urlparse((url_str or "").strip() or "http://localhost")
    return (u.hostname or "localhost").lower()


def _execute_weaviate(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    """Query Weaviate. Try near_text first (no key). If that fails, use user's OpenAI key from config + near_vector."""
    url = (config.get("url") or "").strip()
    api_key = (config.get("api_key") or "").strip() or None
    class_name = (config.get("index_name") or config.get("class_name") or "Document").strip()
    query_text = (arguments.get("query") or "").strip()
    top_k = min(max(int(arguments.get("top_k") or 5), 1), 100)

    if not query_text:
        return "Error: query is required"
    if not url:
        return "Error: Weaviate URL is not configured"

    try:
        import weaviate
        from weaviate.classes.init import Auth
        auth = Auth.api_key(api_key) if api_key else None
        host = _url_host(url)
        is_weaviate_cloud = host == "weaviate.cloud" or host.endswith(".weaviate.io")
        if is_weaviate_cloud:
            client = weaviate.connect_to_weaviate_cloud(
                cluster_url=url.rstrip("/"),
                auth_credentials=auth,
            )
        else:
            host, port, secure = _parse_url(url)
            client = weaviate.connect_to_custom(
                http_host=host,
                http_port=port,
                http_secure=secure,
                grpc_host=host,
                grpc_port=50051 if not secure else 443,
                grpc_secure=secure,
                auth_credentials=auth,
            )
        try:
            collection = client.collections.get(class_name)
            # 1) Try near_text first (Weaviate server vectorizer; no key from us)
            try:
                response = collection.query.near_text(query=query_text, limit=top_k)
                out = {"matches": []}
                for obj in response.objects:
                    entry = {"id": str(obj.uuid), "properties": dict(obj.properties) if obj.properties else {}}
                    if hasattr(obj, "metadata") and obj.metadata and getattr(obj.metadata, "distance", None) is not None:
                        entry["score"] = 1 - (obj.metadata.distance or 0)
                    out["matches"].append(entry)
                return json.dumps(out, indent=2)
            except Exception as text_err:
                logger.info("Weaviate near_text not available, trying embed + near_vector: %s", text_err)
            # 2) Fall back: user's OpenAI key from config + near_vector
            vector = _embed_with_user_key(query_text, config)
            if not vector:
                return "Weaviate collection has no vectorizer for query-by-text. " + _MSG_ADD_OPENAI_IN_CONFIG
            response = collection.query.near_vector(near_vector=vector, limit=top_k)
            out = {"matches": []}
            for obj in response.objects:
                entry = {"id": str(obj.uuid), "properties": dict(obj.properties) if obj.properties else {}}
                if hasattr(obj, "metadata") and obj.metadata and getattr(obj.metadata, "distance", None) is not None:
                    entry["score"] = 1 - (obj.metadata.distance or 0)
                out["matches"].append(entry)
            return json.dumps(out, indent=2)
        finally:
            client.close()
    except Exception as e:
        logger.exception("Weaviate query error")
        return "Weaviate query error"


def _execute_qdrant(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    """Query Qdrant collection. Prefer Qdrant Cloud inference (Document text+model) when available; else user's OpenAI key + vector."""
    url = (config.get("url") or "").strip() or "http://localhost:6333"
    api_key = (config.get("api_key") or "").strip() or None
    collection_name = (config.get("index_name") or "my_collection").strip()
    query_text = (arguments.get("query") or "").strip()
    top_k = min(max(int(arguments.get("top_k") or 5), 1), 100)
    # Model for Qdrant Cloud inference; must match collection. Map common display names to official IDs.
    _qdrant_model_aliases = {
        "all minilm l6 v2": "sentence-transformers/all-minilm-l6-v2",
        "all-minilm-l6-v2": "sentence-transformers/all-minilm-l6-v2",
        "minilm l6 v2": "sentence-transformers/all-minilm-l6-v2",
        "multilingual e5 small": "intfloat/multilingual-e5-small",
        "multilingual-e5-small": "intfloat/multilingual-e5-small",
    }
    raw_model = (config.get("embedding_model") or config.get("qdrant_model") or "sentence-transformers/all-minilm-l6-v2").strip()
    qdrant_model = _qdrant_model_aliases.get(raw_model.lower()) or raw_model

    if not query_text:
        return "Error: query is required"

    def _parse_points_result(result: Any) -> str:
        points = result.points if hasattr(result, "points") else []
        out = {"matches": []}
        for p in points:
            pid = p.id if hasattr(p, "id") else (p.get("id") if isinstance(p, dict) else None)
            score = getattr(p, "score", None) if not isinstance(p, dict) else p.get("score")
            payload = getattr(p, "payload", None) if not isinstance(p, dict) else p.get("payload")
            entry = {"id": str(pid), "score": score}
            if payload:
                entry["metadata"] = dict(payload) if hasattr(payload, "items") else payload
            out["matches"].append(entry)
        return json.dumps(out, indent=2)

    try:
        from qdrant_client import QdrantClient
        host = _url_host(url)
        is_cloud = host == "cloud.qdrant.io" or host.endswith(".cloud.qdrant.io")
        # 1) Qdrant Cloud: use Document(text=..., model=...) so Qdrant embeds server-side (no OpenAI key)
        if is_cloud and api_key:
            try:
                from qdrant_client.http.models import Document
                client = QdrantClient(url=url, api_key=api_key, cloud_inference=True)
                result = client.query_points(
                    collection_name=collection_name,
                    query=Document(text=query_text, model=qdrant_model),
                    limit=top_k,
                    with_payload=True,
                )
                return _parse_points_result(result)
            except Exception as doc_err:
                err_str = str(doc_err).lower()
                if "404" in err_str or "doesn't exist" in err_str or "not found" in err_str:
                    return (
                        f"Qdrant collection '{collection_name}' was not found. "
                        "Cluster name is not the same as collection name: in the tool config, set **Collection name** to the exact name of your collection. "
                        "List collections in Qdrant Cloud (Cluster UI → Collections) to see the correct name."
                    )
                logger.info("Qdrant Cloud Document query failed, falling back to vector query: %s", doc_err)
        # 2) Self-hosted or fallback: embed with user's OpenAI key then query by vector
        vector = _embed_with_user_key(query_text, config)
        if not vector:
            if is_cloud:
                return (
                    "Qdrant Cloud Document query failed (check embedding_model matches your collection). "
                    "Or add your OpenAI API key in this tool's config to use vector search."
                )
            return "Qdrant query-by-text requires embedding. " + _MSG_ADD_OPENAI_IN_CONFIG
        client = QdrantClient(url=url, api_key=api_key)
        result = client.query_points(
            collection_name=collection_name,
            query=vector,
            limit=top_k,
            with_payload=True,
        )
        return _parse_points_result(result)
    except Exception as e:
        logger.exception("Qdrant query error")
        return "Qdrant query error"


def _execute_chroma(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    """Query Chroma collection. Uses Chroma Cloud (api_key + tenant + database) or HttpClient for self-hosted."""
    url = (config.get("url") or "").strip() or "http://localhost:8000"
    collection_name = (config.get("index_name") or "default").strip()
    query_text = (arguments.get("query") or "").strip()
    top_k = min(max(int(arguments.get("top_k") or 5), 1), 100)
    api_key = (config.get("api_key") or "").strip() or None
    tenant = (config.get("tenant") or "").strip() or None
    database = (config.get("database") or "").strip() or None

    if not query_text:
        return "Error: query is required"

    try:
        import chromadb
        host = _url_host(url)
        is_cloud = host == "trychroma.com" or host.endswith(".trychroma.com")
        if is_cloud and api_key:
            client = chromadb.CloudClient(
                tenant=tenant,
                database=database,
                api_key=api_key,
            )
        else:
            if is_cloud and not api_key:
                return (
                    "Chroma Cloud requires an API key. Set **Chroma API key** in this tool's config "
                    "(create one in Chroma Cloud → Settings → Create API key). Optionally set Tenant ID and Database name."
                )
            host, port, _ = _parse_url(url)
            client = chromadb.HttpClient(host=host, port=port)
        coll = client.get_or_create_collection(name=collection_name or "default")
        # Prefer query_texts (Chroma's embedding); fallback to user's OpenAI key from config
        try:
            result = coll.query(query_texts=[query_text], n_results=top_k)
        except Exception as text_err:
            # Use config embedding_dimension if set (e.g. 1024) to match collection
            want_dim = None
            try:
                raw = config.get("embedding_dimension")
                if raw is not None and str(raw).strip():
                    want_dim = int(str(raw).strip())
            except (TypeError, ValueError):
                pass
            vector = _embed_with_user_key(query_text, config, dimensions=want_dim)
            if not vector:
                reason = str(text_err).strip() or "collection may have no embedding function"
                return (
                    f"Chroma query by text failed ({reason}). "
                    "Add your **OpenAI API key** in this tool's config (field 'OpenAI API key (optional, for embedding)') so the query can be embedded and searched. "
                    "The platform does not provide an API key."
                )
            try:
                result = coll.query(query_embeddings=[vector], n_results=top_k)
            except Exception as dim_err:  # e.g. InvalidArgumentError: dimension 1024 vs 1536
                err_str = str(dim_err)
                match = re.search(r"dimension\s+of\s+(\d+)", err_str, re.IGNORECASE) or re.search(
                    r"expecting\s+embedding\s+with\s+dimension\s+of\s+(\d+)", err_str, re.IGNORECASE
                )
                if match:
                    want_dim = int(match.group(1))
                    vector = _embed_with_user_key(query_text, config, dimensions=want_dim)
                    if vector and len(vector) == want_dim:
                        result = coll.query(query_embeddings=[vector], n_results=top_k)
                    else:
                        return (
                            f"Chroma dimension mismatch: collection expects {want_dim}-dim embeddings. "
                            f"Set **embedding_model** to a model that supports dimensions (e.g. text-embedding-3-small) and optionally **embedding_dimension** to {want_dim} in this tool's config."
                        )
                else:
                    raise
        out = {"ids": result.get("ids", [[]])[0], "metadatas": result.get("metadatas", [[]])[0], "documents": result.get("documents", [[]])[0]}
        # Normalize to matches list for consistency
        matches = []
        ids = out["ids"] or []
        metas = out["metadatas"] or []
        docs = out["documents"] or []
        for i, id_ in enumerate(ids):
            m = {"id": id_}
            if i < len(metas) and metas[i]:
                m["metadata"] = _to_json_safe(metas[i])
            if i < len(docs) and docs[i]:
                m["document"] = _to_json_safe(docs[i])
            matches.append(m)
        return json.dumps({"matches": matches}, indent=2)
    except Exception as e:
        logger.exception("Chroma query error")
        return "Chroma query error"


def _execute_vector_db(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    """Query vector DB (generic fallback: try POST /query or return placeholder)."""
    api_key = config.get("api_key")
    url = config.get("url") or ""
    query = (arguments.get("query") or "").strip()
    top_k = int(arguments.get("top_k") or 5)
    if not query:
        return "Error: query is required"
    if url and api_key:
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.post(
                    url.rstrip("/") + "/query",
                    json={"query": query, "top_k": top_k},
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                )
                if r.status_code == 200:
                    return json.dumps(r.json(), indent=2)
        except Exception as e:
            logger.exception("Vector DB query error")
            return "Vector query error"
    return "Vector DB tool is configured; add a compatible endpoint (e.g. Pinecone/Weaviate/Qdrant/Chroma) for live queries."


def _execute_mysql(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    """Run read-only query against MySQL. Query from config (trusted), params from arguments."""
    try:
        import pymysql
    except ImportError:
        return "Error: pymysql not installed. Add pymysql to platform_mcp_server requirements."
    query = (config.get("query") or "").strip()
    if not query:
        return "Error: query is not configured for this tool"
    if not query.upper().startswith("SELECT"):
        return "Error: only SELECT queries are allowed"
    params = arguments.get("params")
    try:
        conn = pymysql.connect(
            host=config.get("host", "localhost"),
            port=int(config.get("port", 3306)),
            user=config.get("user", ""),
            password=config.get("password", ""),
            database=config.get("database", ""),
        )
        cur = conn.cursor()
        if params is None:
            cur.execute(query)
        else:
            cur.execute(query, params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        cur.close()
        conn.close()
        if not rows:
            return "No rows returned."
        lines = ["\t".join(cols)] + ["\t".join(str(c) for c in row) for row in rows]
        return "\n".join(lines)
    except Exception as e:
        logger.exception("MySQL query error")
        return "Query error"


def _execute_pageindex(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    """
    Query PageIndex (vectorless RAG) via legacy retrieval API.
    Config: api_key (required), base_url (optional), default_doc_id (optional).
    Arguments: query (required), doc_id (optional), thinking (optional bool).
    """
    import time
    api_key = (config.get("api_key") or "").strip()
    if not api_key:
        return "Error: api_key not configured"
    base = (config.get("base_url") or "https://api.pageindex.ai").strip().rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base
    query = (arguments.get("query") or "").strip()
    if not query:
        return "Error: query is required"
    doc_id = (arguments.get("doc_id") or config.get("default_doc_id") or "").strip()
    if not doc_id:
        return "Error: doc_id is required (pass in arguments or set default_doc_id in tool config)"
    thinking = arguments.get("thinking", False)
    headers = {"api_key": api_key, "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                f"{base}/retrieval/",
                headers=headers,
                json={"doc_id": doc_id, "query": query, "thinking": thinking},
            )
            if r.status_code != 200:
                return f"PageIndex retrieval start error: {r.status_code} {r.text[:500]}"
            data = r.json()
            retrieval_id = data.get("retrieval_id")
            if not retrieval_id:
                return f"PageIndex unexpected response: {data}"
        # Poll for completion (max ~60s)
        for _ in range(24):
            time.sleep(2.5)
            with httpx.Client(timeout=15.0) as client:
                r = client.get(f"{base}/retrieval/{retrieval_id}/", headers={"api_key": api_key})
            if r.status_code != 200:
                return f"PageIndex status error: {r.status_code} {r.text[:300]}"
            data = r.json()
            status = (data.get("status") or "").lower()
            if status == "completed":
                nodes = data.get("retrieved_nodes") or []
                if not nodes:
                    return "No matching content found."
                lines = []
                for n in nodes:
                    title = n.get("title") or "(no title)"
                    node_id = n.get("node_id") or ""
                    contents = n.get("relevant_contents") or []
                    lines.append(f"[{title}] (node_id={node_id})")
                    for c in contents:
                        page_idx = c.get("page_index", "")
                        text = (c.get("relevant_content") or "").strip()
                        if text:
                            lines.append(f"  Page {page_idx}: {text}")
                return "\n".join(lines) if lines else json.dumps(data, indent=2)
            if status in ("failed", "error"):
                return f"PageIndex retrieval failed: {data.get('detail', data)}"
        return "PageIndex retrieval timed out (still processing)."
    except Exception as e:
        logger.exception("PageIndex error")
        return "PageIndex error"


def execute_platform_tool(tool_type: str, config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    """Dispatch to the right tool implementation."""
    if tool_type == "postgres":
        return _execute_postgres(config, arguments)
    if tool_type == "mysql":
        return _execute_mysql(config, arguments)
    if tool_type == "filesystem":
        return _execute_filesystem(config, arguments)
    if tool_type == "pinecone":
        return _execute_pinecone(config, arguments)
    if tool_type == "weaviate":
        return _execute_weaviate(config, arguments)
    if tool_type == "qdrant":
        return _execute_qdrant(config, arguments)
    if tool_type == "chroma":
        return _execute_chroma(config, arguments)
    if tool_type == "vector_db":
        return _execute_vector_db(config, arguments)
    if tool_type == "pageindex":
        return _execute_pageindex(config, arguments)
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
            logger.exception("Elasticsearch error")
            return "Elasticsearch error"
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
        if path.startswith("http") or "://" in path or path.startswith("/"):
            return "Error: path must be a relative path (no full URLs or leading slash)"
        if not base:
            return "Error: base_url not configured for REST API tool"
        url = base.rstrip("/") + "/" + path.lstrip("/")
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.request(method, url, json=arguments.get("body"), headers={"Authorization": f"Bearer {config.get('api_key', '')}"} if config.get("api_key") else {})
                return json.dumps({"status": r.status_code, "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text})
        except Exception as e:
            logger.exception("REST API error")
            return "REST API error"
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
        logger.warning("Invalid JSON body")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    req_id = body.get("id")
    method = (body.get("method") or "").strip()
    params = body.get("params") or {}
    if not method:
        return JSONResponse({"jsonrpc": JSONRPC_VERSION, "id": req_id, "error": {"code": -32600, "message": "Invalid method"}})
    business_id = _get_business_id(request)
    logger.info("MCP request method=%s business_id=%s id=%s", method, business_id, req_id)

    if method == "initialize":
        logger.info("MCP initialize OK business_id=%s", business_id)
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
            logger.info("MCP tools/list business_id=%s tool_count=%s", business_id, len(tools))
            return JSONResponse({
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "result": {"tools": tools, "nextCursor": None},
            })
        except httpx.HTTPStatusError as e:
            logger.exception("Backend tools/list failed business_id=%s", business_id)
            return JSONResponse({
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {"code": -32000, "message": "Failed to fetch tool list from backend"},
            })
        except Exception as e:
            logger.exception("tools/list error")
            return JSONResponse({
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {"code": -32603, "message": "Internal server error"},
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
            is_err = result_text.startswith("Error:")
            preview = (result_text[:80] + "…") if len(result_text) > 80 else result_text
            logger.info(
                "MCP tools/call business_id=%s tool=%s tool_type=%s is_error=%s result_preview=%s",
                business_id, name, tool_type, is_err, preview.replace("\n", " "),
            )
            return JSONResponse({
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": result_text}],
                    "isError": is_err,
                },
            })
        except httpx.HTTPStatusError as e:
            logger.exception("Backend config fetch failed business_id=%s tool_id=%s", business_id, tool_id)
            return JSONResponse({
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {"code": -32000, "message": "Failed to fetch tool configuration from backend"},
            })
        except Exception:
            logger.error("tools/call error business_id=%s tool=%s", business_id, name)
            return JSONResponse({
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {"code": -32603, "message": "Internal server error"},
            })

    logger.warning("MCP method not found method=%s business_id=%s", method, business_id)
    return JSONResponse({
        "jsonrpc": JSONRPC_VERSION,
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    })
