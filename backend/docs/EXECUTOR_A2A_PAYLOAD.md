# Sandhi executor → agent (A2A) payload

The job executor builds a single JSON object per workflow step and sends it to agents (OpenAI-compatible HTTP, platform A2A adapter, or native A2A `SendMessage`). This document is the support reference for executor payload and A2A task envelope standardization.

## Stable identifiers

| Field | Purpose |
|--------|--------|
| `platform_a2a_schema` | Constant `sandhi.executor_context.v1`. External agents can branch on this string. |
| `sandhi_trace` | Correlation for logs and tickets: `job_id`, `workflow_step_id`, `step_order`, `agent_id`, `total_steps`, `validated_at` (UTC ISO). |
| `sandhi_a2a_task` | Envelope `sandhi.a2a_task.v1`. **Required keys on the wire** (see JSON Schema): `schema_version`, `agent_id`, `task_id`, `payload`, **`next_agent`** (`null` = terminal step), **`assigned_tools`** (array, may be empty). Optional: `parallel`, `task_type`, `assignment_source`, `assignment_flagged`. Schema file: `docs/schemas/a2a/sandhi_a2a_task.v1.schema.json`. |
| `assigned_tools` | Array of objects (tool_name, platform_tool_id, tool_type, …) mirroring the registry/assignment output for this step; duplicated at root and inside `sandhi_a2a_task.assigned_tools`. |

## Tool assignment & registry

- **Registry**: JSON file, default `backend/resources/config/tool_assignment_registry.default.json`. Override with `TOOL_ASSIGNMENT_REGISTRY_PATH` (absolute path). Maps `task_type` (from step `input_data.task_type` or inferred from `assigned_task`) to preferred MCP `tool_type` values and `max_tools`.
- **Compatibility**: Optional `Agent.capabilities` entries `mcp:allow_types:postgres,mysql` or `mcp:deny_types:s3` filter tools before assignment.
- **Assignment**: Rule-based ordering and truncation; `TOOL_ASSIGNMENT_USE_LLM=true` (default) prefers tools listed in `input_data.llm_suggested_tool_names` when present. `TOOL_ASSIGNMENT_LLM_PICK_TOOLS=true` (default) allows the platform planner to populate that list at execution when `AGENT_PLANNER_API_KEY` is set.

## Validation

When `EXECUTOR_PAYLOAD_VALIDATE=true` (default), Pydantic checks **known** fields for sensible types. **Extra keys are allowed** so workflows can add fields without a backend deploy.

Before any agent HTTP/A2A call, `A2A_OUTBOUND_VALIDATE=true` (default) ensures the payload is JSON-serializable, under `A2A_OUTBOUND_MAX_BYTES`, and that `sandhi_a2a_task` parses when present. `A2A_TASK_ENVELOPE_STRICT=true` (default) requires the envelope on every outbound call; set `A2A_TASK_ENVELOPE_STRICT=false` only for legacy agents that do not receive `sandhi_a2a_task`.

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
