"""Unit tests for module-level helpers in services.agent_executor (no DB execution)."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import services.agent_executor as ae


def test_load_step_input_json_empty():
    assert ae._load_step_input_json(None, job_id=1, step_id=2, step_order=1) == {}
    assert ae._load_step_input_json("", job_id=1, step_id=2, step_order=1) == {}
    assert ae._load_step_input_json("   ", job_id=1, step_id=2, step_order=1) == {}


def test_load_step_input_json_valid():
    d = ae._load_step_input_json(
        '{"job_title":"x","documents":[]}',
        job_id=7,
        step_id=8,
        step_order=1,
    )
    assert d["job_title"] == "x"


def test_load_step_input_json_invalid():
    with pytest.raises(ValueError, match="not valid JSON"):
        ae._load_step_input_json("{", job_id=1, step_id=2, step_order=1)


def test_load_step_input_json_not_object():
    with pytest.raises(ValueError, match="must be a JSON object"):
        ae._load_step_input_json("[1]", job_id=1, step_id=2, step_order=1)


def test_sign_trusted_bootstrap_payload_no_secret(monkeypatch):
    monkeypatch.setattr(ae.settings, "MCP_INTERNAL_SECRET", "")
    assert ae._sign_trusted_bootstrap_payload(
        tool_name="t", operation_type="upsert", schema="s", table="tbl", bootstrap_sql="SELECT 1"
    ) is None


def test_sign_trusted_bootstrap_payload_with_secret(monkeypatch):
    monkeypatch.setattr(ae.settings, "MCP_INTERNAL_SECRET", "s3cr3t")
    sig = ae._sign_trusted_bootstrap_payload(
        tool_name="platform_1_postgres",
        operation_type="UPSERT",
        schema="public",
        table="users",
        bootstrap_sql={"x": 1},
    )
    assert isinstance(sig, str) and len(sig) == 64


def test_safe_slug():
    assert ae._safe_slug("") == ""
    assert ae._safe_slug("  Hello World!  ") == "Hello_World_"


def test_parse_allowed_ids():
    assert ae._parse_allowed_ids(None) is None
    assert ae._parse_allowed_ids("") is None
    assert ae._parse_allowed_ids("  ") is None
    assert ae._parse_allowed_ids([1, 2]) == [1, 2]
    assert ae._parse_allowed_ids(json.dumps([1, 2])) == [1, 2]
    assert ae._parse_allowed_ids("not-json") is None


def test_get_workflow_collaboration_hint_from_job():
    job = SimpleNamespace(conversation=None)
    assert ae._get_workflow_collaboration_hint_from_job(job) is None
    job2 = SimpleNamespace(conversation="not-json")
    assert ae._get_workflow_collaboration_hint_from_job(job2) is None
    job3 = SimpleNamespace(conversation=json.dumps({}))
    assert ae._get_workflow_collaboration_hint_from_job(job3) is None
    job4 = SimpleNamespace(
        conversation=json.dumps(
            [
                {"type": "analysis"},
                {"workflow_collaboration_hint": "sequential"},
            ]
        )
    )
    assert ae._get_workflow_collaboration_hint_from_job(job4) == "sequential"


def test_apply_tool_visibility():
    tools = [{"name": "n", "description": "d" * 300, "source": "p", "tool_type": "postgres"}]
    assert ae._apply_tool_visibility(tools, "none") == []
    assert ae._apply_tool_visibility([], "full") == []
    slim = ae._apply_tool_visibility(tools, "names_only")
    assert len(slim) == 1
    assert slim[0]["name"] == "n"
    assert len(slim[0]["description"]) <= 200
    assert ae._apply_tool_visibility(tools, "full") == tools


def test_parse_output_contract():
    assert ae._parse_output_contract(None) == {}
    assert ae._parse_output_contract("") == {}
    assert ae._parse_output_contract("bad") == {}
    assert ae._parse_output_contract(json.dumps({"a": 1})) == {"a": 1}
    assert ae._parse_output_contract(json.dumps([1])) == {}


def test_partition_workflow_waves_empty():
    assert ae._partition_workflow_waves([]) == []


def test_partition_workflow_waves_all_sequential():
    s1 = SimpleNamespace(id=1, step_order=1, depends_on_previous=True)
    s2 = SimpleNamespace(id=2, step_order=2, depends_on_previous=True)
    s3 = SimpleNamespace(id=3, step_order=3, depends_on_previous=True)
    steps = [s1, s2, s3]
    waves = ae._partition_workflow_waves(steps)
    assert waves == [[s1], [s2], [s3]]


def test_partition_workflow_waves_independent_run_together():
    s1 = SimpleNamespace(id=1, step_order=1, depends_on_previous=True)
    s2 = SimpleNamespace(id=2, step_order=2, depends_on_previous=False)
    s3 = SimpleNamespace(id=3, step_order=3, depends_on_previous=False)
    s4 = SimpleNamespace(id=4, step_order=4, depends_on_previous=True)
    steps = [s1, s2, s3, s4]
    waves = ae._partition_workflow_waves(steps)
    assert waves == [[s1, s2, s3], [s4]]


def test_partition_workflow_waves_single_step():
    s1 = SimpleNamespace(id=1, step_order=1, depends_on_previous=True)
    assert ae._partition_workflow_waves([s1]) == [[s1]]


def test_partition_workflow_waves_first_independent_then_two_parallel():
    """First step alone (no prior); second and third independent of each other → one wave of three."""
    s1 = SimpleNamespace(id=1, step_order=1, depends_on_previous=False)
    s2 = SimpleNamespace(id=2, step_order=2, depends_on_previous=False)
    s3 = SimpleNamespace(id=3, step_order=3, depends_on_previous=False)
    waves = ae._partition_workflow_waves([s1, s2, s3])
    assert waves == [[s1, s2, s3]]


def test_partition_workflow_waves_alternating_dependent_independent():
    """T, F, T, F → [s1,s2], [s3,s4] — each dependent step starts a new wave."""
    s1 = SimpleNamespace(id=1, step_order=1, depends_on_previous=True)
    s2 = SimpleNamespace(id=2, step_order=2, depends_on_previous=False)
    s3 = SimpleNamespace(id=3, step_order=3, depends_on_previous=True)
    s4 = SimpleNamespace(id=4, step_order=4, depends_on_previous=False)
    waves = ae._partition_workflow_waves([s1, s2, s3, s4])
    assert waves == [[s1, s2], [s3, s4]]


def test_partition_workflow_waves_missing_depends_attr_defaults_sequential():
    """getattr(..., True) when depends_on_previous is absent → no parallel merge."""
    s1 = SimpleNamespace(id=1, step_order=1)
    s2 = SimpleNamespace(id=2, step_order=2)
    s3 = SimpleNamespace(id=3, step_order=3)
    waves = ae._partition_workflow_waves([s1, s2, s3])
    assert waves == [[s1], [s2], [s3]]


def test_partition_workflow_waves_third_independent_joins_prior_wave():
    """T, T, F — step 3 has depends_on_previous=False so it merges into the same wave as step 2."""
    s1 = SimpleNamespace(id=1, step_order=1, depends_on_previous=True)
    s2 = SimpleNamespace(id=2, step_order=2, depends_on_previous=True)
    s3 = SimpleNamespace(id=3, step_order=3, depends_on_previous=False)
    waves = ae._partition_workflow_waves([s1, s2, s3])
    assert waves == [[s1], [s2, s3]]


def test_next_workflow_step_returns_lowest_following_step_order():
    s1 = SimpleNamespace(id=1, step_order=1)
    s2 = SimpleNamespace(id=2, step_order=3)
    s3 = SimpleNamespace(id=3, step_order=2)
    nxt = ae._next_workflow_step([s1, s2, s3], s1)
    assert nxt is not None and nxt.id == s3.id


def test_next_workflow_step_returns_none_for_terminal_step():
    s1 = SimpleNamespace(id=1, step_order=2)
    assert ae._next_workflow_step([s1], s1) is None


def test_parallel_context_for_step_single_wave():
    s1 = SimpleNamespace(id=10, step_order=1, depends_on_previous=True)
    ctx = ae._parallel_context_for_step([s1], s1)
    assert ctx is not None
    assert ctx["wave_index"] == 0
    assert ctx["parallel_group_id"] == "job-wave-0"
    assert ctx["concurrent_workflow_step_ids"] == [10]
    assert ctx["depends_on_previous_wave"] is False


def test_parallel_context_for_step_second_wave_depends_on_previous():
    s1 = SimpleNamespace(id=1, step_order=1, depends_on_previous=True)
    s2 = SimpleNamespace(id=2, step_order=2, depends_on_previous=True)
    ctx = ae._parallel_context_for_step([s1, s2], s2)
    assert ctx["wave_index"] == 1
    assert ctx["depends_on_previous_wave"] is True


def test_parallel_context_for_step_returns_none_when_step_not_in_waves():
    s1 = SimpleNamespace(id=1, step_order=1, depends_on_previous=True)
    orphan = SimpleNamespace(id=99, step_order=5, depends_on_previous=True)
    assert ae._parallel_context_for_step([s1], orphan) is None


def test_parse_write_policy():
    c = {"write_policy": {"on_write_error": "continue", "min_successful_targets": 2}}
    p = ae._parse_write_policy(c, write_targets_count=5)
    assert p["on_write_error"] == "continue"
    assert p["min_successful_targets"] == 2
    p2 = ae._parse_write_policy({"write_policy": {"on_write_error": "bogus"}}, 3)
    assert p2["on_write_error"] == "fail_job"
    p3 = ae._parse_write_policy({"write_policy": {"min_successful_targets": "x"}}, 4)
    assert p3["min_successful_targets"] == 4


def test_sanitize_platform_sql_tool_arguments():
    args = {"query": "SELECT 1", "params": [], "artifact_ref": {}, "noise": 1}
    out = ae._sanitize_platform_sql_tool_arguments("postgres", args)
    assert set(out.keys()) == {"query", "params"}
    assert ae._sanitize_platform_sql_tool_arguments("slack", args) == args


def test_ensure_records_for_platform_write():
    assert ae._ensure_records_for_platform_write({"records": []}, write_mode="platform", write_targets=[{}]) == {
        "records": []
    }
    with pytest.raises(ValueError, match="tabular"):
        ae._ensure_records_for_platform_write("plain text", write_mode="platform", write_targets=[{}])
    with patch(
        "services.agent_executor.extract_record_rows_from_agent_output",
        return_value=[{"a": 1}],
    ):
        r = ae._ensure_records_for_platform_write({"x": 1}, write_mode="platform", write_targets=[{}])
        assert r == {"records": [{"a": 1}]}


def test_normalize_placeholder_error_values():
    assert ae._normalize_placeholder_error_values({"k": "Error retrieving data"})["k"] is None
    assert ae._normalize_placeholder_error_values(["Error retrieving"]) == [None]
    assert ae._normalize_placeholder_error_values("ok") == "ok"


def test_is_sql_programming_error_tool_result():
    assert ae._is_sql_programming_error_tool_result("ProgrammingError: Error: syntax") is True
    assert ae._is_sql_programming_error_tool_result("ok") is False


def test_sql_schema_discovery_query():
    assert "INFORMATION_SCHEMA" in (ae._sql_schema_discovery_query("sqlserver") or "")
    assert "information_schema" in (ae._sql_schema_discovery_query("postgres") or "").lower()
    assert "DATABASE()" in (ae._sql_schema_discovery_query("mysql") or "")
    assert ae._sql_schema_discovery_query("slack") is None


def test_openai_tools_from_mcp(monkeypatch):
    monkeypatch.setattr(ae, "_input_schema_for_tool_type", lambda tt: {"type": "object", "properties": {"q": {}}})
    tools = ae._openai_tools_from_mcp(
        [
            {"name": "", "description": "x"},
            {
                "name": "platform_1_pg",
                "description": "Run SQL",
                "tool_type": "postgres",
                "schema_metadata": {"t": 1},
            },
            {
                "name": "ext_tool",
                "description": "BYO",
                "source": "external",
                "input_schema": {"type": "object"},
                "tool_type": "rest_api",
            },
        ]
    )
    assert len(tools) == 2
    assert tools[0]["function"]["name"] == "platform_1_pg"
    assert "schema" in tools[0]["function"]["description"].lower()
    assert tools[1]["function"]["parameters"] == {"type": "object"}


def _exec_stub_db():
    return MagicMock()


def test_guardrail_is_retryable_asyncio_timeout():
    ex = ae.AgentExecutor(_exec_stub_db())
    assert ex._is_retryable_step_exception(asyncio.TimeoutError()) is True


def test_guardrail_is_retryable_http_status_503():
    ex = ae.AgentExecutor(_exec_stub_db())
    req = httpx.Request("GET", "http://example.invalid/")
    resp = httpx.Response(503, request=req)
    err = httpx.HTTPStatusError("down", request=req, response=resp)
    assert ex._is_retryable_step_exception(err) is True


def test_guardrail_is_not_retryable_http_status_400():
    ex = ae.AgentExecutor(_exec_stub_db())
    req = httpx.Request("GET", "http://example.invalid/")
    resp = httpx.Response(400, request=req)
    err = httpx.HTTPStatusError("bad", request=req, response=resp)
    assert ex._is_retryable_step_exception(err) is False


def test_guardrail_is_retryable_transport_error():
    ex = ae.AgentExecutor(_exec_stub_db())
    req = httpx.Request("GET", "http://example.invalid/")
    err = httpx.ConnectError("connection refused", request=req)
    assert ex._is_retryable_step_exception(err) is True


def test_guardrail_is_retryable_message_heuristic():
    ex = ae.AgentExecutor(_exec_stub_db())
    assert ex._is_retryable_step_exception(ValueError("upstream gateway timeout")) is True


def test_guardrail_validate_empty_output_when_required(monkeypatch):
    ex = ae.AgentExecutor(_exec_stub_db())
    monkeypatch.setattr(ae.settings, "AGENT_OUTPUT_REQUIRE_NONEMPTY", True)
    with pytest.raises(ValueError, match="empty output"):
        ex._validate_agent_output_guardrails(None)
    with pytest.raises(ValueError, match="empty text"):
        ex._validate_agent_output_guardrails("   ")
    with pytest.raises(ValueError, match="empty list"):
        ex._validate_agent_output_guardrails([])


def test_guardrail_validate_empty_allowed_when_disabled(monkeypatch):
    ex = ae.AgentExecutor(_exec_stub_db())
    monkeypatch.setattr(ae.settings, "AGENT_OUTPUT_REQUIRE_NONEMPTY", False)
    ex._validate_agent_output_guardrails(None)
    ex._validate_agent_output_guardrails([])


def test_guardrail_validate_non_dict_passthrough(monkeypatch):
    ex = ae.AgentExecutor(_exec_stub_db())
    monkeypatch.setattr(ae.settings, "AGENT_OUTPUT_REQUIRE_NONEMPTY", False)
    ex._validate_agent_output_guardrails(42)


def test_guardrail_validate_retryable_and_fatal_status():
    ex = ae.AgentExecutor(_exec_stub_db())
    with pytest.raises(RuntimeError, match="retryable_error"):
        ex._validate_agent_output_guardrails({"status": "transient_error"})
    with pytest.raises(ValueError, match="fatal agent status"):
        ex._validate_agent_output_guardrails({"status": "failed"})


def test_guardrail_validate_confidence_threshold(monkeypatch):
    ex = ae.AgentExecutor(_exec_stub_db())
    monkeypatch.setattr(ae.settings, "AGENT_OUTPUT_MIN_CONFIDENCE", 0.9)
    with pytest.raises(ValueError, match="Low confidence"):
        ex._validate_agent_output_guardrails({"confidence": 0.1, "content": "x"})


def test_guardrail_validate_error_only_payload():
    ex = ae.AgentExecutor(_exec_stub_db())
    with pytest.raises(ValueError, match="only contains error"):
        ex._validate_agent_output_guardrails({"error": "nope"})


def test_guardrail_validate_error_with_content_ok():
    ex = ae.AgentExecutor(_exec_stub_db())
    ex._validate_agent_output_guardrails({"error": "noise", "content": "ok"})


@pytest.mark.asyncio
async def test_trigger_platform_write_requires_tool_name():
    ex = ae.AgentExecutor(_exec_stub_db())
    step = MagicMock()
    with pytest.raises(ValueError, match="tool_name"):
        await ex._trigger_platform_write(
            business_id=1,
            write_spec={"target": {}},
            artifact_ref={"artifact_id": "a"},
            step=step,
        )


@pytest.mark.asyncio
async def test_trigger_platform_write_calls_mcp_with_headers(monkeypatch):
    ex = ae.AgentExecutor(_exec_stub_db())
    ex._mcp_correlation_job_id = 7
    ex._mcp_correlation_step_id = 8
    ex._mcp_correlation_trace_id = "trace-z"
    step = MagicMock()
    step.job_id = 1
    step.id = 2
    step.step_order = 3
    mcp = AsyncMock(return_value={"written": True})
    monkeypatch.setattr(ae, "mcp_call_tool", mcp)
    monkeypatch.setattr(ae.settings, "PLATFORM_MCP_SERVER_URL", "http://platform-mcp:9999")
    monkeypatch.setattr(ae.settings, "MCP_TOOL_DEFAULT_TIMEOUT_SECONDS", 45.0)
    out = await ex._trigger_platform_write(
        business_id=42,
        write_spec={
            "tool_name": "platform_1_postgres",
            "operation_type": "UPSERT",
            "write_mode": "replace",
            "merge_keys": ["id"],
            "options": {"strict": True},
            "timeout_seconds": 12.5,
            "target": {"schema": "public", "table": "users"},
        },
        artifact_ref={"artifact_id": "art", "storage": "s3", "bucket": "b", "path": "p.jsonl", "format": "jsonl"},
        step=step,
    )
    assert out == {"written": True}
    mcp.assert_awaited_once()
    kwargs = mcp.await_args.kwargs
    assert kwargs["tool_name"] == "platform_1_postgres"
    assert kwargs["timeout"] == 12.5
    assert kwargs["extra_headers"]["X-Sandhi-Job-Id"] == "7"
    assert kwargs["extra_headers"]["X-Sandhi-Workflow-Step-Id"] == "8"
    assert kwargs["extra_headers"]["X-Sandhi-Trace-Id"] == "trace-z"


@pytest.mark.asyncio
async def test_trigger_platform_write_adds_trusted_bootstrap_when_signed(monkeypatch):
    ex = ae.AgentExecutor(_exec_stub_db())
    step = MagicMock()
    step.job_id = 1
    step.id = 2
    step.step_order = 1
    mcp = AsyncMock(return_value={})
    monkeypatch.setattr(ae, "mcp_call_tool", mcp)
    monkeypatch.setattr(ae.settings, "PLATFORM_MCP_SERVER_URL", "http://mcp/")
    monkeypatch.setattr(ae.settings, "MCP_TOOL_DEFAULT_TIMEOUT_SECONDS", 60.0)
    monkeypatch.setattr(ae, "_sign_trusted_bootstrap_payload", lambda **kwargs: "sighex")
    await ex._trigger_platform_write(
        business_id=1,
        write_spec={
            "tool_name": "platform_1_postgres",
            "target": {
                "schema": "public",
                "table": "t",
                "bootstrap_sql": "CREATE TABLE t (id int);",
            },
        },
        artifact_ref={"artifact_id": "a", "storage": "local", "format": "json"},
        step=step,
    )
    args = mcp.await_args.kwargs["arguments"]
    assert args["trusted_bootstrap"]["bootstrap_sql"] == "CREATE TABLE t (id int);"
    assert args["trusted_bootstrap"]["sig"] == "sighex"


@pytest.mark.asyncio
async def test_trigger_platform_write_omits_bootstrap_when_signature_none(monkeypatch):
    ex = ae.AgentExecutor(_exec_stub_db())
    step = MagicMock()
    step.job_id = 1
    step.id = 2
    step.step_order = 1
    mcp = AsyncMock(return_value={})
    monkeypatch.setattr(ae, "mcp_call_tool", mcp)
    monkeypatch.setattr(ae.settings, "PLATFORM_MCP_SERVER_URL", "http://mcp/")
    monkeypatch.setattr(ae.settings, "MCP_TOOL_DEFAULT_TIMEOUT_SECONDS", 60.0)
    monkeypatch.setattr(ae, "_sign_trusted_bootstrap_payload", lambda **kwargs: None)
    await ex._trigger_platform_write(
        business_id=1,
        write_spec={
            "tool_name": "platform_1_postgres",
            "target": {"schema": "public", "table": "t", "bootstrap_sql": "SELECT 1"},
        },
        artifact_ref={"artifact_id": "a", "storage": "local", "format": "json"},
        step=step,
    )
    args = mcp.await_args.kwargs["arguments"]
    assert "trusted_bootstrap" not in args


@pytest.mark.asyncio
async def test_persist_output_artifact_json_and_jsonl_variants(monkeypatch):
    ex = ae.AgentExecutor(_exec_stub_db())
    job = MagicMock()
    job.id = 100
    step = MagicMock()
    step.step_order = 2
    files: list = []

    async def capture_persist(filename, payload_bytes, mime, job_id=None):
        files.append(filename)
        return {"storage": "local", "path": "/tmp/x", "size": len(payload_bytes)}

    monkeypatch.setattr(ae, "persist_file", capture_persist)

    job.output_artifact_format = "json"
    r1 = await ex._persist_output_artifact(job, step, {"x": 1})
    assert r1["format"] == "json"
    assert str(files[-1]).endswith("_output.json")

    job.output_artifact_format = "jsonl"
    r2 = await ex._persist_output_artifact(job, step, {"records": [{"a": 1}]})
    assert r2["format"] == "jsonl"
    assert str(files[-1]).endswith("_output.jsonl")

    r3 = await ex._persist_output_artifact(job, step, {"records": None})
    assert r3["format"] == "jsonl"

    r4 = await ex._persist_output_artifact(job, step, "scalar-out")
    assert r4["format"] == "jsonl"

    job.output_artifact_format = "  PARQUET  "
    r5 = await ex._persist_output_artifact(job, step, {"records": []})
    assert r5["format"] == "jsonl"


def test_openai_tools_external_wraps_non_dict_input_schema(monkeypatch):
    monkeypatch.setattr(ae, "_input_schema_for_tool_type", lambda tt: {"type": "object", "properties": {}})
    tools = ae._openai_tools_from_mcp(
        [
            {
                "name": "ext_tool",
                "description": "BYO",
                "source": "external",
                "input_schema": "bad",
                "tool_type": "rest_api",
            },
        ]
    )
    assert len(tools) == 1
    assert tools[0]["function"]["parameters"] == {"type": "object", "properties": {}}


def test_input_schema_for_tool_type_delegates(monkeypatch):
    monkeypatch.setattr(ae, "input_schema_for_platform_tool_type", lambda tt: {"t": tt})
    assert ae._input_schema_for_tool_type("postgres") == {"t": "postgres"}
