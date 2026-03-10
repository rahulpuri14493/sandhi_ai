/**
 * Integration tests: Hiring positions (list, create) with mocked API.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import NewHiringPositionPage from '../../src/pages/NewHiringPosition'

vi.mock('../../src/lib/api', () => ({
  hiringAPI: {
    listPositions: vi.fn().mockResolvedValue([]),
    createPosition: vi.fn().mockResolvedValue({ id: 1, title: 'New Role', description: '', requirements: '' }),
  },
}))
vi.mock('../../src/lib/store', () => ({
  useAuthStore: () => ({ user: { id: 1, role: 'business' }, loadUser: vi.fn() }),
}))

import { hiringAPI } from '../../src/lib/api'

const wrap = (ui: React.ReactElement) => (
  <MemoryRouter initialEntries={['/hiring/new']}>
    <Routes>
      <Route path="/hiring/new" element={ui} />
    </Routes>
  </MemoryRouter>
)

describe('NewHiringPosition integration', () => {
  beforeEach(() => {
    vi.mocked(hiringAPI.createPosition).mockResolvedValue({
      id: 1,
      title: 'E2E Position',
      description: '',
      requirements: '',
    })
  })

  it('renders hiring position form', () => {
    render(wrap(<NewHiringPositionPage />))
    expect(screen.getByText(/Post New Hiring Position/i)).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Enter position title/i)).toBeInTheDocument()
  })

  it('calls createPosition on submit when form valid', async () => {
    render(wrap(<NewHiringPositionPage />))
    const titleInput = screen.getByPlaceholderText(/Enter position title/i)
    const requirementsInput = screen.getByPlaceholderText(/List the specific roles/i)
    fireEvent.change(titleInput, { target: { value: 'E2E Role' } })
    fireEvent.change(requirementsInput, { target: { value: 'Must have 2+ years experience.' } })
    const submit = screen.getByRole('button', { name: /Post Position/i })
    fireEvent.click(submit)
    await waitFor(() => {
      expect(hiringAPI.createPosition).toHaveBeenCalledWith(expect.objectContaining({ title: 'E2E Role' }))
    })
  })
})
