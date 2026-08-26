import React from 'react'
import { Activity, Zap } from 'lucide-react'

interface LatencyBadgeProps {
  latency?: number | null
  status?: string
}

export const LatencyBadge: React.FC<LatencyBadgeProps> = ({ latency, status }) => {
  const isOnline = !status || status === 'connected' || status === 'active'
  if (!isOnline || latency === undefined || latency === null || latency <= 0) {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-mono text-gray-400 dark:text-gray-500 select-none">
        <span className="w-1.5 h-1.5 rounded-full bg-gray-300 dark:bg-gray-600"></span>
        ---
      </span>
    )
  }

  // Latency styling thresholds
  let badgeClasses = ''
  let dotClasses = ''
  let pingDot = false

  if (latency < 80) {
    badgeClasses = 'bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300 border-emerald-200/80 dark:border-emerald-800/50 shadow-sm'
    dotClasses = 'bg-emerald-500'
    pingDot = true
  } else if (latency <= 180) {
    badgeClasses = 'bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300 border-amber-200/80 dark:border-amber-800/50'
    dotClasses = 'bg-amber-500'
    pingDot = false
  } else {
    badgeClasses = 'bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300 border-rose-200/80 dark:border-rose-800/50'
    dotClasses = 'bg-rose-500'
    pingDot = false
  }

  return (
    <div
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-mono font-semibold border transition-all duration-200 hover:scale-105 select-none ${badgeClasses}`}
      title={`Round-trip response time: ${latency} ms`}
    >
      <span className="relative flex h-2 w-2">
        {pingDot && (
          <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${dotClasses}`}></span>
        )}
        <span className={`relative inline-flex rounded-full h-2 w-2 ${dotClasses}`}></span>
      </span>
      <span>{latency} ms</span>
    </div>
  )
}
