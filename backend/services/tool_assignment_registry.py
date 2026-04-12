"""
File-backed tool assignment registry.

Operators can mount JSON at ``TOOL_ASSIGNMENT_REGISTRY_PATH``; otherwise the packaged
default under ``backend/resources/config/tool_assignment_registry.default.json`` is used.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import settings

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_cached: Optional["ToolAssignmentRegistry"] = None


def _default_registry_path() -> Path:
    return (
        Path(__file__).resolve().parent.parent
        / "resources"
        / "config"
        / "tool_assignment_registry.default.json"
    )


@dataclass(frozen=True)
class RegistryRule:
    rule_id: str
    task_types: List[str]
    preferred_tool_types: List[str]
    max_tools: int


@dataclass(frozen=True)
class RegistryFallback:
    max_tools: int
    flag_unmatched: bool


@dataclass(frozen=True)
class ToolAssignmentRegistry:
    version: int
    rules: List[RegistryRule]
    fallback: RegistryFallback
    source_path: str

    def rule_for_task_type(self, task_type: str) -> Optional[RegistryRule]:
        tt = (task_type or "").strip().lower()
        if not tt:
            return None
        for rule in self.rules:
            for candidate in rule.task_types:
                if tt == (candidate or "").strip().lower():
                    return rule
        return None


def _parse_registry(raw: Dict[str, Any], source_path: str) -> ToolAssignmentRegistry:
    version = int(raw.get("version") or 1)
    rules_in = raw.get("rules")
    if rules_in is None:
        rules_in = []
    if not isinstance(rules_in, list):
        raise ValueError("registry.rules must be an array")
    rules: List[RegistryRule] = []
    for i, item in enumerate(rules_in):
        if not isinstance(item, dict):
            raise ValueError(f"registry.rules[{i}] must be an object")
        rid = str(item.get("id") or f"rule_{i}")
        tts = item.get("task_types")
        if tts is None:
            tts = []
        if not isinstance(tts, list):
            raise ValueError(f"registry.rules[{i}].task_types must be an array")
        ptt = item.get("preferred_tool_types")
        if ptt is None:
            ptt = []
        if not isinstance(ptt, list):
            raise ValueError(f"registry.rules[{i}].preferred_tool_types must be an array")
        try:
            max_tools = int(item.get("max_tools") or 24)
        except (TypeError, ValueError) as e:
            raise ValueError(f"registry.rules[{i}].max_tools must be an integer") from e
        max_tools = max(1, min(max_tools, 256))
        rules.append(
            RegistryRule(
                rule_id=rid,
                task_types=[str(x).strip().lower() for x in tts if str(x).strip()],
                preferred_tool_types=[str(x).strip().lower() for x in ptt if str(x).strip()],
                max_tools=max_tools,
            )
        )
    fb = raw.get("fallback")
    if fb is None:
        fb = {}
    if not isinstance(fb, dict):
        raise ValueError("registry.fallback must be an object")
    try:
        fb_max = int(fb.get("max_tools") or 24)
    except (TypeError, ValueError) as e:
        raise ValueError("registry.fallback.max_tools must be an integer") from e
    fb_max = max(1, min(fb_max, 256))
    flag_unmatched = bool(fb.get("flag_unmatched", True))
    return ToolAssignmentRegistry(
        version=version,
        rules=rules,
        fallback=RegistryFallback(max_tools=fb_max, flag_unmatched=flag_unmatched),
        source_path=source_path,
    )


def load_tool_assignment_registry_from_path(path: Path) -> ToolAssignmentRegistry:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("registry root must be a JSON object")
    return _parse_registry(data, str(path))


def get_tool_assignment_registry(*, force_reload: bool = False) -> ToolAssignmentRegistry:
    global _cached
    with _lock:
        if _cached is not None and not force_reload:
            return _cached
        custom = (getattr(settings, "TOOL_ASSIGNMENT_REGISTRY_PATH", None) or "").strip()
        path = Path(custom) if custom else _default_registry_path()
        if not path.is_file():
            raise FileNotFoundError(f"Tool assignment registry not found: {path}")
        reg = load_tool_assignment_registry_from_path(path)
        _cached = reg
        logger.info("tool_assignment_registry_loaded path=%s version=%s rules=%s", path, reg.version, len(reg.rules))
        return reg


def reload_tool_assignment_registry() -> ToolAssignmentRegistry:
    return get_tool_assignment_registry(force_reload=True)
