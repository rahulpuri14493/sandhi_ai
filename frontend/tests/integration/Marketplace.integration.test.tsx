/**
 * Integration tests: Marketplace page (list agents, filters, link to job create).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import MarketplacePage from '../../src/pages/Marketplace'

const mockAgents = [
  {
    id: 1,
    name: 'Math Agent',
    description: 'Does math',
    status: 'active',
    price_per_task: 5,
    price_per_communication: 0.5,
    developer_id: 1,
    capabilities: [],
    pricing_model: 'pay_per_use',
    created_at: '2024-01-01',
  },
  {
    id: 2,
    name: 'Doc Agent',
    description: 'Analyzes documents',
    status: 'active',
    price_per_task: 10,
    price_per_communication: 1,
    developer_id: 1,
    capabilities: [],
    pricing_model: 'pay_per_use',
    created_at: '2024-01-02',
  },
]

vi.mock('../../src/lib/api', () => ({
  agentsAPI: {
    list: vi.fn(),
  },
}))

import { agentsAPI } from '../../src/lib/api'

const wrap = (ui: React.ReactElement) => (
  <MemoryRouter>{ui}</MemoryRouter>
)

describe('Marketplace integration', () => {
  beforeEach(() => {
    vi.mocked(agentsAPI.list).mockResolvedValue(mockAgents)
  })

  it('loads and displays agents from API', async () => {
    render(wrap(<MarketplacePage />))
    expect(agentsAPI.list).toHaveBeenCalled()
    await screen.findByText('Math Agent')
    expect(screen.getByText('Doc Agent')).toBeInTheDocument()
    expect(screen.getByText('Sandhi AI Marketplace')).toBeInTheDocument()
  })

  it('has Create New Job link', async () => {
    render(wrap(<MarketplacePage />))
    await screen.findByText('Math Agent')
    const link = screen.getByRole('link', { name: /Create New Job/i })
    expect(link).toHaveAttribute('href', '/jobs/new')
  })

  it('shows no agents message when API returns empty', async () => {
    vi.mocked(agentsAPI.list).mockResolvedValue([])
    render(wrap(<MarketplacePage />))
    await screen.findByText('No agents found')
    expect(screen.getByText('No agents found')).toBeInTheDocument()
  })

  it('has status filter and capability filter', async () => {
    render(wrap(<MarketplacePage />))
    await screen.findByText('Math Agent')
    expect(screen.getByRole('combobox')).toBeInTheDocument()
    expect(screen.getByText('All Status')).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Filter by capability/i)).toBeInTheDocument()
  })
})
