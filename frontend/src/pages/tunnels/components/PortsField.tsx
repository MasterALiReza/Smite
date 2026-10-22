import React from 'react'
import { Dices, Radio } from 'lucide-react'

interface PortsFieldProps {
  value: string | number
  onChange: (val: string) => void
  label: string
  placeholder?: string
  helperText?: string
  icon?: React.ReactNode
  badge?: string
  showRandomBtn?: boolean
  onRandom?: () => void
  type?: 'text' | 'number'
  required?: boolean
  min?: number | string
  max?: number | string
  accentClass?: string
}

export const PortsField: React.FC<PortsFieldProps> = ({
  value,
  onChange,
  label,
  placeholder = '8080',
  helperText,
  icon,
  badge,
  showRandomBtn = false,
  onRandom,
  type = 'text',
  required = false,
  min,
  max,
  accentClass = 'focus:ring-blue-500/20 focus:border-blue-500',
}) => {
  return (
    <div>
      <div className="flex items-center justify-between mb-1.5 h-5">
        <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
          {icon || <Radio size={14} className="text-blue-500" />}
          <span>{label}</span>
        </label>
        {badge && (
          <span className="text-[11px] text-gray-400 dark:text-gray-500 font-mono">
            {badge}
          </span>
        )}
        {showRandomBtn && onRandom && (
          <button
            type="button"
            onClick={onRandom}
            className="flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[11px] font-medium bg-gray-100 hover:bg-gray-200 dark:bg-gray-700/80 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-gray-600 transition-all cursor-pointer shadow-2xs"
            title="Generate Random Port"
          >
            <Dices size={12} />
            <span>Random</span>
          </button>
        )}
      </div>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 font-mono transition-all shadow-2xs ${accentClass}`}
        placeholder={placeholder}
        required={required}
        min={min}
        max={max}
      />
      {helperText && (
        <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5 leading-tight">
          {helperText}
        </p>
      )}
    </div>
  )
}
