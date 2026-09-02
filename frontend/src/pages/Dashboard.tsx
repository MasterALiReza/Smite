import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Server, Network, Cpu, MemoryStick, Plus, Activity as ActivityIcon, Globe, ArrowRight, ArrowLeft } from 'lucide-react'
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
  const { t, language, dir } = useLanguage()
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

  if (loading || !status) {
    return (
      <div className="w-full max-w-7xl mx-auto animate-pulse">
        <div className="h-8 bg-gray-200 dark:bg-gray-700 rounded-lg w-1/3 mb-3"></div>
        <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded-lg w-1/2 mb-6"></div>
        <div className="grid grid-cols-2 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-6 mb-6 sm:mb-8">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-28 sm:h-36 bg-gray-200 dark:bg-gray-700 rounded-2xl"></div>
          ))}
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6">
          <div className="h-56 bg-gray-200 dark:bg-gray-700 rounded-2xl"></div>
          <div className="h-56 bg-gray-200 dark:bg-gray-700 rounded-2xl"></div>
        </div>
      </div>
    )
  }

  const ArrowIcon = language === 'fa' ? ArrowLeft : ArrowRight

  return (
    <div className="w-full max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl sm:text-3xl font-bold text-gray-900 dark:text-white tracking-tight">{t.dashboard.title}</h1>
        <p className="text-xs sm:text-sm text-gray-500 dark:text-gray-400 mt-1">{t.dashboard.subtitle}</p>
      </div>

      {/* Stats Grid */}
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

      {/* Bottom Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6">
        {/* System Resources Card */}
        <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-xs border border-gray-200/80 dark:border-gray-700/80 p-5 sm:p-6 transition-shadow hover:shadow-md">
          <div className="flex items-center gap-3 mb-5">
            <div className="p-2.5 bg-purple-100 dark:bg-purple-900/40 rounded-xl text-purple-600 dark:text-purple-400">
              <ActivityIcon className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-gray-900 dark:text-white">{t.dashboard.systemResources}</h2>
              <p className="text-xs text-gray-500 dark:text-gray-400">Hardware utilization overview</p>
            </div>
          </div>
          <div className="space-y-5">
            <ProgressBar
              label="CPU"
              value={status.system.cpu_percent}
              color="purple"
            />
            <ProgressBar
              label="Memory"
              value={status.system.memory_percent}
              color="orange"
            />
          </div>
        </div>

        {/* Quick Actions Card */}
        <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-xs border border-gray-200/80 dark:border-gray-700/80 p-5 sm:p-6 transition-shadow hover:shadow-md flex flex-col justify-between">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2.5 bg-blue-100 dark:bg-blue-900/40 rounded-xl text-blue-600 dark:text-blue-400">
              <Plus className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-gray-900 dark:text-white">{t.dashboard.quickActions}</h2>
              <p className="text-xs text-gray-500 dark:text-gray-400">Fast shortcuts to common operations</p>
            </div>
          </div>
          <div className="space-y-2.5">
            <button 
              onClick={() => navigate('/tunnels?create=true')}
              className="w-full px-4 py-3 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 text-white rounded-xl transition-all duration-200 font-semibold shadow-xs hover:shadow-md flex items-center justify-between min-h-[48px] active:scale-[0.99]"
            >
              <div className="flex items-center gap-2.5">
                <Network size={18} />
                <span className="text-sm">{t.dashboard.createNewTunnel}</span>
              </div>
              <ArrowIcon size={16} className="opacity-80" />
            </button>
            <button 
              onClick={() => navigate('/nodes?add=true')}
              className="w-full px-4 py-3 bg-gray-50 dark:bg-gray-700/70 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-800 dark:text-gray-200 rounded-xl transition-all duration-200 font-medium border border-gray-200/80 dark:border-gray-600/80 flex items-center justify-between min-h-[48px] active:scale-[0.99]"
            >
              <div className="flex items-center gap-2.5">
                <Server size={18} className="text-blue-500" />
                <span className="text-sm">{t.dashboard.addNode}</span>
              </div>
              <ArrowIcon size={16} className="text-gray-400" />
            </button>
            <button 
              onClick={() => navigate('/servers?add=true')}
              className="w-full px-4 py-3 bg-gray-50 dark:bg-gray-700/70 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-800 dark:text-gray-200 rounded-xl transition-all duration-200 font-medium border border-gray-200/80 dark:border-gray-600/80 flex items-center justify-between min-h-[48px] active:scale-[0.99]"
            >
              <div className="flex items-center gap-2.5">
                <Globe size={18} className="text-indigo-500" />
                <span className="text-sm">{t.dashboard.addServer}</span>
              </div>
              <ArrowIcon size={16} className="text-gray-400" />
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
      bg: 'bg-blue-50/50 dark:bg-blue-950/20',
      icon: 'bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400',
      accent: 'bg-blue-500'
    },
    green: {
      bg: 'bg-green-50/50 dark:bg-green-950/20',
      icon: 'bg-green-100 dark:bg-green-900/40 text-green-600 dark:text-green-400',
      accent: 'bg-green-500'
    },
    purple: {
      bg: 'bg-purple-50/50 dark:bg-purple-950/20',
      icon: 'bg-purple-100 dark:bg-purple-900/40 text-purple-600 dark:text-purple-400',
      accent: 'bg-purple-500'
    },
    orange: {
      bg: 'bg-orange-50/50 dark:bg-orange-950/20',
      icon: 'bg-orange-100 dark:bg-orange-900/40 text-orange-600 dark:text-orange-400',
      accent: 'bg-orange-500'
    },
  }

  const colors = colorClasses[color]

  return (
    <motion.div 
      className={`relative bg-white dark:bg-gray-800 rounded-2xl shadow-xs border border-gray-200/80 dark:border-gray-700/80 p-4 sm:p-5 transition-all duration-200 hover:shadow-md flex flex-col justify-between overflow-hidden ${colors.bg}`}
      whileHover={{ y: -2 }}
      whileTap={{ scale: 0.98 }}
      {...props}
    >
      <div>
        <div className="flex items-center justify-between mb-2 sm:mb-3">
          <div className={`p-2 sm:p-2.5 rounded-xl ${colors.icon} transition-transform`}>
            <Icon className="w-5 h-5 sm:w-6 sm:h-6" />
          </div>
        </div>
        <h3 className="text-xs sm:text-sm font-medium text-gray-600 dark:text-gray-400 truncate">{title}</h3>
        <p className="text-xl sm:text-3xl font-extrabold text-gray-900 dark:text-white mt-1 tracking-tight">{value}</p>
      </div>
      <p className="text-[11px] sm:text-xs text-gray-500 dark:text-gray-400 mt-1.5 font-medium truncate">{subtitle}</p>
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
      gradient: 'from-purple-500 to-indigo-600'
    },
    orange: {
      gradient: 'from-orange-500 to-amber-600'
    },
  }

  const colors = colorClasses[color]
  const percentage = Math.min(Math.max(value, 0), 100)

  return (
    <div>
      <div className="flex justify-between items-center text-xs sm:text-sm mb-2">
        <span className="font-semibold text-gray-700 dark:text-gray-300">{label}</span>
        <span className="font-mono font-bold text-gray-900 dark:text-white">{value.toFixed(1)}%</span>
      </div>
      <div className="w-full bg-gray-100 dark:bg-gray-700/80 rounded-full h-3 overflow-hidden p-0.5 shadow-inner">
        <div
          className={`h-full rounded-full bg-gradient-to-r ${colors.gradient} transition-all duration-500 ease-out`}
          style={{ width: `${percentage}%` }}
        />
      </div>
    </div>
  )
}

export default Dashboard
