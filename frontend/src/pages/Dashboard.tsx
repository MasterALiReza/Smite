import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Server, Network, Cpu, MemoryStick, Plus, Activity as ActivityIcon } from 'lucide-react'
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
  const { t, dir } = useLanguage()
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
        <div className="h-8 bg-[#DBE2EF] dark:bg-[#27496D] rounded-xl w-1/4 mb-4"></div>
        <div className="h-4 bg-[#DBE2EF] dark:bg-[#27496D] rounded-xl w-1/3 mb-8"></div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-36 bg-[#DBE2EF]/70 dark:bg-[#27496D] rounded-2xl"></div>
          ))}
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="h-64 bg-[#DBE2EF]/70 dark:bg-[#27496D] rounded-2xl"></div>
          <div className="h-64 bg-[#DBE2EF]/70 dark:bg-[#27496D] rounded-2xl"></div>
        </div>
      </div>
    )
  }

  return (
    <div className="w-full max-w-7xl mx-auto space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-black tracking-tight text-[#112D4E] dark:text-[#F9F7F7] mb-2">{t.dashboard.title}</h1>
        <p className="text-sm font-medium text-[#112D4E]/70 dark:text-[#DBE2EF]/80">{t.dashboard.subtitle}</p>
      </div>

      {/* Stats Grid */}
      <motion.div 
        className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5"
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
          variants={{ hidden: { opacity: 0, y: 16 }, show: { opacity: 1, y: 0, transition: { type: "spring", stiffness: 300, damping: 24 } } }}
        />
        <StatCard
          title={t.dashboard.totalTunnels}
          value={status.tunnels.total}
          subtitle={`${status.tunnels.active} ${t.dashboard.active}`}
          icon={Network}
          color="green"
          variants={{ hidden: { opacity: 0, y: 16 }, show: { opacity: 1, y: 0, transition: { type: "spring", stiffness: 300, damping: 24 } } }}
        />
        <StatCard
          title={t.dashboard.cpuUsage}
          value={`${status.system.cpu_percent.toFixed(1)}%`}
          subtitle={t.dashboard.currentUsage}
          icon={Cpu}
          color="cyan"
          variants={{ hidden: { opacity: 0, y: 16 }, show: { opacity: 1, y: 0, transition: { type: "spring", stiffness: 300, damping: 24 } } }}
        />
        <StatCard
          title={t.dashboard.memoryUsage}
          value={`${status.system.memory_used_gb.toFixed(1)} GB`}
          subtitle={`${status.system.memory_percent.toFixed(1)}% of ${status.system.memory_total_gb.toFixed(1)} GB`}
          icon={MemoryStick}
          color="teal"
          variants={{ hidden: { opacity: 0, y: 16 }, show: { opacity: 1, y: 0, transition: { type: "spring", stiffness: 300, damping: 24 } } }}
        />
      </motion.div>

      {/* Bottom Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* System Resources Card */}
        <div className="bg-white dark:bg-[#27496D] rounded-2xl shadow-sm border border-[#DBE2EF] dark:border-[#142850] p-6 transition-all duration-300 hover:shadow-lg dark:hover:shadow-black/25">
          <div className="flex items-center gap-3 mb-6">
            <div className="p-2.5 bg-[#3F72AF]/10 dark:bg-[#00A8CC]/20 rounded-xl text-[#3F72AF] dark:text-[#00A8CC]">
              <ActivityIcon className="w-5 h-5" />
            </div>
            <h2 className="text-lg font-bold text-[#112D4E] dark:text-[#F9F7F7]">{t.dashboard.systemResources}</h2>
          </div>
          <div className="space-y-6">
            <ProgressBar
              label="CPU"
              value={status.system.cpu_percent}
              color="cyan"
            />
            <ProgressBar
              label="Memory"
              value={status.system.memory_percent}
              color="teal"
            />
          </div>
        </div>

        {/* Quick Actions Card */}
        <div className="bg-white dark:bg-[#27496D] rounded-2xl shadow-sm border border-[#DBE2EF] dark:border-[#142850] p-6 transition-all duration-300 hover:shadow-lg dark:hover:shadow-black/25">
          <div className="flex items-center gap-3 mb-6">
            <div className="p-2.5 bg-[#3F72AF]/10 dark:bg-[#00A8CC]/20 rounded-xl text-[#3F72AF] dark:text-[#00A8CC]">
              <Plus className="w-5 h-5" />
            </div>
            <h2 className="text-lg font-bold text-[#112D4E] dark:text-[#F9F7F7]">{t.dashboard.quickActions}</h2>
          </div>
          <div className="space-y-3">
            <button 
              onClick={() => navigate('/tunnels?create=true')}
              className="w-full px-5 py-3 bg-[#3F72AF] hover:bg-[#3F72AF]/90 dark:bg-gradient-to-r dark:from-[#0C7B93] dark:to-[#00A8CC] text-white rounded-xl transition-all duration-200 font-bold text-sm shadow-md shadow-[#3F72AF]/20 dark:shadow-[#00A8CC]/20 hover:scale-[1.01] active:scale-[0.99] flex items-center justify-center gap-2"
            >
              <Plus size={18} />
              <span>{t.dashboard.createNewTunnel}</span>
            </button>
            <button 
              onClick={() => navigate('/nodes?add=true')}
              className="w-full px-5 py-3 bg-[#F9F7F7] dark:bg-[#142850] text-[#112D4E] dark:text-[#DBE2EF] rounded-xl hover:bg-[#DBE2EF]/60 dark:hover:bg-[#142850]/80 transition-all duration-200 font-semibold text-sm border border-[#DBE2EF] dark:border-[#0C7B93]/30 flex items-center justify-center gap-2"
            >
              <Server size={18} className="text-[#3F72AF] dark:text-[#00A8CC]" />
              <span>{t.dashboard.addNode}</span>
            </button>
            <button 
              onClick={() => navigate('/servers?add=true')}
              className="w-full px-5 py-3 bg-[#F9F7F7] dark:bg-[#142850] text-[#112D4E] dark:text-[#DBE2EF] rounded-xl hover:bg-[#DBE2EF]/60 dark:hover:bg-[#142850]/80 transition-all duration-200 font-semibold text-sm border border-[#DBE2EF] dark:border-[#0C7B93]/30 flex items-center justify-center gap-2"
            >
              <Network size={18} className="text-[#3F72AF] dark:text-[#00A8CC]" />
              <span>{t.dashboard.addServer}</span>
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
  color: 'blue' | 'green' | 'cyan' | 'teal'
}

const StatCard = ({ title, value, subtitle, icon: Icon, color, ...props }: StatCardProps & HTMLMotionProps<"div">) => {
  const colorClasses = {
    blue: {
      icon: 'bg-[#3F72AF]/10 text-[#3F72AF] dark:bg-[#00A8CC]/20 dark:text-[#00A8CC]',
      accent: 'bg-[#3F72AF] dark:bg-[#00A8CC]'
    },
    green: {
      icon: 'bg-emerald-500/10 text-emerald-600 dark:bg-emerald-500/20 dark:text-emerald-300',
      accent: 'bg-emerald-500'
    },
    cyan: {
      icon: 'bg-[#0C7B93]/15 text-[#0C7B93] dark:bg-[#00A8CC]/20 dark:text-[#00A8CC]',
      accent: 'bg-[#0C7B93] dark:bg-[#00A8CC]'
    },
    teal: {
      icon: 'bg-[#112D4E]/10 text-[#112D4E] dark:bg-[#0C7B93]/30 dark:text-[#DBE2EF]',
      accent: 'bg-[#112D4E] dark:bg-[#0C7B93]'
    },
  }

  const colors = colorClasses[color]

  return (
    <motion.div 
      className="relative bg-white dark:bg-[#27496D] rounded-2xl shadow-sm border border-[#DBE2EF] dark:border-[#142850] p-5 transition-all duration-300 hover:shadow-lg dark:hover:shadow-black/30 overflow-hidden"
      whileHover={{ scale: 1.02 }}
      whileTap={{ scale: 0.98 }}
      {...props}
    >
      <div className="flex items-start justify-between mb-3">
        <div className={`p-3 rounded-xl ${colors.icon} transition-transform hover:scale-105 shadow-sm`}>
          <Icon className="w-5 h-5" />
        </div>
      </div>
      <h3 className="text-xs font-bold uppercase tracking-wider text-[#112D4E]/70 dark:text-[#DBE2EF]/70 mb-1">{title}</h3>
      <p className="text-2xl font-black text-[#112D4E] dark:text-[#F9F7F7] mb-1 tracking-tight">{value}</p>
      <p className="text-xs font-medium text-[#112D4E]/60 dark:text-[#DBE2EF]/80">{subtitle}</p>
      <div className={`absolute bottom-0 left-0 right-0 h-1 ${colors.accent} rounded-b-2xl opacity-90`}></div>
    </motion.div>
  )
}

interface ProgressBarProps {
  label: string
  value: number
  color: 'cyan' | 'teal'
}

const ProgressBar = ({ label, value, color }: ProgressBarProps) => {
  const colorClasses = {
    cyan: 'from-[#3F72AF] to-[#112D4E] dark:from-[#0C7B93] dark:to-[#00A8CC]',
    teal: 'from-[#3F72AF] to-[#00A8CC] dark:from-[#00A8CC] dark:to-[#F9F7F7]',
  }

  const gradient = colorClasses[color]
  const percentage = Math.min(value, 100)

  return (
    <div>
      <div className="flex justify-between items-center text-xs mb-2">
        <span className="font-bold text-[#112D4E] dark:text-[#F9F7F7]">{label}</span>
        <span className="font-mono font-bold text-[#3F72AF] dark:text-[#00A8CC]">{value.toFixed(1)}%</span>
      </div>
      <div className="w-full bg-[#DBE2EF] dark:bg-[#142850] rounded-full h-2.5 overflow-hidden p-0.5 shadow-inner">
        <div
          className={`h-full rounded-full bg-gradient-to-r ${gradient} transition-all duration-500 ease-out shadow-sm`}
          style={{ width: `${percentage}%` }}
        />
      </div>
    </div>
  )
}

export default Dashboard
