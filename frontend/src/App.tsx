import React, { Suspense, lazy } from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { LanguageProvider } from './contexts/LanguageContext'
import { ThemeProvider } from './contexts/ThemeContext'
import { ToastProvider } from './contexts/ToastContext'
import Layout from './components/Layout'

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
    <Suspense fallback={<PageFallback />}>
      <Routes>
        <Route path="/login" element={isAuthenticated ? <Navigate to="/dashboard" replace /> : <Login />} />
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <Layout>
                <Navigate to="/dashboard" replace />
              </Layout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/dashboard"
          element={
            <ProtectedRoute>
              <Layout>
                <Dashboard />
              </Layout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/nodes"
          element={
            <ProtectedRoute>
              <Layout>
                <Nodes />
              </Layout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/servers"
          element={
            <ProtectedRoute>
              <Layout>
                <Servers />
              </Layout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/tunnels"
          element={
            <ProtectedRoute>
              <Layout>
                <Tunnels />
              </Layout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/logs"
          element={
            <ProtectedRoute>
              <Layout>
                <Logs />
              </Layout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/core-health"
          element={
            <ProtectedRoute>
              <Layout>
                <CoreHealth />
              </Layout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/settings"
          element={
            <ProtectedRoute>
              <Layout>
                <Settings />
              </Layout>
            </ProtectedRoute>
          }
        />
      </Routes>
    </Suspense>
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
