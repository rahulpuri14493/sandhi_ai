/**
 * Dashboard shell: renders Business vs Developer child based on auth role.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import DashboardPage from '../src/pages/Dashboard'

vi.mock('../src/components/BusinessDashboard', () => ({
  BusinessDashboard: () => <div data-testid="business-dashboard">Business dashboard mock</div>,
}))
vi.mock('../src/components/DeveloperDashboard', () => ({
  DeveloperDashboard: () => <div data-testid="developer-dashboard">Developer dashboard mock</div>,
}))

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

let mockUser: { id: number; role: string } | null = { id: 1, role: 'developer' }
const mockLoadUser = vi.fn().mockResolvedValue(undefined)

vi.mock('../src/lib/store', () => ({
  useAuthStore: () => ({
    user: mockUser,
    loadUser: mockLoadUser,
  }),
}))

const wrap = (ui: React.ReactElement) => <MemoryRouter>{ui}</MemoryRouter>

describe('Dashboard page role branches', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockNavigate.mockClear()
    mockLoadUser.mockResolvedValue(undefined)
  })

  it('renders DeveloperDashboard for developer user', async () => {
    mockUser = { id: 1, role: 'developer' }
    render(wrap(<DashboardPage />))
    await waitFor(() => {
      expect(screen.getByTestId('developer-dashboard')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('business-dashboard')).not.toBeInTheDocument()
  })

  it('renders BusinessDashboard for business user', async () => {
    mockUser = { id: 2, role: 'business' }
    render(wrap(<DashboardPage />))
    await waitFor(() => {
      expect(screen.getByTestId('business-dashboard')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('developer-dashboard')).not.toBeInTheDocument()
  })

  it('redirects to login when no user after load', async () => {
    mockUser = null
    render(wrap(<DashboardPage />))
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/auth/login')
    })
  })
})
