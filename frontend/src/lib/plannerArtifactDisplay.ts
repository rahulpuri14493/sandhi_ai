import type { WorkflowStep } from './types'

export function orderedWorkflowSteps(steps: WorkflowStep[] | undefined): WorkflowStep[] {
  if (!steps?.length) return []
  return [...steps].sort((a, b) => a.step_order - b.step_order)
}

export function buildPlatformToolIdToNameMap(
  tools: Array<{ id: number; name: string }>,
): Map<string, string> {
  return new Map(tools.map((t) => [String(t.id), t.name]))
}

/** Map planner `agent_index` to the job's workflow step order (0-based index). */
export function resolveAgentLabelFromIndex(agentIndex: unknown, steps: WorkflowStep[]): string | null {
  if (!steps.length) return null
  const idx =
    typeof agentIndex === 'number'
      ? agentIndex
      : typeof agentIndex === 'string' && agentIndex !== ''
        ? parseInt(agentIndex, 10)
        : NaN
  if (!Number.isFinite(idx) || idx < 0) return null
  const st = steps[idx]
  if (!st) return null
  const n = st.agent_name?.trim()
  if (n) return n
  if (st.agent_id != null) return `Agent #${st.agent_id}`
  return `Workflow step ${idx + 1}`
}

export function formatPlannerAgentLabel(
  agentName: unknown,
  agentIndex: unknown,
  steps: WorkflowStep[],
): string {
  const name = typeof agentName === 'string' ? agentName.trim() : ''
  if (name) return name
  return resolveAgentLabelFromIndex(agentIndex, steps) || 'Agent'
}

function toolIdsToDisplayNames(ids: unknown, platformToolIdToName: Map<string, string>): string[] {
  if (!Array.isArray(ids)) return []
  return ids
    .map((id) => {
      const key = String(id)
      if (key === '' || key === 'undefined' || key === 'null') return ''
      return platformToolIdToName.get(key) || `Tool #${key}`
    })
    .filter(Boolean)
}

function enrichToolSuggestionRows(
  rows: unknown,
  steps: WorkflowStep[],
  platformToolIdToName: Map<string, string>,
): void {
  if (!Array.isArray(rows)) return
  for (const item of rows) {
    if (!item || typeof item !== 'object') continue
    const row = item as Record<string, unknown>
    const explicitName = typeof row.agent_name === 'string' ? row.agent_name.trim() : ''
    const fromIdx = resolveAgentLabelFromIndex(row.agent_index, steps)
    if (explicitName || fromIdx) {
      row.agent_name = explicitName || fromIdx || row.agent_name
      delete row.agent_index
    }
    if (Array.isArray(row.platform_tool_names) && row.platform_tool_names.length > 0) {
      delete row.platform_tool_ids
      continue
    }
    if (Array.isArray(row.platform_tool_ids)) {
      row.platform_tool_names = toolIdsToDisplayNames(row.platform_tool_ids, platformToolIdToName)
      delete row.platform_tool_ids
    }
  }
}

function enrichTaskSplitAssignments(
  assignments: unknown,
  steps: WorkflowStep[],
  platformToolIdToName: Map<string, string>,
): void {
  if (!Array.isArray(assignments)) return
  for (const item of assignments) {
    if (!item || typeof item !== 'object') continue
    const row = item as Record<string, unknown>
    const explicitName = typeof row.agent_name === 'string' ? row.agent_name.trim() : ''
    const fromIdx = resolveAgentLabelFromIndex(row.agent_index, steps)
    if (explicitName || fromIdx) {
      row.agent_name = explicitName || fromIdx || row.agent_name
      delete row.agent_index
    }
    if (Array.isArray(row.allowed_platform_tool_names) && row.allowed_platform_tool_names.length > 0) {
      delete row.allowed_platform_tool_ids
      continue
    }
    if (Array.isArray(row.allowed_platform_tool_ids)) {
      row.allowed_platform_tool_names = toolIdsToDisplayNames(row.allowed_platform_tool_ids, platformToolIdToName)
      delete row.allowed_platform_tool_ids
    }
  }
}

/**
 * Clone planner artifact JSON for UI display: replace agent_index / tool id arrays with
 * human-readable names using the current job workflow and platform tool registry.
 */
export function enrichPlannerArtifactJsonForDisplay(
  artifactType: string,
  payload: unknown,
  ctx: { workflowSteps: WorkflowStep[]; platformToolIdToName: Map<string, string> },
): unknown {
  if (!payload || typeof payload !== 'object') return payload
  const steps = orderedWorkflowSteps(ctx.workflowSteps)
  const out = JSON.parse(JSON.stringify(payload)) as Record<string, unknown>

  if (artifactType === 'tool_suggestion') {
    enrichToolSuggestionRows(out.step_suggestions, steps, ctx.platformToolIdToName)
    const pr = out.parsed_result
    if (pr && typeof pr === 'object') {
      enrichToolSuggestionRows((pr as Record<string, unknown>).step_suggestions, steps, ctx.platformToolIdToName)
    }
    return out
  }

  if (artifactType === 'task_split') {
    enrichTaskSplitAssignments(out.parsed_assignments, steps, ctx.platformToolIdToName)
    return out
  }

  return out
}
