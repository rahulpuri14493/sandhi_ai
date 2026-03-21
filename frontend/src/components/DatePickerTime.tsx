import { useState, useMemo } from 'react'
import { format, isToday } from 'date-fns'
import { CalendarIcon, Clock, ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Calendar } from '@/components/ui/calendar'
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover'
import { Input } from '@/components/ui/input'

interface Props {
  date: Date | undefined
  time: string
  onDateChange: (date: Date | undefined) => void
  onTimeChange: (time: string) => void
  minDate?: Date
  disabled?: boolean
}

function generateTimeSlots(): string[] {
  const slots: string[] = []
  for (let h = 0; h < 24; h++) {
    for (let m = 0; m < 60; m += 15) {
      slots.push(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`)
    }
  }
  return slots
}

function formatTime12h(t: string): string {
  const [hStr, mStr] = t.split(':')
  const h = parseInt(hStr, 10)
  const ampm = h >= 12 ? 'PM' : 'AM'
  const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h
  return `${h12}:${mStr} ${ampm}`
}

function isPastDateTime(date: Date | undefined, time: string): boolean {
  if (!date || !time) return false
  const [h, m] = time.split(':').map(Number)
  const selected = new Date(date)
  selected.setHours(h, m, 0, 0)
  return selected <= new Date()
}

export default function DatePickerTime({
  date,
  time,
  onDateChange,
  onTimeChange,
  minDate,
  disabled,
}: Props) {
  const [calOpen, setCalOpen] = useState(false)
  const [timeOpen, setTimeOpen] = useState(false)

  const currentYear = new Date().getFullYear()
  const allSlots = useMemo(() => generateTimeSlots(), [])

  const isDateToday = date ? isToday(date) : false
  const pastError = isPastDateTime(date, time)

  // Filter out past time slots when selected date is today
  const availableSlots = useMemo(() => {
    if (!isDateToday) return allSlots
    const now = new Date()
    const nowMinutes = now.getHours() * 60 + now.getMinutes()
    return allSlots.filter(slot => {
      const [h, m] = slot.split(':').map(Number)
      return h * 60 + m > nowMinutes
    })
  }, [allSlots, isDateToday])

  return (
    <div className="space-y-3">
      <div className="flex gap-3">
        {/* Date picker */}
        <div className="flex-1 space-y-1.5">
          <label className="block text-xs font-medium text-dark-500">Date</label>
          <Popover open={calOpen} onOpenChange={setCalOpen}>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                disabled={disabled}
                className={cn(
                  "w-full justify-start text-left font-normal h-10",
                  !date && "text-dark-400"
                )}
              >
                <CalendarIcon className="mr-2 h-4 w-4 shrink-0" />
                {date ? format(date, "PPP") : "Pick a date"}
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-auto p-0" align="start">
              <Calendar
                mode="single"
                captionLayout="dropdown"
                fromYear={currentYear}
                toYear={currentYear + 5}
                defaultMonth={date || new Date()}
                selected={date}
                onSelect={(d) => {
                  onDateChange(d)
                  setCalOpen(false)
                }}
                disabled={minDate ? { before: minDate } : undefined}
                initialFocus
              />
            </PopoverContent>
          </Popover>
        </div>

        {/* Time picker with dropdown */}
        <div className="flex-1 space-y-1.5">
          <label className="block text-xs font-medium text-dark-500">Time</label>
          <Popover open={timeOpen} onOpenChange={setTimeOpen}>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                disabled={disabled}
                className={cn(
                  "w-full justify-start text-left font-normal h-10",
                  !time && "text-dark-400",
                  pastError && "border-red-500/50"
                )}
              >
                <Clock className="mr-2 h-4 w-4 shrink-0" />
                <span className="flex-1">{time ? formatTime12h(time) : "Pick a time"}</span>
                <ChevronDown className="h-3.5 w-3.5 opacity-50" />
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-48 p-0" align="start">
              {/* Manual input at top */}
              <div className="p-2 border-b border-dark-300">
                <Input
                  type="time"
                  value={time}
                  onChange={e => {
                    onTimeChange(e.target.value)
                    setTimeOpen(false)
                  }}
                  className="h-8 text-xs"
                />
              </div>
              {/* Preset time slots */}
              <div className="max-h-48 overflow-y-auto p-1">
                {availableSlots.map(slot => (
                  <button
                    key={slot}
                    type="button"
                    onClick={() => {
                      onTimeChange(slot)
                      setTimeOpen(false)
                    }}
                    className={cn(
                      "w-full text-left text-xs px-2.5 py-1.5 rounded-md transition-colors",
                      slot === time
                        ? "bg-primary-600/20 text-primary-300"
                        : "text-white/80 hover:bg-dark-200 hover:text-white"
                    )}
                  >
                    {formatTime12h(slot)}
                  </button>
                ))}
              </div>
            </PopoverContent>
          </Popover>
        </div>
      </div>

      {/* Validation message */}
      {pastError && (
        <p className="text-xs text-red-400">Selected date and time is in the past. Please choose a future time.</p>
      )}
    </div>
  )
}
