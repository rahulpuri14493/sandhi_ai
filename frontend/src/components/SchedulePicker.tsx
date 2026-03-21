import { useState, useEffect, useCallback } from 'react'
import TimezonePicker from '@/components/TimezonePicker'
import DatePickerTime from '@/components/DatePickerTime'

export interface ScheduleData {
  timezone: string
  scheduledAt: string | null
  status: 'active' | 'inactive'
}

interface Props {
  timezone: string
  scheduledAt?: string | null
  status: 'active' | 'inactive'
  onChange: (data: ScheduleData) => void
  readOnly?: boolean
}

function getLocalTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone
  } catch {
    return 'UTC'
  }
}

function formatTimezoneLabel(tz: string): string {
  try {
    const now = new Date()
    const formatter = new Intl.DateTimeFormat('en-US', { timeZone: tz, timeZoneName: 'short' })
    const parts = formatter.formatToParts(now)
    const abbr = parts.find(p => p.type === 'timeZoneName')?.value || tz
    return `${tz.replace(/_/g, ' ')} (${abbr})`
  } catch {
    return tz
  }
}

export function humanReadableSchedule(data: {
  scheduledAt?: string | null
  timezone?: string
}): string {
  const tz = data.timezone || 'UTC'
  const tzShort = formatTimezoneLabel(tz).match(/\((.+)\)/)?.[1] || tz

  if (data.scheduledAt) {
    const d = new Date(data.scheduledAt)
    return `Run once on ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })} at ${d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })} ${tzShort}`
  }

  return 'No schedule configured'
}

export function validateSchedule(data: ScheduleData): string | null {
  if (!data.scheduledAt) return 'Please select a date and time'
  if (new Date(data.scheduledAt) <= new Date()) return 'Scheduled time must be in the future'
  return null
}

export default function SchedulePicker({
  timezone,
  scheduledAt,
  status,
  onChange,
  readOnly = false,
}: Props) {
  // Round minutes up to the next multiple of 5 (e.g. :41 -> :45, :15 -> :20, :00 -> :05)
  const nextFiveMinTime = (): { h: string; m: string } => {
    const now = new Date()
    const min = now.getMinutes()
    const rounded = Math.ceil((min + 1) / 5) * 5
    const d = new Date(now)
    d.setSeconds(0, 0)
    if (rounded >= 60) {
      d.setHours(d.getHours() + 1, rounded - 60)
    } else {
      d.setMinutes(rounded)
    }
    return {
      h: String(d.getHours()).padStart(2, '0'),
      m: String(d.getMinutes()).padStart(2, '0'),
    }
  }

  const [tz, setTz] = useState(timezone || getLocalTimezone())
  const [dateObj, setDateObj] = useState<Date | undefined>(() => {
    if (scheduledAt) return new Date(scheduledAt)
    return new Date()
  })
  const [timeOnce, setTimeOnce] = useState(() => {
    if (scheduledAt) {
      const d = new Date(scheduledAt)
      return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
    }
    const { h, m } = nextFiveMinTime()
    return `${h}:${m}`
  })
  const active = status === 'active'

  const emitChange = useCallback(() => {
    const dateStr = dateObj
      ? `${dateObj.getFullYear()}-${String(dateObj.getMonth() + 1).padStart(2, '0')}-${String(dateObj.getDate()).padStart(2, '0')}`
      : null
    const data: ScheduleData = {
      timezone: tz,
      scheduledAt: dateStr && timeOnce ? `${dateStr}T${timeOnce}:00` : null,
      status: active ? 'active' : 'inactive',
    }
    onChange(data)
  }, [tz, dateObj, timeOnce, active, onChange])

  useEffect(() => {
    emitChange()
  }, [emitChange])

  const summary = humanReadableSchedule({
    scheduledAt: dateObj && timeOnce
      ? `${dateObj.getFullYear()}-${String(dateObj.getMonth() + 1).padStart(2, '0')}-${String(dateObj.getDate()).padStart(2, '0')}T${timeOnce}:00`
      : null,
    timezone: tz,
  })

  if (readOnly) {
    return (
      <div className="max-w-md bg-dark-100 rounded-xl p-4 border border-dark-300">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-medium text-white">Schedule</h3>
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${active ? 'bg-green-500/20 text-green-400' : 'bg-dark-200 text-dark-400'}`}>
            {active ? 'Active' : 'Inactive'}
          </span>
        </div>
        <p className="text-sm text-white/80">{summary}</p>
      </div>
    )
  }

  return (
    <div className="max-w-md bg-dark-100 rounded-xl border border-dark-300 p-5 space-y-4">
      <h3 className="text-sm font-semibold text-white">Schedule</h3>

      <TimezonePicker value={tz} onChange={setTz} />

      <DatePickerTime
        date={dateObj}
        time={timeOnce}
        onDateChange={setDateObj}
        onTimeChange={setTimeOnce}
        minDate={new Date()}
      />

      {summary !== 'No schedule configured' && (
        <div className="bg-dark-50 rounded-lg p-3 text-sm text-dark-600 border border-dark-300">
          {summary}
        </div>
      )}
    </div>
  )
}
