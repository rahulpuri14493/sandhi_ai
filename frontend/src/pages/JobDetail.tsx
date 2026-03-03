import { useEffect, useState } from 'react'
import { useParams, useSearchParams, useNavigate, useLocation } from 'react-router-dom'
import { jobsAPI } from '../lib/api'
import type { Job, WorkflowPreview } from '../lib/types'
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
  const [mode, setMode] = useState<'workflow' | 'preview' | 'status' | 'qa'>('workflow')

  useEffect(() => {
    if (jobId) {
      loadJob()
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

  const handleExecute = async () => {
    if (!job) return
    try {
      await jobsAPI.execute(jobId)
      await loadJob()
      setMode('status')
    } catch (error) {
      console.error('Failed to execute job:', error)
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
              <h3 className="text-2xl font-black text-white mb-6 flex items-center gap-3">
                <svg className="w-6 h-6 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                Uploaded Documents
              </h3>
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
              // Update job state with new conversation
              if (job) {
                setJob({ ...job, conversation })
              }
            }}
          />
        )}

        {mode === 'workflow' && job.status === 'draft' && (
          <WorkflowBuilder
            jobId={jobId}
            onWorkflowCreated={loadWorkflowPreview}
            initialSelectedAgentIds={selectedAgentsFromCreate}
          />
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

        {job.status === 'pending_approval' && (
          <div className="mt-8 bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50">
            <h2 className="text-3xl font-black text-white mb-6">Ready to Execute</h2>
            <button
              onClick={handleExecute}
              className="bg-gradient-to-r from-primary-500 to-primary-700 text-white px-8 py-4 rounded-xl font-bold hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200"
            >
              Execute Job
            </button>
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
