import { useState, useEffect } from 'react'
import { jobsAPI, agentsAPI } from '../lib/api'
import type { Agent } from '../lib/types'

interface WorkflowBuilderProps {
  jobId: number
  onWorkflowCreated: () => void
  initialSelectedAgentIds?: number[]
}

export function WorkflowBuilder({ jobId, onWorkflowCreated, initialSelectedAgentIds }: WorkflowBuilderProps) {
  const [selectedAgents, setSelectedAgents] = useState<number[]>(initialSelectedAgentIds ?? [])
  const [availableAgents, setAvailableAgents] = useState<Agent[]>([])
  const [mode, setMode] = useState<'auto' | 'manual'>('auto')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    loadAgents()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSelectedAgentIds])

  useEffect(() => {
    if (initialSelectedAgentIds && initialSelectedAgentIds.length > 0) {
      setSelectedAgents(initialSelectedAgentIds)
    }
  }, [initialSelectedAgentIds])

  const loadAgents = async () => {
    try {
      const agents = await agentsAPI.list('active')
      if (initialSelectedAgentIds && initialSelectedAgentIds.length > 0) {
        setAvailableAgents(agents.filter((a) => initialSelectedAgentIds.includes(a.id)))
      } else {
        setAvailableAgents(agents)
      }
    } catch (error) {
      console.error('Failed to load agents:', error)
    }
  }

  const handleAutoSplit = async () => {
    if (selectedAgents.length === 0) {
      setError('Please select at least one agent')
      return
    }

    setIsLoading(true)
    setError('')
    try {
      await jobsAPI.autoSplitWorkflow(jobId, selectedAgents)
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
                    <div className="font-bold text-white text-lg">{agent.name}</div>
                    <div className="text-sm text-primary-400 font-semibold mt-1">
                      ${agent.price_per_task.toFixed(2)} per task
                    </div>
                  </div>
                </label>
              ))
            )}
          </div>
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
