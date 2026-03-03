import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Navbar } from '../src/components/Navbar'
import { useAuthStore } from '../src/lib/store'

vi.mock('../src/lib/store', () => ({
  useAuthStore: vi.fn(),
}))

const wrapWithRouter = (ui: React.ReactElement) => (
  <MemoryRouter>{ui}</MemoryRouter>
)

describe('Navbar', () => {
  beforeEach(() => {
    vi.mocked(useAuthStore).mockReturnValue({
      user: null,
      logout: vi.fn(),
      loadUser: vi.fn(),
    } as any)
  })

  it('renders logo/brand', () => {
    render(wrapWithRouter(<Navbar />))
    expect(screen.getByAltText('Sandhi AI')).toBeInTheDocument()
  })

  it('shows login link when user is null', () => {
    render(wrapWithRouter(<Navbar />))
    expect(screen.getByRole('link', { name: /login/i })).toBeInTheDocument()
  })

  it('shows user email and logout when user is logged in', () => {
    vi.mocked(useAuthStore).mockReturnValue({
      user: { id: 1, email: 'test@example.com', role: 'business', created_at: '' },
      logout: vi.fn(),
      loadUser: vi.fn(),
    } as any)
    render(wrapWithRouter(<Navbar />))
    expect(screen.getByText(/test@example.com/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /logout/i })).toBeInTheDocument()
  })

  it('calls logout when logout button is clicked', () => {
    const logout = vi.fn()
    vi.mocked(useAuthStore).mockReturnValue({
      user: { id: 1, email: 'test@example.com', role: 'business', created_at: '' },
      logout,
      loadUser: vi.fn(),
    } as any)
    render(wrapWithRouter(<Navbar />))
    screen.getByRole('button', { name: /logout/i }).click()
    expect(logout).toHaveBeenCalledTimes(1)
  })
})
