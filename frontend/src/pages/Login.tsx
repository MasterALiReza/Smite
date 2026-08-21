import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { LogIn, Loader2, Shield } from 'lucide-react'
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
  const { t, dir } = useLanguage()

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
    <div className="min-h-screen bg-[#F9F7F7] dark:bg-[#142850] flex items-center justify-center p-4 selection:bg-[#3F72AF]/20 dark:selection:bg-[#00A8CC]/30" dir={dir}>
      <div className="w-full max-w-md">
        {/* Logo and Title */}
        <div className="text-center mb-8">
          <div className="flex justify-center mb-6">
            <div className="relative">
              <div className="absolute inset-0 bg-[#3F72AF]/20 dark:bg-[#00A8CC]/20 rounded-full blur-2xl"></div>
              <img
                src={darkMode ? SmiteLogoDark : SmiteLogoLight}
                alt="Smite Logo"
                className="relative h-32 w-32 sm:h-40 sm:w-40"
              />
            </div>
          </div>
          <h1 className="text-4xl sm:text-5xl font-black bg-gradient-to-r from-[#3F72AF] to-[#112D4E] dark:from-[#00A8CC] dark:to-[#F9F7F7] bg-clip-text text-transparent mb-2">
            {t.login.title}
          </h1>
          <p className="text-sm font-medium text-[#112D4E]/70 dark:text-[#DBE2EF]/80">{t.login.subtitle}</p>
        </div>

        {/* Login Card */}
        <div className="bg-white dark:bg-[#27496D] rounded-2xl shadow-2xl border border-[#DBE2EF] dark:border-[#142850] p-8 sm:p-10 animate-in fade-in zoom-in-95 duration-200">
          <div className="flex items-center justify-between mb-8">
            <div className="flex items-center gap-3">
              <div className="p-2.5 bg-[#3F72AF]/10 dark:bg-[#00A8CC]/15 rounded-xl">
                <Shield className="w-5 h-5 text-[#3F72AF] dark:text-[#00A8CC]" />
              </div>
              <h2 className="text-xl font-bold text-[#112D4E] dark:text-[#F9F7F7]">
                {t.login.signIn}
              </h2>
            </div>
            <button
              onClick={() => setDarkMode(!darkMode)}
              className="p-2.5 rounded-xl hover:bg-[#DBE2EF]/60 dark:hover:bg-[#142850] text-[#112D4E] dark:text-[#DBE2EF] transition-colors"
              title={darkMode ? 'Light mode' : 'Dark mode'}
            >
              {darkMode ? '☀️' : '🌙'}
            </button>
          </div>

          {error && (
            <div className="mb-6 p-4 bg-rose-500/10 border border-rose-500/30 rounded-xl">
              <p className="text-xs font-bold text-rose-700 dark:text-rose-300">{error}</p>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label
                htmlFor="username"
                className="block text-xs font-bold uppercase tracking-wider text-[#112D4E]/70 dark:text-[#DBE2EF]/70 mb-1.5"
              >
                {t.login.username}
              </label>
              <input
                id="username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                className="w-full px-4 py-3 border border-[#DBE2EF] dark:border-[#142850] rounded-xl bg-[#F9F7F7] dark:bg-[#142850] text-[#112D4E] dark:text-[#F9F7F7] placeholder-[#112D4E]/40 dark:placeholder-[#DBE2EF]/40 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-[#3F72AF] dark:focus:ring-[#00A8CC] transition-all"
                placeholder={t.login.usernamePlaceholder}
                autoComplete="username"
              />
            </div>

            <div>
              <label
                htmlFor="password"
                className="block text-xs font-bold uppercase tracking-wider text-[#112D4E]/70 dark:text-[#DBE2EF]/70 mb-1.5"
              >
                {t.login.password}
              </label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                className="w-full px-4 py-3 border border-[#DBE2EF] dark:border-[#142850] rounded-xl bg-[#F9F7F7] dark:bg-[#142850] text-[#112D4E] dark:text-[#F9F7F7] placeholder-[#112D4E]/40 dark:placeholder-[#DBE2EF]/40 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-[#3F72AF] dark:focus:ring-[#00A8CC] transition-all"
                placeholder={t.login.passwordPlaceholder}
                autoComplete="current-password"
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full mt-6 px-4 py-3.5 bg-[#3F72AF] hover:bg-[#3F72AF]/90 dark:bg-gradient-to-r dark:from-[#0C7B93] dark:to-[#00A8CC] text-white rounded-xl focus:outline-none focus:ring-2 focus:ring-[#3F72AF] dark:focus:ring-[#00A8CC] focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200 font-bold text-sm shadow-lg shadow-[#3F72AF]/25 dark:shadow-[#00A8CC]/25 flex items-center justify-center gap-2 hover:scale-[1.01] active:scale-[0.99]"
            >
              {loading ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>{t.login.signingIn}</span>
                </>
              ) : (
                <>
                  <LogIn className="w-4 h-4" />
                  <span>{t.login.signIn}</span>
                </>
              )}
            </button>
          </form>
        </div>

        {/* Footer */}
        <div className="mt-8 text-center text-xs font-medium text-[#112D4E]/60 dark:text-[#DBE2EF]/60 space-y-1">
          <p>
            Made with <span className="text-rose-500">❤️</span> by{' '}
            <a
              href="https://github.com/zZedix"
              target="_blank"
              rel="noopener noreferrer"
              className="text-[#3F72AF] dark:text-[#00A8CC] hover:underline font-bold"
            >
              zZedix
            </a>
          </p>
          <p>{version}</p>
        </div>
      </div>
    </div>
  )
}

export default Login
