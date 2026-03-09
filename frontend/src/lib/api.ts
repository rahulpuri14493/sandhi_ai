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
  create(payload: { title: string; description?: string; files?: File[] }) {
    const form = new FormData()
    form.append('title', payload.title)
    if (payload.description) form.append('description', payload.description)
    if (payload.files?.length) {
      payload.files.forEach((f) => form.append('files', f))
    }
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
    workflowMode?: 'independent' | 'sequential'
  ) {
    const body: { agent_ids: number[]; workflow_mode?: string } = { agent_ids: agentIds }
    if (workflowMode) body.workflow_mode = workflowMode
    return api.post('/jobs/' + jobId + '/workflow/auto-split', body).then((res) => res.data)
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

export default api
