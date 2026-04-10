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
    getBusinessAgentPerformance: vi.fn().mockResolvedValue({
      agents: [
        {
          agent_id: 10,
          agent_name: 'Perf Agent',
          api_endpoint: 'https://agent.perf.example.com',
          totals: { steps: 4, completed_steps: 3, failed_steps: 1, in_progress_steps: 0, cost: 10, total_tokens: 1200 },
          quality: { success_rate: 0.75, average_confidence: 0.9 },
          latest_runtime: {
            job_id: 42,
            workflow_step_id: 5,
            phase: 'calling_agent',
            reason_code: 'agent_endpoint_http_error',
            stuck_reason: null,
            status: 'failed',
          },
        },
      ],
    }),
  },
  jobsAPI: {
    getShareLink: vi.fn(),
    rerun: vi.fn(),
    delete: vi.fn(),
  },
  mcpAPI: {
    listTools: vi.fn().mockResolvedValue([
      { id: 1, name: 'local_minio', tool_type: 'minio' },
      { id: 2, name: 'postgres_main', tool_type: 'postgres' },
    ]),
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

  it('displays hired agent performance section', async () => {
    render(wrapWithRouter(<BusinessDashboard />))
    await screen.findByText('Hired Agent Performance')
    expect(screen.getByText('Perf Agent')).toBeInTheDocument()
    expect(screen.getByText('75.0%')).toBeInTheDocument()
    expect(screen.getByText('1,200')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /open runtime/i })).toBeInTheDocument()
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
    const statusSelect = screen.getByDisplayValue('All statuses')
    fireEvent.change(statusSelect, { target: { value: 'completed' } })
    expect(screen.getByText('Math Job')).toBeInTheDocument()
    expect(screen.queryByText('KYC Analysis')).not.toBeInTheDocument()
    expect(screen.queryByText('Financial Report')).not.toBeInTheDocument()
  })

  it('shows New Job button', async () => {
    render(wrapWithRouter(<BusinessDashboard />))
    await screen.findByText('List of Jobs')
    const newJobLink = document.querySelector('a[href="/jobs/new"]')
    expect(newJobLink).not.toBeNull()
  })
})
