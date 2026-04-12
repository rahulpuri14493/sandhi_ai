"""Mocked coverage for app vector tools, Pinecone helpers, PageIndex, and generic vector_db."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module

pytestmark = pytest.mark.unit


class TestPineconeHelpers:
    def test_coerce_dict_and_model_dump(self):
        d = {"hits": [{"id": "1"}]}
        assert app_module._pinecone_coerce_response_dict(d) == d

        class M:
            def model_dump(self, **kwargs):
                return {"matches": [{"id": "a"}]}

        assert app_module._pinecone_coerce_response_dict(M()) == {"matches": [{"id": "a"}]}

    def test_parse_fields_argument(self):
        assert app_module._parse_pinecone_fields_argument(None) is None
        assert app_module._parse_pinecone_fields_argument(["a", " b "]) == ["a", "b"]
        assert app_module._parse_pinecone_fields_argument('["x","y"]') == ["x", "y"]
        assert app_module._parse_pinecone_fields_argument("a, b,c") == ["a", "b", "c"]

    def test_normalize_result_with_dict_hits(self):
        raw = {"matches": [{"id": "m1", "score": 0.5, "metadata": {"z": 1}}]}
        out = json.loads(app_module._pinecone_normalize_result(raw, "__default__"))
        assert len(out["matches"]) == 1
        assert out["matches"][0]["id"] == "m1"


class TestPineconeExecuteMocked:
    def test_validation_errors(self):
        assert "query is required" in app_module._execute_pinecone({"api_key": "k", "host": "h"}, {})
        assert "API key" in app_module._execute_pinecone({"host": "https://x.io"}, {"query": "q"})
        assert "host" in app_module._execute_pinecone({"api_key": "k"}, {"query": "q"}).lower()

    def test_integrated_search_happy_path(self):
        mock_index = MagicMock()
        mock_index.search.return_value = {"matches": [{"id": "a", "score": 0.9}]}
        mock_pc = MagicMock()
        mock_pc.Index.return_value = mock_index
        with patch("pinecone.Pinecone", return_value=mock_pc):
            out = app_module._execute_pinecone(
                {"api_key": "k", "host": "https://idx.pinecone.io"},
                {"query": "hello", "top_k": 3},
            )
        data = json.loads(out)
        assert data["matches"][0]["id"] == "a"

    def test_fallback_when_integrated_unsupported_and_no_embed(self):
        mock_index = MagicMock()
        mock_index.search.side_effect = RuntimeError("integrated text search not supported")
        mock_pc = MagicMock()
        mock_pc.Index.return_value = mock_index
        with patch("pinecone.Pinecone", return_value=mock_pc):
            with patch.object(app_module, "_embed_with_user_key", return_value=None):
                out = app_module._execute_pinecone(
                    {"api_key": "k", "host": "https://idx.pinecone.io"},
                    {"query": "hello"},
                )
        assert "OpenAI" in out or "embedding" in out.lower()

    def test_fallback_vector_query_typeerror_uses_topk_alias(self):
        mock_index = MagicMock()
        mock_index.search.side_effect = RuntimeError("integrated not supported")
        mock_index.query.side_effect = [TypeError("bad kw"), {"matches": [{"id": "v"}]}]
        mock_pc = MagicMock()
        mock_pc.Index.return_value = mock_index
        with patch("pinecone.Pinecone", return_value=mock_pc):
            with patch.object(app_module, "_embed_with_user_key", return_value=[0.1, 0.2, 0.3]):
                out = app_module._execute_pinecone(
                    {"api_key": "k", "host": "https://idx.pinecone.io"},
                    {"query": "hello", "top_k": 2},
                )
        assert "v" in out

    def test_non_fallback_search_error_surfaces(self):
        mock_index = MagicMock()
        mock_index.search.side_effect = ValueError("hard failure")
        mock_pc = MagicMock()
        mock_pc.Index.return_value = mock_index
        with patch("pinecone.Pinecone", return_value=mock_pc):
            out = app_module._execute_pinecone(
                {"api_key": "k", "host": "https://idx.pinecone.io"},
                {"query": "hello"},
            )
        assert out == "Pinecone query error"


class TestWeaviateHelpers:
    def test_exception_detail_chains(self):
        inner = RuntimeError("inner")
        outer = RuntimeError("outer")
        outer.__cause__ = inner
        msg = app_module._weaviate_exception_detail(outer)
        assert "outer" in msg and "inner" in msg

    def test_connection_refused_hint_cloud(self):
        h = app_module._weaviate_connection_refused_hint("connection refused errno 111", "https://x.weaviate.cloud", True)
        assert "Weaviate Cloud" in h

    def test_localhost_docker_hint(self):
        assert "Docker" in app_module._weaviate_localhost_docker_hint("http://localhost:8080")

    def test_query_response_to_json_scores(self):
        meta = SimpleNamespace(score=0.7, distance=None)
        obj = SimpleNamespace(uuid="u1", properties={"a": 1}, metadata=meta)
        resp = SimpleNamespace(objects=[obj])
        raw = json.loads(app_module._weaviate_query_response_to_json(resp))
        assert raw["matches"][0]["score"] == 0.7

    def test_query_response_distance_to_score(self):
        meta = SimpleNamespace(score=None, distance=0.25)
        obj = SimpleNamespace(uuid="u2", properties=None, metadata=meta)
        resp = SimpleNamespace(objects=[obj])
        raw = json.loads(app_module._weaviate_query_response_to_json(resp))
        assert abs(raw["matches"][0]["score"] - 0.75) < 1e-6


class TestWeaviateExecuteMocked:
    @patch.object(app_module, "_weaviate_additional_config", return_value=None)
    def test_near_text_success(self, _mock_add):
        mock_client = MagicMock()
        mock_coll = MagicMock()
        mock_resp = SimpleNamespace(objects=[])
        mock_coll.query.near_text.return_value = mock_resp
        mock_client.collections.get.return_value = mock_coll

        fake_init = MagicMock()
        fake_init.Auth = SimpleNamespace(api_key=lambda k: None)

        fake_weaviate = MagicMock()
        fake_weaviate.connect_to_custom.return_value = mock_client

        with patch.dict(
            sys.modules,
            {
                "weaviate": fake_weaviate,
                "weaviate.classes.init": fake_init,
            },
        ):
            out = app_module._execute_weaviate(
                {"url": "http://localhost:8080", "index_name": "Doc"},
                {"query": "q", "top_k": 2},
            )
        assert "matches" in out
        mock_client.close.assert_called_once()

    @patch.object(app_module, "_weaviate_additional_config", return_value=None)
    def test_cloud_requires_api_key_message(self, _mock_add):
        fake_init = MagicMock()
        fake_init.Auth = SimpleNamespace(api_key=lambda k: None)
        fake_weaviate = MagicMock()
        with patch.dict(sys.modules, {"weaviate": fake_weaviate, "weaviate.classes.init": fake_init}):
            out = app_module._execute_weaviate(
                {"url": "https://xyz.weaviate.network", "index_name": "Doc"},
                {"query": "q"},
            )
        assert "api_key" in out.lower()


class TestQdrantExecuteMocked:
    def test_missing_query(self):
        assert "query is required" in app_module._execute_qdrant({}, {})

    @patch.object(app_module, "_embed_with_user_key", return_value=None)
    def test_self_hosted_requires_embed_when_no_cloud_doc_path(self, _mock_emb):
        pt = SimpleNamespace(id="p1", score=0.5, payload={"a": 1})
        result = SimpleNamespace(points=[pt])
        mock_client = MagicMock()
        mock_client.query_points.return_value = result
        with patch("qdrant_client.QdrantClient", return_value=mock_client):
            out = app_module._execute_qdrant(
                {"url": "http://127.0.0.1:6333", "index_name": "col"},
                {"query": "hi"},
            )
        assert "embedding" in out.lower() or "OpenAI" in out

    @patch.object(app_module, "_embed_with_user_key", return_value=[0.1, 0.2])
    def test_vector_query_points(self, _mock_emb):
        pt = SimpleNamespace(id="p1", score=0.5, payload={"k": "v"})
        result = SimpleNamespace(points=[pt])
        mock_client = MagicMock()
        mock_client.query_points.return_value = result
        with patch("qdrant_client.QdrantClient", return_value=mock_client):
            out = app_module._execute_qdrant(
                {"url": "http://127.0.0.1:6333", "index_name": "col"},
                {"query": "hi", "top_k": 3},
            )
        data = json.loads(out)
        assert data["matches"][0]["id"] == "p1"


class TestChromaCloudHttpEmbed:
    def test_embed_query_vector_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"embeddings": [[0.1, 0.2, 0.3]]}
        mock_http = MagicMock()
        mock_http.post.return_value = mock_resp
        cm = MagicMock()
        cm.__enter__.return_value = mock_http
        cm.__exit__.return_value = None
        with patch("httpx.Client", return_value=cm):
            vec = app_module._chroma_cloud_http_embed_query_vector("tok", "hello", "Qwen/Qwen3-Embedding-0.6B")
        assert vec == [0.1, 0.2, 0.3]

    def test_embed_http_error_raises(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "bad"
        mock_http = MagicMock()
        mock_http.post.return_value = mock_resp
        cm = MagicMock()
        cm.__enter__.return_value = mock_http
        cm.__exit__.return_value = None
        with patch("httpx.Client", return_value=cm):
            with pytest.raises(RuntimeError, match="embed.trychroma.com"):
                app_module._chroma_cloud_http_embed_query_vector("t", "x", "Qwen/Qwen3-Embedding-0.6B")


class TestChromaHelpers:
    def test_meta_get_ci(self):
        assert app_module._chroma_meta_get_ci({"From": "x@y.com"}, "from") == "x@y.com"
        assert app_module._chroma_meta_get_ci({}, "from") is None

    def test_host_is_try_chroma_cloud(self):
        assert app_module._chroma_host_is_try_chroma_cloud("api.trychroma.com") is True
        assert app_module._chroma_host_is_try_chroma_cloud("localhost") is False


class TestChromaExecuteMocked:
    def test_trychroma_without_api_key(self):
        out = app_module._execute_chroma(
            {"url": "https://api.trychroma.com", "index_name": "c"},
            {"query": "q"},
        )
        assert "API key" in out or "Chroma" in out

    def test_self_hosted_query_success(self):
        mock_coll = MagicMock()
        mock_coll.query.return_value = {
            "ids": [["i1"]],
            "metadatas": [[{"sender": "s@x.com"}]],
            "documents": [["hello"]],
            "distances": [[0.2]],
        }
        mock_client = MagicMock()
        mock_client.get_collection.return_value = mock_coll
        with patch("chromadb.HttpClient", return_value=mock_client):
            out = app_module._execute_chroma(
                {"url": "http://127.0.0.1:8000", "index_name": "myidx"},
                {"query": "find", "top_k": 1},
            )
        env = json.loads(out)
        assert env["matches"][0]["id"] == "i1"
        assert "retrieval_note" in env

    def test_get_collection_failure_message(self):
        mock_client = MagicMock()
        mock_client.get_collection.side_effect = ValueError("nope")
        with patch("chromadb.HttpClient", return_value=mock_client):
            out = app_module._execute_chroma(
                {"url": "http://127.0.0.1:8000", "index_name": "missing"},
                {"query": "q"},
            )
        assert "no collection" in out.lower()


class TestVectorDbAndPageIndex:
    def test_vector_db_success_via_httpx(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        cm = MagicMock()
        cm.__enter__.return_value = mock_client
        cm.__exit__.return_value = None
        with patch("httpx.Client", return_value=cm):
            out = app_module._execute_vector_db(
                {"url": "https://api.example.com", "api_key": "tok"},
                {"query": "hi", "top_k": 2},
            )
        assert "ok" in out

    def test_vector_db_placeholder_without_creds(self):
        out = app_module._execute_vector_db({}, {"query": "x"})
        assert "configured" in out.lower()

    def test_vector_db_httpx_client_failure(self):
        cm = MagicMock()
        cm.__enter__.side_effect = RuntimeError("network")
        with patch("httpx.Client", return_value=cm):
            out = app_module._execute_vector_db(
                {"url": "http://api.example", "api_key": "k"},
                {"query": "q"},
            )
        assert "Vector query error" in out

    def test_pageindex_happy_path(self):
        post_resp = MagicMock(status_code=200)
        post_resp.json.return_value = {"retrieval_id": "r1"}
        get_resp = MagicMock(status_code=200)
        get_resp.json.return_value = {
            "status": "completed",
            "retrieved_nodes": [
                {
                    "title": "T",
                    "node_id": "n1",
                    "relevant_contents": [{"page_index": 2, "relevant_content": "body"}],
                }
            ],
        }
        mock_http = MagicMock()
        mock_http.post.return_value = post_resp
        mock_http.get.return_value = get_resp
        cm = MagicMock()
        cm.__enter__.return_value = mock_http
        cm.__exit__.return_value = None
        with patch("httpx.Client", return_value=cm):
            with patch("time.sleep", lambda s: None):
                out = app_module._execute_pageindex(
                    {"api_key": "k", "base_url": "https://api.pageindex.ai"},
                    {"query": "q", "doc_id": "d1"},
                )
        assert "Page 2" in out
        assert "body" in out

    def test_pageindex_missing_api_key_and_doc(self):
        assert "api_key" in app_module._execute_pageindex({}, {"query": "q", "doc_id": "x"}).lower()
        assert "doc_id" in app_module._execute_pageindex({"api_key": "k"}, {"query": "q"}).lower()
