import React, { Suspense, lazy } from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { LanguageProvider } from './contexts/LanguageContext'
import { ThemeProvider } from './contexts/ThemeContext'
import { ToastProvider } from './contexts/ToastContext'
import Layout from './components/Layout'
import ErrorBoundary from './components/ErrorBoundary'

// Eagerly loaded for instant critical path render
import Login from './pages/Login'

// Code-split route components for performance and sub-second initial load
const Dashboard = lazy(() => import('./pages/Dashboard'))
const Nodes = lazy(() => import('./pages/Nodes'))
const Servers = lazy(() => import('./pages/Servers'))
const Tunnels = lazy(() => import('./pages/Tunnels'))
const Logs = lazy(() => import('./pages/Logs'))
const CoreHealth = lazy(() => import('./pages/CoreHealth'))
const Settings = lazy(() => import('./pages/Settings'))

// Minimalist High-Craft Loading Fallback
const PageFallback = () => (
  <div className="w-full min-h-[60vh] flex flex-col items-center justify-center gap-3 animate-fade-in">
    <div className="relative">
      <div className="w-9 h-9 border-2 border-indigo-500/20 border-t-indigo-500 rounded-full animate-spin" />
    </div>
    <span className="text-[11px] font-mono tracking-wider text-slate-400 dark:text-slate-500 uppercase">
      Loading view
    </span>
  </div>
)

// Protected Route Component
const ProtectedRoute = ({ children }: { children: React.ReactNode }) => {
  const { isAuthenticated, isLoading } = useAuth()

  if (isLoading) {
    return (
      <div className="min-h-[100dvh] flex flex-col items-center justify-center bg-slate-50 dark:bg-[#0c0f17] gap-3">
        <div className="relative">
          <div className="w-10 h-10 border-2 border-indigo-500/20 border-t-indigo-500 rounded-full animate-spin" />
        </div>
        <span className="text-xs font-mono tracking-wider text-slate-500 dark:text-slate-400 uppercase">
          Initializing session
        </span>
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}

// App Routes Component
const AppRoutes = () => {
  const { isAuthenticated } = useAuth()

  return (
    <ErrorBoundary>
      <Routes>
        <Route path="/login" element={isAuthenticated ? <Navigate to="/dashboard" replace /> : <Login />} />
        
        {/* Persistent Layout shell: keeps sidebar and header mounted for instant page transitions */}
        <Route
          element={
            <ProtectedRoute>
              <Layout />
            </ProtectedRoute>
          }
        >
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route
            path="/dashboard"
            element={
              <Suspense fallback={<PageFallback />}>
                <Dashboard />
              </Suspense>
            }
          />
          <Route
            path="/nodes"
            element={
              <Suspense fallback={<PageFallback />}>
                <Nodes />
              </Suspense>
            }
          />
          <Route
            path="/servers"
            element={
              <Suspense fallback={<PageFallback />}>
                <Servers />
              </Suspense>
            }
          />
          <Route
            path="/tunnels"
            element={
              <Suspense fallback={<PageFallback />}>
                <Tunnels />
              </Suspense>
            }
          />
          <Route
            path="/logs"
            element={
              <Suspense fallback={<PageFallback />}>
                <Logs />
              </Suspense>
            }
          />
          <Route
            path="/core-health"
            element={
              <Suspense fallback={<PageFallback />}>
                <CoreHealth />
              </Suspense>
            }
          />
          <Route
            path="/settings"
            element={
              <Suspense fallback={<PageFallback />}>
                <Settings />
              </Suspense>
            }
          />
        </Route>
      </Routes>
    </ErrorBoundary>
  )
}

function App() {
  return (
    <Router>
      <ThemeProvider>
        <ToastProvider>
          <LanguageProvider>
            <AuthProvider>
              <AppRoutes />
            </AuthProvider>
          </LanguageProvider>
        </ToastProvider>
      </ThemeProvider>
    </Router>
  )
}

export default App
