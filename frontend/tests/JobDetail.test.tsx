import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import JobDetailPage from '../src/pages/JobDetail'

vi.mock('../src/components/WorkflowBuilder', () => ({
  WorkflowBuilder: () => <div data-testid="workflow-builder-mock">Workflow builder</div>,
}))

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

vi.mock('../src/lib/api', () => ({
  jobsAPI: {
    get: vi.fn(),
    getSchedule: vi.fn(),
    previewWorkflow: vi.fn(),
    approve: vi.fn(),
    execute: vi.fn(),
    analyzeDocuments: vi.fn(),
    updateStepTools: vi.fn(),
    getShareLink: vi.fn(),
    downloadFile: vi.fn(),
    createSchedule: vi.fn(),
    updateSchedule: vi.fn(),
    rerun: vi.fn(),
    cancel: vi.fn(),
    answerQuestion: vi.fn(),
    generateWorkflowQuestions: vi.fn(),
    getAgentPlannerStatus: vi.fn(),
    listPlannerArtifacts: vi.fn(),
    getPlannerArtifactRaw: vi.fn(),
    getPlannerPipeline: vi.fn(),
  },
  mcpAPI: {
    listTools: vi.fn().mockResolvedValue([]),
    listConnections: vi.fn().mockResolvedValue([]),
  },
}))

import { jobsAPI } from '../src/lib/api'

const draftJob = {
  id: 42,
  business_id: 1,
  title: 'Job detail smoke',
  description: 'Draft for tests',
  status: 'draft' as const,
  total_cost: 0,
  created_at: '2024-01-01T00:00:00Z',
  workflow_steps: [],
  files: [],
  conversation: [],
}

const wrap = (ui: React.ReactElement) => (
  <MemoryRouter initialEntries={['/jobs/42']}>
    <Routes>
      <Route path="/jobs/:id" element={ui} />
    </Routes>
  </MemoryRouter>
)

describe('JobDetail page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(jobsAPI.get).mockResolvedValue(draftJob)
    vi.mocked(jobsAPI.getSchedule).mockRejectedValue({ response: { status: 404 } })
  })

  it('loads job and shows title and draft actions', async () => {
    render(wrap(<JobDetailPage />))
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Job detail smoke/i })).toBeInTheDocument()
    })
    expect(jobsAPI.get).toHaveBeenCalledWith(42)
    expect(screen.getByRole('button', { name: /Build Workflow/i })).toBeInTheDocument()
    expect(screen.getByTestId('workflow-builder-mock')).toBeInTheDocument()
  })

  it('shows job not found when get fails', async () => {
    vi.mocked(jobsAPI.get).mockRejectedValue(new Error('network'))
    render(wrap(<JobDetailPage />))
    await waitFor(() => {
      expect(screen.getByText(/Job not found/i)).toBeInTheDocument()
    })
  })

  it('loads planner status and artifacts when audit section opened', async () => {
    vi.mocked(jobsAPI.getAgentPlannerStatus).mockResolvedValue({
      configured: true,
      provider: 'openai_compatible',
      model: 'gpt-4o-mini',
      base_url_configured: true,
    })
    vi.mocked(jobsAPI.listPlannerArtifacts).mockResolvedValue({
      items: [
        {
          id: 9,
          artifact_type: 'task_split',
          storage: 'local',
          byte_size: 120,
          created_at: '2024-06-01T12:00:00Z',
        },
      ],
    })
    vi.mocked(jobsAPI.getPlannerPipeline).mockResolvedValue({
      schema_version: 'planner_pipeline.v1',
      job_id: 42,
      brd_analysis: null,
      task_split: null,
      tool_suggestion: null,
      artifact_ids: {},
    })
    vi.mocked(jobsAPI.getPlannerArtifactRaw).mockResolvedValue({ raw: true })

    render(wrap(<JobDetailPage />))
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Job detail smoke/i })).toBeInTheDocument()
    })

    const toggle = screen.getByRole('button', { name: /Agent planner/i })
    fireEvent.click(toggle)

    await waitFor(() => {
      expect(jobsAPI.getAgentPlannerStatus).toHaveBeenCalled()
      expect(jobsAPI.listPlannerArtifacts).toHaveBeenCalledWith(42)
      expect(jobsAPI.getPlannerPipeline).toHaveBeenCalledWith(42)
    })

    expect(await screen.findByText('task_split')).toBeInTheDocument()

    const viewBtn = screen.getByRole('button', { name: /^View$/i })
    fireEvent.click(viewBtn)

    await waitFor(() => {
      expect(jobsAPI.getPlannerArtifactRaw).toHaveBeenCalledWith(42, 9)
    })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText(/"raw"/)).toBeInTheDocument()
  })
})
