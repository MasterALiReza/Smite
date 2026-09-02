import { useState, useEffect } from 'react'
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
  const { t } = useLanguage()
  const [settings, setSettings] = useState<SettingsData>({
    frp: { enabled: false, port: 7000 },
    telegram: { enabled: false, admin_ids: [] },
    tunnel: { auto_reapply_enabled: false, auto_reapply_interval: 60, auto_reapply_interval_unit: 'minutes' }
  })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null)

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

  const [newAdminId, setNewAdminId] = useState('')

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
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-600 dark:text-gray-400">{t.settings.loadingSettings}</div>
      </div>
    )
  }

  return (
    <div className="w-full max-w-7xl mx-auto space-y-5 sm:space-y-6">
      <div>
        <h1 className="text-2xl sm:text-3xl font-bold text-gray-900 dark:text-white tracking-tight">{t.settings.title}</h1>
      </div>
      
      {message && (
        <div className={`p-4 rounded-2xl text-xs sm:text-sm font-medium ${
          message.type === 'success' 
            ? 'bg-emerald-50 dark:bg-emerald-950/40 text-emerald-800 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800' 
            : 'bg-rose-50 dark:bg-rose-950/40 text-rose-800 dark:text-rose-300 border border-rose-200 dark:border-rose-800'
        }`}>
          {message.text}
        </div>
      )}

      <div className="space-y-4 sm:space-y-6">
        {/* FRP Communication Settings */}
        <div className="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200/80 dark:border-gray-700/80 p-5 sm:p-6 shadow-xs space-y-4">
          <div>
            <h2 className="text-lg font-bold text-gray-900 dark:text-white">{t.settings.frpCommunication}</h2>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
              {t.settings.frpDescription}
            </p>
          </div>
          
          <div className="space-y-4 pt-2">
            <div className="flex items-center justify-between gap-3">
              <label htmlFor="frp-enabled" className="text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300">
                {t.settings.enableFrp}
              </label>
              <button
                type="button"
                id="frp-enabled"
                onClick={() => updateFrp({ enabled: !settings.frp.enabled })}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none min-h-[44px] min-w-[44px] justify-end ${
                  settings.frp.enabled ? 'text-blue-600' : 'text-gray-400'
                }`}
              >
                <span className={`w-11 h-6 rounded-full transition-colors flex items-center p-0.5 ${
                  settings.frp.enabled ? 'bg-blue-600' : 'bg-gray-200 dark:bg-gray-700'
                }`}>
                  <span
                    className={`inline-block h-5 w-5 transform rounded-full bg-white transition-transform shadow-xs ${
                      settings.frp.enabled ? 'translate-x-5 rtl:-translate-x-5' : 'translate-x-0'
                    }`}
                  />
                </span>
              </button>
            </div>

            {settings.frp.enabled && (
              <div className="space-y-4 pt-2 border-t border-gray-100 dark:border-gray-700">
                <div>
                  <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
                    {t.settings.frpPort}
                  </label>
                  <input
                    type="number"
                    value={settings.frp.port}
                    onChange={(e) => updateFrp({ port: parseInt(e.target.value) || 7000 })}
                    className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-750 text-gray-900 dark:text-white text-base sm:text-sm font-mono focus:ring-2 focus:ring-blue-500"
                    placeholder="7000"
                    min="1"
                    max="65535"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1">
                    {t.settings.frpPortDescription}
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
                    {t.settings.frpTokenOptional}
                  </label>
                  <input
                    type="text"
                    value={settings.frp.token || ''}
                    onChange={(e) => updateFrp({ token: e.target.value || undefined })}
                    className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-750 text-gray-900 dark:text-white text-base sm:text-sm font-mono focus:ring-2 focus:ring-blue-500"
                    placeholder="Leave empty for no authentication"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1">
                    {t.settings.frpTokenDescription}
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Telegram Bot Settings */}
        <div className="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200/80 dark:border-gray-700/80 p-5 sm:p-6 shadow-xs space-y-4">
          <div>
            <h2 className="text-lg font-bold text-gray-900 dark:text-white">{t.settings.telegramBot}</h2>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
              {t.settings.telegramDescription}
            </p>
          </div>
          
          <div className="space-y-4 pt-2">
            <div className="flex items-center justify-between gap-3">
              <label htmlFor="telegram-enabled" className="text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300">
                {t.settings.enableTelegram}
              </label>
              <button
                type="button"
                id="telegram-enabled"
                onClick={() => updateTelegram({ enabled: !settings.telegram.enabled })}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none min-h-[44px] min-w-[44px] justify-end ${
                  settings.telegram.enabled ? 'text-blue-600' : 'text-gray-400'
                }`}
              >
                <span className={`w-11 h-6 rounded-full transition-colors flex items-center p-0.5 ${
                  settings.telegram.enabled ? 'bg-blue-600' : 'bg-gray-200 dark:bg-gray-700'
                }`}>
                  <span
                    className={`inline-block h-5 w-5 transform rounded-full bg-white transition-transform shadow-xs ${
                      settings.telegram.enabled ? 'translate-x-5 rtl:-translate-x-5' : 'translate-x-0'
                    }`}
                  />
                </span>
              </button>
            </div>

            {settings.telegram.enabled && (
              <div className="space-y-4 pt-2 border-t border-gray-100 dark:border-gray-700">
                <div>
                  <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
                    {t.settings.botToken}
                  </label>
                  <input
                    type="password"
                    value={settings.telegram.bot_token || ''}
                    onChange={(e) => updateTelegram({ bot_token: e.target.value || undefined })}
                    className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-750 text-gray-900 dark:text-white text-base sm:text-sm font-mono focus:ring-2 focus:ring-blue-500"
                    placeholder="Enter bot token from @BotFather"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1">
                    {t.settings.botTokenDescription}
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
                    {t.settings.adminUserIds}
                  </label>
                  <div className="space-y-2">
                    {settings.telegram.admin_ids.map((id, index) => (
                      <div key={index} className="flex items-center gap-2">
                        <input
                          type="text"
                          value={id}
                          onChange={(e) => {
                            const newIds = [...settings.telegram.admin_ids]
                            newIds[index] = e.target.value
                            updateTelegram({ admin_ids: newIds })
                          }}
                          className="flex-1 px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-750 text-gray-900 dark:text-white text-base sm:text-sm font-mono"
                        />
                        <button
                          onClick={() => removeAdminId(index)}
                          className="px-3.5 py-2.5 bg-rose-100 hover:bg-rose-200 dark:bg-rose-950/40 dark:hover:bg-rose-900/60 text-rose-700 dark:text-rose-300 rounded-xl text-xs font-semibold min-h-[44px]"
                        >
                          {t.settings.remove}
                        </button>
                      </div>
                    ))}
                    <div className="flex items-center gap-2">
                      <input
                        type="text"
                        value={newAdminId}
                        onChange={(e) => setNewAdminId(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && newAdminId.trim()) {
                            e.preventDefault();
                            addAdminId();
                          }
                        }}
                        placeholder={t.settings.enterAdminId || 'Enter Telegram Admin ID'}
                        className="flex-1 px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-750 text-gray-900 dark:text-white text-base sm:text-sm font-mono"
                      />
                      <button
                        onClick={addAdminId}
                        className="px-4 py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 text-white rounded-xl text-xs font-semibold min-h-[44px]"
                      >
                        {t.settings.addAdminId || 'Add'}
                      </button>
                    </div>
                  </div>
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1">
                    {t.settings.adminUserIdsDescription}
                  </p>
                </div>

                <div className="border-t border-gray-100 dark:border-gray-700 pt-4 mt-4">
                  <h3 className="text-sm font-bold text-gray-900 dark:text-white mb-3">{t.settings.automaticBackup}</h3>
                  
                  <div className="flex items-center gap-2 mb-4">
                    <input
                      type="checkbox"
                      id="backup-enabled"
                      checked={settings.telegram.backup_enabled || false}
                      onChange={(e) => updateTelegram({ backup_enabled: e.target.checked })}
                      className="rounded w-4 h-4 text-blue-600"
                    />
                    <label htmlFor="backup-enabled" className="text-xs sm:text-sm font-medium text-gray-700 dark:text-gray-300 cursor-pointer">
                      {t.settings.enableBackup}
                    </label>
                  </div>

                  {settings.telegram.backup_enabled && (
                    <div className="space-y-4">
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div>
                          <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
                            {t.settings.backupInterval}
                          </label>
                          <input
                            type="number"
                            value={settings.telegram.backup_interval || 60}
                            onChange={(e) => updateTelegram({ backup_interval: parseInt(e.target.value) || 60 })}
                            className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-750 text-gray-900 dark:text-white text-base sm:text-sm font-mono"
                            placeholder="60"
                            min="1"
                          />
                        </div>
                        <div>
                          <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
                            {t.settings.intervalUnit}
                          </label>
                          <select
                            value={settings.telegram.backup_interval_unit || 'minutes'}
                            onChange={(e) => updateTelegram({ backup_interval_unit: e.target.value })}
                            className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-750 text-gray-900 dark:text-white text-base sm:text-sm"
                          >
                            <option value="minutes">{t.settings.minutes}</option>
                            <option value="hours">{t.settings.hours}</option>
                          </select>
                        </div>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400">
                        {t.settings.backupDescription}
                      </p>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Tunnel Settings */}
        <div className="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200/80 dark:border-gray-700/80 p-5 sm:p-6 shadow-xs space-y-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-bold text-gray-900 dark:text-white">{t.settings.tunnelAutoReapply || 'Tunnel Auto Reapply'}</h2>
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                {t.settings.tunnelAutoReapplyDescription || 'Automatically reapply all tunnels at specified intervals'}
              </p>
            </div>
            <button
              type="button"
              onClick={() => updateTunnel({ auto_reapply_enabled: !(settings.tunnel?.auto_reapply_enabled || false) })}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none min-h-[44px] min-w-[44px] justify-end ${
                settings.tunnel?.auto_reapply_enabled ? 'text-blue-600' : 'text-gray-400'
              }`}
            >
              <span className={`w-11 h-6 rounded-full transition-colors flex items-center p-0.5 ${
                settings.tunnel?.auto_reapply_enabled ? 'bg-blue-600' : 'bg-gray-200 dark:bg-gray-700'
              }`}>
                <span
                  className={`inline-block h-5 w-5 transform rounded-full bg-white transition-transform shadow-xs ${
                    settings.tunnel?.auto_reapply_enabled ? 'translate-x-5 rtl:-translate-x-5' : 'translate-x-0'
                  }`}
                />
              </span>
            </button>
          </div>

          {settings.tunnel?.auto_reapply_enabled && (
            <div className="pt-3 border-t border-gray-100 dark:border-gray-700">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
                    {t.settings.tunnelReapplyInterval || 'Reapply Interval'}
                  </label>
                  <input
                    type="number"
                    value={settings.tunnel?.auto_reapply_interval || 60}
                    onChange={(e) => updateTunnel({ auto_reapply_interval: parseInt(e.target.value) || 60 })}
                    className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-750 text-gray-900 dark:text-white text-base sm:text-sm font-mono"
                    placeholder="60"
                    min="1"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
                    {t.settings.intervalUnit || 'Interval Unit'}
                  </label>
                  <select
                    value={settings.tunnel?.auto_reapply_interval_unit || 'minutes'}
                    onChange={(e) => updateTunnel({ auto_reapply_interval_unit: e.target.value })}
                    className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-750 text-gray-900 dark:text-white text-base sm:text-sm"
                  >
                    <option value="minutes">{t.settings.minutes}</option>
                    <option value="hours">{t.settings.hours}</option>
                  </select>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Save Button */}
        <div className="flex justify-end pt-2">
          <button
            onClick={saveSettings}
            disabled={saving}
            className="w-full sm:w-auto px-6 py-3 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 text-white rounded-xl transition-all font-semibold shadow-xs hover:shadow-md text-sm min-h-[48px] active:scale-98 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving ? t.settings.saving : t.settings.saveSettings}
          </button>
        </div>
      </div>
    </div>
  )
}

export default Settings
