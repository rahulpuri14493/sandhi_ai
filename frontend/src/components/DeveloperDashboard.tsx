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
  const [agentPerf, setAgentPerf] = useState<{
    kpis?: {
      overview?: {
        steps?: number
        completed_steps?: number
        failed_steps?: number
        in_progress_steps?: number
        success_rate?: number
        failure_rate?: number
        output_tokens_reported?: number
        cost_total?: number
      }
      latency_seconds?: { samples?: number; avg?: number; p50?: number; p95?: number }
      windows?: {
        last_7d?: { steps?: number; success_rate?: number; failure_rate?: number }
        last_30d?: { steps?: number; success_rate?: number; failure_rate?: number }
      }
      failure_mix?: Array<{ reason: string; count: number }>
      risk?: { stuck_steps?: number; loop_signals?: number; drift_signals?: number; retry_signals?: number; timeout_signals?: number }
      efficiency?: { output_tokens_per_completed_step?: number; cost_per_completed_step?: number }
      sla?: {
        status?: 'healthy' | 'at_risk' | 'breached'
        success_rate_min?: number
        p95_latency_seconds_max?: number
        current_success_rate?: number
        current_p95_latency_seconds?: number
        reason?: string
      }
      alerts?: { last_alert_sent_at?: string | null; last_alert_status?: string | null }
    }
    agents?: Array<{
      agent_id: number
      agent_name: string
      api_endpoint?: string
      totals: {
        steps: number
        completed_steps: number
        failed_steps: number
        in_progress_steps: number
        cost: number
        output_tokens: number
      }
      quality?: { success_rate?: number; failure_rate?: number }
      latest_runtime?: { phase?: string; reason_code?: string; status?: string; job_title?: string }
      latency_seconds?: { samples?: number; avg?: number; p50?: number; p95?: number }
      sla?: { status?: 'healthy' | 'at_risk' | 'breached'; reason?: string }
      recent_failures?: Array<{ job_id?: number; job_title?: string; workflow_step_id?: number; step_order?: number; reason_code?: string; failed_at?: string }>
    }>
  } | null>(null)
  const [agentPerfSearch, setAgentPerfSearch] = useState('')
  const [agentPerfSort, setAgentPerfSort] = useState<'failures' | 'success' | 'latency_p95' | 'tokens'>('failures')
  const [selectedAgentId, setSelectedAgentId] = useState<number | null>(null)

  useEffect(() => {
    loadData()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadData = async () => {
    setIsLoading(true)
    try {
      const [earningsData, agentsData, statsData, perfData] = await Promise.all([
        dashboardsAPI.getDeveloperEarnings(),
        dashboardsAPI.getDeveloperAgents(),
        dashboardsAPI.getDeveloperStats(),
        dashboardsAPI.getDeveloperAgentPerformance(800),
      ])
      setEarnings(earningsData)
      setAgents(agentsData)
      setStats(statsData)
      setAgentPerf(perfData)
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
      const [agentsData, statsData, perfData] = await Promise.all([
        dashboardsAPI.getDeveloperAgents(),
        dashboardsAPI.getDeveloperStats(),
        dashboardsAPI.getDeveloperAgentPerformance(800),
      ])
      setAgents(agentsData)
      setStats(statsData)
      setAgentPerf(perfData)
    } catch (error: any) {
      alert(error.response?.data?.detail || 'Failed to delete agent')
    } finally {
      setDeletingId(null)
    }
  }

  const handleEdit = (agentId: number) => {
    navigate(`/agents/edit/${agentId}`)
  }

  const statusDotClass = (status?: string | null) => {
    const s = (status || '').toLowerCase()
    if (s === 'healthy' || s === 'ok' || s === 'completed') return 'bg-green-400'
    if (s === 'at_risk' || s === 'warning') return 'bg-amber-400'
    if (s === 'breached' || s === 'failed' || s === 'error') return 'bg-red-400'
    return 'bg-slate-400'
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

      <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50 mb-10">
        <h2 className="text-3xl font-black text-white tracking-tight mb-2">Published Agent Performance</h2>
        <p className="text-white/60 text-sm mb-6">Endpoint-level reliability and usage metrics for your published agents</p>
        <div className="grid md:grid-cols-4 gap-4 mb-4">
          <div className="rounded-xl border border-dark-300 bg-dark-200/30 p-4">
            <p className="text-xs text-white/60 uppercase">Success Rate</p>
            <p className="text-2xl font-black text-green-300">{(((agentPerf?.kpis?.overview?.success_rate || 0) * 100)).toFixed(1)}%</p>
          </div>
          <div className="rounded-xl border border-dark-300 bg-dark-200/30 p-4">
            <p className="text-xs text-white/60 uppercase">Latency p50 / p95</p>
            <p className="text-xl font-black text-white">
              {(agentPerf?.kpis?.latency_seconds?.p50 || 0).toFixed(1)}s / {(agentPerf?.kpis?.latency_seconds?.p95 || 0).toFixed(1)}s
            </p>
          </div>
          <div className="rounded-xl border border-dark-300 bg-dark-200/30 p-4">
            <p className="text-xs text-white/60 uppercase">Output Tokens</p>
            <p className="text-2xl font-black text-white">{(agentPerf?.kpis?.overview?.output_tokens_reported || 0).toLocaleString()}</p>
            <p className="text-[10px] text-white/45 mt-1">Strict reported completion/output tokens</p>
          </div>
          <div className="rounded-xl border border-dark-300 bg-dark-200/30 p-4">
            <p className="text-xs text-white/60 uppercase">SLA Status</p>
            <p
              className={`text-sm font-black ${
                (agentPerf?.kpis?.sla?.status || 'healthy') === 'healthy'
                  ? 'text-green-300'
                  : (agentPerf?.kpis?.sla?.status || 'healthy') === 'at_risk'
                    ? 'text-amber-300'
                    : 'text-red-300'
              }`}
            >
              <span className="inline-flex items-center gap-2">
                <span className={`h-2 w-2 rounded-full ${statusDotClass(agentPerf?.kpis?.sla?.status || 'healthy')}`} />
                {(agentPerf?.kpis?.sla?.status || 'healthy').replace('_', ' ').toUpperCase()}
              </span>
            </p>
            <p className="text-[10px] text-white/45 mt-1">
              Target: {(Number(agentPerf?.kpis?.sla?.success_rate_min || 0) * 100).toFixed(1)}% success, p95 &lt;= {(agentPerf?.kpis?.sla?.p95_latency_seconds_max || 0).toFixed(0)}s
            </p>
            {(agentPerf?.kpis?.sla?.reason || '').trim() !== '' && (
              <p className="text-[10px] text-amber-200/90 mt-1">
                Reason: {agentPerf?.kpis?.sla?.reason}
              </p>
            )}
            <p className="text-[10px] text-white/45 mt-1">
              Last alert:{' '}
              {agentPerf?.kpis?.alerts?.last_alert_sent_at ? (
                <span className="inline-flex items-center gap-1">
                  <span className={`h-1.5 w-1.5 rounded-full ${statusDotClass(agentPerf?.kpis?.alerts?.last_alert_status || 'unknown')}`} />
                  {`${new Date(agentPerf.kpis.alerts.last_alert_sent_at).toLocaleString()} (${(agentPerf?.kpis?.alerts?.last_alert_status || 'unknown').replace('_', ' ')})`}
                </span>
              ) : (
                'No alert sent yet'
              )}
            </p>
          </div>
          <div className="rounded-xl border border-dark-300 bg-dark-200/30 p-4">
            <p className="text-xs text-white/60 uppercase">Risk Signals</p>
            <p className="text-sm font-bold text-amber-300">
              Stuck {agentPerf?.kpis?.risk?.stuck_steps || 0} · Retry {agentPerf?.kpis?.risk?.retry_signals || 0}
            </p>
            <p className="text-[10px] text-white/45 mt-1">
              Loop {agentPerf?.kpis?.risk?.loop_signals || 0} · Timeout {agentPerf?.kpis?.risk?.timeout_signals || 0}
            </p>
          </div>
        </div>
        <div className="grid md:grid-cols-2 gap-4 mb-4">
          <div className="rounded-xl border border-dark-300 bg-dark-200/30 p-4">
            <p className="text-xs text-white/60 uppercase mb-1">7 Day Window</p>
            <p className="text-sm text-white/85">
              Steps {agentPerf?.kpis?.windows?.last_7d?.steps || 0} · Success {(((agentPerf?.kpis?.windows?.last_7d?.success_rate || 0) * 100)).toFixed(1)}%
            </p>
          </div>
          <div className="rounded-xl border border-dark-300 bg-dark-200/30 p-4">
            <p className="text-xs text-white/60 uppercase mb-1">30 Day Window</p>
            <p className="text-sm text-white/85">
              Steps {agentPerf?.kpis?.windows?.last_30d?.steps || 0} · Success {(((agentPerf?.kpis?.windows?.last_30d?.success_rate || 0) * 100)).toFixed(1)}%
            </p>
          </div>
        </div>
        {Array.isArray(agentPerf?.kpis?.failure_mix) && agentPerf!.kpis!.failure_mix!.length > 0 && (
          <div className="rounded-xl border border-dark-300 bg-dark-200/30 p-4 mb-4">
            <p className="text-xs text-white/60 uppercase mb-2">Top Failure Reasons</p>
            <div className="flex flex-wrap gap-2">
              {agentPerf!.kpis!.failure_mix!.map((f) => (
                <span key={`${f.reason}-${f.count}`} className="px-2 py-1 rounded-lg border border-red-500/40 bg-red-500/10 text-xs font-semibold text-red-300">
                  {f.reason.replace(/_/g, ' ')} ({f.count})
                </span>
              ))}
            </div>
          </div>
        )}
        {Array.isArray(agentPerf?.agents) && agentPerf!.agents!.length > 0 && (
          <div className="overflow-x-auto rounded-xl border border-dark-300">
            <div className="p-3 border-b border-dark-300/60 flex flex-wrap items-center gap-2 bg-dark-200/20">
              <input
                type="text"
                value={agentPerfSearch}
                onChange={(e) => setAgentPerfSearch(e.target.value)}
                placeholder="Search agent by name or endpoint..."
                className="px-3 py-2 rounded-lg bg-dark-100/70 border border-dark-300 text-xs text-white/90 placeholder-white/40 focus:outline-none focus:ring-2 focus:ring-primary-500"
              />
              <select
                value={agentPerfSort}
                onChange={(e) => setAgentPerfSort(e.target.value as any)}
                className="px-3 py-2 rounded-lg bg-dark-100/70 border border-dark-300 text-xs text-white/90 focus:outline-none focus:ring-2 focus:ring-primary-500"
              >
                <option value="failures">Sort: failures</option>
                <option value="success">Sort: success rate</option>
                <option value="latency_p95">Sort: latency p95</option>
                <option value="tokens">Sort: output tokens</option>
              </select>
              <button
                type="button"
                onClick={() => {
                  if (!agentPerf?.agents?.length) return
                  const rows = [
                    ['agent_name', 'api_endpoint', 'steps', 'completed_steps', 'failed_steps', 'success_rate', 'p95_latency_seconds', 'output_tokens', 'sla_status'],
                    ...agentPerf.agents.map((a) => [
                      a.agent_name || '',
                      a.api_endpoint || '',
                      String(a.totals.steps || 0),
                      String(a.totals.completed_steps || 0),
                      String(a.totals.failed_steps || 0),
                      String(((a.quality?.success_rate || 0) * 100).toFixed(2)),
                      String(a.latency_seconds?.p95 || 0),
                      String(a.totals.output_tokens || 0),
                      String(a.sla?.status || 'unknown'),
                    ]),
                  ]
                  const csv = rows.map((r) => r.map((x) => `"${String(x).replace(/"/g, '""')}"`).join(',')).join('\n')
                  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
                  const url = URL.createObjectURL(blob)
                  const a = document.createElement('a')
                  a.href = url
                  a.download = 'published_agent_performance.csv'
                  a.click()
                  URL.revokeObjectURL(url)
                }}
                className="px-3 py-2 rounded-lg bg-primary-500/20 border border-primary-500/40 text-xs font-semibold text-primary-200 hover:bg-primary-500/30"
              >
                Export CSV
              </button>
            </div>
            <table className="w-full text-sm">
              <thead className="bg-dark-200/40 text-white/70">
                <tr>
                  <th className="text-left px-3 py-2">Agent</th>
                  <th className="text-left px-3 py-2">Success</th>
                  <th className="text-left px-3 py-2">SLA</th>
                  <th className="text-left px-3 py-2">Latency p95</th>
                  <th className="text-left px-3 py-2">Output Tokens</th>
                  <th className="text-left px-3 py-2">Latency Status</th>
                  <th className="text-left px-3 py-2">Latest Runtime</th>
                  <th className="text-left px-3 py-2">Failure Drilldown</th>
                </tr>
              </thead>
              <tbody className="text-white/90">
                {agentPerf!.agents!
                  .filter((a) => {
                    const q = agentPerfSearch.trim().toLowerCase()
                    if (!q) return true
                    return (a.agent_name || '').toLowerCase().includes(q) || (a.api_endpoint || '').toLowerCase().includes(q)
                  })
                  .sort((a, b) => {
                    if (agentPerfSort === 'success') return (b.quality?.success_rate || 0) - (a.quality?.success_rate || 0)
                    if (agentPerfSort === 'latency_p95') return (b.latency_seconds?.p95 || 0) - (a.latency_seconds?.p95 || 0)
                    if (agentPerfSort === 'tokens') return (b.totals.output_tokens || 0) - (a.totals.output_tokens || 0)
                    return (b.totals.failed_steps || 0) - (a.totals.failed_steps || 0)
                  })
                  .map((a) => (
                  <tr key={a.agent_id} className="border-t border-dark-300/60">
                    <td className="px-3 py-2">
                      <p className="font-semibold">{a.agent_name}</p>
                      <p className="text-xs text-white/50 truncate max-w-[280px]">{a.api_endpoint || '-'}</p>
                    </td>
                    <td className="px-3 py-2">{(((a.quality?.success_rate || 0) * 100)).toFixed(1)}%</td>
                    <td className="px-3 py-2">
                      <span
                        title={a.sla?.reason || ''}
                        className={`px-2 py-1 rounded-lg border text-xs font-semibold ${
                          (a.sla?.status || 'healthy') === 'healthy'
                            ? 'bg-green-500/15 border-green-500/40 text-green-300'
                            : (a.sla?.status || 'healthy') === 'at_risk'
                              ? 'bg-amber-500/15 border-amber-500/40 text-amber-300'
                              : 'bg-red-500/15 border-red-500/40 text-red-300'
                        }`}
                      >
                        <span className="inline-flex items-center gap-1.5">
                          <span className={`h-1.5 w-1.5 rounded-full ${statusDotClass(a.sla?.status || 'healthy')}`} />
                          {(a.sla?.status || 'healthy').replace('_', ' ')}
                        </span>
                      </span>
                      {(a.sla?.reason || '').trim() !== '' && (
                        <p className="text-[10px] text-white/55 mt-1 max-w-[260px] truncate" title={a.sla?.reason}>
                          {a.sla?.reason}
                        </p>
                      )}
                    </td>
                    <td className="px-3 py-2">{(a.latency_seconds?.p95 || 0).toFixed(1)}s</td>
                    <td className="px-3 py-2">{(a.totals.output_tokens || 0).toLocaleString()}</td>
                    <td className="px-3 py-2">{a.totals.in_progress_steps > 0 ? 'Active' : (a.totals.failed_steps > 0 ? 'Needs attention' : 'Stable')}</td>
                    <td className="px-3 py-2 text-xs text-white/75">
                      {(a.latest_runtime?.phase || a.latest_runtime?.status || '-').replace(/_/g, ' ')}
                      {a.latest_runtime?.reason_code ? ` · ${a.latest_runtime.reason_code}` : ''}
                    </td>
                    <td className="px-3 py-2">
                      {(a.recent_failures?.length || 0) > 0 ? (
                        <button
                          type="button"
                          onClick={() => setSelectedAgentId(selectedAgentId === a.agent_id ? null : a.agent_id)}
                          className="px-2 py-1 rounded-lg border border-dark-300 bg-dark-200/40 text-xs font-semibold text-white/85 hover:bg-dark-200"
                        >
                          View ({a.recent_failures?.length || 0})
                        </button>
                      ) : (
                        <span className="text-xs text-white/40">-</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {selectedAgentId && (
              <div className="border-t border-dark-300/60 p-4 bg-dark-200/15">
                {agentPerf.agents
                  ?.filter((a) => a.agent_id === selectedAgentId)
                  .map((a) => (
                    <div key={a.agent_id}>
                      <p className="text-sm font-bold text-white mb-2">Recent Failures — {a.agent_name}</p>
                      {(a.recent_failures || []).length > 0 ? (
                        <div className="space-y-2">
                          {(a.recent_failures || []).map((f, idx) => (
                            <div key={`${f.workflow_step_id}-${idx}`} className="rounded-lg border border-red-500/30 bg-red-500/10 p-2 text-xs text-red-200">
                              <p className="font-semibold">{(f.reason_code || 'failed_without_reason').replace(/_/g, ' ')}</p>
                              <p className="text-red-200/80">
                                {f.job_title || 'Job'} · Step {f.step_order || '-'} · {f.failed_at ? new Date(f.failed_at).toLocaleString() : '-'}
                              </p>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p className="text-xs text-white/50">No recent failures.</p>
                      )}
                    </div>
                  ))}
              </div>
            )}
          </div>
        )}
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
