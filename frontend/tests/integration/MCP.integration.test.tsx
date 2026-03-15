/**
 * Integration tests for MCP Server page: load with tools and connections, sections and data.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import MCPPage from '../../src/pages/MCP'

const mockListConnections = vi.fn()
const mockListTools = vi.fn()
const mockGetRegistry = vi.fn()

vi.mock('../../src/lib/api', () => ({
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
    refreshToolSchema: vi.fn(),
    validateConnection: vi.fn(),
    validateToolConfig: vi.fn(),
  },
}))
vi.mock('../../src/lib/store', () => ({
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

describe('MCP integration', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockListConnections.mockResolvedValue([])
    mockListTools.mockResolvedValue([])
    mockGetRegistry.mockResolvedValue({ tools: [], platform_tool_count: 0 })
  })

  it('loads MCP page and shows all main sections', async () => {
    render(wrap(<MCPPage />))
    await waitFor(() => {
      expect(screen.getByText('MCP Server')).toBeInTheDocument()
    })
    expect(screen.getByText('Your configured items')).toBeInTheDocument()
    expect(screen.getByText('Platform tools')).toBeInTheDocument()
    expect(screen.getByText('MCP connections')).toBeInTheDocument()
    expect(screen.getByText('Configure platform tools')).toBeInTheDocument()
    expect(screen.getByText('I have an MCP Server')).toBeInTheDocument()
  })

  it('shows platform tools and connections when API returns data', async () => {
    mockListTools.mockResolvedValue([
      { id: 1, user_id: 1, tool_type: 'postgres', name: 'Prod DB', is_active: true, created_at: '', updated_at: '' },
      { id: 2, user_id: 1, tool_type: 'chroma', name: 'Docs Index', is_active: true, created_at: '', updated_at: '' },
    ])
    mockListConnections.mockResolvedValue([
      { id: 1, user_id: 1, name: 'Dev MCP', base_url: 'https://mcp.dev.example.com', endpoint_path: '/mcp', auth_type: 'none', is_platform_configured: false, is_active: true, created_at: '', updated_at: '' },
    ])
    mockGetRegistry.mockResolvedValue({
      tools: [
        { name: 'platform_1_Prod_DB', source: 'platform', tool_type: 'postgres' },
        { name: 'platform_2_Docs_Index', source: 'platform', tool_type: 'chroma' },
        { name: 'Dev MCP', source: 'external' },
      ],
      platform_tool_count: 2,
    })
    render(wrap(<MCPPage />))
    await waitFor(() => {
      expect(screen.getByText('Prod DB')).toBeInTheDocument()
      expect(screen.getByText('Docs Index')).toBeInTheDocument()
      expect(screen.getByText('Dev MCP')).toBeInTheDocument()
    })
    expect(screen.getByText('PostgreSQL')).toBeInTheDocument()
    expect(screen.getByText('Chroma')).toBeInTheDocument()
  })

  it('calls listConnections, listTools, and getRegistry on mount', async () => {
    render(wrap(<MCPPage />))
    await waitFor(() => {
      expect(screen.getByText('MCP Server')).toBeInTheDocument()
    })
    expect(mockListConnections).toHaveBeenCalled()
    expect(mockListTools).toHaveBeenCalled()
    expect(mockGetRegistry).toHaveBeenCalled()
  })
})
