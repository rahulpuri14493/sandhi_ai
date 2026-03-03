import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import type { Earnings } from '../lib/types'
import { format, parseISO } from 'date-fns'

interface EarningsChartProps {
  earnings: Earnings[]
}

export function EarningsChart({ earnings }: EarningsChartProps) {
  // Group earnings by date
  const earningsByDate = earnings.reduce((acc, earning) => {
    const date = format(parseISO(earning.created_at), 'yyyy-MM-dd')
    if (!acc[date]) {
      acc[date] = 0
    }
    acc[date] += earning.amount
    return acc
  }, {} as Record<string, number>)

  const chartData = Object.entries(earningsByDate)
    .map(([date, amount]) => ({
      date: format(parseISO(date), 'MMM dd'),
      amount: parseFloat(amount.toFixed(2)),
    }))
    .sort((a, b) => a.date.localeCompare(b.date))

  if (chartData.length === 0) {
    return <p className="text-white/60 text-center py-8 font-medium">No earnings data yet</p>
  }

  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={chartData}>
        <CartesianGrid strokeDasharray="3 3" stroke="#52525b" />
        <XAxis dataKey="date" stroke="#a1a1aa" />
        <YAxis stroke="#a1a1aa" />
        <Tooltip 
          contentStyle={{ 
            backgroundColor: '#27272a', 
            border: '1px solid #3f3f46',
            borderRadius: '8px',
            color: '#ffffff'
          }}
        />
        <Line type="monotone" dataKey="amount" stroke="#9333ea" strokeWidth={3} />
      </LineChart>
    </ResponsiveContainer>
  )
}
