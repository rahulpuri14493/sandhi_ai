"""Parse platform MCP function names (platform_<id>_...) for tenant checks."""
import re
from typing import Optional

_PLATFORM_NAME_RE = re.compile(r"^platform_(\d+)(?:_|$)")


def platform_tool_id_from_mcp_function_name(tool_name: str) -> Optional[int]:
    """Return numeric tool id from names like platform_5_MyDB or platform_5."""
    if not tool_name or not isinstance(tool_name, str):
        return None
    m = _PLATFORM_NAME_RE.match(tool_name.strip())
    return int(m.group(1)) if m else None
