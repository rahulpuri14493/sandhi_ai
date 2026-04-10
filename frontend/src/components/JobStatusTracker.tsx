import { useEffect, useState } from 'react'
import { jobsAPI } from '../lib/api'
import { formatRerunStartedMessage } from '../lib/rerunFeedback'
import { FlashToast } from './FlashToast'
import { RerunModeModal } from './RerunModeModal'
import { getStepOutputDisplayText } from '../lib/formatStepOutput'
import type { Job } from '../lib/types'

interface JobStatusTrackerProps {
  jobId: number
  job: Job
  onJobUpdate?: () => void
  focusedStepId?: number
}

export function JobStatusTracker({ jobId, job: initialJob, onJobUpdate, focusedStepId }: JobStatusTrackerProps) {
  const [job, setJob] = useState(initialJob)
  const [isPolling, setIsPolling] = useState(false)
  const [isRerunning, setIsRerunning] = useState(false)
  const [showRerunModal, setShowRerunModal] = useState(false)
  const [rerunToast, setRerunToast] = useState<string | null>(null)
  const [highlightedStepId, setHighlightedStepId] = useState<number | null>(null)

  useEffect(() => {
    if (job.status === 'in_progress') {
      setIsPolling(true)
      const interval = setInterval(async () => {
        try {
          const updatedJob = await jobsAPI.getStatus(jobId)
          setJob(updatedJob)
          if (updatedJob.status === 'completed' || updatedJob.status === 'failed' || updatedJob.status === 'cancelled') {
            setIsPolling(false)
            clearInterval(interval)
            onJobUpdate?.()
          }
        } catch (error) {
          console.error('Failed to poll job status:', error)
        }
      }, 5000)

      return () => clearInterval(interval)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, job.status])

  useEffect(() => {
    if (!focusedStepId) return
    setHighlightedStepId(focusedStepId)
    const timer = window.setTimeout(() => setHighlightedStepId(null), 3000)
    return () => window.clearTimeout(timer)
  }, [focusedStepId])

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
      <span className={`inline-flex items-center gap-2 px-4 py-2.5 rounded-full text-sm font-bold border-2 ${statusInfo.bg} ${statusInfo.text} ${statusInfo.border}`}>
        <span className="text-lg">{statusInfo.icon}</span>
        {status.replace('_', ' ').toUpperCase()}
      </span>
    )
  }

  return (
    <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50">
      <div className="flex items-center justify-between mb-8">
        <h2 className="text-5xl font-black text-white tracking-tight">
          Job Status
        </h2>
        {getStatusBadge(job.status)}
      </div>
      {job.workflow_steps && job.workflow_steps.length > 0 && (
        <div className="mt-8 pt-8 border-t border-dark-200/50">
          {job.status === 'completed' && job.workflow_steps.length > 1 && (
            <div className="mb-8 p-6 bg-primary-500/10 border-2 border-primary-500/30 rounded-2xl">
              <h3 className="text-xl font-black text-primary-400 mb-4 flex items-center gap-2">
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2 2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                All Agent Results ({job.workflow_steps.length} agents)
              </h3>
              <div className="space-y-4">
                {job.workflow_steps.map((step) => {
                  let outputData = null
                  try {
                    if (step.output_data) {
                      outputData = typeof step.output_data === 'string' ? JSON.parse(step.output_data) : step.output_data
                    }
                  } catch {
                    outputData = step.output_data
                  }
                  const content = getStepOutputDisplayText(outputData)
                  return (
                    <div key={step.id} className={`p-4 rounded-xl border ${step.status === 'failed' ? 'bg-red-500/10 border-red-500/30' : 'bg-dark-200/30 border-dark-300'}`}>
                      <div className="font-bold text-white mb-2">
                        {step.agent_name || `Agent ${step.agent_id}`} (Step {step.step_order})
                        {step.status === 'failed' && <span className="ml-2 text-red-400 text-sm">— Failed</span>}
                      </div>
                      <pre className="text-sm text-white/90 whitespace-pre-wrap font-sans">{content}</pre>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
          <div className="flex items-center justify-between mb-8">
            <h3 className="text-2xl font-black text-white flex items-center gap-3">
              <svg className="w-6 h-6 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
              </svg>
              Workflow Steps & Agent Outputs
            </h3>
            <div className="flex items-center gap-3">
              {job.workflow_steps && job.workflow_steps.length > 0 && (
                <span className="inline-flex items-center gap-2 text-xs text-emerald-300 bg-emerald-500/15 px-4 py-2 rounded-full border border-emerald-500/40 font-bold">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <span>
                    Task cost (agents): $
                    {job.workflow_steps.reduce((sum, s) => sum + (s.cost || 0), 0).toFixed(2)}
                  </span>
                </span>
              )}
              {typeof job.total_cost === 'number' && job.total_cost > 0 && (
                <span className="inline-flex items-center gap-2 text-xs text-primary-300 bg-primary-500/15 px-4 py-2 rounded-full border border-primary-500/40 font-bold">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <span>
                    Total paid: ${job.total_cost.toFixed(2)}
                  </span>
                </span>
              )}
              {job.files && job.files.length > 0 && (
                <span className="inline-flex items-center gap-2 text-xs text-primary-400 bg-primary-500/20 px-4 py-2 rounded-full border border-primary-500/50 font-bold">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                  {job.files.length} document{job.files.length > 1 ? 's' : ''} included
                </span>
              )}
            </div>
          </div>
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
                // If parsing fails, treat as plain text
                outputData = step.output_data
              }
              
              const getStepBorderColor = () => {
                if (step.status === 'completed') return 'border-green-500/50'
                if (step.status === 'failed') return 'border-red-500/50'
                if (step.status === 'in_progress') return 'border-primary-500/50'
                return 'border-dark-300'
              }
              
              return (
                <div
                  key={step.id}
                  id={`workflow-step-${step.id}`}
                  className={`border-2 rounded-2xl p-6 bg-dark-200/30 backdrop-blur-sm hover:shadow-2xl transition-all duration-200 ${getStepBorderColor()} ${
                    highlightedStepId === step.id ? 'ring-2 ring-yellow-400/80 shadow-[0_0_30px_rgba(250,204,21,0.35)] animate-pulse' : ''
                  }`}
                >
                  <div className="flex items-center justify-between mb-5">
                    <div className="flex items-center gap-4">
                      <div className={`p-3 rounded-xl border ${
                        step.status === 'completed' ? 'bg-green-500/20 border-green-500/50' :
                        step.status === 'failed' ? 'bg-red-500/20 border-red-500/50' :
                        step.status === 'in_progress' ? 'bg-primary-500/20 border-primary-500/50' :
                        'bg-dark-200/50 border-dark-300'
                      }`}>
                        <svg className={`w-6 h-6 ${
                          step.status === 'completed' ? 'text-green-400' :
                          step.status === 'failed' ? 'text-red-400' :
                          step.status === 'in_progress' ? 'text-primary-400' :
                          'text-white/60'
                        }`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                        </svg>
                      </div>
                      <div>
                        <div className="flex flex-wrap items-center gap-3">
                          <span className="font-black text-xl text-white">
                            Step {step.step_order}
                            {step.agent_name && (
                              <span className="ml-3 text-primary-400 font-bold text-lg">({step.agent_name})</span>
                            )}
                          </span>
                          {typeof step.cost === 'number' && step.cost > 0 && (
                            <span className="inline-flex items-center gap-1 px-3 py-1 rounded-full text-[11px] font-bold bg-emerald-500/10 text-emerald-300 border border-emerald-500/40">
                              <span>Task cost:</span>
                              <span>${step.cost.toFixed(2)}</span>
                            </span>
                          )}
                        </div>
                        <span className={`ml-4 px-4 py-1.5 rounded-full text-xs font-bold border ${
                          step.status === 'completed' ? 'bg-green-500/20 text-green-400 border-green-500/50' :
                          step.status === 'failed' ? 'bg-red-500/20 text-red-400 border-red-500/50' :
                          step.status === 'in_progress' ? 'bg-primary-500/20 text-primary-400 border-primary-500/50' :
                          'bg-dark-200/50 text-white/60 border-dark-300'
                        }`}>
                          {step.status.toUpperCase()}
                        </span>
                      </div>
                    </div>
                    {step.completed_at && (
                      <span className="text-xs text-white/50 bg-dark-200/50 px-4 py-2 rounded-full font-medium border border-dark-300">
                        {new Date(step.completed_at).toLocaleString()}
                      </span>
                    )}
                  </div>
                  
                  {step.status === 'completed' && outputData && (
                    <div className="mt-5 p-6 bg-green-500/10 border-2 border-green-500/30 rounded-2xl backdrop-blur-sm">
                      <h4 className="font-black text-green-400 mb-4 flex items-center gap-2 text-lg">
                        <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                        </svg>
                        AI Agent Output
                      </h4>
                      <div className="text-sm text-white/90 whitespace-pre-wrap">
                        <div className="bg-dark-50/50 p-5 rounded-xl border border-dark-200/50">
                          <pre className="whitespace-pre-wrap text-sm leading-relaxed text-white/90">
                            {getStepOutputDisplayText(outputData)}
                          </pre>
                        </div>
                      </div>
                    </div>
                  )}
                  
                  {step.status === 'completed' && !outputData && (
                    <div className="mt-5 p-5 bg-yellow-500/10 border-2 border-yellow-500/30 rounded-2xl">
                      <p className="text-sm text-yellow-400 font-bold">No output data available for this step.</p>
                    </div>
                  )}
                  
                  {step.status === 'failed' && outputData && (
                    <div className="mt-5 p-6 bg-red-500/10 border-2 border-red-500/30 rounded-2xl backdrop-blur-sm">
                      <h4 className="font-black text-red-400 mb-4 flex items-center gap-2 text-lg">
                        <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                        Error Details
                      </h4>
                      <div className="text-sm text-red-300 bg-dark-50/50 p-5 rounded-xl border border-dark-200/50">
                        <pre className="overflow-auto max-h-96 font-mono text-xs text-white/80 whitespace-pre-wrap">
                          {getStepOutputDisplayText(outputData)}
                        </pre>
                      </div>
                    </div>
                  )}
                  
                  {step.status === 'in_progress' && (
                    <div className="mt-5 p-6 bg-primary-500/10 border-2 border-primary-500/30 rounded-2xl backdrop-blur-sm">
                      <div className="flex items-center gap-4">
                        <div className="animate-spin rounded-full h-6 w-6 border-3 border-primary-400 border-t-transparent"></div>
                        <p className="text-sm text-primary-400 font-bold text-lg">Processing...</p>
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
      {isPolling && (
        <div className="mt-4 text-sm text-gray-600">
          Polling for updates...
        </div>
      )}

      {job.status === 'in_progress' && job.show_cancel_option && (
        <div className="mt-8 pt-8 border-t border-orange-500/30">
          <p className="text-orange-400 font-semibold text-sm mb-3">This job has been running longer than expected.</p>
          <button
            onClick={async () => {
              if (!window.confirm('Cancel this job?')) return
              try {
                await jobsAPI.cancel(jobId)
                onJobUpdate?.()
              } catch (error) {
                console.error('Failed to cancel job:', error)
                alert('Failed to cancel job.')
              }
            }}
            className="px-6 py-3 bg-orange-500/20 border border-orange-500/50 text-orange-400 rounded-xl font-bold hover:bg-orange-500/30 transition-all duration-200"
          >
            Cancel Job
          </button>
        </div>
      )}

      {(job.status === 'failed' || job.status === 'cancelled') && (
        <div className="mt-8 pt-8 border-t border-dark-200/50">
          <button
            onClick={() => setShowRerunModal(true)}
            disabled={isRerunning}
            className="bg-gradient-to-r from-primary-500 to-primary-700 text-white px-8 py-4 rounded-xl font-bold hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isRerunning ? 'Rerunning...' : 'Rerun Job'}
          </button>
        </div>
      )}
      <RerunModeModal
        isOpen={showRerunModal}
        isSubmitting={isRerunning}
        onClose={() => setShowRerunModal(false)}
        onSelect={async (mode) => {
          setIsRerunning(true)
          try {
            const resp = await jobsAPI.rerun(jobId, mode)
            setShowRerunModal(false)
            setRerunToast(formatRerunStartedMessage(resp))
            onJobUpdate?.()
          } catch (error) {
            console.error('Failed to rerun job:', error)
            setRerunToast('Failed to rerun job. Only failed or cancelled jobs can be rerun.')
          } finally {
            setIsRerunning(false)
          }
        }}
      />
      <FlashToast message={rerunToast} onDismiss={() => setRerunToast(null)} />
    </div>
  )
}
