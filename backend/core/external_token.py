"""Helpers for external job access tokens (share links)."""

from datetime import datetime, timedelta
from typing import Optional
from jose import jwt, JWTError

from core.config import settings
from core.security import SECRET_KEY, ALGORITHM


def create_job_token(job_id: int) -> str:
    """Create a JWT token for external access to a specific job."""
    expire_days = getattr(settings, "EXTERNAL_TOKEN_EXPIRE_DAYS", 7)
    expire = datetime.utcnow() + timedelta(days=expire_days)
    payload = {"job_id": job_id, "type": "external_view", "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_job_token(token: str, job_id: int) -> bool:
    """Verify JWT token for job access."""
    if not token:
        return False
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return (
            payload.get("job_id") == job_id and payload.get("type") == "external_view"
        )
    except (JWTError, Exception):
        return False


def get_share_url(job_id: int) -> str:
    """Get the full external share URL for a job."""
    token = create_job_token(job_id)
    base_url = getattr(settings, "EXTERNAL_API_BASE_URL", "http://localhost:8000")
    return f"{base_url.rstrip('/')}/api/external/jobs/{job_id}?token={token}"
