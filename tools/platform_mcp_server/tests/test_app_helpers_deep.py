"""Deep unit tests for app.py helpers, filesystem/vector edges, and JSON-RPC branches."""
import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

import app as app_module


class TestToolResultForLog:
    def test_none(self):
        s = app_module._tool_result_for_log(None)
        assert "len=0" in s and "is_error=False" in s

    def test_error_prefix(self):
        s = app_module._tool_result_for_log("Error: boom")
        assert "is_error=True" in s


class TestToJsonSafe:
    def test_nested(self):
        assert app_module._to_json_safe({"a": [1, {"b": None}]}) == {"a": [1, {"b": None}]}

    def test_to_dict_object(self):
        class O:
            def to_dict(self):
                return {"x": 1}

        assert app_module._to_json_safe(O()) == {"x": 1}

    def test_fallback_str(self):
        class Weird:
            pass

        out = app_module._to_json_safe(Weird())
        assert isinstance(out, str)


class TestParsePlatformToolId:
    def test_none_and_bad(self):
        assert app_module._parse_platform_tool_id("") is None
        assert app_module._parse_platform_tool_id("other") is None

    def test_valid(self):
        assert app_module._parse_platform_tool_id("platform_42_postgres") == 42


class TestParseUrlAndHost:
    def test_parse_url_default(self):
        h, p, sec = app_module._parse_url("")
        assert h == "localhost"
        assert p == 8080
        assert sec is False

    def test_parse_url_https(self):
        h, p, sec = app_module._parse_url("https://example.com:9443/path")
        assert h == "example.com"
        assert p == 9443
        assert sec is True

    def test_url_host_lower(self):
        assert app_module._url_host("HTTPS://FOO.BAR") == "foo.bar"


class TestWeaviateHelpers:
    def test_host_is_weaviate_cloud(self):
        assert app_module._host_is_weaviate_cloud("xxx.gcp.weaviate.cloud") is True
        assert app_module._host_is_weaviate_cloud("weaviate.cloud") is True
        assert app_module._host_is_weaviate_cloud("localhost") is False

    def test_weaviate_config_bool(self):
        assert app_module._weaviate_config_bool({}, "k") is False
        assert app_module._weaviate_config_bool({"k": True}, "k") is True
        assert app_module._weaviate_config_bool({"k": "yes"}, "k") is True

    def test_weaviate_init_timeout_seconds(self):
        assert app_module._weaviate_init_timeout_seconds({}) == 45
        assert app_module._weaviate_init_timeout_seconds({"weaviate_init_timeout_seconds": "10"}) == 10
        assert app_module._weaviate_init_timeout_seconds({"weaviate_init_timeout_seconds": "999"}) == 180
        assert app_module._weaviate_init_timeout_seconds({"weaviate_init_timeout_seconds": "bad"}) == 45


class TestPineconeHelpers:
    def test_parse_fields_json_list(self):
        assert app_module._parse_pinecone_fields_argument('["a","b"]') == ["a", "b"]

    def test_parse_fields_csv(self):
        assert app_module._parse_pinecone_fields_argument("a, b") == ["a", "b"]

    def test_normalize_result_dict(self):
        raw = {"namespace": "ns1", "matches": [{"id": "1", "score": 0.9, "metadata": {"k": "v"}}]}
        out = json.loads(app_module._pinecone_normalize_result(raw, None))
        assert out["namespace"] == "ns1"
        assert len(out["matches"]) == 1
        assert out["matches"][0]["id"] == "1"


class TestFilesystemExecute:
    def test_missing_base(self):
        assert "base_path" in app_module._execute_filesystem({}, {"path": "a"})

    def test_path_traversal(self):
        assert ".." in app_module._execute_filesystem({"base_path": "/tmp"}, {"path": "../etc"})

    def test_list_and_read(self, tmp_path):
        (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
        lst = app_module._execute_filesystem(
            {"base_path": str(tmp_path)}, {"path": ".", "action": "list"}
        )
        assert "a.txt" in lst
        body = app_module._execute_filesystem(
            {"base_path": str(tmp_path)}, {"path": "a.txt", "action": "read"}
        )
        assert body == "hi"

    def test_write_via_body(self, tmp_path):
        out = app_module._execute_filesystem(
            {"base_path": str(tmp_path)},
            {"path": "new.txt", "action": "write", "body": "data"},
        )
        assert json.loads(out)["status"] == "ok"
        assert (tmp_path / "new.txt").read_text() == "data"

    def test_write_requires_content(self, tmp_path):
        out = app_module._execute_filesystem(
            {"base_path": str(tmp_path)}, {"path": "x.txt", "action": "write"}
        )
        assert "content" in out.lower()


class TestVectorDbExecute:
    def test_missing_query(self):
        assert "query" in app_module._execute_vector_db({"url": "http://x", "api_key": "k"}, {})

    def test_success_httpx(self, monkeypatch):
        class Resp:
            status_code = 200

            def json(self):
                return {"hits": [1]}

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, *a, **k):
                return Resp()

        monkeypatch.setattr(app_module.httpx, "Client", lambda **kw: Client())
        out = app_module._execute_vector_db(
            {"url": "http://vec", "api_key": "tok"}, {"query": "q", "top_k": 3}
        )
        assert "hits" in out

    def test_placeholder_when_no_url(self):
        out = app_module._execute_vector_db({}, {"query": "q"})
        assert "configured" in out.lower()


class TestJsonRpcMore:
    def test_health(self, mcp_client: TestClient):
        r = mcp_client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_invalid_json_body(self, mcp_client: TestClient, mcp_headers: dict):
        r = mcp_client.post("/mcp", content=b"not-json", headers={**mcp_headers, "Content-Type": "application/json"})
        assert r.status_code == 400

    def test_missing_method_field(self, mcp_client: TestClient, mcp_headers: dict):
        r = mcp_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1}, headers=mcp_headers)
        assert r.status_code == 200
        assert r.json()["error"]["code"] == -32600

    def test_unknown_method(self, mcp_client: TestClient, mcp_headers: dict):
        r = mcp_client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "foo/bar"},
            headers=mcp_headers,
        )
        assert r.status_code == 200
        assert r.json()["error"]["code"] == -32601

    def test_tools_list_backend_http_error(self, mcp_client: TestClient, mcp_headers: dict):
        def boom(*a, **k):
            req = httpx.Request("GET", "http://b")
            raise httpx.HTTPStatusError("x", request=req, response=httpx.Response(503, request=req))

        with patch.object(app_module, "_fetch_platform_tools", side_effect=boom):
            r = mcp_client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                headers=mcp_headers,
            )
        assert r.json()["error"]["code"] == -32000

    def test_tools_list_generic_error(self, mcp_client: TestClient, mcp_headers: dict):
        with patch.object(app_module, "_fetch_platform_tools", side_effect=RuntimeError("x")):
            r = mcp_client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                headers=mcp_headers,
            )
        assert r.json()["error"]["code"] == -32603

    def test_tools_call_internal_error(self, mcp_client: TestClient, mcp_headers: dict):
        with patch.object(app_module, "_fetch_tool_config", return_value={"config": {}, "tool_type": "postgres"}):
            with patch.object(app_module, "execute_platform_tool", side_effect=RuntimeError("bad")):
                r = mcp_client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "platform_9_x", "arguments": {}},
                    },
                    headers=mcp_headers,
                )
        assert r.json()["error"]["code"] == -32603


class TestCorrelationSuffix:
    def test_empty_when_no_headers(self):
        req = MagicMock()
        req.headers.get.return_value = None
        assert app_module._sandhi_correlation_log_suffix(req) == ""

    def test_builds_suffix(self):
        class DH:
            def get(self, key, default=None):
                if str(key).lower() == "x-sandhi-job-id":
                    return "j1"
                return default

        req = MagicMock()
        req.headers = DH()
        s = app_module._sandhi_correlation_log_suffix(req)
        assert "job_id=j1" in s
