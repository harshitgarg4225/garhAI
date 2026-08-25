/**
 * Build-time configuration, validated once at module load.
 *
 * Every value the browser is allowed to know arrives through `import.meta.env`
 * and is `VITE_`-prefixed (§13). Reading them here — literally, one property
 * access per key — matters for two reasons:
 *
 *  1. Vite performs a STATIC text replacement of `import.meta.env.VITE_FOO`.
 *     A computed access (`import.meta.env[key]`) is not replaced and evaluates
 *     to `undefined` in the production bundle while working perfectly in dev.
 *  2. `make secret-audit` greps for exactly this pattern, so keeping all env
 *     access in one file makes the audit's output a useful diff rather than a
 *     scatter of hits.
 *
 * A malformed value throws at import time. That is deliberate: a bad API base
 * URL should stop the app at boot with a readable message, not produce a
 * request to `undefined/projects` twenty seconds into a session.
 */

import { z } from 'zod';

import type { UnitsDisplay } from '@garh/model';

/** `'true'`/`'1'` → true; anything else (including absent) → false. */
const boolish = z
  .string()
  .optional()
  .transform((v) => v === 'true' || v === '1');

const envSchema = z.object({
  /** Trailing slashes are stripped so callers can always join with `/path`. */
  apiBaseUrl: z
    .string()
    .min(1)
    .default('/api/v1')
    .transform((v) => v.replace(/\/+$/, '')),
  appName: z.string().min(1).default('Garh AI'),
  /** Empty means "derive from window.location" — see {@link appOrigin}. */
  appUrl: z
    .string()
    .default('')
    .transform((v) => v.replace(/\/+$/, '')),
  enableDevtools: boolish,
  defaultUnits: z.enum(['ft-in', 'm']).default('ft-in'),
  errorReportingDsn: z.string().default(''),
});

export type AppEnv = z.infer<typeof envSchema> & {
  readonly mode: string;
  readonly isDev: boolean;
  readonly isProd: boolean;
};

function readEnv(): AppEnv {
  const parsed = envSchema.safeParse({
    apiBaseUrl: import.meta.env.VITE_API_BASE_URL,
    appName: import.meta.env.VITE_APP_NAME,
    appUrl: import.meta.env.VITE_APP_URL,
    enableDevtools: import.meta.env.VITE_ENABLE_DEVTOOLS,
    defaultUnits: import.meta.env.VITE_DEFAULT_UNITS,
    errorReportingDsn: import.meta.env.VITE_SENTRY_DSN,
  });

  if (!parsed.success) {
    const detail = parsed.error.issues
      .map((i) => `VITE_${i.path.join('.')}: ${i.message}`)
      .join('; ');
    throw new Error(
      `Garh AI cannot start: the client configuration is invalid (${detail}). ` +
        'Check the CLIENT BUNDLE section of .env.example.',
    );
  }

  return {
    ...parsed.data,
    mode: import.meta.env.MODE,
    isDev: import.meta.env.DEV,
    // Devtools are opt-in AND never available in a production build, so a
    // misconfigured deployment cannot expose an internal panel.
    isProd: import.meta.env.PROD,
  };
}

const resolved = readEnv();

export const env: AppEnv = Object.freeze({
  ...resolved,
  enableDevtools: resolved.enableDevtools && !resolved.isProd,
});

/** Display units before a project states its own preference (§15: ft-in first). */
export const DEFAULT_UNITS_DISPLAY: UnitsDisplay = env.defaultUnits;

/**
 * The origin this app is served from. Prefers the configured value (correct
 * behind a proxy that rewrites Host) and falls back to the live location, which
 * is what makes share links work in a preview deployment nobody configured.
 */
export function appOrigin(): string {
  if (env.appUrl) return env.appUrl;
  if (typeof window !== 'undefined') return window.location.origin;
  return '';
}

/** Absolute URL for an in-app path — used for share links and WhatsApp messages. */
export function appUrlFor(path: string): string {
  const origin = appOrigin();
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `${origin}${suffix}`;
}
