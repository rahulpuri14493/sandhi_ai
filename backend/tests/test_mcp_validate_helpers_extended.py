"""Extended unit tests for services.mcp_validate helpers and branches."""

import socket
import sys
import types

import pytest

from services import mcp_validate as mv
from services.mcp_validate import validate_tool_config


def test_normalize_http_url_edges():
    assert mv._normalize_http_url("") == ""
    assert mv._normalize_http_url("   ") == ""
    assert mv._normalize_http_url("example.com)") == "https://example.com"
    assert mv._normalize_http_url("https://h.test/path") == "https://h.test/path"


def test_http_url_has_host():
    assert mv._http_url_has_host("https://a.b") is True
    assert mv._http_url_has_host("not-a-url") is False


def test_http_reachable_success(monkeypatch):
    class R:
        status_code = 200

    monkeypatch.setattr(
        "httpx.get",
        lambda url, headers=None, timeout=None, follow_redirects=True: R(),
    )
    ok, msg = mv._http_reachable("https://x.test/")
    assert ok is True
    assert "200" in msg


def test_http_reachable_500(monkeypatch):
    class R:
        status_code = 503

    monkeypatch.setattr("httpx.get", lambda *a, **kw: R())
    ok, msg = mv._http_reachable("https://x.test/")
    assert ok is False
    assert "503" in msg


def test_http_reachable_exception(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("net")

    monkeypatch.setattr("httpx.get", boom)
    ok, msg = mv._http_reachable("https://x.test/")
    assert ok is False
    assert "reach" in msg.lower()


def test_http_reachable_restricted_follows_same_host(monkeypatch):
    calls: list = []

    def fake_get(url, headers=None, timeout=None, follow_redirects=True):
        calls.append((url, follow_redirects))
        if len(calls) == 1:

            class R302:
                status_code = 302
                headers = {"location": "/b"}

            return R302()

        class R200:
            status_code = 200
            headers = {}

        return R200()

    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr(
        "services.http_url_guard.socket.getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0))],
    )
    ok, msg = mv._http_reachable("https://x.test/a", restrict_same_host_redirects=True)
    assert ok is True
    assert "200" in msg
    assert calls[0][1] is False and calls[1][1] is False
    assert calls[1][0] == "https://x.test/b"


def test_http_reachable_restricted_allows_subdomain_redirect(monkeypatch):
    calls: list = []

    def fake_get(url, headers=None, timeout=None, follow_redirects=True):
        calls.append(url)
        if len(calls) == 1:

            class R302:
                status_code = 302
                headers = {"location": "https://cdn.example.com/b"}

            return R302()

        class R200:
            status_code = 200
            headers = {}

        return R200()

    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr(
        "services.http_url_guard.socket.getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0))],
    )
    ok, msg = mv._http_reachable("https://api.example.com/a", restrict_same_host_redirects=True)
    assert ok is True
    assert "200" in msg
    assert calls[-1] == "https://cdn.example.com/b"


def test_http_reachable_restricted_blocks_cross_host(monkeypatch):
    class R302:
        status_code = 302
        headers = {"location": "https://evil.example/other"}

    monkeypatch.setattr("httpx.get", lambda *a, **kw: R302())
    monkeypatch.setattr(
        "services.http_url_guard.socket.getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0))],
    )
    ok, msg = mv._http_reachable("https://x.test/start", restrict_same_host_redirects=True)
    assert ok is False
    assert "host" in msg.lower()


def test_http_reachable_restricted_no_location(monkeypatch):
    class R302:
        status_code = 302
        headers = {}

    monkeypatch.setattr("httpx.get", lambda *a, **kw: R302())
    monkeypatch.setattr(
        "services.http_url_guard.socket.getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0))],
    )
    ok, msg = mv._http_reachable("https://x.test/", restrict_same_host_redirects=True)
    assert ok is False
    assert "location" in msg.lower()


def test_validate_tool_config_unsupported_type():
    ok, msg = validate_tool_config("unknown_tool_xyz", {})
    assert ok is False
    assert "unsupported" in msg.lower()


def test_vector_db_missing_url():
    ok, msg = validate_tool_config("vector_db", {})
    assert ok is False
    assert "required" in msg.lower()


def test_vector_db_invalid_host():
    ok, msg = validate_tool_config("vector_db", {"url": "http://"})
    assert ok is False
    assert "invalid" in msg.lower()


def test_weaviate_success(monkeypatch):
    monkeypatch.setattr(
        "services.mcp_validate._http_reachable", lambda url, headers=None, **kwargs: (True, "ok")
    )
    ok, msg = validate_tool_config("weaviate", {"url": "https://w.test", "api_key": "k"})
    assert ok is True


def test_qdrant_with_api_key(monkeypatch):
    monkeypatch.setattr(
        "services.mcp_validate._http_reachable", lambda url, headers=None, **kwargs: (True, "ok")
    )
    ok, _ = validate_tool_config("qdrant", {"url": "https://q.test", "api_key": "abc"})
    assert ok is True


def test_chroma_v2_heartbeat_used_when_v1_fails(monkeypatch):
    def fake(url, headers=None, timeout=None, **kwargs):
        if "/api/v1/heartbeat" in url:
            return False, "v1 down"
        if "/api/v2/heartbeat" in url:
            return True, "v2 up"
        return False, "no"

    monkeypatch.setattr("services.mcp_validate._http_reachable", fake)
    ok, msg = validate_tool_config("chroma", {"url": "http://localhost:8000"})
    assert ok is True
    assert "successful" in msg.lower()


def test_elasticsearch_reachable(monkeypatch):
    monkeypatch.setattr(
        "services.mcp_validate._http_reachable", lambda url, headers=None, **kwargs: (True, "ok")
    )
    monkeypatch.setattr(
        "services.http_url_guard.socket.getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0))],
    )
    ok, _ = validate_tool_config("elasticsearch", {"url": "https://es.test:9200"})
    assert ok is True


def test_pageindex_missing_key():
    ok, msg = validate_tool_config("pageindex", {"base_url": "https://pi.test"})
    assert ok is False
    assert "api" in msg.lower() and "key" in msg.lower()


def test_pinecone_missing_pinecone_package(monkeypatch):
    """Empty pinecone module -> ImportError on Pinecone symbol (same as package missing)."""
    monkeypatch.setitem(sys.modules, "pinecone", types.ModuleType("pinecone"))
    ok, msg = validate_tool_config(
        "pinecone",
        {"api_key": "k", "host": "https://idx.pinecone.io"},
    )
    assert ok is False
    assert "pinecone" in msg.lower()
