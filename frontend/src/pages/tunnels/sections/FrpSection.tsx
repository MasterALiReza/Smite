import React from 'react'
import {
  Layers,
  Radio,
  Server,
  Key,
  EyeOff,
  Lock,
  Zap,
  ArrowLeftRight,
  Activity,
  Gauge,
  Globe,
  Network,
} from 'lucide-react'
import { CustomSelect } from '../../../components/CustomSelect'
import { CoreSectionHeader } from '../components/CoreSectionHeader'
import { PresetGrid } from '../components/PresetGrid'
import { PortsField } from '../components/PortsField'
import { ToggleCard } from '../components/ToggleCard'
import { FRP_PRESETS } from '../presets'
import { generateRandomControlPort } from '../formHelpers'

interface FrpSectionProps {
  formData: any
  onChange: (updates: Record<string, any>) => void
  showToast: (type: 'success' | 'error' | 'warning' | 'info', title: string, message: string) => void
}

export const FrpSection: React.FC<FrpSectionProps> = ({
  formData,
  onChange,
  showToast,
}) => {
  return (
    <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-br from-purple-50/60 via-gray-50 to-indigo-50/40 dark:from-purple-950/20 dark:via-gray-800/60 dark:to-indigo-950/20 border border-purple-200/70 dark:border-purple-900/40 space-y-4">
      {/* 1. Header */}
      <CoreSectionHeader
        title="FRP Advanced Settings"
        subtitle="High-Performance Reverse Proxy with Multiplexing & Encryption"
        badgeText="FRP v0.50+ Core"
        icon={<Layers size={16} />}
        iconBoxClass="bg-purple-100 dark:bg-purple-900/60 text-purple-600 dark:text-purple-300"
        badgeClass="bg-purple-100/80 text-purple-700 dark:bg-purple-900/60 dark:text-purple-300 border border-purple-200 dark:border-purple-800"
      />

      {/* 2. Quick Presets */}
      <PresetGrid
        presets={FRP_PRESETS}
        currentData={formData}
        onApply={(preset) => {
          onChange(preset.appliedValues)
          showToast('info', preset.toastTitle || 'Preset Applied', preset.toastMessage)
        }}
      />

      {/* 3. Bind Port & Forwarded Ports */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start pt-1">
        <PortsField
          label="FRP Bind Port"
          value={formData.frp_bind_port}
          onChange={(val) => onChange({ frp_bind_port: val })}
          placeholder="7000"
          helperText="FRP server port on panel (default: 7000)."
          icon={<Server size={14} className="text-purple-500" />}
          type="number"
          min="1"
          max="65535"
          showRandomBtn
          onRandom={() => onChange({ frp_bind_port: generateRandomControlPort() })}
          accentClass="focus:ring-purple-500/20 focus:border-purple-500"
          required
        />

        <PortsField
          label="Forwarded Ports"
          badge="Remote & Local"
          value={formData.ports}
          onChange={(val) => onChange({ ports: val })}
          placeholder="8080,8081,8082"
          helperText="Ports (comma-separated, same for remote port and local port)."
          icon={<Radio size={14} className="text-blue-500" />}
          accentClass="focus:ring-purple-500/20 focus:border-purple-500"
          required
        />
      </div>

      {/* 4. Transport Protocol & Stealth SNI */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
        <div>
          <div className="flex items-center justify-between mb-1.5 h-5">
            <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-purple-500"></span>
              Transport Protocol
            </label>
            <span className="text-[11px] font-mono uppercase text-gray-400">
              {formData.frp_transport || 'tcp'}
            </span>
          </div>
          <CustomSelect
            value={formData.frp_transport || 'tcp'}
            onChange={(val) => {
              if (val === 'kcp') {
                onChange({ frp_transport: val, security_type: 'none', frp_security: 'none' })
              } else {
                onChange({ frp_transport: val })
              }
            }}
            options={[
              { value: 'tcp', label: 'TCP (with TLS & Zero-Byte Signature)' },
              { value: 'kcp', label: 'KCP (Fast UDP - Resilient to Packet Loss)' },
              { value: 'quic', label: 'QUIC (HTTP/3 UDP + TLS 1.3 Multiplex)' },
              { value: 'websocket', label: 'WebSocket (Plain WS)' },
              { value: 'wss', label: 'WSS (Secure WebSocket - CDN Capable)' },
            ]}
          />
          <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5 leading-tight">
            {formData.frp_transport === 'quic' &&
              'Ultra-fast 0-RTT UDP multiplexing with TLS 1.3 encryption.'}
            {formData.frp_transport === 'kcp' &&
              'Aggressive ARQ UDP for bad or filtered routes (Gaming / High-Loss). Note: TLS is disabled on KCP.'}
            {formData.frp_transport === 'wss' &&
              'Encrypted WebSocket compatible with CDN reverse proxies.'}
            {formData.frp_transport === 'websocket' && 'Standard HTTP WebSocket proxy.'}
            {(!formData.frp_transport || formData.frp_transport === 'tcp') &&
              'Raw TCP stream with TLS signature hiding.'}
          </p>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1.5 h-5">
            <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
              <EyeOff size={14} className="text-indigo-500" />
              Stealth SNI / Camouflage Domain
            </label>
            <span className="text-[11px] text-gray-400">TLS Header</span>
          </div>
          <input
            type="text"
            value={formData.frp_sni || ''}
            onChange={(e) => onChange({ frp_sni: e.target.value })}
            className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all font-medium shadow-2xs"
            placeholder="e.g. speedtest.net or domain.com"
          />
          <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5 leading-tight">
            Domain name inserted into TLS handshake to evade deep packet inspection.
          </p>
        </div>
      </div>

      {/* 5. Security Toggles Card */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
        <ToggleCard
          label="Payload Encryption"
          description="AES-128-CFB / ChaCha20 cipher"
          icon={<Lock size={15} className="text-purple-600 dark:text-purple-400" />}
          checked={formData.frp_encryption}
          onChange={(checked) => onChange({ frp_encryption: checked })}
          activeBgClass="peer-checked:bg-purple-600"
        />

        <ToggleCard
          label="Snappy Compression"
          description="Reduces bandwidth & obfuscates entropy"
          icon={<Zap size={15} className="text-purple-600 dark:text-purple-400" />}
          checked={formData.frp_compression}
          onChange={(checked) => onChange({ frp_compression: checked })}
          activeBgClass="peer-checked:bg-purple-600"
        />
      </div>

      {/* 6. Auth Token */}
      <div>
        <div className="flex items-center justify-between mb-1.5 h-5">
          <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
            <Key size={14} className="text-indigo-500" />
            Authentication Token
          </label>
          <span className="text-[11px] text-gray-400">Optional</span>
        </div>
        <input
          type="text"
          value={formData.frp_token || ''}
          onChange={(e) => onChange({ frp_token: e.target.value })}
          className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all font-mono shadow-2xs"
          placeholder="Auto-generated if empty"
        />
        <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5 leading-tight">
          Shared secret token verifying client-server authorization.
        </p>
      </div>

      {/* 7. Mode & Reliability */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
        <ToggleCard
          label="Reverse Mode"
          description={
            formData.is_reverse
              ? 'Foreign connects to Iran (Recommended)'
              : 'Iran connects to Foreign (Direct STCP)'
          }
          icon={<ArrowLeftRight size={15} className="text-blue-600 dark:text-blue-400" />}
          checked={formData.is_reverse}
          onChange={(checked) => onChange({ is_reverse: checked })}
          activeBgClass="peer-checked:bg-blue-600"
        />

        <ToggleCard
          label="Native Health Check"
          description="Auto-detect dead links & reconnect fast"
          icon={<Activity size={15} className="text-emerald-600 dark:text-emerald-400" />}
          checked={formData.frp_health_check}
          onChange={(checked) => onChange({ frp_health_check: checked })}
          activeBgClass="peer-checked:bg-emerald-600"
        />
      </div>

      {/* 8. Proxy Protocol */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
            <Network size={14} className="text-blue-500" />
            Proxy Protocol Version
          </label>
        </div>
        <CustomSelect
          value={formData.frp_proxy_protocol || 'none'}
          onChange={(val) => onChange({ frp_proxy_protocol: val })}
          options={[
            { value: 'none', label: 'Disabled (Direct)' },
            { value: 'v1', label: 'v1 (ASCII Text)' },
            { value: 'v2', label: 'v2 (Binary Fast)' },
          ]}
        />
      </div>

      {/* 9. Bandwidth Limit & Custom Domains */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
        <div>
          <div className="flex items-center justify-between mb-1.5 h-5">
            <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
              <Gauge size={14} className="text-amber-500" />
              Bandwidth Rate Limit
            </label>
            <span className="text-[11px] text-gray-400">Optional</span>
          </div>
          <input
            type="text"
            value={formData.frp_bandwidth_limit || ''}
            onChange={(e) => onChange({ frp_bandwidth_limit: e.target.value })}
            className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-amber-500/20 focus:border-amber-500 transition-all font-mono shadow-2xs"
            placeholder="e.g. 10MB or 500KB"
          />
          <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5 leading-tight">
            Per-proxy bandwidth limit cap (e.g. 20MB).
          </p>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1.5 h-5">
            <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
              <Globe size={14} className="text-purple-500" />
              VHost Domains (Optional)
            </label>
            <span className="text-[11px] text-gray-400">HTTP/HTTPS</span>
          </div>
          <input
            type="text"
            value={formData.frp_custom_domains || ''}
            onChange={(e) => onChange({ frp_custom_domains: e.target.value })}
            className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-purple-500/20 focus:border-purple-500 transition-all font-mono shadow-2xs"
            placeholder="e.g. app.domain.com"
          />
          <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5 leading-tight">
            Custom domain routing for HTTP/HTTPS tunnels.
          </p>
        </div>
      </div>
    </div>
  )
}
