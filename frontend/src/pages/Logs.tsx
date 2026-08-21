import { useEffect, useState, useRef } from 'react'
import api from '../api/client'
import { useLanguage } from '../contexts/LanguageContext'

interface LogEntry {
  timestamp: string
  level: string
  message: string
}

const Logs = () => {
  const { t } = useLanguage()
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const logEndRef = useRef<HTMLDivElement>(null)
  const logContainerRef = useRef<HTMLDivElement>(null)
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true)

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
      error: { light: 'text-rose-400 font-bold', dark: 'text-rose-400 font-bold' },
      warning: { light: 'text-amber-400 font-bold', dark: 'text-amber-400 font-bold' },
      warn: { light: 'text-amber-400 font-bold', dark: 'text-amber-400 font-bold' },
      info: { light: 'text-[#00A8CC] font-bold', dark: 'text-[#00A8CC] font-bold' },
      debug: { light: 'text-[#DBE2EF]/60', dark: 'text-[#DBE2EF]/60' },
    }
    const c = colors[level.toLowerCase()] ?? { light: 'text-[#DBE2EF]', dark: 'text-[#DBE2EF]' }
    return dark ? c.dark : c.light
  }

  if (loading && logs.length === 0) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center">
          <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-[#3F72AF] dark:border-[#00A8CC] mb-4"></div>
          <p className="text-sm font-medium text-[#112D4E]/70 dark:text-[#DBE2EF]/80">Loading logs...</p>
        </div>
      </div>
    )
  }

  const handleClearDisplay = () => {
    setLogs([]) // Just clears local state
  }

  return (
    <div className="w-full max-w-7xl mx-auto space-y-8">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-black tracking-tight text-[#112D4E] dark:text-[#F9F7F7] mb-2">{t.logs.title}</h1>
          <p className="text-sm font-medium text-[#112D4E]/70 dark:text-[#DBE2EF]/80">{t.logs.subtitle}</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="px-3.5 py-2 bg-[#DBE2EF]/60 dark:bg-[#142850] text-[#112D4E] dark:text-[#00A8CC] rounded-xl text-xs font-mono font-bold border border-[#DBE2EF] dark:border-[#0C7B93]/30">
            {logs.length} entries
          </span>
          <button
            onClick={handleClearDisplay}
            className="px-4 py-2 bg-[#DBE2EF]/60 hover:bg-[#DBE2EF] dark:bg-[#142850] dark:hover:bg-[#142850]/80 text-[#112D4E] dark:text-[#DBE2EF] rounded-xl text-xs font-bold transition-colors border border-[#DBE2EF] dark:border-[#0C7B93]/30"
          >
            Clear display
          </button>
        </div>
      </div>

      <div 
        ref={logContainerRef}
        className="bg-[#142850] rounded-2xl border border-[#DBE2EF] dark:border-[#0C7B93]/40 shadow-2xl p-6 font-mono text-xs overflow-auto text-[#DBE2EF]" 
        style={{ maxHeight: '70vh' }}
      >
        {logs.length === 0 ? (
          <div className="text-center py-12 text-[#DBE2EF]/50 font-medium">No logs available</div>
        ) : (
          logs.map((log, index) => (
            <div key={index} className="mb-1.5 hover:bg-[#27496D]/50 px-2.5 py-1 rounded-lg transition-colors leading-relaxed">
              <span className="text-[#DBE2EF]/50">[{log.timestamp}]</span>{' '}
              <span className={`${getLevelColor(log.level, true)}`}>[{log.level.toUpperCase()}]</span>{' '}
              <span className="text-[#F9F7F7]">{log.message}</span>
            </div>
          ))
        )}
        <div ref={logEndRef} />
      </div>
    </div>
  )
}

export default Logs

