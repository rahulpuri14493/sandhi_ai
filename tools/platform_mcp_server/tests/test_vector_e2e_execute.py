"""
Optional smoke tests: execute_platform_tool against real providers using **environment variables only**.

Never commit API keys. Copy values from your local .env (not tracked) when running:

  pytest tests/test_vector_e2e_execute.py -m vector_e2e -v

Weaviate:   WEAVIATE_E2E_URL, WEAVIATE_E2E_CLASS, WEAVIATE_E2E_API_KEY (required for WCD),
            optional WEAVIATE_E2E_QUERY, WEAVIATE_E2E_TOP_K, WEAVIATE_E2E_CLUSTER_NAME
Pinecone:   PINECONE_E2E_API_KEY, PINECONE_E2E_HOST (https://...index...pinecone.io),
            optional PINECONE_E2E_QUERY, PINECONE_E2E_TOP_K, PINECONE_E2E_OPENAI_API_KEY, PINECONE_E2E_EMBEDDING_MODEL
Qdrant:     QDRANT_E2E_URL, QDRANT_E2E_COLLECTION, QDRANT_E2E_API_KEY (cloud),
            optional QDRANT_E2E_QUERY, QDRANT_E2E_TOP_K, QDRANT_E2E_EMBEDDING_MODEL, QDRANT_E2E_OPENAI_API_KEY
Chroma:     CHROMA_E2E_URL, CHROMA_E2E_COLLECTION (Chroma **collection** name; same value as MCP field index_name),
            CHROMA_E2E_API_KEY, optional CHROMA_E2E_TENANT, CHROMA_E2E_DATABASE, CHROMA_E2E_QUERY, CHROMA_E2E_TOP_K
            Legacy alias: CHROMA_E2E_INDEX_NAME or CHROMA_E2E_COLLECTION_NAME
vector_db:  VECTOR_DB_E2E_URL, VECTOR_DB_E2E_API_KEY, optional VECTOR_DB_E2E_QUERY, VECTOR_DB_E2E_TOP_K
"""
from __future__ import annotations

import json
import os

import pytest

from app import execute_platform_tool

pytestmark = pytest.mark.vector_e2e


def _env(*keys: str) -> tuple[dict[str, str], list[str]]:
    out: dict[str, str] = {}
    missing: list[str] = []
    for k in keys:
        v = (os.environ.get(k) or "").strip()
        if not v:
            missing.append(k)
        else:
            out[k] = v
    return out, missing


def _assert_ok_vector_output(text: str) -> None:
    assert text and isinstance(text, str), "empty tool output"
    assert not text.startswith("Error:"), text[:1500]
    low = text.lower()
    assert "pinecone query error" not in low
    assert "qdrant query error" not in low
    assert "chroma query error" not in low
    if text.strip().startswith("{"):
        data = json.loads(text)
        if "matches" in data:
            assert isinstance(data["matches"], list)


def test_e2e_weaviate_execute():
    cfg, miss = _env("WEAVIATE_E2E_URL", "WEAVIATE_E2E_CLASS")
    if miss:
        pytest.skip(f"set {', '.join(miss)}")
    api_key = (os.environ.get("WEAVIATE_E2E_API_KEY") or "").strip()
    if not api_key and ".weaviate.cloud" in cfg["WEAVIATE_E2E_URL"].lower():
        pytest.skip("WEAVIATE_E2E_API_KEY required for Weaviate Cloud")
    config: dict = {"url": cfg["WEAVIATE_E2E_URL"], "index_name": cfg["WEAVIATE_E2E_CLASS"]}
    if api_key:
        config["api_key"] = api_key
    cluster = (os.environ.get("WEAVIATE_E2E_CLUSTER_NAME") or "").strip()
    if cluster:
        config["weaviate_cluster_name"] = cluster
    q = (os.environ.get("WEAVIATE_E2E_QUERY") or "sample").strip()
    top_k = int((os.environ.get("WEAVIATE_E2E_TOP_K") or "3").strip() or "3")
    out = execute_platform_tool("weaviate", config, {"query": q, "top_k": top_k})
    _assert_ok_vector_output(out)


def test_e2e_pinecone_execute():
    cfg, miss = _env("PINECONE_E2E_API_KEY", "PINECONE_E2E_HOST")
    if miss:
        pytest.skip(f"set {', '.join(miss)}")
    config: dict = {"api_key": cfg["PINECONE_E2E_API_KEY"], "url": cfg["PINECONE_E2E_HOST"]}
    oa = (os.environ.get("PINECONE_E2E_OPENAI_API_KEY") or "").strip()
    em = (os.environ.get("PINECONE_E2E_EMBEDDING_MODEL") or "").strip()
    if oa:
        config["openai_api_key"] = oa
    if em:
        config["embedding_model"] = em
    q = (os.environ.get("PINECONE_E2E_QUERY") or "sample").strip()
    top_k = int((os.environ.get("PINECONE_E2E_TOP_K") or "3").strip() or "3")
    out = execute_platform_tool("pinecone", config, {"query": q, "top_k": top_k})
    _assert_ok_vector_output(out)


def test_e2e_qdrant_execute():
    cfg, miss = _env("QDRANT_E2E_URL", "QDRANT_E2E_COLLECTION")
    if miss:
        pytest.skip(f"set {', '.join(miss)}")
    config: dict = {"url": cfg["QDRANT_E2E_URL"], "index_name": cfg["QDRANT_E2E_COLLECTION"]}
    ak = (os.environ.get("QDRANT_E2E_API_KEY") or "").strip()
    if ak:
        config["api_key"] = ak
    em = (os.environ.get("QDRANT_E2E_EMBEDDING_MODEL") or "text-embedding-3-small").strip()
    if em:
        config["embedding_model"] = em
    oa = (os.environ.get("QDRANT_E2E_OPENAI_API_KEY") or "").strip()
    if oa:
        config["openai_api_key"] = oa
    q = (os.environ.get("QDRANT_E2E_QUERY") or "sample").strip()
    top_k = int((os.environ.get("QDRANT_E2E_TOP_K") or "3").strip() or "3")
    out = execute_platform_tool("qdrant", config, {"query": q, "top_k": top_k})
    _assert_ok_vector_output(out)


def _chroma_collection_env() -> str:
    """Chroma UI uses *collection*; MCP tool config still stores it under key index_name."""
    return (
        (os.environ.get("CHROMA_E2E_COLLECTION") or "").strip()
        or (os.environ.get("CHROMA_E2E_COLLECTION_NAME") or "").strip()
        or (os.environ.get("CHROMA_E2E_INDEX_NAME") or "").strip()
    )


def test_e2e_chroma_execute():
    cfg, miss = _env("CHROMA_E2E_URL", "CHROMA_E2E_API_KEY")
    if miss:
        pytest.skip(f"set {', '.join(miss)}")
    collection = _chroma_collection_env()
    if not collection:
        pytest.skip(
            "set CHROMA_E2E_COLLECTION (Chroma collection name). "
            "Aliases: CHROMA_E2E_COLLECTION_NAME, CHROMA_E2E_INDEX_NAME"
        )
    config: dict = {
        "url": cfg["CHROMA_E2E_URL"],
        "index_name": collection,
        "api_key": cfg["CHROMA_E2E_API_KEY"],
    }
    tenant = (os.environ.get("CHROMA_E2E_TENANT") or "").strip()
    database = (os.environ.get("CHROMA_E2E_DATABASE") or "").strip()
    if tenant:
        config["tenant"] = tenant
    if database:
        config["database"] = database
    q = (os.environ.get("CHROMA_E2E_QUERY") or "sample").strip()
    top_k = int((os.environ.get("CHROMA_E2E_TOP_K") or "3").strip() or "3")
    out = execute_platform_tool("chroma", config, {"query": q, "top_k": top_k})
    _assert_ok_vector_output(out)


def test_e2e_vector_db_execute():
    cfg, miss = _env("VECTOR_DB_E2E_URL", "VECTOR_DB_E2E_API_KEY")
    if miss:
        pytest.skip(f"set {', '.join(miss)}")
    config = {"url": cfg["VECTOR_DB_E2E_URL"], "api_key": cfg["VECTOR_DB_E2E_API_KEY"]}
    q = (os.environ.get("VECTOR_DB_E2E_QUERY") or "sample").strip()
    top_k = int((os.environ.get("VECTOR_DB_E2E_TOP_K") or "3").strip() or "3")
    out = execute_platform_tool("vector_db", config, {"query": q, "top_k": top_k})
    _assert_ok_vector_output(out)
