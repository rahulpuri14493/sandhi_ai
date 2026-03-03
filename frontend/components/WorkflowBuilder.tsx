'use client'

import { useState, useEffect } from 'react'
import { jobsAPI, agentsAPI } from '@/lib/api'
import type { Agent } from '@/lib/types'

interface WorkflowBuilderProps {
  jobId: number
  onWorkflowCreated: () => void
}

export function WorkflowBuilder({ jobId, onWorkflowCreated }: WorkflowBuilderProps) {
  const [selectedAgents, setSelectedAgents] = useState<number[]>([])
  const [availableAgents, setAvailableAgents] = useState<Agent[]>([])
  const [mode, setMode] = useState<'auto' | 'manual'>('auto')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    loadAgents()
  }, [])

  const loadAgents = async () => {
    try {
      const agents = await agentsAPI.list('active')
      setAvailableAgents(agents)
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
    <div className="bg-white rounded-lg shadow p-6">
      <div className="mb-6">
        <h2 className="text-2xl font-bold mb-4">Build Workflow</h2>
        <div className="flex gap-4 mb-4">
          <button
            onClick={() => setMode('auto')}
            className={`px-4 py-2 rounded-lg ${
              mode === 'auto'
                ? 'bg-primary-600 text-white'
                : 'bg-gray-200 text-gray-700'
            }`}
          >
            Auto-Split
          </button>
          <button
            onClick={() => setMode('manual')}
            className={`px-4 py-2 rounded-lg ${
              mode === 'manual'
                ? 'bg-primary-600 text-white'
                : 'bg-gray-200 text-gray-700'
            }`}
          >
            Manual Assignment
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">
          {error}
        </div>
      )}

      {mode === 'auto' && (
        <div>
          <h3 className="font-semibold mb-4">Select Agents for Auto-Split</h3>
          <div className="space-y-2 max-h-64 overflow-y-auto border border-gray-300 rounded-lg p-4 mb-4">
            {availableAgents.map((agent) => (
              <label
                key={agent.id}
                className="flex items-center space-x-3 cursor-pointer hover:bg-gray-50 p-2 rounded"
              >
                <input
                  type="checkbox"
                  checked={selectedAgents.includes(agent.id)}
                  onChange={() => toggleAgent(agent.id)}
                  className="w-4 h-4 text-primary-600"
                />
                <div className="flex-1">
                  <div className="font-medium">{agent.name}</div>
                  <div className="text-sm text-gray-600">
                    ${agent.price_per_task.toFixed(2)} per task
                  </div>
                </div>
              </label>
            ))}
          </div>
          <button
            onClick={handleAutoSplit}
            disabled={isLoading || selectedAgents.length === 0}
            className="bg-primary-600 text-white px-6 py-2 rounded-lg hover:bg-primary-700 disabled:opacity-50"
          >
            {isLoading ? 'Creating...' : 'Create Auto-Split Workflow'}
          </button>
        </div>
      )}

      {mode === 'manual' && (
        <div>
          <p className="text-gray-600 mb-4">
            Manual workflow assignment coming soon...
          </p>
        </div>
      )}
    </div>
  )
}
