import { useEffect, useState, useRef } from 'react'
import { Copy, Trash2, ArrowDownCircle, CheckCircle2, Terminal } from 'lucide-react'
import api from '../api/client'
import { useLanguage } from '../contexts/LanguageContext'
import { useToast } from '../contexts/ToastContext'
import { copyTextToClipboard } from '../utils/clipboard'

interface LogEntry {
  timestamp: string
  level: string
  message: string
}

let _cachedLogs: LogEntry[] = []

const Logs = () => {
  const { t } = useLanguage()
  const { showToast } = useToast()
  const [logs, setLogs] = useState<LogEntry[]>(_cachedLogs)
  const [loading, setLoading] = useState(_cachedLogs.length === 0)
  const logEndRef = useRef<HTMLDivElement>(null)
  const logContainerRef = useRef<HTMLDivElement>(null)
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    fetchLogs()
    const interval = setInterval(() => {
      if (!document.hidden) {
        fetchLogs()
      }
    }, 2000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    if (shouldAutoScroll && logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs, shouldAutoScroll])

  useEffect(() => {
    const container = logContainerRef.current
    if (!container) return

    const handleScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = container
      const isNearBottom = scrollHeight - scrollTop - clientHeight < 100
      setShouldAutoScroll(isNearBottom)
    }

    container.addEventListener('scroll', handleScroll)
    return () => container.removeEventListener('scroll', handleScroll)
  }, [])

  const fetchLogs = async () => {
    try {
      const response = await api.get('/logs?limit=100')
      const fetched = response.data.logs || []
      _cachedLogs = fetched
      setLogs(fetched)
    } catch (error) {
      console.error('Failed to fetch logs:', error)
    } finally {
      setLoading(false)
    }
  }

  const getLevelColor = (level: string): { bg: string; text: string } => {
    switch (level.toLowerCase()) {
      case 'error':
        return { bg: 'bg-rose-500/15 border-rose-500/30', text: 'text-rose-400 font-bold' }
      case 'warning':
      case 'warn':
        return { bg: 'bg-amber-500/15 border-amber-500/30', text: 'text-amber-400 font-bold' }
      case 'info':
        return { bg: 'bg-sky-500/15 border-sky-500/30', text: 'text-sky-400 font-semibold' }
      case 'debug':
        return { bg: 'bg-slate-500/15 border-slate-500/30', text: 'text-slate-400' }
      default:
        return { bg: 'bg-white/[0.06] border-white/[0.1]', text: 'text-slate-300' }
    }
  }

  const handleCopyLogs = async () => {
    if (logs.length === 0) return
    const text = logs.map(l => `[${l.timestamp}] [${l.level.toUpperCase()}] ${l.message}`).join('\n')
    const success = await copyTextToClipboard(text)
    if (success) {
      setCopied(true)
      showToast('success', 'Copied', 'All log entries copied to clipboard', 2000)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  if (loading && logs.length === 0) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center">
          <div className="inline-block animate-spin rounded-full h-10 w-10 border-2 border-sky-500 border-t-transparent mb-4"></div>
          <p className="text-xs font-mono text-slate-500 dark:text-slate-400">Streaming logs...</p>
        </div>
      </div>
    )
  }

  const handleClearDisplay = () => {
    setLogs([])
  }

  return (
    <div className="w-full max-w-7xl mx-auto space-y-5 sm:space-y-6 font-sans">
      {/* Header & Controls */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-2 border-b border-slate-200/60 dark:border-white/[0.05]">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-2xl sm:text-3xl font-extrabold tracking-tight text-slate-900 dark:text-white">
              {t.logs.title}
            </h1>
            <span className="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-mono font-medium bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
              STREAMING
            </span>
          </div>
          <p className="text-xs sm:text-sm text-slate-500 dark:text-slate-400 mt-0.5 font-medium">{t.logs.subtitle}</p>
        </div>
        
        <div className="flex items-center gap-2 flex-wrap">
          <span className="px-3 py-1.5 bg-white dark:bg-white/[0.04] text-slate-700 dark:text-slate-300 rounded-xl text-xs font-mono font-semibold border border-slate-200/80 dark:border-white/[0.08] shadow-2xs tabular-nums">
            {logs.length} lines
          </span>
          
          <button
            onClick={() => setShouldAutoScroll(prev => !prev)}
            className={`px-3.5 py-2 rounded-xl text-xs font-semibold flex items-center gap-1.5 border transition-all min-h-[44px] active:scale-95 cursor-pointer ${
              shouldAutoScroll
                ? 'bg-blue-500/10 dark:bg-sky-500/15 text-blue-600 dark:text-sky-400 border-blue-200 dark:border-sky-500/30'
                : 'bg-white dark:bg-white/[0.04] text-slate-500 dark:text-slate-400 border-slate-200/80 dark:border-white/[0.08]'
            }`}
            title="Toggle automatic scroll on new logs"
            aria-label="Toggle auto scroll"
          >
            <ArrowDownCircle size={15} className={shouldAutoScroll ? 'text-sky-500 animate-bounce' : 'opacity-40'} />
            <span>Auto-Scroll: {shouldAutoScroll ? 'ON' : 'OFF'}</span>
          </button>

          <button
            onClick={handleCopyLogs}
            disabled={logs.length === 0}
            className="px-3.5 py-2 bg-white dark:bg-white/[0.04] hover:bg-slate-100 dark:hover:bg-white/[0.08] text-slate-700 dark:text-slate-200 rounded-xl text-xs font-semibold border border-slate-200/80 dark:border-white/[0.08] transition-colors flex items-center gap-1.5 min-h-[44px] disabled:opacity-40 active:scale-95 cursor-pointer"
            title="Copy all logs"
            aria-label="Copy all logs"
          >
            {copied ? <CheckCircle2 size={15} className="text-emerald-500" /> : <Copy size={15} />}
            <span>{copied ? 'Copied' : 'Copy'}</span>
          </button>

          <button
            onClick={handleClearDisplay}
            className="px-3.5 py-2 bg-white dark:bg-white/[0.04] hover:bg-rose-50 dark:hover:bg-rose-950/30 text-slate-600 hover:text-rose-600 dark:text-slate-300 dark:hover:text-rose-400 rounded-xl text-xs font-semibold border border-slate-200/80 dark:border-white/[0.08] transition-colors flex items-center gap-1.5 min-h-[44px] active:scale-95 cursor-pointer"
            title="Clear current log view"
            aria-label="Clear current log view"
          >
            <Trash2 size={15} />
            <span>Clear</span>
          </button>
        </div>
      </div>

      {/* Terminal Telemetry Box */}
      <div className="relative rounded-3xl overflow-hidden border border-slate-800 bg-[#070b14] shadow-2xl">
        <div className="flex items-center justify-between px-4 py-3 bg-[#0d1220] border-b border-white/[0.06]">
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-rose-500/80"></span>
            <span className="w-3 h-3 rounded-full bg-amber-500/80"></span>
            <span className="w-3 h-3 rounded-full bg-emerald-500/80"></span>
            <span className="text-xs font-mono text-slate-400 ml-2">/var/log/smite.log</span>
          </div>
          <span className="text-[11px] font-mono text-slate-500 flex items-center gap-1.5">
            <Terminal size={12} className="text-sky-400" /> live journal
          </span>
        </div>

        <div 
          ref={logContainerRef}
          className="p-4 sm:p-5 font-mono text-xs overflow-y-auto max-h-[62dvh] sm:max-h-[68vh] selection:bg-sky-500/30 leading-relaxed" 
          dir="ltr"
        >
          {logs.length === 0 ? (
            <div className="text-center py-20 text-slate-500 text-xs font-mono">No log entries available</div>
          ) : (
            <div className="space-y-1.5">
              {logs.map((log, index) => {
                const levelStyle = getLevelColor(log.level)
                return (
                  <div key={index} className="hover:bg-white/[0.03] px-2 py-1 rounded-lg transition-colors break-all flex items-start gap-2.5">
                    <span className="text-slate-500 select-none text-[11px] tabular-nums shrink-0 font-mono">
                      {log.timestamp}
                    </span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded-md border uppercase shrink-0 font-bold ${levelStyle.bg} ${levelStyle.text}`}>
                      {log.level}
                    </span>
                    <span className="text-slate-200 text-xs flex-1">
                      {log.message}
                    </span>
                  </div>
                )
              })}
            </div>
          )}
          <div ref={logEndRef} />
        </div>
      </div>
    </div>
  )
}

export default Logs
