import { describe, expect, it } from 'vitest'
import {
  buildIndependentSharedToolWarning,
  buildJobDetailSharedToolWarning,
  effectiveConnectionIdsForStep,
  effectivePlatformToolIdsForStep,
  hasOverlappingScopedTools,
  workflowMayHaveParallelSteps,
} from '../src/lib/independentWorkflowSharedTools'

describe('independentWorkflowSharedTools', () => {
  it('effectivePlatformToolIdsForStep prefers step list then job list', () => {
    expect(
      effectivePlatformToolIdsForStep(
        { allowed_platform_tool_ids: [3, 1, 3], allowed_connection_ids: null },
        [9, 9]
      )
    ).toEqual([1, 3])
    expect(
      effectivePlatformToolIdsForStep({ allowed_platform_tool_ids: [], allowed_connection_ids: null }, [2, 1])
    ).toEqual([1, 2])
    expect(
      effectivePlatformToolIdsForStep({ allowed_platform_tool_ids: null, allowed_connection_ids: null }, [2])
    ).toEqual([2])
    expect(
      effectivePlatformToolIdsForStep({ allowed_platform_tool_ids: null, allowed_connection_ids: null }, null)
    ).toBeNull()
  })

  it('effectiveConnectionIdsForStep prefers step list then job list', () => {
    expect(
      effectiveConnectionIdsForStep(
        { allowed_connection_ids: [2, 2], allowed_platform_tool_ids: null },
        [5]
      )
    ).toEqual([2])
    expect(
      effectiveConnectionIdsForStep({ allowed_connection_ids: null, allowed_platform_tool_ids: null }, [4, 3])
    ).toEqual([3, 4])
  })

  it('hasOverlappingScopedTools detects shared platform id', () => {
    const steps = [
      { allowed_platform_tool_ids: [1, 2], allowed_connection_ids: null, depends_on_previous: true },
      { allowed_platform_tool_ids: [2, 3], allowed_connection_ids: null, depends_on_previous: false },
    ]
    expect(hasOverlappingScopedTools(steps, null, null)).toBe(true)
  })

  it('hasOverlappingScopedTools detects connection overlap and unbounded scopes', () => {
    expect(
      hasOverlappingScopedTools(
        [
          { allowed_platform_tool_ids: [1], allowed_connection_ids: [10], depends_on_previous: false },
          { allowed_platform_tool_ids: [2], allowed_connection_ids: [10, 11], depends_on_previous: false },
        ],
        null,
        null
      )
    ).toBe(true)

    expect(
      hasOverlappingScopedTools(
        [
          { allowed_platform_tool_ids: [1], allowed_connection_ids: null, depends_on_previous: false },
          { allowed_platform_tool_ids: [2], allowed_connection_ids: null, depends_on_previous: false },
        ],
        null,
        null
      )
    ).toBe(true)

    expect(
      hasOverlappingScopedTools(
        [
          { allowed_platform_tool_ids: [1], allowed_connection_ids: [], depends_on_previous: false },
          { allowed_platform_tool_ids: [2], allowed_connection_ids: [], depends_on_previous: false },
        ],
        [99],
        null
      )
    ).toBe(true)
  })

  it('hasOverlappingScopedTools returns false for disjoint bounded scopes', () => {
    expect(
      hasOverlappingScopedTools(
        [
          { allowed_platform_tool_ids: [1], allowed_connection_ids: [10], depends_on_previous: false },
          { allowed_platform_tool_ids: [2], allowed_connection_ids: [11], depends_on_previous: false },
        ],
        null,
        null
      )
    ).toBe(false)
    expect(hasOverlappingScopedTools([], null, null)).toBe(false)
    expect(hasOverlappingScopedTools([{ allowed_platform_tool_ids: [1], allowed_connection_ids: null }], null, null)).toBe(
      false
    )
  })

  it('workflowMayHaveParallelSteps', () => {
    expect(workflowMayHaveParallelSteps([{ depends_on_previous: true }])).toBe(false)
    expect(workflowMayHaveParallelSteps([])).toBe(false)
    expect(
      workflowMayHaveParallelSteps([
        { depends_on_previous: true },
        { depends_on_previous: false },
      ])
    ).toBe(true)
  })

  it('buildIndependentSharedToolWarning returns null when not independent or single agent', () => {
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
    expect(
      buildIndependentSharedToolWarning({
        collaborationMode: 'independent',
        selectedAgentCount: 1,
        stepToolSelections: [{ platformIds: [1], connectionIds: [] }],
      })
    ).toBeNull()
    expect(
      buildIndependentSharedToolWarning({
        collaborationMode: 'independent',
        selectedAgentCount: 2,
        stepToolSelections: [
          { platformIds: [1], connectionIds: [10] },
          { platformIds: [2], connectionIds: [11] },
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

  it('buildIndependentSharedToolWarning scope variant when overlap from empty step picks', () => {
    const w = buildIndependentSharedToolWarning({
      collaborationMode: 'independent',
      selectedAgentCount: 2,
      stepToolSelections: [
        { platformIds: [], connectionIds: [] },
        { platformIds: [], connectionIds: [] },
      ],
      jobAllowedPlatformIds: [7],
      jobAllowedConnectionIds: null,
    })
    expect(w?.variant).toBe('scope')
    expect(w?.title).toContain('job-wide')
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

  it('buildJobDetailSharedToolWarning strong when parallel steps share tools with per-step picks', () => {
    const w = buildJobDetailSharedToolWarning({
      workflow_steps: [
        { depends_on_previous: true, allowed_platform_tool_ids: [1], allowed_connection_ids: null },
        { depends_on_previous: false, allowed_platform_tool_ids: [1], allowed_connection_ids: null },
      ],
      allowed_platform_tool_ids: null,
      allowed_connection_ids: null,
    })
    expect(w?.variant).toBe('strong')
    expect(w?.title).toContain('concurrent')
  })

  it('buildJobDetailSharedToolWarning scope when unscoped steps and job has explicit MCP scope', () => {
    const w = buildJobDetailSharedToolWarning({
      workflow_steps: [
        { depends_on_previous: true, allowed_platform_tool_ids: null, allowed_connection_ids: null },
        { depends_on_previous: false, allowed_platform_tool_ids: null, allowed_connection_ids: null },
      ],
      allowed_platform_tool_ids: [9],
      allowed_connection_ids: null,
    })
    expect(w?.variant).toBe('scope')
    expect(w?.lines[0]).toContain('job’s tool list')
  })

  it('buildJobDetailSharedToolWarning scope when no job-level tool list', () => {
    const w = buildJobDetailSharedToolWarning({
      workflow_steps: [
        { depends_on_previous: true, allowed_platform_tool_ids: null, allowed_connection_ids: null },
        { depends_on_previous: false, allowed_platform_tool_ids: null, allowed_connection_ids: null },
      ],
      allowed_platform_tool_ids: null,
      allowed_connection_ids: null,
    })
    expect(w?.variant).toBe('scope')
    expect(w?.lines[0]).toContain('no job-level tool list')
  })
})
