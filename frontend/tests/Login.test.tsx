import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import LoginPage from '../src/pages/Login'
import { useAuthStore } from '../src/lib/store'

vi.mock('../src/lib/store', () => ({
  useAuthStore: vi.fn(),
}))

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

const wrapWithRouter = (ui: React.ReactElement) => (
  <MemoryRouter>{ui}</MemoryRouter>
)

describe('Login Page', () => {
  const mockLogin = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useAuthStore).mockReturnValue({
      login: mockLogin,
      isLoading: false,
      user: null,
      logout: vi.fn(),
      loadUser: vi.fn(),
    } as any)
  })

  it('renders login form', () => {
    render(wrapWithRouter(<LoginPage />))
    expect(screen.getByRole('heading', { name: /login/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /login/i })).toBeInTheDocument()
  })

  it('has link to register page', () => {
    render(wrapWithRouter(<LoginPage />))
    const link = screen.getByRole('link', { name: /sign up/i })
    expect(link).toHaveAttribute('href', '/auth/register')
  })

  it('calls login with email and password on submit', async () => {
    mockLogin.mockResolvedValue(undefined)
    render(wrapWithRouter(<LoginPage />))

    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: 'test@example.com' },
    })
    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: 'password123' },
    })
    fireEvent.click(screen.getByRole('button', { name: /login/i }))

    await waitFor(() => {
      expect(mockLogin).toHaveBeenCalledWith('test@example.com', 'password123')
    })
  })

  it('navigates to dashboard on successful login', async () => {
    mockLogin.mockResolvedValue(undefined)
    render(wrapWithRouter(<LoginPage />))

    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: 'test@example.com' },
    })
    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: 'password123' },
    })
    fireEvent.click(screen.getByRole('button', { name: /login/i }))

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/dashboard')
    })
  })

  it('displays error message on login failure', async () => {
    mockLogin.mockRejectedValue({ response: { data: { detail: 'Invalid credentials' } } })
    render(wrapWithRouter(<LoginPage />))

    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: 'bad@example.com' },
    })
    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: 'wrong' },
    })
    fireEvent.click(screen.getByRole('button', { name: /login/i }))

    await waitFor(() => {
      expect(screen.getByText('Invalid credentials')).toBeInTheDocument()
    })
  })

  it('disables submit button when loading', () => {
    vi.mocked(useAuthStore).mockReturnValue({
      login: mockLogin,
      isLoading: true,
      user: null,
      logout: vi.fn(),
      loadUser: vi.fn(),
    } as any)
    render(wrapWithRouter(<LoginPage />))
    expect(screen.getByRole('button', { name: /logging in/i })).toBeDisabled()
  })
})
