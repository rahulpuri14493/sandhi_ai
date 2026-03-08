/**
 * Integration tests: New Job page and job creation flow (mocked API).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import NewJobPage from '../../src/pages/NewJob'

const mockAgents = [
  { id: 1, name: 'Agent One', description: 'Desc', status: 'active', price_per_task: 5, price_per_communication: 0.5, developer_id: 1, capabilities: [], pricing_model: 'pay_per_use', created_at: '2024-01-01' },
]

vi.mock('../../src/lib/api', () => ({
  jobsAPI: {
    create: vi.fn(),
    get: vi.fn(),
    analyzeDocuments: vi.fn(),
    autoSplitWorkflow: vi.fn(),
    previewWorkflow: vi.fn(),
    approve: vi.fn(),
    execute: vi.fn(),
  },
  agentsAPI: { list: vi.fn() },
}))
vi.mock('../../src/lib/store', () => ({
  useAuthStore: () => ({ user: { id: 1, role: 'business' }, loadUser: vi.fn() }),
}))

import { jobsAPI, agentsAPI } from '../../src/lib/api'

const wrap = (ui: React.ReactElement) => (
  <MemoryRouter initialEntries={['/jobs/new']}>
    <Routes>
      <Route path="/jobs/new" element={ui} />
    </Routes>
  </MemoryRouter>
)

describe('New Job integration', () => {
  beforeEach(() => {
    vi.mocked(agentsAPI.list).mockResolvedValue(mockAgents)
    vi.mocked(jobsAPI.create).mockResolvedValue({
      id: 99,
      title: 'Test Job',
      status: 'draft',
      files: [],
      workflow_steps: [],
    })
  })

  it('renders New Job form and loads agents', async () => {
    render(wrap(<NewJobPage />))
    await screen.findByText('Agent One')
    expect(agentsAPI.list).toHaveBeenCalled()
  })

  it('calls jobsAPI.create when at least one agent selected and form submitted', async () => {
    render(wrap(<NewJobPage />))
    await screen.findByText('Agent One')
    const checkboxes = screen.getAllByRole('checkbox')
    if (checkboxes.length > 0) fireEvent.click(checkboxes[0])
    const titleInput = screen.getByPlaceholderText(/Enter job title/i)
    fireEvent.change(titleInput, { target: { value: 'E2E Job' } })
    const submit = screen.getByRole('button', { name: /Create Job/i })
    fireEvent.click(submit)
    await waitFor(() => {
      expect(jobsAPI.create).toHaveBeenCalled()
    })
  })
})
