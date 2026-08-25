/**
 * Vite config for `@garh/web` (playbook §12, §14).
 *
 * Four things here are load-bearing, and each one is a budget or a contract:
 *
 * 1. **Aliases mirror `tsconfig.base.json` `paths` exactly.** `@garh/model` is
 *    consumed as TypeScript SOURCE (its package.json `main`/`types` point at
 *    `src/index.ts`), so the editor, `tsc`, Vite and Vitest must all resolve it
 *    the same way or the app compiles against one copy and bundles another.
 *
 * 2. **`envDir` is the repo root.** There is exactly one `.env` in this
 *    monorepo (see `.env.example`); pointing Vite at `apps/web` would silently
 *    fall back to defaults for every client value.
 *
 * 3. **Manual chunks keep the initial bundle under the §14 budget** (<1.5 MB
 *    gzipped). `three` + `@react-three/*` is by far the heaviest dependency and
 *    is only reachable from the lazily-loaded Plan/3D tabs; splitting it into
 *    its own chunk means the two tabs share one download instead of duplicating
 *    it, and the dashboard never pays for it at all.
 *
 * 4. **No `process.env`.** ESLint bans it in `apps/web/**` (§13: only
 *    `import.meta.env.VITE_*` may reach the browser) and `make secret-audit`
 *    enforces the same rule on the built output. Config-time values come from
 *    Vite's own `loadEnv`, which is prefix-filtered by construction.
 */

import { fileURLToPath } from 'node:url';

import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vitest/config';

/** `apps/web/` — this file's directory. `__dirname` does not exist in ESM. */
const appDir = fileURLToPath(new URL('.', import.meta.url));
/** Repo root: where `.env`, `pnpm-workspace.yaml` and `tsconfig.base.json` live. */
const repoRoot = fileURLToPath(new URL('../../', import.meta.url));
const packagesDir = fileURLToPath(new URL('../../packages/', import.meta.url));

const DEFAULT_PORT = 5173;
const DEFAULT_HMR_PORT = 24678;
const DEFAULT_PREVIEW_PORT = 4173;

function toPort(value: string | undefined, fallback: number): number {
  const n = Number.parseInt(value ?? '', 10);
  return Number.isInteger(n) && n > 0 && n < 65536 ? n : fallback;
}

function isTrue(value: string | undefined): boolean {
  return value === 'true' || value === '1';
}

export default defineConfig(({ mode }) => {
  // Prefix-filtered on purpose: `VITE_` is the client contract, and the other
  // two prefixes are container plumbing that never reaches the bundle.
  const env = loadEnv(mode, repoRoot, ['VITE_', 'CHOKIDAR_', 'WEB_']);

  const hmrPort = toPort(env.VITE_HMR_PORT ?? env.WEB_HMR_PORT, DEFAULT_HMR_PORT);
  // Bind-mounted source on macOS/Windows delivers no inotify events, so the dev
  // server never rebuilds without polling. docker-compose sets this for us.
  const usePolling = isTrue(env.CHOKIDAR_USEPOLLING);

  return {
    root: appDir,
    envDir: repoRoot,
    // Default, stated explicitly: this prefix IS the §13 secrets boundary.
    envPrefix: 'VITE_',

    plugins: [react()],

    resolve: {
      alias: {
        // Directory aliases: `@garh/model` resolves to `src/index.ts` and
        // `@garh/model/units` to `src/units.ts`. Keep in lockstep with the
        // `paths` block in tsconfig.base.json.
        '@garh/model': `${packagesDir}model/src`,
        '@garh/ui': `${packagesDir}ui/src`,
      },
      // One React instance. Two copies (via a workspace link) produce the
      // "invalid hook call" crash that costs an afternoon to diagnose.
      dedupe: ['react', 'react-dom', 'three'],
    },

    server: {
      host: true, // 0.0.0.0 — the container publishes this port
      port: DEFAULT_PORT,
      strictPort: true,
      // The browser connects to the HMR socket directly from the host, so it
      // needs its own published port rather than tunnelling through 5173.
      hmr: { port: hmrPort, clientPort: hmrPort },
      watch: usePolling ? { usePolling: true, interval: 300 } : undefined,
      fs: {
        // The workspace packages live outside `root`.
        allow: [repoRoot],
      },
    },

    preview: {
      host: true,
      port: DEFAULT_PREVIEW_PORT,
      strictPort: true,
    },

    build: {
      target: 'es2022',
      outDir: 'dist',
      emptyOutDir: true,
      sourcemap: true,
      // §14: initial bundle <1.5 MB gz. 900 kB uncompressed per chunk is the
      // tripwire — a chunk over it is a review conversation, not a warning to
      // scroll past.
      chunkSizeWarningLimit: 900,
      rollupOptions: {
        output: {
          manualChunks(id: string): string | undefined {
            if (!id.includes('node_modules')) return undefined;
            if (/[\\/]node_modules[\\/](three|@react-three)[\\/]/.test(id)) return 'three';
            if (/[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id)) return 'react';
            if (/[\\/]node_modules[\\/](react-router|react-router-dom|@remix-run)[\\/]/.test(id)) {
              return 'router';
            }
            return undefined;
          },
        },
      },
    },

    test: {
      environment: 'jsdom',
      globals: false,
      include: ['src/**/*.{test,spec}.{ts,tsx}'],
      restoreMocks: true,
      // Store + client tests are pure logic; a browser-shaped environment is
      // only here so `sessionStorage` and `AbortController` behave as they do
      // in the app rather than as Node approximations.
      coverage: { provider: 'v8', reportsDirectory: 'coverage' },
    },
  };
});
