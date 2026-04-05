import { describe, expect, it } from 'vitest'
import {
  buildIndependentSharedToolWarning,
  buildJobDetailSharedToolWarning,
  hasOverlappingScopedTools,
  workflowMayHaveParallelSteps,
} from '../src/lib/independentWorkflowSharedTools'

describe('independentWorkflowSharedTools', () => {
  it('hasOverlappingScopedTools detects shared platform id', () => {
    const steps = [
      { allowed_platform_tool_ids: [1, 2], allowed_connection_ids: null, depends_on_previous: true },
      { allowed_platform_tool_ids: [2, 3], allowed_connection_ids: null, depends_on_previous: false },
    ]
    expect(hasOverlappingScopedTools(steps, null, null)).toBe(true)
  })

  it('workflowMayHaveParallelSteps', () => {
    expect(workflowMayHaveParallelSteps([{ depends_on_previous: true }])).toBe(false)
    expect(
      workflowMayHaveParallelSteps([
        { depends_on_previous: true },
        { depends_on_previous: false },
      ])
    ).toBe(true)
  })

  it('buildIndependentSharedToolWarning returns null when not independent', () => {
    expect(
      buildIndependentSharedToolWarning({
        collaborationMode: 'sequential',
        selectedAgentCount: 2,
        stepToolSelections: [
          { platformIds: [1], connectionIds: [] },
          { platformIds: [1], connectionIds: [] },
        ],
      })
    ).toBeNull()
  })

  it('buildIndependentSharedToolWarning strong when explicit overlap', () => {
    const w = buildIndependentSharedToolWarning({
      collaborationMode: 'independent',
      selectedAgentCount: 2,
      stepToolSelections: [
        { platformIds: [5], connectionIds: [] },
        { platformIds: [5], connectionIds: [] },
      ],
    })
    expect(w?.variant).toBe('strong')
    expect(w?.title).toContain('Shared tools')
  })

  it('buildJobDetailSharedToolWarning null when all sequential', () => {
    const w = buildJobDetailSharedToolWarning({
      workflow_steps: [
        { depends_on_previous: true, allowed_platform_tool_ids: [1] },
        { depends_on_previous: true, allowed_platform_tool_ids: [1] },
      ],
      allowed_platform_tool_ids: null,
      allowed_connection_ids: null,
    })
    expect(w).toBeNull()
  })
})
