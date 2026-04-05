# Sandhi executor → agent (A2A) payload

The job executor builds a single JSON object per workflow step and sends it to agents (OpenAI-compatible HTTP, platform A2A adapter, or native A2A `SendMessage`). This document is the support reference for **Issue #65**-style standardization.

## Stable identifiers

| Field | Purpose |
|--------|--------|
| `platform_a2a_schema` | Constant `sandhi.executor_context.v1`. External agents can branch on this string. |
| `sandhi_trace` | Correlation for logs and tickets: `job_id`, `workflow_step_id`, `step_order`, `agent_id`, `total_steps`, `validated_at` (UTC ISO). |

## Validation

When `EXECUTOR_PAYLOAD_VALIDATE=true` (default), Pydantic checks **known** fields for sensible types. **Extra keys are allowed** so workflows can add fields without a backend deploy.

### Type rules (fail fast before any HTTP call)

- `documents`, `conversation`, `available_mcp_tools`, `peer_agents`, `write_targets` — if present, must be **JSON arrays** (or `null`).
- `output_contract` — if present, must be a **JSON object** (or `null`).
- **MCP tools are optional**: missing or empty `available_mcp_tools` is valid.

## Common fields (non-exhaustive)

Populated by the workflow builder and executor: `job_title`, `job_description`, `assigned_task`, `documents`, `conversation`, `step_order`, `total_steps`, `previous_step_output` (sequential steps), `available_mcp_tools`, `business_id`, `peer_agents` (async A2A hint), `output_contract`, `write_execution_mode`, `write_targets`, document scope fields.

## Debugging failed jobs

1. Read `job.failure_reason` (now includes exception type + message, truncated).
2. Inspect failed `workflow_steps.output_data` JSON for the step error.
3. Search logs for `executor_payload_validation_failed` or `WorkflowStep input_data is not valid JSON`.
4. In A2A agent logs, parse the message body JSON and read `sandhi_trace` to map back to platform IDs.

## Escape hatch

Set `EXECUTOR_PAYLOAD_VALIDATE=false` only if you need to bypass type checks temporarily; trace enrichment still adds `platform_a2a_schema` and `sandhi_trace`.
