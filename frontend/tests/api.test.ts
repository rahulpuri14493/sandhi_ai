import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest'

// We mock axios before importing the module under test.
const get = vi.fn()
const post = vi.fn()
const put = vi.fn()
const patch = vi.fn()
const del = vi.fn()

let requestInterceptor: ((config: any) => any) | undefined
let responseErrorInterceptor: ((error: any) => any) | undefined

vi.mock('axios', () => {
  return {
    default: {
      create: vi.fn(() => ({
        get,
        post,
        put,
        patch,
        delete: del,
        interceptors: {
          request: {
            use: (fn: any) => {
              requestInterceptor = fn
            },
          },
          response: {
            use: (_ok: any, err: any) => {
              responseErrorInterceptor = err
            },
          },
        },
      })),
    },
  }
})

let api: any
let authAPI: any
let agentsAPI: any
let jobsAPI: any
let dashboardsAPI: any
let mcpAPI: any
let hiringAPI: any

describe('lib/api', () => {
  beforeAll(async () => {
    const mod = await import('../src/lib/api')
    api = mod.default
    authAPI = mod.authAPI
    agentsAPI = mod.agentsAPI
    jobsAPI = mod.jobsAPI
    dashboardsAPI = mod.dashboardsAPI
    mcpAPI = mod.mcpAPI
    hiringAPI = mod.hiringAPI
  })

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('adds Authorization header when token exists', () => {
    localStorage.setItem('access_token', 't1')
    expect(requestInterceptor).toBeTypeOf('function')

    const config = { headers: {} as Record<string, string> }
    const out = requestInterceptor!(config)
    expect(out.headers.Authorization).toBe('Bearer t1')
  })

  it('request interceptor leaves config unchanged without token', () => {
    const config = { headers: {} as Record<string, string> }
    const out = requestInterceptor!(config)
    expect(out).toBe(config)
    expect(out.headers.Authorization).toBeUndefined()
  })

  it('clears token on 401 responses', async () => {
    localStorage.setItem('access_token', 't1')
    expect(responseErrorInterceptor).toBeTypeOf('function')

    await expect(
      responseErrorInterceptor!({ response: { status: 401 } })
    ).rejects.toBeTruthy()

    expect(localStorage.getItem('access_token')).toBeNull()
  })

  it('response error interceptor passes through non-401 errors', async () => {
    localStorage.setItem('access_token', 't1')
    const err = { response: { status: 500 } }
    await expect(responseErrorInterceptor!(err)).rejects.toBe(err)
    expect(localStorage.getItem('access_token')).toBe('t1')
  })

  it('authAPI.login stores access token', async () => {
    post.mockResolvedValueOnce({ data: { access_token: 'abc', token_type: 'bearer' } })
    await authAPI.login('e@example.com', 'pw')
    expect(localStorage.getItem('access_token')).toBe('abc')
    expect(post).toHaveBeenCalledWith('/auth/login', { email: 'e@example.com', password: 'pw' })
  })

  it('agentsAPI.list builds query string', async () => {
    get.mockResolvedValueOnce({ data: ['ok'] })
    const res = await agentsAPI.list('active', 'tools')
    expect(res).toEqual(['ok'])
    expect(get).toHaveBeenCalledWith('/agents?status=active&capability=tools')
  })

  it('agentsAPI basic CRUD methods call expected endpoints', async () => {
    get.mockResolvedValueOnce({ data: { id: 1 } })
    await agentsAPI.get(1)
    expect(get).toHaveBeenCalledWith('/agents/1')

    put.mockResolvedValueOnce({ data: { ok: true } })
    await agentsAPI.update(2, { name: 'x' })
    expect(put).toHaveBeenCalledWith('/agents/2', { name: 'x' })

    del.mockResolvedValueOnce({ data: { ok: true } })
    await agentsAPI.delete(3)
    expect(del).toHaveBeenCalledWith('/agents/3')
  })

  it('jobsAPI.create uses multipart and includes tool scoping fields', async () => {
    const appendSpy = vi.spyOn(FormData.prototype, 'append')
    post.mockResolvedValueOnce({ data: { id: 1 } })

    await jobsAPI.create({
      title: 't',
      description: 'd',
      allowed_platform_tool_ids: [1, 2],
      allowed_connection_ids: [3],
      tool_visibility: 'names_only',
    })

    expect(appendSpy).toHaveBeenCalledWith('title', 't')
    expect(appendSpy).toHaveBeenCalledWith('description', 'd')
    expect(appendSpy).toHaveBeenCalledWith('allowed_platform_tool_ids', JSON.stringify([1, 2]))
    expect(appendSpy).toHaveBeenCalledWith('allowed_connection_ids', JSON.stringify([3]))
    expect(appendSpy).toHaveBeenCalledWith('tool_visibility', 'names_only')

    expect(post).toHaveBeenCalledWith('/jobs', expect.any(FormData), {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  })

  it('jobsAPI.update and workflow helpers call expected endpoints', async () => {
    post.mockResolvedValueOnce({ data: { ok: true } })
    await jobsAPI.execute(9)
    expect(post).toHaveBeenCalledWith('/jobs/9/execute')

    put.mockResolvedValueOnce({ data: { ok: true } })
    await jobsAPI.update(1, { tool_visibility: 'none', allowed_platform_tool_ids: [1] })
    expect(put).toHaveBeenCalledWith('/jobs/1', expect.any(FormData), {
      headers: { 'Content-Type': 'multipart/form-data' },
    })

    post.mockResolvedValueOnce({ data: { ok: true } })
    await jobsAPI.autoSplitWorkflow(2, [10, 11], 'sequential', [{ agent_index: 0, allowed_platform_tool_ids: [1] }], 'full')
    expect(post).toHaveBeenCalledWith('/jobs/2/workflow/auto-split', {
      agent_ids: [10, 11],
      workflow_mode: 'sequential',
      step_tools: [{ agent_index: 0, allowed_platform_tool_ids: [1] }],
      tool_visibility: 'full',
    })

    patch.mockResolvedValueOnce({ data: { ok: true } })
    await jobsAPI.updateStepTools(2, 7, { tool_visibility: 'names_only' })
    expect(patch).toHaveBeenCalledWith('/jobs/2/workflow/steps/7', { tool_visibility: 'names_only' })
  })

  it('dashboardsAPI calls expected endpoints', async () => {
    get.mockResolvedValue({ data: { ok: true } })
    await dashboardsAPI.getBusinessSpending()
    await dashboardsAPI.getBusinessJobs()
    expect(get).toHaveBeenNthCalledWith(1, '/businesses/spending')
    expect(get).toHaveBeenNthCalledWith(2, '/businesses/jobs')
  })

  it('mcpAPI.getRegistry hits /mcp/registry', async () => {
    get.mockResolvedValueOnce({ data: { platform_tool_count: 0, tools: [], platform_tools: [], connection_tools: [] } })
    const res = await mcpAPI.getRegistry()
    expect(res.platform_tool_count).toBe(0)
    expect(get).toHaveBeenCalledWith('/mcp/registry')
  })

  it('mcpAPI proxy and validateToolConfig call expected endpoints', async () => {
    post.mockResolvedValueOnce({ data: { ok: true } })
    await mcpAPI.proxy(5, 'tools/list', { a: 1 })
    expect(post).toHaveBeenCalledWith('/mcp/proxy', { connection_id: 5, method: 'tools/list', params: { a: 1 } })

    post.mockResolvedValueOnce({ data: { valid: true, message: 'ok' } })
    const res = await mcpAPI.validateToolConfig('postgres', { host: 'x' })
    expect(res.valid).toBe(true)
    expect(post).toHaveBeenCalledWith('/mcp/tools/validate', { tool_type: 'postgres', config: { host: 'x' } })
  })

  it('covers remaining API wrapper methods', async () => {
    get.mockResolvedValue({ data: {} })
    post.mockResolvedValue({ data: {} })
    put.mockResolvedValue({ data: {} })
    patch.mockResolvedValue({ data: {} })
    del.mockResolvedValue({ data: {} })

    // auth
    await authAPI.register('a@b.com', 'pw', 'business')
    await authAPI.getCurrentUser()
    authAPI.logout()

    // agents
    await agentsAPI.getReviewSummary(1)
    await agentsAPI.listReviews(1, 10, 0)
    await agentsAPI.submitReview(1, 5, 'great')
    await agentsAPI.updateReview(1, 2, { rating: 4 })
    await agentsAPI.deleteReview(1, 2)
    await agentsAPI.create({ name: 'x' })
    await agentsAPI.testConnection({ api_endpoint: 'http://x' })

    // jobs
    await jobsAPI.get(1)
    await jobsAPI.delete(1)
    await jobsAPI.getStatus(1)
    await jobsAPI.previewWorkflow(1)
    await jobsAPI.approve(1)
    await jobsAPI.rerun(1)
    await jobsAPI.getShareLink(1)
    await jobsAPI.downloadFile(1, 'f1')
    await jobsAPI.analyzeDocuments(1)
    await jobsAPI.answerQuestion(1, 'a')
    await jobsAPI.generateWorkflowQuestions(1)

    // dashboards
    await dashboardsAPI.getDeveloperEarnings()
    await dashboardsAPI.getDeveloperAgents()
    await dashboardsAPI.getDeveloperStats()

    // hiring & payments
    const { hiringAPI, paymentsAPI } = await import('../src/lib/api')
    await hiringAPI.listPositions('open')
    await hiringAPI.getPosition(1)
    await hiringAPI.createPosition({ title: 't' })
    await hiringAPI.createNomination({ hiring_position_id: 1, agent_id: 2 })
    await hiringAPI.reviewNomination(3, { status: 'approved', review_notes: 'ok' })

    await paymentsAPI.calculate({ a: 1 })
    await paymentsAPI.process({ a: 1 })
    await paymentsAPI.getTransactions()

    // mcp CRUD
    await mcpAPI.listConnections()
    await mcpAPI.createConnection({ name: 'n', base_url: 'http://x' })
    await mcpAPI.updateConnection(1, { is_active: true })
    await mcpAPI.deleteConnection(1)
    await mcpAPI.validateConnection({ name: 'n', base_url: 'http://x' })
    await mcpAPI.listTools()
    await mcpAPI.createTool({ tool_type: 'postgres', name: 'db', config: {} })
    await mcpAPI.updateTool(1, { is_active: true })
    await mcpAPI.refreshToolSchema(1)
    await mcpAPI.deleteTool(1)
    await mcpAPI.getTool(1)
  })

  it('jobsAPI queue, schedules, planner, suggest, cancel, and auto-split output settings', async () => {
    get.mockResolvedValue({ data: {} })
    post.mockResolvedValue({ data: {} })
    put.mockResolvedValue({ data: {} })

    await jobsAPI.getQueueStats()
    expect(get).toHaveBeenCalledWith('/jobs/queue/stats')

    await jobsAPI.listAllSchedules()
    await jobsAPI.getSchedule(3)
    await jobsAPI.createSchedule(3, { scheduled_at: '2026-01-01T00:00:00Z', timezone: 'UTC' })
    await jobsAPI.updateSchedule(3, { status: 'inactive' })
    await jobsAPI.cancel(8)

    await jobsAPI.getAgentPlannerStatus()
    await jobsAPI.listPlannerArtifacts(4)
    await jobsAPI.getPlannerArtifactRaw(4, 12)
    await jobsAPI.getPlannerPipeline(4)
    await jobsAPI.suggestWorkflowTools(4, [1, 2])

    await jobsAPI.autoSplitWorkflow(1, [1], 'sequential', undefined, undefined, {
      write_execution_mode: 'platform',
      output_artifact_format: 'jsonl',
      output_contract: { a: 1 },
    })
    expect(post).toHaveBeenCalledWith(
      '/jobs/1/workflow/auto-split',
      expect.objectContaining({
        agent_ids: [1],
        workflow_mode: 'sequential',
        write_execution_mode: 'platform',
        output_artifact_format: 'jsonl',
        output_contract: { a: 1 },
      })
    )
  })

  it('jobsAPI.create optional schedule and execution output fields', async () => {
    const appendSpy = vi.spyOn(FormData.prototype, 'append')
    post.mockResolvedValueOnce({ data: { id: 1 } })
    await jobsAPI.create({
      title: 't',
      schedule_scheduled_at: '2026-06-01T12:00:00Z',
      schedule_timezone: 'America/New_York',
      write_execution_mode: 'agent',
      output_artifact_format: 'json',
      output_contract: { k: 'v' },
    })
    expect(appendSpy).toHaveBeenCalledWith('schedule_scheduled_at', '2026-06-01T12:00:00Z')
    expect(appendSpy).toHaveBeenCalledWith('schedule_timezone', 'America/New_York')
    expect(appendSpy).toHaveBeenCalledWith('write_execution_mode', 'agent')
    expect(appendSpy).toHaveBeenCalledWith('output_artifact_format', 'json')
    expect(appendSpy).toHaveBeenCalledWith('output_contract', JSON.stringify({ k: 'v' }))
    appendSpy.mockRestore()
  })

  it('mcpAPI certifyConnection, async write, and operation status', async () => {
    post.mockResolvedValueOnce({
      data: { certified: true, checks: [], recommended_policy: 'default' },
    })
    await mcpAPI.certifyConnection(9)
    expect(post).toHaveBeenCalledWith('/mcp/connections/9/certify')

    post.mockResolvedValueOnce({ data: { ok: 1 } })
    await mcpAPI.callPlatformWriteAsync({
      tool_name: 't',
      artifact_ref: { storage: 'local', path: 'p', format: 'jsonl' },
      target: { target_type: 'sql', name: 'n' },
      idempotency_key: 'ik',
    })
    expect(post).toHaveBeenCalledWith('/mcp/call-platform-write-async', expect.any(Object))

    get.mockResolvedValueOnce({ data: { status: 'done' } })
    await mcpAPI.getWriteOperation('op-1')
    expect(get).toHaveBeenCalledWith('/mcp/operations/op-1')
  })

  it('hiringAPI.listPositions without status uses bare path', async () => {
    get.mockResolvedValueOnce({ data: [] })
    await hiringAPI.listPositions()
    expect(get).toHaveBeenCalledWith('/hiring/positions')
  })

  it('exports the configured axios instance', () => {
    // Basic sanity: api object is whatever axios.create returned (mocked).
    expect(api).toBeTruthy()
    expect((api as any).get).toBe(get)
  })
})
