/**
 * Detects when independent-style workflow steps may run concurrently and share
 * the same platform tools or MCP connections (contention risk at the tool / DB / object-store layer).
 */

import { jobHasExplicitMcpScope } from './jobMcpScope'

export type StepToolsLike = {
  allowed_platform_tool_ids?: number[] | null
  allowed_connection_ids?: number[] | null
  depends_on_previous?: boolean
}

/** Effective scoped IDs for a step; empty array means "all job tools" (unbounded). */
export function effectivePlatformToolIdsForStep(
  step: StepToolsLike,
  jobAllowedPlatformIds: number[] | null | undefined
): number[] | null {
  const raw = step.allowed_platform_tool_ids
  if (raw != null && raw.length > 0) {
    return [...new Set(raw)].sort((a, b) => a - b)
  }
  if (jobAllowedPlatformIds != null && jobAllowedPlatformIds.length > 0) {
    return [...new Set(jobAllowedPlatformIds)].sort((a, b) => a - b)
  }
  return null
}

export function effectiveConnectionIdsForStep(
  step: StepToolsLike,
  jobAllowedConnectionIds: number[] | null | undefined
): number[] | null {
  const raw = step.allowed_connection_ids
  if (raw != null && raw.length > 0) {
    return [...new Set(raw)].sort((a, b) => a - b)
  }
  if (jobAllowedConnectionIds != null && jobAllowedConnectionIds.length > 0) {
    return [...new Set(jobAllowedConnectionIds)].sort((a, b) => a - b)
  }
  return null
}

function intersectSorted(a: number[], b: number[]): number[] {
  const out: number[] = []
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push(a[i])
      i += 1
      j += 1
    } else if (a[i] < b[j]) i += 1
    else j += 1
  }
  return out
}

/** True if any two steps may overlap on platform tool IDs or connection IDs. */
export function hasOverlappingScopedTools(
  steps: StepToolsLike[],
  jobPlatformIds: number[] | null | undefined,
  jobConnectionIds: number[] | null | undefined
): boolean {
  if (steps.length < 2) return false

  const effP = steps.map((s) => effectivePlatformToolIdsForStep(s, jobPlatformIds))
  const effC = steps.map((s) => effectiveConnectionIdsForStep(s, jobConnectionIds))

  const unboundedPlatform = effP.some((x) => x === null)
  const unboundedConn = effC.some((x) => x === null)

  if (unboundedPlatform || unboundedConn) {
    return true
  }

  for (let i = 0; i < steps.length; i++) {
    for (let j = i + 1; j < steps.length; j++) {
      const pi = effP[i]!
      const pj = effP[j]!
      if (intersectSorted(pi, pj).length > 0) return true
      const ci = effC[i]!
      const cj = effC[j]!
      if (intersectSorted(ci, cj).length > 0) return true
    }
  }
  return false
}

/**
 * Steps might execute in parallel when a step after the first does not depend on the previous output.
 */
export function workflowMayHaveParallelSteps(steps: StepToolsLike[]): boolean {
  if (steps.length < 2) return false
  return steps.some((s, idx) => idx > 0 && s.depends_on_previous === false)
}

export type IndependentOverlapUi = {
  variant: 'strong' | 'scope'
  title: string
  lines: string[]
}

/**
 * Build production copy for the Auto-Split "Independently" path or job tools-per-step view.
 */
export function buildIndependentSharedToolWarning(params: {
  collaborationMode: 'independent' | 'sequential' | 'from_brd'
  selectedAgentCount: number
  stepToolSelections: Array<{ platformIds: number[]; connectionIds: number[] }>
  jobAllowedPlatformIds?: number[] | null
  jobAllowedConnectionIds?: number[] | null
}): IndependentOverlapUi | null {
  if (params.collaborationMode !== 'independent' || params.selectedAgentCount < 2) {
    return null
  }

  const stepsLike: StepToolsLike[] = params.stepToolSelections.map((sel) => ({
    allowed_platform_tool_ids: sel.platformIds.length ? sel.platformIds : null,
    allowed_connection_ids: sel.connectionIds.length ? sel.connectionIds : null,
    depends_on_previous: false,
  }))

  const overlap = hasOverlappingScopedTools(
    stepsLike,
    params.jobAllowedPlatformIds,
    params.jobAllowedConnectionIds
  )

  if (!overlap) {
    return null
  }

  const anyEmptySelection = params.stepToolSelections.some(
    (s) => s.platformIds.length === 0 && s.connectionIds.length === 0
  )

  if (!anyEmptySelection) {
    return {
      variant: 'strong',
      title: 'Shared tools with independent steps',
      lines: [
        'Two or more steps use the same platform tool or MCP connection. In Independent mode the platform may run those steps at the same time, so tool calls can overlap.',
        'Databases, DuckDB, and object storage may lock, return errors, or show inconsistent results if both agents write or mutate the same resource. Use Sequential mode, separate tools or connections per step, or read-only snapshots when you need isolation.',
      ],
    }
  }

  return {
    variant: 'scope',
    title: 'Independent steps and job-wide tool scope',
    lines: [
      'One or more steps inherit all job tools (no per-step restriction). With two or more agents in Independent mode, executions may overlap and call the same tools concurrently.',
        'If those tools target one shared database or bucket, plan for locking and conflicts at the tool layer—the orchestrator does not serialize MCP calls by default.',
    ],
  }
}

/** For saved jobs on Job detail: use workflow_steps + job allowed lists. */
export function buildJobDetailSharedToolWarning(job: {
  workflow_steps?: StepToolsLike[] | null
  allowed_platform_tool_ids?: number[] | null
  allowed_connection_ids?: number[] | null
}): IndependentOverlapUi | null {
  const steps = job.workflow_steps
  if (!steps || steps.length < 2 || !workflowMayHaveParallelSteps(steps)) {
    return null
  }
  const overlap = hasOverlappingScopedTools(
    steps,
    job.allowed_platform_tool_ids,
    job.allowed_connection_ids
  )
  if (!overlap) return null

  const anyUnscoped = steps.some(
    (s) =>
      (!(s.allowed_platform_tool_ids?.length ?? 0) && !(s.allowed_connection_ids?.length ?? 0))
  )

  if (!anyUnscoped) {
    return {
      variant: 'strong',
      title: 'Shared tools — concurrent execution risk',
      lines: [
        'This job has steps that can run in parallel and share at least one platform tool or connection.',
        'Expect overlapping MCP tool calls. Serialize work (sequential steps), split resources, or use read-only paths if writers contend.',
      ],
    }
  }

  const jobScoped = jobHasExplicitMcpScope(job)

  return {
    variant: 'scope',
    title: 'Parallel steps with broad tool scope',
    lines: [
      jobScoped
        ? 'Some steps use the job’s tool list without per-step picks. Parallel runs may invoke the same tool at the same time.'
        : 'Some steps have no tools listed per step and no job-level tool list. If tools are still available at runtime, parallel runs may overlap.',
      'Correlate logs with job, step, and trace IDs on each platform MCP request when debugging contention.',
    ],
  }
}
