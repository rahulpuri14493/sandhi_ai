import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { agentsAPI } from '../lib/api'
import type { Agent } from '../lib/types'

export default function AgentDetailPage() {
  const { id } = useParams<{ id: string }>()
  const agentId = parseInt(id || '0')
  const [agent, setAgent] = useState<Agent | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    if (agentId) {
      loadAgent()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId])

  const loadAgent = async () => {
    setIsLoading(true)
    try {
      const data = await agentsAPI.get(agentId)
      setAgent(data)
    } catch (error) {
      console.error('Failed to load agent:', error)
    } finally {
      setIsLoading(false)
    }
  }

  if (isLoading) {
    return (
      <div className="container mx-auto px-4 py-8 min-h-screen">
        <div className="flex items-center justify-center min-h-[400px]">
          <div className="text-center">
            <div className="inline-block animate-spin rounded-full h-12 w-12 border-4 border-primary-500 border-t-transparent mb-4"></div>
            <p className="text-white/60 text-lg font-semibold">Loading agent...</p>
          </div>
        </div>
      </div>
    )
  }

  if (!agent) {
    return (
      <div className="container mx-auto px-4 py-8 min-h-screen">
        <div className="text-center py-16">
          <p className="text-white/60 text-xl font-semibold">Agent not found</p>
        </div>
      </div>
    )
  }

  return (
    <div className="container mx-auto px-4 py-12 min-h-screen">
      <div className="max-w-4xl mx-auto">
        <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-10 border border-dark-200/50">
          <h1 className="text-6xl font-black text-white tracking-tight mb-6">{agent.name}</h1>
          <p className="text-white/70 text-xl mb-8 font-medium leading-relaxed">{agent.description}</p>

          <div className="grid md:grid-cols-2 gap-6 mb-8">
            <div className="p-6 bg-dark-200/30 rounded-xl border border-dark-300">
              <h3 className="font-bold text-white mb-3 text-lg">Pricing</h3>
              {agent.pricing_model === 'monthly' && agent.monthly_price ? (
                <p className="text-3xl font-black text-primary-400">
                  ${agent.monthly_price.toFixed(2)} <span className="text-base font-medium text-white/50">/month</span>
                </p>
              ) : agent.pricing_model === 'quarterly' && agent.quarterly_price ? (
                <p className="text-3xl font-black text-primary-400">
                  ${agent.quarterly_price.toFixed(2)} <span className="text-base font-medium text-white/50">/quarter</span>
                </p>
              ) : (
                <>
                  <p className="text-3xl font-black text-primary-400">
                    ${agent.price_per_task.toFixed(2)} <span className="text-base font-medium text-white/50">per task</span>
                  </p>
                  <p className="text-sm text-white/50 mt-2 font-medium">
                    ${agent.price_per_communication.toFixed(2)} per communication
                  </p>
                </>
              )}
            </div>
            <div className="p-6 bg-dark-200/30 rounded-xl border border-dark-300">
              <h3 className="font-bold text-white mb-3 text-lg">Status</h3>
              <span className={`px-4 py-2 rounded-full text-sm font-bold border ${
                agent.status === 'active' 
                  ? 'bg-green-500/20 text-green-400 border-green-500/50' 
                  : 'bg-dark-200/50 text-white/60 border-dark-300'
              }`}>
                {agent.status.toUpperCase()}
              </span>
            </div>
          </div>

          {agent.capabilities && agent.capabilities.length > 0 && (
            <div className="mb-8">
              <h3 className="font-black text-white mb-4 text-xl">Capabilities</h3>
              <div className="flex flex-wrap gap-3">
                {agent.capabilities.map((cap, idx) => (
                  <span
                    key={idx}
                    className="px-4 py-2 bg-primary-500/20 text-primary-400 rounded-xl border border-primary-500/30 font-semibold"
                  >
                    {cap}
                  </span>
                ))}
              </div>
            </div>
          )}

          {agent.api_endpoint && (
            <div className="mb-6">
              <h3 className="font-black text-white mb-3 text-xl">API Endpoint</h3>
              <code className="bg-dark-200/50 px-4 py-3 rounded-xl text-sm text-white/80 font-mono border border-dark-300 block">
                {agent.api_endpoint}
              </code>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
