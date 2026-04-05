import { describe, it, expect } from 'vitest'
import { SANDHI_A2A_TASK_SCHEMA_ID, type SandhiA2ATaskV1 } from '../src/lib/sandhiA2aTask'

describe('sandhiA2aTask', () => {
  it('exports stable schema id for sandhi.a2a_task.v1', () => {
    expect(SANDHI_A2A_TASK_SCHEMA_ID).toBe('sandhi.a2a_task.v1')
  })

  it('accepts minimal envelope object matching TS shape', () => {
    const task: SandhiA2ATaskV1 = {
      schema_version: SANDHI_A2A_TASK_SCHEMA_ID,
      agent_id: 42,
      task_id: 'job-1-step-2',
      payload: { job_title: 'J' },
      next_agent: null,
      assigned_tools: [{ tool_name: 'query' }],
    }
    expect(task.schema_version).toBe('sandhi.a2a_task.v1')
    expect(task.assigned_tools).toHaveLength(1)
  })
})
