import React, { Component, ErrorInfo, ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
  errorInfo: ErrorInfo | null
}

export class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
    errorInfo: null,
  }

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error, errorInfo: null }
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('Uncaught component error in Smite Panel:', error, errorInfo)
    this.setState({ errorInfo })
  }

  private handleReset = () => {
    // If it's a dynamic chunk loading error commonly caused by network drops, force reload
    if (this.state.error?.message?.toLowerCase().includes('dynamically imported module') ||
        this.state.error?.message?.toLowerCase().includes('loading chunk')) {
      window.location.reload()
      return
    }
    this.setState({ hasError: false, error: null, errorInfo: null })
  }

  private handleFullReload = () => {
    window.location.href = '/'
  }

  public render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen w-full flex items-center justify-center p-4 bg-slate-950 text-slate-100">
          <div className="relative w-full max-w-lg p-1 rounded-3xl bg-gradient-to-b from-slate-800/80 to-slate-900/40 border border-slate-800 shadow-2xl backdrop-blur-xl">
            <div className="relative p-6 sm:p-8 rounded-[calc(1.5rem-4px)] bg-slate-900/90 border border-slate-800/60 flex flex-col items-center text-center">
              {/* Alert Beacon */}
              <div className="relative mb-5 flex items-center justify-center">
                <div className="absolute w-14 h-14 rounded-full bg-rose-500/20 animate-ping opacity-50" />
                <div className="relative w-12 h-12 rounded-2xl bg-rose-500/10 border border-rose-500/30 flex items-center justify-center text-rose-400">
                  <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                  </svg>
                </div>
              </div>

              {/* Title & Description */}
              <h2 className="text-xl font-bold tracking-tight text-white mb-2">
                Session Interruption
              </h2>
              <p className="text-sm text-slate-400 leading-relaxed max-w-sm mb-6">
                A rendering exception or connection timeout occurred while mounting this view. Your configuration and tunnels remain safely running.
              </p>

              {/* Error Detail Pill */}
              {this.state.error?.message && (
                <div className="w-full mb-6 p-3 rounded-xl bg-slate-950/80 border border-slate-800/80 text-left font-mono text-xs text-rose-300/80 overflow-x-auto select-all max-h-24">
                  {this.state.error.message}
                </div>
              )}

              {/* Actions */}
              <div className="w-full flex flex-col sm:flex-row gap-3">
                <button
                  type="button"
                  onClick={this.handleReset}
                  className="w-full min-h-[44px] px-5 py-2.5 rounded-xl text-sm font-semibold text-white bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 active:scale-[0.98] transition-all shadow-lg shadow-indigo-600/20"
                >
                  Reload Current View
                </button>
                <button
                  type="button"
                  onClick={this.handleFullReload}
                  className="w-full min-h-[44px] px-5 py-2.5 rounded-xl text-sm font-medium text-slate-300 bg-slate-800/60 hover:bg-slate-800 hover:text-white border border-slate-700/60 active:scale-[0.98] transition-all"
                >
                  Return to Dashboard
                </button>
              </div>
            </div>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}

export default ErrorBoundary
