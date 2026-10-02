import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react';
import { Spinner } from '../components/Spinner';

export interface ToastOptions {
  tone?: 'info' | 'error';
  busy?: boolean;
  sticky?: boolean;
  ms?: number;
  action?: string;
  onAction?: () => void;
}
interface ToastItem extends ToastOptions {
  id: string;
  text: string;
}

interface ToastApi {
  toast: (text: string, options?: ToastOptions) => string;
  patch: (id: string, patch: Partial<ToastOptions> & { text?: string }) => void;
  dismiss: (id: string) => void;
}

const Ctx = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useToast outside ToastProvider');
  return ctx;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const timers = useRef(new Map<string, number>());
  const seq = useRef(0);

  const dismiss = useCallback((id: string) => {
    const t = timers.current.get(id);
    if (t) window.clearTimeout(t);
    timers.current.delete(id);
    setItems((s) => s.filter((x) => x.id !== id));
  }, []);

  const arm = useCallback(
    (id: string, ms: number) => {
      const prev = timers.current.get(id);
      if (prev) window.clearTimeout(prev);
      timers.current.set(id, window.setTimeout(() => dismiss(id), ms));
    },
    [dismiss],
  );

  const toast = useCallback(
    (text: string, options: ToastOptions = {}) => {
      const id = `t${++seq.current}`;
      setItems((s) => [...s.slice(-3), { id, text, ...options }]);
      if (!options.sticky) arm(id, options.ms ?? 4500);
      return id;
    },
    [arm],
  );

  const patch = useCallback(
    (id: string, p: Partial<ToastOptions> & { text?: string }) => {
      setItems((s) => s.map((x) => (x.id === id ? { ...x, ...p } : x)));
      if (!p.sticky) arm(id, p.ms ?? 5200);
    },
    [arm],
  );

  const api = useMemo(() => ({ toast, patch, dismiss }), [toast, patch, dismiss]);

  return (
    <Ctx.Provider value={api}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} role="status" className={`toast${t.tone === 'error' ? ' toast--error' : ''}`}>
            {t.busy && (
              <span style={{ paddingTop: 2 }}>
                <Spinner light />
              </span>
            )}
            <span className="toast__text">{t.text}</span>
            {t.action && (
              <button
                type="button"
                className="toast__action"
                onClick={() => {
                  t.onAction?.();
                  dismiss(t.id);
                }}
              >
                {t.action}
              </button>
            )}
            <button type="button" className="toast__close" aria-label="Dismiss" onClick={() => dismiss(t.id)}>
              ×
            </button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
