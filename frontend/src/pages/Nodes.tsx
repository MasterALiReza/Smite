import { useEffect, useState } from 'react'
import { Plus, Copy, Trash2, CheckCircle, XCircle, Download, AlertCircle, Server } from 'lucide-react'
import api from '../api/client'
import { useLanguage } from '../contexts/LanguageContext'
import { useToast } from '../contexts/ToastContext'
import { EmptyState } from '../components/EmptyState'

interface Node {
  id: string
  name: string
  fingerprint: string
  status: string
  registered_at: string
  last_seen: string
  metadata: Record<string, any>
}

const Nodes = () => {
  const { t } = useLanguage()
  const { showToast, showConfirm } = useToast()
  const [nodes, setNodes] = useState<Node[]>([])
  const [loading, setLoading] = useState(true)
  const [showAddModal, setShowAddModal] = useState(false)
  const [showCertModal, setShowCertModal] = useState(false)
  const [certContent, setCertContent] = useState<string>('')
  const [certLoading, setCertLoading] = useState(false)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    fetchNodes()
    const params = new URLSearchParams(window.location.search)
    if (params.get('add') === 'true') {
      setShowAddModal(true)
      window.history.replaceState({}, '', '/nodes')
    }
  }, [])

  const fetchNodes = async () => {
    try {
      const response = await api.get('/nodes')
      // Filter only iran nodes (exclude foreign servers)
      const iranNodes = response.data.filter((node: Node) => 
        node.metadata?.role !== 'foreign' && (node.metadata?.role === 'iran' || !node.metadata?.role)
      )
      setNodes(iranNodes)
    } catch (error) {
      console.error('Failed to fetch nodes:', error)
    } finally {
      setLoading(false)
    }
  }

  const copyToClipboard = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      showToast('success', 'Copied', 'Fingerprint copied to clipboard', 2000)
      setTimeout(() => setCopied(false), 2000)
    } catch (error) {
      console.error('Failed to copy to clipboard:', error)
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
    const confirmed = await showConfirm({
      title: 'Delete Node',
      message: 'Are you sure you want to delete this Iran node? Any tunnels linked to it may stop working.',
      variant: 'danger',
      confirmText: 'Delete'
    })
    if (!confirmed) return
    
    try {
      await api.delete(`/nodes/${id}`)
      showToast('success', 'Deleted', 'Node deleted successfully')
      fetchNodes()
    } catch (error) {
      console.error('Failed to delete node:', error)
      showToast('error', 'Error', 'Failed to delete node')
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center">
          <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-[#3F72AF] dark:border-[#00A8CC] mb-4"></div>
          <p className="text-sm font-medium text-[#112D4E]/70 dark:text-[#DBE2EF]/80">Loading nodes...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="w-full max-w-7xl mx-auto space-y-8">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-black tracking-tight text-[#112D4E] dark:text-[#F9F7F7] mb-2">{t.nodes.title}</h1>
          <p className="text-sm font-medium text-[#112D4E]/70 dark:text-[#DBE2EF]/80">{t.nodes.subtitle}</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={showCA}
            className="px-4 py-2.5 bg-emerald-600 hover:bg-emerald-700 dark:bg-emerald-600 dark:hover:bg-emerald-500 text-white rounded-xl transition-all duration-200 font-semibold text-sm shadow-sm hover:shadow-md flex items-center gap-2"
          >
            <Copy size={17} />
            <span>{t.nodes.viewCACertificate}</span>
          </button>
          <button
            onClick={downloadCA}
            className="px-4 py-2.5 bg-[#F9F7F7] dark:bg-[#142850] text-[#112D4E] dark:text-[#DBE2EF] rounded-xl hover:bg-[#DBE2EF]/60 dark:hover:bg-[#142850]/80 transition-all duration-200 font-semibold text-sm border border-[#DBE2EF] dark:border-[#0C7B93]/30 flex items-center gap-2"
          >
            <Download size={17} />
            <span>{t.nodes.downloadCA}</span>
          </button>
          <button
            onClick={() => setShowAddModal(true)}
            className="px-5 py-2.5 bg-[#3F72AF] hover:bg-[#3F72AF]/90 dark:bg-gradient-to-r dark:from-[#0C7B93] dark:to-[#00A8CC] text-white rounded-xl transition-all duration-200 font-bold text-sm shadow-md shadow-[#3F72AF]/20 dark:shadow-[#00A8CC]/20 flex items-center gap-2 hover:scale-[1.02] active:scale-[0.98]"
          >
            <Plus size={19} />
            <span>{t.nodes.addNode}</span>
          </button>
        </div>
      </div>

      <div className="bg-white dark:bg-[#27496D] rounded-2xl border border-[#DBE2EF] dark:border-[#142850] overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-[#F9F7F7] dark:bg-[#142850]/60 border-b border-[#DBE2EF] dark:border-[#142850]">
              <tr>
                <th className="px-6 py-4 text-start text-xs font-bold text-[#112D4E]/70 dark:text-[#DBE2EF]/70 uppercase tracking-wider">
                  Name
                </th>
                <th className="px-6 py-4 text-start text-xs font-bold text-[#112D4E]/70 dark:text-[#DBE2EF]/70 uppercase tracking-wider">
                  Fingerprint
                </th>
                <th className="px-6 py-4 text-start text-xs font-bold text-[#112D4E]/70 dark:text-[#DBE2EF]/70 uppercase tracking-wider">
                  Status
                </th>
                <th className="px-6 py-4 text-start text-xs font-bold text-[#112D4E]/70 dark:text-[#DBE2EF]/70 uppercase tracking-wider">
                  IP Address
                </th>
                <th className="px-6 py-4 text-start text-xs font-bold text-[#112D4E]/70 dark:text-[#DBE2EF]/70 uppercase tracking-wider">
                  Last Seen
                </th>
                <th className="px-6 py-4 text-start text-xs font-bold text-[#112D4E]/70 dark:text-[#DBE2EF]/70 uppercase tracking-wider">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="bg-white dark:bg-[#27496D] divide-y divide-[#DBE2EF] dark:divide-[#142850]/70">
              {nodes.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-6 py-0">
                    <EmptyState 
                      icon={<Server size={32} />} 
                      title="No Iran nodes" 
                      description="Add an Iran node to get started." 
                      action={{ label: 'Add Node', onClick: () => setShowAddModal(true) }} 
                    />
                  </td>
                </tr>
              ) : (
                nodes.map((node) => (
                <tr key={node.id} className="hover:bg-[#DBE2EF]/30 dark:hover:bg-[#142850]/40 transition-colors">
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="text-sm font-bold text-[#112D4E] dark:text-[#F9F7F7]">{node.name}</div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="flex items-center gap-2">
                      <code className="text-xs text-[#112D4E] dark:text-[#00A8CC] font-mono bg-[#DBE2EF]/60 dark:bg-[#142850] px-2 py-1 rounded-lg border border-[#DBE2EF] dark:border-[#0C7B93]/30">{node.fingerprint}</code>
                      <button
                        onClick={() => copyToClipboard(node.fingerprint)}
                        className="p-1.5 hover:bg-[#DBE2EF]/60 dark:hover:bg-[#142850] rounded-lg text-[#112D4E]/70 dark:text-[#DBE2EF]/70 min-w-[36px] min-h-[36px] flex items-center justify-center transition-colors"
                        title="Copy fingerprint"
                        aria-label="Copy fingerprint"
                      >
                        <Copy size={15} />
                      </button>
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    {(() => {
                      const connStatus = node.metadata?.connection_status || 'failed'
                      const getStatusColor = (status: string) => {
                        switch (status) {
                          case 'connected':
                            return 'bg-emerald-500/10 dark:bg-emerald-500/20 text-emerald-800 dark:text-emerald-300 border border-emerald-500/30'
                          case 'connecting':
                          case 'reconnecting':
                            return 'bg-amber-500/10 dark:bg-amber-500/20 text-amber-800 dark:text-amber-300 border border-amber-500/30'
                          case 'failed':
                            return 'bg-rose-500/10 dark:bg-rose-500/20 text-rose-800 dark:text-rose-300 border border-rose-500/30'
                          default:
                            return 'bg-[#DBE2EF]/60 dark:bg-[#142850] text-[#112D4E] dark:text-[#DBE2EF]'
                        }
                      }
                      const getStatusIcon = (status: string) => {
                        switch (status) {
                          case 'connected':
                            return <CheckCircle size={12} className="text-green-600 dark:text-green-400" />
                          case 'connecting':
                          case 'reconnecting':
                            return <AlertCircle size={12} className="text-yellow-600 dark:text-yellow-400" />
                          case 'failed':
                            return <XCircle size={12} className="text-red-600 dark:text-red-400" />
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
                        <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium ${getStatusColor(connStatus)}`}>
                          {getStatusIcon(connStatus)}
                          {getStatusText(connStatus)}
                        </span>
                      )
                    })()}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                    {node.metadata?.ip_address || 'N/A'}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                    {new Date(node.last_seen).toLocaleString()}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm">
                    <button
                      onClick={() => deleteNode(node.id)}
                      className="p-1.5 text-red-600 dark:text-red-400 hover:text-red-800 dark:hover:text-red-300 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg min-w-[36px] min-h-[36px] flex items-center justify-center transition-colors"
                      title="Delete node"
                      aria-label="Delete node"
                    >
                      <Trash2 size={16} />
                    </button>
                  </td>
                </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
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
          onCopy={() => copyToClipboard(certContent)}
          copied={copied}
        />
      )}
    </div>
  )
}

interface AddNodeModalProps {
  onClose: () => void
  onSuccess: () => void
}

const AddNodeModal = ({ onClose, onSuccess }: AddNodeModalProps) => {
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
        metadata: {} 
      })
      showToast('success', 'Node Added', `Node ${name} added successfully`)
      onSuccess()
    } catch (error) {
      console.error('Failed to add node:', error)
      showToast('error', 'Error', 'Failed to add node')
    }
  }

  return (
    <div className="fixed inset-0 bg-[#112D4E]/60 dark:bg-black/70 backdrop-blur-md flex items-center justify-center z-50 p-4">
      <div className="bg-white dark:bg-[#27496D] border border-[#DBE2EF] dark:border-[#142850] rounded-2xl p-6 w-full max-w-md shadow-2xl animate-in fade-in zoom-in-95 duration-200">
        <h2 className="text-xl font-bold text-[#112D4E] dark:text-[#F9F7F7] mb-4">{t.nodes.addNode}</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-bold uppercase tracking-wider text-[#112D4E]/70 dark:text-[#DBE2EF]/70 mb-1.5">
              Node Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-4 py-2.5 border border-[#DBE2EF] dark:border-[#142850] rounded-xl focus:ring-2 focus:ring-[#3F72AF] dark:focus:ring-[#00A8CC] focus:border-transparent bg-[#F9F7F7] dark:bg-[#142850] text-[#112D4E] dark:text-[#F9F7F7] placeholder-[#112D4E]/40 dark:placeholder-[#DBE2EF]/40 text-sm font-medium transition-colors"
              required
            />
          </div>
          <div>
            <label className="block text-xs font-bold uppercase tracking-wider text-[#112D4E]/70 dark:text-[#DBE2EF]/70 mb-1.5">
              IP Address
            </label>
            <input
              type="text"
              value={ipAddress}
              onChange={(e) => setIpAddress(e.target.value)}
              className="w-full px-4 py-2.5 border border-[#DBE2EF] dark:border-[#142850] rounded-xl focus:ring-2 focus:ring-[#3F72AF] dark:focus:ring-[#00A8CC] focus:border-transparent bg-[#F9F7F7] dark:bg-[#142850] text-[#112D4E] dark:text-[#F9F7F7] placeholder-[#112D4E]/40 dark:placeholder-[#DBE2EF]/40 text-sm font-medium transition-colors"
              placeholder="e.g., 192.168.1.100"
              required
            />
          </div>
          <div>
            <label className="block text-xs font-bold uppercase tracking-wider text-[#112D4E]/70 dark:text-[#DBE2EF]/70 mb-1.5">
              API Port
            </label>
            <input
              type="number"
              value={apiPort}
              onChange={(e) => setApiPort(e.target.value)}
              className="w-full px-4 py-2.5 border border-[#DBE2EF] dark:border-[#142850] rounded-xl focus:ring-2 focus:ring-[#3F72AF] dark:focus:ring-[#00A8CC] focus:border-transparent bg-[#F9F7F7] dark:bg-[#142850] text-[#112D4E] dark:text-[#F9F7F7] placeholder-[#112D4E]/40 dark:placeholder-[#DBE2EF]/40 text-sm font-medium transition-colors"
              placeholder="8888"
              min="1"
              max="65535"
              required
            />
          </div>
          <div className="flex gap-3 justify-end pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2.5 bg-[#DBE2EF]/60 hover:bg-[#DBE2EF] dark:bg-[#142850] dark:hover:bg-[#142850]/80 text-[#112D4E] dark:text-[#DBE2EF] rounded-xl font-semibold text-sm transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="px-5 py-2.5 bg-[#3F72AF] hover:bg-[#3F72AF]/90 dark:bg-gradient-to-r dark:from-[#0C7B93] dark:to-[#00A8CC] text-white rounded-xl transition-all duration-200 font-bold text-sm shadow-md shadow-[#3F72AF]/20 dark:shadow-[#00A8CC]/20 hover:scale-[1.02] active:scale-[0.98]"
            >
              {t.nodes.addNode}
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
  return (
    <div className="fixed inset-0 bg-[#112D4E]/60 dark:bg-black/70 backdrop-blur-md flex items-center justify-center z-50 p-4">
      <div className="bg-white dark:bg-[#27496D] border border-[#DBE2EF] dark:border-[#142850] rounded-2xl p-6 w-full max-w-2xl max-h-[90vh] flex flex-col shadow-2xl animate-in fade-in zoom-in-95 duration-200">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-xl font-bold text-[#112D4E] dark:text-[#F9F7F7]">CA Certificate</h2>
          <button
            onClick={onClose}
            className="text-[#112D4E]/60 hover:text-[#112D4E] dark:text-[#DBE2EF]/60 dark:hover:text-white p-1 rounded-lg transition-colors"
          >
            <XCircle size={22} />
          </button>
        </div>
        
        <div className="mb-4 p-3.5 bg-[#3F72AF]/10 dark:bg-[#00A8CC]/10 border border-[#3F72AF]/20 dark:border-[#00A8CC]/20 rounded-xl">
          <p className="text-xs font-medium text-[#112D4E] dark:text-[#DBE2EF]">
            <strong>Node Installation:</strong> Copy the certificate below (click "Copy Certificate" button). 
            During node installation, you will be prompted to paste this certificate.
          </p>
        </div>

        {loading ? (
          <div className="flex-1 flex items-center justify-center min-h-[240px]">
            <div className="text-sm font-medium text-[#112D4E]/60 dark:text-[#DBE2EF]/60">Loading certificate...</div>
          </div>
        ) : (
          <>
            <textarea
              readOnly
              value={certContent}
              className="flex-1 w-full px-4 py-3 border border-[#DBE2EF] dark:border-[#142850] rounded-xl font-mono text-xs bg-[#F9F7F7] dark:bg-[#142850] text-[#112D4E] dark:text-[#00A8CC] resize-none focus:outline-none"
              style={{ minHeight: '280px' }}
            />
            
            <div className="flex justify-end gap-3 mt-4">
              <button
                type="button"
                onClick={async (e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  try {
                    if (certContent && certContent.trim().length > 0) {
                      await navigator.clipboard.writeText(certContent)
                      onCopy()
                    } else {
                      showToast('warning', 'Empty Certificate', 'Certificate content is empty. Please wait for it to load.')
                    }
                  } catch (error) {
                    console.error('Failed to copy:', error)
                    const textarea = e.currentTarget.closest('.bg-white, .dark\\:bg-[#27496D]')?.querySelector('textarea')
                    if (textarea) {
                      textarea.select()
                      textarea.setSelectionRange(0, 99999)
                      try {
                        document.execCommand('copy')
                        onCopy()
                      } catch (err) {
                        showToast('error', 'Copy Failed', 'Please select and copy manually from the text area.')
                      }
                    } else {
                      showToast('error', 'Copy Failed', 'Please select and copy manually from the text area.')
                    }
                  }
                }}
                disabled={loading || !certContent || certContent.trim().length === 0}
                className={`px-5 py-2.5 rounded-xl transition-all font-bold text-sm flex items-center gap-2 shadow-md ${
                  copied
                    ? 'bg-emerald-600 text-white shadow-emerald-600/20'
                    : 'bg-[#3F72AF] hover:bg-[#3F72AF]/90 dark:bg-gradient-to-r dark:from-[#0C7B93] dark:to-[#00A8CC] text-white shadow-[#3F72AF]/20 dark:shadow-[#00A8CC]/20 disabled:opacity-50 disabled:cursor-not-allowed'
                }`}
              >
                <Copy size={16} />
                <span>{copied ? 'Copied!' : 'Copy Certificate'}</span>
              </button>
              <button
                onClick={onClose}
                className="px-4 py-2.5 bg-[#DBE2EF]/60 hover:bg-[#DBE2EF] dark:bg-[#142850] dark:hover:bg-[#142850]/80 text-[#112D4E] dark:text-[#DBE2EF] rounded-xl font-semibold text-sm transition-colors"
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

