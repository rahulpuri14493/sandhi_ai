import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { jobsAPI } from '../lib/api'
import type { JobScheduleWithJob } from '../lib/types'
import { humanReadableSchedule } from '../components/SchedulePicker'

export default function SchedulesPage() {
  const [schedules, setSchedules] = useState<JobScheduleWithJob[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [filter, setFilter] = useState<'all' | 'active' | 'inactive'>('all')
  const navigate = useNavigate()

  useEffect(() => {
    loadSchedules()
  }, [])

  const loadSchedules = async () => {
    setIsLoading(true)
    try {
      const data = await jobsAPI.listAllSchedules()
      setSchedules(data)
    } catch (error) {
      console.error('Failed to load schedules:', error)
    } finally {
      setIsLoading(false)
    }
  }

  const handleToggle = async (s: JobScheduleWithJob) => {
    try {
      const updated = await jobsAPI.updateSchedule(s.job_id, s.id, {
        status: s.status === 'active' ? 'inactive' : 'active',
      })
      setSchedules((prev) =>
        prev.map((x) => (x.id === s.id ? { ...x, ...updated } : x))
      )
    } catch (error) {
      console.error('Failed to toggle schedule:', error)
    }
  }

  const handleDelete = async (s: JobScheduleWithJob) => {
    if (!window.confirm('Delete this schedule?')) return
    try {
      await jobsAPI.deleteSchedule(s.job_id, s.id)
      setSchedules((prev) => prev.filter((x) => x.id !== s.id))
    } catch (error) {
      console.error('Failed to delete schedule:', error)
    }
  }

  const filtered = schedules.filter((s) => {
    if (filter === 'all') return true
    return s.status === filter
  })

  if (isLoading) {
    return (
      <div className="container mx-auto px-4 py-8 min-h-screen">
        <div className="flex items-center justify-center min-h-[400px]">
          <div className="text-center">
            <div className="inline-block animate-spin rounded-full h-12 w-12 border-4 border-primary-500 border-t-transparent mb-4"></div>
            <p className="text-white/60 text-lg font-semibold">Loading schedules...</p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="container mx-auto px-4 py-8 min-h-screen">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-4xl font-black text-white tracking-tight">Schedules</h1>
            <p className="text-white/50 font-medium mt-1">{schedules.length} schedule{schedules.length !== 1 ? 's' : ''} across your jobs</p>
          </div>
        </div>

        {/* Filters */}
        {schedules.length > 0 && (
          <div className="flex gap-2 mb-6">
            {(['all', 'active', 'inactive'] as const).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`px-4 py-2 rounded-lg text-sm font-bold transition-all duration-200 ${
                  filter === f
                    ? 'bg-primary-500/20 text-primary-300 border border-primary-500/50'
                    : 'bg-dark-200/50 text-white/50 border border-dark-300 hover:text-white/80'
                }`}
              >
                {f.charAt(0).toUpperCase() + f.slice(1)}
                {f !== 'all' && (
                  <span className="ml-1.5 text-xs opacity-70">
                    ({schedules.filter((s) => s.status === f).length})
                  </span>
                )}
              </button>
            ))}
          </div>
        )}

        {/* Empty state */}
        {schedules.length === 0 && (
          <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-12 border border-dark-200/50 text-center">
            <svg className="w-16 h-16 text-white/20 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
            <p className="text-white/60 text-lg font-semibold mb-2">No schedules yet</p>
            <p className="text-white/40 text-sm">Schedules are created from the job detail page using "Schedule for Later".</p>
          </div>
        )}

        {/* Schedule cards */}
        {filtered.length > 0 && (
          <div className="space-y-3">
            {filtered.map((s) => (
              <div
                key={s.id}
                className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-lg p-5 border border-dark-200/50 hover:border-primary-500/30 transition-all duration-200"
              >
                <div className="flex items-start gap-4">
                  {/* Icon */}
                  <div className="p-3 bg-primary-500/20 rounded-xl border border-primary-500/30 shrink-0">
                    <svg className="w-5 h-5 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                    </svg>
                  </div>

                  {/* Content */}
                  <div className="flex-1 min-w-0">
                    {/* Job title link */}
                    <button
                      onClick={() => navigate(`/jobs/${s.job_id}`)}
                      className="text-white font-bold hover:text-primary-300 transition-colors text-left"
                    >
                      {s.job_title}
                    </button>

                    {/* Schedule description */}
                    <p className="text-primary-300 text-sm font-semibold mt-1">
                      {humanReadableSchedule({
                        isOneTime: s.is_one_time,
                        scheduledAt: s.scheduled_at ?? undefined,
                        daysOfWeek: s.days_of_week ?? undefined,
                        time: s.time ?? undefined,
                        timezone: s.timezone,
                      })}
                    </p>

                    {/* Badges */}
                    <div className="flex items-center gap-2 mt-2">
                      <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${
                        s.is_one_time ? 'bg-amber-500/20 text-amber-400' : 'bg-blue-500/20 text-blue-400'
                      }`}>
                        {s.is_one_time ? 'One-time' : 'Recurring'}
                      </span>
                      <span className="text-xs text-white/30">{s.timezone}</span>
                    </div>

                    {/* Timestamps */}
                    <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-xs text-white/40">
                      {s.next_run_time && (
                        <span>Next: {new Date(s.next_run_time).toLocaleString()}</span>
                      )}
                      {s.last_run_time && (
                        <span>Last: {new Date(s.last_run_time).toLocaleString()}</span>
                      )}
                      <span>Created: {new Date(s.created_at).toLocaleDateString()}</span>
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      onClick={() => handleToggle(s)}
                      className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all duration-200 ${
                        s.status === 'active'
                          ? 'bg-green-500/20 text-green-400 border border-green-500/50 hover:bg-green-500/30'
                          : 'bg-dark-200/50 text-white/40 border border-dark-300 hover:text-white/70'
                      }`}
                    >
                      {s.status === 'active' ? 'Active' : 'Inactive'}
                    </button>
                    <button
                      onClick={() => handleDelete(s)}
                      className="p-2 text-red-400/60 hover:text-red-400 hover:bg-red-500/20 rounded-lg transition-all duration-200"
                      title="Delete schedule"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* No results for filter */}
        {schedules.length > 0 && filtered.length === 0 && (
          <div className="text-center py-12">
            <p className="text-white/40 font-medium">No {filter} schedules found.</p>
          </div>
        )}
      </div>
    </div>
  )
}
