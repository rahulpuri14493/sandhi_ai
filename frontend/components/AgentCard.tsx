import Link from 'next/link'
import type { Agent } from '@/lib/types'

interface AgentCardProps {
  agent: Agent
}

export function AgentCard({ agent }: AgentCardProps) {
  return (
    <Link href={`/marketplace/agent/${agent.id}`}>
      <div className="bg-white rounded-lg shadow p-6 hover:shadow-lg transition">
        <h3 className="text-xl font-semibold mb-2">{agent.name}</h3>
        <p className="text-gray-600 text-sm mb-4 line-clamp-2">
          {agent.description || 'No description available'}
        </p>
        <div className="flex items-center justify-between">
          <div>
            <span className="text-2xl font-bold text-primary-600">
              ${agent.price_per_task.toFixed(2)}
            </span>
            <span className="text-gray-500 text-sm ml-1">per task</span>
          </div>
          <span className={`px-3 py-1 rounded-full text-xs ${
            agent.status === 'active' 
              ? 'bg-green-100 text-green-800' 
              : 'bg-gray-100 text-gray-800'
          }`}>
            {agent.status}
          </span>
        </div>
        {agent.capabilities && agent.capabilities.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2">
            {agent.capabilities.slice(0, 3).map((cap, idx) => (
              <span
                key={idx}
                className="px-2 py-1 bg-primary-100 text-primary-700 text-xs rounded"
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
