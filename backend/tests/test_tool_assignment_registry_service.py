"""Tests for ``services.tool_assignment_registry``: JSON registry load, parse, and cache."""

import json
from pathlib import Path

import pytest

import services.tool_assignment_registry as registry_module
from core.config import settings
from services.tool_assignment_registry import (
    ToolAssignmentRegistry,
    get_tool_assignment_registry,
    load_tool_assignment_registry_from_path,
    reload_tool_assignment_registry,
)


@pytest.fixture(autouse=True)
def clear_registry_cache():
    registry_module._cached = None
    yield
    registry_module._cached = None


def _minimal_registry_dict() -> dict:
    return {
        "version": 1,
        "rules": [
            {
                "id": "r0",
                "task_types": ["alpha"],
                "preferred_tool_types": ["postgres"],
                "max_tools": 4,
            }
        ],
        "fallback": {"max_tools": 6, "flag_unmatched": False},
    }


class TestLoadToolAssignmentRegistryFromPath:
    def test_accepts_null_rules_array_in_json(self, tmp_path: Path):
        p = tmp_path / "reg.json"
        p.write_text(
            json.dumps({"version": 1, "rules": None, "fallback": {"max_tools": 3, "flag_unmatched": False}}),
            encoding="utf-8",
        )
        reg = load_tool_assignment_registry_from_path(p)
        assert reg.rules == []

    def test_accepts_null_task_types_and_preferred_on_rule(self, tmp_path: Path):
        p = tmp_path / "reg.json"
        body = {
            "version": 1,
            "rules": [{"id": "x", "task_types": None, "preferred_tool_types": None, "max_tools": 2}],
            "fallback": {},
        }
        p.write_text(json.dumps(body), encoding="utf-8")
        reg = load_tool_assignment_registry_from_path(p)
        assert reg.rules[0].task_types == []
        assert reg.rules[0].preferred_tool_types == []

    def test_accepts_null_fallback_in_json(self, tmp_path: Path):
        p = tmp_path / "reg.json"
        p.write_text(
            json.dumps({"version": 1, "rules": [], "fallback": None}),
            encoding="utf-8",
        )
        reg = load_tool_assignment_registry_from_path(p)
        assert reg.fallback.max_tools >= 1

    def test_loads_valid_file(self, tmp_path: Path):
        p = tmp_path / "reg.json"
        p.write_text(json.dumps(_minimal_registry_dict()), encoding="utf-8")
        reg = load_tool_assignment_registry_from_path(p)
        assert reg.version == 1
        assert reg.source_path == str(p)
        assert reg.rule_for_task_type("alpha") is not None
        assert reg.rule_for_task_type("ALPHA") is not None
        assert reg.rule_for_task_type("") is None
        assert reg.rule_for_task_type("unknown") is None

    def test_rejects_root_not_object(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text('"string"', encoding="utf-8")
        with pytest.raises(ValueError, match="JSON object"):
            load_tool_assignment_registry_from_path(p)

    def test_rejects_rules_not_array(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"version": 1, "rules": {}}), encoding="utf-8")
        with pytest.raises(ValueError, match="must be an array"):
            load_tool_assignment_registry_from_path(p)

    def test_rejects_rule_not_object(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"version": 1, "rules": [1], "fallback": {}}), encoding="utf-8")
        with pytest.raises(ValueError, match="rules\\[0\\] must be an object"):
            load_tool_assignment_registry_from_path(p)

    def test_rejects_task_types_not_array(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        body = {"version": 1, "rules": [{"id": "x", "task_types": {}, "preferred_tool_types": [], "max_tools": 1}], "fallback": {}}
        p.write_text(json.dumps(body), encoding="utf-8")
        with pytest.raises(ValueError, match="task_types must be an array"):
            load_tool_assignment_registry_from_path(p)

    def test_rejects_preferred_tool_types_not_array(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        body = {
            "version": 1,
            "rules": [{"id": "x", "task_types": ["a"], "preferred_tool_types": {}, "max_tools": 1}],
            "fallback": {},
        }
        p.write_text(json.dumps(body), encoding="utf-8")
        with pytest.raises(ValueError, match="preferred_tool_types must be an array"):
            load_tool_assignment_registry_from_path(p)

    def test_rejects_rule_max_tools_non_integer(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        body = {
            "version": 1,
            "rules": [{"id": "x", "task_types": ["a"], "preferred_tool_types": [], "max_tools": "nope"}],
            "fallback": {},
        }
        p.write_text(json.dumps(body), encoding="utf-8")
        with pytest.raises(ValueError, match="max_tools must be an integer"):
            load_tool_assignment_registry_from_path(p)

    def test_clamps_rule_max_tools_to_bounds(self, tmp_path: Path):
        p = tmp_path / "reg.json"
        d = _minimal_registry_dict()
        d["rules"][0]["max_tools"] = 9999
        p.write_text(json.dumps(d), encoding="utf-8")
        reg = load_tool_assignment_registry_from_path(p)
        assert reg.rules[0].max_tools == 256

    def test_rejects_fallback_not_object(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"version": 1, "rules": [], "fallback": []}), encoding="utf-8")
        with pytest.raises(ValueError, match="fallback must be an object"):
            load_tool_assignment_registry_from_path(p)

    def test_rejects_fallback_max_tools_invalid(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"version": 1, "rules": [], "fallback": {"max_tools": "x"}}), encoding="utf-8")
        with pytest.raises(ValueError, match="fallback.max_tools"):
            load_tool_assignment_registry_from_path(p)


class TestGetToolAssignmentRegistry:
    def test_uses_packaged_default_when_path_empty(self):
        reg = reload_tool_assignment_registry()
        assert isinstance(reg, ToolAssignmentRegistry)
        assert reg.version >= 1
        reg2 = get_tool_assignment_registry()
        assert reg2.source_path == reg.source_path

    def test_respects_settings_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        p = tmp_path / "custom.json"
        p.write_text(json.dumps(_minimal_registry_dict()), encoding="utf-8")
        monkeypatch.setattr(settings, "TOOL_ASSIGNMENT_REGISTRY_PATH", str(p))
        registry_module._cached = None
        reg = get_tool_assignment_registry(force_reload=True)
        assert reg.source_path == str(p)

    def test_raises_when_configured_file_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        missing = tmp_path / "missing.json"
        monkeypatch.setattr(settings, "TOOL_ASSIGNMENT_REGISTRY_PATH", str(missing))
        registry_module._cached = None
        with pytest.raises(FileNotFoundError, match="not found"):
            get_tool_assignment_registry(force_reload=True)

    def test_reload_refreshes_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        p = tmp_path / "reg.json"
        p.write_text(json.dumps(_minimal_registry_dict()), encoding="utf-8")
        monkeypatch.setattr(settings, "TOOL_ASSIGNMENT_REGISTRY_PATH", str(p))
        registry_module._cached = None
        r1 = get_tool_assignment_registry(force_reload=True)
        d2 = _minimal_registry_dict()
        d2["version"] = 99
        p.write_text(json.dumps(d2), encoding="utf-8")
        r2 = reload_tool_assignment_registry()
        assert r2.version == 99
        assert r1.version != r2.version or r2.version == 99

    def test_cache_returns_same_object_without_force_reload(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        p = tmp_path / "reg.json"
        p.write_text(json.dumps(_minimal_registry_dict()), encoding="utf-8")
        monkeypatch.setattr(settings, "TOOL_ASSIGNMENT_REGISTRY_PATH", str(p))
        registry_module._cached = None
        a = get_tool_assignment_registry(force_reload=True)
        b = get_tool_assignment_registry()
        assert a is b

    def test_clamps_fallback_max_tools(self, tmp_path: Path):
        p = tmp_path / "reg.json"
        body = {"version": 1, "rules": [], "fallback": {"max_tools": 9999, "flag_unmatched": True}}
        p.write_text(json.dumps(body), encoding="utf-8")
        reg = load_tool_assignment_registry_from_path(p)
        assert reg.fallback.max_tools == 256
