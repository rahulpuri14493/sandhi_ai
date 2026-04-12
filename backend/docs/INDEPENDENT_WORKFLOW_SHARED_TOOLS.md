# Independent workflow steps and shared MCP tools

This document describes **production behavior** when a job runs multiple agents **independently** (steps do not pass the previous agent’s output) and those steps can **use the same platform MCP tool or connection**.

## What the orchestrator does

- Steps marked **independent** (`depends_on_previous = false` on subsequent steps) may be scheduled in **parallel waves** (see `WORKFLOW_PARALLEL_INDEPENDENT_STEPS` in backend settings).
- Each step runs in its own database session; agents receive **their own** tool allow lists.
- The platform **does not** enforce a global queue like “only one job may call tool X at a time.” Multiple steps may issue **concurrent** `tools/call` requests to the platform MCP server or BYO MCP servers.

## What the platform does **not** guarantee

- **No automatic serialization** of tool invocations that target the same logical resource (same database file, same S3 prefix, same DuckDB dataset, etc.).
- **Conflict resolution, locking, and correctness** for overlapping reads/writes are the responsibility of the **tool implementation** and **your data layout** (e.g. separate paths per step, read-only snapshots, transactional design).

## When risk is highest

- **Object storage / SQL / analytical engines** where two concurrent sessions mutate the same path, table, or file.
- **Independent mode + identical tool** selected on two steps (or multiple steps inheriting the full job tool scope).

Prefer **sequential** collaboration when agents must share one writable resource without external locking.

## Observability (production)

The backend forwards correlation metadata on MCP HTTP calls so logs can be tied to a single step execution:

| Header | Meaning |
|--------|---------|
| `X-Sandhi-Job-Id` | Job id |
| `X-Sandhi-Workflow-Step-Id` | `workflow_steps.id` for the executing step |
| `X-Sandhi-Trace-Id` | UUID for this step run (new per step execution) |

- **Backend (executor)** logs structured lines such as `platform_mcp_tools_call` / `byo_mcp_tools_call` including `job_id`, `workflow_step_id`, and `trace_id`.
- **Platform MCP server** appends the same values to `MCP tools/call` log lines when the headers are present.

Use your log stack to **filter by `trace_id` or `job_id`** when debugging interleaved failures under parallel steps.

## UI guardrails

The **Build Workflow → Auto-Split** screen shows a warning when **Independently** is selected and the effective tool scope overlaps across steps (explicit shared tools or broad “all job tools” scope). The job detail **Tools per step** section shows a similar notice when saved steps may run in parallel and share tools.

These warnings are **advisory**; they do not change execution semantics.
