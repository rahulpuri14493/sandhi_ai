"""Slack, GitHub, Notion, REST integrations."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Dict, Tuple
from urllib.parse import urljoin, urlparse

from execution_common import safe_tool_error
from execution_http import get_sync_http_client
from execution_idempotency import cached_tool_json
from execution_read_cache import get_cached_or_run
from http_url_guard import check_url_safe_for_server_fetch, http_hosts_allow_redirect

logger = logging.getLogger(__name__)


def _rest_api_follow_redirects() -> bool:
    """Opt-in: follow redirects only on the same host as the first request, re-checking each Location URL."""
    return os.environ.get("MCP_REST_API_FOLLOW_REDIRECTS", "").strip().lower() in ("1", "true", "yes")


_REST_API_REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))
_REST_API_MAX_REDIRECTS = 10


def _rest_api_hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _rest_api_json_for_method(method: str, body: Any) -> Any:
    if body is None:
        return None
    if method in ("POST", "PUT", "PATCH"):
        return body
    return None


def _rest_api_redirect_next_method_body(method: str, status_code: int, json_body: Any) -> Tuple[str, Any]:
    """Align with common client behavior (303 GET; 301/302 strip POST body)."""
    if status_code == 303:
        return "GET", None
    if status_code in (301, 302) and method == "POST":
        return "GET", None
    return method, json_body


def _github_host_is_api_github_com(base: str) -> bool:
    """True when base URL targets GitHub.com API (not substring match on arbitrary text)."""
    s = (base or "").strip()
    if not s:
        return True
    if "://" not in s:
        s = "https://" + s
    u = urlparse(s)
    return (u.hostname or "").lower() == "api.github.com"

def execute_slack(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError
    except ImportError:
        return "Error: slack_sdk is not installed"
    token = (config.get("bot_token") or config.get("token") or "").strip()
    if not token:
        return "Error: bot_token not configured"
    action = (arguments.get("action") or "send").strip().lower()
    client = WebClient(token=token)
    try:
        if action == "list_channels":
            def _produce_channels() -> str:
                r = client.conversations_list(
                    limit=200,
                    types="public_channel,private_channel",
                    exclude_archived=True,
                )
                detailed = []
                for c in r.get("channels") or []:
                    if not isinstance(c, dict):
                        continue
                    cid = c.get("id")
                    name = c.get("name")
                    if not cid:
                        continue
                    detailed.append(
                        {
                            "id": cid,
                            "name": name,
                            "is_private": bool(c.get("is_private")),
                        }
                    )
                return json.dumps(
                    {
                        "channels": detailed,
                        "note": (
                            "Use id for list_messages when possible; Slack accepts some names with # prefix for send."
                        ),
                    },
                    indent=2,
                )

            ck = "slack:list_channels:" + hashlib.sha256(token.encode()).hexdigest()[:40]
            return get_cached_or_run(ck, _produce_channels)
        if action == "list_messages":
            channel = (arguments.get("channel") or config.get("default_channel") or "").strip()
            if not channel:
                return "Error: channel is required for list_messages"
            try:
                lim = int(arguments.get("limit") or 50)
            except (TypeError, ValueError):
                lim = 50
            lim = max(1, min(lim, 200))
            cursor = (arguments.get("cursor") or arguments.get("oldest") or "").strip() or None
            kwargs: Dict[str, Any] = {"channel": channel, "limit": lim}
            if cursor:
                kwargs["cursor"] = cursor
            hist = client.conversations_history(**kwargs)
            msgs = hist.get("messages") or []
            slim = []
            for m in msgs:
                if not isinstance(m, dict):
                    continue
                text = str(m.get("text") or "")
                if len(text) > 4000:
                    text = text[:4000] + "…"
                slim.append(
                    {
                        "ts": m.get("ts"),
                        "user": m.get("user"),
                        "text": text,
                        "thread_ts": m.get("thread_ts"),
                        "subtype": m.get("subtype"),
                    }
                )
            meta = hist.get("response_metadata") or {}
            out: Dict[str, Any] = {"messages": slim, "has_more": bool(meta.get("next_cursor"))}
            if meta.get("next_cursor"):
                out["next_cursor"] = meta.get("next_cursor")
            return json.dumps(out, indent=2)
        channel = (arguments.get("channel") or config.get("default_channel") or "").strip()
        message = (arguments.get("message") or "").strip()
        if not channel or not message:
            return "Error: channel and message are required for send"
        idem = str(arguments.get("idempotency_key") or "").strip()

        def _post() -> str:
            client.chat_postMessage(channel=channel, text=message)
            return json.dumps({"status": "ok"})

        return cached_tool_json("slack_send", idem, _post, cache_success_only=True)
    except SlackApiError as e:
        code = (e.response or {}).get("error") or "slack_api_error"
        return f"Error: Slack API ({code})"
    except Exception as e:
        return safe_tool_error("Slack error", e)


def execute_github(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    try:
        from github import Github
    except ImportError:
        return "Error: PyGithub is not installed"
    token = (config.get("api_key") or config.get("token") or "").strip()
    if not token:
        return "Error: API token not configured"
    base = (config.get("base_url") or "https://api.github.com").rstrip("/")
    action = (arguments.get("action") or "get_file").strip().lower()
    repo_s = (arguments.get("repo") or "").strip()
    if not repo_s:
        return "Error: repo (owner/name) is required"
    try:
        if _github_host_is_api_github_com(base):
            try:
                from github import Auth

                g = Github(auth=Auth.Token(token))
            except (ImportError, AttributeError, TypeError):
                g = Github(login_or_token=token)
        else:
            try:
                from github import Auth

                g = Github(base_url=base + "/", auth=Auth.Token(token))
            except (ImportError, AttributeError, TypeError):
                g = Github(base_url=base + "/", login_or_token=token)
        repo = g.get_repo(repo_s)
        if action == "get_file":
            path = (arguments.get("path") or "").strip()
            if not path:
                return "Error: path is required"
            c = repo.get_contents(path)
            if isinstance(c, list):
                return json.dumps([{"path": x.path, "type": x.type} for x in c], indent=2)
            import base64

            data = base64.b64decode(c.content).decode("utf-8", errors="replace")
            return data
        if action == "list_issues":
            issues = repo.get_issues(state="open")
            return json.dumps([{"number": i.number, "title": i.title} for i in issues[:50]], indent=2)
        if action == "search":
            q = (arguments.get("query") or "").strip()
            r = g.search_repositories(q)
            return json.dumps([{"full_name": x.full_name} for x in r[:20]], indent=2)
        return f"Error: unknown action {action}"
    except Exception as e:
        return safe_tool_error("GitHub error", e)


def execute_notion(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    try:
        from notion_client import Client
    except ImportError:
        return "Error: notion-client is not installed"
    token = (config.get("api_key") or "").strip()
    if not token:
        return "Error: api_key not configured"
    client = Client(auth=token)
    action = (arguments.get("action") or "search").strip().lower()
    try:
        if action == "search":
            q = (arguments.get("query") or "").strip()
            r = client.search(query=q or "", page_size=20)
            return json.dumps(r.get("results") or [], indent=2, default=str)
        if action == "get_page":
            pid = (arguments.get("query") or "").strip()
            if not pid:
                return "Error: page id required in query"
            p = client.pages.retrieve(page_id=pid)
            return json.dumps(p, indent=2, default=str)
        if action == "get_database":
            did = (arguments.get("query") or "").strip()
            if not did:
                return "Error: database id required in query"
            d = client.databases.retrieve(database_id=did)
            return json.dumps(d, indent=2, default=str)
        return f"Error: unknown action {action}"
    except Exception as e:
        return safe_tool_error("Notion error", e)


def execute_rest_api(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    base = (config.get("base_url") or "").strip()
    path = (arguments.get("path") or "").strip()
    method = (arguments.get("method") or "GET").upper()
    if not path:
        return "Error: path is required"
    if path.startswith("http") or "://" in path or path.startswith("/"):
        return "Error: path must be a relative path (no full URLs or leading slash)"
    if not base:
        return "Error: base_url not configured for REST API tool"
    safe, reason = check_url_safe_for_server_fetch(base, purpose="rest_api_tool")
    if not safe:
        code = "dns_error" if "resolve" in reason.lower() else "base_url_blocked"
        return json.dumps({"error": code, "message": reason}, indent=2)
    url = base.rstrip("/") + "/" + path.lstrip("/")
    headers = {}
    if config.get("api_key"):
        headers["Authorization"] = f"Bearer {config.get('api_key')}"
    client = get_sync_http_client()
    json_body = arguments.get("body")
    try:
        if not _rest_api_follow_redirects():
            r = client.request(
                method,
                url,
                json=_rest_api_json_for_method(method, json_body),
                headers=headers,
                timeout=15.0,
                follow_redirects=False,
            )
        else:
            anchor_host = _rest_api_hostname(url)
            if not anchor_host:
                return json.dumps({"error": "base_url_blocked", "message": "Invalid base URL host"}, indent=2)
            current_method = method
            current_url = url
            current_json = json_body
            n_redir = 0
            while True:
                r = client.request(
                    current_method,
                    current_url,
                    json=_rest_api_json_for_method(current_method, current_json),
                    headers=headers,
                    timeout=15.0,
                    follow_redirects=False,
                )
                if r.status_code not in _REST_API_REDIRECT_STATUSES:
                    break
                if n_redir >= _REST_API_MAX_REDIRECTS:
                    return json.dumps(
                        {"error": "redirect_limit", "message": "Too many HTTP redirects."},
                        indent=2,
                    )
                n_redir += 1
                loc = r.headers.get("location") or r.headers.get("Location")
                if not loc or not str(loc).strip():
                    return json.dumps(
                        {
                            "error": "redirect_no_location",
                            "message": f"HTTP {r.status_code} redirect without Location header.",
                        },
                        indent=2,
                    )
                next_url = urljoin(current_url, str(loc).strip())
                safe, reason = check_url_safe_for_server_fetch(next_url, purpose="rest_api_tool_redirect")
                if not safe:
                    code = "dns_error" if "resolve" in reason.lower() else "redirect_blocked"
                    return json.dumps({"error": code, "message": reason}, indent=2)
                if not http_hosts_allow_redirect(anchor_host, _rest_api_hostname(next_url)):
                    return json.dumps(
                        {
                            "error": "redirect_host_mismatch",
                            "message": (
                                "Redirect target is not the same hostname or registrable domain as base_url "
                                "(e.g. api.example.com → cdn.example.com is allowed)."
                            ),
                        },
                        indent=2,
                    )
                current_method, current_json = _rest_api_redirect_next_method_body(
                    current_method, r.status_code, current_json
                )
                current_url = next_url
        ct = r.headers.get("content-type", "")
        body = r.json() if ct.startswith("application/json") else r.text
        return json.dumps({"status": r.status_code, "body": body})
    except Exception as e:
        return safe_tool_error("REST API error", e)

