"""Slack, GitHub, Notion, REST integrations."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


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
            r = client.conversations_list(limit=200)
            ch = [c.get("name") for c in r.get("channels") or []]
            return json.dumps({"channels": ch}, indent=2)
        channel = (arguments.get("channel") or config.get("default_channel") or "").strip()
        message = (arguments.get("message") or "").strip()
        if not channel or not message:
            return "Error: channel and message are required for send"
        client.chat_postMessage(channel=channel, text=message)
        return json.dumps({"status": "ok"})
    except SlackApiError as e:
        return f"Error: {e.response['error']}"
    except Exception as e:
        logger.exception("Slack error")
        return f"Error: {e}"


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
            g = Github(login_or_token=token)
        else:
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
        logger.exception("GitHub error")
        return f"Error: {e}"


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
        logger.exception("Notion error")
        return f"Error: {e}"


def execute_rest_api(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    import httpx

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
    headers = {}
    if config.get("api_key"):
        headers["Authorization"] = f"Bearer {config.get('api_key')}"
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.request(method, url, json=arguments.get("body"), headers=headers)
            ct = r.headers.get("content-type", "")
            body = r.json() if ct.startswith("application/json") else r.text
            return json.dumps({"status": r.status_code, "body": body})
    except Exception as e:
        logger.exception("REST API error")
        return f"Error: {e}"

