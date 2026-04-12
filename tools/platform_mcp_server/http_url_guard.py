"""
SSRF-oriented URL checks for server-side HTTP (platform REST tool, etc.).

Keep in sync with backend/services/http_url_guard.py.

Set MCP_HTTP_ALLOW_PRIVATE_URLS=true to allow private/loopback targets (local development only).
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
from urllib.parse import urlparse

import tldextract

_METADATA_HOST_SUFFIXES = (".internal",)
_BLOCKED_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata",
        "169.254.169.254",
    }
)


def allow_private_http_urls() -> bool:
    return os.environ.get("MCP_HTTP_ALLOW_PRIVATE_URLS", "").strip().lower() in ("1", "true", "yes")


def _host_from_netloc(netloc: str) -> str:
    host = (netloc or "").strip()
    if not host:
        return ""
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    host = host.lower()
    if host.startswith("["):
        end = host.find("]")
        if end != -1:
            return host[1:end]
        return host
    if host.count(":") > 1:
        return host
    if ":" in host:
        host = host.rsplit(":", 1)[0]
    return host.strip("[]")


def check_url_safe_for_server_fetch(url: str, *, purpose: str = "http") -> tuple[bool, str]:
    raw = (url or "").strip()
    if not raw:
        return False, "URL is empty"
    if len(raw) > 2048:
        return False, "URL is too long"
    normalized = raw if "://" in raw else f"https://{raw}"
    try:
        parsed = urlparse(normalized)
    except Exception:
        return False, "URL is invalid"
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False, f"Only http and https URLs are allowed ({purpose})."
    host = _host_from_netloc(parsed.netloc)
    if not host:
        return False, "URL has no host"
    hlow = host.lower()
    if hlow in _BLOCKED_HOSTNAMES or any(hlow.endswith(sfx) for sfx in _METADATA_HOST_SUFFIXES):
        return False, "This hostname is blocked for security (SSRF protection)."
    allow_priv = allow_private_http_urls()
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_multicast or ip.is_unspecified:
            return False, "This IP address is not allowed."
        if not allow_priv and (ip.is_private or ip.is_loopback or ip.is_link_local):
            return False, _private_blocked_message()
        if ip.version == 4 and ip in ipaddress.ip_network("169.254.0.0/16"):
            return False, "Link-local addresses (169.254.0.0/16) are blocked."
    except ValueError:
        pass
    if not allow_priv:
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror as e:
            return False, f"Could not resolve host ({e})."
        for info in infos:
            ip_s = info[4][0]
            try:
                ipa = ipaddress.ip_address(ip_s)
            except ValueError:
                continue
            if ipa.is_multicast or ipa.is_unspecified:
                return False, "Host resolves to a disallowed address."
            if ipa.is_private or ipa.is_loopback or ipa.is_link_local:
                return False, _private_blocked_message()
            if ipa.version == 4 and ipa in ipaddress.ip_network("169.254.0.0/16"):
                return False, "Host resolves to link-local range 169.254.0.0/16 (blocked)."
    return True, ""


def _hostname_is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address((host or "").strip())
        return True
    except ValueError:
        return False


def http_hosts_allow_redirect(anchor_host: str, target_host: str) -> bool:
    """
    True if an HTTP redirect from anchor_host to target_host is allowed after SSRF checks:
    same hostname, or same registrable domain (eTLD+1), e.g. api.example.com → cdn.example.com.
    IP hostnames must match exactly (no cross-subdomain for IPs).
    """
    a = (anchor_host or "").strip().lower()
    b = (target_host or "").strip().lower()
    if not a or not b:
        return False
    if a == b:
        return True
    if _hostname_is_ip_literal(a) or _hostname_is_ip_literal(b):
        return False
    ex_a = tldextract.extract(a)
    ex_b = tldextract.extract(b)
    ra = ex_a.registered_domain
    rb = ex_b.registered_domain
    return bool(ra and rb and ra == rb)


def _private_blocked_message() -> str:
    return (
        "Private, loopback, and link-local URLs are blocked. "
        "For local development set environment variable MCP_HTTP_ALLOW_PRIVATE_URLS=true."
    )


def safe_url_host_for_logs(url: str, *, max_len: int = 120) -> str:
    s = (url or "").strip()
    if not s:
        return ""
    try:
        p = urlparse(s if "://" in s else f"https://{s}")
        return (p.hostname or "")[:max_len]
    except Exception:
        return re.sub(r"\s+", " ", s)[:max_len]
