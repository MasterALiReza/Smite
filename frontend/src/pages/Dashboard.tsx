import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Server, Network, Cpu, MemoryStick, Plus, Activity as ActivityIcon, Globe, ArrowRight, ArrowLeft, Zap, ShieldCheck } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { motion, HTMLMotionProps } from 'framer-motion'
import { useLanguage } from '../contexts/LanguageContext'
import api from '../api/client'

interface Status {
  system: {
    cpu_percent: number
    memory_percent: number
    memory_total_gb: number
    memory_used_gb: number
  }
  tunnels: {
    total: number
    active: number
  }
  nodes: {
    total: number
    active: number
  }
}

const Dashboard = () => {
  const [status, setStatus] = useState<Status | null>(null)
  const [loading, setLoading] = useState(true)
  const { t, language } = useLanguage()
  const navigate = useNavigate()

  useEffect(() => {
    const fetchData = async () => {
      try {
        const statusResponse = await api.get('/status')
        setStatus(statusResponse.data)
      } catch (error) {
        console.error('Failed to fetch data:', error)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
    const interval = setInterval(fetchData, 5000)
    return () => {
      clearInterval(interval)
    }
  }, [])

  const isRTL = language === 'fa'
  const ArrowIcon = isRTL ? ArrowLeft : ArrowRight

  if (loading || !status) {
    return (
      <div className="w-full max-w-7xl mx-auto space-y-6 animate-pulse">
        <div className="space-y-2">
          <div className="h-8 bg-slate-200 dark:bg-white/[0.05] rounded-xl w-48"></div>
          <div className="h-4 bg-slate-200 dark:bg-white/[0.03] rounded-lg w-72"></div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-5">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-32 sm:h-36 bg-slate-200/80 dark:bg-white/[0.04] rounded-2xl border border-slate-200/60 dark:border-white/[0.05]"></div>
          ))}
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6">
          <div className="h-64 bg-slate-200/80 dark:bg-white/[0.04] rounded-3xl border border-slate-200/60 dark:border-white/[0.05]"></div>
          <div className="h-64 bg-slate-200/80 dark:bg-white/[0.04] rounded-3xl border border-slate-200/60 dark:border-white/[0.05]"></div>
        </div>
      </div>
    )
  }

  return (
    <div className="w-full max-w-7xl mx-auto space-y-6 sm:space-y-8 font-sans">
      {/* Header with Live Engine Beacon */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-2 border-b border-slate-200/60 dark:border-white/[0.05]">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-2xl sm:text-3xl font-extrabold tracking-tight text-slate-900 dark:text-white">
              {t.dashboard.title}
            </h1>
            <span className="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-mono font-medium bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
              LIVE
            </span>
          </div>
          <p className="text-xs sm:text-sm text-slate-500 dark:text-slate-400 mt-1 font-medium">{t.dashboard.subtitle}</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-xl bg-white dark:bg-white/[0.04] border border-slate-200/80 dark:border-white/[0.08] shadow-2xs text-xs font-mono text-slate-600 dark:text-slate-300">
            <ShieldCheck size={14} className="text-sky-500" />
            <span>Zero-Touch TLS</span>
          </div>
        </div>
      </div>

      {/* Stats Cards Grid */}
      <motion.div 
        className="grid grid-cols-2 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-5"
        initial="hidden"
        animate="show"
        variants={{
          hidden: { opacity: 0 },
          show: { opacity: 1, transition: { staggerChildren: 0.08 } }
        }}
      >
        <StatCard
          title={t.dashboard.totalNodes}
          value={status.nodes.total}
          subtitle={`${status.nodes.active} ${t.dashboard.active}`}
          icon={Server}
          color="blue"
          variants={{ hidden: { opacity: 0, y: 15 }, show: { opacity: 1, y: 0, transition: { type: "spring", stiffness: 300, damping: 24 } } }}
        />
        <StatCard
          title={t.dashboard.totalTunnels}
          value={status.tunnels.total}
          subtitle={`${status.tunnels.active} ${t.dashboard.active}`}
          icon={Network}
          color="green"
          variants={{ hidden: { opacity: 0, y: 15 }, show: { opacity: 1, y: 0, transition: { type: "spring", stiffness: 300, damping: 24 } } }}
        />
        <StatCard
          title={t.dashboard.cpuUsage}
          value={`${status.system.cpu_percent.toFixed(1)}%`}
          subtitle={t.dashboard.currentUsage}
          icon={Cpu}
          color="purple"
          variants={{ hidden: { opacity: 0, y: 15 }, show: { opacity: 1, y: 0, transition: { type: "spring", stiffness: 300, damping: 24 } } }}
        />
        <StatCard
          title={t.dashboard.memoryUsage}
          value={`${status.system.memory_used_gb.toFixed(1)} GB`}
          subtitle={`${status.system.memory_percent.toFixed(0)}% / ${status.system.memory_total_gb.toFixed(0)} GB`}
          icon={MemoryStick}
          color="orange"
          variants={{ hidden: { opacity: 0, y: 15 }, show: { opacity: 1, y: 0, transition: { type: "spring", stiffness: 300, damping: 24 } } }}
        />
      </motion.div>

      {/* Hardware Utilization & Quick Actions */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6">
        {/* System Resources Card */}
        <div className="bg-white/85 dark:bg-[#0c1220]/85 rounded-3xl shadow-sm border border-slate-200/80 dark:border-white/[0.08] p-5 sm:p-6 backdrop-blur-xl transition-all hover:border-slate-300 dark:hover:border-white/[0.15]">
          <div className="flex items-center gap-3 mb-5">
            <div className="p-2.5 bg-purple-500/10 dark:bg-purple-500/15 rounded-2xl text-purple-600 dark:text-purple-400 border border-purple-200/50 dark:border-purple-500/20 shadow-glow-sm">
              <ActivityIcon className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-base sm:text-lg font-bold text-slate-900 dark:text-white tracking-tight">
                {t.dashboard.systemResources}
              </h2>
              <p className="text-xs text-slate-500 dark:text-slate-400 font-medium">Hardware utilization overview</p>
            </div>
          </div>
          <div className="space-y-5">
            <ProgressBar
              label="CPU"
              value={status.system.cpu_percent}
              color="purple"
            />
            <ProgressBar
              label="Memory (RAM)"
              value={status.system.memory_percent}
              color="orange"
            />
          </div>
          <div className="mt-5 pt-4 border-t border-slate-100 dark:border-white/[0.05] flex items-center justify-between text-[11px] font-mono text-slate-500 dark:text-slate-400">
            <span>Allocated: {status.system.memory_used_gb.toFixed(1)} GB</span>
            <span>Total: {status.system.memory_total_gb.toFixed(0)} GB</span>
          </div>
        </div>

        {/* Quick Actions Card */}
        <div className="bg-white/85 dark:bg-[#0c1220]/85 rounded-3xl shadow-sm border border-slate-200/80 dark:border-white/[0.08] p-5 sm:p-6 backdrop-blur-xl transition-all hover:border-slate-300 dark:hover:border-white/[0.15] flex flex-col justify-between">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2.5 bg-blue-500/10 dark:bg-sky-500/15 rounded-2xl text-blue-600 dark:text-sky-400 border border-blue-200/50 dark:border-sky-500/20 shadow-glow-sm">
              <Plus className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-base sm:text-lg font-bold text-slate-900 dark:text-white tracking-tight">
                {t.dashboard.quickActions}
              </h2>
              <p className="text-xs text-slate-500 dark:text-slate-400 font-medium">Fast shortcuts to common operations</p>
            </div>
          </div>

          <div className="space-y-2.5">
            {/* Primary Action: Create Tunnel */}
            <button 
              onClick={() => navigate('/tunnels?create=true')}
              className="w-full group px-4 py-3 bg-gradient-to-r from-blue-600 via-sky-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white rounded-2xl transition-all duration-200 font-semibold shadow-lg shadow-blue-600/20 flex items-center justify-between min-h-[48px] active:scale-[0.98] cursor-pointer"
            >
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-xl bg-white/20 flex items-center justify-center">
                  <Network size={18} className="text-white" />
                </div>
                <span className="text-sm font-bold tracking-tight">{t.dashboard.createNewTunnel}</span>
              </div>
              <div className="w-7 h-7 rounded-full bg-white/15 flex items-center justify-center transition-transform duration-200 group-hover:scale-110">
                <ArrowIcon size={14} className="text-white" />
              </div>
            </button>

            {/* Secondary Action: Add Iran Node */}
            <button 
              onClick={() => navigate('/nodes?add=true')}
              className="w-full group px-4 py-3 bg-slate-50 dark:bg-white/[0.04] hover:bg-slate-100 dark:hover:bg-white/[0.08] text-slate-800 dark:text-slate-200 rounded-2xl transition-all duration-200 font-medium border border-slate-200/80 dark:border-white/[0.08] flex items-center justify-between min-h-[48px] active:scale-[0.98] cursor-pointer"
            >
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-xl bg-blue-500/10 dark:bg-sky-500/15 flex items-center justify-center text-blue-600 dark:text-sky-400">
                  <Server size={17} />
                </div>
                <span className="text-sm font-semibold tracking-tight">{t.dashboard.addNode}</span>
              </div>
              <ArrowIcon size={15} className="text-slate-400 group-hover:text-slate-600 dark:group-hover:text-slate-200 transition-colors" />
            </button>

            {/* Secondary Action: Add Foreign Server */}
            <button 
              onClick={() => navigate('/servers?add=true')}
              className="w-full group px-4 py-3 bg-slate-50 dark:bg-white/[0.04] hover:bg-slate-100 dark:hover:bg-white/[0.08] text-slate-800 dark:text-slate-200 rounded-2xl transition-all duration-200 font-medium border border-slate-200/80 dark:border-white/[0.08] flex items-center justify-between min-h-[48px] active:scale-[0.98] cursor-pointer"
            >
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-xl bg-indigo-500/10 dark:bg-indigo-500/15 flex items-center justify-center text-indigo-600 dark:text-indigo-400">
                  <Globe size={17} />
                </div>
                <span className="text-sm font-semibold tracking-tight">{t.dashboard.addServer}</span>
              </div>
              <ArrowIcon size={15} className="text-slate-400 group-hover:text-slate-600 dark:group-hover:text-slate-200 transition-colors" />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

interface StatCardProps {
  title: string
  value: string | number
  subtitle: string
  icon: LucideIcon
  color: 'blue' | 'green' | 'purple' | 'orange'
}

const StatCard = ({ title, value, subtitle, icon: Icon, color, ...props }: StatCardProps & HTMLMotionProps<"div">) => {
  const colorClasses = {
    blue: {
      bg: 'hover:border-blue-500/40 dark:hover:border-sky-500/40',
      icon: 'bg-blue-500/10 dark:bg-sky-500/15 text-blue-600 dark:text-sky-400 border border-blue-200/50 dark:border-sky-500/20 shadow-glow-sm',
      accent: 'bg-gradient-to-r from-blue-500 to-sky-400'
    },
    green: {
      bg: 'hover:border-emerald-500/40 dark:hover:border-emerald-500/40',
      icon: 'bg-emerald-500/10 dark:bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-200/50 dark:border-emerald-500/20 shadow-glow-emerald',
      accent: 'bg-gradient-to-r from-emerald-500 to-teal-400'
    },
    purple: {
      bg: 'hover:border-purple-500/40 dark:hover:border-purple-500/40',
      icon: 'bg-purple-500/10 dark:bg-purple-500/15 text-purple-600 dark:text-purple-400 border border-purple-200/50 dark:border-purple-500/20',
      accent: 'bg-gradient-to-r from-purple-500 to-indigo-400'
    },
    orange: {
      bg: 'hover:border-amber-500/40 dark:hover:border-amber-500/40',
      icon: 'bg-amber-500/10 dark:bg-amber-500/15 text-amber-600 dark:text-amber-400 border border-amber-200/50 dark:border-amber-500/20',
      accent: 'bg-gradient-to-r from-amber-500 to-orange-400'
    },
  }

  const colors = colorClasses[color]

  return (
    <motion.div 
      className={`relative bg-white/90 dark:bg-[#0c1220]/90 rounded-3xl shadow-sm border border-slate-200/80 dark:border-white/[0.08] p-4 sm:p-5 transition-all duration-300 hover:shadow-xl flex flex-col justify-between overflow-hidden backdrop-blur-xl ${colors.bg}`}
      whileHover={{ y: -2 }}
      whileTap={{ scale: 0.98 }}
      {...props}
    >
      <div>
        <div className="flex items-center justify-between mb-2 sm:mb-3">
          <div className={`p-2.5 rounded-2xl ${colors.icon}`}>
            <Icon className="w-5 h-5 sm:w-5 sm:h-5" />
          </div>
        </div>
        <h3 className="text-xs sm:text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider truncate">{title}</h3>
        <p className="text-xl sm:text-2xl lg:text-3xl font-extrabold text-slate-900 dark:text-white mt-1 tracking-tight font-mono tabular-nums">{value}</p>
      </div>
      <p className="text-[11px] sm:text-xs text-slate-500 dark:text-slate-400 mt-2 font-medium truncate">{subtitle}</p>
      <div className={`absolute bottom-0 left-0 right-0 h-1 ${colors.accent}`}></div>
    </motion.div>
  )
}

interface ProgressBarProps {
  label: string
  value: number
  color: 'purple' | 'orange'
}

const ProgressBar = ({ label, value, color }: ProgressBarProps) => {
  const colorClasses = {
    purple: {
      gradient: 'from-blue-500 via-sky-500 to-indigo-600'
    },
    orange: {
      gradient: 'from-amber-500 via-orange-500 to-rose-500'
    },
  }

  const colors = colorClasses[color]
  const percentage = Math.min(Math.max(value, 0), 100)

  return (
    <div>
      <div className="flex justify-between items-center text-xs sm:text-sm mb-2">
        <span className="font-semibold text-slate-700 dark:text-slate-300">{label}</span>
        <span className="font-mono font-bold text-slate-900 dark:text-white tabular-nums">{value.toFixed(1)}%</span>
      </div>
      <div className="w-full bg-slate-100 dark:bg-white/[0.06] rounded-full h-2.5 overflow-hidden p-0.5 shadow-inner">
        <div
          className={`h-full rounded-full bg-gradient-to-r ${colors.gradient} transition-all duration-700 ease-spring`}
          style={{ width: `${percentage}%` }}
        />
      </div>
    </div>
  )
}

export default Dashboard
