import React from 'react'
import {
  Zap,
  Globe,
  Gamepad2,
  ShieldCheck,
  Activity,
  EyeOff,
  Shield,
  Gauge,
  Fingerprint,
  Scale,
  ArrowLeftRight,
  Radio,
} from 'lucide-react'
import { useLanguage } from '../../../contexts/LanguageContext'
import { CustomSelect } from '../../../components/CustomSelect'
import { CoreSectionHeader } from '../components/CoreSectionHeader'
import { PresetGrid } from '../components/PresetGrid'
import { PortsField } from '../components/PortsField'
import { ToggleCard } from '../components/ToggleCard'
import { GOST_PRESETS } from '../presets'

interface GostSectionProps {
  formData: any
  onChange: (updates: Record<string, any>) => void
  showToast: (type: 'success' | 'error' | 'warning' | 'info', title: string, message: string) => void
}

export const GostSection: React.FC<GostSectionProps> = ({
  formData,
  onChange,
  showToast,
}) => {
  const { t } = useLanguage()

  const handleTransportChange = (val: string) => {
    const updates: Record<string, any> = { transport_type: val }
    if (val === 'wss' || val === 'mwss') {
      if (formData.security_type === 'none') {
        updates.security_type = 'tls'
      }
    } else if (val === 'quic') {
      updates.security_type = 'tls'
    } else if (val === 'kcp' || val === 'ssh') {
      updates.security_type = 'none'
    }
    onChange(updates)
  }

  const getSecurityOptions = () => {
    if (formData.transport_type === 'wss' || formData.transport_type === 'mwss') {
      return [
        { value: 'tls', label: 'TLS (Standard Encryption)' },
        { value: 'utls', label: 'uTLS (Browser Spoofing Anti-DPI)' },
      ]
    }
    if (formData.transport_type === 'quic') {
      return [{ value: 'tls', label: 'TLS 1.3 (Enforced by QUIC)' }]
    }
    if (formData.transport_type === 'kcp' || formData.transport_type === 'ssh') {
      return [{ value: 'none', label: 'Native Protocol Encryption' }]
    }
    return [
      { value: 'none', label: 'None (Plaintext / Low Overhead)' },
      { value: 'tls', label: 'TLS (Standard Encryption)' },
      { value: 'utls', label: 'uTLS (Browser Spoofing Anti-DPI)' },
    ]
  }

  const isSecurityLocked =
    formData.transport_type === 'quic' ||
    formData.transport_type === 'kcp' ||
    formData.transport_type === 'ssh'

  return (
    <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-br from-purple-50/60 via-gray-50 to-blue-50/40 dark:from-purple-950/20 dark:via-gray-800/60 dark:to-blue-950/20 border border-purple-200/70 dark:border-purple-900/40 space-y-4">
      {/* 1. Header */}
      <CoreSectionHeader
        title="Advanced GOST Settings"
        subtitle="Next-Gen Anti-DPI & High-Performance Routing Core"
        badgeText="GOST v3.0 Core"
        icon={<Zap size={16} />}
        iconBoxClass="bg-purple-100 dark:bg-purple-900/60 text-purple-600 dark:text-purple-300"
        badgeClass="bg-purple-100/80 text-purple-700 dark:bg-purple-900/60 dark:text-purple-300 border border-purple-200 dark:border-purple-800"
      />

      {/* 2. Quick Presets */}
      <PresetGrid
        presets={GOST_PRESETS}
        currentData={formData}
        onApply={(preset) => {
          onChange(preset.appliedValues)
          showToast('info', preset.toastTitle || 'Preset Applied', preset.toastMessage)
        }}
      />

      {/* 3. Remote IP & Ports */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start pt-1">
        <div>
          <div className="flex items-center justify-between mb-1.5 h-5">
            <label className="text-xs font-semibold text-gray-700 dark:text-gray-300">
              {t.tunnels.remoteIP}
            </label>
          </div>
          <input
            type="text"
            value={formData.remote_ip || ''}
            onChange={(e) =>
              onChange({ remote_ip: e.target.value || '127.0.0.1' })
            }
            className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white font-mono focus:ring-2 focus:ring-purple-500/20 focus:border-purple-500 transition-all shadow-2xs"
            placeholder="127.0.0.1 or [2001:db8::1]"
          />
          <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5 leading-tight">
            {t.tunnels.remoteIPDescription}
          </p>
        </div>

        <PortsField
          label="Ports & Ranges"
          value={formData.ports}
          onChange={(val) => onChange({ ports: val })}
          placeholder="8080, 8081, 10000-20000"
          helperText="Ports or ranges (comma-separated, same for panel and target server)"
          icon={<Radio size={14} className="text-purple-500" />}
          accentClass="focus:ring-purple-500/20 focus:border-purple-500"
          required
        />
      </div>

      {/* 4. Transport Protocol & Security Selects */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
        <div>
          <div className="flex items-center justify-between mb-1.5 h-5">
            <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-blue-500"></span>
              Transport Protocol
            </label>
            <span className="text-[11px] font-mono uppercase text-gray-400 dark:text-gray-500">
              {formData.transport_type}
            </span>
          </div>
          <CustomSelect
            value={formData.transport_type}
            onChange={handleTransportChange}
            options={[
              { value: 'tcp', label: 'TCP (Standard)' },
              { value: 'ws', label: 'WebSocket (WS)' },
              { value: 'wss', label: 'WebSocket Secure (WSS)' },
              { value: 'mws', label: 'Multiplex WS (MWS)' },
              { value: 'mwss', label: 'Multiplex WSS' },
              { value: 'quic', label: 'QUIC (HTTP/3 UDP, 0-RTT)' },
              { value: 'grpc', label: 'gRPC (Multiplexed Stealth)' },
              { value: 'kcp', label: 'KCP (Anti-Packet-Loss ARQ)' },
              { value: 'ssh', label: 'SSH (Encrypted Subsystem)' },
            ]}
          />
          <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5 leading-tight">
            {formData.transport_type === 'quic' &&
              'Fast 0-RTT UDP handshake. Resilient to packet loss (Native TLS 1.3).'}
            {formData.transport_type === 'grpc' &&
              'Multiplexed HTTP/2. Mimics legitimate enterprise API traffic.'}
            {formData.transport_type === 'kcp' &&
              'Aggressive ARQ UDP. Ideal for high packet-loss links (Native encryption).'}
            {formData.transport_type === 'ws' &&
              'Standard WebSocket. Compatible with CDN proxies.'}
            {formData.transport_type === 'wss' &&
              'Secure WebSocket with TLS/uTLS encryption.'}
            {formData.transport_type === 'mws' &&
              'Multiplexed WebSocket. Bundles multiple TCP streams.'}
            {formData.transport_type === 'mwss' &&
              'Multiplexed Secure WebSocket with TLS/uTLS.'}
            {formData.transport_type === 'tcp' && 'Direct raw TCP socket tunnel.'}
            {formData.transport_type === 'ssh' &&
              'Native SSH subsystem encrypted protocol.'}
          </p>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1.5 h-5">
            <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-indigo-500"></span>
              Security / Encryption
            </label>
            <span className="text-[11px] font-mono uppercase text-gray-400 dark:text-gray-500">
              {formData.security_type}
            </span>
          </div>
          <CustomSelect
            value={formData.security_type}
            onChange={(val) => onChange({ security_type: val })}
            options={getSecurityOptions()}
            disabled={isSecurityLocked}
          />
          <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5 leading-tight">
            {(formData.transport_type === 'kcp' || formData.transport_type === 'ssh') &&
              'Secured by the native protocol encryption layer.'}
            {formData.transport_type === 'quic' &&
              'QUIC enforces built-in TLS 1.3 handshake encryption.'}
            {!isSecurityLocked && formData.security_type === 'none' &&
              'No TLS wrapper. Lowest CPU overhead.'}
            {!isSecurityLocked && formData.security_type === 'tls' &&
              'Standard TLS 1.3 handshake encryption.'}
            {!isSecurityLocked && formData.security_type === 'utls' &&
              'Camouflages ClientHello to impersonate real browsers.'}
          </p>
        </div>
      </div>

      {/* 5. uTLS Fingerprint Card (Conditional) */}
      {formData.security_type === 'utls' && (
        <div className="p-3.5 rounded-xl bg-indigo-50/60 dark:bg-indigo-950/20 border border-indigo-200/80 dark:border-indigo-900/50 space-y-2">
          <div className="flex items-center justify-between">
            <label className="text-xs font-bold text-indigo-900 dark:text-indigo-200 flex items-center gap-1.5">
              <Fingerprint size={15} className="text-indigo-600 dark:text-indigo-400" />
              uTLS Client Fingerprint
            </label>
            <span className="text-[10px] px-2 py-0.5 rounded-md font-medium bg-indigo-200/60 text-indigo-800 dark:bg-indigo-900/60 dark:text-indigo-300">
              Anti-DPI Spoofing
            </span>
          </div>
          <p className="text-[11px] text-indigo-700/80 dark:text-indigo-300/80 leading-tight">
            Replicates exact TLS cipher suites, extensions, and curves of legitimate web browsers:
          </p>
          <CustomSelect
            value={formData.utls_fingerprint || 'chrome'}
            onChange={(val) => onChange({ utls_fingerprint: val })}
            options={[
              {
                value: 'chrome',
                label: 'Google Chrome (Recommended - Highest Compatibility)',
              },
              { value: 'firefox', label: 'Mozilla Firefox' },
              { value: 'ios', label: 'Apple iOS Safari' },
              { value: 'android', label: 'Android Chrome' },
              { value: 'edge', label: 'Microsoft Edge' },
              {
                value: 'randomized',
                label: 'Randomized (Rotates browser signature per connection)',
              },
            ]}
          />
        </div>
      )}

      {/* 6. Failover IPs & Selector Strategy */}
      <div className="p-3.5 rounded-xl bg-gray-50/80 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1.5">
              <Globe size={15} className="text-blue-600 dark:text-blue-400" />
              {formData.is_reverse
                ? 'Failover & Secondary Iran Ingress IPs'
                : 'Failover & Additional Foreign Endpoints'}
            </label>
            <p className="text-[11px] text-gray-500 dark:text-gray-400">
              {formData.is_reverse
                ? 'In Reverse Mode, foreign server connects to Iran. Add backup Iran IPs/domains for high-availability failover (one per line).'
                : 'In Direct Mode, Iran server connects to Foreign. Add backup foreign IPs for automatic high-availability failover (one per line).'}
            </p>
          </div>
          {formData.failover_ips && formData.failover_ips.trim().length > 0 && (
            <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 dark:bg-blue-900/60 dark:text-blue-300 shrink-0">
              Multi-Node Active
            </span>
          )}
        </div>
        <textarea
          value={formData.failover_ips || ''}
          onChange={(e) => onChange({ failover_ips: e.target.value })}
          className="w-full px-3 py-2 text-sm sm:text-xs font-mono rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500/20"
          placeholder={formData.is_reverse ? 'iran-backup.example.com\n198.51.100.2' : '1.2.3.4\n5.6.7.8'}
          rows={2}
        />

        {formData.failover_ips && formData.failover_ips.trim().length > 0 && (
          <div className="pt-2 border-t border-gray-200/80 dark:border-gray-700/80 space-y-1.5">
            <div className="flex items-center justify-between">
              <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1">
                <Scale size={14} className="text-blue-500" />
                Load Balancing / Failover Strategy
              </label>
              <span className="text-[11px] font-mono text-gray-400">
                {formData.selector_strategy || 'fifo'}
              </span>
            </div>
            <CustomSelect
              value={formData.selector_strategy || 'fifo'}
              onChange={(val) => onChange({ selector_strategy: val })}
              options={[
                {
                  value: 'fifo',
                  label: 'FIFO Failover (Primary first, fallback to backup IPs)',
                },
                {
                  value: 'round',
                  label: 'Round-Robin (Distribute requests evenly across all IPs)',
                },
                {
                  value: 'parallel',
                  label: 'Parallel Race (Connect all concurrently, use fastest ping)',
                },
                {
                  value: 'rand',
                  label: 'Random (Random distribution across nodes)',
                },
              ]}
            />
          </div>
        )}
      </div>

      {/* 7. Mode Switches (Reverse, CDN, Gaming) */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
        <ToggleCard
          label="Reverse Mode"
          description="Foreign connects to Iran (Recommended)"
          icon={<ArrowLeftRight size={15} className="text-blue-600 dark:text-blue-400" />}
          checked={formData.is_reverse}
          onChange={(checked) => onChange({ is_reverse: checked })}
          activeBgClass="peer-checked:bg-blue-600"
        />

        <ToggleCard
          label="CDN Mode"
          description="Cloudflare / WS proxy"
          icon={<Globe size={15} className="text-sky-600 dark:text-sky-400" />}
          checked={formData.cdn_mode}
          onChange={(checked) => {
            const updates: any = { cdn_mode: checked }
            if (checked && ['tcp', 'udp', 'tcp+udp'].includes(formData.transport_type)) {
              updates.transport_type = 'ws'
              showToast(
                'info',
                'Transport Switched',
                'CDN mode requires WebSocket transport. Auto-switched to WS.'
              )
            }
            onChange(updates)
          }}
          activeBgClass="peer-checked:bg-blue-600"
        />

        <ToggleCard
          label="Gaming (Mux)"
          description="Yamux lower ping"
          icon={<Gamepad2 size={15} className="text-indigo-600 dark:text-indigo-400" />}
          checked={formData.gaming_mode}
          onChange={(checked) => onChange({ gaming_mode: checked })}
          activeBgClass="peer-checked:bg-blue-600"
        />
      </div>

      {/* 8. Security, Limits & KeepAlive Tuning */}
      <div className="pt-3 border-t border-gray-200/80 dark:border-gray-700/80 space-y-3">
        <div className="flex items-center justify-between">
          <h5 className="text-xs font-bold uppercase tracking-wider text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
            <ShieldCheck size={15} className="text-gray-700 dark:text-gray-300" />
            Security & Network Guard
          </h5>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {/* KeepAlive Interval Card */}
          <div className="p-3 rounded-xl bg-gray-50/80 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700">
            <div className="flex items-center justify-between mb-1">
              <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1">
                <Activity size={14} className="text-rose-500 dark:text-rose-400" />
                KeepAlive (Anti-Drop)
              </label>
              <span className="text-xs font-mono font-bold text-blue-600 dark:text-blue-400">
                {formData.keepalive_interval || 15}s
              </span>
            </div>
            <p className="text-[11px] text-gray-500 dark:text-gray-400 mb-2">
              Probe interval to keep stateful NAT firewalls alive
            </p>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min="5"
                max="120"
                value={formData.keepalive_interval || 15}
                onChange={(e) =>
                  onChange({
                    keepalive_interval: parseInt(e.target.value) || 15,
                  })
                }
                className="w-20 px-2.5 py-1.5 text-xs font-mono font-semibold rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white shadow-2xs"
              />
              <div className="flex items-center gap-1">
                {[10, 15, 30].map((sec) => (
                  <button
                    key={sec}
                    type="button"
                    onClick={() => onChange({ keepalive_interval: sec })}
                    className={`text-[10px] font-mono px-2 py-1 rounded-md border transition-all cursor-pointer ${
                      (formData.keepalive_interval || 15) === sec
                        ? 'bg-blue-600 text-white border-blue-600 font-bold'
                        : 'bg-white dark:bg-gray-700 text-gray-600 dark:text-gray-300 border-gray-200 dark:border-gray-600 hover:bg-gray-100'
                    }`}
                  >
                    {sec}s
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Stealth Domain Card */}
          <div className="p-3 rounded-xl bg-gray-50/80 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700">
            <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1 mb-1">
              <EyeOff size={14} className="text-purple-600 dark:text-purple-400" />
              Stealth / Custom SNI
            </label>
            <p className="text-[11px] text-gray-500 dark:text-gray-400 mb-2">
              TLS Server Name (SNI) spoofing for DPI bypass
            </p>
            <input
              type="text"
              value={formData.custom_sni || formData.stealth_domain || ''}
              onChange={(e) =>
                onChange({
                  stealth_domain: e.target.value,
                  custom_sni: e.target.value,
                })
              }
              className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white shadow-2xs"
              placeholder="e.g. www.google.com"
            />
          </div>
        </div>

        {/* IP Whitelist & Rate Limit Toggles */}
        <div className="space-y-2">
          <div className="p-3 rounded-xl bg-gray-50/80 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700">
            <div className="flex items-center justify-between">
              <div>
                <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1.5">
                  <Shield size={14} className="text-blue-600 dark:text-blue-400" />
                  IP Whitelist (ACL)
                </label>
                <p className="text-[11px] text-gray-500 dark:text-gray-400">
                  Restrict tunnel access to specific IP ranges
                </p>
              </div>
              <label className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  className="sr-only peer"
                  checked={formData.allowed_ips_enabled}
                  onChange={(e) =>
                    onChange({ allowed_ips_enabled: e.target.checked })
                  }
                />
                <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 shrink-0 relative"></div>
              </label>
            </div>
            {formData.allowed_ips_enabled && (
              <div className="mt-2.5 pt-2 border-t border-gray-200/80 dark:border-gray-700/80">
                <textarea
                  value={formData.allowed_ips || ''}
                  onChange={(e) => onChange({ allowed_ips: e.target.value })}
                  className="w-full px-2.5 py-1.5 text-xs font-mono rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  placeholder={'192.168.1.1\n10.0.0.0/24'}
                  rows={2}
                />
              </div>
            )}
          </div>

          <div className="p-3 rounded-xl bg-gray-50/80 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700">
            <div className="flex items-center justify-between">
              <div>
                <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1.5">
                  <Gauge size={14} className="text-amber-600 dark:text-amber-400" />
                  Bandwidth Rate Limit
                </label>
                <p className="text-[11px] text-gray-500 dark:text-gray-400">
                  Throttle client speed per connection
                </p>
              </div>
              <label className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  className="sr-only peer"
                  checked={formData.rate_limit_enabled}
                  onChange={(e) =>
                    onChange({ rate_limit_enabled: e.target.checked })
                  }
                />
                <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 shrink-0 relative"></div>
              </label>
            </div>
            {formData.rate_limit_enabled && (
              <div className="mt-2.5 pt-2 border-t border-gray-200/80 dark:border-gray-700/80 flex items-center gap-2">
                <input
                  type="number"
                  value={formData.rate_limit_mbps || ''}
                  onChange={(e) =>
                    onChange({ rate_limit_mbps: e.target.value })
                  }
                  className="w-24 px-2.5 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white shadow-2xs"
                  placeholder="5"
                  min="0.1"
                  step="0.1"
                />
                <span className="text-xs font-medium text-gray-600 dark:text-gray-400">
                  Mbps
                </span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 9. Host, SNI & Path Configuration */}
      {(formData.cdn_mode ||
        formData.transport_type === 'wss' ||
        formData.transport_type === 'ws' ||
        formData.transport_type === 'mws' ||
        formData.transport_type === 'mwss' ||
        formData.transport_type === 'grpc' ||
        formData.security_type === 'tls' ||
        formData.security_type === 'utls' ||
        formData.type === 'ws' ||
        formData.type === 'grpc') && (
        <div className="p-3.5 rounded-xl bg-blue-50/60 dark:bg-blue-950/20 border border-blue-200/80 dark:border-blue-900/50 space-y-2.5">
          <label className="text-xs font-bold text-blue-900 dark:text-blue-200 flex items-center gap-1.5">
            <Globe size={15} className="text-blue-600 dark:text-blue-400" />
            Host, SNI & Path Configuration
          </label>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <div>
              <label className="block text-[11px] font-medium text-gray-700 dark:text-gray-300 mb-1">
                Custom Host
              </label>
              <input
                type="text"
                value={formData.custom_host || ''}
                onChange={(e) => onChange({ custom_host: e.target.value })}
                className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white shadow-2xs"
                placeholder="e.g. speedtest.net"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-gray-700 dark:text-gray-300 mb-1">
                Custom SNI (TLS)
              </label>
              <input
                type="text"
                value={formData.custom_sni || formData.stealth_domain || ''}
                onChange={(e) =>
                  onChange({
                    custom_sni: e.target.value,
                    stealth_domain: e.target.value,
                  })
                }
                className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white shadow-2xs"
                placeholder="e.g. speedtest.net"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-gray-700 dark:text-gray-300 mb-1">
                WS Path
              </label>
              <input
                type="text"
                value={formData.ws_path || ''}
                onChange={(e) => onChange({ ws_path: e.target.value })}
                className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white shadow-2xs"
                placeholder="/graphql"
              />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
