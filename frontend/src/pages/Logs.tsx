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
      error: { light: 'text-red-600', dark: 'text-red-400' },
      warning: { light: 'text-yellow-600', dark: 'text-yellow-300' },
      warn: { light: 'text-yellow-600', dark: 'text-yellow-300' },
      info: { light: 'text-blue-600', dark: 'text-blue-300' },
      debug: { light: 'text-gray-500', dark: 'text-gray-400' },
    }
    const c = colors[level.toLowerCase()] ?? { light: 'text-gray-700', dark: 'text-gray-300' }
    return dark ? c.dark : c.light
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
    setLogs([]) // Just clears local state
  }

  return (
    <div className="w-full max-w-7xl mx-auto">
      <div className="flex justify-between items-center mb-8">
        <div>
          <h1 className="text-3xl font-bold text-gray-900 dark:text-white mb-2">{t.logs.title}</h1>
          <p className="text-gray-500 dark:text-gray-400">{t.logs.subtitle}</p>
        </div>
        <div className="flex gap-3">
          <span className="px-3 py-1.5 bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium border border-gray-200 dark:border-gray-700">
            {logs.length} entries
          </span>
          <button
            onClick={handleClearDisplay}
            className="px-4 py-1.5 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded-lg text-sm font-medium transition-colors"
          >
            Clear display
          </button>
        </div>
      </div>

      <div 
        ref={logContainerRef}
        className="bg-gray-900 dark:bg-black rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm p-6 font-mono text-sm overflow-auto" 
        style={{ maxHeight: '70vh' }}
      >
        {logs.length === 0 ? (
          <div className="text-center py-12 text-gray-400">No logs available</div>
        ) : (
          logs.map((log, index) => (
            <div key={index} className="mb-1 hover:bg-gray-800/50 px-2 py-1 rounded">
              <span className="text-gray-500 dark:text-gray-400">[{log.timestamp}]</span>{' '}
              <span className={`${getLevelColor(log.level, true)}`}>[{log.level.toUpperCase()}]</span>{' '}
              <span className="text-gray-300 dark:text-gray-200">{log.message}</span>
            </div>
          ))
        )}
        <div ref={logEndRef} />
      </div>
    </div>
  )
}

export default Logs

