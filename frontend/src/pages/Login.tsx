import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { LogIn, Loader2, Shield, Languages, Sun, Moon } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import { useLanguage } from '../contexts/LanguageContext'
import { useTheme } from '../contexts/ThemeContext'
import api from '../api/client'
import SmiteLogoDark from '../assets/SmiteD.png'
import SmiteLogoLight from '../assets/SmiteL.png'

const Login = () => {
  const [username, setUsername] = useState('')
  const [version, setVersion] = useState('v0.1.0')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const { darkMode, toggleDarkMode } = useTheme()
  const navigate = useNavigate()
  const { login, isAuthenticated } = useAuth()
  const { t, language, setLanguage, dir } = useLanguage()

  useEffect(() => {
    if (isAuthenticated) {
      navigate('/dashboard')
    }
  }, [isAuthenticated, navigate])

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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      const response = await api.post('/auth/login', {
        username,
        password,
      })

      login(response.data.access_token, response.data.username)
      navigate('/dashboard')
    } catch (err: any) {
      setError(err.response?.data?.detail || t.login.checkCredentials)
    } finally {
      setLoading(false)
    }
  }

  const isRTL = language === 'fa'

  return (
    <div 
      className="min-h-[100dvh] relative overflow-hidden bg-slate-50 dark:bg-[#070a13] flex items-center justify-center p-4 sm:p-6 transition-colors duration-300 font-sans" 
      dir={isRTL ? 'rtl' : 'ltr'}
    >
      {/* Background Decorative Ambient Glows */}
      <div className="absolute top-1/4 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[500px] bg-gradient-to-tr from-blue-600/15 via-sky-500/10 to-indigo-600/15 rounded-full blur-[120px] pointer-events-none" />
      <div className="absolute bottom-10 right-10 w-72 h-72 bg-purple-500/10 rounded-full blur-[90px] pointer-events-none" />

      <div className="w-full max-w-md my-auto py-6 relative z-10">
        {/* Brand Header */}
        <div className="text-center mb-6 sm:mb-8">
          <div className="flex justify-center mb-4 sm:mb-5">
            <div className="relative group">
              <div className="absolute inset-0 bg-blue-500/25 dark:bg-sky-400/20 rounded-full blur-2xl transition-all duration-300 group-hover:scale-110"></div>
              <img
                src={darkMode ? SmiteLogoDark : SmiteLogoLight}
                alt="Smite Logo"
                className="relative h-20 w-20 sm:h-24 sm:w-24 object-contain drop-shadow-md transition-transform duration-300 group-hover:scale-105"
              />
            </div>
          </div>
          <div className="flex items-center justify-center gap-2 mb-1">
            <h1 className="text-2xl sm:text-3xl font-extrabold tracking-tight bg-gradient-to-r from-blue-600 via-sky-600 to-indigo-600 dark:from-sky-400 dark:via-blue-300 dark:to-indigo-300 bg-clip-text text-transparent">
              {t.login.title}
            </h1>
            <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-blue-100 dark:bg-sky-950/70 text-blue-700 dark:text-sky-300 border border-blue-200 dark:border-sky-800 font-bold">
              {version}
            </span>
          </div>
          <p className="text-slate-500 dark:text-slate-400 text-xs sm:text-sm font-medium">{t.login.subtitle}</p>
        </div>

        {/* Double-Bezel Login Card */}
        <div className="relative p-1.5 sm:p-2 rounded-[2rem] bg-gradient-to-b from-slate-200/80 via-slate-100/50 to-slate-200/80 dark:from-white/10 dark:via-white/[0.03] dark:to-white/[0.08] shadow-2xl backdrop-blur-2xl">
          <div className="bg-white/95 dark:bg-[#0c1220]/95 rounded-[calc(2rem-0.375rem)] p-6 sm:p-8 border border-slate-200/70 dark:border-white/[0.06] shadow-inner-bevel">
            {/* Card Header with System Badge & Controls */}
            <div className="flex items-center justify-between mb-6 pb-4 border-b border-slate-100 dark:border-white/[0.06]">
              <div className="flex items-center gap-2.5">
                <div className="p-2.5 bg-blue-500/10 dark:bg-sky-500/15 rounded-xl text-blue-600 dark:text-sky-400 border border-blue-200/50 dark:border-sky-500/20 shadow-glow-sm">
                  <Shield className="w-4 h-4" />
                </div>
                <h2 className="text-base sm:text-lg font-bold text-slate-900 dark:text-white tracking-tight">
                  {t.login.signIn}
                </h2>
              </div>
              
              <div className="flex items-center gap-1.5">
                <button
                  onClick={() => setLanguage(language === 'fa' ? 'en' : 'fa')}
                  className="px-2.5 py-1.5 rounded-xl bg-slate-100 dark:bg-white/[0.05] hover:bg-slate-200/80 dark:hover:bg-white/[0.1] text-slate-700 dark:text-slate-200 text-xs font-semibold flex items-center gap-1.5 transition-colors border border-slate-200/60 dark:border-white/[0.08] min-h-[36px] active:scale-95"
                  title="Toggle Language"
                >
                  <Languages size={14} className="text-sky-500" />
                  <span>{language === 'fa' ? 'English' : 'فارسی'}</span>
                </button>
                <button
                  onClick={toggleDarkMode}
                  className="p-2 rounded-xl bg-slate-100 dark:bg-white/[0.05] hover:bg-slate-200/80 dark:hover:bg-white/[0.1] text-slate-700 dark:text-slate-200 transition-colors border border-slate-200/60 dark:border-white/[0.08] min-h-[36px] min-w-[36px] flex items-center justify-center active:scale-95"
                  title={darkMode ? 'Light mode' : 'Dark mode'}
                  aria-label="Toggle dark mode"
                >
                  {darkMode ? <Sun size={15} className="text-amber-400" /> : <Moon size={15} className="text-indigo-500" />}
                </button>
              </div>
            </div>

            {error && (
              <div className="mb-5 p-3.5 bg-rose-50/90 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-900/60 rounded-2xl animate-shake">
                <p className="text-xs text-rose-600 dark:text-rose-400 font-medium">{error}</p>
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-4 sm:space-y-5">
              <div>
                <label
                  htmlFor="username"
                  className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5 tracking-tight"
                >
                  {t.login.username}
                </label>
                <input
                  id="username"
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                  className="w-full px-4 py-2.5 border border-slate-200 dark:border-white/[0.1] rounded-xl bg-slate-50/50 dark:bg-[#070b14]/70 text-slate-900 dark:text-white placeholder-slate-400 dark:placeholder-slate-500 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500 transition-all min-h-[44px]"
                  placeholder={t.login.usernamePlaceholder}
                  autoComplete="username"
                />
              </div>

              <div>
                <label
                  htmlFor="password"
                  className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5 tracking-tight"
                >
                  {t.login.password}
                </label>
                <input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  className="w-full px-4 py-2.5 border border-slate-200 dark:border-white/[0.1] rounded-xl bg-slate-50/50 dark:bg-[#070b14]/70 text-slate-900 dark:text-white placeholder-slate-400 dark:placeholder-slate-500 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500 transition-all min-h-[44px]"
                  placeholder={t.login.passwordPlaceholder}
                  autoComplete="current-password"
                />
              </div>

              <button
                type="submit"
                disabled={loading}
                className="w-full group mt-3 px-5 py-3 bg-gradient-to-r from-blue-600 via-sky-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white rounded-xl focus:outline-none focus:ring-2 focus:ring-sky-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed transition-all font-bold shadow-lg shadow-blue-600/25 flex items-center justify-center gap-2.5 text-sm min-h-[46px] active:scale-[0.98] cursor-pointer"
              >
                {loading ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>{t.login.signingIn}</span>
                  </>
                ) : (
                  <>
                    <span>{t.login.signIn}</span>
                    <div className="w-6 h-6 rounded-full bg-white/20 flex items-center justify-center transition-transform group-hover:scale-110">
                      <LogIn className="w-3.5 h-3.5 text-white" />
                    </div>
                  </>
                )}
              </button>
            </form>
          </div>
        </div>

        {/* System Credentials & Credit Footer */}
        <div className="mt-6 text-center text-xs text-slate-500 dark:text-slate-400 font-mono">
          <p>
            Smite Panel •{' '}
            <a
              href="https://github.com/MasterALiReza/Smite"
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 dark:text-sky-400 hover:underline font-semibold"
            >
              Open Source Tunneling
            </a>
          </p>
        </div>
      </div>
    </div>
  )
}

export default Login
