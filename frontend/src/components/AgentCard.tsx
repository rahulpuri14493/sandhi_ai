import { Link } from 'react-router-dom'
import type { Agent } from '../lib/types'

interface AgentCardProps {
  agent: Agent
}

const formatAgentPricing = (agent: Agent): { price: string; label: string } => {
  switch (agent.pricing_model) {
    case 'monthly':
      return {
        price: agent.monthly_price 
          ? `$${agent.monthly_price.toFixed(2)}`
          : '$0.00',
        label: '/month'
      }
    case 'quarterly':
      return {
        price: agent.quarterly_price
          ? `$${agent.quarterly_price.toFixed(2)}`
          : '$0.00',
        label: '/quarter'
      }
    case 'pay_per_use':
    default:
      return {
        price: `$${agent.price_per_task.toFixed(2)}`,
        label: 'per task'
      }
  }
}

export function AgentCard({ agent }: AgentCardProps) {
  const pricing = formatAgentPricing(agent)
  
  return (
    <Link to={`/marketplace/agent/${agent.id}`}>
      <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-6 border border-dark-200/50 hover:border-primary-500/50 hover:shadow-2xl transition-all duration-200 card-hover">
        <h3 className="text-2xl font-black text-white mb-3">{agent.name}</h3>
        <p className="text-white/70 text-base mb-5 line-clamp-2 font-medium leading-relaxed">
          {agent.description || 'No description available'}
        </p>
        <div className="flex items-center justify-between mb-4">
          <div>
            <span className="text-3xl font-black text-primary-400">
              {pricing.price}
            </span>
            <span className="text-white/50 text-sm ml-2 font-medium">{pricing.label}</span>
          </div>
          <span className={`px-4 py-2 rounded-full text-xs font-bold border ${
            agent.status === 'active' 
              ? 'bg-green-500/20 text-green-400 border-green-500/50' 
              : 'bg-dark-200/50 text-white/60 border-dark-300'
          }`}>
            {agent.status.toUpperCase()}
          </span>
        </div>
        {agent.capabilities && agent.capabilities.length > 0 && (
          <div className="mt-5 flex flex-wrap gap-2">
            {agent.capabilities.slice(0, 3).map((cap, idx) => (
              <span
                key={idx}
                className="px-3 py-1.5 bg-primary-500/20 text-primary-400 text-xs rounded-lg border border-primary-500/30 font-semibold"
              >
                {cap}
              </span>
            ))}
          </div>
        )}
      </div>
    </Link>
  )
}
