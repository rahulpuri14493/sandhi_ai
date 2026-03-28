import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import EditAgentPage from '../src/pages/EditAgent'

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

vi.mock('../src/lib/api', () => ({
  agentsAPI: {
    get: vi.fn(),
    update: vi.fn(),
    testConnection: vi.fn(),
  },
}))

import { agentsAPI } from '../src/lib/api'

const loadedAgent = {
  id: 5,
  developer_id: 1,
  name: 'Existing Agent',
  description: 'Loaded',
  status: 'active' as const,
  pricing_model: 'pay_per_use' as const,
  price_per_task: 3,
  price_per_communication: 0.5,
  capabilities: ['code'],
  api_endpoint: '',
  created_at: '2024-01-01',
  a2a_enabled: false,
}

const wrap = (ui: React.ReactElement) => (
  <MemoryRouter initialEntries={['/agents/edit/5']}>
    <Routes>
      <Route path="/agents/edit/:id" element={ui} />
    </Routes>
  </MemoryRouter>
)

describe('EditAgent page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(agentsAPI.get).mockResolvedValue(loadedAgent)
    vi.mocked(agentsAPI.update).mockResolvedValue({ ...loadedAgent, name: 'Updated' })
  })

  it('loads agent and renders edit form', async () => {
    render(wrap(<EditAgentPage />))
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /^Edit Agent$/i })).toBeInTheDocument()
    })
    expect(agentsAPI.get).toHaveBeenCalledWith(5)
    expect(screen.getByDisplayValue('Existing Agent')).toBeInTheDocument()
  })

  it('calls update and navigates to dashboard', async () => {
    render(wrap(<EditAgentPage />))
    await waitFor(() => {
      expect(screen.getByDisplayValue('Existing Agent')).toBeInTheDocument()
    })
    fireEvent.change(screen.getByDisplayValue('Existing Agent'), {
      target: { value: 'Renamed Agent' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Update Agent/i }))
    await waitFor(() => {
      expect(agentsAPI.update).toHaveBeenCalled()
    })
    const payload = vi.mocked(agentsAPI.update).mock.calls[0][1] as Record<string, unknown>
    expect(payload.name).toBe('Renamed Agent')
    expect(mockNavigate).toHaveBeenCalledWith('/dashboard')
  })
})
