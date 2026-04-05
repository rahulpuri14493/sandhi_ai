"""Tests for ``services.agent_tool_compatibility``: capability-based MCP tool filtering."""

from unittest.mock import patch

import pytest

from models.agent import Agent, AgentStatus, PricingModel
from services.agent_tool_compatibility import filter_tools_for_agent, validate_tools_for_agent


def _agent(**kwargs) -> Agent:
    base = dict(
        id=1,
        developer_id=1,
        name="A",
        price_per_task=0.0,
        status=AgentStatus.ACTIVE,
        pricing_model=PricingModel.PAY_PER_USE,
    )
    base.update(kwargs)
    return Agent(**base)


class TestFilterToolsForAgent:
    def test_no_capabilities_returns_all_dict_tools(self):
        agent = _agent()
        tools = [{"name": "a", "tool_type": "s3"}, {"name": "b", "tool_type": "postgres"}]
        assert filter_tools_for_agent(agent, tools) == tools

    def test_allow_types_keeps_only_listed_tool_types(self):
        agent = _agent(capabilities=["mcp:allow_types:postgres,mysql"])
        tools = [
            {"name": "p", "tool_type": "postgres"},
            {"name": "s", "tool_type": "s3"},
        ]
        out = filter_tools_for_agent(agent, tools)
        assert len(out) == 1
        assert out[0]["name"] == "p"

    def test_deny_types_removes_listed_tool_types(self):
        agent = _agent(capabilities=["mcp:deny_types:s3"])
        tools = [
            {"name": "p", "tool_type": "postgres"},
            {"name": "s", "tool_type": "s3"},
        ]
        out = filter_tools_for_agent(agent, tools)
        assert len(out) == 1
        assert out[0]["name"] == "p"

    def test_skips_non_dict_entries(self):
        agent = _agent()
        tools = [None, {"name": "ok", "tool_type": "s3"}]
        assert len(filter_tools_for_agent(agent, tools)) == 1

    def test_empty_tool_type_passes_allow_list_filter(self):
        agent = _agent(capabilities=["mcp:allow_types:postgres"])
        tools = [{"name": "weird", "tool_type": ""}]
        assert len(filter_tools_for_agent(agent, tools)) == 1

    def test_capabilities_non_list_treated_as_unconstrained(self):
        agent = _agent(capabilities="not-a-list")
        tools = [{"name": "x", "tool_type": "s3"}]
        assert filter_tools_for_agent(agent, tools) == tools

    def test_debug_log_when_tools_dropped(self):
        agent = _agent(capabilities=["mcp:allow_types:postgres"])
        tools = [{"name": "s", "tool_type": "s3"}]
        with patch("services.agent_tool_compatibility.logger.debug") as log:
            filter_tools_for_agent(agent, tools)
        log.assert_called_once()


class TestValidateToolsForAgent:
    def test_no_constraints_returns_empty_issues(self):
        agent = _agent()
        assert validate_tools_for_agent(agent, [{"name": "x", "tool_type": "s3"}]) == []

    def test_returns_message_when_all_tools_excluded(self):
        agent = _agent(capabilities=["mcp:allow_types:postgres"])
        errs = validate_tools_for_agent(agent, [{"name": "s", "tool_type": "s3"}])
        assert errs and "excluded all MCP tools" in errs[0]

    def test_empty_tool_list_no_error_even_with_constraints(self):
        agent = _agent(capabilities=["mcp:allow_types:postgres"])
        assert validate_tools_for_agent(agent, []) == []
