import React, { createContext, useContext, useState, useCallback, ReactNode, useEffect } from 'react';
import { CheckCircle, XCircle, AlertTriangle, Info, X } from 'lucide-react';

export type ToastType = 'success' | 'error' | 'warning' | 'info';

export interface Toast {
  id: string;
  type: ToastType;
  title: string;
  message?: string;
  duration?: number;
}

export interface ConfirmOptions {
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  variant?: 'danger' | 'default';
}

interface ToastContextType {
  showToast: (type: ToastType, title: string, message?: string, duration?: number) => void;
  showConfirm: (options: ConfirmOptions) => Promise<boolean>;
}

const ToastContext = createContext<ToastContextType | undefined>(undefined);

export const useToast = () => {
  const context = useContext(ToastContext);
  if (!context) throw new Error('useToast must be used within ToastProvider');
  return context;
};

export const ToastProvider = ({ children }: { children: ReactNode }) => {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [confirmConfig, setConfirmConfig] = useState<{ options: ConfirmOptions; resolve: (val: boolean) => void } | null>(null);

  const showToast = useCallback((type: ToastType, title: string, message?: string, duration: number = 4000) => {
    const id = Math.random().toString(36).substring(2, 9);
    setToasts((prev) => {
      const newToasts = [...prev, { id, type, title, message, duration }];
      return newToasts.slice(-5);
    });
  }, []);

  const showConfirm = useCallback((options: ConfirmOptions) => {
    return new Promise<boolean>((resolve) => {
      setConfirmConfig({ options, resolve });
    });
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter(t => t.id !== id));
  }, []);

  useEffect(() => {
    const timers = toasts.map((toast) => {
      if (toast.duration !== Infinity) {
        return setTimeout(() => removeToast(toast.id), toast.duration);
      }
      return null;
    });

    return () => {
      timers.forEach(timer => { if (timer) clearTimeout(timer) });
    };
  }, [toasts, removeToast]);

  const handleConfirm = (result: boolean) => {
    if (confirmConfig) {
      confirmConfig.resolve(result);
      setConfirmConfig(null);
    }
  };

  return (
    <ToastContext.Provider value={{ showToast, showConfirm }}>
      {children}
      
      {/* Floating Dynamic Toasts */}
      <div className="fixed bottom-5 left-4 right-4 sm:left-auto sm:right-5 z-[200] space-y-2.5 flex flex-col items-center sm:items-end pointer-events-none">
        {toasts.map(toast => {
          const styleConfig = {
            success: {
              border: 'border-emerald-500/30 dark:border-emerald-500/40',
              bg: 'bg-white/95 dark:bg-[#0c141f]/95',
              iconBg: 'bg-emerald-50 dark:bg-emerald-950/60 text-emerald-600 dark:text-emerald-400',
              accent: 'bg-emerald-500',
            },
            error: {
              border: 'border-rose-500/30 dark:border-rose-500/40',
              bg: 'bg-white/95 dark:bg-[#1a0f14]/95',
              iconBg: 'bg-rose-50 dark:bg-rose-950/60 text-rose-600 dark:text-rose-400',
              accent: 'bg-rose-500',
            },
            warning: {
              border: 'border-amber-500/30 dark:border-amber-500/40',
              bg: 'bg-white/95 dark:bg-[#1a1409]/95',
              iconBg: 'bg-amber-50 dark:bg-amber-950/60 text-amber-600 dark:text-amber-400',
              accent: 'bg-amber-500',
            },
            info: {
              border: 'border-sky-500/30 dark:border-sky-500/40',
              bg: 'bg-white/95 dark:bg-[#0c1322]/95',
              iconBg: 'bg-sky-50 dark:bg-sky-950/60 text-sky-600 dark:text-sky-400',
              accent: 'bg-sky-500',
            },
          };
          const Icons = {
            success: CheckCircle,
            error: XCircle,
            warning: AlertTriangle,
            info: Info
          };
          const Icon = Icons[toast.type];
          const style = styleConfig[toast.type];

          return (
            <div 
              key={toast.id}
              className={`pointer-events-auto relative overflow-hidden flex items-start gap-3 p-3.5 rounded-2xl shadow-xl w-full max-w-sm sm:w-84 backdrop-blur-xl border ${style.border} ${style.bg} transform transition-all duration-300 ease-spring opacity-100 translate-y-0`}
            >
              <div className={`p-2 rounded-xl shrink-0 ${style.iconBg}`}>
                <Icon className="w-4 h-4" />
              </div>
              <div className="flex-1 min-w-0 pt-0.5">
                <h4 className="text-xs font-bold text-slate-900 dark:text-white tracking-tight truncate">{toast.title}</h4>
                {toast.message && <p className="text-xs text-slate-600 dark:text-slate-300 mt-0.5 leading-relaxed break-words">{toast.message}</p>}
              </div>
              <button 
                onClick={() => removeToast(toast.id)} 
                className="p-1 opacity-60 hover:opacity-100 rounded-lg hover:bg-slate-100 dark:hover:bg-white/10 text-slate-500 dark:text-slate-400 transition-colors shrink-0 cursor-pointer"
                aria-label="Dismiss toast"
              >
                <X className="w-4 h-4" />
              </button>
              <div className={`absolute bottom-0 left-0 right-0 h-0.5 ${style.accent}`} />
            </div>
          );
        })}
      </div>

      {/* Confirm Modal */}
      {confirmConfig && (
        <div 
          className="fixed inset-0 z-[250] flex items-center justify-center bg-black/70 backdrop-blur-sm p-4 pointer-events-auto transition-all"
          onClick={(e) => {
            if (e.target === e.currentTarget) handleConfirm(false);
          }}
        >
          <div className="relative bg-white dark:bg-[#0d121f] rounded-3xl shadow-2xl max-w-md w-full p-6 border border-slate-200/90 dark:border-white/[0.1] overflow-hidden">
            <div className="flex items-start gap-3.5 mb-4">
              {confirmConfig.options.variant === 'danger' ? (
                <div className="p-3 rounded-2xl bg-rose-100/80 dark:bg-rose-950/60 text-rose-600 dark:text-rose-400 shrink-0 shadow-xs border border-rose-200/50 dark:border-rose-900/40">
                  <AlertTriangle className="w-5 h-5" />
                </div>
              ) : (
                <div className="p-3 rounded-2xl bg-sky-100/80 dark:bg-sky-950/60 text-sky-600 dark:text-sky-400 shrink-0 shadow-xs border border-sky-200/50 dark:border-sky-900/40">
                  <Info className="w-5 h-5" />
                </div>
              )}
              <div className="flex-1 min-w-0">
                <h3 className="text-base font-bold text-slate-900 dark:text-white tracking-tight">
                  {confirmConfig.options.title}
                </h3>
                <p className="text-xs sm:text-sm text-slate-600 dark:text-slate-300 mt-1.5 leading-relaxed">
                  {confirmConfig.options.message}
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-2.5 mt-6 pt-3 border-t border-slate-100 dark:border-white/[0.06]">
              <button
                type="button"
                onClick={() => handleConfirm(false)}
                className="px-4 py-2.5 rounded-xl bg-slate-100 hover:bg-slate-200/80 dark:bg-white/[0.06] dark:hover:bg-white/[0.1] text-slate-700 dark:text-slate-300 text-xs font-semibold transition-all active:scale-95 cursor-pointer min-h-[44px]"
              >
                {confirmConfig.options.cancelText || 'Cancel'}
              </button>
              <button
                type="button"
                onClick={() => handleConfirm(true)}
                className={`px-5 py-2.5 rounded-xl text-xs font-semibold text-white transition-all shadow-md active:scale-95 cursor-pointer min-h-[44px] ${
                  confirmConfig.options.variant === 'danger'
                    ? 'bg-rose-600 hover:bg-rose-700 shadow-rose-600/30'
                    : 'bg-blue-600 hover:bg-blue-700 shadow-blue-600/30'
                }`}
              >
                {confirmConfig.options.confirmText || 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}
    </ToastContext.Provider>
  );
};
