"""Unit tests for platform tool capability classification and fallback split."""
from services.mcp_tool_capabilities import partition_tools_for_fallback, tool_access_summary


def test_tool_access_search():
    s = tool_access_summary("pinecone")
    assert s["tier"] == "search"
    assert s["supports_artifact_platform_write"] is False


def test_tool_access_sql():
    s = tool_access_summary("postgres")
    assert s["supports_artifact_platform_write"] is True


def test_tool_access_messaging_read_and_write():
    for tt in ("slack", "teams", "smtp"):
        s = tool_access_summary(tt)
        assert s["tier"] == "messaging"
        assert s["interactive_read_primary"] is True
        assert s["supports_interactive_write"] is True
        assert s["supports_artifact_platform_write"] is False
        assert "read" in s["label"].lower() and "write" in s["label"].lower()


def test_partition_multi_agent():
    tools = [
        {"id": 1, "tool_type": "pinecone", "name": "P"},
        {"id": 2, "tool_type": "postgres", "name": "Pg"},
    ]
    out = partition_tools_for_fallback(tools, 2)
    assert len(out) == 2
    assert out[0]["agent_index"] == 0
    assert out[1]["agent_index"] == 1
    assert 2 in out[1]["platform_tool_ids"]
