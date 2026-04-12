/**
 * Integration: two agents + sequential collaboration → autoSplitWorkflow receives
 * per-step step_tools (tool_visibility) and workflow_mode for multi-agent tool paths.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { WorkflowBuilder } from '../../src/components/WorkflowBuilder'
import type { Job } from '../../src/lib/types'

const agentOne = {
  id: 1,
  name: 'Alpha',
  description: 'A',
  status: 'active' as const,
  price_per_task: 1,
  price_per_communication: 0,
  developer_id: 10,
  capabilities: [],
  pricing_model: 'pay_per_use' as const,
  created_at: '2024-01-01',
}

const agentTwo = {
  id: 2,
  name: 'Beta',
  description: 'B',
  status: 'active' as const,
  price_per_task: 2,
  price_per_communication: 0,
  developer_id: 10,
  capabilities: [],
  pricing_model: 'pay_per_use' as const,
  created_at: '2024-01-01',
}

const draftJob: Job = {
  id: 99,
  business_id: 1,
  title: 'Multi',
  status: 'draft',
  total_cost: 0,
  created_at: '2024-01-01T00:00:00Z',
  files: [],
  conversation: [],
  allowed_platform_tool_ids: [],
  allowed_connection_ids: [],
  tool_visibility: 'full',
}

vi.mock('../../src/lib/api', () => ({
  jobsAPI: {
    autoSplitWorkflow: vi.fn().mockResolvedValue({}),
  },
  agentsAPI: {
    list: vi.fn(),
  },
  mcpAPI: {
    listTools: vi.fn(),
    listConnections: vi.fn(),
  },
}))

import { jobsAPI, agentsAPI, mcpAPI } from '../../src/lib/api'

describe('WorkflowBuilder multi-agent auto-split payload (integration)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(agentsAPI.list).mockResolvedValue([agentOne, agentTwo])
    vi.mocked(mcpAPI.listTools).mockResolvedValue([
      { id: 1, name: 'T1', tool_type: 'postgres', server_connection_id: null },
    ])
    vi.mocked(mcpAPI.listConnections).mockResolvedValue([])
  })

  it('sends sequential mode, names_only visibility, and two step_tools entries', async () => {
    render(
      <WorkflowBuilder
        jobId={draftJob.id}
        job={draftJob}
        initialSelectedAgentIds={[1, 2]}
        onWorkflowCreated={() => {}}
      />
    )

    await waitFor(() => {
      expect(agentsAPI.list).toHaveBeenCalled()
    })

    await screen.findByRole('heading', { name: /^Build Workflow$/i })

    fireEvent.change(screen.getAllByRole('combobox')[0], {
      target: { value: 'sequential' },
    })
    fireEvent.change(screen.getAllByRole('combobox')[1], {
      target: { value: 'names_only' },
    })

    fireEvent.click(screen.getByRole('button', { name: /Create Auto-Split Workflow/i }))

    await waitFor(() => {
      expect(jobsAPI.autoSplitWorkflow).toHaveBeenCalled()
    })

    const call = vi.mocked(jobsAPI.autoSplitWorkflow).mock.calls[0]
    expect(call[0]).toBe(draftJob.id)
    expect(call[1]).toEqual([1, 2])
    expect(call[2]).toBe('sequential')
    const stepTools = call[3] as Array<{ agent_index: number; tool_visibility?: string }>
    expect(Array.isArray(stepTools)).toBe(true)
    expect(stepTools).toHaveLength(2)
    expect(stepTools[0].agent_index).toBe(0)
    expect(stepTools[1].agent_index).toBe(1)
    expect(stepTools.every((s) => s.tool_visibility === 'names_only')).toBe(true)
    expect(call[4]).toBe('names_only')
  }, 30_000)
})
