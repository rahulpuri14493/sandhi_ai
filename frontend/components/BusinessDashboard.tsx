'use client'

import { useEffect, useState } from 'react'
import { dashboardsAPI, jobsAPI } from '@/lib/api'
import type { Job } from '@/lib/types'
import Link from 'next/link'

export function BusinessDashboard() {
  const [spending, setSpending] = useState({ total_spent: 0, job_count: 0 })
  const [jobs, setJobs] = useState<Job[]>([])
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    setIsLoading(true)
    try {
      const [spendingData, jobsData] = await Promise.all([
        dashboardsAPI.getBusinessSpending(),
        dashboardsAPI.getBusinessJobs(),
      ])
      setSpending(spendingData)
      setJobs(jobsData)
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
      <h1 className="text-3xl font-bold mb-8">Business Dashboard</h1>

      <div className="grid md:grid-cols-2 gap-6 mb-8">
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-xl font-semibold mb-2">Total Spent</h2>
          <p className="text-3xl font-bold text-primary-600">
            ${spending.total_spent.toFixed(2)}
          </p>
        </div>
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-xl font-semibold mb-2">Total Jobs</h2>
          <p className="text-3xl font-bold text-primary-600">{spending.job_count}</p>
        </div>
      </div>

      <div className="bg-white rounded-lg shadow p-6">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-2xl font-bold">Recent Jobs</h2>
          <Link
            href="/jobs/new"
            className="bg-primary-600 text-white px-4 py-2 rounded-lg hover:bg-primary-700"
          >
            New Job
          </Link>
        </div>
        {jobs.length === 0 ? (
          <p className="text-gray-500">No jobs yet</p>
        ) : (
          <div className="space-y-4">
            {jobs.map((job) => (
              <Link
                key={job.id}
                href={`/jobs/${job.id}`}
                className="block p-4 border border-gray-200 rounded-lg hover:bg-gray-50"
              >
                <div className="flex justify-between items-center">
                  <div>
                    <h3 className="font-semibold">{job.title}</h3>
                    <p className="text-sm text-gray-600">
                      Status: {job.status.replace('_', ' ')}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="font-semibold">${job.total_cost.toFixed(2)}</p>
                    <p className="text-sm text-gray-600">
                      {new Date(job.created_at).toLocaleDateString()}
                    </p>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
