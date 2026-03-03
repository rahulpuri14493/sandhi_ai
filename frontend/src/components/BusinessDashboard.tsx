import { useEffect, useState } from 'react'
import { dashboardsAPI, jobsAPI } from '../lib/api'
import type { Job } from '../lib/types'
import { Link, useNavigate } from 'react-router-dom'

export function BusinessDashboard() {
  const [spending, setSpending] = useState({ total_spent: 0, job_count: 0 })
  const [jobs, setJobs] = useState<Job[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [expandedJobId, setExpandedJobId] = useState<number | null>(null)
  const [searchTerm, setSearchTerm] = useState('')
  const [statusFilter, setStatusFilter] = useState<'all' | Job['status']>('all')
  const navigate = useNavigate()

  useEffect(() => {
    loadData()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadData = async () => {
    setIsLoading(true)
    try {
      const [spendingData, jobsData] = await Promise.all([
        dashboardsAPI.getBusinessSpending(),
        dashboardsAPI.getBusinessJobs(),
      ])
      setSpending(spendingData)
      setJobs(jobsData)
    } catch (error) {
      console.error('Failed to load dashboard data:', error)
    } finally {
      setIsLoading(false)
    }
  }

  const filteredJobs = jobs.filter((job) => {
    const matchesSearch =
      !searchTerm ||
      job.title.toLowerCase().includes(searchTerm.toLowerCase()) ||
      (job.description || '').toLowerCase().includes(searchTerm.toLowerCase())
    const matchesStatus = statusFilter === 'all' || job.status === statusFilter
    return matchesSearch && matchesStatus
  })

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

      <div className="grid md:grid-cols-2 gap-6 mb-10">
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
      </div>

      <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl border border-dark-200/50 overflow-hidden">
        <div className="bg-gradient-to-r from-primary-600 to-primary-800 px-8 py-6 border-b border-primary-700/50">
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <h2 className="text-3xl font-black text-white tracking-tight">List of Jobs</h2>
            <div className="flex flex-1 gap-3 md:max-w-3xl md:ml-8">
              <div className="relative flex-1">
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
                  className="w-full pl-9 pr-3 py-2.5 rounded-xl bg-dark-100/80 border border-primary-500/40 text-sm text-white placeholder-white/40 focus:outline-none focus:ring-2 focus:ring-primary-400 focus:border-primary-400"
                />
              </div>
              <div className="flex items-center gap-2">
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
              </div>
              <Link
                to="/jobs/new"
                className="bg-white text-primary-600 px-5 py-2.5 rounded-xl font-bold hover:bg-white/90 transition-all duration-200 shadow-xl hover:shadow-2xl hover:scale-105 flex items-center gap-2 whitespace-nowrap"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
                New Job
              </Link>
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
              {filteredJobs.map((job) => {
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
                        {(job.status === 'completed' || job.status === 'failed') && (
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
                                {typeof outputData === 'object' ? (
                                  outputData.choices && Array.isArray(outputData.choices) && outputData.choices.length > 0 ? (
                                    <p className="whitespace-pre-wrap leading-relaxed">
                                      {outputData.choices[0].message?.content || 'No content'}
                                    </p>
                                  ) : (
                                    <pre className="text-xs overflow-auto max-h-48 font-mono text-white/80">
                                      {JSON.stringify(outputData, null, 2)}
                                    </pre>
                                  )
                                ) : (
                                  <p className="whitespace-pre-wrap">{String(outputData)}</p>
                                )}
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
