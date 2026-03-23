"""Shared TLS / verify settings for httpx.AsyncClient across backend services."""
from __future__ import annotations

from typing import Union

from core.config import settings

VerifyType = Union[bool, str]


def httpx_verify_parameter() -> VerifyType:
    """
    Value for httpx ``verify=``: True (default), False (insecure dev), or path to CA bundle.
    """
    if not getattr(settings, "HTTPX_VERIFY_SSL", True):
        return False
    ca = (getattr(settings, "HTTPX_CA_BUNDLE_PATH", None) or "").strip()
    if ca:
        return ca
    return True
