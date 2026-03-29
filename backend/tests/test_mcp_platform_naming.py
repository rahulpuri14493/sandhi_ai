from services.mcp_platform_naming import platform_tool_id_from_mcp_function_name


def test_platform_tool_id_parsed_with_suffix():
    assert platform_tool_id_from_mcp_function_name("platform_5_MyDB") == 5


def test_platform_tool_id_parsed_id_only():
    assert platform_tool_id_from_mcp_function_name("platform_12") == 12


def test_non_platform_name_returns_none():
    assert platform_tool_id_from_mcp_function_name("byo_3_foo") is None
    assert platform_tool_id_from_mcp_function_name("") is None
