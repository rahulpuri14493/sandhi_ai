import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useAuthStore } from '../src/lib/store'
import { authAPI } from '../src/lib/api'

vi.mock('../src/lib/api', () => ({
  authAPI: {
    login: vi.fn(),
    logout: vi.fn(() => localStorage.removeItem('token')),
    getCurrentUser: vi.fn(),
  },
}))

describe('Auth Store', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAuthStore.setState({ user: null })
    localStorage.clear()
  })

  it('starts with null user', () => {
    const state = useAuthStore.getState()
    expect(state.user).toBeNull()
  })

  it('logout clears user and calls authAPI.logout', () => {
    useAuthStore.setState({
      user: {
        id: 1,
        email: 'test@example.com',
        role: 'business',
        created_at: '2024-01-01',
      },
    })
    useAuthStore.getState().logout()
    expect(useAuthStore.getState().user).toBeNull()
    expect(authAPI.logout).toHaveBeenCalledTimes(1)
  })

  it('logout removes token from localStorage', () => {
    localStorage.setItem('token', 'fake-token')
    useAuthStore.getState().logout()
    expect(localStorage.getItem('token')).toBeNull()
  })

  it('login sets isLoading during request', async () => {
    vi.mocked(authAPI.login).mockResolvedValue({ access_token: 'token' })
    vi.mocked(authAPI.getCurrentUser).mockResolvedValue({
      id: 1,
      email: 'test@example.com',
      role: 'business',
      created_at: '2024-01-01',
    })

    const loginPromise = useAuthStore.getState().login('test@example.com', 'password')
    expect(useAuthStore.getState().isLoading).toBe(true)
    await loginPromise
    expect(useAuthStore.getState().isLoading).toBe(false)
  })

  it('login sets user on success', async () => {
    const mockUser = {
      id: 1,
      email: 'test@example.com',
      role: 'business',
      created_at: '2024-01-01',
    }
    vi.mocked(authAPI.login).mockResolvedValue({ access_token: 'token' })
    vi.mocked(authAPI.getCurrentUser).mockResolvedValue(mockUser)

    await useAuthStore.getState().login('test@example.com', 'password')
    expect(useAuthStore.getState().user).toEqual(mockUser)
  })

  it('login throws on error', async () => {
    vi.mocked(authAPI.login).mockRejectedValue(new Error('Invalid credentials'))
    await expect(
      useAuthStore.getState().login('bad@example.com', 'wrong')
    ).rejects.toThrow()
  })

  it('loadUser sets user when token exists', async () => {
    localStorage.setItem('token', 'valid-token')
    const mockUser = {
      id: 1,
      email: 'test@example.com',
      role: 'developer',
      created_at: '2024-01-01',
    }
    vi.mocked(authAPI.getCurrentUser).mockResolvedValue(mockUser)

    await useAuthStore.getState().loadUser()
    expect(useAuthStore.getState().user).toEqual(mockUser)
  })

  it('loadUser sets user to null when no token', async () => {
    vi.mocked(authAPI.getCurrentUser).mockResolvedValue(null)
    await useAuthStore.getState().loadUser()
    expect(useAuthStore.getState().user).toBeNull()
  })
})
