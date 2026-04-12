"""Tests for services.http_url_guard (SSRF-oriented URL policy)."""

import socket

import pytest

from services import http_url_guard as g


def test_blocks_loopback_literal(monkeypatch):
    monkeypatch.delenv("MCP_HTTP_ALLOW_PRIVATE_URLS", raising=False)
    ok, msg = g.check_url_safe_for_server_fetch("http://127.0.0.1:8080/api")
    assert ok is False
    assert "loopback" in msg.lower() or "private" in msg.lower()


def test_blocks_metadata_host(monkeypatch):
    monkeypatch.delenv("MCP_HTTP_ALLOW_PRIVATE_URLS", raising=False)
    ok, msg = g.check_url_safe_for_server_fetch("http://169.254.169.254/latest/meta-data/")
    assert ok is False


def test_allows_public_when_resolve_public(monkeypatch):
    monkeypatch.delenv("MCP_HTTP_ALLOW_PRIVATE_URLS", raising=False)

    def _fake_gai(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0))]

    monkeypatch.setattr("services.http_url_guard.socket.getaddrinfo", _fake_gai)
    ok, msg = g.check_url_safe_for_server_fetch("https://example.com")
    assert ok is True
    assert msg == ""


def test_safe_url_host_for_logs_strips_path():
    assert g.safe_url_host_for_logs("https://user:pass@api.example.com/v1/x?k=1") == "api.example.com"


def test_allow_private_env(monkeypatch):
    monkeypatch.setenv("MCP_HTTP_ALLOW_PRIVATE_URLS", "true")
    ok, msg = g.check_url_safe_for_server_fetch("http://127.0.0.1:9/")
    assert ok is True


def test_http_hosts_allow_redirect_same_subdomain_registrable_domain():
    assert g.http_hosts_allow_redirect("api.example.com", "cdn.example.com") is True
    assert g.http_hosts_allow_redirect("a.api.example.com", "b.example.com") is True


def test_http_hosts_allow_redirect_rejects_other_domain():
    assert g.http_hosts_allow_redirect("api.example.com", "api.evil.com") is False


def test_http_hosts_allow_redirect_ip_no_cross():
    assert g.http_hosts_allow_redirect("203.0.113.1", "203.0.113.1") is True
    assert g.http_hosts_allow_redirect("203.0.113.1", "203.0.113.2") is False
