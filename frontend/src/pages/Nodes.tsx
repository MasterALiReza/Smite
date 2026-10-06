import { useEffect, useState } from 'react'
import { Plus, Copy, Trash2, CheckCircle, XCircle, Download, AlertCircle, Server, Sparkles, Edit2, Loader2, X } from 'lucide-react'
import api from '../api/client'
import { useLanguage } from '../contexts/LanguageContext'
import { useToast } from '../contexts/ToastContext'
import { EmptyState } from '../components/EmptyState'
import { copyTextToClipboard } from '../utils/clipboard'
import { JoinModal } from '../components/JoinModal'
import { EditNodeModal } from '../components/EditNodeModal'
import { LatencyBadge } from '../components/LatencyBadge'
import { getCountryFlag, formatLocalizedNodeName, extractCountryCode } from '../utils/country'

interface Node {
  id: string
  name: string
  fingerprint: string
  status: string
  registered_at: string
  last_seen: string
  metadata: Record<string, any>
}

let _cachedIranNodes: Node[] = []

const Nodes = () => {
  const { t, language } = useLanguage()
  const { showToast, showConfirm } = useToast()
  const [nodes, setNodes] = useState<Node[]>(_cachedIranNodes)
  const [loading, setLoading] = useState(_cachedIranNodes.length === 0)
  const [deletingNodeId, setDeletingNodeId] = useState<string | null>(null)
  const [showAddModal, setShowAddModal] = useState(false)
  const [showCertModal, setShowCertModal] = useState(false)
  const [showJoinModal, setShowJoinModal] = useState(false)
  const [editingNode, setEditingNode] = useState<Node | null>(null)
  const [certContent, setCertContent] = useState<string>('')
  const [certLoading, setCertLoading] = useState(false)
  const [copiedFingerprintId, setCopiedFingerprintId] = useState<string | null>(null)
  const [certCopied, setCertCopied] = useState(false)

  useEffect(() => {
    fetchNodes()
    const interval = setInterval(fetchNodes, 6000)
    const params = new URLSearchParams(window.location.search)
    if (params.get('add') === 'true') {
      setShowAddModal(true)
      window.history.replaceState({}, '', '/nodes')
    }
    return () => clearInterval(interval)
  }, [])

  const fetchNodes = async () => {
    try {
      const response = await api.get('/nodes')
      // Filter only iran nodes (exclude foreign servers)
      const iranNodes = response.data.filter((node: Node) => 
        node.metadata?.role !== 'foreign' && (node.metadata?.role === 'iran' || !node.metadata?.role)
      )
      _cachedIranNodes = iranNodes
      setNodes(iranNodes)
    } catch (error) {
      console.error('Failed to fetch nodes:', error)
    } finally {
      setLoading(false)
    }
  }

  const copyToClipboard = async (text: string, nodeId: string) => {
    const success = await copyTextToClipboard(text)
    if (success) {
      setCopiedFingerprintId(nodeId)
      showToast('success', 'Copied', 'Fingerprint copied to clipboard', 2000)
      setTimeout(() => setCopiedFingerprintId(null), 2000)
    } else {
      showToast('error', 'Error', 'Failed to copy to clipboard')
    }
  }

  const showCA = async () => {
    setShowCertModal(true)
    setCertLoading(true)
    try {
      const response = await api.get('/panel/ca', {
        responseType: 'text',
        headers: {
          'Accept': 'text/plain'
        }
      })
      const text = response.data
      if (!text || text.trim().length === 0) {
        throw new Error('Certificate is empty. Make sure the panel has generated it.')
      }
      setCertContent(text)
    } catch (error: any) {
      console.error('Failed to fetch CA:', error)
      const errorMessage = error.response?.data?.detail || error.message || 'Failed to fetch CA certificate'
      showToast('error', 'Error', `Failed to fetch CA certificate: ${errorMessage}`)
      setShowCertModal(false)
    } finally {
      setCertLoading(false)
    }
  }

  const downloadCA = async () => {
    try {
      const response = await api.get('/panel/ca?download=true', { responseType: 'blob' })
      const url = window.URL.createObjectURL(new Blob([response.data]))
      const link = document.createElement('a')
      link.href = url
      link.setAttribute('download', 'ca.crt')
      document.body.appendChild(link)
      link.click()
      link.remove()
      showToast('success', 'Downloaded', 'CA certificate downloaded')
    } catch (error) {
      console.error('Failed to download CA:', error)
      showToast('error', 'Error', 'Failed to download CA certificate')
    }
  }

  const deleteNode = async (id: string) => {
    const target = nodes.find(n => n.id === id)
    const targetName = target?.name || 'this Iran node'

    // Check linked tunnels count
    let linkedCount = 0
    try {
      const tunnelsRes = await api.get('/tunnels')
      const linked = (tunnelsRes.data || []).filter((t: any) => 
        t.iran_node_id === id || t.foreign_node_id === id || t.node_id === id
      )
      linkedCount = linked.length
    } catch {
      // fallback
    }

    const confirmMsg = linkedCount > 0
      ? `Are you sure you want to delete "${targetName}"? This node has ${linkedCount} active tunnel(s). All linked tunnels and their ports will be closed on both servers, and the remote Docker container will be decommissioned.`
      : `Are you sure you want to delete "${targetName}"? The remote Docker container and all running services will be cleanly decommissioned.`

    const confirmed = await showConfirm({
      title: 'Delete Node & Decommission',
      message: confirmMsg,
      variant: 'danger',
      confirmText: 'Delete & Decommission'
    })
    if (!confirmed) return
    
    setDeletingNodeId(id)
    showToast('info', 'Decommissioning Node', `Cleaning up tunnels and removing "${targetName}"...`, 3000)

    try {
      const resp = await api.delete(`/nodes/${id}`)
      setNodes(prev => prev.filter(n => n.id !== id))
      
      const decomRemote = resp.data?.decommissioned_remote
      if (decomRemote) {
        showToast('success', 'Decommissioned', `Node "${targetName}" and its remote container were decommissioned cleanly.`, 4000)
      } else {
        showToast('warning', 'Deleted (Node Offline)', `Node "${targetName}" removed from panel. Remote server was offline; to clean up Docker manually, run: docker stop smite-node && docker rm -f smite-node`, 6000)
      }
      fetchNodes()
    } catch (error: any) {
      console.error('Failed to delete node:', error)
      const errorMsg = error.response?.data?.detail || error.message || 'Failed to delete node'
      showToast('error', 'Delete Failed', errorMsg)
      fetchNodes()
    } finally {
      setDeletingNodeId(null)
    }
  }

  if (loading && nodes.length === 0) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center">
          <div className="inline-block animate-spin rounded-full h-10 w-10 border-2 border-sky-500 border-t-transparent mb-4"></div>
          <p className="text-xs font-mono text-slate-500 dark:text-slate-400">Loading nodes...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="w-full max-w-7xl mx-auto space-y-6 font-sans">
      {/* Header & Actions */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-2 border-b border-slate-200/60 dark:border-white/[0.05]">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-2xl sm:text-3xl font-extrabold tracking-tight text-slate-900 dark:text-white">
              {t.nodes.title}
            </h1>
            <span className="px-2.5 py-0.5 rounded-full text-xs font-mono font-bold bg-blue-500/10 text-blue-600 dark:text-sky-400 border border-blue-500/20">
              {nodes.length}
            </span>
          </div>
          <p className="text-xs sm:text-sm text-slate-500 dark:text-slate-400 mt-0.5 font-medium">{t.nodes.subtitle}</p>
        </div>

        <div className="flex items-center gap-2 sm:gap-2.5 flex-wrap">
          <button
            onClick={() => setShowJoinModal(true)}
            className="group px-3.5 py-2.5 bg-gradient-to-r from-purple-600 via-indigo-600 to-sky-600 hover:from-purple-500 hover:to-sky-500 text-white rounded-xl transition-all duration-200 font-semibold shadow-md shadow-purple-600/20 flex items-center justify-center gap-2 text-xs sm:text-sm min-h-[44px] active:scale-95 cursor-pointer"
            title="One-Click Automatic Node Join Command"
            aria-label="One-Click Automatic Node Join Command"
          >
            <Sparkles size={16} className="text-purple-200 group-hover:rotate-12 transition-transform" />
            <span>Auto Join</span>
          </button>
          
          <button
            onClick={showCA}
            className="px-3.5 py-2.5 bg-white dark:bg-white/[0.04] hover:bg-slate-100 dark:hover:bg-white/[0.08] text-slate-700 dark:text-slate-300 rounded-xl transition-all font-semibold border border-slate-200/80 dark:border-white/[0.08] shadow-2xs flex items-center justify-center gap-1.5 text-xs sm:text-sm min-h-[44px] active:scale-95 cursor-pointer"
            aria-label={t.nodes.viewCACertificate}
          >
            <Copy size={15} className="text-sky-500" />
            <span>{t.nodes.viewCACertificate}</span>
          </button>

          <button
            onClick={downloadCA}
            className="p-2.5 bg-white dark:bg-white/[0.04] hover:bg-slate-100 dark:hover:bg-white/[0.08] text-slate-700 dark:text-slate-300 rounded-xl transition-all font-semibold border border-slate-200/80 dark:border-white/[0.08] shadow-2xs flex items-center justify-center min-h-[44px] min-w-[44px] active:scale-95 cursor-pointer"
            title={t.nodes.downloadCA}
            aria-label={t.nodes.downloadCA}
          >
            <Download size={16} />
          </button>

          <button
            onClick={() => setShowAddModal(true)}
            className="px-4 py-2.5 bg-gradient-to-r from-blue-600 via-sky-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white rounded-xl transition-all duration-200 font-semibold shadow-md shadow-blue-600/20 flex items-center justify-center gap-2 text-xs sm:text-sm min-h-[44px] active:scale-95 cursor-pointer"
            aria-label={t.nodes.addNode}
          >
            <Plus size={17} />
            <span>{t.nodes.addNode}</span>
          </button>
        </div>
      </div>

      {/* Desktop Table View */}
      <div className="hidden lg:block bg-white/90 dark:bg-[#0c1220]/90 rounded-3xl border border-slate-200/80 dark:border-white/[0.08] overflow-hidden shadow-sm backdrop-blur-xl">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-slate-50/80 dark:bg-white/[0.02] border-b border-slate-200/70 dark:border-white/[0.06]">
              <tr>
                <th className="px-6 py-3.5 text-start text-[11px] font-mono font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                  Name & Flag
                </th>
                <th className="px-6 py-3.5 text-start text-[11px] font-mono font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                  Fingerprint
                </th>
                <th className="px-6 py-3.5 text-start text-[11px] font-mono font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                  Status
                </th>
                <th className="px-6 py-3.5 text-start text-[11px] font-mono font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                  Ping
                </th>
                <th className="px-6 py-3.5 text-start text-[11px] font-mono font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                  IP & Port
                </th>
                <th className="px-6 py-3.5 text-start text-[11px] font-mono font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                  Last Seen
                </th>
                <th className="px-6 py-3.5 text-end text-[11px] font-mono font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-white/[0.04]">
              {nodes.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-6 py-8">
                    <EmptyState 
                      icon={<Server size={32} className="text-slate-400" />} 
                      title="No Iran nodes" 
                      description="Add an Iran node or run the 1-click Auto Join command to get started." 
                      action={{ label: 'Auto Join Command', onClick: () => setShowJoinModal(true) }} 
                    />
                  </td>
                </tr>
              ) : (
                nodes.map((node) => {
                  const cc = extractCountryCode(node.name, node.metadata?.country_code, 'iran') || 'IR'
                  const isCopied = copiedFingerprintId === node.id
                  return (
                    <tr key={node.id} className="hover:bg-slate-50/70 dark:hover:bg-white/[0.02] transition-colors">
                      {/* Name & Flag */}
                      <td className="px-6 py-4 whitespace-nowrap">
                        <div className="flex items-center gap-3">
                          <div className="flex items-center gap-1.5 px-2 py-1 bg-slate-100 dark:bg-white/[0.05] rounded-xl border border-slate-200/60 dark:border-white/[0.08] shadow-2xs">
                            <img
                              src={`https://purecatamphetamine.github.io/country-flag-icons/3x2/${cc}.svg`}
                              alt={cc}
                              className="w-4 h-3 object-cover rounded-xs"
                              onError={(e) => {
                                (e.target as HTMLElement).style.display = 'none'
                              }}
                            />
                            <span className="text-[10px] font-mono font-bold text-slate-700 dark:text-slate-300">{cc}</span>
                          </div>
                          <div className="flex flex-col gap-0.5">
                            <span className="text-sm font-bold text-slate-900 dark:text-white tracking-tight">
                              {formatLocalizedNodeName(node.name, language === 'fa', cc)}
                            </span>
                            <span className="text-[11px] text-slate-400 dark:text-slate-500 font-mono">
                              ID: {node.id.substring(0, 8)}...
                            </span>
                          </div>
                        </div>
                      </td>

                      {/* Fingerprint */}
                      <td className="px-6 py-4 whitespace-nowrap">
                        <button
                          onClick={() => copyToClipboard(node.fingerprint, node.id)}
                          className="group inline-flex items-center gap-2 px-2.5 py-1 rounded-xl bg-slate-100/80 dark:bg-white/[0.04] hover:bg-slate-200/80 dark:hover:bg-white/[0.08] border border-slate-200/60 dark:border-white/[0.06] transition-all cursor-pointer"
                          title="Click to copy fingerprint"
                        >
                          <span className="font-mono text-xs text-slate-600 dark:text-slate-300">
                            {node.fingerprint ? `${node.fingerprint.substring(0, 10)}...${node.fingerprint.substring(node.fingerprint.length - 6)}` : 'N/A'}
                          </span>
                          {isCopied ? (
                            <CheckCircle size={13} className="text-emerald-500" />
                          ) : (
                            <Copy size={13} className="text-slate-400 group-hover:text-sky-500 transition-colors" />
                          )}
                        </button>
                      </td>

                      {/* Status */}
                      <td className="px-6 py-4 whitespace-nowrap">
                        {renderConnectionStatusBadge(node.metadata?.connection_status || 'failed')}
                      </td>

                      {/* Ping */}
                      <td className="px-6 py-4 whitespace-nowrap">
                        <LatencyBadge
                          latency={node.metadata?.latency_ms}
                          status={node.metadata?.connection_status || 'failed'}
                        />
                      </td>

                      {/* IP & Port */}
                      <td className="px-6 py-4 whitespace-nowrap font-mono text-xs">
                        <div className="text-slate-800 dark:text-slate-200 font-bold tabular-nums">
                          {node.metadata?.ip_address || 'N/A'}
                        </div>
                        <div className="text-slate-400 dark:text-slate-500 text-[11px]">
                          Port: {node.metadata?.api_port || '8888'}
                        </div>
                      </td>

                      {/* Last Seen */}
                      <td className="px-6 py-4 whitespace-nowrap text-xs text-slate-500 dark:text-slate-400 font-mono">
                        {node.last_seen ? new Date(node.last_seen).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'Never'}
                      </td>

                      {/* Actions */}
                      <td className="px-6 py-4 whitespace-nowrap text-end">
                        <div className="flex items-center justify-end gap-1.5">
                          <button
                            type="button"
                            onClick={() => setEditingNode(node)}
                            disabled={deletingNodeId === node.id}
                            className="p-2.5 min-h-[40px] min-w-[40px] text-slate-600 dark:text-slate-300 hover:text-sky-500 dark:hover:text-sky-400 hover:bg-slate-100 dark:hover:bg-white/[0.08] rounded-xl transition-all cursor-pointer disabled:opacity-50 flex items-center justify-center active:scale-95"
                            title="Edit Node Name"
                            aria-label="Edit Node Name"
                          >
                            <Edit2 size={16} />
                          </button>
                          <button
                            type="button"
                            onClick={() => deleteNode(node.id)}
                            disabled={deletingNodeId === node.id}
                            className="p-2.5 min-h-[40px] min-w-[40px] text-slate-400 hover:text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/30 rounded-xl transition-all cursor-pointer disabled:opacity-50 flex items-center justify-center active:scale-95"
                            title={deletingNodeId === node.id ? "Deleting..." : "Delete Node"}
                            aria-label="Delete Node"
                          >
                            {deletingNodeId === node.id ? (
                              <Loader2 size={16} className="animate-spin text-rose-500" />
                            ) : (
                              <Trash2 size={16} />
                            )}
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Mobile Card View (< 1024px) */}
      <div className="lg:hidden space-y-3.5">
        {nodes.length === 0 ? (
          <div className="bg-white/90 dark:bg-[#0c1220]/90 rounded-3xl p-6 border border-slate-200/80 dark:border-white/[0.08]">
            <EmptyState 
              icon={<Server size={32} className="text-slate-400" />} 
              title="No Iran nodes" 
              description="Add an Iran node or use Auto Join to get started." 
              action={{ label: 'Auto Join Command', onClick: () => setShowJoinModal(true) }} 
            />
          </div>
        ) : (
          nodes.map((node) => {
            const cc = extractCountryCode(node.name, node.metadata?.country_code, 'iran') || 'IR'
            const isCopied = copiedFingerprintId === node.id
            return (
              <div 
                key={node.id} 
                className="bg-white/90 dark:bg-[#0c1220]/90 rounded-2xl border border-slate-200/80 dark:border-white/[0.08] p-4 shadow-sm space-y-3.5 backdrop-blur-xl"
              >
                {/* Header: Flag, Name, Status */}
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <div className="flex items-center gap-1 px-2 py-0.5 bg-slate-100 dark:bg-white/[0.05] rounded-lg border border-slate-200/60 dark:border-white/[0.08] shrink-0">
                      <img
                        src={`https://purecatamphetamine.github.io/country-flag-icons/3x2/${cc}.svg`}
                        alt={cc}
                        className="w-3.5 h-2.5 object-cover rounded-xs"
                        onError={(e) => {
                          (e.target as HTMLElement).style.display = 'none'
                        }}
                      />
                      <span className="text-[10px] font-mono font-bold text-slate-700 dark:text-slate-300">{cc}</span>
                    </div>
                    <div className="min-w-0">
                      <h3 className="text-sm font-bold text-slate-900 dark:text-white tracking-tight truncate">
                        {formatLocalizedNodeName(node.name, language === 'fa', cc)}
                      </h3>
                      <p className="text-[11px] text-slate-400 dark:text-slate-500 font-mono">
                        Port: {node.metadata?.api_port || '8888'}
                      </p>
                    </div>
                  </div>
                  <div className="shrink-0">
                    {renderConnectionStatusBadge(node.metadata?.connection_status || 'failed')}
                  </div>
                </div>

                {/* Metadata Row: IP & Ping */}
                <div className="flex items-center justify-between gap-2 p-2.5 bg-slate-50 dark:bg-white/[0.03] rounded-xl text-xs">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span className="text-slate-400 dark:text-slate-500 font-medium font-mono text-[11px]">IP:</span>
                    <span className="font-mono font-bold text-slate-800 dark:text-slate-200 truncate tabular-nums">{node.metadata?.ip_address || 'N/A'}</span>
                  </div>
                  <div className="shrink-0">
                    <LatencyBadge
                      latency={node.metadata?.latency_ms}
                      status={node.metadata?.connection_status || 'failed'}
                    />
                  </div>
                </div>

                {/* Fingerprint Box with Tap-to-Copy */}
                <div 
                  onClick={() => copyToClipboard(node.fingerprint, node.id)}
                  className="flex items-center justify-between gap-2 px-3 py-2 bg-slate-100/70 dark:bg-white/[0.03] rounded-xl border border-slate-200/50 dark:border-white/[0.06] cursor-pointer active:scale-98 transition-all"
                >
                  <div className="flex flex-col min-w-0">
                    <span className="text-[10px] uppercase font-bold text-slate-400 dark:text-slate-500 font-mono">Fingerprint</span>
                    <span className="text-xs font-mono text-slate-700 dark:text-slate-300 truncate">{node.fingerprint || 'N/A'}</span>
                  </div>
                  <div className="p-1 text-slate-400">
                    {isCopied ? <CheckCircle size={15} className="text-emerald-500" /> : <Copy size={15} />}
                  </div>
                </div>

                {/* Actions Footer */}
                <div className="flex items-center justify-between pt-2 border-t border-slate-100 dark:border-white/[0.05] text-xs">
                  <span className="text-slate-400 dark:text-slate-500 font-mono text-[11px]">
                    {node.last_seen ? new Date(node.last_seen).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'Never'}
                  </span>
                  
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setEditingNode(node)}
                      className="px-3.5 py-2 rounded-xl bg-slate-100 dark:bg-white/[0.05] hover:bg-slate-200 dark:hover:bg-white/[0.1] text-slate-700 dark:text-slate-200 text-xs font-semibold flex items-center gap-1.5 transition-colors min-h-[44px] active:scale-95"
                    >
                      <Edit2 size={13} className="text-sky-500" />
                      <span>Edit</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => deleteNode(node.id)}
                      disabled={deletingNodeId === node.id}
                      className="px-3.5 py-2 rounded-xl bg-rose-50 dark:bg-rose-950/30 hover:bg-rose-100 dark:hover:bg-rose-950/50 text-rose-600 dark:text-rose-400 text-xs font-semibold flex items-center gap-1.5 transition-colors min-h-[44px] active:scale-95 disabled:opacity-50"
                    >
                      {deletingNodeId === node.id ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                      <span>Delete</span>
                    </button>
                  </div>
                </div>
              </div>
            )
          })
        )}
      </div>

      {showAddModal && (
        <AddNodeModal
          onClose={() => setShowAddModal(false)}
          onSuccess={() => {
            setShowAddModal(false)
            fetchNodes()
          }}
        />
      )}

      {showCertModal && (
        <CertModal
          certContent={certContent}
          loading={certLoading}
          onClose={() => setShowCertModal(false)}
          onCopy={() => setCertCopied(true)}
          copied={certCopied}
        />
      )}

      <JoinModal
        isOpen={showJoinModal}
        onClose={() => {
          setShowJoinModal(false)
          fetchNodes()
        }}
        role="iran"
        onNodeRegistered={fetchNodes}
      />

      <EditNodeModal
        isOpen={!!editingNode}
        node={editingNode}
        onClose={() => setEditingNode(null)}
        onSuccess={fetchNodes}
      />
    </div>
  )
}

const renderConnectionStatusBadge = (connStatus: string) => {
  const getStatusColor = (status: string) => {
    switch (status) {
      case 'connected':
        return 'bg-emerald-500/10 dark:bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30'
      case 'connecting':
      case 'reconnecting':
        return 'bg-amber-500/10 dark:bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30'
      case 'failed':
        return 'bg-rose-500/10 dark:bg-rose-500/15 text-rose-600 dark:text-rose-400 border-rose-500/30'
      default:
        return 'bg-slate-100 dark:bg-white/[0.05] text-slate-600 dark:text-slate-400 border-slate-200 dark:border-white/[0.08]'
    }
  }
  
  const getStatusText = (status: string) => {
    switch (status) {
      case 'connected': return 'Connected'
      case 'connecting': return 'Connecting'
      case 'reconnecting': return 'Reconnecting'
      case 'failed': return 'Failed'
      default: return status
    }
  }

  const isConnected = connStatus === 'connected'

  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-mono font-bold border ${getStatusColor(connStatus)}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-emerald-500 animate-pulse' : connStatus === 'failed' ? 'bg-rose-500' : 'bg-amber-500'}`} />
      <span>{getStatusText(connStatus)}</span>
    </span>
  )
}

interface AddNodeModalProps {
  onClose: () => void
  onSuccess: () => void
}

const AddNodeModal = ({ onClose, onSuccess }: AddNodeModalProps) => {
  const { t } = useLanguage()
  const { showToast } = useToast()
  const [name, setName] = useState('')
  const [ipAddress, setIpAddress] = useState('')
  const [apiPort, setApiPort] = useState('8888')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    try {
      await api.post('/nodes', { 
        name, 
        ip_address: ipAddress, 
        api_port: parseInt(apiPort) || 8888,
        metadata: {} 
      })
      showToast('success', 'Node Added', `Node ${name} added successfully`)
      onSuccess()
    } catch (error) {
      console.error('Failed to add node:', error)
      showToast('error', 'Error', 'Failed to add node')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div 
      className="fixed inset-0 bg-black/75 backdrop-blur-md flex items-center justify-center z-50 p-4 animate-in fade-in duration-200"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="relative bg-white dark:bg-[#0c1220] rounded-3xl p-6 sm:p-7 w-full max-w-md max-h-[92dvh] overflow-y-auto shadow-2xl border border-slate-200/90 dark:border-white/[0.1]">
        <div className="flex justify-between items-center mb-5 pb-3 border-b border-slate-100 dark:border-white/[0.06]">
          <h2 className="text-base sm:text-lg font-bold text-slate-900 dark:text-white tracking-tight">{t.nodes.addNode}</h2>
          <button onClick={onClose} className="p-2 rounded-xl text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center">
            <X size={19} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5 tracking-tight">
              Node Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-4 py-2.5 bg-slate-50/50 dark:bg-[#070b14]/70 border border-slate-200 dark:border-white/[0.1] rounded-xl text-base sm:text-sm text-slate-900 dark:text-white placeholder-slate-400 dark:placeholder-slate-500 focus:ring-2 focus:ring-sky-500/40 focus:border-sky-500 outline-none transition-all min-h-[44px]"
              required
              placeholder="e.g. Tehran Node 1"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5 tracking-tight">
              IP Address
            </label>
            <input
              type="text"
              value={ipAddress}
              onChange={(e) => setIpAddress(e.target.value)}
              className="w-full px-4 py-2.5 bg-slate-50/50 dark:bg-[#070b14]/70 border border-slate-200 dark:border-white/[0.1] rounded-xl text-base sm:text-sm font-mono text-slate-900 dark:text-white placeholder-slate-400 dark:placeholder-slate-500 focus:ring-2 focus:ring-sky-500/40 focus:border-sky-500 outline-none transition-all min-h-[44px]"
              placeholder="e.g., 185.100.x.x"
              required
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5 tracking-tight">
              API Port
            </label>
            <input
              type="number"
              value={apiPort}
              onChange={(e) => setApiPort(e.target.value)}
              className="w-full px-4 py-2.5 bg-slate-50/50 dark:bg-[#070b14]/70 border border-slate-200 dark:border-white/[0.1] rounded-xl text-base sm:text-sm font-mono text-slate-900 dark:text-white placeholder-slate-400 dark:placeholder-slate-500 focus:ring-2 focus:ring-sky-500/40 focus:border-sky-500 outline-none transition-all min-h-[44px]"
              placeholder="8888"
              min="1"
              max="65535"
              required
            />
          </div>

          <div className="flex gap-2.5 justify-end pt-4 border-t border-slate-100 dark:border-white/[0.06]">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2.5 bg-slate-100 dark:bg-white/[0.06] hover:bg-slate-200 dark:hover:bg-white/[0.1] text-slate-700 dark:text-slate-200 rounded-xl text-xs font-semibold cursor-pointer min-h-[44px]"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              className="px-5 py-2.5 bg-gradient-to-r from-blue-600 via-sky-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white rounded-xl text-xs font-semibold shadow-md shadow-blue-600/25 cursor-pointer min-h-[44px] active:scale-95 disabled:opacity-50"
            >
              {loading ? 'Adding...' : t.nodes.addNode}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

interface CertModalProps {
  certContent: string
  loading: boolean
  onClose: () => void
  onCopy: () => void
  copied: boolean
}

const CertModal = ({ certContent, loading, onClose, onCopy, copied }: CertModalProps) => {
  const { showToast } = useToast()

  return (
    <div 
      className="fixed inset-0 bg-black/75 backdrop-blur-md flex items-center justify-center z-50 p-4 animate-in fade-in duration-200"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="relative bg-white dark:bg-[#0c1220] rounded-3xl p-6 sm:p-7 w-full max-w-2xl max-h-[92dvh] flex flex-col shadow-2xl border border-slate-200/90 dark:border-white/[0.1]">
        <div className="flex justify-between items-center mb-4 pb-3 border-b border-slate-100 dark:border-white/[0.06]">
          <h2 className="text-base sm:text-lg font-bold text-slate-900 dark:text-white tracking-tight">CA Certificate</h2>
          <button
            onClick={onClose}
            className="p-2 rounded-xl text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center"
            aria-label="Close"
          >
            <X size={19} />
          </button>
        </div>
        
        <div className="mb-4 p-3.5 bg-sky-50/80 dark:bg-sky-950/30 border border-sky-200/70 dark:border-sky-800/40 rounded-2xl">
          <p className="text-xs sm:text-sm text-sky-900 dark:text-sky-200 leading-relaxed font-medium">
            <strong>Node Installation:</strong> Copy the certificate below. 
            During node installation, you will be prompted to paste this certificate.
          </p>
        </div>

        {loading ? (
          <div className="flex-1 flex items-center justify-center py-12">
            <div className="text-xs font-mono text-slate-500 dark:text-slate-400 animate-pulse">Loading certificate...</div>
          </div>
        ) : (
          <>
            <textarea
              readOnly
              value={certContent}
              className="flex-1 w-full p-4 border border-slate-800 rounded-2xl font-mono text-xs bg-[#090d16] text-emerald-400 resize-none min-h-[220px] max-h-[40vh] focus:outline-none select-all leading-relaxed"
              onClick={(e) => (e.target as HTMLTextAreaElement).select()}
            />
            
            <div className="flex justify-end gap-2.5 mt-5 pt-3 border-t border-slate-100 dark:border-white/[0.06]">
              <button
                type="button"
                onClick={async (e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  if (!certContent || certContent.trim().length === 0) {
                    showToast('warning', 'Empty Certificate', 'Certificate content is empty. Please wait for it to load.')
                    return
                  }
                  const success = await copyTextToClipboard(certContent)
                  if (success) {
                    onCopy()
                    showToast('success', 'Copied', 'Certificate copied to clipboard', 2000)
                  }
                }}
                disabled={loading || !certContent || certContent.trim().length === 0}
                className={`px-5 py-2.5 rounded-xl transition-all font-semibold flex items-center gap-2 text-xs min-h-[44px] cursor-pointer active:scale-95 ${
                  copied
                    ? 'bg-emerald-600 text-white shadow-emerald-600/30'
                    : 'bg-gradient-to-r from-blue-600 via-sky-600 to-indigo-600 text-white hover:from-blue-500 hover:to-indigo-500 shadow-blue-600/25 disabled:opacity-50'
                }`}
              >
                <Copy size={14} />
                <span>{copied ? 'Copied!' : 'Copy Certificate'}</span>
              </button>
              <button
                onClick={onClose}
                className="px-4 py-2.5 bg-slate-100 dark:bg-white/[0.06] hover:bg-slate-200 dark:hover:bg-white/[0.1] text-slate-700 dark:text-slate-200 rounded-xl text-xs font-semibold cursor-pointer min-h-[44px]"
              >
                Close
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

export default Nodes
