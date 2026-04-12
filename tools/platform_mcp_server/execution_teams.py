"""Microsoft Teams via Microsoft Graph (channel messages) for platform MCP."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional
from urllib.parse import quote, urlparse

import httpx

from execution_common import safe_tool_error
from execution_contract import (
    ERROR_AUTH_FAILED,
    ERROR_PERMISSION_DENIED,
    ERROR_UNKNOWN_ACTION,
    ERROR_UPSTREAM_ERROR,
    ERROR_UPSTREAM_UNAVAILABLE,
    ERROR_VALIDATION_FAILED,
    maybe_validate_messaging_output,
    tool_error_json,
    write_blocked_without_idempotency,
)
from execution_http import get_sync_http_client
from execution_idempotency import cached_tool_json
from execution_read_cache import get_cached_or_run

_MAX_BODY_CHARS = 32_000
_MAX_CHANNEL_MSG_BODY_PREVIEW = 8_000
_MAX_MAIL_BODY_CHARS = 64_000
_MAX_GRAPH_ATTACHMENT_BYTES = 4 * 1024 * 1024

_DEFAULT_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _normalize_graph_base_url(raw: Any) -> str:
    """Resolve graph_base_url to a real Microsoft Graph API root.

    Operators sometimes paste the Graph Explorer page URL or a base ending in ``/me``;
    both break requests such as ``me/joinedTeams``.
    """
    s = "" if raw is None else str(raw).strip()
    if not s:
        return _DEFAULT_GRAPH_BASE
    low = s.lower()
    if "developer.microsoft.com" in low or "graph-explorer" in low:
        return _DEFAULT_GRAPH_BASE
    if not low.startswith(("http://", "https://")):
        s = "https://" + s
        low = s.lower()
    try:
        p = urlparse(s)
        host = (p.hostname or "").lower()
    except ValueError:
        return _DEFAULT_GRAPH_BASE
    if host == "login.microsoftonline.com":
        return _DEFAULT_GRAPH_BASE
    base = s.rstrip("/")
    lowb = base.lower()
    if host.endswith("graph.microsoft.com"):
        while lowb.endswith("/me"):
            base = base[:-3].rstrip("/")
            lowb = base.lower()
    return base or _DEFAULT_GRAPH_BASE


def _graph_base(config: Dict[str, Any]) -> str:
    return _normalize_graph_base_url(config.get("graph_base_url"))


def _token(config: Dict[str, Any]) -> str:
    return str(config.get("access_token") or config.get("oauth2_access_token") or "").strip()


def _graph_read_cache_key(segment: str, config: Dict[str, Any], extra: str = "") -> str:
    token = _token(config)
    if not token:
        return ""
    base = _graph_base(config)
    h = hashlib.sha256(f"{token}|{base}|{extra}".encode()).hexdigest()[:40]
    return f"graph:{segment}:{h}"


def _request(
    config: Dict[str, Any],
    method: str,
    path: str,
    *,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
) -> httpx.Response:
    token = _token(config)
    if not token:
        raise ValueError("access_token not configured")
    base = _graph_base(config)
    url = base + "/" + path.lstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    return get_sync_http_client().request(
        method, url, headers=headers, json=json_body, timeout=timeout
    )


def execute_teams(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    try:
        action = str(arguments.get("action") or "list_joined_teams").strip().lower()
        if action == "list_joined_teams":
            return _list_joined_teams(config)
        if action == "list_channels":
            return _list_channels(config, arguments)
        if action == "send_message":
            return _send_message(config, arguments)
        if action == "reply_message":
            return _reply_message(config, arguments)
        if action == "list_channel_messages":
            return _list_channel_messages(config, arguments)
        if action == "get_channel_message":
            return _get_channel_message(config, arguments)
        if action == "list_mail_messages":
            return _list_mail_messages(config, arguments)
        if action == "get_mail_message":
            return _get_mail_message(config, arguments)
        if action == "get_mail_attachment":
            return _get_mail_attachment(config, arguments)
        return tool_error_json(ERROR_UNKNOWN_ACTION, f"Unknown action: {action}", action=action)
    except ValueError as e:
        return tool_error_json(ERROR_VALIDATION_FAILED, str(e))
    except Exception as e:
        return safe_tool_error("Microsoft Teams error", e)


def _list_joined_teams(config: Dict[str, Any]) -> str:
    def _produce() -> str:
        r = _request(config, "GET", "me/joinedTeams")
        if r.status_code >= 400:
            return _graph_error_response(r)
        data = r.json()
        teams = data.get("value") or []
        slim = [{"id": t.get("id"), "displayName": t.get("displayName")} for t in teams if isinstance(t, dict)]
        raw = json.dumps({"teams": slim}, indent=2)
        return maybe_validate_messaging_output("teams", "list_joined_teams", raw)

    ck = _graph_read_cache_key("joined", config)
    if not ck:
        return _produce()
    return get_cached_or_run(ck, _produce)


def _list_channels(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    team_id = str(arguments.get("team_id") or "").strip()
    if not team_id:
        return tool_error_json(ERROR_VALIDATION_FAILED, "team_id is required for list_channels")

    def _produce() -> str:
        r = _request(config, "GET", f"teams/{team_id}/channels")
        if r.status_code >= 400:
            return _graph_error_response(r)
        data = r.json()
        chans = data.get("value") or []
        slim = [
            {"id": c.get("id"), "displayName": c.get("displayName"), "membershipType": c.get("membershipType")}
            for c in chans
            if isinstance(c, dict)
        ]
        raw = json.dumps({"channels": slim}, indent=2)
        return maybe_validate_messaging_output("teams", "list_channels", raw)

    ck = _graph_read_cache_key("channels", config, team_id)
    if not ck:
        return _produce()
    return get_cached_or_run(ck, _produce)


def _normalize_body(arguments: Dict[str, Any]) -> tuple[str, str]:
    text = str(arguments.get("body") or arguments.get("text") or arguments.get("message") or "").strip()
    if len(text) > _MAX_BODY_CHARS:
        raise ValueError(f"message body too large (max {_MAX_BODY_CHARS} characters)")
    ctype = str(arguments.get("content_type") or "text").strip().lower()
    if ctype not in ("text", "html"):
        ctype = "text"
    graph_type = "html" if ctype == "html" else "text"
    return text, graph_type


def _send_message(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    blocked = write_blocked_without_idempotency(arguments, operation="teams send_message")
    if blocked:
        return blocked
    idem = str(arguments.get("idempotency_key") or "").strip()

    def _do() -> str:
        return _send_message_impl(config, arguments)

    return cached_tool_json("teams_send_message", idem, _do, cache_success_only=True)


def _send_message_impl(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    team_id = str(arguments.get("team_id") or "").strip()
    channel_id = str(arguments.get("channel_id") or "").strip()
    if not team_id or not channel_id:
        return tool_error_json(ERROR_VALIDATION_FAILED, "team_id and channel_id are required for send_message")
    text, graph_type = _normalize_body(arguments)
    if not text:
        return tool_error_json(ERROR_VALIDATION_FAILED, "body (message text) is required for send_message")
    payload = {"body": {"content": text, "contentType": graph_type}}
    r = _request(
        config,
        "POST",
        f"teams/{team_id}/channels/{channel_id}/messages",
        json_body=payload,
    )
    if r.status_code >= 400:
        return _graph_error_response(r)
    try:
        data = r.json()
    except Exception:
        data = {"status": r.status_code}
    return json.dumps({"status": "ok", "message": data}, indent=2, default=str)


def _reply_message(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    blocked = write_blocked_without_idempotency(arguments, operation="teams reply_message")
    if blocked:
        return blocked
    idem = str(arguments.get("idempotency_key") or "").strip()

    def _do() -> str:
        return _reply_message_impl(config, arguments)

    return cached_tool_json("teams_reply_message", idem, _do, cache_success_only=True)


def _reply_message_impl(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    team_id = str(arguments.get("team_id") or "").strip()
    channel_id = str(arguments.get("channel_id") or "").strip()
    message_id = str(arguments.get("message_id") or "").strip()
    if not team_id or not channel_id or not message_id:
        return tool_error_json(
            ERROR_VALIDATION_FAILED,
            "team_id, channel_id, and message_id are required for reply_message",
        )
    text, graph_type = _normalize_body(arguments)
    if not text:
        return tool_error_json(ERROR_VALIDATION_FAILED, "body (message text) is required for reply_message")
    payload = {"body": {"content": text, "contentType": graph_type}}
    r = _request(
        config,
        "POST",
        f"teams/{team_id}/channels/{channel_id}/messages/{message_id}/replies",
        json_body=payload,
    )
    if r.status_code >= 400:
        return _graph_error_response(r)
    try:
        data = r.json()
    except Exception:
        data = {"status": r.status_code}
    return json.dumps({"status": "ok", "message": data}, indent=2, default=str)


def _seg(s: str) -> str:
    return quote((s or "").strip(), safe="")


def _int_arg(arguments: Dict[str, Any], key: str, default: int, *, min_v: int, max_v: int) -> int:
    try:
        v = int(arguments.get(key) if arguments.get(key) is not None else default)
    except (TypeError, ValueError):
        v = default
    return max(min_v, min(v, max_v))


def _truncate(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _list_channel_messages(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    team_id = str(arguments.get("team_id") or "").strip()
    channel_id = str(arguments.get("channel_id") or "").strip()
    if not team_id or not channel_id:
        return tool_error_json(ERROR_VALIDATION_FAILED, "team_id and channel_id are required for list_channel_messages")
    top = _int_arg(arguments, "top", 25, min_v=1, max_v=50)
    path = f"teams/{_seg(team_id)}/channels/{_seg(channel_id)}/messages?$top={top}"
    r = _request(config, "GET", path)
    if r.status_code >= 400:
        return _graph_error_response(r)
    data = r.json()
    raw = data.get("value") or []
    slim = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        body = (m.get("body") or {}) if isinstance(m.get("body"), dict) else {}
        content = str(body.get("content") or "")
        slim.append(
            {
                "id": m.get("id"),
                "createdDateTime": m.get("createdDateTime"),
                "from": m.get("from"),
                "body_preview": _truncate(content, _MAX_CHANNEL_MSG_BODY_PREVIEW),
                "replyToId": m.get("replyToId"),
            }
        )
    out: Dict[str, Any] = {"messages": slim}
    if data.get("@odata.nextLink"):
        out["next_link"] = data.get("@odata.nextLink")
    return json.dumps(out, indent=2, default=str)


def _get_channel_message(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    team_id = str(arguments.get("team_id") or "").strip()
    channel_id = str(arguments.get("channel_id") or "").strip()
    message_id = str(arguments.get("message_id") or "").strip()
    if not team_id or not channel_id or not message_id:
        return tool_error_json(
            ERROR_VALIDATION_FAILED,
            "team_id, channel_id, and message_id are required for get_channel_message",
        )
    r = _request(
        config,
        "GET",
        f"teams/{_seg(team_id)}/channels/{_seg(channel_id)}/messages/{_seg(message_id)}",
    )
    if r.status_code >= 400:
        return _graph_error_response(r)
    m = r.json()
    if not isinstance(m, dict):
        return json.dumps({"message": m}, indent=2, default=str)
    body = (m.get("body") or {}) if isinstance(m.get("body"), dict) else {}
    content = str(body.get("content") or "")
    if len(content) > _MAX_BODY_CHARS:
        content = content[: _MAX_BODY_CHARS - 1] + "…"
    slim = {
        "id": m.get("id"),
        "createdDateTime": m.get("createdDateTime"),
        "from": m.get("from"),
        "body": {"contentType": body.get("contentType"), "content": content},
        "hasAttachments": m.get("hasAttachments"),
    }
    return json.dumps({"message": slim}, indent=2, default=str)


def _list_mail_messages(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    top = _int_arg(arguments, "top", 15, min_v=1, max_v=50)
    folder = str(arguments.get("mail_folder") or arguments.get("folder") or "inbox").strip() or "inbox"
    # Well-known folder name (e.g. inbox) or folder id.
    path = (
        f"me/mailFolders/{folder}/messages"
        f"?$top={top}&$orderby=receivedDateTime desc"
        "&$select=id,subject,bodyPreview,hasAttachments,receivedDateTime,from,isRead"
    )
    r = _request(config, "GET", path)
    if r.status_code >= 400:
        return _graph_error_response(r)
    data = r.json()
    raw = data.get("value") or []
    slim = [
        {
            "id": x.get("id"),
            "subject": x.get("subject"),
            "bodyPreview": x.get("bodyPreview"),
            "hasAttachments": x.get("hasAttachments"),
            "receivedDateTime": x.get("receivedDateTime"),
            "from": x.get("from"),
            "isRead": x.get("isRead"),
        }
        for x in raw
        if isinstance(x, dict)
    ]
    out: Dict[str, Any] = {"messages": slim}
    if data.get("@odata.nextLink"):
        out["next_link"] = data.get("@odata.nextLink")
    return json.dumps(out, indent=2, default=str)


def _get_mail_message(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    message_id = str(arguments.get("message_id") or "").strip()
    if not message_id:
        return tool_error_json(ERROR_VALIDATION_FAILED, "message_id is required for get_mail_message")
    include_full = bool(arguments.get("include_full_body") or arguments.get("full_body"))
    select = (
        "id,subject,body,bodyPreview,hasAttachments,receivedDateTime,from,toRecipients,ccRecipients"
    )
    r = _request(config, "GET", f"me/messages/{_seg(message_id)}?$select={select}")
    if r.status_code >= 400:
        return _graph_error_response(r)
    m = r.json()
    if not isinstance(m, dict):
        return json.dumps({"message": m}, indent=2, default=str)
    body = (m.get("body") or {}) if isinstance(m.get("body"), dict) else {}
    content = str(body.get("content") or "")
    if not include_full:
        content = str(m.get("bodyPreview") or _truncate(content, 4000))
    elif len(content) > _MAX_MAIL_BODY_CHARS:
        content = content[: _MAX_MAIL_BODY_CHARS - 1] + "…"
    slim = {
        "id": m.get("id"),
        "subject": m.get("subject"),
        "receivedDateTime": m.get("receivedDateTime"),
        "from": m.get("from"),
        "toRecipients": m.get("toRecipients"),
        "ccRecipients": m.get("ccRecipients"),
        "hasAttachments": m.get("hasAttachments"),
        "body": {"contentType": body.get("contentType"), "content": content},
    }
    return json.dumps({"message": slim}, indent=2, default=str)


def _get_mail_attachment(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    import base64

    message_id = str(arguments.get("message_id") or "").strip()
    attachment_id = str(arguments.get("attachment_id") or "").strip()
    if not message_id or not attachment_id:
        return tool_error_json(
            ERROR_VALIDATION_FAILED,
            "message_id and attachment_id are required for get_mail_attachment",
        )
    r = _request(
        config,
        "GET",
        f"me/messages/{_seg(message_id)}/attachments/{_seg(attachment_id)}",
    )
    if r.status_code >= 400:
        return _graph_error_response(r)
    att = r.json()
    if not isinstance(att, dict):
        return json.dumps({"attachment": att}, indent=2, default=str)
    odata_type = str(att.get("@odata.type") or "")
    name = str(att.get("name") or "attachment")
    ctype = str(att.get("contentType") or "application/octet-stream")
    b64 = att.get("contentBytes")
    if not isinstance(b64, str) or not b64.strip():
        return json.dumps(
            {
                "error": "attachment_not_downloadable",
                "odata_type": odata_type,
                "name": name,
                "hint": "Graph returned metadata only; fileAttachment contentBytes required for binary payload.",
            },
            indent=2,
        )
    try:
        raw = base64.b64decode(b64.strip(), validate=True)
    except (ValueError, TypeError):
        return json.dumps({"error": "invalid_attachment_base64"}, indent=2)
    truncated = False
    if len(raw) > _MAX_GRAPH_ATTACHMENT_BYTES:
        raw = raw[:_MAX_GRAPH_ATTACHMENT_BYTES]
        truncated = True
    out_b64 = base64.b64encode(raw).decode("ascii")
    return json.dumps(
        {
            "filename": name,
            "content_type": ctype,
            "content_base64": out_b64,
            "truncated": truncated,
        },
        indent=2,
    )


def _graph_error_response(r: httpx.Response) -> str:
    try:
        body = r.json()
    except Exception:
        body = {"text": (r.text or "")[:2000]}
    err = body.get("error") if isinstance(body, dict) else None
    code = ""
    msg = ""
    if isinstance(err, dict):
        code = str(err.get("code") or "")
        msg = str(err.get("message") or "")
    hint = ""
    if r.status_code == 401:
        hint = (
            "Token may be invalid or expired. Use Connect Microsoft in MCP settings "
            "or paste a fresh access token, then Save."
        )
    elif r.status_code == 403:
        lowmsg = (msg or "").lower()
        # Graph uses this wording for several cases; for /me/joinedTeams it often means the user principal
        # cannot use that API (e.g. personal Microsoft account) or the token is not a Graph access token.
        if "no authorization information present" in lowmsg:
            hint = (
                "Despite the wording, this 403 usually does not mean the HTTP client omitted Authorization. "
                "For /me/joinedTeams, Microsoft requires a work or school (Entra) user — personal Microsoft "
                "accounts (MSA / @outlook.com consumer) are not supported for this API. "
                "Also verify at jwt.ms that this is a Graph access token: aud should be https://graph.microsoft.com "
                "(or a regional equivalent), and delegated scp should include Team.ReadBasic.All (and User.Read) after "
                "Entra admin consent if needed. Re-authorize the Teams tool with prompt=consent after fixing the app registration."
            )
        else:
            hint = (
                "HTTP 403 from Graph often means missing delegated permissions, a consumer/personal account limitation, "
                "or guest restrictions. For Teams listing: work/school account + Team.ReadBasic.All. "
                "For channel message read: ChannelMessage.Read.All. For mailbox read: Mail.Read. "
                "Add them in Entra, then Re-authorize (prompt=consent) and verify scp at jwt.ms. "
                "Guests may be blocked; use a member account where required."
            )
        if "mail" in lowmsg or "mailbox" in lowmsg:
            hint = (
                "Mail read was denied. Ensure the Entra app has delegated Mail.Read (admin consent if required), "
                "then Re-authorize Microsoft on the Teams tool. Verify the token scp includes Mail.Read."
            )
        elif "channel" in lowmsg and "message" in lowmsg:
            hint = (
                "Channel messages read was denied. Ensure delegated ChannelMessage.Read.All is granted, "
                "then Re-authorize. The bot/app must be allowed to read that team's channel content."
            )
    sc = r.status_code
    if sc == 401:
        unified = ERROR_AUTH_FAILED
    elif sc == 403:
        unified = ERROR_PERMISSION_DENIED
    elif sc in (502, 503, 504, 429):
        unified = ERROR_UPSTREAM_UNAVAILABLE
    elif sc >= 500:
        unified = ERROR_UPSTREAM_UNAVAILABLE
    elif sc >= 400:
        unified = ERROR_UPSTREAM_ERROR
    else:
        unified = ERROR_UPSTREAM_ERROR
    payload = {
        "error": unified,
        "message": msg or r.reason_phrase,
        "provider": "graph",
        "status": sc,
        "upstream_code": code,
    }
    if hint:
        payload["hint"] = hint
    return json.dumps(payload, indent=2)
