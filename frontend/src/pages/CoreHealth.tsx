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
          <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-[#3F72AF] dark:border-[#00A8CC] mb-4"></div>
          <p className="text-sm font-medium text-[#112D4E]/70 dark:text-[#DBE2EF]/80">{t.common.loading}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="w-full max-w-7xl mx-auto space-y-8">
      <div>
        <h1 className="text-3xl font-black tracking-tight text-[#112D4E] dark:text-[#F9F7F7] mb-2">{t.coreHealth.title}</h1>
        <p className="text-sm font-medium text-[#112D4E]/70 dark:text-[#DBE2EF]/80">{t.coreHealth.subtitle}</p>
      </div>

      <div className="space-y-6">
        {health.map((coreHealth) => {
          const config = configs.find(c => c.core === coreHealth.core)
          const nodeCount = Object.keys(coreHealth.nodes_status).length
          const serverCount = Object.keys(coreHealth.servers_status).length

          return (
            <div
              key={coreHealth.core}
              className="bg-white dark:bg-[#27496D] rounded-2xl border border-[#DBE2EF] dark:border-[#142850] p-6 shadow-sm space-y-6"
            >
              <div className="flex items-center gap-3.5">
                <div className="p-3 bg-[#3F72AF]/10 dark:bg-[#00A8CC]/15 text-[#3F72AF] dark:text-[#00A8CC] rounded-xl">
                  <Activity className="w-6 h-6" />
                </div>
                <div>
                  <h2 className="text-lg font-bold text-[#112D4E] dark:text-[#F9F7F7] capitalize">
                    {coreHealth.core}
                  </h2>
                  <p className="text-xs font-medium text-[#112D4E]/60 dark:text-[#DBE2EF]/70">
                    {nodeCount} node(s), {serverCount} server(s)
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="p-4 bg-[#F9F7F7] dark:bg-[#142850]/50 rounded-xl border border-[#DBE2EF] dark:border-[#142850]">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-[#112D4E]/70 dark:text-[#DBE2EF]/70 mb-3">
                    Node Status
                  </h3>
                  <div className="space-y-2.5">
                    {Object.entries(coreHealth.nodes_status).map(([nodeId, nodeInfo]) => (
                      <div key={nodeId} className="space-y-1">
                        <div className="flex items-center justify-between text-xs font-medium">
                          <span className="text-[#112D4E] dark:text-[#DBE2EF] truncate max-w-[200px]">
                            {nodeInfo.name || nodeId.substring(0, 8)}...
                          </span>
                          <div className="flex items-center gap-1.5">
                            {getStatusIcon(nodeInfo.status)}
                            <span className={`text-xs font-bold ${getStatusColor(nodeInfo.status)}`}>
                              {getStatusText(nodeInfo.status)}
                            </span>
                          </div>
                        </div>
                        {nodeInfo.error_message && (
                          <p className="text-xs text-rose-600 dark:text-rose-400 font-medium">
                            {nodeInfo.error_message}
                          </p>
                        )}
                      </div>
                    ))}
                    {nodeCount === 0 && (
                      <span className="text-xs text-[#112D4E]/50 dark:text-[#DBE2EF]/50">No active nodes</span>
                    )}
                  </div>
                </div>

                <div className="p-4 bg-[#F9F7F7] dark:bg-[#142850]/50 rounded-xl border border-[#DBE2EF] dark:border-[#142850]">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-[#112D4E]/70 dark:text-[#DBE2EF]/70 mb-3">
                    Server Status
                  </h3>
                  <div className="space-y-2.5">
                    {serverCount === 0 ? (
                      <span className="text-xs text-[#112D4E]/50 dark:text-[#DBE2EF]/50">No active servers</span>
                    ) : (
                      Object.entries(coreHealth.servers_status).map(([serverId, serverInfo]) => (
                        <div key={serverId} className="space-y-1">
                          <div className="flex items-center justify-between text-xs font-medium">
                            <span className="text-[#112D4E] dark:text-[#DBE2EF] truncate max-w-[200px]">
                              {serverInfo.name || serverId.substring(0, 8)}...
                            </span>
                            <div className="flex items-center gap-1.5">
                              {getStatusIcon(serverInfo.status)}
                              <span className={`text-xs font-bold ${getStatusColor(serverInfo.status)}`}>
                                {getStatusText(serverInfo.status)}
                              </span>
                            </div>
                          </div>
                          {serverInfo.error_message && (
                            <p className="text-xs text-rose-600 dark:text-rose-400 font-medium">
                              {serverInfo.error_message}
                            </p>
                          )}
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </div>

              <div className="border-t border-[#DBE2EF] dark:border-[#142850] pt-4 space-y-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Clock className="w-4 h-4 text-[#112D4E]/60 dark:text-[#DBE2EF]/60" />
                    <h3 className="text-xs font-bold uppercase tracking-wider text-[#112D4E]/70 dark:text-[#DBE2EF]/70">
                      Auto Reset Timer
                    </h3>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      checked={config?.enabled || false}
                      onChange={(e) => handleConfigUpdate(coreHealth.core, { enabled: e.target.checked })}
                      disabled={updating === coreHealth.core}
                      className="sr-only peer"
                    />
                    <div className="w-11 h-6 bg-[#DBE2EF] peer-focus:outline-none rounded-full peer dark:bg-[#142850] peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-[#3F72AF] dark:peer-checked:bg-[#00A8CC]"></div>
                  </label>
                </div>

                {config?.enabled && (
                  <div className="flex items-center gap-3">
                    <label className="text-xs font-semibold text-[#112D4E]/70 dark:text-[#DBE2EF]/70">
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
                      className="w-24 px-3 py-1.5 text-xs font-bold border border-[#DBE2EF] dark:border-[#142850] rounded-xl bg-[#F9F7F7] dark:bg-[#142850] text-[#112D4E] dark:text-[#F9F7F7] focus:outline-none focus:ring-2 focus:ring-[#3F72AF] dark:focus:ring-[#00A8CC]"
                    />
                  </div>
                )}

                <div className="flex items-center justify-between pt-2">
                  <button
                    onClick={() => handleReset(coreHealth.core)}
                    disabled={updating === coreHealth.core}
                    className="flex items-center gap-2 px-4 py-2.5 bg-[#3F72AF] hover:bg-[#3F72AF]/90 dark:bg-gradient-to-r dark:from-[#0C7B93] dark:to-[#00A8CC] text-white rounded-xl transition-all font-bold text-xs shadow-md shadow-[#3F72AF]/20 dark:shadow-[#00A8CC]/20 disabled:opacity-50 disabled:cursor-not-allowed hover:scale-[1.02] active:scale-[0.98]"
                  >
                    {updating === coreHealth.core ? (
                      <>
                        <div className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
                        <span>Resetting...</span>
                      </>
                    ) : (
                      <>
                        <RefreshCw className="w-3.5 h-3.5" />
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

