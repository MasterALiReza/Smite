import React from 'react'

interface CoreSectionHeaderProps {
  title: string
  subtitle: string
  badgeText: string
  icon: React.ReactNode
  iconBoxClass?: string
  badgeClass?: string
}

export const CoreSectionHeader: React.FC<CoreSectionHeaderProps> = ({
  title,
  subtitle,
  badgeText,
  icon,
  iconBoxClass = 'bg-blue-100 dark:bg-blue-900/60 text-blue-600 dark:text-blue-300',
  badgeClass = 'bg-blue-100/80 text-blue-700 dark:bg-blue-900/60 dark:text-blue-300 border-blue-200 dark:border-blue-800',
}) => {
  return (
    <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 pb-3 border-b border-gray-200/70 dark:border-gray-700/70">
      <div className="flex items-center gap-2.5">
        <span className={`p-1.5 rounded-lg shrink-0 ${iconBoxClass}`}>
          {icon}
        </span>
        <div className="min-w-0">
          <h4 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider truncate">
            {title}
          </h4>
          <p className="text-xs text-gray-500 dark:text-gray-400 line-clamp-1 sm:line-clamp-none">
            {subtitle}
          </p>
        </div>
      </div>
      <span
        className={`self-start sm:self-auto text-xs px-2.5 py-0.5 rounded-full font-mono font-medium border shrink-0 ${badgeClass}`}
      >
        {badgeText}
      </span>
    </div>
  )
}
