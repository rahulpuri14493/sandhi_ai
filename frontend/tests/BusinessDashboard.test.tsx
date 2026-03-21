import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { BusinessDashboard } from '../src/components/BusinessDashboard'

vi.mock('../src/lib/api', () => ({
  dashboardsAPI: {
    getBusinessSpending: vi.fn().mockResolvedValue({ total_spent: 100, job_count: 3 }),
    getBusinessJobs: vi.fn().mockResolvedValue([
      { id: 1, title: 'Math Job', description: 'Add numbers', status: 'completed', total_cost: 5.5, created_at: '2024-01-01' },
      { id: 2, title: 'KYC Analysis', description: 'Verify documents', status: 'draft', total_cost: 0, created_at: '2024-01-02' },
      { id: 3, title: 'Financial Report', description: 'Generate report', status: 'in_progress', total_cost: 10, created_at: '2024-01-03' },
    ]),
  },
  jobsAPI: {
    getShareLink: vi.fn(),
    rerun: vi.fn(),
    delete: vi.fn(),
  },
}))

const wrapWithRouter = (ui: React.ReactElement) => (
  <MemoryRouter>
    {ui}
  </MemoryRouter>
)

describe('BusinessDashboard', () => {
  beforeEach(async () => {
    vi.clearAllMocks()
  })

  it('renders Business Dashboard title', async () => {
    render(wrapWithRouter(<BusinessDashboard />))
    await screen.findByText('Business Dashboard', {}, { timeout: 10000 })
    expect(screen.getByText('Business Dashboard')).toBeInTheDocument()
  }, 12000)

  it('displays total spent and job count after loading', async () => {
    render(wrapWithRouter(<BusinessDashboard />))
    await screen.findByText('$100.00')
    expect(screen.getByText('$100.00')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
  })

  it('displays List of Jobs section', async () => {
    render(wrapWithRouter(<BusinessDashboard />))
    await screen.findByText('List of Jobs')
    expect(screen.getByText('List of Jobs')).toBeInTheDocument()
  })

  it('displays job titles from API', async () => {
    render(wrapWithRouter(<BusinessDashboard />))
    await screen.findByText('Math Job')
    expect(screen.getByText('Math Job')).toBeInTheDocument()
    expect(screen.getByText('KYC Analysis')).toBeInTheDocument()
    expect(screen.getByText('Financial Report')).toBeInTheDocument()
  })

  it('filters jobs by search term', async () => {
    render(wrapWithRouter(<BusinessDashboard />))
    await screen.findByText('Math Job')
    const searchInput = screen.getByPlaceholderText(/search jobs/i)
    fireEvent.change(searchInput, { target: { value: 'Math' } })
    expect(screen.getByText('Math Job')).toBeInTheDocument()
    expect(screen.queryByText('KYC Analysis')).not.toBeInTheDocument()
    expect(screen.queryByText('Financial Report')).not.toBeInTheDocument()
  })

  it('filters jobs by status', async () => {
    render(wrapWithRouter(<BusinessDashboard />))
    await screen.findByText('Math Job')
    const statusSelect = screen.getByRole('combobox')
    fireEvent.change(statusSelect, { target: { value: 'completed' } })
    expect(screen.getByText('Math Job')).toBeInTheDocument()
    expect(screen.queryByText('KYC Analysis')).not.toBeInTheDocument()
    expect(screen.queryByText('Financial Report')).not.toBeInTheDocument()
  })

  it('shows New Job button', async () => {
    render(wrapWithRouter(<BusinessDashboard />))
    // Wait for the dashboard to finish loading before asserting the CTA.
    await screen.findByText('List of Jobs', {}, { timeout: 25000 })
    const newJobLink = screen.getByRole('link', { name: /new job/i })
    expect(newJobLink).toHaveAttribute('href', '/jobs/new')
  }, 30000)
})
