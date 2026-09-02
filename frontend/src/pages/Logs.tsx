import { useEffect, useState, useRef } from 'react'
import { Copy, Trash2, ArrowDownCircle, CheckCircle2 } from 'lucide-react'
import api from '../api/client'
import { useLanguage } from '../contexts/LanguageContext'
import { useToast } from '../contexts/ToastContext'
import { copyTextToClipboard } from '../utils/clipboard'

interface LogEntry {
  timestamp: string
  level: string
  message: string
}

const Logs = () => {
  const { t } = useLanguage()
  const { showToast } = useToast()
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const logEndRef = useRef<HTMLDivElement>(null)
  const logContainerRef = useRef<HTMLDivElement>(null)
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    fetchLogs()
    const interval = setInterval(fetchLogs, 2000)
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
      setLogs(response.data.logs || [])
    } catch (error) {
      console.error('Failed to fetch logs:', error)
    } finally {
      setLoading(false)
    }
  }

  const getLevelColor = (level: string, dark = false): string => {
    const colors: Record<string, { light: string; dark: string }> = {
      error: { light: 'text-red-600', dark: 'text-rose-400 font-bold' },
      warning: { light: 'text-yellow-600', dark: 'text-amber-300 font-bold' },
      warn: { light: 'text-yellow-600', dark: 'text-amber-300 font-bold' },
      info: { light: 'text-blue-600', dark: 'text-blue-400 font-semibold' },
      debug: { light: 'text-gray-500', dark: 'text-gray-400' },
    }
    const c = colors[level.toLowerCase()] ?? { light: 'text-gray-700', dark: 'text-gray-300' }
    return dark ? c.dark : c.light
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
          <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 dark:border-blue-400 mb-4"></div>
          <p className="text-gray-500 dark:text-gray-400">Loading logs...</p>
        </div>
      </div>
    )
  }

  const handleClearDisplay = () => {
    setLogs([])
  }

  return (
    <div className="w-full max-w-7xl mx-auto space-y-4 sm:space-y-6">
      {/* Header & Controls */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold text-gray-900 dark:text-white tracking-tight">{t.logs.title}</h1>
          <p className="text-xs sm:text-sm text-gray-500 dark:text-gray-400 mt-1">{t.logs.subtitle}</p>
        </div>
        
        <div className="flex items-center gap-2 flex-wrap">
          <span className="px-3 py-1.5 bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 rounded-xl text-xs font-mono font-semibold border border-gray-200/80 dark:border-gray-700/80">
            {logs.length} entries
          </span>
          
          <button
            onClick={() => setShouldAutoScroll(prev => !prev)}
            className={`px-3 py-1.5 rounded-xl text-xs font-semibold flex items-center gap-1.5 border transition-all min-h-[38px] ${
              shouldAutoScroll
                ? 'bg-blue-50 dark:bg-blue-950/40 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800'
                : 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700'
            }`}
            title="Toggle automatic scroll on new logs"
          >
            <ArrowDownCircle size={15} className={shouldAutoScroll ? 'animate-bounce' : 'opacity-40'} />
            <span>Auto-Scroll: {shouldAutoScroll ? 'ON' : 'OFF'}</span>
          </button>

          <button
            onClick={handleCopyLogs}
            disabled={logs.length === 0}
            className="px-3 py-1.5 bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-800 dark:text-gray-200 rounded-xl text-xs font-semibold border border-gray-200/80 dark:border-gray-700/80 transition-colors flex items-center gap-1.5 min-h-[38px] disabled:opacity-40"
            title="Copy all logs"
          >
            {copied ? <CheckCircle2 size={15} className="text-emerald-500" /> : <Copy size={15} />}
            <span>{copied ? 'Copied' : 'Copy'}</span>
          </button>

          <button
            onClick={handleClearDisplay}
            className="px-3 py-1.5 bg-gray-100 hover:bg-rose-50 dark:bg-gray-800 dark:hover:bg-rose-950/30 text-gray-700 hover:text-rose-600 dark:text-gray-300 dark:hover:text-rose-400 rounded-xl text-xs font-semibold border border-gray-200/80 dark:border-gray-700/80 transition-colors flex items-center gap-1.5 min-h-[38px]"
            title="Clear current log view"
          >
            <Trash2 size={15} />
            <span>Clear</span>
          </button>
        </div>
      </div>

      {/* Terminal View */}
      <div 
        ref={logContainerRef}
        className="bg-gray-950 rounded-2xl border border-gray-800 shadow-lg p-3.5 sm:p-5 font-mono text-xs sm:text-sm overflow-y-auto max-h-[62dvh] sm:max-h-[70vh] selection:bg-blue-500/30" 
        dir="ltr"
      >
        {logs.length === 0 ? (
          <div className="text-center py-16 text-gray-500 text-xs sm:text-sm">No log entries available</div>
        ) : (
          <div className="space-y-1">
            {logs.map((log, index) => (
              <div key={index} className="hover:bg-gray-900/80 px-2 py-1 rounded-md transition-colors break-all leading-relaxed">
                <span className="text-gray-500 select-none text-[11px] sm:text-xs">[{log.timestamp}]</span>{' '}
                <span className={`text-[11px] sm:text-xs uppercase ${getLevelColor(log.level, true)}`}>[{log.level}]</span>{' '}
                <span className="text-gray-200 text-xs sm:text-sm">{log.message}</span>
              </div>
            ))}
          </div>
        )}
        <div ref={logEndRef} />
      </div>
    </div>
  )
}

export default Logs

