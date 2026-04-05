"""Tests for standardized executor → agent (A2A) payload validation and trace enrichment."""

import pytest

from schemas.executor_platform_payload import (
    EXECUTOR_CONTEXT_SCHEMA_ID,
    enrich_executor_payload_trace_only,
    validate_and_enrich_executor_payload,
)


def test_validate_minimal_payload_ok():
    raw = {"job_title": "J", "assigned_task": "Do work", "documents": [], "conversation": []}
    out = validate_and_enrich_executor_payload(
        raw,
        job_id=10,
        workflow_step_id=20,
        step_order=1,
        agent_id=30,
        total_steps=2,
    )
    assert out["platform_a2a_schema"] == EXECUTOR_CONTEXT_SCHEMA_ID
    assert out["job_title"] == "J"
    trace = out["sandhi_trace"]
    assert trace["job_id"] == 10
    assert trace["workflow_step_id"] == 20
    assert trace["step_order"] == 1
    assert trace["agent_id"] == 30
    assert trace["total_steps"] == 2
    assert "validated_at" in trace


def test_validate_preserves_extra_keys():
    raw = {
        "documents": [],
        "conversation": [],
        "custom_brd_field": {"foo": 1},
    }
    out = validate_and_enrich_executor_payload(
        raw,
        job_id=1,
        workflow_step_id=2,
        step_order=1,
        agent_id=3,
        total_steps=1,
    )
    assert out["custom_brd_field"] == {"foo": 1}


def test_validate_no_mcp_tools_ok():
    """Tools are optional for a job/step."""
    out = validate_and_enrich_executor_payload(
        {"documents": [], "conversation": []},
        job_id=1,
        workflow_step_id=2,
        step_order=1,
        agent_id=3,
        total_steps=1,
    )
    assert "available_mcp_tools" not in out or out.get("available_mcp_tools") is None


def test_validate_rejects_documents_not_list():
    with pytest.raises(ValueError, match="Invalid executor payload"):
        validate_and_enrich_executor_payload(
            {"documents": "not-a-list", "conversation": []},
            job_id=1,
            workflow_step_id=2,
            step_order=1,
            agent_id=3,
            total_steps=1,
        )


def test_validate_rejects_output_contract_not_object():
    with pytest.raises(ValueError, match="Invalid executor payload"):
        validate_and_enrich_executor_payload(
            {"documents": [], "conversation": [], "output_contract": []},
            job_id=1,
            workflow_step_id=2,
            step_order=1,
            agent_id=3,
            total_steps=1,
        )


def test_enrich_trace_only_skips_validation():
    raw = {"documents": "bad-but-allowed", "conversation": []}
    out = enrich_executor_payload_trace_only(
        raw,
        job_id=1,
        workflow_step_id=2,
        step_order=1,
        agent_id=3,
        total_steps=1,
    )
    assert out["documents"] == "bad-but-allowed"
    assert out["sandhi_trace"]["job_id"] == 1
