import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { JobStatusTracker } from '../src/components/JobStatusTracker'
import type { Job } from '../src/lib/types'

vi.mock('../src/lib/api', () => ({
  jobsAPI: {
    getStatus: vi.fn(),
  },
}))

const baseJob: Job = {
  id: 1,
  business_id: 1,
  title: 'Test Job',
  description: 'Test description',
  status: 'draft',
  total_cost: 0,
  created_at: '2024-01-01T00:00:00Z',
}

describe('JobStatusTracker', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders job status header', () => {
    render(<JobStatusTracker jobId={1} job={baseJob} />)
    expect(screen.getByText('Job Status')).toBeInTheDocument()
  })

  it('displays draft status badge', () => {
    render(<JobStatusTracker jobId={1} job={baseJob} />)
    expect(screen.getByText('DRAFT')).toBeInTheDocument()
  })

  it('displays pending_approval status badge', () => {
    const job = { ...baseJob, status: 'pending_approval' as const }
    render(<JobStatusTracker jobId={1} job={job} />)
    expect(screen.getByText('PENDING APPROVAL')).toBeInTheDocument()
  })

  it('displays approved status badge', () => {
    const job = { ...baseJob, status: 'approved' as const }
    render(<JobStatusTracker jobId={1} job={job} />)
    expect(screen.getByText('APPROVED')).toBeInTheDocument()
  })

  it('displays in_progress status badge', () => {
    const job = { ...baseJob, status: 'in_progress' as const }
    render(<JobStatusTracker jobId={1} job={job} />)
    expect(screen.getByText('IN PROGRESS')).toBeInTheDocument()
  })

  it('displays completed status badge', () => {
    const job = { ...baseJob, status: 'completed' as const }
    render(<JobStatusTracker jobId={1} job={job} />)
    expect(screen.getByText('COMPLETED')).toBeInTheDocument()
  })

  it('displays failed status badge', () => {
    const job = { ...baseJob, status: 'failed' as const }
    render(<JobStatusTracker jobId={1} job={job} />)
    expect(screen.getByText('FAILED')).toBeInTheDocument()
  })

  it('does not render workflow steps when none exist', () => {
    render(<JobStatusTracker jobId={1} job={baseJob} />)
    expect(screen.queryByText('Workflow Steps & Agent Outputs')).not.toBeInTheDocument()
  })

  it('renders workflow steps when provided', () => {
    const jobWithSteps: Job = {
      ...baseJob,
      workflow_steps: [
        {
          id: 1,
          job_id: 1,
          agent_id: 1,
          step_order: 1,
          status: 'completed',
          cost: 5.0,
        },
      ],
    }
    render(<JobStatusTracker jobId={1} job={jobWithSteps} />)
    expect(screen.getByText('Workflow Steps & Agent Outputs')).toBeInTheDocument()
  })

  it('shows document count when files are present', () => {
    const jobWithFiles: Job = {
      ...baseJob,
      files: [
        { id: '1', name: 'doc.pdf', type: 'application/pdf', size: 1024 },
      ],
      workflow_steps: [
        {
          id: 1,
          job_id: 1,
          agent_id: 1,
          step_order: 1,
          status: 'completed',
          cost: 5.0,
        },
      ],
    }
    render(<JobStatusTracker jobId={1} job={jobWithFiles} />)
    expect(screen.getByText('1 document included')).toBeInTheDocument()
  })

  it('shows documents (plural) when multiple files', () => {
    const jobWithFiles: Job = {
      ...baseJob,
      files: [
        { id: '1', name: 'doc1.pdf', type: 'application/pdf', size: 1024 },
        { id: '2', name: 'doc2.pdf', type: 'application/pdf', size: 2048 },
      ],
      workflow_steps: [
        {
          id: 1,
          job_id: 1,
          agent_id: 1,
          step_order: 1,
          status: 'completed',
          cost: 5.0,
        },
      ],
    }
    render(<JobStatusTracker jobId={1} job={jobWithFiles} />)
    expect(screen.getByText('2 documents included')).toBeInTheDocument()
  })

  it('shows All Agent Results section for multi-agent completed job', () => {
    const multiAgentJob: Job = {
      ...baseJob,
      status: 'completed',
      workflow_steps: [
        {
          id: 1,
          job_id: 1,
          agent_id: 1,
          agent_name: 'Agent Alpha',
          step_order: 1,
          status: 'completed',
          cost: 5.0,
          output_data: JSON.stringify({ choices: [{ message: { content: 'Result A' } }] }),
        },
        {
          id: 2,
          job_id: 1,
          agent_id: 2,
          agent_name: 'Agent Beta',
          step_order: 2,
          status: 'completed',
          cost: 5.0,
          output_data: JSON.stringify({ choices: [{ message: { content: 'Result B' } }] }),
        },
      ],
    }
    render(<JobStatusTracker jobId={1} job={multiAgentJob} />)
    expect(screen.getByText(/All Agent Results \(2 agents\)/)).toBeInTheDocument()
    expect(screen.getAllByText(/Agent Alpha/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Agent Beta/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Result A/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Result B/).length).toBeGreaterThan(0)
  })

  it('does not show All Agent Results for single-agent completed job', () => {
    const singleAgentJob: Job = {
      ...baseJob,
      status: 'completed',
      workflow_steps: [
        {
          id: 1,
          job_id: 1,
          agent_id: 1,
          agent_name: 'Solo Agent',
          step_order: 1,
          status: 'completed',
          cost: 5.0,
        },
      ],
    }
    render(<JobStatusTracker jobId={1} job={singleAgentJob} />)
    expect(screen.queryByText(/All Agent Results/)).not.toBeInTheDocument()
  })

  it('displays agent name when workflow step has agent_name', () => {
    const jobWithAgentName: Job = {
      ...baseJob,
      workflow_steps: [
        {
          id: 1,
          job_id: 1,
          agent_id: 1,
          agent_name: 'Custom Agent',
          step_order: 1,
          status: 'completed',
          cost: 5.0,
        },
      ],
    }
    render(<JobStatusTracker jobId={1} job={jobWithAgentName} />)
    expect(screen.getByText(/Custom Agent/)).toBeInTheDocument()
  })
})
