# A2A task envelope & tool assignment

This document ties together the **versioned A2A task schema**, **executor payload**, **tool registry**, **assignment**, **compatibility**, **validation**, and **tests**. It maps to the implementation priorities below.

## Implementation priorities (reference)

| Priority | Area | Status in this repo |
|---------|------|---------------------|
| 1 | **A2A schema** | **`sandhi.a2a_task.v1`** — Pydantic in `backend/schemas/sandhi_a2a_task.py`; JSON Schema in [`docs/schemas/a2a/sandhi_a2a_task.v1.schema.json`](./schemas/a2a/sandhi_a2a_task.v1.schema.json). |
| 2 | **Task payload** | **`payload`** object (minimal job/step hints) plus root executor fields; **`assigned_tools`** array mirrors structured assignment (see below). |
| 3 | **Mandatory fields** | Schema **requires**: `schema_version`, `agent_id`, `task_id`, `payload`, **`next_agent`** (JSON `null` if terminal step), **`assigned_tools`** (array, may be empty). Optional: `parallel`, `task_type`, `assignment_source`, `assignment_flagged`. |
| 4 | **Validation** | **Before execution**: `validate_and_enrich_executor_payload` (`EXECUTOR_PAYLOAD_VALIDATE`). **Before A2A HTTP**: `validate_outbound_a2a_payload` (`A2A_OUTBOUND_VALIDATE`, size limit, envelope parse, trace `agent_id` match). **`A2A_TASK_ENVELOPE_STRICT`** (default **true**): envelope must be present; set **false** only for legacy integrations. |
| 5 | **Registry** | Default JSON: `backend/resources/config/tool_assignment_registry.default.json`. Override: **`TOOL_ASSIGNMENT_REGISTRY_PATH`**. |
| 6 | **Assignment engine** | `services/tool_assignment_engine.py` — task-type rules + fallback; **`TOOL_ASSIGNMENT_USE_LLM`** (default **true**) merges `llm_suggested_tool_names` when present. **`TOOL_ASSIGNMENT_LLM_PICK_TOOLS`** (default **true**): with planner API key, executor may ask planner to pick names from the allowlist (`services/tool_assignment_llm.py`); set **false** to skip. |
| 7 | **Compatibility** | `services/agent_tool_compatibility.py` — `Agent.capabilities`: `mcp:allow_types` / `mcp:deny_types`, plus **`mcp:allow_connection_ids`** / **`mcp:deny_connection_ids`**. |
| — | **Auto-split `task_type`** | Optional per-step **`task_type`** on `StepToolsAssignment` → persisted in step **`input_data.task_type`** (workflow UI + API). |
| — | **Registry reload (ops)** | **`POST /api/external/platform/tool-assignment-registry/reload`** with **`X-API-Key`** = **`EXTERNAL_API_KEY`** (same as external job create). |
| — | **Frontend types** | `frontend/src/lib/sandhiA2aTask.ts` mirrors envelope fields for TS consumers. |
| 8 | **Parallelism** | **`parallel`** on the envelope when the step sits in a parallel wave (`wave_index`, `parallel_group_id`, `concurrent_workflow_step_ids`, `depends_on_previous_wave`). Omitted when not applicable (not a required key). |
| 9 | **E2E + docs** | Backend: `tests/test_agent_executor_sandhi_task_envelope_integration.py`, `tests/test_*a2a*`, `tests/test_tool_assignment_*`. Frontend: `frontend/tests/integration/*.integration.test.tsx`. Executor payload reference: `backend/docs/EXECUTOR_A2A_PAYLOAD.md`. |

## Wire shape (executor → agent)

The HTTP body is one JSON object. Stable markers:

- `platform_a2a_schema`: `sandhi.executor_context.v1`
- `sandhi_trace`: job / step / agent correlation
- `sandhi_a2a_task`: envelope as above (always includes `next_agent` and `assigned_tools` when the executor builds it)
- `assigned_tools`: duplicate flat list at root for legacy readers (same content as `sandhi_a2a_task.assigned_tools`)

External agents should prefer **`sandhi_a2a_task`** for routing and tool expectations; the flat executor fields remain for human context and older integrations.

## Environment variables (summary)

| Variable | Purpose |
|----------|---------|
| `TOOL_ASSIGNMENT_REGISTRY_PATH` | Custom registry JSON path |
| `TOOL_ASSIGNMENT_ENABLED` | Disable engine → passthrough ordering |
| `TOOL_ASSIGNMENT_USE_LLM` | Prefer `llm_suggested_tool_names` order (default **true**; **false** to disable merge) |
| `TOOL_ASSIGNMENT_LLM_PICK_TOOLS` | With `USE_LLM` + planner: pick tool names at execution (default **true**; **false** to disable) |
| `TOOL_ASSIGNMENT_LLM_MAX_TOOLS` | Cap for planner-picked names (also capped by visible tools) |
| `EXECUTOR_PAYLOAD_VALIDATE` | Pydantic executor payload checks |
| `A2A_OUTBOUND_VALIDATE` | Pre-flight JSON + envelope checks |
| `A2A_OUTBOUND_MAX_BYTES` | Max serialized payload size |
| `A2A_TASK_ENVELOPE_STRICT` | Require `sandhi_a2a_task` on every outbound call (default **true**; **false** for legacy) |

## Related links

- [A2A for developers](A2A_DEVELOPERS.md) — transport and registration
- [Executor → agent payload](../backend/docs/EXECUTOR_A2A_PAYLOAD.md) — field types and escape hatches
- [Codebase layout](CODEBASE_LAYOUT.md) — where code lives
