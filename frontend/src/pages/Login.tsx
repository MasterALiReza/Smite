import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { LogIn, Loader2, Shield, Languages } from 'lucide-react'
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
  const { darkMode, setDarkMode } = useTheme()
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

  return (
    <div className="min-h-[100dvh] bg-gradient-to-br from-slate-50 via-blue-50/50 to-indigo-50 dark:from-gray-950 dark:via-gray-900 dark:to-gray-950 flex items-center justify-center p-4 sm:p-6" dir={dir}>
      <div className="w-full max-w-md my-auto py-6">
        {/* Logo and Title */}
        <div className="text-center mb-6 sm:mb-8">
          <div className="flex justify-center mb-4 sm:mb-5">
            <div className="relative">
              <div className="absolute inset-0 bg-blue-500/20 dark:bg-blue-400/20 rounded-full blur-2xl"></div>
              <img
                src={darkMode ? SmiteLogoDark : SmiteLogoLight}
                alt="Smite Logo"
                className="relative h-24 w-24 sm:h-32 sm:w-32 object-contain drop-shadow-md"
              />
            </div>
          </div>
          <h1 className="text-3xl sm:text-4xl font-extrabold bg-gradient-to-r from-blue-600 via-indigo-600 to-purple-600 dark:from-blue-400 dark:via-indigo-300 dark:to-purple-400 bg-clip-text text-transparent mb-1.5 tracking-tight">
            {t.login.title}
          </h1>
          <p className="text-gray-500 dark:text-gray-400 text-xs sm:text-sm">{t.login.subtitle}</p>
        </div>

        {/* Login Card */}
        <div className="bg-white/90 dark:bg-gray-800/90 backdrop-blur-md rounded-3xl shadow-xl border border-gray-200/80 dark:border-gray-700/80 p-6 sm:p-8 transition-all">
          <div className="flex items-center justify-between mb-6 pb-4 border-b border-gray-100 dark:border-gray-700/60">
            <div className="flex items-center gap-2.5">
              <div className="p-2 bg-blue-100 dark:bg-blue-900/40 rounded-xl text-blue-600 dark:text-blue-400">
                <Shield className="w-5 h-5" />
              </div>
              <h2 className="text-lg sm:text-xl font-bold text-gray-900 dark:text-white">
                {t.login.signIn}
              </h2>
            </div>
            <div className="flex items-center gap-1.5">
              <button
                onClick={() => setLanguage(language === 'fa' ? 'en' : 'fa')}
                className="p-2 rounded-xl hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300 text-xs font-semibold flex items-center gap-1 transition-colors min-h-[38px]"
                title="Toggle Language"
              >
                <Languages size={15} />
                <span>{language === 'fa' ? 'EN' : 'فا'}</span>
              </button>
              <button
                onClick={() => setDarkMode(!darkMode)}
                className="p-2 rounded-xl hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300 transition-colors min-h-[38px] min-w-[38px] flex items-center justify-center text-sm"
                title={darkMode ? 'Light mode' : 'Dark mode'}
              >
                {darkMode ? '☀️' : '🌙'}
              </button>
            </div>
          </div>

          {error && (
            <div className="mb-5 p-3.5 bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-800/60 rounded-2xl animate-shake">
              <p className="text-xs text-rose-600 dark:text-rose-400 font-medium">{error}</p>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4 sm:space-y-5">
            <div>
              <label
                htmlFor="username"
                className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5"
              >
                {t.login.username}
              </label>
              <input
                id="username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                className="w-full px-4 py-3 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-750 text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-500 text-base sm:text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all min-h-[46px]"
                placeholder={t.login.usernamePlaceholder}
                autoComplete="username"
              />
            </div>

            <div>
              <label
                htmlFor="password"
                className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5"
              >
                {t.login.password}
              </label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                className="w-full px-4 py-3 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-750 text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-500 text-base sm:text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all min-h-[46px]"
                placeholder={t.login.passwordPlaceholder}
                autoComplete="current-password"
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full mt-2 px-4 py-3 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 text-white rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed transition-all font-bold shadow-md hover:shadow-lg flex items-center justify-center gap-2 text-sm min-h-[48px] active:scale-[0.99]"
            >
              {loading ? (
                <>
                  <Loader2 className="w-5 h-5 animate-spin" />
                  <span>{t.login.signingIn}</span>
                </>
              ) : (
                <>
                  <LogIn className="w-5 h-5" />
                  <span>{t.login.signIn}</span>
                </>
              )}
            </button>
          </form>
        </div>

        {/* Footer */}
        <div className="mt-6 text-center text-xs text-gray-500 dark:text-gray-400">
          <p>
            Made with <span className="text-red-500">❤️</span> by{' '}
            <a
              href="https://github.com/zZedix"
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 dark:text-blue-400 hover:underline font-semibold"
            >
              zZedix
            </a>
          </p>
          <p className="mt-1 font-mono">{version}</p>
        </div>
      </div>
    </div>
  )
}

export default Login
