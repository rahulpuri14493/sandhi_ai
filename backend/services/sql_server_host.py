"""
Azure SQL / SQL Server hostname helpers.

Uses normalized hostname suffix checks instead of loose substring matching on the
whole config string (satisfies CodeQL py/incomplete-url-substring-sanitization).
"""
from urllib.parse import urlparse


def normalized_sql_server_hostname(host: str) -> str:
    """
    Extract the logical hostname from common pymssql/TDS host forms:
    ``server``, ``tcp:server,port``, ``server:port``, ``[ipv6]:port``,
    or ``http(s)://server/...`` (hostname only).
    Returns lowercase hostname without brackets; empty if missing or invalid.
    """
    s = (host or "").strip().lower()
    if not s:
        return ""
    if "://" in s:
        parsed = urlparse(s if "://" in s else f"//{s}")
        h = (parsed.hostname or "").strip().lower()
        return h
    if s.startswith("tcp:"):
        s = s[4:].strip()
    s = s.split(",")[0].strip()
    if "/" in s or "?" in s or "#" in s:
        return ""
    if s.startswith("["):
        end = s.find("]")
        if end != -1:
            return s[1:end].strip().lower()
        return ""
    if ":" in s:
        head, _, tail = s.rpartition(":")
        if tail.isdigit():
            return head.strip().lower()
    return s.strip().lower()


def sql_server_host_is_azure_sql(host: str) -> bool:
    """True when host refers to an Azure SQL Database logical server (*.database.windows.net)."""
    hn = normalized_sql_server_hostname(host)
    if not hn:
        return False
    return hn.endswith(".database.windows.net") or hn == "database.windows.net"
