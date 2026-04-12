/**
 * E2E-style integration: Build workflow with tool visibility "None", create workflow,
 * open Edit tools — modal must show "None" (not defaulting to Full).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import JobDetailPage from '../../src/pages/JobDetail'
import type { Job, WorkflowStep } from '../../src/lib/types'

const mockAgent = {
  id: 1,
  name: 'Agent One',
  description: 'Desc',
  status: 'active',
  price_per_task: 5,
  price_per_communication: 0.5,
  developer_id: 1,
  capabilities: [],
  pricing_model: 'pay_per_use' as const,
  created_at: '2024-01-01',
}

const baseJobFields = {
  business_id: 1,
  title: 'Workflow visibility E2E',
  description: 'Test job',
  total_cost: 0,
  created_at: '2024-01-01T00:00:00Z',
  files: [] as Job['files'],
  conversation: [] as Job['conversation'],
  allowed_platform_tool_ids: [] as number[],
  allowed_connection_ids: [] as number[],
}

const stepAfterWorkflow: WorkflowStep = {
  id: 101,
  job_id: 1,
  agent_id: 1,
  agent_name: 'Agent One',
  step_order: 1,
  status: 'pending',
  cost: 0,
  depends_on_previous: false,
  tool_visibility: 'none',
  allowed_platform_tool_ids: [],
  allowed_connection_ids: [],
}

const jobBeforeWorkflow: Job = {
  ...baseJobFields,
  id: 1,
  status: 'draft',
  tool_visibility: 'full',
  workflow_steps: [],
}

const jobAfterWorkflow: Job = {
  ...baseJobFields,
  id: 1,
  status: 'draft',
  tool_visibility: 'none',
  workflow_steps: [stepAfterWorkflow],
}

vi.mock('../../src/lib/api', () => ({
  jobsAPI: {
    get: vi.fn(),
    getSchedule: vi.fn().mockRejectedValue(new Error('404')),
    previewWorkflow: vi.fn().mockResolvedValue({
      steps: [],
      total_cost: 0,
      breakdown: {
        task_costs: 0,
        communication_costs: 0,
        commission: 0,
      },
    }),
    autoSplitWorkflow: vi.fn().mockResolvedValue({}),
    updateStepTools: vi.fn(),
    analyzeDocuments: vi.fn(),
    approve: vi.fn(),
    execute: vi.fn(),
    createSchedule: vi.fn(),
    updateSchedule: vi.fn(),
    rerun: vi.fn(),
    cancel: vi.fn(),
    getShareLink: vi.fn(),
    getAgentPlannerStatus: vi.fn(),
    listPlannerArtifacts: vi.fn(),
    getPlannerPipeline: vi.fn(),
    getPlannerArtifactRaw: vi.fn(),
  },
  agentsAPI: { list: vi.fn() },
  mcpAPI: {
    listTools: vi.fn(),
    listConnections: vi.fn(),
  },
}))

import { jobsAPI, agentsAPI, mcpAPI } from '../../src/lib/api'

const wrap = (ui: React.ReactElement) => (
  <MemoryRouter
    initialEntries={[
      {
        pathname: '/jobs/1',
        state: { selectedAgents: [1] },
      },
    ]}
  >
    <Routes>
      <Route path="/jobs/:id" element={ui} />
    </Routes>
  </MemoryRouter>
)

describe('Job detail workflow tool visibility (integration)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(agentsAPI.list).mockResolvedValue([mockAgent])
    vi.mocked(mcpAPI.listTools).mockResolvedValue([
      { id: 99, name: 'Orphan Tool', tool_type: 'rest_api', server_connection_id: null },
    ])
    vi.mocked(mcpAPI.listConnections).mockResolvedValue([])
    vi.mocked(jobsAPI.get).mockResolvedValueOnce(jobBeforeWorkflow).mockResolvedValue(jobAfterWorkflow)
  })

  it('Build Workflow → None → Create Auto-Split → Edit tools shows None in modal', async () => {
    render(wrap(<JobDetailPage />))

    await waitFor(() => {
      expect(screen.queryByText(/Loading job details/i)).not.toBeInTheDocument()
    })

    await screen.findByRole('heading', { name: /^Build Workflow$/i })

    const comboboxes = screen.getAllByRole('combobox')
    // [0] Agents work, [1] Tool visibility (what agents see)
    const toolVisibilitySelect = comboboxes[1]
    expect(toolVisibilitySelect).toBeTruthy()
    fireEvent.change(toolVisibilitySelect, { target: { value: 'none' } })
    expect(toolVisibilitySelect).toHaveValue('none')

    fireEvent.click(screen.getByRole('button', { name: /Create Auto-Split Workflow/i }))

    await waitFor(() => {
      expect(jobsAPI.autoSplitWorkflow).toHaveBeenCalled()
    })

    const splitCall = vi.mocked(jobsAPI.autoSplitWorkflow).mock.calls[0]
    expect(splitCall[0]).toBe(1)
    expect(splitCall[1]).toEqual([1])
    expect(splitCall[4]).toBe('none')
    expect(Array.isArray(splitCall[3])).toBe(true)

    await screen.findByRole('button', { name: /Edit tools/i })

    fireEvent.click(screen.getByRole('button', { name: /Edit tools/i }))

    await screen.findByRole('heading', { name: /Tools for Step 1: Agent One/i })

    const visibilityBlock = screen.getByText('Tool visibility (what this step sees)').parentElement
    expect(visibilityBlock).toBeTruthy()
    expect(within(visibilityBlock as HTMLElement).getByRole('combobox')).toHaveValue('none')
  }, 30_000)
})
