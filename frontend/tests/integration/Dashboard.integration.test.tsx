/**
 * Integration tests: Dashboard routing and developer/business views.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import DashboardPage from '../../src/pages/Dashboard'

vi.mock('../../src/lib/api', () => ({
  dashboardsAPI: {
    getDeveloperEarnings: vi.fn().mockResolvedValue({
      total_earnings: 0,
      pending_earnings: 0,
      recent_earnings: [],
    }),
    getDeveloperAgents: vi.fn().mockResolvedValue([]),
    getDeveloperStats: vi.fn().mockResolvedValue({ total_earnings: 0, total_tasks: 0, agent_count: 0, total_communications: 0 }),
    getBusinessJobs: vi.fn().mockResolvedValue([]),
    getBusinessSpending: vi.fn().mockResolvedValue({ total_spent: 0, job_count: 0 }),
  },
}))
vi.mock('../../src/lib/store', () => ({
  useAuthStore: () => ({ user: { id: 1, role: 'developer' }, loadUser: vi.fn() }),
}))


const wrap = (ui: React.ReactElement) => (
  <MemoryRouter>
    <Routes>
      <Route path="/" element={ui} />
    </Routes>
  </MemoryRouter>
)

describe('Dashboard integration', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders dashboard for developer user', async () => {
    render(wrap(<DashboardPage />))
    await waitFor(() => {
      expect(screen.queryByText(/Loading/i)).not.toBeInTheDocument()
    }, { timeout: 3000 })
    expect(screen.getByText(/Developer Dashboard/i)).toBeInTheDocument()
  })
})
