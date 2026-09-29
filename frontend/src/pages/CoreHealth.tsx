import { useState, useEffect } from 'react'
import { Activity, RefreshCw, Clock, CheckCircle2, XCircle, AlertCircle, Shield } from 'lucide-react'
import api from '../api/client'
import { useLanguage } from '../contexts/LanguageContext'
import { useToast } from '../contexts/ToastContext'

interface CoreHealth {
  core: string
  nodes_status: Record<string, {
    id: string
    name: string
    role: string
    status: string
    error_message?: string | null
  }>
  servers_status: Record<string, {
    id: string
    name: string
    role: string
    status: string
    error_message?: string | null
  }>
}

interface ResetConfig {
  core: string
  enabled: boolean
  interval_minutes: number
  last_reset: string | null
  next_reset: string | null
}

const CoreHealth = () => {
  const { t } = useLanguage()
  const { showToast, showConfirm } = useToast()
  
  // Instant SWR: Load from cache immediately to eliminate perceived loading latency
  const [health, setHealth] = useState<CoreHealth[]>(() => {
    try {
      const cached = sessionStorage.getItem('smite_core_health')
      return cached ? JSON.parse(cached) : []
    } catch {
      return []
    }
  })
  const [configs, setConfigs] = useState<ResetConfig[]>(() => {
    try {
      const cached = sessionStorage.getItem('smite_core_configs')
      return cached ? JSON.parse(cached) : []
    } catch {
      return []
    }
  })
  const [loading, setLoading] = useState(() => health.length === 0)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [updating, setUpdating] = useState<string | null>(null)

  const fetchData = async (isManual = false) => {
    if (isManual) setIsRefreshing(true)
    try {
      const [healthRes, configsRes] = await Promise.all([
        api.get('/core-health/health'),
        api.get('/core-health/reset-config')
      ])
      setHealth(healthRes.data)
      setConfigs(configsRes.data)
      try {
        sessionStorage.setItem('smite_core_health', JSON.stringify(healthRes.data))
        sessionStorage.setItem('smite_core_configs', JSON.stringify(configsRes.data))
      } catch (e) {
        // Ignore session storage errors
      }
    } catch (error) {
      console.error('Failed to fetch core health:', error)
    } finally {
      setLoading(false)
      setIsRefreshing(false)
    }
  }

  useEffect(() => {
    fetchData()
    const interval = setInterval(() => fetchData(false), 10000)
    return () => clearInterval(interval)
  }, [])

  const handleReset = async (core: string) => {
    const confirmed = await showConfirm({
      title: 'Reset Core Service',
      message: `Are you sure you want to reset the ${core} core? Active connections using this core may be temporarily interrupted.`,
      variant: 'danger',
      confirmText: 'Reset Core'
    })
    if (!confirmed) return
    
    setUpdating(core)
    try {
      await api.post(`/core-health/reset/${core}`)
      showToast('success', 'Core Reset', `${core} core was successfully reset`)
      await fetchData(true)
    } catch (error) {
      console.error(`Failed to reset ${core}:`, error)
      showToast('error', 'Error', `Failed to reset ${core}`)
    } finally {
      setUpdating(null)
    }
  }

  const handleConfigUpdate = async (core: string, updates: Partial<ResetConfig>) => {
    setUpdating(core)
    try {
      await api.put(`/core-health/reset-config/${core}`, updates)
      showToast('success', 'Configuration Updated', `Reset schedule for ${core} updated`)
      await fetchData(true)
    } catch (error) {
      console.error(`Failed to update config for ${core}:`, error)
      showToast('error', 'Error', 'Failed to update reset configuration')
    } finally {
      setUpdating(null)
    }
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case "connected":
        return "text-emerald-600 dark:text-emerald-400"
      case "connecting":
      case "reconnecting":
        return "text-amber-600 dark:text-amber-400"
      case "failed":
        return "text-rose-600 dark:text-rose-400"
      default:
        return "text-slate-500 dark:text-slate-400"
    }
  }

  const getStatusIcon = (status: string) => {
    switch (status) {
      case "connected":
        return <CheckCircle2 className="w-4 h-4 text-emerald-500" />
      case "connecting":
      case "reconnecting":
        return <AlertCircle className="w-4 h-4 text-amber-500" />
      case "failed":
        return <XCircle className="w-4 h-4 text-rose-500" />
      default:
        return <AlertCircle className="w-4 h-4 text-slate-400" />
    }
  }

  const getStatusText = (status: string) => {
    switch (status) {
      case "connected": return "Connected"
      case "connecting": return "Connecting"
      case "reconnecting": return "Reconnecting"
      case "failed": return "Failed"
      default: return "Unknown"
    }
  }

  if (loading && health.length === 0) {
    return (
      <div className="w-full max-w-7xl mx-auto space-y-6 font-sans">
        <div className="pb-2 border-b border-slate-200/60 dark:border-white/[0.05] flex items-center justify-between">
          <div>
            <div className="h-8 w-48 bg-slate-200 dark:bg-slate-800 rounded-lg animate-pulse mb-2" />
            <div className="h-4 w-72 bg-slate-100 dark:bg-slate-800/60 rounded-md animate-pulse" />
          </div>
        </div>

        <div className="space-y-4 sm:space-y-6">
          {['backhaul', 'rathole', 'chisel', 'frp', 'gost'].map((c) => (
            <div
              key={c}
              className="bg-white/90 dark:bg-[#0c1220]/90 rounded-3xl border border-slate-200/80 dark:border-white/[0.08] p-5 sm:p-7 space-y-5 animate-pulse"
            >
              <div className="flex items-center justify-between pb-3 border-b border-slate-100 dark:border-white/[0.05]">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-2xl bg-slate-200 dark:bg-slate-800" />
                  <div className="space-y-1.5">
                    <div className="h-5 w-32 bg-slate-200 dark:bg-slate-800 rounded-md" />
                    <div className="h-3 w-48 bg-slate-100 dark:bg-slate-800/60 rounded-md" />
                  </div>
                </div>
                <div className="h-9 w-28 bg-slate-200 dark:bg-slate-800 rounded-xl" />
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="h-24 bg-slate-100 dark:bg-white/[0.02] rounded-2xl border border-slate-200/50 dark:border-white/[0.05]" />
                <div className="h-24 bg-slate-100 dark:bg-white/[0.02] rounded-2xl border border-slate-200/50 dark:border-white/[0.05]" />
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="w-full max-w-7xl mx-auto space-y-6 font-sans">
      <div className="pb-2 border-b border-slate-200/60 dark:border-white/[0.05] flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl sm:text-3xl font-extrabold tracking-tight text-slate-900 dark:text-white">
            {t.coreHealth.title}
          </h1>
          <p className="text-xs sm:text-sm text-slate-500 dark:text-slate-400 mt-0.5 font-medium">{t.coreHealth.subtitle}</p>
        </div>

        <button
          onClick={() => fetchData(true)}
          disabled={isRefreshing}
          className="flex items-center gap-2 px-3.5 py-2 bg-white dark:bg-slate-800/80 hover:bg-slate-50 dark:hover:bg-slate-700/80 text-slate-700 dark:text-slate-200 rounded-xl text-xs font-semibold border border-slate-200/80 dark:border-white/[0.08] shadow-xs active:scale-95 transition-all min-h-[44px] cursor-pointer self-start sm:self-auto"
        >
          <RefreshCw className={`w-3.5 h-3.5 text-blue-500 ${isRefreshing ? 'animate-spin' : ''}`} />
          <span>{isRefreshing ? 'Checking Health...' : 'Refresh Status'}</span>
        </button>
      </div>

      <div className="space-y-4 sm:space-y-6">
        {health.map((coreHealth) => {
          const config = configs.find(c => c.core === coreHealth.core)
          const nodeCount = Object.keys(coreHealth.nodes_status).length
          const serverCount = Object.keys(coreHealth.servers_status).length

          return (
            <div
              key={coreHealth.core}
              className="bg-white/90 dark:bg-[#0c1220]/90 rounded-3xl shadow-sm border border-slate-200/80 dark:border-white/[0.08] p-5 sm:p-7 transition-all backdrop-blur-xl hover:border-slate-300 dark:hover:border-white/[0.15] space-y-5"
            >
              {/* Core Header */}
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-slate-100 dark:border-white/[0.05]">
                <div className="flex items-center gap-3">
                  <div className="p-2.5 bg-blue-500/10 dark:bg-sky-500/15 rounded-2xl text-blue-600 dark:text-sky-400 border border-blue-200/50 dark:border-sky-500/20 shadow-glow-sm">
                    <Activity className="w-5 h-5" />
                  </div>
                  <div>
                    <h2 className="text-base sm:text-lg font-extrabold text-slate-900 dark:text-white capitalize tracking-tight">
                      {coreHealth.core} Core
                    </h2>
                    <p className="text-xs text-slate-500 dark:text-slate-400 font-mono">
                      {nodeCount} Iran node(s) • {serverCount} Foreign server(s)
                    </p>
                  </div>
                </div>

                <button
                  onClick={() => handleReset(coreHealth.core)}
                  disabled={updating === coreHealth.core}
                  className="flex items-center justify-center gap-2 px-4 py-2 bg-slate-100 dark:bg-white/[0.06] hover:bg-slate-200 dark:hover:bg-white/[0.1] text-slate-700 dark:text-slate-200 rounded-xl text-xs font-semibold transition-all min-h-[44px] active:scale-95 disabled:opacity-50 cursor-pointer self-start sm:self-auto border border-slate-200/60 dark:border-white/[0.08]"
                >
                  {updating === coreHealth.core ? (
                    <>
                      <div className="w-3.5 h-3.5 border-2 border-slate-400 border-t-transparent rounded-full animate-spin"></div>
                      <span>Resetting...</span>
                    </>
                  ) : (
                    <>
                      <RefreshCw className="w-3.5 h-3.5 text-sky-500" />
                      <span>Restart Core</span>
                    </>
                  )}
                </button>
              </div>

              {/* Status Grid */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-6">
                {/* Iran Nodes */}
                <div className="bg-slate-50/70 dark:bg-white/[0.02] p-4 rounded-2xl border border-slate-200/70 dark:border-white/[0.05]">
                  <h3 className="text-[11px] font-mono font-bold uppercase tracking-wider text-slate-400 dark:text-slate-500 mb-3">
                    Iran Nodes Status
                  </h3>
                  <div className="space-y-2">
                    {Object.entries(coreHealth.nodes_status).map(([nodeId, nodeInfo]) => (
                      <div key={nodeId} className="space-y-1">
                        <div className="flex items-center justify-between text-xs sm:text-sm">
                          <span className="text-slate-700 dark:text-slate-300 font-medium truncate max-w-[200px]">
                            {nodeInfo.name || nodeId.substring(0, 8)}
                          </span>
                          <div className="flex items-center gap-1.5 shrink-0 font-mono text-xs">
                            {getStatusIcon(nodeInfo.status)}
                            <span className={`font-bold ${getStatusColor(nodeInfo.status)}`}>
                              {getStatusText(nodeInfo.status)}
                            </span>
                          </div>
                        </div>
                        {nodeInfo.error_message && (
                          <p className="text-[11px] text-rose-600 dark:text-rose-400 bg-rose-50 dark:bg-rose-950/40 p-2 rounded-xl border border-rose-200/60 dark:border-rose-900/40 font-mono">
                            {nodeInfo.error_message}
                          </p>
                        )}
                      </div>
                    ))}
                    {nodeCount === 0 && (
                      <span className="text-xs text-slate-400 dark:text-slate-500 italic font-mono">No active Iran nodes</span>
                    )}
                  </div>
                </div>

                {/* Foreign Servers */}
                <div className="bg-slate-50/70 dark:bg-white/[0.02] p-4 rounded-2xl border border-slate-200/70 dark:border-white/[0.05]">
                  <h3 className="text-[11px] font-mono font-bold uppercase tracking-wider text-slate-400 dark:text-slate-500 mb-3">
                    Foreign Servers Status
                  </h3>
                  <div className="space-y-2">
                    {serverCount === 0 ? (
                      <span className="text-xs text-slate-400 dark:text-slate-500 italic font-mono">No active foreign servers</span>
                    ) : (
                      Object.entries(coreHealth.servers_status).map(([serverId, serverInfo]) => (
                        <div key={serverId} className="space-y-1">
                          <div className="flex items-center justify-between text-xs sm:text-sm">
                            <span className="text-slate-700 dark:text-slate-300 font-medium truncate max-w-[200px]">
                              {serverInfo.name || serverId.substring(0, 8)}
                            </span>
                            <div className="flex items-center gap-1.5 shrink-0 font-mono text-xs">
                              {getStatusIcon(serverInfo.status)}
                              <span className={`font-bold ${getStatusColor(serverInfo.status)}`}>
                                {getStatusText(serverInfo.status)}
                              </span>
                            </div>
                          </div>
                          {serverInfo.error_message && (
                            <p className="text-[11px] text-rose-600 dark:text-rose-400 bg-rose-50 dark:bg-rose-950/40 p-2 rounded-xl border border-rose-200/60 dark:border-rose-900/40 font-mono">
                              {serverInfo.error_message}
                            </p>
                          )}
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </div>

              {/* Auto Reset Timer & Actions */}
              <div className="border-t border-slate-100 dark:border-white/[0.05] pt-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-xl bg-slate-100 dark:bg-white/[0.05] text-slate-500 dark:text-slate-400">
                    <Clock className="w-4 h-4" />
                  </div>
                  <div>
                    <h3 className="text-xs sm:text-sm font-bold text-slate-800 dark:text-slate-200">
                      Auto Reset Schedule
                    </h3>
                    <p className="text-[11px] text-slate-500 dark:text-slate-400">Periodically restarts core daemon</p>
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  {config?.enabled && (
                    <div className="flex items-center gap-2 px-3 py-1.5 bg-blue-50/60 dark:bg-sky-950/30 rounded-xl border border-blue-100 dark:border-sky-900/40 text-xs">
                      <span className="text-slate-600 dark:text-slate-300 font-medium">Interval:</span>
                      <input
                        type="number"
                        min="1"
                        value={config.interval_minutes}
                        onChange={(e) => {
                          const minutes = parseInt(e.target.value)
                          if (minutes >= 1) {
                            handleConfigUpdate(coreHealth.core, { interval_minutes: minutes })
                          }
                        }}
                        disabled={updating === coreHealth.core}
                        className="w-16 px-2 py-1.5 text-base sm:text-xs border border-slate-300 dark:border-white/[0.1] rounded-lg bg-white dark:bg-[#070b14] text-slate-900 dark:text-white font-mono focus:outline-none focus:ring-1 focus:ring-sky-500 tabular-nums"
                      />
                      <span className="text-slate-500 font-mono">min</span>
                    </div>
                  )}

                  <label className="relative inline-flex items-center cursor-pointer min-h-[44px] min-w-[44px] justify-end">
                    <input
                      type="checkbox"
                      checked={config?.enabled || false}
                      onChange={(e) => handleConfigUpdate(coreHealth.core, { enabled: e.target.checked })}
                      disabled={updating === coreHealth.core}
                      className="sr-only peer"
                    />
                    <div className="w-11 h-6 bg-slate-200 peer-focus:outline-none rounded-full peer dark:bg-slate-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[12px] after:right-[22px] peer-checked:after:right-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-slate-600 peer-checked:bg-blue-600"></div>
                  </label>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default CoreHealth
