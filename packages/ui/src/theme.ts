/**
 * theme.ts — light/dark switching.
 *
 * The UI package owns the *mechanism* (which class goes on <html>, how the OS
 * preference is read). The web app's `ui` Zustand store owns the *state* (what
 * the user picked, persisted). Keeping it split this way means packages/ui has
 * no store dependency and can be rendered in isolation by tests.
 */

export type Theme = 'light' | 'dark' | 'system';

export const THEME_STORAGE_KEY = 'garh.theme';

/** Resolve `'system'` against the OS setting. */
export function resolveTheme(theme: Theme): 'light' | 'dark' {
  if (theme !== 'system') return theme;
  if (typeof window === 'undefined' || !window.matchMedia) return 'light';
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

/** Put the resolved theme on <html>. Both hooks in tokens.css are set so the
 *  class strategy and a `[data-theme]` attribute selector agree. */
export function applyTheme(theme: Theme): 'light' | 'dark' {
  const resolved = resolveTheme(theme);
  if (typeof document === 'undefined') return resolved;
  const root = document.documentElement;
  root.classList.toggle('dark', resolved === 'dark');
  root.dataset['theme'] = resolved;
  return resolved;
}

/** Read the persisted choice; defaults to `'system'`. */
export function readStoredTheme(): Theme {
  if (typeof localStorage === 'undefined') return 'system';
  const raw = localStorage.getItem(THEME_STORAGE_KEY);
  return raw === 'light' || raw === 'dark' || raw === 'system' ? raw : 'system';
}

export function storeTheme(theme: Theme): void {
  if (typeof localStorage === 'undefined') return;
  localStorage.setItem(THEME_STORAGE_KEY, theme);
}

/**
 * Call once at boot (before React mounts, ideally from an inline module in
 * main.tsx) to avoid a flash of the wrong theme. Returns an unsubscribe for the
 * OS-preference listener that keeps `'system'` live.
 */
export function initTheme(): () => void {
  const theme = readStoredTheme();
  applyTheme(theme);
  if (typeof window === 'undefined' || !window.matchMedia) return () => undefined;
  const mq = window.matchMedia('(prefers-color-scheme: dark)');
  const onChange = (): void => {
    if (readStoredTheme() === 'system') applyTheme('system');
  };
  mq.addEventListener('change', onChange);
  return () => mq.removeEventListener('change', onChange);
}
