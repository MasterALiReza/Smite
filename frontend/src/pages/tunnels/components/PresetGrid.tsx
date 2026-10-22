import React from 'react'
import { Sparkles, Shield, Zap, Rocket, Globe, Layers, Gamepad2 } from 'lucide-react'
import { TunnelPreset } from '../presets'

interface PresetGridProps {
  presets: TunnelPreset[]
  currentData: Record<string, any>
  onApply: (preset: TunnelPreset) => void
  label?: string
  hint?: string
}

export const PresetGrid: React.FC<PresetGridProps> = ({
  presets,
  currentData,
  onApply,
  label = 'Quick Presets (1-Click Optimization)',
  hint = 'Auto-configures transport, encryption & performance',
}) => {
  const renderIcon = (iconName: TunnelPreset['icon'], colorClass: string) => {
    switch (iconName) {
      case 'shield':
        return <Shield size={16} className={colorClass} />
      case 'zap':
        return <Zap size={16} className={colorClass} />
      case 'rocket':
        return <Rocket size={16} className={colorClass} />
      case 'globe':
        return <Globe size={16} className={colorClass} />
      case 'layers':
        return <Layers size={16} className={colorClass} />
      case 'gamepad':
        return <Gamepad2 size={16} className={colorClass} />
      default:
        return <Sparkles size={16} className={colorClass} />
    }
  }

  const getPresetClasses = (
    preset: TunnelPreset,
    isActive: boolean
  ): { container: string; title: string; icon: string } => {
    switch (preset.colorScheme) {
      case 'purple':
        return {
          container: isActive
            ? 'bg-purple-100/80 dark:bg-purple-950/50 border-purple-400 dark:border-purple-500 ring-2 ring-purple-400/20'
            : 'bg-white/80 dark:bg-gray-800/80 hover:bg-purple-50 dark:hover:bg-purple-950/30 border-gray-200 dark:border-gray-700',
          title: 'text-purple-700 dark:text-purple-300',
          icon: 'text-purple-600 dark:text-purple-400',
        }
      case 'amber':
        return {
          container: isActive
            ? 'bg-amber-100/80 dark:bg-amber-950/50 border-amber-400 dark:border-amber-500 ring-2 ring-amber-400/20'
            : 'bg-white/80 dark:bg-gray-800/80 hover:bg-amber-50 dark:hover:bg-amber-950/30 border-gray-200 dark:border-gray-700',
          title: 'text-amber-700 dark:text-amber-300',
          icon: 'text-amber-600 dark:text-amber-400',
        }
      case 'emerald':
        return {
          container: isActive
            ? 'bg-emerald-100/80 dark:bg-emerald-950/50 border-emerald-400 dark:border-emerald-500 ring-2 ring-emerald-400/20'
            : 'bg-white/80 dark:bg-gray-800/80 hover:bg-emerald-50 dark:hover:bg-emerald-950/30 border-gray-200 dark:border-gray-700',
          title: 'text-emerald-700 dark:text-emerald-300',
          icon: 'text-emerald-600 dark:text-emerald-400',
        }
      case 'cyan':
        return {
          container: isActive
            ? 'bg-cyan-100/80 dark:bg-cyan-950/50 border-cyan-400 dark:border-cyan-500 ring-2 ring-cyan-400/20'
            : 'bg-white/80 dark:bg-gray-800/80 hover:bg-cyan-50 dark:hover:bg-cyan-950/30 border-gray-200 dark:border-gray-700',
          title: 'text-cyan-700 dark:text-cyan-300',
          icon: 'text-cyan-600 dark:text-cyan-400',
        }
      case 'indigo':
        return {
          container: isActive
            ? 'bg-indigo-100/80 dark:bg-indigo-950/50 border-indigo-400 dark:border-indigo-500 ring-2 ring-indigo-400/20'
            : 'bg-white/80 dark:bg-gray-800/80 hover:bg-indigo-50 dark:hover:bg-indigo-950/30 border-gray-200 dark:border-gray-700',
          title: 'text-indigo-700 dark:text-indigo-300',
          icon: 'text-indigo-600 dark:text-indigo-400',
        }
      case 'blue':
      default:
        return {
          container: isActive
            ? 'bg-blue-100/80 dark:bg-blue-950/50 border-blue-400 dark:border-blue-500 ring-2 ring-blue-400/20'
            : 'bg-white/80 dark:bg-gray-800/80 hover:bg-blue-50 dark:hover:bg-blue-950/30 border-gray-200 dark:border-gray-700',
          title: 'text-blue-700 dark:text-blue-300',
          icon: 'text-blue-600 dark:text-blue-400',
        }
    }
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
          <Sparkles size={14} className="text-blue-500" />
          {label}
        </span>
        <span className="text-[11px] text-gray-400 dark:text-gray-500 hidden sm:inline">
          {hint}
        </span>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        {presets.map((preset) => {
          const active = preset.isActive(currentData)
          const styles = getPresetClasses(preset, active)

          return (
            <button
              key={preset.id}
              type="button"
              onClick={() => onApply(preset)}
              className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between shadow-2xs ${styles.container}`}
            >
              <div>
                <div className="flex items-center justify-between gap-1 mb-1">
                  <div className="flex items-center gap-1.5">
                    {renderIcon(preset.icon, styles.icon)}
                    <span className={`text-xs font-bold ${styles.title}`}>
                      {preset.name}
                    </span>
                  </div>
                  {preset.badge && (
                    <span className="text-[9px] font-semibold px-1.5 py-0.5 rounded-md bg-black/5 dark:bg-white/10 text-gray-600 dark:text-gray-300 uppercase tracking-wider shrink-0">
                      {preset.badge}
                    </span>
                  )}
                </div>
                <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                  {preset.description}
                </p>
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}
