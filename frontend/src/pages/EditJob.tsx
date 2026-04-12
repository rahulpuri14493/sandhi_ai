import { useState, useEffect, useRef } from 'react'
import { useNavigate, useParams, useLocation } from 'react-router-dom'
import { jobsAPI, agentsAPI, mcpAPI } from '../lib/api'
import type { Job, Agent, WorkflowStep } from '../lib/types'
import type { MCPToolConfigRes, MCPServerConnectionRes } from '../lib/api'


export default function EditJobPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const selectedAgentsFromState = (location.state as { selectedAgents?: number[] })?.selectedAgents
  const [formData, setFormData] = useState<Partial<Job>>({
    title: '',
    description: '',
    status: 'draft',
  })
  const [toolVisibility, setToolVisibility] = useState<'full' | 'names_only' | 'none'>('none')
  const [existingFiles, setExistingFiles] = useState<Array<{ id: string; name: string; type: string; size: number }>>([])
  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  const [selectedAgents, setSelectedAgents] = useState<number[]>(selectedAgentsFromState ?? [])
  const [availableAgents, setAvailableAgents] = useState<Agent[]>([])
  const [platformTools, setPlatformTools] = useState<MCPToolConfigRes[]>([])
  const [connections, setConnections] = useState<MCPServerConnectionRes[]>([])
  const [selectedPlatformToolIds, setSelectedPlatformToolIds] = useState<number[]>([])
  const [selectedConnectionIds, setSelectedConnectionIds] = useState<number[]>([])
  /** Job's allowed tool IDs when loaded (for showing "N tools no longer configured") */
  const [jobOriginalPlatformToolIds, setJobOriginalPlatformToolIds] = useState<number[]>([])
  const hasSyncedToolSelection = useRef(false)
  const [isLoading, setIsLoading] = useState(false)
  const [isLoadingJob, setIsLoadingJob] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    hasSyncedToolSelection.current = false
    loadJob()
    loadAgents()
    loadTools()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  // Sync platform checkboxes to saved job scope (drop ids no longer in catalog). If the job has no
  // saved platform tools, finish immediately — do not wait on platformTools.length (catalog may be empty).
  useEffect(() => {
    if (isLoadingJob || hasSyncedToolSelection.current) return
    if (jobOriginalPlatformToolIds.length === 0) {
      hasSyncedToolSelection.current = true
      return
    }
    if (platformTools.length === 0) return
    hasSyncedToolSelection.current = true
    const currentIds = new Set(platformTools.map((t) => t.id))
    const validIds = jobOriginalPlatformToolIds.filter((id) => currentIds.has(id))
    setSelectedPlatformToolIds(validIds)
  }, [isLoadingJob, platformTools, jobOriginalPlatformToolIds])

  useEffect(() => {
    if (selectedAgentsFromState && selectedAgentsFromState.length > 0) {
      setSelectedAgents(selectedAgentsFromState)
    }
  }, [selectedAgentsFromState])

  const loadJob = async () => {
    if (!id) return
    setIsLoadingJob(true)
    try {
      const job = await jobsAPI.get(parseInt(id))
      setFormData({
        title: job.title || '',
        description: job.description || '',
        status: job.status || 'draft',
      })
      setExistingFiles(job.files || [])
      const allowedToolIds = Array.isArray(job.allowed_platform_tool_ids)
        ? job.allowed_platform_tool_ids
        : []
      const allowedConnIds = Array.isArray(job.allowed_connection_ids) ? job.allowed_connection_ids : []
      setJobOriginalPlatformToolIds(allowedToolIds)
      setSelectedPlatformToolIds(allowedToolIds)
      setSelectedConnectionIds(allowedConnIds)
      setToolVisibility((job.tool_visibility ?? 'none') as 'full' | 'names_only' | 'none')
      if (
        !selectedAgentsFromState?.length &&
        job.workflow_steps &&
        job.workflow_steps.length > 0
      ) {
        setSelectedAgents(Array.from(new Set(job.workflow_steps.map((s: WorkflowStep) => s.agent_id))))
      }
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to load job')
    } finally {
      setIsLoadingJob(false)
    }
  }

  const loadAgents = async () => {
    try {
      const agents = await agentsAPI.list('active')
      setAvailableAgents(agents)
    } catch (err) {
      console.error('Failed to load agents:', err)
    }
  }

  const loadTools = async () => {
    try {
      const [tools, conns] = await Promise.all([mcpAPI.listTools(), mcpAPI.listConnections()])
      setPlatformTools(tools)
      setConnections(conns)
    } catch (err) {
      console.error('Failed to load tools:', err)
    }
  }

  const togglePlatformTool = (id: number) => {
    setSelectedPlatformToolIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    )
  }
  const toggleConnection = (id: number) => {
    setSelectedConnectionIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    )
  }  
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const files = Array.from(e.target.files)
      setSelectedFiles(files)
    }
  }

  const removeFile = (index: number) => {
    setSelectedFiles(prev => prev.filter((_, i) => i !== index))
  }

  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 Bytes'
    const k = 1024
    const sizes = ['Bytes', 'KB', 'MB', 'GB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i]
  }

  const toggleAgent = (agentId: number) => {
    setSelectedAgents((prev) =>
      prev.includes(agentId) ? prev.filter((id) => id !== agentId) : [...prev, agentId]
    )
  }

  const formatAgentPricing = (agent: Agent): string => {
    switch (agent.pricing_model) {
      case 'monthly':
        return agent.monthly_price
          ? `$${agent.monthly_price.toFixed(2)}/month`
          : 'Pricing not set'
      case 'quarterly':
        return agent.quarterly_price
          ? `$${agent.quarterly_price.toFixed(2)}/quarter`
          : 'Pricing not set'
      case 'pay_per_use':
      default:
        return `$${agent.price_per_task.toFixed(2)} per task`
    }
  }

  const goBackToJob = () => {
    navigate(id ? `/jobs/${id}` : '/dashboard', {
      state: { selectedAgents },
    })
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!id) return
    
    setIsLoading(true)
    setError('')
    try {
      await jobsAPI.update(
        parseInt(id),
        {
          ...formData,
          // Same contract as create job: always send both arrays (possibly empty) so the API
          // stores explicit scope and Build workflow → Tools per agent lists stay in sync.
          allowed_platform_tool_ids: selectedPlatformToolIds,
          allowed_connection_ids: selectedConnectionIds,
          tool_visibility: toolVisibility,
        },
        selectedFiles.length > 0 ? selectedFiles : undefined
      )
      
      // If new files were uploaded, redirect to job detail to show Q&A (analysis happens automatically on backend)
      if (selectedFiles.length > 0) {
        // Wait a moment for backend to process analysis, then navigate
        setTimeout(() => {
          navigate(`/jobs/${id}?qa=true`, { state: { selectedAgents } })
        }, 1000)
      } else {
        navigate(`/jobs/${id}`, { state: { selectedAgents } })
      }
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to update job')
      setIsLoading(false)
    }
  }

  if (isLoadingJob) {
    return (
      <div className="container mx-auto px-4 py-8 min-h-screen">
        <div className="max-w-2xl mx-auto">
          <div className="flex items-center justify-center min-h-[400px]">
            <div className="text-center">
              <div className="inline-block animate-spin rounded-full h-12 w-12 border-4 border-primary-500 border-t-transparent mb-4"></div>
              <p className="text-white/60 text-lg font-semibold">Loading job...</p>
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="container mx-auto px-4 py-12 min-h-screen">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center mb-10">
          <button
            onClick={goBackToJob}
            className="flex items-center text-white/70 hover:text-white transition-all duration-200 mr-6 px-4 py-2.5 rounded-xl hover:bg-dark-200/50"
          >
            <svg className="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            <span className="font-semibold">Back to Job</span>
          </button>
          <h1 className="text-6xl font-black text-white tracking-tight">Edit Job</h1>
        </div>
        {error && (
          <div className="bg-red-500/20 border-2 border-red-500/50 text-red-400 px-6 py-4 rounded-2xl mb-6 font-semibold">
            {error}
          </div>
        )}
        <form onSubmit={handleSubmit} className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-10 border border-dark-200/50">
          <div className="mb-6">
            <label className="block text-white font-bold mb-2" htmlFor="title">
              Job Title
            </label>
            <input
              id="title"
              type="text"
              value={formData.title}
              onChange={(e) => setFormData({ ...formData, title: e.target.value })}
              className="w-full px-3 py-2 bg-white border-2 border-gray-300 rounded-lg text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-500"
              required
            />
          </div>
          <div className="mb-6">
            <label className="block text-white font-bold mb-2" htmlFor="description">
              Description
            </label>
            <textarea
              id="description"
              value={formData.description}
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              rows={5}
              className="w-full px-3 py-2 bg-white border-2 border-gray-300 rounded-lg text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
          </div>
          <div className="mb-8">
            <label className="block text-white font-bold mb-3 text-lg">
              Tools for this job
            </label>
            <p className="text-sm text-white/60 mb-3 font-medium">
              Only tools you select here are in scope for this job and appear in Build workflow → Tools per agent.
              Leave all unchecked for no job-level tools (add-tool dropdowns there stay empty).
            </p>
            {jobOriginalPlatformToolIds.length > 0 && platformTools.length > 0 && (() => {
              const currentToolIds = new Set(platformTools.map((t) => t.id))
              const missingCount = jobOriginalPlatformToolIds.filter((id) => !currentToolIds.has(id)).length
              if (missingCount === 0) return null
              return (
                <p className="text-sm text-amber-400/90 mb-3 font-medium">
                  This job was run with {missingCount} tool{missingCount !== 1 ? 's' : ''} that are no longer in your list. Re-add them in MCP Server if you want to use them again.
                </p>
              )
            })()}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
              {platformTools.length > 0 && (
                <div className="border-2 border-dark-300 rounded-xl p-4 bg-dark-200/30">
                  <h4 className="text-white font-semibold mb-2">Platform tools</h4>
                  <div className="space-y-2 max-h-40 overflow-y-auto">
                    {platformTools.map((t) => (
                      <label key={t.id} className="flex items-center gap-2 cursor-pointer text-white/90">
                        <input
                          type="checkbox"
                          checked={selectedPlatformToolIds.includes(t.id)}
                          onChange={() => togglePlatformTool(t.id)}
                          className="w-4 h-4 text-primary-600 rounded"
                        />
                        <span className="text-sm">{t.name}</span>
                        <span className="text-xs text-white/50">({t.tool_type})</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
              {connections.length > 0 && (
                <div className="border-2 border-dark-300 rounded-xl p-4 bg-dark-200/30">
                  <h4 className="text-white font-semibold mb-2">MCP connections</h4>
                  <div className="space-y-2 max-h-40 overflow-y-auto">
                    {connections.map((c) => (
                      <label key={c.id} className="flex items-center gap-2 cursor-pointer text-white/90">
                        <input
                          type="checkbox"
                          checked={selectedConnectionIds.includes(c.id)}
                          onChange={() => toggleConnection(c.id)}
                          className="w-4 h-4 text-primary-600 rounded"
                        />
                        <span className="text-sm truncate">{c.name}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </div>
            {platformTools.length === 0 && connections.length === 0 && (
              <p className="text-white/50 text-sm">No tools or connections configured yet. Add them in MCP Server to make them available for jobs.</p>
            )}
            <div className="mt-4 pt-4 border-t border-dark-300">
              <label className="block text-white font-bold mb-2">Tool visibility (what agents see)</label>
              <select
                value={toolVisibility}
                onChange={(e) => setToolVisibility(e.target.value as 'full' | 'names_only' | 'none')}
                className="px-4 py-2.5 bg-white border-2 border-gray-300 rounded-lg text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-500 w-full max-w-xl"
              >
                <option value="full">Full — Names, descriptions, schema & business context</option>
                <option value="names_only">Names only — Tool names and short description; no schema or DB context</option>
                <option value="none">None — No tool list; agents cannot use MCP tools for this job</option>
              </select>
              <p className="text-sm text-white/50 mt-1.5">Credentials are never shared. This only controls how much tool metadata agents receive.</p>
            </div>
          </div>
          <div className="mb-8">
            <label className="block text-white font-bold mb-3 text-lg">
              Existing Documents
            </label>
            {existingFiles.length === 0 ? (
              <p className="text-sm text-white/50 font-medium">No documents uploaded yet</p>
            ) : (
              <div className="space-y-3 mb-6">
                {existingFiles.map((file) => (
                  <div
                    key={file.id}
                    className="flex items-center justify-between p-4 bg-dark-200/30 rounded-xl border border-dark-300"
                  >
                    <div className="flex items-center gap-3">
                      <div className="p-2 bg-primary-500/20 rounded-lg border border-primary-500/30">
                        <svg className="w-5 h-5 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                        </svg>
                      </div>
                      <div>
                        <span className="text-sm font-bold text-white">{file.name}</span>
                        <span className="text-xs text-white/50 ml-2 font-medium">({formatFileSize(file.size)})</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <label className="block text-white font-bold mb-3 text-lg" htmlFor="files">
              Upload New Documents (overwrite existing)
            </label>
            <p className="text-sm text-white/50 mb-4 font-medium">
              Supported formats: CSV, TXT, DOC, DOCX, PDF, XLS, XLSX, JSON, XML, MD, RTF, ODT, ODS. Uploading new files will replace existing BRD documents for this job.
            </p>
            <input
              id="files"
              type="file"
              multiple
              onChange={handleFileChange}
              accept=".csv,.txt,.doc,.docx,.pdf,.xls,.xlsx,.json,.xml,.md,.rtf,.odt,.ods"
              className="w-full px-5 py-4 bg-dark-200/50 border-2 border-dashed border-dark-300 rounded-xl text-white/70 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 cursor-pointer file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-semibold file:bg-primary-500 file:text-white hover:file:bg-primary-600"
            />
            {selectedFiles.length > 0 && (
              <div className="mt-4 space-y-3">
                {selectedFiles.map((file, index) => (
                  <div
                    key={index}
                    className="flex items-center justify-between p-4 bg-dark-200/30 rounded-xl border border-dark-300"
                  >
                    <div className="flex items-center gap-4 flex-1">
                      <div className="p-2 bg-primary-500/20 rounded-lg border border-primary-500/30">
                        <svg className="w-5 h-5 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                        </svg>
                      </div>
                      <div className="flex-1">
                        <p className="text-sm font-bold text-white">{file.name}</p>
                        <p className="text-xs text-white/50 font-medium">{formatFileSize(file.size)}</p>
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => removeFile(index)}
                      className="ml-4 text-red-400 hover:text-red-300 p-2 hover:bg-red-500/20 rounded-lg transition-colors"
                    >
                      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  </div>
                ))}
              </div>
            )}
            {selectedFiles.length > 0 && (
              <p className="text-sm text-primary-400 mt-4 font-semibold">
                ℹ️ New documents will overwrite previous BRDs and trigger automatic analysis/questions.
              </p>
            )}
          </div>
          <div className="mb-8">
            <label className="block text-white font-bold mb-3 text-lg">
              Select Agents
            </label>
            <p className="text-sm text-white/50 mb-4 font-medium">
              Choose which AI agents to use for this job. These will be pre-selected when you build the workflow.
            </p>
            <div className="space-y-3 max-h-80 overflow-y-auto border-2 border-dark-300 rounded-xl p-5 bg-dark-200/30">
              {availableAgents.length === 0 ? (
                <p className="text-white/50 text-center py-8 font-medium">No agents available</p>
              ) : (
                availableAgents.map((agent) => (
                  <label
                    key={agent.id}
                    className="flex items-center space-x-4 cursor-pointer hover:bg-dark-200/50 p-4 rounded-xl transition-colors border border-transparent hover:border-primary-500/30"
                  >
                    <input
                      type="checkbox"
                      checked={selectedAgents.includes(agent.id)}
                      onChange={() => toggleAgent(agent.id)}
                      className="w-5 h-5 text-primary-600 bg-dark-200 border-dark-300 rounded focus:ring-primary-500 focus:ring-2"
                    />
                    <div className="flex-1">
                      <div className="font-bold text-white text-lg">{agent.name}</div>
                      <div className="text-sm text-primary-400 font-semibold mt-1">
                        {formatAgentPricing(agent)}
                      </div>
                    </div>
                  </label>
                ))
              )}
            </div>
            {selectedAgents.length === 0 && (
              <p className="text-sm text-amber-400 mt-2 font-medium">
                Please select at least one agent before building the workflow.
              </p>
            )}
          </div>
          <div className="mb-8">
            <label className="block text-white font-bold mb-3 text-lg" htmlFor="status">
              Status
            </label>
            <select
              id="status"
              value={formData.status}
              onChange={(e) => setFormData({ ...formData, status: e.target.value as Job['status'] })}
              className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium"
            >
              <option value="draft" className="bg-white text-gray-900">Draft</option>
              <option value="pending_approval" className="bg-white text-gray-900">Pending Approval</option>
              <option value="approved" className="bg-white text-gray-900">Approved</option>
              <option value="in_progress" className="bg-white text-gray-900">In Progress</option>
              <option value="completed" className="bg-white text-gray-900">Completed</option>
              <option value="failed" className="bg-white text-gray-900">Failed</option>
              <option value="cancelled" className="bg-white text-gray-900">Cancelled</option>
            </select>
            <p className="text-sm text-white/50 mt-3 font-medium">
              Note: Only draft jobs can have their title and description updated. Status can be updated for any job.
            </p>
          </div>
          <div className="flex justify-end gap-4">
            <button
              type="button"
              onClick={goBackToJob}
              className="bg-dark-200/50 text-white/80 hover:text-white px-6 py-3 rounded-xl font-bold hover:bg-dark-200 border border-dark-300 transition-all duration-200"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isLoading}
              className="bg-gradient-to-r from-primary-500 to-primary-700 text-white px-8 py-3 rounded-xl font-bold hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100"
            >
              {isLoading ? (
                <span className="flex items-center gap-2">
                  <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent"></div>
                  Updating...
                </span>
              ) : (
                'Update Job'
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
