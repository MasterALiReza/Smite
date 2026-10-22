import React from 'react'
import { Terminal, Radio, Server, Shield, Zap, EyeOff, Globe } from 'lucide-react'
import { CustomSelect } from '../../../components/CustomSelect'
import { CoreSectionHeader } from '../components/CoreSectionHeader'
import { PresetGrid } from '../components/PresetGrid'
import { PortsField } from '../components/PortsField'
import { CHISEL_PRESETS } from '../presets'
import { generateRandomControlPort } from '../formHelpers'

interface ChiselSectionProps {
  formData: any
  onChange: (updates: Record<string, any>) => void
  showToast: (type: 'success' | 'error' | 'warning' | 'info', title: string, message: string) => void
}

export const ChiselSection: React.FC<ChiselSectionProps> = ({
  formData,
  onChange,
  showToast,
}) => {
  return (
    <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-br from-teal-50/60 via-gray-50 to-emerald-50/40 dark:from-teal-950/20 dark:via-gray-800/60 dark:to-emerald-950/20 border border-teal-200/70 dark:border-teal-900/40 space-y-4">
      {/* 1. Header */}
      <CoreSectionHeader
        title="Chisel Advanced Settings"
        subtitle="High-Speed WebSocket Tunnel with SSH Multiplexing, WSS TLS & Anti-DPI Camouflage"
        badgeText="Chisel Core"
        icon={<Terminal size={16} />}
        iconBoxClass="bg-teal-100 dark:bg-teal-900/60 text-teal-600 dark:text-teal-300"
        badgeClass="bg-teal-100/80 text-teal-700 dark:bg-teal-900/60 dark:text-teal-300 border border-teal-200 dark:border-teal-800"
      />

      {/* 2. Quick Presets */}
      <PresetGrid
        presets={CHISEL_PRESETS}
        currentData={formData}
        onApply={(preset) => {
          onChange(preset.appliedValues)
          showToast('info', preset.toastTitle || 'Preset Applied', preset.toastMessage)
        }}
      />

      {/* 3. Forwarded Ports & Control Port */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start pt-1">
        <PortsField
          label="Forwarded Ports"
          badge="Reverse & Local"
          value={formData.ports}
          onChange={(val) => onChange({ ports: val })}
          placeholder="8080,8081,8082"
          helperText="Comma-separated ports mapped through the tunnel."
          icon={<Radio size={14} className="text-teal-500" />}
          accentClass="focus:ring-teal-500/20 focus:border-teal-500"
          required
        />

        <PortsField
          label="Control Port"
          value={formData.chisel_control_port}
          onChange={(val) => onChange({ chisel_control_port: val })}
          placeholder={`${(parseInt(formData.ports?.split(',')[0]?.trim()) || 8080) + 10000} (auto)`}
          helperText="Chisel server control port (leave empty for auto: first port + 10000)."
          icon={<Server size={14} className="text-emerald-500" />}
          type="number"
          min="1"
          max="65535"
          showRandomBtn
          onRandom={() => onChange({ chisel_control_port: generateRandomControlPort() })}
          accentClass="focus:ring-teal-500/20 focus:border-teal-500"
        />
      </div>

      {/* 4. Transport Protocol & Keepalive Heartbeat */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start pt-1">
        <div>
          <div className="flex items-center justify-between mb-1.5 h-5">
            <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
              <Shield size={14} className="text-teal-500" />
              Transport Protocol & Encryption
            </label>
            <span className="text-[11px] font-mono uppercase text-gray-400 dark:text-gray-500">
              {formData.chisel_transport}
            </span>
          </div>
          <CustomSelect
            value={formData.chisel_transport}
            onChange={(val) => onChange({ chisel_transport: val })}
            options={[
              { value: 'ws', label: 'WS - Plain WebSocket (HTTP, Low Overhead)' },
              { value: 'wss', label: 'WSS - Encrypted WebSocket over TLS (HTTPS)' },
            ]}
          />
          <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5 leading-tight">
            {formData.chisel_transport === 'wss'
              ? 'Outer TLS encryption layer + Inner SSH stream encryption (Double Layer Security).'
              : 'Standard WebSocket with built-in inner SSH encryption layer.'}
          </p>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1.5 h-5">
            <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
              <Zap size={14} className="text-amber-500" />
              Keepalive Heartbeat
            </label>
            <span className="text-[11px] font-mono text-gray-400 dark:text-gray-500">
              {formData.chisel_keepalive}
            </span>
          </div>
          <CustomSelect
            value={formData.chisel_keepalive}
            onChange={(val) => onChange({ chisel_keepalive: val })}
            options={[
              { value: '5s', label: '5s (Ultra-Aggressive / Competitive Gaming)' },
              { value: '10s', label: '10s (Recommended - High Stability)' },
              { value: '15s', label: '15s (Balanced)' },
              { value: '25s', label: '25s (Default / Low Overhead)' },
            ]}
          />
          <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5 leading-tight">
            Prevents firewall NAT translation state dropouts across Iranian ISPs.
          </p>
        </div>
      </div>

      {/* 5. Anti-Probing Camouflage & Decoy Website */}
      <div className="p-3.5 rounded-xl bg-teal-50/70 dark:bg-teal-950/20 border border-teal-200/70 dark:border-teal-900/40 space-y-3">
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-xs font-bold text-teal-900 dark:text-teal-200 flex items-center gap-1.5">
              <Shield size={14} className="text-teal-600 dark:text-teal-400" />
              Decoy Camouflage Backend (--backend)
            </label>
            <span className="text-[10px] px-2 py-0.5 rounded-md font-medium bg-teal-200/60 text-teal-800 dark:bg-teal-900/60 dark:text-teal-300">
              Anti-Active Probing
            </span>
          </div>
          <p className="text-[11px] text-teal-800/80 dark:text-teal-300/80 mb-2">
            When censors or scanning bots probe your control port via standard HTTP/HTTPS, Chisel proxies the request to this legitimate website with HTTP 200 OK:
          </p>
          <input
            type="text"
            value={formData.chisel_backend_url || ''}
            onChange={(e) => onChange({ chisel_backend_url: e.target.value })}
            className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-teal-300 dark:border-teal-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white font-mono shadow-2xs"
            placeholder="https://speedtest.net or https://example.com"
          />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
          <div>
            <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 block mb-1">
              Custom SNI (--sni)
            </label>
            <input
              type="text"
              value={formData.chisel_custom_sni || ''}
              onChange={(e) => onChange({ chisel_custom_sni: e.target.value })}
              className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white font-mono shadow-2xs"
              placeholder="speedtest.net or domain.com"
            />
            <p className="text-[10px] text-gray-500 dark:text-gray-400 mt-1">
              Overrides TLS ClientHello Server Name Indication.
            </p>
          </div>

          <div>
            <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 block mb-1">
              Custom Host Header (--hostname)
            </label>
            <input
              type="text"
              value={formData.chisel_custom_host || ''}
              onChange={(e) => onChange({ chisel_custom_host: e.target.value })}
              className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white font-mono shadow-2xs"
              placeholder="cdn.speedtest.net"
            />
            <p className="text-[10px] text-gray-500 dark:text-gray-400 mt-1">
              Sets HTTP Host header for reverse proxies / CDN fronting.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
