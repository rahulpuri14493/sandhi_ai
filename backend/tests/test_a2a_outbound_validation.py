"""Tests for ``services.a2a_outbound_validation``: pre-flight A2A payload checks."""

from unittest.mock import patch

import pytest

from core.config import settings
from services.a2a_outbound_validation import validate_outbound_a2a_payload


def _minimal_task(agent_id: int = 5) -> dict:
    return {
        "schema_version": "sandhi.a2a_task.v1",
        "agent_id": agent_id,
        "task_id": "tid",
        "payload": {},
    }


class TestValidateOutboundA2APayload:
    def test_accepts_serializable_payload_without_envelope_when_strict_off(self):
        with patch.object(settings, "A2A_TASK_ENVELOPE_STRICT", False):
            validate_outbound_a2a_payload({"documents": [], "conversation": []})

    def test_skips_all_checks_when_disabled(self):
        with patch.object(settings, "A2A_OUTBOUND_VALIDATE", False):
            validate_outbound_a2a_payload({"x": object()})

    def test_rejects_non_json_serializable(self):
        circular: dict = {}
        circular["k"] = circular
        with pytest.raises(ValueError, match="not JSON-serializable"):
            validate_outbound_a2a_payload(circular)

    def test_rejects_oversized_payload(self):
        payload = {"sandhi_a2a_task": _minimal_task(), "sandhi_trace": {"agent_id": 5}, "pad": "x" * 500}
        with patch.object(settings, "A2A_OUTBOUND_MAX_BYTES", 50):
            with pytest.raises(ValueError, match="exceeds A2A_OUTBOUND_MAX_BYTES"):
                validate_outbound_a2a_payload(payload)

    def test_strict_requires_envelope_by_default(self):
        with pytest.raises(ValueError, match="sandhi_a2a_task is missing"):
            validate_outbound_a2a_payload({"sandhi_trace": {"agent_id": 1}})

    def test_parses_envelope_when_present(self):
        validate_outbound_a2a_payload(
            {
                "sandhi_a2a_task": _minimal_task(7),
                "sandhi_trace": {"agent_id": 7},
            }
        )

    def test_rejects_invalid_envelope(self):
        with pytest.raises(ValueError, match="Invalid sandhi_a2a_task"):
            validate_outbound_a2a_payload(
                {
                    "sandhi_a2a_task": {"schema_version": "sandhi.a2a_task.v1", "agent_id": 1},
                    "sandhi_trace": {"agent_id": 1},
                }
            )

    def test_rejects_agent_id_mismatch_with_trace(self):
        with pytest.raises(ValueError, match="does not match"):
            validate_outbound_a2a_payload(
                {
                    "sandhi_a2a_task": _minimal_task(1),
                    "sandhi_trace": {"agent_id": 2},
                }
            )

    def test_non_dict_trace_skips_agent_id_check(self):
        validate_outbound_a2a_payload(
            {
                "sandhi_a2a_task": _minimal_task(3),
                "sandhi_trace": "not-a-dict",
            }
        )
