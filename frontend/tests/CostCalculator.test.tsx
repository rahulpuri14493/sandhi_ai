import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { CostCalculator } from '../src/components/CostCalculator'
import type { WorkflowPreview } from '../src/lib/types'

const basePreview: WorkflowPreview = {
  steps: [],
  total_cost: 25.50,
  breakdown: {
    task_costs: 15.0,
    communication_costs: 5.0,
    commission: 5.50,
  },
}

describe('CostCalculator', () => {
  it('renders cost breakdown section', () => {
    render(<CostCalculator preview={basePreview} />)
    expect(screen.getByText('Cost Breakdown')).toBeInTheDocument()
  })

  it('displays task costs correctly', () => {
    render(<CostCalculator preview={basePreview} />)
    expect(screen.getByText('Task Costs:')).toBeInTheDocument()
    expect(screen.getByText('$15.00')).toBeInTheDocument()
  })

  it('displays communication costs correctly', () => {
    render(<CostCalculator preview={basePreview} />)
    expect(screen.getByText('Communication Costs:')).toBeInTheDocument()
    expect(screen.getByText('$5.00')).toBeInTheDocument()
  })

  it('displays platform commission correctly', () => {
    render(<CostCalculator preview={basePreview} />)
    expect(screen.getByText('Platform Commission:')).toBeInTheDocument()
    expect(screen.getByText('$5.50')).toBeInTheDocument()
  })

  it('displays total cost correctly', () => {
    render(<CostCalculator preview={basePreview} />)
    expect(screen.getByText('Total Cost:')).toBeInTheDocument()
    expect(screen.getByText('$25.50')).toBeInTheDocument()
  })

  it('does not render workflow steps when steps array is empty', () => {
    render(<CostCalculator preview={basePreview} />)
    expect(screen.queryByText('Workflow Steps')).not.toBeInTheDocument()
  })

  it('renders workflow steps when steps are provided', () => {
    const previewWithSteps: WorkflowPreview = {
      ...basePreview,
      steps: [
        {
          id: 1,
          job_id: 1,
          agent_id: 1,
          step_order: 1,
          status: 'completed',
          cost: 7.5,
        },
        {
          id: 2,
          job_id: 1,
          agent_id: 2,
          step_order: 2,
          status: 'completed',
          cost: 7.5,
        },
      ],
    }
    render(<CostCalculator preview={previewWithSteps} />)
    expect(screen.getByText('Workflow Steps')).toBeInTheDocument()
    expect(screen.getByText(/Step 1: Agent 1/)).toBeInTheDocument()
    expect(screen.getByText(/Step 2: Agent 2/)).toBeInTheDocument()
    expect(screen.getAllByText('$7.50')).toHaveLength(2)
  })

  it('formats decimal values correctly', () => {
    const previewWithDecimals: WorkflowPreview = {
      ...basePreview,
      breakdown: {
        task_costs: 10.1,
        communication_costs: 2.22,
        commission: 1.234,
      },
      total_cost: 13.554,
    }
    render(<CostCalculator preview={previewWithDecimals} />)
    expect(screen.getByText('$10.10')).toBeInTheDocument()
    expect(screen.getByText('$2.22')).toBeInTheDocument()
    expect(screen.getByText('$1.23')).toBeInTheDocument()
    expect(screen.getByText('$13.55')).toBeInTheDocument()
  })
})
