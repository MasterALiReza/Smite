import { createContext, useContext, useState, useEffect, ReactNode } from 'react'
import api from '../api/client'

interface AuthContextType {
  isAuthenticated: boolean
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

  useEffect(() => {
    const token = localStorage.getItem('token')
    const savedUsername = localStorage.getItem('username')
    
    if (token && savedUsername) {
      // Verify token is still valid
      checkAuth().catch(() => {
        logout()
      })
    }
  }, [])

  const checkAuth = async (): Promise<boolean> => {
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
      // Token is invalid, logout
      logout()
      return false
    }
  }

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

