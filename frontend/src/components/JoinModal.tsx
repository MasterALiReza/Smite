import React, { useEffect, useState } from 'react'
import { Terminal, Copy, CheckCircle, XCircle, Sparkles, Server } from 'lucide-react'
import api from '../api/client'
import { useToast } from '../contexts/ToastContext'
import { copyTextToClipboard } from '../utils/clipboard'

interface JoinModalProps {
  isOpen: boolean
  onClose: () => void
  role: 'foreign' | 'iran'
  onNodeRegistered?: () => void
}

export const JoinModal: React.FC<JoinModalProps> = ({ isOpen, onClose, role, onNodeRegistered }) => {
  const { showToast } = useToast()
  const [token, setToken] = useState('')
  const [command, setCommand] = useState('')
  const [loading, setLoading] = useState(true)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (isOpen) {
      fetchJoinToken()
    }
  }, [isOpen, role])

  const fetchJoinToken = async () => {
    setLoading(true)
    try {
      const resp = await api.get(`/panel/join-command?role=${role}`)
      const regToken = resp.data.token
      setToken(regToken)
      
      const host = window.location.host || '127.0.0.1:8000'
      const cmd = `curl -sSL https://raw.githubusercontent.com/MasterALiReza/Smite/main/scripts/smite-node.sh | sudo bash -s -- --panel ${host} --token ${regToken} --role ${role}`
      setCommand(cmd)
    } catch (err: any) {
      console.error('Failed to get join token:', err)
      showToast('error', 'Error', 'Failed to generate join command')
    } finally {
      setLoading(false)
    }
  }

  const handleCopy = async () => {
    if (!command) return
    const success = await copyTextToClipboard(command)
    if (success) {
      setCopied(true)
      showToast('success', 'Copied', 'Install command copied to clipboard!', 2000)
      setTimeout(() => setCopied(false), 2000)
    } else {
      showToast('error', 'Copy Failed', 'Please select and copy manually')
    }
  }

  if (!isOpen) return null

  const roleTitle = role === 'foreign' ? 'Foreign Server' : 'Iran Node'

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 w-full max-w-2xl shadow-2xl border border-gray-100 dark:border-gray-700 flex flex-col animate-in fade-in zoom-in duration-200">
        <div className="flex justify-between items-center mb-4">
          <div className="flex items-center gap-2.5">
            <div className="p-2 bg-gradient-to-tr from-blue-600 to-indigo-600 text-white rounded-xl shadow-md">
              <Sparkles size={20} />
            </div>
            <div>
              <h2 className="text-xl font-bold text-gray-900 dark:text-white">
                One-Click Auto Join ({roleTitle})
              </h2>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                Zero-Touch automatic discovery & registration
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

        <div className="mb-4 p-3.5 bg-blue-50/70 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800/50 rounded-xl">
          <p className="text-sm text-blue-900 dark:text-blue-200 leading-relaxed">
            Run the command below in your server terminal. The node will <strong>auto-detect its public IP</strong>, 
            configure an available port, start Docker, and <strong>automatically appear as Connected in this panel</strong>.
          </p>
        </div>

        {loading ? (
          <div className="py-12 flex flex-col items-center justify-center gap-3">
            <div className="w-8 h-8 border-3 border-blue-600 border-t-transparent rounded-full animate-spin"></div>
            <div className="text-sm text-gray-500 dark:text-gray-400">Generating secure join token...</div>
          </div>
        ) : (
          <>
            <div className="relative">
              <label className="block text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-1.5 flex items-center gap-1.5">
                <Terminal size={14} /> One-Line Bash Command
              </label>
              <textarea
                readOnly
                value={command}
                rows={4}
                className="w-full px-4 py-3 border border-gray-300 dark:border-gray-600 rounded-xl font-mono text-sm bg-gray-900 text-green-400 shadow-inner resize-none focus:outline-none select-all"
                onClick={(e) => (e.target as HTMLTextAreaElement).select()}
              />
            </div>

            <div className="flex justify-between items-center mt-5 pt-3 border-t border-gray-100 dark:border-gray-700">
              <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500"></span>
                </span>
                Waiting for node registration...
              </div>

              <div className="flex gap-2.5">
                <button
                  type="button"
                  onClick={handleCopy}
                  className={`px-5 py-2.5 rounded-xl font-medium transition-all shadow-sm flex items-center gap-2 text-sm ${
                    copied
                      ? 'bg-green-600 text-white'
                      : 'bg-gradient-to-r from-blue-600 to-indigo-600 text-white hover:from-blue-700 hover:to-indigo-700'
                  }`}
                >
                  {copied ? <CheckCircle size={16} /> : <Copy size={16} />}
                  {copied ? 'Command Copied!' : 'Copy Command'}
                </button>
                <button
                  type="button"
                  onClick={onClose}
                  className="px-4 py-2.5 bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-200 rounded-xl hover:bg-gray-200 dark:hover:bg-gray-600 text-sm font-medium"
                >
                  Close
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
