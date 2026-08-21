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
            success: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-800 dark:text-emerald-300 border shadow-lg backdrop-blur-md',
            error: 'border-rose-500/40 bg-rose-500/10 text-rose-800 dark:text-rose-300 border shadow-lg backdrop-blur-md',
            warning: 'border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-300 border shadow-lg backdrop-blur-md',
            info: 'border-[#3F72AF]/40 dark:border-[#00A8CC]/40 bg-[#3F72AF]/10 dark:bg-[#00A8CC]/10 text-[#112D4E] dark:text-[#F9F7F7] border shadow-lg backdrop-blur-md'
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
              className={`pointer-events-auto flex items-start gap-3 p-4 rounded-2xl shadow-xl w-full max-w-sm sm:w-80 transform transition-all duration-300 ease-out opacity-100 translate-y-0 ${colors[toast.type]}`}
            >
              <Icon className="w-5 h-5 mt-0.5 shrink-0" />
              <div className="flex-1 min-w-0">
                <h4 className="text-sm font-bold truncate">{toast.title}</h4>
                {toast.message && <p className="text-xs mt-1 font-medium opacity-90 break-words">{toast.message}</p>}
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
        <div className="fixed inset-0 z-[250] flex items-center justify-center bg-[#112D4E]/60 dark:bg-black/70 backdrop-blur-md p-4 pointer-events-auto">
          <div className="bg-white dark:bg-[#27496D] border border-[#DBE2EF] dark:border-[#142850] rounded-2xl shadow-2xl max-w-md w-full p-6 animate-in fade-in zoom-in-95 duration-200">
            <h3 className="text-xl font-bold text-[#112D4E] dark:text-[#F9F7F7] mb-2">
              {confirmConfig.options.title}
            </h3>
            <p className="text-sm text-[#112D4E]/80 dark:text-[#DBE2EF] mb-6">
              {confirmConfig.options.message}
            </p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => handleConfirm(false)}
                className="px-4 py-2.5 rounded-xl bg-[#DBE2EF]/70 hover:bg-[#DBE2EF] dark:bg-[#142850] dark:hover:bg-[#142850]/80 text-[#112D4E] dark:text-[#DBE2EF] font-semibold text-sm transition-colors"
              >
                {confirmConfig.options.cancelText || 'Cancel'}
              </button>
              <button
                onClick={() => handleConfirm(true)}
                className={`px-5 py-2.5 rounded-xl font-semibold text-sm text-white transition-all shadow-md ${
                  confirmConfig.options.variant === 'danger'
                    ? 'bg-rose-600 hover:bg-rose-700 shadow-rose-600/20'
                    : 'bg-[#3F72AF] hover:bg-[#3F72AF]/90 dark:bg-[#00A8CC] dark:hover:bg-[#00A8CC]/90 dark:text-[#142850] shadow-[#3F72AF]/20'
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
