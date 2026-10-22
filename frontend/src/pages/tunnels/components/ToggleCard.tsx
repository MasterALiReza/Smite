import React from 'react'

interface ToggleCardProps {
  label: string
  description?: string
  checked: boolean
  onChange: (checked: boolean) => void
  icon?: React.ReactNode
  activeBgClass?: string
  disabled?: boolean
}

export const ToggleCard: React.FC<ToggleCardProps> = ({
  label,
  description,
  checked,
  onChange,
  icon,
  activeBgClass = 'peer-checked:bg-blue-600',
  disabled = false,
}) => {
  return (
    <label
      className={`p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between transition-all select-none ${
        disabled
          ? 'opacity-60 cursor-not-allowed'
          : 'cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40'
      }`}
    >
      <div className="pr-2 min-w-0">
        <div className="flex items-center gap-1.5">
          {icon}
          <span className="text-xs font-bold text-gray-900 dark:text-white truncate">
            {label}
          </span>
        </div>
        {description && (
          <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
            {description}
          </p>
        )}
      </div>
      <input
        type="checkbox"
        className="sr-only peer"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <div
        className={`w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all ${activeBgClass} shrink-0 relative`}
      ></div>
    </label>
  )
}
