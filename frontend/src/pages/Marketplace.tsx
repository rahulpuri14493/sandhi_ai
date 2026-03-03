import { useEffect, useState } from 'react'
import { agentsAPI } from '../lib/api'
import type { Agent } from '../lib/types'
import { AgentCard } from '../components/AgentCard'
import { Link } from 'react-router-dom'

export default function MarketplacePage() {
  const [agents, setAgents] = useState<Agent[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [filter, setFilter] = useState({ status: '', capability: '' })

  useEffect(() => {
    loadAgents()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter])

  const loadAgents = async () => {
    setIsLoading(true)
    try {
      const data = await agentsAPI.list(
        filter.status || undefined,
        filter.capability || undefined
      )
      setAgents(data)
    } catch (error) {
      console.error('Failed to load agents:', error)
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="container mx-auto px-4 py-12 min-h-screen">
      <div className="flex justify-between items-center mb-10">
        <h1 className="text-6xl font-black text-white tracking-tight">Sandhi AI Marketplace</h1>
        <Link
          to="/jobs/new"
          className="bg-gradient-to-r from-primary-500 to-primary-700 text-white px-6 py-3 rounded-xl font-bold hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200"
        >
          Create New Job
        </Link>
      </div>

      <div className="mb-8 flex gap-4">
        <select
          value={filter.status}
          onChange={(e) => setFilter({ ...filter, status: e.target.value })}
          className="px-5 py-3 bg-white border-2 border-gray-300 rounded-xl text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 font-medium"
        >
          <option value="" className="bg-dark-100">All Status</option>
          <option value="active" className="bg-dark-100">Active</option>
          <option value="inactive" className="bg-dark-100">Inactive</option>
        </select>
        <input
          type="text"
          placeholder="Filter by capability..."
          value={filter.capability}
          onChange={(e) => setFilter({ ...filter, capability: e.target.value })}
          className="px-5 py-3 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 flex-1 font-medium"
        />
      </div>

      {isLoading ? (
        <div className="text-center py-16">
          <div className="inline-block animate-spin rounded-full h-12 w-12 border-4 border-primary-500 border-t-transparent mb-4"></div>
          <p className="text-white/60 text-lg font-semibold">Loading agents...</p>
        </div>
      ) : agents.length === 0 ? (
        <div className="text-center py-16">
          <p className="text-white/60 text-xl font-semibold">No agents found</p>
        </div>
      ) : (
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
          {agents.map((agent) => (
            <AgentCard key={agent.id} agent={agent} />
          ))}
        </div>
      )}
    </div>
  )
}
