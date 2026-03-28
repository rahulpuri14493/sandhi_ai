import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import NewJobPage from '../src/pages/NewJob'

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

vi.mock('../src/lib/api', () => ({
  jobsAPI: { create: vi.fn() },
  agentsAPI: { list: vi.fn() },
  mcpAPI: {
    listTools: vi.fn(),
    listConnections: vi.fn(),
  },
}))

import { jobsAPI, agentsAPI, mcpAPI } from '../src/lib/api'

const mockAgent = {
  id: 7,
  developer_id: 1,
  name: 'Selectable Agent',
  description: 'Test',
  status: 'active' as const,
  pricing_model: 'pay_per_use' as const,
  price_per_task: 2,
  price_per_communication: 0.5,
  created_at: '2024-01-01',
}

const wrap = (ui: React.ReactElement) => <MemoryRouter>{ui}</MemoryRouter>

describe('NewJob page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(agentsAPI.list).mockResolvedValue([mockAgent])
    vi.mocked(mcpAPI.listTools).mockResolvedValue([])
    vi.mocked(mcpAPI.listConnections).mockResolvedValue([])
    vi.mocked(jobsAPI.create).mockResolvedValue({
      id: 99,
      title: 'New',
      status: 'draft',
      total_cost: 0,
      created_at: '2024-01-01',
    })
  })

  it('renders create job form', async () => {
    render(wrap(<NewJobPage />))
    expect(screen.getByRole('heading', { name: /Create New Job/i })).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByText('Selectable Agent')).toBeInTheDocument()
    })
  })

  it('submits job when title and at least one agent selected', async () => {
    render(wrap(<NewJobPage />))
    await waitFor(() => {
      expect(screen.getByText('Selectable Agent')).toBeInTheDocument()
    })

    fireEvent.change(screen.getByLabelText(/^Job Title$/i), {
      target: { value: 'Integration job' },
    })
    const checkboxes = screen.getAllByRole('checkbox')
    fireEvent.click(checkboxes[0])

    fireEvent.click(screen.getByRole('button', { name: /^Create Job$/i }))

    await waitFor(() => {
      expect(jobsAPI.create).toHaveBeenCalled()
    })
    const call = vi.mocked(jobsAPI.create).mock.calls[0][0]
    expect(call).toEqual(
      expect.objectContaining({
        title: 'Integration job',
        tool_visibility: 'none',
      })
    )
    expect(mockNavigate).toHaveBeenCalledWith('/jobs/99', {
      state: { selectedAgents: [7] },
    })
  })

  it('shows error when no agent selected', async () => {
    render(wrap(<NewJobPage />))
    await waitFor(() => {
      expect(screen.getByText('Selectable Agent')).toBeInTheDocument()
    })
    fireEvent.change(screen.getByLabelText(/^Job Title$/i), {
      target: { value: 'No agents' },
    })
    fireEvent.click(screen.getByRole('button', { name: /^Create Job$/i }))
    await waitFor(() => {
      expect(screen.getByText(/Please select at least one agent/i)).toBeInTheDocument()
    })
    expect(jobsAPI.create).not.toHaveBeenCalled()
  })
})
