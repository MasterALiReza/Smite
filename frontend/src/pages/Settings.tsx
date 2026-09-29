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
  Clock,
  Sparkles
} from 'lucide-react'
import api from '../api/client'
import { useLanguage } from '../contexts/LanguageContext'

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
                    className="w-full px-3.5 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-sm font-mono tracking-wider focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 transition-all tabular-nums"
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
                  className="w-full px-3.5 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 transition-all"
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
                  className="w-full px-3.5 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 transition-all"
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
                          className="w-full px-3.5 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
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
                      className="flex-1 px-3.5 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
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
                          className="w-full px-3.5 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-sm font-mono tabular-nums focus:outline-none focus:ring-2 focus:ring-indigo-500"
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
                          className="w-full px-3.5 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
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
                    className="w-full px-3.5 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-sm font-mono tabular-nums focus:outline-none focus:ring-2 focus:ring-indigo-500"
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
                    className="w-full px-3.5 py-2.5 border border-slate-200 dark:border-white/[0.08] rounded-xl bg-white dark:bg-slate-900 text-slate-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
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
