import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { jobsAPI, agentsAPI, mcpAPI } from '../lib/api'
import type { Agent } from '../lib/types'
import type { MCPToolConfigRes, MCPServerConnectionRes } from '../lib/api'

export default function NewJobPage() {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [selectedAgents, setSelectedAgents] = useState<number[]>([])
  const [availableAgents, setAvailableAgents] = useState<Agent[]>([])
  const [platformTools, setPlatformTools] = useState<MCPToolConfigRes[]>([])
  const [connections, setConnections] = useState<MCPServerConnectionRes[]>([])
  const [selectedPlatformToolIds, setSelectedPlatformToolIds] = useState<number[]>([])
  const [selectedConnectionIds, setSelectedConnectionIds] = useState<number[]>([])
  const [toolVisibility, setToolVisibility] = useState<'full' | 'names_only' | 'none'>('none')
  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState('')
  const navigate = useNavigate()

  useEffect(() => {
    loadAgents()
    loadTools()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadAgents = async () => {
    try {
      const agents = await agentsAPI.list('active')
      setAvailableAgents(agents)
    } catch (error) {
      console.error('Failed to load agents:', error)
    }
  }

  const loadTools = async () => {
    try {
      const [tools, conns] = await Promise.all([mcpAPI.listTools(), mcpAPI.listConnections()])
      setPlatformTools(tools)
      setConnections(conns)
    } catch (error) {
      console.error('Failed to load tools:', error)
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
      setSelectedFiles(prev => [...prev, ...files])
    }
    e.target.value = ''
  }

  const handleFolderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const files = Array.from(e.target.files)
      setSelectedFiles(prev => [...prev, ...files])
    }
    e.target.value = ''
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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (selectedAgents.length === 0) {
      setError('Please select at least one agent')
      return
    }

    setIsLoading(true)
    setError('')
    try {
      const hasPlatform = selectedPlatformToolIds.length > 0
      const hasConn = selectedConnectionIds.length > 0
      const job = await jobsAPI.create({ 
        title, 
        description,
        files: selectedFiles.length > 0 ? selectedFiles : undefined,
        allowed_platform_tool_ids: hasPlatform ? selectedPlatformToolIds : (hasConn ? [] : undefined),
        allowed_connection_ids: hasConn ? selectedConnectionIds : (hasPlatform ? [] : undefined),
        tool_visibility: toolVisibility,
      })
      navigate(`/jobs/${job.id}`, { state: { selectedAgents } })
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to create job')
    } finally {
      setIsLoading(false)
    }
  }

  const toggleAgent = (agentId: number) => {
    setSelectedAgents((prev) =>
      prev.includes(agentId)
        ? prev.filter((id) => id !== agentId)
        : [...prev, agentId]
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

  return (
    <div className="container mx-auto px-4 py-12 min-h-screen">
      <div className="max-w-3xl mx-auto">
        <h1 className="text-6xl font-black text-white tracking-tight mb-4">Create New Job</h1>
        <p className="text-white/60 text-xl font-medium mb-10">Define your requirements and let AI agents handle the work</p>
        {error && (
          <div className="bg-red-500/20 border-2 border-red-500/50 text-red-400 px-6 py-4 rounded-2xl mb-6 font-semibold">
            {error}
          </div>
        )}
        <form onSubmit={handleSubmit} className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-10 border border-dark-200/50">
          <div className="mb-8">
            <label className="block text-white font-bold mb-3 text-lg" htmlFor="title">
              Job Title
            </label>
            <input
              id="title"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium"
              placeholder="Enter job title..."
              required
            />
          </div>
          <div className="mb-8">
            <label className="block text-white font-bold mb-3 text-lg" htmlFor="description">
              Description
            </label>
            <textarea
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={6}
              className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium resize-none"
              placeholder="Describe what you need..."
            />
          </div>
          <div className="mb-8">
            <label className="block text-white font-bold mb-2 text-lg">Tool visibility (optional)</label>
            <p className="text-sm text-white/50 mb-2 font-medium">Control how much tool info agents see. Credentials are never shared.</p>
            <select
              value={toolVisibility}
              onChange={(e) => setToolVisibility(e.target.value as 'full' | 'names_only' | 'none')}
              className="px-4 py-2.5 bg-white border-2 border-gray-300 rounded-xl text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-500 w-full max-w-xl"
            >
              <option value="full">Full — Names, descriptions, schema & business context</option>
              <option value="names_only">Names only — Tool names and short description; no schema or DB context</option>
              <option value="none">None — No tool list; agents cannot use MCP tools for this job</option>
            </select>
          </div>
          <div className="mb-8">
            <label className="block text-white font-bold mb-3 text-lg" htmlFor="files">
              Upload Documents (optional)
            </label>
            <p className="text-sm text-white/50 mb-4 font-medium">
              Supported: CSV, TXT, DOC, DOCX, PDF, XLS, XLSX, JSON, XML, MD, RTF, ODT, ODS, ZIP. You can also select a folder to add all files inside it.
            </p>
            <div className="flex flex-wrap gap-3">
              <label className="flex-1 min-w-[200px] cursor-pointer">
                <input
                  id="files"
                  type="file"
                  multiple
                  onChange={handleFileChange}
                  accept=".csv,.txt,.doc,.docx,.pdf,.xls,.xlsx,.json,.xml,.md,.rtf,.odt,.ods,.zip"
                  className="hidden"
                />
                <span className="block w-full px-5 py-4 bg-dark-200/50 border-2 border-dashed border-dark-300 rounded-xl text-white/90 focus-within:ring-2 focus-within:ring-primary-500 text-center font-semibold hover:bg-dark-200/70 transition-colors">
                  Choose files
                </span>
              </label>
              <label className="flex-1 min-w-[200px] cursor-pointer">
                <input
                  id="folder"
                  type="file"
                  multiple
                  onChange={handleFolderChange}
                  className="hidden"
                  {...{ webkitdirectory: true, directory: true }}
                />
                <span className="block w-full px-5 py-4 bg-dark-200/50 border-2 border-dashed border-dark-300 rounded-xl text-white/90 focus-within:ring-2 focus-within:ring-primary-500 text-center font-semibold hover:bg-dark-200/70 transition-colors">
                  Choose folder
                </span>
              </label>
            </div>
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
          </div>
          <div className="mb-8">
            <label className="block text-white font-bold mb-3 text-lg">
              Tools for this job (optional)
            </label>
            <p className="text-sm text-white/50 mb-3 font-medium">
              Choose which tools agents can use for this job. Leave all unchecked to allow every configured tool. If you check only MCP connections (e.g. pageindex), only those connections are used—no platform tools.
            </p>
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
          </div>
          <div className="mb-8">
            <label className="block text-white font-bold mb-3 text-lg">
              Select Agents
            </label>
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
          </div>
          <button
            type="submit"
            disabled={isLoading}
            className="w-full bg-gradient-to-r from-primary-500 to-primary-700 text-white py-5 rounded-xl font-black text-lg hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100"
          >
            {isLoading ? (
              <span className="flex items-center justify-center gap-3">
                <div className="animate-spin rounded-full h-5 w-5 border-2 border-white border-t-transparent"></div>
                Creating...
              </span>
            ) : (
              'Create Job'
            )}
          </button>
        </form>
      </div>
    </div>
  )
}
