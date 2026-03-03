'use client'

import { useEffect, useState } from 'react'
import { dashboardsAPI } from '@/lib/api'
import type { Agent, Earnings } from '@/lib/types'
import { EarningsChart } from './EarningsChart'
import Link from 'next/link'

export function DeveloperDashboard() {
  const [earnings, setEarnings] = useState({
    total_earnings: 0,
    pending_earnings: 0,
    recent_earnings: [] as Earnings[],
  })
  const [agents, setAgents] = useState<Agent[]>([])
  const [stats, setStats] = useState({
    agent_count: 0,
    total_tasks: 0,
    total_communications: 0,
  })
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    setIsLoading(true)
    try {
      const [earningsData, agentsData, statsData] = await Promise.all([
        dashboardsAPI.getDeveloperEarnings(),
        dashboardsAPI.getDeveloperAgents(),
        dashboardsAPI.getDeveloperStats(),
      ])
      setEarnings(earningsData)
      setAgents(agentsData)
      setStats(statsData)
    } catch (error) {
      console.error('Failed to load dashboard data:', error)
    } finally {
      setIsLoading(false)
    }
  }

  if (isLoading) {
    return <div>Loading dashboard...</div>
  }

  return (
    <div>
      <h1 className="text-3xl font-bold mb-8">Developer Dashboard</h1>

      <div className="grid md:grid-cols-3 gap-6 mb-8">
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-xl font-semibold mb-2">Total Earnings</h2>
          <p className="text-3xl font-bold text-green-600">
            ${earnings.total_earnings.toFixed(2)}
          </p>
        </div>
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-xl font-semibold mb-2">Pending Earnings</h2>
          <p className="text-3xl font-bold text-yellow-600">
            ${earnings.pending_earnings.toFixed(2)}
          </p>
        </div>
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-xl font-semibold mb-2">Agents Published</h2>
          <p className="text-3xl font-bold text-primary-600">{stats.agent_count}</p>
        </div>
      </div>

      <div className="grid md:grid-cols-2 gap-6 mb-8">
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-xl font-semibold mb-4">Earnings Over Time</h2>
          <EarningsChart earnings={earnings.recent_earnings} />
        </div>
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-xl font-semibold mb-4">Statistics</h2>
          <div className="space-y-3">
            <div className="flex justify-between">
              <span>Total Tasks:</span>
              <span className="font-semibold">{stats.total_tasks}</span>
            </div>
            <div className="flex justify-between">
              <span>Total Communications:</span>
              <span className="font-semibold">{stats.total_communications}</span>
            </div>
          </div>
        </div>
      </div>

      <div className="bg-white rounded-lg shadow p-6">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-2xl font-bold">My Agents</h2>
          <Link
            href="/agents/new"
            className="bg-primary-600 text-white px-4 py-2 rounded-lg hover:bg-primary-700"
          >
            Publish New Agent
          </Link>
        </div>
        {agents.length === 0 ? (
          <p className="text-gray-500">No agents published yet</p>
        ) : (
          <div className="space-y-4">
            {agents.map((agent) => (
              <div
                key={agent.id}
                className="p-4 border border-gray-200 rounded-lg"
              >
                <div className="flex justify-between items-center">
                  <div>
                    <h3 className="font-semibold">{agent.name}</h3>
                    <p className="text-sm text-gray-600">{agent.description}</p>
                  </div>
                  <div className="text-right">
                    <p className="font-semibold">${agent.price_per_task.toFixed(2)}</p>
                    <span className={`px-2 py-1 rounded text-xs ${
                      agent.status === 'active'
                        ? 'bg-green-100 text-green-800'
                        : 'bg-gray-100 text-gray-800'
                    }`}>
                      {agent.status}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
