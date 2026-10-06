import { ReactNode, useState, useEffect } from 'react'
import { Link, useLocation, useNavigate, Outlet } from 'react-router-dom'
import { LayoutDashboard, Network, FileText, Activity, Moon, Sun, Github, Menu, X, LogOut, Settings, Heart, Globe, Languages, MoreHorizontal } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import { useLanguage } from '../contexts/LanguageContext'
import { useTheme } from '../contexts/ThemeContext'
import SmiteLogoDark from '../assets/SmiteD.png'
import SmiteLogoLight from '../assets/SmiteL.png'

interface LayoutProps {
  children?: ReactNode
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

  const isRTL = language === 'fa'

  return (
    <div 
      className="min-h-screen h-[100dvh] bg-slate-50 dark:bg-[#070a13] text-slate-900 dark:text-slate-100 overflow-hidden flex flex-col font-sans transition-colors duration-300"
      dir={isRTL ? 'rtl' : 'ltr'}
    >
      <div className="flex flex-1 h-full overflow-hidden relative">
        {/* Mobile Sidebar Overlay with backdrop blur */}
        {sidebarOpen && (
          <div
            className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 lg:hidden transition-opacity duration-300"
            onClick={() => setSidebarOpen(false)}
            aria-hidden="true"
          />
        )}

        {/* Sidebar */}
        <aside
          dir={isRTL ? 'rtl' : 'ltr'}
          className={`fixed lg:static inset-y-0 ${isRTL ? 'right-0 border-l' : 'left-0 border-r'} w-72 lg:w-64 bg-white/90 dark:bg-[#0a0e1a]/95 backdrop-blur-2xl border-slate-200/80 dark:border-white/[0.07] flex flex-col z-50 transform transition-transform duration-300 ease-spring h-full shadow-2xl lg:shadow-none ${
            sidebarOpen ? 'translate-x-0' : (isRTL ? 'translate-x-full lg:translate-x-0' : '-translate-x-full lg:translate-x-0')
          }`}
        >
          {/* Sidebar Header with Brand Identity */}
          <div className="p-5 lg:p-6 border-b border-slate-200/80 dark:border-white/[0.06] shrink-0">
            <div className="flex items-center justify-between lg:hidden mb-2">
              <span className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-widest font-mono">
                {isRTL ? 'منوی ناوبری' : 'NAVIGATION'}
              </span>
              <button
                onClick={() => setSidebarOpen(false)}
                className="p-2 rounded-xl hover:bg-slate-100 dark:hover:bg-white/10 text-slate-500 dark:text-slate-400 min-w-[44px] min-h-[44px] flex items-center justify-center transition-colors"
                aria-label="Close sidebar"
              >
                <X size={20} />
              </button>
            </div>
            
            <div className="flex flex-col items-center gap-2.5">
              <div className="relative group cursor-pointer" onClick={() => navigate('/dashboard')}>
                <div className="absolute inset-0 bg-blue-500/25 dark:bg-sky-400/20 rounded-full blur-xl transition-all duration-300 group-hover:scale-110"></div>
                <img 
                  src={darkMode ? SmiteLogoDark : SmiteLogoLight} 
                  alt="Smite Logo" 
                  className="relative h-16 w-16 lg:h-20 lg:w-20 object-contain drop-shadow-sm transition-transform duration-300 group-hover:scale-105"
                />
              </div>
              <div className="text-center">
                <div className="flex items-center justify-center gap-1.5">
                  <h1 className="text-xl font-extrabold tracking-tight bg-gradient-to-r from-blue-600 via-sky-600 to-indigo-600 dark:from-sky-400 dark:via-blue-300 dark:to-indigo-300 bg-clip-text text-transparent">
                    Smite
                  </h1>
                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded-md bg-blue-50 dark:bg-sky-950/60 text-blue-600 dark:text-sky-400 border border-blue-200/60 dark:border-sky-800/50">
                    CORE
                  </span>
                </div>
                <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5 font-medium">Tunneling Control Panel</p>
                {username && (
                  <div className="mt-2 inline-flex items-center gap-1.5 px-2.5 py-1 bg-slate-100/90 dark:bg-white/[0.05] rounded-full border border-slate-200/60 dark:border-white/[0.08]">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
                    <span className="text-[11px] font-mono font-medium text-slate-700 dark:text-slate-300">{username}</span>
                  </div>
                )}
              </div>
            </div>
          </div>
          
          {/* Navigation Links */}
          <nav className="flex-1 p-3.5 lg:p-4 space-y-1.5 overflow-y-auto">
            {navItems.map((item) => {
              const Icon = item.icon
              const isActive = location.pathname === item.path
              return (
                <Link
                  key={item.path}
                  to={item.path}
                  className={`group relative flex items-center gap-3 px-3.5 py-2.5 rounded-xl transition-all duration-200 min-h-[44px] ${
                    isActive
                      ? 'bg-blue-500/10 dark:bg-sky-500/15 text-blue-600 dark:text-sky-400 font-semibold shadow-inner-bevel border border-blue-200/60 dark:border-sky-500/30'
                      : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200 hover:bg-slate-100/80 dark:hover:bg-white/[0.04] font-medium border border-transparent'
                  }`}
                >
                  {/* Active Indicator Bar */}
                  {isActive && (
                    <span className={`absolute ${isRTL ? '-right-1' : '-left-1'} top-2.5 bottom-2.5 w-1 bg-blue-600 dark:bg-sky-400 rounded-full shadow-glow-sm`} />
                  )}
                  <Icon 
                    size={19} 
                    className={`shrink-0 transition-transform duration-200 group-hover:scale-110 ${
                      isActive ? 'text-blue-600 dark:text-sky-400' : 'text-slate-400 dark:text-slate-500'
                    }`} 
                  />
                  <span className="text-sm tracking-tight">{item.label}</span>
                </Link>
              )
            })}
          </nav>
          
          {/* Sidebar Footer Controls */}
          <div className="p-4 border-t border-slate-200/80 dark:border-white/[0.06] space-y-2.5 shrink-0 bg-slate-50/60 dark:bg-[#070a13]/70">
            <div className="grid grid-cols-2 gap-2">
              <button
                onClick={toggleDarkMode}
                className="flex items-center justify-center gap-1.5 py-2 px-3 rounded-xl text-slate-700 dark:text-slate-300 bg-white dark:bg-white/[0.04] hover:bg-slate-100 dark:hover:bg-white/[0.08] transition-all min-h-[40px] text-xs font-semibold border border-slate-200/80 dark:border-white/[0.07] shadow-2xs active:scale-95"
                aria-label="Toggle dark mode"
              >
                {darkMode ? <Sun size={15} className="text-amber-400" /> : <Moon size={15} className="text-indigo-500" />}
                <span>{darkMode ? t.navigation.light : t.navigation.dark}</span>
              </button>
              
              <button
                onClick={() => setLanguage(language === 'en' ? 'fa' : 'en')}
                className="flex items-center justify-center gap-1.5 py-2 px-3 rounded-xl text-slate-700 dark:text-slate-300 bg-white dark:bg-white/[0.04] hover:bg-slate-100 dark:hover:bg-white/[0.08] transition-all min-h-[40px] text-xs font-semibold border border-slate-200/80 dark:border-white/[0.07] shadow-2xs active:scale-95"
                title={language === 'en' ? 'Switch to Farsi' : 'Switch to English'}
                aria-label="Toggle language"
              >
                <Languages size={15} className="text-sky-500" />
                <span>{language === 'en' ? 'فارسی' : 'English'}</span>
              </button>
            </div>

            <button
              onClick={() => {
                logout()
                navigate('/login')
              }}
              className="w-full flex items-center justify-center gap-2 py-2 px-3 rounded-xl text-rose-600 dark:text-rose-400 bg-rose-50/70 dark:bg-rose-950/20 hover:bg-rose-100 dark:hover:bg-rose-950/40 transition-all min-h-[40px] text-xs font-semibold border border-rose-200/60 dark:border-rose-900/40 active:scale-98"
            >
              <LogOut size={15} />
              <span>{t.navigation.logout}</span>
            </button>
            
            <div className="flex flex-col items-center gap-1 text-[11px] text-slate-400 dark:text-slate-500 pt-2 border-t border-slate-200/60 dark:border-white/[0.05]">
              <div className="flex items-center gap-1 flex-wrap justify-center font-mono">
                <span>Smite</span>
                <span className="font-semibold text-slate-600 dark:text-slate-400">{version}</span>
                <span>•</span>
                <a 
                  href="https://github.com/MasterALiReza/Smite" 
                  target="_blank" 
                  rel="noopener noreferrer"
                  className="hover:text-slate-700 dark:hover:text-slate-300 transition-colors inline-flex items-center gap-1"
                  title="GitHub Repository"
                >
                  <Github size={12} />
                </a>
              </div>
            </div>
          </div>
        </aside>

        {/* Main Content Viewport */}
        <main className="flex-1 flex flex-col h-full overflow-hidden bg-slate-50/50 dark:bg-[#070a13]" dir={isRTL ? 'rtl' : 'ltr'}>
          {/* Mobile Top App Bar */}
          <header className="lg:hidden sticky top-0 z-30 bg-white/80 dark:bg-[#070a13]/85 backdrop-blur-xl border-b border-slate-200/80 dark:border-white/[0.06] px-4 py-2.5 flex items-center justify-between shrink-0">
            <div className="flex items-center gap-2.5">
              <button
                onClick={() => setSidebarOpen(true)}
                className="p-2 rounded-xl hover:bg-slate-100 dark:hover:bg-white/10 text-slate-700 dark:text-slate-200 min-w-[44px] min-h-[44px] flex items-center justify-center transition-colors active:scale-95"
                aria-label="Open navigation menu"
              >
                <Menu size={22} />
              </button>
              <div className="flex items-center gap-2 cursor-pointer" onClick={() => navigate('/dashboard')}>
                <img 
                  src={darkMode ? SmiteLogoDark : SmiteLogoLight} 
                  alt="Smite" 
                  className="h-7 w-7 object-contain drop-shadow-xs"
                />
                <h1 className="text-base font-extrabold tracking-tight bg-gradient-to-r from-blue-600 via-sky-600 to-indigo-600 dark:from-sky-400 dark:via-blue-300 dark:to-indigo-300 bg-clip-text text-transparent">
                  Smite
                </h1>
              </div>
            </div>
            
            <div className="flex items-center gap-1.5">
              <button
                onClick={toggleDarkMode}
                className="p-2 rounded-xl hover:bg-slate-100 dark:hover:bg-white/10 text-slate-600 dark:text-slate-300 min-w-[44px] min-h-[44px] flex items-center justify-center transition-colors active:scale-95"
                aria-label="Toggle dark mode"
              >
                {darkMode ? <Sun size={18} className="text-amber-400" /> : <Moon size={18} className="text-indigo-500" />}
              </button>
              <button
                onClick={() => setLanguage(language === 'en' ? 'fa' : 'en')}
                className="px-2.5 py-1 rounded-xl hover:bg-slate-100 dark:hover:bg-white/10 text-xs font-bold text-slate-700 dark:text-slate-200 min-h-[44px] min-w-[44px] flex items-center justify-center border border-slate-200/80 dark:border-white/[0.08] transition-colors active:scale-95"
                aria-label="Toggle language"
              >
                {language === 'en' ? 'FA' : 'EN'}
              </button>
            </div>
          </header>
          
          {/* Scrollable Page Content Container */}
          <div className="flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8 pb-28 lg:pb-8">
            {children || <Outlet />}
          </div>

          {/* Floating Mobile Bottom Navigation Island */}
          <nav 
            className="lg:hidden fixed bottom-3 left-3 right-3 z-30 max-w-md mx-auto bg-white/90 dark:bg-[#0c111f]/95 backdrop-blur-2xl border border-slate-200/90 dark:border-white/[0.12] px-2 py-1.5 rounded-2xl shadow-2xl flex items-center justify-around"
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
                  className={`flex flex-col items-center justify-center py-1 px-3 rounded-xl transition-all min-w-[56px] min-h-[48px] ${
                    isActive
                      ? 'text-blue-600 dark:text-sky-400 font-bold'
                      : 'text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200'
                  }`}
                >
                  <div className={`p-1.5 rounded-xl transition-all duration-200 ${isActive ? 'bg-blue-500/15 dark:bg-sky-500/20 scale-110 shadow-glow-sm' : ''}`}>
                    <Icon size={19} className={isActive ? 'text-blue-600 dark:text-sky-400' : ''} />
                  </div>
                  <span className="text-[10px] mt-0.5 tracking-tight font-medium">{item.label}</span>
                </Link>
              )
            })}
            <button
              onClick={() => setSidebarOpen(true)}
              className="flex flex-col items-center justify-center py-1 px-3 rounded-xl text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 min-w-[56px] min-h-[48px] transition-all"
              aria-label="More navigation options"
            >
              <div className="p-1.5 rounded-xl">
                <MoreHorizontal size={19} />
              </div>
              <span className="text-[10px] mt-0.5 tracking-tight font-medium">{isRTL ? 'بیشتر' : 'More'}</span>
            </button>
          </nav>
        </main>
      </div>
    </div>
  )
}

export default Layout
