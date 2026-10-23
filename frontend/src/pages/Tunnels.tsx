import { useEffect, useState, useRef } from 'react'
import { Plus, Trash2, Edit2, RotateCw, CheckCircle2, XCircle, Clock, Loader2, X, Network, Zap, AlertTriangle, Activity, Folder, FolderPlus, FolderMinus, CheckSquare, Tag, Layers, Shield, Globe, Gamepad2, Sliders, Sparkles, Rocket, Fingerprint, Scale, ArrowLeftRight, ShieldCheck, EyeOff, Gauge, Radio, Key, Lock, Server, Cpu, Terminal, RefreshCw, Settings2, RadioTower, Wifi, Info, Dices, Search, LayoutGrid, List, ArrowRight, ChevronDown, Check } from 'lucide-react'
import api from '../api/client'
import { parseAddressPort, formatAddressPort } from '../utils/addressUtils'
import { useLanguage } from '../contexts/LanguageContext'
import { useToast } from '../contexts/ToastContext'
import { EmptyState } from '../components/EmptyState'
import { LatencyBadge } from '../components/LatencyBadge'
import { CustomSelect } from '../components/CustomSelect'

import {
  Tunnel, TunnelCategory, ReapplyStatus, TunnelReapplyState,
  getBackhaulDisplayInfo, getCategoryColorClasses
} from './tunnels/types'
import EditTunnelModal from './tunnels/EditTunnelModal'
import AddTunnelModal from './tunnels/AddTunnelModal'

let _cachedTunnels: Tunnel[] = []
let _cachedCategories: TunnelCategory[] = []
let _cachedTunnelsIranNodes: any[] = []
let _cachedTunnelsForeignServers: any[] = []

const Tunnels = () => {
  const { t, isRTL } = useLanguage()
  const { showToast, showConfirm } = useToast()
  const [tunnels, setTunnels] = useState<Tunnel[]>(_cachedTunnels)
  const [nodes, setNodes] = useState<any[]>(_cachedTunnelsIranNodes)
  const [servers, setServers] = useState<any[]>(_cachedTunnelsForeignServers)
  const [loading, setLoading] = useState(_cachedTunnels.length === 0)
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
  const [categories, setCategories] = useState<TunnelCategory[]>(_cachedCategories)
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

  // ─── Sticky Toolbar Scroll State ─────────────────────────────
  const [isScrolled, setIsScrolled] = useState(false)
  const pageContainerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = pageContainerRef.current
    if (!el) return
    const scrollParent = el.closest('.overflow-y-auto') || window

    const handleScroll = () => {
      const scrollTop = scrollParent instanceof Window ? window.scrollY : scrollParent.scrollTop
      setIsScrolled(scrollTop > 20)
    }

    scrollParent.addEventListener('scroll', handleScroll, { passive: true })
    handleScroll()
    return () => scrollParent.removeEventListener('scroll', handleScroll)
  }, [])


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
      // 1. Fetch tunnels & categories first - returns immediately
      const [tunnelsRes, categoriesRes] = await Promise.all([
        api.get('/tunnels'),
        api.get('/tunnels/categories').catch(() => ({ data: [] })),
      ])
      _cachedTunnels = tunnelsRes.data || []
      _cachedCategories = categoriesRes.data || []
      setTunnels(_cachedTunnels)
      setCategories(_cachedCategories)
      // Immediately unblock UI so tunnel cards render instantaneously
      setLoading(false)

      // 2. Fetch nodes concurrently to enrich topology tags without delaying the view
      api.get('/nodes').then((nodesRes) => {
        if (nodesRes && Array.isArray(nodesRes.data)) {
          const iranNodes = nodesRes.data.filter((node: any) => 
            node.metadata?.role === 'iran' || !node.metadata?.role
          )
          const foreignServers = nodesRes.data.filter((node: any) => 
            node.metadata?.role === 'foreign'
          )
          _cachedTunnelsIranNodes = iranNodes
          _cachedTunnelsForeignServers = foreignServers
          setNodes(iranNodes)
          setServers(foreignServers)
        }
      }).catch((err) => {
        console.error('Failed to fetch nodes in tunnels view:', err)
      })
    } catch (error) {
      console.error('Failed to fetch data:', error)
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
      message: isRTL
        ? `آیا از حذف دسته‌بندی «${name}» اطمینان دارید؟ تونل‌های این دسته بدون دسته‌بندی خواهند شد.`
        : `Delete "${name}" category? Tunnels in this category will become uncategorized.`,
      variant: 'danger',
      confirmText: t.tunnels.deleteTunnel || 'Delete',
      cancelText: t.tunnels.cancel || 'Cancel',
    })
    if (!ok) return
    try {
      await api.delete(`/tunnels/categories/${encodeURIComponent(name)}`)
      fetchData()
      if (activeCategoryTab === name) setActiveCategoryTab('all')
      showToast('success', isRTL ? 'دسته‌بندی حذف شد' : 'Deleted', isRTL ? `دسته‌بندی «${name}» حذف شد.` : `Category "${name}" deleted`)
    } catch (e: any) {
      showToast('error', isRTL ? 'خطا در حذف' : 'Error', e.response?.data?.detail || (isRTL ? 'حذف دسته‌بندی با خطا مواجه شد' : 'Failed to delete category'))
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
      title: t.tunnels.confirmDeleteTunnelTitle || 'Delete Tunnel',
      message: isRTL
        ? `آیا از حذف تونل «${targetName}» اطمینان دارید؟ این اتصال به طور دائمی متوقف و حذف خواهد شد.`
        : `Are you sure you want to delete "${targetName}"? The connection will be permanently stopped and removed.`,
      variant: 'danger',
      confirmText: t.tunnels.deleteConfirmBtn || 'Delete Tunnel',
      cancelText: t.tunnels.cancel || 'Cancel',
    })
    if (!confirmed) return
    
    setDeletingTunnelId(id)
    showToast('info', isRTL ? 'در حال حذف تونل' : 'Deleting Tunnel', isRTL ? `در حال حذف «${targetName}»...` : `Removing "${targetName}"...`, 2500)

    try {
      await api.delete(`/tunnels/${id}`)
      // Optimistic removal: remove immediately from UI so feedback is instantaneous
      setTunnels(prev => prev.filter(t => t.id !== id))
      setSelectedTunnelIds(prev => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
      showToast('success', isRTL ? 'تونل حذف شد' : 'Tunnel Deleted', isRTL ? `تونل «${targetName}» با موفقیت حذف شد.` : `"${targetName}" was deleted successfully`)
      fetchData()
    } catch (error: any) {
      console.error('Failed to delete tunnel:', error)
      const errorMsg = error.response?.data?.detail || error.message || (isRTL ? 'حذف تونل با خطا مواجه شد' : 'Failed to delete tunnel')
      showToast('error', isRTL ? 'خطا در حذف تونل' : 'Delete Failed', errorMsg)
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
        showToast('success', isRTL ? 'اعمال مجدد انجام شد' : 'Tunnel Reapplied', isRTL ? `تونل «${tunnel.name}» با موفقیت مجدداً اعمال شد.` : `${tunnel.name} reapplied successfully`)
        fetchData()
      } else {
        throw new Error(response.data?.message || (isRTL ? 'اعمال مجدد تونل ناموفق بود' : 'Failed to reapply tunnel'))
      }
    } catch (error: any) {
      console.error('Failed to reapply tunnel:', error)
      const errorMessage = error.response?.data?.detail || error.message || (isRTL ? 'اعمال مجدد تونل با خطا مواجه شد' : 'Failed to reapply tunnel')
      showToast('error', isRTL ? 'خطا در اعمال مجدد' : 'Reapply Failed', errorMessage)
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
        const message = response.data.message || `${tunnel.name}: ${latency ? `${latency} ms` : 'Online'} — Healthy and routing traffic`
        showToast('success', 'Tunnel Connection Active', message)
        setTunnels(prev => prev.map(t => t.id === tunnel.id ? { ...t, spec: { ...t.spec, latency_ms: latency } } : t))
      } else {
        showToast('error', 'Tunnel Test Failed', response.data?.message || 'Tunnel is not responding')
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
    <div ref={pageContainerRef} className="w-full max-w-7xl mx-auto animate-fade-in">
      {/* ── Header ──────────────────────────────────────────── */}
      <div className="flex items-center justify-between gap-3 pb-3 border-b border-slate-200/60 dark:border-white/[0.06] mb-3 sm:mb-4">
        <div>
          <div className="flex items-center gap-1.5 sm:gap-2 mb-0.5 sm:mb-1">
            <span className="inline-flex items-center justify-center p-1 sm:p-1.5 rounded-lg bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border border-indigo-500/20">
              <Network className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
            </span>
            <span className="text-[10px] sm:text-xs font-mono font-medium text-indigo-600 dark:text-indigo-400 uppercase tracking-wider">
              {t.tunnels.networkInfra || 'Network Infrastructure'}
            </span>
          </div>
          <h1 className="text-xl sm:text-3xl font-extrabold text-slate-900 dark:text-white tracking-tight">
            {t.tunnels.title}
          </h1>
          <p className="hidden sm:block text-xs sm:text-sm text-slate-500 dark:text-slate-400 mt-0.5">
            {t.tunnels.subtitle}
          </p>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {/* Live Ping Toggle */}
          <button
            type="button"
            onClick={() => setLivePingEnabled(prev => !prev)}
            className={`px-3 py-2 sm:px-3.5 sm:py-2.5 rounded-xl sm:rounded-2xl border transition-all font-semibold text-xs flex items-center gap-2 shadow-xs min-h-[44px] active:scale-95 cursor-pointer ${
              livePingEnabled
                ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-600 dark:text-emerald-400'
                : 'bg-white dark:bg-[#12161f] border-slate-200 dark:border-white/[0.07] text-slate-500 dark:text-slate-400'
            }`}
            title={livePingEnabled ? (isRTL ? 'پینگ زنده فعال است (کلیک برای توقف)' : 'Live ping auto-refreshing every 2s (Click to pause)') : (isRTL ? 'پینگ زنده متوقف است (کلیک برای فعال‌سازی)' : 'Live ping paused (Click to enable)')}
            aria-label={t.tunnels.livePing || 'Live Ping'}
          >
            <span className="relative flex h-2 w-2">
              {livePingEnabled && (
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
              )}
              <span className={`relative inline-flex rounded-full h-2 w-2 ${livePingEnabled ? 'bg-emerald-500' : 'bg-slate-400'}`}></span>
            </span>
            <span className="font-mono text-xs">{t.tunnels.livePing || 'Live Ping:'} {livePingEnabled ? '2s' : (t.tunnels.livePingOff || 'OFF')}</span>
          </button>
        </div>
      </div>

      {/* ── Sticky Toolbar: Unified full-bleed frosted header bar flush against the top edge ── */}
      <div
        className={`sticky -top-3.5 sm:-top-6 lg:-top-8 z-20 -mx-3.5 sm:-mx-6 lg:-mx-8 px-3.5 sm:px-6 lg:px-8 py-2.5 sm:py-3 backdrop-blur-2xl border-b transition-all duration-200 ease-out ${
          isScrolled ? 'shadow-md dark:shadow-2xl' : 'shadow-xs'
        } ${
          selectedTunnelIds.size > 0
            ? 'bg-indigo-50/95 dark:bg-[#0f1424]/95 border-indigo-200/90 dark:border-indigo-500/30'
            : 'bg-slate-50/95 dark:bg-[#070a13]/95 border-slate-200/80 dark:border-white/[0.08]'
        }`}
      >
        <div className="max-w-7xl mx-auto">
        {selectedTunnelIds.size > 0 ? (
          /* ── Contextual Bulk Selection Mode (Mobile-Optimized Grid + Desktop Inline) ── */
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-2.5 px-0.5 py-0.5 animate-fade-in">
            {/* Top row on mobile / Left side on desktop: Master Toggle + Counter + Clear */}
            <div className="flex items-center justify-between sm:justify-start gap-2 min-w-0">
              <div className="flex items-center gap-2 min-w-0">
                <button
                  type="button"
                  onClick={toggleSelectAllFiltered}
                  aria-label={filteredTunnels.every(t => selectedTunnelIds.has(t.id)) ? (t.tunnels.deselectAll || 'Deselect All') : (t.tunnels.selectAll || 'Select All')}
                  className="min-h-[42px] h-10 sm:h-9 px-3 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs flex items-center gap-2 shadow-xs transition-all active:scale-95 cursor-pointer shrink-0"
                  title={filteredTunnels.every(t => selectedTunnelIds.has(t.id)) ? (t.tunnels.deselectAll || 'Deselect All') : (t.tunnels.selectAll || 'Select All')}
                >
                  <CheckSquare size={16} />
                  <span>
                    {filteredTunnels.every(t => selectedTunnelIds.has(t.id))
                      ? (t.tunnels.deselectAll || 'Deselect All')
                      : (t.tunnels.selectAll || 'Select All')}
                  </span>
                </button>

                <div className="flex items-center gap-1.5 font-mono text-xs font-bold text-indigo-700 dark:text-indigo-300 bg-indigo-500/10 dark:bg-indigo-500/20 px-2.5 py-2 sm:py-1.5 rounded-xl border border-indigo-500/20 shrink-0 min-h-[42px] sm:min-h-0">
                  <span className="text-sm tabular-nums">{selectedTunnelIds.size}</span>
                  <span className="font-sans font-medium text-slate-500 dark:text-slate-400 text-xs">
                    / {filteredTunnels.length}
                  </span>
                </div>
              </div>

              {/* Clear button on mobile */}
              <button
                type="button"
                onClick={() => setSelectedTunnelIds(new Set())}
                className="text-xs font-semibold text-slate-500 hover:text-rose-600 dark:text-slate-400 dark:hover:text-rose-400 transition-colors sm:hidden px-2.5 py-2 min-h-[42px] flex items-center gap-1 cursor-pointer"
              >
                <X size={14} />
                <span>{t.tunnels.clearSelection || 'Clear selection'}</span>
              </button>
            </div>

            {/* Bottom row on mobile (2 equal 50/50 columns) / Right side on desktop (inline flex) */}
            <div className="grid grid-cols-2 sm:flex sm:items-center gap-2 w-full sm:w-auto pt-2 sm:pt-0 border-t sm:border-t-0 border-indigo-200/50 dark:border-indigo-500/15">
              <button
                type="button"
                onClick={handleReapplySelected}
                disabled={!!reapplyAllProgress && !reapplyAllDone}
                aria-label={t.tunnels.reapplySelected || 'Reapply Selected'}
                className="w-full sm:w-auto min-h-[42px] h-11 sm:h-9 px-3 sm:px-4 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs flex items-center justify-center gap-1.5 sm:gap-2 transition-all shadow-xs active:scale-95 disabled:opacity-50 cursor-pointer"
                title={t.tunnels.reapplySelected || 'Reapply Selected'}
              >
                <RotateCw size={15} className={!!reapplyAllProgress && !reapplyAllDone ? 'animate-spin' : ''} />
                <span className="truncate">{t.tunnels.reapplySelected || 'Reapply'}</span>
                <span className="font-mono tabular-nums bg-white/20 px-1.5 py-0.5 rounded-md text-[11px] shrink-0">
                  {selectedTunnelIds.size}
                </span>
              </button>

              <button
                type="button"
                onClick={() => setShowAssignCategoryModal(true)}
                aria-label={t.tunnels.moveToCategory || 'Move to Category'}
                className="w-full sm:w-auto min-h-[42px] h-11 sm:h-9 px-3 rounded-xl bg-white dark:bg-[#161c28] hover:bg-slate-50 dark:hover:bg-white/[0.08] text-slate-700 dark:text-slate-200 border border-slate-200/80 dark:border-white/[0.08] font-semibold text-xs flex items-center justify-center gap-1.5 transition-all active:scale-95 cursor-pointer"
                title={t.tunnels.moveToCategory || 'Move to Category'}
              >
                <Folder size={15} />
                <span className="truncate">{t.tunnels.moveToCategory || 'Category'}</span>
              </button>

              {/* Desktop Clear Button */}
              <button
                type="button"
                onClick={() => setSelectedTunnelIds(new Set())}
                aria-label={t.tunnels.deselectAll || 'Cancel selection'}
                className="hidden sm:flex min-h-[36px] h-9 w-9 rounded-xl bg-white dark:bg-[#161c28] hover:bg-rose-50 dark:hover:bg-rose-950/30 text-slate-400 hover:text-rose-600 dark:hover:text-rose-400 border border-slate-200/80 dark:border-white/[0.08] items-center justify-center transition-all active:scale-95 cursor-pointer shrink-0"
                title={t.tunnels.deselectAll || 'Cancel selection'}
              >
                <X size={16} />
              </button>
            </div>
          </div>
        ) : (
          /* ── Normal Browsing Mode ── */
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 sm:gap-3">
            {/* Row 1 on mobile: Search Input (flex-1) + View Mode Toggle on mobile (shrink-0) */}
            <div className="flex items-center gap-2 w-full sm:w-auto">
              {/* Search Input */}
              <div className="relative flex-1 sm:w-64 md:w-72 sm:flex-initial">
                <Search size={16} className="absolute left-3.5 rtl:left-auto rtl:right-3.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder={t.tunnels.searchPlaceholder || 'Search name or port...'}
                  aria-label={t.tunnels.searchPlaceholder || 'Search name or port'}
                  className="w-full h-11 sm:h-9 pl-10 pr-9 rtl:pl-9 rtl:pr-10 bg-slate-50/80 dark:bg-[#161c28] border border-slate-200/80 dark:border-white/[0.08] focus:border-indigo-500 dark:focus:border-indigo-400 focus:bg-white dark:focus:bg-[#161c28] focus:ring-2 focus:ring-indigo-500/20 rounded-xl text-[16px] sm:text-xs font-medium text-slate-900 dark:text-slate-100 placeholder:text-slate-400 transition-all focus:outline-none shadow-2xs"
                />
                {searchQuery && (
                  <button
                    type="button"
                    onClick={() => setSearchQuery('')}
                    aria-label={t.tunnels.clearSearch || 'Clear search'}
                    className="absolute right-1.5 rtl:right-auto rtl:left-1.5 top-1/2 -translate-y-1/2 w-8 h-8 flex items-center justify-center text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 rounded-lg transition-colors cursor-pointer"
                    title={t.tunnels.clearSearch || 'Clear search'}
                  >
                    <X size={15} />
                  </button>
                )}
              </div>

              {/* View Mode Toggle: List / Grid (Shown on mobile in Row 1!) */}
              <div className="flex sm:hidden items-center p-0.5 rounded-xl bg-slate-100 dark:bg-[#161c28] border border-slate-200/80 dark:border-white/[0.08] h-11 shrink-0" role="group" aria-label="View mode">
                <button
                  type="button"
                  onClick={() => handleSetViewMode('list')}
                  className={`h-10 px-3 rounded-lg transition-all cursor-pointer flex items-center justify-center ${
                    viewMode === 'list'
                      ? 'bg-white dark:bg-slate-700 text-indigo-600 dark:text-indigo-400 shadow-2xs font-semibold'
                      : 'text-slate-400 hover:text-slate-600 dark:hover:text-slate-300'
                  }`}
                  title={t.tunnels.listView || 'List View'}
                  aria-label={t.tunnels.listView || 'List View'}
                  aria-pressed={viewMode === 'list'}
                >
                  <List size={17} />
                </button>
                <button
                  type="button"
                  onClick={() => handleSetViewMode('grid')}
                  className={`h-10 px-3 rounded-lg transition-all cursor-pointer flex items-center justify-center ${
                    viewMode === 'grid'
                      ? 'bg-white dark:bg-slate-700 text-indigo-600 dark:text-indigo-400 shadow-2xs font-semibold'
                      : 'text-slate-400 hover:text-slate-600 dark:hover:text-slate-300'
                  }`}
                  title={t.tunnels.gridView || 'Grid View'}
                  aria-label={t.tunnels.gridView || 'Grid View'}
                  aria-pressed={viewMode === 'grid'}
                >
                  <LayoutGrid size={17} />
                </button>
              </div>
            </div>

            {/* Row 2 on mobile / Right side on desktop: Category Dropdown + Desktop View Toggle + Actions */}
            <div className="flex items-center justify-between sm:justify-end gap-1.5 sm:gap-2 w-full sm:w-auto">
              {/* Category Dropdown (Takes flex-1 on mobile so category name has plenty of room!) */}
              <div ref={categoryDropdownRef} className="relative min-w-0 flex-1 sm:flex-initial">
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
                        className={`w-full sm:w-auto h-11 sm:h-9 px-2.5 sm:px-3 rounded-xl text-xs font-semibold transition-all duration-150 flex items-center justify-between sm:justify-start gap-1.5 sm:gap-2 border select-none cursor-pointer ${
                          showCategoryDropdown || activeCategoryTab !== 'all'
                            ? 'bg-white dark:bg-[#161c28] border-indigo-500/60 dark:border-indigo-400/60 text-slate-900 dark:text-white shadow-2xs ring-2 ring-indigo-500/15'
                            : 'bg-white dark:bg-[#161c28] border-slate-200/80 dark:border-white/[0.08] text-slate-700 dark:text-slate-300 hover:border-slate-300 dark:hover:border-white/20'
                        }`}
                        title={t.tunnels.categories || 'Categories'}
                      >
                        <div className="flex items-center gap-1.5 min-w-0">
                          {activeCategoryTab === 'all' ? (
                            <Layers size={15} className="text-indigo-600 dark:text-indigo-400 shrink-0" />
                          ) : activeCategoryTab === 'uncategorized' ? (
                            <FolderMinus size={15} className="text-slate-500 dark:text-slate-400 shrink-0" />
                          ) : (
                            <Tag size={14} className={`shrink-0 ${getCategoryColorClasses(currentCategory?.color || 'blue').text}`} />
                          )}

                          <span className="truncate max-w-[110px] sm:max-w-[140px]">
                            {activeCategoryTab === 'all'
                              ? (t.tunnels.allTunnels || 'All Tunnels')
                              : activeCategoryTab === 'uncategorized'
                              ? (t.tunnels.uncategorized || 'Uncategorized')
                              : (currentCategory?.name || activeCategoryTab)}
                          </span>
                        </div>

                        <div className="flex items-center gap-1 shrink-0">
                          <span className="px-1.5 py-0.5 rounded-full text-[10px] font-mono font-bold leading-none tabular-nums bg-slate-100 dark:bg-white/10 text-slate-600 dark:text-slate-300">
                            {categoryCount}
                          </span>

                          <ChevronDown size={14} className={`text-slate-400 transition-transform duration-200 ${showCategoryDropdown ? 'rotate-180 text-indigo-600 dark:text-indigo-400' : ''}`} />
                        </div>
                      </button>

                      {/* Dropdown Menu Popover */}
                      {showCategoryDropdown && (
                        <div className="absolute left-0 rtl:left-auto rtl:right-0 top-full mt-1.5 w-64 max-w-[calc(100vw-2.5rem)] bg-white dark:bg-[#161c28] border border-slate-200/90 dark:border-white/[0.1] rounded-2xl shadow-xl z-50 py-1.5 backdrop-blur-md animate-in fade-in-50 zoom-in-95 duration-100 max-h-80 overflow-y-auto custom-scrollbar">
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
                            className={`w-full px-3 py-2.5 sm:py-2 text-xs font-semibold flex items-center justify-between gap-2 transition-colors cursor-pointer min-h-[44px] ${
                              activeCategoryTab === 'all'
                                ? 'bg-indigo-50 dark:bg-indigo-950/40 text-indigo-600 dark:text-indigo-400'
                                : 'text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04]'
                            }`}
                          >
                            <div className="flex items-center gap-2 min-w-0">
                              <Layers size={15} className={activeCategoryTab === 'all' ? 'text-indigo-600 dark:text-indigo-400' : 'text-slate-400'} />
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
                                className={`group/catitem px-3 py-2 sm:py-1.5 flex items-center justify-between gap-2 transition-colors cursor-pointer min-h-[44px] ${
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
                                <div className="flex items-center gap-1 shrink-0">
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
                                    className="min-w-[36px] min-h-[36px] flex items-center justify-center rounded-lg text-slate-400 hover:text-rose-600 dark:hover:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/40 opacity-70 sm:opacity-0 group-hover/catitem:opacity-100 transition-opacity cursor-pointer shrink-0"
                                    title={`${t.tunnels.deleteCategory || 'Delete'} "${cat.name}"`}
                                    aria-label={`${t.tunnels.deleteCategory || 'Delete'} "${cat.name}"`}
                                  >
                                    <X size={14} strokeWidth={2.5} />
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
                              className={`w-full px-3 py-2.5 sm:py-2 text-xs font-semibold flex items-center justify-between gap-2 transition-colors cursor-pointer min-h-[44px] ${
                                activeCategoryTab === 'uncategorized'
                                  ? 'bg-indigo-50 dark:bg-indigo-950/40 text-indigo-600 dark:text-indigo-400'
                                  : 'text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04]'
                              }`}
                            >
                              <div className="flex items-center gap-2 min-w-0">
                                <FolderMinus size={15} className="text-slate-400 shrink-0" />
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
                            className="w-full px-3 py-2.5 sm:py-2 text-xs font-semibold text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50/70 dark:hover:bg-indigo-950/30 flex items-center gap-2 transition-colors cursor-pointer min-h-[44px]"
                          >
                            <FolderPlus size={15} />
                            <span>{t.tunnels.newCategory || 'New Category'}</span>
                          </button>
                        </div>
                      )}
                    </>
                  )
                })()}
              </div>

              {/* Desktop View Mode Toggle (hidden on mobile, shown in Row 1 on mobile) */}
              <div className="hidden sm:flex items-center p-0.5 rounded-xl bg-slate-100 dark:bg-[#161c28] border border-slate-200/80 dark:border-white/[0.08] h-9 shrink-0" role="group" aria-label="View mode">
                <button
                  type="button"
                  onClick={() => handleSetViewMode('list')}
                  className={`h-7 px-2 rounded-lg transition-all cursor-pointer flex items-center justify-center ${
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
                  className={`h-7 px-2 rounded-lg transition-all cursor-pointer flex items-center justify-center ${
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

              {/* Select All Toggle Button */}
              {filteredTunnels.length > 0 && (
                <button
                  type="button"
                  onClick={toggleSelectAllFiltered}
                  aria-label={t.tunnels.selectAll || 'Select All'}
                  className="min-h-[44px] sm:min-h-0 h-11 sm:h-9 px-3 sm:px-3 rounded-xl min-w-[44px] text-xs font-semibold transition-all duration-150 flex items-center justify-center gap-1.5 shrink-0 border select-none cursor-pointer active:scale-95 bg-white dark:bg-[#161c28] border-slate-200/80 dark:border-white/[0.06] text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white"
                  title={`${t.tunnels.selectAll || 'Select All'} (${filteredTunnels.length})`}
                >
                  <CheckSquare size={16} className="text-slate-400" />
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
                className="min-h-[44px] sm:min-h-0 h-11 sm:h-9 px-3 sm:px-3 bg-emerald-600 hover:bg-emerald-500 text-white rounded-xl min-w-[44px] transition-all font-semibold shadow-xs flex items-center justify-center gap-1.5 text-xs active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer whitespace-nowrap shrink-0"
                title={t.tunnels.reapplyAll}
              >
                <RotateCw size={15} className={!!reapplyAllProgress && !reapplyAllDone ? 'animate-spin' : ''} />
                <span className="hidden sm:inline">{t.tunnels.reapplyAll}</span>
              </button>

              {/* Create Tunnel */}
              <button
                type="button"
                onClick={() => setShowAddModal(true)}
                aria-label={t.tunnels.createTunnel}
                className="min-h-[44px] sm:min-h-0 h-11 sm:h-9 px-3.5 sm:px-3.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl min-w-[44px] transition-all font-semibold shadow-xs flex items-center justify-center gap-1.5 text-xs active:scale-95 cursor-pointer whitespace-nowrap shrink-0"
                title={t.tunnels.createTunnel}
              >
                <Plus size={17} />
                <span className="hidden sm:inline">{t.tunnels.createTunnel}</span>
              </button>
            </div>
          </div>
        )}
        </div>
      </div>

      {/* ── Tunnel Cards ────────────────────────────────────── */}
      <div className="mt-4 sm:mt-6">
      {filteredTunnels.length === 0 ? (
        searchQuery.trim() ? (
          <EmptyState
            icon={<Search size={32} />}
            title={t.tunnels.noMatchingTunnels || 'No tunnels match your search'}
            description={isRTL ? `هیچ تونلی با نام یا پورت مطابق "${searchQuery}" یافت نشد.` : `No tunnels found with name or port matching "${searchQuery}".`}
            action={{ label: t.tunnels.clearSearch || 'Clear search', onClick: () => setSearchQuery('') }}
          />
        ) : (
          <EmptyState
            icon={<Network size={32} />}
            title={isRTL ? 'هیچ تونلی در این دسته‌بندی وجود ندارد' : 'No tunnels in this category'}
            description={isRTL ? 'هیچ تونلی مطابق با فیلتر دسته‌بندی انتخاب‌شده یافت نشد.' : 'No tunnels match the selected category filter.'}
            action={{ label: isRTL ? 'مشاهده همه تونل‌ها' : (t.tunnels.allTunnels || 'View All Tunnels'), onClick: () => setActiveCategoryTab('all') }}
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

            let longPressTimer: any = null;

            return (
              <div
                key={tunnel.id}
                onClick={() => {
                  if (selectedTunnelIds.size > 0 && !isReapplying && !isDeleting) {
                    toggleSelectTunnel(tunnel.id);
                  }
                }}
                onTouchStart={() => {
                  if (!isReapplying && !isDeleting) {
                    longPressTimer = setTimeout(() => {
                      toggleSelectTunnel(tunnel.id);
                      if (typeof navigator !== 'undefined' && 'vibrate' in navigator) {
                        try { navigator.vibrate(40); } catch (_) {}
                      }
                    }, 450);
                  }
                }}
                onTouchEnd={() => {
                  if (longPressTimer) clearTimeout(longPressTimer);
                }}
                onTouchMove={() => {
                  if (longPressTimer) clearTimeout(longPressTimer);
                }}
                className={`relative bg-white dark:bg-[#12161f]/90 rounded-3xl shadow-xs border transition-all duration-200 ${
                  viewMode === 'grid' ? 'flex flex-col justify-between h-full' : ''
                } ${
                  selectedTunnelIds.size > 0
                    ? 'cursor-pointer select-none active:scale-[0.99]'
                    : ''
                } ${
                  isSelected
                    ? 'border-indigo-500 ring-2 ring-indigo-500/30 bg-indigo-500/[0.05] dark:bg-indigo-950/25 shadow-md'
                    : isDeleting
                    ? 'border-rose-500/60 ring-2 ring-rose-500/20 bg-rose-500/[0.03] shadow-md opacity-80'
                    : isReapplying
                    ? 'border-emerald-500/60 shadow-emerald-500/10 ring-2 ring-emerald-500/20'
                    : selectedTunnelIds.size > 0
                    ? 'border-slate-200/80 dark:border-white/[0.07] hover:border-indigo-300 dark:hover:border-indigo-700/50'
                    : 'border-slate-200/80 dark:border-white/[0.07] hover:border-slate-300 dark:hover:border-white/[0.12] hover:shadow-md'
                }`}>
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
                  <div className="p-4 sm:p-5 flex flex-col justify-between h-full gap-3.5 min-w-0">
                    <div>
                      {/* Top Bar: Checkbox + Status + Pending on left/start; LatencyBadge (mobile) or Desktop Action Buttons on right/end */}
                      <div className="flex items-center justify-between gap-2 min-w-0">
                        <div className="flex items-center gap-2 min-w-0">
                          {/* Multi-Selection Checkbox with touch padding */}
                          <button
                            type="button"
                            onClick={(e) => toggleSelectTunnel(tunnel.id, e)}
                            disabled={isReapplying || isDeleting}
                            aria-label={selectedTunnelIds.has(tunnel.id) ? (t.tunnels.deselectAll || 'Deselect') : (t.tunnels.selectAll || 'Select')}
                            title={selectedTunnelIds.has(tunnel.id) ? (t.tunnels.deselectAll || 'Deselect') : (t.tunnels.selectAll || 'Select')}
                            className="shrink-0 -m-2.5 p-2.5 min-w-[44px] min-h-[44px] flex items-center justify-center cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                          >
                            <span className={`w-5 h-5 rounded-lg border flex items-center justify-center transition-all ${
                              selectedTunnelIds.has(tunnel.id)
                                ? 'bg-indigo-600 border-indigo-600 text-white shadow-xs scale-105'
                                : 'border-slate-300 dark:border-white/20 hover:border-indigo-400 dark:hover:border-indigo-500 bg-white dark:bg-[#161c28]'
                            }`}>
                              {selectedTunnelIds.has(tunnel.id) && (
                                <CheckCircle2 size={13} className="fill-current text-white" />
                              )}
                            </span>
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
                              className="px-1.5 py-0.5 rounded-md text-[10px] font-mono font-bold tracking-tight bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/30 text-center animate-pulse shrink-0"
                              title={isRTL ? "پیکربندی تغییر کرده است. برای اعمال روی «اعمال مجدد» کلیک کنید." : "Configuration modified. Click Reapply to apply changes to live core."}
                            >
                              {t.tunnels.pendingChanges || 'Pending'}
                            </span>
                          )}
                        </div>

                        {/* Mobile Top-Right / Top-Left (RTL): LatencyBadge */}
                        <div className="flex sm:hidden shrink-0">
                          <LatencyBadge latency={tunnel.spec?.latency_ms} status={tunnel.status} />
                        </div>

                        {/* Desktop Action Icon Buttons (hidden on mobile, shown on sm:) */}
                        <div className="hidden sm:flex items-center gap-1 shrink-0">
                          <button
                            type="button"
                            onClick={(e) => { e.stopPropagation(); handleTestActiveTunnel(tunnel); }}
                            disabled={isReapplying || isDeleting || isTesting}
                            className="p-1.5 text-amber-600 dark:text-amber-400 hover:bg-amber-500/10 border border-transparent hover:border-amber-500/20 rounded-lg transition-all disabled:opacity-40 min-w-[32px] min-h-[32px] flex items-center justify-center active:scale-95 cursor-pointer"
                            title={t.tunnels.testLiveConnection || 'Test Ping'}
                            aria-label={t.tunnels.testLiveConnection || 'Test Ping'}
                          >
                            {isTesting ? (
                              <Loader2 size={16} className="animate-spin text-amber-500" />
                            ) : (
                              <Zap size={16} />
                            )}
                          </button>
                          <button
                            type="button"
                            onClick={(e) => { e.stopPropagation(); reapplyTunnel(tunnel); }}
                            disabled={isReapplying || isDeleting || !!reapplyingTunnelId}
                            className={`p-1.5 rounded-lg transition-all disabled:opacity-40 disabled:cursor-not-allowed min-w-[32px] min-h-[32px] flex items-center justify-center active:scale-95 cursor-pointer ${
                              Boolean(tunnel.spec?._pending_reapply)
                                ? 'text-amber-600 dark:text-amber-400 bg-amber-500/15 border border-amber-500/30 ring-2 ring-amber-500/20 shadow-xs hover:bg-amber-500/25'
                                : 'text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/20'
                            }`}
                            title={Boolean(tunnel.spec?._pending_reapply) ? (isRTL ? 'اعمال تغییرات روی هسته' : 'Reapply pending changes to core') : (t.tunnels.reapplyTunnel || 'Reapply tunnel')}
                            aria-label={t.tunnels.reapplyTunnel || 'Reapply tunnel'}
                          >
                            {isReapplying ? (
                              <Loader2 size={16} className="animate-spin" />
                            ) : (
                              <RotateCw size={16} className={Boolean(tunnel.spec?._pending_reapply) ? 'text-amber-600 dark:text-amber-400' : ''} />
                            )}
                          </button>
                          <button
                            type="button"
                            onClick={(e) => { e.stopPropagation(); setEditingTunnel(tunnel); }}
                            disabled={isReapplying || isDeleting}
                            className="p-1.5 text-slate-600 dark:text-slate-300 hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-indigo-500/10 border border-transparent hover:border-indigo-500/20 rounded-lg transition-all disabled:opacity-40 min-w-[32px] min-h-[32px] flex items-center justify-center active:scale-95 cursor-pointer"
                            title={t.tunnels.editTunnel || 'Edit tunnel'}
                            aria-label={t.tunnels.editTunnel || 'Edit tunnel'}
                          >
                            <Edit2 size={16} />
                          </button>
                          <button
                            type="button"
                            onClick={(e) => { e.stopPropagation(); deleteTunnel(tunnel.id); }}
                            disabled={isReapplying || isDeleting}
                            className="p-1.5 text-slate-600 dark:text-slate-300 hover:text-rose-600 dark:hover:text-rose-400 hover:bg-rose-500/10 border border-transparent hover:border-rose-500/20 rounded-lg transition-all disabled:opacity-40 min-w-[32px] min-h-[32px] flex items-center justify-center active:scale-95 cursor-pointer"
                            title={isDeleting ? (isRTL ? 'در حال حذف تونل...' : 'Deleting tunnel...') : (t.tunnels.deleteTunnel || 'Delete tunnel')}
                            aria-label={t.tunnels.deleteTunnel || 'Delete tunnel'}
                          >
                            {isDeleting ? (
                              <Loader2 size={16} className="animate-spin text-rose-500" />
                            ) : (
                              <Trash2 size={16} />
                            )}
                          </button>
                        </div>
                      </div>

                      {/* Tunnel Name */}
                      <h3 className="text-base font-bold text-slate-900 dark:text-white truncate mt-2.5" title={tunnel.name}>
                        {tunnel.name}
                      </h3>

                      {/* Badges Row */}
                      <div className="flex items-center gap-1.5 flex-wrap mt-2 min-w-0">
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

                        {/* Latency on desktop */}
                        <div className="hidden sm:inline-flex">
                          <LatencyBadge latency={tunnel.spec?.latency_ms} status={tunnel.status} />
                        </div>
                      </div>
                    </div>

                    {/* Bottom Details: Ports, Core Port, Topology Nodes */}
                    <div className="pt-2.5 border-t border-slate-100 dark:border-white/[0.05] space-y-2 text-xs">
                      <div className="flex items-center justify-between gap-2 text-xs">
                        <span className="text-slate-400 dark:text-slate-500 font-medium">{t.tunnels.portsLabel || 'Ports:'}</span>
                        <span className="font-mono font-bold text-slate-800 dark:text-slate-200 tabular-nums truncate max-w-[180px]" title={ports}>{ports}</span>
                      </div>

                      {corePort && (
                        <div className="flex items-center justify-between gap-2 text-xs">
                          <span className="font-medium text-slate-400">{t.tunnels.corePortLabel || 'Core Port:'}</span>
                          <span className="text-slate-700 dark:text-slate-300 font-mono font-semibold tabular-nums">{corePort}</span>
                        </div>
                      )}

                      {/* Topology Nodes */}
                      {(iranNode || foreignServer) && (
                        <div className="flex items-center gap-1.5 pt-1 text-[11px]">
                          {iranNode && (
                            <div className="flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-slate-100 dark:bg-white/[0.04] border border-slate-200/60 dark:border-white/[0.05] truncate flex-1 min-w-0" title={isRTL ? `نود ایران: ${iranNode.name || iranNode.id}` : `Iran Node: ${iranNode.name || iranNode.id}`}>
                              <span className="text-slate-400 text-[9px] uppercase font-mono shrink-0">{t.tunnels.irNodeLabel ? `${t.tunnels.irNodeLabel}:` : 'IR:'}</span>
                              <span className="text-slate-700 dark:text-slate-300 font-semibold truncate">{iranNode.name || iranNode.id.substring(0, 8)}</span>
                            </div>
                          )}
                          {iranNode && foreignServer && (
                            <ArrowRight size={12} className="text-slate-400 shrink-0 rtl:rotate-180" />
                          )}
                          {foreignServer && (
                            <div className="flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-slate-100 dark:bg-white/[0.04] border border-slate-200/60 dark:border-white/[0.05] truncate flex-1 min-w-0" title={isRTL ? `سرور خارج: ${foreignServer.name || foreignServer.id}` : `Foreign Server: ${foreignServer.name || foreignServer.id}`}>
                              <span className="text-slate-400 text-[9px] uppercase font-mono shrink-0">{t.tunnels.extServerLabel ? `${t.tunnels.extServerLabel}:` : 'EXT:'}</span>
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

                    {/* ── Mobile Action Buttons Bar for Grid View (4 comfortable 44px touch buttons) ── */}
                    <div className="grid grid-cols-4 gap-2 pt-2.5 mt-2.5 border-t border-slate-100 dark:border-white/[0.06] sm:hidden">
                      {/* Test Connection / Ping */}
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); handleTestActiveTunnel(tunnel); }}
                        disabled={isReapplying || isDeleting || isTesting}
                        className="h-11 rounded-xl bg-slate-100/80 dark:bg-white/[0.04] hover:bg-amber-500/15 text-amber-600 dark:text-amber-400 border border-slate-200/60 dark:border-white/[0.06] flex items-center justify-center transition-all disabled:opacity-40 active:scale-95 cursor-pointer"
                        title={t.tunnels.testLiveConnection || 'Test Ping'}
                        aria-label={t.tunnels.testLiveConnection || 'Test Ping'}
                      >
                        {isTesting ? (
                          <Loader2 size={18} className="animate-spin text-amber-500" />
                        ) : (
                          <Zap size={18} />
                        )}
                      </button>

                      {/* Reapply Tunnel */}
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); reapplyTunnel(tunnel); }}
                        disabled={isReapplying || isDeleting || !!reapplyingTunnelId}
                        className={`h-11 rounded-xl flex items-center justify-center transition-all disabled:opacity-40 active:scale-95 cursor-pointer border ${
                          Boolean(tunnel.spec?._pending_reapply)
                            ? 'text-amber-600 dark:text-amber-400 bg-amber-500/15 border-amber-500/30 ring-2 ring-amber-500/20 shadow-xs'
                            : 'text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 hover:bg-emerald-500/20 border-emerald-500/20'
                        }`}
                        title={Boolean(tunnel.spec?._pending_reapply) ? (isRTL ? 'اعمال تغییرات روی هسته' : 'Reapply pending changes to core') : (t.tunnels.reapplyTunnel || 'Reapply tunnel')}
                        aria-label={t.tunnels.reapplyTunnel || 'Reapply tunnel'}
                      >
                        {isReapplying ? (
                          <Loader2 size={18} className="animate-spin" />
                        ) : (
                          <RotateCw size={18} className={Boolean(tunnel.spec?._pending_reapply) ? 'text-amber-600 dark:text-amber-400' : ''} />
                        )}
                      </button>

                      {/* Edit Tunnel */}
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); setEditingTunnel(tunnel); }}
                        disabled={isReapplying || isDeleting}
                        className="h-11 rounded-xl bg-slate-100/80 dark:bg-white/[0.04] hover:bg-indigo-500/15 text-slate-600 dark:text-slate-300 hover:text-indigo-600 dark:hover:text-indigo-400 border border-slate-200/60 dark:border-white/[0.06] flex items-center justify-center transition-all disabled:opacity-40 active:scale-95 cursor-pointer"
                        title={t.tunnels.editTunnel || 'Edit tunnel'}
                        aria-label={t.tunnels.editTunnel || 'Edit tunnel'}
                      >
                        <Edit2 size={18} />
                      </button>

                      {/* Delete Tunnel */}
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); deleteTunnel(tunnel.id); }}
                        disabled={isReapplying || isDeleting}
                        className="h-11 rounded-xl bg-slate-100/80 dark:bg-white/[0.04] hover:bg-rose-500/15 text-slate-600 dark:text-slate-300 hover:text-rose-600 dark:hover:text-rose-400 border border-slate-200/60 dark:border-white/[0.06] flex items-center justify-center transition-all disabled:opacity-40 active:scale-95 cursor-pointer"
                        title={isDeleting ? (isRTL ? 'در حال حذف تونل...' : 'Deleting tunnel...') : (t.tunnels.deleteTunnel || 'Delete tunnel')}
                        aria-label={t.tunnels.deleteTunnel || 'Delete tunnel'}
                      >
                        {isDeleting ? (
                          <Loader2 size={18} className="animate-spin text-rose-500" />
                        ) : (
                          <Trash2 size={18} />
                        )}
                      </button>
                    </div>
                  </div>
                ) : (
                  /* ══════════════════════════════════════════════════════════
                     LIST CARD LAYOUT
                     ══════════════════════════════════════════════════════════ */
                  <div className="p-3.5 sm:p-5 lg:p-6">
                    {/* ── Mobile Top Header (hidden on sm:): Checkbox + Status + Latency ── */}
                    <div className="flex sm:hidden items-center justify-between gap-2 pb-2 mb-2 border-b border-slate-100 dark:border-white/[0.04]">
                      <div className="flex items-center gap-2 min-w-0">
                        {/* Multi-Selection Checkbox with enlarged hit area */}
                        <button
                          type="button"
                          onClick={(e) => toggleSelectTunnel(tunnel.id, e)}
                          disabled={isReapplying || isDeleting}
                          aria-label={selectedTunnelIds.has(tunnel.id) ? (t.tunnels.deselectAll || 'Deselect') : (t.tunnels.selectAll || 'Select')}
                          title={selectedTunnelIds.has(tunnel.id) ? (t.tunnels.deselectAll || 'Deselect') : (t.tunnels.selectAll || 'Select')}
                          className="shrink-0 -m-2.5 p-2.5 min-w-[44px] min-h-[44px] flex items-center justify-center cursor-pointer disabled:opacity-40"
                        >
                          <span className={`w-5 h-5 rounded-lg border flex items-center justify-center transition-all ${
                            selectedTunnelIds.has(tunnel.id)
                              ? 'bg-indigo-600 border-indigo-600 text-white shadow-xs scale-105'
                              : 'border-slate-300 dark:border-white/20 hover:border-indigo-400 bg-white dark:bg-[#161c28]'
                          }`}>
                            {selectedTunnelIds.has(tunnel.id) && (
                              <CheckCircle2 size={13} className="fill-current text-white" />
                            )}
                          </span>
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
                            className="px-1.5 py-0.5 rounded-md text-[10px] font-mono font-bold tracking-tight bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/30 text-center animate-pulse shrink-0"
                            title={isRTL ? "پیکربندی تغییر کرده است. برای اعمال روی «اعمال مجدد» کلیک کنید." : "Configuration modified. Click Reapply to apply changes to live core."}
                          >
                            {t.tunnels.pendingChanges || 'Pending'}
                          </span>
                        )}
                      </div>

                      {/* Latency on mobile top right (top left in RTL) */}
                      <LatencyBadge latency={tunnel.spec?.latency_ms} status={tunnel.status} />
                    </div>

                    <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-3 sm:gap-4">
                      <div className="flex items-start gap-3 sm:gap-4 flex-1 min-w-0">
                        {/* Desktop Multi-Selection Checkbox & Status Badge (hidden on mobile) */}
                        <div className="hidden sm:flex items-start gap-3 shrink-0 pt-0.5">
                          <button
                            type="button"
                            onClick={(e) => toggleSelectTunnel(tunnel.id, e)}
                            disabled={isReapplying || isDeleting}
                            aria-label={selectedTunnelIds.has(tunnel.id) ? (t.tunnels.deselectAll || 'Deselect') : (t.tunnels.selectAll || 'Select')}
                            className={`w-5 h-5 rounded-lg border flex items-center justify-center transition-all disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer ${
                              selectedTunnelIds.has(tunnel.id)
                                ? 'bg-indigo-600 border-indigo-600 text-white shadow-xs scale-105'
                                : 'border-slate-300 dark:border-white/20 hover:border-indigo-400 dark:hover:border-indigo-500 bg-white dark:bg-[#161c28]'
                            }`}
                            title={selectedTunnelIds.has(tunnel.id) ? (t.tunnels.deselectAll || 'Deselect') : (t.tunnels.selectAll || 'Select')}
                          >
                            {selectedTunnelIds.has(tunnel.id) && (
                              <CheckCircle2 size={14} className="fill-current text-white" />
                            )}
                          </button>

                          <div className="flex flex-col gap-1.5 shrink-0">
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
                                title={isRTL ? "پیکربندی تغییر کرده است. برای اعمال روی «اعمال مجدد» کلیک کنید." : "Configuration modified. Click Reapply to apply changes to live core."}
                              >
                                {t.tunnels.pendingChanges || 'Pending'}
                              </span>
                            )}
                          </div>
                        </div>

                        <div className="flex-1 min-w-0 space-y-2 sm:space-y-2.5">
                          {/* Tunnel Name (Full width on mobile!) */}
                          <h3 className="text-sm sm:text-base font-bold text-slate-900 dark:text-white truncate" title={tunnel.name}>
                            {tunnel.name}
                          </h3>

                          {/* Badges row: Core, Transmission, Category, Ports, and on desktop: Latency */}
                          <div className="flex items-center gap-1.5 sm:gap-2 flex-wrap">
                            <span
                              className={`px-2 sm:px-2.5 py-0.5 rounded-md sm:rounded-lg text-[11px] sm:text-xs font-mono font-bold uppercase tracking-wide border ${coreBadge.bg} ${coreBadge.text} ${coreBadge.border} shrink-0`}
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
                                className={`px-2 sm:px-2.5 py-0.5 rounded-md sm:rounded-lg text-[11px] sm:text-xs font-semibold flex items-center gap-1 border shrink-0 transition-all hover:scale-105 active:scale-95 cursor-pointer ${
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

                            {transmissionType && transmissionBadge && (
                              <span
                                className={`px-2 sm:px-2.5 py-0.5 rounded-md sm:rounded-lg text-[11px] sm:text-xs font-mono font-bold uppercase tracking-wide border ${transmissionBadge.bg} ${transmissionBadge.text} ${transmissionBadge.border} shrink-0`}
                              >
                                {transmissionType}
                              </span>
                            )}

                            <div className="flex items-center gap-1 px-2 py-0.5 rounded-md bg-slate-100 dark:bg-white/[0.04] border border-slate-200/60 dark:border-white/[0.06] text-[11px] sm:text-xs">
                              <span className="text-slate-400 dark:text-slate-500 font-medium">{t.tunnels.portsLabel || 'Ports:'}</span>
                              <span className="font-mono font-bold text-slate-800 dark:text-slate-200 tabular-nums max-w-[140px] sm:max-w-xs truncate" title={ports}>{ports}</span>
                            </div>

                            {/* Latency on desktop */}
                            <div className="hidden sm:inline-flex">
                              <LatencyBadge latency={tunnel.spec?.latency_ms} status={tunnel.status} />
                            </div>
                          </div>

                          {/* Node & Server Route info */}
                          {(iranNode || foreignServer || corePort) && (
                            <div className="flex items-center gap-2 sm:gap-3 text-[11px] sm:text-xs text-slate-500 dark:text-slate-400 flex-wrap pt-0.5">
                              {corePort && (
                                <div className="flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-slate-100/60 dark:bg-white/[0.03] border border-slate-200/50 dark:border-white/[0.05]">
                                  <span className="font-medium text-slate-400">{t.tunnels.corePortLabel || 'Core Port:'}</span>
                                  <span className="text-slate-700 dark:text-slate-300 font-mono font-semibold tabular-nums">{corePort}</span>
                                </div>
                              )}
                              {iranNode && (
                                <div className="flex items-center gap-1 px-2 py-0.5 rounded-md bg-slate-100 dark:bg-white/[0.04] border border-slate-200/60 dark:border-white/[0.05] max-w-[170px] truncate" title={isRTL ? `نود ایران: ${iranNode.name || iranNode.id}` : `Iran Node: ${iranNode.name || iranNode.id}`}>
                                  <span className="text-slate-400 text-[10px] uppercase font-mono font-bold shrink-0">{t.tunnels.irNodeLabel || 'IR'}:</span>
                                  <span className="text-slate-700 dark:text-slate-300 font-semibold truncate">{iranNode.name || iranNode.id.substring(0, 8)}</span>
                                </div>
                              )}
                              {iranNode && foreignServer && (
                                <ArrowRight size={12} className="text-slate-400 shrink-0 rtl:rotate-180" />
                              )}
                              {foreignServer && (
                                <div className="flex items-center gap-1 px-2 py-0.5 rounded-md bg-slate-100 dark:bg-white/[0.04] border border-slate-200/60 dark:border-white/[0.05] max-w-[170px] truncate" title={isRTL ? `سرور خارج: ${foreignServer.name || foreignServer.id}` : `Foreign Server: ${foreignServer.name || foreignServer.id}`}>
                                  <span className="text-slate-400 text-[10px] uppercase font-mono font-bold shrink-0">{t.tunnels.extServerLabel || 'EXT'}:</span>
                                  <span className="text-slate-700 dark:text-slate-300 font-semibold truncate">{foreignServer.name || foreignServer.id.substring(0, 8)}</span>
                                </div>
                              )}
                            </div>
                          )}

                          {/* Error Message */}
                          {tunnel.status === 'error' && tunnel.error_message && (
                            <div className="mt-2 text-xs text-rose-600 dark:text-rose-400 p-2.5 sm:p-3 bg-rose-500/10 border border-rose-500/20 rounded-xl leading-relaxed">
                              {tunnel.error_message}
                            </div>
                          )}
                        </div>
                      </div>

                      {/* Desktop Action Buttons */}
                      <div className="hidden sm:flex items-center gap-1.5 shrink-0 self-start">
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); handleTestActiveTunnel(tunnel); }}
                          disabled={isReapplying || isDeleting || isTesting}
                          className="p-2.5 text-amber-600 dark:text-amber-400 hover:bg-amber-500/10 border border-transparent hover:border-amber-500/20 rounded-xl transition-all disabled:opacity-40 min-w-[40px] min-h-[40px] flex items-center justify-center active:scale-95 cursor-pointer"
                          title={t.tunnels.testLiveConnection || 'Test Ping'}
                          aria-label={t.tunnels.testLiveConnection || 'Test Ping'}
                        >
                          {isTesting ? (
                            <Loader2 size={18} className="animate-spin text-amber-500" />
                          ) : (
                            <Zap size={18} />
                          )}
                        </button>
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); reapplyTunnel(tunnel); }}
                          disabled={isReapplying || isDeleting || !!reapplyingTunnelId}
                          className={`p-2.5 rounded-xl transition-all disabled:opacity-40 disabled:cursor-not-allowed min-w-[40px] min-h-[40px] flex items-center justify-center active:scale-95 cursor-pointer ${
                            Boolean(tunnel.spec?._pending_reapply)
                              ? 'text-amber-600 dark:text-amber-400 bg-amber-500/15 border border-amber-500/30 ring-2 ring-amber-500/20 shadow-xs hover:bg-amber-500/25'
                              : 'text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/20'
                          }`}
                          title={Boolean(tunnel.spec?._pending_reapply) ? (isRTL ? 'اعمال تغییرات روی هسته' : 'Reapply pending changes to core') : (t.tunnels.reapplyTunnel || 'Reapply tunnel')}
                          aria-label={t.tunnels.reapplyTunnel || 'Reapply tunnel'}
                        >
                          {isReapplying ? (
                            <Loader2 size={18} className="animate-spin" />
                          ) : (
                            <RotateCw size={18} className={Boolean(tunnel.spec?._pending_reapply) ? 'text-amber-600 dark:text-amber-400' : ''} />
                          )}
                        </button>
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); setEditingTunnel(tunnel); }}
                          disabled={isReapplying || isDeleting}
                          className="p-2.5 text-slate-600 dark:text-slate-300 hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-indigo-500/10 border border-transparent hover:border-indigo-500/20 rounded-xl transition-all disabled:opacity-40 min-w-[40px] min-h-[40px] flex items-center justify-center active:scale-95 cursor-pointer"
                          title={t.tunnels.editTunnel || 'Edit tunnel'}
                          aria-label={t.tunnels.editTunnel || 'Edit tunnel'}
                        >
                          <Edit2 size={18} />
                        </button>
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); deleteTunnel(tunnel.id); }}
                          disabled={isReapplying || isDeleting}
                          className="p-2.5 text-slate-600 dark:text-slate-300 hover:text-rose-600 dark:hover:text-rose-400 hover:bg-rose-500/10 border border-transparent hover:border-rose-500/20 rounded-xl transition-all disabled:opacity-40 min-w-[40px] min-h-[40px] flex items-center justify-center active:scale-95 cursor-pointer"
                          title={isDeleting ? (isRTL ? 'در حال حذف تونل...' : 'Deleting tunnel...') : (t.tunnels.deleteTunnel || 'Delete tunnel')}
                          aria-label={t.tunnels.deleteTunnel || 'Delete tunnel'}
                        >
                          {isDeleting ? (
                            <Loader2 size={18} className="animate-spin text-rose-500" />
                          ) : (
                            <Trash2 size={18} />
                          )}
                        </button>
                      </div>
                    </div>

                    {/* ── Mobile Action Buttons Bar (grid of 4 comfortable 44px touch buttons) ── */}
                    <div className="grid grid-cols-4 gap-2 pt-2.5 mt-2.5 border-t border-slate-100 dark:border-white/[0.06] sm:hidden">
                      {/* Test Connection / Ping */}
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); handleTestActiveTunnel(tunnel); }}
                        disabled={isReapplying || isDeleting || isTesting}
                        className="h-11 rounded-xl bg-slate-100/80 dark:bg-white/[0.04] hover:bg-amber-500/15 text-amber-600 dark:text-amber-400 border border-slate-200/60 dark:border-white/[0.06] flex items-center justify-center transition-all disabled:opacity-40 active:scale-95 cursor-pointer"
                        title={t.tunnels.testLiveConnection || 'Test Ping'}
                        aria-label={t.tunnels.testLiveConnection || 'Test Ping'}
                      >
                        {isTesting ? (
                          <Loader2 size={18} className="animate-spin text-amber-500" />
                        ) : (
                          <Zap size={18} />
                        )}
                      </button>

                      {/* Reapply Tunnel */}
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); reapplyTunnel(tunnel); }}
                        disabled={isReapplying || isDeleting || !!reapplyingTunnelId}
                        className={`h-11 rounded-xl flex items-center justify-center transition-all disabled:opacity-40 active:scale-95 cursor-pointer border ${
                          Boolean(tunnel.spec?._pending_reapply)
                            ? 'text-amber-600 dark:text-amber-400 bg-amber-500/15 border-amber-500/30 ring-2 ring-amber-500/20 shadow-xs'
                            : 'text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 hover:bg-emerald-500/20 border-emerald-500/20'
                        }`}
                        title={Boolean(tunnel.spec?._pending_reapply) ? (isRTL ? 'اعمال تغییرات روی هسته' : 'Reapply pending changes to core') : (t.tunnels.reapplyTunnel || 'Reapply tunnel')}
                        aria-label={t.tunnels.reapplyTunnel || 'Reapply tunnel'}
                      >
                        {isReapplying ? (
                          <Loader2 size={18} className="animate-spin" />
                        ) : (
                          <RotateCw size={18} className={Boolean(tunnel.spec?._pending_reapply) ? 'text-amber-600 dark:text-amber-400' : ''} />
                        )}
                      </button>

                      {/* Edit Tunnel */}
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); setEditingTunnel(tunnel); }}
                        disabled={isReapplying || isDeleting}
                        className="h-11 rounded-xl bg-slate-100/80 dark:bg-white/[0.04] hover:bg-indigo-500/15 text-slate-600 dark:text-slate-300 hover:text-indigo-600 dark:hover:text-indigo-400 border border-slate-200/60 dark:border-white/[0.06] flex items-center justify-center transition-all disabled:opacity-40 active:scale-95 cursor-pointer"
                        title={t.tunnels.editTunnel || 'Edit tunnel'}
                        aria-label={t.tunnels.editTunnel || 'Edit tunnel'}
                      >
                        <Edit2 size={18} />
                      </button>

                      {/* Delete Tunnel */}
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); deleteTunnel(tunnel.id); }}
                        disabled={isReapplying || isDeleting}
                        className="h-11 rounded-xl bg-slate-100/80 dark:bg-white/[0.04] hover:bg-rose-500/15 text-slate-600 dark:text-slate-300 hover:text-rose-600 dark:hover:text-rose-400 border border-slate-200/60 dark:border-white/[0.06] flex items-center justify-center transition-all disabled:opacity-40 active:scale-95 cursor-pointer"
                        title={isDeleting ? (isRTL ? 'در حال حذف تونل...' : 'Deleting tunnel...') : (t.tunnels.deleteTunnel || 'Delete tunnel')}
                        aria-label={t.tunnels.deleteTunnel || 'Delete tunnel'}
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
      </div>

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
                type="button"
                onClick={() => setShowConfirmReapplyAll(false)}
                className="min-h-[44px] px-5 py-2.5 text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 rounded-xl transition-colors cursor-pointer"
              >
                {t.tunnels.cancel || 'Cancel'}
              </button>
              <button
                type="button"
                onClick={startReapplyAll}
                className="min-h-[44px] px-5 py-2.5 text-xs sm:text-sm font-semibold text-white bg-green-600 hover:bg-green-700 rounded-xl transition-colors cursor-pointer shadow-xs active:scale-95"
              >
                {isRTL ? 'بله، اعمال مجدد همه' : 'Yes, Reapply All'}
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
                    {reapplyAllDone ? (isRTL ? 'اعمال مجدد انجام شد' : 'Reapply Complete') : (isRTL ? 'در حال اعمال تونل‌ها...' : 'Applying Tunnels...')}
                  </h3>
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 font-medium">
                    {isRTL 
                      ? `${reapplyAllProgress.filter(i => i.status === 'success' || i.status === 'error').length} از ${reapplyAllProgress.length} تونل پردازش شد`
                      : `${reapplyAllProgress.filter(i => i.status === 'success' || i.status === 'error').length} of ${reapplyAllProgress.length} tunnels processed`}
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
                      <p className="text-xs text-blue-600 dark:text-blue-400 mt-0.5 animate-pulse">
                        {isRTL ? 'در حال اعمال پیکربندی...' : 'Applying configuration...'}
                      </p>
                    )}
                  </div>

                  {/* Status badge */}
                  <span className={`text-[11px] font-bold px-2 py-0.5 rounded-full shrink-0 tracking-wide ${
                    item.status === 'pending' ? 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400' :
                    item.status === 'running' ? 'bg-blue-100 dark:bg-blue-900/60 text-blue-700 dark:text-blue-300' :
                    item.status === 'success' ? 'bg-emerald-100 dark:bg-emerald-900/60 text-emerald-700 dark:text-emerald-300' :
                    'bg-red-100 dark:bg-red-900/60 text-red-700 dark:text-red-300'
                  }`}>
                    {item.status === 'pending' ? (isRTL ? 'در انتظار' : 'Waiting') :
                     item.status === 'running' ? (isRTL ? 'در حال اجرا' : 'Running') :
                     item.status === 'success' ? (isRTL ? 'موفق' : 'Done') : (isRTL ? 'ناموفق' : 'Failed')}
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
                    {isRTL ? `${reapplyAllProgress.filter(i => i.status === 'success').length} موفق` : `${reapplyAllProgress.filter(i => i.status === 'success').length} succeeded`}
                  </span>
                  {reapplyAllProgress.filter(i => i.status === 'error').length > 0 && (
                    <span className="flex items-center gap-1.5 text-red-600 dark:text-red-400">
                      <XCircle size={15} />
                      {isRTL ? `${reapplyAllProgress.filter(i => i.status === 'error').length} ناموفق` : `${reapplyAllProgress.filter(i => i.status === 'error').length} failed`}
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={closeReapplyAllModal}
                  className="w-full px-4 py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 text-white rounded-xl hover:from-blue-700 hover:to-indigo-700 transition-all font-medium text-sm shadow-md hover:shadow-lg active:scale-[0.99] cursor-pointer"
                >
                  {isRTL ? 'تکمیل' : 'Done'}
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
                className="min-h-[44px] px-5 py-2.5 text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 rounded-xl transition-colors cursor-pointer"
              >
                {t.tunnels.cancel || 'Cancel'}
              </button>
              <button
                type="button"
                onClick={startReapplySelected}
                className="min-h-[44px] px-5 py-2.5 text-xs sm:text-sm font-semibold text-white bg-emerald-600 hover:bg-emerald-700 rounded-xl transition-colors flex items-center justify-center gap-1.5 shadow-xs active:scale-95 cursor-pointer"
              >
                <RotateCw size={15} />
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
                className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 p-2 min-w-[36px] min-h-[36px] flex items-center justify-center cursor-pointer"
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
                  className="w-full px-3.5 py-2.5 text-[16px] sm:text-sm rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500"
                  autoFocus
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-2">
                  {isRTL ? 'رنگ برچسب' : 'Color Tag'}
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
                        className={`min-h-[38px] px-3.5 py-2 rounded-xl text-xs font-semibold border transition-all cursor-pointer flex items-center justify-center ${
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
                  className="min-h-[44px] px-5 py-2.5 text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 rounded-xl cursor-pointer"
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
                  className="min-h-[44px] px-5 py-2.5 text-xs sm:text-sm font-semibold text-white bg-blue-600 hover:bg-blue-700 rounded-xl disabled:opacity-50 cursor-pointer shadow-xs active:scale-95"
                >
                  {isRTL ? 'ایجاد' : 'Create'}
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
                className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 p-2 min-w-[36px] min-h-[36px] flex items-center justify-center cursor-pointer"
              >
                <X size={18} />
              </button>
            </div>

            <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">
              {isRTL ? `یک دسته‌بندی برای ${selectedTunnelIds.size} تونل انتخاب‌شده تعیین کنید:` : `Select a category for ${selectedTunnelIds.size} selected tunnel(s):`}
            </p>

            <div className="space-y-2 max-h-60 overflow-y-auto pr-1">
              {/* Option to clear category (Uncategorized) */}
              <button
                type="button"
                onClick={() => bulkAssignCategory(null)}
                className="w-full text-start p-3 rounded-xl border border-dashed border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700/50 flex items-center justify-between text-xs font-semibold text-gray-600 dark:text-gray-300 cursor-pointer"
              >
                <span>{t.tunnels.uncategorized || 'None (Uncategorized)'}</span>
                <span className="text-[10px] text-gray-400">{isRTL ? 'حذف دسته‌بندی' : 'Clear category'}</span>
              </button>

              {/* List of existing categories */}
              {categories.map((cat) => {
                const colorStyle = getCategoryColorClasses(cat.color)
                return (
                  <button
                    key={cat.id}
                    type="button"
                    onClick={() => bulkAssignCategory(cat.name)}
                    className="w-full text-start p-3 rounded-xl border border-gray-200 dark:border-gray-700 hover:border-blue-400 dark:hover:border-blue-500 hover:bg-blue-50/50 dark:hover:bg-blue-950/20 flex items-center justify-between transition-all cursor-pointer"
                  >
                    <div className="flex items-center gap-2">
                      <span className={`w-3 h-3 rounded-full ${colorStyle.activeBg.split(' ')[0]}`}></span>
                      <span className="text-xs font-semibold text-gray-800 dark:text-gray-200">{cat.name}</span>
                    </div>
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${colorStyle.bg} ${colorStyle.text}`}>
                      {tunnels.filter(t => t.category === cat.name).length} {isRTL ? 'تونل' : 'tunnels'}
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
                className="min-h-[44px] text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline flex items-center gap-1.5 cursor-pointer"
              >
                <FolderPlus size={16} />
                <span>{isRTL ? '+ ایجاد دسته‌بندی جدید' : '+ Create new category'}</span>
              </button>
              <button
                type="button"
                onClick={() => setShowAssignCategoryModal(false)}
                className="min-h-[44px] px-5 py-2.5 text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 rounded-xl cursor-pointer"
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

export default Tunnels
