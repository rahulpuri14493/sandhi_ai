"""Tests for ``schemas.sandhi_a2a_task``: A2A task envelope v1 models and helpers."""

import pytest

from schemas.sandhi_a2a_task import (
    SANDHI_A2A_TASK_SCHEMA_ID,
    SandhiA2ATaskV1,
    parse_sandhi_a2a_task,
    task_envelope_to_dict,
)


class TestParseSandhiA2ATask:
    def test_parse_accepts_missing_next_agent_and_wire_dict_adds_null(self):
        m = parse_sandhi_a2a_task(
            {
                "schema_version": "sandhi.a2a_task.v1",
                "agent_id": 9,
                "task_id": "tid",
                "payload": {"k": 1},
            }
        )
        assert m.next_agent is None
        d = task_envelope_to_dict(m)
        assert d["next_agent"] is None
        assert d["assigned_tools"] == []

    def test_parses_full_envelope_with_parallel_and_tools(self):
        data = {
            "schema_version": "sandhi.a2a_task.v1",
            "agent_id": 1,
            "task_id": "job-1-step-2-trace",
            "payload": {"job_id": 1},
            "next_agent": {"agent_id": 2, "workflow_step_id": 9, "name": "B"},
            "assigned_tools": [{"tool_name": "t1", "tool_type": "postgres"}],
            "parallel": {
                "wave_index": 0,
                "parallel_group_id": "job-wave-0",
                "concurrent_workflow_step_ids": [1, 2],
                "depends_on_previous_wave": False,
            },
            "task_type": "research",
            "assignment_source": "registry",
            "assignment_flagged": False,
        }
        model = parse_sandhi_a2a_task(data)
        assert isinstance(model, SandhiA2ATaskV1)
        assert model.assigned_tools[0].tool_name == "t1"
        assert model.next_agent is not None
        assert model.next_agent.agent_id == 2

    def test_payload_none_becomes_empty_dict(self):
        model = parse_sandhi_a2a_task(
            {
                "schema_version": "sandhi.a2a_task.v1",
                "agent_id": 1,
                "task_id": "x",
                "payload": None,
            }
        )
        assert model.payload == {}

    def test_rejects_non_object_payload(self):
        with pytest.raises(Exception):
            parse_sandhi_a2a_task(
                {
                    "schema_version": "sandhi.a2a_task.v1",
                    "agent_id": 1,
                    "task_id": "x",
                    "payload": [],
                }
            )

    def test_rejects_non_dict_root(self):
        with pytest.raises(ValueError, match="JSON object"):
            parse_sandhi_a2a_task([])

    def test_rejects_empty_task_id(self):
        with pytest.raises(Exception):
            parse_sandhi_a2a_task(
                {
                    "schema_version": "sandhi.a2a_task.v1",
                    "agent_id": 1,
                    "task_id": "",
                    "payload": {},
                }
            )


class TestTaskEnvelopeToDict:
    def test_serializes_next_agent_null_and_assigned_tools_for_terminal_step(self):
        model = SandhiA2ATaskV1(
            agent_id=3,
            task_id="t",
            payload={},
            next_agent=None,
        )
        d = task_envelope_to_dict(model)
        assert d["next_agent"] is None
        assert d["assigned_tools"] == []
        assert d["schema_version"] == SANDHI_A2A_TASK_SCHEMA_ID

    def test_preserves_extra_fields_on_envelope(self):
        model = SandhiA2ATaskV1.model_validate(
            {
                "schema_version": "sandhi.a2a_task.v1",
                "agent_id": 1,
                "task_id": "t",
                "payload": {},
                "custom_vendor_hint": "x",
            }
        )
        d = task_envelope_to_dict(model)
        assert d.get("custom_vendor_hint") == "x"
