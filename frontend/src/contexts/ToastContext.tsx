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
      
      {/* Toasts */}
      <div className="fixed bottom-4 left-4 right-4 sm:left-auto sm:right-4 z-[200] space-y-2 flex flex-col items-center sm:items-end pointer-events-none">
        {toasts.map(toast => {
          const colors = {
            success: 'border-green-500 bg-green-50 dark:bg-green-900/40 text-green-800 dark:text-green-200 border shadow-md',
            error: 'border-red-500 bg-red-50 dark:bg-red-900/40 text-red-800 dark:text-red-200 border shadow-md',
            warning: 'border-yellow-500 bg-yellow-50 dark:bg-yellow-900/40 text-yellow-800 dark:text-yellow-200 border shadow-md',
            info: 'border-blue-500 bg-blue-50 dark:bg-blue-900/40 text-blue-800 dark:text-blue-200 border shadow-md'
          };
          const Icons = {
            success: CheckCircle,
            error: XCircle,
            warning: AlertTriangle,
            info: Info
          };
          const Icon = Icons[toast.type];

          return (
            <div 
              key={toast.id}
              className={`pointer-events-auto flex items-start gap-3 p-4 rounded-xl shadow-lg w-full max-w-sm sm:w-80 transform transition-all duration-300 ease-in-out opacity-100 translate-y-0 ${colors[toast.type]}`}
            >
              <Icon className="w-5 h-5 mt-0.5 shrink-0" />
              <div className="flex-1 min-w-0">
                <h4 className="text-sm font-semibold truncate">{toast.title}</h4>
                {toast.message && <p className="text-sm mt-1 opacity-90 break-words">{toast.message}</p>}
              </div>
              <button 
                onClick={() => removeToast(toast.id)} 
                className="p-1 opacity-70 hover:opacity-100 rounded-lg hover:bg-black/5 dark:hover:bg-white/10 transition-colors shrink-0"
                aria-label="Dismiss toast"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          );
        })}
      </div>

      {/* Confirm Modal */}
      {confirmConfig && (
        <div 
          className="fixed inset-0 z-[250] flex items-center justify-center bg-black/60 backdrop-blur-xs p-4 pointer-events-auto animate-in fade-in duration-150"
          onClick={(e) => {
            if (e.target === e.currentTarget) handleConfirm(false);
          }}
        >
          <div className="bg-white dark:bg-slate-800 rounded-2xl shadow-2xl max-w-md w-full p-6 border border-gray-100 dark:border-gray-700/80 animate-in fade-in zoom-in-95 duration-150">
            <div className="flex items-start gap-3.5 mb-4">
              {confirmConfig.options.variant === 'danger' ? (
                <div className="p-2.5 rounded-xl bg-rose-100 dark:bg-rose-950/60 text-rose-600 dark:text-rose-400 shrink-0 shadow-xs">
                  <AlertTriangle className="w-5 h-5" />
                </div>
              ) : (
                <div className="p-2.5 rounded-xl bg-blue-100 dark:bg-blue-950/60 text-blue-600 dark:text-blue-400 shrink-0 shadow-xs">
                  <Info className="w-5 h-5" />
                </div>
              )}
              <div className="flex-1 min-w-0">
                <h3 className="text-lg font-bold text-slate-900 dark:text-white">
                  {confirmConfig.options.title}
                </h3>
                <p className="text-sm text-slate-600 dark:text-slate-300 mt-1.5 leading-relaxed">
                  {confirmConfig.options.message}
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-2.5 mt-6 pt-2 border-t border-gray-100 dark:border-gray-700/50">
              <button
                type="button"
                onClick={() => handleConfirm(false)}
                className="px-4 py-2 rounded-xl bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-200 text-sm font-semibold transition-all active:scale-95 cursor-pointer"
              >
                {confirmConfig.options.cancelText || 'Cancel'}
              </button>
              <button
                type="button"
                onClick={() => handleConfirm(true)}
                className={`px-4 py-2 rounded-xl text-sm font-semibold text-white transition-all shadow-md active:scale-95 cursor-pointer ${
                  confirmConfig.options.variant === 'danger'
                    ? 'bg-rose-600 hover:bg-rose-700 shadow-rose-600/20'
                    : 'bg-blue-600 hover:bg-blue-700 shadow-blue-600/20'
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
