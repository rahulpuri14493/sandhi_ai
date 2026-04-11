from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Dict

from core.config import settings


def _secret_key_bytes() -> bytes:
    secret = (getattr(settings, "SECRET_KEY", None) or "").strip()
    return secret.encode("utf-8")


def derive_execution_hmac_key(*, job_id: int, execution_token: str) -> bytes:
    material = f"hb:{int(job_id)}:{execution_token}".encode("utf-8")
    return hmac.new(_secret_key_bytes(), material, hashlib.sha256).digest()


def canonical_heartbeat_body(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def heartbeat_body_sha256(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_heartbeat_body(payload)).hexdigest()


def heartbeat_signing_string(
    *,
    method: str,
    route_path: str,
    version: str,
    key_id: str,
    timestamp: int,
    nonce: str,
    body_sha256: str,
    job_id: int,
    workflow_step_id: int,
) -> str:
    return "\n".join(
        [
            method.upper(),
            route_path,
            version,
            key_id,
            str(int(timestamp)),
            nonce,
            body_sha256,
            str(int(job_id)),
            str(int(workflow_step_id)),
        ]
    )


def sign_heartbeat_string(*, key: bytes, signing_string: str) -> str:
    return hmac.new(key, signing_string.encode("utf-8"), hashlib.sha256).hexdigest()


def secure_equals_hex(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").strip().lower(), (b or "").strip().lower())


def now_epoch_s() -> int:
    return int(time.time())

