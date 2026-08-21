import { useEffect, useState } from 'react'
import { Plus, Trash2, Edit2, RotateCw, CheckCircle2, XCircle, Clock, Loader2, X, Network } from 'lucide-react'
import api from '../api/client'
import { parseAddressPort, formatAddressPort } from '../utils/addressUtils'
import { useLanguage } from '../contexts/LanguageContext'
import { useToast } from '../contexts/ToastContext'
import { EmptyState } from '../components/EmptyState'

// ─── Reapply Progress Types ────────────────────────────────────────────────
type ReapplyStatus = 'pending' | 'running' | 'success' | 'error'

interface TunnelReapplyState {
  id: string
  name: string
  status: ReapplyStatus
  error?: string
}

interface Tunnel {
  id: string
  name: string
  core: string
  type: string
  node_id: string
  spec: Record<string, any>
  status: string
  error_message?: string | null
  revision: number
  created_at: string
  updated_at: string
}

type BackhaulTransport = 'tcp' | 'udp' | 'ws' | 'wsmux' | 'tcpmux'

interface BackhaulFormState {
  transport: BackhaulTransport
  control_port: string
  public_port: string
  listen_ip: string
  public_host: string
  remote_addr: string
  target_host: string
  target_port: string
  token: string
  accept_udp: boolean
}

interface BackhaulAdvancedServerState {
  keepalive_period: string
  heartbeat: string
  channel_size: string
  mux_con: string
  log_level: string
  nodelay: boolean
  skip_optz: boolean
  tls_cert: string
  tls_key: string
  sniffer: boolean
  web_port: string
  proxy_protocol: boolean
}

interface BackhaulAdvancedClientState {
  connection_pool: string
  retry_interval: string
  dial_timeout: string
  keepalive_period: string
  log_level: string
  nodelay: boolean
  aggressive_pool: boolean
  edge_ip: string
  skip_optz: boolean
}

interface BackhaulAdvancedState {
  server: BackhaulAdvancedServerState
  client: BackhaulAdvancedClientState
  customPorts: string
}

const createDefaultBackhaulState = (): BackhaulFormState => ({
  transport: 'tcp',
  control_port: '3080',
  public_port: '443',
  listen_ip: '0.0.0.0',
  public_host: '',
  remote_addr: '',
  target_host: '127.0.0.1',
  target_port: '8080',
  token: '',
  accept_udp: false,
})

const createDefaultBackhaulAdvancedState = (): BackhaulAdvancedState => ({
  server: {
    keepalive_period: '75',
    heartbeat: '40',
    channel_size: '2048',
    mux_con: '8',
    log_level: 'info',
    nodelay: true,
    skip_optz: false,
    tls_cert: '',
    tls_key: '',
    sniffer: false,
    web_port: '',
    proxy_protocol: false,
  },
  client: {
    connection_pool: '4',
    retry_interval: '3',
    dial_timeout: '10',
    keepalive_period: '75',
    log_level: 'info',
    nodelay: true,
    aggressive_pool: false,
    edge_ip: '',
    skip_optz: false,
  },
  customPorts: '',
})

const numericServerKeys = new Set([
  'keepalive_period',
  'heartbeat',
  'channel_size',
  'mux_con',
  'web_port',
])
const booleanServerKeys = new Set(['nodelay', 'skip_optz', 'sniffer', 'proxy_protocol'])
const stringServerKeys = new Set(['log_level', 'tls_cert', 'tls_key', 'sniffer_log'])

const numericClientKeys = new Set(['connection_pool', 'retry_interval', 'dial_timeout', 'keepalive_period'])
const booleanClientKeys = new Set(['nodelay', 'aggressive_pool', 'skip_optz'])
const stringClientKeys = new Set(['log_level', 'edge_ip'])

interface BackhaulDisplayInfo {
  controlPort: string
  publicPort: string
  target: string
}

const getBackhaulDisplayInfo = (spec: Record<string, any> | undefined): BackhaulDisplayInfo => {
  if (!spec) {
    return { controlPort: 'N/A', publicPort: 'N/A', target: 'N/A' }
  }

  const controlPort =
    spec.control_port ||
    (typeof spec.bind_addr === 'string' && spec.bind_addr.includes(':') ? spec.bind_addr.split(':').pop() : undefined) ||
    (typeof spec.remote_addr === 'string' && spec.remote_addr.includes(':') ? spec.remote_addr.split(':').pop() : undefined) ||
    'N/A'

  const publicPort =
    spec.public_port ||
    spec.listen_port ||
    (Array.isArray(spec.ports) && spec.ports.length > 0
      ? (() => {
          const [first] = spec.ports
          if (typeof first !== 'string') return undefined
          const [left] = first.split('=')
          const parts = left.split(':')
          return parts.pop()
        })()
      : undefined) ||
    'N/A'

  const target =
    spec.target_addr ||
    (Array.isArray(spec.ports) && spec.ports.length > 0
      ? (() => {
          const [first] = spec.ports
          if (typeof first !== 'string') return undefined
          const segments = first.split('=')
          return segments.length > 1 ? segments[1] : undefined
        })()
      : undefined) ||
    'N/A'

  return {
    controlPort: controlPort?.toString() || 'N/A',
    publicPort: publicPort?.toString() || 'N/A',
    target: target?.toString() || 'N/A',
  }
}

const Tunnels = () => {
  const { t } = useLanguage()
  const { showToast, showConfirm } = useToast()
  const [tunnels, setTunnels] = useState<Tunnel[]>([])
  const [nodes, setNodes] = useState<any[]>([])
  const [servers, setServers] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [showAddModal, setShowAddModal] = useState(false)
  const [editingTunnel, setEditingTunnel] = useState<Tunnel | null>(null)
  // Per-tunnel reapply loading (stores the tunnel id being reapplied)
  const [reapplyingTunnelId, setReapplyingTunnelId] = useState<string | null>(null)
  // Reapply All progress modal
  const [reapplyAllProgress, setReapplyAllProgress] = useState<TunnelReapplyState[] | null>(null)
  const [reapplyAllDone, setReapplyAllDone] = useState(false)
  const [showConfirmReapplyAll, setShowConfirmReapplyAll] = useState(false)

  useEffect(() => {
    fetchData()
    const params = new URLSearchParams(window.location.search)
    if (params.get('create') === 'true') {
      setShowAddModal(true)
      window.history.replaceState({}, '', '/tunnels')
    }
    
  }, [])

  const fetchData = async () => {
    try {
      const [tunnelsRes, nodesRes] = await Promise.all([
        api.get('/tunnels'),
        api.get('/nodes'),
      ])
      setTunnels(tunnelsRes.data)
      // Filter nodes: iran nodes and foreign servers
      const iranNodes = nodesRes.data.filter((node: any) => 
        node.metadata?.role === 'iran' || !node.metadata?.role  // Default to iran for backward compatibility
      )
      const foreignServers = nodesRes.data.filter((node: any) => 
        node.metadata?.role === 'foreign'
      )
      setNodes(iranNodes)
      setServers(foreignServers)
    } catch (error) {
      console.error('Failed to fetch data:', error)
    } finally {
      setLoading(false)
    }
  }

  const deleteTunnel = async (id: string) => {
    const confirmed = await showConfirm({
      title: 'Delete Tunnel',
      message: 'Are you sure you want to delete this tunnel? The connection will be permanently stopped.',
      variant: 'danger',
      confirmText: 'Delete'
    })
    if (!confirmed) return
    
    try {
      await api.delete(`/tunnels/${id}`)
      showToast('success', 'Tunnel Deleted', 'Tunnel was deleted successfully')
      fetchData()
    } catch (error) {
      console.error('Failed to delete tunnel:', error)
      showToast('error', 'Error', 'Failed to delete tunnel')
    }
  }

  // ─── Reapply single tunnel (with per-card loading overlay) ─────────────
  const reapplyTunnel = async (tunnel: Tunnel) => {
    setReapplyingTunnelId(tunnel.id)
    try {
      const response = await api.post(`/tunnels/${tunnel.id}/apply`)
      const isSuccess = response.data && (response.data.status === 'success' || response.data.status === 'applied' || !response.data.status)
      if (isSuccess) {
        showToast('success', 'Tunnel Reapplied', `${tunnel.name} reapplied successfully`)
        fetchData()
      } else {
        throw new Error(response.data?.message || 'Failed to reapply tunnel')
      }
    } catch (error: any) {
      console.error('Failed to reapply tunnel:', error)
      const errorMessage = error.response?.data?.detail || error.message || 'Failed to reapply tunnel'
      showToast('error', 'Reapply Failed', errorMessage)
    } finally {
      setReapplyingTunnelId(null)
    }
  }

  // ─── Reapply All — opens progress modal, applies sequentially ──────────
  const handleReapplyAll = () => {
    setShowConfirmReapplyAll(true)
  }

  const startReapplyAll = async () => {
    setShowConfirmReapplyAll(false)
    // Initialize progress state for each tunnel
    const initial: TunnelReapplyState[] = tunnels.map(t => ({
      id: t.id,
      name: t.name,
      status: 'pending',
    }))
    setReapplyAllProgress(initial)
    setReapplyAllDone(false)

    // Apply each tunnel sequentially so progress is visible
    let current = [...initial]
    for (let i = 0; i < tunnels.length; i++) {
      const tunnel = tunnels[i]
      // Mark as running
      current = current.map((item, idx) =>
        idx === i ? { ...item, status: 'running' } : item
      )
      setReapplyAllProgress([...current])

      try {
        const response = await api.post(`/tunnels/${tunnel.id}/apply`)
        const isSuccess = response.data && (response.data.status === 'success' || response.data.status === 'applied' || !response.data.status)
        if (isSuccess) {
          current = current.map((item, idx) =>
            idx === i ? { ...item, status: 'success' } : item
          )
        } else {
          throw new Error(response.data?.message || 'Failed')
        }
      } catch (error: any) {
        const errorMsg = error.response?.data?.detail || error.message || 'Failed to apply'
        current = current.map((item, idx) =>
          idx === i ? { ...item, status: 'error', error: errorMsg } : item
        )
      }
      setReapplyAllProgress([...current])
    }

    setReapplyAllDone(true)
    fetchData()
  }

  const closeReapplyAllModal = () => {
    setReapplyAllProgress(null)
    setReapplyAllDone(false)
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center">
          <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-[#3F72AF] dark:border-[#00A8CC] mb-4"></div>
          <p className="text-sm font-medium text-[#112D4E]/70 dark:text-[#DBE2EF]/80">{t.tunnels.loadingTunnels}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="w-full max-w-7xl mx-auto space-y-8">
      {/* ── Header ──────────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-black tracking-tight text-[#112D4E] dark:text-[#F9F7F7] mb-2">{t.tunnels.title}</h1>
          <p className="text-sm font-medium text-[#112D4E]/70 dark:text-[#DBE2EF]/80">{t.tunnels.subtitle}</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={handleReapplyAll}
            disabled={!!reapplyAllProgress && !reapplyAllDone}
            className="px-4 py-2.5 bg-emerald-600 hover:bg-emerald-700 dark:bg-emerald-600 dark:hover:bg-emerald-500 text-white rounded-xl transition-all duration-200 font-semibold text-sm shadow-sm hover:shadow-md flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <RotateCw size={18} className={!!reapplyAllProgress && !reapplyAllDone ? 'animate-spin' : ''} />
            <span>{t.tunnels.reapplyAll}</span>
          </button>
          <button
            onClick={() => setShowAddModal(true)}
            className="px-5 py-2.5 bg-[#3F72AF] hover:bg-[#3F72AF]/90 dark:bg-gradient-to-r dark:from-[#0C7B93] dark:to-[#00A8CC] text-white rounded-xl transition-all duration-200 font-bold text-sm shadow-md shadow-[#3F72AF]/20 dark:shadow-[#00A8CC]/20 flex items-center gap-2 hover:scale-[1.02] active:scale-[0.98]"
          >
            <Plus size={19} />
            <span>{t.tunnels.createTunnel}</span>
          </button>
        </div>
      </div>

      {/* ── Tunnel Cards ────────────────────────────────────── */}
      <div className="space-y-4">
        {tunnels.length === 0 && (
          <EmptyState
            icon={<Network size={32} />}
            title="No tunnels yet"
            description="Create your first tunnel to get started forwarding traffic between your Iran and foreign nodes."
            action={{ label: 'Create Tunnel', onClick: () => setShowAddModal(true) }}
          />
        )}
        {tunnels.map((tunnel) => {
          const isReapplying = reapplyingTunnelId === tunnel.id

          // Extract ports from spec
          const getPorts = (): string => {
            if (tunnel.spec?.ports) {
              if (Array.isArray(tunnel.spec.ports)) {
                if (tunnel.core === 'backhaul' && typeof tunnel.spec.ports[0] === 'string' && tunnel.spec.ports[0].includes('=')) {
                  return tunnel.spec.ports.map(p => {
                    const portPart = p.split('=')[0]
                    const port = portPart.includes(':') ? portPart.split(':')[1] : portPart
                    return port
                  }).join(', ')
                }
                return tunnel.spec.ports.map(p => typeof p === 'object' && p.local ? p.local : p).join(', ')
              } else if (typeof tunnel.spec.ports === 'string') {
                return tunnel.spec.ports
              }
            }
            const port = tunnel.spec?.listen_port || tunnel.spec?.remote_port
            return port ? port.toString() : 'N/A'
          }

          const coreBadge = {
            bg: 'bg-[#DBE2EF]/60 dark:bg-[#142850]',
            text: 'text-[#3F72AF] dark:text-[#00A8CC]',
            border: 'border-[#DBE2EF] dark:border-[#0C7B93]/40'
          }

          const ports = getPorts()
          const iranNode = nodes.find(n => n.id === tunnel.iran_node_id || n.id === tunnel.node_id)
          const foreignServer = servers.find(s => s.id === tunnel.foreign_node_id)

          return (
            <div
              key={tunnel.id}
              className={`relative bg-white dark:bg-[#27496D] rounded-2xl border transition-all duration-300 ${
                isReapplying
                  ? 'border-emerald-500 shadow-lg shadow-emerald-500/20'
                  : 'border-[#DBE2EF] dark:border-[#142850] hover:shadow-xl dark:hover:shadow-black/30'
              }`}
            >
              {/* ── Per-card loading overlay ── */}
              {isReapplying && (
                <div className="absolute inset-0 bg-white/80 dark:bg-[#27496D]/85 rounded-2xl z-10 flex items-center justify-center backdrop-blur-sm">
                  <div className="flex items-center gap-3 px-5 py-2.5 bg-white dark:bg-[#142850] rounded-xl shadow-xl border border-emerald-500/30">
                    <Loader2 size={18} className="animate-spin text-emerald-600 dark:text-emerald-400" />
                    <span className="text-xs font-bold text-emerald-700 dark:text-emerald-300">Applying Tunnel...</span>
                  </div>
                </div>
              )}

              <div className="p-5 sm:p-6">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex items-start gap-4 flex-1 min-w-0">
                    {/* Status Badge */}
                    <span
                      className={`px-3 py-1 rounded-full text-xs font-bold whitespace-nowrap shrink-0 ${
                        tunnel.status === 'active'
                          ? 'bg-emerald-500/10 dark:bg-emerald-500/20 text-emerald-800 dark:text-emerald-300 border border-emerald-500/30'
                          : tunnel.status === 'error'
                          ? 'bg-rose-500/10 dark:bg-rose-500/20 text-rose-800 dark:text-rose-300 border border-rose-500/30'
                          : 'bg-[#DBE2EF]/60 dark:bg-[#142850] text-[#112D4E] dark:text-[#DBE2EF] border border-[#DBE2EF] dark:border-[#0C7B93]/30'
                      }`}
                    >
                      {tunnel.status}
                    </span>

                    {/* Tunnel Details */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-2 flex-wrap">
                        <h3 className="text-base font-bold text-[#112D4E] dark:text-[#F9F7F7] truncate">{tunnel.name}</h3>
                        <span className={`px-2.5 py-0.5 rounded-lg text-xs font-bold uppercase tracking-wider border ${coreBadge.bg} ${coreBadge.text} ${coreBadge.border}`}>
                          {tunnel.core}
                        </span>
                        {tunnel.mode && (
                          <span className="text-xs text-[#112D4E]/60 dark:text-[#DBE2EF]/60 font-medium capitalize">
                            ({tunnel.mode})
                          </span>
                        )}
                      </div>

                      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3 text-xs text-[#112D4E]/70 dark:text-[#DBE2EF]/80 mt-3">
                        <div className="flex items-center gap-1.5">
                          <span className="font-bold text-[#112D4E] dark:text-[#F9F7F7]">Ports:</span>
                          <span className="font-mono text-[#3F72AF] dark:text-[#00A8CC] font-bold">{ports}</span>
                        </div>
                        {(() => {
                          let corePort: string | undefined
                          if (tunnel.core === 'rathole') {
                            corePort = tunnel.spec?.control_port || tunnel.spec?.server_port
                          } else if (tunnel.core === 'backhaul') {
                            corePort = tunnel.spec?.control_port || tunnel.spec?.public_port || '3080'
                          } else if (tunnel.core === 'frp') {
                            corePort = tunnel.spec?.bind_port || '7000'
                          }
                          return corePort ? (
                            <div className="flex items-center gap-1.5">
                              <span className="font-bold text-[#112D4E] dark:text-[#F9F7F7]">Core Port:</span>
                              <span className="text-[#3F72AF] dark:text-[#00A8CC] font-mono font-bold">{corePort}</span>
                            </div>
                          ) : null
                        })()}
                        {iranNode && (
                          <div className="flex items-center gap-1.5">
                            <span className="font-bold text-[#112D4E] dark:text-[#F9F7F7]">Node:</span>
                            <span className="text-[#112D4E]/80 dark:text-[#DBE2EF]">{iranNode.name || iranNode.id.substring(0, 8)}</span>
                          </div>
                        )}
                        {foreignServer && (
                          <div className="flex items-center gap-1.5">
                            <span className="font-bold text-[#112D4E] dark:text-[#F9F7F7]">Server:</span>
                            <span className="text-[#112D4E]/80 dark:text-[#DBE2EF]">{foreignServer.name || foreignServer.id.substring(0, 8)}</span>
                          </div>
                        )}
                      </div>

                      {/* Error Message */}
                      {tunnel.status === 'error' && tunnel.error_message && (
                        <div className="mt-2.5 text-xs text-rose-600 dark:text-rose-400 font-medium">
                          {tunnel.error_message}
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Action Buttons */}
                  <div className="flex gap-1.5 shrink-0">
                    <button
                      onClick={() => reapplyTunnel(tunnel)}
                      disabled={isReapplying || !!reapplyingTunnelId}
                      className="p-2 text-emerald-600 hover:bg-emerald-500/10 dark:text-emerald-400 rounded-xl transition-colors disabled:opacity-40 disabled:cursor-not-allowed min-w-[38px] min-h-[38px] flex items-center justify-center"
                      title="Reapply tunnel"
                      aria-label="Reapply tunnel"
                    >
                      {isReapplying ? (
                        <Loader2 size={18} className="animate-spin" />
                      ) : (
                        <RotateCw size={18} />
                      )}
                    </button>
                    <button
                      onClick={() => setEditingTunnel(tunnel)}
                      disabled={isReapplying}
                      className="p-2 text-[#3F72AF] hover:bg-[#3F72AF]/10 dark:text-[#00A8CC] rounded-xl transition-colors disabled:opacity-40 min-w-[38px] min-h-[38px] flex items-center justify-center"
                      title="Edit tunnel"
                      aria-label="Edit tunnel"
                    >
                      <Edit2 size={18} />
                    </button>
                    <button
                      onClick={() => deleteTunnel(tunnel.id)}
                      disabled={isReapplying}
                      className="p-2 text-rose-600 hover:bg-rose-500/10 dark:text-rose-400 rounded-xl transition-colors disabled:opacity-40 min-w-[38px] min-h-[38px] flex items-center justify-center"
                      title="Delete tunnel"
                      aria-label="Delete tunnel"
                    >
                      <Trash2 size={18} />
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* ── Reapply All — Confirm Dialog ─────────────────────── */}
      {showConfirmReapplyAll && (
        <div className="fixed inset-0 bg-[#112D4E]/60 dark:bg-black/70 backdrop-blur-md flex items-center justify-center z-[100] p-4">
          <div className="bg-white dark:bg-[#27496D] rounded-2xl shadow-2xl border border-[#DBE2EF] dark:border-[#142850] p-6 w-full max-w-sm animate-in fade-in zoom-in-95 duration-200">
            <h3 className="text-lg font-bold text-[#112D4E] dark:text-[#F9F7F7] mb-2">
              {t.tunnels.reapplyAll}
            </h3>
            <p className="text-xs font-medium text-[#112D4E]/70 dark:text-[#DBE2EF]/80 mb-6">
              {t.tunnels.confirmReapplyAll || 'Are you sure you want to reapply all tunnels? This will restart all active connections.'}
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setShowConfirmReapplyAll(false)}
                className="px-4 py-2 text-xs font-semibold text-[#112D4E] dark:text-[#DBE2EF] bg-[#DBE2EF]/60 dark:bg-[#142850] hover:bg-[#DBE2EF] dark:hover:bg-[#142850]/80 rounded-xl transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={startReapplyAll}
                className="px-4 py-2 text-xs font-bold text-white bg-emerald-600 hover:bg-emerald-700 rounded-xl shadow-md transition-colors"
              >
                Yes, Reapply All
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Reapply All — Progress Modal ─────────────────────── */}
      {reapplyAllProgress && (
        <div className="fixed inset-0 bg-[#112D4E]/60 dark:bg-black/70 backdrop-blur-md flex items-center justify-center z-[100] p-4">
          <div className="bg-white dark:bg-[#27496D] rounded-2xl shadow-2xl border border-[#DBE2EF] dark:border-[#142850] w-full max-w-md max-h-[85vh] flex flex-col animate-in fade-in zoom-in-95 duration-200">
            {/* Modal header */}
            <div className="flex items-center justify-between p-5 border-b border-[#DBE2EF] dark:border-[#142850]">
              <div className="flex items-center gap-3">
                <div className={`p-2.5 rounded-xl flex items-center justify-center ${
                  reapplyAllDone 
                    ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400' 
                    : 'bg-[#3F72AF]/15 text-[#3F72AF] dark:text-[#00A8CC]'
                }`}>
                  {reapplyAllDone ? (
                    <CheckCircle2 size={22} />
                  ) : (
                    <Loader2 size={22} className="animate-spin" />
                  )}
                </div>
                <div>
                  <h3 className="text-base font-bold text-[#112D4E] dark:text-[#F9F7F7]">
                    {reapplyAllDone ? 'Reapply Complete' : 'Applying Tunnels...'}
                  </h3>
                  <p className="text-xs text-[#112D4E]/60 dark:text-[#DBE2EF]/70 mt-0.5 font-medium">
                    {reapplyAllProgress.filter(i => i.status === 'success' || i.status === 'error').length} of {reapplyAllProgress.length} tunnels processed
                  </p>
                </div>
              </div>
              {reapplyAllDone && (
                <button
                  onClick={closeReapplyAllModal}
                  className="p-1.5 text-[#112D4E]/60 hover:text-[#112D4E] dark:text-[#DBE2EF]/60 dark:hover:text-white rounded-lg transition-colors"
                  aria-label="Close modal"
                >
                  <X size={18} />
                </button>
              )}
            </div>

            {/* Progress bar */}
            <div className="px-5 pt-4">
              <div className="w-full bg-[#DBE2EF] dark:bg-[#142850] rounded-full h-2 overflow-hidden shadow-inner">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-[#3F72AF] via-[#00A8CC] to-emerald-500 transition-all duration-300 ease-out"
                  style={{
                    width: `${(reapplyAllProgress.filter(i => i.status === 'success' || i.status === 'error').length / reapplyAllProgress.length) * 100}%`
                  }}
                />
              </div>
            </div>

            {/* Tunnel list */}
            <div className="flex-1 overflow-y-auto p-5 space-y-2 mt-1">
              {reapplyAllProgress.map((item) => (
                <div
                  key={item.id}
                  className={`flex items-center gap-3 px-3.5 py-3 rounded-xl border transition-all duration-200 ${
                    item.status === 'running'
                      ? 'bg-[#3F72AF]/10 dark:bg-[#00A8CC]/10 border-[#3F72AF]/30 dark:border-[#00A8CC]/30 shadow-sm'
                      : item.status === 'success'
                      ? 'bg-emerald-500/10 border-emerald-500/30'
                      : item.status === 'error'
                      ? 'bg-rose-500/10 border-rose-500/30'
                      : 'bg-[#F9F7F7] dark:bg-[#142850]/50 border-[#DBE2EF] dark:border-[#142850]'
                  }`}
                >
                  {/* Status icon */}
                  <div className="shrink-0">
                    {item.status === 'pending' && <Clock size={18} className="text-[#112D4E]/40 dark:text-[#DBE2EF]/40" />}
                    {item.status === 'running' && <Loader2 size={18} className="animate-spin text-[#3F72AF] dark:text-[#00A8CC]" />}
                    {item.status === 'success' && <CheckCircle2 size={18} className="text-emerald-600 dark:text-emerald-400" />}
                    {item.status === 'error' && <XCircle size={18} className="text-rose-600 dark:text-rose-400" />}
                  </div>

                  {/* Tunnel info */}
                  <div className="flex-1 min-w-0">
                    <p className={`text-sm font-bold truncate ${
                      item.status === 'running' ? 'text-[#3F72AF] dark:text-[#00A8CC]' :
                      item.status === 'success' ? 'text-emerald-700 dark:text-emerald-300' :
                      item.status === 'error' ? 'text-rose-700 dark:text-rose-300' :
                      'text-[#112D4E] dark:text-[#F9F7F7]'
                    }`}>
                      {item.name}
                    </p>
                    {item.status === 'error' && item.error && (
                      <p className="text-xs text-rose-600 dark:text-rose-400 truncate mt-0.5" title={item.error}>{item.error}</p>
                    )}
                    {item.status === 'running' && (
                      <p className="text-xs text-[#3F72AF] dark:text-[#00A8CC] mt-0.5 animate-pulse">Applying configuration...</p>
                    )}
                  </div>

                  {/* Status badge */}
                  <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full shrink-0 tracking-wide ${
                    item.status === 'pending' ? 'bg-[#DBE2EF]/60 dark:bg-[#142850] text-[#112D4E]/60 dark:text-[#DBE2EF]/60' :
                    item.status === 'running' ? 'bg-[#3F72AF]/20 text-[#3F72AF] dark:text-[#00A8CC]' :
                    item.status === 'success' ? 'bg-emerald-500/20 text-emerald-700 dark:text-emerald-300' :
                    'bg-rose-500/20 text-rose-700 dark:text-rose-300'
                  }`}>
                    {item.status === 'pending' ? 'Waiting' :
                     item.status === 'running' ? 'Running' :
                     item.status === 'success' ? 'Done' : 'Failed'}
                  </span>
                </div>
              ))}
            </div>

            {/* Footer */}
            {reapplyAllDone && (
              <div className="p-5 border-t border-[#DBE2EF] dark:border-[#142850] bg-[#F9F7F7]/60 dark:bg-[#142850]/40 rounded-b-2xl">
                <div className="flex items-center justify-between text-xs font-bold text-[#112D4E]/70 dark:text-[#DBE2EF]/70 mb-3">
                  <span className="flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400">
                    <CheckCircle2 size={15} />
                    {reapplyAllProgress.filter(i => i.status === 'success').length} succeeded
                  </span>
                  {reapplyAllProgress.filter(i => i.status === 'error').length > 0 && (
                    <span className="flex items-center gap-1.5 text-rose-600 dark:text-rose-400">
                      <XCircle size={15} />
                      {reapplyAllProgress.filter(i => i.status === 'error').length} failed
                    </span>
                  )}
                </div>
                <button
                  onClick={closeReapplyAllModal}
                  className="w-full px-5 py-2.5 bg-[#3F72AF] hover:bg-[#3F72AF]/90 dark:bg-gradient-to-r dark:from-[#0C7B93] dark:to-[#00A8CC] text-white rounded-xl transition-all font-bold text-sm shadow-md"
                >
                  Done
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {showAddModal && (
        <AddTunnelModal
          nodes={nodes}
          servers={servers}
          onClose={() => setShowAddModal(false)}
          onSuccess={() => {
            setShowAddModal(false)
            fetchData()
          }}
        />
      )}

      {editingTunnel && (
        <EditTunnelModal
          tunnel={editingTunnel}
          nodes={nodes}
          onClose={() => setEditingTunnel(null)}
          onSuccess={() => {
            setEditingTunnel(null)
            fetchData()
          }}
        />
      )}
    </div>
  )
}

interface EditTunnelModalProps {
  tunnel: Tunnel
  nodes: any[]
  onClose: () => void
  onSuccess: () => void
}

const EditTunnelModal = ({ tunnel, onClose, onSuccess }: EditTunnelModalProps) => {
  const { t } = useLanguage()
  const { showToast } = useToast()
  const forwardToParsed = tunnel.spec?.forward_to ? parseAddressPort(tunnel.spec.forward_to) : null
  const remoteIp = tunnel.spec?.remote_ip || forwardToParsed?.host || '127.0.0.1'
  const remotePort = tunnel.spec?.remote_port || forwardToParsed?.port || 8080
  
  // Parse ports from spec
  const parsePortsFromSpec = (spec: Record<string, any>): string => {
    if (spec?.ports) {
      if (Array.isArray(spec.ports)) {
        // For Backhaul, ports are in format "8080=127.0.0.1:8080" or "0.0.0.0:8080=127.0.0.1:8080"
        // Extract just the port number (first number before = or after :)
        return spec.ports.map(p => {
          if (typeof p === 'object' && p.local) {
            return p.local.toString()
          } else if (typeof p === 'string') {
            // Handle Backhaul format: "8080=127.0.0.1:8080" or "0.0.0.0:8080=127.0.0.1:8080"
            if (p.includes('=')) {
              const leftPart = p.split('=')[0]
              // Extract port from left part (could be "8080" or "0.0.0.0:8080")
              if (leftPart.includes(':')) {
                return leftPart.split(':')[1]
              }
              return leftPart
            }
            // If it's just a number, return as-is
            return p
          }
          return p.toString()
        }).join(',')
      } else if (typeof spec.ports === 'string') {
        return spec.ports
      }
    }
    // Fallback to single port
    return (spec?.listen_port || spec?.remote_port || 8080).toString()
  }
  
  const [formData, setFormData] = useState({
    name: tunnel.name,
    ports: parsePortsFromSpec(tunnel.spec || {}),
    remote_ip: remoteIp,
    rathole_remote_addr: tunnel.spec?.remote_addr ? (() => {
      const parsed = parseAddressPort(tunnel.spec.remote_addr)
      return parsed.port?.toString() || ''
    })() : '',
    chisel_control_port: tunnel.spec?.control_port ? tunnel.spec.control_port.toString() : '',
    frp_bind_port: tunnel.spec?.bind_port ? tunnel.spec.bind_port.toString() : '7000',
    frp_token: tunnel.spec?.token || '',
    frp_local_ip: tunnel.spec?.local_ip || '127.0.0.1',
    frp_transport: tunnel.spec?.transport_type || tunnel.spec?.transport || 'tcp',
    frp_security: tunnel.spec?.security_type || 'tls',
    frp_sni: tunnel.spec?.custom_sni || tunnel.spec?.stealth_domain || '',
    frp_encryption: tunnel.spec?.use_encryption !== false,
    frp_compression: tunnel.spec?.use_compression !== false,
    node_ipv6: tunnel.spec?.node_ipv6 || '',
    cdn_mode: tunnel.cdn_mode || false,
    gaming_mode: tunnel.gaming_mode || false,
    custom_host: tunnel.custom_host || '',
    custom_sni: tunnel.custom_sni || '',
    ws_path: tunnel.ws_path || '',
    is_reverse: tunnel.is_reverse || false,
    stealth_domain: tunnel.stealth_domain || '',
    transport_type: tunnel.transport_type || 'tcp',
    security_type: tunnel.security_type || 'none',
    failover_ips: tunnel.failover_ips && Array.isArray(tunnel.failover_ips) ? tunnel.failover_ips.join('\n') : '',
    rate_limit_mbps: tunnel.rate_limit_mbps ? tunnel.rate_limit_mbps.toString() : '',
    allowed_ips: tunnel.allowed_ips && Array.isArray(tunnel.allowed_ips) ? tunnel.allowed_ips.join('\n') : '',
    rate_limit_enabled: !!tunnel.rate_limit_mbps,
    allowed_ips_enabled: !!(tunnel.allowed_ips && tunnel.allowed_ips.length > 0),
  })
  const parsedBackhaul = parseBackhaulSpec(tunnel.spec, tunnel.type)
  const [backhaulState, setBackhaulState] = useState<BackhaulFormState>(parsedBackhaul.state)
  const [backhaulAdvanced, setBackhaulAdvanced] = useState<BackhaulAdvancedState>(parsedBackhaul.advanced)
  const [showBackhaulAdvanced, setShowBackhaulAdvanced] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      let updatedSpec = { ...tunnel.spec }
      
      const useV4ToV6 = updatedSpec.use_ipv6 || false
      
      // Parse comma-separated ports and ranges
      const parsePortsAndRanges = (portsStr: string): { ports: number[], port_ranges: string[] } => {
        const ports: number[] = []
        const port_ranges: string[] = []
        
        portsStr.split(',').forEach(p => {
          const trimmed = p.trim()
          if (!trimmed) return
          if (trimmed.includes('-')) {
            port_ranges.push(trimmed)
          } else {
            const num = parseInt(trimmed)
            if (!isNaN(num) && num > 0 && num <= 65535) {
              ports.push(num)
            }
          }
        })
        return { ports, port_ranges }
      }
      
      let ports: number[] = []
      let port_ranges: string[] = []
      
      if (tunnel.core === 'gost') {
        const parsed = parsePortsAndRanges(formData.ports)
        ports = parsed.ports
        port_ranges = parsed.port_ranges
        if (ports.length === 0 && port_ranges.length === 0) {
          showToast('warning', 'Invalid Ports', 'Please enter at least one valid port or port range')
          return
        }
      } else {
        ports = formData.ports
          .split(',')
          .map(p => p.trim())
          .filter(p => p)
          .map(p => parseInt(p))
          .filter(p => !isNaN(p) && p > 0 && p <= 65535)
          
        if (ports.length === 0) {
          showToast('warning', 'Invalid Port', 'Please enter at least one valid port')
          return
        }
      }
      
      if (tunnel.core === 'rathole') {
        if (formData.rathole_remote_addr) {
          const remoteHost = window.location.hostname
          const remotePort = formData.rathole_remote_addr.includes(':') 
            ? formData.rathole_remote_addr.split(':')[1] 
            : formData.rathole_remote_addr
          updatedSpec.remote_addr = `${remoteHost}:${remotePort || '23333'}`
        }
        if (formData.node_ipv6) {
          updatedSpec.node_ipv6 = formData.node_ipv6
        }
        updatedSpec.ports = ports
        updatedSpec.remote_port = ports[0]  // Keep for backward compatibility
        updatedSpec.listen_port = ports[0]  // Keep for backward compatibility
      } else if (tunnel.core === 'gost' && (tunnel.type === 'tcp' || tunnel.type === 'udp' || tunnel.type === 'grpc' || tunnel.type === 'tcpmux')) {
        const remoteIp = formData.remote_ip || '127.0.0.1'
        updatedSpec.remote_ip = remoteIp
        updatedSpec.ports = ports
        updatedSpec.port_ranges = port_ranges
        const fallbackPort = ports.length > 0 ? ports[0] : (port_ranges.length > 0 ? parseInt(port_ranges[0].split('-')[0]) : 8080)
        updatedSpec.remote_port = fallbackPort  // Keep for backward compatibility
        updatedSpec.listen_port = fallbackPort  // Keep for backward compatibility
      } else if (tunnel.core === 'chisel') {
        updatedSpec.ports = ports
        const firstPort = ports[0]
        updatedSpec.listen_port = firstPort
        updatedSpec.remote_port = firstPort
        const controlPort = formData.chisel_control_port 
          ? parseInt(formData.chisel_control_port.toString())
          : firstPort + 10000
        updatedSpec.control_port = controlPort
        if (formData.node_ipv6) {
          updatedSpec.node_ipv6 = formData.node_ipv6
        }
      } else if (tunnel.core === 'frp') {
        const bindPort = parseInt(formData.frp_bind_port) || 7000
        updatedSpec.bind_port = bindPort
        updatedSpec.ports = ports
        updatedSpec.listen_port = ports[0]  // Keep for backward compatibility
        updatedSpec.remote_port = ports[0]  // Keep for backward compatibility
        if (formData.frp_token) {
          updatedSpec.token = formData.frp_token
        } else {
          delete updatedSpec.token
        }
        updatedSpec.local_ip = formData.frp_local_ip || '127.0.0.1'
        updatedSpec.local_port = ports[0]  // Keep for backward compatibility
        updatedSpec.type = tunnel.type === 'udp' ? 'udp' : 'tcp'
        updatedSpec.transport_type = formData.frp_transport || 'tcp'
        updatedSpec.security_type = formData.frp_security || 'tls'
        updatedSpec.custom_sni = formData.frp_sni || ''
        updatedSpec.use_encryption = formData.frp_encryption
        updatedSpec.use_compression = formData.frp_compression
      } else if (tunnel.core === 'backhaul') {
        updatedSpec = buildBackhaulSpec(backhaulState, backhaulAdvanced, tunnel.type as BackhaulTransport)
        // Override ports if provided
        if (ports.length > 0) {
          const targetHost = updatedSpec.target_host || '127.0.0.1'
          updatedSpec.ports = ports.map(p => `${p}=${targetHost}:${p}`)
        }
      }

      if (tunnel.core === 'gost' && formData.ws_path && !formData.ws_path.startsWith('/')) {
        showToast('warning', 'Validation', 'WS Path must start with a slash (e.g., /graphql)')
        return
      }

      await api.put(`/tunnels/${tunnel.id}`, {
        name: formData.name,
        spec: updatedSpec,
        ...(tunnel.core === 'gost' && {
          cdn_mode: formData.cdn_mode,
          gaming_mode: formData.gaming_mode,
          custom_host: formData.custom_host,
          custom_sni: formData.custom_sni,
          ws_path: formData.ws_path,
          is_reverse: formData.is_reverse,
          stealth_domain: formData.stealth_domain || null,
          transport_type: formData.transport_type,
          security_type: formData.security_type,
          failover_ips: formData.failover_ips ? formData.failover_ips.split('\n').map(ip => ip.trim()).filter(ip => ip.length > 0) : null,
          rate_limit_mbps: formData.rate_limit_enabled && formData.rate_limit_mbps ? parseFloat(formData.rate_limit_mbps) : null,
          allowed_ips: formData.allowed_ips_enabled && formData.allowed_ips 
            ? formData.allowed_ips.split('\n').map(ip => ip.trim()).filter(ip => ip.length > 0)
            : null,
          port_ranges: port_ranges.length > 0 ? port_ranges : null
        }),
        node_id: formData.is_reverse ? formData.iran_node_id : formData.node_id,
        iran_node_id: formData.iran_node_id,
        foreign_node_id: formData.foreign_node_id
      })
      showToast('success', 'Tunnel Updated', `${formData.name} was updated successfully`)
      onSuccess()
    } catch (error) {
      console.error('Failed to update tunnel:', error)
      showToast('error', 'Error', 'Failed to update tunnel')
    }
  }

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[100] overflow-auto p-4">
      <div className="bg-white dark:bg-gray-800 rounded-lg p-6 w-full max-w-md max-h-[90vh] overflow-y-auto">
        <h2 className="text-xl font-bold text-gray-900 dark:text-white mb-4">Edit Tunnel</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              {t.tunnels.name}
            </label>
            <input
              type="text"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
              required
            />
          </div>
          {tunnel.core === 'gost' && (tunnel.type === 'tcp' || tunnel.type === 'udp' || tunnel.type === 'grpc' || tunnel.type === 'tcpmux') && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
              <div>
                <div className="flex items-center justify-between mb-1 h-5">
                  <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                    {t.tunnels.remoteIP}
                  </label>
                </div>
                <input
                  type="text"
                  value={formData.remote_ip}
                  onChange={(e) =>
                    setFormData({ ...formData, remote_ip: e.target.value || '127.0.0.1' })
                  }
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                  placeholder="127.0.0.1 or [2001:db8::1]"
                />
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  {t.tunnels.remoteIPDescription}
                </p>
              </div>
              <div>
                <div className="flex items-center justify-between mb-1 h-5">
                  <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                    Ports & Ranges
                  </label>
                </div>
                <input
                  type="text"
                  value={formData.ports}
                  onChange={(e) =>
                    setFormData({ ...formData, ports: e.target.value })
                  }
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                  placeholder="8080, 8081, 10000-20000"
                />
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  Ports or ranges (comma-separated, same for panel and target server)
                </p>
              </div>
            </div>
          )}
          
          {tunnel.core === 'backhaul' && (
            <BackhaulForm
              state={backhaulState}
              onChange={(partial) => {
                setBackhaulState((prev) => ({ ...prev, ...partial }))
              }}
              onOpenAdvanced={() => setShowBackhaulAdvanced(true)}
              acceptUdpVisible={
                backhaulState.transport === 'tcp' || backhaulState.transport === 'tcpmux'
              }
            />
          )}
          
          {tunnel.core === 'rathole' && (
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Ports
              </label>
              <input
                type="text"
                value={formData.ports}
                onChange={(e) =>
                  setFormData({ ...formData, ports: e.target.value })
                }
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                placeholder="8080,8081,8082"
              />
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                Ports (comma-separated, same for panel and node local service)
              </p>
            </div>
          )}
          
          {tunnel.core === 'rathole' && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
              <div>
                <div className="flex items-center justify-between mb-1 h-5">
                  <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                    Rathole Port
                  </label>
                </div>
                <input
                  type="number"
                  value={formData.rathole_remote_addr ? formData.rathole_remote_addr.split(':')[1] || formData.rathole_remote_addr : ''}
                  onChange={(e) => {
                    const port = e.target.value
                    const host = window.location.hostname
                    setFormData({ ...formData, rathole_remote_addr: port ? `${host}:${port}` : '' })
                  }}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                  placeholder="23333"
                  min="1"
                  max="65535"
                />
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">Rathole server port on panel (IP: {window.location.hostname})</p>
              </div>
              <div>
                <div className="flex items-center justify-between mb-1 h-5">
                  <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                    Local Port
                  </label>
                </div>
                <input
                  type="number"
                  value={formData.rathole_local_port}
                  onChange={(e) =>
                    setFormData({ ...formData, rathole_local_port: e.target.value })
                  }
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                  placeholder="8080"
                  min="1"
                  max="65535"
                />
              </div>
            </div>
          )}
          
          {tunnel.core === 'chisel' && (
            <>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Ports
                </label>
                <input
                  type="text"
                  value={formData.ports}
                  onChange={(e) =>
                    setFormData({ ...formData, ports: e.target.value })
                  }
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                  placeholder="8080,8081,8082"
                  required
                />
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  Ports (comma-separated, same for reverse port and local port)
                </p>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Control Port
                </label>
                <input
                  type="number"
                  value={formData.chisel_control_port}
                  onChange={(e) =>
                    setFormData({ ...formData, chisel_control_port: e.target.value })
                  }
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                  placeholder={`${(parseInt(formData.ports.split(',')[0]?.trim()) || 8080) + 10000} (auto)`}
                  min="1"
                  max="65535"
                />
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  Chisel server control port (leave empty for auto: first port + 10000)
                </p>
              </div>
              {/* Node IPv6 address field for Chisel when v4 to v6 is enabled */}
              {tunnel.spec?.use_ipv6 && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Node IPv6 Address (Optional)
                  </label>
                  <input
                    type="text"
                    value={formData.node_ipv6}
                    onChange={(e) =>
                      setFormData({ ...formData, node_ipv6: e.target.value })
                    }
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                    placeholder="::1 or 2001:db8::1"
                  />
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                    IPv6 address of the node. Leave empty to use ::1 (localhost IPv6)
                  </p>
                </div>
              )}
            </>
          )}
          
          {tunnel.core === 'frp' && (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                <div>
                  <div className="flex items-center justify-between mb-1 h-5">
                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                      Bind Port
                    </label>
                  </div>
                  <input
                    type="number"
                    value={formData.frp_bind_port}
                    onChange={(e) =>
                      setFormData({ ...formData, frp_bind_port: e.target.value })
                    }
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                    placeholder="7000"
                    min="1"
                    max="65535"
                    required
                  />
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                    FRP server port on panel (default: 7000)
                  </p>
                </div>
                <div>
                  <div className="flex items-center justify-between mb-1 h-5">
                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                      Ports
                    </label>
                  </div>
                  <input
                    type="text"
                    value={formData.ports}
                    onChange={(e) =>
                      setFormData({ ...formData, ports: e.target.value })
                    }
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                    placeholder="8080,8081,8082"
                    required
                  />
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                    Ports (comma-separated, same for remote port and local port)
                  </p>
                </div>
              </div>

              {/* Advanced FRP & Anti-DPI Settings */}
              <div className="p-4 bg-gray-50 dark:bg-gray-800/60 rounded-xl border border-gray-200 dark:border-gray-700 space-y-4">
                <h4 className="text-sm font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-cyan-500"></span>
                  Advanced FRP & Anti-DPI Settings
                </h4>
                
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                  <div>
                    <div className="flex items-center justify-between mb-1 h-5">
                      <label className="text-xs font-medium text-gray-700 dark:text-gray-300">
                        Transport Protocol
                      </label>
                    </div>
                    <select
                      value={formData.frp_transport}
                      onChange={(e) => setFormData({ ...formData, frp_transport: e.target.value })}
                      className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                    >
                      <option value="tcp">TCP (with TLS & Zero-Byte Signature)</option>
                      <option value="kcp">KCP (Fast UDP - Resilient to Packet Loss)</option>
                      <option value="quic">QUIC (HTTP/3 UDP + TLS 1.3 Multiplex)</option>
                      <option value="websocket">WebSocket (Plain WS)</option>
                      <option value="wss">WSS (Secure WebSocket - CDN Capable)</option>
                    </select>
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-1 h-5">
                      <label className="text-xs font-medium text-gray-700 dark:text-gray-300">
                        Stealth SNI / Camouflage Domain
                      </label>
                    </div>
                    <input
                      type="text"
                      value={formData.frp_sni}
                      onChange={(e) => setFormData({ ...formData, frp_sni: e.target.value })}
                      className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                      placeholder="e.g. speedtest.net or domain.com"
                    />
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-6 pt-1">
                  <label className="flex items-center gap-2 cursor-pointer text-xs font-medium text-gray-700 dark:text-gray-300">
                    <input
                      type="checkbox"
                      checked={formData.frp_encryption}
                      onChange={(e) => setFormData({ ...formData, frp_encryption: e.target.checked })}
                      className="rounded border-gray-300 text-cyan-600 focus:ring-cyan-500"
                    />
                    Payload Encryption (AES/ChaCha20)
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer text-xs font-medium text-gray-700 dark:text-gray-300">
                    <input
                      type="checkbox"
                      checked={formData.frp_compression}
                      onChange={(e) => setFormData({ ...formData, frp_compression: e.target.checked })}
                      className="rounded border-gray-300 text-cyan-600 focus:ring-cyan-500"
                    />
                    Snappy Compression (Packet Entropy Scrambling)
                  </label>
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between mb-1 h-5">
                  <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                    Token
                  </label>
                  <span className="text-xs text-gray-400 dark:text-gray-500 font-normal">Optional</span>
                </div>
                <input
                  type="text"
                  value={formData.frp_token}
                  onChange={(e) =>
                    setFormData({ ...formData, frp_token: e.target.value })
                  }
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                  placeholder="Auto-generated if empty"
                />
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">Authentication token (auto-generated if left blank)</p>
              </div>
            </>
          )}
          
          {/* Node IPv6 address field for Rathole when v4 to v6 is enabled */}
          {tunnel.core === 'rathole' && tunnel.spec?.use_ipv6 && (
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Node IPv6 Address (Optional)
              </label>
              <input
                type="text"
                value={formData.node_ipv6}
                onChange={(e) =>
                  setFormData({ ...formData, node_ipv6: e.target.value })
                }
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                placeholder="::1 or 2001:db8::1"
              />
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                IPv6 address of the node. Leave empty to use ::1 (localhost IPv6)
              </p>
            </div>
          )}
          
          {/* Advanced GOST Settings */}
          {tunnel.core === 'gost' && (
            <div className="mt-6 border-t border-gray-200 dark:border-gray-700 pt-6">
              <h4 className="text-sm font-semibold text-gray-900 dark:text-white mb-4 uppercase tracking-wider">Advanced GOST Settings</h4>
              <div className="space-y-4">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                  <div>
                    <div className="flex items-center justify-between mb-1 h-5">
                      <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Transport Type</label>
                    </div>
                    <select
                      value={formData.transport_type}
                      onChange={(e) => setFormData({...formData, transport_type: e.target.value})}
                      className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white focus:ring-blue-500 focus:border-blue-500"
                    >
                      <option value="tcp">TCP</option>
                      <option value="ws">WebSocket (WS)</option>
                      <option value="mws">Multiplex WS (MWS)</option>
                    </select>
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-1 h-5">
                      <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Security Type</label>
                    </div>
                    <select
                      value={formData.security_type}
                      onChange={(e) => setFormData({...formData, security_type: e.target.value})}
                      className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white focus:ring-blue-500 focus:border-blue-500"
                    >
                      <option value="none">None</option>
                      <option value="tls">TLS</option>
                      <option value="utls">uTLS (Stealth TLS)</option>
                    </select>
                  </div>
                </div>

                <div className="p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-100 dark:border-gray-700">
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Failover IPs</label>
                  <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">Foreign IPs to fallback to if the main IP is blocked (One per line)</p>
                  <textarea
                    value={formData.failover_ips}
                    onChange={(e) => setFormData({...formData, failover_ips: e.target.value})}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white focus:ring-blue-500 focus:border-blue-500"
                    placeholder="1.2.3.4&#10;5.6.7.8"
                    rows={2}
                  />
                </div>
                <div className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-100 dark:border-gray-700">
                  <div>
                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Reverse Tunnel Mode</label>
                    <p className="text-xs text-gray-500 dark:text-gray-400">Iran node will act as a client and connect to the foreign server</p>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input type="checkbox" className="sr-only peer" checked={formData.is_reverse} onChange={(e) => setFormData({...formData, is_reverse: e.target.checked})} />
                    <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 dark:peer-focus:ring-blue-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"></div>
                  </label>
                </div>

                <div className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-100 dark:border-gray-700">
                  <div>
                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">CDN Mode</label>
                    <p className="text-xs text-gray-500 dark:text-gray-400">Optimize traffic for CDN / Cloudflare</p>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input type="checkbox" className="sr-only peer" checked={formData.cdn_mode} onChange={(e) => {
                      const isChecked = e.target.checked;
                      const updates: any = { cdn_mode: isChecked };
                      if (isChecked && ['tcp', 'udp', 'tcp+udp'].includes(formData.transport_type)) {
                        updates.transport_type = 'ws';
                        showToast('info', 'Transport Switched', 'CDN mode requires WebSocket transport. Transport type auto-switched to WS.')
                      }
                      setFormData({...formData, ...updates});
                    }} />
                    <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 dark:peer-focus:ring-blue-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"></div>
                  </label>
                </div>

                <div className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-100 dark:border-gray-700">
                  <div>
                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Gaming Mode (Mux)</label>
                    <p className="text-xs text-gray-500 dark:text-gray-400">Enable multiplexing (Yamux) to reduce latency</p>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input type="checkbox" className="sr-only peer" checked={formData.gaming_mode} onChange={(e) => setFormData({...formData, gaming_mode: e.target.checked})} />
                    <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 dark:peer-focus:ring-blue-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"></div>
                  </label>
                </div>

                {/* Security & Traffic Limits */}
                <div className="mt-4 pt-4 border-t border-gray-200 dark:border-gray-700">
                  <h4 className="text-sm font-semibold text-gray-900 dark:text-white mb-4 uppercase tracking-wider text-red-500">Security & Limits</h4>
                  <div className="space-y-4">
                    
                    {/* IP Whitelist Toggle */}
                    <div className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-100 dark:border-gray-700">
                      <div>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300">IP Whitelist (ACL)</label>
                        <p className="text-xs text-gray-500 dark:text-gray-400">Restrict access to specific IPs (One per line)</p>
                      </div>
                      <label className="relative inline-flex items-center cursor-pointer">
                        <input type="checkbox" className="sr-only peer" checked={formData.allowed_ips_enabled} onChange={(e) => setFormData({...formData, allowed_ips_enabled: e.target.checked})} />
                        <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 dark:peer-focus:ring-blue-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"></div>
                      </label>
                    </div>
                    {formData.allowed_ips_enabled && (
                      <div className="mt-2 pl-2 border-l-2 border-blue-500">
                        <textarea
                          value={formData.allowed_ips}
                          onChange={(e) => setFormData({...formData, allowed_ips: e.target.value})}
                          className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white focus:ring-blue-500 focus:border-blue-500"
                          placeholder="192.168.1.1&#10;10.0.0.0/24"
                          rows={3}
                        />
                      </div>
                    )}

                    {/* Rate Limit Toggle */}
                    <div className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-100 dark:border-gray-700">
                      <div>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Bandwidth Limit (Client-side)</label>
                        <p className="text-xs text-gray-500 dark:text-gray-400">Limit speed per connection to save server bandwidth</p>
                      </div>
                      <label className="relative inline-flex items-center cursor-pointer">
                        <input type="checkbox" className="sr-only peer" checked={formData.rate_limit_enabled} onChange={(e) => setFormData({...formData, rate_limit_enabled: e.target.checked})} />
                        <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 dark:peer-focus:ring-blue-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"></div>
                      </label>
                    </div>
                    {formData.rate_limit_enabled && (
                      <div className="mt-2 pl-2 border-l-2 border-blue-500">
                        <div className="flex items-center gap-2">
                          <input
                            type="number"
                            value={formData.rate_limit_mbps}
                            onChange={(e) => setFormData({...formData, rate_limit_mbps: e.target.value})}
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white focus:ring-blue-500 focus:border-blue-500"
                            placeholder="e.g. 5"
                            min="0.1"
                            step="0.1"
                          />
                          <span className="text-sm text-gray-600 dark:text-gray-400">Mbps</span>
                        </div>
                      </div>
                    )}

                    {/* Stealth Domain */}
                    <div className="p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-100 dark:border-gray-700">
                      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Stealth SNI (TLS Spoofing)</label>
                      <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">Mask your traffic as a legitimate website</p>
                      <input
                        type="text"
                        value={formData.stealth_domain}
                        onChange={(e) => setFormData({...formData, stealth_domain: e.target.value})}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white focus:ring-blue-500 focus:border-blue-500"
                        placeholder="e.g. www.google.com"
                      />
                    </div>
                  </div>
                </div>

                {formData.cdn_mode && (
                  <div className="grid grid-cols-1 gap-4 p-4 bg-gray-50 dark:bg-gray-800/30 rounded-lg border border-gray-100 dark:border-gray-700 mt-2">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Custom Host (Metadata)</label>
                      <input type="text" value={formData.custom_host} onChange={(e) => setFormData({...formData, custom_host: e.target.value})} className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white focus:ring-blue-500 focus:border-blue-500" placeholder="e.g. speedtest.net" />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Custom SNI</label>
                      <input type="text" value={formData.custom_sni} onChange={(e) => setFormData({...formData, custom_sni: e.target.value})} className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white focus:ring-blue-500 focus:border-blue-500" placeholder="e.g. speedtest.net" />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">WebSocket Path</label>
                      <input type="text" value={formData.ws_path} onChange={(e) => setFormData({...formData, ws_path: e.target.value})} className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white focus:ring-blue-500 focus:border-blue-500" placeholder="/graphql" />
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          <div className="flex gap-3 justify-end">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-200 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600"
            >
              {t.tunnels.cancel}
            </button>
            <button
              type="submit"
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
            >
              Save Changes
            </button>
          </div>
        </form>
        <BackhaulAdvancedDrawer
          open={showBackhaulAdvanced}
          state={backhaulAdvanced}
          onClose={() => setShowBackhaulAdvanced(false)}
          onChange={setBackhaulAdvanced}
        />
      </div>
    </div>
  )
}

interface AddTunnelModalProps {
  nodes: any[]
  servers: any[]
  onClose: () => void
  onSuccess: () => void
}

const AddTunnelModal = ({ nodes, servers, onClose, onSuccess }: AddTunnelModalProps) => {
  const { t } = useLanguage()
  const { showToast } = useToast()
  const [formData, setFormData] = useState({
    name: '',
    core: 'gost',
    type: 'tcp',
    node_id: '',
    foreign_node_id: '',
    iran_node_id: '',
    ports: '8080',  // Comma-separated ports (e.g., "8080,8081,8082")
    remote_ip: '127.0.0.1',
    rathole_remote_addr: '23333',
    rathole_token: '',
    chisel_control_port: '',  // Empty means auto (listen_port + 10000)
    frp_bind_port: '7000',
    frp_token: '',
    frp_local_ip: '127.0.0.1',
    frp_transport: 'tcp',
    frp_security: 'tls',
    frp_sni: '',
    frp_encryption: true,
    frp_compression: true,
    use_ipv6: false,
    node_ipv6: '',  // Optional IPv6 address for node (Rathole/Chisel)
    spec: {} as Record<string, any>,
    cdn_mode: false,
    gaming_mode: false,
    custom_host: '',
    custom_sni: '',
    ws_path: '',
    is_reverse: false,
    stealth_domain: '',
    transport_type: 'tcp',
    security_type: 'none',
    failover_ips: '',
    rate_limit_mbps: '',
    allowed_ips: '',
    rate_limit_enabled: false,
    allowed_ips_enabled: false,
  })
  const [backhaulState, setBackhaulState] = useState<BackhaulFormState>(createDefaultBackhaulState())
  const [backhaulAdvanced, setBackhaulAdvanced] = useState<BackhaulAdvancedState>(createDefaultBackhaulAdvancedState())
  const [showBackhaulAdvanced, setShowBackhaulAdvanced] = useState(false)

  // Auto-populate remote_ip with foreign server IP when GOST is selected
  useEffect(() => {
    if (formData.core === 'gost' && formData.foreign_node_id) {
      const selectedServer = servers.find(s => s.id === formData.foreign_node_id)
      if (selectedServer?.metadata?.ip_address) {
        setFormData(prev => ({
          ...prev,
          remote_ip: selectedServer.metadata.ip_address
        }))
      }
    }
  }, [formData.foreign_node_id, formData.core, servers])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      let spec = getSpecForType(formData.core, formData.type)
      let tunnelType = formData.type
      
      spec.use_ipv6 = formData.use_ipv6 || false
      
      // Parse comma-separated ports and ranges
      const parsePortsAndRanges = (portsStr: string): { ports: number[], port_ranges: string[] } => {
        const ports: number[] = []
        const port_ranges: string[] = []
        
        portsStr.split(',').forEach(p => {
          const trimmed = p.trim()
          if (!trimmed) return
          if (trimmed.includes('-')) {
            port_ranges.push(trimmed)
          } else {
            const num = parseInt(trimmed)
            if (!isNaN(num) && num > 0 && num <= 65535) {
              ports.push(num)
            }
          }
        })
        return { ports, port_ranges }
      }
      
      let ports: number[] = []
      let port_ranges: string[] = []
      
      if (formData.core === 'gost') {
        const parsed = parsePortsAndRanges(formData.ports)
        ports = parsed.ports
        port_ranges = parsed.port_ranges
        if (ports.length === 0 && port_ranges.length === 0) {
          showToast('warning', 'Invalid Ports', 'Please enter at least one valid port or port range')
          return
        }
      } else {
        ports = formData.ports
          .split(',')
          .map(p => p.trim())
          .filter(p => p)
          .map(p => parseInt(p))
          .filter(p => !isNaN(p) && p > 0 && p <= 65535)
          
        if (ports.length === 0) {
          showToast('warning', 'Invalid Port', 'Please enter at least one valid port')
          return
        }
      }
      
      if (formData.core === 'gost' && (formData.type === 'tcp' || formData.type === 'udp' || formData.type === 'grpc' || formData.type === 'tcpmux')) {
        const remoteIp = formData.remote_ip || (formData.use_ipv6 ? '::1' : '127.0.0.1')
        // For GOST, ports are equal (listen_port = forward_to port)
        spec.remote_ip = remoteIp
        spec.ports = ports  // Store multiple ports
        spec.port_ranges = port_ranges
        const fallbackPort = ports.length > 0 ? ports[0] : (port_ranges.length > 0 ? parseInt(port_ranges[0].split('-')[0]) : 8080)
        spec.listen_port = fallbackPort  // Keep first port for backward compatibility
        spec.remote_port = fallbackPort  // Keep first port for backward compatibility
      }
      
      if (formData.core === 'rathole') {
        const remoteHost = window.location.hostname
        const remotePort = formData.rathole_remote_addr || '23333'
        spec.remote_addr = `${remoteHost}:${remotePort}`
        if (formData.rathole_token) {
          spec.token = formData.rathole_token
        }
        spec.ports = ports
        spec.remote_port = ports[0]
        spec.listen_port = ports[0]
      }
      
      if (formData.core === 'chisel') {
        // For Chisel, ports are equal (reverse_port = local_port)
        spec.ports = ports  // Store multiple ports
        const firstPort = ports[0]
        spec.listen_port = firstPort
        spec.remote_port = firstPort
        spec.server_port = firstPort
        const controlPort = formData.chisel_control_port 
          ? parseInt(formData.chisel_control_port.toString())
          : firstPort + 10000
        spec.control_port = controlPort
        const panelHost = typeof window !== 'undefined' ? window.location.hostname : 'localhost'
        spec.panel_host = panelHost
      }
      
      if (formData.core === 'backhaul') {
        if (!formData.node_id) {
          showToast('warning', 'Node Required', 'Backhaul tunnels require an Iran node')
          return
        }
        // CRITICAL: For Backhaul, the Ports field is in BackhaulForm, not in the main formData.ports
        // backhaulState.public_port contains the comma-separated ports from the Backhaul form
        // We should use backhaulState.public_port, NOT formData.ports (which is for other cores)
        console.log('Backhaul tunnel creation - formData.ports:', formData.ports, 'type:', typeof formData.ports)
        console.log('Backhaul tunnel creation - backhaulState.public_port:', backhaulState.public_port)
        
        // Use backhaulState.public_port (from BackhaulForm) - it has the correct comma-separated ports
        // Only fallback to formData.ports if backhaulState.public_port is empty
        const portsToUse = backhaulState.public_port && backhaulState.public_port.trim() 
          ? backhaulState.public_port 
          : (formData.ports || '8080')
        
        const updatedBackhaulState = {
          ...backhaulState,
          public_port: portsToUse,
          target_port: portsToUse
        }
        console.log('Backhaul tunnel creation - updatedBackhaulState.public_port (final):', updatedBackhaulState.public_port)
        spec = buildBackhaulSpec(updatedBackhaulState, backhaulAdvanced)
        spec.use_ipv6 = formData.use_ipv6 || false
        // buildBackhaulSpec should already build ports array from public_port (formData.ports)
        // Verify ports were built correctly - if not, build them from parsed ports
        if (!spec.ports || !Array.isArray(spec.ports) || spec.ports.length === 0) {
          // buildBackhaulSpec didn't build ports, so build them from formData.ports
          if (backhaulAdvanced.customPorts && backhaulAdvanced.customPorts.trim()) {
            // Use customPorts if provided
            spec.ports = backhaulAdvanced.customPorts
              .split(/\r?\n/)
              .map((line) => line.trim())
              .filter(Boolean)
          } else if (ports.length > 0) {
            // Build from parsed ports (numbers) - format: "port=targetHost:port"
            const targetHost = spec.target_host || '127.0.0.1'
            const listenIp = spec.listen_ip || updatedBackhaulState.listen_ip || '0.0.0.0'
            spec.ports = ports.map(p => {
              // Format: "port=targetHost:port" or "listenIp:port=targetHost:port" if listenIp is set
              const listenPart = listenIp !== '0.0.0.0' ? `${listenIp}:${p}` : `${p}`
              return `${listenPart}=${targetHost}:${p}`
            })
          }
        }
        // Ensure ports array is properly formatted and has all ports
        if (spec.ports && Array.isArray(spec.ports) && spec.ports.length > 0) {
          console.log('Backhaul tunnel creation - final ports:', spec.ports, 'count:', spec.ports.length)
        } else {
          console.warn('Backhaul tunnel creation - no ports found! formData.ports:', formData.ports, 'publicPorts:', updatedBackhaulState.public_port)
        }
        tunnelType = backhaulState.transport
      }
      
      if (formData.core === 'frp') {
        if (!formData.node_id) {
          showToast('warning', 'Node Required', 'FRP tunnels require an Iran node')
          return
        }
        const bindPort = parseInt(formData.frp_bind_port) || 7000
        spec.bind_port = bindPort
        spec.ports = ports
        spec.listen_port = ports[0]
        spec.remote_port = ports[0]
        if (formData.frp_token) {
          spec.token = formData.frp_token
        }
        spec.local_ip = formData.frp_local_ip || '127.0.0.1'
        spec.local_port = ports[0]
        spec.type = formData.type === 'udp' ? 'udp' : 'tcp'
        tunnelType = formData.type === 'udp' ? 'udp' : 'tcp'
        spec.transport_type = formData.frp_transport || 'tcp'
        spec.security_type = formData.frp_security || 'tls'
        spec.custom_sni = formData.frp_sni || ''
        spec.use_encryption = formData.frp_encryption
        spec.use_compression = formData.frp_compression
      }
      
      if (formData.core === 'gost' && formData.ws_path && !formData.ws_path.startsWith('/')) {
        showToast('warning', 'Validation', 'WS Path must start with a slash (e.g., /graphql)')
        return
      }

      const payload = {
        name: formData.name,
        core: formData.core,
        type: tunnelType,
        spec: spec,
        ...(formData.core === 'gost' && {
          cdn_mode: formData.cdn_mode,
          gaming_mode: formData.gaming_mode,
          custom_host: formData.custom_host,
          custom_sni: formData.custom_sni,
          ws_path: formData.ws_path,
          is_reverse: formData.is_reverse,
          stealth_domain: formData.stealth_domain || null,
          transport_type: formData.transport_type,
          security_type: formData.security_type,
          failover_ips: formData.failover_ips ? formData.failover_ips.split('\n').map(ip => ip.trim()).filter(ip => ip.length > 0) : null,
          rate_limit_mbps: formData.rate_limit_enabled && formData.rate_limit_mbps ? parseFloat(formData.rate_limit_mbps) : null,
          allowed_ips: formData.allowed_ips_enabled && formData.allowed_ips 
            ? formData.allowed_ips.split('\n').map(ip => ip.trim()).filter(ip => ip.length > 0)
            : null,
          port_ranges: port_ranges.length > 0 ? port_ranges : null
        }),
        node_id: formData.is_reverse ? formData.iran_node_id : formData.node_id,
        foreign_node_id: formData.foreign_node_id || null,
        iran_node_id: formData.iran_node_id || formData.node_id || null
      }
      await api.post('/tunnels', payload)
      showToast('success', 'Tunnel Created', `${formData.name} was created successfully`)
      onSuccess()
    } catch (error) {
      console.error('Failed to create tunnel:', error)
      showToast('error', 'Error', 'Failed to create tunnel')
    }
  }

  const getSpecForType = (core: string, type: string): Record<string, any> => {
    const baseSpec: Record<string, any> = {}

    if (core === 'rathole') {
      return { ...baseSpec, remote_addr: '', token: '', local_addr: '127.0.0.1:8080' }
    }

    switch (type) {
      case 'grpc':
        return { ...baseSpec, service_name: 'GrpcService', uuid: generateUUID() }
      case 'udp':
        return { ...baseSpec, uuid: generateUUID(), header_type: 'none' }
      default:
        return baseSpec
    }
  }

  const handleCoreChange = (core: string) => {
    let newType = formData.type
    if (core === 'rathole' || core === 'chisel') {
      newType = core
    } else if (core === 'frp') {
      // Keep current type if it's tcp or udp, otherwise default to tcp
      newType = (formData.type === 'tcp' || formData.type === 'udp') ? formData.type : 'tcp'
    } else if (core === 'backhaul') {
      newType = backhaulState.transport
    } else if (formData.type === 'rathole' || formData.type === 'chisel' || formData.core === 'backhaul') {
      newType = 'tcp'
    }
    setFormData({ ...formData, core, type: newType })
  }

  const generateUUID = () => {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0
      const v = c === 'x' ? r : (r & 0x3) | 0x8
      return v.toString(16)
    })
  }

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[100] overflow-auto">
      <div className="bg-white dark:bg-gray-800 rounded-lg p-4 w-full max-w-xl my-4 max-h-[90vh] overflow-y-auto">
        <h2 className="text-xl font-bold text-gray-900 dark:text-white mb-4">{t.tunnels.createTunnel}</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Name
            </label>
            <input
              type="text"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
              required
            />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
            <div>
              <div className="flex items-center justify-between mb-1 h-5">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  {t.tunnels.iranNode}
                </label>
              </div>
              <select
                value={formData.iran_node_id || formData.node_id}
                onChange={(e) => setFormData({ ...formData, iran_node_id: e.target.value, node_id: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                required={formData.core === 'rathole' || formData.core === 'backhaul' || formData.core === 'frp' || formData.core === 'chisel'}
              >
                <option value="">{t.tunnels.selectIranNode}</option>
                {nodes.map((node) => (
                  <option key={node.id} value={node.id}>
                    {node.name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <div className="flex items-center justify-between mb-1 h-5">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  {t.tunnels.foreignServer}
                </label>
              </div>
              <select
                value={formData.foreign_node_id}
                onChange={(e) => setFormData({ ...formData, foreign_node_id: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                required={formData.core === 'rathole' || formData.core === 'backhaul' || formData.core === 'frp' || formData.core === 'chisel'}
              >
                <option value="">{t.tunnels.selectForeignServer}</option>
                {servers.map((server) => (
                  <option key={server.id} value={server.id}>
                    {server.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
            <div>
              <div className="flex items-center justify-between mb-1 h-5">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  {t.tunnels.core}
                </label>
              </div>
              <select
                value={formData.core}
                onChange={(e) => handleCoreChange(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
              >
                <option value="gost">GOST</option>
                <option value="rathole">Rathole</option>
                <option value="backhaul">Backhaul</option>
                <option value="chisel">Chisel</option>
                <option value="frp">FRP</option>
              </select>
            </div>
            <div>
              <div className="flex items-center justify-between mb-1 h-5">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  {t.tunnels.type}
                </label>
              </div>
              <select
                value={formData.type}
                onChange={(e) => {
                  const value = e.target.value as BackhaulTransport
                  setFormData({ ...formData, type: value })
                  if (formData.core === 'backhaul') {
                    setBackhaulState((prev) => ({ ...prev, transport: value }))
                  }
                }}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                disabled={formData.core === 'chisel'}
              >
                {formData.core === 'chisel' ? (
                  <option value={formData.core}>{formData.core.charAt(0).toUpperCase() + formData.core.slice(1)}</option>
                ) : formData.core === 'rathole' ? (
                  <>
                    <option value="tcp">TCP</option>
                    <option value="ws">WebSocket (WS)</option>
                  </>
                ) : formData.core === 'frp' ? (
                  <>
                    <option value="tcp">TCP</option>
                    <option value="udp">UDP</option>
                  </>
                ) : formData.core === 'backhaul' ? (
                  <>
                    <option value="tcp">TCP</option>
                    <option value="udp">UDP</option>
                    <option value="ws">WebSocket (WS)</option>
                    <option value="wsmux">WebSocket Mux</option>
                    <option value="tcpmux">TCPMux</option>
                  </>
                ) : (
                  <>
                    <option value="tcp">TCP</option>
                    <option value="udp">UDP</option>
                    <option value="tcp+udp">TCP + UDP</option>
                  </>
                )}
              </select>
            </div>
          </div>

          {formData.core === 'gost' && (formData.type === 'tcp' || formData.type === 'udp' || formData.type === 'grpc' || formData.type === 'tcpmux') && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
              <div>
                <div className="flex items-center justify-between mb-1 h-5">
                  <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                    {t.tunnels.remoteIP}
                  </label>
                </div>
                <input
                  type="text"
                  value={formData.remote_ip}
                  onChange={(e) =>
                    setFormData({ ...formData, remote_ip: e.target.value || '127.0.0.1' })
                  }
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                  placeholder="127.0.0.1 or [2001:db8::1]"
                />
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  {t.tunnels.remoteIPDescription}
                </p>
              </div>
              <div>
                <div className="flex items-center justify-between mb-1 h-5">
                  <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                    Ports & Ranges
                  </label>
                </div>
                <input
                  type="text"
                  value={formData.ports}
                  onChange={(e) =>
                    setFormData({ ...formData, ports: e.target.value })
                  }
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                  placeholder="8080, 8081, 10000-20000"
                  required
                />
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  Ports or ranges (comma-separated, same for panel and target server)
                </p>
              </div>
            </div>
          )}
          
          {formData.core === 'backhaul' && (
            <BackhaulForm
              state={backhaulState}
              onChange={(partial) => {
                setBackhaulState((prev) => ({ ...prev, ...partial }))
                if (partial.transport) {
                  setFormData((prev) => ({ ...prev, type: partial.transport as string }))
                }
              }}
              onOpenAdvanced={() => setShowBackhaulAdvanced(true)}
              acceptUdpVisible={
                backhaulState.transport === 'tcp' || backhaulState.transport === 'tcpmux'
              }
            />
          )}
          
          {formData.core === 'rathole' && (
            <>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Ports
                </label>
                <input
                  type="text"
                  value={formData.ports}
                  onChange={(e) =>
                    setFormData({ ...formData, ports: e.target.value })
                  }
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                  placeholder="8080,8081,8082"
                  required
                />
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  Ports (comma-separated, same for panel and node local service)
                </p>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                <div>
                  <div className="flex items-center justify-between mb-1 h-5">
                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                      Rathole Port
                    </label>
                  </div>
                  <input
                    type="number"
                    value={formData.rathole_remote_addr}
                    onChange={(e) =>
                      setFormData({ ...formData, rathole_remote_addr: e.target.value })
                    }
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                    placeholder="23333"
                    min="1"
                    max="65535"
                    required
                  />
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">Rathole server port on panel (IP: {window.location.hostname})</p>
                </div>
                <div>
                  <div className="flex items-center justify-between mb-1 h-5">
                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                      Token
                    </label>
                    <span className="text-xs text-gray-400 dark:text-gray-500 font-normal">Optional</span>
                  </div>
                  <input
                    type="text"
                    value={formData.rathole_token}
                    onChange={(e) =>
                      setFormData({ ...formData, rathole_token: e.target.value })
                    }
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                    placeholder="Auto-generated if empty"
                  />
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">Authentication token (auto-generated if left blank)</p>
                </div>
              </div>
            </>
          )}
          
          {formData.core === 'chisel' && (
            <>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Ports
                </label>
                <input
                  type="text"
                  value={formData.ports}
                  onChange={(e) =>
                    setFormData({ ...formData, ports: e.target.value })
                  }
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                  placeholder="8080,8081,8082"
                  required
                />
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  Ports (comma-separated, same for reverse port and local port)
                </p>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Control Port
                </label>
                <input
                  type="number"
                  value={formData.chisel_control_port}
                  onChange={(e) =>
                    setFormData({ ...formData, chisel_control_port: e.target.value })
                  }
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                  placeholder={`${(parseInt(formData.ports.split(',')[0]?.trim()) || 8080) + 10000} (auto)`}
                  min="1"
                  max="65535"
                />
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  Chisel server control port (leave empty for auto: first port + 10000)
                </p>
              </div>
            </>
          )}
          
          {formData.core === 'frp' && (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                <div>
                  <div className="flex items-center justify-between mb-1 h-5">
                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                      Bind Port
                    </label>
                  </div>
                  <input
                    type="number"
                    value={formData.frp_bind_port}
                    onChange={(e) =>
                      setFormData({ ...formData, frp_bind_port: e.target.value })
                    }
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                    placeholder="7000"
                    min="1"
                    max="65535"
                    required
                  />
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                    FRP server port on panel (default: 7000)
                  </p>
                </div>
                <div>
                  <div className="flex items-center justify-between mb-1 h-5">
                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                      Ports
                    </label>
                  </div>
                  <input
                    type="text"
                    value={formData.ports}
                    onChange={(e) =>
                      setFormData({ ...formData, ports: e.target.value })
                    }
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                    placeholder="8080,8081,8082"
                    required
                  />
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                    Ports (comma-separated, same for remote port and local port)
                  </p>
                </div>
              </div>

              {/* Advanced FRP & Anti-DPI Settings */}
              <div className="p-4 bg-gray-50 dark:bg-gray-800/60 rounded-xl border border-gray-200 dark:border-gray-700 space-y-4">
                <h4 className="text-sm font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-cyan-500"></span>
                  Advanced FRP & Anti-DPI Settings
                </h4>
                
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                  <div>
                    <div className="flex items-center justify-between mb-1 h-5">
                      <label className="text-xs font-medium text-gray-700 dark:text-gray-300">
                        Transport Protocol
                      </label>
                    </div>
                    <select
                      value={formData.frp_transport}
                      onChange={(e) => setFormData({ ...formData, frp_transport: e.target.value })}
                      className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                    >
                      <option value="tcp">TCP (with TLS & Zero-Byte Signature)</option>
                      <option value="kcp">KCP (Fast UDP - Resilient to Packet Loss)</option>
                      <option value="quic">QUIC (HTTP/3 UDP + TLS 1.3 Multiplex)</option>
                      <option value="websocket">WebSocket (Plain WS)</option>
                      <option value="wss">WSS (Secure WebSocket - CDN Capable)</option>
                    </select>
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-1 h-5">
                      <label className="text-xs font-medium text-gray-700 dark:text-gray-300">
                        Stealth SNI / Camouflage Domain
                      </label>
                    </div>
                    <input
                      type="text"
                      value={formData.frp_sni}
                      onChange={(e) => setFormData({ ...formData, frp_sni: e.target.value })}
                      className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                      placeholder="e.g. speedtest.net or domain.com"
                    />
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-6 pt-1">
                  <label className="flex items-center gap-2 cursor-pointer text-xs font-medium text-gray-700 dark:text-gray-300">
                    <input
                      type="checkbox"
                      checked={formData.frp_encryption}
                      onChange={(e) => setFormData({ ...formData, frp_encryption: e.target.checked })}
                      className="rounded border-gray-300 text-cyan-600 focus:ring-cyan-500"
                    />
                    Payload Encryption (AES/ChaCha20)
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer text-xs font-medium text-gray-700 dark:text-gray-300">
                    <input
                      type="checkbox"
                      checked={formData.frp_compression}
                      onChange={(e) => setFormData({ ...formData, frp_compression: e.target.checked })}
                      className="rounded border-gray-300 text-cyan-600 focus:ring-cyan-500"
                    />
                    Snappy Compression (Packet Entropy Scrambling)
                  </label>
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between mb-1 h-5">
                  <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                    Token
                  </label>
                  <span className="text-xs text-gray-400 dark:text-gray-500 font-normal">Optional</span>
                </div>
                <input
                  type="text"
                  value={formData.frp_token}
                  onChange={(e) =>
                    setFormData({ ...formData, frp_token: e.target.value })
                  }
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
                  placeholder="Auto-generated if empty"
                />
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">Authentication token (auto-generated if left blank)</p>
              </div>
            </>
          )}
          
          {/* v4 to v6 tunnel checkbox - only for Rathole, Backhaul, Chisel, FRP (not GOST) */}
          {formData.core !== 'gost' && (
            <>
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="v4_to_v6"
                  checked={formData.use_ipv6}
                  onChange={(e) => setFormData({ ...formData, use_ipv6: e.target.checked })}
                  className="w-4 h-4 text-blue-600 bg-gray-100 border-gray-300 rounded focus:ring-blue-500 dark:focus:ring-blue-600 dark:ring-offset-gray-800 focus:ring-2 dark:bg-gray-700 dark:border-gray-600"
                />
                <label htmlFor="v4_to_v6" className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  v4 to v6 tunnel
                </label>
              </div>
              <p className="text-xs text-gray-500 dark:text-gray-400 -mt-2">
                Enable this to create a tunnel from IPv4 (iran node) to IPv6 (node/target). Iran node listens on IPv4, target uses IPv6.
              </p>
            </>
          )}

          {/* Advanced GOST Settings */}
          {formData.core === 'gost' && (
            <div className="mt-6 border-t border-gray-200 dark:border-gray-700 pt-6">
              <h4 className="text-sm font-semibold text-gray-900 dark:text-white mb-4 uppercase tracking-wider">Advanced GOST Settings</h4>
              <div className="space-y-4">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                  <div>
                    <div className="flex items-center justify-between mb-1 h-5">
                      <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Transport Type</label>
                    </div>
                    <select
                      value={formData.transport_type}
                      onChange={(e) => setFormData({...formData, transport_type: e.target.value})}
                      className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white focus:ring-blue-500 focus:border-blue-500"
                    >
                      <option value="tcp">TCP</option>
                      <option value="ws">WebSocket (WS)</option>
                      <option value="mws">Multiplex WS (MWS)</option>
                    </select>
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-1 h-5">
                      <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Security Type</label>
                    </div>
                    <select
                      value={formData.security_type}
                      onChange={(e) => setFormData({...formData, security_type: e.target.value})}
                      className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white focus:ring-blue-500 focus:border-blue-500"
                    >
                      <option value="none">None</option>
                      <option value="tls">TLS</option>
                      <option value="utls">uTLS (Stealth TLS)</option>
                    </select>
                  </div>
                </div>

                <div className="p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-100 dark:border-gray-700">
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Failover IPs</label>
                  <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">Foreign IPs to fallback to if the main IP is blocked (One per line)</p>
                  <textarea
                    value={formData.failover_ips}
                    onChange={(e) => setFormData({...formData, failover_ips: e.target.value})}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white focus:ring-blue-500 focus:border-blue-500"
                    placeholder="1.2.3.4&#10;5.6.7.8"
                    rows={2}
                  />
                </div>
                <div className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-100 dark:border-gray-700">
                  <div>
                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Reverse Tunnel Mode</label>
                    <p className="text-xs text-gray-500 dark:text-gray-400">Iran node will act as a client and connect to the foreign server</p>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input type="checkbox" className="sr-only peer" checked={formData.is_reverse} onChange={(e) => setFormData({...formData, is_reverse: e.target.checked})} />
                    <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 dark:peer-focus:ring-blue-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"></div>
                  </label>
                </div>

                <div className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-100 dark:border-gray-700">
                  <div>
                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">CDN Mode</label>
                    <p className="text-xs text-gray-500 dark:text-gray-400">Optimize traffic for CDN / Cloudflare</p>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input type="checkbox" className="sr-only peer" checked={formData.cdn_mode} onChange={(e) => {
                      const isChecked = e.target.checked;
                      const updates: any = { cdn_mode: isChecked };
                      if (isChecked && ['tcp', 'udp', 'tcp+udp'].includes(formData.transport_type)) {
                        updates.transport_type = 'ws';
                        showToast('info', 'Transport Switched', 'CDN mode requires WebSocket transport. Transport type auto-switched to WS.')
                      }
                      setFormData({...formData, ...updates});
                    }} />
                    <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 dark:peer-focus:ring-blue-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"></div>
                  </label>
                </div>

                <div className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-100 dark:border-gray-700">
                  <div>
                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Gaming Mode (Mux)</label>
                    <p className="text-xs text-gray-500 dark:text-gray-400">Enable multiplexing (Yamux) to reduce latency</p>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input type="checkbox" className="sr-only peer" checked={formData.gaming_mode} onChange={(e) => setFormData({...formData, gaming_mode: e.target.checked})} />
                    <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 dark:peer-focus:ring-blue-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"></div>
                  </label>
                </div>

                {/* Security & Traffic Limits */}
                <div className="mt-4 pt-4 border-t border-gray-200 dark:border-gray-700">
                  <h4 className="text-sm font-semibold text-gray-900 dark:text-white mb-4 uppercase tracking-wider text-red-500">Security & Limits</h4>
                  <div className="space-y-4">
                    
                    {/* IP Whitelist Toggle */}
                    <div className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-100 dark:border-gray-700">
                      <div>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300">IP Whitelist (ACL)</label>
                        <p className="text-xs text-gray-500 dark:text-gray-400">Restrict access to specific IPs (One per line)</p>
                      </div>
                      <label className="relative inline-flex items-center cursor-pointer">
                        <input type="checkbox" className="sr-only peer" checked={formData.allowed_ips_enabled} onChange={(e) => setFormData({...formData, allowed_ips_enabled: e.target.checked})} />
                        <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 dark:peer-focus:ring-blue-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"></div>
                      </label>
                    </div>
                    {formData.allowed_ips_enabled && (
                      <div className="mt-2 pl-2 border-l-2 border-blue-500">
                        <textarea
                          value={formData.allowed_ips}
                          onChange={(e) => setFormData({...formData, allowed_ips: e.target.value})}
                          className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white focus:ring-blue-500 focus:border-blue-500"
                          placeholder="192.168.1.1&#10;10.0.0.0/24"
                          rows={3}
                        />
                      </div>
                    )}

                    {/* Rate Limit Toggle */}
                    <div className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-100 dark:border-gray-700">
                      <div>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Bandwidth Limit (Client-side)</label>
                        <p className="text-xs text-gray-500 dark:text-gray-400">Limit speed per connection to save server bandwidth</p>
                      </div>
                      <label className="relative inline-flex items-center cursor-pointer">
                        <input type="checkbox" className="sr-only peer" checked={formData.rate_limit_enabled} onChange={(e) => setFormData({...formData, rate_limit_enabled: e.target.checked})} />
                        <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 dark:peer-focus:ring-blue-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"></div>
                      </label>
                    </div>
                    {formData.rate_limit_enabled && (
                      <div className="mt-2 pl-2 border-l-2 border-blue-500">
                        <div className="flex items-center gap-2">
                          <input
                            type="number"
                            value={formData.rate_limit_mbps}
                            onChange={(e) => setFormData({...formData, rate_limit_mbps: e.target.value})}
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white focus:ring-blue-500 focus:border-blue-500"
                            placeholder="e.g. 5"
                            min="0.1"
                            step="0.1"
                          />
                          <span className="text-sm text-gray-600 dark:text-gray-400">Mbps</span>
                        </div>
                      </div>
                    )}

                    {/* Stealth Domain */}
                    <div className="p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-100 dark:border-gray-700">
                      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Stealth SNI (TLS Spoofing)</label>
                      <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">Mask your traffic as a legitimate website</p>
                      <input
                        type="text"
                        value={formData.stealth_domain}
                        onChange={(e) => setFormData({...formData, stealth_domain: e.target.value})}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white focus:ring-blue-500 focus:border-blue-500"
                        placeholder="e.g. www.google.com"
                      />
                    </div>
                  </div>
                </div>

                {formData.cdn_mode && (
                  <div className="grid grid-cols-1 gap-4 p-4 bg-gray-50 dark:bg-gray-800/30 rounded-lg border border-gray-100 dark:border-gray-700 mt-2">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Custom Host (Metadata)</label>
                      <input type="text" value={formData.custom_host} onChange={(e) => setFormData({...formData, custom_host: e.target.value})} className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white focus:ring-blue-500 focus:border-blue-500" placeholder="e.g. speedtest.net" />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Custom SNI</label>
                      <input type="text" value={formData.custom_sni} onChange={(e) => setFormData({...formData, custom_sni: e.target.value})} className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white focus:ring-blue-500 focus:border-blue-500" placeholder="e.g. speedtest.net" />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">WebSocket Path</label>
                      <input type="text" value={formData.ws_path} onChange={(e) => setFormData({...formData, ws_path: e.target.value})} className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white focus:ring-blue-500 focus:border-blue-500" placeholder="/graphql" />
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          <div className="flex gap-3 justify-end">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-200 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600"
            >
              {t.tunnels.cancel}
            </button>
            <button
              type="submit"
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
            >
              {t.tunnels.createTunnel}
            </button>
          </div>
        </form>
        <BackhaulAdvancedDrawer
          open={showBackhaulAdvanced}
          state={backhaulAdvanced}
          onClose={() => setShowBackhaulAdvanced(false)}
          onChange={setBackhaulAdvanced}
        />
      </div>
    </div>
  )
}

const BACKHAUL_TRANSPORTS: BackhaulTransport[] = ['tcp', 'udp', 'ws', 'wsmux', 'tcpmux']

function BackhaulForm({
  state,
  onChange,
  onOpenAdvanced,
  acceptUdpVisible,
}: {
  state: BackhaulFormState
  onChange: (partial: Partial<BackhaulFormState>) => void
  onOpenAdvanced: () => void
  acceptUdpVisible?: boolean
}) {
  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Control Port
        </label>
        <input
          type="number"
          value={state.control_port}
          onChange={(e) => onChange({ control_port: e.target.value })}
          className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
          placeholder="3080"
          min={1}
          max={65535}
        />
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
          Port where the node connects back to the panel.
        </p>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Ports
        </label>
        <input
          type="text"
          value={state.public_port}
          onChange={(e) => {
            onChange({ public_port: e.target.value, target_port: e.target.value })
          }}
          className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
          placeholder="8080,8081,8082"
        />
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
          Ports (comma-separated, same for public port and target port)
        </p>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Token (Optional - Auto-generated if empty)
        </label>
        <input
          type="text"
          value={state.token}
          onChange={(e) => onChange({ token: e.target.value })}
          className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white"
          placeholder="Leave empty for auto-generation"
        />
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">Authentication token (will be auto-generated if not provided)</p>
      </div>

      {acceptUdpVisible && (
        <div className="flex items-center justify-between">
          <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
            Allow UDP over TCP
          </label>
          <input
            type="checkbox"
            checked={state.accept_udp}
            onChange={() => onChange({ accept_udp: !state.accept_udp })}
            className="h-4 w-4 text-blue-600 rounded border-gray-300 dark:border-gray-600 focus:ring-blue-500"
          />
        </div>
      )}

      <div className="pt-2">
        <button
          type="button"
          onClick={onOpenAdvanced}
          className="px-3 py-2 text-sm font-medium text-blue-600 dark:text-blue-400 hover:underline"
        >
          Advanced settings
        </button>
      </div>
    </div>
  )
}

function BackhaulAdvancedDrawer({
  open,
  onClose,
  state,
  onChange,
}: {
  open: boolean
  onClose: () => void
  state: BackhaulAdvancedState
  onChange: (next: BackhaulAdvancedState) => void
}) {
  if (!open) {
    return null
  }

  const updateServer = (key: keyof BackhaulAdvancedServerState, value: string | boolean) => {
    onChange({
      ...state,
      server: {
        ...state.server,
        [key]: value,
      },
    })
  }

  const updateClient = (key: keyof BackhaulAdvancedClientState, value: string | boolean) => {
    onChange({
      ...state,
      client: {
        ...state.client,
        [key]: value,
      },
    })
  }

  return (
    <div className="fixed inset-0 z-[100] flex">
      <div className="flex-1 bg-black bg-opacity-40" onClick={onClose} />
      <div className="w-full max-w-xl h-full bg-white dark:bg-gray-900 shadow-xl overflow-y-auto p-6">
        <div className="flex justify-between items-center mb-6">
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white">Backhaul Advanced Settings</h3>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
          >
            Close
          </button>
        </div>

        <div className="space-y-6">
          <div>
            <h4 className="text-sm font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-3">
              Server Options
            </h4>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Keepalive (s)</label>
                <input
                  type="number"
                  value={state.server.keepalive_period}
                  onChange={(e) => updateServer('keepalive_period', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  min={1}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Heartbeat (s)</label>
                <input
                  type="number"
                  value={state.server.heartbeat}
                  onChange={(e) => updateServer('heartbeat', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  min={1}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Channel Size</label>
                <input
                  type="number"
                  value={state.server.channel_size}
                  onChange={(e) => updateServer('channel_size', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  min={1}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Mux Concurrency</label>
                <input
                  type="number"
                  value={state.server.mux_con}
                  onChange={(e) => updateServer('mux_con', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  min={1}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Log Level</label>
                <select
                  value={state.server.log_level}
                  onChange={(e) => updateServer('log_level', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                >
                  <option value="panic">panic</option>
                  <option value="fatal">fatal</option>
                  <option value="error">error</option>
                  <option value="warn">warn</option>
                  <option value="info">info</option>
                  <option value="debug">debug</option>
                  <option value="trace">trace</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Web UI Port</label>
                <input
                  type="number"
                  value={state.server.web_port}
                  onChange={(e) => updateServer('web_port', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  placeholder="0 (disable)"
                  min={0}
                />
              </div>
              <div className="col-span-2 flex items-center gap-3">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300 flex-1">Enable Sniffer</label>
                <input
                  type="checkbox"
                  checked={state.server.sniffer}
                  onChange={() => updateServer('sniffer', !state.server.sniffer)}
                  className="h-4 w-4 text-blue-600 rounded border-gray-300 dark:border-gray-600 focus:ring-blue-500"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Sniffer Log Path</label>
                <input
                  type="text"
                  value={state.server.sniffer_log}
                  onChange={(e) => updateServer('sniffer_log', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  placeholder="/var/log/backhaul.json"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">TLS Certificate Path</label>
                <input
                  type="text"
                  value={state.server.tls_cert}
                  onChange={(e) => updateServer('tls_cert', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">TLS Key Path</label>
                <input
                  type="text"
                  value={state.server.tls_key}
                  onChange={(e) => updateServer('tls_key', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                />
              </div>
              <div className="col-span-2 flex items-center gap-3">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300 flex-1">Disable Optimizations</label>
                <input
                  type="checkbox"
                  checked={state.server.skip_optz}
                  onChange={() => updateServer('skip_optz', !state.server.skip_optz)}
                  className="h-4 w-4 text-blue-600 rounded border-gray-300 dark:border-gray-600 focus:ring-blue-500"
                />
              </div>
              <div className="col-span-2 flex items-center gap-3">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300 flex-1">Enable Proxy Protocol</label>
                <input
                  type="checkbox"
                  checked={state.server.proxy_protocol}
                  onChange={() => updateServer('proxy_protocol', !state.server.proxy_protocol)}
                  className="h-4 w-4 text-blue-600 rounded border-gray-300 dark:border-gray-600 focus:ring-blue-500"
                />
              </div>
              <div className="col-span-2 flex items-center gap-3">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300 flex-1">TCP Nodelay</label>
                <input
                  type="checkbox"
                  checked={state.server.nodelay}
                  onChange={() => updateServer('nodelay', !state.server.nodelay)}
                  className="h-4 w-4 text-blue-600 rounded border-gray-300 dark:border-gray-600 focus:ring-blue-500"
                />
              </div>
            </div>
          </div>

          <div>
            <h4 className="text-sm font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-3">
              Client Options
            </h4>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Connection Pool</label>
                <input
                  type="number"
                  value={state.client.connection_pool}
                  onChange={(e) => updateClient('connection_pool', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  min={1}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Retry Interval (s)</label>
                <input
                  type="number"
                  value={state.client.retry_interval}
                  onChange={(e) => updateClient('retry_interval', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  min={1}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Dial Timeout (s)</label>
                <input
                  type="number"
                  value={state.client.dial_timeout}
                  onChange={(e) => updateClient('dial_timeout', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  min={1}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Keepalive (s)</label>
                <input
                  type="number"
                  value={state.client.keepalive_period}
                  onChange={(e) => updateClient('keepalive_period', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  min={1}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Log Level</label>
                <select
                  value={state.client.log_level}
                  onChange={(e) => updateClient('log_level', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                >
                  <option value="panic">panic</option>
                  <option value="fatal">fatal</option>
                  <option value="error">error</option>
                  <option value="warn">warn</option>
                  <option value="info">info</option>
                  <option value="debug">debug</option>
                  <option value="trace">trace</option>
                </select>
              </div>
              <div className="col-span-2">
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Edge IP (for WS/WSS)</label>
                <input
                  type="text"
                  value={state.client.edge_ip}
                  onChange={(e) => updateClient('edge_ip', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  placeholder="Optional CDN edge IP"
                />
              </div>
              <div className="col-span-2 flex items-center gap-3">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300 flex-1">Aggressive Pool</label>
                <input
                  type="checkbox"
                  checked={state.client.aggressive_pool}
                  onChange={() => updateClient('aggressive_pool', !state.client.aggressive_pool)}
                  className="h-4 w-4 text-blue-600 rounded border-gray-300 dark:border-gray-600 focus:ring-blue-500"
                />
              </div>
              <div className="col-span-2 flex items-center gap-3">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300 flex-1">TCP Nodelay</label>
                <input
                  type="checkbox"
                  checked={state.client.nodelay}
                  onChange={() => updateClient('nodelay', !state.client.nodelay)}
                  className="h-4 w-4 text-blue-600 rounded border-gray-300 dark:border-gray-600 focus:ring-blue-500"
                />
              </div>
              <div className="col-span-2 flex items-center gap-3">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300 flex-1">Disable Optimizations</label>
                <input
                  type="checkbox"
                  checked={state.client.skip_optz}
                  onChange={() => updateClient('skip_optz', !state.client.skip_optz)}
                  className="h-4 w-4 text-blue-600 rounded border-gray-300 dark:border-gray-600 focus:ring-blue-500"
                />
              </div>
            </div>
          </div>

          <div>
            <h4 className="text-sm font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-3">
              Custom Ports
            </h4>
            <textarea
              value={state.customPorts}
              onChange={(e) => onChange({ ...state, customPorts: e.target.value })}
              className="w-full min-h-[120px] px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
              placeholder={`One entry per line. Examples:\n443\n443=127.0.0.1:8080\n443=[2001:db8::1]:8080\n2000-2100=127.0.0.1:22`}
            />
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
              Format matches Backhaul ports syntax. Leave empty to use the single public port above.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}

function buildBackhaulSpec(
  base: BackhaulFormState,
  advanced: BackhaulAdvancedState,
  transportOverride?: BackhaulTransport,
): Record<string, any> {
  const transport = transportOverride ?? base.transport
  const controlPort = parseInt(base.control_port, 10)
  const publicPort = parseInt(base.public_port, 10)
  const targetPort = parseInt(base.target_port, 10)
  const listenIp = base.listen_ip.trim() || '0.0.0.0'
  const targetHost = base.target_host.trim() || '127.0.0.1'
  const token = base.token.trim()
  const panelHost = base.public_host.trim() || (typeof window !== 'undefined' ? window.location.hostname : '') || '127.0.0.1'

  const effectiveControlPort = !Number.isNaN(controlPort) && controlPort > 0
    ? controlPort
    : (!Number.isNaN(publicPort) && publicPort > 0
        ? publicPort
        : (!Number.isNaN(targetPort) && targetPort > 0 ? targetPort : 3080))
  
  // Parse comma-separated ports from public_port
  const parsePortsFromString = (portStr: string): number[] => {
    if (!portStr || typeof portStr !== 'string') {
      console.warn('parsePortsFromString: invalid input:', portStr, 'type:', typeof portStr)
      return []
    }
    const parsed = portStr
      .split(',')
      .map(p => p.trim())
      .filter(p => p)
      .map(p => parseInt(p, 10))
      .filter(p => !isNaN(p) && p > 0 && p <= 65535)
    console.log('parsePortsFromString: input:', portStr, '-> parsed:', parsed, 'count:', parsed.length)
    return parsed
  }
  
  // CRITICAL: Ensure base.public_port is a string before parsing
  const publicPortStr = String(base.public_port || '')
  console.log('buildBackhaulSpec: base.public_port (raw):', base.public_port, 'type:', typeof base.public_port, '-> string:', publicPortStr)
  const publicPorts = parsePortsFromString(publicPortStr)
  console.log('buildBackhaulSpec: parsed publicPorts:', publicPorts, 'count:', publicPorts.length)
  const effectivePublicPort = publicPorts.length > 0 ? publicPorts[0] : (!Number.isNaN(publicPort) && publicPort > 0 ? publicPort : effectiveControlPort)
  const effectiveTargetPort = publicPorts.length > 0 ? publicPorts[0] : (!Number.isNaN(targetPort) && targetPort > 0 ? targetPort : effectivePublicPort)

  const remoteAddr = base.remote_addr.trim() || `${panelHost}:${effectiveControlPort}`
  const listenedPort = listenIp !== '0.0.0.0' ? `${listenIp}:${effectivePublicPort}` : `${effectivePublicPort}`
  const defaultPortEntry = `${listenedPort}=${targetHost}:${effectiveTargetPort}`

  // Use customPorts if provided, otherwise build from comma-separated public_port
  let ports: string[] = []
  
  // CRITICAL: Check if customPorts is set AND has content
  // If customPorts is empty or just whitespace, use publicPorts instead
  const hasCustomPorts = advanced.customPorts && advanced.customPorts.trim().length > 0
  
  if (hasCustomPorts) {
    // User manually entered ports in CUSTOM PORTS field
    ports = advanced.customPorts
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
    console.log('buildBackhaulSpec: Using customPorts, count:', ports.length, 'ports:', ports)
  } else if (publicPorts.length > 0) {
    // Build ports array from comma-separated public_port (e.g., "8080,8081,8082")
    // This is the automatic conversion from Ports field to Backhaul format
    ports = publicPorts.map(p => {
      const listenedPort = listenIp !== '0.0.0.0' ? `${listenIp}:${p}` : `${p}`
      return `${listenedPort}=${targetHost}:${p}`
    })
    console.log('buildBackhaulSpec: Built ports from publicPorts:', publicPorts, '-> ports:', ports, 'count:', ports.length)
  }
  
  if (ports.length === 0) {
    ports.push(defaultPortEntry)
    console.log('buildBackhaulSpec: No ports found, using default:', defaultPortEntry)
  }
  
  // Final verification - ensure we have ports
  console.log('buildBackhaulSpec: Final ports array:', ports, 'count:', ports.length)

  const serverOptions: Record<string, any> = {}
  Object.entries(advanced.server).forEach(([key, value]) => {
    if (booleanServerKeys.has(key)) {
      if (value) {
        serverOptions[key] = true
      }
      return
    }
    if (numericServerKeys.has(key)) {
      const num = Number(value)
      if (!Number.isNaN(num) && value !== '') {
        serverOptions[key] = num
      }
      return
    }
    if (stringServerKeys.has(key)) {
      const val = typeof value === 'string' ? value.trim() : value
      if (val) {
        serverOptions[key] = val
      }
    }
  })

  const clientOptions: Record<string, any> = {}
  Object.entries(advanced.client).forEach(([key, value]) => {
    if (booleanClientKeys.has(key)) {
      if (value) {
        clientOptions[key] = true
      }
      return
    }
    if (numericClientKeys.has(key)) {
      const num = Number(value)
      if (!Number.isNaN(num) && value !== '') {
        clientOptions[key] = num
      }
      return
    }
    if (stringClientKeys.has(key)) {
      const val = typeof value === 'string' ? value.trim() : value
      if (val) {
        clientOptions[key] = val
      }
    }
  })

  const spec: Record<string, any> = {
    transport,
    bind_addr: `0.0.0.0:${effectiveControlPort}`,
    remote_addr: remoteAddr,
    listen_ip: listenIp,
    control_port: effectiveControlPort,
    public_port: effectivePublicPort,
    listen_port: effectivePublicPort,
    target_host: targetHost,
    target_port: effectiveTargetPort,
    target_addr: `${targetHost}:${effectiveTargetPort}`,
    public_host: panelHost,
    ports,
  }

  if (token) {
    spec.token = token
  }
  if (base.accept_udp && (transport === 'tcp' || transport === 'tcpmux')) {
    spec.accept_udp = true
  }
  if (Object.keys(serverOptions).length > 0) {
    spec.server_options = serverOptions
  }
  if (Object.keys(clientOptions).length > 0) {
    spec.client_options = clientOptions
  }

  return spec
}

function parseBackhaulSpec(spec: Record<string, any>, currentType: string): {
  state: BackhaulFormState
  advanced: BackhaulAdvancedState
} {
  const state = createDefaultBackhaulState()
  const advanced = createDefaultBackhaulAdvancedState()

  if (BACKHAUL_TRANSPORTS.includes(currentType as BackhaulTransport)) {
    state.transport = currentType as BackhaulTransport
  }

  if (!spec) {
    return { state, advanced }
  }

  const controlPortCandidate =
    spec.control_port ??
    extractPort(spec.bind_addr) ??
    extractPort(spec.remote_addr)
  if (controlPortCandidate) {
    state.control_port = String(controlPortCandidate)
  }

  state.listen_ip = spec.listen_ip ?? state.listen_ip

  const publicPortCandidate =
    spec.public_port ??
    spec.listen_port ??
    derivePortFromPorts(spec.ports)
  if (publicPortCandidate) {
    state.public_port = String(publicPortCandidate)
  }

  if (spec.target_host) {
    state.target_host = String(spec.target_host)
  } else if (typeof spec.target_addr === 'string') {
    const parsed = parseAddressPort(spec.target_addr)
    state.target_host = parsed.host
  }

  const targetPortCandidate =
    spec.target_port ??
    (typeof spec.target_addr === 'string'
      ? parseAddressPort(spec.target_addr).port
      : undefined)
  if (targetPortCandidate) {
    state.target_port = String(targetPortCandidate)
  }

  state.token = spec.token ?? ''
  state.public_host = spec.public_host ?? ''
  state.remote_addr = spec.remote_addr ?? ''
  state.accept_udp = Boolean(spec.accept_udp)

  if (Array.isArray(spec.ports) && spec.ports.length > 0) {
    advanced.customPorts = spec.ports.join('\n')
  }

  const serverOptions = spec.server_options || {}
  Object.entries(advanced.server).forEach(([key, defaultValue]) => {
    const value = serverOptions[key]
    if (value === undefined || value === null) {
      return
    }
    if (typeof defaultValue === 'boolean') {
      advanced.server[key as keyof BackhaulAdvancedServerState] = Boolean(value)
    } else {
      advanced.server[key as keyof BackhaulAdvancedServerState] = String(value)
    }
  })

  const clientOptions = spec.client_options || {}
  Object.entries(advanced.client).forEach(([key, defaultValue]) => {
    const value = clientOptions[key]
    if (value === undefined || value === null) {
      return
    }
    if (typeof defaultValue === 'boolean') {
      advanced.client[key as keyof BackhaulAdvancedClientState] = Boolean(value)
    } else {
      advanced.client[key as keyof BackhaulAdvancedClientState] = String(value)
    }
  })

  return { state, advanced }
}

function extractPort(value: unknown): string | undefined {
  if (typeof value === 'number') {
    return value.toString()
  }
  if (typeof value === 'string') {
    const parts = value.split(':')
    const port = parts[parts.length - 1]
    if (port && !Number.isNaN(Number(port))) {
      return port
    }
  }
  return undefined
}

function derivePortFromPorts(value: unknown): string | undefined {
  if (!Array.isArray(value) || value.length === 0) {
    return undefined
  }
  const first = value[0]
  if (typeof first !== 'string') {
    return undefined
  }
  const [left] = first.split('=')
  if (!left) {
    return undefined
  }
  const segments = left.split(':')
  const port = segments[segments.length - 1]
  return port && !Number.isNaN(Number(port)) ? port : undefined
}

export default Tunnels
