import json
import logging

from core.config import settings

logger = logging.getLogger(__name__)


def warn_mcp_guardrail_sanity() -> None:
    tenant_limit = int(getattr(settings, "MCP_TENANT_MAX_CONCURRENT_CALLS", 0) or 0)
    target_limit = int(getattr(settings, "MCP_TARGET_MAX_CONCURRENT_CALLS", 0) or 0)
    wait_s = float(getattr(settings, "MCP_CONCURRENCY_WAIT_SECONDS", 10.0) or 10.0)
    read_attempts = int(getattr(settings, "MCP_READ_INVOCATION_MAX_ATTEMPTS", 0) or 0)
    write_attempts = int(getattr(settings, "MCP_WRITE_INVOCATION_MAX_ATTEMPTS", 0) or 0)
    legacy_attempts = int(getattr(settings, "MCP_INVOCATION_MAX_ATTEMPTS", 3) or 3)
    if tenant_limit > 0 and target_limit > tenant_limit:
        logger.warning(
            "mcp_guardrails_sanity target concurrency (%s) exceeds tenant concurrency (%s)",
            target_limit,
            tenant_limit,
        )
    max_attempts = max(read_attempts, write_attempts, legacy_attempts)
    if max_attempts >= 5 and wait_s >= 15.0:
        logger.warning(
            "mcp_guardrails_sanity high retries (%s) + high queue wait (%s) may amplify latency under load",
            max_attempts,
            wait_s,
        )
    for key in ("MCP_TOOL_POLICY_JSON", "MCP_TENANT_TIER_POLICY_JSON"):
        raw = getattr(settings, key, "{}") or "{}"
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                logger.warning("mcp_guardrails_sanity %s must decode to object/dict; got %s", key, type(parsed).__name__)
        except Exception:
            logger.warning("mcp_guardrails_sanity invalid JSON in %s", key)
