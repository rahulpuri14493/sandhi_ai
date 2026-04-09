"""
Business KPI SLA alert hooks.

Sends low-noise webhook alerts when end-user business KPI SLA moves into
unhealthy states (at_risk/breached), with Redis dedupe and metadata.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

_redis_client = None


def _get_redis_client():
    global _redis_client
    if _redis_client is False:
        return None
    if _redis_client is not None:
        return _redis_client
    url = (getattr(settings, "HEARTBEAT_REDIS_URL", None) or "").strip()
    if not url:
        url = (getattr(settings, "CELERY_BROKER_URL", None) or "").strip()
    if not url:
        return None
    try:
        import redis
    except ModuleNotFoundError:
        _redis_client = False
        return None
    try:
        _redis_client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
        )
    except Exception:
        _redis_client = False
    return _redis_client if _redis_client is not False else None


def _dedupe_key(business_id: int) -> str:
    return f"sandhi:business_kpi_alert:v1:{business_id}"


def _meta_key(business_id: int) -> str:
    return f"sandhi:business_kpi_alert_meta:v1:{business_id}"


def get_business_kpi_alert_state(*, business_id: int) -> Dict[str, Any]:
    r = _get_redis_client()
    if r is None:
        return {"last_alert_sent_at": None, "last_alert_status": None}
    try:
        raw = r.get(_meta_key(int(business_id)))
        if not raw:
            return {"last_alert_sent_at": None, "last_alert_status": None}
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return {"last_alert_sent_at": None, "last_alert_status": None}
        return {
            "last_alert_sent_at": obj.get("last_alert_sent_at"),
            "last_alert_status": obj.get("last_alert_status"),
        }
    except Exception:
        return {"last_alert_sent_at": None, "last_alert_status": None}


def maybe_send_business_kpi_alert(
    *,
    business_id: int,
    business_email: str,
    kpis: Dict[str, Any],
) -> None:
    if not bool(getattr(settings, "BUSINESS_KPI_ALERTS_ENABLED", False)):
        return
    webhook = (getattr(settings, "BUSINESS_KPI_ALERT_WEBHOOK_URL", None) or "").strip()
    if not webhook:
        return
    sla = (kpis or {}).get("sla") if isinstance(kpis, dict) else None
    if not isinstance(sla, dict):
        return
    status = (sla.get("status") or "").strip().lower()
    if status not in {"healthy", "at_risk", "breached"}:
        return

    key = _dedupe_key(int(business_id))
    cooldown = max(60, int(getattr(settings, "BUSINESS_KPI_ALERT_COOLDOWN_SECONDS", 900) or 900))
    prior_status = None
    prior_meta = get_business_kpi_alert_state(business_id=int(business_id))
    if isinstance(prior_meta, dict):
        prior_status = (prior_meta.get("last_alert_status") or "").strip().lower() or None

    # Recovery notice is sent only when we previously alerted unhealthy status.
    if status == "healthy":
        if prior_status not in {"at_risk", "breached"}:
            return
        payload = {
            "event": "business_kpi_sla_recovered",
            "severity": "info",
            "business_id": int(business_id),
            "business_email": business_email,
            "sla": sla,
            "message": "Business KPI SLA recovered to healthy",
        }
        try:
            with httpx.Client(timeout=6.0) as client:
                client.post(webhook, json=payload)
            r2 = _get_redis_client()
            if r2 is not None:
                try:
                    meta_payload = json.dumps(
                        {
                            "last_alert_sent_at": datetime.now(timezone.utc).isoformat(),
                            "last_alert_status": "healthy",
                        },
                        separators=(",", ":"),
                    )
                    r2.setex(_meta_key(int(business_id)), max(cooldown * 4, 3600), meta_payload)
                    # Set a short dedupe fingerprint for healthy recovery state to avoid noise.
                    r2.setex(key, cooldown, json.dumps({"status": "healthy"}, separators=(",", ":")))
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("business_kpi_alert_send_fail business_id=%s err=%s", business_id, type(exc).__name__)
        return
    fingerprint = json.dumps(
        {
            "status": status,
            "current_success_rate": sla.get("current_success_rate"),
            "current_p95_latency_seconds": sla.get("current_p95_latency_seconds"),
            "reason": sla.get("reason"),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    r = _get_redis_client()
    if r is not None:
        try:
            prev = r.get(key)
            if prev == fingerprint:
                return
            r.setex(key, cooldown, fingerprint)
        except Exception:
            pass

    payload = {
        "event": "business_kpi_sla_alert",
        "severity": "critical" if status == "breached" else "warning",
        "business_id": int(business_id),
        "business_email": business_email,
        "sla": sla,
        "message": "Business KPI SLA breached" if status == "breached" else "Business KPI SLA at risk",
    }
    try:
        with httpx.Client(timeout=6.0) as client:
            client.post(webhook, json=payload)
        r2 = _get_redis_client()
        if r2 is not None:
            try:
                meta_payload = json.dumps(
                    {
                        "last_alert_sent_at": datetime.now(timezone.utc).isoformat(),
                        "last_alert_status": status,
                    },
                    separators=(",", ":"),
                )
                r2.setex(_meta_key(int(business_id)), max(cooldown * 4, 3600), meta_payload)
            except Exception:
                pass
    except Exception as exc:
        logger.warning("business_kpi_alert_send_fail business_id=%s err=%s", business_id, type(exc).__name__)

