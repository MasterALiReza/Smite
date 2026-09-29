import { useState, useEffect } from 'react'
import { 
  Radio, 
  Send, 
  RefreshCw, 
  Save, 
  Plus, 
  Trash2, 
  CheckCircle2, 
  AlertCircle,
  Database,
  Sliders,
  Shield,
  ShieldCheck,
  Lock,
  Unlock,
  Globe,
  Copy,
  Check,
  ExternalLink,
  AlertTriangle,
  Key,
  FileText,
  Clock,
  Sparkles
} from 'lucide-react'
import api from '../api/client'
import { useLanguage } from '../contexts/LanguageContext'

interface SslCertInfo {
  installed: boolean
  valid?: boolean
  subject?: string
  issuer?: string
  not_valid_before?: string
  not_valid_after?: string
  days_remaining?: number
  san?: string[]
  fingerprint?: string
  status?: 'valid' | 'expiring_soon' | 'expired' | 'not_installed' | 'invalid'
  error?: string
}

interface SslStatusData {
  enabled: boolean
  domain: string
  email?: string
  mode: 'letsencrypt' | 'custom'
  auto_renew: boolean
  server_ip?: string | null
  cert_info?: SslCertInfo
}

interface DnsPrecheckResult {
  domain: string
  status: 'ready' | 'mismatch' | 'unresolved' | 'cloudflare_proxied' | 'invalid_domain' | 'error'
  server_ip?: string | null
  resolved_ips: string[]
  matches: boolean
  is_cloudflare: boolean
  warning?: string | null
  message: string
}

interface FrpSettings {
  enabled: boolean
  port: number
  token?: string
}

interface TelegramSettings {
  enabled: boolean
  bot_token?: string
  admin_ids: string[]
  backup_enabled?: boolean
  backup_interval?: number
  backup_interval_unit?: string
}

interface TunnelSettings {
  auto_reapply_enabled?: boolean
  auto_reapply_interval?: number
  auto_reapply_interval_unit?: string
}

interface SettingsData {
  frp: FrpSettings
  telegram: TelegramSettings
  tunnel?: TunnelSettings
}

const Settings = () => {
  const { t, isRTL } = useLanguage()
  const [settings, setSettings] = useState<SettingsData>({
    frp: { enabled: false, port: 7000 },
    telegram: { enabled: false, admin_ids: [] },
    tunnel: { auto_reapply_enabled: false, auto_reapply_interval: 60, auto_reapply_interval_unit: 'minutes' }
  })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null)
  const [newAdminId, setNewAdminId] = useState('')

  useEffect(() => {
    loadSettings()
  }, [])

  const loadSettings = async () => {
    try {
      const response = await api.get('/settings')
      setSettings(response.data)
    } catch (error) {
      console.error('Failed to load settings:', error)
      setMessage({ type: 'error', text: t.settings.failedToLoad })
    } finally {
      setLoading(false)
    }
  }

  // SSL & Domain Management State
  const [sslData, setSslData] = useState<SslStatusData | null>(null)
  const [sslLoading, setSslLoading] = useState(false)
  const [sslTab, setSslTab] = useState<'letsencrypt' | 'custom'>('letsencrypt')
  const [domainInput, setDomainInput] = useState('')
  const [emailInput, setEmailInput] = useState('')
  const [certPemInput, setCertPemInput] = useState('')
  const [keyPemInput, setKeyPemInput] = useState('')
  const [dnsCheckResult, setDnsCheckResult] = useState<DnsPrecheckResult | null>(null)
  const [dnsChecking, setDnsChecking] = useState(false)
  const [ipCopied, setIpCopied] = useState(false)
  const [issuing, setIssuing] = useState(false)
  const [renewing, setRenewing] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [showRemoveConfirm, setShowRemoveConfirm] = useState(false)
  const [showReconfigure, setShowReconfigure] = useState(false)

  const loadSslStatus = async () => {
    setSslLoading(true)
    try {
      const response = await api.get('/ssl')
      setSslData(response.data)
      if (response.data.domain && !domainInput) {
        setDomainInput(response.data.domain)
      }
      if (response.data.email && !emailInput) {
        setEmailInput(response.data.email)
      }
    } catch (error) {
      console.error('Failed to load SSL status:', error)
    } finally {
      setSslLoading(false)
    }
  }

  useEffect(() => {
    loadSslStatus()
  }, [])

  const handleCopyIp = () => {
    if (sslData?.server_ip) {
      navigator.clipboard.writeText(sslData.server_ip)
      setIpCopied(true)
      setTimeout(() => setIpCopied(false), 2000)
    }
  }

  const handleDnsPrecheck = async () => {
    if (!domainInput.trim()) return
    setDnsChecking(true)
    setDnsCheckResult(null)
    try {
      const res = await api.post('/ssl/precheck', { domain: domainInput.trim() })
      setDnsCheckResult(res.data)
    } catch (err: any) {
      const detail = err?.response?.data?.detail || 'DNS check failed'
      setMessage({ type: 'error', text: detail })
    } finally {
      setDnsChecking(false)
    }
  }

  const handleIssueLetsEncrypt = async () => {
    if (!domainInput.trim()) return
    setIssuing(true)
    setMessage(null)
    try {
      const res = await api.post('/ssl/issue', {
        domain: domainInput.trim(),
        email: emailInput.trim() || undefined,
        auto_renew: true
      })
      setMessage({ type: 'success', text: res.data?.message || t.settings.sslIssuedSuccess || 'SSL Certificate Issued!' })
      await loadSslStatus()
      setShowReconfigure(false)
    } catch (err: any) {
      const detail = err?.response?.data?.detail || 'Failed to issue SSL certificate'
      setMessage({ type: 'error', text: detail })
    } finally {
      setIssuing(false)
    }
  }

  const handleInstallCustomSsl = async () => {
    if (!domainInput.trim() || !certPemInput.trim() || !keyPemInput.trim()) return
    setIssuing(true)
    setMessage(null)
    try {
      const res = await api.post('/ssl/custom', {
        domain: domainInput.trim(),
        cert_pem: certPemInput.trim(),
        key_pem: keyPemInput.trim(),
        auto_renew: false
      })
      setMessage({ type: 'success', text: res.data?.message || t.settings.customSslSuccess || 'Custom SSL Installed!' })
      await loadSslStatus()
      setShowReconfigure(false)
      setCertPemInput('')
      setKeyPemInput('')
    } catch (err: any) {
      const detail = err?.response?.data?.detail || 'Failed to install custom SSL certificate'
      setMessage({ type: 'error', text: detail })
    } finally {
      setIssuing(false)
    }
  }

  const handleToggleAutoRenew = async (enabled: boolean) => {
    try {
      await api.put('/ssl/auto-renew', { enabled })
      if (sslData) {
        setSslData({ ...sslData, auto_renew: enabled })
      }
      setMessage({ type: 'success', text: 'Auto-renewal updated successfully' })
    } catch (err: any) {
      const detail = err?.response?.data?.detail || 'Failed to update auto-renewal'
      setMessage({ type: 'error', text: detail })
    }
  }

  const handleRenewSsl = async () => {
    setRenewing(true)
    setMessage(null)
    try {
      const res = await api.post('/ssl/renew')
      setMessage({ type: 'success', text: res.data?.message || t.settings.renewSuccess || 'Certificate renewed successfully!' })
      await loadSslStatus()
    } catch (err: any) {
      const detail = err?.response?.data?.detail || 'Failed to renew SSL'
      setMessage({ type: 'error', text: detail })
    } finally {
      setRenewing(false)
    }
  }

  const handleRemoveSsl = async () => {
    setRemoving(true)
    setMessage(null)
    try {
      await api.delete('/ssl')
      setMessage({ type: 'success', text: t.settings.sslRemovedSuccess || 'SSL disabled. Panel reverted to HTTP.' })
      await loadSslStatus()
      setShowRemoveConfirm(false)
      setShowReconfigure(false)
    } catch (err: any) {
      const detail = err?.response?.data?.detail || 'Failed to remove SSL'
      setMessage({ type: 'error', text: detail })
    } finally {
      setRemoving(false)
    }
  }

  const saveSettings = async () => {
    setSaving(true)
    setMessage(null)
    try {
      await api.put('/settings', settings)
      setMessage({ type: 'success', text: t.settings.settingsSaved })
      await loadSettings()
    } catch (error) {
      console.error('Failed to save settings:', error)
      setMessage({ type: 'error', text: t.settings.failedToSave })
    } finally {
      setSaving(false)
    }
  }

  const updateFrp = (updates: Partial<FrpSettings>) => {
    setSettings(prev => ({
      ...prev,
      frp: { ...prev.frp, ...updates }
    }))
  }

  const updateTelegram = (updates: Partial<TelegramSettings>) => {
    setSettings(prev => ({
      ...prev,
      telegram: { ...prev.telegram, ...updates }
    }))
  }

  const updateTunnel = (updates: Partial<TunnelSettings>) => {
    setSettings(prev => ({
      ...prev,
      tunnel: { ...prev.tunnel, ...updates } as TunnelSettings
    }))
  }

  const addAdminId = () => {
    if (newAdminId && newAdminId.trim()) {
      updateTelegram({
        admin_ids: [...settings.telegram.admin_ids, newAdminId.trim()]
      })
      setNewAdminId('')
    }
  }

  const removeAdminId = (index: number) => {
    updateTelegram({
      admin_ids: settings.telegram.admin_ids.filter((_, i) => i !== index)
    })
  }

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[50vh] gap-3 text-slate-500 dark:text-slate-400">
        <div className="relative">
          <div className="w-10 h-10 border-2 border-indigo-500/20 border-t-indigo-500 rounded-full animate-spin" />
        </div>
        <span className="text-xs font-mono uppercase tracking-wider">{t.settings.loadingSettings}</span>
      </div>
    )
  }

  return (
    <div className="w-full max-w-5xl mx-auto space-y-6 sm:space-y-8 animate-fade-in" dir={isRTL ? 'rtl' : 'ltr'}>
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 pb-2 border-b border-slate-200/60 dark:border-white/[0.06]">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="inline-flex items-center justify-center p-1.5 rounded-lg bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border border-indigo-500/20">
              <Sliders className="w-4 h-4" />
            </span>
            <span className="text-xs font-mono font-medium text-indigo-600 dark:text-indigo-400 uppercase tracking-wider">
              System Configuration
            </span>
          </div>
          <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 dark:text-white tracking-tight">
            {t.settings.title}
          </h1>
        </div>

        {/* Global Save Button - Quick Access */}
        <button
          onClick={saveSettings}
          disabled={saving}
          className="group relative inline-flex items-center justify-center gap-2.5 px-6 py-2.5 rounded-2xl bg-indigo-600 hover:bg-indigo-500 active:scale-[0.98] text-white font-medium text-xs sm:text-sm shadow-md shadow-indigo-500/20 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed min-h-[44px]"
        >
          {saving ? (
            <RefreshCw className="w-4 h-4 animate-spin" />
          ) : (
            <Save className="w-4 h-4 transition-transform group-hover:scale-110" />
          )}
          <span>{saving ? t.settings.saving : t.settings.saveSettings}</span>
        </button>
      </div>

      {/* Status Banner */}
      {message && (
        <div
          className={`flex items-start gap-3 p-4 rounded-2xl text-xs sm:text-sm font-medium border transition-all animate-slide-up ${
            message.type === 'success'
              ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/20 shadow-xs'
              : 'bg-rose-500/10 text-rose-700 dark:text-rose-300 border-rose-500/20 shadow-xs'
          }`}
        >
          {message.type === 'success' ? (
            <CheckCircle2 className="w-5 h-5 text-emerald-500 shrink-0 mt-0.5" />
          ) : (
            <AlertCircle className="w-5 h-5 text-rose-500 shrink-0 mt-0.5" />
          )}
          <span className="flex-1 leading-relaxed">{message.text}</span>
        </div>
      )}

      <div className="space-y-6">
        {/* 0. SSL & Domain Management Card */}
        <div className="bg-white dark:bg-[#12161f]/90 rounded-3xl border border-slate-200/80 dark:border-white/[0.07] p-5 sm:p-7 shadow-xs relative overflow-hidden group transition-all duration-300">
          {/* Card Header */}
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 pb-4 border-b border-slate-100 dark:border-white/[0.05]">
            <div className="flex items-start gap-3">
              <div className={`w-10 h-10 rounded-2xl border flex items-center justify-center shrink-0 mt-0.5 transition-colors ${
                sslData?.enabled && sslData?.cert_info?.valid
                  ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-500'
                  : 'bg-indigo-500/10 border-indigo-500/20 text-indigo-500'
              }`}>
                <ShieldCheck className="w-5 h-5" />
              </div>
              <div>
                <h2 className="text-base sm:text-lg font-bold text-slate-900 dark:text-white flex items-center gap-2">
                  <span>{t.settings.sslManagement || 'SSL & Domain Management'}</span>
                  {sslData?.mode === 'custom' && (
                    <span className="text-[10px] uppercase font-mono px-2 py-0.5 rounded-full bg-indigo-500/10 text-indigo-500 border border-indigo-500/20">
                      Custom
                    </span>
                  )}
                </h2>
                <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5 max-w-xl">
                  {t.settings.sslDescription || 'Secure your panel web interface with an automated Let\'s Encrypt certificate or custom SSL.'}
                </p>
              </div>
            </div>

            {/* Status Pill Badge */}
            <div className="flex items-center gap-2">
              {sslLoading ? (
                <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-mono bg-slate-100 dark:bg-slate-800 text-slate-500">
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  <span>Checking...</span>
                </div>
              ) : sslData?.enabled && sslData?.cert_info?.valid ? (
                <span className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 shadow-xs">
                  <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                  <span>{t.settings.sslActive || 'SSL Active & Secured'}</span>
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-xs font-semibold bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 border border-slate-200 dark:border-white/[0.08]">
                  <Unlock className="w-3.5 h-3.5" />
                  <span>{t.settings.sslInactive || 'SSL Inactive (HTTP)'}</span>
                </span>
              )}
            </div>
          </div>

          {/* Card Body */}
          <div className="mt-5 space-y-5">
            {/* If SSL Active and NOT Reconfiguring */}
            {sslData?.enabled && sslData?.cert_info?.valid && !showReconfigure ? (
              <div className="space-y-5">
                {/* Active SSL Status Showcase */}
                <div className="bg-emerald-500/[0.03] dark:bg-emerald-500/[0.02] border border-emerald-500/20 rounded-2xl p-4 sm:p-6 space-y-4">
                  <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                    <div className="space-y-1">
                      <span className="text-[11px] font-mono text-emerald-600 dark:text-emerald-400 uppercase tracking-wider font-semibold">
                        Domain & Protection
                      </span>
                      <div className="flex items-center gap-2">
                        <a
                          href={`https://${sslData.domain}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-lg sm:text-xl font-bold font-mono text-slate-900 dark:text-white hover:text-indigo-500 flex items-center gap-1.5 group/link"
                        >
                          <span>{sslData.domain}</span>
                          <ExternalLink className="w-4 h-4 text-slate-400 group-hover/link:text-indigo-500 transition-colors" />
                        </a>
                      </div>
                    </div>

                    {/* Expiration Counter Badge */}
                    <div className="text-right sm:text-left rtl:sm:text-right">
                      <div className="text-xs text-slate-500 dark:text-slate-400">
                        {t.settings.expiresOn || 'Expires On'}:{' '}
                        <span className="font-mono text-slate-900 dark:text-slate-200">
                          {sslData.cert_info.not_valid_after ? new Date(sslData.cert_info.not_valid_after).toLocaleDateString() : 'N/A'}
                        </span>
                      </div>
                      <div className="text-sm sm:text-base font-extrabold font-mono text-emerald-600 dark:text-emerald-400">
                        {sslData.cert_info.days_remaining} {t.settings.days || 'days'} {t.settings.daysRemaining || 'remaining'}
                      </div>
                    </div>
                  </div>

                  {/* Expiration Progress Bar */}
                  <div className="space-y-1.5 pt-1">
                    <div className="w-full h-2.5 bg-slate-200 dark:bg-slate-800 rounded-full overflow-hidden p-0.5">
                      <div
                        className={`h-full rounded-full transition-all duration-500 ${
                          (sslData.cert_info.days_remaining || 0) > 30
                            ? 'bg-gradient-to-r from-emerald-500 to-teal-400'
                            : (sslData.cert_info.days_remaining || 0) > 15
                            ? 'bg-gradient-to-r from-amber-500 to-yellow-400'
                            : 'bg-gradient-to-r from-rose-500 to-red-400'
                        }`}
                        style={{
                          width: `${Math.min(100, Math.max(5, ((sslData.cert_info.days_remaining || 0) / 90) * 100))}%`
                        }}
                      />
                    </div>
                  </div>

                  {/* Certificate Specs Grid */}
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-2 text-xs">
                    <div className="bg-white/80 dark:bg-black/30 p-3 rounded-xl border border-slate-200/50 dark:border-white/[0.04]">
                      <span className="text-slate-400 block mb-0.5">{t.settings.issuer || 'Issuer'}</span>
                      <span className="font-semibold text-slate-800 dark:text-slate-200 truncate block">
                        {sslData.cert_info.issuer || 'Let\'s Encrypt'}
                      </span>
                    </div>

                    <div className="bg-white/80 dark:bg-black/30 p-3 rounded-xl border border-slate-200/50 dark:border-white/[0.04]">
                      <span className="text-slate-400 block mb-0.5">{t.settings.subject || 'Subject'}</span>
                      <span className="font-semibold font-mono text-slate-800 dark:text-slate-200 truncate block">
                        {sslData.cert_info.subject || sslData.domain}
                      </span>
                    </div>

                    <div className="bg-white/80 dark:bg-black/30 p-3 rounded-xl border border-slate-200/50 dark:border-white/[0.04]">
                      <span className="text-slate-400 block mb-0.5">{t.settings.serverPublicIp || 'Server Public IP'}</span>
                      <span className="font-semibold font-mono text-slate-800 dark:text-slate-200 truncate block">
                        {sslData.server_ip || 'N/A'}
                      </span>
                    </div>
                  </div>

                  {/* Fingerprint (collapsible or single line) */}
                  {sslData.cert_info.fingerprint && (
                    <div className="pt-1 text-[11px] text-slate-500 dark:text-slate-400 flex items-center gap-2">
                      <span className="font-medium shrink-0">{t.settings.fingerprint || 'SHA-256'}:</span>
                      <span className="font-mono truncate bg-white/50 dark:bg-black/20 px-2 py-0.5 rounded border border-slate-200/40 dark:border-white/[0.04]">
                        {sslData.cert_info.fingerprint}
                      </span>
                    </div>
                  )}
                </div>

                {/* Auto-Renewal Switch (for Let's Encrypt) */}
                {sslData.mode === 'letsencrypt' && (
                  <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 p-4 rounded-2xl bg-slate-50/80 dark:bg-black/30 border border-slate-200/60 dark:border-white/[0.05]">
                    <div>
                      <h4 className="text-xs sm:text-sm font-bold text-slate-900 dark:text-white">
                        {t.settings.autoRenewal || 'Automatic Renewal'}
                      </h4>
                      <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
                        {t.settings.autoRenewalDesc || 'Automatically renews certificate every 12 hours when < 30 days remain'}
                      </p>
                    </div>

                    <button
                      type="button"
                      role="switch"
                      aria-checked={sslData.auto_renew}
                      onClick={() => handleToggleAutoRenew(!sslData.auto_renew)}
                      className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-emerald-500 ${
                        sslData.auto_renew ? 'bg-emerald-600' : 'bg-slate-200 dark:bg-slate-800'
                      }`}
                    >
                      <span
                        aria-hidden="true"
                        className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow-md ring-0 transition duration-200 ease-in-out ${
                          sslData.auto_renew
                            ? isRTL ? '-translate-x-5' : 'translate-x-5'
                            : 'translate-x-0'
                        }`}
                      />
                    </button>
                  </div>
                )}

                {/* Action Buttons Row */}
                <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
                  <div className="flex items-center gap-2">
                    {sslData.mode === 'letsencrypt' && (
                      <button
                        type="button"
                        onClick={handleRenewSsl}
                        disabled={renewing}
                        className="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 text-slate-800 dark:text-slate-200 text-xs font-semibold transition-all duration-200 disabled:opacity-50"
                      >
                        <RefreshCw className={`w-3.5 h-3.5 ${renewing ? 'animate-spin' : ''}`} />
                        <span>{renewing ? t.settings.renewing || 'Renewing...' : t.settings.renewNow || 'Renew Now'}</span>
                      </button>
                    )}

                    <button
                      type="button"
                      onClick={() => setShowReconfigure(true)}
                      className="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 text-slate-800 dark:text-slate-200 text-xs font-semibold transition-all duration-200"
                    >
                      <Globe className="w-3.5 h-3.5" />
                      <span>{t.settings.domainName || 'Change Domain'}</span>
                    </button>
                  </div>

                  {/* Remove SSL Button */}
                  <button
                    type="button"
                    onClick={() => setShowRemoveConfirm(true)}
                    className="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 text-xs font-semibold border border-rose-500/20 transition-all duration-200"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    <span>{t.settings.removeSsl || 'Remove SSL'}</span>
                  </button>
                </div>
              </div>
            ) : (
              /* Setup & Configuration Mode */
              <div className="space-y-5 animate-slide-up">
                {/* 1. Server Public IP & DNS Banner */}
                <div className="p-4 rounded-2xl bg-indigo-50/70 dark:bg-indigo-950/20 border border-indigo-200/60 dark:border-indigo-500/20 space-y-2">
                  <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-semibold text-indigo-900 dark:text-indigo-300">
                        {t.settings.serverPublicIp || 'Server Public IP'}:
                      </span>
                      <code className="px-2.5 py-1 rounded-lg bg-white dark:bg-slate-900 font-mono text-sm font-bold text-indigo-600 dark:text-indigo-400 border border-indigo-200 dark:border-indigo-500/30">
                        {sslData?.server_ip || 'Detecting...'}
                      </code>
                    </div>

                    <button
                      type="button"
                      onClick={handleCopyIp}
                      disabled={!sslData?.server_ip}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-white dark:bg-slate-900 hover:bg-slate-50 dark:hover:bg-slate-800 text-indigo-600 dark:text-indigo-400 border border-indigo-200 dark:border-indigo-500/30 text-xs font-semibold transition-all shadow-xs"
                    >
                      {ipCopied ? (
                        <>
                          <Check className="w-3.5 h-3.5 text-emerald-500" />
                          <span className="text-emerald-500">{t.settings.ipCopied || 'Copied!'}</span>
                        </>
                      ) : (
                        <>
                          <Copy className="w-3.5 h-3.5" />
                          <span>{t.settings.copyIp || 'Copy IP'}</span>
                        </>
                      )}
                    </button>
                  </div>
                  <p className="text-[11px] text-indigo-800/80 dark:text-indigo-300/70 leading-relaxed">
                    {t.settings.dnsInstructions || 'Make sure your domain DNS (A-Record) points to this server IP address before issuing.'}
                  </p>
                </div>

                {/* 2. Mode Selector Tabs */}
                <div className="flex items-center gap-2 p-1 rounded-2xl bg-slate-100 dark:bg-slate-900 border border-slate-200/80 dark:border-white/[0.06] max-w-md">
                  <button
                    type="button"
                    onClick={() => setSslTab('letsencrypt')}
                    className={`flex-1 py-2 px-3 rounded-xl text-xs font-semibold transition-all ${
                      sslTab === 'letsencrypt'
                        ? 'bg-white dark:bg-slate-800 text-indigo-600 dark:text-indigo-400 shadow-xs'
                        : 'text-slate-500 hover:text-slate-800 dark:hover:text-slate-200'
                    }`}
                  >
                    {t.settings.letsEncryptTab || 'Let\'s Encrypt (Automated)'}
                  </button>
                  <button
                    type="button"
                    onClick={() => setSslTab('custom')}
                    className={`flex-1 py-2 px-3 rounded-xl text-xs font-semibold transition-all ${
                      sslTab === 'custom'
                        ? 'bg-white dark:bg-slate-800 text-indigo-600 dark:text-indigo-400 shadow-xs'
                        : 'text-slate-500 hover:text-slate-800 dark:hover:text-slate-200'
                    }`}
                  >
                    {t.settings.customSslTab || 'Custom SSL (Cloudflare / Own)'}
                  </button>
                </div>

                {/* 3. Form Content */}
                {sslTab === 'letsencrypt' ? (
                  <div className="space-y-4">
                    {/* Domain Name Input */}
                    <div>
                      <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5">
                        {t.settings.domainName || 'Domain Name'} <span className="text-rose-500">*</span>
                      </label>
                      <div className="flex flex-col sm:flex-row gap-2">
                        <div className="relative flex-1">
                          <Globe className="w-4 h-4 text-slate-400 absolute left-3.5 rtl:left-auto rtl:right-3.5 top-1/2 -translate-y-1/2" />
                          <input
                            type="text"
                            value={domainInput}
                            onChange={(e) => {
                              setDomainInput(e.target.value)
                              setDnsCheckResult(null)
                            }}
                            placeholder={t.settings.domainPlaceholder || 'e.g. panel.example.com'}
                            className="w-full pl-10 pr-3.5 rtl:pl-3.5 rtl:pr-10 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-base sm:text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 min-h-[44px]"
                          />
                        </div>

                        {/* Test DNS Button */}
                        <button
                          type="button"
                          onClick={handleDnsPrecheck}
                          disabled={!domainInput.trim() || dnsChecking}
                          className="inline-flex items-center justify-center gap-1.5 px-4 py-2.5 rounded-xl bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 text-slate-800 dark:text-slate-200 text-xs font-semibold border border-slate-200 dark:border-white/[0.06] transition-all disabled:opacity-50 min-h-[44px]"
                        >
                          <RefreshCw className={`w-3.5 h-3.5 ${dnsChecking ? 'animate-spin' : ''}`} />
                          <span>{dnsChecking ? t.settings.testingDns || 'Testing...' : t.settings.testDns || 'Test DNS'}</span>
                        </button>
                      </div>
                    </div>

                    {/* Email Input */}
                    <div>
                      <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5">
                        {t.settings.emailOptional || 'Admin Email (Optional)'}
                      </label>
                      <input
                        type="email"
                        value={emailInput}
                        onChange={(e) => setEmailInput(e.target.value)}
                        placeholder={t.settings.emailPlaceholder || 'e.g. admin@example.com'}
                        className="w-full px-3.5 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-base sm:text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 min-h-[44px]"
                      />
                    </div>

                    {/* DNS Result Banner */}
                    {dnsCheckResult && (
                      <div className={`p-4 rounded-xl text-xs space-y-1 animate-slide-up border ${
                        dnsCheckResult.status === 'ready'
                          ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-700 dark:text-emerald-300'
                          : dnsCheckResult.status === 'cloudflare_proxied'
                          ? 'bg-amber-500/10 border-amber-500/20 text-amber-700 dark:text-amber-300'
                          : 'bg-rose-500/10 border-rose-500/20 text-rose-700 dark:text-rose-300'
                      }`}>
                        <div className="flex items-center gap-2 font-bold">
                          {dnsCheckResult.status === 'ready' ? (
                            <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                          ) : dnsCheckResult.status === 'cloudflare_proxied' ? (
                            <AlertTriangle className="w-4 h-4 text-amber-500" />
                          ) : (
                            <AlertCircle className="w-4 h-4 text-rose-500" />
                          )}
                          <span>
                            {dnsCheckResult.status === 'ready'
                              ? t.settings.dnsReady || 'DNS Points to Server!'
                              : dnsCheckResult.status === 'cloudflare_proxied'
                              ? t.settings.cfProxied || 'Cloudflare Proxy Detected'
                              : t.settings.dnsMismatch || 'DNS Mismatch or Unresolved'}
                          </span>
                        </div>
                        <p className="leading-relaxed pl-6 rtl:pl-0 rtl:pr-6">
                          {dnsCheckResult.warning || dnsCheckResult.message}
                        </p>
                      </div>
                    )}

                    {/* Issue Button */}
                    <div className="flex items-center gap-3 pt-2">
                      <button
                        type="button"
                        onClick={handleIssueLetsEncrypt}
                        disabled={!domainInput.trim() || issuing}
                        className="inline-flex items-center justify-center gap-2 px-6 py-2.5 rounded-2xl bg-indigo-600 hover:bg-indigo-500 active:scale-[0.98] text-white font-semibold text-xs sm:text-sm shadow-md shadow-indigo-500/20 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed min-h-[44px]"
                      >
                        {issuing ? (
                          <>
                            <RefreshCw className="w-4 h-4 animate-spin" />
                            <span>{t.settings.issuingSsl || 'Issuing Certificate...'}</span>
                          </>
                        ) : (
                          <>
                            <Lock className="w-4 h-4" />
                            <span>{t.settings.issueSslButton || 'Issue SSL Certificate'}</span>
                          </>
                        )}
                      </button>

                      {showReconfigure && (
                        <button
                          type="button"
                          onClick={() => setShowReconfigure(false)}
                          className="px-4 py-2.5 rounded-2xl bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 text-xs font-semibold min-h-[44px]"
                        >
                          Cancel
                        </button>
                      )}
                    </div>
                  </div>
                ) : (
                  /* Custom SSL Tab */
                  <div className="space-y-4">
                    {/* Domain Input */}
                    <div>
                      <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5">
                        {t.settings.domainName || 'Domain Name'} <span className="text-rose-500">*</span>
                      </label>
                      <input
                        type="text"
                        value={domainInput}
                        onChange={(e) => setDomainInput(e.target.value)}
                        placeholder={t.settings.domainPlaceholder || 'e.g. panel.example.com'}
                        className="w-full px-3.5 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-base sm:text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 min-h-[44px]"
                      />
                    </div>

                    {/* Certificate PEM */}
                    <div>
                      <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5">
                        {t.settings.certChain || 'Certificate Chain (PEM)'} <span className="text-rose-500">*</span>
                      </label>
                      <textarea
                        rows={4}
                        value={certPemInput}
                        onChange={(e) => setCertPemInput(e.target.value)}
                        placeholder={t.settings.certChainPlaceholder || '-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----'}
                        className="w-full p-3 font-mono text-xs border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-y"
                      />
                    </div>

                    {/* Private Key PEM */}
                    <div>
                      <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5">
                        {t.settings.privateKey || 'Private Key (PEM)'} <span className="text-rose-500">*</span>
                      </label>
                      <textarea
                        rows={4}
                        value={keyPemInput}
                        onChange={(e) => setKeyPemInput(e.target.value)}
                        placeholder={t.settings.privateKeyPlaceholder || '-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----'}
                        className="w-full p-3 font-mono text-xs border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-y"
                      />
                    </div>

                    {/* Install Custom Button */}
                    <div className="flex items-center gap-3 pt-2">
                      <button
                        type="button"
                        onClick={handleInstallCustomSsl}
                        disabled={!domainInput.trim() || !certPemInput.trim() || !keyPemInput.trim() || issuing}
                        className="inline-flex items-center justify-center gap-2 px-6 py-2.5 rounded-2xl bg-indigo-600 hover:bg-indigo-500 active:scale-[0.98] text-white font-semibold text-xs sm:text-sm shadow-md shadow-indigo-500/20 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed min-h-[44px]"
                      >
                        {issuing ? (
                          <>
                            <RefreshCw className="w-4 h-4 animate-spin" />
                            <span>{t.settings.installingCustomSsl || 'Validating & Installing...'}</span>
                          </>
                        ) : (
                          <>
                            <Key className="w-4 h-4" />
                            <span>{t.settings.installCustomSslButton || 'Install Custom Certificate'}</span>
                          </>
                        )}
                      </button>

                      {showReconfigure && (
                        <button
                          type="button"
                          onClick={() => setShowReconfigure(false)}
                          className="px-4 py-2.5 rounded-2xl bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 text-xs font-semibold min-h-[44px]"
                        >
                          Cancel
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Confirmation Modal for Removing SSL */}
        {showRemoveConfirm && (
          <div className="fixed inset-0 z-50 bg-black/50 backdrop-blur-xs flex items-center justify-center p-4">
            <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-white/[0.08] rounded-3xl p-6 max-w-md w-full shadow-2xl space-y-4 animate-scale-in">
              <div className="w-12 h-12 rounded-2xl bg-rose-500/10 border border-rose-500/20 text-rose-500 flex items-center justify-center mx-auto">
                <AlertTriangle className="w-6 h-6" />
              </div>
              <div className="text-center space-y-1">
                <h3 className="text-lg font-bold text-slate-900 dark:text-white">
                  {t.settings.removeSsl || 'Remove SSL Certificate'}
                </h3>
                <p className="text-xs text-slate-500 dark:text-slate-400 leading-relaxed">
                  {t.settings.removeSslConfirm || 'Are you sure you want to disable SSL and revert the panel to HTTP? (Node connections will not be affected).'}
                </p>
              </div>
              <div className="flex items-center gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowRemoveConfirm(false)}
                  disabled={removing}
                  className="flex-1 py-2.5 rounded-xl border border-slate-200 dark:border-white/[0.08] text-slate-700 dark:text-slate-300 font-semibold text-xs hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleRemoveSsl}
                  disabled={removing}
                  className="flex-1 py-2.5 rounded-xl bg-rose-600 hover:bg-rose-500 text-white font-semibold text-xs transition-colors flex items-center justify-center gap-1.5"
                >
                  {removing && <RefreshCw className="w-3.5 h-3.5 animate-spin" />}
                  <span>{removing ? t.settings.removingSsl || 'Removing...' : t.settings.removeSsl || 'Confirm Remove'}</span>
                </button>
              </div>
            </div>
          </div>
        )}

        {/* 1. FRP Communication Settings */}
        <div className="bg-white dark:bg-[#12161f]/90 rounded-3xl border border-slate-200/80 dark:border-white/[0.07] p-5 sm:p-7 shadow-xs relative overflow-hidden group">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 pb-4 border-b border-slate-100 dark:border-white/[0.05]">
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 rounded-2xl bg-sky-500/10 border border-sky-500/20 flex items-center justify-center text-sky-500 shrink-0 mt-0.5">
                <Radio className="w-5 h-5" />
              </div>
              <div>
                <h2 className="text-base sm:text-lg font-bold text-slate-900 dark:text-white">
                  {t.settings.frpCommunication}
                </h2>
                <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5 max-w-xl">
                  {t.settings.frpDescription}
                </p>
              </div>
            </div>

            {/* Custom Modern Switch */}
            <div className="flex items-center gap-3">
              <span className="text-xs font-mono font-medium text-slate-600 dark:text-slate-400">
                {settings.frp.enabled ? (
                  <span className="text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                    ACTIVE
                  </span>
                ) : (
                  <span className="text-slate-400 dark:text-slate-500">DISABLED</span>
                )}
              </span>
              <button
                type="button"
                id="frp-enabled"
                role="switch"
                aria-checked={settings.frp.enabled}
                onClick={() => updateFrp({ enabled: !settings.frp.enabled })}
                className={`relative inline-flex h-7 w-12 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 dark:focus:ring-offset-slate-900 min-h-[44px] items-center ${
                  settings.frp.enabled ? 'bg-indigo-600' : 'bg-slate-200 dark:bg-slate-800'
                }`}
              >
                <span
                  aria-hidden="true"
                  className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow-md ring-0 transition duration-200 ease-in-out ${
                    settings.frp.enabled
                      ? isRTL ? '-translate-x-5' : 'translate-x-5'
                      : 'translate-x-0.5'
                  }`}
                />
              </button>
            </div>
          </div>

          {settings.frp.enabled && (
            <div className="mt-5 grid grid-cols-1 sm:grid-cols-2 gap-4 sm:gap-6 pt-1 animate-slide-up">
              <div className="bg-slate-50/80 dark:bg-black/30 border border-slate-200/60 dark:border-white/[0.05] rounded-2xl p-4 sm:p-5">
                <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5">
                  {t.settings.frpPort}
                </label>
                <div className="relative">
                  <input
                    type="number"
                    value={settings.frp.port}
                    onChange={(e) => updateFrp({ port: parseInt(e.target.value) || 7000 })}
                    className="w-full px-3.5 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-base sm:text-sm font-mono tracking-wider focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 transition-all tabular-nums"
                    placeholder="7000"
                    min="1"
                    max="65535"
                  />
                </div>
                <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-2 leading-relaxed">
                  {t.settings.frpPortDescription}
                </p>
              </div>

              <div className="bg-slate-50/80 dark:bg-black/30 border border-slate-200/60 dark:border-white/[0.05] rounded-2xl p-4 sm:p-5">
                <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5">
                  {t.settings.frpTokenOptional}
                </label>
                <input
                  type="text"
                  value={settings.frp.token || ''}
                  onChange={(e) => updateFrp({ token: e.target.value })}
                  className="w-full px-3.5 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-base sm:text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 transition-all"
                  placeholder="Leave empty for no authentication"
                />
                <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-2 leading-relaxed">
                  {t.settings.frpTokenDescription}
                </p>
              </div>
            </div>
          )}
        </div>

        {/* 2. Telegram Bot Settings */}
        <div className="bg-white dark:bg-[#12161f]/90 rounded-3xl border border-slate-200/80 dark:border-white/[0.07] p-5 sm:p-7 shadow-xs relative overflow-hidden group">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 pb-4 border-b border-slate-100 dark:border-white/[0.05]">
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 rounded-2xl bg-blue-500/10 border border-blue-500/20 flex items-center justify-center text-blue-500 shrink-0 mt-0.5">
                <Send className="w-5 h-5" />
              </div>
              <div>
                <h2 className="text-base sm:text-lg font-bold text-slate-900 dark:text-white">
                  {t.settings.telegramBot}
                </h2>
                <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5 max-w-xl">
                  {t.settings.telegramDescription}
                </p>
              </div>
            </div>

            {/* Custom Modern Switch */}
            <div className="flex items-center gap-3">
              <span className="text-xs font-mono font-medium text-slate-600 dark:text-slate-400">
                {settings.telegram.enabled ? (
                  <span className="text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                    ACTIVE
                  </span>
                ) : (
                  <span className="text-slate-400 dark:text-slate-500">DISABLED</span>
                )}
              </span>
              <button
                type="button"
                id="telegram-enabled"
                role="switch"
                aria-checked={settings.telegram.enabled}
                onClick={() => updateTelegram({ enabled: !settings.telegram.enabled })}
                className={`relative inline-flex h-7 w-12 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 dark:focus:ring-offset-slate-900 min-h-[44px] items-center ${
                  settings.telegram.enabled ? 'bg-indigo-600' : 'bg-slate-200 dark:bg-slate-800'
                }`}
              >
                <span
                  aria-hidden="true"
                  className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow-md ring-0 transition duration-200 ease-in-out ${
                    settings.telegram.enabled
                      ? isRTL ? '-translate-x-5' : 'translate-x-5'
                      : 'translate-x-0.5'
                  }`}
                />
              </button>
            </div>
          </div>

          {settings.telegram.enabled && (
            <div className="mt-5 space-y-6 animate-slide-up">
              {/* Bot Token */}
              <div className="bg-slate-50/80 dark:bg-black/30 border border-slate-200/60 dark:border-white/[0.05] rounded-2xl p-4 sm:p-5">
                <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5">
                  {t.settings.botToken}
                </label>
                <input
                  type="password"
                  value={settings.telegram.bot_token || ''}
                  onChange={(e) => updateTelegram({ bot_token: e.target.value })}
                  className="w-full px-3.5 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-base sm:text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 transition-all min-h-[44px]"
                  placeholder="Enter bot token from @BotFather"
                />
                <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-2 leading-relaxed">
                  {t.settings.botTokenDescription}
                </p>
              </div>

              {/* Admin User IDs */}
              <div className="bg-slate-50/80 dark:bg-black/30 border border-slate-200/60 dark:border-white/[0.05] rounded-2xl p-4 sm:p-5">
                <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-2">
                  {t.settings.adminUserIds}
                </label>
                <div className="space-y-2.5">
                  {settings.telegram.admin_ids.map((id, index) => (
                    <div key={index} className="flex items-center gap-2 group/id">
                      <div className="flex-1 relative">
                        <input
                          type="text"
                          value={id}
                          onChange={(e) => {
                            const newIds = [...settings.telegram.admin_ids]
                            newIds[index] = e.target.value
                            updateTelegram({ admin_ids: newIds })
                          }}
                          className="w-full px-3.5 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-base sm:text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 min-h-[44px]"
                        />
                      </div>
                      <button
                        onClick={() => removeAdminId(index)}
                        className="inline-flex items-center justify-center p-2.5 rounded-xl bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/20 transition-colors min-h-[44px] min-w-[44px]"
                        title={t.settings.remove}
                        aria-label={t.settings.remove}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  ))}

                  {/* Add Admin ID Input */}
                  <div className="flex items-center gap-2 pt-1">
                    <input
                      type="text"
                      value={newAdminId}
                      onChange={(e) => setNewAdminId(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && newAdminId.trim()) {
                          e.preventDefault()
                          addAdminId()
                        }
                      }}
                      placeholder={t.settings.enterAdminId || 'Enter Telegram Admin ID'}
                      className="flex-1 px-3.5 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-base sm:text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 min-h-[44px]"
                    />
                    <button
                      onClick={addAdminId}
                      disabled={!newAdminId.trim()}
                      className="inline-flex items-center justify-center gap-1.5 px-4 py-2.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-xl text-xs font-semibold min-h-[44px] transition-all shadow-xs"
                    >
                      <Plus className="w-4 h-4" />
                      <span>{t.settings.addAdminId || 'Add'}</span>
                    </button>
                  </div>
                </div>
                <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-2.5 leading-relaxed">
                  {t.settings.adminUserIdsDescription}
                </p>
              </div>

              {/* Automatic Database Backup */}
              <div className="bg-slate-50/80 dark:bg-black/30 border border-slate-200/60 dark:border-white/[0.05] rounded-2xl p-4 sm:p-5">
                <div className="flex items-center justify-between pb-3 border-b border-slate-200/60 dark:border-white/[0.05]">
                  <div className="flex items-center gap-2.5">
                    <Database className="w-4 h-4 text-indigo-500" />
                    <h3 className="text-sm font-bold text-slate-900 dark:text-white">
                      {t.settings.automaticBackup}
                    </h3>
                  </div>

                  <label className="relative inline-flex items-center cursor-pointer min-h-[44px]">
                    <input
                      type="checkbox"
                      id="backup-enabled"
                      checked={settings.telegram.backup_enabled || false}
                      onChange={(e) => updateTelegram({ backup_enabled: e.target.checked })}
                      className="sr-only peer"
                    />
                    <div className="w-11 h-6 bg-slate-200 dark:bg-slate-800 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-indigo-500 rounded-full peer peer-checked:after:translate-x-full rtl:peer-checked:after:-translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[12px] after:start-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-indigo-600"></div>
                  </label>
                </div>

                {settings.telegram.backup_enabled && (
                  <div className="mt-4 space-y-3 animate-slide-up">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                      <div>
                        <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5">
                          {t.settings.backupInterval}
                        </label>
                        <input
                          type="number"
                          value={settings.telegram.backup_interval || 60}
                          onChange={(e) => updateTelegram({ backup_interval: parseInt(e.target.value) || 60 })}
                          className="w-full px-3.5 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-base sm:text-sm font-mono tabular-nums focus:outline-none focus:ring-2 focus:ring-indigo-500 min-h-[44px]"
                          placeholder="60"
                          min="1"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5">
                          {t.settings.intervalUnit}
                        </label>
                        <select
                          value={settings.telegram.backup_interval_unit || 'minutes'}
                          onChange={(e) => updateTelegram({ backup_interval_unit: e.target.value })}
                          className="w-full px-3.5 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-base sm:text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 min-h-[44px]"
                        >
                          <option value="minutes">{t.settings.minutes}</option>
                          <option value="hours">{t.settings.hours}</option>
                        </select>
                      </div>
                    </div>
                    <p className="text-[11px] text-slate-500 dark:text-slate-400 leading-relaxed">
                      {t.settings.backupDescription}
                    </p>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* 3. Tunnel Settings (Auto Reapply) */}
        <div className="bg-white dark:bg-[#12161f]/90 rounded-3xl border border-slate-200/80 dark:border-white/[0.07] p-5 sm:p-7 shadow-xs relative overflow-hidden group">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 pb-4 border-b border-slate-100 dark:border-white/[0.05]">
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-500 shrink-0 mt-0.5">
                <RefreshCw className="w-5 h-5" />
              </div>
              <div>
                <h2 className="text-base sm:text-lg font-bold text-slate-900 dark:text-white">
                  {t.settings.tunnelAutoReapply || 'Tunnel Auto Reapply'}
                </h2>
                <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5 max-w-xl">
                  {t.settings.tunnelAutoReapplyDescription || 'Automatically reapply all tunnels at specified intervals'}
                </p>
              </div>
            </div>

            {/* Custom Modern Switch */}
            <div className="flex items-center gap-3">
              <span className="text-xs font-mono font-medium text-slate-600 dark:text-slate-400">
                {settings.tunnel?.auto_reapply_enabled ? (
                  <span className="text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                    ACTIVE
                  </span>
                ) : (
                  <span className="text-slate-400 dark:text-slate-500">DISABLED</span>
                )}
              </span>
              <button
                type="button"
                role="switch"
                aria-checked={settings.tunnel?.auto_reapply_enabled || false}
                onClick={() => updateTunnel({ auto_reapply_enabled: !(settings.tunnel?.auto_reapply_enabled || false) })}
                className={`relative inline-flex h-7 w-12 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 dark:focus:ring-offset-slate-900 min-h-[44px] items-center ${
                  settings.tunnel?.auto_reapply_enabled ? 'bg-indigo-600' : 'bg-slate-200 dark:bg-slate-800'
                }`}
              >
                <span
                  aria-hidden="true"
                  className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow-md ring-0 transition duration-200 ease-in-out ${
                    settings.tunnel?.auto_reapply_enabled
                      ? isRTL ? '-translate-x-5' : 'translate-x-5'
                      : 'translate-x-0.5'
                  }`}
                />
              </button>
            </div>
          </div>

          {settings.tunnel?.auto_reapply_enabled && (
            <div className="mt-5 bg-slate-50/80 dark:bg-black/30 border border-slate-200/60 dark:border-white/[0.05] rounded-2xl p-4 sm:p-5 animate-slide-up">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5">
                    {t.settings.tunnelReapplyInterval || 'Reapply Interval'}
                  </label>
                  <input
                    type="number"
                    value={settings.tunnel?.auto_reapply_interval || 60}
                    onChange={(e) => updateTunnel({ auto_reapply_interval: parseInt(e.target.value) || 60 })}
                    className="w-full px-3.5 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-base sm:text-sm font-mono tabular-nums focus:outline-none focus:ring-2 focus:ring-indigo-500 min-h-[44px]"
                    placeholder="60"
                    min="1"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5">
                    {t.settings.intervalUnit || 'Interval Unit'}
                  </label>
                  <select
                    value={settings.tunnel?.auto_reapply_interval_unit || 'minutes'}
                    onChange={(e) => updateTunnel({ auto_reapply_interval_unit: e.target.value })}
                    className="w-full px-3.5 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-base sm:text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 min-h-[44px]"
                  >
                    <option value="minutes">{t.settings.minutes}</option>
                    <option value="hours">{t.settings.hours}</option>
                  </select>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Bottom Save Action Bar */}
        <div className="flex items-center justify-between pt-2">
          <span className="text-xs text-slate-400 dark:text-slate-500 hidden sm:inline">
            Changes will take effect immediately upon saving.
          </span>
          <button
            onClick={saveSettings}
            disabled={saving}
            className="w-full sm:w-auto inline-flex items-center justify-center gap-2 px-8 py-3.5 rounded-2xl bg-indigo-600 hover:bg-indigo-500 active:scale-[0.98] text-white font-semibold text-sm shadow-lg shadow-indigo-500/25 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed min-h-[48px]"
          >
            {saving ? (
              <RefreshCw className="w-4 h-4 animate-spin" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            <span>{saving ? t.settings.saving : t.settings.saveSettings}</span>
          </button>
        </div>
      </div>
    </div>
  )
}

export default Settings
