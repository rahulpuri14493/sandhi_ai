/**
 * Tests for EditJob page: overwrite UX, file upload labels, and info text.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import EditJobPage from '../src/pages/EditJob'

// Mock API modules so the component can mount without real network calls.
vi.mock('../src/lib/api', () => ({
  jobsAPI: {
    get: vi.fn(),
    update: vi.fn(),
    analyzeDocuments: vi.fn(),
    autoSplitWorkflow: vi.fn(),
    previewWorkflow: vi.fn(),
  },
  agentsAPI: { list: vi.fn() },
  mcpAPI: {
    listTools: vi.fn(),
    listConnections: vi.fn(),
  },
}))

vi.mock('../src/lib/store', () => ({
  useAuthStore: () => ({ user: { id: 1, role: 'business' }, loadUser: vi.fn() }),
}))

import { jobsAPI, agentsAPI, mcpAPI } from '../src/lib/api'

const wrap = (ui: React.ReactElement) => (
  <MemoryRouter initialEntries={['/jobs/1/edit']}>
    <Routes>
      <Route path="/jobs/:id/edit" element={ui} />
    </Routes>
  </MemoryRouter>
)

const mockJob = {
  id: 1,
  title: 'Test Job',
  description: 'Desc',
  status: 'draft',
  files: [{ id: 'f1', name: 'old.txt', type: 'text/plain', size: 100 }],
  workflow_steps: [],
  allowed_platform_tool_ids: [],
  allowed_connection_ids: [],
  tool_visibility: 'none',
}

describe('EditJob overwrite UX', () => {
  beforeEach(() => {
    vi.mocked(jobsAPI.get).mockResolvedValue(mockJob)
    vi.mocked(agentsAPI.list).mockResolvedValue([])
    vi.mocked(mcpAPI.listTools).mockResolvedValue([])
    vi.mocked(mcpAPI.listConnections).mockResolvedValue([])
  })

  it('renders the overwrite upload label', async () => {
    render(wrap(<EditJobPage />))
    await waitFor(() => {
      expect(screen.getByText(/Upload New Documents \(overwrite existing\)/i)).toBeTruthy()
    })
  })

  it('shows the overwrite info text', async () => {
    render(wrap(<EditJobPage />))
    await waitFor(() => {
      expect(
        screen.getByText(/Uploading new files will replace existing BRD documents for this job/i)
      ).toBeTruthy()
    })
  })

  it('displays existing file names from the loaded job', async () => {
    render(wrap(<EditJobPage />))
    await waitFor(() => {
      expect(screen.getByText('old.txt')).toBeTruthy()
    })
  })
})
