import { ReactNode, useState, useEffect } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { LayoutDashboard, Network, FileText, Activity, Moon, Sun, Github, Menu, X, LogOut, Settings, Heart, Globe, Languages, MoreHorizontal } from 'lucide-react'
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

  const bottomNavItems = [
    { path: '/dashboard', label: t.navigation.dashboard, icon: LayoutDashboard },
    { path: '/nodes', label: language === 'fa' ? 'ایران' : 'Iran', icon: Network },
    { path: '/servers', label: language === 'fa' ? 'خارج' : 'Foreign', icon: Globe },
    { path: '/tunnels', label: language === 'fa' ? 'تونل' : 'Tunnels', icon: Activity },
  ]

  return (
    <div className="min-h-screen h-[100dvh] bg-gray-50 dark:bg-gray-900 overflow-hidden flex flex-col" dir={language === 'fa' ? 'rtl' : 'ltr'}>
      <div className="flex flex-1 h-full overflow-hidden relative">
        {/* Mobile Sidebar Overlay */}
        {sidebarOpen && (
          <div
            className="fixed inset-0 bg-black/60 backdrop-blur-xs z-40 lg:hidden transition-opacity duration-300"
            onClick={() => setSidebarOpen(false)}
            aria-hidden="true"
          />
        )}

        {/* Sidebar */}
        <aside
          dir={language === 'fa' ? 'rtl' : 'ltr'}
          className={`fixed lg:static inset-y-0 ${language === 'fa' ? 'right-0 border-l' : 'left-0 border-r'} w-72 lg:w-64 bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 flex flex-col z-50 transform transition-transform duration-300 ease-in-out h-full shadow-2xl lg:shadow-none ${
            sidebarOpen ? 'translate-x-0' : (language === 'fa' ? 'translate-x-full lg:translate-x-0' : '-translate-x-full lg:translate-x-0')
          }`}
        >
          {/* Sidebar Header */}
          <div className="p-5 lg:p-6 border-b border-gray-200 dark:border-gray-700 shrink-0">
            <div className="flex items-center justify-between lg:hidden mb-2">
              <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
                {language === 'fa' ? 'منوی اصلی' : 'Navigation Menu'}
              </span>
              <button
                onClick={() => setSidebarOpen(false)}
                className="p-2 rounded-xl hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300 min-w-[44px] min-h-[44px] flex items-center justify-center transition-colors"
                aria-label="Close sidebar"
              >
                <X size={22} />
              </button>
            </div>
            <div className="flex flex-col items-center gap-3">
              <div className="relative">
                <div className="absolute inset-0 bg-blue-500/20 dark:bg-blue-400/20 rounded-full blur-xl"></div>
                <img 
                  src={darkMode ? SmiteLogoDark : SmiteLogoLight} 
                  alt="Smite Logo" 
                  className="relative h-20 w-20 lg:h-24 lg:w-24 object-contain"
                />
              </div>
              <div className="text-center">
                <h1 className="text-xl font-bold bg-gradient-to-r from-blue-600 to-indigo-600 dark:from-blue-400 dark:to-indigo-400 bg-clip-text text-transparent">Smite</h1>
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Control Panel</p>
                {username && (
                  <p className="text-xs font-mono text-gray-500 dark:text-gray-400 mt-1.5 px-2.5 py-0.5 bg-gray-100 dark:bg-gray-700/80 rounded-full border border-gray-200/50 dark:border-gray-600/50 inline-block">{username}</p>
                )}
              </div>
            </div>
          </div>
          
          {/* Navigation Items */}
          <nav className="flex-1 p-3.5 lg:p-4 space-y-1.5 overflow-y-auto">
            {navItems.map((item) => {
              const Icon = item.icon
              const isActive = location.pathname === item.path
              return (
                <Link
                  key={item.path}
                  to={item.path}
                  className={`flex items-center gap-3 px-3.5 py-3 rounded-xl transition-all min-h-[44px] ${
                    isActive
                      ? 'bg-gradient-to-r from-blue-50 to-indigo-50 dark:from-blue-900/30 dark:to-indigo-900/30 text-blue-600 dark:text-blue-400 shadow-sm font-semibold border border-blue-100/60 dark:border-blue-800/40'
                      : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100/80 dark:hover:bg-gray-700/50 font-medium'
                  }`}
                >
                  <Icon size={20} className={`shrink-0 ${isActive ? 'text-blue-600 dark:text-blue-400' : 'text-gray-500 dark:text-gray-400'}`} />
                  <span className="text-sm">{item.label}</span>
                </Link>
              )
            })}
          </nav>
          
          {/* Sidebar Footer */}
          <div className="p-4 border-t border-gray-200 dark:border-gray-700 space-y-2 shrink-0 bg-gray-50/50 dark:bg-gray-800/50">
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <button
                  onClick={toggleDarkMode}
                  className="flex-1 flex items-center justify-center gap-2 py-2 px-3 rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-200/60 dark:hover:bg-gray-700 transition-colors min-h-[40px] text-xs font-semibold border border-gray-200 dark:border-gray-700"
                >
                  {darkMode ? <Sun size={16} className="text-amber-400" /> : <Moon size={16} className="text-indigo-500" />}
                  <span>{darkMode ? t.navigation.light : t.navigation.dark}</span>
                </button>
                <button
                  onClick={() => setLanguage(language === 'en' ? 'fa' : 'en')}
                  className="flex-1 flex items-center justify-center gap-2 py-2 px-3 rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-200/60 dark:hover:bg-gray-700 transition-colors min-h-[40px] text-xs font-semibold border border-gray-200 dark:border-gray-700"
                  title={language === 'en' ? 'Switch to Farsi' : 'Switch to English'}
                >
                  <Languages size={16} className="text-blue-500" />
                  <span>{language === 'en' ? 'فارسی' : 'English'}</span>
                </button>
              </div>
              <div>
                <button
                  onClick={() => {
                    logout()
                    navigate('/login')
                  }}
                  className="w-full flex items-center justify-center gap-2 py-2 px-3 rounded-lg text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/30 transition-colors min-h-[40px] text-xs font-semibold border border-rose-200/50 dark:border-rose-900/30"
                >
                  <LogOut size={16} />
                  <span>{t.navigation.logout}</span>
                </button>
              </div>
            </div>
            <div className="flex flex-col items-center gap-1.5 text-[11px] text-gray-500 dark:text-gray-400 pt-2 border-t border-gray-200/80 dark:border-gray-700/80">
              <div className="flex items-center gap-1 flex-wrap justify-center">
                <span>Made with</span>
                <span className="text-red-500">❤️</span>
                <span>by</span>
                <a 
                  href="https://github.com/zZedix" 
                  target="_blank" 
                  rel="noopener noreferrer"
                  className="text-blue-600 dark:text-blue-400 hover:underline font-medium"
                >
                  zZedix
                </a>
              </div>
              <div className="flex items-center gap-2 font-mono">
                <span>{version}</span>
                <a 
                  href="https://github.com/MasterALiReza/Smite" 
                  target="_blank" 
                  rel="noopener noreferrer"
                  className="hover:text-gray-700 dark:hover:text-gray-300 transition-colors"
                  title="GitHub Repository"
                >
                  <Github size={13} />
                </a>
              </div>
            </div>
          </div>
        </aside>

        {/* Main Content Area */}
        <main className="flex-1 flex flex-col h-full overflow-hidden bg-gray-50 dark:bg-gray-900" dir={language === 'fa' ? 'rtl' : 'ltr'}>
          {/* Mobile Top Header */}
          <header className="lg:hidden sticky top-0 z-30 bg-white/90 dark:bg-gray-800/90 backdrop-blur-md border-b border-gray-200 dark:border-gray-700 px-4 py-2.5 flex items-center justify-between shadow-xs shrink-0">
            <div className="flex items-center gap-2.5">
              <button
                onClick={() => setSidebarOpen(true)}
                className="p-2 rounded-xl hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-200 min-w-[44px] min-h-[44px] flex items-center justify-center transition-colors active:scale-95"
                aria-label="Open navigation menu"
              >
                <Menu size={22} />
              </button>
              <div className="flex items-center gap-2">
                <img 
                  src={darkMode ? SmiteLogoDark : SmiteLogoLight} 
                  alt="Smite" 
                  className="h-7 w-7 object-contain"
                />
                <h1 className="text-base font-bold bg-gradient-to-r from-blue-600 to-indigo-600 dark:from-blue-400 dark:to-indigo-400 bg-clip-text text-transparent">Smite</h1>
              </div>
            </div>
            
            <div className="flex items-center gap-1.5">
              <button
                onClick={toggleDarkMode}
                className="p-2 rounded-xl hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300 min-w-[44px] min-h-[44px] flex items-center justify-center transition-colors active:scale-95"
                aria-label="Toggle dark mode"
              >
                {darkMode ? <Sun size={18} className="text-amber-400" /> : <Moon size={18} className="text-indigo-500" />}
              </button>
              <button
                onClick={() => setLanguage(language === 'en' ? 'fa' : 'en')}
                className="px-2.5 py-1 rounded-xl hover:bg-gray-100 dark:hover:bg-gray-700 text-xs font-bold text-gray-700 dark:text-gray-200 min-h-[44px] min-w-[44px] flex items-center justify-center border border-gray-200/80 dark:border-gray-700/80 transition-colors active:scale-95"
                aria-label="Toggle language"
              >
                {language === 'en' ? 'FA' : 'EN'}
              </button>
            </div>
          </header>
          
          {/* Scrollable Page Content */}
          <div className="flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8 pb-24 lg:pb-8">
            {children}
          </div>

          {/* Mobile Bottom Navigation Bar */}
          <nav 
            className="lg:hidden fixed bottom-0 left-0 right-0 z-30 bg-white/95 dark:bg-gray-800/95 backdrop-blur-md border-t border-gray-200/80 dark:border-gray-700/80 px-2 py-1.5 shadow-lg flex items-center justify-around"
            style={{ paddingBottom: 'calc(env(safe-area-inset-bottom, 0px) + 0.375rem)' }}
            aria-label="Mobile Navigation"
          >
            {bottomNavItems.map((item) => {
              const Icon = item.icon
              const isActive = location.pathname === item.path
              return (
                <Link
                  key={item.path}
                  to={item.path}
                  className={`flex flex-col items-center justify-center py-1 px-3 rounded-xl transition-all min-w-[60px] min-h-[48px] ${
                    isActive
                      ? 'text-blue-600 dark:text-blue-400 font-bold'
                      : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'
                  }`}
                >
                  <div className={`p-1 rounded-lg transition-transform ${isActive ? 'bg-blue-50 dark:bg-blue-900/40 scale-105' : ''}`}>
                    <Icon size={20} className={isActive ? 'text-blue-600 dark:text-blue-400' : ''} />
                  </div>
                  <span className="text-[10px] mt-0.5 tracking-tight">{item.label}</span>
                </Link>
              )
            })}
            <button
              onClick={() => setSidebarOpen(true)}
              className="flex flex-col items-center justify-center py-1 px-3 rounded-xl text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 min-w-[60px] min-h-[48px] transition-all"
              aria-label="More navigation options"
            >
              <div className="p-1 rounded-lg">
                <MoreHorizontal size={20} />
              </div>
              <span className="text-[10px] mt-0.5 tracking-tight">{language === 'fa' ? 'بیشتر' : 'More'}</span>
            </button>
          </nav>
        </main>
      </div>
    </div>
  )
}

export default Layout
