/// <reference types="vite/client" />

/**
 * The complete set of build-time values this client may read.
 *
 * §13: only `VITE_`-prefixed values may exist here, because everything in this
 * interface is compiled into a public JavaScript bundle and is readable by
 * anyone who opens devtools. `make secret-audit` fails the build on any other
 * `import.meta.env.*` access in `apps/web/`, and ESLint bans `process.env`
 * outright in this package.
 *
 * Adding a field here is a two-file change: this interface AND the CLIENT
 * BUNDLE section of `.env.example`. Read the values through `src/lib/env.ts`,
 * never directly — that module validates them once, at boot, so a missing or
 * malformed value is a loud startup failure instead of `undefined` leaking into
 * a URL three screens later.
 */
interface ImportMetaEnv {
  /** Absolute or origin-relative base for the API, e.g. `/api/v1`. */
  readonly VITE_API_BASE_URL?: string;
  /** Product name shown in the title bar and share messages. */
  readonly VITE_APP_NAME?: string;
  /** Public origin of this app; used to build share links. */
  readonly VITE_APP_URL?: string;
  /** `'true'` enables in-app devtools panels. Never enabled in production. */
  readonly VITE_ENABLE_DEVTOOLS?: string;
  /** `'ft-in'` | `'m'` — the display default before a project overrides it. */
  readonly VITE_DEFAULT_UNITS?: string;
  /** Browser error-reporting endpoint. Public by design (it is a write-only ingest URL). */
  readonly VITE_SENTRY_DSN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
