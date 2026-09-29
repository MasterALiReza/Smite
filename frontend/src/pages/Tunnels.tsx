import { useEffect, useState, useRef } from 'react'
import { Plus, Trash2, Edit2, RotateCw, CheckCircle2, XCircle, Clock, Loader2, X, Network, Zap, AlertTriangle, Activity, Folder, FolderPlus, FolderMinus, CheckSquare, Tag, Layers, Shield, Globe, Gamepad2, Sliders, Sparkles, Rocket, Fingerprint, Scale, ArrowLeftRight, ShieldCheck, EyeOff, Gauge, Radio, Key, Lock, Server, Cpu, Terminal, RefreshCw, Settings2, RadioTower, Wifi, Info, Dices, Search, LayoutGrid, List, ArrowRight, ChevronDown, Check } from 'lucide-react'
import api from '../api/client'
import { parseAddressPort, formatAddressPort } from '../utils/addressUtils'
import { useLanguage } from '../contexts/LanguageContext'
import { useToast } from '../contexts/ToastContext'
import { EmptyState } from '../components/EmptyState'
import { LatencyBadge } from '../components/LatencyBadge'

// ─── Reapply Progress Types ────────────────────────────────────────────────
type ReapplyStatus = 'pending' | 'running' | 'success' | 'error'

interface TunnelReapplyState {
  id: string
  name: string
  status: ReapplyStatus
  error?: string
}

export interface TunnelCategory {
  id: string
  name: string
  color: string
  description?: string
  tunnel_count: number
  created_at?: string
}

interface Tunnel {
  id: string
  name: string
  core: string
  type: string
  node_id: string
  iran_node_id?: string
  foreign_node_id?: string
  spec: Record<string, any>
  status: string
  error_message?: string | null
  revision: number
  category?: string | null
  created_at: string
  updated_at: string
  [key: string]: any
}

type BackhaulTransport = 'tcp' | 'udp' | 'ws' | 'wsmux' | 'tcpmux' | 'wss' | 'wssmux'

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
  gaming_mode?: boolean
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
  mss: string
  so_rcvbuf: string
  so_sndbuf: string
  mux_version: string
  mux_framesize: string
  mux_recievebuffer: string
  mux_streambuffer: string
}

interface BackhaulAdvancedClientState {
  connection_pool: string
  retry_interval: string
  dial_timeout: string
  keepalive_period: string
  heartbeat: string
  channel_size: string
  log_level: string
  nodelay: boolean
  aggressive_pool: boolean
  edge_ip: string
  skip_optz: boolean
  mss: string
  so_rcvbuf: string
  so_sndbuf: string
  mux_version: string
  mux_framesize: string
  mux_recievebuffer: string
  mux_streambuffer: string
}

interface BackhaulAdvancedState {
  server: BackhaulAdvancedServerState
  client: BackhaulAdvancedClientState
  customPorts: string
}

export const generateRandomControlPort = (): string => {
  return String(Math.floor(20000 + Math.random() * 25000))
}

const createDefaultBackhaulState = (): BackhaulFormState => ({
  transport: 'tcp',
  control_port: generateRandomControlPort(),
  public_port: '443',
  listen_ip: '0.0.0.0',
  public_host: '',
  remote_addr: '',
  target_host: '127.0.0.1',
  target_port: '8080',
  token: '',
  accept_udp: false,
  gaming_mode: false,
})

const createDefaultBackhaulAdvancedState = (): BackhaulAdvancedState => ({
  server: {
    keepalive_period: '20',
    heartbeat: '20',
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
    mss: '',
    so_rcvbuf: '',
    so_sndbuf: '',
    mux_version: '1',
    mux_framesize: '32768',
    mux_recievebuffer: '',
    mux_streambuffer: '',
  },
  client: {
    connection_pool: '8',
    retry_interval: '3',
    dial_timeout: '10',
    keepalive_period: '20',
    heartbeat: '20',
    channel_size: '2048',
    log_level: 'info',
    nodelay: true,
    aggressive_pool: true,
    edge_ip: '',
    skip_optz: false,
    mss: '',
    so_rcvbuf: '',
    so_sndbuf: '',
    mux_version: '1',
    mux_framesize: '32768',
    mux_recievebuffer: '',
    mux_streambuffer: '',
  },
  customPorts: '',
})

const numericServerKeys = new Set([
  'keepalive_period',
  'heartbeat',
  'channel_size',
  'mux_con',
  'web_port',
  'mss',
  'so_rcvbuf',
  'so_sndbuf',
  'mux_version',
  'mux_framesize',
  'mux_recievebuffer',
  'mux_streambuffer',
])
const booleanServerKeys = new Set(['nodelay', 'skip_optz', 'sniffer', 'proxy_protocol'])
const stringServerKeys = new Set(['log_level', 'tls_cert', 'tls_key', 'sniffer_log'])

const numericClientKeys = new Set([
  'connection_pool',
  'retry_interval',
  'dial_timeout',
  'keepalive_period',
  'heartbeat',
  'channel_size',
  'mss',
  'so_rcvbuf',
  'so_sndbuf',
  'mux_version',
  'mux_framesize',
  'mux_recievebuffer',
  'mux_streambuffer',
])
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

export const getCategoryColorClasses = (color: string = 'blue') => {
  const map: Record<string, { bg: string; text: string; border: string; activeBg: string }> = {
    blue: { bg: 'bg-blue-50 dark:bg-blue-950/40', text: 'text-blue-700 dark:text-blue-300', border: 'border-blue-200 dark:border-blue-800', activeBg: 'bg-blue-600 text-white border-blue-600' },
    emerald: { bg: 'bg-emerald-50 dark:bg-emerald-950/40', text: 'text-emerald-700 dark:text-emerald-300', border: 'border-emerald-200 dark:border-emerald-800', activeBg: 'bg-emerald-600 text-white border-emerald-600' },
    purple: { bg: 'bg-purple-50 dark:bg-purple-950/40', text: 'text-purple-700 dark:text-purple-300', border: 'border-purple-200 dark:border-purple-800', activeBg: 'bg-purple-600 text-white border-purple-600' },
    amber: { bg: 'bg-amber-50 dark:bg-amber-950/40', text: 'text-amber-700 dark:text-amber-300', border: 'border-amber-200 dark:border-amber-800', activeBg: 'bg-amber-600 text-white border-amber-600' },
    rose: { bg: 'bg-rose-50 dark:bg-rose-950/40', text: 'text-rose-700 dark:text-rose-300', border: 'border-rose-200 dark:border-rose-800', activeBg: 'bg-rose-600 text-white border-rose-600' },
    cyan: { bg: 'bg-cyan-50 dark:bg-cyan-950/40', text: 'text-cyan-700 dark:text-cyan-300', border: 'border-cyan-200 dark:border-cyan-800', activeBg: 'bg-cyan-600 text-white border-cyan-600' },
    indigo: { bg: 'bg-indigo-50 dark:bg-indigo-950/40', text: 'text-indigo-700 dark:text-indigo-300', border: 'border-indigo-200 dark:border-indigo-800', activeBg: 'bg-indigo-600 text-white border-indigo-600' },
  }
  return map[color] || map.blue
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
  // Per-tunnel delete loading (stores the tunnel id being deleted)
  const [deletingTunnelId, setDeletingTunnelId] = useState<string | null>(null)
  // Reapply All progress modal
  const [reapplyAllProgress, setReapplyAllProgress] = useState<TunnelReapplyState[] | null>(null)
  const [reapplyAllDone, setReapplyAllDone] = useState(false)
  const [showConfirmReapplyAll, setShowConfirmReapplyAll] = useState(false)
  const [showConfirmReapplySelected, setShowConfirmReapplySelected] = useState(false)
  const [livePingEnabled, setLivePingEnabled] = useState(true)

  // ─── Category & Multi-Selection States ───────────────────────
  const [categories, setCategories] = useState<TunnelCategory[]>([])
  const [activeCategoryTab, setActiveCategoryTab] = useState<string>('all')
  const [showCategoryDropdown, setShowCategoryDropdown] = useState(false)
  const categoryDropdownRef = useRef<HTMLDivElement>(null)
  const [selectedTunnelIds, setSelectedTunnelIds] = useState<Set<string>>(new Set())
  const [showCreateCategoryModal, setShowCreateCategoryModal] = useState(false)
  const [showAssignCategoryModal, setShowAssignCategoryModal] = useState(false)
  const [newCategoryName, setNewCategoryName] = useState('')
  const [newCategoryColor, setNewCategoryColor] = useState('blue')

  // Close category dropdown on outside click or Escape key
  useEffect(() => {
    if (!showCategoryDropdown) return
    const handleClickOutside = (event: MouseEvent) => {
      if (categoryDropdownRef.current && !categoryDropdownRef.current.contains(event.target as Node)) {
        setShowCategoryDropdown(false)
      }
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setShowCategoryDropdown(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [showCategoryDropdown])

  // ─── Search State ───────────────────────────────────────────
  const [searchQuery, setSearchQuery] = useState('')

  // ─── View Mode (List vs Grid) ──────────────────────────────
  const [viewMode, setViewMode] = useState<'list' | 'grid'>(() => {
    try {
      return (localStorage.getItem('smite_tunnel_view_mode') as 'list' | 'grid') || 'list'
    } catch {
      return 'list'
    }
  })

  const handleSetViewMode = (mode: 'list' | 'grid') => {
    setViewMode(mode)
    try {
      localStorage.setItem('smite_tunnel_view_mode', mode)
    } catch {
      // Silently ignore storage errors
    }
  }


  useEffect(() => {
    fetchData()
    const params = new URLSearchParams(window.location.search)
    if (params.get('create') === 'true') {
      setShowAddModal(true)
      window.history.replaceState({}, '', '/tunnels')
    }
  }, [])

  // ─── Live 2-second Latency Auto-Refresh ───────────────────────
  useEffect(() => {
    if (!livePingEnabled) return

    let isMounted = true
    let intervalId: any = null

    const fetchLatencies = async () => {
      if (document.hidden) return
      try {
        const response = await api.get('/tunnels/latencies')
        if (!isMounted || !response.data?.tunnels) return

        const latMap: Record<string, number> = response.data.tunnels
        setTunnels(prev =>
          prev.map(t => {
            if (latMap[t.id] !== undefined && t.spec?.latency_ms !== latMap[t.id]) {
              return {
                ...t,
                spec: {
                  ...t.spec,
                  latency_ms: latMap[t.id],
                },
              }
            }
            return t
          })
        )
      } catch (err) {
        // Silently continue
      }
    }

    intervalId = setInterval(fetchLatencies, 2000)

    const handleVisibilityChange = () => {
      if (!document.hidden) {
        fetchLatencies()
      }
    }
    document.addEventListener('visibilitychange', handleVisibilityChange)

    return () => {
      isMounted = false
      if (intervalId) clearInterval(intervalId)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [livePingEnabled])

  const fetchData = async () => {
    try {
      const [tunnelsRes, nodesRes, categoriesRes] = await Promise.all([
        api.get('/tunnels'),
        api.get('/nodes'),
        api.get('/tunnels/categories').catch(() => ({ data: [] })),
      ])
      setTunnels(tunnelsRes.data)
      setCategories(categoriesRes.data || [])
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

  // ─── Category & Bulk Handlers ──────────────────────────────────────────
  const createCategory = async (name: string, color: string = 'blue') => {
    try {
      const res = await api.post('/tunnels/categories', { name, color })
      setCategories(prev => [...prev.filter(c => c.name !== name), res.data])
      showToast('success', t.tunnels.categoryCreated || 'Category created', name)
      return res.data
    } catch (e: any) {
      showToast('error', 'Error', e.response?.data?.detail || 'Failed to create category')
      return null
    }
  }

  const deleteCategory = async (name: string) => {
    const ok = await showConfirm({
      title: t.tunnels.deleteCategory || 'Delete Category',
      message: `Delete "${name}" category? Tunnels in this category will become uncategorized.`,
      variant: 'danger',
      confirmText: 'Delete'
    })
    if (!ok) return
    try {
      await api.delete(`/tunnels/categories/${encodeURIComponent(name)}`)
      fetchData()
      if (activeCategoryTab === name) setActiveCategoryTab('all')
      showToast('success', 'Deleted', `Category "${name}" deleted`)
    } catch (e: any) {
      showToast('error', 'Error', e.response?.data?.detail || 'Failed to delete category')
    }
  }

  const bulkAssignCategory = async (categoryName: string | null) => {
    if (selectedTunnelIds.size === 0) return
    try {
      await api.post('/tunnels/bulk-category', {
        tunnel_ids: Array.from(selectedTunnelIds),
        category: categoryName
      })
      showToast('success', 'Category Updated', `${selectedTunnelIds.size} tunnels updated`)
      setSelectedTunnelIds(new Set())
      setShowAssignCategoryModal(false)
      fetchData()
    } catch (e: any) {
      showToast('error', 'Error', e.response?.data?.detail || 'Failed to assign category')
    }
  }

  // ─── Multi-Selection & Selective Reapply ──────────────────────────────
  const toggleSelectTunnel = (id: string, e?: React.MouseEvent) => {
    if (e) e.stopPropagation()
    setSelectedTunnelIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // ─── Search Port Token Extractor ──────────────────────────────────────
  const getTunnelSearchPorts = (tunnel: Tunnel): string[] => {
    const portList: string[] = []
    const addPort = (val: any) => {
      if (val === undefined || val === null) return
      const s = String(val).trim()
      if (s && !portList.includes(s)) portList.push(s)
    }

    if (tunnel.spec?.ports) {
      if (Array.isArray(tunnel.spec.ports)) {
        tunnel.spec.ports.forEach((p: any) => {
          if (typeof p === 'string') {
            const tokens = p.split(/[:=,\s]+/)
            tokens.forEach(tok => addPort(tok))
          } else if (typeof p === 'object' && p !== null) {
            if (p.local) addPort(p.local)
            if (p.remote) addPort(p.remote)
            if (p.port) addPort(p.port)
          } else {
            addPort(p)
          }
        })
      } else if (typeof tunnel.spec.ports === 'string') {
        tunnel.spec.ports.split(/[,\s]+/).forEach(tok => {
          const subTokens = tok.split(/[:=]/)
          subTokens.forEach(st => addPort(st))
        })
      }
    }

    addPort(tunnel.spec?.listen_port)
    addPort(tunnel.spec?.remote_port)
    addPort(tunnel.spec?.bind_port)
    addPort(tunnel.spec?.control_port)
    addPort(tunnel.spec?.public_port)
    addPort(tunnel.spec?.client_port)
    addPort(tunnel.spec?.server_port)
    addPort(tunnel.spec?.port)

    if (typeof tunnel.spec?.bind_addr === 'string' && tunnel.spec.bind_addr.includes(':')) {
      addPort(tunnel.spec.bind_addr.split(':').pop())
    }
    if (typeof tunnel.spec?.remote_addr === 'string' && tunnel.spec.remote_addr.includes(':')) {
      addPort(tunnel.spec.remote_addr.split(':').pop())
    }
    if (typeof tunnel.spec?.target_addr === 'string' && tunnel.spec.target_addr.includes(':')) {
      addPort(tunnel.spec.target_addr.split(':').pop())
    }

    return portList
  }

  const filteredTunnels = tunnels.filter(t => {
    // 1. Category Filter
    if (activeCategoryTab !== 'all') {
      if (activeCategoryTab === 'uncategorized' && t.category) return false
      if (activeCategoryTab !== 'uncategorized' && t.category !== activeCategoryTab) return false
    }

    // 2. Search Query Filter
    const query = searchQuery.trim().toLowerCase()
    if (!query) return true

    // Match tunnel name
    if (t.name?.toLowerCase().includes(query)) return true

    // Match any port associated with this tunnel
    const ports = getTunnelSearchPorts(t)
    if (ports.some(p => p.toLowerCase().includes(query))) return true

    // Match core or protocol
    if (t.core?.toLowerCase().includes(query)) return true
    if (t.type?.toLowerCase().includes(query)) return true

    // Match Iran or Foreign node name
    const iranNode = nodes.find(n => n.id === t.iran_node_id || n.id === t.node_id)
    if (iranNode?.name?.toLowerCase().includes(query)) return true
    const foreignServer = servers.find(s => s.id === t.foreign_node_id)
    if (foreignServer?.name?.toLowerCase().includes(query)) return true

    return false
  })

  const toggleSelectAllFiltered = () => {
    const filteredIds = filteredTunnels.map(t => t.id)
    const allSelected = filteredIds.length > 0 && filteredIds.every(id => selectedTunnelIds.has(id))
    setSelectedTunnelIds(prev => {
      const next = new Set(prev)
      if (allSelected) {
        filteredIds.forEach(id => next.delete(id))
      } else {
        filteredIds.forEach(id => next.add(id))
      }
      return next
    })
  }

  const handleReapplySelected = () => {
    if (selectedTunnelIds.size === 0) return
    setShowConfirmReapplySelected(true)
  }

  const startReapplySelected = async () => {
    setShowConfirmReapplySelected(false)
    const targets = tunnels.filter(t => selectedTunnelIds.has(t.id))
    if (targets.length === 0) return

    const initial: TunnelReapplyState[] = targets.map(t => ({
      id: t.id,
      name: t.name,
      status: 'pending',
    }))
    setReapplyAllProgress(initial)
    setReapplyAllDone(false)

    let current = [...initial]
    for (let i = 0; i < targets.length; i++) {
      const tunnel = targets[i]
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

  const deleteTunnel = async (id: string) => {
    const target = tunnels.find(t => t.id === id)
    const targetName = target?.name || 'this tunnel'

    const confirmed = await showConfirm({
      title: 'Delete Tunnel',
      message: `Are you sure you want to delete "${targetName}"? The connection will be permanently stopped and removed.`,
      variant: 'danger',
      confirmText: 'Delete Tunnel'
    })
    if (!confirmed) return
    
    setDeletingTunnelId(id)
    showToast('info', 'Deleting Tunnel', `Removing "${targetName}"...`, 2500)

    try {
      await api.delete(`/tunnels/${id}`)
      // Optimistic removal: remove immediately from UI so feedback is instantaneous
      setTunnels(prev => prev.filter(t => t.id !== id))
      setSelectedTunnelIds(prev => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
      showToast('success', 'Tunnel Deleted', `"${targetName}" was deleted successfully`)
      fetchData()
    } catch (error: any) {
      console.error('Failed to delete tunnel:', error)
      const errorMsg = error.response?.data?.detail || error.message || 'Failed to delete tunnel'
      showToast('error', 'Delete Failed', errorMsg)
      fetchData()
    } finally {
      setDeletingTunnelId(null)
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

  const [testingTunnelId, setTestingTunnelId] = useState<string | null>(null)

  const handleTestActiveTunnel = async (tunnel: Tunnel) => {
    setTestingTunnelId(tunnel.id)
    try {
      const response = await api.post(`/tunnels/${tunnel.id}/test`)
      if (response.data?.status === 'active') {
        const latency = response.data.latency_ms
        showToast('success', 'Tunnel Connection Active', `${tunnel.name}: ${latency ? `${latency} ms` : 'Online'} — Healthy and routing traffic`)
        setTunnels(prev => prev.map(t => t.id === tunnel.id ? { ...t, spec: { ...t.spec, latency_ms: latency } } : t))
      } else {
        showToast('error', 'Tunnel Ping Failed', response.data?.message || 'Tunnel is not responding')
      }
    } catch (error: any) {
      const msg = error.response?.data?.detail || error.message || 'Could not test tunnel'
      showToast('error', 'Test Failed', msg)
    } finally {
      setTestingTunnelId(null)
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
      <div className="flex flex-col items-center justify-center min-h-[50vh] gap-3 text-slate-500 dark:text-slate-400">
        <div className="relative">
          <div className="w-10 h-10 border-2 border-indigo-500/20 border-t-indigo-500 rounded-full animate-spin" />
        </div>
        <span className="text-xs font-mono uppercase tracking-wider">{t.tunnels.loadingTunnels}</span>
      </div>
    )
  }

  return (
    <div className="w-full max-w-7xl mx-auto space-y-6 sm:space-y-8 animate-fade-in">
      {/* ── Header ──────────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-2 border-b border-slate-200/60 dark:border-white/[0.06]">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="inline-flex items-center justify-center p-1.5 rounded-lg bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border border-indigo-500/20">
              <Network className="w-4 h-4" />
            </span>
            <span className="text-xs font-mono font-medium text-indigo-600 dark:text-indigo-400 uppercase tracking-wider">
              Network Infrastructure
            </span>
          </div>
          <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 dark:text-white tracking-tight">
            {t.tunnels.title}
          </h1>
          <p className="text-xs sm:text-sm text-slate-500 dark:text-slate-400 mt-0.5">
            {t.tunnels.subtitle}
          </p>
        </div>

        <div className="flex items-center gap-2 sm:gap-3">
          {/* Live Ping Toggle */}
          <button
            type="button"
            onClick={() => setLivePingEnabled(prev => !prev)}
            className={`px-3.5 py-2.5 rounded-2xl border transition-all font-semibold text-xs flex items-center gap-2 shadow-xs min-h-[44px] active:scale-95 cursor-pointer ${
              livePingEnabled
                ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-600 dark:text-emerald-400'
                : 'bg-white dark:bg-[#12161f] border-slate-200 dark:border-white/[0.07] text-slate-500 dark:text-slate-400'
            }`}
            title={livePingEnabled ? 'Live ping auto-refreshing every 2s (Click to pause)' : 'Live ping paused (Click to enable)'}
          >
            <span className="relative flex h-2 w-2">
              {livePingEnabled && (
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
              )}
              <span className={`relative inline-flex rounded-full h-2 w-2 ${livePingEnabled ? 'bg-emerald-500' : 'bg-slate-400'}`}></span>
            </span>
            <span className="font-mono">Live Ping: {livePingEnabled ? '2s' : 'OFF'}</span>
          </button>
        </div>
      </div>

      {/* ── Sticky Toolbar: Switches between Normal Mode and Contextual Bulk Selection Mode ── */}
      <div
        className={`sticky top-2 z-20 transition-all duration-200 rounded-2xl border p-2 backdrop-blur-xl shadow-md ${
          selectedTunnelIds.size > 0
            ? 'bg-indigo-50/90 dark:bg-[#0f1424]/95 border-indigo-200/80 dark:border-indigo-500/25 ring-2 ring-indigo-500/20'
            : 'bg-white/95 dark:bg-[#0c101d]/95 border-slate-200/80 dark:border-white/[0.08] ring-1 ring-black/5 dark:ring-white/5'
        }`}
      >
        {selectedTunnelIds.size > 0 ? (
          /* ── Contextual Bulk Selection Mode (Linear / Gmail Pattern) ── */
          <div className="flex items-center justify-between gap-2 px-1 py-0.5 animate-fade-in">
            {/* Left: Master Toggle Checkbox + Counter Pill + Clear Button */}
            <div className="flex items-center gap-2 sm:gap-3 min-w-0">
              <button
                type="button"
                onClick={toggleSelectAllFiltered}
                aria-label={filteredTunnels.every(t => selectedTunnelIds.has(t.id)) ? (t.tunnels.deselectAll || 'Deselect All') : (t.tunnels.selectAll || 'Select All')}
                className="h-9 px-3 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs flex items-center gap-2 shadow-xs transition-all active:scale-95 cursor-pointer shrink-0"
                title={filteredTunnels.every(t => selectedTunnelIds.has(t.id)) ? (t.tunnels.deselectAll || 'Deselect All') : (t.tunnels.selectAll || 'Select All')}
              >
                <CheckSquare size={15} />
                <span className="hidden sm:inline">
                  {filteredTunnels.every(t => selectedTunnelIds.has(t.id))
                    ? (t.tunnels.deselectAll || 'Deselect All')
                    : (t.tunnels.selectAll || 'Select All')}
                </span>
              </button>

              <div className="flex items-center gap-1.5 font-mono text-xs font-bold text-indigo-700 dark:text-indigo-300 bg-indigo-500/10 dark:bg-indigo-500/20 px-3 py-1.5 rounded-xl border border-indigo-500/20 shrink-0">
                <span className="text-sm tabular-nums">{selectedTunnelIds.size}</span>
                <span className="font-sans font-medium text-slate-500 dark:text-slate-400 text-xs">
                  / {filteredTunnels.length}
                </span>
                <span className="font-sans font-medium text-slate-600 dark:text-slate-300 text-xs hidden sm:inline">
                  {t.tunnels.selectedCount || 'selected'}
                </span>
              </div>

              <button
                type="button"
                onClick={() => setSelectedTunnelIds(new Set())}
                className="text-xs font-semibold text-slate-500 hover:text-rose-600 dark:text-slate-400 dark:hover:text-rose-400 transition-colors hidden md:inline cursor-pointer"
              >
                {t.tunnels.deselectAll || 'Clear selection'}
              </button>
            </div>

            {/* Right: Actions on Selected Tunnels */}
            <div className="flex items-center gap-1.5 sm:gap-2 shrink-0">
              <button
                type="button"
                onClick={handleReapplySelected}
                disabled={!!reapplyAllProgress && !reapplyAllDone}
                aria-label={t.tunnels.reapplySelected || 'Reapply Selected'}
                className="h-9 px-3 sm:px-4 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs flex items-center gap-2 transition-all shadow-xs active:scale-95 disabled:opacity-50 cursor-pointer whitespace-nowrap"
                title={t.tunnels.reapplySelected || 'Reapply Selected'}
              >
                <RotateCw size={14} className={!!reapplyAllProgress && !reapplyAllDone ? 'animate-spin' : ''} />
                <span>{t.tunnels.reapplySelected || 'Reapply Selected'}</span>
                <span className="font-mono tabular-nums bg-white/20 px-1.5 py-0.5 rounded-md text-[11px]">
                  {selectedTunnelIds.size}
                </span>
              </button>

              <button
                type="button"
                onClick={() => setShowAssignCategoryModal(true)}
                aria-label={t.tunnels.moveToCategory || 'Move to Category'}
                className="h-9 px-3 rounded-xl bg-white dark:bg-[#161c28] hover:bg-slate-50 dark:hover:bg-white/[0.08] text-slate-700 dark:text-slate-200 border border-slate-200/80 dark:border-white/[0.08] font-semibold text-xs flex items-center gap-1.5 transition-all active:scale-95 cursor-pointer whitespace-nowrap"
                title={t.tunnels.moveToCategory || 'Move to Category'}
              >
                <Folder size={14} />
                <span className="hidden sm:inline">{t.tunnels.moveToCategory || 'Category'}</span>
              </button>

              <button
                type="button"
                onClick={() => setSelectedTunnelIds(new Set())}
                aria-label={t.tunnels.deselectAll || 'Cancel selection'}
                className="h-9 w-9 rounded-xl bg-white dark:bg-[#161c28] hover:bg-rose-50 dark:hover:bg-rose-950/30 text-slate-400 hover:text-rose-600 dark:hover:text-rose-400 border border-slate-200/80 dark:border-white/[0.08] flex items-center justify-center transition-all active:scale-95 cursor-pointer"
                title={t.tunnels.deselectAll || 'Cancel selection'}
              >
                <X size={15} />
              </button>
            </div>
          </div>
        ) : (
          /* ── Normal Browsing Mode ── */
          <div className="flex flex-wrap items-center justify-between gap-2">
            {/* Left Controls: Search + Category Dropdown + View Mode Toggle */}
            <div className="flex items-center gap-2 flex-wrap sm:flex-nowrap flex-1 min-w-0">
              {/* Port & Name Search Input */}
              <div className="relative w-full sm:w-52 md:w-60 lg:w-64 shrink-0">
                <Search size={14} className="absolute start-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder={t.tunnels.searchPlaceholder || 'Search name or port...'}
                  aria-label={t.tunnels.searchPlaceholder || 'Search name or port'}
                  className="w-full h-9 ps-9 pe-8 bg-slate-50/80 dark:bg-[#161c28] border border-slate-200/80 dark:border-white/[0.08] focus:border-indigo-500 dark:focus:border-indigo-400 focus:bg-white dark:focus:bg-[#161c28] focus:ring-2 focus:ring-indigo-500/20 rounded-xl text-xs font-medium text-slate-900 dark:text-slate-100 placeholder:text-slate-400 transition-all outline-hidden shadow-2xs"
                />
                {searchQuery && (
                  <button
                    type="button"
                    onClick={() => setSearchQuery('')}
                    aria-label={t.tunnels.clearSearch || 'Clear search'}
                    className="absolute end-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 p-0.5 rounded-md transition-colors cursor-pointer"
                    title={t.tunnels.clearSearch || 'Clear search'}
                  >
                    <X size={13} />
                  </button>
                )}
              </div>

              {/* Category Dropdown */}
              <div ref={categoryDropdownRef} className="relative shrink-0">
                {(() => {
                  const currentCategory = categories.find(c => c.name === activeCategoryTab)
                  const categoryCount = activeCategoryTab === 'all'
                    ? tunnels.length
                    : activeCategoryTab === 'uncategorized'
                    ? tunnels.filter(t => !t.category).length
                    : tunnels.filter(t => t.category === activeCategoryTab).length

                  return (
                    <>
                      <button
                        type="button"
                        onClick={() => setShowCategoryDropdown(prev => !prev)}
                        aria-expanded={showCategoryDropdown}
                        aria-haspopup="true"
                        className={`h-9 px-3 rounded-xl text-xs font-semibold transition-all duration-150 flex items-center gap-2 border select-none cursor-pointer ${
                          showCategoryDropdown || activeCategoryTab !== 'all'
                            ? 'bg-white dark:bg-[#161c28] border-indigo-500/60 dark:border-indigo-400/60 text-slate-900 dark:text-white shadow-2xs ring-2 ring-indigo-500/15'
                            : 'bg-white dark:bg-[#161c28] border-slate-200/80 dark:border-white/[0.08] text-slate-700 dark:text-slate-300 hover:border-slate-300 dark:hover:border-white/20'
                        }`}
                        title={t.tunnels.categories || 'Categories'}
                      >
                        {activeCategoryTab === 'all' ? (
                          <Layers size={14} className="text-indigo-600 dark:text-indigo-400 shrink-0" />
                        ) : activeCategoryTab === 'uncategorized' ? (
                          <FolderMinus size={14} className="text-slate-500 dark:text-slate-400 shrink-0" />
                        ) : (
                          <Tag size={13} className={`shrink-0 ${getCategoryColorClasses(currentCategory?.color || 'blue').text}`} />
                        )}

                        <span className="truncate max-w-[120px] sm:max-w-[140px]">
                          {activeCategoryTab === 'all'
                            ? (t.tunnels.allTunnels || 'All Tunnels')
                            : activeCategoryTab === 'uncategorized'
                            ? (t.tunnels.uncategorized || 'Uncategorized')
                            : (currentCategory?.name || activeCategoryTab)}
                        </span>

                        <span className="px-1.5 py-0.5 rounded-full text-[10px] font-mono font-bold leading-none tabular-nums bg-slate-100 dark:bg-white/10 text-slate-600 dark:text-slate-300">
                          {categoryCount}
                        </span>

                        <ChevronDown size={13} className={`text-slate-400 transition-transform duration-200 shrink-0 ${showCategoryDropdown ? 'rotate-180 text-indigo-600 dark:text-indigo-400' : ''}`} />
                      </button>

                      {/* Dropdown Menu Popover */}
                      {showCategoryDropdown && (
                        <div className="absolute start-0 top-full mt-1.5 w-64 bg-white dark:bg-[#161c28] border border-slate-200/90 dark:border-white/[0.1] rounded-2xl shadow-xl z-50 py-1.5 backdrop-blur-md animate-in fade-in-50 zoom-in-95 duration-100 max-h-80 overflow-y-auto">
                          {/* Header */}
                          <div className="px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-400 dark:text-slate-500">
                            {t.tunnels.categories || 'Categories'}
                          </div>

                          {/* Option: All Tunnels */}
                          <button
                            type="button"
                            onClick={() => {
                              setActiveCategoryTab('all')
                              setShowCategoryDropdown(false)
                            }}
                            className={`w-full px-3 py-2 text-xs font-semibold flex items-center justify-between gap-2 transition-colors cursor-pointer ${
                              activeCategoryTab === 'all'
                                ? 'bg-indigo-50 dark:bg-indigo-950/40 text-indigo-600 dark:text-indigo-400'
                                : 'text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04]'
                            }`}
                          >
                            <div className="flex items-center gap-2 min-w-0">
                              <Layers size={14} className={activeCategoryTab === 'all' ? 'text-indigo-600 dark:text-indigo-400' : 'text-slate-400'} />
                              <span className="truncate">{t.tunnels.allTunnels || 'All Tunnels'}</span>
                            </div>
                            <div className="flex items-center gap-1.5 shrink-0">
                              <span className="px-1.5 py-0.5 rounded-full text-[10px] font-mono font-bold leading-none tabular-nums bg-slate-100 dark:bg-white/10 text-slate-600 dark:text-slate-400">
                                {tunnels.length}
                              </span>
                              {activeCategoryTab === 'all' && <Check size={14} className="text-indigo-600 dark:text-indigo-400" />}
                            </div>
                          </button>

                          {/* User Categories */}
                          {categories.map((cat) => {
                            const count = tunnels.filter(t => t.category === cat.name).length
                            const colorStyle = getCategoryColorClasses(cat.color)
                            const isActive = activeCategoryTab === cat.name
                            return (
                              <div
                                key={cat.id}
                                className={`group/catitem px-3 py-1.5 flex items-center justify-between gap-2 transition-colors cursor-pointer ${
                                  isActive
                                    ? 'bg-indigo-50 dark:bg-indigo-950/40 text-indigo-600 dark:text-indigo-400'
                                    : 'text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04]'
                                }`}
                                onClick={() => {
                                  setActiveCategoryTab(cat.name)
                                  setShowCategoryDropdown(false)
                                }}
                              >
                                <div className="flex items-center gap-2 min-w-0">
                                  <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${colorStyle.bg} border ${colorStyle.border}`} />
                                  <span className="text-xs font-semibold truncate">{cat.name}</span>
                                </div>
                                <div className="flex items-center gap-1.5 shrink-0">
                                  <span className="px-1.5 py-0.5 rounded-full text-[10px] font-mono font-bold leading-none tabular-nums bg-slate-100 dark:bg-white/10 text-slate-600 dark:text-slate-400">
                                    {count}
                                  </span>
                                  {isActive && <Check size={14} className="text-indigo-600 dark:text-indigo-400" />}
                                  <button
                                    type="button"
                                    onClick={(e) => {
                                      e.stopPropagation()
                                      deleteCategory(cat.name)
                                    }}
                                    className="p-1 rounded-md text-slate-400 hover:text-rose-600 dark:hover:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/40 opacity-0 group-hover/catitem:opacity-100 transition-opacity cursor-pointer"
                                    title={`${t.tunnels.deleteCategory || 'Delete'} "${cat.name}"`}
                                    aria-label={`${t.tunnels.deleteCategory || 'Delete'} "${cat.name}"`}
                                  >
                                    <X size={12} strokeWidth={2.5} />
                                  </button>
                                </div>
                              </div>
                            )
                          })}

                          {/* Uncategorized Option (if any) */}
                          {tunnels.some(t => !t.category) && (
                            <button
                              type="button"
                              onClick={() => {
                                setActiveCategoryTab('uncategorized')
                                setShowCategoryDropdown(false)
                              }}
                              className={`w-full px-3 py-2 text-xs font-semibold flex items-center justify-between gap-2 transition-colors cursor-pointer ${
                                activeCategoryTab === 'uncategorized'
                                  ? 'bg-indigo-50 dark:bg-indigo-950/40 text-indigo-600 dark:text-indigo-400'
                                  : 'text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04]'
                              }`}
                            >
                              <div className="flex items-center gap-2 min-w-0">
                                <FolderMinus size={14} className="text-slate-400 shrink-0" />
                                <span className="truncate">{t.tunnels.uncategorized || 'Uncategorized'}</span>
                              </div>
                              <div className="flex items-center gap-1.5 shrink-0">
                                <span className="px-1.5 py-0.5 rounded-full text-[10px] font-mono font-bold leading-none tabular-nums bg-slate-100 dark:bg-white/10 text-slate-600 dark:text-slate-400">
                                  {tunnels.filter(t => !t.category).length}
                                </span>
                                {activeCategoryTab === 'uncategorized' && <Check size={14} className="text-indigo-600 dark:text-indigo-400" />}
                              </div>
                            </button>
                          )}

                          {/* Divider */}
                          <div className="my-1 border-t border-slate-100 dark:border-white/[0.06]" />

                          {/* Create Category Action */}
                          <button
                            type="button"
                            onClick={() => {
                              setShowCategoryDropdown(false)
                              setShowCreateCategoryModal(true)
                            }}
                            className="w-full px-3 py-2 text-xs font-semibold text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50/70 dark:hover:bg-indigo-950/30 flex items-center gap-2 transition-colors cursor-pointer"
                          >
                            <FolderPlus size={14} />
                            <span>{t.tunnels.newCategory || 'New Category'}</span>
                          </button>
                        </div>
                      )}
                    </>
                  )
                })()}
              </div>

              {/* View Mode Toggle (List / Grid) */}
              <div className="flex items-center p-0.5 rounded-xl bg-slate-100 dark:bg-[#161c28] border border-slate-200/80 dark:border-white/[0.08] shrink-0" role="group" aria-label="View mode">
                <button
                  type="button"
                  onClick={() => handleSetViewMode('list')}
                  className={`p-1.5 rounded-lg transition-all cursor-pointer ${
                    viewMode === 'list'
                      ? 'bg-white dark:bg-slate-700 text-indigo-600 dark:text-indigo-400 shadow-2xs font-semibold'
                      : 'text-slate-400 hover:text-slate-600 dark:hover:text-slate-300'
                  }`}
                  title={t.tunnels.listView || 'List View'}
                  aria-label={t.tunnels.listView || 'List View'}
                  aria-pressed={viewMode === 'list'}
                >
                  <List size={15} />
                </button>
                <button
                  type="button"
                  onClick={() => handleSetViewMode('grid')}
                  className={`p-1.5 rounded-lg transition-all cursor-pointer ${
                    viewMode === 'grid'
                      ? 'bg-white dark:bg-slate-700 text-indigo-600 dark:text-indigo-400 shadow-2xs font-semibold'
                      : 'text-slate-400 hover:text-slate-600 dark:hover:text-slate-300'
                  }`}
                  title={t.tunnels.gridView || 'Grid View'}
                  aria-label={t.tunnels.gridView || 'Grid View'}
                  aria-pressed={viewMode === 'grid'}
                >
                  <LayoutGrid size={15} />
                </button>
              </div>
            </div>

            {/* Right Action Controls: Select All + Reapply All + Create Tunnel */}
            <div className="flex items-center gap-1.5 shrink-0 justify-end ms-auto sm:ms-0">
              {/* Select All Toggle Button */}
              {filteredTunnels.length > 0 && (
                <button
                  type="button"
                  onClick={toggleSelectAllFiltered}
                  aria-label={t.tunnels.selectAll || 'Select All'}
                  className="h-9 px-2.5 sm:px-3 rounded-xl text-xs font-semibold transition-all duration-150 flex items-center gap-1.5 shrink-0 border select-none cursor-pointer active:scale-95 bg-white dark:bg-[#161c28] border-slate-200/80 dark:border-white/[0.06] text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white"
                  title={`${t.tunnels.selectAll || 'Select All'} (${filteredTunnels.length})`}
                >
                  <CheckSquare size={14} className="text-slate-400" />
                  <span className="hidden sm:inline whitespace-nowrap">
                    {`${t.tunnels.selectAll || 'Select All'} (${filteredTunnels.length})`}
                  </span>
                </button>
              )}

              {/* Reapply All */}
              <button
                type="button"
                onClick={handleReapplyAll}
                disabled={!!reapplyAllProgress && !reapplyAllDone}
                aria-label={t.tunnels.reapplyAll}
                className="h-9 px-2.5 sm:px-3 bg-emerald-600 hover:bg-emerald-500 text-white rounded-xl transition-all font-semibold shadow-xs flex items-center justify-center gap-1.5 text-xs active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer whitespace-nowrap"
                title={t.tunnels.reapplyAll}
              >
                <RotateCw size={13} className={!!reapplyAllProgress && !reapplyAllDone ? 'animate-spin' : ''} />
                <span className="hidden sm:inline">{t.tunnels.reapplyAll}</span>
              </button>

              {/* Create Tunnel */}
              <button
                type="button"
                onClick={() => setShowAddModal(true)}
                aria-label={t.tunnels.createTunnel}
                className="h-9 px-2.5 sm:px-3.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl transition-all font-semibold shadow-xs flex items-center justify-center gap-1.5 text-xs active:scale-95 cursor-pointer whitespace-nowrap"
                title={t.tunnels.createTunnel}
              >
                <Plus size={14} />
                <span className="hidden sm:inline">{t.tunnels.createTunnel}</span>
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ── Tunnel Cards ────────────────────────────────────── */}
      {filteredTunnels.length === 0 ? (
        searchQuery.trim() ? (
          <EmptyState
            icon={<Search size={32} />}
            title={t.tunnels.noMatchingTunnels || 'No tunnels match your search'}
            description={`No tunnels found with name or port matching "${searchQuery}".`}
            action={{ label: t.tunnels.clearSearch || 'Clear search', onClick: () => setSearchQuery('') }}
          />
        ) : (
          <EmptyState
            icon={<Network size={32} />}
            title="No tunnels in this category"
            description="No tunnels match the selected category filter."
            action={{ label: 'View All Tunnels', onClick: () => setActiveCategoryTab('all') }}
          />
        )
      ) : (
        <div className={viewMode === 'grid' ? 'grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4' : 'space-y-4'}>
          {filteredTunnels.map((tunnel) => {
            const isReapplying = reapplyingTunnelId === tunnel.id
            const isSelected = selectedTunnelIds.has(tunnel.id)
            const isDeleting = deletingTunnelId === tunnel.id
            const isTesting = testingTunnelId === tunnel.id

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

            const getCoreBadge = () => {
              const coreColors: Record<string, { bg: string; text: string; border: string }> = {
                rathole: { bg: 'bg-purple-500/10', text: 'text-purple-600 dark:text-purple-400', border: 'border-purple-500/20' },
                backhaul: { bg: 'bg-blue-500/10', text: 'text-blue-600 dark:text-blue-400', border: 'border-blue-500/20' },
                chisel: { bg: 'bg-amber-500/10', text: 'text-amber-600 dark:text-amber-400', border: 'border-amber-500/20' },
                frp: { bg: 'bg-cyan-500/10', text: 'text-cyan-600 dark:text-cyan-400', border: 'border-cyan-500/20' },
                gost: { bg: 'bg-indigo-500/10', text: 'text-indigo-600 dark:text-indigo-400', border: 'border-indigo-500/20' },
              }
              return coreColors[tunnel.core] || { bg: 'bg-slate-100 dark:bg-white/[0.05]', text: 'text-slate-600 dark:text-slate-300', border: 'border-slate-200 dark:border-white/[0.08]' }
            }

            let transmissionType: string | null = null
            if (tunnel.core === 'chisel') {
              transmissionType = 'TCP'
            } else if (tunnel.core === 'rathole') {
              const transport = tunnel.spec?.transport || (tunnel.type && tunnel.type !== 'rathole' ? tunnel.type : 'tcp')
              transmissionType = transport.toUpperCase()
            } else if (tunnel.type && tunnel.type.toLowerCase() !== tunnel.core.toLowerCase()) {
              transmissionType = tunnel.type.toUpperCase()
            }

            const getTransmissionBadge = (type: string) => {
              const typeColors: Record<string, { bg: string; text: string; border: string }> = {
                TCP: { bg: 'bg-emerald-500/10', text: 'text-emerald-600 dark:text-emerald-400', border: 'border-emerald-500/20' },
                UDP: { bg: 'bg-amber-500/10', text: 'text-amber-600 dark:text-amber-400', border: 'border-amber-500/20' },
                WS: { bg: 'bg-pink-500/10', text: 'text-pink-600 dark:text-pink-400', border: 'border-pink-500/20' },
                WSS: { bg: 'bg-pink-500/10', text: 'text-pink-600 dark:text-pink-400', border: 'border-pink-500/20' },
                GRPC: { bg: 'bg-teal-500/10', text: 'text-teal-600 dark:text-teal-400', border: 'border-teal-500/20' },
                TCPMUX: { bg: 'bg-violet-500/10', text: 'text-violet-600 dark:text-violet-400', border: 'border-violet-500/20' },
              }
              return typeColors[type] || { bg: 'bg-slate-100 dark:bg-white/[0.05]', text: 'text-slate-600 dark:text-slate-300', border: 'border-slate-200 dark:border-white/[0.08]' }
            }
            const transmissionBadge = transmissionType ? getTransmissionBadge(transmissionType) : null

            let corePort: string | null = null
            if (tunnel.core === 'rathole') {
              if (tunnel.spec?.bind_addr) {
                const match = tunnel.spec.bind_addr.match(/:(\d+)$/)
                if (match) corePort = match[1]
              }
              if (!corePort && tunnel.spec?.control_port) {
                corePort = tunnel.spec.control_port
              }
              if (!corePort) {
                const remoteAddr = tunnel.spec?.remote_addr || ''
                const match = remoteAddr.match(/:(\d+)$/)
                if (match) corePort = match[1]
              }
              if (!corePort) corePort = '23333'
            } else if (tunnel.core === 'chisel') {
              corePort = tunnel.spec?.control_port || tunnel.spec?.server_port
            } else if (tunnel.core === 'backhaul') {
              corePort = tunnel.spec?.control_port || tunnel.spec?.public_port || '3080'
            } else if (tunnel.core === 'frp') {
              corePort = tunnel.spec?.bind_port || '7000'
            }

            const coreBadge = getCoreBadge()
            const ports = getPorts()
            const iranNode = nodes.find(n => n.id === tunnel.iran_node_id || n.id === tunnel.node_id)
            const foreignServer = servers.find(s => s.id === tunnel.foreign_node_id)

            return (
              <div
                key={tunnel.id}
                className={`relative bg-white dark:bg-[#12161f]/90 rounded-3xl shadow-xs border transition-all duration-200 ${
                  viewMode === 'grid' ? 'flex flex-col justify-between h-full' : ''
                } ${
                  isSelected
                    ? 'border-indigo-500 ring-2 ring-indigo-500/20 bg-indigo-500/[0.03] shadow-md'
                    : isDeleting
                    ? 'border-rose-500/60 ring-2 ring-rose-500/20 bg-rose-500/[0.03] shadow-md opacity-80'
                    : isReapplying
                    ? 'border-emerald-500/60 shadow-emerald-500/10 ring-2 ring-emerald-500/20'
                    : 'border-slate-200/80 dark:border-white/[0.07] hover:border-slate-300 dark:hover:border-white/[0.12] hover:shadow-md'
                }`}
              >
                {/* ── Per-card loading overlay ── */}
                {isReapplying && (
                  <div className="absolute inset-0 bg-white/80 dark:bg-[#12161f]/80 rounded-3xl z-10 flex items-center justify-center backdrop-blur-xs">
                    <div className="flex items-center gap-3 px-5 py-2.5 bg-white dark:bg-[#161c28] rounded-2xl shadow-xl border border-emerald-500/20">
                      <Loader2 size={18} className="animate-spin text-emerald-500" />
                      <span className="text-xs font-mono font-semibold text-emerald-600 dark:text-emerald-400">Applying changes...</span>
                    </div>
                  </div>
                )}

                {/* ── Per-card deleting overlay ── */}
                {isDeleting && (
                  <div className="absolute inset-0 bg-white/85 dark:bg-[#12161f]/85 rounded-3xl z-10 flex items-center justify-center backdrop-blur-xs">
                    <div className="flex items-center gap-3 px-5 py-2.5 bg-white dark:bg-[#161c28] rounded-2xl shadow-xl border border-rose-500/20">
                      <Loader2 size={18} className="animate-spin text-rose-500" />
                      <span className="text-xs font-mono font-bold text-rose-600 dark:text-rose-400">Deleting tunnel...</span>
                    </div>
                  </div>
                )}

                {viewMode === 'grid' ? (
                  /* ══════════════════════════════════════════════════════════
                     GRID CARD LAYOUT
                     ══════════════════════════════════════════════════════════ */
                  <div className="p-4 sm:p-5 flex flex-col justify-between h-full gap-3.5">
                    <div>
                      {/* Top Bar: Checkbox + Status + Action Buttons */}
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2 min-w-0">
                          {/* Multi-Selection Checkbox */}
                          <button
                            type="button"
                            onClick={(e) => toggleSelectTunnel(tunnel.id, e)}
                            disabled={isReapplying || isDeleting}
                            className={`shrink-0 w-5 h-5 rounded-lg border flex items-center justify-center transition-all disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer ${
                              selectedTunnelIds.has(tunnel.id)
                                ? 'bg-indigo-600 border-indigo-600 text-white shadow-xs scale-105'
                                : 'border-slate-300 dark:border-white/20 hover:border-indigo-400 dark:hover:border-indigo-500 bg-white dark:bg-[#161c28]'
                            }`}
                            title={selectedTunnelIds.has(tunnel.id) ? 'Deselect' : 'Select'}
                          >
                            {selectedTunnelIds.has(tunnel.id) && (
                              <CheckCircle2 size={14} className="fill-current text-white" />
                            )}
                          </button>

                          {/* Status Badge */}
                          <span
                            className={`px-2 py-0.5 rounded-full text-[11px] font-semibold whitespace-nowrap flex items-center gap-1.5 ${
                              tunnel.status === 'active'
                                ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20'
                                : tunnel.status === 'error'
                                ? 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20'
                                : 'bg-slate-100 dark:bg-white/[0.05] text-slate-600 dark:text-slate-400 border border-slate-200 dark:border-white/[0.08]'
                            }`}
                          >
                            <span className={`w-1.5 h-1.5 rounded-full ${
                              tunnel.status === 'active' ? 'bg-emerald-500 animate-pulse' :
                              tunnel.status === 'error' ? 'bg-rose-500' : 'bg-slate-400'
                            }`} />
                            {tunnel.status}
                          </span>

                          {/* Pending Badge */}
                          {Boolean(tunnel.spec?._pending_reapply) && (
                            <span
                              className="px-1.5 py-0.5 rounded-md text-[10px] font-mono font-bold tracking-tight bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/30 text-center animate-pulse"
                              title="Configuration modified. Click Reapply to apply changes to live core."
                            >
                              Pending
                            </span>
                          )}
                        </div>

                        {/* Action Icon Buttons */}
                        <div className="flex items-center gap-0.5 shrink-0">
                          <button
                            type="button"
                            onClick={() => handleTestActiveTunnel(tunnel)}
                            disabled={isReapplying || isDeleting || isTesting}
                            className="p-1.5 text-amber-600 dark:text-amber-400 hover:bg-amber-500/10 border border-transparent hover:border-amber-500/20 rounded-lg transition-all disabled:opacity-40 min-w-[32px] min-h-[32px] flex items-center justify-center active:scale-95 cursor-pointer"
                            title="Test Live Connection & Ping"
                            aria-label="Test Live Connection & Ping"
                          >
                            {isTesting ? (
                              <Loader2 size={15} className="animate-spin text-amber-500" />
                            ) : (
                              <Zap size={15} />
                            )}
                          </button>
                          <button
                            type="button"
                            onClick={() => reapplyTunnel(tunnel)}
                            disabled={isReapplying || isDeleting || !!reapplyingTunnelId}
                            className={`p-1.5 rounded-lg transition-all disabled:opacity-40 disabled:cursor-not-allowed min-w-[32px] min-h-[32px] flex items-center justify-center active:scale-95 cursor-pointer ${
                              Boolean(tunnel.spec?._pending_reapply)
                                ? 'text-amber-600 dark:text-amber-400 bg-amber-500/15 border border-amber-500/30 ring-2 ring-amber-500/20 shadow-xs hover:bg-amber-500/25'
                                : 'text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/20'
                            }`}
                            title={Boolean(tunnel.spec?._pending_reapply) ? "Reapply pending changes to core" : "Reapply tunnel"}
                            aria-label="Reapply tunnel"
                          >
                            {isReapplying ? (
                              <Loader2 size={15} className="animate-spin" />
                            ) : (
                              <RotateCw size={15} className={Boolean(tunnel.spec?._pending_reapply) ? 'text-amber-600 dark:text-amber-400' : ''} />
                            )}
                          </button>
                          <button
                            type="button"
                            onClick={() => setEditingTunnel(tunnel)}
                            disabled={isReapplying || isDeleting}
                            className="p-1.5 text-slate-600 dark:text-slate-300 hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-indigo-500/10 border border-transparent hover:border-indigo-500/20 rounded-lg transition-all disabled:opacity-40 min-w-[32px] min-h-[32px] flex items-center justify-center active:scale-95 cursor-pointer"
                            title="Edit tunnel"
                            aria-label="Edit tunnel"
                          >
                            <Edit2 size={15} />
                          </button>
                          <button
                            type="button"
                            onClick={() => deleteTunnel(tunnel.id)}
                            disabled={isReapplying || isDeleting}
                            className="p-1.5 text-slate-600 dark:text-slate-300 hover:text-rose-600 dark:hover:text-rose-400 hover:bg-rose-500/10 border border-transparent hover:border-rose-500/20 rounded-lg transition-all disabled:opacity-40 min-w-[32px] min-h-[32px] flex items-center justify-center active:scale-95 cursor-pointer"
                            title={isDeleting ? "Deleting tunnel..." : "Delete tunnel"}
                            aria-label="Delete tunnel"
                          >
                            {isDeleting ? (
                              <Loader2 size={15} className="animate-spin text-rose-500" />
                            ) : (
                              <Trash2 size={15} />
                            )}
                          </button>
                        </div>
                      </div>

                      {/* Tunnel Name */}
                      <h3 className="text-base font-bold text-slate-900 dark:text-white truncate mt-2.5" title={tunnel.name}>
                        {tunnel.name}
                      </h3>

                      {/* Badges Row */}
                      <div className="flex items-center gap-1.5 flex-wrap mt-2">
                        <span
                          className={`px-2 py-0.5 rounded-md text-[11px] font-mono font-bold uppercase tracking-wide border ${coreBadge.bg} ${coreBadge.text} ${coreBadge.border} shrink-0`}
                        >
                          {tunnel.core}
                        </span>

                        {transmissionType && transmissionBadge && (
                          <span
                            className={`px-2 py-0.5 rounded-md text-[11px] font-mono font-bold uppercase tracking-wide border ${transmissionBadge.bg} ${transmissionBadge.text} ${transmissionBadge.border} shrink-0`}
                          >
                            {transmissionType}
                          </span>
                        )}

                        {tunnel.category && (
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation()
                              setActiveCategoryTab(tunnel.category || 'all')
                            }}
                            className={`px-2 py-0.5 rounded-md text-[11px] font-semibold flex items-center gap-1 border shrink-0 transition-all hover:scale-105 active:scale-95 cursor-pointer ${
                              getCategoryColorClasses(
                                categories.find(c => c.name === tunnel.category)?.color || 'blue'
                              ).bg
                            } ${
                              getCategoryColorClasses(
                                categories.find(c => c.name === tunnel.category)?.color || 'blue'
                              ).text
                            } ${
                              getCategoryColorClasses(
                                categories.find(c => c.name === tunnel.category)?.color || 'blue'
                              ).border
                            }`}
                            title={`Filter tunnels by "${tunnel.category}"`}
                          >
                            <Tag size={10} />
                            <span>{tunnel.category}</span>
                          </button>
                        )}

                        <LatencyBadge latency={tunnel.spec?.latency_ms} status={tunnel.status} />
                      </div>
                    </div>

                    {/* Bottom Details: Ports, Core Port, Topology Nodes */}
                    <div className="pt-2.5 border-t border-slate-100 dark:border-white/[0.05] space-y-2 text-xs">
                      <div className="flex items-center justify-between gap-2 text-xs">
                        <span className="text-slate-400 dark:text-slate-500 font-medium">Ports:</span>
                        <span className="font-mono font-bold text-slate-800 dark:text-slate-200 tabular-nums truncate">{ports}</span>
                      </div>

                      {corePort && (
                        <div className="flex items-center justify-between gap-2 text-xs">
                          <span className="font-medium text-slate-400">Core Port:</span>
                          <span className="text-slate-700 dark:text-slate-300 font-mono font-semibold tabular-nums">{corePort}</span>
                        </div>
                      )}

                      {/* Topology Nodes */}
                      {(iranNode || foreignServer) && (
                        <div className="flex items-center gap-1.5 pt-1 text-[11px]">
                          {iranNode && (
                            <div className="flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-slate-100 dark:bg-white/[0.04] border border-slate-200/60 dark:border-white/[0.05] truncate flex-1 min-w-0" title={`Iran Node: ${iranNode.name || iranNode.id}`}>
                              <span className="text-slate-400 text-[9px] uppercase font-mono shrink-0">IR:</span>
                              <span className="text-slate-700 dark:text-slate-300 font-semibold truncate">{iranNode.name || iranNode.id.substring(0, 8)}</span>
                            </div>
                          )}
                          {iranNode && foreignServer && (
                            <ArrowRight size={12} className="text-slate-400 shrink-0 rtl:rotate-180" />
                          )}
                          {foreignServer && (
                            <div className="flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-slate-100 dark:bg-white/[0.04] border border-slate-200/60 dark:border-white/[0.05] truncate flex-1 min-w-0" title={`Foreign Server: ${foreignServer.name || foreignServer.id}`}>
                              <span className="text-slate-400 text-[9px] uppercase font-mono shrink-0">EXT:</span>
                              <span className="text-slate-700 dark:text-slate-300 font-semibold truncate">{foreignServer.name || foreignServer.id.substring(0, 8)}</span>
                            </div>
                          )}
                        </div>
                      )}

                      {/* Error Message */}
                      {tunnel.status === 'error' && tunnel.error_message && (
                        <div className="mt-1 text-[11px] text-rose-600 dark:text-rose-400 p-2 bg-rose-500/10 border border-rose-500/20 rounded-lg leading-relaxed">
                          {tunnel.error_message}
                        </div>
                      )}
                    </div>
                  </div>
                ) : (
                  /* ══════════════════════════════════════════════════════════
                     LIST CARD LAYOUT
                     ══════════════════════════════════════════════════════════ */
                  <div className="p-5 sm:p-6">
                    <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
                      <div className="flex items-start gap-3 sm:gap-4 flex-1 min-w-0">
                        {/* Multi-Selection Checkbox */}
                        <button
                          type="button"
                          onClick={(e) => toggleSelectTunnel(tunnel.id, e)}
                          disabled={isReapplying || isDeleting}
                          className={`mt-1 shrink-0 w-5 h-5 rounded-lg border flex items-center justify-center transition-all disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer ${
                            selectedTunnelIds.has(tunnel.id)
                              ? 'bg-indigo-600 border-indigo-600 text-white shadow-xs scale-105'
                              : 'border-slate-300 dark:border-white/20 hover:border-indigo-400 dark:hover:border-indigo-500 bg-white dark:bg-[#161c28]'
                          }`}
                          title={selectedTunnelIds.has(tunnel.id) ? 'Deselect' : 'Select'}
                        >
                          {selectedTunnelIds.has(tunnel.id) && (
                            <CheckCircle2 size={14} className="fill-current text-white" />
                          )}
                        </button>

                        {/* Status Badge */}
                        <div className="flex flex-col gap-1.5 shrink-0 pt-0.5">
                          <span
                            className={`px-2.5 py-1 rounded-full text-xs font-semibold whitespace-nowrap text-center flex items-center gap-1.5 ${
                              tunnel.status === 'active'
                                ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20'
                                : tunnel.status === 'error'
                                ? 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20'
                                : 'bg-slate-100 dark:bg-white/[0.05] text-slate-600 dark:text-slate-400 border border-slate-200 dark:border-white/[0.08]'
                            }`}
                          >
                            <span className={`w-1.5 h-1.5 rounded-full ${
                              tunnel.status === 'active' ? 'bg-emerald-500 animate-pulse' :
                              tunnel.status === 'error' ? 'bg-rose-500' : 'bg-slate-400'
                            }`} />
                            {tunnel.status}
                          </span>
                          {Boolean(tunnel.spec?._pending_reapply) && (
                            <span
                              className="px-2 py-0.5 rounded-md text-[10px] font-mono font-bold tracking-tight bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/30 text-center animate-pulse"
                              title="Configuration modified. Click Reapply to apply changes to live core."
                            >
                              Pending
                            </span>
                          )}
                        </div>

                        <div className="flex-1 min-w-0 space-y-2.5">
                          {/* Name, Core Badge, Category Badge, Transmission Badge, Ports, Latency */}
                          <div className="flex items-center gap-2 sm:gap-2.5 flex-wrap">
                            <h3 className="text-base font-bold text-slate-900 dark:text-white truncate">{tunnel.name}</h3>
                            <span
                              className={`px-2.5 py-0.5 rounded-lg text-xs font-mono font-bold uppercase tracking-wide border ${coreBadge.bg} ${coreBadge.text} ${coreBadge.border} shrink-0`}
                            >
                              {tunnel.core}
                            </span>
                            {tunnel.category && (
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  setActiveCategoryTab(tunnel.category || 'all')
                                }}
                                className={`px-2.5 py-0.5 rounded-lg text-xs font-semibold flex items-center gap-1 border shrink-0 transition-all hover:scale-105 active:scale-95 cursor-pointer ${
                                  getCategoryColorClasses(
                                    categories.find(c => c.name === tunnel.category)?.color || 'blue'
                                  ).bg
                                } ${
                                  getCategoryColorClasses(
                                    categories.find(c => c.name === tunnel.category)?.color || 'blue'
                                  ).text
                                } ${
                                  getCategoryColorClasses(
                                    categories.find(c => c.name === tunnel.category)?.color || 'blue'
                                  ).border
                                }`}
                                title={`Filter tunnels by "${tunnel.category}"`}
                              >
                                <Tag size={11} />
                                <span>{tunnel.category}</span>
                              </button>
                            )}
                            {transmissionType && transmissionBadge && (
                              <span
                                className={`px-2.5 py-0.5 rounded-lg text-xs font-mono font-bold uppercase tracking-wide border ${transmissionBadge.bg} ${transmissionBadge.text} ${transmissionBadge.border} shrink-0`}
                              >
                                {transmissionType}
                              </span>
                            )}
                            <div className="flex items-center gap-1.5 text-xs">
                              <span className="text-slate-400 dark:text-slate-500 font-medium">Ports:</span>
                              <span className="font-mono font-bold text-slate-800 dark:text-slate-200 tabular-nums">{ports}</span>
                            </div>
                            <LatencyBadge latency={tunnel.spec?.latency_ms} status={tunnel.status} />
                          </div>

                          {/* Node & Server Route info */}
                          <div className="flex items-center gap-3 text-xs text-slate-500 dark:text-slate-400 flex-wrap">
                            {corePort && (
                              <div className="flex items-center gap-1.5">
                                <span className="font-medium text-slate-400">Core Port:</span>
                                <span className="text-slate-700 dark:text-slate-300 font-mono font-semibold tabular-nums">{corePort}</span>
                              </div>
                            )}
                            {iranNode && (
                              <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-slate-100 dark:bg-white/[0.04] border border-slate-200/60 dark:border-white/[0.05]">
                                <span className="text-slate-400 text-[10px] uppercase font-mono">IR:</span>
                                <span className="text-slate-700 dark:text-slate-300 font-semibold">{iranNode.name || iranNode.id.substring(0, 8)}</span>
                              </div>
                            )}
                            {foreignServer && (
                              <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-slate-100 dark:bg-white/[0.04] border border-slate-200/60 dark:border-white/[0.05]">
                                <span className="text-slate-400 text-[10px] uppercase font-mono">EXT:</span>
                                <span className="text-slate-700 dark:text-slate-300 font-semibold">{foreignServer.name || foreignServer.id.substring(0, 8)}</span>
                              </div>
                            )}
                          </div>

                          {/* Error Message */}
                          {tunnel.status === 'error' && tunnel.error_message && (
                            <div className="mt-2 text-xs text-rose-600 dark:text-rose-400 p-3 bg-rose-500/10 border border-rose-500/20 rounded-xl leading-relaxed">
                              {tunnel.error_message}
                            </div>
                          )}
                        </div>
                      </div>

                      {/* Desktop Action Buttons */}
                      <div className="hidden sm:flex items-center gap-1.5 shrink-0">
                        <button
                          type="button"
                          onClick={() => handleTestActiveTunnel(tunnel)}
                          disabled={isReapplying || isDeleting || isTesting}
                          className="p-2.5 text-amber-600 dark:text-amber-400 hover:bg-amber-500/10 border border-transparent hover:border-amber-500/20 rounded-xl transition-all disabled:opacity-40 min-w-[40px] min-h-[40px] flex items-center justify-center active:scale-95 cursor-pointer"
                          title="Test Live Connection & Ping"
                          aria-label="Test Live Connection & Ping"
                        >
                          {isTesting ? (
                            <Loader2 size={18} className="animate-spin text-amber-500" />
                          ) : (
                            <Zap size={18} />
                          )}
                        </button>
                        <button
                          type="button"
                          onClick={() => reapplyTunnel(tunnel)}
                          disabled={isReapplying || isDeleting || !!reapplyingTunnelId}
                          className={`p-2.5 rounded-xl transition-all disabled:opacity-40 disabled:cursor-not-allowed min-w-[40px] min-h-[40px] flex items-center justify-center active:scale-95 cursor-pointer ${
                            Boolean(tunnel.spec?._pending_reapply)
                              ? 'text-amber-600 dark:text-amber-400 bg-amber-500/15 border border-amber-500/30 ring-2 ring-amber-500/20 shadow-xs hover:bg-amber-500/25'
                              : 'text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/20'
                          }`}
                          title={Boolean(tunnel.spec?._pending_reapply) ? "Reapply pending changes to core" : "Reapply tunnel"}
                          aria-label="Reapply tunnel"
                        >
                          {isReapplying ? (
                            <Loader2 size={18} className="animate-spin" />
                          ) : (
                            <RotateCw size={18} className={Boolean(tunnel.spec?._pending_reapply) ? 'text-amber-600 dark:text-amber-400' : ''} />
                          )}
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditingTunnel(tunnel)}
                          disabled={isReapplying || isDeleting}
                          className="p-2.5 text-slate-600 dark:text-slate-300 hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-indigo-500/10 border border-transparent hover:border-indigo-500/20 rounded-xl transition-all disabled:opacity-40 min-w-[40px] min-h-[40px] flex items-center justify-center active:scale-95 cursor-pointer"
                          title="Edit tunnel"
                          aria-label="Edit tunnel"
                        >
                          <Edit2 size={18} />
                        </button>
                        <button
                          type="button"
                          onClick={() => deleteTunnel(tunnel.id)}
                          disabled={isReapplying || isDeleting}
                          className="p-2.5 text-slate-600 dark:text-slate-300 hover:text-rose-600 dark:hover:text-rose-400 hover:bg-rose-500/10 border border-transparent hover:border-rose-500/20 rounded-xl transition-all disabled:opacity-40 min-w-[40px] min-h-[40px] flex items-center justify-center active:scale-95 cursor-pointer"
                          title={isDeleting ? "Deleting tunnel..." : "Delete tunnel"}
                          aria-label="Delete tunnel"
                        >
                          {isDeleting ? (
                            <Loader2 size={18} className="animate-spin text-rose-500" />
                          ) : (
                            <Trash2 size={18} />
                          )}
                        </button>
                      </div>
                    </div>

                    {/* Mobile Action Buttons Bar */}
                    <div className="flex sm:hidden items-center justify-end gap-2 pt-3 mt-3 border-t border-slate-100 dark:border-white/[0.05]">
                      <button
                        type="button"
                        onClick={() => handleTestActiveTunnel(tunnel)}
                        disabled={isReapplying || isDeleting || isTesting}
                        className="p-2.5 text-amber-600 dark:text-amber-400 hover:bg-amber-500/10 rounded-xl transition-colors disabled:opacity-40 min-w-[44px] min-h-[44px] flex items-center justify-center active:scale-95 cursor-pointer"
                        title="Test Live Connection & Ping"
                        aria-label="Test Live Connection & Ping"
                      >
                        {isTesting ? (
                          <Loader2 size={18} className="animate-spin text-amber-500" />
                        ) : (
                          <Zap size={18} />
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => reapplyTunnel(tunnel)}
                        disabled={isReapplying || isDeleting || !!reapplyingTunnelId}
                        className={`p-2.5 rounded-xl transition-all disabled:opacity-40 disabled:cursor-not-allowed min-w-[44px] min-h-[44px] flex items-center justify-center active:scale-95 cursor-pointer ${
                          Boolean(tunnel.spec?._pending_reapply)
                            ? 'text-amber-600 dark:text-amber-400 bg-amber-500/15 border border-amber-500/30 ring-2 ring-amber-500/20 shadow-xs'
                            : 'text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/20'
                        }`}
                        title={Boolean(tunnel.spec?._pending_reapply) ? "Reapply pending changes to core" : "Reapply tunnel"}
                        aria-label="Reapply tunnel"
                      >
                        {isReapplying ? (
                          <Loader2 size={18} className="animate-spin" />
                        ) : (
                          <RotateCw size={18} className={Boolean(tunnel.spec?._pending_reapply) ? 'text-amber-600 dark:text-amber-400' : ''} />
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditingTunnel(tunnel)}
                        disabled={isReapplying || isDeleting}
                        className="p-2.5 text-slate-600 dark:text-slate-300 hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-indigo-500/10 rounded-xl transition-colors disabled:opacity-40 min-w-[44px] min-h-[44px] flex items-center justify-center active:scale-95 cursor-pointer"
                        title="Edit tunnel"
                        aria-label="Edit tunnel"
                      >
                        <Edit2 size={18} />
                      </button>
                      <button
                        type="button"
                        onClick={() => deleteTunnel(tunnel.id)}
                        disabled={isReapplying || isDeleting}
                        className="p-2.5 text-slate-600 dark:text-slate-300 hover:text-rose-600 dark:hover:text-rose-400 hover:bg-rose-500/10 rounded-xl transition-colors disabled:opacity-40 min-w-[44px] min-h-[44px] flex items-center justify-center active:scale-95 cursor-pointer"
                        title={isDeleting ? "Deleting tunnel..." : "Delete tunnel"}
                        aria-label="Delete tunnel"
                      >
                        {isDeleting ? (
                          <Loader2 size={18} className="animate-spin text-rose-500" />
                        ) : (
                          <Trash2 size={18} />
                        )}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* ── Reapply All — Confirm Dialog ─────────────────────── */}
      {showConfirmReapplyAll && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[100] p-4">
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl border border-gray-200 dark:border-gray-700 p-6 w-full max-w-sm">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-2">
              {t.tunnels.reapplyAll}
            </h3>
            <p className="text-sm text-gray-600 dark:text-gray-400 mb-6">
              {t.tunnels.confirmReapplyAll || 'Are you sure you want to reapply all tunnels? This will restart all active connections.'}
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setShowConfirmReapplyAll(false)}
                className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={startReapplyAll}
                className="px-4 py-2 text-sm font-medium text-white bg-green-600 hover:bg-green-700 rounded-lg transition-colors"
              >
                Yes, Reapply All
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Reapply All — Progress Modal ─────────────────────── */}
      {reapplyAllProgress && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-[100] p-4">
          <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl border border-gray-200/80 dark:border-gray-700/80 w-full max-w-md max-h-[85vh] flex flex-col animate-in fade-in zoom-in-95 duration-200">
            {/* Modal header */}
            <div className="flex items-center justify-between p-5 border-b border-gray-200 dark:border-gray-700">
              <div className="flex items-center gap-3">
                <div className={`p-2 rounded-xl flex items-center justify-center ${
                  reapplyAllDone 
                    ? 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-600 dark:text-emerald-400' 
                    : 'bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400'
                }`}>
                  {reapplyAllDone ? (
                    <CheckCircle2 size={22} />
                  ) : (
                    <Loader2 size={22} className="animate-spin" />
                  )}
                </div>
                <div>
                  <h3 className="text-base font-bold text-gray-900 dark:text-white">
                    {reapplyAllDone ? 'Reapply Complete' : 'Applying Tunnels...'}
                  </h3>
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 font-medium">
                    {reapplyAllProgress.filter(i => i.status === 'success' || i.status === 'error').length} of {reapplyAllProgress.length} tunnels processed
                  </p>
                </div>
              </div>
              {reapplyAllDone && (
                <button
                  onClick={closeReapplyAllModal}
                  className="p-2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
                  aria-label="Close modal"
                >
                  <X size={18} />
                </button>
              )}
            </div>

            {/* Progress bar */}
            <div className="px-5 pt-4">
              <div className="w-full bg-gray-100 dark:bg-gray-700/80 rounded-full h-2 overflow-hidden shadow-inner">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-blue-500 via-indigo-500 to-emerald-500 transition-all duration-300 ease-out"
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
                      ? 'bg-blue-50/80 dark:bg-blue-950/30 border-blue-200 dark:border-blue-800/60 shadow-sm'
                      : item.status === 'success'
                      ? 'bg-emerald-50/80 dark:bg-emerald-950/30 border-emerald-200 dark:border-emerald-800/60'
                      : item.status === 'error'
                      ? 'bg-red-50/80 dark:bg-red-950/30 border-red-200 dark:border-red-800/60'
                      : 'bg-gray-50/50 dark:bg-gray-700/30 border-gray-200/70 dark:border-gray-700/50'
                  }`}
                >
                  {/* Status icon */}
                  <div className="shrink-0">
                    {item.status === 'pending' && <Clock size={18} className="text-gray-400" />}
                    {item.status === 'running' && <Loader2 size={18} className="animate-spin text-blue-600 dark:text-blue-400" />}
                    {item.status === 'success' && <CheckCircle2 size={18} className="text-emerald-600 dark:text-emerald-400" />}
                    {item.status === 'error' && <XCircle size={18} className="text-red-600 dark:text-red-400" />}
                  </div>

                  {/* Tunnel info */}
                  <div className="flex-1 min-w-0">
                    <p className={`text-sm font-semibold truncate ${
                      item.status === 'running' ? 'text-blue-900 dark:text-blue-200' :
                      item.status === 'success' ? 'text-emerald-900 dark:text-emerald-200' :
                      item.status === 'error' ? 'text-red-900 dark:text-red-200' :
                      'text-gray-700 dark:text-gray-300'
                    }`}>
                      {item.name}
                    </p>
                    {item.status === 'error' && item.error && (
                      <p className="text-xs text-red-600 dark:text-red-400 truncate mt-0.5" title={item.error}>{item.error}</p>
                    )}
                    {item.status === 'running' && (
                      <p className="text-xs text-blue-600 dark:text-blue-400 mt-0.5 animate-pulse">Applying configuration...</p>
                    )}
                  </div>

                  {/* Status badge */}
                  <span className={`text-[11px] font-bold px-2 py-0.5 rounded-full shrink-0 tracking-wide ${
                    item.status === 'pending' ? 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400' :
                    item.status === 'running' ? 'bg-blue-100 dark:bg-blue-900/60 text-blue-700 dark:text-blue-300' :
                    item.status === 'success' ? 'bg-emerald-100 dark:bg-emerald-900/60 text-emerald-700 dark:text-emerald-300' :
                    'bg-red-100 dark:bg-red-900/60 text-red-700 dark:text-red-300'
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
              <div className="p-5 border-t border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50 rounded-b-2xl">
                <div className="flex items-center justify-between text-xs font-semibold text-gray-600 dark:text-gray-400 mb-3">
                  <span className="flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400">
                    <CheckCircle2 size={15} />
                    {reapplyAllProgress.filter(i => i.status === 'success').length} succeeded
                  </span>
                  {reapplyAllProgress.filter(i => i.status === 'error').length > 0 && (
                    <span className="flex items-center gap-1.5 text-red-600 dark:text-red-400">
                      <XCircle size={15} />
                      {reapplyAllProgress.filter(i => i.status === 'error').length} failed
                    </span>
                  )}
                </div>
                <button
                  onClick={closeReapplyAllModal}
                  className="w-full px-4 py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 text-white rounded-xl hover:from-blue-700 hover:to-indigo-700 transition-all font-medium text-sm shadow-md hover:shadow-lg active:scale-[0.99]"
                >
                  Done
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Reapply Selected — Confirm Dialog ─────────────────────── */}
      {showConfirmReapplySelected && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[100] p-4">
          <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl border border-gray-200 dark:border-gray-700 p-6 w-full max-w-sm">
            <h3 className="text-lg font-bold text-gray-900 dark:text-white mb-2">
              {t.tunnels.reapplySelected || 'Reapply Selected Tunnels'}
            </h3>
            <p className="text-sm text-gray-600 dark:text-gray-400 mb-6">
              {t.tunnels.confirmReapplySelected || `Are you sure you want to reapply ${selectedTunnelIds.size} selected tunnels?`}
            </p>
            <div className="flex gap-3 justify-end">
              <button
                type="button"
                onClick={() => setShowConfirmReapplySelected(false)}
                className="px-4 py-2 text-xs font-semibold text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 rounded-xl transition-colors cursor-pointer"
              >
                {t.tunnels.cancel || 'Cancel'}
              </button>
              <button
                type="button"
                onClick={startReapplySelected}
                className="px-4 py-2 text-xs font-semibold text-white bg-emerald-600 hover:bg-emerald-700 rounded-xl transition-colors flex items-center gap-1.5 shadow-xs active:scale-95 cursor-pointer"
              >
                <RotateCw size={14} />
                <span>{t.tunnels.reapplySelected || 'Yes, Reapply'}</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Create Category Modal ─────────────────────────────────── */}
      {showCreateCategoryModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[100] p-4">
          <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl border border-gray-200 dark:border-gray-700 p-6 w-full max-w-md animate-in fade-in zoom-in-95 duration-150">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                <FolderPlus size={20} className="text-blue-600" />
                <span>{t.tunnels.newCategory || 'New Category'}</span>
              </h3>
              <button
                type="button"
                onClick={() => { setShowCreateCategoryModal(false); setNewCategoryName(''); }}
                className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 p-1 cursor-pointer"
              >
                <X size={18} />
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1">
                  {t.tunnels.categoryName || 'Category Name'}
                </label>
                <input
                  type="text"
                  placeholder="e.g. Gaming, V2Ray TR, Office"
                  value={newCategoryName}
                  onChange={(e) => setNewCategoryName(e.target.value)}
                  className="w-full px-3 py-2 text-sm rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500"
                  autoFocus
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-2">
                  Color Tag
                </label>
                <div className="flex gap-2 flex-wrap">
                  {['blue', 'emerald', 'purple', 'amber', 'rose', 'cyan', 'indigo'].map((c) => {
                    const style = getCategoryColorClasses(c)
                    const isSelected = newCategoryColor === c
                    return (
                      <button
                        key={c}
                        type="button"
                        onClick={() => setNewCategoryColor(c)}
                        className={`px-3 py-1.5 rounded-lg text-xs font-semibold border transition-all cursor-pointer ${
                          isSelected ? `${style.activeBg} ring-2 ring-offset-2 ring-blue-500` : `${style.bg} ${style.border} ${style.text}`
                        }`}
                      >
                        {c.charAt(0).toUpperCase() + c.slice(1)}
                      </button>
                    )
                  })}
                </div>
              </div>

              <div className="flex justify-end gap-2 pt-3 border-t border-gray-100 dark:border-gray-700">
                <button
                  type="button"
                  onClick={() => { setShowCreateCategoryModal(false); setNewCategoryName(''); }}
                  className="px-4 py-2 text-xs font-semibold text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 rounded-xl cursor-pointer"
                >
                  {t.tunnels.cancel || 'Cancel'}
                </button>
                <button
                  type="button"
                  onClick={async () => {
                    if (!newCategoryName.trim()) return
                    const res = await createCategory(newCategoryName.trim(), newCategoryColor)
                    if (res) {
                      setNewCategoryName('')
                      setShowCreateCategoryModal(false)
                    }
                  }}
                  disabled={!newCategoryName.trim()}
                  className="px-4 py-2 text-xs font-semibold text-white bg-blue-600 hover:bg-blue-700 rounded-xl disabled:opacity-50 cursor-pointer"
                >
                  Create
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Assign Category Modal ─────────────────────────────────── */}
      {showAssignCategoryModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[100] p-4">
          <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl border border-gray-200 dark:border-gray-700 p-6 w-full max-w-md animate-in fade-in zoom-in-95 duration-150">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                <Folder size={20} className="text-blue-600" />
                <span>{t.tunnels.assignCategory || 'Assign Category'}</span>
              </h3>
              <button
                type="button"
                onClick={() => setShowAssignCategoryModal(false)}
                className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 p-1 cursor-pointer"
              >
                <X size={18} />
              </button>
            </div>

            <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">
              Select a category for {selectedTunnelIds.size} selected tunnel(s):
            </p>

            <div className="space-y-2 max-h-60 overflow-y-auto pr-1">
              {/* Option to clear category (Uncategorized) */}
              <button
                type="button"
                onClick={() => bulkAssignCategory(null)}
                className="w-full text-left p-3 rounded-xl border border-dashed border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700/50 flex items-center justify-between text-xs font-semibold text-gray-600 dark:text-gray-300 cursor-pointer"
              >
                <span>{t.tunnels.uncategorized || 'None (Uncategorized)'}</span>
                <span className="text-[10px] text-gray-400">Clear category</span>
              </button>

              {/* List of existing categories */}
              {categories.map((cat) => {
                const colorStyle = getCategoryColorClasses(cat.color)
                return (
                  <button
                    key={cat.id}
                    type="button"
                    onClick={() => bulkAssignCategory(cat.name)}
                    className="w-full text-left p-3 rounded-xl border border-gray-200 dark:border-gray-700 hover:border-blue-400 dark:hover:border-blue-500 hover:bg-blue-50/50 dark:hover:bg-blue-950/20 flex items-center justify-between transition-all cursor-pointer"
                  >
                    <div className="flex items-center gap-2">
                      <span className={`w-3 h-3 rounded-full ${colorStyle.activeBg.split(' ')[0]}`}></span>
                      <span className="text-xs font-semibold text-gray-800 dark:text-gray-200">{cat.name}</span>
                    </div>
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${colorStyle.bg} ${colorStyle.text}`}>
                      {tunnels.filter(t => t.category === cat.name).length} tunnels
                    </span>
                  </button>
                )
              })}
            </div>

            <div className="mt-4 pt-3 border-t border-gray-100 dark:border-gray-700 flex justify-between items-center">
              <button
                type="button"
                onClick={() => {
                  setShowAssignCategoryModal(false)
                  setShowCreateCategoryModal(true)
                }}
                className="text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline flex items-center gap-1 cursor-pointer"
              >
                <FolderPlus size={14} />
                <span>+ Create new category</span>
              </button>
              <button
                type="button"
                onClick={() => setShowAssignCategoryModal(false)}
                className="px-4 py-2 text-xs font-semibold text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 rounded-xl cursor-pointer"
              >
                {t.tunnels.cancel || 'Cancel'}
              </button>
            </div>
          </div>
        </div>
      )}

      {showAddModal && (
        <AddTunnelModal
          nodes={nodes}
          servers={servers}
          categories={categories}
          onCategoryCreated={createCategory}
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
          categories={categories}
          onCategoryCreated={createCategory}
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
  categories?: TunnelCategory[]
  onCategoryCreated?: (name: string, color?: string) => Promise<any>
  onClose: () => void
  onSuccess: () => void
}

const EditTunnelModal = ({ tunnel, nodes, categories = [], onCategoryCreated, onClose, onSuccess }: EditTunnelModalProps) => {
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
    node_id: tunnel.node_id || '',
    iran_node_id: tunnel.iran_node_id || tunnel.node_id || '',
    foreign_node_id: tunnel.foreign_node_id || '',
    ports: parsePortsFromSpec(tunnel.spec || {}),
    remote_ip: remoteIp,
    rathole_remote_addr: tunnel.spec?.control_port ? tunnel.spec.control_port.toString() : (tunnel.spec?.remote_addr ? (() => {
      const parsed = parseAddressPort(tunnel.spec.remote_addr)
      return parsed.port?.toString() || ''
    })() : ''),
    rathole_token: tunnel.spec?.token || tunnel.spec?.rathole_token || '',
    rathole_transport: tunnel.spec?.transport_type || tunnel.spec?.transport || 'tcp',
    rathole_local_port: tunnel.spec?.local_port ? tunnel.spec.local_port.toString() : '8080',
    chisel_control_port: tunnel.spec?.control_port ? tunnel.spec.control_port.toString() : '',
    chisel_transport: tunnel.spec?.transport || tunnel.spec?.transport_type || 'ws',
    chisel_backend_url: tunnel.spec?.backend_url || '',
    chisel_custom_sni: tunnel.spec?.custom_sni || tunnel.spec?.stealth_domain || '',
    chisel_custom_host: tunnel.spec?.custom_host || '',
    chisel_keepalive: tunnel.spec?.keepalive || '10s',
    frp_bind_port: tunnel.spec?.bind_port ? tunnel.spec.bind_port.toString() : '7000',
    frp_token: tunnel.spec?.token || '',
    frp_local_ip: tunnel.spec?.local_ip || '127.0.0.1',
    frp_transport: tunnel.spec?.transport_type || tunnel.spec?.transport || 'tcp',
    frp_security: tunnel.spec?.security_type || 'tls',
    frp_sni: tunnel.spec?.custom_sni || tunnel.spec?.stealth_domain || '',
    frp_encryption: tunnel.spec?.use_encryption !== false,
    frp_compression: tunnel.spec?.use_compression !== false,
    frp_health_check: tunnel.spec?.enable_health_check !== false && tunnel.spec?.health_check_type !== null,
    frp_bandwidth_limit: tunnel.spec?.bandwidth_limit || '',
    frp_proxy_protocol: tunnel.spec?.proxy_protocol_version || 'none',
    frp_custom_domains: Array.isArray(tunnel.spec?.custom_domains) ? tunnel.spec.custom_domains.join(', ') : (tunnel.spec?.custom_domains || ''),
    node_ipv6: tunnel.spec?.node_ipv6 || '',
    cdn_mode: tunnel.cdn_mode || false,
    gaming_mode: tunnel.gaming_mode || false,
    custom_host: tunnel.custom_host || '',
    custom_sni: tunnel.custom_sni || tunnel.spec?.custom_sni || tunnel.spec?.stealth_domain || '',
    ws_path: tunnel.ws_path || '',
    is_reverse: tunnel.is_reverse || false,
    stealth_domain: tunnel.stealth_domain || tunnel.spec?.stealth_domain || tunnel.spec?.custom_sni || '',
    transport_type: tunnel.transport_type || 'tcp',
    security_type: tunnel.security_type || 'none',
    selector_strategy: tunnel.selector_strategy || 'fifo',
    utls_fingerprint: tunnel.utls_fingerprint || 'chrome',
    keepalive_interval: tunnel.keepalive_interval || 15,
    failover_ips: tunnel.failover_ips && Array.isArray(tunnel.failover_ips) ? tunnel.failover_ips.join('\n') : '',
    rate_limit_mbps: tunnel.rate_limit_mbps ? tunnel.rate_limit_mbps.toString() : '',
    allowed_ips: tunnel.allowed_ips && Array.isArray(tunnel.allowed_ips) ? tunnel.allowed_ips.join('\n') : '',
    rate_limit_enabled: !!tunnel.rate_limit_mbps,
    allowed_ips_enabled: !!(tunnel.allowed_ips && tunnel.allowed_ips.length > 0),
    category: tunnel.category || '',
  })
  const [showInlineNewCategory, setShowInlineNewCategory] = useState(false)
  const [inlineCategoryName, setInlineCategoryName] = useState('')
  const parsedBackhaul = parseBackhaulSpec(tunnel.spec, tunnel.type)
  const [backhaulState, setBackhaulState] = useState<BackhaulFormState>(parsedBackhaul.state)
  const [backhaulAdvanced, setBackhaulAdvanced] = useState<BackhaulAdvancedState>(parsedBackhaul.advanced)
  const [showBackhaulAdvanced, setShowBackhaulAdvanced] = useState(false)
  const [isTestingConfig, setIsTestingConfig] = useState(false)
  const [testResult, setTestResult] = useState<any | null>(null)

  const handleTestConfig = async () => {
    setIsTestingConfig(true)
    setTestResult(null)
    try {
      const payload: any = {
        core: tunnel.core,
        iran_node_id: tunnel.iran_node_id || tunnel.node_id,
        foreign_node_id: tunnel.foreign_node_id,
        ports: tunnel.core === 'backhaul' ? backhaulState.public_port : formData.ports,
        rathole_token: formData.rathole_token,
        rathole_transport: formData.rathole_transport,
        rathole_remote_addr: formData.rathole_remote_addr,
        frp_token: formData.frp_token,
        frp_transport: formData.frp_transport,
        frp_security: formData.frp_security,
        frp_health_check: formData.frp_health_check,
        frp_bandwidth_limit: formData.frp_bandwidth_limit,
        frp_proxy_protocol: formData.frp_proxy_protocol,
        chisel_transport: formData.chisel_transport,
        chisel_backend_url: formData.chisel_backend_url,
        transport: tunnel.core === 'backhaul' ? backhaulState.transport : (tunnel.core === 'rathole' ? formData.rathole_transport : (tunnel.core === 'chisel' ? formData.chisel_transport : (tunnel.core === 'frp' ? formData.frp_transport : formData.transport_type))),
        is_reverse: formData.is_reverse,
      }
      const response = await api.post('/tunnels/test-config', payload)
      setTestResult(response.data)
      if (response.data?.valid) {
        showToast('success', 'Configuration Valid', response.data.summary || 'All checks passed!')
      } else {
        showToast('warning', 'Configuration Warning', response.data.summary || 'Some checks failed')
      }
    } catch (err: any) {
      const msg = err.response?.data?.detail || err.message || 'Could not perform test'
      showToast('error', 'Test Failed', msg)
      setTestResult({
        valid: false,
        summary: msg,
        checks: [{ title: 'Connection Error', status: 'failed', detail: msg }]
      })
    } finally {
      setIsTestingConfig(false)
    }
  }

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
          const remotePort = formData.rathole_remote_addr.includes(':') 
            ? formData.rathole_remote_addr.split(':')[1] 
            : formData.rathole_remote_addr
          const parsedPort = parseInt(remotePort) || 23333
          updatedSpec.control_port = parsedPort
          const remoteHost = window.location.hostname
          updatedSpec.remote_addr = `${remoteHost}:${parsedPort}`
        }
        if (formData.rathole_token) {
          updatedSpec.token = formData.rathole_token
        }
        if (formData.node_ipv6) {
          updatedSpec.node_ipv6 = formData.node_ipv6
        }
        if (formData.rathole_transport) {
          updatedSpec.transport = formData.rathole_transport
          updatedSpec.transport_type = formData.rathole_transport
        }
        if (formData.custom_sni) {
          updatedSpec.custom_sni = formData.custom_sni
          updatedSpec.stealth_domain = formData.custom_sni
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
        updatedSpec.transport = formData.chisel_transport || 'ws'
        updatedSpec.transport_type = formData.chisel_transport || 'ws'
        if (formData.chisel_backend_url) {
          updatedSpec.backend_url = formData.chisel_backend_url
        } else {
          delete updatedSpec.backend_url
        }
        if (formData.chisel_custom_sni) {
          updatedSpec.custom_sni = formData.chisel_custom_sni
          updatedSpec.stealth_domain = formData.chisel_custom_sni
        } else {
          delete updatedSpec.custom_sni
          delete updatedSpec.stealth_domain
        }
        if (formData.chisel_custom_host) {
          updatedSpec.custom_host = formData.chisel_custom_host
        } else {
          delete updatedSpec.custom_host
        }
        if (formData.chisel_keepalive) {
          updatedSpec.keepalive = formData.chisel_keepalive
        }
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
        updatedSpec.type = (tunnel.type === 'udp' || tunnel.type === 'tcp+udp' || tunnel.type === 'http' || tunnel.type === 'https') ? tunnel.type : 'tcp'
        updatedSpec.transport_type = formData.frp_transport || 'tcp'
        updatedSpec.transport = formData.frp_transport || 'tcp'
        updatedSpec.security_type = formData.frp_security || 'tls'
        updatedSpec.custom_sni = formData.frp_sni || ''
        updatedSpec.use_encryption = formData.frp_encryption
        updatedSpec.use_compression = formData.frp_compression
        updatedSpec.enable_health_check = formData.frp_health_check !== false
        updatedSpec.health_check_type = formData.frp_health_check !== false ? (tunnel.type === 'http' ? 'http' : 'tcp') : null
        if (formData.frp_bandwidth_limit) {
          updatedSpec.bandwidth_limit = formData.frp_bandwidth_limit
        } else {
          delete updatedSpec.bandwidth_limit
        }
        if (formData.frp_proxy_protocol && formData.frp_proxy_protocol !== 'none') {
          updatedSpec.proxy_protocol_version = formData.frp_proxy_protocol
        } else {
          delete updatedSpec.proxy_protocol_version
        }
        if (formData.frp_custom_domains) {
          updatedSpec.custom_domains = formData.frp_custom_domains.split(',').map((d: string) => d.trim()).filter(Boolean)
        } else {
          delete updatedSpec.custom_domains
        }
      } else if (tunnel.core === 'backhaul') {
        updatedSpec = buildBackhaulSpec(backhaulState, backhaulAdvanced, tunnel.type as BackhaulTransport)
        if ((!updatedSpec.ports || updatedSpec.ports.length === 0) && ports.length > 0) {
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
        category: formData.category ? formData.category.trim() : null,
        spec: updatedSpec,
        transport_type: tunnel.core === 'rathole' ? (formData.rathole_transport || 'tcp') : (tunnel.core === 'frp' ? (formData.frp_transport || 'tcp') : (tunnel.core === 'chisel' ? (formData.chisel_transport || 'ws') : formData.transport_type)),
        ...(tunnel.core === 'frp' && {
          security_type: formData.frp_security || 'tls',
          custom_sni: formData.frp_sni || null,
          is_reverse: true,
        }),
        ...(tunnel.core === 'chisel' && {
          security_type: formData.chisel_transport === 'wss' ? 'tls' : 'none',
          custom_sni: formData.chisel_custom_sni || null,
          custom_host: formData.chisel_custom_host || null,
          is_reverse: true,
        }),
        ...(tunnel.core === 'gost' && {
          cdn_mode: formData.cdn_mode,
          gaming_mode: formData.gaming_mode,
          custom_host: formData.custom_host,
          custom_sni: formData.custom_sni,
          ws_path: formData.ws_path,
          is_reverse: formData.is_reverse,
          stealth_domain: formData.stealth_domain || null,
          security_type: formData.security_type,
          selector_strategy: formData.selector_strategy || 'fifo',
          utls_fingerprint: formData.security_type === 'utls' ? (formData.utls_fingerprint || 'chrome') : null,
          keepalive_interval: formData.keepalive_interval ? parseInt(String(formData.keepalive_interval)) : 15,
          failover_ips: formData.failover_ips ? formData.failover_ips.split('\n').map((ip: string) => ip.trim()).filter((ip: string) => ip.length > 0) : null,
          rate_limit_mbps: formData.rate_limit_enabled && formData.rate_limit_mbps ? parseFloat(formData.rate_limit_mbps) : null,
          allowed_ips: formData.allowed_ips_enabled && formData.allowed_ips 
            ? formData.allowed_ips.split('\n').map((ip: string) => ip.trim()).filter((ip: string) => ip.length > 0)
            : null,
          port_ranges: port_ranges.length > 0 ? port_ranges : null
        }),
        node_id: formData.is_reverse ? (formData.iran_node_id || tunnel.iran_node_id || tunnel.node_id) : (formData.foreign_node_id || tunnel.foreign_node_id || tunnel.node_id),
        iran_node_id: formData.iran_node_id || tunnel.iran_node_id || tunnel.node_id || undefined,
        foreign_node_id: formData.foreign_node_id || tunnel.foreign_node_id || undefined
      })
      showToast('success', 'Configuration Saved', `${formData.name} was saved safely. Active tunnel remains live until you click Reapply.`)
      onSuccess()
    } catch (error) {
      console.error('Failed to update tunnel:', error)
      showToast('error', 'Error', 'Failed to update tunnel')
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-xs flex items-center justify-center z-[100] p-3.5 sm:p-4">
      <div className="bg-white dark:bg-gray-800 rounded-2xl w-full max-w-2xl max-h-[90dvh] shadow-2xl border border-gray-200/80 dark:border-gray-700/80 flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        <form onSubmit={handleSubmit} className="flex flex-col h-full max-h-[90dvh] overflow-hidden">
          {/* Sticky Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 dark:border-gray-700/70 bg-gray-50/70 dark:bg-gray-800/90 shrink-0">
            <div className="flex items-center gap-2.5 min-w-0">
              <span className="p-1.5 rounded-lg bg-blue-100 dark:bg-blue-900/50 text-blue-600 dark:text-blue-400 shrink-0">
                <Edit2 size={18} />
              </span>
              <h2 className="text-lg sm:text-xl font-bold text-gray-900 dark:text-white truncate">
                Edit Tunnel: <span className="text-blue-600 dark:text-blue-400 font-mono text-base sm:text-lg">{tunnel.name}</span>
              </h2>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors cursor-pointer shrink-0 ml-2"
              title="Close"
            >
              <X size={18} />
            </button>
          </div>

          {/* Scrollable Body */}
          <div className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-4 min-h-0">
            <div className="p-3 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 rounded-xl">
              <p className="text-xs text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5">
                <ShieldCheck size={14} className="shrink-0 text-emerald-600 dark:text-emerald-400" />
                <span>Zero-Downtime: Saving changes will not drop live connections until you click Reapply.</span>
              </p>
            </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                {t.tunnels.name}
              </label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700/80 text-gray-900 dark:text-white text-base sm:text-sm font-medium focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all shadow-xs"
                required
              />
            </div>
            <div>
              <div className="flex items-center justify-between mb-1 h-5">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300 flex items-center gap-1">
                  <Tag size={13} className="text-blue-500" />
                  <span>{t.tunnels.categories || 'Category'}</span>
                </label>
                <button
                  type="button"
                  onClick={() => setShowInlineNewCategory(prev => !prev)}
                  className="text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline cursor-pointer"
                >
                  {showInlineNewCategory ? 'Cancel' : '+ New'}
                </button>
              </div>
              <select
                value={formData.category}
                onChange={(e) => setFormData({ ...formData, category: e.target.value })}
                className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700/80 text-gray-900 dark:text-white text-base sm:text-sm font-medium focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all shadow-xs"
              >
                <option value="">{t.tunnels.uncategorized || 'No Category'}</option>
                {categories.map((c) => (
                  <option key={c.id} value={c.name}>{c.name}</option>
                ))}
              </select>
              {showInlineNewCategory && (
                <div className="mt-2 p-2 rounded-xl bg-gray-50 dark:bg-gray-700/60 border border-gray-200 dark:border-gray-600 flex gap-1.5">
                  <input
                    type="text"
                    placeholder="New category..."
                    value={inlineCategoryName}
                    onChange={(e) => setInlineCategoryName(e.target.value)}
                    className="flex-1 px-2.5 py-1 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                  <button
                    type="button"
                    onClick={async () => {
                      if (!inlineCategoryName.trim()) return
                      const created = await onCategoryCreated?.(inlineCategoryName.trim())
                      if (created) {
                        setFormData({ ...formData, category: created.name })
                        setInlineCategoryName('')
                        setShowInlineNewCategory(false)
                      }
                    }}
                    className="px-2.5 py-1 bg-blue-600 text-white rounded-lg text-xs font-semibold hover:bg-blue-700 cursor-pointer"
                  >
                    Save
                  </button>
                </div>
              )}
            </div>
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
                  className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700/80 text-gray-900 dark:text-white text-base sm:text-sm font-mono focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all shadow-xs"
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
                  className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700/80 text-gray-900 dark:text-white text-base sm:text-sm font-mono focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all shadow-xs"
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
                if (partial.gaming_mode !== undefined) {
                  setFormData((prev) => ({ ...prev, gaming_mode: partial.gaming_mode }))
                  if (partial.gaming_mode) {
                    setBackhaulAdvanced((prev) => ({
                      ...prev,
                      server: {
                        ...prev.server,
                        nodelay: true,
                        channel_size: '8192',
                        mux_framesize: '4096',
                        mss: '1380',
                        keepalive_period: '12',
                        heartbeat: '12',
                        so_rcvbuf: '2097152',
                        so_sndbuf: '2097152',
                      },
                      client: {
                        ...prev.client,
                        nodelay: true,
                        channel_size: '8192',
                        mux_framesize: '4096',
                        mss: '1380',
                        keepalive_period: '12',
                        heartbeat: '12',
                        so_rcvbuf: '2097152',
                        so_sndbuf: '2097152',
                        aggressive_pool: true,
                      }
                    }))
                  }
                }
              }}
              onOpenAdvanced={() => setShowBackhaulAdvanced(true)}
              acceptUdpVisible={
                backhaulState.transport === 'tcp' || backhaulState.transport === 'tcpmux'
              }
            />
          )}
          
          {/* Rathole Core Settings */}
          {tunnel.core === 'rathole' && (
            <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-br from-orange-50/60 via-gray-50 to-amber-50/40 dark:from-orange-950/20 dark:via-gray-800/60 dark:to-amber-950/20 border border-orange-200/70 dark:border-orange-900/40 space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 pb-3 border-b border-gray-200/70 dark:border-gray-700/70">
                <div className="flex items-center gap-2">
                  <span className="p-1.5 rounded-lg bg-orange-100 dark:bg-orange-900/60 text-orange-600 dark:text-orange-300">
                    <Cpu size={16} />
                  </span>
                  <div>
                    <h4 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">
                      Rathole Settings
                    </h4>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      Ultra-Lightweight, Secure NAT-Traversal Core in Rust
                    </p>
                  </div>
                </div>
                <span className="self-start sm:self-auto text-xs px-2.5 py-0.5 rounded-full font-mono font-medium bg-orange-100/80 text-orange-700 dark:bg-orange-900/60 dark:text-orange-300 border border-orange-200 dark:border-orange-800">
                  Rathole Core
                </span>
              </div>

              {/* Quick Presets for Rathole */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                    <Sparkles size={14} className="text-orange-600 dark:text-orange-400" />
                    Quick Presets (1-Click Optimization)
                  </span>
                  <span className="text-[11px] text-gray-400 dark:text-gray-500">Auto-configures transport & encryption</span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        rathole_transport: 'noise',
                        custom_sni: ''
                      }));
                      showToast('info', 'Preset Applied', 'Noise Protocol (Gaming & Anti-DPI) applied');
                    }}
                    className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.rathole_transport === 'noise'
                        ? 'bg-orange-100/80 dark:bg-orange-950/50 border-orange-400 dark:border-orange-500 ring-2 ring-orange-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-orange-50 dark:hover:bg-orange-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Shield size={16} className="text-orange-600 dark:text-orange-400" />
                      <span className="text-xs font-bold text-orange-700 dark:text-orange-300">Noise Protocol</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      WireGuard-grade encrypted tunnel. Zero handshake lag.
                    </p>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        rathole_transport: 'wss',
                        custom_sni: 'dl.google.com'
                      }));
                      showToast('info', 'Preset Applied', 'WSS Camouflage (TLS + CDN Capable) applied');
                    }}
                    className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.rathole_transport === 'wss'
                        ? 'bg-blue-100/80 dark:bg-blue-950/50 border-blue-400 dark:border-blue-500 ring-2 ring-blue-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-blue-50 dark:hover:bg-blue-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Globe size={16} className="text-blue-600 dark:text-blue-400" />
                      <span className="text-xs font-bold text-blue-700 dark:text-blue-300">WSS Stealth</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      HTTPS WebSocket + TLS SNI spoofing for anti-DPI.
                    </p>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        rathole_transport: 'tcp',
                        custom_sni: ''
                      }));
                      showToast('info', 'Preset Applied', 'Standard TCP applied');
                    }}
                    className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      (!formData.rathole_transport || formData.rathole_transport === 'tcp')
                        ? 'bg-gray-100/80 dark:bg-gray-700/50 border-gray-400 dark:border-gray-500 ring-2 ring-gray-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-gray-50 dark:hover:bg-gray-700/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Zap size={16} className="text-gray-600 dark:text-gray-400" />
                      <span className="text-xs font-bold text-gray-700 dark:text-gray-300">TCP Standard</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      Raw direct TCP stream. Lowest CPU overhead.
                    </p>
                  </button>
                </div>
              </div>

              {/* Ports & Connectivity Grid */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-start pt-1">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Radio size={14} className="text-orange-500" />
                      Forwarded Ports
                    </label>
                  </div>
                  <input
                    type="text"
                    value={formData.ports}
                    onChange={(e) => setFormData({ ...formData, ports: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 transition-all font-mono"
                    placeholder="8080,8081,8082"
                    required
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Ports mapped between panel and node.
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Server size={14} className="text-amber-500" />
                      Rathole Port
                    </label>
                    <button
                      type="button"
                      onClick={() => {
                        const port = generateRandomControlPort()
                        const host = window.location.hostname
                        setFormData({ ...formData, rathole_remote_addr: `${host}:${port}` })
                      }}
                      className="flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[11px] font-medium bg-orange-50 dark:bg-orange-950/40 text-orange-600 dark:text-orange-400 hover:bg-orange-100 dark:hover:bg-orange-900/60 border border-orange-200/60 dark:border-orange-800/40 transition-all cursor-pointer shadow-xs"
                      title="Generate Random Port"
                    >
                      <Dices size={12} />
                      <span>Random</span>
                    </button>
                  </div>
                  <input
                    type="number"
                    value={formData.rathole_remote_addr ? formData.rathole_remote_addr.split(':')[1] || formData.rathole_remote_addr : ''}
                    onChange={(e) => {
                      const port = e.target.value
                      const host = window.location.hostname
                      setFormData({ ...formData, rathole_remote_addr: port ? `${host}:${port}` : '' })
                    }}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 transition-all font-mono"
                    placeholder="23333"
                    min="1"
                    max="65535"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Panel server bind port.
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Server size={14} className="text-emerald-500" />
                      Local Port
                    </label>
                  </div>
                  <input
                    type="number"
                    value={formData.rathole_local_port}
                    onChange={(e) => setFormData({ ...formData, rathole_local_port: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 transition-all font-mono"
                    placeholder="8080"
                    min="1"
                    max="65535"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Local service destination port.
                  </p>
                </div>
              </div>

              {/* Transport Protocol Select & Token */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <span className="w-1.5 h-1.5 rounded-full bg-orange-500"></span>
                      Transport Protocol
                    </label>
                    <span className="text-[11px] font-mono uppercase text-gray-400">
                      {formData.rathole_transport || 'tcp'}
                    </span>
                  </div>
                  <select
                    value={formData.rathole_transport || 'tcp'}
                    onChange={(e) => setFormData({ ...formData, rathole_transport: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 transition-all font-medium"
                  >
                    <option value="tcp">TCP (Standard Raw)</option>
                    <option value="noise">Noise Protocol (Encrypted / Gaming / Anti-DPI)</option>
                    <option value="ws">WebSocket (WS)</option>
                    <option value="wss">WebSocket + TLS (WSS)</option>
                  </select>
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    {formData.rathole_transport === 'noise' && 'WireGuard-grade 256-bit encryption with zero-handshake delay.'}
                    {formData.rathole_transport === 'wss' && 'Standard TLS 1.3 encapsulation for anti-DPI camouflage.'}
                    {formData.rathole_transport === 'ws' && 'Standard WebSocket stream.'}
                    {(!formData.rathole_transport || formData.rathole_transport === 'tcp') && 'Raw TCP stream without encryption wrapper.'}
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Key size={14} className="text-indigo-500" />
                      Auth Token
                    </label>
                    <span className="text-[11px] text-gray-400">Optional</span>
                  </div>
                  <input
                    type="text"
                    value={formData.rathole_token}
                    onChange={(e) => setFormData({ ...formData, rathole_token: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all font-mono"
                    placeholder="Auto-generated if empty"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Cryptographic secret token shared between panel and client.
                  </p>
                </div>
              </div>

              {/* SNI Camouflage */}
              {(formData.rathole_transport === 'wss' || formData.rathole_transport === 'ws') && (
                <div className="p-3.5 rounded-xl bg-orange-50/70 dark:bg-orange-950/20 border border-orange-200/80 dark:border-orange-900/50 space-y-2">
                  <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1.5">
                    <EyeOff size={14} className="text-orange-600 dark:text-orange-400" />
                    Custom SNI / Domain Camouflage
                  </label>
                  <p className="text-[11px] text-gray-500 dark:text-gray-400">
                    TLS Server Name Indication (SNI) spoofing to mimic legitimate web services:
                  </p>
                  <input
                    type="text"
                    value={formData.custom_sni || ''}
                    onChange={(e) => setFormData({ ...formData, custom_sni: e.target.value })}
                    placeholder="e.g. dl.google.com or cdn.cloudflare.com"
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-lg border border-orange-300 dark:border-orange-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500/20 font-medium"
                  />
                </div>
              )}

              {/* Node IPv6 address field for Rathole when v4 to v6 is enabled */}
              {tunnel.spec?.use_ipv6 && (
                <div className="p-3.5 rounded-xl bg-gray-50/70 dark:bg-gray-800/40 border border-gray-200 dark:border-gray-700 space-y-1.5">
                  <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                    <Globe size={14} className="text-blue-500" />
                    Node IPv6 Address (Optional)
                  </label>
                  <input
                    type="text"
                    value={formData.node_ipv6}
                    onChange={(e) => setFormData({ ...formData, node_ipv6: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500/20 font-mono"
                    placeholder="::1 or 2001:db8::1"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400">
                    IPv6 address of the target node (defaults to ::1 localhost IPv6).
                  </p>
                </div>
              )}
            </div>
          )}

          {/* Chisel Core Settings */}
          {tunnel.core === 'chisel' && (
            <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-br from-teal-50/60 via-gray-50 to-emerald-50/40 dark:from-teal-950/20 dark:via-gray-800/60 dark:to-emerald-950/20 border border-teal-200/70 dark:border-teal-900/40 space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 pb-3 border-b border-gray-200/70 dark:border-gray-700/70">
                <div className="flex items-center gap-2">
                  <span className="p-1.5 rounded-lg bg-teal-100 dark:bg-teal-900/60 text-teal-600 dark:text-teal-300">
                    <Terminal size={16} />
                  </span>
                  <div>
                    <h4 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">
                      Chisel Advanced Settings
                    </h4>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      High-Speed WebSocket Tunnel with SSH Multiplexing, WSS TLS & Anti-DPI Camouflage
                    </p>
                  </div>
                </div>
                <span className="self-start sm:self-auto text-xs px-2.5 py-0.5 rounded-full font-mono font-medium bg-teal-100/80 text-teal-700 dark:bg-teal-900/60 dark:text-teal-300 border border-teal-200 dark:border-teal-800">
                  Chisel Core
                </span>
              </div>

              {/* Quick Preset Buttons */}
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                  <Sparkles size={14} className="text-teal-500" />
                  Quick Optimization Presets
                </label>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        chisel_transport: 'wss',
                        chisel_backend_url: 'https://speedtest.net',
                        chisel_custom_sni: 'speedtest.net',
                        chisel_keepalive: '10s'
                      }));
                      showToast('info', 'Preset Applied', 'Anti-DPI Stealth Preset (WSS + Camouflage + SNI) configured');
                    }}
                    className={`p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.chisel_transport === 'wss' && formData.chisel_backend_url
                        ? 'bg-teal-100/80 dark:bg-teal-900/50 border-teal-400 dark:border-teal-500 ring-2 ring-teal-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-teal-50 dark:hover:bg-teal-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Shield size={15} className="text-teal-600 dark:text-teal-400" />
                      <span className="text-xs font-bold text-teal-800 dark:text-teal-200">Anti-DPI Stealth</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      WSS TLS + speedtest.net decoy camouflage against active probers.
                    </p>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        chisel_keepalive: '5s'
                      }));
                      showToast('info', 'Preset Applied', 'Ultra-Low Ping Gaming (5s Keepalive) configured');
                    }}
                    className={`p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.chisel_keepalive === '5s'
                        ? 'bg-amber-100/80 dark:bg-amber-900/50 border-amber-400 dark:border-amber-500 ring-2 ring-amber-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-amber-50 dark:hover:bg-amber-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Zap size={15} className="text-amber-500 dark:text-amber-400" />
                      <span className="text-xs font-bold text-amber-700 dark:text-amber-300">Ultra-Low Ping</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      Aggressive 5s Keepalive to prevent NAT firewall table drops.
                    </p>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        chisel_transport: 'ws',
                        chisel_custom_host: 'cdn.cloudflare.com',
                        chisel_keepalive: '15s'
                      }));
                      showToast('info', 'Preset Applied', 'CDN-Friendly WebSocket Preset configured');
                    }}
                    className={`p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.chisel_transport === 'ws' && formData.chisel_custom_host === 'cdn.cloudflare.com'
                        ? 'bg-blue-100/80 dark:bg-blue-900/50 border-blue-400 dark:border-blue-500 ring-2 ring-blue-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-blue-50 dark:hover:bg-blue-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Globe size={15} className="text-blue-500 dark:text-blue-400" />
                      <span className="text-xs font-bold text-blue-700 dark:text-blue-300">CDN Friendly</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      HTTP/WS with Host spoofing compatible with reverse proxies.
                    </p>
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Radio size={14} className="text-teal-500" />
                      Forwarded Ports
                    </label>
                    <span className="text-[11px] text-gray-400">Reverse & Local</span>
                  </div>
                  <input
                    type="text"
                    value={formData.ports}
                    onChange={(e) => setFormData({ ...formData, ports: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500 transition-all font-mono"
                    placeholder="8080,8081,8082"
                    required
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Comma-separated ports mapped through the tunnel.
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Server size={14} className="text-emerald-500" />
                      Control Port
                    </label>
                    <button
                      type="button"
                      onClick={() => setFormData({ ...formData, chisel_control_port: generateRandomControlPort() })}
                      className="flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[11px] font-medium bg-teal-50 dark:bg-teal-950/40 text-teal-600 dark:text-teal-400 hover:bg-teal-100 dark:hover:bg-teal-900/60 border border-teal-200/60 dark:border-teal-800/40 transition-all cursor-pointer shadow-xs"
                      title="Generate Random Control Port"
                    >
                      <Dices size={12} />
                      <span>Random</span>
                    </button>
                  </div>
                  <input
                    type="number"
                    value={formData.chisel_control_port}
                    onChange={(e) => setFormData({ ...formData, chisel_control_port: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500 transition-all font-mono"
                    placeholder={`${(parseInt(formData.ports.split(',')[0]?.trim()) || 8080) + 10000} (auto)`}
                    min="1"
                    max="65535"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Server control port (defaults to first port + 10000 if empty).
                  </p>
                </div>
              </div>

              {/* Transport Protocol & Keepalive */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start pt-1">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Shield size={14} className="text-teal-500" />
                      Transport Protocol & Encryption
                    </label>
                    <span className="text-[11px] font-mono uppercase text-gray-400 dark:text-gray-500">
                      {formData.chisel_transport}
                    </span>
                  </div>
                  <select
                    value={formData.chisel_transport}
                    onChange={(e) => setFormData({ ...formData, chisel_transport: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500 transition-all font-medium"
                  >
                    <option value="ws">WS - Plain WebSocket (HTTP, Low Overhead)</option>
                    <option value="wss">WSS - Encrypted WebSocket over TLS (HTTPS)</option>
                  </select>
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    {formData.chisel_transport === 'wss'
                      ? 'Outer TLS encryption layer + Inner SSH stream encryption (Double Layer Security).'
                      : 'Standard WebSocket with built-in inner SSH encryption layer.'}
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Zap size={14} className="text-amber-500" />
                      Keepalive Heartbeat
                    </label>
                    <span className="text-[11px] font-mono text-gray-400 dark:text-gray-500">
                      {formData.chisel_keepalive}
                    </span>
                  </div>
                  <select
                    value={formData.chisel_keepalive}
                    onChange={(e) => setFormData({ ...formData, chisel_keepalive: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500 transition-all font-medium"
                  >
                    <option value="5s">5s (Ultra-Aggressive / Competitive Gaming)</option>
                    <option value="10s">10s (Recommended - High Stability)</option>
                    <option value="15s">15s (Balanced)</option>
                    <option value="25s">25s (Default / Low Overhead)</option>
                  </select>
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Prevents firewall NAT translation state dropouts across Iranian ISPs.
                  </p>
                </div>
              </div>

              {/* Anti-Probing Camouflage & Decoy Website */}
              <div className="p-3.5 rounded-xl bg-teal-50/70 dark:bg-teal-950/20 border border-teal-200/70 dark:border-teal-900/40 space-y-3">
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-xs font-bold text-teal-900 dark:text-teal-200 flex items-center gap-1.5">
                      <Shield size={14} className="text-teal-600 dark:text-teal-400" />
                      Decoy Camouflage Backend (--backend)
                    </label>
                    <span className="text-[10px] px-2 py-0.5 rounded-md font-medium bg-teal-200/60 text-teal-800 dark:bg-teal-900/60 dark:text-teal-300">
                      Anti-Active Probing
                    </span>
                  </div>
                  <p className="text-[11px] text-teal-800/80 dark:text-teal-300/80 mb-2">
                    When censors or scanning bots probe your control port via standard HTTP/HTTPS, Chisel proxies the request to this legitimate website with HTTP 200 OK:
                  </p>
                  <input
                    type="text"
                    value={formData.chisel_backend_url}
                    onChange={(e) => setFormData({ ...formData, chisel_backend_url: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-teal-300 dark:border-teal-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white font-mono"
                    placeholder="https://speedtest.net or https://example.com"
                  />
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
                  <div>
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 block mb-1">
                      Custom SNI (--sni)
                    </label>
                    <input
                      type="text"
                      value={formData.chisel_custom_sni}
                      onChange={(e) => setFormData({ ...formData, chisel_custom_sni: e.target.value })}
                      className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white font-mono"
                      placeholder="speedtest.net or domain.com"
                    />
                    <p className="text-[10px] text-gray-500 dark:text-gray-400 mt-1">
                      Overrides TLS ClientHello Server Name Indication.
                    </p>
                  </div>

                  <div>
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 block mb-1">
                      Custom Host Header (--hostname)
                    </label>
                    <input
                      type="text"
                      value={formData.chisel_custom_host}
                      onChange={(e) => setFormData({ ...formData, chisel_custom_host: e.target.value })}
                      className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white font-mono"
                      placeholder="cdn.speedtest.net"
                    />
                    <p className="text-[10px] text-gray-500 dark:text-gray-400 mt-1">
                      Sets HTTP Host header for reverse proxies / CDN fronting.
                    </p>
                  </div>
                </div>
              </div>

              {tunnel.spec?.use_ipv6 && (
                <div className="p-3.5 rounded-xl bg-gray-50/70 dark:bg-gray-800/40 border border-gray-200 dark:border-gray-700 space-y-1.5">
                  <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                    <Globe size={14} className="text-teal-500" />
                    Node IPv6 Address (Optional)
                  </label>
                  <input
                    type="text"
                    value={formData.node_ipv6}
                    onChange={(e) => setFormData({ ...formData, node_ipv6: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-teal-500/20 font-mono"
                    placeholder="::1 or 2001:db8::1"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400">
                    IPv6 address of the node (defaults to ::1 localhost IPv6).
                  </p>
                </div>
              )}
            </div>
          )}

          {/* FRP Core Settings */}
          {tunnel.core === 'frp' && (
            <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-br from-cyan-50/60 via-gray-50 to-blue-50/40 dark:from-cyan-950/20 dark:via-gray-800/60 dark:to-blue-950/20 border border-cyan-200/70 dark:border-cyan-900/40 space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 pb-3 border-b border-gray-200/70 dark:border-gray-700/70">
                <div className="flex items-center gap-2">
                  <span className="p-1.5 rounded-lg bg-cyan-100 dark:bg-cyan-900/60 text-cyan-600 dark:text-cyan-300">
                    <Layers size={16} />
                  </span>
                  <div>
                    <h4 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">
                      FRP Advanced Settings
                    </h4>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      High-Performance Reverse Proxy with Multiplexing & Encryption
                    </p>
                  </div>
                </div>
                <span className="self-start sm:self-auto text-xs px-2.5 py-0.5 rounded-full font-mono font-medium bg-cyan-100/80 text-cyan-700 dark:bg-cyan-900/60 dark:text-cyan-300 border border-cyan-200 dark:border-cyan-800">
                  FRP v0.50+ Core
                </span>
              </div>

              {/* Quick Presets for FRP */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                    <Sparkles size={14} className="text-cyan-600 dark:text-cyan-400" />
                    Quick Presets (1-Click Optimization)
                  </span>
                  <span className="text-[11px] text-gray-400 dark:text-gray-500">Auto-configures transport & encryption</span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        frp_transport: 'quic',
                        frp_encryption: true,
                        frp_compression: true,
                      }));
                      showToast('info', 'Preset Applied', 'QUIC / HTTP/3 Ultra-Fast applied');
                    }}
                    className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.frp_transport === 'quic'
                        ? 'bg-cyan-100/80 dark:bg-cyan-950/50 border-cyan-400 dark:border-cyan-500 ring-2 ring-cyan-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-cyan-50 dark:hover:bg-cyan-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Zap size={16} className="text-cyan-600 dark:text-cyan-400" />
                      <span className="text-xs font-bold text-cyan-700 dark:text-cyan-300">QUIC Fast UDP</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      Fast 0-RTT UDP stream with built-in TLS 1.3 multiplexing.
                    </p>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        frp_transport: 'wss',
                        frp_sni: 'dl.google.com',
                        frp_encryption: true,
                        frp_compression: true,
                      }));
                      showToast('info', 'Preset Applied', 'WSS Stealth / Anti-DPI applied');
                    }}
                    className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.frp_transport === 'wss'
                        ? 'bg-indigo-100/80 dark:bg-indigo-950/50 border-indigo-400 dark:border-indigo-500 ring-2 ring-indigo-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-indigo-50 dark:hover:bg-indigo-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Globe size={16} className="text-indigo-600 dark:text-indigo-400" />
                      <span className="text-xs font-bold text-indigo-700 dark:text-indigo-300">WSS Stealth</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      Secure WebSocket + SNI camouflage. CDN friendly.
                    </p>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        frp_transport: 'kcp',
                        frp_encryption: true,
                        frp_compression: false,
                      }));
                      showToast('info', 'Preset Applied', 'KCP Anti-Packet-Loss applied');
                    }}
                    className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.frp_transport === 'kcp'
                        ? 'bg-emerald-100/80 dark:bg-emerald-950/50 border-emerald-400 dark:border-emerald-500 ring-2 ring-emerald-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-emerald-50 dark:hover:bg-emerald-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Rocket size={16} className="text-emerald-600 dark:text-emerald-400" />
                      <span className="text-xs font-bold text-emerald-700 dark:text-emerald-300">KCP Anti-Loss</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      Aggressive ARQ retransmission for unstable routes.
                    </p>
                  </button>
                </div>
              </div>

              {/* Bind Port & Ports */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start pt-1">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Server size={14} className="text-cyan-500" />
                      FRP Bind Port
                    </label>
                    <button
                      type="button"
                      onClick={() => setFormData({ ...formData, frp_bind_port: generateRandomControlPort() })}
                      className="flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[11px] font-medium bg-cyan-50 dark:bg-cyan-950/40 text-cyan-600 dark:text-cyan-400 hover:bg-cyan-100 dark:hover:bg-cyan-900/60 border border-cyan-200/60 dark:border-cyan-800/40 transition-all cursor-pointer shadow-xs"
                      title="Generate Random Bind Port"
                    >
                      <Dices size={12} />
                      <span>Random</span>
                    </button>
                  </div>
                  <input
                    type="number"
                    value={formData.frp_bind_port}
                    onChange={(e) => setFormData({ ...formData, frp_bind_port: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-cyan-500/20 focus:border-cyan-500 transition-all font-mono"
                    placeholder="7000"
                    min="1"
                    max="65535"
                    required
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Server bind port on the panel machine.
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Radio size={14} className="text-blue-500" />
                      Forwarded Ports
                    </label>
                    <span className="text-[11px] text-gray-400">Remote & Local</span>
                  </div>
                  <input
                    type="text"
                    value={formData.ports}
                    onChange={(e) => setFormData({ ...formData, ports: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-cyan-500/20 focus:border-cyan-500 transition-all font-mono"
                    placeholder="8080,8081,8082"
                    required
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Comma-separated ports forwarded to remote service.
                  </p>
                </div>
              </div>

              {/* Transport & SNI */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <span className="w-1.5 h-1.5 rounded-full bg-cyan-500"></span>
                      Transport Protocol
                    </label>
                    <span className="text-[11px] font-mono uppercase text-gray-400">
                      {formData.frp_transport || 'tcp'}
                    </span>
                  </div>
                  <select
                    value={formData.frp_transport || 'tcp'}
                    onChange={(e) => setFormData({ ...formData, frp_transport: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-cyan-500/20 focus:border-cyan-500 transition-all font-medium"
                  >
                    <option value="tcp">TCP (with TLS & Zero-Byte Signature)</option>
                    <option value="kcp">KCP (Fast UDP - Resilient to Packet Loss)</option>
                    <option value="quic">QUIC (HTTP/3 UDP + TLS 1.3 Multiplex)</option>
                    <option value="websocket">WebSocket (Plain WS)</option>
                    <option value="wss">WSS (Secure WebSocket - CDN Capable)</option>
                  </select>
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    {formData.frp_transport === 'quic' && 'Ultra-fast 0-RTT UDP multiplexing with TLS 1.3 encryption.'}
                    {formData.frp_transport === 'kcp' && 'Aggressive ARQ UDP for bad or filtered routes.'}
                    {formData.frp_transport === 'wss' && 'Encrypted WebSocket compatible with CDN reverse proxies.'}
                    {formData.frp_transport === 'websocket' && 'Standard HTTP WebSocket proxy.'}
                    {(!formData.frp_transport || formData.frp_transport === 'tcp') && 'Raw TCP stream with TLS signature hiding.'}
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <EyeOff size={14} className="text-purple-500" />
                      Stealth SNI / Camouflage Domain
                    </label>
                    <span className="text-[11px] text-gray-400">TLS Header</span>
                  </div>
                  <input
                    type="text"
                    value={formData.frp_sni}
                    onChange={(e) => setFormData({ ...formData, frp_sni: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-purple-500/20 focus:border-purple-500 transition-all font-medium"
                    placeholder="e.g. speedtest.net or dl.google.com"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Domain name inserted into TLS handshake to evade deep packet inspection.
                  </p>
                </div>
              </div>

              {/* Security Toggles Card */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
                  <div className="pr-2">
                    <div className="flex items-center gap-1.5">
                      <Lock size={15} className="text-cyan-600 dark:text-cyan-400" />
                      <span className="text-xs font-bold text-gray-900 dark:text-white">Payload Encryption</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                      AES-128-CFB / ChaCha20 cipher
                    </p>
                  </div>
                  <input
                    type="checkbox"
                    className="sr-only peer"
                    checked={formData.frp_encryption}
                    onChange={(e) => setFormData({ ...formData, frp_encryption: e.target.checked })}
                  />
                  <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-cyan-600 shrink-0 relative"></div>
                </label>

                <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
                  <div className="pr-2">
                    <div className="flex items-center gap-1.5">
                      <Zap size={15} className="text-cyan-600 dark:text-cyan-400" />
                      <span className="text-xs font-bold text-gray-900 dark:text-white">Snappy Compression</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                      Reduces bandwidth & obfuscates entropy
                    </p>
                  </div>
                  <input
                    type="checkbox"
                    className="sr-only peer"
                    checked={formData.frp_compression}
                    onChange={(e) => setFormData({ ...formData, frp_compression: e.target.checked })}
                  />
                  <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-cyan-600 shrink-0 relative"></div>
                </label>
              </div>

              {/* Auth Token */}
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                    <Key size={14} className="text-indigo-500" />
                    Authentication Token
                  </label>
                  <span className="text-[11px] text-gray-400">Optional</span>
                </div>
                <input
                  type="text"
                  value={formData.frp_token}
                  onChange={(e) => setFormData({ ...formData, frp_token: e.target.value })}
                  className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all font-mono"
                  placeholder="Auto-generated if empty"
                />
                <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                  Shared secret token verifying client-server authorization.
                </p>
              </div>

              {/* Reliability & Shaping */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
                  <div className="pr-2">
                    <div className="flex items-center gap-1.5">
                      <Activity size={15} className="text-emerald-600 dark:text-emerald-400" />
                      <span className="text-xs font-bold text-gray-900 dark:text-white">Native Health Check</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                      Auto-detect dead links & reconnect fast
                    </p>
                  </div>
                  <input
                    type="checkbox"
                    className="sr-only peer"
                    checked={formData.frp_health_check}
                    onChange={(e) => setFormData({ ...formData, frp_health_check: e.target.checked })}
                  />
                  <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-emerald-600 shrink-0 relative"></div>
                </label>

                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Network size={14} className="text-blue-500" />
                      Proxy Protocol Version
                    </label>
                  </div>
                  <select
                    value={formData.frp_proxy_protocol || 'none'}
                    onChange={(e) => setFormData({ ...formData, frp_proxy_protocol: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all font-medium"
                  >
                    <option value="none">Disabled (Direct)</option>
                    <option value="v1">v1 (ASCII Text)</option>
                    <option value="v2">v2 (Binary Fast)</option>
                  </select>
                </div>
              </div>

              {/* Bandwidth Limit & Custom Domains */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Gauge size={14} className="text-amber-500" />
                      Bandwidth Rate Limit
                    </label>
                    <span className="text-[11px] text-gray-400">Optional</span>
                  </div>
                  <input
                    type="text"
                    value={formData.frp_bandwidth_limit}
                    onChange={(e) => setFormData({ ...formData, frp_bandwidth_limit: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-amber-500/20 focus:border-amber-500 transition-all font-mono"
                    placeholder="e.g. 10MB or 500KB"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Per-proxy bandwidth limit cap (e.g. 20MB).
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Globe size={14} className="text-cyan-500" />
                      VHost Domains (Optional)
                    </label>
                    <span className="text-[11px] text-gray-400">HTTP/HTTPS</span>
                  </div>
                  <input
                    type="text"
                    value={formData.frp_custom_domains}
                    onChange={(e) => setFormData({ ...formData, frp_custom_domains: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-cyan-500/20 focus:border-cyan-500 transition-all font-mono"
                    placeholder="e.g. app.domain.com"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Custom domain routing for HTTP/HTTPS tunnels.
                  </p>
                </div>
              </div>
            </div>
          )}
          
          {/* Advanced GOST Settings */}
          {tunnel.core === 'gost' && (
            <div className="mt-6 border-t border-gray-200 dark:border-gray-700 pt-6">
              <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-br from-purple-50/60 via-gray-50 to-blue-50/40 dark:from-purple-950/20 dark:via-gray-800/60 dark:to-blue-950/20 border border-purple-200/70 dark:border-purple-900/40 space-y-4">
                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 pb-3 border-b border-gray-200/70 dark:border-gray-700/70">
                  <div className="flex items-center gap-2">
                    <span className="p-1.5 rounded-lg bg-purple-100 dark:bg-purple-900/60 text-purple-600 dark:text-purple-300">
                      <Zap size={16} />
                    </span>
                    <div>
                      <h4 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">
                        Advanced GOST Settings
                      </h4>
                      <p className="text-xs text-gray-500 dark:text-gray-400">
                        Next-Gen Anti-DPI & High-Performance Routing Core
                      </p>
                    </div>
                  </div>
                  <span className="self-start sm:self-auto text-xs px-2.5 py-0.5 rounded-full font-mono font-medium bg-purple-100/80 text-purple-700 dark:bg-purple-900/60 dark:text-purple-300 border border-purple-200 dark:border-purple-800">
                    GOST v3.0 Core
                  </span>
                </div>

                {/* Quick Presets Grid */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Sparkles size={14} className="text-purple-600 dark:text-purple-400" />
                      Quick Presets (1-Click Optimization)
                    </span>
                    <span className="text-[11px] text-gray-400 dark:text-gray-500">Auto-configures transport & security</span>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        setFormData(prev => ({
                          ...prev,
                          transport_type: 'grpc',
                          security_type: 'utls',
                          utls_fingerprint: 'chrome',
                          stealth_domain: 'www.google.com',
                          keepalive_interval: 15
                        }));
                        showToast('info', 'Preset Applied', 'Stealth Anti-DPI (gRPC + uTLS Chrome) applied');
                      }}
                      className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                        formData.transport_type === 'grpc' && formData.security_type === 'utls'
                          ? 'bg-purple-100/80 dark:bg-purple-900/50 border-purple-400 dark:border-purple-500 ring-2 ring-purple-400/20'
                          : 'bg-white/80 dark:bg-gray-800/80 hover:bg-purple-50 dark:hover:bg-purple-950/30 border-gray-200 dark:border-gray-700'
                      }`}
                    >
                      <div className="flex items-center gap-1.5 mb-1">
                        <Shield size={16} className="text-purple-600 dark:text-purple-400" />
                        <span className="text-xs font-bold text-purple-700 dark:text-purple-300">Stealth Anti-DPI</span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                        gRPC + uTLS Chrome. Bypasses deep packet inspection.
                      </p>
                    </button>

                    <button
                      type="button"
                      onClick={() => {
                        setFormData(prev => ({
                          ...prev,
                          transport_type: 'quic',
                          security_type: 'tls',
                          gaming_mode: true,
                          keepalive_interval: 15
                        }));
                        showToast('info', 'Preset Applied', 'Ultra-Low Ping (QUIC HTTP/3) applied');
                      }}
                      className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                        formData.transport_type === 'quic'
                          ? 'bg-amber-100/80 dark:bg-amber-900/50 border-amber-400 dark:border-amber-500 ring-2 ring-amber-400/20'
                          : 'bg-white/80 dark:bg-gray-800/80 hover:bg-amber-50 dark:hover:bg-amber-950/30 border-gray-200 dark:border-gray-700'
                      }`}
                    >
                      <div className="flex items-center gap-1.5 mb-1">
                        <Zap size={16} className="text-amber-500 dark:text-amber-400" />
                        <span className="text-xs font-bold text-amber-700 dark:text-amber-300">Ultra-Low Ping</span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                        QUIC / HTTP/3. Fast 0-RTT UDP for gaming & streaming.
                      </p>
                    </button>

                    <button
                      type="button"
                      onClick={() => {
                        setFormData(prev => ({
                          ...prev,
                          transport_type: 'kcp',
                          security_type: 'none',
                          gaming_mode: true,
                          keepalive_interval: 15
                        }));
                        showToast('info', 'Preset Applied', 'Anti-Packet-Loss (KCP ARQ) applied');
                      }}
                      className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                        formData.transport_type === 'kcp'
                          ? 'bg-emerald-100/80 dark:bg-emerald-900/50 border-emerald-400 dark:border-emerald-500 ring-2 ring-emerald-400/20'
                          : 'bg-white/80 dark:bg-gray-800/80 hover:bg-emerald-50 dark:hover:bg-emerald-950/30 border-gray-200 dark:border-gray-700'
                      }`}
                    >
                      <div className="flex items-center gap-1.5 mb-1">
                        <Rocket size={16} className="text-emerald-500 dark:text-emerald-400" />
                        <span className="text-xs font-bold text-emerald-700 dark:text-emerald-300">Anti-Packet-Loss</span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                        KCP ARQ. Aggressive retransmission for bad networks.
                      </p>
                    </button>
                  </div>
                </div>

                {/* Transport & Security Selects */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start pt-1">
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                        <span className="w-1.5 h-1.5 rounded-full bg-blue-500"></span>
                        Transport Protocol
                      </label>
                      <span className="text-[11px] font-mono uppercase text-gray-400 dark:text-gray-500">
                        {formData.transport_type}
                      </span>
                    </div>
                    <select
                      value={formData.transport_type}
                      onChange={(e) => setFormData({...formData, transport_type: e.target.value})}
                      className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all font-medium"
                    >
                      <option value="tcp">TCP (Standard)</option>
                      <option value="ws">WebSocket (WS)</option>
                      <option value="mws">Multiplex WS (MWS)</option>
                      <option value="quic">QUIC (HTTP/3 UDP, 0-RTT)</option>
                      <option value="grpc">gRPC (Multiplexed Stealth)</option>
                      <option value="kcp">KCP (Anti-Packet-Loss ARQ)</option>
                      <option value="ssh">SSH (Encrypted Subsystem)</option>
                    </select>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                      {formData.transport_type === 'quic' && 'Fast 0-RTT UDP handshake. Resilient to packet loss.'}
                      {formData.transport_type === 'grpc' && 'Multiplexed HTTP/2. Mimics legitimate enterprise API traffic.'}
                      {formData.transport_type === 'kcp' && 'Aggressive ARQ UDP. Ideal for high packet-loss links.'}
                      {formData.transport_type === 'ws' && 'Standard WebSocket. Compatible with CDN proxies.'}
                      {formData.transport_type === 'mws' && 'Multiplexed WebSocket. Bundles multiple TCP streams.'}
                      {formData.transport_type === 'tcp' && 'Direct raw TCP socket tunnel.'}
                      {formData.transport_type === 'ssh' && 'Native SSH subsystem encrypted protocol.'}
                    </p>
                  </div>

                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                        <span className="w-1.5 h-1.5 rounded-full bg-indigo-500"></span>
                        Security / Encryption
                      </label>
                      <span className="text-[11px] font-mono uppercase text-gray-400 dark:text-gray-500">
                        {formData.security_type}
                      </span>
                    </div>
                    <select
                      value={formData.security_type}
                      onChange={(e) => setFormData({...formData, security_type: e.target.value})}
                      className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all font-medium"
                    >
                      <option value="none">None (Plaintext / Low Overhead)</option>
                      <option value="tls">TLS (Standard Encryption)</option>
                      <option value="utls">uTLS (Browser Spoofing Anti-DPI)</option>
                    </select>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                      {formData.security_type === 'none' && 'No TLS wrapper. Lowest CPU overhead.'}
                      {formData.security_type === 'tls' && 'Standard TLS 1.3 handshake encryption.'}
                      {formData.security_type === 'utls' && 'Camouflages ClientHello to impersonate real browsers.'}
                    </p>
                  </div>
                </div>

                {/* uTLS Fingerprint Card */}
                {formData.security_type === 'utls' && (
                  <div className="p-3.5 rounded-xl bg-indigo-50/60 dark:bg-indigo-950/20 border border-indigo-200/80 dark:border-indigo-900/50 space-y-2">
                    <div className="flex items-center justify-between">
                      <label className="text-xs font-bold text-indigo-900 dark:text-indigo-200 flex items-center gap-1.5">
                        <Fingerprint size={15} className="text-indigo-600 dark:text-indigo-400" />
                        uTLS Client Fingerprint
                      </label>
                      <span className="text-[10px] px-2 py-0.5 rounded-md font-medium bg-indigo-200/60 text-indigo-800 dark:bg-indigo-900/60 dark:text-indigo-300">
                        Anti-DPI Spoofing
                      </span>
                    </div>
                    <p className="text-[11px] text-indigo-700/80 dark:text-indigo-300/80">
                      Replicates exact TLS cipher suites, extensions, and curves of legitimate web browsers:
                    </p>
                    <select
                      value={formData.utls_fingerprint || 'chrome'}
                      onChange={(e) => setFormData({...formData, utls_fingerprint: e.target.value})}
                      className="w-full px-3 py-2 text-sm sm:text-xs rounded-lg border border-indigo-300 dark:border-indigo-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-indigo-500/20 font-medium"
                    >
                      <option value="chrome">Google Chrome (Recommended - Highest Compatibility)</option>
                      <option value="firefox">Mozilla Firefox</option>
                      <option value="ios">Apple iOS Safari</option>
                      <option value="android">Android Chrome</option>
                      <option value="edge">Microsoft Edge</option>
                      <option value="randomized">Randomized (Rotates browser signature per connection)</option>
                    </select>
                  </div>
                )}

                {/* Failover IPs & Selector Strategy */}
                <div className="p-3.5 rounded-xl bg-gray-50/80 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 space-y-3">
                  <div className="flex items-center justify-between">
                    <div>
                      <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1.5">
                        <Globe size={15} className="text-blue-600 dark:text-blue-400" />
                        Failover & Additional Foreign Endpoints
                      </label>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400">
                        Add secondary foreign IPs for automatic high-availability failover or load balancing (one per line)
                      </p>
                    </div>
                    {formData.failover_ips && formData.failover_ips.trim().length > 0 && (
                      <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 dark:bg-blue-900/60 dark:text-blue-300 shrink-0">
                        Multi-Node Active
                      </span>
                    )}
                  </div>
                  <textarea
                    value={formData.failover_ips}
                    onChange={(e) => setFormData({...formData, failover_ips: e.target.value})}
                    className="w-full px-3 py-2 text-sm sm:text-xs font-mono rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500/20"
                    placeholder={"1.2.3.4\n5.6.7.8"}
                    rows={2}
                  />

                  {formData.failover_ips && formData.failover_ips.trim().length > 0 && (
                    <div className="pt-2 border-t border-gray-200/80 dark:border-gray-700/80 space-y-1.5">
                      <div className="flex items-center justify-between">
                        <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1">
                          <Scale size={14} className="text-blue-500" />
                          Load Balancing / Failover Strategy
                        </label>
                        <span className="text-[11px] font-mono text-gray-400">
                          {formData.selector_strategy || 'fifo'}
                        </span>
                      </div>
                      <select
                        value={formData.selector_strategy || 'fifo'}
                        onChange={(e) => setFormData({...formData, selector_strategy: e.target.value})}
                        className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500/20 font-medium"
                      >
                        <option value="fifo">FIFO Failover (Primary first, fallback to backup IPs)</option>
                        <option value="round">Round-Robin (Distribute requests evenly across all IPs)</option>
                        <option value="parallel">Parallel Race (Connect all concurrently, use fastest ping)</option>
                        <option value="rand">Random (Random distribution across nodes)</option>
                      </select>
                    </div>
                  )}
                </div>

                {/* Mode Switches */}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
                  <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
                    <div className="pr-2">
                      <div className="flex items-center gap-1.5">
                        <ArrowLeftRight size={15} className="text-blue-600 dark:text-blue-400" />
                        <span className="text-xs font-bold text-gray-900 dark:text-white">Reverse Mode</span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                        Iran connects to foreign
                      </p>
                    </div>
                    <input
                      type="checkbox"
                      className="sr-only peer"
                      checked={formData.is_reverse}
                      onChange={(e) => setFormData({...formData, is_reverse: e.target.checked})}
                    />
                    <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 shrink-0 relative"></div>
                  </label>

                  <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
                    <div className="pr-2">
                      <div className="flex items-center gap-1.5">
                        <Globe size={15} className="text-sky-600 dark:text-sky-400" />
                        <span className="text-xs font-bold text-gray-900 dark:text-white">CDN Mode</span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                        Cloudflare / WS proxy
                      </p>
                    </div>
                    <input
                      type="checkbox"
                      className="sr-only peer"
                      checked={formData.cdn_mode}
                      onChange={(e) => {
                        const isChecked = e.target.checked;
                        const updates: any = { cdn_mode: isChecked };
                        if (isChecked && ['tcp', 'udp', 'tcp+udp'].includes(formData.transport_type)) {
                          updates.transport_type = 'ws';
                          showToast('info', 'Transport Switched', 'CDN mode requires WebSocket transport. Auto-switched to WS.')
                        }
                        setFormData({...formData, ...updates});
                      }}
                    />
                    <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 shrink-0 relative"></div>
                  </label>

                  <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
                    <div className="pr-2">
                      <div className="flex items-center gap-1.5">
                        <Gamepad2 size={15} className="text-indigo-600 dark:text-indigo-400" />
                        <span className="text-xs font-bold text-gray-900 dark:text-white">Gaming (Mux)</span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                        Yamux lower ping
                      </p>
                    </div>
                    <input
                      type="checkbox"
                      className="sr-only peer"
                      checked={formData.gaming_mode}
                      onChange={(e) => setFormData({...formData, gaming_mode: e.target.checked})}
                    />
                    <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 shrink-0 relative"></div>
                  </label>
                </div>

                {/* Security, Limits & KeepAlive Tuning */}
                <div className="pt-3 border-t border-gray-200/80 dark:border-gray-700/80 space-y-3">
                  <div className="flex items-center justify-between">
                    <h5 className="text-xs font-bold uppercase tracking-wider text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <ShieldCheck size={15} className="text-gray-700 dark:text-gray-300" />
                      Security & Network Guard
                    </h5>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {/* KeepAlive Interval Card */}
                    <div className="p-3 rounded-xl bg-gray-50/80 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700">
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1">
                          <Activity size={14} className="text-rose-500 dark:text-rose-400" />
                          KeepAlive (Anti-Drop)
                        </label>
                        <span className="text-xs font-mono font-bold text-blue-600 dark:text-blue-400">
                          {formData.keepalive_interval || 15}s
                        </span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 mb-2">
                        Probe interval to keep stateful NAT firewalls alive
                      </p>
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          min="5"
                          max="120"
                          value={formData.keepalive_interval || 15}
                          onChange={(e) => setFormData({...formData, keepalive_interval: parseInt(e.target.value) || 15})}
                          className="w-20 px-2.5 py-1.5 text-xs font-mono font-semibold rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                        />
                        <div className="flex items-center gap-1">
                          {[10, 15, 30].map(sec => (
                            <button
                              key={sec}
                              type="button"
                              onClick={() => setFormData({...formData, keepalive_interval: sec})}
                              className={`text-[10px] font-mono px-2 py-1 rounded-md border transition-all cursor-pointer ${
                                (formData.keepalive_interval || 15) === sec
                                  ? 'bg-blue-600 text-white border-blue-600 font-bold'
                                  : 'bg-white dark:bg-gray-700 text-gray-600 dark:text-gray-300 border-gray-200 dark:border-gray-600 hover:bg-gray-100'
                              }`}
                            >
                              {sec}s
                            </button>
                          ))}
                        </div>
                      </div>
                    </div>

                    {/* Stealth Domain Card */}
                    <div className="p-3 rounded-xl bg-gray-50/80 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700">
                      <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1 mb-1">
                        <EyeOff size={14} className="text-purple-600 dark:text-purple-400" />
                        Stealth SNI (TLS Spoof)
                      </label>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 mb-2">
                        Mask traffic as legitimate website
                      </p>
                      <input
                        type="text"
                        value={formData.stealth_domain}
                        onChange={(e) => setFormData({...formData, stealth_domain: e.target.value})}
                        className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                        placeholder="e.g. www.google.com"
                      />
                    </div>
                  </div>

                  {/* IP Whitelist & Rate Limit Toggles */}
                  <div className="space-y-2">
                    <div className="p-3 rounded-xl bg-gray-50/80 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700">
                      <div className="flex items-center justify-between">
                        <div>
                          <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1.5">
                            <Shield size={14} className="text-blue-600 dark:text-blue-400" />
                            IP Whitelist (ACL)
                          </label>
                          <p className="text-[11px] text-gray-500 dark:text-gray-400">
                            Restrict tunnel access to specific IP ranges
                          </p>
                        </div>
                        <label className="relative inline-flex items-center cursor-pointer">
                          <input
                            type="checkbox"
                            className="sr-only peer"
                            checked={formData.allowed_ips_enabled}
                            onChange={(e) => setFormData({...formData, allowed_ips_enabled: e.target.checked})}
                          />
                          <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 shrink-0 relative"></div>
                        </label>
                      </div>
                      {formData.allowed_ips_enabled && (
                        <div className="mt-2.5 pt-2 border-t border-gray-200/80 dark:border-gray-700/80">
                          <textarea
                            value={formData.allowed_ips}
                            onChange={(e) => setFormData({...formData, allowed_ips: e.target.value})}
                            className="w-full px-2.5 py-1.5 text-xs font-mono rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                            placeholder={"192.168.1.1\n10.0.0.0/24"}
                            rows={2}
                          />
                        </div>
                      )}
                    </div>

                    <div className="p-3 rounded-xl bg-gray-50/80 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700">
                      <div className="flex items-center justify-between">
                        <div>
                          <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1.5">
                            <Gauge size={14} className="text-amber-600 dark:text-amber-400" />
                            Bandwidth Rate Limit
                          </label>
                          <p className="text-[11px] text-gray-500 dark:text-gray-400">
                            Throttle client speed per connection
                          </p>
                        </div>
                        <label className="relative inline-flex items-center cursor-pointer">
                          <input
                            type="checkbox"
                            className="sr-only peer"
                            checked={formData.rate_limit_enabled}
                            onChange={(e) => setFormData({...formData, rate_limit_enabled: e.target.checked})}
                          />
                          <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 shrink-0 relative"></div>
                        </label>
                      </div>
                      {formData.rate_limit_enabled && (
                        <div className="mt-2.5 pt-2 border-t border-gray-200/80 dark:border-gray-700/80 flex items-center gap-2">
                          <input
                            type="number"
                            value={formData.rate_limit_mbps}
                            onChange={(e) => setFormData({...formData, rate_limit_mbps: e.target.value})}
                            className="w-24 px-2.5 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                            placeholder="5"
                            min="0.1"
                            step="0.1"
                          />
                          <span className="text-xs font-medium text-gray-600 dark:text-gray-400">Mbps</span>
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                {/* CDN Mode Extra Options */}
                {formData.cdn_mode && (
                  <div className="p-3.5 rounded-xl bg-blue-50/60 dark:bg-blue-950/20 border border-blue-200/80 dark:border-blue-900/50 space-y-2.5">
                    <label className="text-xs font-bold text-blue-900 dark:text-blue-200 flex items-center gap-1.5">
                      <Globe size={15} className="text-blue-600 dark:text-blue-400" />
                      CDN / WebSocket Configuration
                    </label>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                      <div>
                        <label className="block text-[11px] font-medium text-gray-700 dark:text-gray-300 mb-1">Custom Host</label>
                        <input type="text" value={formData.custom_host} onChange={(e) => setFormData({...formData, custom_host: e.target.value})} className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white" placeholder="e.g. speedtest.net" />
                      </div>
                      <div>
                        <label className="block text-[11px] font-medium text-gray-700 dark:text-gray-300 mb-1">Custom SNI</label>
                        <input type="text" value={formData.custom_sni} onChange={(e) => setFormData({...formData, custom_sni: e.target.value})} className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white" placeholder="e.g. speedtest.net" />
                      </div>
                      <div>
                        <label className="block text-[11px] font-medium text-gray-700 dark:text-gray-300 mb-1">WS Path</label>
                        <input type="text" value={formData.ws_path} onChange={(e) => setFormData({...formData, ws_path: e.target.value})} className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white" placeholder="/graphql" />
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Pre-flight Diagnostic Box */}
          {isTestingConfig && (
            <div className="p-4 rounded-xl bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800 flex items-center gap-3">
              <Loader2 className="animate-spin text-blue-600 dark:text-blue-400 shrink-0" size={20} />
              <span className="text-sm font-medium text-blue-800 dark:text-blue-200">
                Testing node reachability, network ping, and protocol specifications...
              </span>
            </div>
          )}

          {testResult && !isTestingConfig && (
            <div className={`p-4 rounded-xl border transition-all ${
              testResult.valid 
                ? 'bg-emerald-50/70 dark:bg-emerald-950/30 border-emerald-200 dark:border-emerald-800' 
                : 'bg-amber-50/70 dark:bg-amber-950/30 border-amber-200 dark:border-amber-800'
            }`}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  {testResult.valid ? (
                    <CheckCircle2 className="text-emerald-600 dark:text-emerald-400 shrink-0" size={20} />
                  ) : (
                    <AlertTriangle className="text-amber-600 dark:text-amber-400 shrink-0" size={20} />
                  )}
                  <span className="text-sm font-semibold text-gray-900 dark:text-white">
                    {testResult.summary}
                  </span>
                </div>
                {testResult.latency_ms && (
                  <LatencyBadge latency={testResult.latency_ms} status="active" />
                )}
              </div>

              <div className="space-y-2 mt-2">
                {testResult.checks?.map((check: any, idx: number) => (
                  <div key={idx} className="flex items-start gap-2 text-xs">
                    {check.status === 'passed' ? (
                      <CheckCircle2 size={14} className="text-emerald-500 shrink-0 mt-0.5" />
                    ) : check.status === 'warning' ? (
                      <AlertTriangle size={14} className="text-amber-500 shrink-0 mt-0.5" />
                    ) : (
                      <XCircle size={14} className="text-rose-500 shrink-0 mt-0.5" />
                    )}
                    <div className="flex-1">
                      <span className="font-semibold text-gray-800 dark:text-gray-200">{check.title}: </span>
                      <span className="text-gray-600 dark:text-gray-300">{check.detail}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          </div>

          {/* Sticky Footer */}
          <div className="shrink-0 px-4 sm:px-6 py-3.5 border-t border-gray-100 dark:border-gray-700/80 bg-gray-50/90 dark:bg-gray-800/95 backdrop-blur-xs flex items-center justify-between gap-2.5 sm:gap-3">
            <button
              type="button"
              onClick={handleTestConfig}
              disabled={isTestingConfig}
              className="inline-flex items-center gap-2 px-3.5 sm:px-4 py-2 sm:py-2.5 bg-indigo-50 hover:bg-indigo-100 dark:bg-indigo-950/50 dark:hover:bg-indigo-900/60 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800/80 rounded-xl text-xs sm:text-sm font-semibold transition-all shadow-xs cursor-pointer min-h-[44px] disabled:opacity-50"
            >
              {isTestingConfig ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Zap size={16} />
              )}
              <span className="hidden xs:inline sm:inline">{isTestingConfig ? 'Testing...' : 'Test Connection'}</span>
              <span className="inline xs:hidden sm:hidden">{isTestingConfig ? '...' : 'Test'}</span>
            </button>

            <div className="flex items-center gap-2 sm:gap-3">
              <button
                type="button"
                onClick={onClose}
                className="px-3.5 sm:px-4 py-2 sm:py-2.5 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 rounded-xl hover:bg-gray-100 dark:hover:bg-gray-600 border border-gray-200 dark:border-gray-600 text-xs sm:text-sm font-semibold transition-all shadow-xs cursor-pointer min-h-[44px]"
              >
                {t.tunnels.cancel}
              </button>
              <button
                type="submit"
                disabled={isTestingConfig}
                className="px-4 sm:px-5 py-2 sm:py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 text-white rounded-xl text-xs sm:text-sm font-semibold transition-all shadow-md shadow-blue-500/20 disabled:opacity-50 cursor-pointer flex items-center gap-1.5 min-h-[44px]"
              >
                Save Changes
              </button>
            </div>
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
  categories?: TunnelCategory[]
  onCategoryCreated?: (name: string, color?: string) => Promise<any>
  onClose: () => void
  onSuccess: () => void
}

const AddTunnelModal = ({ nodes, servers, categories = [], onCategoryCreated, onClose, onSuccess }: AddTunnelModalProps) => {
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
    rathole_remote_addr: generateRandomControlPort(),
    rathole_token: '',
    rathole_transport: 'tcp',
    chisel_control_port: generateRandomControlPort(),
    chisel_transport: 'ws',
    chisel_backend_url: 'https://speedtest.net',
    chisel_custom_sni: '',
    chisel_custom_host: '',
    chisel_keepalive: '10s',
    frp_bind_port: generateRandomControlPort(),
    frp_token: '',
    frp_local_ip: '127.0.0.1',
    frp_transport: 'tcp',
    frp_security: 'tls',
    frp_sni: '',
    frp_encryption: true,
    frp_compression: true,
    frp_health_check: true,
    frp_bandwidth_limit: '',
    frp_proxy_protocol: 'none',
    frp_custom_domains: '',
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
    selector_strategy: 'fifo',
    utls_fingerprint: 'chrome',
    keepalive_interval: 15,
    failover_ips: '',
    rate_limit_mbps: '',
    allowed_ips: '',
    rate_limit_enabled: false,
    allowed_ips_enabled: false,
    category: '',
  })
  const [showInlineNewCategory, setShowInlineNewCategory] = useState(false)
  const [inlineCategoryName, setInlineCategoryName] = useState('')
  const [backhaulState, setBackhaulState] = useState<BackhaulFormState>(createDefaultBackhaulState())
  const [backhaulAdvanced, setBackhaulAdvanced] = useState<BackhaulAdvancedState>(createDefaultBackhaulAdvancedState())
  const [showBackhaulAdvanced, setShowBackhaulAdvanced] = useState(false)
  const [isTestingConfig, setIsTestingConfig] = useState(false)
  const [testResult, setTestResult] = useState<any | null>(null)

  const handleTestConfig = async () => {
    setIsTestingConfig(true)
    setTestResult(null)
    try {
      const payload: any = {
        core: formData.core,
        iran_node_id: formData.iran_node_id || formData.node_id,
        foreign_node_id: formData.foreign_node_id,
        ports: formData.core === 'backhaul' ? backhaulState.public_port : formData.ports,
        rathole_token: formData.rathole_token,
        rathole_transport: formData.rathole_transport,
        rathole_remote_addr: formData.rathole_remote_addr,
        frp_token: formData.frp_token,
        frp_transport: formData.frp_transport,
        frp_security: formData.frp_security,
        frp_health_check: formData.frp_health_check,
        frp_bandwidth_limit: formData.frp_bandwidth_limit,
        frp_proxy_protocol: formData.frp_proxy_protocol,
        chisel_transport: formData.chisel_transport,
        chisel_backend_url: formData.chisel_backend_url,
        transport: formData.core === 'backhaul' ? backhaulState.transport : (formData.core === 'rathole' ? formData.rathole_transport : (formData.core === 'chisel' ? formData.chisel_transport : (formData.core === 'frp' ? formData.frp_transport : formData.transport_type))),
        is_reverse: formData.is_reverse,
      }
      const response = await api.post('/tunnels/test-config', payload)
      setTestResult(response.data)
      if (response.data?.valid) {
        showToast('success', 'Configuration Valid', response.data.summary || 'All checks passed!')
      } else {
        showToast('warning', 'Configuration Warning', response.data.summary || 'Some checks failed')
      }
    } catch (err: any) {
      const msg = err.response?.data?.detail || err.message || 'Could not perform test'
      showToast('error', 'Test Failed', msg)
      setTestResult({
        valid: false,
        summary: msg,
        checks: [{ title: 'Connection Error', status: 'failed', detail: msg }]
      })
    } finally {
      setIsTestingConfig(false)
    }
  }

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
        const remotePort = formData.rathole_remote_addr || String(Math.floor(25000 + Math.random() * 25000))
        spec.remote_addr = `${remoteHost}:${remotePort}`
        spec.control_port = parseInt(remotePort)
        if (formData.rathole_token) {
          spec.token = formData.rathole_token
        }
        spec.ports = ports
        spec.remote_port = ports[0]
        spec.listen_port = ports[0]
        spec.transport = formData.rathole_transport || 'tcp'
        spec.transport_type = formData.rathole_transport || 'tcp'
        if (formData.custom_sni) {
          spec.custom_sni = formData.custom_sni
          spec.stealth_domain = formData.custom_sni
        }
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
        spec.transport = formData.chisel_transport || 'ws'
        spec.transport_type = formData.chisel_transport || 'ws'
        if (formData.chisel_backend_url) {
          spec.backend_url = formData.chisel_backend_url
        }
        if (formData.chisel_custom_sni) {
          spec.custom_sni = formData.chisel_custom_sni
          spec.stealth_domain = formData.chisel_custom_sni
        }
        if (formData.chisel_custom_host) {
          spec.custom_host = formData.chisel_custom_host
        }
        if (formData.chisel_keepalive) {
          spec.keepalive = formData.chisel_keepalive
        }
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
        if (!formData.node_id && !formData.iran_node_id) {
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
        spec.type = (formData.type === 'udp' || formData.type === 'tcp+udp' || formData.type === 'http' || formData.type === 'https') ? formData.type : 'tcp'
        tunnelType = spec.type
        spec.transport_type = formData.frp_transport || 'tcp'
        spec.transport = formData.frp_transport || 'tcp'
        spec.security_type = formData.frp_security || 'tls'
        spec.custom_sni = formData.frp_sni || ''
        spec.use_encryption = formData.frp_encryption
        spec.use_compression = formData.frp_compression
        spec.enable_health_check = formData.frp_health_check !== false
        spec.health_check_type = formData.frp_health_check !== false ? (formData.type === 'http' ? 'http' : 'tcp') : null
        if (formData.frp_bandwidth_limit) {
          spec.bandwidth_limit = formData.frp_bandwidth_limit
        }
        if (formData.frp_proxy_protocol && formData.frp_proxy_protocol !== 'none') {
          spec.proxy_protocol_version = formData.frp_proxy_protocol
        }
        if (formData.frp_custom_domains) {
          spec.custom_domains = formData.frp_custom_domains.split(',').map((d: string) => d.trim()).filter(Boolean)
        }
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
        gaming_mode: Boolean(formData.gaming_mode || spec?.gaming_mode),
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
          selector_strategy: formData.selector_strategy || 'fifo',
          utls_fingerprint: formData.security_type === 'utls' ? (formData.utls_fingerprint || 'chrome') : null,
          keepalive_interval: formData.keepalive_interval ? parseInt(String(formData.keepalive_interval)) : 15,
          failover_ips: formData.failover_ips ? formData.failover_ips.split('\n').map(ip => ip.trim()).filter(ip => ip.length > 0) : null,
          rate_limit_mbps: formData.rate_limit_enabled && formData.rate_limit_mbps ? parseFloat(formData.rate_limit_mbps) : null,
          allowed_ips: formData.allowed_ips_enabled && formData.allowed_ips 
            ? formData.allowed_ips.split('\n').map(ip => ip.trim()).filter(ip => ip.length > 0)
            : null,
          port_ranges: port_ranges.length > 0 ? port_ranges : null
        }),
        ...(formData.core === 'frp' && {
          transport_type: formData.frp_transport || 'tcp',
          security_type: formData.frp_security || 'tls',
          custom_sni: formData.frp_sni || null,
          is_reverse: true,
        }),
        ...(formData.core === 'chisel' && {
          transport_type: formData.chisel_transport || 'ws',
          security_type: formData.chisel_transport === 'wss' ? 'tls' : 'none',
          custom_sni: formData.chisel_custom_sni || null,
          custom_host: formData.chisel_custom_host || null,
          is_reverse: true,
        }),
        ...(formData.core === 'rathole' && {
          transport_type: formData.rathole_transport || 'tcp',
          is_reverse: true,
        }),
        ...(formData.core === 'backhaul' && {
          transport_type: backhaulState.transport || 'tcpmux',
          is_reverse: true,
        }),
        node_id: formData.is_reverse ? formData.iran_node_id : formData.node_id,
        foreign_node_id: formData.foreign_node_id || null,
        iran_node_id: formData.iran_node_id || formData.node_id || null,
        category: formData.category ? formData.category.trim() : null
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
    const updated = { ...formData, core }
    if (core === 'chisel') {
      newType = 'chisel'
      if (!updated.chisel_control_port) {
        updated.chisel_control_port = generateRandomControlPort()
      }
    } else if (core === 'rathole') {
      newType = (formData.type === 'tcp' || formData.type === 'udp' || formData.type === 'tcp+udp') ? formData.type : 'tcp'
      if (!updated.rathole_remote_addr) {
        updated.rathole_remote_addr = generateRandomControlPort()
      }
    } else if (core === 'frp') {
      newType = (formData.type === 'tcp' || formData.type === 'udp' || formData.type === 'tcp+udp') ? formData.type : 'tcp'
      if (!updated.frp_bind_port || updated.frp_bind_port === '7000') {
        updated.frp_bind_port = generateRandomControlPort()
      }
    } else if (core === 'backhaul') {
      newType = backhaulState.transport
      if (!backhaulState.control_port || backhaulState.control_port === '3080') {
        setBackhaulState(prev => ({ ...prev, control_port: generateRandomControlPort() }))
      }
    } else if (core === 'gost') {
      newType = (formData.type === 'tcp' || formData.type === 'udp' || formData.type === 'grpc' || formData.type === 'tcpmux') ? formData.type : 'tcp'
    } else {
      newType = 'tcp'
    }
    updated.type = newType
    setFormData(updated)
  }

  const generateUUID = () => {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0
      const v = c === 'x' ? r : (r & 0x3) | 0x8
      return v.toString(16)
    })
  }

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-xs flex items-center justify-center z-[100] p-3.5 sm:p-4">
      <div className="bg-white dark:bg-gray-800 rounded-2xl w-full max-w-2xl max-h-[90dvh] shadow-2xl border border-gray-200/80 dark:border-gray-700/80 flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        <form onSubmit={handleSubmit} className="flex flex-col h-full max-h-[90dvh] overflow-hidden">
          {/* Sticky Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 dark:border-gray-700/70 bg-gray-50/70 dark:bg-gray-800/90 shrink-0">
            <div className="flex items-center gap-2.5">
              <span className="p-1.5 rounded-lg bg-blue-100 dark:bg-blue-900/50 text-blue-600 dark:text-blue-400">
                <Plus size={18} />
              </span>
              <h2 className="text-lg sm:text-xl font-bold text-gray-900 dark:text-white">
                {t.tunnels.createTunnel}
              </h2>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors cursor-pointer"
              title="Close"
            >
              <X size={18} />
            </button>
          </div>

          {/* Scrollable Form Body */}
          <div className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-4 min-h-0">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
            <div>
              <label className="block text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
                Name
              </label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700/80 text-gray-900 dark:text-white text-sm font-medium focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all shadow-xs"
                placeholder="e.g. My-Backhaul-Tunnel"
                required
              />
            </div>
            <div>
              <div className="flex items-center justify-between mb-1.5 h-5">
                <label className="text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                  <Tag size={14} className="text-blue-500" />
                  <span>{t.tunnels.categories || 'Category'}</span>
                </label>
                <button
                  type="button"
                  onClick={() => setShowInlineNewCategory(prev => !prev)}
                  className="text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline cursor-pointer"
                >
                  {showInlineNewCategory ? 'Cancel' : '+ New'}
                </button>
              </div>
              <select
                value={formData.category}
                onChange={(e) => setFormData({ ...formData, category: e.target.value })}
                className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700/80 text-gray-900 dark:text-white text-sm font-medium focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all shadow-xs"
              >
                <option value="">{t.tunnels.uncategorized || 'No Category'}</option>
                {categories.map((c) => (
                  <option key={c.id} value={c.name}>{c.name}</option>
                ))}
              </select>
              {showInlineNewCategory && (
                <div className="mt-2 p-2 rounded-xl bg-gray-50 dark:bg-gray-700/60 border border-gray-200 dark:border-gray-600 flex gap-1.5">
                  <input
                    type="text"
                    placeholder="New category..."
                    value={inlineCategoryName}
                    onChange={(e) => setInlineCategoryName(e.target.value)}
                    className="flex-1 px-2.5 py-1 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                  <button
                    type="button"
                    onClick={async () => {
                      if (!inlineCategoryName.trim()) return
                      const created = await onCategoryCreated?.(inlineCategoryName.trim())
                      if (created) {
                        setFormData({ ...formData, category: created.name })
                        setInlineCategoryName('')
                        setShowInlineNewCategory(false)
                      }
                    }}
                    className="px-2.5 py-1 bg-blue-600 text-white rounded-lg text-xs font-semibold hover:bg-blue-700 cursor-pointer"
                  >
                    Save
                  </button>
                </div>
              )}
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
            <div>
              <div className="flex items-center justify-between mb-1.5 h-5">
                <label className="text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300">
                  {t.tunnels.iranNode}
                </label>
              </div>
              <select
                value={formData.iran_node_id || formData.node_id}
                onChange={(e) => setFormData({ ...formData, iran_node_id: e.target.value, node_id: e.target.value })}
                className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700/80 text-gray-900 dark:text-white text-sm font-medium focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all shadow-xs"
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
              <div className="flex items-center justify-between mb-1.5 h-5">
                <label className="text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300">
                  {t.tunnels.foreignServer}
                </label>
              </div>
              <select
                value={formData.foreign_node_id}
                onChange={(e) => setFormData({ ...formData, foreign_node_id: e.target.value })}
                className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700/80 text-gray-900 dark:text-white text-sm font-medium focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all shadow-xs"
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
              <div className="flex items-center justify-between mb-1.5 h-5">
                <label className="text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300">
                  {t.tunnels.core}
                </label>
              </div>
              <select
                value={formData.core}
                onChange={(e) => handleCoreChange(e.target.value)}
                className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700/80 text-gray-900 dark:text-white text-sm font-medium focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all shadow-xs"
              >
                <option value="gost">GOST</option>
                <option value="rathole">Rathole</option>
                <option value="backhaul">Backhaul</option>
                <option value="chisel">Chisel</option>
                <option value="frp">FRP</option>
              </select>
            </div>
            <div>
              <div className="flex items-center justify-between mb-1.5 h-5">
                <label className="text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300">
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
                className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700/80 text-gray-900 dark:text-white text-sm font-medium focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all shadow-xs"
              >
                {formData.core === 'chisel' ? (
                  <>
                    <option value="tcp">TCP (Standard)</option>
                    <option value="udp">UDP (Gaming / Anti-Lag)</option>
                    <option value="tcp+udp">TCP + UDP (Dual Forward)</option>
                    <option value="socks5">SOCKS5 (Dynamic Proxy)</option>
                  </>
                ) : formData.core === 'rathole' ? (
                  <>
                    <option value="tcp">TCP</option>
                    <option value="udp">UDP (Gaming)</option>
                    <option value="tcp+udp">TCP + UDP</option>
                  </>
                ) : formData.core === 'frp' ? (
                  <>
                    <option value="tcp">TCP</option>
                    <option value="udp">UDP</option>
                  </>
                ) : formData.core === 'backhaul' ? (
                  <>
                    <option value="tcp">TCP</option>
                    <option value="udp">UDP (Pure UDP - Gaming)</option>
                    <option value="tcpmux">TCPMux</option>
                    <option value="ws">WebSocket (WS)</option>
                    <option value="wsmux">WebSocket Mux</option>
                    <option value="wss">WebSocket Secure (WSS)</option>
                    <option value="wssmux">WebSocket Secure Mux</option>
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
                  className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700/80 text-gray-900 dark:text-white text-base sm:text-sm font-mono focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all shadow-xs"
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
                  className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700/80 text-gray-900 dark:text-white text-base sm:text-sm font-mono focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all shadow-xs"
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
                if (partial.gaming_mode !== undefined) {
                  setFormData((prev: any) => ({ ...prev, gaming_mode: Boolean(partial.gaming_mode) }))
                  if (partial.gaming_mode) {
                    setBackhaulAdvanced((prev) => ({
                      ...prev,
                      server: {
                        ...prev.server,
                        nodelay: true,
                        channel_size: '8192',
                        mux_framesize: '4096',
                        mss: '1380',
                        keepalive_period: '12',
                        heartbeat: '12',
                        so_rcvbuf: '2097152',
                        so_sndbuf: '2097152',
                      },
                      client: {
                        ...prev.client,
                        nodelay: true,
                        channel_size: '8192',
                        mux_framesize: '4096',
                        mss: '1380',
                        keepalive_period: '12',
                        heartbeat: '12',
                        so_rcvbuf: '2097152',
                        so_sndbuf: '2097152',
                        aggressive_pool: true,
                      }
                    }))
                  }
                }
              }}
              onOpenAdvanced={() => setShowBackhaulAdvanced(true)}
              acceptUdpVisible={
                backhaulState.transport === 'tcp' || backhaulState.transport === 'tcpmux'
              }
            />
          )}
          
          {/* Rathole Core Settings */}
          {formData.core === 'rathole' && (
            <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-br from-orange-50/60 via-gray-50 to-amber-50/40 dark:from-orange-950/20 dark:via-gray-800/60 dark:to-amber-950/20 border border-orange-200/70 dark:border-orange-900/40 space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 pb-3 border-b border-gray-200/70 dark:border-gray-700/70">
                <div className="flex items-center gap-2">
                  <span className="p-1.5 rounded-lg bg-orange-100 dark:bg-orange-900/60 text-orange-600 dark:text-orange-300">
                    <Cpu size={16} />
                  </span>
                  <div>
                    <h4 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">
                      Rathole Settings
                    </h4>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      Ultra-Lightweight, Secure NAT-Traversal Core in Rust
                    </p>
                  </div>
                </div>
                <span className="self-start sm:self-auto text-xs px-2.5 py-0.5 rounded-full font-mono font-medium bg-orange-100/80 text-orange-700 dark:bg-orange-900/60 dark:text-orange-300 border border-orange-200 dark:border-orange-800">
                  Rathole Core
                </span>
              </div>

              {/* Quick Presets for Rathole */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                    <Sparkles size={14} className="text-orange-600 dark:text-orange-400" />
                    Quick Presets (1-Click Optimization)
                  </span>
                  <span className="text-[11px] text-gray-400 dark:text-gray-500">Auto-configures transport & encryption</span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        rathole_transport: 'noise',
                        custom_sni: ''
                      }));
                      showToast('info', 'Preset Applied', 'Noise Protocol (Gaming & Anti-DPI) applied');
                    }}
                    className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.rathole_transport === 'noise'
                        ? 'bg-orange-100/80 dark:bg-orange-950/50 border-orange-400 dark:border-orange-500 ring-2 ring-orange-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-orange-50 dark:hover:bg-orange-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Shield size={16} className="text-orange-600 dark:text-orange-400" />
                      <span className="text-xs font-bold text-orange-700 dark:text-orange-300">Noise Protocol</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      WireGuard-grade encrypted tunnel. Zero handshake lag.
                    </p>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        rathole_transport: 'wss',
                        custom_sni: 'dl.google.com'
                      }));
                      showToast('info', 'Preset Applied', 'WSS Camouflage (TLS + CDN Capable) applied');
                    }}
                    className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.rathole_transport === 'wss'
                        ? 'bg-blue-100/80 dark:bg-blue-950/50 border-blue-400 dark:border-blue-500 ring-2 ring-blue-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-blue-50 dark:hover:bg-blue-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Globe size={16} className="text-blue-600 dark:text-blue-400" />
                      <span className="text-xs font-bold text-blue-700 dark:text-blue-300">WSS Stealth</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      HTTPS WebSocket + TLS SNI spoofing for anti-DPI.
                    </p>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        rathole_transport: 'tcp',
                        custom_sni: ''
                      }));
                      showToast('info', 'Preset Applied', 'Standard TCP applied');
                    }}
                    className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      (!formData.rathole_transport || formData.rathole_transport === 'tcp')
                        ? 'bg-gray-100/80 dark:bg-gray-700/50 border-gray-400 dark:border-gray-500 ring-2 ring-gray-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-gray-50 dark:hover:bg-gray-700/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Zap size={16} className="text-gray-600 dark:text-gray-400" />
                      <span className="text-xs font-bold text-gray-700 dark:text-gray-300">TCP Standard</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      Raw direct TCP stream. Lowest CPU overhead.
                    </p>
                  </button>
                </div>
              </div>

              {/* Ports & Connectivity Grid */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start pt-1">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Radio size={14} className="text-orange-500" />
                      Forwarded Ports
                    </label>
                  </div>
                  <input
                    type="text"
                    value={formData.ports}
                    onChange={(e) => setFormData({ ...formData, ports: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 transition-all font-mono"
                    placeholder="8080,8081,8082"
                    required
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Ports (comma-separated, same for panel and node local service).
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Server size={14} className="text-amber-500" />
                      Rathole Port
                    </label>
                    <button
                      type="button"
                      onClick={() => setFormData({ ...formData, rathole_remote_addr: generateRandomControlPort() })}
                      className="flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[11px] font-medium bg-orange-50 dark:bg-orange-950/40 text-orange-600 dark:text-orange-400 hover:bg-orange-100 dark:hover:bg-orange-900/60 border border-orange-200/60 dark:border-orange-800/40 transition-all cursor-pointer shadow-xs"
                      title="Generate Random Port"
                    >
                      <Dices size={12} />
                      <span>Random</span>
                    </button>
                  </div>
                  <input
                    type="number"
                    value={formData.rathole_remote_addr}
                    onChange={(e) => setFormData({ ...formData, rathole_remote_addr: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 transition-all font-mono"
                    placeholder="23333"
                    min="1"
                    max="65535"
                    required
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Rathole server port on panel (IP: {window.location.hostname}).
                  </p>
                </div>
              </div>

              {/* Transport Protocol Select & Token */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <span className="w-1.5 h-1.5 rounded-full bg-orange-500"></span>
                      Transport Protocol
                    </label>
                    <span className="text-[11px] font-mono uppercase text-gray-400">
                      {formData.rathole_transport || 'tcp'}
                    </span>
                  </div>
                  <select
                    value={formData.rathole_transport || 'tcp'}
                    onChange={(e) => setFormData({ ...formData, rathole_transport: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 transition-all font-medium"
                  >
                    <option value="tcp">TCP (Standard Raw)</option>
                    <option value="noise">Noise Protocol (Encrypted / Gaming / Anti-DPI)</option>
                    <option value="ws">WebSocket (WS)</option>
                    <option value="wss">WebSocket + TLS (WSS)</option>
                  </select>
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    {formData.rathole_transport === 'noise' && 'WireGuard-grade 256-bit encryption with zero-handshake delay.'}
                    {formData.rathole_transport === 'wss' && 'Standard TLS 1.3 encapsulation for anti-DPI camouflage.'}
                    {formData.rathole_transport === 'ws' && 'Standard WebSocket stream.'}
                    {(!formData.rathole_transport || formData.rathole_transport === 'tcp') && 'Raw TCP stream without encryption wrapper.'}
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Key size={14} className="text-indigo-500" />
                      Auth Token
                    </label>
                    <span className="text-[11px] text-gray-400">Optional</span>
                  </div>
                  <input
                    type="text"
                    value={formData.rathole_token}
                    onChange={(e) => setFormData({ ...formData, rathole_token: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all font-mono"
                    placeholder="Auto-generated if empty"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Cryptographic secret token shared between panel and client.
                  </p>
                </div>
              </div>

              {/* SNI Camouflage */}
              {(formData.rathole_transport === 'wss' || formData.rathole_transport === 'ws') && (
                <div className="p-3.5 rounded-xl bg-orange-50/70 dark:bg-orange-950/20 border border-orange-200/80 dark:border-orange-900/50 space-y-2">
                  <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1.5">
                    <EyeOff size={14} className="text-orange-600 dark:text-orange-400" />
                    Custom SNI / Domain Camouflage
                  </label>
                  <p className="text-[11px] text-gray-500 dark:text-gray-400">
                    TLS Server Name Indication (SNI) spoofing to mimic legitimate web services:
                  </p>
                  <input
                    type="text"
                    value={formData.custom_sni || ''}
                    onChange={(e) => setFormData({ ...formData, custom_sni: e.target.value })}
                    placeholder="e.g. dl.google.com or cdn.cloudflare.com"
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-lg border border-orange-300 dark:border-orange-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500/20 font-medium"
                  />
                </div>
              )}
            </div>
          )}
          
          {/* Chisel Core Settings */}
          {/* Chisel Core Settings */}
          {formData.core === 'chisel' && (
            <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-br from-teal-50/60 via-gray-50 to-emerald-50/40 dark:from-teal-950/20 dark:via-gray-800/60 dark:to-emerald-950/20 border border-teal-200/70 dark:border-teal-900/40 space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 pb-3 border-b border-gray-200/70 dark:border-gray-700/70">
                <div className="flex items-center gap-2">
                  <span className="p-1.5 rounded-lg bg-teal-100 dark:bg-teal-900/60 text-teal-600 dark:text-teal-300">
                    <Terminal size={16} />
                  </span>
                  <div>
                    <h4 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">
                      Chisel Advanced Settings
                    </h4>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      High-Speed WebSocket Tunnel with SSH Multiplexing, WSS TLS & Anti-DPI Camouflage
                    </p>
                  </div>
                </div>
                <span className="self-start sm:self-auto text-xs px-2.5 py-0.5 rounded-full font-mono font-medium bg-teal-100/80 text-teal-700 dark:bg-teal-900/60 dark:text-teal-300 border border-teal-200 dark:border-teal-800">
                  Chisel Core
                </span>
              </div>

              {/* Quick Preset Buttons */}
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                  <Sparkles size={14} className="text-teal-500" />
                  Quick Optimization Presets
                </label>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        chisel_transport: 'wss',
                        chisel_backend_url: 'https://speedtest.net',
                        chisel_custom_sni: 'speedtest.net',
                        chisel_keepalive: '10s'
                      }));
                      showToast('info', 'Preset Applied', 'Anti-DPI Stealth Preset (WSS + Camouflage + SNI) configured');
                    }}
                    className={`p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.chisel_transport === 'wss' && formData.chisel_backend_url
                        ? 'bg-teal-100/80 dark:bg-teal-900/50 border-teal-400 dark:border-teal-500 ring-2 ring-teal-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-teal-50 dark:hover:bg-teal-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Shield size={15} className="text-teal-600 dark:text-teal-400" />
                      <span className="text-xs font-bold text-teal-800 dark:text-teal-200">Anti-DPI Stealth</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      WSS TLS + speedtest.net decoy camouflage against active probers.
                    </p>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        type: 'udp',
                        chisel_keepalive: '5s'
                      }));
                      showToast('info', 'Preset Applied', 'Ultra-Low Ping Gaming (UDP + 5s Keepalive) configured');
                    }}
                    className={`p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.type === 'udp' && formData.chisel_keepalive === '5s'
                        ? 'bg-amber-100/80 dark:bg-amber-900/50 border-amber-400 dark:border-amber-500 ring-2 ring-amber-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-amber-50 dark:hover:bg-amber-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Zap size={15} className="text-amber-500 dark:text-amber-400" />
                      <span className="text-xs font-bold text-amber-700 dark:text-amber-300">Ultra-Low Ping</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      UDP Gaming mode + aggressive 5s Keepalive against packet drops.
                    </p>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        chisel_transport: 'ws',
                        chisel_custom_host: 'cdn.cloudflare.com',
                        chisel_keepalive: '15s'
                      }));
                      showToast('info', 'Preset Applied', 'CDN-Friendly WebSocket Preset configured');
                    }}
                    className={`p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.chisel_transport === 'ws' && formData.chisel_custom_host === 'cdn.cloudflare.com'
                        ? 'bg-blue-100/80 dark:bg-blue-900/50 border-blue-400 dark:border-blue-500 ring-2 ring-blue-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-blue-50 dark:hover:bg-blue-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Globe size={15} className="text-blue-500 dark:text-blue-400" />
                      <span className="text-xs font-bold text-blue-700 dark:text-blue-300">CDN Friendly</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      HTTP/WS with Host spoofing compatible with reverse proxies.
                    </p>
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Radio size={14} className="text-teal-500" />
                      Forwarded Ports
                    </label>
                    <span className="text-[11px] text-gray-400">Reverse & Local</span>
                  </div>
                  <input
                    type="text"
                    value={formData.ports}
                    onChange={(e) => setFormData({ ...formData, ports: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500 transition-all font-mono"
                    placeholder="8080,8081,8082"
                    required
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Comma-separated ports mapped through the tunnel.
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Server size={14} className="text-emerald-500" />
                      Control Port
                    </label>
                    <button
                      type="button"
                      onClick={() => setFormData({ ...formData, chisel_control_port: generateRandomControlPort() })}
                      className="flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[11px] font-medium bg-teal-50 dark:bg-teal-950/40 text-teal-600 dark:text-teal-400 hover:bg-teal-100 dark:hover:bg-teal-900/60 border border-teal-200/60 dark:border-teal-800/40 transition-all cursor-pointer shadow-xs"
                      title="Generate Random Control Port"
                    >
                      <Dices size={12} />
                      <span>Random</span>
                    </button>
                  </div>
                  <input
                    type="number"
                    value={formData.chisel_control_port}
                    onChange={(e) => setFormData({ ...formData, chisel_control_port: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500 transition-all font-mono"
                    placeholder={`${(parseInt(formData.ports.split(',')[0]?.trim()) || 8080) + 10000} (auto)`}
                    min="1"
                    max="65535"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Chisel server control port (leave empty for auto: first port + 10000).
                  </p>
                </div>
              </div>

              {/* Transport Protocol & Keepalive */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start pt-1">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Shield size={14} className="text-teal-500" />
                      Transport Protocol & Encryption
                    </label>
                    <span className="text-[11px] font-mono uppercase text-gray-400 dark:text-gray-500">
                      {formData.chisel_transport}
                    </span>
                  </div>
                  <select
                    value={formData.chisel_transport}
                    onChange={(e) => setFormData({ ...formData, chisel_transport: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500 transition-all font-medium"
                  >
                    <option value="ws">WS - Plain WebSocket (HTTP, Low Overhead)</option>
                    <option value="wss">WSS - Encrypted WebSocket over TLS (HTTPS)</option>
                  </select>
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    {formData.chisel_transport === 'wss'
                      ? 'Outer TLS encryption layer + Inner SSH stream encryption (Double Layer Security).'
                      : 'Standard WebSocket with built-in inner SSH encryption layer.'}
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Zap size={14} className="text-amber-500" />
                      Keepalive Heartbeat
                    </label>
                    <span className="text-[11px] font-mono text-gray-400 dark:text-gray-500">
                      {formData.chisel_keepalive}
                    </span>
                  </div>
                  <select
                    value={formData.chisel_keepalive}
                    onChange={(e) => setFormData({ ...formData, chisel_keepalive: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500 transition-all font-medium"
                  >
                    <option value="5s">5s (Ultra-Aggressive / Competitive Gaming)</option>
                    <option value="10s">10s (Recommended - High Stability)</option>
                    <option value="15s">15s (Balanced)</option>
                    <option value="25s">25s (Default / Low Overhead)</option>
                  </select>
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Prevents firewall NAT translation state dropouts across Iranian ISPs.
                  </p>
                </div>
              </div>

              {/* Anti-Probing Camouflage & Decoy Website */}
              <div className="p-3.5 rounded-xl bg-teal-50/70 dark:bg-teal-950/20 border border-teal-200/70 dark:border-teal-900/40 space-y-3">
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-xs font-bold text-teal-900 dark:text-teal-200 flex items-center gap-1.5">
                      <Shield size={14} className="text-teal-600 dark:text-teal-400" />
                      Decoy Camouflage Backend (--backend)
                    </label>
                    <span className="text-[10px] px-2 py-0.5 rounded-md font-medium bg-teal-200/60 text-teal-800 dark:bg-teal-900/60 dark:text-teal-300">
                      Anti-Active Probing
                    </span>
                  </div>
                  <p className="text-[11px] text-teal-800/80 dark:text-teal-300/80 mb-2">
                    When censors or scanning bots probe your control port via standard HTTP/HTTPS, Chisel proxies the request to this legitimate website with HTTP 200 OK:
                  </p>
                  <input
                    type="text"
                    value={formData.chisel_backend_url}
                    onChange={(e) => setFormData({ ...formData, chisel_backend_url: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-teal-300 dark:border-teal-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white font-mono"
                    placeholder="https://speedtest.net or https://example.com"
                  />
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
                  <div>
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 block mb-1">
                      Custom SNI (--sni)
                    </label>
                    <input
                      type="text"
                      value={formData.chisel_custom_sni}
                      onChange={(e) => setFormData({ ...formData, chisel_custom_sni: e.target.value })}
                      className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white font-mono"
                      placeholder="speedtest.net or domain.com"
                    />
                    <p className="text-[10px] text-gray-500 dark:text-gray-400 mt-1">
                      Overrides TLS ClientHello Server Name Indication.
                    </p>
                  </div>

                  <div>
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 block mb-1">
                      Custom Host Header (--hostname)
                    </label>
                    <input
                      type="text"
                      value={formData.chisel_custom_host}
                      onChange={(e) => setFormData({ ...formData, chisel_custom_host: e.target.value })}
                      className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white font-mono"
                      placeholder="cdn.speedtest.net"
                    />
                    <p className="text-[10px] text-gray-500 dark:text-gray-400 mt-1">
                      Sets HTTP Host header for reverse proxies / CDN fronting.
                    </p>
                  </div>
                </div>
              </div>
            </div>
          )}
          
          {/* FRP Core Settings */}
          {formData.core === 'frp' && (
            <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-br from-cyan-50/60 via-gray-50 to-blue-50/40 dark:from-cyan-950/20 dark:via-gray-800/60 dark:to-blue-950/20 border border-cyan-200/70 dark:border-cyan-900/40 space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 pb-3 border-b border-gray-200/70 dark:border-gray-700/70">
                <div className="flex items-center gap-2">
                  <span className="p-1.5 rounded-lg bg-cyan-100 dark:bg-cyan-900/60 text-cyan-600 dark:text-cyan-300">
                    <Layers size={16} />
                  </span>
                  <div>
                    <h4 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">
                      FRP Advanced Settings
                    </h4>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      High-Performance Reverse Proxy with Multiplexing & Encryption
                    </p>
                  </div>
                </div>
                <span className="self-start sm:self-auto text-xs px-2.5 py-0.5 rounded-full font-mono font-medium bg-cyan-100/80 text-cyan-700 dark:bg-cyan-900/60 dark:text-cyan-300 border border-cyan-200 dark:border-cyan-800">
                  FRP v0.50+ Core
                </span>
              </div>

              {/* Quick Presets for FRP */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                    <Sparkles size={14} className="text-cyan-600 dark:text-cyan-400" />
                    Quick Presets (1-Click Optimization)
                  </span>
                  <span className="text-[11px] text-gray-400 dark:text-gray-500">Auto-configures transport & encryption</span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        frp_transport: 'quic',
                        frp_encryption: true,
                        frp_compression: true,
                      }));
                      showToast('info', 'Preset Applied', 'QUIC Fast UDP applied');
                    }}
                    className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.frp_transport === 'quic'
                        ? 'bg-cyan-100/80 dark:bg-cyan-950/50 border-cyan-400 dark:border-cyan-500 ring-2 ring-cyan-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-cyan-50 dark:hover:bg-cyan-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Zap size={16} className="text-cyan-600 dark:text-cyan-400" />
                      <span className="text-xs font-bold text-cyan-700 dark:text-cyan-300">QUIC Fast UDP</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      Fast 0-RTT UDP stream with built-in TLS 1.3 multiplexing.
                    </p>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        frp_transport: 'wss',
                        frp_sni: 'dl.google.com',
                        frp_encryption: true,
                        frp_compression: true,
                      }));
                      showToast('info', 'Preset Applied', 'WSS Stealth / Anti-DPI applied');
                    }}
                    className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.frp_transport === 'wss'
                        ? 'bg-indigo-100/80 dark:bg-indigo-950/50 border-indigo-400 dark:border-indigo-500 ring-2 ring-indigo-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-indigo-50 dark:hover:bg-indigo-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Globe size={16} className="text-indigo-600 dark:text-indigo-400" />
                      <span className="text-xs font-bold text-indigo-700 dark:text-indigo-300">WSS Stealth</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      Secure WebSocket + SNI camouflage. CDN friendly.
                    </p>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        frp_transport: 'kcp',
                        frp_encryption: true,
                        frp_compression: false,
                      }));
                      showToast('info', 'Preset Applied', 'KCP Anti-Packet-Loss applied');
                    }}
                    className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.frp_transport === 'kcp'
                        ? 'bg-emerald-100/80 dark:bg-emerald-950/50 border-emerald-400 dark:border-emerald-500 ring-2 ring-emerald-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-emerald-50 dark:hover:bg-emerald-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Rocket size={16} className="text-emerald-600 dark:text-emerald-400" />
                      <span className="text-xs font-bold text-emerald-700 dark:text-emerald-300">KCP Anti-Loss</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      Aggressive ARQ retransmission for unstable routes.
                    </p>
                  </button>
                </div>
              </div>

              {/* Bind Port & Ports */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start pt-1">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Server size={14} className="text-cyan-500" />
                      FRP Bind Port
                    </label>
                    <button
                      type="button"
                      onClick={() => setFormData({ ...formData, frp_bind_port: generateRandomControlPort() })}
                      className="flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[11px] font-medium bg-cyan-50 dark:bg-cyan-950/40 text-cyan-600 dark:text-cyan-400 hover:bg-cyan-100 dark:hover:bg-cyan-900/60 border border-cyan-200/60 dark:border-cyan-800/40 transition-all cursor-pointer shadow-xs"
                      title="Generate Random Bind Port"
                    >
                      <Dices size={12} />
                      <span>Random</span>
                    </button>
                  </div>
                  <input
                    type="number"
                    value={formData.frp_bind_port}
                    onChange={(e) => setFormData({ ...formData, frp_bind_port: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-cyan-500/20 focus:border-cyan-500 transition-all font-mono"
                    placeholder="7000"
                    min="1"
                    max="65535"
                    required
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    FRP server port on panel (default: 7000).
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Radio size={14} className="text-blue-500" />
                      Forwarded Ports
                    </label>
                    <span className="text-[11px] text-gray-400">Remote & Local</span>
                  </div>
                  <input
                    type="text"
                    value={formData.ports}
                    onChange={(e) => setFormData({ ...formData, ports: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-cyan-500/20 focus:border-cyan-500 transition-all font-mono"
                    placeholder="8080,8081,8082"
                    required
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Ports (comma-separated, same for remote port and local port).
                  </p>
                </div>
              </div>

              {/* Transport & SNI */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <span className="w-1.5 h-1.5 rounded-full bg-cyan-500"></span>
                      Transport Protocol
                    </label>
                    <span className="text-[11px] font-mono uppercase text-gray-400">
                      {formData.frp_transport || 'tcp'}
                    </span>
                  </div>
                  <select
                    value={formData.frp_transport || 'tcp'}
                    onChange={(e) => setFormData({ ...formData, frp_transport: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-cyan-500/20 focus:border-cyan-500 transition-all font-medium"
                  >
                    <option value="tcp">TCP (with TLS & Zero-Byte Signature)</option>
                    <option value="kcp">KCP (Fast UDP - Resilient to Packet Loss)</option>
                    <option value="quic">QUIC (HTTP/3 UDP + TLS 1.3 Multiplex)</option>
                    <option value="websocket">WebSocket (Plain WS)</option>
                    <option value="wss">WSS (Secure WebSocket - CDN Capable)</option>
                  </select>
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    {formData.frp_transport === 'quic' && 'Ultra-fast 0-RTT UDP multiplexing with TLS 1.3 encryption.'}
                    {formData.frp_transport === 'kcp' && 'Aggressive ARQ UDP for bad or filtered routes.'}
                    {formData.frp_transport === 'wss' && 'Encrypted WebSocket compatible with CDN reverse proxies.'}
                    {formData.frp_transport === 'websocket' && 'Standard HTTP WebSocket proxy.'}
                    {(!formData.frp_transport || formData.frp_transport === 'tcp') && 'Raw TCP stream with TLS signature hiding.'}
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <EyeOff size={14} className="text-purple-500" />
                      Stealth SNI / Camouflage Domain
                    </label>
                    <span className="text-[11px] text-gray-400">TLS Header</span>
                  </div>
                  <input
                    type="text"
                    value={formData.frp_sni}
                    onChange={(e) => setFormData({ ...formData, frp_sni: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-purple-500/20 focus:border-purple-500 transition-all font-medium"
                    placeholder="e.g. speedtest.net or domain.com"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Domain name inserted into TLS handshake to evade deep packet inspection.
                  </p>
                </div>
              </div>

              {/* Security Toggles Card */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
                  <div className="pr-2">
                    <div className="flex items-center gap-1.5">
                      <Lock size={15} className="text-cyan-600 dark:text-cyan-400" />
                      <span className="text-xs font-bold text-gray-900 dark:text-white">Payload Encryption</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                      AES-128-CFB / ChaCha20 cipher
                    </p>
                  </div>
                  <input
                    type="checkbox"
                    className="sr-only peer"
                    checked={formData.frp_encryption}
                    onChange={(e) => setFormData({ ...formData, frp_encryption: e.target.checked })}
                  />
                  <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-cyan-600 shrink-0 relative"></div>
                </label>

                <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
                  <div className="pr-2">
                    <div className="flex items-center gap-1.5">
                      <Zap size={15} className="text-cyan-600 dark:text-cyan-400" />
                      <span className="text-xs font-bold text-gray-900 dark:text-white">Snappy Compression</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                      Reduces bandwidth & obfuscates entropy
                    </p>
                  </div>
                  <input
                    type="checkbox"
                    className="sr-only peer"
                    checked={formData.frp_compression}
                    onChange={(e) => setFormData({ ...formData, frp_compression: e.target.checked })}
                  />
                  <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-cyan-600 shrink-0 relative"></div>
                </label>
              </div>

              {/* Auth Token */}
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                    <Key size={14} className="text-indigo-500" />
                    Authentication Token
                  </label>
                  <span className="text-[11px] text-gray-400">Optional</span>
                </div>
                <input
                  type="text"
                  value={formData.frp_token}
                  onChange={(e) => setFormData({ ...formData, frp_token: e.target.value })}
                  className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all font-mono"
                  placeholder="Auto-generated if empty"
                />
                <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                  Shared secret token verifying client-server authorization.
                </p>
              </div>

              {/* Reliability & Shaping */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
                  <div className="pr-2">
                    <div className="flex items-center gap-1.5">
                      <Activity size={15} className="text-emerald-600 dark:text-emerald-400" />
                      <span className="text-xs font-bold text-gray-900 dark:text-white">Native Health Check</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                      Auto-detect dead links & reconnect fast
                    </p>
                  </div>
                  <input
                    type="checkbox"
                    className="sr-only peer"
                    checked={formData.frp_health_check}
                    onChange={(e) => setFormData({ ...formData, frp_health_check: e.target.checked })}
                  />
                  <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-emerald-600 shrink-0 relative"></div>
                </label>

                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Network size={14} className="text-blue-500" />
                      Proxy Protocol Version
                    </label>
                  </div>
                  <select
                    value={formData.frp_proxy_protocol || 'none'}
                    onChange={(e) => setFormData({ ...formData, frp_proxy_protocol: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all font-medium"
                  >
                    <option value="none">Disabled (Direct)</option>
                    <option value="v1">v1 (ASCII Text)</option>
                    <option value="v2">v2 (Binary Fast)</option>
                  </select>
                </div>
              </div>

              {/* Bandwidth Limit & Custom Domains */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Gauge size={14} className="text-amber-500" />
                      Bandwidth Rate Limit
                    </label>
                    <span className="text-[11px] text-gray-400">Optional</span>
                  </div>
                  <input
                    type="text"
                    value={formData.frp_bandwidth_limit}
                    onChange={(e) => setFormData({ ...formData, frp_bandwidth_limit: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-amber-500/20 focus:border-amber-500 transition-all font-mono"
                    placeholder="e.g. 10MB or 500KB"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Per-proxy bandwidth limit cap (e.g. 20MB).
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Globe size={14} className="text-cyan-500" />
                      VHost Domains (Optional)
                    </label>
                    <span className="text-[11px] text-gray-400">HTTP/HTTPS</span>
                  </div>
                  <input
                    type="text"
                    value={formData.frp_custom_domains}
                    onChange={(e) => setFormData({ ...formData, frp_custom_domains: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-cyan-500/20 focus:border-cyan-500 transition-all font-mono"
                    placeholder="e.g. app.domain.com"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Custom domain routing for HTTP/HTTPS tunnels.
                  </p>
                </div>
              </div>
            </div>
          )}
          
          {/* v4 to v6 tunnel toggle card - only for Rathole, Backhaul, Chisel, FRP (not GOST) */}
          {formData.core !== 'gost' && (
            <label className="p-3.5 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
              <div className="pr-2">
                <div className="flex items-center gap-2">
                  <Globe size={15} className="text-blue-600 dark:text-blue-400" />
                  <span className="text-xs font-bold text-gray-900 dark:text-white">v4 to v6 Tunnel</span>
                </div>
                <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                  Listen on IPv4 (Iran node) and forward to target node via IPv6.
                </p>
              </div>
              <input
                type="checkbox"
                id="v4_to_v6"
                className="sr-only peer"
                checked={formData.use_ipv6}
                onChange={(e) => setFormData({ ...formData, use_ipv6: e.target.checked })}
              />
              <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 shrink-0 relative"></div>
            </label>
          )}

          {/* Advanced GOST Settings */}
          {formData.core === 'gost' && (
            <div className="mt-6 border-t border-gray-200 dark:border-gray-700 pt-6">
              <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-br from-purple-50/60 via-gray-50 to-blue-50/40 dark:from-purple-950/20 dark:via-gray-800/60 dark:to-blue-950/20 border border-purple-200/70 dark:border-purple-900/40 space-y-4">
                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 pb-3 border-b border-gray-200/70 dark:border-gray-700/70">
                  <div className="flex items-center gap-2">
                    <span className="p-1.5 rounded-lg bg-purple-100 dark:bg-purple-900/60 text-purple-600 dark:text-purple-300">
                      <Zap size={16} />
                    </span>
                    <div>
                      <h4 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">
                        Advanced GOST Settings
                      </h4>
                      <p className="text-xs text-gray-500 dark:text-gray-400">
                        Next-Gen Anti-DPI & High-Performance Routing Core
                      </p>
                    </div>
                  </div>
                  <span className="self-start sm:self-auto text-xs px-2.5 py-0.5 rounded-full font-mono font-medium bg-purple-100/80 text-purple-700 dark:bg-purple-900/60 dark:text-purple-300 border border-purple-200 dark:border-purple-800">
                    GOST v3.0 Core
                  </span>
                </div>

                {/* Quick Presets Grid */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Sparkles size={14} className="text-purple-600 dark:text-purple-400" />
                      Quick Presets (1-Click Optimization)
                    </span>
                    <span className="text-[11px] text-gray-400 dark:text-gray-500">Auto-configures transport & security</span>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        setFormData(prev => ({
                          ...prev,
                          transport_type: 'grpc',
                          security_type: 'utls',
                          utls_fingerprint: 'chrome',
                          stealth_domain: 'www.google.com',
                          keepalive_interval: 15
                        }));
                        showToast('info', 'Preset Applied', 'Stealth Anti-DPI (gRPC + uTLS Chrome) applied');
                      }}
                      className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                        formData.transport_type === 'grpc' && formData.security_type === 'utls'
                          ? 'bg-purple-100/80 dark:bg-purple-900/50 border-purple-400 dark:border-purple-500 ring-2 ring-purple-400/20'
                          : 'bg-white/80 dark:bg-gray-800/80 hover:bg-purple-50 dark:hover:bg-purple-950/30 border-gray-200 dark:border-gray-700'
                      }`}
                    >
                      <div className="flex items-center gap-1.5 mb-1">
                        <Shield size={16} className="text-purple-600 dark:text-purple-400" />
                        <span className="text-xs font-bold text-purple-700 dark:text-purple-300">Stealth Anti-DPI</span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                        gRPC + uTLS Chrome. Bypasses deep packet inspection.
                      </p>
                    </button>

                    <button
                      type="button"
                      onClick={() => {
                        setFormData(prev => ({
                          ...prev,
                          transport_type: 'quic',
                          security_type: 'tls',
                          gaming_mode: true,
                          keepalive_interval: 15
                        }));
                        showToast('info', 'Preset Applied', 'Ultra-Low Ping (QUIC HTTP/3) applied');
                      }}
                      className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                        formData.transport_type === 'quic'
                          ? 'bg-amber-100/80 dark:bg-amber-900/50 border-amber-400 dark:border-amber-500 ring-2 ring-amber-400/20'
                          : 'bg-white/80 dark:bg-gray-800/80 hover:bg-amber-50 dark:hover:bg-amber-950/30 border-gray-200 dark:border-gray-700'
                      }`}
                    >
                      <div className="flex items-center gap-1.5 mb-1">
                        <Zap size={16} className="text-amber-500 dark:text-amber-400" />
                        <span className="text-xs font-bold text-amber-700 dark:text-amber-300">Ultra-Low Ping</span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                        QUIC / HTTP/3. Fast 0-RTT UDP for gaming & streaming.
                      </p>
                    </button>

                    <button
                      type="button"
                      onClick={() => {
                        setFormData(prev => ({
                          ...prev,
                          transport_type: 'kcp',
                          security_type: 'none',
                          gaming_mode: true,
                          keepalive_interval: 15
                        }));
                        showToast('info', 'Preset Applied', 'Anti-Packet-Loss (KCP ARQ) applied');
                      }}
                      className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                        formData.transport_type === 'kcp'
                          ? 'bg-emerald-100/80 dark:bg-emerald-900/50 border-emerald-400 dark:border-emerald-500 ring-2 ring-emerald-400/20'
                          : 'bg-white/80 dark:bg-gray-800/80 hover:bg-emerald-50 dark:hover:bg-emerald-950/30 border-gray-200 dark:border-gray-700'
                      }`}
                    >
                      <div className="flex items-center gap-1.5 mb-1">
                        <Rocket size={16} className="text-emerald-500 dark:text-emerald-400" />
                        <span className="text-xs font-bold text-emerald-700 dark:text-emerald-300">Anti-Packet-Loss</span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                        KCP ARQ. Aggressive retransmission for bad networks.
                      </p>
                    </button>
                  </div>
                </div>

                {/* Transport & Security Selects */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start pt-1">
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                        <span className="w-1.5 h-1.5 rounded-full bg-blue-500"></span>
                        Transport Protocol
                      </label>
                      <span className="text-[11px] font-mono uppercase text-gray-400 dark:text-gray-500">
                        {formData.transport_type}
                      </span>
                    </div>
                    <select
                      value={formData.transport_type}
                      onChange={(e) => setFormData({...formData, transport_type: e.target.value})}
                      className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all font-medium"
                    >
                      <option value="tcp">TCP (Standard)</option>
                      <option value="ws">WebSocket (WS)</option>
                      <option value="mws">Multiplex WS (MWS)</option>
                      <option value="quic">QUIC (HTTP/3 UDP, 0-RTT)</option>
                      <option value="grpc">gRPC (Multiplexed Stealth)</option>
                      <option value="kcp">KCP (Anti-Packet-Loss ARQ)</option>
                      <option value="ssh">SSH (Encrypted Subsystem)</option>
                    </select>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                      {formData.transport_type === 'quic' && 'Fast 0-RTT UDP handshake. Resilient to packet loss.'}
                      {formData.transport_type === 'grpc' && 'Multiplexed HTTP/2. Mimics legitimate enterprise API traffic.'}
                      {formData.transport_type === 'kcp' && 'Aggressive ARQ UDP. Ideal for high packet-loss links.'}
                      {formData.transport_type === 'ws' && 'Standard WebSocket. Compatible with CDN proxies.'}
                      {formData.transport_type === 'mws' && 'Multiplexed WebSocket. Bundles multiple TCP streams.'}
                      {formData.transport_type === 'tcp' && 'Direct raw TCP socket tunnel.'}
                      {formData.transport_type === 'ssh' && 'Native SSH subsystem encrypted protocol.'}
                    </p>
                  </div>

                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                        <span className="w-1.5 h-1.5 rounded-full bg-indigo-500"></span>
                        Security / Encryption
                      </label>
                      <span className="text-[11px] font-mono uppercase text-gray-400 dark:text-gray-500">
                        {formData.security_type}
                      </span>
                    </div>
                    <select
                      value={formData.security_type}
                      onChange={(e) => setFormData({...formData, security_type: e.target.value})}
                      className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all font-medium"
                    >
                      <option value="none">None (Plaintext / Low Overhead)</option>
                      <option value="tls">TLS (Standard Encryption)</option>
                      <option value="utls">uTLS (Browser Spoofing Anti-DPI)</option>
                    </select>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                      {formData.security_type === 'none' && 'No TLS wrapper. Lowest CPU overhead.'}
                      {formData.security_type === 'tls' && 'Standard TLS 1.3 handshake encryption.'}
                      {formData.security_type === 'utls' && 'Camouflages ClientHello to impersonate real browsers.'}
                    </p>
                  </div>
                </div>

                {/* uTLS Fingerprint Card */}
                {formData.security_type === 'utls' && (
                  <div className="p-3.5 rounded-xl bg-indigo-50/60 dark:bg-indigo-950/20 border border-indigo-200/80 dark:border-indigo-900/50 space-y-2">
                    <div className="flex items-center justify-between">
                      <label className="text-xs font-bold text-indigo-900 dark:text-indigo-200 flex items-center gap-1.5">
                        <Fingerprint size={15} className="text-indigo-600 dark:text-indigo-400" />
                        uTLS Client Fingerprint
                      </label>
                      <span className="text-[10px] px-2 py-0.5 rounded-md font-medium bg-indigo-200/60 text-indigo-800 dark:bg-indigo-900/60 dark:text-indigo-300">
                        Anti-DPI Spoofing
                      </span>
                    </div>
                    <p className="text-[11px] text-indigo-700/80 dark:text-indigo-300/80">
                      Replicates exact TLS cipher suites, extensions, and curves of legitimate web browsers:
                    </p>
                    <select
                      value={formData.utls_fingerprint || 'chrome'}
                      onChange={(e) => setFormData({...formData, utls_fingerprint: e.target.value})}
                      className="w-full px-3 py-2 text-sm sm:text-xs rounded-lg border border-indigo-300 dark:border-indigo-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-indigo-500/20 font-medium"
                    >
                      <option value="chrome">Google Chrome (Recommended - Highest Compatibility)</option>
                      <option value="firefox">Mozilla Firefox</option>
                      <option value="ios">Apple iOS Safari</option>
                      <option value="android">Android Chrome</option>
                      <option value="edge">Microsoft Edge</option>
                      <option value="randomized">Randomized (Rotates browser signature per connection)</option>
                    </select>
                  </div>
                )}

                {/* Failover IPs & Selector Strategy */}
                <div className="p-3.5 rounded-xl bg-gray-50/80 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 space-y-3">
                  <div className="flex items-center justify-between">
                    <div>
                      <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1.5">
                        <Globe size={15} className="text-blue-600 dark:text-blue-400" />
                        Failover & Additional Foreign Endpoints
                      </label>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400">
                        Add secondary foreign IPs for automatic high-availability failover or load balancing (one per line)
                      </p>
                    </div>
                    {formData.failover_ips && formData.failover_ips.trim().length > 0 && (
                      <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 dark:bg-blue-900/60 dark:text-blue-300 shrink-0">
                        Multi-Node Active
                      </span>
                    )}
                  </div>
                  <textarea
                    value={formData.failover_ips}
                    onChange={(e) => setFormData({...formData, failover_ips: e.target.value})}
                    className="w-full px-3 py-2 text-sm sm:text-xs font-mono rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500/20"
                    placeholder={"1.2.3.4\n5.6.7.8"}
                    rows={2}
                  />

                  {formData.failover_ips && formData.failover_ips.trim().length > 0 && (
                    <div className="pt-2 border-t border-gray-200/80 dark:border-gray-700/80 space-y-1.5">
                      <div className="flex items-center justify-between">
                        <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1">
                          <Scale size={14} className="text-blue-500" />
                          Load Balancing / Failover Strategy
                        </label>
                        <span className="text-[11px] font-mono text-gray-400">
                          {formData.selector_strategy || 'fifo'}
                        </span>
                      </div>
                      <select
                        value={formData.selector_strategy || 'fifo'}
                        onChange={(e) => setFormData({...formData, selector_strategy: e.target.value})}
                        className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500/20 font-medium"
                      >
                        <option value="fifo">FIFO Failover (Primary first, fallback to backup IPs)</option>
                        <option value="round">Round-Robin (Distribute requests evenly across all IPs)</option>
                        <option value="parallel">Parallel Race (Connect all concurrently, use fastest ping)</option>
                        <option value="rand">Random (Random distribution across nodes)</option>
                      </select>
                    </div>
                  )}
                </div>

                {/* Mode Switches */}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
                  <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
                    <div className="pr-2">
                      <div className="flex items-center gap-1.5">
                        <ArrowLeftRight size={15} className="text-blue-600 dark:text-blue-400" />
                        <span className="text-xs font-bold text-gray-900 dark:text-white">Reverse Mode</span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                        Iran connects to foreign
                      </p>
                    </div>
                    <input
                      type="checkbox"
                      className="sr-only peer"
                      checked={formData.is_reverse}
                      onChange={(e) => setFormData({...formData, is_reverse: e.target.checked})}
                    />
                    <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 shrink-0 relative"></div>
                  </label>

                  <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
                    <div className="pr-2">
                      <div className="flex items-center gap-1.5">
                        <Globe size={15} className="text-sky-600 dark:text-sky-400" />
                        <span className="text-xs font-bold text-gray-900 dark:text-white">CDN Mode</span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                        Cloudflare / WS proxy
                      </p>
                    </div>
                    <input
                      type="checkbox"
                      className="sr-only peer"
                      checked={formData.cdn_mode}
                      onChange={(e) => {
                        const isChecked = e.target.checked;
                        const updates: any = { cdn_mode: isChecked };
                        if (isChecked && ['tcp', 'udp', 'tcp+udp'].includes(formData.transport_type)) {
                          updates.transport_type = 'ws';
                          showToast('info', 'Transport Switched', 'CDN mode requires WebSocket transport. Auto-switched to WS.')
                        }
                        setFormData({...formData, ...updates});
                      }}
                    />
                    <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 shrink-0 relative"></div>
                  </label>

                  <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
                    <div className="pr-2">
                      <div className="flex items-center gap-1.5">
                        <Gamepad2 size={15} className="text-indigo-600 dark:text-indigo-400" />
                        <span className="text-xs font-bold text-gray-900 dark:text-white">Gaming (Mux)</span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                        Yamux lower ping
                      </p>
                    </div>
                    <input
                      type="checkbox"
                      className="sr-only peer"
                      checked={formData.gaming_mode}
                      onChange={(e) => setFormData({...formData, gaming_mode: e.target.checked})}
                    />
                    <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 shrink-0 relative"></div>
                  </label>
                </div>

                {/* Security, Limits & KeepAlive Tuning */}
                <div className="pt-3 border-t border-gray-200/80 dark:border-gray-700/80 space-y-3">
                  <div className="flex items-center justify-between">
                    <h5 className="text-xs font-bold uppercase tracking-wider text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <ShieldCheck size={15} className="text-gray-700 dark:text-gray-300" />
                      Security & Network Guard
                    </h5>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {/* KeepAlive Interval Card */}
                    <div className="p-3 rounded-xl bg-gray-50/80 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700">
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1">
                          <Activity size={14} className="text-rose-500 dark:text-rose-400" />
                          KeepAlive (Anti-Drop)
                        </label>
                        <span className="text-xs font-mono font-bold text-blue-600 dark:text-blue-400">
                          {formData.keepalive_interval || 15}s
                        </span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 mb-2">
                        Probe interval to keep stateful NAT firewalls alive
                      </p>
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          min="5"
                          max="120"
                          value={formData.keepalive_interval || 15}
                          onChange={(e) => setFormData({...formData, keepalive_interval: parseInt(e.target.value) || 15})}
                          className="w-20 px-2.5 py-1.5 text-xs font-mono font-semibold rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                        />
                        <div className="flex items-center gap-1">
                          {[10, 15, 30].map(sec => (
                            <button
                              key={sec}
                              type="button"
                              onClick={() => setFormData({...formData, keepalive_interval: sec})}
                              className={`text-[10px] font-mono px-2 py-1 rounded-md border transition-all cursor-pointer ${
                                (formData.keepalive_interval || 15) === sec
                                  ? 'bg-blue-600 text-white border-blue-600 font-bold'
                                  : 'bg-white dark:bg-gray-700 text-gray-600 dark:text-gray-300 border-gray-200 dark:border-gray-600 hover:bg-gray-100'
                              }`}
                            >
                              {sec}s
                            </button>
                          ))}
                        </div>
                      </div>
                    </div>

                    {/* Stealth Domain Card */}
                    <div className="p-3 rounded-xl bg-gray-50/80 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700">
                      <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1 mb-1">
                        <EyeOff size={14} className="text-purple-600 dark:text-purple-400" />
                        Stealth SNI (TLS Spoof)
                      </label>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 mb-2">
                        Mask traffic as legitimate website
                      </p>
                      <input
                        type="text"
                        value={formData.stealth_domain}
                        onChange={(e) => setFormData({...formData, stealth_domain: e.target.value})}
                        className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                        placeholder="e.g. www.google.com"
                      />
                    </div>
                  </div>

                  {/* IP Whitelist & Rate Limit Toggles */}
                  <div className="space-y-2">
                    <div className="p-3 rounded-xl bg-gray-50/80 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700">
                      <div className="flex items-center justify-between">
                        <div>
                          <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1.5">
                            <Shield size={14} className="text-blue-600 dark:text-blue-400" />
                            IP Whitelist (ACL)
                          </label>
                          <p className="text-[11px] text-gray-500 dark:text-gray-400">
                            Restrict tunnel access to specific IP ranges
                          </p>
                        </div>
                        <label className="relative inline-flex items-center cursor-pointer">
                          <input
                            type="checkbox"
                            className="sr-only peer"
                            checked={formData.allowed_ips_enabled}
                            onChange={(e) => setFormData({...formData, allowed_ips_enabled: e.target.checked})}
                          />
                          <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 shrink-0 relative"></div>
                        </label>
                      </div>
                      {formData.allowed_ips_enabled && (
                        <div className="mt-2.5 pt-2 border-t border-gray-200/80 dark:border-gray-700/80">
                          <textarea
                            value={formData.allowed_ips}
                            onChange={(e) => setFormData({...formData, allowed_ips: e.target.value})}
                            className="w-full px-2.5 py-1.5 text-xs font-mono rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                            placeholder={"192.168.1.1\n10.0.0.0/24"}
                            rows={2}
                          />
                        </div>
                      )}
                    </div>

                    <div className="p-3 rounded-xl bg-gray-50/80 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700">
                      <div className="flex items-center justify-between">
                        <div>
                          <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1.5">
                            <Gauge size={14} className="text-amber-600 dark:text-amber-400" />
                            Bandwidth Rate Limit
                          </label>
                          <p className="text-[11px] text-gray-500 dark:text-gray-400">
                            Throttle client speed per connection
                          </p>
                        </div>
                        <label className="relative inline-flex items-center cursor-pointer">
                          <input
                            type="checkbox"
                            className="sr-only peer"
                            checked={formData.rate_limit_enabled}
                            onChange={(e) => setFormData({...formData, rate_limit_enabled: e.target.checked})}
                          />
                          <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 shrink-0 relative"></div>
                        </label>
                      </div>
                      {formData.rate_limit_enabled && (
                        <div className="mt-2.5 pt-2 border-t border-gray-200/80 dark:border-gray-700/80 flex items-center gap-2">
                          <input
                            type="number"
                            value={formData.rate_limit_mbps}
                            onChange={(e) => setFormData({...formData, rate_limit_mbps: e.target.value})}
                            className="w-24 px-2.5 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                            placeholder="5"
                            min="0.1"
                            step="0.1"
                          />
                          <span className="text-xs font-medium text-gray-600 dark:text-gray-400">Mbps</span>
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                {/* CDN Mode Extra Options */}
                {formData.cdn_mode && (
                  <div className="p-3.5 rounded-xl bg-blue-50/60 dark:bg-blue-950/20 border border-blue-200/80 dark:border-blue-900/50 space-y-2.5">
                    <label className="text-xs font-bold text-blue-900 dark:text-blue-200 flex items-center gap-1.5">
                      <Globe size={15} className="text-blue-600 dark:text-blue-400" />
                      CDN / WebSocket Configuration
                    </label>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                      <div>
                        <label className="block text-[11px] font-medium text-gray-700 dark:text-gray-300 mb-1">Custom Host</label>
                        <input type="text" value={formData.custom_host} onChange={(e) => setFormData({...formData, custom_host: e.target.value})} className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white" placeholder="e.g. speedtest.net" />
                      </div>
                      <div>
                        <label className="block text-[11px] font-medium text-gray-700 dark:text-gray-300 mb-1">Custom SNI</label>
                        <input type="text" value={formData.custom_sni} onChange={(e) => setFormData({...formData, custom_sni: e.target.value})} className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white" placeholder="e.g. speedtest.net" />
                      </div>
                      <div>
                        <label className="block text-[11px] font-medium text-gray-700 dark:text-gray-300 mb-1">WS Path</label>
                        <input type="text" value={formData.ws_path} onChange={(e) => setFormData({...formData, ws_path: e.target.value})} className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white" placeholder="/graphql" />
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Pre-flight Diagnostic Box */}
          {isTestingConfig && (
            <div className="p-4 rounded-xl bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800 flex items-center gap-3">
              <Loader2 className="animate-spin text-blue-600 dark:text-blue-400 shrink-0" size={20} />
              <span className="text-sm font-medium text-blue-800 dark:text-blue-200">
                Testing node reachability, network ping, and protocol specifications...
              </span>
            </div>
          )}

          {testResult && !isTestingConfig && (
            <div className={`p-4 rounded-xl border transition-all ${
              testResult.valid 
                ? 'bg-emerald-50/70 dark:bg-emerald-950/30 border-emerald-200 dark:border-emerald-800' 
                : 'bg-amber-50/70 dark:bg-amber-950/30 border-amber-200 dark:border-amber-800'
            }`}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  {testResult.valid ? (
                    <CheckCircle2 className="text-emerald-600 dark:text-emerald-400 shrink-0" size={20} />
                  ) : (
                    <AlertTriangle className="text-amber-600 dark:text-amber-400 shrink-0" size={20} />
                  )}
                  <span className="text-sm font-semibold text-gray-900 dark:text-white">
                    {testResult.summary}
                  </span>
                </div>
                {testResult.latency_ms && (
                  <LatencyBadge latency={testResult.latency_ms} status="active" />
                )}
              </div>

              <div className="space-y-2 mt-2">
                {testResult.checks?.map((check: any, idx: number) => (
                  <div key={idx} className="flex items-start gap-2 text-xs">
                    {check.status === 'passed' ? (
                      <CheckCircle2 size={14} className="text-emerald-500 shrink-0 mt-0.5" />
                    ) : check.status === 'warning' ? (
                      <AlertTriangle size={14} className="text-amber-500 shrink-0 mt-0.5" />
                    ) : (
                      <XCircle size={14} className="text-rose-500 shrink-0 mt-0.5" />
                    )}
                    <div className="flex-1">
                      <span className="font-semibold text-gray-800 dark:text-gray-200">{check.title}: </span>
                      <span className="text-gray-600 dark:text-gray-300">{check.detail}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          </div>

          {/* Sticky Footer */}
          <div className="shrink-0 px-4 sm:px-6 py-3.5 border-t border-gray-100 dark:border-gray-700/80 bg-gray-50/90 dark:bg-gray-800/95 backdrop-blur-xs flex items-center justify-between gap-2.5 sm:gap-3">
            <button
              type="button"
              onClick={handleTestConfig}
              disabled={isTestingConfig}
              className="inline-flex items-center gap-2 px-3.5 sm:px-4 py-2 sm:py-2.5 bg-indigo-50 hover:bg-indigo-100 dark:bg-indigo-950/50 dark:hover:bg-indigo-900/60 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800/80 rounded-xl text-xs sm:text-sm font-semibold transition-all shadow-xs cursor-pointer min-h-[44px] disabled:opacity-50"
            >
              {isTestingConfig ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Zap size={16} />
              )}
              <span className="hidden xs:inline sm:inline">{isTestingConfig ? 'Testing...' : 'Test Connection'}</span>
              <span className="inline xs:hidden sm:hidden">{isTestingConfig ? '...' : 'Test'}</span>
            </button>

            <div className="flex items-center gap-2 sm:gap-3">
              <button
                type="button"
                onClick={onClose}
                className="px-3.5 sm:px-4 py-2 sm:py-2.5 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 rounded-xl hover:bg-gray-100 dark:hover:bg-gray-600 border border-gray-200 dark:border-gray-600 text-xs sm:text-sm font-semibold transition-all shadow-xs cursor-pointer min-h-[44px]"
              >
                {t.tunnels.cancel}
              </button>
              <button
                type="submit"
                disabled={isTestingConfig}
                className="px-4 sm:px-5 py-2 sm:py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 text-white rounded-xl text-xs sm:text-sm font-semibold transition-all shadow-md shadow-blue-500/20 disabled:opacity-50 cursor-pointer flex items-center gap-1.5 min-h-[44px]"
              >
                {t.tunnels.createTunnel}
              </button>
            </div>
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

const BACKHAUL_TRANSPORTS: BackhaulTransport[] = ['tcp', 'udp', 'ws', 'wsmux', 'tcpmux', 'wss', 'wssmux']

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
    <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-br from-emerald-50/60 via-gray-50 to-teal-50/40 dark:from-emerald-950/20 dark:via-gray-800/60 dark:to-teal-950/20 border border-emerald-200/70 dark:border-emerald-900/40 space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 pb-3 border-b border-gray-200/70 dark:border-gray-700/70">
        <div className="flex items-center gap-2">
          <span className="p-1.5 rounded-lg bg-emerald-100 dark:bg-emerald-900/60 text-emerald-600 dark:text-emerald-300">
            <RadioTower size={16} />
          </span>
          <div>
            <h4 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">
              Backhaul Settings
            </h4>
            <p className="text-xs text-gray-500 dark:text-gray-400">
              High-Throughput Multiplexed Tunneling Core (v0.7.2)
            </p>
          </div>
        </div>
        <span className="self-start sm:self-auto text-xs px-2.5 py-0.5 rounded-full font-mono font-medium bg-emerald-100/80 text-emerald-700 dark:bg-emerald-900/60 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800">
          Backhaul Core
        </span>
      </div>

      {/* ⚡ Ultra-Low Latency Gaming Mode Card */}
      <label className="p-3.5 rounded-xl border border-indigo-200 dark:border-indigo-800/60 bg-gradient-to-r from-indigo-50/90 via-purple-50/40 to-pink-50/30 dark:from-indigo-950/40 dark:via-purple-950/30 dark:to-pink-950/20 flex items-center justify-between cursor-pointer hover:border-indigo-300 dark:hover:border-indigo-700 transition-all shadow-sm">
        <div className="pr-3">
          <div className="flex items-center gap-2">
            <span className="p-1 rounded-md bg-indigo-100 dark:bg-indigo-900/70 text-indigo-600 dark:text-indigo-400">
              <Gamepad2 size={16} />
            </span>
            <span className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1.5">
              ⚡ Ultra-Low Latency Gaming Mode
              <span className="text-[10px] uppercase font-mono font-bold tracking-wider px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700 dark:bg-indigo-900/80 dark:text-indigo-300">
                Anti-Jitter
              </span>
            </span>
          </div>
          <p className="text-[11px] text-gray-600 dark:text-gray-300 mt-1 leading-normal">
            Optimizes real-time streams to eliminate jitter, packet loss, and ping spikes for competitive gaming.
          </p>
        </div>
        <input
          type="checkbox"
          className="sr-only peer"
          checked={Boolean(state.gaming_mode)}
          onChange={() => onChange({ gaming_mode: !state.gaming_mode })}
        />
        <div className="w-10 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-indigo-600 shrink-0 relative"></div>
      </label>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
              <Server size={14} className="text-emerald-500" />
              Control Port
            </label>
            <button
              type="button"
              onClick={() => onChange({ control_port: generateRandomControlPort() })}
              className="flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[11px] font-medium bg-emerald-50 dark:bg-emerald-950/40 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-100 dark:hover:bg-emerald-900/60 border border-emerald-200/60 dark:border-emerald-800/40 transition-all cursor-pointer shadow-xs"
              title="Generate Random Control Port"
            >
              <Dices size={12} />
              <span>Random</span>
            </button>
          </div>
          <input
            type="number"
            value={state.control_port}
            onChange={(e) => onChange({ control_port: e.target.value })}
            className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 transition-all font-mono"
            placeholder="3080"
            min={1}
            max={65535}
          />
          <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
            Port where the foreign node connects back to the Iran server.
          </p>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
              <Radio size={14} className="text-teal-500" />
              Forwarded Ports & Ranges
            </label>
            <span className="text-[11px] text-gray-400">Public & Target</span>
          </div>
          <input
            type="text"
            value={state.public_port}
            onChange={(e) => {
              onChange({ public_port: e.target.value, target_port: e.target.value })
            }}
            className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 transition-all font-mono"
            placeholder="8080,8081 or 27000-27050"
          />
          <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
            Single ports (8080), comma list (8080,8081), or ranges (27000-27050).
          </p>
        </div>
      </div>

      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
            <Key size={14} className="text-indigo-500" />
            Authentication Token
          </label>
          <span className="text-[11px] text-gray-400">Optional</span>
        </div>
        <input
          type="text"
          value={state.token}
          onChange={(e) => onChange({ token: e.target.value })}
          className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all font-mono"
          placeholder="Leave empty for auto-generation"
        />
        <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
          Mutual auth token (auto-generated if empty).
        </p>
      </div>

      {acceptUdpVisible && (
        <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
          <div className="pr-2">
            <div className="flex items-center gap-1.5">
              <Zap size={15} className="text-emerald-600 dark:text-emerald-400" />
              <span className="text-xs font-bold text-gray-900 dark:text-white">Allow UDP over TCP (Turbo)</span>
            </div>
            <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
              Encapsulate UDP packets inside TCP streams for networks with strict UDP filtering
            </p>
          </div>
          <input
            type="checkbox"
            className="sr-only peer"
            checked={state.accept_udp}
            onChange={() => onChange({ accept_udp: !state.accept_udp })}
          />
          <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-emerald-600 shrink-0 relative"></div>
        </label>
      )}

      <div className="pt-1 flex items-center justify-end">
        <button
          type="button"
          onClick={onOpenAdvanced}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-emerald-700 dark:text-emerald-300 bg-emerald-100/70 hover:bg-emerald-200/70 dark:bg-emerald-950/40 dark:hover:bg-emerald-900/60 rounded-lg border border-emerald-300 dark:border-emerald-800 transition-colors"
        >
          <Settings2 size={13} />
          Advanced Engine Parameters
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

  const updateBoth = (key: string, value: string | boolean) => {
    onChange({
      ...state,
      server: {
        ...state.server,
        [key]: value,
      },
      client: {
        ...state.client,
        [key]: value,
      },
    })
  }

  return (
    <div className="fixed inset-0 z-[100] flex">
      <div className="flex-1 bg-black/50 backdrop-blur-sm transition-opacity" onClick={onClose} />
      <div className="w-full max-w-2xl h-full bg-white dark:bg-gray-900 shadow-2xl overflow-y-auto p-6 border-l border-gray-200 dark:border-gray-800">
        <div className="flex justify-between items-center pb-4 mb-6 border-b border-gray-200 dark:border-gray-800">
          <div className="flex items-center gap-2.5">
            <span className="p-2 rounded-xl bg-emerald-100 dark:bg-emerald-950/60 text-emerald-600 dark:text-emerald-400">
              <Settings2 size={20} />
            </span>
            <div>
              <h3 className="text-lg font-bold text-gray-900 dark:text-white">Backhaul Engine Tuning</h3>
              <p className="text-xs text-gray-500 dark:text-gray-400">Upstream Musixal/Backhaul v0.7.2 High-Performance Core</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        <div className="space-y-6">
          {/* Card 1: ⚡ Low-Latency & Gaming Tuning */}
          <div className="p-4 rounded-xl border border-indigo-200/80 dark:border-indigo-900/50 bg-indigo-50/30 dark:bg-indigo-950/20 space-y-4">
            <div className="flex items-center gap-2 text-indigo-700 dark:text-indigo-300 font-bold text-xs uppercase tracking-wider">
              <Zap size={15} />
              <span>Latency & WAN Transmission (Gaming & Real-time)</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
              <label className="p-2.5 rounded-lg bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 flex items-center justify-between cursor-pointer">
                <div>
                  <span className="text-xs font-semibold text-gray-800 dark:text-gray-200 block">Server TCP Nodelay</span>
                  <span className="text-[10px] text-gray-500 dark:text-gray-400">Disables Nagle buffering</span>
                </div>
                <input
                  type="checkbox"
                  className="rounded text-indigo-600 focus:ring-indigo-500"
                  checked={state.server.nodelay}
                  onChange={(e) => updateServer('nodelay', e.target.checked)}
                />
              </label>

              <label className="p-2.5 rounded-lg bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 flex items-center justify-between cursor-pointer">
                <div>
                  <span className="text-xs font-semibold text-gray-800 dark:text-gray-200 block">Client TCP Nodelay</span>
                  <span className="text-[10px] text-gray-500 dark:text-gray-400">Instant client tick dispatch</span>
                </div>
                <input
                  type="checkbox"
                  className="rounded text-indigo-600 focus:ring-indigo-500"
                  checked={state.client.nodelay}
                  onChange={(e) => updateClient('nodelay', e.target.checked)}
                />
              </label>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  MSS Clamping (MTU Optimization)
                </label>
                <input
                  type="number"
                  value={state.server.mss}
                  onChange={(e) => updateBoth('mss', e.target.value)}
                  placeholder="e.g. 1380 (prevents fragmentation)"
                  className="w-full px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  min={1000}
                  max={1500}
                />
                <span className="text-[10px] text-gray-400">Recommended 1360-1400 for WAN gaming</span>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Heartbeat & Keepalive (seconds)
                </label>
                <div className="grid grid-cols-2 gap-2">
                  <input
                    type="number"
                    value={state.server.heartbeat}
                    onChange={(e) => updateBoth('heartbeat', e.target.value)}
                    placeholder="Heartbeat (12)"
                    className="w-full px-2.5 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                    min={1}
                    max={25}
                  />
                  <input
                    type="number"
                    value={state.server.keepalive_period}
                    onChange={(e) => updateBoth('keepalive_period', e.target.value)}
                    placeholder="Keepalive (12)"
                    className="w-full px-2.5 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                    min={1}
                    max={25}
                  />
                </div>
                <span className="text-[10px] text-gray-400">Fast link loss detection (≤ 25s)</span>
              </div>

              <label className="p-2.5 rounded-lg bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 flex items-center justify-between cursor-pointer col-span-1 sm:col-span-2">
                <div>
                  <span className="text-xs font-semibold text-gray-800 dark:text-gray-200 block">Skip Kernel Sysctl Optimizations (Docker Mode)</span>
                  <span className="text-[10px] text-gray-500 dark:text-gray-400">Bypasses sysctl error logs in unprivileged Docker containers</span>
                </div>
                <input
                  type="checkbox"
                  className="rounded text-indigo-600 focus:ring-indigo-500"
                  checked={Boolean(state.server.skip_optz || state.client.skip_optz)}
                  onChange={(e) => updateBoth('skip_optz', e.target.checked)}
                />
              </label>
            </div>
          </div>

          {/* Card 2: 🚀 Buffer & Socket Capacity */}
          <div className="p-4 rounded-xl border border-teal-200/80 dark:border-teal-900/50 bg-teal-50/30 dark:bg-teal-950/20 space-y-4">
            <div className="flex items-center gap-2 text-teal-700 dark:text-teal-300 font-bold text-xs uppercase tracking-wider">
              <Activity size={15} />
              <span>Socket & Buffer Capacity</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Channel Queue Size
                </label>
                <input
                  type="number"
                  value={state.server.channel_size}
                  onChange={(e) => updateBoth('channel_size', e.target.value)}
                  placeholder="2048 (default) or 8192 (gaming)"
                  className="w-full px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  min={512}
                />
                <span className="text-[10px] text-gray-400">Queue buffer for high-frequency game bursts</span>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Client Connection Pool
                </label>
                <input
                  type="number"
                  value={state.client.connection_pool}
                  onChange={(e) => updateClient('connection_pool', e.target.value)}
                  className="w-full px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  min={1}
                />
                <span className="text-[10px] text-gray-400">Parallel multiplex connections (default 8)</span>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Socket RCV Buffer (so_rcvbuf bytes)
                </label>
                <input
                  type="number"
                  value={state.server.so_rcvbuf}
                  onChange={(e) => updateBoth('so_rcvbuf', e.target.value)}
                  placeholder="e.g. 2097152 (2MB)"
                  className="w-full px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Socket SND Buffer (so_sndbuf bytes)
                </label>
                <input
                  type="number"
                  value={state.server.so_sndbuf}
                  onChange={(e) => updateBoth('so_sndbuf', e.target.value)}
                  placeholder="e.g. 2097152 (2MB)"
                  className="w-full px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                />
              </div>

              <label className="p-2.5 rounded-lg bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 flex items-center justify-between cursor-pointer col-span-1 sm:col-span-2">
                <div>
                  <span className="text-xs font-semibold text-gray-800 dark:text-gray-200 block">Aggressive Pool</span>
                  <span className="text-[10px] text-gray-500 dark:text-gray-400">Maintain pooled connections in hot standby state</span>
                </div>
                <input
                  type="checkbox"
                  className="rounded text-teal-600 focus:ring-teal-500"
                  checked={state.client.aggressive_pool}
                  onChange={(e) => updateClient('aggressive_pool', e.target.checked)}
                />
              </label>
            </div>
          </div>

          {/* Card 3: 🔀 SMUX Multiplexing Engine */}
          <div className="p-4 rounded-xl border border-cyan-200/80 dark:border-cyan-900/50 bg-cyan-50/30 dark:bg-cyan-950/20 space-y-4">
            <div className="flex items-center gap-2 text-cyan-700 dark:text-cyan-300 font-bold text-xs uppercase tracking-wider">
              <Network size={15} />
              <span>SMUX Multiplexing Engine</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Yamux Protocol Version
                </label>
                <select
                  value={state.server.mux_version || '1'}
                  onChange={(e) => updateBoth('mux_version', e.target.value)}
                  className="w-full px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                >
                  <option value="1">Version 1 (Standard)</option>
                  <option value="2">Version 2 (Modern)</option>
                </select>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Mux Frame Size (bytes)
                </label>
                <input
                  type="number"
                  value={state.server.mux_framesize}
                  onChange={(e) => updateBoth('mux_framesize', e.target.value)}
                  placeholder="32768 (default) or 4096 (gaming)"
                  className="w-full px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                />
                <span className="text-[10px] text-gray-400">4096 bytes dispatches game ticks instantly</span>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Mux Concurrency
                </label>
                <input
                  type="number"
                  value={state.server.mux_con}
                  onChange={(e) => updateServer('mux_con', e.target.value)}
                  className="w-full px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  min={1}
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Stream Buffer (bytes)
                </label>
                <input
                  type="number"
                  value={state.server.mux_streambuffer}
                  onChange={(e) => updateBoth('mux_streambuffer', e.target.value)}
                  placeholder="e.g. 131072 (128KB)"
                  className="w-full px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                />
              </div>
            </div>
          </div>

          {/* Card 4: 🛡️ Security, Proxy & Monitoring */}
          <div className="p-4 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/40 space-y-4">
            <div className="flex items-center gap-2 text-gray-700 dark:text-gray-300 font-bold text-xs uppercase tracking-wider">
              <ShieldCheck size={15} />
              <span>Security, Proxy & Port Ranges</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
              <label className="p-2.5 rounded-lg bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 flex items-center justify-between cursor-pointer col-span-1 sm:col-span-2">
                <div>
                  <span className="text-xs font-semibold text-gray-800 dark:text-gray-200 block">HAProxy Proxy Protocol</span>
                  <span className="text-[10px] text-gray-500 dark:text-gray-400">Preserves original client IP addresses across reverse proxies</span>
                </div>
                <input
                  type="checkbox"
                  className="rounded text-blue-600 focus:ring-blue-500"
                  checked={state.server.proxy_protocol}
                  onChange={(e) => updateServer('proxy_protocol', e.target.checked)}
                />
              </label>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Log Level</label>
                <select
                  value={state.server.log_level}
                  onChange={(e) => updateBoth('log_level', e.target.value)}
                  className="w-full px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                >
                  <option value="info">Info</option>
                  <option value="warn">Warn</option>
                  <option value="error">Error</option>
                  <option value="debug">Debug</option>
                  <option value="trace">Trace</option>
                </select>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Web Monitor Port</label>
                <input
                  type="number"
                  value={state.server.web_port}
                  onChange={(e) => updateServer('web_port', e.target.value)}
                  className="w-full px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  placeholder="0 (disabled)"
                  min={0}
                />
              </div>

              <div className="col-span-1 sm:col-span-2">
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Custom Port Mappings & Ranges
                </label>
                <textarea
                  value={state.customPorts}
                  onChange={(e) => onChange({ ...state, customPorts: e.target.value })}
                  className="w-full min-h-[90px] px-3 py-2 text-xs font-mono border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  placeholder={`One entry per line. Examples:\n443\n443=127.0.0.1:8080\n27000-27050=127.0.0.1:27000-27050`}
                />
                <span className="text-[10px] text-gray-400">Leave empty to use single port from main form</span>
              </div>
            </div>
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
  const isPureUdp = transport === 'udp'
  const isUdpOverTcp = !isPureUdp && (base.accept_udp || transport === 'tcp' && base.accept_udp || transport === 'tcpmux' && base.accept_udp)
  const normalizedTransport = transport
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
        ? (publicPort + 10000 > 65535 ? publicPort - 10000 : publicPort + 10000)
        : (!Number.isNaN(targetPort) && targetPort > 0 ? targetPort : 3080))
  
  // Parse comma-separated ports or ranges from public_port without truncating ranges
  const parsePortsFromString = (portStr: string): string[] => {
    if (!portStr || typeof portStr !== 'string') {
      return []
    }
    const parsed = portStr
      .split(',')
      .map(p => p.trim())
      .filter(p => {
        if (!p) return false
        if (p.includes('-')) {
          const parts = p.split('-').map(x => parseInt(x.trim(), 10))
          return parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1]) && parts[0] > 0 && parts[1] <= 65535 && parts[0] <= parts[1]
        }
        const num = parseInt(p, 10)
        return !isNaN(num) && num > 0 && num <= 65535
      })
    return parsed
  }
  
  const publicPortStr = String(base.public_port || '')
  const publicPorts = parsePortsFromString(publicPortStr)
  const firstPortRaw = publicPorts.length > 0 ? publicPorts[0] : ''
  const firstPortNum = firstPortRaw.includes('-')
    ? parseInt(firstPortRaw.split('-')[0], 10)
    : parseInt(firstPortRaw, 10)
  const effectivePublicPort = !Number.isNaN(firstPortNum) && firstPortNum > 0
    ? firstPortNum
    : (!Number.isNaN(publicPort) && publicPort > 0 ? publicPort : 8080)
  const effectiveTargetPort = !Number.isNaN(targetPort) && targetPort > 0 ? targetPort : effectivePublicPort

  const remoteAddr = base.remote_addr.trim() || `${panelHost}:${effectiveControlPort}`
  const listenedPort = listenIp !== '0.0.0.0' ? `${listenIp}:${effectivePublicPort}` : `${effectivePublicPort}`
  const defaultPortEntry = `${listenedPort}=${targetHost}:${effectiveTargetPort}`

  let ports: string[] = []
  const hasCustomPorts = advanced.customPorts && advanced.customPorts.trim().length > 0
  
  if (hasCustomPorts) {
    ports = advanced.customPorts
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
  } else if (publicPorts.length > 0) {
    ports = publicPorts.map(p => {
      const listenedPort = listenIp !== '0.0.0.0' ? `${listenIp}:${p}` : `${p}`
      return `${listenedPort}=${targetHost}:${p}`
    })
  }
  
  if (ports.length === 0) {
    ports.push(defaultPortEntry)
  }

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

  if (isUdpOverTcp) {
    serverOptions.accept_udp = true
    clientOptions.accept_udp = true
  }
  if (!serverOptions.keepalive_period || Number(serverOptions.keepalive_period) > 25) {
    serverOptions.keepalive_period = base.gaming_mode ? 12 : 20
  }
  if (!serverOptions.heartbeat || Number(serverOptions.heartbeat) > 25) {
    serverOptions.heartbeat = base.gaming_mode ? 12 : 20
  }
  if (!clientOptions.keepalive_period || Number(clientOptions.keepalive_period) > 25) {
    clientOptions.keepalive_period = base.gaming_mode ? 12 : 20
  }
  if (!clientOptions.heartbeat || Number(clientOptions.heartbeat) > 25) {
    clientOptions.heartbeat = base.gaming_mode ? 12 : 20
  }

  const spec: Record<string, any> = {
    transport: normalizedTransport,
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
  if (isUdpOverTcp) {
    spec.accept_udp = true
  }
  if (base.gaming_mode) {
    spec.gaming_mode = true
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
  if (spec.gaming_mode !== undefined) {
    state.gaming_mode = Boolean(spec.gaming_mode)
  }

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
      (advanced.server as any)[key] = Boolean(value)
    } else {
      (advanced.server as any)[key] = String(value)
    }
  })

  const clientOptions = spec.client_options || {}
  Object.entries(advanced.client).forEach(([key, defaultValue]) => {
    const value = clientOptions[key]
    if (value === undefined || value === null) {
      return
    }
    if (typeof defaultValue === 'boolean') {
      (advanced.client as any)[key] = Boolean(value)
    } else {
      (advanced.client as any)[key] = String(value)
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
