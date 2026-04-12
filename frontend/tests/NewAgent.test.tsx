import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import NewAgentPage from '../src/pages/NewAgent'

vi.mock('../src/lib/api', () => ({
  agentsAPI: {
    create: vi.fn(),
    testConnection: vi.fn(),
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

import { agentsAPI } from '../src/lib/api'

const wrapWithRouter = (ui: React.ReactElement) => <MemoryRouter>{ui}</MemoryRouter>

describe('NewAgent Page (developer publish)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders publish new agent form', () => {
    render(wrapWithRouter(<NewAgentPage />))
    expect(screen.getByRole('heading', { name: /publish new agent/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/^Agent Name$/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /publish agent/i })).toBeInTheDocument()
  })

  it('calls create without status, then navigates to dashboard', async () => {
    vi.mocked(agentsAPI.create).mockResolvedValue({
      id: 1,
      name: 'E2E Published Agent',
      developer_id: 99,
      status: 'pending',
    })

    render(wrapWithRouter(<NewAgentPage />))

    fireEvent.change(screen.getByLabelText(/^Agent Name$/i), {
      target: { value: 'E2E Published Agent' },
    })
    fireEvent.change(screen.getByLabelText(/^Description$/i), {
      target: { value: 'From frontend test' },
    })

    fireEvent.click(screen.getByRole('button', { name: /publish agent/i }))

    await waitFor(() => {
      expect(agentsAPI.create).toHaveBeenCalledTimes(1)
    })

    const payload = vi.mocked(agentsAPI.create).mock.calls[0][0] as Record<string, unknown>
    expect(payload).not.toHaveProperty('status')
    expect(payload.name).toBe('E2E Published Agent')
    expect(payload.description).toBe('From frontend test')
    expect(payload.pricing_model).toBe('pay_per_use')
    expect(payload.a2a_enabled).toBe(false)

    expect(mockNavigate).toHaveBeenCalledWith('/dashboard')
  })

  it('when api_endpoint is set but untested, confirm true still omits status from payload', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.mocked(agentsAPI.create).mockResolvedValue({ id: 2, name: 'With Endpoint' })

    try {
      render(wrapWithRouter(<NewAgentPage />))

      fireEvent.change(screen.getByLabelText(/^Agent Name$/i), {
        target: { value: 'With Endpoint' },
      })
      fireEvent.change(screen.getByLabelText(/^API Endpoint/i), {
        target: { value: 'https://api.example.com/v1/chat/completions' },
      })

      fireEvent.click(screen.getByRole('button', { name: /publish agent/i }))

      await waitFor(() => {
        expect(confirmSpy).toHaveBeenCalled()
      })
      await waitFor(() => {
        expect(agentsAPI.create).toHaveBeenCalled()
      })

      const payload = vi.mocked(agentsAPI.create).mock.calls[0][0] as Record<string, unknown>
      expect(payload).not.toHaveProperty('status')
      expect(payload.api_endpoint).toBe('https://api.example.com/v1/chat/completions')
    } finally {
      confirmSpy.mockRestore()
    }
  })

  it('shows error when create fails', async () => {
    vi.mocked(agentsAPI.create).mockRejectedValue({
      response: { data: { detail: 'Validation failed' } },
    })

    render(wrapWithRouter(<NewAgentPage />))
    fireEvent.change(screen.getByLabelText(/^Agent Name$/i), {
      target: { value: 'Bad Agent' },
    })
    fireEvent.click(screen.getByRole('button', { name: /publish agent/i }))

    await waitFor(() => {
      expect(screen.getByText('Validation failed')).toBeInTheDocument()
    })
    expect(mockNavigate).not.toHaveBeenCalled()
  })
})
