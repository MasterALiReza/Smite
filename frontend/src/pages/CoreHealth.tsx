import { useState, useEffect } from 'react'
import { Activity, RefreshCw, Clock, CheckCircle2, XCircle, AlertCircle, Settings } from 'lucide-react'
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
  const [health, setHealth] = useState<CoreHealth[]>([])
  const [configs, setConfigs] = useState<ResetConfig[]>([])
  const [loading, setLoading] = useState(true)
  const [updating, setUpdating] = useState<string | null>(null)

  const fetchData = async () => {
    try {
      const [healthRes, configsRes] = await Promise.all([
        api.get('/core-health/health'),
        api.get('/core-health/reset-config')
      ])
      setHealth(healthRes.data)
      setConfigs(configsRes.data)
    } catch (error) {
      console.error('Failed to fetch core health:', error)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 10000)
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
      await fetchData()
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
      await fetchData()
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
        return "text-green-500"
      case "connecting":
        return "text-yellow-500"
      case "reconnecting":
        return "text-yellow-500"
      case "failed":
        return "text-red-500"
      default:
        return "text-gray-500"
    }
  }

  const getStatusBgColor = (status: string) => {
    switch (status) {
      case "connected":
        return "bg-green-100 dark:bg-green-900/30 text-green-800 dark:text-green-200"
      case "connecting":
        return "bg-yellow-100 dark:bg-yellow-900/30 text-yellow-800 dark:text-yellow-200"
      case "reconnecting":
        return "bg-yellow-100 dark:bg-yellow-900/30 text-yellow-800 dark:text-yellow-200"
      case "failed":
        return "bg-red-100 dark:bg-red-900/30 text-red-800 dark:text-red-200"
      default:
        return "bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-200"
    }
  }

  const getStatusIcon = (status: string) => {
    switch (status) {
      case "connected":
        return <CheckCircle2 className="w-5 h-5 text-green-500" />
      case "connecting":
      case "reconnecting":
        return <AlertCircle className="w-5 h-5 text-yellow-500" />
      case "failed":
        return <XCircle className="w-5 h-5 text-red-500" />
      default:
        return <AlertCircle className="w-5 h-5 text-gray-500" />
    }
  }

  const getStatusText = (status: string) => {
    switch (status) {
      case "connected":
        return "Connected"
      case "connecting":
        return "Connecting"
      case "reconnecting":
        return "Reconnecting"
      case "failed":
        return "Failed"
      default:
        return "Unknown"
    }
  }


  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center">
          <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 dark:border-blue-400 mb-4"></div>
          <p className="text-gray-500 dark:text-gray-400">{t.common.loading}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="w-full max-w-7xl mx-auto space-y-5 sm:space-y-6">
      <div>
        <h1 className="text-2xl sm:text-3xl font-bold text-gray-900 dark:text-white tracking-tight">{t.coreHealth.title}</h1>
        <p className="text-xs sm:text-sm text-gray-500 dark:text-gray-400 mt-0.5">{t.coreHealth.subtitle}</p>
      </div>

      <div className="space-y-4 sm:space-y-6">
        {health.map((coreHealth) => {
          const config = configs.find(c => c.core === coreHealth.core)
          const nodeCount = Object.keys(coreHealth.nodes_status).length
          const serverCount = Object.keys(coreHealth.servers_status).length

          return (
            <div
              key={coreHealth.core}
              className="bg-white dark:bg-gray-800 rounded-2xl shadow-xs border border-gray-200/80 dark:border-gray-700/80 p-5 sm:p-6 transition-shadow hover:shadow-md space-y-5"
            >
              {/* Core Header */}
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <div className="p-2.5 bg-blue-100 dark:bg-blue-900/40 rounded-xl text-blue-600 dark:text-blue-400">
                    <Activity className="w-5 h-5" />
                  </div>
                  <div>
                    <h2 className="text-lg font-bold text-gray-900 dark:text-white capitalize">
                      {coreHealth.core}
                    </h2>
                    <p className="text-xs text-gray-500 dark:text-gray-400 font-medium">
                      {nodeCount} node(s), {serverCount} server(s)
                    </p>
                  </div>
                </div>
              </div>

              {/* Status Grid */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-6">
                {/* Iran Nodes */}
                <div className="bg-gray-50 dark:bg-gray-750/50 p-3.5 sm:p-4 rounded-xl border border-gray-100 dark:border-gray-700/60">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-3">
                    Iran Nodes Status
                  </h3>
                  <div className="space-y-2">
                    {Object.entries(coreHealth.nodes_status).map(([nodeId, nodeInfo]) => (
                      <div key={nodeId} className="space-y-1">
                        <div className="flex items-center justify-between text-xs sm:text-sm">
                          <span className="text-gray-700 dark:text-gray-300 font-medium truncate max-w-[180px]">
                            {nodeInfo.name || nodeId.substring(0, 8)}
                          </span>
                          <div className="flex items-center gap-1.5 shrink-0">
                            {getStatusIcon(nodeInfo.status)}
                            <span className={`font-semibold ${getStatusColor(nodeInfo.status)}`}>
                              {getStatusText(nodeInfo.status)}
                            </span>
                          </div>
                        </div>
                        {nodeInfo.error_message && (
                          <p className="text-[11px] text-rose-600 dark:text-rose-400 bg-rose-50 dark:bg-rose-950/40 p-1.5 rounded-md">
                            {nodeInfo.error_message}
                          </p>
                        )}
                      </div>
                    ))}
                    {nodeCount === 0 && (
                      <span className="text-xs text-gray-400 dark:text-gray-500 italic">No active Iran nodes</span>
                    )}
                  </div>
                </div>

                {/* Foreign Servers */}
                <div className="bg-gray-50 dark:bg-gray-750/50 p-3.5 sm:p-4 rounded-xl border border-gray-100 dark:border-gray-700/60">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-3">
                    Foreign Servers Status
                  </h3>
                  <div className="space-y-2">
                    {serverCount === 0 ? (
                      <span className="text-xs text-gray-400 dark:text-gray-500 italic">No active foreign servers</span>
                    ) : (
                      Object.entries(coreHealth.servers_status).map(([serverId, serverInfo]) => (
                        <div key={serverId} className="space-y-1">
                          <div className="flex items-center justify-between text-xs sm:text-sm">
                            <span className="text-gray-700 dark:text-gray-300 font-medium truncate max-w-[180px]">
                              {serverInfo.name || serverId.substring(0, 8)}
                            </span>
                            <div className="flex items-center gap-1.5 shrink-0">
                              {getStatusIcon(serverInfo.status)}
                              <span className={`font-semibold ${getStatusColor(serverInfo.status)}`}>
                                {getStatusText(serverInfo.status)}
                              </span>
                            </div>
                          </div>
                          {serverInfo.error_message && (
                            <p className="text-[11px] text-rose-600 dark:text-rose-400 bg-rose-50 dark:bg-rose-950/40 p-1.5 rounded-md">
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
              <div className="border-t border-gray-100 dark:border-gray-700/80 pt-4 space-y-4">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <Clock className="w-4 h-4 text-gray-400 dark:text-gray-500" />
                    <h3 className="text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300">
                      Auto Reset Timer
                    </h3>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer min-h-[44px] min-w-[44px] justify-end">
                    <input
                      type="checkbox"
                      checked={config?.enabled || false}
                      onChange={(e) => handleConfigUpdate(coreHealth.core, { enabled: e.target.checked })}
                      disabled={updating === coreHealth.core}
                      className="sr-only peer"
                    />
                    <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[11px] after:right-[22px] peer-checked:after:right-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"></div>
                  </label>
                </div>

                {config?.enabled && (
                  <div className="flex items-center gap-3 p-3 bg-blue-50/50 dark:bg-blue-950/30 rounded-xl border border-blue-100 dark:border-blue-900/40">
                    <label className="text-xs sm:text-sm text-gray-700 dark:text-gray-300 font-medium">
                      Interval (minutes):
                    </label>
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
                      className="w-24 px-3 py-1.5 text-base sm:text-sm border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-750 text-gray-900 dark:text-white font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                )}

                <div className="flex items-center justify-end pt-2">
                  <button
                    onClick={() => handleReset(coreHealth.core)}
                    disabled={updating === coreHealth.core}
                    className="flex items-center justify-center gap-2 px-4 py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 text-white rounded-xl transition-all font-semibold shadow-xs hover:shadow-md text-xs sm:text-sm min-h-[44px] min-w-[120px] active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {updating === coreHealth.core ? (
                      <>
                        <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
                        <span>Resetting...</span>
                      </>
                    ) : (
                      <>
                        <RefreshCw className="w-4 h-4" />
                        <span>Reset Now</span>
                      </>
                    )}
                  </button>
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

