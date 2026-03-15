import { useEffect, useState } from 'react'
import { useParams, useSearchParams, useNavigate, useLocation } from 'react-router-dom'
import { jobsAPI, mcpAPI } from '../lib/api'
import type { Job, WorkflowPreview, WorkflowStep, JobSchedule } from '../lib/types'
import SchedulePicker, { humanReadableSchedule } from '../components/SchedulePicker'
import { WorkflowBuilder } from '../components/WorkflowBuilder'
import { CostCalculator } from '../components/CostCalculator'
import { JobStatusTracker } from '../components/JobStatusTracker'
import { DocumentConversation } from '../components/DocumentConversation'

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
  const [isAnalyzingDocuments, setIsAnalyzingDocuments] = useState(false)
  const [mode, setMode] = useState<'workflow' | 'preview' | 'status' | 'qa'>('workflow')
  const [editingStepTools, setEditingStepTools] = useState<WorkflowStep | null>(null)
  const [stepToolsModalPlatform, setStepToolsModalPlatform] = useState<{ id: number; name: string; tool_type: string }[]>([])
  const [stepToolsModalConnections, setStepToolsModalConnections] = useState<{ id: number; name: string }[]>([])
  const [stepToolsSelection, setStepToolsSelection] = useState<{ platformIds: number[]; connectionIds: number[]; toolVisibility?: 'full' | 'names_only' | 'none' }>({ platformIds: [], connectionIds: [] })
  const [savingStepTools, setSavingStepTools] = useState(false)
  /** Loaded for showing tool names on completed job "Tools per step" */
  const [platformToolsList, setPlatformToolsList] = useState<{ id: number; name: string; tool_type: string }[]>([])
  const [schedules, setSchedules] = useState<JobSchedule[]>([])
  const [showScheduleLater, setShowScheduleLater] = useState(false)
  const [scheduleData, setScheduleData] = useState({
    isOneTime: true,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
    scheduledAt: null as string | null,
    daysOfWeek: [] as number[],
    time: '',
    status: 'active' as 'active' | 'inactive',
  })
  const [isSavingSchedule, setIsSavingSchedule] = useState(false)
  const [editingScheduleId, setEditingScheduleId] = useState<number | null>(null)

  useEffect(() => {
    if (jobId) {
      loadJob()
      loadSchedules()
    }
    // Check if Q&A mode should be shown from URL params
    if (searchParams.get('qa') === 'true') {
      setMode('qa')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, searchParams])

  useEffect(() => {
    if (job && job.status !== 'draft') {
      loadWorkflowPreview()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job])

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
    try {
      const preview = await jobsAPI.previewWorkflow(jobId)
      setWorkflowPreview(preview)
    } catch (error) {
      console.error('Failed to load workflow preview:', error)
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
    try {
      const result = await jobsAPI.analyzeDocuments(jobId)
      await loadJob()
      if (result.conversation?.length) setMode('qa')
    } catch (error) {
      console.error('Failed to analyze documents:', error)
      alert((error as any)?.response?.data?.detail || 'Failed to analyze documents')
    } finally {
      setIsAnalyzingDocuments(false)
    }
  }

  const openStepToolsModal = async (step: WorkflowStep) => {
    setEditingStepTools(step)
    setStepToolsSelection({
      platformIds: step.allowed_platform_tool_ids ?? [],
      connectionIds: step.allowed_connection_ids ?? [],
    })
    try {
      const [tools, conns] = await Promise.all([mcpAPI.listTools(), mcpAPI.listConnections()])
      const allowedPlatform = job?.allowed_platform_tool_ids
      const allowedConn = job?.allowed_connection_ids
      setStepToolsModalPlatform(allowedPlatform?.length ? tools.filter((t: { id: number }) => allowedPlatform.includes(t.id)) : tools)
      setStepToolsModalConnections(allowedConn?.length ? conns.filter((c: { id: number }) => allowedConn.includes(c.id)) : conns)
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
  const loadSchedules = async () => {
    try {
      const data = await jobsAPI.listSchedules(jobId)
      setSchedules(data)
    } catch {
      // no schedules yet — not an error
    }
  }

  const handleScheduleLater = async () => {
    setIsSavingSchedule(true)
    try {
      const payload = {
        is_one_time: scheduleData.isOneTime,
        timezone: scheduleData.timezone,
        scheduled_at: scheduleData.scheduledAt || undefined,
        days_of_week: scheduleData.daysOfWeek.length > 0 ? scheduleData.daysOfWeek : undefined,
        time: scheduleData.time || undefined,
        status: scheduleData.status,
      }

      if (editingScheduleId) {
        // Update existing schedule
        const updated = await jobsAPI.updateSchedule(jobId, editingScheduleId, payload)
        setSchedules((prev) => prev.map((s) => (s.id === editingScheduleId ? updated : s)))
        setEditingScheduleId(null)
      } else {
        // Create new schedule
        const created = await jobsAPI.createSchedule(jobId, payload)
        setSchedules((prev) => [...prev, created])
      }
      setShowScheduleLater(false)
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
      // After rerun, job status will be pending_approval, so show execute button
      setMode('status')
    } catch (error) {
      console.error('Failed to rerun job:', error)
      alert('Failed to rerun job. Only completed or failed jobs can be rerun.')
    }
  }

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
      in_progress: { bg: 'bg-primary-500/20', text: 'text-primary-400', border: 'border-primary-500/50', icon: '⚙️' },
      completed: { bg: 'bg-green-500/20', text: 'text-green-400', border: 'border-green-500/50', icon: '✓' },
      failed: { bg: 'bg-red-500/20', text: 'text-red-400', border: 'border-red-500/50', icon: '✗' },
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
                onClick={() => setMode('preview')}
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

        {mode === 'qa' && (
          <DocumentConversation
            jobId={jobId}
            files={job.files}
            initialConversation={job.conversation || []}
            onConversationUpdate={(conversation) => {
              if (job) setJob({ ...job, conversation })
            }}
            workflowSteps={job.workflow_steps}
          />
        )}

        {mode === 'workflow' && job.status === 'draft' && (
          <>
            <WorkflowBuilder
              jobId={jobId}
              job={job}
              onWorkflowCreated={() => { loadJob(); loadWorkflowPreview(); }}
              initialSelectedAgentIds={selectedAgentsFromCreate}
            />
            {job.workflow_steps && job.workflow_steps.length > 0 && (
              <div className="mt-6 p-6 bg-dark-100/50 rounded-2xl border border-dark-200/50">
                <h3 className="font-bold text-white mb-3">Tools per step</h3>
                <p className="text-sm text-white/60 mb-4">Choose which tools each agent can use. You can limit an agent to specific tools (e.g. only Postgres) or leave it to use all job tools.</p>
                <div className="space-y-2">
                  {job.workflow_steps.map((step) => (
                    <div key={step.id} className="flex items-center justify-between py-2 px-4 bg-dark-200/30 rounded-xl border border-dark-300">
                      <span className="text-white/90 font-medium">Step {step.step_order}: {step.agent_name ?? `Agent ${step.agent_id}`}</span>
                      <div className="flex items-center gap-3">
                        <span className="text-sm text-white/50">
                          {(step.allowed_platform_tool_ids?.length ?? 0) + (step.allowed_connection_ids?.length ?? 0) > 0
                            ? `${step.allowed_platform_tool_ids?.length ?? 0} platform, ${step.allowed_connection_ids?.length ?? 0} connection(s)`
                            : 'All job tools'}
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
                    value={stepToolsSelection.toolVisibility ?? 'full'}
                    onChange={(e) => setStepToolsSelection((prev) => ({ ...prev, toolVisibility: e.target.value as 'full' | 'names_only' | 'none' }))}
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

        {mode === 'preview' && workflowPreview && (
          <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50">
            <CostCalculator preview={workflowPreview} />
            <div className="mt-8 flex gap-4">
              <button
                onClick={handleApprove}
                className="bg-gradient-to-r from-primary-500 to-primary-700 text-white px-8 py-4 rounded-xl font-bold hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200"
              >
                Approve & Pay
              </button>
            </div>
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
                        : 'All job tools')}
                      {visibilityLabel && <span className="text-white/50 ml-1">(visibility: {visibilityLabel})</span>}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {job.status === 'pending_approval' && (
          <div className="mt-8 bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50">

            {/* ── STATE 1: No schedules yet ── */}
            {schedules.length === 0 && (
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

                {/* Execute Now confirmation */}
                {showExecuteConfirm && (
                  <div className="mt-6 p-5 bg-dark-200/30 rounded-xl border border-dark-300">
                    <p className="text-white font-semibold mb-1">Execute this job now?</p>
                    <p className="text-white/50 text-sm mb-4">The workflow will start immediately and agents will begin processing. This action cannot be undone.</p>
                    <div className="flex gap-3">
                      <button
                        onClick={handleExecute}
                        className="px-6 py-2.5 bg-gradient-to-r from-primary-500 to-primary-700 text-white rounded-xl font-bold hover:shadow-xl hover:shadow-primary-500/40 transition-all duration-200"
                      >
                        Yes, Execute
                      </button>
                      <button
                        onClick={() => setShowExecuteConfirm(false)}
                        className="px-6 py-2.5 bg-dark-200/50 text-white/70 rounded-xl font-bold border border-dark-300 hover:text-white transition-all duration-200"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}

                {/* Schedule for Later form */}
                {showScheduleLater && (
                  <div className="mt-6 space-y-4">
                    <SchedulePicker
                      isOneTime={scheduleData.isOneTime}
                      timezone={scheduleData.timezone}
                      scheduledAt={scheduleData.scheduledAt}
                      daysOfWeek={scheduleData.daysOfWeek}
                      time={scheduleData.time}
                      status={scheduleData.status}
                      onChange={setScheduleData}
                    />
                    <div className="flex gap-3">
                      <button
                        onClick={handleScheduleLater}
                        disabled={isSavingSchedule}
                        className="px-6 py-3 bg-gradient-to-r from-primary-500 to-primary-700 text-white rounded-xl font-bold hover:shadow-xl hover:shadow-primary-500/40 transition-all duration-200 disabled:opacity-50"
                      >
                        {isSavingSchedule ? 'Saving...' : 'Save Schedule'}
                      </button>
                      <button
                        onClick={() => setShowScheduleLater(false)}
                        className="px-6 py-3 bg-dark-200/50 text-white/70 rounded-xl font-bold border border-dark-300 hover:text-white transition-all duration-200"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}

            {/* ── STATE 2: Schedule exists ── */}
            {schedules.length > 0 && (
              <>
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-3xl font-black text-white">Scheduled</h2>
                </div>

                <div className="space-y-3 mb-6">
                  {schedules.map((s) => (
                    <div key={s.id} className="flex items-center gap-3 p-4 bg-dark-200/40 rounded-xl border border-dark-300">
                      <svg className="w-5 h-5 text-primary-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                      </svg>
                      <div className="flex-1 min-w-0">
                        <p className="text-white font-semibold text-sm">{humanReadableSchedule({
                          isOneTime: s.is_one_time,
                          scheduledAt: s.scheduled_at ?? undefined,
                          daysOfWeek: s.days_of_week ?? undefined,
                          time: s.time ?? undefined,
                          timezone: s.timezone,
                        })}</p>
                        <div className="flex items-center gap-2 mt-1">
                          <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${s.is_one_time ? 'bg-amber-500/20 text-amber-400' : 'bg-blue-500/20 text-blue-400'}`}>
                            {s.is_one_time ? 'One-time' : 'Recurring'}
                          </span>
                          <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${
                            s.status === 'active'
                              ? 'bg-green-500/20 text-green-400'
                              : 'bg-dark-200/50 text-white/40'
                          }`}>{s.status}</span>
                        </div>
                        {s.next_run_time && (
                          <p className="text-white/40 text-xs mt-1">Next run: {new Date(s.next_run_time).toLocaleString()}</p>
                        )}
                      </div>
                      <button
                        onClick={() => {
                          // Pre-fill picker with this schedule's values — no API call yet
                          setEditingScheduleId(s.id)
                          setScheduleData({
                            isOneTime: s.is_one_time,
                            timezone: s.timezone,
                            scheduledAt: s.scheduled_at,
                            daysOfWeek: s.days_of_week ?? [],
                            time: s.time ?? '',
                            status: s.status,
                          })
                          setShowScheduleLater(true)
                        }}
                        className="p-2 text-primary-400/60 hover:text-primary-400 hover:bg-primary-500/20 rounded-lg transition-all duration-200"
                        title="Edit schedule"
                      >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                        </svg>
                      </button>
                      <button
                        onClick={async () => {
                          if (!window.confirm('Delete this schedule?')) return
                          try {
                            await jobsAPI.deleteSchedule(jobId, s.id)
                            setSchedules((prev) => prev.filter((x) => x.id !== s.id))
                          } catch (error) {
                            console.error('Failed to delete schedule:', error)
                          }
                        }}
                        className="p-2 text-red-400/60 hover:text-red-400 hover:bg-red-500/20 rounded-lg transition-all duration-200"
                        title="Delete schedule"
                      >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                        </svg>
                      </button>
                    </div>
                  ))}
                </div>

                {/* Edit existing schedule */}
                {showScheduleLater && (
                  <div className="mb-6 space-y-4">
                    <SchedulePicker
                      isOneTime={scheduleData.isOneTime}
                      timezone={scheduleData.timezone}
                      scheduledAt={scheduleData.scheduledAt}
                      daysOfWeek={scheduleData.daysOfWeek}
                      time={scheduleData.time}
                      status={scheduleData.status}
                      onChange={setScheduleData}
                    />
                    <div className="flex gap-3">
                      <button
                        onClick={handleScheduleLater}
                        disabled={isSavingSchedule}
                        className="px-6 py-2.5 bg-gradient-to-r from-primary-500 to-primary-700 text-white rounded-xl font-bold text-sm hover:shadow-xl hover:shadow-primary-500/40 transition-all duration-200 disabled:opacity-50"
                      >
                        {isSavingSchedule ? 'Saving...' : editingScheduleId ? 'Update Schedule' : 'Save Schedule'}
                      </button>
                      <button
                        onClick={() => { setShowScheduleLater(false); setEditingScheduleId(null) }}
                        className="px-6 py-2.5 bg-dark-200/50 text-white/70 rounded-xl font-bold text-sm border border-dark-300 hover:text-white transition-all duration-200"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}

                {/* Secondary: Execute Now option */}
                <div className="pt-4 border-t border-dark-300/50">
                  {!showExecuteConfirm ? (
                    <button
                      onClick={() => setShowExecuteConfirm(true)}
                      className="text-sm font-semibold text-white/50 hover:text-white transition-colors flex items-center gap-1.5"
                    >
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
                        <button
                          onClick={handleExecute}
                          className="px-5 py-2 bg-gradient-to-r from-primary-500 to-primary-700 text-white rounded-xl font-bold text-sm hover:shadow-xl hover:shadow-primary-500/40 transition-all duration-200"
                        >
                          Yes, Execute
                        </button>
                        <button
                          onClick={() => setShowExecuteConfirm(false)}
                          className="px-5 py-2 bg-dark-200/50 text-white/70 rounded-xl font-bold text-sm border border-dark-300 hover:text-white transition-all duration-200"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        )}

        {(job.status === 'completed' || job.status === 'failed') && (
          <div className="mt-8 bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50">
            <h2 className="text-3xl font-black text-white mb-6">
              {job.status === 'completed' ? 'Job Completed' : 'Job Failed'}
            </h2>
            <button
              onClick={handleRerun}
              className="bg-gradient-to-r from-primary-500 to-primary-700 text-white px-8 py-4 rounded-xl font-bold hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200"
            >
              Rerun Job
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
