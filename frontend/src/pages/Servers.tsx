import { useEffect, useState } from 'react'
import { Plus, Copy, Trash2, CheckCircle, XCircle, AlertCircle, Server, Sparkles, Edit2 } from 'lucide-react'
import api from '../api/client'
import { useLanguage } from '../contexts/LanguageContext'
import { useToast } from '../contexts/ToastContext'
import { EmptyState } from '../components/EmptyState'
import { copyTextToClipboard } from '../utils/clipboard'
import { JoinModal } from '../components/JoinModal'
import { EditNodeModal } from '../components/EditNodeModal'
import { LatencyBadge } from '../components/LatencyBadge'
import { getCountryFlag, formatLocalizedNodeName, extractCountryCode } from '../utils/country'

interface Server {
  id: string
  name: string
  fingerprint: string
  status: string
  registered_at: string
  last_seen: string
  metadata: Record<string, any>
}

const Servers = () => {
  const { t, language } = useLanguage()
  const { showToast, showConfirm } = useToast()
  const [servers, setServers] = useState<Server[]>([])
  const [loading, setLoading] = useState(true)
  const [showAddModal, setShowAddModal] = useState(false)
  const [showCertModal, setShowCertModal] = useState(false)
  const [showJoinModal, setShowJoinModal] = useState(false)
  const [editingServer, setEditingServer] = useState<Server | null>(null)
  const [certContent, setCertContent] = useState('')
  const [certLoading, setCertLoading] = useState(false)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    fetchServers()
    const interval = setInterval(fetchServers, 6000)
    const params = new URLSearchParams(window.location.search)
    if (params.get('add') === 'true') {
      setShowAddModal(true)
      window.history.replaceState({}, '', '/servers')
    }
    return () => clearInterval(interval)
  }, [])

  const fetchServers = async () => {
    try {
      const response = await api.get('/nodes')
      // Filter only foreign servers
      const foreignServers = response.data.filter((node: Server) => 
        node.metadata?.role === 'foreign'
      )
      setServers(foreignServers)
    } catch (error) {
      console.error('Failed to fetch servers:', error)
    } finally {
      setLoading(false)
    }
  }

  const copyToClipboard = async (text: string) => {
    const success = await copyTextToClipboard(text)
    if (success) {
      setCopied(true)
      showToast('success', 'Copied', 'Fingerprint copied to clipboard', 2000)
      setTimeout(() => setCopied(false), 2000)
    } else {
      showToast('error', 'Error', 'Failed to copy to clipboard')
    }
  }

  const showCA = async () => {
    setShowCertModal(true)
    setCertLoading(true)
    try {
      const response = await api.get('/panel/ca/server', {
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
      const response = await api.get('/panel/ca/server?download=true', { responseType: 'blob' })
      const url = window.URL.createObjectURL(new Blob([response.data]))
      const link = document.createElement('a')
      link.href = url
      link.setAttribute('download', 'ca-server.crt')
      document.body.appendChild(link)
      link.click()
      link.remove()
      showToast('success', 'Downloaded', 'CA certificate downloaded')
    } catch (error) {
      console.error('Failed to download CA:', error)
      showToast('error', 'Error', 'Failed to download CA certificate')
    }
  }

  const deleteServer = async (id: string) => {
    const confirmed = await showConfirm({
      title: 'Delete Foreign Server',
      message: 'Are you sure you want to delete this foreign server? Any tunnels linked to it may stop working.',
      variant: 'danger',
      confirmText: 'Delete'
    })
    if (!confirmed) return
    
    try {
      await api.delete(`/nodes/${id}`)
      showToast('success', 'Deleted', 'Server deleted successfully')
      fetchServers()
    } catch (error) {
      console.error('Failed to delete server:', error)
      showToast('error', 'Error', 'Failed to delete server')
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center">
          <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 dark:border-blue-400 mb-4"></div>
          <p className="text-gray-500 dark:text-gray-400">Loading servers...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="w-full max-w-7xl mx-auto space-y-5 sm:space-y-6">
      {/* Header & Actions */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold text-gray-900 dark:text-white tracking-tight">{t.servers.title}</h1>
          <p className="text-xs sm:text-sm text-gray-500 dark:text-gray-400 mt-0.5">{t.servers.subtitle}</p>
        </div>
        <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
          <button
            onClick={() => setShowJoinModal(true)}
            className="flex-1 sm:flex-none px-3.5 py-2.5 bg-gradient-to-r from-purple-600 to-indigo-600 text-white rounded-xl hover:from-purple-700 hover:to-indigo-700 transition-all font-semibold shadow-xs hover:shadow-md flex items-center justify-center gap-2 text-xs sm:text-sm min-h-[44px] active:scale-95"
            title="One-Click Automatic Node Join Command"
          >
            <Sparkles size={16} />
            <span>Auto Join</span>
          </button>
          <button
            onClick={showCA}
            className="px-3.5 py-2.5 bg-emerald-600 text-white rounded-xl hover:bg-emerald-700 transition-all font-semibold shadow-xs hover:shadow-md flex items-center justify-center gap-1.5 text-xs sm:text-sm min-h-[44px] active:scale-95"
          >
            <Copy size={16} />
            <span>{t.servers.viewCACertificate}</span>
          </button>
          <button
            onClick={() => setShowAddModal(true)}
            className="w-full sm:w-auto px-4 py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 text-white rounded-xl hover:from-blue-700 hover:to-indigo-700 transition-all font-semibold shadow-xs hover:shadow-md flex items-center justify-center gap-2 text-xs sm:text-sm min-h-[44px] active:scale-95"
          >
            <Plus size={18} />
            <span>{t.dashboard.addServer}</span>
          </button>
        </div>
      </div>

      {/* Desktop Table View */}
      <div className="hidden md:block bg-white dark:bg-gray-800 rounded-2xl border border-gray-200/80 dark:border-gray-700/80 overflow-hidden shadow-xs">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-50/80 dark:bg-gray-750/50 border-b border-gray-200 dark:border-gray-700">
              <tr>
                <th className="px-6 py-3.5 text-start text-xs font-semibold text-gray-500 dark:text-gray-300 uppercase tracking-wider">
                  Name
                </th>
                <th className="px-6 py-3.5 text-start text-xs font-semibold text-gray-500 dark:text-gray-300 uppercase tracking-wider">
                  Fingerprint
                </th>
                <th className="px-6 py-3.5 text-start text-xs font-semibold text-gray-500 dark:text-gray-300 uppercase tracking-wider">
                  Status
                </th>
                <th className="px-6 py-3.5 text-start text-xs font-semibold text-gray-500 dark:text-gray-300 uppercase tracking-wider">
                  Ping
                </th>
                <th className="px-6 py-3.5 text-start text-xs font-semibold text-gray-500 dark:text-gray-300 uppercase tracking-wider">
                  IP Address
                </th>
                <th className="px-6 py-3.5 text-start text-xs font-semibold text-gray-500 dark:text-gray-300 uppercase tracking-wider">
                  Last Seen
                </th>
                <th className="px-6 py-3.5 text-start text-xs font-semibold text-gray-500 dark:text-gray-300 uppercase tracking-wider">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-100 dark:divide-gray-700">
              {servers.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-6 py-0">
                    <EmptyState 
                      icon={<Server size={32} />} 
                      title="No foreign servers" 
                      description="Add a foreign server to get started." 
                      action={{ label: 'Add Server', onClick: () => setShowAddModal(true) }} 
                    />
                  </td>
                </tr>
              ) : (
                servers.map((server) => {
                  const cc = extractCountryCode(server.name, server.metadata?.country_code, 'foreign')
                  const flag = getCountryFlag(cc)
                  return (
                    <tr key={server.id} className="hover:bg-gray-50/70 dark:hover:bg-gray-700/50 transition-colors">
                      <td className="px-6 py-4 whitespace-nowrap">
                        <div className="flex items-center gap-3">
                          {cc ? (
                            <div className="flex items-center gap-1.5 px-2 py-1 bg-gray-100 dark:bg-gray-700/70 rounded-lg border border-gray-200/60 dark:border-gray-600/60 shadow-2xs">
                              <img
                                src={`https://purecatamphetamine.github.io/country-flag-icons/3x2/${cc}.svg`}
                                alt={cc}
                                className="w-4 h-3 object-cover rounded-xs"
                                onError={(e) => {
                                  (e.target as HTMLElement).style.display = 'none'
                                }}
                              />
                              <span className="text-[11px] font-mono font-bold text-gray-700 dark:text-gray-300">{cc}</span>
                            </div>
                          ) : (
                            <div className="w-7 h-6 bg-gray-100 dark:bg-gray-750 rounded-lg flex items-center justify-center text-xs">
                              🌐
                            </div>
                          )}
                          <div className="flex flex-col gap-0.5">
                            <span className="text-sm font-semibold text-gray-900 dark:text-white">
                              {formatLocalizedNodeName(server.name, language === 'fa', cc)}
                            </span>
                            <span className="text-xs text-gray-500 dark:text-gray-400 font-mono">
                              Port: <span className="font-medium text-gray-700 dark:text-gray-300">{server.metadata?.api_port || '8888'}</span>
                            </span>
                          </div>
                        </div>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <div className="flex items-center gap-2">
                          <code className="text-sm text-gray-600 dark:text-gray-300 font-mono">{server.fingerprint}</code>
                          <button
                            onClick={() => copyToClipboard(server.fingerprint)}
                            className="p-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg text-gray-600 dark:text-gray-400 min-w-[36px] min-h-[36px] flex items-center justify-center transition-colors"
                            title="Copy fingerprint"
                            aria-label="Copy fingerprint"
                          >
                            <Copy size={15} />
                          </button>
                        </div>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        {renderServerStatusBadge(server.metadata?.connection_status || 'failed')}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <LatencyBadge
                          latency={server.metadata?.latency_ms}
                          status={server.metadata?.connection_status || 'failed'}
                        />
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400 font-mono">
                        {server.metadata?.ip_address || 'N/A'}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                        {new Date(server.last_seen).toLocaleString()}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm">
                        <div className="flex items-center gap-1.5">
                          <button
                            onClick={() => setEditingServer(server)}
                            className="p-2 text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-xl min-w-[38px] min-h-[38px] flex items-center justify-center transition-colors"
                            title="Edit Server Name"
                            aria-label="Edit Server Name"
                          >
                            <Edit2 size={16} />
                          </button>
                          <button
                            onClick={() => deleteServer(server.id)}
                            className="p-2 text-red-600 dark:text-red-400 hover:text-red-800 dark:hover:text-red-300 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-xl min-w-[38px] min-h-[38px] flex items-center justify-center transition-colors"
                            title="Delete server"
                            aria-label="Delete server"
                          >
                            <Trash2 size={16} />
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

      {/* Mobile Card View */}
      <div className="block md:hidden space-y-3">
        {servers.length === 0 ? (
          <div className="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200/80 dark:border-gray-700/80 p-4">
            <EmptyState 
              icon={<Server size={32} />} 
              title="No foreign servers" 
              description="Add a foreign server to get started." 
              action={{ label: 'Add Server', onClick: () => setShowAddModal(true) }} 
            />
          </div>
        ) : (
          servers.map((server) => {
            const cc = extractCountryCode(server.name, server.metadata?.country_code, 'foreign')
            return (
              <div 
                key={server.id} 
                className="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200/80 dark:border-gray-700/80 p-4 shadow-xs space-y-3.5 transition-shadow hover:shadow-md"
              >
                {/* Header: Flag, Name, Status */}
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2.5 min-w-0">
                    {cc ? (
                      <div className="flex items-center gap-1 px-2 py-0.5 bg-gray-100 dark:bg-gray-700/70 rounded-md border border-gray-200/60 dark:border-gray-600/60 shrink-0">
                        <img
                          src={`https://purecatamphetamine.github.io/country-flag-icons/3x2/${cc}.svg`}
                          alt={cc}
                          className="w-3.5 h-2.5 object-cover rounded-xs"
                          onError={(e) => {
                            (e.target as HTMLElement).style.display = 'none'
                          }}
                        />
                        <span className="text-[10px] font-mono font-bold text-gray-700 dark:text-gray-300">{cc}</span>
                      </div>
                    ) : (
                      <div className="w-6 h-6 bg-gray-100 dark:bg-gray-750 rounded-md flex items-center justify-center text-xs shrink-0">
                        🌐
                      </div>
                    )}
                    <div className="min-w-0">
                      <h3 className="text-sm font-bold text-gray-900 dark:text-white truncate">
                        {formatLocalizedNodeName(server.name, language === 'fa', cc)}
                      </h3>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 font-mono">
                        Port: {server.metadata?.api_port || '8888'}
                      </p>
                    </div>
                  </div>
                  <div className="shrink-0">
                    {renderServerStatusBadge(server.metadata?.connection_status || 'failed')}
                  </div>
                </div>

                {/* Metadata Row: IP & Ping */}
                <div className="flex items-center justify-between gap-2 p-2.5 bg-gray-50 dark:bg-gray-750/50 rounded-xl text-xs">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span className="text-gray-400 dark:text-gray-500 font-medium">IP:</span>
                    <span className="font-mono text-gray-800 dark:text-gray-200 truncate">{server.metadata?.ip_address || 'N/A'}</span>
                  </div>
                  <div className="shrink-0">
                    <LatencyBadge
                      latency={server.metadata?.latency_ms}
                      status={server.metadata?.connection_status || 'failed'}
                    />
                  </div>
                </div>

                {/* Fingerprint Box */}
                <div className="flex items-center justify-between gap-2 px-3 py-2 bg-gray-100/70 dark:bg-gray-900/60 rounded-xl border border-gray-200/50 dark:border-gray-700/50">
                  <div className="flex flex-col min-w-0">
                    <span className="text-[10px] uppercase font-semibold text-gray-400 dark:text-gray-500">Fingerprint</span>
                    <span className="text-xs font-mono text-gray-700 dark:text-gray-300 truncate">{server.fingerprint}</span>
                  </div>
                  <button
                    onClick={() => copyToClipboard(server.fingerprint)}
                    className="p-2 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-200 rounded-lg shadow-2xs border border-gray-200/80 dark:border-gray-700 min-h-[38px] min-w-[38px] flex items-center justify-center shrink-0 active:scale-95 transition-all"
                    title="Copy Fingerprint"
                    aria-label="Copy Fingerprint"
                  >
                    <Copy size={15} />
                  </button>
                </div>

                {/* Footer: Last Seen & Actions */}
                <div className="flex items-center justify-between pt-1 text-xs text-gray-400 dark:text-gray-500 border-t border-gray-100 dark:border-gray-700/60">
                  <span className="truncate">{new Date(server.last_seen).toLocaleDateString()} {new Date(server.last_seen).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                  <div className="flex items-center gap-1.5">
                    <button
                      onClick={() => setEditingServer(server)}
                      className="p-2 text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/30 rounded-xl min-w-[44px] min-h-[44px] flex items-center justify-center transition-colors active:scale-95"
                      title="Edit Server Name"
                      aria-label="Edit Server Name"
                    >
                      <Edit2 size={16} />
                    </button>
                    <button
                      onClick={() => deleteServer(server.id)}
                      className="p-2 text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-900/30 rounded-xl min-w-[44px] min-h-[44px] flex items-center justify-center transition-colors active:scale-95"
                      title="Delete Server"
                      aria-label="Delete Server"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                </div>
              </div>
            )
          })
        )}
      </div>

      {showAddModal && (
        <AddServerModal
          onClose={() => setShowAddModal(false)}
          onSuccess={() => {
            setShowAddModal(false)
            fetchServers()
          }}
        />
      )}

      {showCertModal && (
        <CertModal
          certContent={certContent}
          loading={certLoading}
          onClose={() => setShowCertModal(false)}
          onCopy={() => setCopied(true)}
          copied={copied}
        />
      )}

      <JoinModal
        isOpen={showJoinModal}
        onClose={() => {
          setShowJoinModal(false)
          fetchServers()
        }}
        role="foreign"
        onNodeRegistered={fetchServers}
      />

      <EditNodeModal
        isOpen={!!editingServer}
        node={editingServer}
        onClose={() => setEditingServer(null)}
        onSuccess={fetchServers}
      />
    </div>
  )
}

const renderServerStatusBadge = (connStatus: string) => {
  const getStatusColor = (status: string) => {
    switch (status) {
      case 'connected':
        return 'bg-emerald-100 dark:bg-emerald-950/40 text-emerald-800 dark:text-emerald-300 border-emerald-200/80 dark:border-emerald-800/60'
      case 'connecting':
      case 'reconnecting':
        return 'bg-amber-100 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300 border-amber-200/80 dark:border-amber-800/60'
      case 'failed':
        return 'bg-rose-100 dark:bg-rose-950/40 text-rose-800 dark:text-rose-300 border-rose-200/80 dark:border-rose-800/60'
      default:
        return 'bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-200 border-gray-200 dark:border-gray-700'
    }
  }
  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'connected':
        return <CheckCircle size={12} className="text-emerald-600 dark:text-emerald-400" />
      case 'connecting':
      case 'reconnecting':
        return <AlertCircle size={12} className="text-amber-600 dark:text-amber-400" />
      case 'failed':
        return <XCircle size={12} className="text-rose-600 dark:text-rose-400" />
      default:
        return <XCircle size={12} />
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
  return (
    <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold border ${getStatusColor(connStatus)}`}>
      {getStatusIcon(connStatus)}
      <span>{getStatusText(connStatus)}</span>
    </span>
  )
}

interface AddServerModalProps {
  onClose: () => void
  onSuccess: () => void
}

const AddServerModal = ({ onClose, onSuccess }: AddServerModalProps) => {
  const { t } = useLanguage()
  const { showToast } = useToast()
  const [name, setName] = useState('')
  const [ipAddress, setIpAddress] = useState('')
  const [apiPort, setApiPort] = useState('8888')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      await api.post('/nodes', { 
        name, 
        ip_address: ipAddress, 
        api_port: parseInt(apiPort) || 8888,
        metadata: {
          role: 'foreign'
        } 
      })
      showToast('success', 'Server Added', `Foreign server ${name} added successfully`)
      onSuccess()
    } catch (error) {
      console.error('Failed to add server:', error)
      showToast('error', 'Error', 'Failed to add server')
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-xs flex items-center justify-center z-50 p-4">
      <div className="bg-white dark:bg-gray-800 rounded-2xl p-5 sm:p-6 w-full max-w-md max-h-[90dvh] overflow-y-auto shadow-2xl border border-gray-200/80 dark:border-gray-700/80 animate-in fade-in zoom-in-95 duration-200">
        <h2 className="text-xl font-bold text-gray-900 dark:text-white mb-4">Add Foreign Server</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
              Server Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white dark:bg-gray-750 text-gray-900 dark:text-white placeholder-gray-400 text-base sm:text-sm transition-all"
              required
              placeholder="e.g. Frankfurt Server 1"
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
              IP Address
            </label>
            <input
              type="text"
              value={ipAddress}
              onChange={(e) => setIpAddress(e.target.value)}
              className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white dark:bg-gray-750 text-gray-900 dark:text-white placeholder-gray-400 text-base sm:text-sm font-mono transition-all"
              placeholder="e.g., 95.217.x.x"
              required
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
              API Port
            </label>
            <input
              type="number"
              value={apiPort}
              onChange={(e) => setApiPort(e.target.value)}
              className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white dark:bg-gray-750 text-gray-900 dark:text-white placeholder-gray-400 text-base sm:text-sm font-mono transition-all"
              placeholder="8888"
              min="1"
              max="65535"
              required
            />
          </div>
          <div className="flex gap-2.5 justify-end pt-3 border-t border-gray-100 dark:border-gray-700">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2.5 bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-200 rounded-xl hover:bg-gray-200 dark:hover:bg-gray-600 font-medium text-sm min-h-[44px]"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="px-5 py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 text-white rounded-xl hover:from-blue-700 hover:to-indigo-700 transition-all font-semibold shadow-xs hover:shadow-md text-sm min-h-[44px]"
            >
              {t.dashboard.addServer}
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
    <div className="fixed inset-0 bg-black/60 backdrop-blur-xs flex items-center justify-center z-50 p-4">
      <div className="bg-white dark:bg-gray-800 rounded-2xl p-5 sm:p-6 w-full max-w-2xl max-h-[90dvh] flex flex-col shadow-2xl border border-gray-200/80 dark:border-gray-700/80 animate-in fade-in zoom-in-95 duration-200">
        <div className="flex justify-between items-center mb-3">
          <h2 className="text-lg sm:text-xl font-bold text-gray-900 dark:text-white">Foreign Server CA Certificate</h2>
          <button
            onClick={onClose}
            className="p-1 rounded-xl text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 min-h-[44px] min-w-[44px] flex items-center justify-center"
            aria-label="Close"
          >
            <XCircle size={22} />
          </button>
        </div>
        
        <div className="mb-3 p-3 bg-blue-50 dark:bg-blue-950/40 border border-blue-200/80 dark:border-blue-800/60 rounded-xl">
          <p className="text-xs sm:text-sm text-blue-900 dark:text-blue-200 leading-relaxed">
            <strong>Foreign Server Installation:</strong> Copy the certificate below. 
            During foreign server installation, you will be prompted to paste this certificate.
          </p>
        </div>

        {loading ? (
          <div className="flex-1 flex items-center justify-center py-12">
            <div className="text-gray-500 dark:text-gray-400 text-sm animate-pulse">Loading certificate...</div>
          </div>
        ) : (
          <>
            <textarea
              readOnly
              value={certContent}
              className="flex-1 w-full p-3.5 border border-gray-300 dark:border-gray-600 rounded-xl font-mono text-xs bg-gray-900 text-green-400 resize-none min-h-[220px] max-h-[40vh] focus:outline-none select-all"
              onClick={(e) => (e.target as HTMLTextAreaElement).select()}
            />
            
            <div className="flex justify-end gap-2.5 mt-4 pt-3 border-t border-gray-100 dark:border-gray-700">
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
                className={`px-4 py-2.5 rounded-xl transition-all font-semibold flex items-center gap-2 text-sm min-h-[44px] ${
                  copied
                    ? 'bg-emerald-600 text-white'
                    : 'bg-gradient-to-r from-blue-600 to-indigo-600 text-white hover:from-blue-700 hover:to-indigo-700 disabled:opacity-50'
                }`}
              >
                <Copy size={16} />
                <span>{copied ? 'Copied!' : 'Copy Certificate'}</span>
              </button>
              <button
                onClick={onClose}
                className="px-4 py-2.5 bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-200 rounded-xl hover:bg-gray-200 dark:hover:bg-gray-600 text-sm font-medium min-h-[44px]"
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

export default Servers

