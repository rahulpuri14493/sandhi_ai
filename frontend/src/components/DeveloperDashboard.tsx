import { useEffect, useState } from 'react'
import { dashboardsAPI, agentsAPI } from '../lib/api'
import type { Agent, Earnings } from '../lib/types'
import { EarningsChart } from './EarningsChart'
import { Link, useNavigate } from 'react-router-dom'

export function DeveloperDashboard() {
  const navigate = useNavigate()
  const [earnings, setEarnings] = useState({
    total_earnings: 0,
    pending_earnings: 0,
    recent_earnings: [] as Earnings[],
  })
  const [agents, setAgents] = useState<Agent[]>([])
  const [stats, setStats] = useState({
    agent_count: 0,
    total_tasks: 0,
    total_communications: 0,
  })
  const [isLoading, setIsLoading] = useState(true)
  const [deletingId, setDeletingId] = useState<number | null>(null)

  useEffect(() => {
    loadData()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadData = async () => {
    setIsLoading(true)
    try {
      const [earningsData, agentsData, statsData] = await Promise.all([
        dashboardsAPI.getDeveloperEarnings(),
        dashboardsAPI.getDeveloperAgents(),
        dashboardsAPI.getDeveloperStats(),
      ])
      setEarnings(earningsData)
      setAgents(agentsData)
      setStats(statsData)
    } catch (error) {
      console.error('Failed to load dashboard data:', error)
    } finally {
      setIsLoading(false)
    }
  }

  const handleDelete = async (agentId: number) => {
    if (!window.confirm('Are you sure you want to delete this agent? This action cannot be undone.')) {
      return
    }
    
    setDeletingId(agentId)
    try {
      await agentsAPI.delete(agentId)
      // Reload agents list
      const agentsData = await dashboardsAPI.getDeveloperAgents()
      setAgents(agentsData)
      // Reload stats
      const statsData = await dashboardsAPI.getDeveloperStats()
      setStats(statsData)
    } catch (error: any) {
      alert(error.response?.data?.detail || 'Failed to delete agent')
    } finally {
      setDeletingId(null)
    }
  }

  const handleEdit = (agentId: number) => {
    navigate(`/agents/edit/${agentId}`)
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center">
          <div className="inline-block animate-spin rounded-full h-12 w-12 border-4 border-primary-500 border-t-transparent mb-4"></div>
          <p className="text-white/60 text-lg font-semibold">Loading dashboard...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="animate-fadeIn min-h-screen">
      <div className="flex items-center justify-between mb-12">
        <div>
          <h1 className="text-6xl font-black text-white tracking-tight mb-2">
            Developer Dashboard
          </h1>
          <p className="text-white/60 text-lg font-medium">Manage your agents and track earnings</p>
        </div>
      </div>

      <div className="grid md:grid-cols-3 gap-6 mb-10">
        <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50 card-hover">
          <div className="flex items-center justify-between mb-6">
            <div className="p-4 bg-gradient-to-br from-green-500 to-green-700 rounded-2xl shadow-lg">
              <svg className="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
          </div>
          <h2 className="text-sm font-semibold text-white/60 mb-2 uppercase tracking-wider">Total Earnings</h2>
          <p className="text-5xl font-black text-white mb-2">
            ${earnings.total_earnings.toFixed(2)}
          </p>
          <p className="text-xs text-white/40 font-medium">All-time earnings</p>
        </div>
        <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50 card-hover">
          <div className="flex items-center justify-between mb-6">
            <div className="p-4 bg-gradient-to-br from-yellow-500 to-yellow-700 rounded-2xl shadow-lg">
              <svg className="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
          </div>
          <h2 className="text-sm font-semibold text-white/60 mb-2 uppercase tracking-wider">Pending Earnings</h2>
          <p className="text-5xl font-black text-white mb-2">
            ${earnings.pending_earnings.toFixed(2)}
          </p>
          <p className="text-xs text-white/40 font-medium">Awaiting payment</p>
        </div>
        <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50 card-hover">
          <div className="flex items-center justify-between mb-6">
            <div className="p-4 bg-gradient-to-br from-primary-500 to-primary-700 rounded-2xl shadow-lg">
              <svg className="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
              </svg>
            </div>
          </div>
          <h2 className="text-sm font-semibold text-white/60 mb-2 uppercase tracking-wider">Agents Published</h2>
          <p className="text-5xl font-black text-white mb-2">{stats.agent_count}</p>
          <p className="text-xs text-white/40 font-medium">Active agents</p>
        </div>
      </div>

      <div className="grid md:grid-cols-2 gap-6 mb-10">
        <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50">
          <h2 className="text-2xl font-black text-white mb-6">Earnings Over Time</h2>
          <EarningsChart earnings={earnings.recent_earnings} />
        </div>
        <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50">
          <h2 className="text-2xl font-black text-white mb-6">Statistics</h2>
          <div className="space-y-4">
            <div className="flex justify-between items-center p-4 bg-dark-200/30 rounded-xl border border-dark-300">
              <span className="text-white/70 font-medium">Total Tasks:</span>
              <span className="font-black text-white text-xl">{stats.total_tasks}</span>
            </div>
            <div className="flex justify-between items-center p-4 bg-dark-200/30 rounded-xl border border-dark-300">
              <span className="text-white/70 font-medium">Total Communications:</span>
              <span className="font-black text-white text-xl">{stats.total_communications}</span>
            </div>
          </div>
        </div>
      </div>

      <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50">
        <div className="flex justify-between items-center mb-6">
          <h2 className="text-3xl font-black text-white tracking-tight">My Agents</h2>
          <Link
            to="/agents/new"
            className="bg-gradient-to-r from-primary-500 to-primary-700 text-white px-6 py-3 rounded-xl font-bold hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200"
          >
            Publish New Agent
          </Link>
        </div>
        {agents.length === 0 ? (
          <div className="text-center py-12">
            <p className="text-white/60 text-lg font-semibold">No agents published yet</p>
            <Link
              to="/agents/new"
              className="inline-block mt-4 text-primary-400 hover:text-primary-300 font-bold text-lg transition-colors"
            >
              Publish your first agent →
            </Link>
          </div>
        ) : (
          <div className="space-y-4">
            {agents.map((agent) => (
              <div
                key={agent.id}
                className="p-6 border border-dark-200/50 rounded-2xl hover:border-primary-500/50 hover:shadow-2xl transition-all duration-200 bg-dark-200/30 backdrop-blur-sm"
              >
                <div className="flex justify-between items-start">
                  <div className="flex-1">
                    <h3 className="font-black text-xl text-white mb-2">{agent.name}</h3>
                    {agent.description && (
                      <p className="text-sm text-white/70 mt-2 font-medium">{agent.description}</p>
                    )}
                    {agent.api_endpoint && (
                      <p className="text-xs text-white/50 mt-2 font-medium">
                        API: {agent.api_endpoint.substring(0, 50)}{agent.api_endpoint.length > 50 ? '...' : ''}
                      </p>
                    )}
                    {agent.api_key && (
                      <p className="text-xs text-green-400 mt-2 font-semibold">✓ API Key configured</p>
                    )}
                  </div>
                  <div className="flex items-center gap-4 ml-6">
                    <div className="text-right">
                      <p className="font-black text-2xl text-white">${agent.price_per_task.toFixed(2)}</p>
                      <span className={`px-3 py-1.5 rounded-full text-xs font-bold border mt-2 inline-block ${
                        agent.status === 'active'
                          ? 'bg-green-500/20 text-green-400 border-green-500/50'
                          : agent.status === 'pending'
                          ? 'bg-yellow-500/20 text-yellow-400 border-yellow-500/50'
                          : 'bg-dark-200/50 text-white/60 border-dark-300'
                      }`}>
                        {agent.status.toUpperCase()}
                      </span>
                    </div>
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleEdit(agent.id)}
                        className="px-4 py-2 bg-primary-500/20 text-primary-400 border border-primary-500/50 text-sm rounded-xl font-bold hover:bg-primary-500/30 transition-all duration-200"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => handleDelete(agent.id)}
                        disabled={deletingId === agent.id}
                        className="px-4 py-2 bg-red-500/20 text-red-400 border border-red-500/50 text-sm rounded-xl font-bold hover:bg-red-500/30 transition-all duration-200 disabled:opacity-50"
                      >
                        {deletingId === agent.id ? 'Deleting...' : 'Delete'}
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
