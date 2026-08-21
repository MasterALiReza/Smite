import { ReactNode } from 'react'

interface EmptyStateProps {
  icon: ReactNode
  title: string
  description?: string
  action?: {
    label: string
    onClick: () => void
  }
}

export const EmptyState = ({ icon, title, description, action }: EmptyStateProps) => (
  <div className="flex flex-col items-center justify-center py-16 px-4 text-center">
    <div className="w-16 h-16 mb-4 flex items-center justify-center rounded-2xl bg-[#DBE2EF]/60 dark:bg-[#142850] text-[#3F72AF] dark:text-[#00A8CC] border border-[#DBE2EF] dark:border-[#0C7B93]/40 shadow-inner">
      {icon}
    </div>
    <h3 className="text-lg font-bold text-[#112D4E] dark:text-[#F9F7F7] mb-1">{title}</h3>
    {description && (
      <p className="text-sm text-[#112D4E]/70 dark:text-[#DBE2EF]/80 max-w-sm mb-6">{description}</p>
    )}
    {action && (
      <button
        onClick={action.onClick}
        className="px-5 py-2.5 bg-[#3F72AF] hover:bg-[#3F72AF]/90 dark:bg-[#00A8CC] dark:hover:bg-[#00A8CC]/90 text-white dark:text-[#142850] rounded-xl font-semibold text-sm shadow-md shadow-[#3F72AF]/20 dark:shadow-[#00A8CC]/20 transition-all hover:scale-[1.02] active:scale-[0.98]"
      >
        {action.label}
      </button>
    )}
  </div>
)
