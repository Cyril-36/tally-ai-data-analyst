import { useEffect, useId, useRef, type ReactNode, type KeyboardEvent } from 'react';

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

interface DialogProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** alertdialog for destructive confirmations */
  role?: 'dialog' | 'alertdialog';
  wide?: boolean;
  /** Close when the backdrop is clicked (default true). */
  closeOnBackdrop?: boolean;
  /** Show the × button in the header (the upload dialog does). */
  showClose?: boolean;
  /** Element to focus first; defaults to the first focusable control. */
  initialFocus?: React.RefObject<HTMLElement>;
}

/**
 * Modal dialog: focus moves in on open, Tab is trapped, Esc closes, focus returns to the
 * element that opened it.
 */
export function Dialog({
  title,
  onClose,
  children,
  role = 'dialog',
  wide = false,
  closeOnBackdrop = true,
  showClose = false,
  initialFocus,
}: DialogProps) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLElement | null>(null);

  useEffect(() => {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const target = initialFocus?.current ?? panel.current?.querySelector<HTMLElement>(FOCUSABLE) ?? panel.current;
    target?.focus();
    return () => {
      const el = opener.current;
      if (el && document.contains(el)) el.focus();
    };
    // run once per mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      onClose();
      return;
    }
    if (e.key !== 'Tab' || !panel.current) return;
    const items = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null || el === document.activeElement);
    if (!items.length) {
      e.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && (document.activeElement === first || !panel.current.contains(document.activeElement))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  return (
    <div
      className="dialog-backdrop"
      onMouseDown={(e) => {
        if (closeOnBackdrop && e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panel}
        role={role}
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`dialog${wide ? ' dialog--wide' : ''}`}
        onKeyDown={onKeyDown}
      >
        {showClose ? (
          <div className="dialog__head">
            <h2 id={titleId}>{title}</h2>
            <button type="button" className="btn btn--quiet" style={{ width: 32, height: 32, padding: 0, fontSize: 18 }} aria-label="Close" onClick={onClose}>
              ×
            </button>
          </div>
        ) : (
          <h2 id={titleId}>{title}</h2>
        )}
        {children}
      </div>
    </div>
  );
}
