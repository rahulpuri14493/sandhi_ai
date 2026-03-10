import { useState, useEffect } from 'react'
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

/** Per-step tool assignment: agent_index -> { platformIds, connectionIds } */
type StepToolSelection = Record<number, { platformIds: number[]; connectionIds: number[] }>

export function WorkflowBuilder({ jobId, onWorkflowCreated, initialSelectedAgentIds, job }: WorkflowBuilderProps) {
  const [selectedAgents, setSelectedAgents] = useState<number[]>(initialSelectedAgentIds ?? [])
  const [availableAgents, setAvailableAgents] = useState<Agent[]>([])
  const [platformTools, setPlatformTools] = useState<MCPToolConfigRes[]>([])
  const [connections, setConnections] = useState<MCPServerConnectionRes[]>([])
  const [stepToolSelections, setStepToolSelections] = useState<StepToolSelection>({})
  const [mode, setMode] = useState<'auto' | 'manual'>('auto')
  const [workflowCollaboration, setWorkflowCollaboration] = useState<WorkflowCollaborationMode>('from_brd')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState('')

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

  const setStepTools = (agentIndex: number, platformIds: number[], connectionIds: number[]) => {
    setStepToolSelections((prev) => ({ ...prev, [agentIndex]: { platformIds, connectionIds } }))
  }

  const toggleStepPlatformTool = (agentIndex: number, toolId: number) => {
    const cur = stepToolSelections[agentIndex] ?? { platformIds: [], connectionIds: [] }
    const platformIds = cur.platformIds.includes(toolId)
      ? cur.platformIds.filter((id) => id !== toolId)
      : [...cur.platformIds, toolId]
    setStepTools(agentIndex, platformIds, cur.connectionIds)
  }

  const toggleStepConnection = (agentIndex: number, connId: number) => {
    const cur = stepToolSelections[agentIndex] ?? { platformIds: [], connectionIds: [] }
    const connectionIds = cur.connectionIds.includes(connId)
      ? cur.connectionIds.filter((id) => id !== connId)
      : [...cur.connectionIds, connId]
    setStepTools(agentIndex, cur.platformIds, connectionIds)
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
      const stepTools = selectedAgents.map((_, idx) => {
        const sel = stepToolSelections[idx]
        return {
          agent_index: idx,
          allowed_platform_tool_ids: sel?.platformIds?.length ? sel.platformIds : undefined,
          allowed_connection_ids: sel?.connectionIds?.length ? sel.connectionIds : undefined,
        }
      }).filter((s) => (s.allowed_platform_tool_ids?.length ?? 0) > 0 || (s.allowed_connection_ids?.length ?? 0) > 0)
      await jobsAPI.autoSplitWorkflow(jobId, selectedAgents, workflowMode, stepTools.length ? stepTools : undefined)
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
              <h4 className="font-bold text-emerald-400 mb-3">Assign tools per agent (optional)</h4>
              <p className="text-sm text-white/70 mb-4">Restrict which tools each agent can use. E.g. Agent 1 = Postgres only, Agent 2 = arithmetic.</p>
              <div className="space-y-4">
                {selectedAgents.map((agentId, idx) => {
                  const agent = availableAgents.find((a) => a.id === agentId)
                  const sel = stepToolSelections[idx] ?? { platformIds: [], connectionIds: [] }
                  return (
                    <div key={agentId} className="border border-dark-300 rounded-lg p-4 bg-dark-200/30">
                      <div className="font-bold text-white mb-2">Step {idx + 1}: {agent?.name ?? `Agent ${agentId}`}</div>
                      <div className="flex flex-wrap gap-4">
                        {platformTools.length > 0 && (
                          <div className="flex flex-wrap gap-2">
                            {platformTools.map((t) => (
                              <label key={t.id} className="inline-flex items-center gap-1.5 text-sm text-white/90 cursor-pointer">
                                <input
                                  type="checkbox"
                                  checked={sel.platformIds.includes(t.id)}
                                  onChange={() => toggleStepPlatformTool(idx, t.id)}
                                  className="w-3.5 h-3.5 text-primary-600 rounded"
                                />
                                <span>{t.name}</span>
                              </label>
                            ))}
                          </div>
                        )}
                        {connections.length > 0 && (
                          <div className="flex flex-wrap gap-2">
                            {connections.map((c) => (
                              <label key={c.id} className="inline-flex items-center gap-1.5 text-sm text-white/90 cursor-pointer">
                                <input
                                  type="checkbox"
                                  checked={sel.connectionIds.includes(c.id)}
                                  onChange={() => toggleStepConnection(idx, c.id)}
                                  className="w-3.5 h-3.5 text-primary-600 rounded"
                                />
                                <span className="truncate max-w-[120px]">{c.name}</span>
                              </label>
                            ))}
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
