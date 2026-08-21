import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react'
import api from '../api/client'

interface AuthContextType {
  isAuthenticated: boolean
  isLoading: boolean
  username: string | null
  login: (token: string, username: string) => void
  logout: () => void
  checkAuth: () => Promise<boolean>
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export const useAuth = () => {
  const context = useContext(AuthContext)
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}

interface AuthProviderProps {
  children: ReactNode
}

export const AuthProvider = ({ children }: AuthProviderProps) => {
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [username, setUsername] = useState<string | null>(null)
  // isLoading: true during the initial auth check on app startup
  const [isLoading, setIsLoading] = useState(true)

  // Stable reference — won't cause ProtectedRoute re-renders
  const checkAuth = useCallback(async (): Promise<boolean> => {
    try {
      const token = localStorage.getItem('token')
      if (!token) {
        setIsAuthenticated(false)
        setUsername(null)
        return false
      }

      // Set token in axios default headers
      api.defaults.headers.common['Authorization'] = `Bearer ${token}`
      
      // Verify token by calling /me endpoint
      const response = await api.get('/auth/me')
      
      setIsAuthenticated(true)
      setUsername(response.data.username || localStorage.getItem('username'))
      return true
    } catch (error) {
      // Token is invalid, clear state
      logout()
      return false
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Run auth check once on app startup
  useEffect(() => {
    const token = localStorage.getItem('token')
    if (token) {
      checkAuth().finally(() => setIsLoading(false))
    } else {
      setIsLoading(false)
    }
  }, [checkAuth])

  const login = (token: string, username: string) => {
    localStorage.setItem('token', token)
    localStorage.setItem('username', username)
    api.defaults.headers.common['Authorization'] = `Bearer ${token}`
    setIsAuthenticated(true)
    setUsername(username)
  }

  const logout = () => {
    localStorage.removeItem('token')
    localStorage.removeItem('username')
    delete api.defaults.headers.common['Authorization']
    setIsAuthenticated(false)
    setUsername(null)
  }

  return (
    <AuthContext.Provider
      value={{
        isAuthenticated,
        isLoading,
        username,
        login,
        logout,
        checkAuth,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}
