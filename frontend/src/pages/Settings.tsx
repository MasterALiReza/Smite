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
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center">
          <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-[#3F72AF] dark:border-[#00A8CC] mb-4"></div>
          <p className="text-sm font-medium text-[#112D4E]/70 dark:text-[#DBE2EF]/80">{t.settings.loadingSettings}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="w-full max-w-7xl mx-auto space-y-8">
      <div>
        <h1 className="text-3xl font-black tracking-tight text-[#112D4E] dark:text-[#F9F7F7] mb-2">{t.settings.title}</h1>
        <p className="text-sm font-medium text-[#112D4E]/70 dark:text-[#DBE2EF]/80">Configure system parameters, backups, and daemon settings.</p>
      </div>
      
      {message && (
        <div className={`p-4 rounded-2xl text-xs font-bold ${
          message.type === 'success' 
            ? 'bg-emerald-500/10 text-emerald-800 dark:text-emerald-300 border border-emerald-500/30' 
            : 'bg-rose-500/10 text-rose-800 dark:text-rose-300 border border-rose-500/30'
        }`}>
          {message.text}
        </div>
      )}

      <div className="space-y-6">
        {/* FRP Communication Settings */}
        <div className="bg-white dark:bg-[#27496D] rounded-2xl border border-[#DBE2EF] dark:border-[#142850] p-6 shadow-sm space-y-4">
          <div>
            <h2 className="text-lg font-bold text-[#112D4E] dark:text-[#F9F7F7] mb-1">{t.settings.frpCommunication}</h2>
            <p className="text-xs font-medium text-[#112D4E]/60 dark:text-[#DBE2EF]/70">
              {t.settings.frpDescription}
            </p>
          </div>
          
          <div className="space-y-4 pt-2">
            <div className="flex items-center justify-between">
              <label htmlFor="frp-enabled" className="text-xs font-bold uppercase tracking-wider text-[#112D4E]/70 dark:text-[#DBE2EF]/70">
                {t.settings.enableFrp}
              </label>
              <button
                type="button"
                id="frp-enabled"
                onClick={() => updateFrp({ enabled: !settings.frp.enabled })}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-[#3F72AF] dark:focus:ring-[#00A8CC] ${
                  settings.frp.enabled ? 'bg-[#3F72AF] dark:bg-[#00A8CC]' : 'bg-[#DBE2EF] dark:bg-[#142850]'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    settings.frp.enabled ? 'translate-x-6 rtl:-translate-x-6' : 'translate-x-1 rtl:-translate-x-1'
                  }`}
                />
              </button>
            </div>

            {settings.frp.enabled && (
              <div className="space-y-4 pt-2 border-t border-[#DBE2EF] dark:border-[#142850]">
                <div>
                  <label className="block text-xs font-bold uppercase tracking-wider text-[#112D4E]/70 dark:text-[#DBE2EF]/70 mb-1.5">
                    {t.settings.frpPort}
                  </label>
                  <input
                    type="number"
                    value={settings.frp.port}
                    onChange={(e) => updateFrp({ port: parseInt(e.target.value) || 7000 })}
                    className="w-full px-4 py-2.5 border border-[#DBE2EF] dark:border-[#142850] rounded-xl bg-[#F9F7F7] dark:bg-[#142850] text-[#112D4E] dark:text-[#F9F7F7] text-xs font-medium focus:ring-2 focus:ring-[#3F72AF] dark:focus:ring-[#00A8CC] outline-none"
                    placeholder="7000"
                    min="1"
                    max="65535"
                  />
                  <p className="text-xs text-[#112D4E]/50 dark:text-[#DBE2EF]/50 mt-1">
                    {t.settings.frpPortDescription}
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-bold uppercase tracking-wider text-[#112D4E]/70 dark:text-[#DBE2EF]/70 mb-1.5">
                    {t.settings.frpTokenOptional}
                  </label>
                  <input
                    type="text"
                    value={settings.frp.token || ''}
                    onChange={(e) => updateFrp({ token: e.target.value || undefined })}
                    className="w-full px-4 py-2.5 border border-[#DBE2EF] dark:border-[#142850] rounded-xl bg-[#F9F7F7] dark:bg-[#142850] text-[#112D4E] dark:text-[#F9F7F7] text-xs font-medium focus:ring-2 focus:ring-[#3F72AF] dark:focus:ring-[#00A8CC] outline-none"
                    placeholder="Leave empty for no authentication"
                  />
                  <p className="text-xs text-[#112D4E]/50 dark:text-[#DBE2EF]/50 mt-1">
                    {t.settings.frpTokenDescription}
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Telegram Bot Settings */}
        <div className="bg-white dark:bg-[#27496D] rounded-2xl border border-[#DBE2EF] dark:border-[#142850] p-6 shadow-sm space-y-4">
          <div>
            <h2 className="text-lg font-bold text-[#112D4E] dark:text-[#F9F7F7] mb-1">{t.settings.telegramBot}</h2>
            <p className="text-xs font-medium text-[#112D4E]/60 dark:text-[#DBE2EF]/70">
              {t.settings.telegramDescription}
            </p>
          </div>
          
          <div className="space-y-4 pt-2">
            <div className="flex items-center justify-between">
              <label htmlFor="telegram-enabled" className="text-xs font-bold uppercase tracking-wider text-[#112D4E]/70 dark:text-[#DBE2EF]/70">
                {t.settings.enableTelegram}
              </label>
              <button
                type="button"
                id="telegram-enabled"
                onClick={() => updateTelegram({ enabled: !settings.telegram.enabled })}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-[#3F72AF] dark:focus:ring-[#00A8CC] ${
                  settings.telegram.enabled ? 'bg-[#3F72AF] dark:bg-[#00A8CC]' : 'bg-[#DBE2EF] dark:bg-[#142850]'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    settings.telegram.enabled ? 'translate-x-6 rtl:-translate-x-6' : 'translate-x-1 rtl:-translate-x-1'
                  }`}
                />
              </button>
            </div>

            {settings.telegram.enabled && (
              <div className="space-y-4 pt-2 border-t border-[#DBE2EF] dark:border-[#142850]">
                <div>
                  <label className="block text-xs font-bold uppercase tracking-wider text-[#112D4E]/70 dark:text-[#DBE2EF]/70 mb-1.5">
                    {t.settings.botToken}
                  </label>
                  <input
                    type="password"
                    value={settings.telegram.bot_token || ''}
                    onChange={(e) => updateTelegram({ bot_token: e.target.value || undefined })}
                    className="w-full px-4 py-2.5 border border-[#DBE2EF] dark:border-[#142850] rounded-xl bg-[#F9F7F7] dark:bg-[#142850] text-[#112D4E] dark:text-[#F9F7F7] text-xs font-medium focus:ring-2 focus:ring-[#3F72AF] dark:focus:ring-[#00A8CC] outline-none"
                    placeholder="Enter bot token from @BotFather"
                  />
                  <p className="text-xs text-[#112D4E]/50 dark:text-[#DBE2EF]/50 mt-1">
                    {t.settings.botTokenDescription}
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-bold uppercase tracking-wider text-[#112D4E]/70 dark:text-[#DBE2EF]/70 mb-1.5">
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
                          className="flex-1 px-4 py-2 border border-[#DBE2EF] dark:border-[#142850] rounded-xl bg-[#F9F7F7] dark:bg-[#142850] text-[#112D4E] dark:text-[#F9F7F7] text-xs font-medium focus:ring-2 focus:ring-[#3F72AF] dark:focus:ring-[#00A8CC] outline-none"
                        />
                        <button
                          onClick={() => removeAdminId(index)}
                          className="px-3 py-2 bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 rounded-xl text-xs font-bold transition-colors"
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
                        className="flex-1 px-4 py-2 border border-[#DBE2EF] dark:border-[#142850] rounded-xl bg-[#F9F7F7] dark:bg-[#142850] text-[#112D4E] dark:text-[#F9F7F7] text-xs font-medium focus:ring-2 focus:ring-[#3F72AF] dark:focus:ring-[#00A8CC] outline-none"
                      />
                      <button
                        onClick={addAdminId}
                        className="px-4 py-2 bg-[#3F72AF] hover:bg-[#3F72AF]/90 dark:bg-gradient-to-r dark:from-[#0C7B93] dark:to-[#00A8CC] text-white rounded-xl text-xs font-bold transition-colors"
                      >
                        {t.settings.addAdminId || 'Add'}
                      </button>
                    </div>
                  </div>
                  <p className="text-xs text-[#112D4E]/50 dark:text-[#DBE2EF]/50 mt-1">
                    {t.settings.adminUserIdsDescription}
                  </p>
                </div>

                <div className="border-t border-[#DBE2EF] dark:border-[#142850] pt-4 mt-4 space-y-4">
                  <h3 className="text-sm font-bold text-[#112D4E] dark:text-[#F9F7F7]">{t.settings.automaticBackup}</h3>
                  
                  <div className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      id="backup-enabled"
                      checked={settings.telegram.backup_enabled || false}
                      onChange={(e) => updateTelegram({ backup_enabled: e.target.checked })}
                      className="rounded accent-[#3F72AF] dark:accent-[#00A8CC]"
                    />
                    <label htmlFor="backup-enabled" className="text-xs font-bold text-[#112D4E] dark:text-[#DBE2EF]">
                      {t.settings.enableBackup}
                    </label>
                  </div>

                  {settings.telegram.backup_enabled && (
                    <div className="space-y-3 pt-2">
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                        <div>
                          <label className="block text-xs font-bold uppercase tracking-wider text-[#112D4E]/70 dark:text-[#DBE2EF]/70 mb-1.5">
                            {t.settings.backupInterval}
                          </label>
                          <input
                            type="number"
                            value={settings.telegram.backup_interval || 60}
                            onChange={(e) => updateTelegram({ backup_interval: parseInt(e.target.value) || 60 })}
                            className="w-full px-4 py-2 border border-[#DBE2EF] dark:border-[#142850] rounded-xl bg-[#F9F7F7] dark:bg-[#142850] text-[#112D4E] dark:text-[#F9F7F7] text-xs font-medium focus:ring-2 focus:ring-[#3F72AF] dark:focus:ring-[#00A8CC] outline-none"
                            placeholder="60"
                            min="1"
                          />
                        </div>
                        <div>
                          <label className="block text-xs font-bold uppercase tracking-wider text-[#112D4E]/70 dark:text-[#DBE2EF]/70 mb-1.5">
                            {t.settings.intervalUnit}
                          </label>
                          <select
                            value={settings.telegram.backup_interval_unit || 'minutes'}
                            onChange={(e) => updateTelegram({ backup_interval_unit: e.target.value })}
                            className="w-full px-4 py-2 border border-[#DBE2EF] dark:border-[#142850] rounded-xl bg-[#F9F7F7] dark:bg-[#142850] text-[#112D4E] dark:text-[#F9F7F7] text-xs font-medium focus:ring-2 focus:ring-[#3F72AF] dark:focus:ring-[#00A8CC] outline-none"
                          >
                            <option value="minutes">{t.settings.minutes}</option>
                            <option value="hours">{t.settings.hours}</option>
                          </select>
                        </div>
                      </div>
                      <p className="text-xs text-[#112D4E]/50 dark:text-[#DBE2EF]/50">
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
        <div className="bg-white dark:bg-[#27496D] rounded-2xl border border-[#DBE2EF] dark:border-[#142850] p-6 shadow-sm space-y-4">
          <div>
            <h2 className="text-lg font-bold text-[#112D4E] dark:text-[#F9F7F7] mb-1">{t.settings.tunnelAutoReapply || 'Tunnel Auto Reapply'}</h2>
            <p className="text-xs font-medium text-[#112D4E]/60 dark:text-[#DBE2EF]/70">
              {t.settings.tunnelAutoReapplyDescription || 'Automatically reapply all tunnels at specified intervals'}
            </p>
          </div>
          <div className="space-y-4 pt-2">
            <div className="flex items-center justify-between">
              <div>
                <label className="text-xs font-bold uppercase tracking-wider text-[#112D4E]/70 dark:text-[#DBE2EF]/70">
                  {t.settings.enableTunnelAutoReapply || 'Enable Automatic Tunnel Reapply'}
                </label>
              </div>
              <button
                type="button"
                onClick={() => updateTunnel({ auto_reapply_enabled: !(settings.tunnel?.auto_reapply_enabled || false) })}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-[#3F72AF] dark:focus:ring-[#00A8CC] ${
                  settings.tunnel?.auto_reapply_enabled ? 'bg-[#3F72AF] dark:bg-[#00A8CC]' : 'bg-[#DBE2EF] dark:bg-[#142850]'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    settings.tunnel?.auto_reapply_enabled ? 'translate-x-6 rtl:-translate-x-6' : 'translate-x-1 rtl:-translate-x-1'
                  }`}
                />
              </button>
            </div>

            {settings.tunnel?.auto_reapply_enabled && (
              <div className="space-y-4 pt-2 border-t border-[#DBE2EF] dark:border-[#142850]">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-bold uppercase tracking-wider text-[#112D4E]/70 dark:text-[#DBE2EF]/70 mb-1.5">
                      {t.settings.tunnelReapplyInterval || 'Reapply Interval'}
                    </label>
                    <input
                      type="number"
                      value={settings.tunnel?.auto_reapply_interval || 60}
                      onChange={(e) => updateTunnel({ auto_reapply_interval: parseInt(e.target.value) || 60 })}
                      className="w-full px-4 py-2 border border-[#DBE2EF] dark:border-[#142850] rounded-xl bg-[#F9F7F7] dark:bg-[#142850] text-[#112D4E] dark:text-[#F9F7F7] text-xs font-medium focus:ring-2 focus:ring-[#3F72AF] dark:focus:ring-[#00A8CC] outline-none"
                      placeholder="60"
                      min="1"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-bold uppercase tracking-wider text-[#112D4E]/70 dark:text-[#DBE2EF]/70 mb-1.5">
                      {t.settings.intervalUnit || 'Interval Unit'}
                    </label>
                    <select
                      value={settings.tunnel?.auto_reapply_interval_unit || 'minutes'}
                      onChange={(e) => updateTunnel({ auto_reapply_interval_unit: e.target.value })}
                      className="w-full px-4 py-2 border border-[#DBE2EF] dark:border-[#142850] rounded-xl bg-[#F9F7F7] dark:bg-[#142850] text-[#112D4E] dark:text-[#F9F7F7] text-xs font-medium focus:ring-2 focus:ring-[#3F72AF] dark:focus:ring-[#00A8CC] outline-none"
                    >
                      <option value="minutes">{t.settings.minutes}</option>
                      <option value="hours">{t.settings.hours}</option>
                    </select>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Save Button */}
        <div className="flex justify-end pt-2">
          <button
            onClick={saveSettings}
            disabled={saving}
            className="px-6 py-2.5 bg-[#3F72AF] hover:bg-[#3F72AF]/90 dark:bg-gradient-to-r dark:from-[#0C7B93] dark:to-[#00A8CC] text-white rounded-xl transition-all duration-200 font-bold text-xs shadow-md shadow-[#3F72AF]/20 dark:shadow-[#00A8CC]/20 disabled:opacity-50 disabled:cursor-not-allowed hover:scale-[1.02] active:scale-[0.98]"
          >
            {saving ? t.settings.saving : t.settings.saveSettings}
          </button>
        </div>
      </div>
    </div>
  )
}

export default Settings
