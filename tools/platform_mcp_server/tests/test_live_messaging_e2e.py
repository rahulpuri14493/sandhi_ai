"""
Optional live checks against real APIs (opt-in via env). Not run in default CI.

Run: SLACK_LIVE_BOT_TOKEN=xoxb-... pytest -m messaging_live tools/platform_mcp_server/tests/test_live_messaging_e2e.py
"""
from __future__ import annotations

import json
import os

import pytest

pytestmark = [pytest.mark.messaging_live, pytest.mark.e2e]

from execution_integrations import execute_slack


@pytest.mark.skipif(not (os.environ.get("SLACK_LIVE_BOT_TOKEN") or "").strip(), reason="SLACK_LIVE_BOT_TOKEN not set")
def test_slack_live_list_channels():
    token = os.environ["SLACK_LIVE_BOT_TOKEN"].strip()
    out = execute_slack({"bot_token": token}, {"action": "list_channels"})
    data = json.loads(out)
    if data.get("error"):
        pytest.fail(f"list_channels failed: {data}")
    assert isinstance(data.get("channels"), list)
