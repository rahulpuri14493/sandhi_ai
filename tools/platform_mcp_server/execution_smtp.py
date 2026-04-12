"""SMTP outbound email (password or OAuth2 / XOAUTH2) for platform MCP."""
from __future__ import annotations

import base64
import json
import os
import re
import smtplib
import ssl
import urllib.parse
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any, Dict, List, Optional, Tuple

from execution_common import safe_tool_error
from execution_contract import write_blocked_without_idempotency
from execution_http import get_sync_http_client
from execution_idempotency import cached_tool_json

_PRESETS: Dict[str, Tuple[str, int, bool]] = {
    # (host, port, use_ssl) — use_ssl True => SMTP_SSL on connect
    "gmail": ("smtp.gmail.com", 587, False),
    "outlook": ("smtp.office365.com", 587, False),
    "yahoo": ("smtp.mail.yahoo.com", 587, False),
}

_MAX_BODY_CHARS = 512_000
_MAX_RECIPIENTS = 50
_MAX_ATTACHMENTS = 10
_MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
_MAX_ATTACHMENTS_TOTAL_BYTES = 12 * 1024 * 1024
_MAX_GMAIL_MAIL_BODY_CHARS = 64_000
_MAX_GMAIL_ATTACHMENT_BYTES = 4 * 1024 * 1024


def _parse_recipients(val: Any) -> List[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    s = str(val).strip()
    if not s:
        return []
    parts = re.split(r"[,;\n]+", s)
    return [p.strip() for p in parts if p.strip()]


def _parse_attachments(arguments: Dict[str, Any]) -> Tuple[List[Tuple[str, bytes, str]], Optional[str]]:
    raw = arguments.get("attachments")
    if raw is None or raw == [] or raw == "":
        return [], None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return [], "attachments must be a JSON array or list"
    if not isinstance(raw, list):
        return [], "attachments must be a list"
    if len(raw) > _MAX_ATTACHMENTS:
        return [], f"too many attachments (max {_MAX_ATTACHMENTS})"
    out: List[Tuple[str, bytes, str]] = []
    total = 0
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            return [], f"attachment {i} must be an object with filename and content_base64"
        fn = str(item.get("filename") or item.get("name") or "attachment").strip() or "attachment"
        b64 = item.get("content_base64") or item.get("data") or item.get("base64")
        if not isinstance(b64, str) or not b64.strip():
            return [], f"attachment {i} missing content_base64"
        try:
            data = base64.b64decode(b64.strip(), validate=True)
        except (ValueError, TypeError):
            return [], f"attachment {i} has invalid base64"
        if len(data) > _MAX_ATTACHMENT_BYTES:
            return [], f"attachment {i} exceeds max size ({_MAX_ATTACHMENT_BYTES} bytes)"
        total += len(data)
        if total > _MAX_ATTACHMENTS_TOTAL_BYTES:
            return [], f"total attachment size exceeds {_MAX_ATTACHMENTS_TOTAL_BYTES} bytes"
        ctype = str(item.get("content_type") or "application/octet-stream").strip()
        main, _, sub = ctype.partition("/")
        if not sub:
            main, sub = "application", "octet-stream"
        out.append((fn, data, f"{main}/{sub}"))
    return out, None


def _xoauth2_b64(username: str, access_token: str) -> str:
    auth_string = f"user={username}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(auth_string.encode("utf-8")).decode("ascii")


def _smtp_oauth_access_token(config: Dict[str, Any]) -> str:
    return str(config.get("access_token") or config.get("oauth2_access_token") or "").strip()


def _smtp_oauth_refresh_token(config: Dict[str, Any]) -> str:
    return str(config.get("oauth_refresh_token") or config.get("refresh_token") or "").strip()


def _host_is_domain_or_subdomain(host: str, domain: str) -> bool:
    """True if host is exactly domain or a direct subdomain (avoids naive substring URL matching)."""
    h = (host or "").strip().lower().rstrip(".")
    d = (domain or "").strip().lower().rstrip(".")
    return bool(h) and bool(d) and (h == d or h.endswith("." + d))


def _smtp_refresh_oauth_provider(config: Dict[str, Any]) -> str:
    """
    Which IdP to use for refresh_token exchange. Matches MCP OAuth env (MCP_OAUTH_*).
    """
    prov = str(config.get("provider") or "custom").strip().lower()
    if prov in ("outlook", "gmail"):
        return prov
    host = str(config.get("smtp_host") or "").strip().lower()
    if _host_is_domain_or_subdomain(host, "office365.com") or host == "smtp-mail.outlook.com":
        return "outlook"
    if _host_is_domain_or_subdomain(host, "gmail.com"):
        return "gmail"
    return ""


def _smtp_force_refresh_access_token(config: Dict[str, Any]) -> bool:
    """
    Exchange oauth_refresh_token for a new access_token using MCP_OAUTH_* credentials
    from the environment (same vars as the Sandhi backend OAuth routes).
    Mutates config in place. Returns True if access_token was set.
    """
    refresh = _smtp_oauth_refresh_token(config)
    if not refresh:
        return False
    prov = _smtp_refresh_oauth_provider(config)
    client = get_sync_http_client()
    try:
        if prov == "outlook":
            cid = (os.environ.get("MCP_OAUTH_MICROSOFT_CLIENT_ID") or "").strip()
            secret = (os.environ.get("MCP_OAUTH_MICROSOFT_CLIENT_SECRET") or "").strip()
            tenant = (os.environ.get("MCP_OAUTH_MICROSOFT_TENANT") or "common").strip() or "common"
            if not cid or not secret:
                return False
            token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
            r = client.post(
                token_url,
                data={
                    "client_id": cid,
                    "client_secret": secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                },
                timeout=30.0,
            )
        elif prov == "gmail":
            cid = (os.environ.get("MCP_OAUTH_GOOGLE_CLIENT_ID") or "").strip()
            secret = (os.environ.get("MCP_OAUTH_GOOGLE_CLIENT_SECRET") or "").strip()
            if not cid or not secret:
                return False
            r = client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": cid,
                    "client_secret": secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                },
                timeout=30.0,
            )
        else:
            return False
    except Exception:
        return False
    if r.status_code != 200:
        return False
    try:
        data = r.json()
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    at = str(data.get("access_token") or "").strip()
    if not at:
        return False
    config["access_token"] = at
    new_rt = str(data.get("refresh_token") or "").strip()
    if new_rt:
        config["oauth_refresh_token"] = new_rt
    return True


def _resolve_endpoint(config: Dict[str, Any]) -> Tuple[str, int, bool, bool]:
    provider = str(config.get("provider") or "custom").strip().lower()
    if provider in _PRESETS:
        host, port, use_ssl = _PRESETS[provider]
        return host, port, use_ssl, True
    host = str(config.get("smtp_host") or "").strip()
    port = int(config.get("smtp_port") or 587)
    use_ssl = bool(config.get("use_ssl"))
    use_starttls = bool(config.get("use_tls", True))
    return host, port, use_ssl, use_starttls


def _smtp_connect(host: str, port: int, *, use_ssl: bool, use_starttls: bool, timeout: float) -> smtplib.SMTP:
    ctx = ssl.create_default_context()
    if use_ssl or port == 465:
        return smtplib.SMTP_SSL(host, port, context=ctx, timeout=timeout)
    client = smtplib.SMTP(host, port, timeout=timeout)
    client.ehlo()
    if use_starttls:
        client.starttls(context=ctx)
        client.ehlo()
    return client


def config_timeout(config: Dict[str, Any]) -> float:
    t = config.get("timeout_seconds")
    try:
        v = float(t) if t is not None else 30.0
    except (TypeError, ValueError):
        v = 30.0
    return max(5.0, min(v, 120.0))


def _auth_smtp(client: smtplib.SMTP, config: Dict[str, Any]) -> Tuple[bool, str]:
    auth_mode = str(config.get("auth_mode") or "").strip().lower()
    username = str(config.get("username") or config.get("from_address") or "").strip()
    password = str(config.get("password") or "").strip()
    access_token = _smtp_oauth_access_token(config)
    if not auth_mode:
        auth_mode = (
            "oauth2"
            if (access_token or _smtp_oauth_refresh_token(config))
            else "password"
        )
    if auth_mode == "oauth2":
        if not username:
            return False, "username (mailbox) is required for auth_mode=oauth2"
        if not access_token and _smtp_oauth_refresh_token(config):
            _smtp_force_refresh_access_token(config)
            access_token = _smtp_oauth_access_token(config)
        if not access_token:
            return False, (
                "username and access_token (OAuth2) are required for auth_mode=oauth2; "
                "or store oauth_refresh_token and set MCP_OAUTH_MICROSOFT_* / MCP_OAUTH_GOOGLE_* on the platform MCP server "
                "so expired access tokens can be refreshed."
            )
        try:
            code, resp = client.docmd("AUTH", "XOAUTH2 " + _xoauth2_b64(username, access_token))
        except smtplib.SMTPException as e:
            return False, f"SMTP OAuth2 auth failed ({type(e).__name__})"
        if code != 235 and _smtp_oauth_refresh_token(config):
            if _smtp_force_refresh_access_token(config):
                access_token = _smtp_oauth_access_token(config)
                try:
                    code, resp = client.docmd("AUTH", "XOAUTH2 " + _xoauth2_b64(username, access_token))
                except smtplib.SMTPException as e:
                    return False, f"SMTP OAuth2 auth failed after token refresh ({type(e).__name__})"
        if code != 235:
            detail = resp.decode(errors="replace") if isinstance(resp, bytes) else str(resp)
            msg = f"SMTP OAuth2 rejected ({code}): {detail.strip()[:400]}"
            if code == 535:
                msg += (
                    " Hint: Microsoft SMTP (smtp.office365.com) expects token aud https://outlook.office.com — use OAuth scope "
                    "https://outlook.office.com/SMTP.Send when connecting, not Graph-only SMTP.Send. Turn on Authenticated SMTP "
                    "for the mailbox; consumer @outlook.com may not support app SMTP OAuth. "
                    "If this worked earlier and now fails with 535, the access token may have expired — ensure oauth_refresh_token "
                    "is saved and platform MCP has MCP_OAUTH_MICROSOFT_CLIENT_ID/SECRET (same app as Connect Microsoft)."
                )
            return False, msg
        return True, ""
    if not username or not password:
        return False, "username and password are required for password authentication"
    try:
        client.login(username, password)
    except smtplib.SMTPException as e:
        return False, f"SMTP login failed ({type(e).__name__})"
    return True, ""


def _gmail_oauth_token(config: Dict[str, Any]) -> str:
    t = _smtp_oauth_access_token(config)
    if t:
        return t
    if _smtp_oauth_refresh_token(config) and _smtp_force_refresh_access_token(config):
        return _smtp_oauth_access_token(config)
    return ""


def _gmail_api_only_error() -> str:
    return json.dumps(
        {
            "error": "gmail_api_only",
            "message": (
                "Gmail read actions require provider=gmail, OAuth access_token (Connect Google), and "
                "gmail.readonly scope. Outlook inboxes are not readable via SMTP OAuth; use the Teams "
                "(Microsoft Graph) tool with list_mail_messages / get_mail_message."
            ),
        },
        indent=2,
    )


def _gmail_walk_payload(
    payload: Dict[str, Any],
    bodies: List[str],
    attach_refs: List[Dict[str, Any]],
    *,
    depth: int = 0,
) -> None:
    if depth > 24 or not isinstance(payload, dict):
        return
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    aid = body.get("attachmentId")
    if aid:
        attach_refs.append(
            {
                "attachment_id": aid,
                "filename": str(payload.get("filename") or ""),
                "mimeType": str(payload.get("mimeType") or ""),
                "size": body.get("size"),
            }
        )
    elif body.get("data"):
        try:
            pad = body["data"] + "==="
            raw = base64.urlsafe_b64decode(pad)
            bodies.append(raw.decode("utf-8", errors="replace"))
        except Exception:
            pass
    for sub in payload.get("parts") or []:
        if isinstance(sub, dict):
            _gmail_walk_payload(sub, bodies, attach_refs, depth=depth + 1)


def _smtp_gmail_list_mail(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    token = _gmail_oauth_token(config)
    if not token:
        return "Error: access_token is required for Gmail list_mail_messages"
    try:
        lim = int(arguments.get("max_results") or arguments.get("limit") or 20)
    except (TypeError, ValueError):
        lim = 20
    lim = max(1, min(lim, 50))
    q = str(arguments.get("query") or arguments.get("q") or "").strip() or None
    params: Dict[str, Any] = {"maxResults": lim}
    if q:
        params["q"] = q
    url = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
    client = get_sync_http_client()
    r = client.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=30.0)
    if r.status_code == 401 and _smtp_force_refresh_access_token(config):
        token = _gmail_oauth_token(config)
        r = client.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=30.0)
    if r.status_code >= 400:
        return _gmail_api_error_response(r)
    data = r.json()
    msgs = data.get("messages") or []
    slim = [{"id": m.get("id"), "threadId": m.get("threadId")} for m in msgs if isinstance(m, dict)]
    out: Dict[str, Any] = {"messages": slim, "resultSizeEstimate": data.get("resultSizeEstimate")}
    return json.dumps(out, indent=2)


def _gmail_api_error_response(r: Any) -> str:
    try:
        body = r.json()
    except Exception:
        body = {"text": (r.text or "")[:2000]}
    return json.dumps(
        {
            "error": "gmail_api_error",
            "status": getattr(r, "status_code", None),
            "body": body,
            "hint": "Reconnect Google (smtp_gmail) after adding gmail.readonly if you see 403.",
        },
        indent=2,
        default=str,
    )


def _smtp_gmail_get_mail(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    token = _gmail_oauth_token(config)
    mid = str(arguments.get("message_id") or arguments.get("id") or "").strip()
    if not mid:
        return "Error: message_id is required for get_mail_message"
    if not token:
        return "Error: access_token is required for Gmail get_mail_message"
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{urllib.parse.quote(mid)}"
    client = get_sync_http_client()
    r = client.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params={"format": "full"},
        timeout=45.0,
    )
    if r.status_code == 401 and _smtp_force_refresh_access_token(config):
        token = _gmail_oauth_token(config)
        r = client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params={"format": "full"},
            timeout=45.0,
        )
    if r.status_code >= 400:
        return _gmail_api_error_response(r)
    data = r.json()
    if not isinstance(data, dict):
        return json.dumps({"message": data}, indent=2, default=str)
    payload = data.get("payload")
    bodies: List[str] = []
    attach_refs: List[Dict[str, Any]] = []
    if isinstance(payload, dict):
        _gmail_walk_payload(payload, bodies, attach_refs)
    text = "\n\n".join(bodies).strip()
    if len(text) > _MAX_GMAIL_MAIL_BODY_CHARS:
        text = text[: _MAX_GMAIL_MAIL_BODY_CHARS - 1] + "…"
    pl = data.get("payload")
    headers_raw = pl.get("headers") or [] if isinstance(pl, dict) else []
    hdr_map: Dict[str, str] = {}
    if isinstance(headers_raw, list):
        for h in headers_raw:
            if isinstance(h, dict) and h.get("name"):
                hdr_map[str(h["name"]).lower()] = str(h.get("value") or "")
    slim = {
        "id": data.get("id"),
        "threadId": data.get("threadId"),
        "snippet": data.get("snippet"),
        "labelIds": data.get("labelIds"),
        "headers": {
            "subject": hdr_map.get("subject"),
            "from": hdr_map.get("from"),
            "to": hdr_map.get("to"),
            "date": hdr_map.get("date"),
        },
        "body_text": text,
        "attachments": attach_refs,
    }
    return json.dumps({"message": slim}, indent=2, default=str)


def _smtp_gmail_get_attachment(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    token = _gmail_oauth_token(config)
    mid = str(arguments.get("message_id") or "").strip()
    aid = str(arguments.get("attachment_id") or "").strip()
    if not mid or not aid:
        return "Error: message_id and attachment_id are required for get_mail_attachment"
    if not token:
        return "Error: access_token is required for Gmail get_mail_attachment"
    url = (
        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/"
        f"{urllib.parse.quote(mid)}/attachments/{urllib.parse.quote(aid)}"
    )
    client = get_sync_http_client()
    r = client.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=45.0)
    if r.status_code == 401 and _smtp_force_refresh_access_token(config):
        token = _gmail_oauth_token(config)
        r = client.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=45.0)
    if r.status_code >= 400:
        return _gmail_api_error_response(r)
    data = r.json()
    if not isinstance(data, dict):
        return json.dumps({"attachment": data}, indent=2, default=str)
    b64url = data.get("data")
    if not isinstance(b64url, str) or not b64url.strip():
        return json.dumps({"error": "gmail_no_attachment_data", "raw": data}, indent=2, default=str)
    try:
        raw = base64.urlsafe_b64decode(b64url + "===")
    except Exception:
        return json.dumps({"error": "gmail_attachment_decode_failed"}, indent=2)
    truncated = False
    if len(raw) > _MAX_GMAIL_ATTACHMENT_BYTES:
        raw = raw[:_MAX_GMAIL_ATTACHMENT_BYTES]
        truncated = True
    out_b64 = base64.b64encode(raw).decode("ascii")
    size = data.get("size")
    return json.dumps(
        {
            "size": size,
            "content_base64": out_b64,
            "truncated": truncated,
        },
        indent=2,
    )


def execute_smtp(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    try:
        action = str(arguments.get("action") or "send").strip().lower()
        if action == "validate":
            return _smtp_validate(config)
        if action == "send":
            return _smtp_send(config, arguments)
        if action in ("list_mail_messages", "get_mail_message", "get_mail_attachment"):
            provider = str(config.get("provider") or "").strip().lower()
            if provider != "gmail":
                return _gmail_api_only_error()
            if action == "list_mail_messages":
                return _smtp_gmail_list_mail(config, arguments)
            if action == "get_mail_message":
                return _smtp_gmail_get_mail(config, arguments)
            return _smtp_gmail_get_attachment(config, arguments)
        return json.dumps({"error": "unknown_action", "action": action})
    except Exception as e:
        return safe_tool_error("SMTP error", e)


def _smtp_validate(config: Dict[str, Any]) -> str:
    host, port, use_ssl, use_starttls = _resolve_endpoint(config)
    if not host:
        return "Error: smtp_host is required for provider=custom"
    try:
        client = _smtp_connect(
            host, port, use_ssl=use_ssl, use_starttls=use_starttls, timeout=config_timeout(config)
        )
        try:
            ok, msg = _auth_smtp(client, config)
            if not ok:
                return f"Error: {msg}"
            return json.dumps({"status": "ok", "message": "SMTP connection and authentication successful"})
        finally:
            try:
                client.quit()
            except Exception:
                pass
    except Exception as e:
        return safe_tool_error("SMTP validate", e)


def _smtp_send(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    blocked = write_blocked_without_idempotency(arguments, operation="smtp send")
    if blocked:
        return blocked
    idem = str(arguments.get("idempotency_key") or "").strip()

    def _do_send() -> str:
        return _smtp_send_impl(config, arguments)

    return cached_tool_json("smtp_send", idem, _do_send, cache_success_only=True)


def _smtp_send_impl(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    host, port, use_ssl, use_starttls = _resolve_endpoint(config)
    if not host:
        return "Error: smtp_host is required for provider=custom"
    to_addrs = _parse_recipients(arguments.get("to"))
    cc = _parse_recipients(arguments.get("cc"))
    bcc = _parse_recipients(arguments.get("bcc"))
    all_rcpt = list(dict.fromkeys(to_addrs + cc + bcc))
    if not to_addrs:
        return "Error: to (recipient) is required for send"
    if len(all_rcpt) > _MAX_RECIPIENTS:
        return f"Error: too many recipients (max {_MAX_RECIPIENTS})"
    subject = str(arguments.get("subject") or "").strip()
    if not subject:
        return "Error: subject is required for send"
    body = str(arguments.get("body") or "")
    html_body = str(arguments.get("html_body") or "")
    att_list, att_err = _parse_attachments(arguments)
    if att_err:
        return f"Error: {att_err}"
    if not body.strip() and not html_body.strip() and not att_list:
        return "Error: body, html_body, or attachments is required for send"
    if len(body) > _MAX_BODY_CHARS or len(html_body) > _MAX_BODY_CHARS:
        return f"Error: body too large (max {_MAX_BODY_CHARS} characters)"
    from_addr = str(
        arguments.get("from_address")
        or config.get("from_address")
        or config.get("username")
        or ""
    ).strip()
    if not from_addr:
        return "Error: from_address (or config username/from_address) is required"
    display_name = str(arguments.get("from_name") or config.get("from_name") or "").strip()
    from_header = formataddr((display_name, from_addr)) if display_name else from_addr

    if html_body.strip() and body.strip():
        inner = MIMEMultipart("alternative")
        inner.attach(MIMEText(body, "plain", "utf-8"))
        inner.attach(MIMEText(html_body, "html", "utf-8"))
    elif html_body.strip():
        inner = MIMEText(html_body, "html", "utf-8")
    elif body.strip():
        inner = MIMEText(body, "plain", "utf-8")
    else:
        inner = MIMEText("", "plain", "utf-8")

    if att_list:
        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"] = from_header
        msg["To"] = ", ".join(to_addrs)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg.attach(inner)
        for fn, data, ctype in att_list:
            main, _, sub = ctype.partition("/")
            part = MIMEBase(main or "application", sub or "octet-stream")
            part.set_payload(data)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=fn)
            msg.attach(part)
    else:
        msg = inner
        msg["Subject"] = subject
        msg["From"] = from_header
        msg["To"] = ", ".join(to_addrs)
        if cc:
            msg["Cc"] = ", ".join(cc)

    envelope_to = list(dict.fromkeys(to_addrs + cc + bcc))

    try:
        client = _smtp_connect(
            host, port, use_ssl=use_ssl, use_starttls=use_starttls, timeout=config_timeout(config)
        )
        try:
            ok, err = _auth_smtp(client, config)
            if not ok:
                return f"Error: {err}"
            client.sendmail(from_addr, envelope_to, msg.as_string())
            return json.dumps(
                {
                    "status": "ok",
                    "recipients": len(envelope_to),
                    "message_id_hint": (msg.get("Message-ID") or "").strip() or None,
                }
            )
        finally:
            try:
                client.quit()
            except Exception:
                pass
    except Exception as e:
        return safe_tool_error("SMTP send", e)
