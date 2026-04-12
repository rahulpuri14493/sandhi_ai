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
  mcpAPI: {
    listTools: vi.fn(),
    listConnections: vi.fn(),
  },
}))
vi.mock('../../src/lib/store', () => ({
  useAuthStore: () => ({ user: { id: 1, role: 'business' }, loadUser: vi.fn() }),
}))

import { jobsAPI, agentsAPI, mcpAPI } from '../../src/lib/api'

const wrap = (ui: React.ReactElement) => (
  <MemoryRouter initialEntries={['/jobs/new']}>
    <Routes>
      <Route path="/jobs/new" element={ui} />
    </Routes>
  </MemoryRouter>
)

const mockPlatformTools = [
  { id: 1, name: 'Postgres Query', tool_type: 'query', server_connection_id: null },
]
const mockConnections = [
  { id: 10, name: 'Local Postgres', user_id: 1, base_url: '', endpoint_path: '', auth_type: 'none', is_platform_configured: false, is_active: true, created_at: '' },
]

describe('New Job integration', () => {
  beforeEach(() => {
    vi.mocked(agentsAPI.list).mockResolvedValue(mockAgents)
    vi.mocked(mcpAPI.listTools).mockResolvedValue(mockPlatformTools)
    vi.mocked(mcpAPI.listConnections).mockResolvedValue(mockConnections)
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
    const agentLabel = screen.getByText(/Agent One/).closest('label')
    const agentCheckbox = agentLabel?.querySelector('input[type="checkbox"]')
    if (agentCheckbox) fireEvent.click(agentCheckbox as HTMLElement)
    const titleInput = screen.getByPlaceholderText(/Enter job title/i)
    fireEvent.change(titleInput, { target: { value: 'E2E Job' } })
    const submit = screen.getByRole('button', { name: /Create Job/i })
    fireEvent.click(submit)
    await waitFor(() => {
      expect(jobsAPI.create).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'E2E Job',
          allowed_platform_tool_ids: [],
          allowed_connection_ids: [],
        })
      )
    }, { timeout: 10000 })
  }, 12000)

  it('calls jobsAPI.create with allowed_platform_tool_ids and tool_visibility when user selects tools and visibility', async () => {
    render(wrap(<NewJobPage />))
    await screen.findByText('Platform tools')
    await screen.findByText(/Postgres Query/)
    fireEvent.click(screen.getByText(/Postgres Query/))
    const visibilitySelect = screen.getByRole('combobox')
    fireEvent.change(visibilitySelect, { target: { value: 'names_only' } })
    fireEvent.change(screen.getByPlaceholderText(/Enter job title/i), { target: { value: 'Scoped Job' } })
    const agentCheckboxes = screen.getAllByRole('checkbox')
    const agentCb = agentCheckboxes.find((el) => el.closest('label')?.textContent?.includes('Agent One'))
    if (agentCb) fireEvent.click(agentCb)
    fireEvent.click(screen.getByRole('button', { name: /Create Job/i }))
    await waitFor(() => {
      expect(jobsAPI.create).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'Scoped Job',
          allowed_platform_tool_ids: [1],
          tool_visibility: 'names_only',
        })
      )
    })
  })
})
