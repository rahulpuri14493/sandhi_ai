import { useEffect, useState } from 'react'
import { dashboardsAPI, jobsAPI, mcpAPI } from '../lib/api'
import { getStepOutputDisplayText } from '../lib/formatStepOutput'
import type { Job } from '../lib/types'
import { Link, useNavigate } from 'react-router-dom'

export function BusinessDashboard() {
  const [spending, setSpending] = useState({ total_spent: 0, job_count: 0 })
  const [jobs, setJobs] = useState<Job[]>([])
  const [queueStats, setQueueStats] = useState<{
    execution_backend: string
    queue_name: string
    pending_jobs: number | null
    workers: { online: number; active: number; reserved: number }
  } | null>(null)
  const [platformToolNamesById, setPlatformToolNamesById] = useState<Record<number, string>>({})
  const [isLoading, setIsLoading] = useState(true)
  const [lastPerfRefreshAt, setLastPerfRefreshAt] = useState<number | null>(null)
  const [perfKpis, setPerfKpis] = useState<{
    generated_at?: string
    overview?: {
      agents?: number
      steps?: number
      completed_steps?: number
      failed_steps?: number
      in_progress_steps?: number
      success_rate?: number
      failure_rate?: number
      cost_total?: number
      prompt_tokens_total?: number
      completion_tokens_total?: number
      total_tokens?: number
    }
    latency_seconds?: { samples?: number; avg?: number; p50?: number; p95?: number }
    windows?: {
      last_7d?: { steps?: number; completed_steps?: number; failed_steps?: number; success_rate?: number; cost_total?: number }
      last_30d?: { steps?: number; completed_steps?: number; failed_steps?: number; success_rate?: number; cost_total?: number }
    }
    efficiency?: { cost_per_completed_step?: number; completion_tokens_per_completed_step?: number }
    failure_mix?: Array<{ reason: string; count: number }>
    risk?: { stuck_steps?: number; loop_signals?: number; drift_signals?: number; retry_signals?: number }
  } | null>(null)
  const [agentPerf, setAgentPerf] = useState<Array<{
    agent_id: number
    agent_name: string
    api_endpoint?: string
    totals: {
      steps: number
      completed_steps: number
      failed_steps: number
      in_progress_steps: number
      cost: number
      total_tokens: number
    }
    quality: {
      success_rate: number
      average_confidence: number | null
    }
    latest_runtime?: {
      job_id?: number
      workflow_step_id?: number
      step_order?: number
      phase?: string
      reason_code?: string
      reason_detail?: Record<string, unknown> | null
      trace_id?: string | null
      started_at?: string | null
      phase_started_at?: string | null
      last_activity_at?: string | null
      last_progress_at?: string | null
      stuck_since?: string | null
      stuck_reason?: string
      status?: string
    } | null
  }>>([])
  const [selectedRuntime, setSelectedRuntime] = useState<{
    agent_name: string
    agent_id: number
    runtime: {
      job_id?: number
      workflow_step_id?: number
      step_order?: number
      phase?: string
      reason_code?: string
      reason_detail?: Record<string, unknown> | null
      trace_id?: string | null
      started_at?: string | null
      phase_started_at?: string | null
      last_activity_at?: string | null
      last_progress_at?: string | null
      stuck_since?: string | null
      stuck_reason?: string
      status?: string
    } | null
  } | null>(null)
  const [expandedJobId, setExpandedJobId] = useState<number | null>(null)
  const [searchTerm, setSearchTerm] = useState('')
  const [statusFilter, setStatusFilter] = useState<'all' | Job['status']>('all')
  const [riskFilter, setRiskFilter] = useState<'all' | 'stable' | 'possible_drift'>('all')
  const [stageFilter, setStageFilter] = useState<string>('all')
  const [heartbeatFilter, setHeartbeatFilter] = useState<
    'all' | 'healthy' | 'delayed' | 'stale' | 'stuck' | 'failed' | 'completed' | 'no_signal'
  >('all')
  const [sortBy, setSortBy] = useState<'created_desc' | 'created_asc' | 'last_executed_desc' | 'last_executed_asc'>(
    'created_desc',
  )
  const navigate = useNavigate()

  useEffect(() => {
    loadData()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    // Lightweight live refresh for end-user observability.
    const timer = setInterval(async () => {
      try {
        const perfData = await dashboardsAPI.getBusinessAgentPerformance(800)
        setAgentPerf(perfData?.agents || [])
        setPerfKpis(perfData?.kpis || null)
        setLastPerfRefreshAt(Date.now())
      } catch {
        // Keep previous data on transient failures.
      }
    }, 15000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    let mounted = true
    const loadQueueStats = async () => {
      try {
        const stats = await jobsAPI.getQueueStats()
        if (mounted) setQueueStats(stats)
      } catch {
        if (mounted) setQueueStats(null)
      }
    }
    loadQueueStats()
    const timer = setInterval(loadQueueStats, 10000)
    return () => {
      mounted = false
      clearInterval(timer)
    }
  }, [])

  const loadData = async () => {
    setIsLoading(true)
    try {
      const [spendingData, jobsData] = await Promise.all([
        dashboardsAPI.getBusinessSpending(),
        dashboardsAPI.getBusinessJobs(),
      ])
      const perfData = await dashboardsAPI.getBusinessAgentPerformance(800)
      const tools = await mcpAPI.listTools().catch(() => [])
      const toolMap: Record<number, string> = {}
      for (const t of tools || []) {
        if (typeof t?.id === 'number' && typeof t?.name === 'string' && t.name.trim()) {
          toolMap[t.id] = t.name.trim()
        }
      }
      setSpending(spendingData)
      setJobs(jobsData)
      setAgentPerf(perfData?.agents || [])
      setPerfKpis(perfData?.kpis || null)
      setLastPerfRefreshAt(Date.now())
      setPlatformToolNamesById(toolMap)
    } catch (error) {
      console.error('Failed to load dashboard data:', error)
    } finally {
      setIsLoading(false)
    }
  }

  const refreshSelectedRuntime = async (agentId: number, keepName?: string) => {
    try {
      const perfData = await dashboardsAPI.getBusinessAgentPerformance(800)
      const agents = perfData?.agents || []
      const next = agents.find((x: any) => Number(x?.agent_id) === Number(agentId))
      if (!next) return
      setAgentPerf(agents)
      setPerfKpis(perfData?.kpis || null)
      setLastPerfRefreshAt(Date.now())
      setSelectedRuntime((prev) => {
        if (!prev || Number(prev.agent_id) !== Number(agentId)) return prev
        return {
          agent_name: next.agent_name || keepName || prev.agent_name,
          agent_id: Number(next.agent_id),
          runtime: next.latest_runtime || null,
        }
      })
    } catch {
      // Keep current drawer content on transient failures.
    }
  }

  const parseOutputPayload = (raw?: string) => {
    if (!raw) return null
    try {
      return JSON.parse(raw)
    } catch {
      return null
    }
  }

  const deriveJobMetrics = (job: Job) => {
    const steps = job.workflow_steps || []
    const tools = new Set<string>()
    let tokenTotal = 0
    let hasTokenUsage = false
    let writeTransactions = 0
    let processedRecords = 0
    let failedSteps = 0
    let stage = '-'
    let reason = '-'
    let latestHeartbeatAt: string | null = null
    const scopedTools = new Set<string>()

    for (const s of steps) {
      if ((s.status || '').toLowerCase() === 'failed') failedSteps += 1
      const out = parseOutputPayload(s.output_data)
      const usage =
        out?.agent_output?.usage ||
        out?.agent_output?.token_usage ||
        out?.usage ||
        out?.token_usage ||
        out?.usage_metadata ||
        out?.response_metadata?.token_usage ||
        out?.agent_output?.response_metadata?.token_usage ||
        out?.metrics?.token_usage
      // Strict metric: count only agent response/output tokens reported by provider.
      const completion = Number(usage?.completion_tokens || usage?.output_tokens || 0)
      if (Number.isFinite(completion) && completion > 0) {
        hasTokenUsage = true
        tokenTotal += completion
      }

      const records = out?.agent_output?.records || out?.records
      if (Array.isArray(records)) processedRecords += records.length

      const wr = Array.isArray(out?.write_results) ? out.write_results : []
      for (const row of wr) {
        const n = row?.tool_name
        if (typeof n === 'string' && n.trim()) tools.add(n.trim())
        if ((row?.status || '').toLowerCase() === 'success') writeTransactions += 1
      }

      const toolCallCandidates = [
        out?.mcp_tools_used,
        out?.tool_calls,
        out?.agent_output?.tool_calls,
        out?.agent_output?.tools_used,
        out?.tools_used,
      ]
      for (const calls of toolCallCandidates) {
        if (!Array.isArray(calls)) continue
        for (const c of calls) {
          const fnName = c?.function?.name
          const name = typeof c?.name === 'string' ? c.name : typeof fnName === 'string' ? fnName : null
          if (name && name.trim()) tools.add(name.trim())
        }
      }

      const scopedIds = Array.isArray(s.allowed_platform_tool_ids)
        ? s.allowed_platform_tool_ids
        : Array.isArray(job.allowed_platform_tool_ids)
          ? job.allowed_platform_tool_ids
          : []
      for (const id of scopedIds || []) {
        const n = Number(id)
        if (!Number.isFinite(n) || n <= 0) continue
        scopedTools.add(platformToolNamesById[n] || `platform_${n}`)
      }
    }

    const active = [...steps].sort((a, b) => {
      const ta = new Date(a.last_activity_at || a.started_at || a.completed_at || 0).getTime()
      const tb = new Date(b.last_activity_at || b.started_at || b.completed_at || 0).getTime()
      return tb - ta
    })[0]
    if (active) {
      stage = active.live_phase || active.status || '-'
      reason = active.stuck_reason || active.live_reason_code || '-'
      latestHeartbeatAt = active.last_activity_at || active.last_progress_at || active.completed_at || active.started_at || null
    }

    const normalizedToolNames: string[] = []
    const seenToolNames = new Set<string>()
    for (const rawTool of Array.from(new Set([...tools, ...scopedTools]))) {
      const label = prettifyToolName(rawTool)
      const key = label.trim().toLowerCase()
      if (!key || seenToolNames.has(key)) continue
      seenToolNames.add(key)
      normalizedToolNames.push(label)
    }

    const scopedCount = scopedTools.size
    const usedCount = normalizedToolNames.length
    const toolCoverage =
      scopedCount > 0
        ? `${Math.min(usedCount, scopedCount)}/${scopedCount}`
        : usedCount > 0
          ? `${usedCount}/-`
          : '-'
    const toolAnomaly = scopedCount > 0 && usedCount === 0
    const reasonLower = (reason || '').toLowerCase()
    const driftRisk =
      reasonLower.includes('drift') ||
      reasonLower.includes('loop') ||
      reasonLower.includes('tool_call_loop') ||
      reasonLower.includes('retry')

    return {
      tokenTotal,
      hasTokenUsage,
      writeTransactions,
      processedRecords,
      toolsUsed: normalizedToolNames,
      failedSteps,
      stage,
      reason,
      latestHeartbeatAt,
      toolCoverage,
      toolAnomaly,
      driftRisk,
    }
  }

  const humanizeMetricText = (value?: string | null) => {
    if (!value || value === '-') return '-'
    return value
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase())
  }

  const prettifyToolName = (name: string) => {
    const raw = (name || '').trim()
    if (!raw) return raw
    const directPlatformId = raw.match(/^platform_(\d+)$/i)
    if (directPlatformId) {
      const toolId = Number(directPlatformId[1])
      const mapped = platformToolNamesById[toolId]
      if (mapped && mapped.trim()) return prettifyToolName(mapped)
      return `Tool #${toolId}`
    }
    const prefixedPlatform = raw.match(/^platform_(\d+)_(.+)$/i)
    if (prefixedPlatform) {
      const toolId = Number(prefixedPlatform[1])
      const mapped = platformToolNamesById[toolId]
      if (mapped && mapped.trim()) return prettifyToolName(mapped)
      return prettifyToolName(prefixedPlatform[2] || raw)
    }
    const withoutPrefix = raw.replace(/^byo_\d+_/, '')
    return withoutPrefix
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase())
  }

  const getStageBadgeClass = (stage?: string | null, status?: string | null) => {
    const v = (stage || status || '').toLowerCase()
    if (v.includes('failed') || v.includes('error')) return 'bg-red-500/15 border-red-500/40 text-red-300'
    if (v.includes('completed')) return 'bg-green-500/15 border-green-500/40 text-green-300'
    if (v.includes('retry') || v.includes('blocked')) return 'bg-amber-500/15 border-amber-500/40 text-amber-300'
    if (v.includes('progress') || v.includes('calling') || v.includes('planning') || v.includes('starting')) {
      return 'bg-primary-500/20 border-primary-500/40 text-primary-200'
    }
    return 'bg-dark-200/40 border-dark-300 text-white/75'
  }

  const getHealthBadge = (successRate: number, failedSteps: number, inProgress: number) => {
    if (failedSteps > 0) return { label: 'Needs Attention', className: 'bg-red-500/15 border-red-500/40 text-red-300' }
    if (inProgress > 0) return { label: 'Running', className: 'bg-primary-500/20 border-primary-500/40 text-primary-200' }
    if (successRate >= 0.9) return { label: 'Healthy', className: 'bg-green-500/15 border-green-500/40 text-green-300' }
    if (successRate >= 0.7) return { label: 'Fair', className: 'bg-amber-500/15 border-amber-500/40 text-amber-300' }
    return { label: 'At Risk', className: 'bg-red-500/15 border-red-500/40 text-red-300' }
  }

  const getHeartbeatBadge = (ts?: string | null, status?: string | null, stuckSince?: string | null) => {
    if (stuckSince) return { label: 'Stuck', className: 'bg-red-500/15 border-red-500/40 text-red-300' }
    if (!ts) return { label: 'No Signal', className: 'bg-dark-200/40 border-dark-300 text-white/70' }
    const ageMs = Date.now() - new Date(ts).getTime()
    if (!Number.isFinite(ageMs) || ageMs < 0) {
      return { label: 'Unknown', className: 'bg-dark-200/40 border-dark-300 text-white/70' }
    }
    const st = (status || '').toLowerCase()
    if (st === 'completed') return { label: 'Completed', className: 'bg-green-500/15 border-green-500/40 text-green-300' }
    if (st === 'failed') return { label: 'Failed', className: 'bg-red-500/15 border-red-500/40 text-red-300' }
    if (ageMs <= 60_000) return { label: 'Healthy', className: 'bg-green-500/15 border-green-500/40 text-green-300' }
    if (ageMs <= 180_000) return { label: 'Delayed', className: 'bg-amber-500/15 border-amber-500/40 text-amber-300' }
    return { label: 'Stale', className: 'bg-red-500/15 border-red-500/40 text-red-300' }
  }

  const normalizeKey = (v?: string | null) =>
    (v || '')
      .trim()
      .toLowerCase()
      .replace(/\s+/g, '_')

  const phaseOrder = ['starting', 'planning', 'thinking', 'calling_agent', 'calling_tool', 'writing_artifact', 'completed']
  const phaseLabel = (phase?: string | null) => humanizeMetricText(phase || '-')

  const classifyReason = (reason?: string | null) => {
    const raw = (reason || '').trim()
    if (!raw) return { title: '-', hint: 'No runtime error reported' }
    const v = raw.toLowerCase()
    if (v.includes('throttle') || v.includes('429')) return { title: 'Rate Limit', hint: 'Provider is throttling; retries/backoff usually help.' }
    if (v.includes('upstream_5xx') || v.includes('internal server error')) return { title: 'Provider Error', hint: 'Upstream model/tool endpoint returned server error.' }
    if (v.includes('timeout')) return { title: 'Timeout', hint: 'Execution exceeded expected time window.' }
    if (v.includes('tool')) return { title: 'Tool Error', hint: 'A MCP/platform tool call failed or looped unexpectedly.' }
    if (v.includes('validation') || v.includes('contract')) return { title: 'Output Validation', hint: 'Agent output shape failed validation checks.' }
    return { title: humanizeMetricText(raw), hint: 'Review runtime details for full context.' }
  }

  const sparkPoints = (values: number[]) => {
    const xs = values.map((v) => (Number.isFinite(v) ? Math.max(0, Number(v)) : 0))
    const m = Math.max(1, ...xs)
    return xs.map((v) => Math.max(10, Math.round((v / m) * 100)))
  }

  const jobsWithComputed = jobs.map((job) => {
    const metrics = deriveJobMetrics(job)
    const heartbeat = getHeartbeatBadge(metrics.latestHeartbeatAt, job.status, null)
    const steps = job.workflow_steps || []
    let lastExecutedTs = 0
    for (const s of steps) {
      const t = new Date(s.completed_at || s.started_at || 0).getTime()
      if (Number.isFinite(t) && t > lastExecutedTs) lastExecutedTs = t
    }
    return {
      job,
      metrics,
      heartbeatKey: normalizeKey(heartbeat.label),
      stageKey: normalizeKey(metrics.stage),
      riskKey: metrics.driftRisk ? 'possible_drift' : 'stable',
      createdTs: new Date(job.created_at || 0).getTime() || 0,
      lastExecutedTs,
    }
  })

  const stageOptions = Array.from(new Set(jobsWithComputed.map((x) => x.stageKey).filter(Boolean))).sort()

  const filteredJobs = jobsWithComputed
    .filter(({ job, metrics, heartbeatKey, stageKey, riskKey }) => {
      const matchesSearch =
        !searchTerm ||
        job.title.toLowerCase().includes(searchTerm.toLowerCase()) ||
        (job.description || '').toLowerCase().includes(searchTerm.toLowerCase())
      const matchesStatus = statusFilter === 'all' || job.status === statusFilter
      const matchesRisk = riskFilter === 'all' || riskKey === riskFilter
      const matchesStage = stageFilter === 'all' || stageKey === stageFilter
      const matchesHeartbeat = heartbeatFilter === 'all' || heartbeatKey === heartbeatFilter
      return matchesSearch && matchesStatus && matchesRisk && matchesStage && matchesHeartbeat && !!metrics
    })
    .sort((a, b) => {
      if (sortBy === 'created_asc') return a.createdTs - b.createdTs
      if (sortBy === 'last_executed_desc') return (b.lastExecutedTs || 0) - (a.lastExecutedTs || 0)
      if (sortBy === 'last_executed_asc') return (a.lastExecutedTs || 0) - (b.lastExecutedTs || 0)
      return b.createdTs - a.createdTs
    })

  const formatTelemetryValue = (v: unknown): string => {
    if (v == null) return '-'
    if (typeof v === 'number' || typeof v === 'boolean') return String(v)
    if (typeof v === 'string') return v
    try {
      return JSON.stringify(v)
    } catch {
      return String(v)
    }
  }

  const formatTimestamp = (v?: string | null): string => {
    if (!v) return '-'
    const d = new Date(v)
    if (Number.isNaN(d.getTime())) return v
    return d.toLocaleString()
  }

  useEffect(() => {
    if (!selectedRuntime?.agent_id) return
    const timer = setInterval(() => {
      void refreshSelectedRuntime(selectedRuntime.agent_id, selectedRuntime.agent_name)
    }, 15000)
    return () => clearInterval(timer)
    // Depend on identity only; runtime payload updates should not reset timer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRuntime?.agent_id, selectedRuntime?.agent_name])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center">
          <div className="inline-block animate-spin rounded-full h-12 w-12 border-4 border-primary-600 border-t-transparent mb-4"></div>
          <p className="text-gray-600 text-lg">Loading dashboard...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="animate-fadeIn min-h-screen">
      <div className="flex items-center justify-between mb-12">
        <div className="flex items-center">
          <button
            onClick={() => navigate('/')}
            className="flex items-center text-white/70 hover:text-white transition-all duration-200 mr-6 px-4 py-2.5 rounded-xl hover:bg-dark-100/50"
          >
            <svg className="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            Back to Home
          </button>
          <div>
            <h1 className="text-6xl font-black text-white tracking-tight mb-2">
              Business Dashboard
            </h1>
            <p className="text-white/60 text-lg font-medium">Manage your jobs and track spending</p>
          </div>
        </div>
      </div>

      <div className="grid md:grid-cols-3 gap-6 mb-10">
        <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50 card-hover">
          <div className="flex items-center justify-between mb-6">
            <div className="p-4 bg-gradient-to-br from-primary-500 to-primary-700 rounded-2xl shadow-lg">
              <svg className="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
          </div>
          <h2 className="text-sm font-semibold text-white/60 mb-2 uppercase tracking-wider">Total Spent</h2>
          <p className="text-5xl font-black text-white mb-2">
            ${spending.total_spent.toFixed(2)}
          </p>
          <p className="text-xs text-white/40 font-medium">All-time spending</p>
        </div>
        <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50 card-hover">
          <div className="flex items-center justify-between mb-6">
            <div className="p-4 bg-gradient-to-br from-primary-500 to-primary-700 rounded-2xl shadow-lg">
              <svg className="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
              </svg>
            </div>
          </div>
          <h2 className="text-sm font-semibold text-white/60 mb-2 uppercase tracking-wider">Total Jobs</h2>
          <p className="text-5xl font-black text-white mb-2">{spending.job_count}</p>
          <p className="text-xs text-white/40 font-medium">Jobs created</p>
        </div>
        <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50 card-hover">
          <div className="flex items-center justify-between mb-6">
            <div className="p-4 bg-gradient-to-br from-primary-500 to-primary-700 rounded-2xl shadow-lg">
              <svg className="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4m16 0l-4-4m4 4l-4 4" />
              </svg>
            </div>
          </div>
          <h2 className="text-sm font-semibold text-white/60 mb-2 uppercase tracking-wider">Queue (Redis/Celery)</h2>
          <p className="text-4xl font-black text-white mb-1">{queueStats?.pending_jobs ?? '-'}</p>
          <p className="text-xs text-white/40 font-medium mb-2">Pending jobs in `{queueStats?.queue_name || 'celery'}`</p>
          <p className="text-xs text-white/60 font-medium">
            Workers: {queueStats?.workers.online ?? '-'} online, {queueStats?.workers.active ?? '-'} active, {queueStats?.workers.reserved ?? '-'} reserved
          </p>
        </div>
      </div>

      <div className="grid md:grid-cols-4 gap-4 mb-8">
        <div className="rounded-xl border border-dark-200/60 bg-dark-100/40 p-4">
          <p className="text-[11px] uppercase tracking-wide text-white/50">Agents Monitored</p>
          <p className="mt-1 text-2xl font-black text-white">{agentPerf.length}</p>
        </div>
        <div className="rounded-xl border border-dark-200/60 bg-dark-100/40 p-4">
          <p className="text-[11px] uppercase tracking-wide text-white/50">Running Agents</p>
          <p className="mt-1 text-2xl font-black text-primary-300">
            {agentPerf.filter((a) => Number(a.totals.in_progress_steps || 0) > 0).length}
          </p>
        </div>
        <div className="rounded-xl border border-dark-200/60 bg-dark-100/40 p-4">
          <p className="text-[11px] uppercase tracking-wide text-white/50">Agents Needing Attention</p>
          <p className="mt-1 text-2xl font-black text-red-300">
            {agentPerf.filter((a) => Number(a.totals.failed_steps || 0) > 0).length}
          </p>
        </div>
        <div className="rounded-xl border border-dark-200/60 bg-dark-100/40 p-4">
          <p className="text-[11px] uppercase tracking-wide text-white/50">Last Runtime Refresh</p>
          <p className="mt-1 text-sm font-bold text-white">
            {lastPerfRefreshAt ? `${Math.max(0, Math.round((Date.now() - lastPerfRefreshAt) / 1000))}s ago` : '-'}
          </p>
        </div>
      </div>

      <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl border border-dark-200/50 overflow-hidden mb-10">
        <div className="bg-gradient-to-r from-primary-600 to-primary-800 px-8 py-5 border-b border-primary-700/50">
          <h2 className="text-2xl font-black text-white tracking-tight">KPI Analytics</h2>
          <p className="text-white/70 text-sm mt-1">Business-level performance, reliability, latency, and trend indicators</p>
        </div>
        <div className="p-6">
          <div className="mb-4 rounded-xl border border-dark-200/60 bg-dark-200/25 p-3 text-xs text-white/75">
            <p className="font-semibold text-white mb-1">How to read this section</p>
            <p>Mini sparklines show relative trend from 7d -&gt; 30d -&gt; overall sample. Taller bars mean higher values.</p>
          </div>
          <div className="grid md:grid-cols-4 gap-3">
            <div className="rounded-xl border border-dark-200/60 bg-dark-200/25 p-4">
              <p className="text-[11px] uppercase tracking-wide text-white/50 flex items-center gap-1">
                Success Rate
                <span title="Completed steps divided by total sampled steps." className="cursor-help text-white/40">ⓘ</span>
              </p>
              <p className="text-2xl font-black text-green-300">
                {((perfKpis?.overview?.success_rate || 0) * 100).toFixed(1)}%
              </p>
              <div className="mt-2 flex items-end gap-1 h-8" title="7d -> 30d -> overall">
                {sparkPoints([
                  (perfKpis?.windows?.last_7d?.success_rate || 0) * 100,
                  (perfKpis?.windows?.last_30d?.success_rate || 0) * 100,
                  (perfKpis?.overview?.success_rate || 0) * 100,
                ]).map((h, i) => (
                  <span key={i} className="w-2 rounded-sm bg-green-400/80" style={{ height: `${h}%` }} />
                ))}
              </div>
            </div>
            <div className="rounded-xl border border-dark-200/60 bg-dark-200/25 p-4">
              <p className="text-[11px] uppercase tracking-wide text-white/50 flex items-center gap-1">
                Failure Rate
                <span title="Failed steps divided by total sampled steps." className="cursor-help text-white/40">ⓘ</span>
              </p>
              <p className="text-2xl font-black text-red-300">
                {((perfKpis?.overview?.failure_rate || 0) * 100).toFixed(1)}%
              </p>
              <div className="mt-2 flex items-end gap-1 h-8" title="7d -> 30d -> overall">
                {sparkPoints([
                  (perfKpis?.windows?.last_7d?.failed_steps || 0),
                  (perfKpis?.windows?.last_30d?.failed_steps || 0),
                  (perfKpis?.overview?.failed_steps || 0),
                ]).map((h, i) => (
                  <span key={i} className="w-2 rounded-sm bg-red-400/80" style={{ height: `${h}%` }} />
                ))}
              </div>
            </div>
            <div className="rounded-xl border border-dark-200/60 bg-dark-200/25 p-4">
              <p className="text-[11px] uppercase tracking-wide text-white/50 flex items-center gap-1">
                Latency p50 / p95
                <span title="Median and 95th percentile execution time in seconds." className="cursor-help text-white/40">ⓘ</span>
              </p>
              <p className="text-lg font-black text-white">
                {(perfKpis?.latency_seconds?.p50 || 0).toFixed(1)}s / {(perfKpis?.latency_seconds?.p95 || 0).toFixed(1)}s
              </p>
              <div className="mt-2 flex items-end gap-1 h-8" title="p50 vs p95">
                {sparkPoints([
                  perfKpis?.latency_seconds?.p50 || 0,
                  perfKpis?.latency_seconds?.p95 || 0,
                ]).map((h, i) => (
                  <span key={i} className="w-2 rounded-sm bg-primary-300/90" style={{ height: `${h}%` }} />
                ))}
              </div>
            </div>
            <div className="rounded-xl border border-dark-200/60 bg-dark-200/25 p-4">
              <p className="text-[11px] uppercase tracking-wide text-white/50 flex items-center gap-1">
                Output Tokens / Completed Step
                <span title="Provider-reported completion tokens divided by completed steps." className="cursor-help text-white/40">ⓘ</span>
              </p>
              <p className="text-lg font-black text-white">
                {(perfKpis?.efficiency?.completion_tokens_per_completed_step || 0).toFixed(1)}
              </p>
            </div>
          </div>

          <div className="grid md:grid-cols-3 gap-3 mt-4">
            <div className="rounded-xl border border-dark-200/60 bg-dark-200/25 p-4">
              <p className="text-[11px] uppercase tracking-wide text-white/50 mb-1">Last 7 Days</p>
              <p className="text-sm text-white/85">
                Steps: {perfKpis?.windows?.last_7d?.steps || 0} · Success: {(((perfKpis?.windows?.last_7d?.success_rate || 0) * 100)).toFixed(1)}%
              </p>
              <p className="text-xs text-white/60 mt-1">Cost: ${(perfKpis?.windows?.last_7d?.cost_total || 0).toFixed(2)}</p>
            </div>
            <div className="rounded-xl border border-dark-200/60 bg-dark-200/25 p-4">
              <p className="text-[11px] uppercase tracking-wide text-white/50 mb-1">Last 30 Days</p>
              <p className="text-sm text-white/85">
                Steps: {perfKpis?.windows?.last_30d?.steps || 0} · Success: {(((perfKpis?.windows?.last_30d?.success_rate || 0) * 100)).toFixed(1)}%
              </p>
              <p className="text-xs text-white/60 mt-1">Cost: ${(perfKpis?.windows?.last_30d?.cost_total || 0).toFixed(2)}</p>
            </div>
            <div className="rounded-xl border border-dark-200/60 bg-dark-200/25 p-4">
              <p className="text-[11px] uppercase tracking-wide text-white/50 mb-1">Risk Signals</p>
              <p className="text-sm text-white/85">
                Stuck: {perfKpis?.risk?.stuck_steps || 0} · Drift: {perfKpis?.risk?.drift_signals || 0}
              </p>
              <p className="text-xs text-white/60 mt-1">
                Loop: {perfKpis?.risk?.loop_signals || 0} · Retry: {perfKpis?.risk?.retry_signals || 0}
              </p>
            </div>
          </div>

          <div className="mt-4 rounded-xl border border-dark-200/60 bg-dark-200/25 p-4">
            <p className="text-[11px] uppercase tracking-wide text-white/50 mb-2">Top Failure Reasons</p>
            {Array.isArray(perfKpis?.failure_mix) && perfKpis!.failure_mix!.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {perfKpis!.failure_mix!.map((f) => (
                  <span key={`${f.reason}-${f.count}`} className="px-2.5 py-1 rounded-lg border border-red-500/30 bg-red-500/10 text-xs font-semibold text-red-300">
                    {humanizeMetricText(f.reason)} ({f.count})
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-xs text-white/50">No failures in sampled period.</p>
            )}
          </div>
        </div>
      </div>

      <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl border border-dark-200/50 overflow-hidden mb-10">
        <div className="bg-gradient-to-r from-primary-600 to-primary-800 px-8 py-6 border-b border-primary-700/50">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-3xl font-black text-white tracking-tight">Hired Agent Performance</h2>
              <p className="text-white/70 text-sm mt-1">Live stage, usage, errors, and reliability per hired agent</p>
              <p className="text-white/50 text-xs mt-1">Token metric is strict: reported output/completion tokens only.</p>
            </div>
            <details className="group relative">
              <summary className="list-none cursor-pointer inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-white/30 bg-white/10 text-white/90 text-xs font-semibold hover:bg-white/20 transition-colors">
                <span className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-white/50 text-[10px]">i</span>
                Metric definitions
              </summary>
              <div className="absolute right-0 mt-2 w-[300px] rounded-xl border border-dark-200/60 bg-dark-100/95 backdrop-blur-xl p-3 text-xs text-white/85 shadow-2xl z-20">
                <p><span className="font-bold text-white">Health:</span> Quick overall state from failures, running steps, and success rate.</p>
                <p className="mt-1"><span className="font-bold text-white">Success Rate:</span> Completed steps divided by total steps.</p>
                <p className="mt-1"><span className="font-bold text-white">Tokens:</span> Strict provider-reported output/completion tokens only.</p>
                <p className="mt-1"><span className="font-bold text-white">Current Stage:</span> Latest execution phase for that agent.</p>
                <p className="mt-1"><span className="font-bold text-white">Last Error/Reason:</span> Most recent failure or stuck reason from runtime telemetry.</p>
              </div>
            </details>
          </div>
        </div>
        <div className="p-6">
          {agentPerf.length === 0 ? (
            <p className="text-white/50 text-sm">No hired-agent execution data yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-white/60 border-b border-dark-200/60">
                    <th className="py-3 pr-4">Agent</th>
                    <th className="py-3 pr-4">Health</th>
                    <th className="py-3 pr-4">Heartbeat</th>
                    <th className="py-3 pr-4">Success Rate</th>
                    <th className="py-3 pr-4">Tokens</th>
                    <th className="py-3 pr-4">Failed</th>
                    <th className="py-3 pr-4">Current Stage</th>
                    <th className="py-3 pr-4">Last Error/Reason</th>
                    <th className="py-3 pr-4">Risk</th>
                    <th className="py-3 pr-4">Debug</th>
                  </tr>
                </thead>
                <tbody>
                  {agentPerf.map((a) => {
                    const health = getHealthBadge(
                      Number(a.quality.success_rate || 0),
                      Number(a.totals.failed_steps || 0),
                      Number(a.totals.in_progress_steps || 0),
                    )
                    const hb = getHeartbeatBadge(
                      a.latest_runtime?.last_activity_at || a.latest_runtime?.last_progress_at || a.latest_runtime?.phase_started_at || a.latest_runtime?.started_at || null,
                      a.latest_runtime?.status || null,
                      a.latest_runtime?.stuck_since || null,
                    )
                    const reasonInfo = classifyReason(a.latest_runtime?.stuck_reason || a.latest_runtime?.reason_code || null)
                    const reasonText = a.latest_runtime?.stuck_reason || a.latest_runtime?.reason_code || '-'
                    return (
                    <tr key={a.agent_id} className="border-b border-dark-200/30 text-white/90">
                      <td className="py-3 pr-4">
                        <div className="font-semibold">{a.agent_name}</div>
                        <div className="text-xs text-white/50 truncate max-w-[260px]">{a.api_endpoint || '-'}</div>
                      </td>
                      <td className="py-3 pr-4">
                        <span className={`px-2.5 py-1 rounded-lg border text-xs font-semibold ${health.className}`}>
                          {health.label}
                        </span>
                      </td>
                      <td className="py-3 pr-4">
                        <span className={`px-2.5 py-1 rounded-lg border text-xs font-semibold ${hb.className}`}>
                          {hb.label}
                        </span>
                      </td>
                      <td className="py-3 pr-4" title="Completed steps / total steps">
                        {(a.quality.success_rate * 100).toFixed(1)}%
                      </td>
                      <td className="py-3 pr-4" title="Total prompt + completion tokens">
                        {(a.totals.total_tokens || 0).toLocaleString()}
                      </td>
                      <td className="py-3 pr-4">{a.totals.failed_steps}</td>
                      <td className="py-3 pr-4">
                        <span
                          className={`px-2 py-1 rounded-lg border text-xs font-semibold ${getStageBadgeClass(a.latest_runtime?.phase, a.latest_runtime?.status)}`}
                        >
                          {humanizeMetricText(a.latest_runtime?.phase || a.latest_runtime?.status || '-')}
                        </span>
                      </td>
                      <td className="py-3 pr-4 text-xs text-red-300/90 max-w-[320px]">
                        <span title={reasonInfo.hint}>
                          {reasonInfo.title === '-' ? '-' : reasonInfo.title}
                        </span>
                      </td>
                      <td className="py-3 pr-4 text-xs">
                        {String(reasonText).toLowerCase().includes('loop') || String(reasonText).toLowerCase().includes('drift') ? (
                          <span className="px-2 py-1 rounded-lg border border-amber-500/40 bg-amber-500/15 text-amber-300 font-semibold">Drift Risk</span>
                        ) : (
                          <span className="px-2 py-1 rounded-lg border border-dark-300 bg-dark-200/40 text-white/70 font-semibold">Low</span>
                        )}
                      </td>
                      <td className="py-3 pr-4">
                        {a.latest_runtime?.job_id ? (
                          <div className="flex items-center gap-2">
                            <button
                              onClick={() =>
                                setSelectedRuntime({
                                  agent_name: a.agent_name,
                                  agent_id: a.agent_id,
                                  runtime: a.latest_runtime || null,
                                })
                              }
                              className="px-3 py-1.5 rounded-lg bg-dark-200/60 border border-dark-300 text-white/80 text-xs font-semibold hover:bg-dark-200 transition-colors"
                            >
                              View Details
                            </button>
                            <button
                              onClick={() => {
                                const q = a.latest_runtime?.workflow_step_id
                                  ? `?focus_step=${a.latest_runtime.workflow_step_id}&mode=status`
                                  : ''
                                navigate(`/jobs/${a.latest_runtime!.job_id}${q}`)
                              }}
                              className="px-3 py-1.5 rounded-lg bg-primary-500/20 border border-primary-500/40 text-primary-200 text-xs font-semibold hover:bg-primary-500/30 transition-colors"
                            >
                              Open Runtime
                            </button>
                          </div>
                        ) : (
                          <span className="text-xs text-white/40">-</span>
                        )}
                      </td>
                    </tr>
                  )})}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
      {selectedRuntime && (
        <div className="fixed inset-0 z-50">
          <div className="absolute inset-0 bg-black/65" onClick={() => setSelectedRuntime(null)} />
          <div className="absolute right-0 top-0 h-full w-full max-w-xl bg-dark-100/95 backdrop-blur-xl border-l border-dark-200/70 shadow-2xl p-6 overflow-y-auto">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h3 className="text-xl font-black text-white">Runtime Details</h3>
                <p className="text-xs text-white/60 mt-1">{selectedRuntime.agent_name}</p>
              </div>
              <button
                onClick={() => setSelectedRuntime(null)}
                className="px-3 py-1.5 rounded-lg bg-dark-200/60 border border-dark-300 text-white/80 text-xs font-semibold hover:bg-dark-200"
              >
                Close
              </button>
            </div>

            {selectedRuntime.runtime ? (
              <div className="mt-5 space-y-4">
                <div className="rounded-xl border border-dark-200/60 bg-dark-200/30 p-4">
                  <p className="text-[11px] uppercase tracking-wide text-white/50 mb-2">Identifiers</p>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <p className="text-white/65">Job ID</p>
                    <p className="text-white">{selectedRuntime.runtime.job_id ?? '-'}</p>
                    <p className="text-white/65">Workflow Step ID</p>
                    <p className="text-white">{selectedRuntime.runtime.workflow_step_id ?? '-'}</p>
                    <p className="text-white/65">Step Order</p>
                    <p className="text-white">{selectedRuntime.runtime.step_order ?? '-'}</p>
                    <p className="text-white/65">Trace ID</p>
                    <p className="text-white break-all">{selectedRuntime.runtime.trace_id || '-'}</p>
                  </div>
                </div>

                <div className="rounded-xl border border-dark-200/60 bg-dark-200/30 p-4">
                  <p className="text-[11px] uppercase tracking-wide text-white/50 mb-2">Phase Timeline</p>
                  <div className="flex flex-wrap gap-2">
                    {phaseOrder.map((p) => {
                      const isCurrent = (selectedRuntime.runtime?.phase || '').toLowerCase() === p
                      const isPast = phaseOrder.indexOf((selectedRuntime.runtime?.phase || '').toLowerCase()) > phaseOrder.indexOf(p)
                      const cls = isCurrent
                        ? 'bg-primary-500/25 border-primary-500/50 text-primary-200'
                        : isPast
                          ? 'bg-green-500/15 border-green-500/40 text-green-300'
                          : 'bg-dark-200/50 border-dark-300 text-white/60'
                      return (
                        <span key={p} className={`px-2 py-1 rounded-lg border text-[11px] font-semibold ${cls}`}>
                          {phaseLabel(p)}
                        </span>
                      )
                    })}
                  </div>
                </div>

                <div className="rounded-xl border border-dark-200/60 bg-dark-200/30 p-4">
                  <p className="text-[11px] uppercase tracking-wide text-white/50 mb-2">Timing</p>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <p className="text-white/65">Started At</p>
                    <p className="text-white">{formatTimestamp(selectedRuntime.runtime.started_at)}</p>
                    <p className="text-white/65">Stuck Since</p>
                    <p className="text-white">{formatTimestamp(selectedRuntime.runtime.stuck_since)}</p>
                  </div>
                </div>

                <div className="rounded-xl border border-dark-200/60 bg-dark-200/30 p-4">
                  <p className="text-[11px] uppercase tracking-wide text-white/50 mb-2">Telemetry Detail</p>
                  {selectedRuntime.runtime.reason_detail &&
                  typeof selectedRuntime.runtime.reason_detail === 'object' &&
                  Object.keys(selectedRuntime.runtime.reason_detail).length > 0 ? (
                    <div className="space-y-2">
                      {Object.entries(selectedRuntime.runtime.reason_detail).map(([k, v]) => (
                        <div key={k} className="grid grid-cols-2 gap-2 text-xs">
                          <p className="text-white/65">{humanizeMetricText(k)}</p>
                          <p className="text-white break-all">{formatTelemetryValue(v)}</p>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-white/50">No additional runtime detail available for this event.</p>
                  )}
                </div>
              </div>
            ) : (
              <p className="mt-6 text-sm text-white/60">No runtime snapshot available.</p>
            )}
          </div>
        </div>
      )}

      <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl border border-dark-200/50 overflow-hidden">
        <div className="bg-gradient-to-r from-primary-600 to-primary-800 px-8 py-6 border-b border-primary-700/50">
          <div className="flex flex-col gap-4">
            <h2 className="text-3xl font-black text-white tracking-tight">List of Jobs</h2>
            <div className="flex flex-col gap-3">
              <div className="flex flex-col md:flex-row gap-3 md:items-center">
                <div className="relative flex-1 min-w-[280px]">
                  <span className="absolute inset-y-0 left-3 flex items-center text-white/40">
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-4.35-4.35M11 18a7 7 0 100-14 7 7 0 000 14z" />
                    </svg>
                  </span>
                  <input
                    type="text"
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                    placeholder="Search jobs by title or description..."
                    className="w-full pl-9 pr-10 py-2.5 rounded-xl bg-dark-100/80 border border-primary-500/40 text-sm text-white placeholder-white/40 focus:outline-none focus:ring-2 focus:ring-primary-400 focus:border-primary-400"
                  />
                  {searchTerm.trim().length > 0 && (
                    <button
                      type="button"
                      onClick={() => setSearchTerm('')}
                      className="absolute inset-y-0 right-2 my-auto h-7 w-7 rounded-lg text-white/60 hover:text-white hover:bg-dark-200/60 transition-colors"
                      title="Clear search"
                    >
                      ×
                    </button>
                  )}
                </div>
                <Link
                  to="/jobs/new"
                  className="bg-white text-primary-600 px-5 py-2.5 rounded-xl font-bold hover:bg-white/90 transition-all duration-200 shadow-xl hover:shadow-2xl hover:scale-105 flex items-center justify-center gap-2 whitespace-nowrap"
                >
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                  </svg>
                  New Job
                </Link>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[11px] uppercase tracking-wide text-white/55 font-semibold pr-1">Filters</span>
                <select
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value as any)}
                  className="px-3 py-2 rounded-xl bg-dark-100/80 border border-dark-300 text-xs font-semibold text-white/80 focus:outline-none focus:ring-2 focus:ring-primary-400"
                >
                  <option value="all">All statuses</option>
                  <option value="draft">Draft</option>
                  <option value="pending_approval">Pending approval</option>
                  <option value="approved">Approved</option>
                  <option value="in_progress">In progress</option>
                  <option value="completed">Completed</option>
                  <option value="failed">Failed</option>
                </select>
                <select
                  value={riskFilter}
                  onChange={(e) => setRiskFilter(e.target.value as any)}
                  className="px-3 py-2 rounded-xl bg-dark-100/80 border border-dark-300 text-xs font-semibold text-white/80 focus:outline-none focus:ring-2 focus:ring-primary-400"
                  title="Execution risk"
                >
                  <option value="all">All risk</option>
                  <option value="stable">Stable</option>
                  <option value="possible_drift">Possible drift</option>
                </select>
                <select
                  value={stageFilter}
                  onChange={(e) => setStageFilter(e.target.value)}
                  className="px-3 py-2 rounded-xl bg-dark-100/80 border border-dark-300 text-xs font-semibold text-white/80 focus:outline-none focus:ring-2 focus:ring-primary-400"
                  title="Current stage"
                >
                  <option value="all">All stage</option>
                  {stageOptions.map((s) => (
                    <option key={s} value={s}>
                      {humanizeMetricText(s)}
                    </option>
                  ))}
                </select>
                <select
                  value={heartbeatFilter}
                  onChange={(e) => setHeartbeatFilter(e.target.value as any)}
                  className="px-3 py-2 rounded-xl bg-dark-100/80 border border-dark-300 text-xs font-semibold text-white/80 focus:outline-none focus:ring-2 focus:ring-primary-400"
                  title="Live signal"
                >
                  <option value="all">All live signal</option>
                  <option value="healthy">Healthy</option>
                  <option value="delayed">Delayed</option>
                  <option value="stale">Stale</option>
                  <option value="stuck">Stuck</option>
                  <option value="failed">Failed</option>
                  <option value="completed">Completed</option>
                  <option value="no_signal">No signal</option>
                </select>
                <select
                  value={sortBy}
                  onChange={(e) => setSortBy(e.target.value as any)}
                  className="px-3 py-2 rounded-xl bg-dark-100/80 border border-dark-300 text-xs font-semibold text-white/80 focus:outline-none focus:ring-2 focus:ring-primary-400"
                  title="Sort jobs"
                >
                  <option value="created_desc">Newest created</option>
                  <option value="created_asc">Oldest created</option>
                  <option value="last_executed_desc">Last executed (latest)</option>
                  <option value="last_executed_asc">Last executed (oldest)</option>
                </select>
                <button
                  type="button"
                  onClick={() => {
                    setStatusFilter('all')
                    setRiskFilter('all')
                    setStageFilter('all')
                    setHeartbeatFilter('all')
                    setSortBy('created_desc')
                    setSearchTerm('')
                  }}
                  className="px-3 py-2 rounded-xl bg-dark-200/60 border border-dark-300 text-xs font-semibold text-white/80 hover:bg-dark-200 transition-colors"
                >
                  Reset
                </button>
              </div>
            </div>
          </div>
        </div>
        <div className="p-8">
          {filteredJobs.length === 0 ? (
            <div className="text-center py-16">
              <svg className="w-20 h-20 text-white/20 mx-auto mb-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              {jobs.length === 0 ? (
                <>
                  <p className="text-white/60 text-xl font-semibold mb-4">No jobs yet</p>
                  <Link
                    to="/jobs/new"
                    className="inline-block mt-4 text-primary-400 hover:text-primary-300 font-bold text-lg transition-colors"
                  >
                    Create your first job →
                  </Link>
                </>
              ) : (
                <p className="text-white/60 text-sm font-medium">
                  No jobs match your current search or status filter.
                </p>
              )}
            </div>
          ) : (
            <div className="space-y-4">
              {filteredJobs.map(({ job, metrics }) => {
                const isExpanded = expandedJobId === job.id
                const hasOutputs = job.workflow_steps && job.workflow_steps.some(step => step.status === 'completed' && step.output_data)
                
                const getStatusBadge = (status: string) => {
                  const statusMap: Record<string, { bg: string; text: string; border: string; icon: string }> = {
                    draft: { bg: 'bg-dark-200/50', text: 'text-white/80', border: 'border-dark-300', icon: '📝' },
                    pending_approval: { bg: 'bg-yellow-500/20', text: 'text-yellow-400', border: 'border-yellow-500/50', icon: '⏳' },
                    approved: { bg: 'bg-blue-500/20', text: 'text-blue-400', border: 'border-blue-500/50', icon: '✅' },
                    in_progress: { bg: 'bg-primary-500/20', text: 'text-primary-400', border: 'border-primary-500/50', icon: '⚙️' },
                    completed: { bg: 'bg-green-500/20', text: 'text-green-400', border: 'border-green-500/50', icon: '✓' },
                    failed: { bg: 'bg-red-500/20', text: 'text-red-400', border: 'border-red-500/50', icon: '✗' },
                  }
                  const statusInfo = statusMap[status] || statusMap.draft
                  return (
                    <span className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold border ${statusInfo.bg} ${statusInfo.text} ${statusInfo.border}`}>
                      <span>{statusInfo.icon}</span>
                      {status.replace('_', ' ').toUpperCase()}
                    </span>
                  )
                }
                
                return (
                  <div
                    key={job.id}
                    className="p-6 border border-dark-200/50 rounded-2xl hover:border-primary-500/50 hover:shadow-2xl transition-all duration-200 bg-dark-100/30 backdrop-blur-sm card-hover"
                  >
                    <div className="flex justify-between items-start gap-4">
                    <div className="flex-1 min-w-0">
                      <Link to={`/jobs/${job.id}`} className="block group">
                        <div className="flex items-start gap-4 mb-3">
                          <div className="p-3 bg-primary-500/20 rounded-xl group-hover:bg-primary-500/30 transition-colors border border-primary-500/30">
                            <svg className="w-6 h-6 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                            </svg>
                          </div>
                          <div className="flex-1 min-w-0">
                            <h3 className="font-black text-xl text-white group-hover:text-primary-400 transition-colors truncate mb-2">{job.title}</h3>
                            <div className="mt-1.5">
                              {getStatusBadge(job.status)}
                            </div>
                          </div>
                        </div>
                        {job.status === 'failed' && job.failure_reason && (
                          <div className="mt-4 p-4 bg-red-500/10 border-l-4 border-red-500 rounded-r-xl">
                            <p className="text-xs font-bold text-red-400 mb-1">⚠️ Failure Reason:</p>
                            <p className="text-xs text-red-300">{job.failure_reason}</p>
                          </div>
                        )}
                        <div className="mt-4 grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2">
                          <div className="px-3 py-2 rounded-xl bg-dark-50/50 border border-dark-200/40">
                            <p className="text-[10px] text-white/50 uppercase">Current Step</p>
                            <p className="text-xs font-bold text-primary-300 truncate">{humanizeMetricText(metrics.stage)}</p>
                          </div>
                          <div className="px-3 py-2 rounded-xl bg-dark-50/50 border border-dark-200/40">
                            <p className="text-[10px] text-white/50 uppercase">Failed Steps</p>
                            <p className="text-sm font-bold text-red-300">{metrics.failedSteps}</p>
                          </div>
                          <div className="px-3 py-2 rounded-xl bg-dark-50/50 border border-dark-200/40">
                            <p className="text-[10px] text-white/50 uppercase">Token Used</p>
                            <p className="text-sm font-bold text-white">
                              {metrics.hasTokenUsage ? metrics.tokenTotal.toLocaleString() : '-'}
                            </p>
                            {!metrics.hasTokenUsage && (
                              <p className="text-[10px] text-white/45 mt-0.5">Not reported by provider</p>
                            )}
                          </div>
                          <div className="px-3 py-2 rounded-xl bg-dark-50/50 border border-dark-200/40">
                            <p className="text-[10px] text-white/50 uppercase">Transactions</p>
                            <p className="text-sm font-bold text-white">{metrics.writeTransactions}</p>
                          </div>
                          <div className="px-3 py-2 rounded-xl bg-dark-50/50 border border-dark-200/40">
                            <p className="text-[10px] text-white/50 uppercase">Records</p>
                            <p className="text-sm font-bold text-white">{metrics.processedRecords}</p>
                          </div>
                          <div className="px-3 py-2 rounded-xl bg-dark-50/50 border border-dark-200/40">
                            <p className="text-[10px] text-white/50 uppercase">MCP Tools</p>
                            {metrics.toolsUsed.length > 0 ? (
                              <div
                                className="flex items-center gap-1 flex-wrap"
                                title={metrics.toolsUsed.map(prettifyToolName).join(', ')}
                              >
                                {metrics.toolsUsed.slice(0, 2).map((tool) => (
                                  <span
                                    key={tool}
                                    className="inline-flex items-center px-2 py-0.5 rounded-md bg-dark-200/60 border border-dark-300 text-[10px] font-semibold text-white/85"
                                  >
                                    {tool}
                                  </span>
                                ))}
                                {metrics.toolsUsed.length > 2 && (
                                  <span className="text-[10px] text-white/65 font-semibold">
                                    +{metrics.toolsUsed.length - 2} more
                                  </span>
                                )}
                              </div>
                            ) : (
                              <p className="text-xs font-bold text-white">-</p>
                            )}
                          </div>
                        </div>
                        <div className="mt-2 grid grid-cols-1 md:grid-cols-3 gap-2">
                          <div className="px-3 py-2 rounded-xl bg-dark-50/50 border border-dark-200/40">
                              <p className="text-[10px] text-white/50 uppercase">Live Signal</p>
                            <p className="text-xs font-bold text-white">
                              {getHeartbeatBadge(metrics.latestHeartbeatAt, job.status, null).label}
                            </p>
                          </div>
                          <div className="px-3 py-2 rounded-xl bg-dark-50/50 border border-dark-200/40">
                            <p className="text-[10px] text-white/50 uppercase">Tool Coverage</p>
                            <p className={`text-xs font-bold ${metrics.toolAnomaly ? 'text-amber-300' : 'text-white'}`}>
                              {metrics.toolCoverage}
                            </p>
                          </div>
                          <div className="px-3 py-2 rounded-xl bg-dark-50/50 border border-dark-200/40">
                            <p className="text-[10px] text-white/50 uppercase">Execution Risk</p>
                            <p className={`text-xs font-bold ${metrics.driftRisk ? 'text-amber-300' : 'text-green-300'}`}>
                              {metrics.driftRisk ? 'Possible Drift' : 'Stable'}
                            </p>
                          </div>
                        </div>
                        <p className="mt-2 text-xs text-red-300/90">Last reason: {humanizeMetricText(metrics.reason)}</p>
                      </Link>
                      {hasOutputs && (
                        <button
                          onClick={() => setExpandedJobId(isExpanded ? null : job.id)}
                          className="mt-4 inline-flex items-center gap-2 text-sm text-primary-400 hover:text-primary-300 font-bold transition-colors"
                        >
                          <svg className={`w-4 h-4 transition-transform ${isExpanded ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                          </svg>
                          {isExpanded ? 'Hide' : 'Show'} AI Agent Outputs
                        </button>
                      )}
                    </div>
                    <div className="flex items-start gap-4">
                      <div className="text-right">
                        <div className="flex items-baseline gap-1">
                          <span className="text-3xl font-black text-white">${job.total_cost.toFixed(2)}</span>
                        </div>
                        <p className="text-xs text-white/50 mt-1 font-medium">
                          {new Date(job.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                        </p>
                      </div>
                      <div className="flex flex-col gap-2">
                        <button
                          onClick={async () => {
                            try {
                              const { share_url } = await jobsAPI.getShareLink(job.id)
                              await navigator.clipboard.writeText(share_url)
                              alert('Share link copied to clipboard! Anyone with this link can view the job (no login required).')
                            } catch (e) {
                              console.error(e)
                              alert('Failed to get share link')
                            }
                          }}
                          className="px-4 py-2 text-xs font-bold text-white bg-primary-500/20 border border-primary-500/50 rounded-xl hover:bg-primary-500/30 hover:border-primary-400 transition-all duration-200 flex items-center gap-2"
                          title="Copy share link"
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z" />
                          </svg>
                          Share
                        </button>
                        <button
                          onClick={() => navigate(`/jobs/edit/${job.id}`)}
                          className="px-4 py-2 text-xs font-bold text-white bg-primary-500/20 border border-primary-500/50 rounded-xl hover:bg-primary-500/30 hover:border-primary-400 transition-all duration-200 flex items-center gap-2"
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                          </svg>
                          Edit
                        </button>
                        {(job.status === 'failed' || job.status === 'cancelled') && (
                          <button
                            onClick={async () => {
                              if (window.confirm('Are you sure you want to rerun this job? This will reset the workflow and execute it again.')) {
                                try {
                                  await jobsAPI.rerun(job.id)
                                  loadData()
                                  navigate(`/jobs/${job.id}`)
                                } catch (error) {
                                  console.error('Failed to rerun job:', error)
                                  alert('Failed to rerun job. Only completed or failed jobs can be rerun.')
                                }
                              }
                            }}
                            className="px-4 py-2 text-xs font-bold text-white bg-green-500/20 border border-green-500/50 rounded-xl hover:bg-green-500/30 hover:border-green-400 transition-all duration-200 flex items-center gap-2"
                          >
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                            </svg>
                            Rerun
                          </button>
                        )}
                        <button
                          onClick={async () => {
                            const confirmMessage = job.status === 'draft' 
                              ? 'Are you sure you want to delete this job? This action cannot be undone.'
                              : `Are you sure you want to delete this ${job.status} job? This action cannot be undone.`;
                            
                            if (window.confirm(confirmMessage)) {
                              try {
                                await jobsAPI.delete(job.id)
                                loadData()
                              } catch (error: any) {
                                console.error('Failed to delete job:', error)
                                const errorMessage = error.response?.data?.detail || 'Failed to delete job. Please try again.'
                                alert(errorMessage)
                              }
                            }
                          }}
                          className="px-4 py-2 text-xs font-bold text-white bg-red-500/20 border border-red-500/50 rounded-xl hover:bg-red-500/30 hover:border-red-400 transition-all duration-200 flex items-center gap-2"
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                          Delete
                        </button>
                      </div>
                    </div>
                    </div>
                  
                  {isExpanded && job.workflow_steps && job.workflow_steps.length > 0 && (
                    <div className="mt-6 pt-6 border-t border-dark-200/50 animate-fadeIn">
                      <h4 className="font-black mb-5 text-base text-white flex items-center gap-2">
                        <svg className="w-5 h-5 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                        AI Agent Outputs
                      </h4>
                      <div className="space-y-4">
                        {job.workflow_steps.map((step) => {
                          let outputData = null
                          try {
                            if (step.output_data) {
                              outputData = typeof step.output_data === 'string' 
                                ? JSON.parse(step.output_data) 
                                : step.output_data
                            }
                          } catch (e) {
                            outputData = step.output_data
                          }
                          
                          if (step.status !== 'completed' || !outputData) return null
                          
                          return (
                            <div key={step.id} className="p-5 bg-green-500/10 border border-green-500/30 rounded-2xl backdrop-blur-sm">
                              <div className="flex items-center justify-between mb-4">
                                <span className="inline-flex items-center gap-2 px-3 py-1.5 bg-green-500/20 rounded-full text-xs font-bold text-green-400 border border-green-500/50">
                                  <span className="w-2 h-2 bg-green-500 rounded-full"></span>
                                  Step {step.step_order}
                                </span>
                                <span className="text-xs text-white/50 font-medium">
                                  {step.completed_at && new Date(step.completed_at).toLocaleString()}
                                </span>
                              </div>
                              <div className="text-sm text-white/90 bg-dark-50/50 p-4 rounded-xl border border-dark-200/50">
                                <pre className="text-xs overflow-auto max-h-48 font-mono text-white/80 whitespace-pre-wrap">
                                  {getStepOutputDisplayText(outputData)}
                                </pre>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                      {!hasOutputs && (
                        <p className="text-sm text-white/40 italic text-center py-6 font-medium">No completed outputs yet</p>
                      )}
                    </div>
                  )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
