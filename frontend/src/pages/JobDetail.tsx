import { useCallback, useEffect, useState } from 'react'
import { useParams, useSearchParams, useNavigate, useLocation } from 'react-router-dom'
import { jobsAPI, mcpAPI } from '../lib/api'
import type { Job, WorkflowPreview, WorkflowStep, JobSchedule, PlannerPipelineBundle } from '../lib/types'
import SchedulePicker, { humanReadableSchedule } from '../components/SchedulePicker'
import { WorkflowBuilder } from '../components/WorkflowBuilder'
import { CostCalculator } from '../components/CostCalculator'
import { JobStatusTracker } from '../components/JobStatusTracker'
import { DocumentConversation } from '../components/DocumentConversation'
import { buildJobDetailSharedToolWarning } from '../lib/independentWorkflowSharedTools'
import { filterByJobAllowedIds, jobHasExplicitMcpScope } from '../lib/jobMcpScope'

function ppStr(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  return ''
}

function ppStrList(v: unknown): string[] {
  if (!Array.isArray(v)) return []
  return v.map(ppStr).filter((s) => s.length > 0)
}

type StepToolVisibilityUi = 'full' | 'names_only' | 'none'

/** Step override, else job default, else full (legacy rows with no stored visibility). */
function effectiveStepToolVisibility(step: WorkflowStep, job: Job | null | undefined): StepToolVisibilityUi {
  const v = step.tool_visibility ?? job?.tool_visibility
  if (v === 'full' || v === 'names_only' || v === 'none') return v
  return 'full'
}

export default function JobDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const location = useLocation()
  const selectedAgentsFromCreate = (location.state as { selectedAgents?: number[] })?.selectedAgents
  const jobId = parseInt(id || '0')
  const [job, setJob] = useState<Job | null>(null)
  const [workflowPreview, setWorkflowPreview] = useState<WorkflowPreview | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isLoadingPreview, setIsLoadingPreview] = useState(false)
  const [previewError, setPreviewError] = useState('')
  const [isAnalyzingDocuments, setIsAnalyzingDocuments] = useState(false)
  const [analyzeFeedback, setAnalyzeFeedback] = useState<string>('')
  const [mode, setMode] = useState<'workflow' | 'preview' | 'status' | 'qa'>('workflow')
  const [editingStepTools, setEditingStepTools] = useState<WorkflowStep | null>(null)
  const [stepToolsModalPlatform, setStepToolsModalPlatform] = useState<{ id: number; name: string; tool_type: string }[]>([])
  const [stepToolsModalConnections, setStepToolsModalConnections] = useState<{ id: number; name: string }[]>([])
  const [stepToolsSelection, setStepToolsSelection] = useState<{ platformIds: number[]; connectionIds: number[]; toolVisibility?: 'full' | 'names_only' | 'none' }>({ platformIds: [], connectionIds: [] })
  const [savingStepTools, setSavingStepTools] = useState(false)
  /** Loaded for showing tool names on completed job "Tools per step" */
  const [platformToolsList, setPlatformToolsList] = useState<{ id: number; name: string; tool_type: string }[]>([])
  const [schedule, setSchedule] = useState<JobSchedule | null>(null)
  const [showScheduleLater, setShowScheduleLater] = useState(false)
  const [scheduleData, setScheduleData] = useState({
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
    scheduledAt: null as string | null,
    status: 'active' as 'active' | 'inactive',
  })
  const [isSavingSchedule, setIsSavingSchedule] = useState(false)
  const [countdown, setCountdown] = useState<number | null>(null)
  const [plannerSectionOpen, setPlannerSectionOpen] = useState(false)
  const [plannerStatus, setPlannerStatus] = useState<{
    configured: boolean
    provider?: string
    model?: string
    base_url_configured?: boolean
  } | null>(null)
  const [plannerArtifacts, setPlannerArtifacts] = useState<
    Array<{
      id: number
      artifact_type: string
      storage: string
      byte_size: number
      created_at: string
    }>
  >([])
  const [plannerSupportLoading, setPlannerSupportLoading] = useState(false)
  const [plannerRawModal, setPlannerRawModal] = useState<{ title: string; body: string } | null>(null)
  const [plannerPipeline, setPlannerPipeline] = useState<PlannerPipelineBundle | null>(null)
  const [plannerPipelineError, setPlannerPipelineError] = useState<string | null>(null)

  useEffect(() => {
    if (jobId) {
      loadJob()
      loadSchedule()
    }
    // Check if Q&A mode should be shown from URL params
    if (searchParams.get('qa') === 'true') {
      setMode('qa')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, searchParams])

  useEffect(() => {
    if (job && mode === 'preview') {
      loadWorkflowPreview()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job, mode])

  useEffect(() => {
    if (job?.workflow_steps?.length && job.status !== 'draft') {
      const hasStepTools = job.workflow_steps.some(
        (s) => (s.allowed_platform_tool_ids?.length ?? 0) > 0 || (s.allowed_connection_ids?.length ?? 0) > 0
      )
      if (hasStepTools) {
        mcpAPI.listTools().then((tools) => setPlatformToolsList(tools)).catch(() => {})
      }
    }
  }, [job?.id, job?.status, job?.workflow_steps?.length])

  const loadJob = async () => {
    setIsLoading(true)
    try {
      const data = await jobsAPI.get(jobId)
      setJob(data)
      if (data.status !== 'draft') {
        setMode('status')
      } else if (data.files && data.files.length > 0) {
        // If there are files, show Q&A mode by default
        // Also check if Q&A mode is requested via URL param
        if (searchParams.get('qa') === 'true' || (data.conversation && data.conversation.length > 0)) {
          setMode('qa')
        }
      }
    } catch (error) {
      console.error('Failed to load job:', error)
    } finally {
      setIsLoading(false)
    }
  }

  const loadWorkflowPreview = async () => {
    setIsLoadingPreview(true)
    setPreviewError('')
    try {
      const preview = await jobsAPI.previewWorkflow(jobId)
      setWorkflowPreview(preview)
    } catch (error) {
      console.error('Failed to load workflow preview:', error)
      setPreviewError((error as any)?.response?.data?.detail || 'Failed to load preview cost')
      setWorkflowPreview(null)
    } finally {
      setIsLoadingPreview(false)
    }
  }

  const handleApprove = async () => {
    if (!job) return
    try {
      await jobsAPI.approve(jobId)
      await loadJob()
    } catch (error) {
      console.error('Failed to approve job:', error)
    }
  }

  const [showExecuteConfirm, setShowExecuteConfirm] = useState(false)

  const handleExecute = async () => {
    if (!job) return
    try {
      await jobsAPI.execute(jobId)
      setShowExecuteConfirm(false)
      await loadJob()
      setMode('status')
    } catch (error) {
      console.error('Failed to execute job:', error)
    }
  }

  const handleAnalyzeDocuments = async () => {
    if (!job?.files?.length) return
    setIsAnalyzingDocuments(true)
    setAnalyzeFeedback('')
    try {
      const result = await jobsAPI.analyzeDocuments(jobId)
      const questionCount = Array.isArray(result?.questions) ? result.questions.length : 0
      if (questionCount > 0) {
        setAnalyzeFeedback(`Document analysis complete. ${questionCount} clarification question${questionCount > 1 ? 's' : ''} generated.`)
      } else {
        setAnalyzeFeedback('Document analysis complete. No clarification questions needed; requirements look complete.')
      }
      await loadJob()
      // Always move user to Q&A/analysis panel so they can see outcomes.
      setMode('qa')
    } catch (error) {
      console.error('Failed to analyze documents:', error)
      alert((error as any)?.response?.data?.detail || 'Failed to analyze documents')
    } finally {
      setIsAnalyzingDocuments(false)
    }
  }

  const openStepToolsModal = async (step: WorkflowStep) => {
    let latestJob: Job | null = job
    try {
      latestJob = await jobsAPI.get(jobId)
      setJob(latestJob)
    } catch {
      // keep cached job
    }
    const stepFromServer = latestJob?.workflow_steps?.find((s) => s.id === step.id) ?? step
    setEditingStepTools(stepFromServer)
    setStepToolsSelection({
      platformIds: stepFromServer.allowed_platform_tool_ids ?? [],
      connectionIds: stepFromServer.allowed_connection_ids ?? [],
      toolVisibility: effectiveStepToolVisibility(stepFromServer, latestJob),
    })
    try {
      const [tools, conns] = await Promise.all([mcpAPI.listTools(), mcpAPI.listConnections()])
      const allowedPlatform = latestJob?.allowed_platform_tool_ids
      const allowedConn = latestJob?.allowed_connection_ids
      setStepToolsModalPlatform(filterByJobAllowedIds(tools, allowedPlatform))
      setStepToolsModalConnections(filterByJobAllowedIds(conns, allowedConn))
    } catch (e) {
      console.error(e)
    }
  }

  const saveStepTools = async () => {
    if (!editingStepTools) return
    setSavingStepTools(true)
    try {
      await jobsAPI.updateStepTools(jobId, editingStepTools.id, {
        allowed_platform_tool_ids: stepToolsSelection.platformIds,
        allowed_connection_ids: stepToolsSelection.connectionIds,
        tool_visibility: stepToolsSelection.toolVisibility,
      })
      await loadJob()
      await loadWorkflowPreview()
      setEditingStepTools(null)
    } catch (e) {
      console.error(e)
      alert((e as any)?.response?.data?.detail || 'Failed to update step tools')
    } finally {
      setSavingStepTools(false)

      }
    }
  const loadSchedule = async () => {
    try {
      const data = await jobsAPI.getSchedule(jobId)
      setSchedule(data)
    } catch {
      // 404 = no schedule yet — not an error
      setSchedule(null)
    }
  }

  const handleScheduleLater = async () => {
    setIsSavingSchedule(true)
    try {
      const payload = {
        scheduled_at: scheduleData.scheduledAt || undefined,
        timezone: scheduleData.timezone,
        status: scheduleData.status,
      }

      if (schedule) {
        const result = await jobsAPI.updateSchedule(jobId, payload)
        setSchedule(result.data)
      } else {
        const result = await jobsAPI.createSchedule(jobId, { ...payload, scheduled_at: payload.scheduled_at! })
        setSchedule(result.data)
      }
      setShowScheduleLater(false)
      await loadJob()
    } catch (error) {
      console.error('Failed to save schedule:', error)
    } finally {
      setIsSavingSchedule(false)
    }
  }

  const handleRerun = async () => {
    if (!job) return
    if (!window.confirm('Are you sure you want to rerun this job? This will reset the workflow and execute it again.')) {
      return
    }
    try {
      await jobsAPI.rerun(jobId)
      await loadJob()
      setMode('status')
    } catch (error) {
      console.error('Failed to rerun job:', error)
      alert('Failed to rerun job. Only failed or cancelled jobs can be rerun.')
    }
  }

  const handleCancel = async () => {
    if (!job) return
    if (!window.confirm('Are you sure you want to cancel this job? This will stop execution.')) {
      return
    }
    try {
      await jobsAPI.cancel(jobId)
      await loadJob()
    } catch (error) {
      console.error('Failed to cancel job:', error)
      alert('Failed to cancel job.')
    }
  }

  // Countdown timer for in_queue jobs
  useEffect(() => {
    if (job?.status !== 'in_queue' || !job.scheduled_at) {
      setCountdown(null)
      return
    }
    const targetTime = new Date(job.scheduled_at).getTime()
    const tick = () => {
      const remaining = Math.max(0, Math.floor((targetTime - Date.now()) / 1000))
      setCountdown(remaining)
      if (remaining <= 0) {
        // Schedule passed — start polling for status change
        loadJob()
      }
    }
    tick()
    const timer = setInterval(tick, 1000)
    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.status, job?.scheduled_at])

  useEffect(() => {
    if (!jobId || !plannerSectionOpen) return
    let cancelled = false
    setPlannerSupportLoading(true)
    setPlannerPipeline(null)
    setPlannerPipelineError(null)
    ;(async () => {
      try {
        const [stRes, listRes, pipeRes] = await Promise.allSettled([
          jobsAPI.getAgentPlannerStatus(),
          jobsAPI.listPlannerArtifacts(jobId),
          jobsAPI.getPlannerPipeline(jobId),
        ])
        if (cancelled) return
        if (stRes.status === 'fulfilled') setPlannerStatus(stRes.value)
        else setPlannerStatus(null)
        if (listRes.status === 'fulfilled') {
          setPlannerArtifacts(Array.isArray(listRes.value?.items) ? listRes.value.items : [])
        } else {
          setPlannerArtifacts([])
        }
        if (pipeRes.status === 'fulfilled') {
          setPlannerPipeline(pipeRes.value)
          setPlannerPipelineError(null)
        } else {
          setPlannerPipeline(null)
          setPlannerPipelineError('Could not load combined planning view.')
        }
      } finally {
        if (!cancelled) setPlannerSupportLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [jobId, plannerSectionOpen])

  const refreshPlannerPipelineIfOpen = useCallback(async () => {
    if (!jobId || !plannerSectionOpen) return
    try {
      const pipe = await jobsAPI.getPlannerPipeline(jobId)
      setPlannerPipeline(pipe)
      setPlannerPipelineError(null)
    } catch {
      setPlannerPipeline(null)
      setPlannerPipelineError('Could not load combined planning view.')
    }
  }, [jobId, plannerSectionOpen])

  if (isLoading) {
    return (
      <div className="container mx-auto px-4 py-8 min-h-screen">
        <div className="flex items-center justify-center min-h-[400px]">
          <div className="text-center">
            <div className="inline-block animate-spin rounded-full h-12 w-12 border-4 border-primary-500 border-t-transparent mb-4"></div>
            <p className="text-white/60 text-lg font-semibold">Loading job details...</p>
          </div>
        </div>
      </div>
    )
  }

  if (!job) {
    return (
      <div className="container mx-auto px-4 py-8 min-h-screen">
        <div className="text-center py-16">
          <svg className="w-20 h-20 text-white/20 mx-auto mb-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <p className="text-white/60 text-xl font-semibold">Job not found</p>
        </div>
      </div>
    )
  }

  const getStatusBadge = (status: string) => {
    const statusMap: Record<string, { bg: string; text: string; border: string; icon: string }> = {
      draft: { bg: 'bg-dark-200/50', text: 'text-white/80', border: 'border-dark-300', icon: '📝' },
      pending_approval: { bg: 'bg-yellow-500/20', text: 'text-yellow-400', border: 'border-yellow-500/50', icon: '⏳' },
      approved: { bg: 'bg-blue-500/20', text: 'text-blue-400', border: 'border-blue-500/50', icon: '✅' },
      in_queue: { bg: 'bg-cyan-500/20', text: 'text-cyan-400', border: 'border-cyan-500/50', icon: '🕐' },
      in_progress: { bg: 'bg-primary-500/20', text: 'text-primary-400', border: 'border-primary-500/50', icon: '⚙️' },
      completed: { bg: 'bg-green-500/20', text: 'text-green-400', border: 'border-green-500/50', icon: '✓' },
      failed: { bg: 'bg-red-500/20', text: 'text-red-400', border: 'border-red-500/50', icon: '✗' },
      cancelled: { bg: 'bg-orange-500/20', text: 'text-orange-400', border: 'border-orange-500/50', icon: '⊘' },
    }
    const statusInfo = statusMap[status] || statusMap.draft
    return (
      <span className={`inline-flex items-center gap-1.5 px-4 py-2 rounded-full text-sm font-bold border ${statusInfo.bg} ${statusInfo.text} ${statusInfo.border}`}>
        <span>{statusInfo.icon}</span>
        {status.replace('_', ' ').toUpperCase()}
      </span>
    )
  }

  return (
    <div className="container mx-auto px-4 py-8 min-h-screen">
      <div className="max-w-6xl mx-auto">
        <div className="mb-8 bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50">
          <div className="mb-6 flex flex-wrap gap-3">
            {job.status === 'draft' && (
              <button
                onClick={() => navigate(`/jobs/edit/${jobId}`, {
                  state: {
                    selectedAgents:
                      selectedAgentsFromCreate ??
                      (job.workflow_steps?.length
                        ? [...new Set(job.workflow_steps.map((s) => s.agent_id))]
                        : []),
                  },
                })}
                className="flex items-center text-white/70 hover:text-white transition-all duration-200 px-4 py-2.5 rounded-xl hover:bg-dark-200/50"
              >
                <svg className="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 15l-3-3m0 0l3-3m-3 3h8M3 12a9 9 0 1118 0 9 9 0 01-18 0z" />
                </svg>
                <span className="font-semibold">Edit Job</span>
              </button>
            )}
            <button
              onClick={async () => {
                try {
                  const { share_url } = await jobsAPI.getShareLink(jobId)
                  await navigator.clipboard.writeText(share_url)
                  alert('Share link copied to clipboard! Anyone with this link can view the job (no login required).')
                } catch (e) {
                  console.error(e)
                  alert('Failed to get share link')
                }
              }}
              className="flex items-center text-white/70 hover:text-white transition-all duration-200 px-4 py-2.5 rounded-xl hover:bg-dark-200/50"
            >
              <svg className="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z" />
              </svg>
              <span className="font-semibold">Share (External Link)</span>
            </button>
            <button
              onClick={() => navigate('/dashboard')}
              className="flex items-center text-white/70 hover:text-white transition-all duration-200 px-4 py-2.5 rounded-xl hover:bg-dark-200/50"
            >
              <svg className="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
              </svg>
              <span className="font-semibold">Back to Dashboard</span>
            </button>
          </div>
          <div className="flex items-start justify-between gap-4 mb-6">
            <div className="flex-1">
              <h1 className="text-6xl font-black text-white tracking-tight mb-4">
                {job.title}
              </h1>
              <p className="text-white/70 text-xl mb-6 font-medium leading-relaxed">{job.description}</p>
              <div className="flex items-center gap-4">
                {getStatusBadge(job.status)}
                {job.files && job.files.length > 0 && (
                  <span className="inline-flex items-center gap-2 text-sm text-primary-400 bg-primary-500/20 px-4 py-2 rounded-full border border-primary-500/50 font-bold">
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                    </svg>
                    {job.files.length} document{job.files.length > 1 ? 's' : ''} attached
                  </span>
                )}
              </div>
            </div>
          </div>
          {job.files && job.files.length > 0 && (
            <div className="mt-8 pt-8 border-t border-dark-200/50">
              <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
                <h3 className="text-2xl font-black text-white flex items-center gap-3">
                  <svg className="w-6 h-6 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                  Uploaded Documents
                </h3>
                {job.status === 'draft' && (
                  <button
                    onClick={handleAnalyzeDocuments}
                    disabled={isAnalyzingDocuments}
                    className="px-6 py-3 bg-gradient-to-r from-blue-500 to-blue-700 text-white rounded-xl font-bold hover:shadow-2xl hover:shadow-blue-500/50 hover:scale-105 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100 flex items-center gap-2"
                  >
                    {isAnalyzingDocuments ? (
                      <>
                        <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent" />
                        Analyzing...
                      </>
                    ) : (
                      <>
                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
                        </svg>
                        Analyze Documents (optional)
                      </>
                    )}
                  </button>
                )}
              </div>
              {analyzeFeedback && (
                <div className="mb-4 p-4 bg-blue-500/15 border border-blue-500/40 rounded-xl text-blue-300 font-semibold">
                  {analyzeFeedback}
                </div>
              )}
              <div className="grid gap-4">
                {job.files.map((file) => (
                  <div
                    key={file.id}
                    className="flex items-center justify-between p-5 bg-dark-200/30 rounded-2xl border border-dark-200/50 hover:border-primary-500/50 hover:shadow-2xl transition-all duration-200 backdrop-blur-sm"
                  >
                    <div className="flex items-center gap-4">
                      <div className="p-4 bg-primary-500/20 rounded-xl border border-primary-500/30">
                        <svg className="w-7 h-7 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                        </svg>
                      </div>
                      <div>
                        <p className="text-base font-bold text-white">{file.name}</p>
                        <p className="text-xs text-white/50 mt-1 font-medium">
                          {file.type} • {(file.size / 1024).toFixed(2)} KB
                        </p>
                      </div>
                    </div>
                    <button
                      onClick={async () => {
                        try {
                          const blob = await jobsAPI.downloadFile(job.id, file.id)
                          const url = window.URL.createObjectURL(blob)
                          const a = document.createElement('a')
                          a.href = url
                          a.download = file.name
                          document.body.appendChild(a)
                          a.click()
                          window.URL.revokeObjectURL(url)
                          document.body.removeChild(a)
                        } catch (error) {
                          console.error('Failed to download file:', error)
                          alert('Failed to download file')
                        }
                      }}
                      className="px-5 py-3 bg-gradient-to-r from-primary-500 to-primary-700 text-white text-sm font-bold rounded-xl hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200 flex items-center gap-2"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                      </svg>
                      Download
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="mb-8 bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl border border-dark-200/50 overflow-hidden">
          <button
            type="button"
            onClick={() => setPlannerSectionOpen((o) => !o)}
            className="w-full flex items-center justify-between gap-4 px-6 py-4 text-left hover:bg-dark-200/30 transition-colors"
          >
            <span className="text-white font-bold text-lg">Agent planner &amp; audit artifacts</span>
            <span className="text-white/50 text-sm font-semibold">{plannerSectionOpen ? 'Hide' : 'Show'}</span>
          </button>
          {plannerSectionOpen && (
            <div className="px-6 pb-6 pt-0 border-t border-dark-200/50">
              {plannerSupportLoading ? (
                <p className="text-white/60 py-4">Loading planner status and artifacts…</p>
              ) : (
                <>
                  <div className="py-4 space-y-2 text-white/80 text-sm">
                    <p className="font-semibold text-white">Platform planner</p>
                    {plannerStatus ? (
                      <ul className="list-disc list-inside space-y-1 text-white/70">
                        <li>Configured: {plannerStatus.configured ? 'yes' : 'no'}</li>
                        {plannerStatus.provider != null && <li>Provider: {plannerStatus.provider}</li>}
                        {plannerStatus.model != null && <li>Model: {plannerStatus.model}</li>}
                        {plannerStatus.base_url_configured != null && (
                          <li>Base URL set: {plannerStatus.base_url_configured ? 'yes' : 'no'}</li>
                        )}
                      </ul>
                    ) : (
                      <p className="text-white/50">Could not load planner status.</p>
                    )}
                  </div>

                  <div className="mt-6 pt-6 border-t border-dark-200/50">
                    <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
                      <div>
                        <p className="font-semibold text-white text-sm">Planning overview</p>
                        <p className="text-white/50 text-xs mt-0.5">
                          Latest BRD analysis, auto-split tasks, and tool suggestions in one place. Export downloads the same JSON as the API.
                        </p>
                      </div>
                      <button
                        type="button"
                        disabled={!plannerPipeline}
                        onClick={() => {
                          if (!plannerPipeline) return
                          const blob = new Blob([JSON.stringify(plannerPipeline, null, 2)], {
                            type: 'application/json',
                          })
                          const url = window.URL.createObjectURL(blob)
                          const a = document.createElement('a')
                          a.href = url
                          a.download = `job-${jobId}-planner-pipeline.json`
                          document.body.appendChild(a)
                          a.click()
                          window.URL.revokeObjectURL(url)
                          document.body.removeChild(a)
                        }}
                        className="shrink-0 px-4 py-2 rounded-xl text-sm font-bold bg-dark-200/70 text-white border border-dark-300 hover:bg-dark-200 disabled:opacity-40 disabled:pointer-events-none"
                      >
                        Export combined JSON
                      </button>
                    </div>
                    {plannerPipelineError && (
                      <p className="text-amber-400/90 text-sm mb-3">{plannerPipelineError}</p>
                    )}
                    {plannerPipeline && (
                      <div className="space-y-5">
                        <section className="rounded-xl border border-dark-200/50 bg-dark-200/20 p-4">
                          <h4 className="text-white font-bold text-sm mb-2">BRD analysis</h4>
                          {plannerPipeline.artifact_ids?.brd_analysis != null &&
                          plannerPipeline.brd_analysis == null ? (
                            <p className="text-amber-400/90 text-xs">
                              Latest artifact is stored but JSON could not be read. Open raw from the table below.
                            </p>
                          ) : plannerPipeline.brd_analysis ? (
                            <div className="space-y-3 text-sm text-white/85">
                              {ppStr(plannerPipeline.brd_analysis.analysis) ? (
                                <div>
                                  <p className="text-white/50 text-xs font-semibold uppercase tracking-wide mb-1">
                                    Analysis
                                  </p>
                                  <div className="max-h-48 overflow-y-auto rounded-lg bg-dark-100/50 p-3 text-white/90 whitespace-pre-wrap">
                                    {ppStr(plannerPipeline.brd_analysis.analysis)}
                                  </div>
                                </div>
                              ) : null}
                              {ppStrList(plannerPipeline.brd_analysis.questions).length > 0 ? (
                                <div>
                                  <p className="text-white/50 text-xs font-semibold uppercase tracking-wide mb-1">
                                    Questions
                                  </p>
                                  <ul className="list-disc list-inside space-y-1 text-white/80">
                                    {ppStrList(plannerPipeline.brd_analysis.questions).map((q, i) => (
                                      <li key={i}>{q}</li>
                                    ))}
                                  </ul>
                                </div>
                              ) : null}
                              {ppStrList(plannerPipeline.brd_analysis.recommendations).length > 0 ? (
                                <div>
                                  <p className="text-white/50 text-xs font-semibold uppercase tracking-wide mb-1">
                                    Recommendations
                                  </p>
                                  <ul className="list-disc list-inside space-y-1 text-white/80">
                                    {ppStrList(plannerPipeline.brd_analysis.recommendations).map((r, i) => (
                                      <li key={i}>{r}</li>
                                    ))}
                                  </ul>
                                </div>
                              ) : null}
                              {!ppStr(plannerPipeline.brd_analysis.analysis) &&
                                ppStrList(plannerPipeline.brd_analysis.questions).length === 0 &&
                                ppStrList(plannerPipeline.brd_analysis.recommendations).length === 0 && (
                                  <p className="text-white/50 text-xs">No summary fields in this artifact.</p>
                                )}
                            </div>
                          ) : (
                            <p className="text-white/50 text-xs">No BRD analysis artifact for this job yet.</p>
                          )}
                        </section>

                        <section className="rounded-xl border border-dark-200/50 bg-dark-200/20 p-4">
                          <h4 className="text-white font-bold text-sm mb-2">Task split (auto-split)</h4>
                          {plannerPipeline.artifact_ids?.task_split != null &&
                          plannerPipeline.task_split == null ? (
                            <p className="text-amber-400/90 text-xs">
                              Latest artifact is stored but JSON could not be read. Open raw from the table below.
                            </p>
                          ) : plannerPipeline.task_split &&
                            Array.isArray(plannerPipeline.task_split.parsed_assignments) ? (
                            <ol className="space-y-3 list-decimal list-inside text-sm text-white/85">
                              {(plannerPipeline.task_split.parsed_assignments as Record<string, unknown>[]).map(
                                (row, idx) => {
                                  const reason = ppStr(row.assignment_reason)
                                  const docs = Array.isArray(row.assigned_document_ids)
                                    ? (row.assigned_document_ids as unknown[]).map(ppStr).filter(Boolean)
                                    : []
                                  return (
                                    <li key={idx} className="pl-1">
                                      <span className="font-semibold text-white">
                                        Agent index {ppStr(row.agent_index) || String(idx)}
                                      </span>
                                      {ppStr(row.task) ? (
                                        <p className="mt-1 text-white/80 whitespace-pre-wrap">{ppStr(row.task)}</p>
                                      ) : null}
                                      {docs.length > 0 ? (
                                        <p className="mt-1 text-xs text-white/55">
                                          Documents: {docs.join(', ')}
                                        </p>
                                      ) : null}
                                      {reason ? (
                                        <p className="mt-2 text-xs text-primary-300/90 border-l-2 border-primary-500/40 pl-2">
                                          <span className="text-white/50 font-semibold">Why this agent: </span>
                                          {reason}
                                        </p>
                                      ) : null}
                                    </li>
                                  )
                                },
                              )}
                            </ol>
                          ) : plannerPipeline.task_split ? (
                            <p className="text-white/50 text-xs">
                              No parsed_assignments in the latest task split. Use View on the artifact row for full JSON.
                            </p>
                          ) : (
                            <p className="text-white/50 text-xs">No task split artifact for this job yet.</p>
                          )}
                        </section>

                        <section className="rounded-xl border border-dark-200/50 bg-dark-200/20 p-4">
                          <h4 className="text-white font-bold text-sm mb-2">Tool suggestions</h4>
                          {plannerPipeline.artifact_ids?.tool_suggestion != null &&
                          plannerPipeline.tool_suggestion == null ? (
                            <p className="text-amber-400/90 text-xs">
                              Latest artifact is stored but JSON could not be read. Open raw from the table below.
                            </p>
                          ) : plannerPipeline.tool_suggestion &&
                            Array.isArray(plannerPipeline.tool_suggestion.step_suggestions) ? (
                            <ul className="space-y-3 text-sm text-white/85">
                              {(plannerPipeline.tool_suggestion.step_suggestions as Record<string, unknown>[]).map(
                                (s, idx) => (
                                  <li
                                    key={idx}
                                    className="rounded-lg bg-dark-100/40 border border-dark-200/30 p-3"
                                  >
                                    <p className="font-semibold text-white">
                                      Step {idx + 1}
                                      {' · '}
                                      agent index{' '}
                                      {typeof s.agent_index === 'number' ||
                                      (typeof s.agent_index === 'string' && s.agent_index !== '')
                                        ? ppStr(s.agent_index)
                                        : '—'}
                                    </p>
                                    {ppStr(s.rationale) ? (
                                      <p className="mt-1 text-white/75 text-xs whitespace-pre-wrap">
                                        {ppStr(s.rationale)}
                                      </p>
                                    ) : null}
                                    {Array.isArray(s.platform_tool_ids) && s.platform_tool_ids.length > 0 ? (
                                      <p className="mt-1 text-xs text-white/50">
                                        Platform tool IDs:{' '}
                                        {(s.platform_tool_ids as unknown[]).map(ppStr).join(', ')}
                                      </p>
                                    ) : null}
                                  </li>
                                ),
                              )}
                            </ul>
                          ) : plannerPipeline.tool_suggestion ? (
                            <p className="text-white/50 text-xs">
                              No step_suggestions in this artifact. Use View for full JSON.
                            </p>
                          ) : (
                            <p className="text-white/50 text-xs">No tool suggestion artifact for this job yet.</p>
                          )}
                        </section>
                      </div>
                    )}
                  </div>

                  <div>
                    <p className="font-semibold text-white mb-2 text-sm">Stored artifacts (BRD / task split / tool suggestion)</p>
                    {plannerArtifacts.length === 0 ? (
                      <p className="text-white/50 text-sm">No artifacts for this job yet.</p>
                    ) : (
                      <div className="overflow-x-auto rounded-xl border border-dark-200/50">
                        <table className="w-full text-sm text-left">
                          <thead className="bg-dark-200/40 text-white/70">
                            <tr>
                              <th className="px-3 py-2 font-semibold">Type</th>
                              <th className="px-3 py-2 font-semibold">Storage</th>
                              <th className="px-3 py-2 font-semibold">Size</th>
                              <th className="px-3 py-2 font-semibold">Created</th>
                              <th className="px-3 py-2 font-semibold">Raw JSON</th>
                            </tr>
                          </thead>
                          <tbody className="text-white/85">
                            {plannerArtifacts.map((row) => (
                              <tr key={row.id} className="border-t border-dark-200/40">
                                <td className="px-3 py-2">{row.artifact_type}</td>
                                <td className="px-3 py-2">{row.storage}</td>
                                <td className="px-3 py-2">{row.byte_size}</td>
                                <td className="px-3 py-2 whitespace-nowrap">
                                  {row.created_at ? new Date(row.created_at).toLocaleString() : '—'}
                                </td>
                                <td className="px-3 py-2">
                                  <button
                                    type="button"
                                    className="text-primary-400 hover:text-primary-300 font-semibold underline-offset-2 hover:underline"
                                    onClick={async () => {
                                      try {
                                        const data = await jobsAPI.getPlannerArtifactRaw(jobId, row.id)
                                        setPlannerRawModal({
                                          title: `${row.artifact_type} (#${row.id})`,
                                          body: JSON.stringify(data, null, 2),
                                        })
                                      } catch (e) {
                                        console.error(e)
                                        alert('Failed to load artifact JSON')
                                      }
                                    }}
                                  >
                                    View
                                  </button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          )}
        </div>

        {plannerRawModal && (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
            role="dialog"
            aria-modal="true"
            aria-labelledby="planner-raw-title"
          >
            <div className="bg-dark-100 border border-dark-200 rounded-2xl shadow-2xl max-w-4xl w-full max-h-[85vh] flex flex-col">
              <div className="flex items-center justify-between px-5 py-4 border-b border-dark-200/50">
                <h2 id="planner-raw-title" className="text-lg font-bold text-white">
                  {plannerRawModal.title}
                </h2>
                <button
                  type="button"
                  onClick={() => setPlannerRawModal(null)}
                  className="text-white/70 hover:text-white px-3 py-1 rounded-lg hover:bg-dark-200/50 font-semibold"
                >
                  Close
                </button>
              </div>
              <pre className="flex-1 overflow-auto p-4 text-xs text-white/90 font-mono whitespace-pre-wrap break-all">
                {plannerRawModal.body}
              </pre>
            </div>
          </div>
        )}

        {job.status === 'draft' && (
          <div className="mb-8 bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-6 border border-dark-200/50">
            <div className="flex gap-4">
              <button
                onClick={() => setMode('workflow')}
                className={`px-6 py-3.5 rounded-xl font-bold transition-all duration-200 flex items-center gap-2 ${
                  mode === 'workflow'
                    ? 'bg-gradient-to-r from-primary-500 to-primary-700 text-white shadow-2xl shadow-primary-500/50 scale-105'
                    : 'bg-dark-200/50 text-white/70 hover:text-white hover:bg-dark-200 border border-dark-300'
                }`}
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                </svg>
                Build Workflow
              </button>
              {job.files && job.files.length > 0 && (
                <button
                  onClick={() => setMode('qa')}
                  className={`px-6 py-3.5 rounded-xl font-bold transition-all duration-200 flex items-center gap-2 ${
                    mode === 'qa'
                      ? 'bg-gradient-to-r from-primary-500 to-primary-700 text-white shadow-2xl shadow-primary-500/50 scale-105'
                      : 'bg-dark-200/50 text-white/70 hover:text-white hover:bg-dark-200 border border-dark-300'
                  }`}
                >
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
                  </svg>
                  Q&A
                </button>
              )}
              <button
                onClick={() => {
                  setMode('preview')
                  // Trigger fetch immediately for better UX.
                  loadWorkflowPreview()
                }}
                className={`px-6 py-3.5 rounded-xl font-bold transition-all duration-200 flex items-center gap-2 ${
                  mode === 'preview'
                    ? 'bg-gradient-to-r from-primary-500 to-primary-700 text-white shadow-2xl shadow-primary-500/50 scale-105'
                    : 'bg-dark-200/50 text-white/70 hover:text-white hover:bg-dark-200 border border-dark-300'
                }`}
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                Preview Cost
              </button>
            </div>
          </div>
        )}

        {job.status !== 'draft' && job.workflow_steps && job.workflow_steps.length > 0 && (
          <div className="mb-8 bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-6 border border-dark-200/50">
            <div className="flex flex-wrap gap-4">
              <button
                type="button"
                onClick={() => setMode('status')}
                className={`px-6 py-3.5 rounded-xl font-bold transition-all duration-200 flex items-center gap-2 ${
                  mode === 'status'
                    ? 'bg-gradient-to-r from-primary-500 to-primary-700 text-white shadow-2xl shadow-primary-500/50 scale-105'
                    : 'bg-dark-200/50 text-white/70 hover:text-white hover:bg-dark-200 border border-dark-300'
                }`}
              >
                Job status
              </button>
              <button
                type="button"
                onClick={() => setMode('workflow')}
                className={`px-6 py-3.5 rounded-xl font-bold transition-all duration-200 flex items-center gap-2 ${
                  mode === 'workflow'
                    ? 'bg-gradient-to-r from-primary-500 to-primary-700 text-white shadow-2xl shadow-primary-500/50 scale-105'
                    : 'bg-dark-200/50 text-white/70 hover:text-white hover:bg-dark-200 border border-dark-300'
                }`}
              >
                Output contract &amp; write mode
              </button>
            </div>
          </div>
        )}

        {mode === 'qa' && (
          <DocumentConversation
            jobId={jobId}
            files={job.files}
            initialConversation={job.conversation || []}
            onConversationUpdate={(conversation) => {
              if (job) setJob({ ...job, conversation })
            }}
            onNoClarificationNeeded={() => {
              setMode('preview')
            }}
            workflowSteps={job.workflow_steps}
          />
        )}

        {mode === 'workflow' && (job.status === 'draft' || (job.workflow_steps && job.workflow_steps.length > 0)) && (
          <>
            <WorkflowBuilder
              key={jobId}
              jobId={jobId}
              job={job}
              onWorkflowCreated={() => {
                loadJob()
                loadWorkflowPreview()
                void refreshPlannerPipelineIfOpen()
              }}
              initialSelectedAgentIds={selectedAgentsFromCreate}
            />
            {job.workflow_steps && job.workflow_steps.length > 0 && (
              <div className="mt-6 p-6 bg-dark-100/50 rounded-2xl border border-dark-200/50">
                <h3 className="font-bold text-white mb-3">Tools per step</h3>
                {(() => {
                  const w = buildJobDetailSharedToolWarning(job)
                  if (!w) return null
                  return (
                    <div
                      className={`mb-4 p-4 rounded-xl border-2 text-sm ${
                        w.variant === 'strong'
                          ? 'bg-amber-500/15 border-amber-500/50 text-amber-100'
                          : 'bg-slate-600/20 border-slate-400/40 text-white/90'
                      }`}
                      role="status"
                    >
                      <p className="font-bold text-white mb-2">{w.title}</p>
                      <ul className="list-disc list-inside space-y-1.5 text-white/85">
                        {w.lines.map((line, i) => (
                          <li key={i}>{line}</li>
                        ))}
                      </ul>
                      <p className="mt-2 text-xs text-white/50">
                        See <code className="text-primary-300">backend/docs/INDEPENDENT_WORKFLOW_SHARED_TOOLS.md</code> for headers and behavior.
                      </p>
                    </div>
                  )
                })()}
                <p className="text-sm text-white/60 mb-4">
                  {jobHasExplicitMcpScope(job)
                    ? 'Choose which tools each agent can use. You can limit an agent to specific tools (e.g. only Postgres) or leave a step empty to use the job\u2019s tool list.'
                    : 'Choose which tools each agent can use. With no job-level tool list, a step left empty shows no tools assigned here.'}
                </p>
                <div className="space-y-2">
                  {job.workflow_steps.map((step) => (
                    <div key={step.id} className="flex items-center justify-between py-2 px-4 bg-dark-200/30 rounded-xl border border-dark-300">
                      <span className="text-white/90 font-medium">Step {step.step_order}: {step.agent_name ?? `Agent ${step.agent_id}`}</span>
                      <div className="flex items-center gap-3">
                        <span className="text-sm text-white/50">
                          {(step.allowed_platform_tool_ids?.length ?? 0) + (step.allowed_connection_ids?.length ?? 0) > 0
                            ? `${step.allowed_platform_tool_ids?.length ?? 0} platform, ${step.allowed_connection_ids?.length ?? 0} connection(s)`
                            : jobHasExplicitMcpScope(job)
                              ? 'Uses job-scoped tools'
                              : '—'}
                        </span>
                        <button
                          type="button"
                          onClick={() => openStepToolsModal(step)}
                          className="text-primary-400 hover:text-primary-300 text-sm font-semibold"
                        >
                          Edit tools
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        {editingStepTools && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => setEditingStepTools(null)}>
            <div className="bg-dark-100 border-2 border-dark-300 rounded-2xl shadow-2xl max-w-lg w-full max-h-[80vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
              <div className="p-6 border-b border-dark-300">
                <h3 className="text-xl font-bold text-white">Tools for Step {editingStepTools.step_order}: {editingStepTools.agent_name}</h3>
              </div>
              <div className="p-6 overflow-y-auto flex-1">
                <div className="mb-4">
                  <h4 className="text-white font-semibold mb-2">Tool visibility (what this step sees)</h4>
                  <select
                    value={
                      stepToolsSelection.toolVisibility ??
                      (editingStepTools ? effectiveStepToolVisibility(editingStepTools, job) : 'full')
                    }
                    onChange={(e) =>
                      setStepToolsSelection((prev) => ({
                        ...prev,
                        toolVisibility: e.target.value as StepToolVisibilityUi,
                      }))
                    }
                    className="w-full px-3 py-2 bg-dark-200 border border-dark-300 rounded-lg text-white focus:ring-2 focus:ring-primary-500"
                  >
                    <option value="full">Full — Names, descriptions, schema & business context</option>
                    <option value="names_only">Names only — Tool names and short description; no schema</option>
                    <option value="none">None — No tool list for this step</option>
                  </select>
                  <p className="text-xs text-white/50 mt-1">Credentials are never shared. Full lets agents write correct SQL; names only/none restrict what they see.</p>
                </div>
                {stepToolsModalPlatform.length > 0 && (
                  <div className="mb-4">
                    <h4 className="text-white font-semibold mb-2">Platform tools</h4>
                    <div className="space-y-2">
                      {stepToolsModalPlatform.map((t) => (
                        <label key={t.id} className="flex items-center gap-2 cursor-pointer text-white/90">
                          <input
                            type="checkbox"
                            checked={stepToolsSelection.platformIds.includes(t.id)}
                            onChange={() => setStepToolsSelection((prev) => ({
                              ...prev,
                              platformIds: prev.platformIds.includes(t.id) ? prev.platformIds.filter((id) => id !== t.id) : [...prev.platformIds, t.id],
                            }))}
                            className="w-4 h-4 text-primary-600 rounded"
                          />
                          <span>{t.name}</span>
                          <span className="text-white/50 text-xs">({t.tool_type})</span>
                        </label>
                      ))}
                    </div>
                  </div>
                )}
                {stepToolsModalConnections.length > 0 && (
                  <div>
                    <h4 className="text-white font-semibold mb-2">MCP connections</h4>
                    <div className="space-y-2">
                      {stepToolsModalConnections.map((c) => (
                        <label key={c.id} className="flex items-center gap-2 cursor-pointer text-white/90">
                          <input
                            type="checkbox"
                            checked={stepToolsSelection.connectionIds.includes(c.id)}
                            onChange={() => setStepToolsSelection((prev) => ({
                              ...prev,
                              connectionIds: prev.connectionIds.includes(c.id) ? prev.connectionIds.filter((id) => id !== c.id) : [...prev.connectionIds, c.id],
                            }))}
                            className="w-4 h-4 text-primary-600 rounded"
                          />
                          <span>{c.name}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                )}
              </div>
              <div className="p-6 border-t border-dark-300 flex gap-3 justify-end">
                <button type="button" onClick={() => setEditingStepTools(null)} className="px-4 py-2 rounded-xl font-semibold text-white/80 hover:text-white border border-dark-300">Cancel</button>
                <button type="button" onClick={saveStepTools} disabled={savingStepTools} className="px-4 py-2 rounded-xl font-semibold bg-primary-500 text-white hover:bg-primary-600 disabled:opacity-50">{savingStepTools ? 'Saving...' : 'Save'}</button>
              </div>
            </div>
          </div>
        )}

        {mode === 'preview' && (
          <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50">
            {isLoadingPreview ? (
              <div className="py-10 text-center text-white/70 font-semibold">Loading preview cost...</div>
            ) : previewError ? (
              <div className="py-10 text-center">
                <p className="text-red-400 font-semibold mb-3">{previewError}</p>
                <button
                  onClick={loadWorkflowPreview}
                  className="px-5 py-2.5 bg-dark-200/70 text-white rounded-lg border border-dark-300 hover:bg-dark-200"
                >
                  Retry
                </button>
              </div>
            ) : workflowPreview ? (
              <>
                <CostCalculator preview={workflowPreview} />
                <div className="mt-8 flex gap-4">
                  <button
                    onClick={handleApprove}
                    className="bg-gradient-to-r from-primary-500 to-primary-700 text-white px-8 py-4 rounded-xl font-bold hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200"
                  >
                    Approve & Pay
                  </button>
                </div>
              </>
            ) : (
              <div className="py-10 text-center text-white/70 font-semibold">
                No workflow cost preview available yet. Build workflow first.
              </div>
            )}
          </div>
        )}

        {mode === 'status' && (
          <JobStatusTracker jobId={jobId} job={job} onJobUpdate={loadJob} />
        )}

        {mode === 'status' && job.status !== 'draft' && job.workflow_steps && job.workflow_steps.length > 0 && (
          <div className="mt-6 p-6 bg-dark-100/50 rounded-2xl border border-dark-200/50">
            <h3 className="font-bold text-white mb-3">Tools per step</h3>
            <p className="text-sm text-white/60 mb-4">Tools assigned to each agent for this job (saved when the workflow was created).</p>
            {job.tool_visibility && job.tool_visibility !== 'full' && (
              <p className="text-sm text-primary-400/90 mb-2 font-medium">
                Job tool visibility: {job.tool_visibility === 'names_only'
                  ? 'Names only (no schema or DB context)'
                  : 'None (no tool list)'}
              </p>
            )}
            <div className="space-y-2">
              {job.workflow_steps.map((step) => {
                const platformIds = step.allowed_platform_tool_ids ?? []
                const connCount = step.allowed_connection_ids?.length ?? 0
                const names =
                  platformToolsList.length > 0 && platformIds.length > 0
                    ? platformIds
                        .map((id) => platformToolsList.find((t) => t.id === id)?.name)
                        .filter(Boolean)
                        .join(', ')
                    : null
                const visibilityLabel = step.tool_visibility === 'names_only' ? 'names only (no schema)' : step.tool_visibility === 'none' ? 'none (no tools)' : null
                return (
                  <div
                    key={step.id}
                    className="flex items-center justify-between py-2 px-4 bg-dark-200/30 rounded-xl border border-dark-300"
                  >
                    <span className="text-white/90 font-medium">
                      Step {step.step_order}: {step.agent_name ?? `Agent ${step.agent_id}`}
                    </span>
                    <span className="text-sm text-white/70">
                      {names || (platformIds.length > 0 || connCount > 0
                        ? `${platformIds.length} platform, ${connCount} connection(s)`
                        : jobHasExplicitMcpScope(job)
                          ? 'Uses job-scoped tools'
                          : '—')}
                      {visibilityLabel && <span className="text-white/50 ml-1">(visibility: {visibilityLabel})</span>}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* ── PENDING APPROVAL: Execute Now or Schedule for Later ── */}
        {job.status === 'pending_approval' && (
          <div className="mt-8 bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50">
            {!schedule && (
              <>
                <h2 className="text-3xl font-black text-white mb-2">Ready to Execute</h2>
                <p className="text-white/50 font-medium mb-6">Run the job now, or set a schedule to auto-trigger it.</p>

                <div className="flex flex-wrap gap-3">
                  <button
                    onClick={() => { setShowScheduleLater(false); setShowExecuteConfirm(true) }}
                    className={`px-8 py-4 rounded-xl font-bold transition-all duration-200 flex items-center gap-2 ${
                      !showScheduleLater
                        ? 'bg-gradient-to-r from-primary-500 to-primary-700 text-white hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105'
                        : 'bg-dark-200/50 border border-dark-300 text-white/80 hover:text-white hover:bg-dark-200'
                    }`}
                  >
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    Execute Now
                  </button>
                  <button
                    onClick={() => { setShowExecuteConfirm(false); setShowScheduleLater((v) => !v) }}
                    className={`px-8 py-4 rounded-xl font-bold transition-all duration-200 flex items-center gap-2 ${
                      showScheduleLater
                        ? 'bg-gradient-to-r from-primary-500 to-primary-700 text-white hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105'
                        : 'bg-dark-200/50 border border-dark-300 text-white/80 hover:text-white hover:bg-dark-200'
                    }`}
                  >
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                    </svg>
                    Schedule for Later
                  </button>
                </div>

                {showExecuteConfirm && (
                  <div className="mt-6 p-5 bg-dark-200/30 rounded-xl border border-dark-300">
                    <p className="text-white font-semibold mb-1">Execute this job now?</p>
                    <p className="text-white/50 text-sm mb-4">The workflow will start immediately and agents will begin processing.</p>
                    <div className="flex gap-3">
                      <button onClick={handleExecute} className="px-6 py-2.5 bg-gradient-to-r from-primary-500 to-primary-700 text-white rounded-xl font-bold hover:shadow-xl hover:shadow-primary-500/40 transition-all duration-200">
                        Yes, Execute
                      </button>
                      <button onClick={() => setShowExecuteConfirm(false)} className="px-6 py-2.5 bg-dark-200/50 text-white/70 rounded-xl font-bold border border-dark-300 hover:text-white transition-all duration-200">
                        Cancel
                      </button>
                    </div>
                  </div>
                )}

                {showScheduleLater && (
                  <div className="mt-6 space-y-4">
                    <SchedulePicker
                      timezone={scheduleData.timezone}
                      scheduledAt={scheduleData.scheduledAt}
                      status={scheduleData.status}
                      onChange={setScheduleData}
                    />
                    <div className="flex gap-3">
                      <button onClick={handleScheduleLater} disabled={isSavingSchedule} className="px-6 py-3 bg-gradient-to-r from-primary-500 to-primary-700 text-white rounded-xl font-bold hover:shadow-xl hover:shadow-primary-500/40 transition-all duration-200 disabled:opacity-50">
                        {isSavingSchedule ? 'Saving...' : 'Save Schedule'}
                      </button>
                      <button onClick={() => setShowScheduleLater(false)} className="px-6 py-3 bg-dark-200/50 text-white/70 rounded-xl font-bold border border-dark-300 hover:text-white transition-all duration-200">
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}

            {schedule && (
              <>
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-3xl font-black text-white">Scheduled</h2>
                </div>

                <div className="flex items-center gap-3 p-4 bg-dark-200/40 rounded-xl border border-dark-300 mb-6">
                  <svg className="w-5 h-5 text-primary-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                  </svg>
                  <div className="flex-1 min-w-0">
                    <p className="text-white font-semibold text-sm">{humanReadableSchedule({ scheduledAt: schedule.scheduled_at, timezone: schedule.timezone })}</p>
                    <div className="flex items-center gap-2 mt-1">
                      <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${schedule.status === 'active' ? 'bg-green-500/20 text-green-400' : 'bg-dark-200/50 text-white/40'}`}>{schedule.status}</span>
                    </div>
                    {schedule.next_run_time && (
                      <p className="text-white/40 text-xs mt-1">Next run: {new Date(schedule.next_run_time).toLocaleString()}</p>
                    )}
                  </div>
                  <button
                    onClick={() => {
                      setScheduleData({ timezone: schedule.timezone, scheduledAt: schedule.scheduled_at, status: schedule.status })
                      setShowScheduleLater(true)
                    }}
                    className="p-2 text-primary-400/60 hover:text-primary-400 hover:bg-primary-500/20 rounded-lg transition-all duration-200"
                    title="Edit schedule"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                    </svg>
                  </button>
                </div>

                {showScheduleLater && (
                  <div className="mb-6 space-y-4">
                    <SchedulePicker timezone={scheduleData.timezone} scheduledAt={scheduleData.scheduledAt} status={scheduleData.status} onChange={setScheduleData} />
                    <div className="flex gap-3">
                      <button onClick={handleScheduleLater} disabled={isSavingSchedule} className="px-6 py-2.5 bg-gradient-to-r from-primary-500 to-primary-700 text-white rounded-xl font-bold text-sm hover:shadow-xl hover:shadow-primary-500/40 transition-all duration-200 disabled:opacity-50">
                        {isSavingSchedule ? 'Saving...' : 'Update Schedule'}
                      </button>
                      <button onClick={() => setShowScheduleLater(false)} className="px-6 py-2.5 bg-dark-200/50 text-white/70 rounded-xl font-bold text-sm border border-dark-300 hover:text-white transition-all duration-200">
                        Cancel
                      </button>
                    </div>
                  </div>
                )}

                <div className="pt-4 border-t border-dark-300/50">
                  {!showExecuteConfirm ? (
                    <button onClick={() => setShowExecuteConfirm(true)} className="text-sm font-semibold text-white/50 hover:text-white transition-colors flex items-center gap-1.5">
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                      Or execute now instead
                    </button>
                  ) : (
                    <div className="p-4 bg-dark-200/30 rounded-xl border border-dark-300">
                      <p className="text-white font-semibold text-sm mb-1">Execute this job now?</p>
                      <p className="text-white/50 text-xs mb-3">This will run the job immediately, independent of the schedule.</p>
                      <div className="flex gap-3">
                        <button onClick={handleExecute} className="px-5 py-2 bg-gradient-to-r from-primary-500 to-primary-700 text-white rounded-xl font-bold text-sm hover:shadow-xl hover:shadow-primary-500/40 transition-all duration-200">Yes, Execute</button>
                        <button onClick={() => setShowExecuteConfirm(false)} className="px-5 py-2 bg-dark-200/50 text-white/70 rounded-xl font-bold text-sm border border-dark-300 hover:text-white transition-all duration-200">Cancel</button>
                      </div>
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        )}

        {/* ── IN_QUEUE: Show schedule info, countdown, and update option ── */}
        {job.status === 'in_queue' && (
          <div className="mt-8 bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50">
            <h2 className="text-3xl font-black text-white mb-2">Scheduled</h2>
            {schedule && (
              <p className="text-primary-300 font-semibold mb-4">{humanReadableSchedule({ scheduledAt: schedule.scheduled_at, timezone: schedule.timezone })}</p>
            )}
            {countdown !== null && countdown > 0 && (
              <div className="mb-6 p-4 bg-cyan-500/10 border border-cyan-500/30 rounded-xl">
                <p className="text-cyan-400 font-bold text-lg">
                  {countdown <= 60
                    ? `Starting in ${countdown} second${countdown !== 1 ? 's' : ''}...`
                    : countdown < 3600
                      ? `Starting in ${Math.floor(countdown / 60)} minute${Math.floor(countdown / 60) !== 1 ? 's' : ''}`
                      : `Starting in ${Math.floor(countdown / 3600)}h ${Math.floor((countdown % 3600) / 60)}m`
                  }
                </p>
              </div>
            )}
            {countdown === 0 && (
              <div className="mb-6 p-4 bg-primary-500/10 border border-primary-500/30 rounded-xl">
                <p className="text-primary-400 font-bold flex items-center gap-2">
                  <span className="inline-block animate-spin rounded-full h-4 w-4 border-2 border-primary-400 border-t-transparent"></span>
                  Starting execution...
                </p>
              </div>
            )}
            <button
              onClick={() => {
                if (schedule) {
                  setScheduleData({ timezone: schedule.timezone, scheduledAt: schedule.scheduled_at, status: schedule.status })
                }
                setShowScheduleLater((v) => !v)
              }}
              className="px-6 py-3 bg-dark-200/50 border border-dark-300 text-white/80 hover:text-white hover:bg-dark-200 rounded-xl font-bold transition-all duration-200 flex items-center gap-2"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
              </svg>
              Update Schedule
            </button>

            {showScheduleLater && (
              <div className="mt-6 space-y-4">
                <SchedulePicker timezone={scheduleData.timezone} scheduledAt={scheduleData.scheduledAt} status={scheduleData.status} onChange={setScheduleData} />
                <div className="flex gap-3">
                  <button onClick={handleScheduleLater} disabled={isSavingSchedule} className="px-6 py-3 bg-gradient-to-r from-primary-500 to-primary-700 text-white rounded-xl font-bold hover:shadow-xl hover:shadow-primary-500/40 transition-all duration-200 disabled:opacity-50">
                    {isSavingSchedule ? 'Saving...' : 'Update Schedule'}
                  </button>
                  <button onClick={() => setShowScheduleLater(false)} className="px-6 py-3 bg-dark-200/50 text-white/70 rounded-xl font-bold border border-dark-300 hover:text-white transition-all duration-200">
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── IN_PROGRESS: Show cancel if stuck ── */}
        {job.status === 'in_progress' && job.show_cancel_option && (
          <div className="mt-8 bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-orange-500/30">
            <h2 className="text-2xl font-black text-orange-400 mb-2">Job Running Too Long</h2>
            <p className="text-white/50 font-medium mb-4">This job has been running longer than expected. You can cancel it if it appears stuck.</p>
            <button
              onClick={handleCancel}
              className="px-8 py-4 bg-orange-500/20 border border-orange-500/50 text-orange-400 rounded-xl font-bold hover:bg-orange-500/30 transition-all duration-200"
            >
              Cancel Job
            </button>
          </div>
        )}

        {/* ── FAILED / CANCELLED: Rerun or Reschedule ── */}
        {(job.status === 'failed' || job.status === 'cancelled') && (
          <div className="mt-8 bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50">
            <h2 className="text-3xl font-black text-white mb-2">
              {job.status === 'failed' ? 'Job Failed' : 'Job Cancelled'}
            </h2>
            {job.failure_reason && (
              <p className="text-red-400/80 text-sm mb-4">{job.failure_reason}</p>
            )}
            <div className="flex flex-wrap gap-3">
              <button
                onClick={handleRerun}
                className="px-8 py-4 bg-gradient-to-r from-primary-500 to-primary-700 text-white rounded-xl font-bold hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200 flex items-center gap-2"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                Rerun Now
              </button>
              <button
                onClick={() => {
                  if (schedule) {
                    setScheduleData({ timezone: schedule.timezone, scheduledAt: null, status: 'active' })
                  }
                  setShowScheduleLater((v) => !v)
                }}
                className="px-8 py-4 bg-dark-200/50 border border-dark-300 text-white/80 hover:text-white hover:bg-dark-200 rounded-xl font-bold transition-all duration-200 flex items-center gap-2"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
                Reschedule
              </button>
            </div>

            {showScheduleLater && (
              <div className="mt-6 space-y-4">
                <SchedulePicker timezone={scheduleData.timezone} scheduledAt={scheduleData.scheduledAt} status={scheduleData.status} onChange={setScheduleData} />
                <div className="flex gap-3">
                  <button onClick={handleScheduleLater} disabled={isSavingSchedule} className="px-6 py-3 bg-gradient-to-r from-primary-500 to-primary-700 text-white rounded-xl font-bold hover:shadow-xl hover:shadow-primary-500/40 transition-all duration-200 disabled:opacity-50">
                    {isSavingSchedule ? 'Saving...' : 'Save Schedule'}
                  </button>
                  <button onClick={() => setShowScheduleLater(false)} className="px-6 py-3 bg-dark-200/50 text-white/70 rounded-xl font-bold border border-dark-300 hover:text-white transition-all duration-200">
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
