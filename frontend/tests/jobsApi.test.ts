import { describe, it, expect, vi, beforeEach } from 'vitest'

const { mockGet, mockPost, mockPatch, mockPut, mockDelete } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockPatch: vi.fn(),
  mockPut: vi.fn(),
  mockDelete: vi.fn(),
}))

vi.mock('axios', () => ({
  default: {
    create: () => ({
      get: mockGet,
      post: mockPost,
      patch: mockPatch,
      put: mockPut,
      delete: mockDelete,
      interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    }),
  },
}))

import { jobsAPI } from '../src/lib/api'

describe('jobsAPI', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGet.mockResolvedValue({ data: {} })
    mockPost.mockResolvedValue({ data: {} })
    mockPatch.mockResolvedValue({ data: {} })
    mockPut.mockResolvedValue({ data: {} })
    mockDelete.mockResolvedValue(undefined)
  })

  describe('create', () => {
    it('sends title and description in FormData', async () => {
      await jobsAPI.create({ title: 'Job', description: 'Desc' })
      expect(mockPost).toHaveBeenCalledWith(
        '/jobs',
        expect.any(FormData),
        expect.objectContaining({ headers: expect.objectContaining({ 'Content-Type': 'multipart/form-data' }) })
      )
      const form = mockPost.mock.calls[0][1] as FormData
      expect(form.get('title')).toBe('Job')
      expect(form.get('description')).toBe('Desc')
    })

    it('sends allowed_platform_tool_ids and allowed_connection_ids and tool_visibility when provided', async () => {
      await jobsAPI.create({
        title: 'Scoped',
        allowed_platform_tool_ids: [10, 20],
        allowed_connection_ids: [5],
        tool_visibility: 'names_only',
      })
      const form = mockPost.mock.calls[0][1] as FormData
      expect(form.get('title')).toBe('Scoped')
      expect(form.get('allowed_platform_tool_ids')).toBe(JSON.stringify([10, 20]))
      expect(form.get('allowed_connection_ids')).toBe(JSON.stringify([5]))
      expect(form.get('tool_visibility')).toBe('names_only')
    })

    it('does not append tool_visibility when not provided', async () => {
      await jobsAPI.create({ title: 'Minimal' })
      const form = mockPost.mock.calls[0][1] as FormData
      expect(form.has('tool_visibility')).toBe(false)
    })
  })

  describe('autoSplitWorkflow', () => {
    it('sends agent_ids and optional tool_visibility and step_tools', async () => {
      await jobsAPI.autoSplitWorkflow(
        1,
        [2, 3],
        'sequential',
        [{ agent_index: 0, tool_visibility: 'none' }],
        'names_only'
      )
      expect(mockPost).toHaveBeenCalledWith(
        '/jobs/1/workflow/auto-split',
        expect.objectContaining({
          agent_ids: [2, 3],
          workflow_mode: 'sequential',
          tool_visibility: 'names_only',
          step_tools: [{ agent_index: 0, tool_visibility: 'none' }],
        })
      )
    })

    it('sends only agent_ids when no optional params', async () => {
      await jobsAPI.autoSplitWorkflow(1, [2])
      expect(mockPost).toHaveBeenCalledWith('/jobs/1/workflow/auto-split', { agent_ids: [2] })
    })
  })

  describe('updateStepTools', () => {
    it('sends tool_visibility in PATCH body', async () => {
      await jobsAPI.updateStepTools(1, 10, { tool_visibility: 'none' })
      expect(mockPatch).toHaveBeenCalledWith('/jobs/1/workflow/steps/10', { tool_visibility: 'none' })
    })

    it('sends allowed_platform_tool_ids and allowed_connection_ids when provided', async () => {
      await jobsAPI.updateStepTools(1, 10, {
        allowed_platform_tool_ids: [1, 2],
        allowed_connection_ids: [3],
        tool_visibility: 'full',
      })
      expect(mockPatch).toHaveBeenCalledWith('/jobs/1/workflow/steps/10', {
        allowed_platform_tool_ids: [1, 2],
        allowed_connection_ids: [3],
        tool_visibility: 'full',
      })
    })
  })

  describe('getPlannerPipeline', () => {
    it('GETs composed planner bundle', async () => {
      const bundle = {
        schema_version: 'planner_pipeline.v1',
        job_id: 9,
        brd_analysis: null,
        task_split: null,
        tool_suggestion: null,
        artifact_ids: {},
      }
      mockGet.mockResolvedValueOnce({ data: bundle })
      const out = await jobsAPI.getPlannerPipeline(9)
      expect(mockGet).toHaveBeenCalledWith('/jobs/9/planner-pipeline')
      expect(out).toEqual(bundle)
    })
  })
})
