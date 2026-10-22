import React from 'react'
import { RadioTower } from 'lucide-react'
import { CoreSectionHeader } from '../components/CoreSectionHeader'
import { PresetGrid } from '../components/PresetGrid'
import { BackhaulForm } from '../BackhaulComponents'
import { BackhaulFormState, BackhaulAdvancedState } from '../types'
import { BACKHAUL_PRESETS, TunnelPreset } from '../presets'

interface BackhaulSectionProps {
  state: BackhaulFormState
  advancedState: BackhaulAdvancedState
  onChange: (partial: Partial<BackhaulFormState>) => void
  onAdvancedChange: (updater: (prev: BackhaulAdvancedState) => BackhaulAdvancedState) => void
  onOpenAdvanced: () => void
  acceptUdpVisible?: boolean
  showToast: (type: 'success' | 'error' | 'warning' | 'info', title: string, message: string) => void
}

export const BackhaulSection: React.FC<BackhaulSectionProps> = ({
  state,
  advancedState,
  onChange,
  onAdvancedChange,
  onOpenAdvanced,
  acceptUdpVisible,
  showToast,
}) => {
  const handleApplyPreset = (preset: TunnelPreset) => {
    onChange(preset.appliedValues)

    if (preset.appliedValues.gaming_mode) {
      onAdvancedChange((prev) => ({
        ...prev,
        server: {
          ...prev.server,
          nodelay: true,
          channel_size: '8192',
          mux_framesize: '4096',
          mss: '1380',
          keepalive_period: '12',
          heartbeat: '12',
          so_rcvbuf: '2097152',
          so_sndbuf: '2097152',
        },
        client: {
          ...prev.client,
          nodelay: true,
          channel_size: '8192',
          mux_framesize: '4096',
          mss: '1380',
          keepalive_period: '12',
          heartbeat: '12',
          so_rcvbuf: '2097152',
          so_sndbuf: '2097152',
          aggressive_pool: true,
        },
      }))
    }

    showToast('info', preset.toastTitle || 'Preset Applied', preset.toastMessage)
  }

  return (
    <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-br from-emerald-50/60 via-gray-50 to-teal-50/40 dark:from-emerald-950/20 dark:via-gray-800/60 dark:to-teal-950/20 border border-emerald-200/70 dark:border-emerald-900/40 space-y-4">
      {/* 1. Header */}
      <CoreSectionHeader
        title="Backhaul Multiplexing Settings"
        subtitle="High-Performance Reverse Tunnel with Yamux & WebSockets"
        badgeText="Backhaul Core"
        icon={<RadioTower size={16} />}
        iconBoxClass="bg-emerald-100 dark:bg-emerald-900/60 text-emerald-600 dark:text-emerald-300"
        badgeClass="bg-emerald-100/80 text-emerald-700 dark:bg-emerald-900/60 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800"
      />

      {/* 2. Quick Presets */}
      <PresetGrid
        presets={BACKHAUL_PRESETS}
        currentData={state}
        onApply={handleApplyPreset}
      />

      {/* 3. Core Form Fields */}
      <BackhaulForm
        state={state}
        onChange={onChange}
        onOpenAdvanced={onOpenAdvanced}
        acceptUdpVisible={acceptUdpVisible}
      />
    </div>
  )
}
