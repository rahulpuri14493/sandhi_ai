'use client'

import { useEffect, useState } from 'react'
import { jobsAPI } from '@/lib/api'
import type { Job } from '@/lib/types'

interface JobStatusTrackerProps {
  jobId: number
  job: Job
}

export function JobStatusTracker({ jobId, job: initialJob }: JobStatusTrackerProps) {
  const [job, setJob] = useState(initialJob)
  const [isPolling, setIsPolling] = useState(false)

  useEffect(() => {
    if (job.status === 'in_progress') {
      setIsPolling(true)
      const interval = setInterval(async () => {
        try {
          const updatedJob = await jobsAPI.getStatus(jobId)
          setJob(updatedJob)
          if (updatedJob.status === 'completed' || updatedJob.status === 'failed') {
            setIsPolling(false)
            clearInterval(interval)
          }
        } catch (error) {
          console.error('Failed to poll job status:', error)
        }
      }, 2000)

      return () => clearInterval(interval)
    }
  }, [jobId, job.status])

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed':
        return 'bg-green-100 text-green-800'
      case 'failed':
        return 'bg-red-100 text-red-800'
      case 'in_progress':
        return 'bg-blue-100 text-blue-800'
      default:
        return 'bg-gray-100 text-gray-800'
    }
  }

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <h2 className="text-2xl font-bold mb-4">Job Status</h2>
      <div className="mb-4">
        <span className={`px-3 py-1 rounded-full text-sm ${getStatusColor(job.status)}`}>
          {job.status.replace('_', ' ').toUpperCase()}
        </span>
      </div>
      {job.workflow_steps && job.workflow_steps.length > 0 && (
        <div className="mt-6">
          <h3 className="font-semibold mb-3">Workflow Steps</h3>
          <div className="space-y-2">
            {job.workflow_steps.map((step) => (
              <div
                key={step.id}
                className="flex items-center justify-between p-3 bg-gray-50 rounded"
              >
                <div>
                  <span className="font-medium">Step {step.step_order}</span>
                  <span className="text-sm text-gray-600 ml-2">({step.status})</span>
                </div>
                {step.completed_at && (
                  <span className="text-sm text-gray-600">
                    Completed at {new Date(step.completed_at).toLocaleString()}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
      {isPolling && (
        <div className="mt-4 text-sm text-gray-600">
          Polling for updates...
        </div>
      )}
    </div>
  )
}
