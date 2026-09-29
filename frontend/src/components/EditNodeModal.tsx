import React, { useEffect, useState } from 'react'
import { Edit2, Save, X } from 'lucide-react'
import api from '../api/client'
import { useToast } from '../contexts/ToastContext'
import { getCountryFlag } from '../utils/country'

interface EditNodeModalProps {
  isOpen: boolean
  node: {
    id: string
    name: string
    fingerprint?: string
    metadata?: Record<string, any>
  } | null
  onClose: () => void
  onSuccess: () => void
}

export const EditNodeModal: React.FC<EditNodeModalProps> = ({
  isOpen,
  node,
  onClose,
  onSuccess,
}) => {
  const { showToast } = useToast()
  const [name, setName] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (node) {
      setName(node.name || '')
    }
  }, [node])

  if (!isOpen || !node) return null

  const countryCode = node.metadata?.country_code
  const flag = getCountryFlag(countryCode)
  const ipAddress = node.metadata?.ip_address || 'Unknown'

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) {
      showToast('warning', 'Validation', 'Node name cannot be empty')
      return
    }

    setLoading(true)
    try {
      await api.put(`/nodes/${node.id}`, {
        name: name.trim(),
      })
      showToast('success', 'Updated', 'Node name updated successfully!')
      onSuccess()
      onClose()
    } catch (err: any) {
      console.error('Failed to update node:', err)
      showToast('error', 'Error', err.response?.data?.detail || 'Failed to update node name')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div 
      className="fixed inset-0 bg-black/75 backdrop-blur-md flex items-center justify-center z-50 p-4 animate-in fade-in duration-200"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="relative bg-white dark:bg-[#0c1220] rounded-3xl p-6 sm:p-7 w-full max-w-md max-h-[92dvh] overflow-y-auto shadow-2xl border border-slate-200/90 dark:border-white/[0.1] flex flex-col">
        <div className="flex justify-between items-center mb-5 pb-3 border-b border-slate-100 dark:border-white/[0.06]">
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-blue-500/10 dark:bg-sky-500/15 text-blue-600 dark:text-sky-400 rounded-2xl border border-blue-200/50 dark:border-sky-500/20 shadow-glow-sm">
              <Edit2 size={18} />
            </div>
            <div>
              <h2 className="text-base sm:text-lg font-bold text-slate-900 dark:text-white tracking-tight">
                Edit Display Name
              </h2>
              <p className="text-xs text-slate-500 dark:text-slate-400 font-medium">
                Change the visible alias for this node
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 p-2 rounded-xl hover:bg-slate-100 dark:hover:bg-white/[0.06] transition-colors cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center"
            aria-label="Close"
          >
            <X size={19} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="p-3.5 bg-slate-50 dark:bg-white/[0.03] rounded-2xl border border-slate-200/70 dark:border-white/[0.06] space-y-2 text-xs">
            <div className="flex justify-between items-center text-slate-600 dark:text-slate-300">
              <span className="text-slate-400 dark:text-slate-500 font-medium">IP Address:</span>
              <span className="font-mono font-bold text-slate-900 dark:text-white tabular-nums">{ipAddress}</span>
            </div>
            {countryCode && (
              <div className="flex justify-between items-center text-slate-600 dark:text-slate-300">
                <span className="text-slate-400 dark:text-slate-500 font-medium">Location:</span>
                <span className="flex items-center gap-1.5 font-bold font-mono">
                  <span>{flag}</span>
                  <span>{countryCode}</span>
                </span>
              </div>
            )}
            {node.fingerprint && (
              <div className="flex justify-between items-center text-slate-600 dark:text-slate-300">
                <span className="text-slate-400 dark:text-slate-500 font-medium">Fingerprint:</span>
                <span className="font-mono text-[11px] text-slate-500 dark:text-slate-400 truncate max-w-[200px]">{node.fingerprint}</span>
              </div>
            )}
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5 tracking-tight">
              Node Display Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. DE Node 1, Germany Main, etc."
              className="w-full px-4 py-2.5 bg-slate-50/50 dark:bg-[#070b14]/70 border border-slate-200 dark:border-white/[0.1] rounded-xl text-base sm:text-sm text-slate-900 dark:text-white placeholder-slate-400 dark:placeholder-slate-500 focus:ring-2 focus:ring-sky-500/40 focus:border-sky-500 outline-none transition-all min-h-[44px]"
              autoFocus
            />
          </div>

          <div className="flex justify-end gap-2.5 pt-4 border-t border-slate-100 dark:border-white/[0.06]">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2.5 bg-slate-100 dark:bg-white/[0.06] hover:bg-slate-200 dark:hover:bg-white/[0.1] text-slate-700 dark:text-slate-300 text-xs font-semibold rounded-xl transition-all cursor-pointer min-h-[44px]"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              className="px-5 py-2.5 bg-gradient-to-r from-blue-600 via-sky-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white text-xs font-semibold rounded-xl shadow-md shadow-blue-600/25 transition-all flex items-center gap-2 disabled:opacity-50 cursor-pointer min-h-[44px] active:scale-95"
            >
              <Save size={15} />
              <span>{loading ? 'Saving...' : 'Save Changes'}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
