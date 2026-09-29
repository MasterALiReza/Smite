import React from 'react'

interface LatencyBadgeProps {
  latency?: number | null
  status?: string
}

export const LatencyBadge: React.FC<LatencyBadgeProps> = ({ latency, status }) => {
  const isOnline = !status || status === 'connected' || status === 'active'
  if (!isOnline || latency === undefined || latency === null || latency <= 0) {
    return (
      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-mono text-slate-400 dark:text-slate-500 bg-slate-100 dark:bg-white/[0.04] border border-slate-200/60 dark:border-white/[0.06] select-none">
        <span className="w-1.5 h-1.5 rounded-full bg-slate-300 dark:bg-slate-600"></span>
        <span className="tabular-nums">---</span>
      </span>
    )
  }

  let badgeClasses = ''
  let dotClasses = ''
  let pingDot = false

  if (latency < 80) {
    badgeClasses = 'bg-emerald-500/10 dark:bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30 dark:border-emerald-500/30 shadow-glow-emerald'
    dotClasses = 'bg-emerald-500'
    pingDot = true
  } else if (latency <= 180) {
    badgeClasses = 'bg-amber-500/10 dark:bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30 dark:border-amber-500/30'
    dotClasses = 'bg-amber-500'
    pingDot = false
  } else {
    badgeClasses = 'bg-rose-500/10 dark:bg-rose-500/15 text-rose-600 dark:text-rose-400 border-rose-500/30 dark:border-rose-500/30 shadow-glow-rose'
    dotClasses = 'bg-rose-500'
    pingDot = false
  }

  return (
    <div
      className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-mono font-bold border transition-colors select-none ${badgeClasses}`}
      title={`Round-trip response time: ${latency} ms`}
    >
      <span className="relative flex h-2 w-2">
        {pingDot && (
          <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-60 ${dotClasses}`}></span>
        )}
        <span className={`relative inline-flex rounded-full h-2 w-2 ${dotClasses}`}></span>
      </span>
      <span className="tabular-nums">{latency} ms</span>
    </div>
  )
}
