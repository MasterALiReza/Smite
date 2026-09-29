import React, { useEffect, useState } from 'react'
import { Terminal, Copy, CheckCircle, X, Sparkles } from 'lucide-react'
import api from '../api/client'
import { useToast } from '../contexts/ToastContext'
import { copyTextToClipboard } from '../utils/clipboard'

interface JoinModalProps {
  isOpen: boolean
  onClose: () => void
  role: 'foreign' | 'iran'
  onNodeRegistered?: () => void
}

export const JoinModal: React.FC<JoinModalProps> = ({ isOpen, onClose, role }) => {
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
    <div 
      className="fixed inset-0 bg-black/75 backdrop-blur-md flex items-center justify-center z-50 p-4 animate-in fade-in duration-200"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="relative bg-white dark:bg-[#0c1220] rounded-3xl p-6 sm:p-7 w-full max-w-2xl shadow-2xl border border-slate-200/90 dark:border-white/[0.1] flex flex-col overflow-hidden">
        {/* Modal Header */}
        <div className="flex justify-between items-center mb-5 pb-3 border-b border-slate-100 dark:border-white/[0.06]">
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-gradient-to-tr from-blue-600 via-sky-600 to-indigo-600 text-white rounded-2xl shadow-glow-sm">
              <Sparkles size={18} />
            </div>
            <div>
              <h2 className="text-base sm:text-lg font-bold text-slate-900 dark:text-white tracking-tight">
                One-Click Auto Join ({roleTitle})
              </h2>
              <p className="text-xs text-slate-500 dark:text-slate-400 font-medium">
                Zero-Touch automatic discovery & registration
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 p-1.5 rounded-xl hover:bg-slate-100 dark:hover:bg-white/[0.06] transition-colors"
            aria-label="Close"
          >
            <X size={19} />
          </button>
        </div>

        <div className="mb-5 p-3.5 bg-sky-50/80 dark:bg-sky-950/30 border border-sky-200/70 dark:border-sky-800/40 rounded-2xl">
          <p className="text-xs sm:text-sm text-sky-900 dark:text-sky-200 leading-relaxed font-medium">
            Run the command below in your server terminal. The node will <strong>auto-detect its public IP</strong>, 
            configure an available port, start Docker, and <strong>automatically appear as Connected in this panel</strong>.
          </p>
        </div>

        {loading ? (
          <div className="py-14 flex flex-col items-center justify-center gap-3">
            <div className="w-8 h-8 border-2 border-sky-500 border-t-transparent rounded-full animate-spin"></div>
            <div className="text-xs font-mono text-slate-500 dark:text-slate-400">Generating secure join token...</div>
          </div>
        ) : (
          <>
            {/* Terminal Window Box */}
            <div className="relative rounded-2xl overflow-hidden border border-slate-800 bg-[#090d16] shadow-xl">
              <div className="flex items-center justify-between px-4 py-2.5 bg-[#0e1422] border-b border-white/[0.06]">
                <div className="flex items-center gap-1.5">
                  <span className="w-2.5 h-2.5 rounded-full bg-rose-500/80"></span>
                  <span className="w-2.5 h-2.5 rounded-full bg-amber-500/80"></span>
                  <span className="w-2.5 h-2.5 rounded-full bg-emerald-500/80"></span>
                </div>
                <span className="text-[11px] font-mono text-slate-400 flex items-center gap-1.5">
                  <Terminal size={12} className="text-sky-400" /> bash
                </span>
              </div>
              <textarea
                readOnly
                value={command}
                rows={4}
                className="w-full px-4 py-3.5 font-mono text-xs sm:text-sm bg-transparent text-emerald-400 resize-none focus:outline-none select-all leading-relaxed"
                onClick={(e) => (e.target as HTMLTextAreaElement).select()}
              />
            </div>

            {/* Modal Footer */}
            <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-3 mt-6 pt-3 border-t border-slate-100 dark:border-white/[0.06]">
              <div className="flex items-center gap-2 text-xs font-mono text-slate-500 dark:text-slate-400">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
                </span>
                <span>Listening for reverse ping...</span>
              </div>

              <div className="flex items-center justify-end gap-2.5">
                <button
                  type="button"
                  onClick={onClose}
                  className="px-4 py-2.5 bg-slate-100 dark:bg-white/[0.06] hover:bg-slate-200 dark:hover:bg-white/[0.1] text-slate-700 dark:text-slate-200 rounded-xl text-xs font-semibold transition-all cursor-pointer min-h-[40px]"
                >
                  Close
                </button>
                <button
                  type="button"
                  onClick={handleCopy}
                  className={`px-5 py-2.5 rounded-xl font-semibold transition-all shadow-md flex items-center gap-2 text-xs cursor-pointer min-h-[40px] active:scale-95 ${
                    copied
                      ? 'bg-emerald-600 text-white shadow-emerald-600/30'
                      : 'bg-gradient-to-r from-blue-600 via-sky-600 to-indigo-600 text-white hover:from-blue-500 hover:to-indigo-500 shadow-blue-600/25'
                  }`}
                >
                  {copied ? <CheckCircle size={15} /> : <Copy size={15} />}
                  <span>{copied ? 'Command Copied!' : 'Copy Command'}</span>
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
