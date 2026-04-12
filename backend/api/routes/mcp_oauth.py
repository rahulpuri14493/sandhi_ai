"""
OAuth2 "Connect" for platform MCP tools (Microsoft Teams / Graph, Outlook SMTP, Gmail SMTP).

Requires: MCP_OAUTH_ENABLED, Redis (MCP_GUARDRAILS_REDIS_URL), and provider app credentials.
Callback URLs to register in Azure / Google Cloud:
  {MCP_OAUTH_BACKEND_PUBLIC_URL}/api/mcp/oauth/microsoft/callback
  {MCP_OAUTH_BACKEND_PUBLIC_URL}/api/mcp/oauth/google/callback
"""
from __future__ import annotations

import base64
import json
import logging
import secrets
from typing import Any, Literal, Optional
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from core.config import settings
from core.security import get_current_business_user
from models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/oauth", tags=["mcp-oauth"])

STATE_PREFIX = "sandhi:mcp_oauth:state:"
STATE_TTL = 600
CLAIM_PREFIX = "sandhi:mcp_oauth:claim:"
CLAIM_TTL = 600
# After first successful /claim, same nonce may be requested again immediately (React Strict Mode, double fetch).
CLAIM_REPLAY_PREFIX = "sandhi:mcp_oauth:claim_replay:"
CLAIM_REPLAY_TTL = 120


def _frontend_oauth_error_redirect(front: str, message: str) -> RedirectResponse:
    msg = (message or "unknown").strip()
    if len(msg) > 480:
        msg = msg[:480]
    sep = "&" if "?" in front else "?"
    return RedirectResponse(url=f"{front}{sep}oauth_error={quote(msg, safe='')}", status_code=302)


def _login_hint_from_access_token_jwt(access_token: str) -> str:
    """Best-effort mailbox/login hint from a JWT-shaped OAuth access token (unverified decode)."""
    if not access_token or access_token.count(".") != 2:
        return ""
    try:
        _h, payload_b64, _s = access_token.split(".")
        pad = "=" * (-len(payload_b64) % 4)
        raw = base64.urlsafe_b64decode(payload_b64 + pad)
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return ""
    for key in ("preferred_username", "email", "upn", "unique_name"):
        v = data.get(key)
        if isinstance(v, str):
            s = v.strip()
            if s and "@" in s:
                return s
    return ""


def _token_exchange_error_message(exc: BaseException) -> str:
    """Short message for redirect query; prefer provider error_description when available."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        try:
            body = exc.response.json()
            desc = body.get("error_description") or body.get("error")
            if isinstance(desc, str) and desc.strip():
                return f"token_exchange {desc.strip()[:350]}"
        except Exception:
            pass
        try:
            t = (exc.response.text or "").strip()
            if t:
                return f"token_exchange {t[:350]}"
        except Exception:
            pass
    return "token_exchange"


_MS_GRAPH_SCOPES = (
    "offline_access openid "
    "https://graph.microsoft.com/User.Read "
    "https://graph.microsoft.com/Team.ReadBasic.All "
    "https://graph.microsoft.com/Channel.ReadBasic.All "
    "https://graph.microsoft.com/ChannelMessage.Send "
    "https://graph.microsoft.com/ChannelMessage.Read.All "
    "https://graph.microsoft.com/Mail.Read"
)
# Use Exchange resource in the authorize URL so access_token "aud" matches smtp.office365.com (XOAUTH2).
# Graph-only scope (https://graph.microsoft.com/SMTP.Send) often authorizes but SMTP returns 535.7.3 - wrong audience.
# Entra still lists SMTP.Send under "Microsoft Graph"; that permission backs this scope for many tenants.
# https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth
_DEFAULT_MS_SMTP_SCOPES = "offline_access openid https://outlook.office.com/SMTP.Send"
# mail.google.com: SMTP/IMAP XOAUTH2; gmail.readonly: Gmail REST (list/read messages, attachments).
_GOOGLE_SMTP_SCOPES = "https://mail.google.com/ https://www.googleapis.com/auth/gmail.readonly"


def _microsoft_smtp_scopes() -> str:
    custom = (getattr(settings, "MCP_OAUTH_MICROSOFT_SMTP_SCOPES", "") or "").strip()
    return custom if custom else _DEFAULT_MS_SMTP_SCOPES


def _oauth_redis():
    url = (getattr(settings, "MCP_GUARDRAILS_REDIS_URL", "") or "").strip()
    if not url:
        return None
    try:
        import redis

        return redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=2.0, socket_timeout=2.0)
    except Exception as exc:
        logger.warning("mcp_oauth: redis unavailable (%s)", type(exc).__name__)
        return None


def _require_oauth_config():
    if not bool(getattr(settings, "MCP_OAUTH_ENABLED", False)):
        raise HTTPException(
            status_code=503,
            detail="OAuth is disabled. Set MCP_OAUTH_ENABLED=true and provider credentials.",
        )
    r = _oauth_redis()
    if r is None:
        raise HTTPException(
            status_code=503,
            detail="Redis is required for OAuth (set MCP_GUARDRAILS_REDIS_URL).",
        )
    try:
        r.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Redis ping failed: {type(exc).__name__}") from exc
    return r


def _backend_base() -> str:
    return (getattr(settings, "MCP_OAUTH_BACKEND_PUBLIC_URL", "") or "http://localhost:8000").rstrip("/")


def _frontend_redirect() -> str:
    return (getattr(settings, "MCP_OAUTH_FRONTEND_REDIRECT_URL", "") or "http://localhost:3000/sandhi_ai/mcp").strip()


class OAuthMicrosoftStartBody(BaseModel):
    purpose: Literal["teams", "smtp_outlook"] = Field(
        ...,
        description="teams=Microsoft Graph; smtp_outlook=Outlook/Office365 SMTP OAuth",
    )
    force_consent: bool = Field(
        False,
        description="If true, adds prompt=consent so Microsoft re-issues a token with current app scopes (use after Entra permission changes).",
    )


class OAuthGoogleStartBody(BaseModel):
    purpose: Literal["smtp_gmail"] = "smtp_gmail"


@router.post("/microsoft/start")
def oauth_microsoft_start(
    body: OAuthMicrosoftStartBody,
    current_user: User = Depends(get_current_business_user),
):
    r = _require_oauth_config()
    cid = (getattr(settings, "MCP_OAUTH_MICROSOFT_CLIENT_ID", "") or "").strip()
    secret = (getattr(settings, "MCP_OAUTH_MICROSOFT_CLIENT_SECRET", "") or "").strip()
    if not cid or not secret:
        raise HTTPException(status_code=503, detail="Microsoft OAuth is not configured (client id/secret).")
    tenant = (getattr(settings, "MCP_OAUTH_MICROSOFT_TENANT", "") or "common").strip() or "common"
    scopes = _MS_GRAPH_SCOPES if body.purpose == "teams" else _microsoft_smtp_scopes()
    state = secrets.token_urlsafe(32)
    payload = {
        "user_id": int(current_user.id),
        "purpose": body.purpose,
    }
    r.setex(STATE_PREFIX + state, STATE_TTL, json.dumps(payload, separators=(",", ":")))

    redirect_uri = f"{_backend_base()}/api/mcp/oauth/microsoft/callback"
    auth_params: dict[str, str] = {
        "client_id": cid,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": scopes,
        "state": state,
    }
    if body.force_consent:
        auth_params["prompt"] = "consent"
    q = urlencode(auth_params, safe=":/")
    auth_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?{q}"
    return {"authorize_url": auth_url}


@router.get("/microsoft/callback")
async def oauth_microsoft_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
):
    front = _frontend_redirect()
    if error:
        msg = quote((error_description or error)[:400], safe="")
        return RedirectResponse(url=f"{front}?oauth_error={msg}", status_code=302)

    if not code or not state:
        return RedirectResponse(url=f"{front}?oauth_error=missing_code", status_code=302)

    r = _oauth_redis()
    if not r:
        return RedirectResponse(url=f"{front}?oauth_error=no_redis", status_code=302)

    raw_state = r.get(STATE_PREFIX + state)
    if not raw_state:
        return RedirectResponse(url=f"{front}?oauth_error=invalid_state", status_code=302)
    try:
        st = json.loads(raw_state)
    except json.JSONDecodeError:
        return RedirectResponse(url=f"{front}?oauth_error=bad_state", status_code=302)
    r.delete(STATE_PREFIX + state)

    uid = int(st.get("user_id", 0) or 0)
    purpose = str(st.get("purpose") or "")

    cid = (getattr(settings, "MCP_OAUTH_MICROSOFT_CLIENT_ID", "") or "").strip()
    secret = (getattr(settings, "MCP_OAUTH_MICROSOFT_CLIENT_SECRET", "") or "").strip()
    tenant = (getattr(settings, "MCP_OAUTH_MICROSOFT_TENANT", "") or "common").strip() or "common"
    redirect_uri = f"{_backend_base()}/api/mcp/oauth/microsoft/callback"
    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    data = {
        "client_id": cid,
        "client_secret": secret,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            tr = await client.post(token_url, data=data)
            tr.raise_for_status()
            tok = tr.json()
    except Exception as exc:
        logger.warning("microsoft token exchange failed: %s", type(exc).__name__)
        return _frontend_oauth_error_redirect(front, _token_exchange_error_message(exc))

    access = (tok.get("access_token") or "").strip()
    refresh = (tok.get("refresh_token") or "").strip()
    expires_in = tok.get("expires_in")
    if not access:
        return _frontend_oauth_error_redirect(front, "no_access_token")

    claim_nonce = secrets.token_urlsafe(32)
    claim: dict[str, Any] = {
        "user_id": uid,
        "provider": "microsoft",
        "purpose": purpose,
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": expires_in,
    }
    if purpose == "smtp_outlook":
        hint = _login_hint_from_access_token_jwt(access)
        if hint:
            claim["username"] = hint
    r.setex(CLAIM_PREFIX + claim_nonce, CLAIM_TTL, json.dumps(claim, separators=(",", ":")))
    sep = "&" if "?" in front else "?"
    return RedirectResponse(url=f"{front}{sep}oauth_nonce={claim_nonce}", status_code=302)


@router.post("/google/start")
def oauth_google_start(
    body: OAuthGoogleStartBody,
    current_user: User = Depends(get_current_business_user),
):
    r = _require_oauth_config()
    cid = (getattr(settings, "MCP_OAUTH_GOOGLE_CLIENT_ID", "") or "").strip()
    if not cid:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured (client id).")
    state = secrets.token_urlsafe(32)
    r.setex(
        STATE_PREFIX + state,
        STATE_TTL,
        json.dumps({"user_id": int(current_user.id), "purpose": body.purpose}, separators=(",", ":")),
    )
    redirect_uri = f"{_backend_base()}/api/mcp/oauth/google/callback"
    q = urlencode(
        {
            "client_id": cid,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": _GOOGLE_SMTP_SCOPES,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        },
        safe=":/",
    )
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{q}"
    return {"authorize_url": auth_url}


@router.get("/google/callback")
async def oauth_google_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    front = _frontend_redirect()
    if error:
        return RedirectResponse(url=f"{front}?oauth_error=google_denied", status_code=302)
    if not code or not state:
        return RedirectResponse(url=f"{front}?oauth_error=missing_code", status_code=302)

    r = _oauth_redis()
    if not r:
        return RedirectResponse(url=f"{front}?oauth_error=no_redis", status_code=302)

    raw_state = r.get(STATE_PREFIX + state)
    if not raw_state:
        return RedirectResponse(url=f"{front}?oauth_error=invalid_state", status_code=302)
    try:
        st = json.loads(raw_state)
    except json.JSONDecodeError:
        return RedirectResponse(url=f"{front}?oauth_error=bad_state", status_code=302)
    r.delete(STATE_PREFIX + state)

    uid = int(st.get("user_id", 0) or 0)
    purpose = str(st.get("purpose") or "smtp_gmail")

    cid = (getattr(settings, "MCP_OAUTH_GOOGLE_CLIENT_ID", "") or "").strip()
    secret = (getattr(settings, "MCP_OAUTH_GOOGLE_CLIENT_SECRET", "") or "").strip()
    redirect_uri = f"{_backend_base()}/api/mcp/oauth/google/callback"
    data = {
        "code": code,
        "client_id": cid,
        "client_secret": secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            tr = await client.post("https://oauth2.googleapis.com/token", data=data)
            tr.raise_for_status()
            tok = tr.json()
    except Exception as exc:
        logger.warning("google token exchange failed: %s", type(exc).__name__)
        return _frontend_oauth_error_redirect(front, _token_exchange_error_message(exc))

    access = (tok.get("access_token") or "").strip()
    refresh = (tok.get("refresh_token") or "").strip()
    expires_in = tok.get("expires_in")
    if not access:
        return RedirectResponse(url=f"{front}?oauth_error=no_access_token", status_code=302)

    claim_nonce = secrets.token_urlsafe(32)
    claim: dict[str, Any] = {
        "user_id": uid,
        "provider": "google",
        "purpose": purpose,
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": expires_in,
    }
    if purpose == "smtp_gmail":
        hint = _login_hint_from_access_token_jwt(access)
        if hint:
            claim["username"] = hint
    r.setex(CLAIM_PREFIX + claim_nonce, CLAIM_TTL, json.dumps(claim, separators=(",", ":")))
    sep = "&" if "?" in front else "?"
    return RedirectResponse(url=f"{front}{sep}oauth_nonce={claim_nonce}", status_code=302)


@router.get("/claim")
def oauth_claim(
    nonce: str = Query(..., min_length=8, max_length=256),
    current_user: User = Depends(get_current_business_user),
):
    if not bool(getattr(settings, "MCP_OAUTH_ENABLED", False)):
        raise HTTPException(status_code=503, detail="OAuth is disabled.")
    r = _oauth_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Redis unavailable.")
    nonce_key = nonce.strip()
    pk = CLAIM_PREFIX + nonce_key
    rk = CLAIM_REPLAY_PREFIX + nonce_key
    raw = r.get(pk)
    if not raw:
        replay_raw = r.get(rk)
        if replay_raw:
            try:
                rp = json.loads(replay_raw)
                if int(rp.get("_uid", 0) or 0) != int(current_user.id):
                    raise HTTPException(
                        status_code=403,
                        detail="OAuth session does not match this account.",
                    )
                out = {
                    "provider": rp.get("provider"),
                    "purpose": rp.get("purpose"),
                    "access_token": rp.get("access_token") or "",
                    "refresh_token": rp.get("refresh_token") or "",
                    "expires_in": rp.get("expires_in"),
                }
                if rp.get("username"):
                    out["username"] = rp.get("username")
                return out
            except HTTPException:
                raise
            except Exception:
                pass
        raise HTTPException(status_code=404, detail="Invalid or expired OAuth session; try Connect again.")
    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid claim payload") from None
    if int(data.get("user_id", 0) or 0) != int(current_user.id):
        raise HTTPException(status_code=403, detail="OAuth session does not match this account.")
    r.delete(pk)
    body: dict[str, Any] = {
        "provider": data.get("provider"),
        "purpose": data.get("purpose"),
        "access_token": data.get("access_token") or "",
        "refresh_token": data.get("refresh_token") or "",
        "expires_in": data.get("expires_in"),
    }
    if data.get("username"):
        body["username"] = data.get("username")
    replay_store = {**body, "_uid": int(current_user.id)}
    r.setex(rk, CLAIM_REPLAY_TTL, json.dumps(replay_store, separators=(",", ":")))
    return body
