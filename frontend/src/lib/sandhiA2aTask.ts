/**
 * Shared types for `sandhi_a2a_task` (sandhi.a2a_task.v1) on the executor payload.
 * Keep in sync with `backend/schemas/sandhi_a2a_task.py` and the JSON Schema under docs/schemas/a2a/.
 */

export const SANDHI_A2A_TASK_SCHEMA_ID = 'sandhi.a2a_task.v1' as const

export interface AssignedToolMeta {
  tool_name: string
  platform_tool_id?: number | null
  external_tool_name?: string | null
  tool_type?: string | null
  connection_id?: number | null
  input_schema?: Record<string, unknown> | null
  execution_hints?: Record<string, unknown> | null
  [key: string]: unknown
}

export interface NextAgentRef {
  agent_id: number
  workflow_step_id?: number | null
  name?: string | null
  a2a_endpoint?: string | null
  step_order?: number | null
  [key: string]: unknown
}

export interface ParallelExecutionContext {
  wave_index: number
  parallel_group_id: string
  concurrent_workflow_step_ids?: number[]
  depends_on_previous_wave?: boolean
  [key: string]: unknown
}

export interface SandhiA2ATaskV1 {
  schema_version: typeof SANDHI_A2A_TASK_SCHEMA_ID
  agent_id: number
  task_id: string
  payload: Record<string, unknown>
  next_agent: NextAgentRef | null
  assigned_tools: AssignedToolMeta[]
  parallel?: ParallelExecutionContext | null
  task_type?: string | null
  assignment_source?: string | null
  assignment_flagged?: boolean
  [key: string]: unknown
}
