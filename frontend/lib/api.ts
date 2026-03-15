/// <reference types="node" />
import axios from 'axios';
import type { User, Agent, Job, WorkflowPreview, Transaction, Earnings } from './types';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const api = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add auth token to requests
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Auth API
export const authAPI = {
  register: async (email: string, password: string, role: 'business' | 'developer') => {
    const response = await api.post('/api/auth/register', { email, password, role });
    return response.data;
  },
  login: async (email: string, password: string) => {
    const response = await api.post('/api/auth/login', { email, password });
    localStorage.setItem('token', response.data.access_token);
    return response.data;
  },
  logout: () => {
    localStorage.removeItem('token');
  },
  getCurrentUser: async (): Promise<User> => {
    const response = await api.get('/api/auth/me');
    return response.data;
  },
};

// Agents API
export const agentsAPI = {
  list: async (status?: string, capability?: string): Promise<Agent[]> => {
    const params: any = {};
    if (status) params.status = status;
    if (capability) params.capability = capability;
    const response = await api.get('/api/agents', { params });
    return response.data;
  },
  get: async (id: number): Promise<Agent> => {
    const response = await api.get(`/api/agents/${id}`);
    return response.data;
  },
  create: async (agent: Partial<Agent>): Promise<Agent> => {
    const response = await api.post('/api/agents', agent);
    return response.data;
  },
  update: async (id: number, agent: Partial<Agent>): Promise<Agent> => {
    const response = await api.put(`/api/agents/${id}`, agent);
    return response.data;
  },
  delete: async (id: number): Promise<void> => {
    await api.delete(`/api/agents/${id}`);
  },
};

// Jobs API
export const jobsAPI = {
  create: async (job: { title: string; description?: string }): Promise<Job> => {
    const response = await api.post('/api/jobs', job);
    return response.data;
  },
  list: async (): Promise<Job[]> => {
    const response = await api.get('/api/jobs');
    return response.data;
  },
  get: async (id: number): Promise<Job> => {
    const response = await api.get(`/api/jobs/${id}`);
    return response.data;
  },
  autoSplitWorkflow: async (jobId: number, agentIds: number[]): Promise<WorkflowPreview> => {
    const response = await api.post(`/api/jobs/${jobId}/workflow/auto-split`, agentIds);
    return response.data;
  },
  manualWorkflow: async (jobId: number, workflowSteps: any[]): Promise<WorkflowPreview> => {
    const response = await api.post(`/api/jobs/${jobId}/workflow/manual`, workflowSteps);
    return response.data;
  },
  previewWorkflow: async (jobId: number): Promise<WorkflowPreview> => {
    const response = await api.get(`/api/jobs/${jobId}/workflow/preview`);
    return response.data;
  },
  approve: async (jobId: number): Promise<Job> => {
    const response = await api.post(`/api/jobs/${jobId}/approve`);
    return response.data;
  },
  execute: async (jobId: number): Promise<Job> => {
    const response = await api.post(`/api/jobs/${jobId}/execute`);
    return response.data;
  },
  getStatus: async (jobId: number): Promise<Job> => {
    const response = await api.get(`/api/jobs/${jobId}/status`);
    return response.data;
  },
};

// Payments API
export const paymentsAPI = {
  calculate: async (jobId: number): Promise<WorkflowPreview> => {
    const response = await api.post('/api/payments/calculate', null, { params: { job_id: jobId } });
    return response.data;
  },
  process: async (jobId: number): Promise<Transaction> => {
    const response = await api.post('/api/payments/process', null, { params: { job_id: jobId } });
    return response.data;
  },
  listTransactions: async (): Promise<Transaction[]> => {
    const response = await api.get('/api/payments/transactions');
    return response.data;
  },
};

// Dashboards API
export const dashboardsAPI = {
  getDeveloperEarnings: async (): Promise<{
    total_earnings: number;
    pending_earnings: number;
    recent_earnings: Earnings[];
  }> => {
    const response = await api.get('/api/developers/earnings');
    return response.data;
  },
  getDeveloperAgents: async (): Promise<Agent[]> => {
    const response = await api.get('/api/developers/agents');
    return response.data;
  },
  getDeveloperStats: async (): Promise<{
    agent_count: number;
    total_tasks: number;
    total_communications: number;
  }> => {
    const response = await api.get('/api/developers/stats');
    return response.data;
  },
  getBusinessJobs: async (): Promise<Job[]> => {
    const response = await api.get('/api/businesses/jobs');
    return response.data;
  },
  getBusinessSpending: async (): Promise<{
    total_spent: number;
    job_count: number;
  }> => {
    const response = await api.get('/api/businesses/spending');
    return response.data;
  },
};
