/**
 * Unit/component tests for MCP Server page.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import MCPPage from '../src/pages/MCP'

const mockListConnections = vi.fn()
const mockListTools = vi.fn()
const mockGetRegistry = vi.fn()

vi.mock('../src/lib/api', () => ({
  mcpAPI: {
    listConnections: () => mockListConnections(),
    listTools: () => mockListTools(),
    getRegistry: () => mockGetRegistry(),
    createConnection: vi.fn(),
    updateConnection: vi.fn(),
    deleteConnection: vi.fn(),
    createTool: vi.fn(),
    updateTool: vi.fn(),
    deleteTool: vi.fn(),
    validateToolConfig: vi.fn(),
    getTool: vi.fn(),
    proxy: vi.fn(),
  },
}))
vi.mock('../src/lib/store', () => ({
  useAuthStore: () => ({
    user: { id: 1, email: 'business@test.com', role: 'business', created_at: '' },
    loadUser: vi.fn().mockResolvedValue(undefined),
  }),
}))

const wrap = (ui: React.ReactElement) => (
  <MemoryRouter>
    <Routes>
      <Route path="/" element={ui} />
    </Routes>
  </MemoryRouter>
)

describe('MCP page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockListConnections.mockResolvedValue([])
    mockListTools.mockResolvedValue([])
    mockGetRegistry.mockResolvedValue({ tools: [], platform_tool_count: 0 })
  })

  it('renders MCP Server title and subtitle', async () => {
    render(wrap(<MCPPage />))
    await waitFor(() => {
      expect(screen.getByText('MCP Server')).toBeInTheDocument()
    })
    expect(screen.getByText(/View your configured tools and connections below/i)).toBeInTheDocument()
  })

  it('shows Your configured items section', async () => {
    render(wrap(<MCPPage />))
    await waitFor(() => {
      expect(screen.getByText('Your configured items')).toBeInTheDocument()
    })
    expect(screen.getByText('Platform tools')).toBeInTheDocument()
    expect(screen.getByText('MCP connections')).toBeInTheDocument()
  })

  it('shows empty state when no tools or connections', async () => {
    render(wrap(<MCPPage />))
    await waitFor(() => {
      expect(screen.getByText('No tools configured yet.')).toBeInTheDocument()
    })
    expect(screen.getByText('No MCP servers connected yet.')).toBeInTheDocument()
  })

  it('shows I have an MCP Server and Configure platform tools cards', async () => {
    render(wrap(<MCPPage />))
    await waitFor(() => {
      expect(screen.getByText('I have an MCP Server')).toBeInTheDocument()
    })
    expect(screen.getByText('Configure platform tools')).toBeInTheDocument()
  })

  it('calls listConnections and listTools on load', async () => {
    render(wrap(<MCPPage />))
    await waitFor(() => {
      expect(screen.getByText('MCP Server')).toBeInTheDocument()
    })
    expect(mockListConnections).toHaveBeenCalled()
    expect(mockListTools).toHaveBeenCalled()
    expect(mockGetRegistry).toHaveBeenCalled()
  })

  it('displays configured tools in the list when present', async () => {
    mockListTools.mockResolvedValue([
      { id: 1, user_id: 1, tool_type: 'postgres', name: 'Local DB', is_active: true, created_at: '', updated_at: '' },
    ])
    mockListConnections.mockResolvedValue([])
    render(wrap(<MCPPage />))
    await waitFor(() => {
      expect(screen.getByText('Local DB')).toBeInTheDocument()
    })
    expect(screen.getByText('PostgreSQL')).toBeInTheDocument()
  })

  it('displays configured connections in the list when present', async () => {
    mockListConnections.mockResolvedValue([
      { id: 1, user_id: 1, name: 'My MCP', base_url: 'https://mcp.example.com', endpoint_path: '/mcp', auth_type: 'none', is_platform_configured: false, is_active: true, created_at: '', updated_at: '' },
    ])
    mockListTools.mockResolvedValue([])
    render(wrap(<MCPPage />))
    await waitFor(() => {
      expect(screen.getByText('My MCP')).toBeInTheDocument()
    })
  })
})
