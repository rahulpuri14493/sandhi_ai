"""
Encryption utilities for sensitive data (e.g. MCP credentials).
Uses Fernet (symmetric). Production: set MCP_ENCRYPTION_KEY to a long random
value (e.g. 32+ chars). If unset, the key is derived from SECRET_KEY.

Generate a key: python -c "import secrets; print(secrets.token_urlsafe(32))"
"""

import base64
import hashlib
import logging
import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Production: set MCP_ENCRYPTION_KEY (long random string). If unset, key is derived from SECRET_KEY.
MCP_ENCRYPTION_KEY_ENV = "MCP_ENCRYPTION_KEY"
SECRET_KEY_DEFAULT = "your-secret-key-change-in-production"
SECRET_KEY = os.getenv("SECRET_KEY", SECRET_KEY_DEFAULT)


def _get_fernet_key() -> bytes:
    key_env = os.getenv(MCP_ENCRYPTION_KEY_ENV)
    if key_env and len(key_env) >= 16:
        try:
            return base64.urlsafe_b64encode(hashlib.sha256(key_env.encode()).digest())
        except Exception:
            pass
    return base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY.encode()).digest())


def encrypt_value(plain: str) -> str:
    """Encrypt a string; returns base64-encoded ciphertext."""
    from cryptography.fernet import Fernet

    key = _get_fernet_key()
    f = Fernet(key)
    return f.encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_value(cipher: str) -> str:
    """Decrypt a base64 Fernet ciphertext to string."""
    from cryptography.fernet import Fernet

    key = _get_fernet_key()
    f = Fernet(key)
    return f.decrypt(cipher.encode("ascii")).decode("utf-8")


def encrypt_json(data: dict) -> str:
    """Serialize dict to JSON and encrypt."""
    import json

    return encrypt_value(json.dumps(data, sort_keys=True))


def decrypt_json(cipher: str) -> dict:
    """Decrypt and parse JSON."""
    import json

    return json.loads(decrypt_value(cipher))


def ensure_encryption_key_for_production() -> None:
    """
    Call at startup. Logs warnings when running without a dedicated MCP_ENCRYPTION_KEY:
    - If both SECRET_KEY and MCP_ENCRYPTION_KEY are default/missing, warns about dev setup.
    - If SECRET_KEY is changed (production-like) but MCP_ENCRYPTION_KEY is unset,
      recommends setting MCP_ENCRYPTION_KEY for production so MCP credentials use
      a key independent of JWT signing.
    """
    mcp_key = os.getenv(MCP_ENCRYPTION_KEY_ENV)
    secret = os.getenv("SECRET_KEY", SECRET_KEY_DEFAULT)
    if mcp_key and len(mcp_key) >= 32:
        return
    if secret == SECRET_KEY_DEFAULT and not mcp_key:
        logger.warning(
            "MCP_ENCRYPTION_KEY is not set; MCP credentials use key derived from SECRET_KEY. "
            'For production, set MCP_ENCRYPTION_KEY (e.g. python -c "import secrets; print(secrets.token_urlsafe(32))").'
        )
        return
    logger.warning(
        "MCP_ENCRYPTION_KEY is not set. MCP credentials are encrypted with a key derived from SECRET_KEY. "
        "For production, set MCP_ENCRYPTION_KEY to a long random value for independent credential encryption."
    )
