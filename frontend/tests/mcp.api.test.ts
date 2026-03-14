import { describe, it, expect, vi, beforeEach } from 'vitest'

const { mockGet, mockPost, mockPatch, mockDelete } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockPatch: vi.fn(),
  mockDelete: vi.fn(),
}))

vi.mock('axios', () => ({
  default: {
    create: () => ({
      get: mockGet,
      post: mockPost,
      patch: mockPatch,
      delete: mockDelete,
      interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    }),
  },
}))

import { mcpAPI } from '../src/lib/api'

describe('mcpAPI', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGet.mockResolvedValue({ data: [] })
    mockPost.mockResolvedValue({ data: {} })
    mockPatch.mockResolvedValue({ data: {} })
    mockDelete.mockResolvedValue(undefined)
  })

  describe('listConnections', () => {
    it('calls GET /mcp/connections and returns data', async () => {
      const connections = [{ id: 1, name: 'Test', base_url: 'https://mcp.test', endpoint_path: '/mcp', user_id: 1, auth_type: 'none', is_platform_configured: false, is_active: true, created_at: '', updated_at: '' }]
      mockGet.mockResolvedValueOnce({ data: connections })
      const result = await mcpAPI.listConnections()
      expect(mockGet).toHaveBeenCalledWith('/mcp/connections')
      expect(result).toEqual(connections)
    })
  })

  describe('createConnection', () => {
    it('calls POST /mcp/connections with payload', async () => {
      const payload = { name: 'My MCP', base_url: 'https://mcp.example.com', endpoint_path: '/mcp', auth_type: 'none' }
      const created = { id: 1, ...payload, user_id: 1, is_platform_configured: false, is_active: true, created_at: '', updated_at: '' }
      mockPost.mockResolvedValueOnce({ data: created })
      await mcpAPI.createConnection(payload)
      expect(mockPost).toHaveBeenCalledWith('/mcp/connections', payload)
    })
  })

  describe('listTools', () => {
    it('calls GET /mcp/tools and returns data', async () => {
      const tools = [{ id: 1, user_id: 1, tool_type: 'postgres', name: 'My DB', is_active: true, created_at: '', updated_at: '' }]
      mockGet.mockResolvedValueOnce({ data: tools })
      const result = await mcpAPI.listTools()
      expect(mockGet).toHaveBeenCalledWith('/mcp/tools')
      expect(result).toEqual(tools)
    })
  })

  describe('createTool', () => {
    it('calls POST /mcp/tools with tool_type, name, config', async () => {
      const payload = { tool_type: 'postgres', name: 'Prod DB', config: { connection_string: 'postgresql://x/y' } }
      const created = { id: 1, user_id: 1, tool_type: 'postgres', name: 'Prod DB', is_active: true, created_at: '', updated_at: '' }
      mockPost.mockResolvedValueOnce({ data: created })
      await mcpAPI.createTool(payload)
      expect(mockPost).toHaveBeenCalledWith('/mcp/tools', payload)
    })
  })

  describe('updateTool', () => {
    it('calls PATCH /mcp/tools/:id with partial payload', async () => {
      mockPatch.mockResolvedValueOnce({ data: { id: 1, name: 'Updated', tool_type: 'postgres', user_id: 1, is_active: true, created_at: '', updated_at: '' } })
      await mcpAPI.updateTool(1, { name: 'Updated' })
      expect(mockPatch).toHaveBeenCalledWith('/mcp/tools/1', { name: 'Updated' })
    })
  })

  describe('deleteTool', () => {
    it('calls DELETE /mcp/tools/:id', async () => {
      await mcpAPI.deleteTool(1)
      expect(mockDelete).toHaveBeenCalledWith('/mcp/tools/1')
    })
  })

  describe('validateToolConfig', () => {
    it('calls POST /mcp/tools/validate with tool_type and config', async () => {
      mockPost.mockResolvedValueOnce({ data: { valid: true, message: 'OK' } })
      const result = await mcpAPI.validateToolConfig('postgres', { connection_string: 'postgresql://x/y' })
      expect(mockPost).toHaveBeenCalledWith('/mcp/tools/validate', { tool_type: 'postgres', config: { connection_string: 'postgresql://x/y' } })
      expect(result).toEqual({ valid: true, message: 'OK' })
    })
  })

  describe('getRegistry', () => {
    it('calls GET /mcp/registry and returns tools array', async () => {
      const registry = { tools: [{ name: 'platform_1_MyDB', source: 'platform', tool_type: 'postgres' }], platform_tool_count: 1 }
      mockGet.mockResolvedValueOnce({ data: registry })
      const result = await mcpAPI.getRegistry()
      expect(mockGet).toHaveBeenCalledWith('/mcp/registry')
      expect(result.tools).toHaveLength(1)
    })
  })

  describe('getTool', () => {
    it('calls GET /mcp/tools/:id and returns tool', async () => {
      const tool = { id: 1, user_id: 1, tool_type: 'chroma', name: 'My Chroma', is_active: true, created_at: '', updated_at: '' }
      mockGet.mockResolvedValueOnce({ data: tool })
      const result = await mcpAPI.getTool(1)
      expect(mockGet).toHaveBeenCalledWith('/mcp/tools/1')
      expect(result).toEqual(tool)
    })
  })

  describe('refreshToolSchema', () => {
    it('calls POST /mcp/tools/:id/refresh-schema', async () => {
      mockPost.mockResolvedValueOnce({ data: { success: true, message: 'Schema refreshed: 3 table(s)', table_count: 3 } })
      const result = await mcpAPI.refreshToolSchema(2)
      expect(mockPost).toHaveBeenCalledWith('/mcp/tools/2/refresh-schema')
      expect(result.table_count).toBe(3)
    })
  })

  describe('validateConnection', () => {
    it('calls POST /mcp/connections/validate with payload', async () => {
      mockPost.mockResolvedValueOnce({ data: { valid: true, message: 'MCP server connection successful' } })
      const result = await mcpAPI.validateConnection({
        name: 'Test',
        base_url: 'https://mcp.example.com',
        endpoint_path: '/mcp',
        auth_type: 'none',
      })
      expect(mockPost).toHaveBeenCalledWith('/mcp/connections/validate', expect.any(Object))
      expect(result.valid).toBe(true)
    })
  })

  describe('updateConnection', () => {
    it('calls PATCH /mcp/connections/:id', async () => {
      const updated = { id: 1, name: 'Updated MCP', base_url: 'https://mcp.example.com', endpoint_path: '/mcp', user_id: 1, auth_type: 'none', is_platform_configured: false, is_active: true, created_at: '', updated_at: '' }
      mockPatch.mockResolvedValueOnce({ data: updated })
      await mcpAPI.updateConnection(1, { name: 'Updated MCP' })
      expect(mockPatch).toHaveBeenCalledWith('/mcp/connections/1', { name: 'Updated MCP' })
    })
  })

  describe('deleteConnection', () => {
    it('calls DELETE /mcp/connections/:id', async () => {
      await mcpAPI.deleteConnection(1)
      expect(mockDelete).toHaveBeenCalledWith('/mcp/connections/1')
    })
  })

  describe('proxy', () => {
    it('calls POST /mcp/proxy with connection_id, method, params', async () => {
      mockPost.mockResolvedValueOnce({ data: { jsonrpc: '2.0', id: 1, result: {} } })
      await mcpAPI.proxy(1, 'tools/list', {})
      expect(mockPost).toHaveBeenCalledWith('/mcp/proxy', { connection_id: 1, method: 'tools/list', params: {} })
    })
  })
})
