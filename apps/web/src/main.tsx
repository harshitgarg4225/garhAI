/**
 * Browser entry point.
 *
 * The only script `index.html` loads, which is what lets a production CSP ship
 * `script-src 'self'` with no nonce and no `'unsafe-inline'` (§13). Nothing here
 * may become an inline `<script>`, and nothing here may read configuration from
 * anywhere but `lib/env.ts`.
 *
 * Order matters in exactly two places:
 *
 *  1. **`@garh/ui/tokens.css` before `./index.css`.** The tokens declare the
 *     `--garh-*` variables that `tailwind.config.ts` maps every semantic class
 *     onto; the app stylesheet's `@tailwind base` layer consumes them.
 *  2. **`initTheme()` before `createRoot`.** It puts `dark` / `data-theme` on
 *     `<html>` synchronously, so the first paint is already in the right theme.
 *     Doing it in an effect gives a visible flash of the wrong one.
 */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { initTheme } from '@garh/ui';
import '@garh/ui/tokens.css';
import './index.css';

import { App } from './App';
import { env } from './lib/env';
import { installSessionWatcher } from './stores/session';

/**
 * Wire the transport's "your session is gone" signal into the session store.
 *
 * A registration rather than an import-time side effect inside `lib/http.ts`,
 * because that would make the HTTP layer depend on a Zustand store and drag
 * React into every client test.
 */
installSessionWatcher();

/**
 * Theme, applied before React mounts. The returned unsubscribe keeps `'system'`
 * live as the OS setting changes; it is never called, because the listener
 * lives exactly as long as the page does.
 */
initTheme();

/**
 * Last-resort logging (§18 "Sentry-compatible error hook").
 *
 * The error boundary in `App.tsx` catches render-time throws; these two catch
 * what escapes it — a rejected promise nobody awaited, an error inside a
 * non-React callback. Logging is all they do: a toast for an error the app has
 * already survived is noise, and one for an error it has not survived will be
 * covered by the boundary anyway.
 */
window.addEventListener('error', (event) => {
  console.error('[garh] uncaught error', event.error ?? event.message);
});
window.addEventListener('unhandledrejection', (event) => {
  console.error('[garh] unhandled rejection', event.reason);
});

const container = document.getElementById('root');
if (!container) {
  // `index.html` ships an empty `#root`. If it is missing, the document being
  // served is not this app's, and failing loudly beats rendering into nothing.
  throw new Error('Garh AI could not start: no #root element in the document.');
}

document.title = env.appName;

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
