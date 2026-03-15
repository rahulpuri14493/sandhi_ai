import { useState, useEffect, useCallback } from 'react'
import { Clock } from 'lucide-react'
import { Input } from '@/components/ui/input'
import TimezonePicker from '@/components/TimezonePicker'
import DatePickerTime from '@/components/DatePickerTime'

const DAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

interface ScheduleData {
  isOneTime: boolean
  timezone: string
  scheduledAt: string | null
  daysOfWeek: number[]
  time: string
  status: 'active' | 'inactive'
}

interface Props {
  isOneTime: boolean
  timezone: string
  scheduledAt?: string | null
  daysOfWeek?: number[]
  time?: string
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
  isOneTime: boolean
  scheduledAt?: string | null
  daysOfWeek?: number[]
  time?: string
  timezone?: string
}): string {
  const tz = data.timezone || 'UTC'
  const tzShort = formatTimezoneLabel(tz).match(/\((.+)\)/)?.[1] || tz

  if (data.isOneTime && data.scheduledAt) {
    const d = new Date(data.scheduledAt)
    return `Run once on ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })} at ${d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })} ${tzShort}`
  }

  if (!data.isOneTime && data.daysOfWeek?.length && data.time) {
    const days = data.daysOfWeek.sort().map(d => DAY_LABELS[d]).join(', ')
    const [h, m] = data.time.split(':')
    const hour = parseInt(h, 10)
    const ampm = hour >= 12 ? 'PM' : 'AM'
    const h12 = hour === 0 ? 12 : hour > 12 ? hour - 12 : hour
    return `Every ${days} at ${h12}:${m} ${ampm} ${tzShort}`
  }

  return 'No schedule configured'
}

export function validateSchedule(data: ScheduleData): string | null {
  if (data.isOneTime) {
    if (!data.scheduledAt) return 'Please select a date and time'
    if (new Date(data.scheduledAt) <= new Date()) return 'Scheduled time must be in the future'
  } else {
    if (!data.daysOfWeek.length) return 'Please select at least one day'
    if (!data.time) return 'Please select a time'
  }
  return null
}

export default function SchedulePicker({
  isOneTime,
  timezone,
  scheduledAt,
  daysOfWeek,
  time,
  status,
  onChange,
  readOnly = false,
}: Props) {
  // Round minutes up to the next multiple of 5 (e.g. :41 → :45, :15 → :20, :00 → :05)
  const nextFiveMinTime = (): { h: string; m: string; date: Date } => {
    const now = new Date()
    const min = now.getMinutes()
    const rounded = Math.ceil((min + 1) / 5) * 5  // 41→45, 15→20, 0→5
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
      date: d,
    }
  }

  const [tab, setTab] = useState<'once' | 'recurring'>(isOneTime ? 'once' : 'recurring')
  const [tz, setTz] = useState(timezone || getLocalTimezone())
  const [dateObj, setDateObj] = useState<Date | undefined>(() => {
    if (scheduledAt) return new Date(scheduledAt)
    return new Date() // default to today
  })
  const [timeOnce, setTimeOnce] = useState(() => {
    if (scheduledAt) {
      const d = new Date(scheduledAt)
      return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
    }
    const { h, m } = nextFiveMinTime()
    return `${h}:${m}`
  })
  const [selectedDays, setSelectedDays] = useState<number[]>(daysOfWeek || [])
  const [recurringTime, setRecurringTime] = useState(() => {
    if (time) return time
    const { h, m } = nextFiveMinTime()
    return `${h}:${m}`
  })
  const active = status === 'active'

  const emitChange = useCallback(() => {
    const isOnce = tab === 'once'
    const dateStr = isOnce && dateObj
      ? `${dateObj.getFullYear()}-${String(dateObj.getMonth() + 1).padStart(2, '0')}-${String(dateObj.getDate()).padStart(2, '0')}`
      : null
    const data: ScheduleData = {
      isOneTime: isOnce,
      timezone: tz,
      scheduledAt: dateStr && timeOnce ? `${dateStr}T${timeOnce}:00` : null,
      daysOfWeek: isOnce ? [] : selectedDays,
      time: isOnce ? '' : recurringTime,
      status: active ? 'active' : 'inactive',
    }
    onChange(data)
  }, [tab, tz, dateObj, timeOnce, selectedDays, recurringTime, active, onChange])

  useEffect(() => {
    emitChange()
  }, [emitChange])

  const toggleDay = (day: number) => {
    setSelectedDays(prev =>
      prev.includes(day) ? prev.filter(d => d !== day) : [...prev, day].sort()
    )
  }

  const summary = humanReadableSchedule({
    isOneTime: tab === 'once',
    scheduledAt: tab === 'once' && dateObj && timeOnce
      ? `${dateObj.getFullYear()}-${String(dateObj.getMonth() + 1).padStart(2, '0')}-${String(dateObj.getDate()).padStart(2, '0')}T${timeOnce}:00`
      : null,
    daysOfWeek: tab === 'once' ? [] : selectedDays,
    time: tab === 'once' ? '' : recurringTime,
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
        <span className={`inline-block mt-1 text-xs px-2 py-0.5 rounded-full ${tab === 'once' ? 'bg-amber-500/20 text-amber-400' : 'bg-primary-600/20 text-primary-300'}`}>
          {tab === 'once' ? 'One-time' : 'Recurring'}
        </span>
      </div>
    )
  }

  return (
    <div className="max-w-md bg-dark-100 rounded-xl border border-dark-300 p-5 space-y-4">
      {/* Header */}
      <h3 className="text-sm font-semibold text-white">Schedule</h3>

      {/* Tab buttons */}
      <div className="flex gap-1 bg-dark-50 rounded-lg p-1 border border-dark-300">
        <button
          type="button"
          onClick={() => setTab('once')}
          className={`flex-1 py-1.5 px-3 text-sm rounded-md transition-colors ${
            tab === 'once'
              ? 'bg-primary-600 text-white font-medium shadow-lg shadow-primary-600/20'
              : 'text-dark-500 hover:text-white'
          }`}
        >
          Run Once
        </button>
        <button
          type="button"
          onClick={() => setTab('recurring')}
          className={`flex-1 py-1.5 px-3 text-sm rounded-md transition-colors ${
            tab === 'recurring'
              ? 'bg-primary-600 text-white font-medium shadow-lg shadow-primary-600/20'
              : 'text-dark-500 hover:text-white'
          }`}
        >
          Recurring
        </button>
      </div>

      {/* Timezone */}
      <TimezonePicker value={tz} onChange={setTz} />

      {/* Run Once tab */}
      {tab === 'once' && (
        <DatePickerTime
          date={dateObj}
          time={timeOnce}
          onDateChange={setDateObj}
          onTimeChange={setTimeOnce}
          minDate={new Date()}
        />
      )}

      {/* Recurring tab */}
      {tab === 'recurring' && (
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-dark-500 mb-1.5">Days</label>
            <div className="flex gap-1">
              {DAY_LABELS.map((label, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => toggleDay(i)}
                  className={`flex-1 py-1.5 text-xs rounded-md border transition-colors ${
                    selectedDays.includes(i)
                      ? 'bg-primary-600 text-white border-primary-600'
                      : 'bg-dark-50 text-dark-500 border-dark-300 hover:border-primary-400 hover:text-white'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-dark-500 mb-1.5">Time</label>
            <div className="relative">
              <Clock className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-dark-400 pointer-events-none" />
              <Input
                type="time"
                value={recurringTime}
                onChange={e => setRecurringTime(e.target.value)}
                className="pl-9"
              />
            </div>
          </div>
        </div>
      )}

      {/* Summary */}
      {summary !== 'No schedule configured' && (
        <div className="bg-dark-50 rounded-lg p-3 text-sm text-dark-600 border border-dark-300">
          {summary}
        </div>
      )}
    </div>
  )
}
