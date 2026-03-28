import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SchedulesPage from '../src/pages/Schedules'

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
    listAllSchedules: vi.fn(),
  },
}))

import { jobsAPI } from '../src/lib/api'

const wrap = (ui: React.ReactElement) => <MemoryRouter>{ui}</MemoryRouter>

describe('Schedules page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows empty state when no schedules', async () => {
    vi.mocked(jobsAPI.listAllSchedules).mockResolvedValue({ items: [] })
    render(wrap(<SchedulesPage />))
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /^Schedules$/i })).toBeInTheDocument()
    })
    expect(screen.getByText(/No schedules yet/i)).toBeInTheDocument()
    expect(jobsAPI.listAllSchedules).toHaveBeenCalled()
  })

  it('renders schedule row when API returns items', async () => {
    vi.mocked(jobsAPI.listAllSchedules).mockResolvedValue({
      items: [
        {
          id: 1,
          job_id: 10,
          scheduled_at: '2026-04-01T12:00:00Z',
          timezone: 'UTC',
          status: 'active',
          job_title: 'My scheduled job',
          job_status: 'in_queue',
          last_run_time: null,
          next_run_time: null,
          created_at: '2026-01-01T00:00:00Z',
        },
      ],
    })
    render(wrap(<SchedulesPage />))
    await waitFor(() => {
      expect(screen.getByText('My scheduled job')).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: /^All$/i })).toBeInTheDocument()
  })
})
