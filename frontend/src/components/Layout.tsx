import { ReactNode, useState, useEffect } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { LayoutDashboard, Network, FileText, Activity, Moon, Sun, Github, Menu, X, LogOut, Settings, Heart, Globe, Languages } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import { useLanguage } from '../contexts/LanguageContext'
import { useTheme } from '../contexts/ThemeContext'
import SmiteLogoDark from '../assets/SmiteD.png'
import SmiteLogoLight from '../assets/SmiteL.png'

interface LayoutProps {
  children: ReactNode
}

const Layout = ({ children }: LayoutProps) => {
  const location = useLocation()
  const navigate = useNavigate()
  const { logout, username } = useAuth()
  const { language, setLanguage, dir, t } = useLanguage()
  const { darkMode, toggleDarkMode } = useTheme()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [version, setVersion] = useState('v0.1.0')

  useEffect(() => {
    setSidebarOpen(false)
  }, [location.pathname])

  useEffect(() => {
    fetch('/api/status/version')
      .then(res => res.json())
      .then(data => {
        if (data.version) {
          setVersion(`v${data.version}`)
        }
      })
      .catch(() => {
        setVersion('v0.1.0')
      })
  }, [])
  
  const navItems = [
    { path: '/dashboard', label: t.navigation.dashboard, icon: LayoutDashboard },
    { path: '/nodes', label: t.navigation.nodes, icon: Network },
    { path: '/servers', label: t.navigation.servers, icon: Globe },
    { path: '/tunnels', label: t.navigation.tunnels, icon: Activity },
    { path: '/core-health', label: t.navigation.coreHealth, icon: Heart },
    { path: '/logs', label: t.navigation.logs, icon: FileText },
    { path: '/settings', label: t.navigation.settings, icon: Settings },
  ]

  return (
    <div className="min-h-screen bg-[#F9F7F7] dark:bg-[#142850] text-[#112D4E] dark:text-[#F9F7F7]" dir={language === 'fa' ? 'rtl' : 'ltr'}>
      <div className="flex h-screen overflow-hidden">
        {/* Mobile Sidebar Overlay */}
        {sidebarOpen && (
          <div
            className="fixed inset-0 bg-[#112D4E]/60 dark:bg-black/70 backdrop-blur-sm z-40 lg:hidden transition-opacity"
            onClick={() => setSidebarOpen(false)}
          />
        )}

        {/* Sidebar */}
        <aside
          dir={language === 'fa' ? 'rtl' : 'ltr'}
          className={`fixed lg:static inset-y-0 ${language === 'fa' ? 'right-0 border-l' : 'left-0 border-r'} w-64 bg-white dark:bg-[#27496D] border-[#DBE2EF] dark:border-[#142850] flex flex-col z-50 transform transition-transform duration-300 ease-out shadow-lg lg:shadow-none ${
            sidebarOpen ? 'translate-x-0' : (language === 'fa' ? 'translate-x-full lg:translate-x-0' : '-translate-x-full lg:translate-x-0')
          }`}
        >
          {/* Sidebar Header */}
          <div className="p-6 border-b border-[#DBE2EF] dark:border-[#142850]/70">
            <div className="flex items-center justify-end lg:hidden mb-2">
              <button
                onClick={() => setSidebarOpen(false)}
                className="p-2 rounded-xl hover:bg-[#DBE2EF]/60 dark:hover:bg-[#142850]/60 text-[#112D4E] dark:text-[#DBE2EF] min-w-[40px] min-h-[40px] flex items-center justify-center transition-colors"
                aria-label="Close sidebar"
              >
                <X size={20} />
              </button>
            </div>
            <div className="flex flex-col items-center gap-3">
              <div className="relative group">
                <div className="absolute inset-0 bg-[#3F72AF]/20 dark:bg-[#00A8CC]/25 rounded-full blur-xl transition-all group-hover:blur-2xl"></div>
                <img 
                  src={darkMode ? SmiteLogoDark : SmiteLogoLight} 
                  alt="Smite Logo" 
                  className="relative h-20 w-20 drop-shadow-md transition-transform duration-300 group-hover:scale-105"
                />
              </div>
              <div className="text-center">
                <h1 className="text-2xl font-black tracking-tight bg-gradient-to-r from-[#112D4E] via-[#3F72AF] to-[#3F72AF] dark:from-[#00A8CC] dark:via-[#F9F7F7] dark:to-[#00A8CC] bg-clip-text text-transparent">
                  Smite
                </h1>
                <p className="text-xs font-semibold text-[#3F72AF] dark:text-[#00A8CC]/90 tracking-wide mt-0.5 uppercase">Control Panel</p>
                {username && (
                  <p className="text-xs font-medium text-[#112D4E] dark:text-[#F9F7F7] mt-2 px-3 py-1 bg-[#DBE2EF]/70 dark:bg-[#142850]/80 rounded-full border border-[#DBE2EF] dark:border-[#0C7B93]/30 inline-block shadow-sm">
                    {username}
                  </p>
                )}
              </div>
            </div>
          </div>
          
          {/* Navigation */}
          <nav className="flex-1 p-3.5 space-y-1.5 overflow-y-auto">
            {navItems.map((item) => {
              const Icon = item.icon
              const isActive = location.pathname === item.path
              return (
                <Link
                  key={item.path}
                  to={item.path}
                  className={`flex items-center gap-3.5 px-4 py-2.5 rounded-xl transition-all duration-200 ${
                    isActive
                      ? 'bg-[#3F72AF] text-white shadow-md shadow-[#3F72AF]/25 dark:bg-gradient-to-r dark:from-[#0C7B93] dark:to-[#00A8CC] dark:text-white dark:shadow-[#00A8CC]/20 font-semibold scale-[1.01]'
                      : 'text-[#112D4E]/80 dark:text-[#DBE2EF]/80 hover:bg-[#DBE2EF]/50 dark:hover:bg-[#142850]/50 hover:text-[#112D4E] dark:hover:text-white font-medium'
                  }`}
                >
                  <Icon size={19} className={isActive ? 'text-white' : 'text-[#3F72AF] dark:text-[#00A8CC]'} />
                  <span className="text-sm">{item.label}</span>
                </Link>
              )
            })}
          </nav>
          
          {/* Sidebar Footer */}
          <div className="p-4 border-t border-[#DBE2EF] dark:border-[#142850]/70 space-y-2.5 bg-[#F9F7F7]/60 dark:bg-[#142850]/30">
            <div className="space-y-2">
              <div className="flex items-center justify-between px-2 py-1">
                <button
                  onClick={toggleDarkMode}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-lg hover:bg-[#DBE2EF]/60 dark:hover:bg-[#142850]/60 text-[#112D4E] dark:text-[#DBE2EF] transition-colors"
                >
                  {darkMode ? <Sun size={17} className="text-[#00A8CC]" /> : <Moon size={17} className="text-[#3F72AF]" />}
                  <span className="text-xs font-semibold">{darkMode ? t.navigation.light : t.navigation.dark}</span>
                </button>
                <button
                  onClick={() => setLanguage(language === 'en' ? 'fa' : 'en')}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg hover:bg-[#DBE2EF]/60 dark:hover:bg-[#142850]/60 text-[#112D4E] dark:text-[#DBE2EF] transition-colors font-bold text-xs"
                  title={language === 'en' ? 'Switch to Farsi' : 'Switch to English'}
                >
                  <Languages size={17} className="text-[#3F72AF] dark:text-[#00A8CC]" />
                  <span>{language === 'en' ? 'FA' : 'EN'}</span>
                </button>
              </div>
              <div className="px-2">
                <button
                  onClick={() => {
                    logout()
                    navigate('/login')
                  }}
                  className="w-full flex items-center justify-center gap-2 py-2 px-3 rounded-lg text-red-600 dark:text-red-400 hover:bg-red-500/10 transition-colors text-xs font-semibold"
                >
                  <LogOut size={16} />
                  <span>{t.navigation.logout}</span>
                </button>
              </div>
            </div>
            <div className="flex flex-col items-center gap-1.5 text-[11px] text-[#112D4E]/60 dark:text-[#DBE2EF]/60 pt-2 border-t border-[#DBE2EF] dark:border-[#142850]/50">
              <div className="flex items-center gap-1 flex-wrap justify-center font-medium">
                <span>Made with</span>
                <span className="text-red-500">❤️</span>
                <span>by</span>
                <a 
                  href="https://github.com/zZedix" 
                  target="_blank" 
                  rel="noopener noreferrer"
                  className="text-[#3F72AF] dark:text-[#00A8CC] font-semibold hover:underline"
                >
                  zZedix
                </a>
              </div>
              <div className="flex items-center gap-2">
                <span className="font-mono">{version}</span>
                <a 
                  href="https://github.com/MasterALiReza/Smite" 
                  target="_blank" 
                  rel="noopener noreferrer"
                  className="text-[#112D4E]/60 dark:text-[#DBE2EF]/60 hover:text-[#3F72AF] dark:hover:text-[#00A8CC] transition-colors"
                  title="GitHub Repository"
                >
                  <Github size={13} />
                </a>
              </div>
            </div>
          </div>
        </aside>

        {/* Main Content */}
        <main className="flex-1 overflow-auto bg-[#F9F7F7] dark:bg-[#142850]" dir={language === 'fa' ? 'rtl' : 'ltr'}>
          {/* Mobile Header */}
          <div className="lg:hidden sticky top-0 z-30 bg-white/90 dark:bg-[#27496D]/90 backdrop-blur-md border-b border-[#DBE2EF] dark:border-[#142850] px-4 py-2.5 flex items-center justify-between shadow-sm">
            <button
              onClick={() => setSidebarOpen(true)}
              className="p-2 rounded-xl hover:bg-[#DBE2EF]/60 dark:hover:bg-[#142850]/60 text-[#112D4E] dark:text-[#DBE2EF] min-w-[44px] min-h-[44px] flex items-center justify-center"
              aria-label="Open navigation menu"
            >
              <Menu size={22} />
            </button>
            <h1 className="text-base font-black tracking-tight bg-gradient-to-r from-[#112D4E] to-[#3F72AF] dark:from-[#00A8CC] dark:to-white bg-clip-text text-transparent">Smite</h1>
            <div className="flex items-center gap-1">
              <button
                onClick={toggleDarkMode}
                className="p-2 rounded-xl hover:bg-[#DBE2EF]/60 dark:hover:bg-[#142850]/60 text-[#112D4E] dark:text-[#DBE2EF] min-w-[44px] min-h-[44px] flex items-center justify-center"
                aria-label="Toggle dark mode"
              >
                {darkMode ? <Sun size={18} className="text-[#00A8CC]" /> : <Moon size={18} className="text-[#3F72AF]" />}
              </button>
              <button
                onClick={() => setLanguage(language === 'en' ? 'fa' : 'en')}
                className="px-2 py-1 rounded-xl hover:bg-[#DBE2EF]/60 dark:hover:bg-[#142850]/60 text-xs font-bold text-[#112D4E] dark:text-[#DBE2EF] min-h-[44px] min-w-[44px] flex items-center justify-center"
                aria-label="Toggle language"
              >
                {language === 'en' ? 'FA' : 'EN'}
              </button>
            </div>
          </div>
          
          <div className="p-4 sm:p-6 lg:p-8 max-w-7xl mx-auto">
            {children}
          </div>
        </main>
      </div>
    </div>
  )
}

export default Layout
