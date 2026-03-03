import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import RegisterPage from '../src/pages/Register'

vi.mock('../src/lib/api', () => ({
  authAPI: {
    register: vi.fn(),
  },
}))

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

import { authAPI } from '../src/lib/api'

const wrapWithRouter = (ui: React.ReactElement) => (
  <MemoryRouter>{ui}</MemoryRouter>
)

describe('Register Page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders sign up form', () => {
    render(wrapWithRouter(<RegisterPage />))
    expect(screen.getByRole('heading', { name: /sign up/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign up/i })).toBeInTheDocument()
  })

  it('has link to login page', () => {
    render(wrapWithRouter(<RegisterPage />))
    const link = screen.getByRole('link', { name: /login/i })
    expect(link).toHaveAttribute('href', '/auth/login')
  })

  it('has role selector', () => {
    render(wrapWithRouter(<RegisterPage />))
    expect(screen.getByLabelText(/i am a/i)).toBeInTheDocument()
    const select = screen.getByRole('combobox')
    expect(select).toHaveValue('business')
  })

  it('calls register with email, password, and role on submit', async () => {
    vi.mocked(authAPI.register).mockResolvedValue(undefined)
    render(wrapWithRouter(<RegisterPage />))

    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: 'newuser@test.com' },
    })
    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: 'password123' },
    })
    fireEvent.change(screen.getByRole('combobox'), {
      target: { value: 'developer' },
    })
    fireEvent.click(screen.getByRole('button', { name: /sign up/i }))

    await waitFor(() => {
      expect(authAPI.register).toHaveBeenCalledWith('newuser@test.com', 'password123', 'developer')
    })
  })

  it('navigates to login on successful registration', async () => {
    vi.mocked(authAPI.register).mockResolvedValue(undefined)
    render(wrapWithRouter(<RegisterPage />))

    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: 'user@test.com' },
    })
    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: 'pass123' },
    })
    fireEvent.click(screen.getByRole('button', { name: /sign up/i }))

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/auth/login')
    })
  })

  it('displays error message on registration failure', async () => {
    vi.mocked(authAPI.register).mockRejectedValue({
      response: { data: { detail: 'Email already registered' } },
    })
    render(wrapWithRouter(<RegisterPage />))

    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: 'existing@test.com' },
    })
    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: 'pass123' },
    })
    fireEvent.click(screen.getByRole('button', { name: /sign up/i }))

    await waitFor(() => {
      expect(screen.getByText('Email already registered')).toBeInTheDocument()
    })
  })
})
