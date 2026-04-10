import { describe, expect, it } from 'vitest'
import {
  enrichPlannerArtifactJsonForDisplay,
  formatPlannerAgentLabel,
  orderedWorkflowSteps,
} from '../src/lib/plannerArtifactDisplay'
import type { WorkflowStep } from '../src/lib/types'

describe('plannerArtifactDisplay', () => {
  const steps: WorkflowStep[] = [
    {
      id: 1,
      job_id: 1,
      agent_id: 10,
      agent_name: 'Alpha',
      step_order: 1,
      status: 'completed',
      cost: 0,
    },
    {
      id: 2,
      job_id: 1,
      agent_id: 20,
      agent_name: 'Beta',
      step_order: 2,
      status: 'pending',
      cost: 0,
    },
  ]

  const toolMap = new Map<string, string>([
    ['7', 'Slack Post'],
    ['99', 'Unknown Tool'],
  ])

  it('orders workflow steps by step_order', () => {
    const shuffled: WorkflowStep[] = [steps[1], steps[0]]
    expect(orderedWorkflowSteps(shuffled).map((s) => s.agent_name)).toEqual(['Alpha', 'Beta'])
  })

  it('formatPlannerAgentLabel prefers name then resolves index', () => {
    const ordered = orderedWorkflowSteps(steps)
    expect(formatPlannerAgentLabel('Custom', 5, ordered)).toBe('Custom')
    expect(formatPlannerAgentLabel('', 0, ordered)).toBe('Alpha')
    expect(formatPlannerAgentLabel(null, 1, ordered)).toBe('Beta')
  })

  it('enriches tool_suggestion JSON with names and drops raw ids', () => {
    const raw = {
      step_suggestions: [{ agent_index: 0, platform_tool_ids: [7, 42] }],
      parsed_result: { step_suggestions: [{ agent_index: 1, platform_tool_ids: [7] }] },
    }
    const out = enrichPlannerArtifactJsonForDisplay('tool_suggestion', raw, {
      workflowSteps: steps,
      platformToolIdToName: toolMap,
    }) as Record<string, unknown>
    const row0 = (out.step_suggestions as Record<string, unknown>[])[0]
    expect(row0.agent_name).toBe('Alpha')
    expect(row0.agent_index).toBeUndefined()
    expect(row0.platform_tool_names).toEqual(['Slack Post', 'Tool #42'])
    expect(row0.platform_tool_ids).toBeUndefined()
  })

  it('enriches task_split parsed_assignments', () => {
    const raw = {
      parsed_assignments: [{ agent_index: 1, allowed_platform_tool_ids: [7] }],
    }
    const out = enrichPlannerArtifactJsonForDisplay('task_split', raw, {
      workflowSteps: steps,
      platformToolIdToName: toolMap,
    }) as Record<string, unknown>
    const row0 = (out.parsed_assignments as Record<string, unknown>[])[0]
    expect(row0.agent_name).toBe('Beta')
    expect(row0.agent_index).toBeUndefined()
    expect(row0.allowed_platform_tool_names).toEqual(['Slack Post'])
    expect(row0.allowed_platform_tool_ids).toBeUndefined()
  })
})
