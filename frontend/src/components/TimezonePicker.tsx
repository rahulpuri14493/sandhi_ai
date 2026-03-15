import { useState, useMemo } from 'react'
import { ChevronDown, Globe, Search } from 'lucide-react'
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover'
import { Input } from '@/components/ui/input'

interface Props {
  value: string
  onChange: (tz: string) => void
  disabled?: boolean
}

function formatTzLabel(tz: string): string {
  try {
    const now = new Date()
    const formatter = new Intl.DateTimeFormat('en-US', { timeZone: tz, timeZoneName: 'short' })
    const parts = formatter.formatToParts(now)
    const abbr = parts.find(p => p.type === 'timeZoneName')?.value || tz
    const city = tz.split('/').pop()?.replace(/_/g, ' ') || tz
    return `${city} (${abbr})`
  } catch {
    return tz
  }
}

function getAllTimezones(): string[] {
  try {
    return (Intl as unknown as { supportedValuesOf: (key: string) => string[] }).supportedValuesOf('timeZone')
  } catch {
    // Fallback for older browsers
    return [
      'UTC',
      'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
      'America/Toronto', 'America/Vancouver', 'America/Sao_Paulo', 'America/Mexico_City',
      'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Europe/Moscow', 'Europe/Istanbul',
      'Asia/Kolkata', 'Asia/Dubai', 'Asia/Shanghai', 'Asia/Tokyo', 'Asia/Singapore',
      'Asia/Seoul', 'Asia/Hong_Kong', 'Asia/Karachi', 'Asia/Dhaka', 'Asia/Bangkok',
      'Australia/Sydney', 'Australia/Melbourne', 'Pacific/Auckland',
      'Africa/Cairo', 'Africa/Lagos', 'Africa/Johannesburg',
    ]
  }
}

export default function TimezonePicker({ value, onChange, disabled }: Props) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')

  const allTimezones = useMemo(() => getAllTimezones(), [])

  const filtered = useMemo(() => {
    if (!search.trim()) return allTimezones
    const q = search.toLowerCase()
    return allTimezones.filter(tz => {
      const label = formatTzLabel(tz).toLowerCase()
      return tz.toLowerCase().includes(q) || label.includes(q)
    })
  }, [search, allTimezones])

  const grouped = useMemo(() => {
    const groups: Record<string, string[]> = {}
    for (const tz of filtered) {
      const region = tz.includes('/') ? tz.split('/')[0] : 'Other'
      if (!groups[region]) groups[region] = []
      groups[region].push(tz)
    }
    return groups
  }, [filtered])

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild disabled={disabled}>
        <button
          type="button"
          className="inline-flex items-center gap-1.5 text-xs text-dark-500 hover:text-white transition-colors rounded-md px-2 py-1 hover:bg-dark-200"
        >
          <Globe className="w-3.5 h-3.5" />
          <span>{formatTzLabel(value)}</span>
          <ChevronDown className="w-3 h-3 opacity-60" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 p-0">
        <div className="p-2 border-b border-dark-300">
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-dark-400" />
            <Input
              placeholder="Search timezone..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="pl-8 h-9 text-xs"
              autoFocus
            />
          </div>
        </div>
        <div className="max-h-60 overflow-y-auto p-1">
          {Object.keys(grouped).length === 0 && (
            <p className="text-xs text-dark-400 text-center py-4">No timezones found</p>
          )}
          {Object.entries(grouped).map(([region, tzs]) => (
            <div key={region}>
              <p className="text-[10px] font-semibold text-dark-400 uppercase tracking-wider px-2 pt-2 pb-1">
                {region}
              </p>
              {tzs.map(tz => (
                <button
                  key={tz}
                  type="button"
                  onClick={() => {
                    onChange(tz)
                    setSearch('')
                    setOpen(false)
                  }}
                  className={`w-full text-left text-xs px-2 py-1.5 rounded-md transition-colors ${
                    tz === value
                      ? 'bg-primary-600/20 text-primary-300'
                      : 'text-white/80 hover:bg-dark-200 hover:text-white'
                  }`}
                >
                  {formatTzLabel(tz)}
                </button>
              ))}
            </div>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  )
}
