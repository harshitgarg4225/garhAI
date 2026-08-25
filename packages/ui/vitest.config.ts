/**
 * Vitest config for @garh/ui.
 *
 * The tests here are deliberately DOM-FREE. Every one of them exercises pure
 * logic — the Tailwind class merger, the +91 phone normaliser, the WhatsApp
 * deep link, the score bands, the icon table — so the suite needs no jsdom and
 * adds no dependency beyond vitest itself, which the pinned toolchain already
 * carries. Rendering assertions belong in the Playwright suite (`e2e`), where a
 * real browser tells the truth about focus rings, tab order and contrast in a
 * way a simulated DOM does not.
 *
 * `environment: 'node'` is therefore not a limitation but the point: it keeps
 * `window`, `document` and `localStorage` undefined, which is exactly the
 * condition the SSR-safe guards in `theme.ts` and the portal components claim
 * to handle. `theme.test.ts` asserts that claim.
 */

import { defineConfig } from 'vitest/config';

export default defineConfig({
  // The primitives are .tsx; the automatic JSX runtime keeps `import React`
  // out of every file. Set explicitly rather than relying on the default so a
  // future Vite major cannot change it underneath the suite.
  esbuild: { jsx: 'automatic' },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
    // No globals: every test imports `describe`/`it`/`expect` explicitly, which
    // is what makes the files readable in isolation and type-check without a
    // `types` entry in tsconfig.
    globals: false,
  },
});
