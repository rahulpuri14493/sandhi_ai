import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { hiringAPI, dashboardsAPI } from '../lib/api'
import type { HiringPosition, Agent } from '../lib/types'

interface NominateAgentModalProps {
  position: HiringPosition
  onClose: () => void
  onSuccess: () => void
}

export function NominateAgentModal({ position, onClose, onSuccess }: NominateAgentModalProps) {
  const [agents, setAgents] = useState<Agent[]>([])
  const [selectedAgentId, setSelectedAgentId] = useState<number | null>(null)
  const [coverLetter, setCoverLetter] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [isLoadingAgents, setIsLoadingAgents] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    loadAgents()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadAgents = async () => {
    setIsLoadingAgents(true)
    try {
      // Get developer's own agents from dashboard API
      const data = await dashboardsAPI.getDeveloperAgents()
      setAgents(data)
    } catch (error) {
      console.error('Failed to load agents:', error)
    } finally {
      setIsLoadingAgents(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedAgentId) {
      setError('Please select an agent')
      return
    }

    setIsLoading(true)
    setError('')
    try {
      await hiringAPI.createNomination({
        hiring_position_id: position.id,
        agent_id: selectedAgentId,
        cover_letter: coverLetter || undefined,
      })
      onSuccess()
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to nominate agent')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-dark-100/95 backdrop-blur-xl rounded-2xl shadow-2xl max-w-2xl w-full max-h-[90vh] overflow-y-auto border border-dark-200/50">
        <div className="p-8">
          <div className="flex justify-between items-center mb-6">
            <h2 className="text-4xl font-black text-white tracking-tight">Nominate Agent for Position</h2>
            <button
              onClick={onClose}
              className="text-white/60 hover:text-white text-3xl transition-colors"
            >
              ×
            </button>
          </div>

          <div className="mb-6 p-4 bg-dark-200/30 rounded-xl border border-dark-300">
            <h3 className="font-black text-white text-xl mb-2">{position.title}</h3>
            {position.description && (
              <p className="text-white/70 text-sm mb-2 font-medium">{position.description}</p>
            )}
          </div>

          {error && (
            <div className="bg-red-500/20 border-2 border-red-500/50 text-red-400 px-6 py-4 rounded-2xl mb-6 font-semibold">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit}>
            <div className="mb-6">
              <label className="block text-white font-bold mb-3 text-lg">
                Select Your Agent *
              </label>
              {isLoadingAgents ? (
                <div className="text-white/60 font-medium">Loading your agents...</div>
              ) : agents.length === 0 ? (
                <div className="text-white/60 mb-4 font-medium">
                  You don't have any agents yet.{' '}
                  <Link to="/agents/new" className="text-primary-400 hover:text-primary-300 font-bold">
                    Create one now
                  </Link>
                </div>
              ) : (
                <select
                  value={selectedAgentId || ''}
                  onChange={(e) => setSelectedAgentId(parseInt(e.target.value))}
                  className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium"
                  required
                >
                  <option value="" className="bg-dark-100">-- Select an agent --</option>
                  {agents.map((agent) => (
                    <option key={agent.id} value={agent.id} className="bg-dark-100">
                      {agent.name} {agent.status !== 'active' && `(${agent.status})`}
                    </option>
                  ))}
                </select>
              )}
            </div>

            <div className="mb-6">
              <label className="block text-white font-bold mb-3 text-lg">
                Cover Letter (Optional)
              </label>
              <textarea
                value={coverLetter}
                onChange={(e) => setCoverLetter(e.target.value)}
                rows={5}
                placeholder="Explain why your agent is a good fit for this position..."
                className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium resize-none"
              />
            </div>

            <div className="flex justify-end gap-4">
              <button
                type="button"
                onClick={onClose}
                className="bg-dark-200/50 text-white/80 hover:text-white px-6 py-3 rounded-xl font-bold hover:bg-dark-200 border border-dark-300 transition-all duration-200"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={isLoading || !selectedAgentId || agents.length === 0}
                className="bg-gradient-to-r from-primary-500 to-primary-700 text-white px-8 py-3 rounded-xl font-bold hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100"
              >
                {isLoading ? (
                  <span className="flex items-center gap-2">
                    <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent"></div>
                    Submitting...
                  </span>
                ) : (
                  'Submit Nomination'
                )}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}
