"""
Validate executor JSON immediately before A2A SendMessage.

Catches serialization issues and optional strict task-envelope validation so bad
payloads never leave the platform.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from core.config import settings
from schemas.sandhi_a2a_task import parse_sandhi_a2a_task

logger = logging.getLogger(__name__)

# Practical ceiling for a single text part (A2A agents often buffer in memory)
_DEFAULT_MAX_BYTES = 4_194_304  # 4 MiB


def validate_outbound_a2a_payload(input_data: Dict[str, Any]) -> None:
    """
    Raises:
        ValueError: if serialization fails, size exceeds limit, or strict envelope checks fail.
    """
    if not getattr(settings, "A2A_OUTBOUND_VALIDATE", True):
        return
    try:
        raw = json.dumps(input_data, default=str)
    except (TypeError, ValueError) as e:
        raise ValueError(f"A2A outbound payload is not JSON-serializable: {e}") from e

    max_b = int(getattr(settings, "A2A_OUTBOUND_MAX_BYTES", _DEFAULT_MAX_BYTES) or _DEFAULT_MAX_BYTES)
    encoded = raw.encode("utf-8")
    if len(encoded) > max_b:
        raise ValueError(
            f"A2A outbound payload exceeds A2A_OUTBOUND_MAX_BYTES={max_b} (got {len(encoded)})"
        )

    task = input_data.get("sandhi_a2a_task")
    strict = bool(getattr(settings, "A2A_TASK_ENVELOPE_STRICT", False))
    if strict and task is None:
        raise ValueError("A2A_TASK_ENVELOPE_STRICT=true but sandhi_a2a_task is missing")

    if task is not None:
        try:
            envelope = parse_sandhi_a2a_task(task)
        except Exception as e:
            raise ValueError(f"Invalid sandhi_a2a_task envelope: {e}") from e
        trace = input_data.get("sandhi_trace") or {}
        if isinstance(trace, dict):
            tid_agent = trace.get("agent_id")
            if tid_agent is not None and int(tid_agent) != int(envelope.agent_id):
                raise ValueError(
                    "sandhi_a2a_task.agent_id does not match sandhi_trace.agent_id"
                )
