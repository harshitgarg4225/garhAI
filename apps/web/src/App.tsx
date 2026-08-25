/**
 * The application root: providers, boot sequence, and the router.
 *
 * Everything that must exist exactly once lives here — the toast provider, the
 * session bootstrap, the global keyboard map, the outermost error boundary —
 * and nothing else does. Screens are in `pages/`, chrome is in `components/`,
 * and this file is the seam between them and the stores.
 *
 * ## Two toast systems, one toaster
 *
 * `@garh/ui` owns the toast component and its `useToast()` hook, which is what
 * pages call. The `ui` store owns a toast *queue*, which is what the model
 * store pushes to — it has to report "that edit was rejected, here is why" from
 * inside an async flush, with no component in scope and no hook available.
 *
 * {@link StoreToastBridge} joins them: it drains the store queue into the
 * provider. Without it, the model store's rejection toasts would be state
 * nobody renders — which is a silent failure, and golden rule 9 exists to
 * prevent exactly that.
 */

import { useEffect, useRef } from 'react';
import { RouterProvider } from 'react-router-dom';

import { ToastProvider, useToast } from '@garh/ui';
import type { ToastInput as UiToastInput } from '@garh/ui';

import { ErrorBoundary } from './components';
import { useAppShortcuts } from './lib/shortcuts';
import { router } from './routes';
import { useSessionStore } from './stores/session';
import { useUiStore, type Toast as StoreToast } from './stores/ui';

// ---------------------------------------------------------------------------
// Toast bridge
// ---------------------------------------------------------------------------

const SEVERITY: Readonly<Record<StoreToast['tone'], 'info' | 'pass' | 'warn' | 'fail'>> = {
  info: 'info',
  success: 'pass',
  warning: 'warn',
  error: 'fail',
};

/**
 * Translate one store toast into the provider's input.
 *
 * The `fail` branch is separate because `@garh/ui` types a failure toast as
 * *requiring* an action — you cannot ship a dead-end error through that API.
 * When the store did not supply one (the model store often has nothing better
 * to offer than "read this"), the bridge supplies an acknowledge button rather
 * than downgrading the severity, which would hide a real failure behind an
 * amber chip.
 */
function toUiToast(toast: StoreToast, dismiss: (id: string) => void): UiToastInput {
  const severity = SEVERITY[toast.tone];
  const description =
    toast.requestId === null
      ? (toast.description ?? undefined)
      : `${toast.description ?? ''}${toast.description ? ' ' : ''}(ref ${toast.requestId})`.trim();

  const common = {
    id: toast.id,
    title: toast.title,
    description,
    // The store already ran its own auto-dismiss timer; letting the provider
    // run a second one would double-schedule every toast. `null` = manual.
    duration: null,
  } as const;

  if (severity === 'fail') {
    return {
      ...common,
      severity,
      action: toast.action
        ? { label: toast.action.label, onClick: toast.action.run }
        : { label: 'Got it', onClick: () => dismiss(toast.id) },
    };
  }

  return {
    ...common,
    severity,
    ...(toast.action === null
      ? {}
      : { action: { label: toast.action.label, onClick: toast.action.run } }),
  };
}

/**
 * Drain `ui.toasts` into the `@garh/ui` toaster.
 *
 * Renders nothing. It tracks which store toasts it has already forwarded so a
 * re-render caused by anything else does not re-announce them to a screen
 * reader; ids are shared between the two systems, so a store dismissal and a
 * provider dismissal refer to the same card.
 */
function StoreToastBridge(): null {
  const toasts = useUiStore((s) => s.toasts);
  const dismissInStore = useUiStore((s) => s.dismissToast);
  const { toast: show, dismiss: hide } = useToast();
  const shown = useRef(new Set<string>());

  useEffect(() => {
    const live = new Set(toasts.map((t) => t.id));

    for (const item of toasts) {
      if (shown.current.has(item.id)) continue;
      shown.current.add(item.id);
      show(toUiToast(item, dismissInStore));
    }

    // The store dropped one (its timer fired, or something dismissed it):
    // take the card off screen too.
    for (const id of shown.current) {
      if (!live.has(id)) {
        shown.current.delete(id);
        hide(id);
      }
    }
  }, [toasts, show, hide, dismissInStore]);

  return null;
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

/**
 * One-time startup work that needs to be inside React.
 *
 * `bootstrap()` is the important one: the refresh credential is an httpOnly
 * cookie, so the only way to answer "am I signed in?" is to try. The route
 * guards wait on `status` while it is in flight rather than bouncing to the
 * login screen (see `routes.tsx`).
 */
function AppBoot(): null {
  const bootstrap = useSessionStore((s) => s.bootstrap);

  useEffect(() => {
    // `/share/:token` is the anonymous client surface (§13): a guest has no
    // refresh cookie to probe, and a bootstrap result landing AFTER the page
    // enters share mode would overwrite that session state. Guests boot from
    // the token alone.
    if (window.location.pathname.startsWith('/share/')) return;
    void bootstrap();
  }, [bootstrap]);

  useAppShortcuts();

  return null;
}

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

export function App(): JSX.Element {
  return (
    <ErrorBoundary
      region="application"
      onError={(error, info) => {
        // §18 asks for a Sentry-compatible hook. Until a reporter is wired, the
        // console is the honest destination — and it is where a developer will
        // look first regardless.
        console.error('[garh] unhandled render error', error, info.componentStack);
      }}
    >
      <ToastProvider>
        <AppBoot />
        <StoreToastBridge />
        <RouterProvider router={router} />
      </ToastProvider>
    </ErrorBoundary>
  );
}

export default App;
