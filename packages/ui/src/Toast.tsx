/**
 * Toast / Toaster — transient messages with a next action.
 *
 * Golden rule 9 ("errors say what to do next") is enforced in the type system
 * here: a toast with `severity: 'fail'` MUST carry an `action`. You cannot ship
 * a dead-end error toast through this API. §15 also wants "Wall deleted — Undo"
 * after destructive ops, which is the same shape.
 *
 * Live-region behaviour: errors go to `role="alert"` (assertive, interrupts);
 * everything else to `role="status"` (polite). The container is always mounted
 * so screen readers pick up later insertions.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { ReactNode } from 'react';
import { cn } from './cn';
import { Icon } from './icons';
import type { IconName } from './icons';
import { IconButton } from './Button';

export type ToastSeverity = 'info' | 'pass' | 'warn' | 'fail';

export interface ToastAction {
  label: string;
  onClick: () => void;
}

interface ToastCommon {
  /** Short, plain, warm. "Couldn't save that wall" — not "Mutation failed". */
  title: string;
  description?: string | undefined;
  /** ms before auto-dismiss. `null` keeps it until dismissed. */
  duration?: number | null | undefined;
  /** Stable key: re-showing the same id replaces rather than stacks. */
  id?: string | undefined;
}

/** A failure MUST offer a next action — see golden rule 9. */
export type ToastInput =
  | (ToastCommon & { severity?: 'info' | 'pass' | 'warn' | undefined; action?: ToastAction | undefined })
  | (ToastCommon & { severity: 'fail'; action: ToastAction });

interface ToastRecord {
  id: string;
  severity: ToastSeverity;
  title: string;
  description: string | undefined;
  action: ToastAction | undefined;
  duration: number | null;
}

interface ToastApi {
  /** Show a toast; returns its id so you can dismiss it early. */
  toast: (input: ToastInput) => string;
  dismiss: (id: string) => void;
  dismissAll: () => void;
}

const ToastContext = createContext<ToastApi | null>(null);

const DEFAULT_DURATION: Record<ToastSeverity, number | null> = {
  info: 5000,
  pass: 4000,
  warn: 8000,
  // Errors never auto-dismiss: the action is the point, and it must still be
  // there when the user looks back at the screen.
  fail: null,
};

const SEVERITY_ICON: Record<ToastSeverity, IconName> = {
  info: 'info',
  pass: 'check-circle',
  warn: 'alert-triangle',
  fail: 'alert-circle',
};

const SEVERITY_SKIN: Record<ToastSeverity, string> = {
  info: 'border-info-line bg-surface',
  pass: 'border-pass-line bg-surface',
  warn: 'border-warn-line bg-surface',
  fail: 'border-fail-line bg-surface',
};

const SEVERITY_ICON_COLOR: Record<ToastSeverity, string> = {
  info: 'text-info-ink',
  pass: 'text-pass-ink',
  warn: 'text-warn-ink',
  fail: 'text-fail-ink',
};

let counter = 0;
function nextId(): string {
  counter += 1;
  return `toast-${counter}`;
}

export interface ToastProviderProps {
  children: ReactNode;
  /** Max simultaneous toasts; older ones drop off the top. */
  limit?: number | undefined;
}

export function ToastProvider({ children, limit = 4 }: ToastProviderProps): JSX.Element {
  const [items, setItems] = useState<ToastRecord[]>([]);
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: string) => {
    const t = timers.current.get(id);
    if (t !== undefined) {
      clearTimeout(t);
      timers.current.delete(id);
    }
    setItems((prev) => prev.filter((i) => i.id !== id));
  }, []);

  const dismissAll = useCallback(() => {
    for (const t of timers.current.values()) clearTimeout(t);
    timers.current.clear();
    setItems([]);
  }, []);

  const toast = useCallback(
    (input: ToastInput): string => {
      const severity: ToastSeverity = input.severity ?? 'info';
      const id = input.id ?? nextId();
      const duration = input.duration === undefined ? DEFAULT_DURATION[severity] : input.duration;
      const record: ToastRecord = {
        id,
        severity,
        title: input.title,
        description: input.description,
        action: input.action,
        duration,
      };
      setItems((prev) => {
        const without = prev.filter((i) => i.id !== id);
        const next = [...without, record];
        return next.length > limit ? next.slice(next.length - limit) : next;
      });
      const existing = timers.current.get(id);
      if (existing !== undefined) clearTimeout(existing);
      if (duration !== null) {
        timers.current.set(
          id,
          setTimeout(() => dismiss(id), duration),
        );
      }
      return id;
    },
    [dismiss, limit],
  );

  useEffect(() => {
    const map = timers.current;
    return () => {
      for (const t of map.values()) clearTimeout(t);
      map.clear();
    };
  }, []);

  const api = useMemo<ToastApi>(() => ({ toast, dismiss, dismissAll }), [toast, dismiss, dismissAll]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <Toaster items={items} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

/**
 * `const { toast } = useToast()`.
 * Throws if the provider is missing — a silently swallowed error toast is worse
 * than a crash in development.
 */
export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (ctx === null) {
    throw new Error('useToast must be used inside <ToastProvider>. Wrap the app root in it.');
  }
  return ctx;
}

interface ToasterProps {
  items: readonly ToastRecord[];
  onDismiss: (id: string) => void;
}

export function Toaster({ items, onDismiss }: ToasterProps): JSX.Element | null {
  if (typeof document === 'undefined') return null;
  return createPortal(
    <div
      className="pointer-events-none fixed inset-x-0 bottom-0 z-toast flex flex-col items-center gap-2 p-4 sm:items-end sm:p-6"
      // The region is always present so insertions are announced.
      aria-live="polite"
      aria-relevant="additions text"
    >
      {items.map((item) => (
        <div
          key={item.id}
          role={item.severity === 'fail' ? 'alert' : 'status'}
          className={cn(
            'pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-lg border p-3 shadow-md',
            'animate-slide-in-right',
            SEVERITY_SKIN[item.severity],
          )}
        >
          <Icon
            name={SEVERITY_ICON[item.severity]}
            size={17}
            className={cn('mt-0.5', SEVERITY_ICON_COLOR[item.severity])}
          />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium leading-5 text-ink">{item.title}</p>
            {item.description !== undefined ? (
              <p className="mt-0.5 text-xs leading-4 text-ink-muted">{item.description}</p>
            ) : null}
            {item.action !== undefined ? (
              <button
                type="button"
                className="garh-focus-ring mt-1.5 rounded-sm text-xs font-semibold text-brand-ink underline underline-offset-2 hover:text-brand"
                onClick={() => {
                  item.action?.onClick();
                  onDismiss(item.id);
                }}
              >
                {item.action.label}
              </button>
            ) : null}
          </div>
          <IconButton
            label="Dismiss"
            icon="x"
            size="sm"
            className="-mr-1 -mt-1"
            onClick={() => onDismiss(item.id)}
          />
        </div>
      ))}
    </div>,
    document.body,
  );
}
