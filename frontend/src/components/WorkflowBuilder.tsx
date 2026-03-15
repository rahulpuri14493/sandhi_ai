import { useState, useEffect, useRef } from 'react'
import { jobsAPI, agentsAPI, mcpAPI } from '../lib/api'
import type { Agent } from '../lib/types'
import type { Job } from '../lib/types'
import type { MCPToolConfigRes, MCPServerConnectionRes } from '../lib/api'

interface WorkflowBuilderProps {
  jobId: number
  onWorkflowCreated: () => void
  initialSelectedAgentIds?: number[]
  /** Job data (optional); if provided, used for job-level allowed tools and step tool pool */
  job?: Job | null
}

export type WorkflowCollaborationMode = 'from_brd' | 'independent' | 'sequential'

export type ToolVisibility = 'full' | 'names_only' | 'none'

/** Per-step tool assignment: agent_index -> { platformIds, connectionIds, toolVisibility? } */
type StepToolSelection = Record<number, { platformIds: number[]; connectionIds: number[]; toolVisibility?: ToolVisibility }>

export function WorkflowBuilder({ jobId, onWorkflowCreated, initialSelectedAgentIds, job }: WorkflowBuilderProps) {
  const [selectedAgents, setSelectedAgents] = useState<number[]>(initialSelectedAgentIds ?? [])
  const [availableAgents, setAvailableAgents] = useState<Agent[]>([])
  const [platformTools, setPlatformTools] = useState<MCPToolConfigRes[]>([])
  const [connections, setConnections] = useState<MCPServerConnectionRes[]>([])
  const [stepToolSelections, setStepToolSelections] = useState<StepToolSelection>({})
  const stepToolSelectionsRef = useRef<StepToolSelection>({})
  const [jobToolVisibility, setJobToolVisibility] = useState<ToolVisibility>('full')
  const [mode, setMode] = useState<'auto' | 'manual'>('auto')
  const [workflowCollaboration, setWorkflowCollaboration] = useState<WorkflowCollaborationMode>('from_brd')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    stepToolSelectionsRef.current = stepToolSelections
  }, [stepToolSelections])

  useEffect(() => {
    loadAgents()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSelectedAgentIds])

  useEffect(() => {
    loadTools()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, job?.id, job?.allowed_platform_tool_ids, job?.allowed_connection_ids])

  useEffect(() => {
    if (initialSelectedAgentIds && initialSelectedAgentIds.length > 0) {
      setSelectedAgents(initialSelectedAgentIds)
    }
  }, [initialSelectedAgentIds])

  // When job has workflow steps, pre-fill step tool selections from saved state (by step order)
  useEffect(() => {
    const jobData = job ?? null
    const steps = jobData?.workflow_steps
    if (jobData?.tool_visibility) setJobToolVisibility(jobData.tool_visibility as ToolVisibility)
    if (!steps?.length || selectedAgents.length === 0) return
    // Steps are ordered by step_order; index i = step_order - 1
    const next: StepToolSelection = {}
    steps.forEach((step, i) => {
      const platformIds = step.allowed_platform_tool_ids ?? []
      const connectionIds = step.allowed_connection_ids ?? []
      const toolVisibility = step.tool_visibility as ToolVisibility | undefined
      next[i] = { platformIds, connectionIds, toolVisibility }
    })
    setStepToolSelections((prev) => {
      // Only overwrite when we have steps and prev is empty or we're initializing from job
      if (Object.keys(prev).length === 0) return next
      return prev
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id, job?.tool_visibility, job?.workflow_steps, selectedAgents.length])

  const loadAgents = async () => {
    try {
      const agents = await agentsAPI.list('active')
      if (initialSelectedAgentIds && initialSelectedAgentIds.length > 0) {
        setAvailableAgents(agents.filter((a: Agent) => initialSelectedAgentIds!.includes(a.id)))
      } else {
        setAvailableAgents(agents)
      }
    } catch (error) {
      console.error('Failed to load agents:', error)
    }
  }

  const loadTools = async () => {
    try {
      const [tools, conns] = await Promise.all([mcpAPI.listTools(), mcpAPI.listConnections()])
      const jobData = job ?? (jobId ? await jobsAPI.get(jobId).catch(() => null) : null)
      const allowedPlatform = jobData?.allowed_platform_tool_ids
      const allowedConn = jobData?.allowed_connection_ids
      setPlatformTools(
        allowedPlatform?.length
          ? tools.filter((t: MCPToolConfigRes) => allowedPlatform.includes(t.id))
          : tools
      )
      setConnections(
        allowedConn?.length
          ? conns.filter((c: MCPServerConnectionRes) => allowedConn.includes(c.id))
          : conns
      )
    } catch (error) {
      console.error('Failed to load tools:', error)
    }
  }

  const setStepTools = (agentIndex: number, platformIds: number[], connectionIds: number[], toolVisibility?: ToolVisibility) => {
    setStepToolSelections((prev) => {
      const next = { ...prev, [agentIndex]: { platformIds, connectionIds, toolVisibility } }
      stepToolSelectionsRef.current = next
      return next
    })
  }

  const toggleStepPlatformTool = (agentIndex: number, toolId: number) => {
    const cur = stepToolSelections[agentIndex] ?? { platformIds: [], connectionIds: [] }
    const platformIds = cur.platformIds.includes(toolId)
      ? cur.platformIds.filter((id) => id !== toolId)
      : [...cur.platformIds, toolId]
    setStepTools(agentIndex, platformIds, cur.connectionIds, cur.toolVisibility)
  }

  const toggleStepConnection = (agentIndex: number, connId: number) => {
    const cur = stepToolSelections[agentIndex] ?? { platformIds: [], connectionIds: [] }
    const connectionIds = cur.connectionIds.includes(connId)
      ? cur.connectionIds.filter((id) => id !== connId)
      : [...cur.connectionIds, connId]
    setStepTools(agentIndex, cur.platformIds, connectionIds, cur.toolVisibility)
  }

  const addStepPlatformTool = (agentIndex: number, toolId: number) => {
    const cur = stepToolSelections[agentIndex] ?? { platformIds: [], connectionIds: [] }
    if (cur.platformIds.includes(toolId)) return
    setStepTools(agentIndex, [...cur.platformIds, toolId], cur.connectionIds, cur.toolVisibility)
  }

  const addStepConnection = (agentIndex: number, connId: number) => {
    const cur = stepToolSelections[agentIndex] ?? { platformIds: [], connectionIds: [] }
    if (cur.connectionIds.includes(connId)) return
    setStepTools(agentIndex, cur.platformIds, [...cur.connectionIds, connId], cur.toolVisibility)
  }

  const handleAutoSplit = async () => {
    if (selectedAgents.length === 0) {
      setError('Please select at least one agent')
      return
    }

    setIsLoading(true)
    setError('')
    try {
      const workflowMode =
        workflowCollaboration === 'from_brd'
          ? undefined
          : workflowCollaboration === 'independent'
            ? 'independent'
            : 'sequential'
      // Read from ref so we always use the latest tool selections (avoids stale state when user clicks Create right after checking a box)
      const current = stepToolSelectionsRef.current
      const stepTools = selectedAgents.map((_, idx) => {
        const sel = current[idx]
        const hasPlatform = (sel?.platformIds?.length ?? 0) > 0
        const hasConn = (sel?.connectionIds?.length ?? 0) > 0
        // When only connections are selected, send empty platform list so backend does not inherit job-level platform tools
        const platformIds = hasPlatform ? sel!.platformIds : (hasConn ? [] : undefined)
        const connectionIds = hasConn ? sel!.connectionIds : (hasPlatform ? [] : undefined)
        return {
          agent_index: idx,
          allowed_platform_tool_ids: platformIds,
          allowed_connection_ids: connectionIds,
          tool_visibility: sel?.toolVisibility,
        }
      })
      const hasStepTools = stepTools.some((s) => (s.allowed_platform_tool_ids?.length ?? 0) > 0 || (s.allowed_connection_ids?.length ?? 0) > 0 || s.tool_visibility)
      await jobsAPI.autoSplitWorkflow(jobId, selectedAgents, workflowMode, hasStepTools ? stepTools : undefined, jobToolVisibility !== 'full' ? jobToolVisibility : undefined)
      onWorkflowCreated()
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to create workflow')
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

  return (
    <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50">
      <div className="mb-6 p-5 bg-primary-500/10 border-2 border-primary-500/30 rounded-xl">
        <h3 className="font-bold text-primary-400 mb-2 text-base">Workflow mode</h3>
        <p className="text-sm text-white/70 font-medium mb-2">
          All agents are called over A2A by the platform. The badge below is about collaboration style, not transport.
        </p>
        <p className="text-sm text-white/90 font-medium">
          <strong>Sequential:</strong> Each agent receives the previous agent’s output (pipeline). Use agents without the A2A badge.
          <strong className="ml-1"> A2A:</strong> Agents collaborate asynchronously; choose agents with the &quot;A2A&quot; badge when your requirements need peer-to-peer collaboration.
        </p>
      </div>
      <div className="mb-6 p-5 bg-emerald-500/10 border-2 border-emerald-500/30 rounded-xl">
        <h3 className="font-bold text-emerald-400 mb-2 text-base">BRD &amp; Q&A → Auto-Split</h3>
        <p className="text-sm text-white/90 font-medium">
          Work is divided among agents based on your <strong>job prompt</strong> and <strong>BRD documents</strong>. Run <strong>Analyze Documents</strong> first so the AI can ask questions from your BRD; your answers are then used when splitting work. Each agent receives only its assigned subtask derived from the BRD and prompt.
        </p>
      </div>
      <div className="mb-8">
        <h2 className="text-4xl font-black text-white tracking-tight mb-6">Build Workflow</h2>
        <div className="flex gap-4 mb-6">
          <button
            onClick={() => setMode('auto')}
            className={`px-6 py-3.5 rounded-xl font-bold transition-all duration-200 ${
              mode === 'auto'
                ? 'bg-gradient-to-r from-primary-500 to-primary-700 text-white shadow-2xl shadow-primary-500/50 scale-105'
                : 'bg-dark-200/50 text-white/70 hover:text-white border border-dark-300'
            }`}
          >
            Auto-Split
          </button>
          <button
            onClick={() => setMode('manual')}
            className={`px-6 py-3.5 rounded-xl font-bold transition-all duration-200 ${
              mode === 'manual'
                ? 'bg-gradient-to-r from-primary-500 to-primary-700 text-white shadow-2xl shadow-primary-500/50 scale-105'
                : 'bg-dark-200/50 text-white/70 hover:text-white border border-dark-300'
            }`}
          >
            Manual Assignment
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-500/20 border-2 border-red-500/50 text-red-400 px-6 py-4 rounded-2xl mb-6 font-semibold">
          {error}
        </div>
      )}

      {mode === 'auto' && (
        <div>
          <h3 className="font-black text-white mb-5 text-xl">Select Agents for Auto-Split</h3>
          <div className="mb-6">
            <label className="block text-sm font-bold text-white/90 mb-2">Agents work</label>
            <select
              value={workflowCollaboration}
              onChange={(e) => setWorkflowCollaboration(e.target.value as WorkflowCollaborationMode)}
              className="px-4 py-2.5 bg-dark-200/80 border-2 border-dark-300 rounded-xl text-white font-medium focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
            >
              <option value="from_brd">From BRD / document (default)</option>
              <option value="independent">Independently (each agent gets only its task)</option>
              <option value="sequential">Sequentially (each agent receives previous agent output)</option>
            </select>
            <p className="text-xs text-white/50 mt-1.5">
              {workflowCollaboration === 'independent' && 'Best when tasks are separate (e.g. 2+5 and 9-1).'}
              {workflowCollaboration === 'sequential' && 'Best when agent 2 needs agent 1’s result (pipeline).'}
              {workflowCollaboration === 'from_brd' && 'Uses analyze-documents hint from your BRD when available.'}
            </p>
            {workflowCollaboration === 'from_brd' && job?.allowed_platform_tool_ids && job.allowed_platform_tool_ids.length > 0 && (
              <p className="text-xs text-emerald-400/90 mt-1.5 font-medium">
                Job tools will be assigned to all agents automatically. You can optionally restrict tools per agent below.
              </p>
            )}
          </div>
          <div className="mb-6">
            <label className="block text-sm font-bold text-white/90 mb-2">Tool visibility (what agents see)</label>
            <select
              value={jobToolVisibility}
              onChange={(e) => setJobToolVisibility(e.target.value as ToolVisibility)}
              className="px-4 py-2.5 bg-dark-200/80 border-2 border-dark-300 rounded-xl text-white font-medium focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
            >
              <option value="full">Full — Names, descriptions, schema & business context</option>
              <option value="names_only">Names only — Tool names and short description; no schema or DB context</option>
              <option value="none">None — No tool list; agents cannot use MCP tools for this job</option>
            </select>
            <p className="text-xs text-white/50 mt-1.5">Credentials are never shared. This only controls how much tool metadata (names, schema, etc.) agents receive.</p>
          </div>
          <div className="space-y-3 max-h-80 overflow-y-auto border-2 border-dark-300 rounded-xl p-5 mb-6 bg-dark-200/30">
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
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-bold text-white text-lg">{agent.name}</span>
                      {agent.a2a_enabled && (
                        <span className="px-2 py-0.5 text-xs font-bold bg-primary-500/30 text-primary-300 rounded border border-primary-500/50">
                          A2A
                        </span>
                      )}
                    </div>
                    <div className="text-sm text-primary-400 font-semibold mt-1">
                      ${agent.price_per_task.toFixed(2)} per task
                    </div>
                  </div>
                </label>
              ))
            )}
          </div>
          {(platformTools.length > 0 || connections.length > 0) && selectedAgents.length > 0 && (
            <div className="mb-6 p-5 bg-emerald-500/10 border-2 border-emerald-500/30 rounded-xl">
              <h4 className="font-bold text-emerald-400 mb-3">Tools per agent (optional)</h4>
              <p className="text-sm text-white/70 mb-4">Add only the tools each agent can use. If none are added, that agent will have access to all job tools.</p>
              <div className="space-y-4">
                {selectedAgents.map((agentId, idx) => {
                  const agent = availableAgents.find((a) => a.id === agentId)
                  const sel = stepToolSelections[idx] ?? { platformIds: [], connectionIds: [] }
                  const selectedPlatformTools = platformTools.filter((t) => sel.platformIds.includes(t.id))
                  const selectedConns = connections.filter((c) => sel.connectionIds.includes(c.id))
                  const availableToAddPlatform = platformTools.filter((t) => !sel.platformIds.includes(t.id))
                  const availableToAddConn = connections.filter((c) => !sel.connectionIds.includes(c.id))
                  const hasAnySelected = selectedPlatformTools.length > 0 || selectedConns.length > 0
                  return (
                    <div key={agentId} className="border border-dark-300 rounded-lg p-4 bg-dark-200/30">
                      <div className="font-bold text-white mb-2">Step {idx + 1}: {agent?.name ?? `Agent ${agentId}`}</div>
                      <div className="mb-3">
                        <label className="text-xs text-white/70 mr-2">Step tool visibility:</label>
                        <select
                          value={sel.toolVisibility ?? jobToolVisibility}
                          onChange={(e) => setStepTools(idx, sel.platformIds, sel.connectionIds, e.target.value as ToolVisibility)}
                          className="px-2 py-1 bg-dark-200/80 border border-dark-300 rounded text-white text-sm min-w-[200px]"
                          title="Full = names, descriptions, schema. Names only = names + short description. None = no tools."
                        >
                          <option value="full">Full (schema + context)</option>
                          <option value="names_only">Names only (no schema)</option>
                          <option value="none">None (no tools)</option>
                        </select>
                      </div>
                      <div className="space-y-2">
                        <div className="text-sm font-semibold text-white/90">Selected for this step:</div>
                        {!hasAnySelected ? (
                          <p className="text-xs text-white/50 italic">No tools selected — this step will use all job tools.</p>
                        ) : (
                          <div className="flex flex-wrap gap-2">
                            {selectedPlatformTools.map((t) => (
                              <span
                                key={t.id}
                                className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-primary-500/20 border border-primary-500/50 rounded-lg text-sm text-white"
                              >
                                <span>{t.name}</span>
                                <button
                                  type="button"
                                  onClick={() => toggleStepPlatformTool(idx, t.id)}
                                  className="text-white/70 hover:text-white focus:outline-none"
                                  aria-label={`Remove ${t.name}`}
                                >
                                  <span className="sr-only">Remove</span>×
                                </button>
                              </span>
                            ))}
                            {selectedConns.map((c) => (
                              <span
                                key={c.id}
                                className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-primary-500/20 border border-primary-500/50 rounded-lg text-sm text-white"
                              >
                                <span className="truncate max-w-[120px]">{c.name}</span>
                                <button
                                  type="button"
                                  onClick={() => toggleStepConnection(idx, c.id)}
                                  className="text-white/70 hover:text-white focus:outline-none"
                                  aria-label={`Remove ${c.name}`}
                                >
                                  <span className="sr-only">Remove</span>×
                                </button>
                              </span>
                            ))}
                          </div>
                        )}
                        {(availableToAddPlatform.length > 0 || availableToAddConn.length > 0) && (
                          <div className="flex flex-wrap items-center gap-2 pt-1">
                            <span className="text-xs text-white/70">Add tool:</span>
                            {availableToAddPlatform.length > 0 && (
                              <select
                                value=""
                                onChange={(e) => {
                                  const id = Number(e.target.value)
                                  if (id) addStepPlatformTool(idx, id)
                                  e.target.value = ''
                                }}
                                className="px-2 py-1 bg-dark-200/80 border border-dark-300 rounded text-white text-sm"
                              >
                                <option value="">— Platform tool —</option>
                                {availableToAddPlatform.map((t) => (
                                  <option key={t.id} value={t.id}>{t.name}</option>
                                ))}
                              </select>
                            )}
                            {availableToAddConn.length > 0 && (
                              <select
                                value=""
                                onChange={(e) => {
                                  const id = Number(e.target.value)
                                  if (id) addStepConnection(idx, id)
                                  e.target.value = ''
                                }}
                                className="px-2 py-1 bg-dark-200/80 border border-dark-300 rounded text-white text-sm"
                              >
                                <option value="">— Connection —</option>
                                {availableToAddConn.map((c) => (
                                  <option key={c.id} value={c.id}>{c.name}</option>
                                ))}
                              </select>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
          <button
            onClick={handleAutoSplit}
            disabled={isLoading || selectedAgents.length === 0}
            className="bg-gradient-to-r from-primary-500 to-primary-700 text-white px-8 py-4 rounded-xl font-bold hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100"
          >
            {isLoading ? (
              <span className="flex items-center gap-2">
                <div className="animate-spin rounded-full h-5 w-5 border-2 border-white border-t-transparent"></div>
                Creating...
              </span>
            ) : (
              'Create Auto-Split Workflow'
            )}
          </button>
        </div>
      )}

      {mode === 'manual' && (
        <div>
          <p className="text-white/60 text-lg font-medium mb-4">
            Manual workflow assignment coming soon...
          </p>
        </div>
      )}
    </div>
  )
}
