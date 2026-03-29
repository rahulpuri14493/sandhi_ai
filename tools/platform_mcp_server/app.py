"""
Platform MCP Server for Sandhi AI.

Exposes MCP protocol (JSON-RPC 2.0): initialize, tools/list, tools/call.
Tools are resolved per business (tenant) via the Sandhi AI backend internal API.
Implements Vector DB, PostgreSQL, and File system tools using tenant-stored config.
"""
from execution_common import safe_tool_error
from execution import (
    execute_artifact_write,
    execute_azure_blob,
    execute_bigquery_sql,
    execute_databricks_sql,
    execute_elasticsearch,
    execute_gcs,
    execute_github,
    execute_mysql,
    execute_notion,
    execute_postgres,
    execute_rest_api,
    execute_s3_family,
    execute_slack,
    execute_sqlserver_sql,
    execute_snowflake_sql,
    is_artifact_platform_write,
)
import json
import logging
import os
import re
import sys
from contextlib import asynccontextmanager
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

# Exact strings accepted by https://embed.trychroma.com for `x-chroma-embedding-model` (as of Chroma Cloud API).
CHROMA_CLOUD_HTTP_EMBED_MODELS = frozenset(
    {
        "Qwen/Qwen3-Embedding-0.6B",
        "BAAI/bge-m3",
        "sentence-transformers/all-MiniLM-L6-v2",
        "prithivida/Splade_PP_en_v1",
        "naver/efficient-splade-VI-BT-large-doc",
        "naver/efficient-splade-VI-BT-large-query",
    }
)


def _resolve_chroma_cloud_http_embed_model(config: Dict[str, Any]) -> str:
    """Pick model id for embed.trychroma.com. OpenAI model names belong in self-hosted paths only — ignore them here."""
    default = "Qwen/Qwen3-Embedding-0.6B"
    for key in ("chroma_embed_model", "embedding_model"):
        raw = config.get(key)
        if raw is None:
            continue
        s = str(raw).strip()
        if not s:
            continue
        if s in CHROMA_CLOUD_HTTP_EMBED_MODELS:
            return s
        logger.warning(
            "Chroma tool config %r=%r is not a Chroma Cloud HTTP embed model id; using %r "
            "(use **chroma_embed_model** with a HuggingFace id from the Chroma dashboard, not OpenAI names).",
            key,
            s,
            default,
        )
    return default


# Backend internal API (same network as platform)
BACKEND_BASE = os.environ.get("BACKEND_INTERNAL_URL", "http://backend:8000").strip().rstrip("/")
MCP_INTERNAL_SECRET = os.environ.get("MCP_INTERNAL_SECRET", "").strip()
INTERNAL_HEADERS = {"Content-Type": "application/json"}
if MCP_INTERNAL_SECRET:
    INTERNAL_HEADERS["X-Internal-Secret"] = MCP_INTERNAL_SECRET


@asynccontextmanager
async def _lifespan(app: FastAPI):
    logger.info("Platform MCP server started; BACKEND_INTERNAL_URL=%s", BACKEND_BASE)
    yield
    logger.info("Platform MCP server shutdown")


app = FastAPI(
    title="Sandhi AI Platform MCP Server",
    description="MCP server exposing platform-configured tools (Vector DB, Postgres, File system) per tenant.",
    version="1.0.0",
    lifespan=_lifespan,
)


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


def _tool_result_for_log(text: str, max_len: int = 12000) -> str:
    """Redacted summary for MCP tool output logs (never includes tool result content)."""
    _ = max_len  # backward-compatible signature; output content is always redacted
    if text is None:
        length = 0
        is_err = False
    else:
        length = len(text)
        is_err = str(text).startswith("Error:")
    return f"[redacted tool output] len={length} is_error={is_err}"


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
            logger.error("Filesystem list error")
            return "List error"
    if action == "write":
        raw = arguments.get("content")
        if raw is None and arguments.get("body") is not None:
            raw = arguments.get("body")
        if raw is None:
            return "Error: content is required for write"
        text = raw if isinstance(raw, str) else str(raw)
        if full.exists() and full.is_dir():
            return "Error: path is a directory; use a file path for write"
        try:
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(text, encoding="utf-8")
            return json.dumps({"status": "ok", "path": rel, "bytes_written": len(text.encode("utf-8"))})
        except Exception as e:
            return safe_tool_error("Filesystem write error", e)
    try:
        return full.read_text(encoding="utf-8", errors="replace")
    except Exception:
        logger.error("Filesystem read error")
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
        logger.warning("OpenAI embedding (user key) failed: %s", type(e).__name__)
    return None


# Message when embedding is required but user has not provided a key in tool config
_MSG_ADD_OPENAI_IN_CONFIG = (
    "Query-by-text requires embedding. Add your own **OpenAI API key** in this tool's configuration "
    "(optional field 'OpenAI API key for embedding'). The platform does not provide an API key."
)

# --- Pinecone only -----------------------------------------------------------------
# All `_pinecone_*` helpers and `_parse_pinecone_fields_argument` are used exclusively
# by `_execute_pinecone`. Chroma, Weaviate, Qdrant, `vector_db`, and PageIndex keep
# their own code paths—do not route those tools through these helpers. After edits here,
# run: pytest tests/ -q (from tools/platform_mcp_server; skip test_mcp_live_e2e if offline).
# ------------------------------------------------------------------------------------


def _pinecone_coerce_response_dict(result: Any, _depth: int = 0) -> Optional[Dict[str, Any]]:
    """Turn Pinecone OpenAPI / Pydantic responses into a plain dict so we can find nested hits."""
    if result is None or _depth > 4:
        return None
    if isinstance(result, dict):
        return result
    for attr in ("actual_instance", "value", "data"):
        inner = getattr(result, attr, None)
        if inner is not None and inner is not result:
            d = _pinecone_coerce_response_dict(inner, _depth + 1)
            if d is not None:
                return d
    model_dump = getattr(result, "model_dump", None)
    if callable(model_dump):
        for kwargs in ({"by_alias": True, "mode": "python"}, {"mode": "python"}, {}):
            try:
                dumped = model_dump(**kwargs)
            except TypeError:
                continue
            except Exception:
                break
            if isinstance(dumped, dict):
                return dumped
    to_dict = getattr(result, "to_dict", None)
    if callable(to_dict):
        try:
            dumped = to_dict()
        except Exception:
            dumped = None
        if isinstance(dumped, dict):
            return dumped
    return None


def _pinecone_hits_from_plain_dict(d: Any, _depth: int = 0) -> List[Any]:
    """Recursively find the first non-empty hits / matches / records list (prefer result.* subtree)."""
    if not isinstance(d, dict) or _depth > 10:
        return []
    for nest_key in ("result", "data", "response", "body"):
        nested = d.get(nest_key)
        if isinstance(nested, dict):
            got = _pinecone_hits_from_plain_dict(nested, _depth + 1)
            if got:
                return got
    for list_key in ("hits", "matches", "records"):
        v = d.get(list_key)
        if isinstance(v, list) and len(v) > 0:
            return v
    return []


def _pinecone_collect_hits(result: Any) -> List[Any]:
    """Collect hit/match objects from Pinecone search() or query() (SDK return shapes differ)."""
    if result is None:
        return []

    plain = _pinecone_coerce_response_dict(result)
    if plain is not None:
        from_dict = _pinecone_hits_from_plain_dict(plain)
        if from_dict:
            return from_dict

    def as_list_nonempty(v: Any) -> Optional[List[Any]]:
        if v is None:
            return None
        try:
            lst = list(v)
        except TypeError:
            return None
        return lst if lst else None

    def from_obj(obj: Any) -> Optional[List[Any]]:
        if obj is None:
            return None
        for key in ("hits", "matches", "records"):
            if isinstance(obj, dict):
                lst = as_list_nonempty(obj.get(key))
            else:
                lst = as_list_nonempty(getattr(obj, key, None))
            if lst is not None:
                return lst
        return None

    inner = getattr(result, "result", None)
    if inner is None and isinstance(result, dict):
        inner = result.get("result")
    for node in (inner, result):
        lst = from_obj(node)
        if lst is not None:
            return lst
    return []


def _pinecone_hit_to_metadata_dict(hit: Any) -> Dict[str, Any]:
    """Merge `fields` and `metadata` from integrated search hits or query matches."""
    out: Dict[str, Any] = {}
    if isinstance(hit, dict):
        fields = hit.get("fields")
        meta = hit.get("metadata")
        if isinstance(fields, dict):
            out.update(fields)
        if isinstance(meta, dict):
            for k, v in meta.items():
                if k not in out:
                    out[k] = v
        if not out:
            skip = {"id", "_id", "score", "_score", "metadata", "fields"}
            for k, v in hit.items():
                if k not in skip:
                    out[k] = v
        return out
    for attr in ("fields", "metadata"):
        src = getattr(hit, attr, None)
        if src is None:
            continue
        if isinstance(src, dict):
            for k, v in src.items():
                if k not in out:
                    out[k] = v
        elif hasattr(src, "items"):
            try:
                for k, v in src.items():
                    kk = str(k)
                    if kk not in out:
                        out[kk] = v
            except Exception:
                pass
    return out


def _pinecone_normalize_result(result: Any, ns: Optional[str]) -> str:
    """JSON string with matches[{id, score, metadata?}] for agents."""
    matches = _pinecone_collect_hits(result)
    namespace_out: Optional[str] = ns
    if hasattr(result, "namespace"):
        n = getattr(result, "namespace", None)
        if n is not None and n != "":
            namespace_out = n
    if isinstance(result, dict):
        n = result.get("namespace")
        if n is not None and n != "":
            namespace_out = n
    out: Dict[str, Any] = {"matches": [], "namespace": namespace_out}
    for m in matches:
        mid = getattr(m, "id", None) or getattr(m, "_id", None)
        if mid is None and isinstance(m, dict):
            mid = m.get("id") or m.get("_id")
        score = getattr(m, "score", None) or getattr(m, "_score", None)
        if score is None and isinstance(m, dict):
            score = m.get("score") or m.get("_score")
        meta_dict = _pinecone_hit_to_metadata_dict(m)
        entry: Dict[str, Any] = {"id": mid, "score": score}
        if meta_dict:
            entry["metadata"] = meta_dict
        out["matches"].append(entry)
    return json.dumps(out, indent=2, default=str)


def _parse_pinecone_fields_argument(raw: Any) -> Optional[List[str]]:
    if raw is None:
        return None
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except Exception:
            pass
        return [s.strip() for s in raw.split(",") if s.strip()]
    return None


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
    fields_list = _parse_pinecone_fields_argument(arguments.get("fields"))

    try:
        from pinecone import Pinecone
        pc = Pinecone(api_key=api_key)
        index = pc.Index(host=index_host)

        # 1) Try integrated text search first (no OpenAI key required)
        try:
            search_kw: Dict[str, Any] = {
                "namespace": namespace,
                "query": {"inputs": {"text": query_text}, "top_k": top_k},
                # Explicit default: some SDK/API combos omit field payloads if `fields` is unset
                "fields": fields_list if fields_list else ["*"],
            }
            search_result = index.search(**search_kw)
            normalized = _pinecone_normalize_result(search_result, namespace)
            if json.loads(normalized).get("matches") == []:
                logger.debug(
                    "Pinecone search returned 0 matches after normalize; coerced keys=%s",
                    list((_pinecone_coerce_response_dict(search_result) or {}).keys()),
                )
            return normalized
        except Exception as text_err:
            err_msg = str(text_err).lower()
            fallback = (
                "integrated" in err_msg or "inputs" in err_msg or "not supported" in err_msg
                or "text search" in err_msg or "400" in err_msg or "invalid" in err_msg
                or isinstance(text_err, AttributeError)  # e.g. .search() not in this SDK
            )
            if fallback:
                logger.info(
                    "Pinecone integrated text search not available, falling back to vector query (exc_type=%s)",
                    type(text_err).__name__,
                )
            else:
                raise

        # 2) Fall back: embed with user's OpenAI key from config then vector query
        vector = _embed_with_user_key(query_text, config)
        if not vector:
            return (
                "This Pinecone index does not support query-by-text (integrated embedding). "
                + _MSG_ADD_OPENAI_IN_CONFIG
            )
        try:
            result = index.query(
                vector=vector,
                top_k=top_k,
                namespace=namespace,
                include_metadata=True,
            )
        except TypeError:
            result = index.query(
                vector=vector,
                topK=top_k,
                namespace=namespace,
                includeMetadata=True,
            )
        return _pinecone_normalize_result(result, namespace)
    except Exception as e:
        logger.error("Pinecone query error (%s)", type(e).__name__)
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


def _host_is_weaviate_cloud(host: str) -> bool:
    """True for WCD / Weaviate Cloud hostnames (incl. *.gcp.weaviate.cloud). Must use connect_to_weaviate_cloud so gRPC host differs from REST."""
    h = (host or "").lower()
    return (
        h == "weaviate.cloud"
        or h.endswith(".weaviate.io")
        or h.endswith(".weaviate.network")
        or h.endswith(".weaviate.cloud")
    )


def _weaviate_config_bool(config: Dict[str, Any], key: str) -> bool:
    v = config.get(key)
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "on")


def _weaviate_init_timeout_seconds(config: Dict[str, Any]) -> int:
    """Client default init timeout is ~2s; Docker / WCD often needs longer."""
    try:
        raw = config.get("weaviate_init_timeout_seconds")
        if raw is None or not str(raw).strip():
            return 45
        return max(5, min(int(str(raw).strip()), 180))
    except (TypeError, ValueError):
        return 45


def _weaviate_additional_config(config: Dict[str, Any]) -> Any:
    from weaviate.classes.init import AdditionalConfig, Timeout

    return AdditionalConfig(
        timeout=Timeout(
            init=_weaviate_init_timeout_seconds(config),
            query=90,
            insert=120,
        ),
        trust_env=_weaviate_config_bool(config, "weaviate_trust_env"),
    )


def _weaviate_exception_detail(exc: BaseException) -> str:
    """Include __cause__ / __context__ — Weaviate often wraps gRPC errors with empty top message."""
    parts: List[str] = []
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    for _ in range(10):
        if cur is None or id(cur) in seen:
            break
        seen.add(id(cur))
        msg = (str(cur) or "").strip()
        if msg:
            parts.append(msg)
        nxt = cur.__cause__ or cur.__context__
        cur = nxt
    if not parts:
        return type(exc).__name__
    return " | ".join(parts)[:1200]


def _weaviate_localhost_docker_hint(url: str) -> str:
    h = _url_host(url)
    if h in ("localhost", "127.0.0.1", "::1"):
        return (
            " This MCP server appears to use localhost for Weaviate; from inside Docker, localhost is the container itself. "
            "Use host.docker.internal (Docker Desktop) or your LAN IP / Docker bridge gateway if Weaviate runs on the host."
        )
    return ""


def _weaviate_connection_refused_hint(detail: str, url: str, is_weaviate_cloud: bool) -> str:
    """errno 111 / 'connection refused' = TCP reset; almost always wrong host or port from Docker."""
    dlow = detail.lower()
    if "connection refused" not in dlow and "errno 111" not in dlow and "[111]" not in detail:
        return ""
    if is_weaviate_cloud:
        return (
            " Diagnosis: TCP connection refused to Weaviate Cloud from this container — check outbound firewall/proxy, "
            "VPN, and that the cluster URL matches the Weaviate Cloud console (no typo in hostname)."
        )
    h = _url_host(url)
    _, port, secure = _parse_url(url)
    grpc_p = 443 if secure else 50051
    parts = [
        " Diagnosis: TCP connection refused (errno 111) — from inside the platform-mcp-server container, nothing is "
        f"accepting connections to host {h!r} on REST port {port} and gRPC port {grpc_p}."
    ]
    if h in ("localhost", "127.0.0.1", "::1"):
        parts.append(
            " Fix: point the tool URL at the machine running Weaviate, not localhost — e.g. "
            "http://host.docker.internal:8080 when Weaviate runs on the Docker host. "
            "On Linux Docker, add to the platform-mcp-server service: "
            "extra_hosts: [\"host.docker.internal:host-gateway\"]."
        )
    parts.append(
        " Ensure Weaviate publishes both REST (8080) and gRPC (50051 for HTTP, or 443 for HTTPS) to that address. "
        "If Weaviate is a Compose service on the same stack, use its service name as the hostname (not localhost)."
    )
    return "".join(parts)


def _weaviate_err_message(exc: BaseException, max_len: int = 600) -> str:
    """Human-readable Weaviate client error (WeaviateQueryError often embeds the server message in str())."""
    msg = (str(exc) or "").strip()
    if not msg:
        msg = type(exc).__name__
    return msg[:max_len]


def _weaviate_result_context(config: Dict[str, Any], class_name: str) -> Dict[str, Any]:
    """Non-secret metadata echoed with query results (matches Chroma envelope pattern)."""
    cluster = (config.get("weaviate_cluster_name") or config.get("cluster_name") or "").strip()
    note = (
        f"Queried Weaviate class/collection `{class_name}`. "
        + (f"WCD cluster label (informational): `{cluster}`. " if cluster else "")
        + "The collection/class name must match your Weaviate schema (e.g. from GET /v1/schema), not the cluster display name alone."
    )
    ctx: Dict[str, Any] = {
        "weaviate_collection": class_name,
        "retrieval_note": note,
        "access_scope_note": (
            "This call used only this Sandhi user's Weaviate URL, API key, and collection settings."
        ),
    }
    if cluster:
        ctx["weaviate_cluster_name"] = cluster
    return ctx


def _weaviate_query_response_to_json(
    response: Any, *, extra: Optional[Dict[str, Any]] = None
) -> str:
    """Serialize Weaviate v4 query response.objects to JSON matches (near_text, bm25, or near_vector)."""
    out: Dict[str, Any] = {"matches": []}
    for obj in response.objects:
        entry: Dict[str, Any] = {"id": str(obj.uuid), "properties": dict(obj.properties) if obj.properties else {}}
        meta = getattr(obj, "metadata", None)
        if meta is not None:
            if getattr(meta, "score", None) is not None:
                try:
                    entry["score"] = float(meta.score)
                except (TypeError, ValueError):
                    pass
            dist = getattr(meta, "distance", None)
            if dist is not None and "score" not in entry:
                try:
                    entry["score"] = 1.0 - float(dist)
                except (TypeError, ValueError):
                    pass
        out["matches"].append(entry)
    if extra:
        out.update(extra)
    return json.dumps(out, indent=2)


def _execute_weaviate(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    """Query Weaviate: near_text, then bm25 (keyword), then OpenAI embed + near_vector."""
    url = (config.get("url") or "").strip()
    api_key = (config.get("api_key") or "").strip() or None
    class_name = (config.get("index_name") or config.get("class_name") or "Document").strip()
    cluster_label = (config.get("weaviate_cluster_name") or config.get("cluster_name") or "").strip()
    query_text = (arguments.get("query") or "").strip()
    top_k = min(max(int(arguments.get("top_k") or 5), 1), 100)
    skip_init_user = _weaviate_config_bool(config, "weaviate_skip_init_checks")
    result_ctx = _weaviate_result_context(config, class_name)

    if not query_text:
        return "Error: query is required"
    if not url:
        return "Error: Weaviate URL is not configured"

    try:
        import weaviate
        from weaviate.classes.init import Auth
        auth = Auth.api_key(api_key) if api_key else None
        host = _url_host(url)
        is_weaviate_cloud = _host_is_weaviate_cloud(host)
        if is_weaviate_cloud and not api_key:
            return (
                "Error: Weaviate Cloud requires api_key in this tool's config (cluster admin key from Weaviate Cloud console)."
            )

        add_cfg = _weaviate_additional_config(config)

        def _open_client(skip_init_checks: bool):
            if is_weaviate_cloud:
                return weaviate.connect_to_weaviate_cloud(
                    cluster_url=url.rstrip("/"),
                    auth_credentials=auth,
                    additional_config=add_cfg,
                    skip_init_checks=skip_init_checks,
                )
            host_p, port, secure = _parse_url(url)
            return weaviate.connect_to_custom(
                http_host=host_p,
                http_port=port,
                http_secure=secure,
                grpc_host=host_p,
                grpc_port=50051 if not secure else 443,
                grpc_secure=secure,
                auth_credentials=auth,
                additional_config=add_cfg,
                skip_init_checks=skip_init_checks,
            )

        client = None
        last_exc: Optional[BaseException] = None
        # Default: run startup checks; on WeaviateStartUpError retry once with skip_init_checks (slow WCD / Docker).
        skip_sequence = [True] if skip_init_user else [False, True]
        for i, skip_checks in enumerate(skip_sequence):
            try:
                client = _open_client(skip_checks)
                if skip_checks and i > 0:
                    logger.info("Weaviate connected with skip_init_checks=True after WeaviateStartUpError")
                break
            except Exception as e:
                last_exc = e
                if type(e).__name__ == "WeaviateStartUpError" and not skip_checks and not skip_init_user:
                    logger.warning(
                        "Weaviate WeaviateStartUpError with skip_init_checks=False; retrying with skip_init_checks=True"
                    )
                    continue
                raise
        if client is None:
            raise last_exc if last_exc else RuntimeError("Weaviate client connect failed")

        try:
            logger.info(
                "Weaviate query class=%s cluster_name=%s",
                class_name,
                cluster_label or "(not set)",
            )
            collection = client.collections.get(class_name)
            # 1) Semantic search via server vectorizer (no OpenAI key)
            try:
                response = collection.query.near_text(query=query_text, limit=top_k)
                return _weaviate_query_response_to_json(response, extra=result_ctx)
            except Exception as text_err:
                logger.info(
                    "Weaviate near_text failed (%s): %s; trying bm25 keyword search",
                    type(text_err).__name__,
                    _weaviate_err_message(text_err, 800),
                )
            # 2) Keyword BM25 — works for many collections without a text2vec module
            bm_err: Optional[BaseException] = None
            try:
                response = collection.query.bm25(query=query_text, limit=top_k)
                logger.info("Weaviate bm25 query succeeded")
                return _weaviate_query_response_to_json(response, extra=result_ctx)
            except Exception as e_bm:
                bm_err = e_bm
                logger.info(
                    "Weaviate bm25 failed (%s): %s; trying OpenAI embed + near_vector",
                    type(e_bm).__name__,
                    _weaviate_err_message(e_bm, 800),
                )
            # 3) Client-side embed + near_vector
            vector = _embed_with_user_key(query_text, config)
            if not vector:
                bm_why = _weaviate_err_message(bm_err) if bm_err else "n/a"
                return (
                    "Error: Weaviate cannot answer this text query with current collection settings. "
                    "near_text failed (often: no text vectorizer on the class, or query not supported). "
                    f"bm25 failed ({type(bm_err).__name__ if bm_err else 'n/a'}): {bm_why}. "
                    "Common BM25 causes: no text properties with inverted index / tokenization, "
                    "multi-tenancy without tenant filter, or class name mismatch. "
                    "Add openai_api_key + embedding_model for near_vector, or fix schema in Weaviate (vectorizer + searchable text)."
                )
            response = collection.query.near_vector(near_vector=vector, limit=top_k)
            return _weaviate_query_response_to_json(response, extra=result_ctx)
        finally:
            client.close()
    except Exception as e:
        detail = _weaviate_exception_detail(e)
        logger.error("Weaviate query error (%s): %s", type(e).__name__, detail[:800])
        uh = _url_host(url)
        is_cloud = _host_is_weaviate_cloud(uh)
        refused = _weaviate_connection_refused_hint(detail, url, is_cloud)
        hint = refused or _weaviate_localhost_docker_hint(url)
        return (
            f"Error: Weaviate connection or query failed ({type(e).__name__}): {detail[:900]}.{hint} "
            "For Weaviate Cloud: use the cluster hostname from the console, a valid admin API key, and the correct class name. "
            "Ensure outbound HTTPS/gRPC (TCP 443) from this container to Weaviate is allowed. "
            "Optional tool config: weaviate_init_timeout_seconds (default 45), weaviate_trust_env=true behind an HTTP(S) proxy, "
            "weaviate_skip_init_checks=true if startup probes fail but the cluster is up."
        )


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
                logger.info(
                    "Qdrant Cloud Document query failed, falling back to vector query (exc_type=%s)",
                    type(doc_err).__name__,
                )
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
        logger.error("Qdrant query error (%s)", type(e).__name__)
        return "Qdrant query error"


def _chroma_cloud_http_embed_query_vector(api_key: str, text: str, model: str) -> List[float]:
    """Embed query text via Chroma Cloud's embed service (same contract as chromadb ChromaCloudQwenEmbeddingFunction with task=None)."""
    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            "https://embed.trychroma.com",
            json={"instructions": "", "texts": [text]},
            headers={
                "x-chroma-token": api_key.strip(),
                "x-chroma-embedding-model": model.strip(),
                "Content-Type": "application/json",
            },
        )
        if r.status_code >= 400:
            raise RuntimeError(f"embed.trychroma.com HTTP {r.status_code}: {r.text[:600]}")
        data = r.json()
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(str(data.get("error"))[:600])
    embeddings = data.get("embeddings") if isinstance(data, dict) else None
    if not embeddings or not isinstance(embeddings, list) or not embeddings[0]:
        raise RuntimeError(f"unexpected embed response: {type(data).__name__} keys={list(data.keys()) if isinstance(data, dict) else 'n/a'}")
    vec = embeddings[0]
    if isinstance(vec, list):
        return [float(x) for x in vec]
    return [float(x) for x in list(vec)]


def _chroma_host_is_try_chroma_cloud(host: str) -> bool:
    """True if URL host points at Chroma Cloud (chromadb.CloudClient default is api.trychroma.com)."""
    h = (host or "").strip().lower()
    return h == "trychroma.com" or h.endswith(".trychroma.com")


_CHROMA_QUERY_INCLUDE = ["metadatas", "documents", "distances"]


def _chroma_meta_get_ci(meta: Dict[str, Any], want_key: str) -> Any:
    """Case-insensitive metadata lookup (Chroma keys vary by ingestion pipeline)."""
    w = want_key.lower()
    for k, v in meta.items():
        if str(k).lower() == w:
            return v
    return None


def _chroma_sender_from_metadata(meta: Any) -> tuple[Optional[str], Optional[str]]:
    """
    Best-effort sender/creator for agent-facing output. Returns (value, original_metadata_key_used).
    """
    if not isinstance(meta, dict):
        return (None, None)
    for key in (
        "from",
        "sender",
        "author",
        "user_email",
        "email",
        "customer_email",
        "submitted_by",
        "created_by",
        "user_id",
        "userid",
    ):
        v = _chroma_meta_get_ci(meta, key)
        if v is None or v == "":
            continue
        s = str(v).strip()
        if s:
            return (s, key)
    return (None, None)


def _execute_chroma(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    """Query Chroma collection. Uses Chroma Cloud (api_key + tenant + database) or HttpClient for self-hosted."""
    url = (config.get("url") or "").strip() or "http://localhost:8000"
    collection_name = (config.get("index_name") or "default").strip()
    query_text = (arguments.get("query") or "").strip()
    top_k = min(max(int(arguments.get("top_k") or 5), 1), 100)
    api_key = (
        (config.get("api_key") or config.get("chroma_api_key") or config.get("chroma_token") or "")
        .strip()
        or None
    )
    tenant = (config.get("tenant") or "").strip() or None
    database = (config.get("database") or "").strip() or None

    if not query_text:
        return "Error: query is required"

    try:
        import chromadb
        host_only = _url_host(url)
        use_cloud_sdk = bool(api_key) and _chroma_host_is_try_chroma_cloud(host_only)
        if use_cloud_sdk:
            client = chromadb.CloudClient(
                tenant=tenant,
                database=database,
                api_key=api_key,
            )
        else:
            if _chroma_host_is_try_chroma_cloud(host_only) and not api_key:
                return (
                    "Chroma Cloud (trychroma host) requires a **Chroma API key** in this tool's config "
                    "(field 'Chroma API key (for Cloud)'). Create one in Chroma Cloud → Settings. "
                    "Also set **Tenant ID** and **Database name** if your project uses them."
                )
            host, port, secure = _parse_url(url)
            headers = {"X-Chroma-Token": api_key} if api_key else None
            hc_kw: Dict[str, Any] = {"host": host, "port": port, "ssl": secure}
            if headers:
                hc_kw["headers"] = headers
            if tenant:
                hc_kw["tenant"] = tenant
            if database:
                hc_kw["database"] = database
            client = chromadb.HttpClient(**hc_kw)
        try:
            coll = client.get_collection(name=collection_name or "default")
        except Exception as ge:
            return (
                f"Chroma: no collection named {collection_name!r} ({type(ge).__name__}: {ge}). "
                "Set **Collection name** in this MCP tool to the exact name in the Chroma UI "
                "(e.g. customer-support-messages)."
            )
        # Prefer query_texts (Chroma Cloud / server embeds the query, e.g. Qwen—same as the dashboard).
        try:
            result = coll.query(
                query_texts=[query_text], n_results=top_k, include=_CHROMA_QUERY_INCLUDE
            )
        except Exception as text_err:
            if use_cloud_sdk:
                logger.warning("Chroma Cloud query_texts failed: %s", text_err)
                # Dashboard hits server-side search; the Python client may fail to rebuild Qwen EF config
                # (jsonschema vs stored metadata, e.g. task null / instructions). Same vectors as UI via embed.trychroma.com.
                embed_model = _resolve_chroma_cloud_http_embed_model(config)
                try:
                    qvec = _chroma_cloud_http_embed_query_vector(api_key, query_text, embed_model)
                    result = coll.query(
                        query_embeddings=[qvec], n_results=top_k, include=_CHROMA_QUERY_INCLUDE
                    )
                    logger.info("Chroma Cloud: embed.trychroma.com fallback succeeded after query_texts failure")
                except Exception as embed_err:
                    logger.warning("Chroma Cloud HTTP embed fallback failed: %s", embed_err)
                    return (
                        "Chroma Cloud: the Python client's `query_texts` failed (often Qwen collection metadata vs local schema). "
                        f"SDK error: {text_err}. "
                        f"HTTP embed fallback: {embed_err}. "
                        "**chroma_embed_model** must be a Chroma Cloud id (e.g. Qwen/Qwen3-Embedding-0.6B), not an OpenAI model name."
                    )
            # Self-hosted: optional local embed via OpenAI when the server has no embedding for query_texts
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
                    "Chroma (self-hosted): `query_texts` failed and no **OpenAI API key** is set for local embedding. "
                    f"Details: {reason}. "
                    "Add **OpenAI API key (for query embedding)** in this tool's config, or configure a default embedding on the collection in Chroma."
                )
            try:
                result = coll.query(
                    query_embeddings=[vector], n_results=top_k, include=_CHROMA_QUERY_INCLUDE
                )
            except Exception as dim_err:  # e.g. InvalidArgumentError: dimension 1024 vs 1536
                err_str = str(dim_err)
                match = re.search(r"dimension\s+of\s+(\d+)", err_str, re.IGNORECASE) or re.search(
                    r"expecting\s+embedding\s+with\s+dimension\s+of\s+(\d+)", err_str, re.IGNORECASE
                )
                if match:
                    want_dim = int(match.group(1))
                    vector = _embed_with_user_key(query_text, config, dimensions=want_dim)
                    if vector and len(vector) == want_dim:
                        result = coll.query(
                            query_embeddings=[vector],
                            n_results=top_k,
                            include=_CHROMA_QUERY_INCLUDE,
                        )
                    else:
                        return (
                            f"Chroma dimension mismatch: collection expects {want_dim}-dim embeddings. "
                            f"Set **embedding_model** to a model that supports dimensions (e.g. text-embedding-3-small) and optionally **embedding_dimension** to {want_dim} in this tool's config."
                        )
                else:
                    raise
        out = {
            "ids": result.get("ids", [[]])[0],
            "metadatas": result.get("metadatas", [[]])[0],
            "documents": result.get("documents", [[]])[0],
            "distances": (result.get("distances") or [[]])[0],
        }
        # Normalize to matches list for consistency
        matches = []
        ids = out["ids"] or []
        metas = out["metadatas"] or []
        docs = out["documents"] or []
        dists = out["distances"] or []
        for i, id_ in enumerate(ids):
            m: Dict[str, Any] = {"id": id_}
            raw_meta = metas[i] if i < len(metas) else None
            if raw_meta:
                safe_meta = _to_json_safe(raw_meta)
                if isinstance(safe_meta, dict):
                    m["metadata"] = safe_meta
                sender_val, sender_key = _chroma_sender_from_metadata(raw_meta)
                if sender_val:
                    m["sender"] = sender_val
                if sender_key:
                    m["sender_metadata_key"] = sender_key
            if i < len(dists) and dists[i] is not None:
                try:
                    m["distance"] = float(dists[i])
                except (TypeError, ValueError):
                    pass
            if i < len(docs) and docs[i]:
                m["document"] = _to_json_safe(docs[i])
            matches.append(m)
        envelope: Dict[str, Any] = {
            "matches": matches,
            "retrieval_note": (
                "Results are ranked by vector similarity to the query, not by exact match on words like an email address. "
                "Always report each hit's `sender` (and full `metadata`) so the reader sees who the message is from."
            ),
            "access_scope_note": (
                "This call used only the Chroma collection and API credentials stored in this Sandhi user's MCP tool. "
                "Other Sandhi users cannot invoke this tool configuration."
            ),
        }
        return json.dumps(envelope, indent=2)
    except Exception as e:
        logger.error("Chroma query error (%s)", type(e).__name__)
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
            logger.error("Vector DB query error (%s)", type(e).__name__)
            return "Vector query error"
    return "Vector DB tool is configured; add a compatible endpoint (e.g. Pinecone/Weaviate/Qdrant/Chroma) for live queries."


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
        logger.error("PageIndex error (%s)", type(e).__name__)
        return "PageIndex error"


def execute_platform_tool(tool_type: str, config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    """Dispatch to the right tool implementation."""
    if is_artifact_platform_write(arguments):
        return execute_artifact_write(tool_type, config, arguments)
    if tool_type == "postgres":
        return execute_postgres(config, arguments)
    if tool_type == "mysql":
        return execute_mysql(config, arguments)
    if tool_type == "sqlserver":
        return execute_sqlserver_sql(config, arguments)
    if tool_type == "snowflake":
        return execute_snowflake_sql(config, arguments)
    if tool_type == "bigquery":
        return execute_bigquery_sql(config, arguments)
    if tool_type == "databricks":
        return execute_databricks_sql(config, arguments)
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
    if tool_type == "elasticsearch":
        return execute_elasticsearch(config, arguments)
    if tool_type in ("s3", "minio", "ceph"):
        return execute_s3_family(tool_type, config, arguments)
    if tool_type == "azure_blob":
        return execute_azure_blob(config, arguments)
    if tool_type == "gcs":
        return execute_gcs(config, arguments)
    if tool_type == "slack":
        return execute_slack(config, arguments)
    if tool_type == "github":
        return execute_github(config, arguments)
    if tool_type == "notion":
        return execute_notion(config, arguments)
    if tool_type == "rest_api":
        return execute_rest_api(config, arguments)
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
            status = e.response.status_code if e.response is not None else None
            logger.error("Backend tools/list failed business_id=%s http_status=%s", business_id, status)
            return JSONResponse({
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {"code": -32000, "message": "Failed to fetch tool list from backend"},
            })
        except Exception as e:
            logger.error("tools/list error (%s)", type(e).__name__)
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
            logger.info(
                "MCP tools/call business_id=%s tool=%s tool_type=%s is_error=%s result_chars=%s result_output=%s",
                business_id,
                name,
                tool_type,
                is_err,
                len(result_text),
                _tool_result_for_log(result_text),
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
            status = e.response.status_code if e.response is not None else None
            logger.error(
                "Backend config fetch failed business_id=%s tool_id=%s http_status=%s",
                business_id,
                tool_id,
                status,
            )
            return JSONResponse({
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {"code": -32000, "message": "Failed to fetch tool configuration from backend"},
            })
        except Exception as e:
            logger.error("tools/call error business_id=%s tool=%s (%s)", business_id, name, type(e).__name__)
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
