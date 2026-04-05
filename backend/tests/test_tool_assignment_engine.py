"""Tests for ``services.tool_assignment_engine``: task-type inference and tool ordering."""

from unittest.mock import patch

import pytest

from core.config import settings
from models.agent import Agent, AgentStatus, PricingModel
from services.tool_assignment_engine import assign_tools_for_step, infer_task_type
from services.tool_assignment_registry import load_tool_assignment_registry_from_path


def _agent(agent_id: int = 1) -> Agent:
    return Agent(
        id=agent_id,
        developer_id=1,
        name="TestAgent",
        price_per_task=0.0,
        status=AgentStatus.ACTIVE,
        pricing_model=PricingModel.PAY_PER_USE,
    )


class TestInferTaskType:
    def test_returns_explicit_task_type_when_valid_slug(self):
        assert infer_task_type({"task_type": "Research", "assigned_task": "x"}) == "research"

    def test_ignores_invalid_explicit_task_type_and_falls_back_to_assigned_task_token(self):
        assert infer_task_type({"task_type": "not a slug!!!", "assigned_task": "hello world"}) == "hello"

    def test_invalid_explicit_and_unusable_assigned_task_yields_default(self):
        assert infer_task_type({"task_type": "!!!", "assigned_task": "123 nope"}) == "default"

    def test_infers_from_assigned_task_first_token(self):
        assert infer_task_type({"assigned_task": "research the documents"}) == "research"

    def test_returns_default_for_empty_input(self):
        assert infer_task_type({}) == "default"

    def test_returns_default_when_first_token_not_slug(self):
        assert infer_task_type({"assigned_task": "123broken token"}) == "default"


class TestAssignToolsForStep:
    def test_registry_orders_by_preferred_tool_types(self, tmp_path):
        reg_path = tmp_path / "reg.json"
        reg_path.write_text(
            """
            {
              "version": 1,
              "rules": [{
                "id": "vec",
                "task_types": ["research"],
                "preferred_tool_types": ["pinecone", "postgres"],
                "max_tools": 10
              }],
              "fallback": {"max_tools": 10, "flag_unmatched": true}
            }
            """.strip(),
            encoding="utf-8",
        )
        reg = load_tool_assignment_registry_from_path(reg_path)
        tools = [
            {"name": "p", "tool_type": "postgres"},
            {"name": "b", "tool_type": "pinecone"},
            {"name": "z", "tool_type": "mysql"},
        ]
        ordered, meta, source, flagged = assign_tools_for_step(
            input_data={"task_type": "research"},
            agent=_agent(),
            available_mcp_tools=tools,
            registry=reg,
        )
        assert source == "registry"
        assert not flagged
        assert [t["name"] for t in ordered[:2]] == ["b", "p"]

    def test_fallback_marks_flagged_when_no_rule(self, tmp_path):
        reg_path = tmp_path / "reg.json"
        reg_path.write_text(
            '{"version":1,"rules":[],"fallback":{"max_tools":5,"flag_unmatched":true}}',
            encoding="utf-8",
        )
        reg = load_tool_assignment_registry_from_path(reg_path)
        tools = [{"name": "a", "tool_type": "s3"}]
        _ordered, _meta, source, flagged = assign_tools_for_step(
            input_data={"task_type": "unknown"},
            agent=_agent(),
            available_mcp_tools=tools,
            registry=reg,
        )
        assert source == "registry_fallback"
        assert flagged

    def test_passthrough_when_assignment_disabled(self):
        tools = [{"name": "x", "tool_type": "s3", "source": "platform"}]
        with patch.object(settings, "TOOL_ASSIGNMENT_ENABLED", False):
            ordered, meta, source, flagged = assign_tools_for_step(
                input_data={},
                agent=_agent(),
                available_mcp_tools=tools,
                registry=None,
            )
        assert ordered == tools
        assert source == "passthrough"
        assert not flagged
        assert meta[0].execution_hints == {"source": "platform"}

    def test_llm_suggested_names_reorder_when_enabled(self, tmp_path):
        reg_path = tmp_path / "reg.json"
        reg_path.write_text(
            '{"version":1,"rules":[{"id":"r","task_types":["t"],"preferred_tool_types":["s3"],"max_tools":5}],'
            '"fallback":{"max_tools":5,"flag_unmatched":false}}',
            encoding="utf-8",
        )
        reg = load_tool_assignment_registry_from_path(reg_path)
        tools = [
            {"name": "second", "tool_type": "s3"},
            {"name": "first", "tool_type": "s3"},
        ]
        with patch.object(settings, "TOOL_ASSIGNMENT_USE_LLM", True):
            ordered, _meta, source, flagged = assign_tools_for_step(
                input_data={"task_type": "t", "llm_suggested_tool_names": ["first", "second"]},
                agent=_agent(),
                available_mcp_tools=tools,
                registry=reg,
            )
        assert source == "llm"
        assert not flagged
        assert [t["name"] for t in ordered] == ["first", "second"]

    def test_assignment_requirements_must_include_appends_tools(self, tmp_path):
        reg_path = tmp_path / "reg.json"
        reg_path.write_text(
            '{"version":1,"rules":[{"id":"r","task_types":["t"],"preferred_tool_types":["s3"],"max_tools":2}],'
            '"fallback":{"max_tools":2,"flag_unmatched":false}}',
            encoding="utf-8",
        )
        reg = load_tool_assignment_registry_from_path(reg_path)
        tools = [
            {"name": "keep", "tool_type": "s3"},
            {"name": "extra", "tool_type": "s3"},
            {"name": "must", "tool_type": "postgres"},
        ]
        ordered, meta, _source, _flagged = assign_tools_for_step(
            input_data={
                "task_type": "t",
                "assignment_requirements": {"must_include_tool_names": ["must"]},
            },
            agent=_agent(),
            available_mcp_tools=tools,
            registry=reg,
        )
        names = [t["name"] for t in ordered]
        assert "must" in names
        assert len(meta) == len(ordered)

    def test_must_include_two_tools_when_max_tools_two_keeps_both(self, tmp_path):
        """max_tools=2 with two required tools in a 3+ tool pool: both required tools stay in ordered."""
        reg_path = tmp_path / "reg.json"
        reg_path.write_text(
            '{"version":1,"rules":[{"id":"r","task_types":["t"],"preferred_tool_types":["s3"],'
            '"max_tools":2}],"fallback":{"max_tools":2,"flag_unmatched":false}}',
            encoding="utf-8",
        )
        reg = load_tool_assignment_registry_from_path(reg_path)
        tools = [
            {"name": "m1", "tool_type": "postgres"},
            {"name": "m2", "tool_type": "mysql"},
            {"name": "extra", "tool_type": "s3"},
        ]
        ordered, meta, _source, _flagged = assign_tools_for_step(
            input_data={
                "task_type": "t",
                "assignment_requirements": {"must_include_tool_names": ["m1", "m2"]},
            },
            agent=_agent(),
            available_mcp_tools=tools,
            registry=reg,
        )
        names = [t["name"] for t in ordered]
        assert set(names) >= {"m1", "m2"}
        assert len(ordered) >= 2
        assert len(meta) == len(ordered)

    def test_non_dict_assignment_requirements_ignored(self, tmp_path):
        reg_path = tmp_path / "reg.json"
        reg_path.write_text(
            '{"version":1,"rules":[{"id":"r","task_types":["t"],"preferred_tool_types":[],"max_tools":3}],'
            '"fallback":{"max_tools":3,"flag_unmatched":false}}',
            encoding="utf-8",
        )
        reg = load_tool_assignment_registry_from_path(reg_path)
        tools = [{"name": "a", "tool_type": "x"}]
        assign_tools_for_step(
            input_data={"task_type": "t", "assignment_requirements": "invalid"},
            agent=_agent(),
            available_mcp_tools=tools,
            registry=reg,
        )

    def test_skips_non_dict_and_nameless_tool_rows(self, tmp_path):
        reg_path = tmp_path / "reg.json"
        reg_path.write_text(
            '{"version":1,"rules":[],"fallback":{"max_tools":10,"flag_unmatched":false}}',
            encoding="utf-8",
        )
        reg = load_tool_assignment_registry_from_path(reg_path)
        tools = [1, {"name": "", "tool_type": "s3"}, {"name": "ok", "tool_type": "s3"}]
        ordered, _meta, _s, _f = assign_tools_for_step(
            input_data={},
            agent=_agent(),
            available_mcp_tools=tools,
            registry=reg,
        )
        assert len(ordered) == 1
        assert ordered[0]["name"] == "ok"
