import axios from 'axios'
import type { User } from './types'

const API_BASE = '/api'

const api = axios.create({
  baseURL: API_BASE,
  headers: { 'Content-Type': 'application/json' },
})

const TOKEN_KEY = 'access_token'

api.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    config.headers.Authorization = 'Bearer ' + token
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
    }
    return Promise.reject(error)
  }
)

export const authAPI = {
  async register(email: string, password: string, role: string) {
    await api.post('/auth/register', { email, password, role })
  },
  async login(email: string, password: string) {
    const { data } = await api.post<{ access_token: string; token_type: string }>('/auth/login', { email, password })
    localStorage.setItem(TOKEN_KEY, data.access_token)
  },
  getCurrentUser(): Promise<User | null> {
    return api.get<User>('/auth/me').then((res) => res.data).catch(() => null)
  },
  logout() {
    localStorage.removeItem(TOKEN_KEY)
  },
}
export const agentsAPI = {
  list(status?: string, capability?: string) {
    const params = new URLSearchParams()
    if (status) params.set('status', status)
    if (capability) params.set('capability', capability)
    const q = params.toString()
    return api.get('/agents' + (q ? '?' + q : '')).then((res) => res.data)
  },
  get(agentId: number) {
    return api.get('/agents/' + agentId).then((res) => res.data)
  },
  getReviewSummary(agentId: number) {
    return api.get('/agents/' + agentId + '/reviews/summary').then((res) => res.data)
  },
  listReviews(agentId: number, limit: number, offset: number) {
    return api.get('/agents/' + agentId + '/reviews?limit=' + limit + '&offset=' + offset).then((res) => res.data)
  },
  submitReview(agentId: number, rating: number, review_text?: string) {
    return api.post('/agents/' + agentId + '/reviews', { rating, review_text }).then((res) => res.data)
  },
  updateReview(agentId: number, reviewId: number, payload: { rating?: number; review_text?: string }) {
    return api.put('/agents/' + agentId + '/reviews/' + reviewId, payload).then((res) => res.data)
  },
  deleteReview(agentId: number, reviewId: number) {
    return api.delete('/agents/' + agentId + '/reviews/' + reviewId)
  },
  create(data: Record<string, unknown>) {
    return api.post('/agents', data).then((res) => res.data)
  },
  update(agentId: number, data: Record<string, unknown>) {
    return api.put('/agents/' + agentId, data).then((res) => res.data)
  },
  delete(agentId: number) {
    return api.delete('/agents/' + agentId)
  },
  testConnection(params: { api_endpoint: string; api_key?: string; test_data?: object; llm_model?: string; temperature?: number; a2a_enabled?: boolean }) {
    return api.post('/agents/test-connection', params).then((res) => res.data)
  },
}
export const jobsAPI = {
  create(payload: {
    title: string
    description?: string
    files?: File[]
    allowed_platform_tool_ids?: number[]
    allowed_connection_ids?: number[]
    tool_visibility?: 'full' | 'names_only' | 'none'
    write_execution_mode?: 'platform' | 'agent'
    output_artifact_format?: 'jsonl' | 'json'
    output_contract?: Record<string, unknown>
    schedule_timezone?: string
    schedule_scheduled_at?: string
  }) {
    const form = new FormData()
    form.append('title', payload.title)
    if (payload.description) form.append('description', payload.description)
    if (payload.schedule_scheduled_at) {
      form.append('schedule_scheduled_at', payload.schedule_scheduled_at)
      if (payload.schedule_timezone) form.append('schedule_timezone', payload.schedule_timezone)
    }
    if (payload.files?.length) {
      payload.files.forEach((f) => form.append('files', f))
    }
    if (payload.allowed_platform_tool_ids !== undefined && Array.isArray(payload.allowed_platform_tool_ids)) {
      form.append('allowed_platform_tool_ids', JSON.stringify(payload.allowed_platform_tool_ids))
    }
    if (payload.allowed_connection_ids !== undefined && Array.isArray(payload.allowed_connection_ids)) {
      form.append('allowed_connection_ids', JSON.stringify(payload.allowed_connection_ids))
    }
    if (payload.tool_visibility) form.append('tool_visibility', payload.tool_visibility)
    if (payload.write_execution_mode) form.append('write_execution_mode', payload.write_execution_mode)
    if (payload.output_artifact_format) form.append('output_artifact_format', payload.output_artifact_format)
    if (payload.output_contract) form.append('output_contract', JSON.stringify(payload.output_contract))
    return api.post('/jobs', form, { headers: { 'Content-Type': 'multipart/form-data' } }).then((res) => res.data)
  },
  get(jobId: number) {
    return api.get('/jobs/' + jobId).then((res) => res.data)
  },
  update(jobId: number, data: Record<string, unknown>, files?: File[]) {
    const form = new FormData()
    Object.entries(data).forEach(([k, v]) => {
      if (v !== undefined && v !== null) form.append(k, typeof v === 'string' ? v : JSON.stringify(v))
    })
    if (files?.length) files.forEach((f) => form.append('files', f))
    return api.put('/jobs/' + jobId, form, { headers: { 'Content-Type': 'multipart/form-data' } }).then((res) => res.data)
  },
  delete(jobId: number) {
    return api.delete('/jobs/' + jobId)
  },
  getStatus(jobId: number) {
    return api.get('/jobs/' + jobId + '/status').then((res) => res.data)
  },
  getQueueStats() {
    return api.get('/jobs/queue/stats').then((res) => res.data)
  },
  previewWorkflow(jobId: number) {
    return api.get('/jobs/' + jobId + '/workflow/preview').then((res) => res.data)
  },
  approve(jobId: number) {
    return api.post('/jobs/' + jobId + '/approve').then((res) => res.data)
  },
  execute(jobId: number) {
    return api.post('/jobs/' + jobId + '/execute').then((res) => res.data)
  },
  rerun(jobId: number) {
    return api.post('/jobs/' + jobId + '/rerun').then((res) => res.data)
  },
  getShareLink(jobId: number) {
    return api.get('/jobs/' + jobId + '/share-link').then((res) => res.data)
  },
  downloadFile(jobId: number, fileId: string) {
    return api.get('/jobs/' + jobId + '/files/' + fileId, { responseType: 'blob' }).then((res) => res.data)
  },
  listAllSchedules() {
    return api.get('/jobs/schedules/all').then((res) => res.data)
  },
  getSchedule(jobId: number) {
    return api.get('/jobs/' + jobId + '/schedule').then((res) => res.data)
  },
  createSchedule(jobId: number, payload: {
    scheduled_at: string
    timezone?: string
    status?: string
  }) {
    return api.post('/jobs/' + jobId + '/schedule', payload).then((res) => res.data)
  },
  updateSchedule(jobId: number, payload: {
    scheduled_at?: string
    timezone?: string
    status?: string
  }) {
    return api.put('/jobs/' + jobId + '/schedule', payload).then((res) => res.data)
  },
  cancel(jobId: number) {
    return api.post('/jobs/' + jobId + '/cancel').then((res) => res.data)
  },
  analyzeDocuments(jobId: number) {
    return api.post('/jobs/' + jobId + '/analyze-documents').then((res) => res.data)
  },
  answerQuestion(jobId: number, answer: string) {
    return api.post('/jobs/' + jobId + '/answer-question', { answer }).then((res) => res.data)
  },
  generateWorkflowQuestions(jobId: number) {
    return api.post('/jobs/' + jobId + '/generate-workflow-questions').then((res) => res.data)
  },
  autoSplitWorkflow(
    jobId: number,
    agentIds: number[],
    workflowMode?: 'independent' | 'sequential',
    stepTools?: Array<{ agent_index: number; allowed_platform_tool_ids?: number[]; allowed_connection_ids?: number[]; tool_visibility?: 'full' | 'names_only' | 'none' }>,
    toolVisibility?: 'full' | 'names_only' | 'none'
  ) {
    const body: {
      agent_ids: number[]
      workflow_mode?: string
      step_tools?: Array<{ agent_index: number; allowed_platform_tool_ids?: number[]; allowed_connection_ids?: number[]; tool_visibility?: string }>
      tool_visibility?: string
    } = { agent_ids: agentIds }
    if (workflowMode) body.workflow_mode = workflowMode
    if (stepTools?.length) body.step_tools = stepTools
    if (toolVisibility) body.tool_visibility = toolVisibility
    return api.post('/jobs/' + jobId + '/workflow/auto-split', body).then((res) => res.data)
  },
  updateStepTools(
    jobId: number,
    stepId: number,
    payload: { allowed_platform_tool_ids?: number[]; allowed_connection_ids?: number[]; tool_visibility?: 'full' | 'names_only' | 'none' }
  ) {
    return api.patch('/jobs/' + jobId + '/workflow/steps/' + stepId, payload).then((res) => res.data)
  },
}
export const dashboardsAPI = {
  getBusinessSpending() {
    return api.get('/businesses/spending').then((res) => res.data)
  },
  getBusinessJobs() {
    return api.get('/businesses/jobs').then((res) => res.data)
  },
  getDeveloperEarnings() {
    return api.get('/developers/earnings').then((res) => res.data)
  },
  getDeveloperAgents() {
    return api.get('/developers/agents').then((res) => res.data)
  },
  getDeveloperStats() {
    return api.get('/developers/stats').then((res) => res.data)
  },
}

export const hiringAPI = {
  listPositions(status?: string) {
    const q = status ? '?status=' + encodeURIComponent(status) : ''
    return api.get('/hiring/positions' + q).then((res) => res.data)
  },
  getPosition(positionId: number) {
    return api.get('/hiring/positions/' + positionId).then((res) => res.data)
  },
  createPosition(data: { title: string; description?: string; requirements?: string }) {
    return api.post('/hiring/positions', data).then((res) => res.data)
  },
  createNomination(data: { hiring_position_id: number; agent_id: number; cover_letter?: string }) {
    return api.post('/hiring/nominations', data).then((res) => res.data)
  },
  reviewNomination(nominationId: number, data: { status: 'approved' | 'rejected'; review_notes?: string }) {
    return api.put('/hiring/nominations/' + nominationId + '/review', data).then((res) => res.data)
  },
}

export const paymentsAPI = {
  calculate(payload: unknown) {
    return api.post('/payments/calculate', payload).then((res) => res.data)
  },
  process(payload: unknown) {
    return api.post('/payments/process', payload).then((res) => res.data)
  },
  getTransactions() {
    return api.get('/payments/transactions').then((res) => res.data)
  },
}

export interface MCPServerConnectionRes {
  id: number
  user_id: number
  name: string
  base_url: string
  endpoint_path: string
  auth_type: string
  is_platform_configured: boolean
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface MCPToolConfigRes {
  id: number
  user_id: number
  tool_type: string
  name: string
  is_active: boolean
  business_description?: string | null
  schema_metadata?: string | null
  schema_table_count?: number | null
  created_at: string
  updated_at: string
}

export const mcpAPI = {
  listConnections() {
    return api.get<MCPServerConnectionRes[]>('/mcp/connections').then((res) => res.data)
  },
  createConnection(data: { name: string; base_url: string; endpoint_path?: string; auth_type?: string; credentials?: Record<string, string> }) {
    return api.post<MCPServerConnectionRes>('/mcp/connections', data).then((res) => res.data)
  },
  updateConnection(id: number, data: Partial<{ name: string; base_url: string; endpoint_path: string; auth_type: string; credentials: Record<string, string>; is_active: boolean }>) {
    return api.patch<MCPServerConnectionRes>('/mcp/connections/' + id, data).then((res) => res.data)
  },
  deleteConnection(id: number) {
    return api.delete('/mcp/connections/' + id)
  },
  validateConnection(data: { name: string; base_url: string; endpoint_path?: string; auth_type?: string; credentials?: Record<string, string> }) {
    return api.post<{ valid: boolean; message: string }>('/mcp/connections/validate', data).then((res) => res.data)
  },
  certifyConnection(connectionId: number) {
    return api.post<{
      certified: boolean
      checks: Array<{ name: string; passed: boolean; error?: string; tool_count?: number; write_tool_count?: number }>
      recommended_policy: string
    }>('/mcp/connections/' + connectionId + '/certify').then((res) => res.data)
  },
  listTools() {
    return api.get<MCPToolConfigRes[]>('/mcp/tools').then((res) => res.data)
  },
  createTool(data: { tool_type: string; name: string; config: Record<string, unknown>; business_description?: string | null }) {
    return api.post<MCPToolConfigRes>('/mcp/tools', data).then((res) => res.data)
  },
  updateTool(id: number, data: Partial<{ name: string; config: Record<string, unknown>; business_description?: string | null; is_active: boolean }>) {
    return api.patch<MCPToolConfigRes>('/mcp/tools/' + id, data).then((res) => res.data)
  },
  refreshToolSchema(id: number) {
    return api.post<{ success: boolean; message: string; table_count: number }>('/mcp/tools/' + id + '/refresh-schema').then((res) => res.data)
  },
  deleteTool(id: number) {
    return api.delete('/mcp/tools/' + id)
  },
  proxy(connectionId: number, method: string, params?: Record<string, unknown>) {
    return api.post('/mcp/proxy', { connection_id: connectionId, method, params }).then((res) => res.data)
  },
  getRegistry() {
    return api.get<{
      tools: Array<{
        source: string
        name: string
        tool_type?: string
        description?: string
        id?: number
        connection_id?: number
        base_url?: string
      }>;
      platform_tools: Array<{
        source: string
        id?: number
        name: string
        tool_type?: string
        description?: string
      }>;
      connection_tools: Array<{
        connection_id: number
        name: string
        base_url: string
        tools: Array<{ name: string; description?: string }>;
        error?: string
      }>;
      platform_tool_count: number
    }>('/mcp/registry').then((res) => res.data)
  },
  validateToolConfig(tool_type: string, config: Record<string, unknown>) {
    return api.post<{ valid: boolean; message: string }>('/mcp/tools/validate', { tool_type, config }).then((res) => res.data)
  },
  getTool(toolId: number) {
    return api.get<MCPToolConfigRes>('/mcp/tools/' + toolId).then((res) => res.data)
  },
  callPlatformWriteAsync(payload: {
    tool_name: string
    artifact_ref: { storage: string; path: string; format: string; checksum?: string }
    target: { target_type: string; name: string; database?: string; schema?: string; table?: string; bucket?: string; prefix?: string }
    operation_type?: 'insert' | 'update' | 'upsert' | 'merge'
    write_mode?: 'append' | 'overwrite' | 'upsert' | 'merge'
    merge_keys?: string[]
    idempotency_key: string
    options?: Record<string, unknown>
    timeout_seconds?: number
  }) {
    return api.post('/mcp/call-platform-write-async', payload).then((res) => res.data)
  },
  getWriteOperation(operationId: string) {
    return api.get('/mcp/operations/' + operationId).then((res) => res.data)
  },
}

export default api
