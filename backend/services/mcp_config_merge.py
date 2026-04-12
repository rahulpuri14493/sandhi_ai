"""
Merge partial MCP tool/connection config over stored encrypted values.

UI forms use empty fields to mean "keep existing". JSON may also carry null or
whitespace-only strings; those must not wipe stored secrets when merging.
"""

# Keys never returned in GET /tools/{id} config_preview (edit-form repopulation).
_MCP_CONFIG_SECRET_KEYS = frozenset(
    {
        "access_token",
        "oauth_refresh_token",
        "oauth2_access_token",
        "password",
        "api_key",
        "token",
        "secret_access_key",
        "secret_key",
        "connection_string",
        "credentials_json",
        "bot_token",
        "openai_api_key",
        "ssl_key",
    }
)


def public_config_preview(cfg: dict) -> dict[str, str]:
    """Scalar, non-secret fields from stored config for the MCP edit UI."""
    if not isinstance(cfg, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in cfg.items():
        if k in _MCP_CONFIG_SECRET_KEYS:
            continue
        if v is None:
            continue
        if isinstance(v, (dict, list)):
            continue
        s = str(v).strip()
        if not s:
            continue
        out[str(k)] = s[:4096]
    return out


def merge_shallow_config(base: dict, patch: dict) -> dict:
    """
    Return a copy of `base` updated with keys from `patch` except:
    - patch value is None → skip (keep base value if any)
    - patch value is a str and strip() is empty → skip
    """
    out = dict(base) if isinstance(base, dict) else {}
    if not isinstance(patch, dict):
        return out
    for key, value in patch.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        out[key] = value
    return out
