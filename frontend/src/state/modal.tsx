import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react';
import { Dialog } from '../components/Dialog';

export interface ConfirmOptions {
  title: string;
  body: string;
  effects?: string[];
  confirm?: string | null;
  cancel?: string;
  danger?: boolean;
}

interface ModalApi {
  /** Resolves true when the user confirms, false on cancel / Esc / backdrop. */
  confirm: (options: ConfirmOptions) => Promise<boolean>;
}

const Ctx = createContext<ModalApi | null>(null);

export function useModal(): ModalApi {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useModal outside ModalProvider');
  return ctx;
}

export function ModalProvider({ children }: { children: ReactNode }) {
  const [current, setCurrent] = useState<ConfirmOptions | null>(null);
  const resolver = useRef<((v: boolean) => void) | null>(null);

  const confirm = useCallback(
    (options: ConfirmOptions) =>
      new Promise<boolean>((resolve) => {
        resolver.current?.(false);
        resolver.current = resolve;
        setCurrent(options);
      }),
    [],
  );

  const close = (value: boolean) => {
    resolver.current?.(value);
    resolver.current = null;
    setCurrent(null);
  };

  const api = useMemo(() => ({ confirm }), [confirm]);

  return (
    <Ctx.Provider value={api}>
      {children}
      {current && (
        <Dialog title={current.title} role="alertdialog" onClose={() => close(false)}>
          <p className="dialog__body">{current.body}</p>
          {current.effects && current.effects.length > 0 && (
            <ul className="dialog__effects">
              {current.effects.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}
          <div className="dialog__actions">
            <button type="button" className="btn" onClick={() => close(false)}>
              {current.cancel ?? 'Cancel'}
            </button>
            {current.confirm !== null && (
              <button type="button" className={`btn ${current.danger ? 'btn--danger' : 'btn--primary'}`} onClick={() => close(true)}>
                {current.confirm ?? 'Confirm'}
              </button>
            )}
          </div>
        </Dialog>
      )}
    </Ctx.Provider>
  );
}
