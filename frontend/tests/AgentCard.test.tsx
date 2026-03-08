import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import { AgentCard } from '../src/components/AgentCard'
import type { Agent } from '../src/lib/types'

const wrapWithRouter = (ui: React.ReactElement) => (
  <BrowserRouter>{ui}</BrowserRouter>
)

const baseAgent: Agent = {
  id: 1,
  developer_id: 1,
  name: 'Math Agent',
  description: 'Performs mathematical operations',
  capabilities: ['addition', 'subtraction'],
  pricing_model: 'pay_per_use',
  price_per_task: 2.5,
  price_per_communication: 0.1,
  status: 'active',
  created_at: '2024-01-01T00:00:00Z',
}

describe('AgentCard', () => {
  it('renders agent name and description', () => {
    render(wrapWithRouter(<AgentCard agent={baseAgent} />))
    expect(screen.getByText('Math Agent')).toBeInTheDocument()
    expect(screen.getByText(/Performs mathematical operations/)).toBeInTheDocument()
  })

  it('displays pay_per_use pricing correctly', () => {
    render(wrapWithRouter(<AgentCard agent={baseAgent} />))
    expect(screen.getByText('$2.50')).toBeInTheDocument()
    expect(screen.getByText('per task')).toBeInTheDocument()
  })

  it('displays monthly pricing when pricing_model is monthly', () => {
    const monthlyAgent: Agent = {
      ...baseAgent,
      pricing_model: 'monthly',
      monthly_price: 29.99,
    }
    render(wrapWithRouter(<AgentCard agent={monthlyAgent} />))
    expect(screen.getByText('$29.99')).toBeInTheDocument()
    expect(screen.getByText('/month')).toBeInTheDocument()
  })

  it('displays quarterly pricing when pricing_model is quarterly', () => {
    const quarterlyAgent: Agent = {
      ...baseAgent,
      pricing_model: 'quarterly',
      quarterly_price: 79.99,
    }
    render(wrapWithRouter(<AgentCard agent={quarterlyAgent} />))
    expect(screen.getByText('$79.99')).toBeInTheDocument()
    expect(screen.getByText('/quarter')).toBeInTheDocument()
  })

  it('shows ACTIVE status badge for active agents', () => {
    render(wrapWithRouter(<AgentCard agent={baseAgent} />))
    expect(screen.getByText('ACTIVE')).toBeInTheDocument()
  })

  it('shows INACTIVE status badge for inactive agents', () => {
    const inactiveAgent: Agent = { ...baseAgent, status: 'inactive' }
    render(wrapWithRouter(<AgentCard agent={inactiveAgent} />))
    expect(screen.getByText('INACTIVE')).toBeInTheDocument()
  })

  it('renders capabilities when provided', () => {
    render(wrapWithRouter(<AgentCard agent={baseAgent} />))
    expect(screen.getByText('addition')).toBeInTheDocument()
    expect(screen.getByText('subtraction')).toBeInTheDocument()
  })

  it('links to agent detail page', () => {
    render(wrapWithRouter(<AgentCard agent={baseAgent} />))
    const link = screen.getByRole('link', { name: /Math Agent/i })
    expect(link).toHaveAttribute('href', '/marketplace/agent/1')
  })

  it('shows fallback when description is missing', () => {
    const agentWithoutDesc: Agent = { ...baseAgent, description: undefined }
    render(wrapWithRouter(<AgentCard agent={agentWithoutDesc} />))
    expect(screen.getByText('No description available')).toBeInTheDocument()
  })

  it('shows "No reviews yet" when agent has no reviews', () => {
    render(wrapWithRouter(<AgentCard agent={baseAgent} />))
    expect(screen.getByText('No reviews yet')).toBeInTheDocument()
  })

  it('shows overall rating and review count when present', () => {
    const agentWithReviews: Agent = {
      ...baseAgent,
      average_rating: 4.2,
      review_count: 12,
    }
    render(wrapWithRouter(<AgentCard agent={agentWithReviews} />))
    expect(screen.getByText('4.2')).toBeInTheDocument()
    expect(screen.getByText('(12 reviews)')).toBeInTheDocument()
  })

  it('shows singular "review" when review_count is 1', () => {
    const agentWithOneReview: Agent = {
      ...baseAgent,
      average_rating: 5,
      review_count: 1,
    }
    render(wrapWithRouter(<AgentCard agent={agentWithOneReview} />))
    expect(screen.getByText('(1 review)')).toBeInTheDocument()
  })
})
