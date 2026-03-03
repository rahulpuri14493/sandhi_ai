'use client'

import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import type { Earnings } from '@/lib/types'
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
    return <p className="text-gray-500 text-center py-8">No earnings data yet</p>
  }

  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={chartData}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey="date" />
        <YAxis />
        <Tooltip />
        <Line type="monotone" dataKey="amount" stroke="#0ea5e9" strokeWidth={2} />
      </LineChart>
    </ResponsiveContainer>
  )
}
