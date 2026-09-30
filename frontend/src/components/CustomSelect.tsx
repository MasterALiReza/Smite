import React, { useState, useRef, useEffect } from 'react'
import { ChevronDown, Check, Search } from 'lucide-react'

export interface CustomSelectOption {
  value: string
  label: string
  icon?: React.ReactNode
  badge?: string
  color?: string
  description?: string
}

export interface CustomSelectProps {
  value: string
  onChange: (value: string) => void
  options: CustomSelectOption[]
  placeholder?: string
  disabled?: boolean
  required?: boolean
  id?: string
  name?: string
  className?: string
  buttonClassName?: string
  menuClassName?: string
  searchable?: boolean
  searchPlaceholder?: string
  ariaLabel?: string
}

export const CustomSelect: React.FC<CustomSelectProps> = ({
  value,
  onChange,
  options,
  placeholder = 'Select an option',
  disabled = false,
  required = false,
  id,
  name,
  className = '',
  buttonClassName = '',
  menuClassName = '',
  searchable,
  searchPlaceholder = 'Search...',
  ariaLabel
}) => {
  const [isOpen, setIsOpen] = useState(false)
  const [openUpward, setOpenUpward] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const containerRef = useRef<HTMLDivElement>(null)
  const searchInputRef = useRef<HTMLInputElement>(null)

  // Find currently selected option
  const selectedOption = options.find((opt) => opt.value === value)

  // Auto-determine whether search is needed (if > 7 items or explicitly set)
  const isSearchable = searchable ?? options.length > 7

  // Filtered options based on search query
  const filteredOptions = isSearchable && searchQuery.trim()
    ? options.filter((opt) =>
        opt.label.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (opt.badge && opt.badge.toLowerCase().includes(searchQuery.toLowerCase())) ||
        (opt.description && opt.description.toLowerCase().includes(searchQuery.toLowerCase()))
      )
    : options

  // Toggle dropdown and detect available vertical space
  const handleToggle = () => {
    if (disabled) return
    if (!isOpen && containerRef.current) {
      const rect = containerRef.current.getBoundingClientRect()
      const spaceBelow = window.innerHeight - rect.bottom
      const spaceAbove = rect.top
      // Open upward if space below is tight and more space exists above
      setOpenUpward(spaceBelow < 230 && spaceAbove > spaceBelow)
      setSearchQuery('')
    }
    setIsOpen((prev) => !prev)
  }

  // Focus search input when opened
  useEffect(() => {
    if (isOpen && isSearchable) {
      setTimeout(() => {
        searchInputRef.current?.focus()
      }, 50)
    }
  }, [isOpen, isSearchable])

  // Close on click outside or Escape key
  useEffect(() => {
    if (!isOpen) return

    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false)
      }
    }

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setIsOpen(false)
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [isOpen])

  return (
    <div ref={containerRef} className={`relative w-full ${className}`}>
      {/* Hidden input for form submission & HTML validation */}
      {name && <input type="hidden" name={name} value={value} required={required} />}

      {/* Trigger Button */}
      <button
        type="button"
        id={id}
        disabled={disabled}
        onClick={handleToggle}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        aria-label={ariaLabel || placeholder}
        className={`w-full px-3.5 py-2.5 border rounded-xl flex items-center justify-between text-start text-sm font-medium transition-all shadow-xs outline-none select-none min-h-[42px] ${
          disabled
            ? 'opacity-50 cursor-not-allowed bg-slate-100 dark:bg-gray-800 border-slate-200 dark:border-gray-700 text-slate-400'
            : isOpen
            ? 'bg-white dark:bg-gray-700/90 border-blue-500 ring-2 ring-blue-500/20 text-slate-900 dark:text-white cursor-pointer'
            : 'bg-white dark:bg-gray-700/80 border-gray-300 dark:border-gray-600 text-gray-900 dark:text-white hover:border-gray-400 dark:hover:border-gray-500 cursor-pointer'
        } ${buttonClassName}`}
      >
        <div className="flex items-center gap-2 truncate min-w-0 flex-1">
          {selectedOption ? (
            <>
              {selectedOption.icon && <span className="shrink-0">{selectedOption.icon}</span>}
              <span className="truncate">{selectedOption.label}</span>
              {selectedOption.badge && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-blue-500/10 text-blue-600 dark:text-blue-400 font-mono font-bold shrink-0">
                  {selectedOption.badge}
                </span>
              )}
            </>
          ) : (
            <span className="text-gray-400 dark:text-gray-400 truncate">{placeholder}</span>
          )}
        </div>

        <ChevronDown
          size={15}
          className={`text-gray-400 dark:text-gray-400 shrink-0 ms-2 transition-transform duration-200 ${
            isOpen ? 'rotate-180 text-blue-500' : ''
          }`}
        />
      </button>

      {/* Dropdown Menu Popover */}
      {isOpen && (
        <div
          role="listbox"
          className={`absolute start-0 w-full z-[120] rounded-2xl border shadow-2xl backdrop-blur-md animate-in fade-in-50 zoom-in-95 duration-100 bg-white dark:bg-[#161c28] border-slate-200/90 dark:border-white/[0.1] py-1.5 flex flex-col ${
            openUpward ? 'bottom-full mb-1.5' : 'top-full mt-1.5'
          } ${menuClassName}`}
          style={{ minWidth: '100%' }}
        >
          {/* Optional Search Filter */}
          {isSearchable && (
            <div className="px-2 pt-1 pb-2 border-b border-slate-100 dark:border-white/[0.06] shrink-0">
              <div className="relative">
                <Search size={13} className="absolute start-2.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
                <input
                  ref={searchInputRef}
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder={searchPlaceholder}
                  className="w-full h-8 ps-8 pe-3 bg-slate-50 dark:bg-black/30 border border-slate-200 dark:border-white/[0.08] rounded-lg text-xs font-medium text-slate-900 dark:text-white placeholder:text-slate-400 outline-none focus:border-blue-500 transition-colors"
                />
              </div>
            </div>
          )}

          {/* Options List with Unified Custom Scrollbar */}
          <div className="max-h-56 overflow-y-auto custom-scrollbar p-1 space-y-0.5">
            {filteredOptions.length > 0 ? (
              filteredOptions.map((opt) => {
                const isSelected = opt.value === value
                return (
                  <button
                    type="button"
                    key={opt.value}
                    role="option"
                    aria-selected={isSelected}
                    onClick={() => {
                      onChange(opt.value)
                      setIsOpen(false)
                    }}
                    className={`w-full px-3 py-2 text-xs sm:text-sm font-medium rounded-xl flex items-center justify-between text-start transition-colors cursor-pointer select-none ${
                      isSelected
                        ? 'bg-blue-600/10 dark:bg-blue-500/20 text-blue-600 dark:text-blue-400 font-semibold'
                        : 'text-slate-700 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-white/[0.08] hover:text-slate-900 dark:hover:text-white'
                    }`}
                  >
                    <div className="flex items-center gap-2 min-w-0 flex-1">
                      {opt.icon && <span className="shrink-0">{opt.icon}</span>}
                      <span className="truncate">{opt.label}</span>
                      {opt.badge && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-slate-100 dark:bg-white/10 text-slate-500 dark:text-slate-400 font-mono shrink-0">
                          {opt.badge}
                        </span>
                      )}
                    </div>
                    {isSelected && (
                      <Check size={14} className="shrink-0 text-blue-600 dark:text-blue-400 ms-2" />
                    )}
                  </button>
                )
              })
            ) : (
              <div className="py-4 text-center text-xs text-slate-400 dark:text-slate-500">
                No matching options
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default CustomSelect
