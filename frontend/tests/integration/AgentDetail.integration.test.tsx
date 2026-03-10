/**
 * Integration tests: Agent detail page (reviews, protocol, API info).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import AgentDetailPage from '../../src/pages/AgentDetail'

const mockAgent = {
  id: 1,
  name: 'Test Agent',
  description: 'An agent for tests',
  status: 'active',
  price_per_task: 10,
  price_per_communication: 1,
  developer_id: 1,
  capabilities: [],
  pricing_model: 'pay_per_use',
  created_at: '2024-01-01',
  a2a_enabled: false,
}

vi.mock('../../src/lib/api', () => ({
  agentsAPI: {
    get: vi.fn(),
    getReviewSummary: vi.fn().mockResolvedValue({ average_rating: 4.5, total_reviews: 10 }),
    listReviews: vi.fn().mockResolvedValue({ reviews: [], total: 0 }),
  },
}))
vi.mock('../../src/lib/store', () => ({
  useAuthStore: () => ({ user: { id: 1, role: 'developer' }, loadUser: vi.fn() }),
}))

import { agentsAPI } from '../../src/lib/api'

const wrap = (ui: React.ReactElement) => (
  <MemoryRouter initialEntries={['/agents/1']}>
    <Routes>
      <Route path="/agents/:id" element={ui} />
    </Routes>
  </MemoryRouter>
)

describe('AgentDetail integration', () => {
  beforeEach(() => {
    vi.mocked(agentsAPI.get).mockResolvedValue(mockAgent)
  })

  it('loads and displays agent from API', async () => {
    render(wrap(<AgentDetailPage />))
    expect(agentsAPI.get).toHaveBeenCalledWith(1)
    await screen.findByText('Test Agent')
    expect(screen.getByText(/An agent for tests/)).toBeInTheDocument()
  })

  it('fetches review summary for agent id from route', async () => {
    render(wrap(<AgentDetailPage />))
    await screen.findByText('Test Agent')
    expect(agentsAPI.get).toHaveBeenCalledWith(1)
    expect(agentsAPI.getReviewSummary).toHaveBeenCalledWith(1)
  })
})
