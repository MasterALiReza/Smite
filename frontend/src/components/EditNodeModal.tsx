import React, { useEffect, useState } from 'react'
import { Edit2, Save, XCircle, Server, Globe } from 'lucide-react'
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
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 w-full max-w-md shadow-2xl border border-gray-100 dark:border-gray-700 flex flex-col animate-in fade-in zoom-in duration-200">
        <div className="flex justify-between items-center mb-5">
          <div className="flex items-center gap-2.5">
            <div className="p-2 bg-blue-600/10 dark:bg-blue-500/20 text-blue-600 dark:text-blue-400 rounded-xl">
              <Edit2 size={20} />
            </div>
            <div>
              <h2 className="text-lg font-bold text-gray-900 dark:text-white">
                Edit Node
              </h2>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                Change the display name of this node
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 p-1 rounded-lg"
          >
            <XCircle size={22} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="p-3 bg-gray-50 dark:bg-gray-700/50 rounded-xl border border-gray-100 dark:border-gray-700 space-y-1.5 text-xs">
            <div className="flex justify-between items-center text-gray-600 dark:text-gray-300">
              <span className="text-gray-400 dark:text-gray-500">IP Address:</span>
              <span className="font-mono font-medium">{ipAddress}</span>
            </div>
            {countryCode && (
              <div className="flex justify-between items-center text-gray-600 dark:text-gray-300">
                <span className="text-gray-400 dark:text-gray-500">Location:</span>
                <span className="flex items-center gap-1.5 font-medium">
                  <span>{flag}</span>
                  <span>{countryCode}</span>
                </span>
              </div>
            )}
            {node.fingerprint && (
              <div className="flex justify-between items-center text-gray-600 dark:text-gray-300">
                <span className="text-gray-400 dark:text-gray-500">Fingerprint:</span>
                <span className="font-mono text-[11px] text-gray-500">{node.fingerprint}</span>
              </div>
            )}
          </div>

          <div>
            <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
              Node Name
            </label>
            <div className="relative">
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. DE Node 1, Germany Main, etc."
                className="w-full px-3.5 py-2.5 bg-gray-50 dark:bg-gray-900 border border-gray-300 dark:border-gray-600 rounded-xl text-sm text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all"
                autoFocus
              />
            </div>
          </div>

          <div className="flex justify-end gap-2.5 pt-3 border-t border-gray-100 dark:border-gray-700">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 text-sm font-medium rounded-xl hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              className="px-5 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-xl shadow-sm hover:shadow transition-all flex items-center gap-2 disabled:opacity-50"
            >
              <Save size={16} />
              {loading ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
