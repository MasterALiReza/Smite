import React from 'react'
import { Cpu, Radio, Server, Key, EyeOff, Shield } from 'lucide-react'
import { CustomSelect } from '../../../components/CustomSelect'
import { CoreSectionHeader } from '../components/CoreSectionHeader'
import { PresetGrid } from '../components/PresetGrid'
import { PortsField } from '../components/PortsField'
import { RATHOLE_PRESETS } from '../presets'
import { generateRandomControlPort } from '../formHelpers'

interface RatholeSectionProps {
  formData: any
  onChange: (updates: Record<string, any>) => void
  showToast: (type: 'success' | 'error' | 'warning' | 'info', title: string, message: string) => void
}

export const RatholeSection: React.FC<RatholeSectionProps> = ({
  formData,
  onChange,
  showToast,
}) => {
  return (
    <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-br from-orange-50/60 via-gray-50 to-amber-50/40 dark:from-orange-950/20 dark:via-gray-800/60 dark:to-amber-950/20 border border-orange-200/70 dark:border-orange-900/40 space-y-4">
      {/* 1. Header */}
      <CoreSectionHeader
        title="Rathole Settings"
        subtitle="Ultra-Lightweight, Secure NAT-Traversal Core in Rust"
        badgeText="Rathole Core"
        icon={<Cpu size={16} />}
        iconBoxClass="bg-orange-100 dark:bg-orange-900/60 text-orange-600 dark:text-orange-300"
        badgeClass="bg-orange-100/80 text-orange-700 dark:bg-orange-900/60 dark:text-orange-300 border-orange-200 dark:border-orange-800"
      />

      {/* 2. Quick Presets */}
      <PresetGrid
        presets={RATHOLE_PRESETS}
        currentData={formData}
        onApply={(preset) => {
          onChange(preset.appliedValues)
          showToast('info', preset.toastTitle || 'Preset Applied', preset.toastMessage)
        }}
      />

      {/* 3. Ports & Connectivity */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start pt-1">
        <PortsField
          label="Forwarded Ports"
          value={formData.ports}
          onChange={(val) => onChange({ ports: val })}
          placeholder="8080,8081,8082"
          helperText="Ports (comma-separated, same for panel and node local service)."
          icon={<Radio size={14} className="text-orange-500" />}
          accentClass="focus:ring-orange-500/20 focus:border-orange-500"
          required
        />

        <PortsField
          label="Rathole Port"
          value={formData.rathole_remote_addr}
          onChange={(val) => onChange({ rathole_remote_addr: val })}
          placeholder="23333"
          helperText={`Rathole server port on panel (IP: ${typeof window !== 'undefined' ? window.location.hostname : 'localhost'}).`}
          icon={<Server size={14} className="text-amber-500" />}
          type="number"
          min="1"
          max="65535"
          showRandomBtn
          onRandom={() => onChange({ rathole_remote_addr: generateRandomControlPort() })}
          accentClass="focus:ring-orange-500/20 focus:border-orange-500"
          required
        />
      </div>

      {/* 4. Transport & Auth Token */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
        <div>
          <div className="flex items-center justify-between mb-1.5 h-5">
            <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-orange-500"></span>
              Transport Protocol
            </label>
            <span className="text-[11px] font-mono uppercase text-gray-400">
              {formData.rathole_transport || 'tcp'}
            </span>
          </div>
          <CustomSelect
            value={formData.rathole_transport || 'tcp'}
            onChange={(val) => onChange({ rathole_transport: val })}
            options={[
              { value: 'tcp', label: 'TCP (Standard Raw)' },
              { value: 'noise', label: 'Noise Protocol (Encrypted / Gaming / Anti-DPI)' },
              { value: 'ws', label: 'WebSocket (WS)' },
              { value: 'wss', label: 'WebSocket + TLS (WSS)' },
            ]}
          />
          <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5 leading-tight">
            {formData.rathole_transport === 'noise' &&
              'Noise_KK (X25519/ChaCha20-Poly1305) with mutual keys and replay protection. Low overhead, not WireGuard.'}
            {formData.rathole_transport === 'wss' &&
              'Standard TLS 1.3 encapsulation for anti-DPI camouflage.'}
            {formData.rathole_transport === 'ws' && 'Standard WebSocket stream.'}
            {(!formData.rathole_transport || formData.rathole_transport === 'tcp') &&
              'Raw TCP stream without encryption wrapper.'}
          </p>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1.5 h-5">
            <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
              <Key size={14} className="text-indigo-500" />
              Auth Token
            </label>
            <span className="text-[11px] text-gray-400">Optional</span>
          </div>
          <input
            type="text"
            value={formData.rathole_token || ''}
            onChange={(e) => onChange({ rathole_token: e.target.value })}
            className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all font-mono shadow-2xs"
            placeholder="Auto-generated if empty"
          />
          <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5 leading-tight">
            Cryptographic secret token shared between panel and client.
          </p>
        </div>
      </div>

      {/* 5. SNI Camouflage (Conditional) */}
      {(formData.rathole_transport === 'wss' || formData.rathole_transport === 'ws') && (
        <div className="p-3.5 rounded-xl bg-orange-50/70 dark:bg-orange-950/20 border border-orange-200/80 dark:border-orange-900/50 space-y-2">
          <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1.5">
            <EyeOff size={14} className="text-orange-600 dark:text-orange-400" />
            Custom SNI / Domain Camouflage
          </label>
          <p className="text-[11px] text-gray-500 dark:text-gray-400">
            TLS Server Name Indication (SNI) spoofing to mimic legitimate web services:
          </p>
          <input
            type="text"
            value={formData.custom_sni || ''}
            onChange={(e) => onChange({ custom_sni: e.target.value })}
            placeholder="e.g. dl.google.com or cdn.cloudflare.com"
            className="w-full px-3 py-2 text-sm sm:text-xs rounded-lg border border-orange-300 dark:border-orange-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500/20 font-medium"
          />
        </div>
      )}
    </div>
  )
}
