import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import HomePage from '../src/pages/Home'

vi.mock('../src/components/HiringPositionsList', () => ({
  HiringPositionsList: () => <div data-testid="hirings-mock">Open positions (mock)</div>,
}))

const wrap = (ui: React.ReactElement) => <MemoryRouter>{ui}</MemoryRouter>

describe('Home page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders hero and primary links', () => {
    render(wrap(<HomePage />))
    expect(screen.getByRole('heading', { name: /^Sandhi AI$/i })).toBeInTheDocument()
    expect(
      screen.getByText(/Hire AI agents to get work done/i)
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Browse Agents/i })).toHaveAttribute('href', '/marketplace')
    expect(screen.getByRole('link', { name: /Get Started/i })).toHaveAttribute('href', '/auth/register')
  })

  it('switches to Hirings tab and shows hiring list region', () => {
    render(wrap(<HomePage />))
    fireEvent.click(screen.getByRole('button', { name: /^Hirings$/i }))
    expect(screen.getByTestId('hirings-mock')).toBeInTheDocument()
  })
})
