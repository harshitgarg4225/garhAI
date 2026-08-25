/**
 * Tailwind config for `@garh/web` (playbook §12 panels, §15 tone & accessibility).
 *
 * THE PALETTE IS NOT DEFINED HERE. `@garh/ui` owns the semantic scale — colours,
 * radii, shadows, and the `rail`/`inspector`/`topbar`/`strip` chrome dimensions —
 * and ships it as `tailwind-preset.cjs`, resolving against the `--garh-*`
 * variables in `@garh/ui/tokens.css`. Every primitive in that package emits class
 * names from that scale (`border-line`, `bg-surface-muted`, `text-2xs`,
 * `h-topbar`, `bg-pass-soft`), so an app that declared a rival palette here would
 * compile a stylesheet in which half the shared components render unstyled. The
 * preset is the single source of truth; this file adds only what is specific to
 * the app shell.
 *
 * `content` deliberately includes `packages/ui/src`: Tailwind's JIT only emits
 * classes it can see, and the shared primitives live outside this app's tree.
 * The preset cannot declare that glob itself — content paths resolve relative to
 * the config file that owns them.
 */

import { createRequire } from 'node:module';

import type { Config } from 'tailwindcss';

/**
 * The preset is CommonJS (Tailwind 3.4 loads configs through `require`, and both
 * this package and the workspace root are `"type": "module"`). Resolving it with
 * `createRequire` rather than an `import` keeps it independent of the TypeScript
 * `paths` aliases, which map `@garh/ui/*` onto `packages/ui/src/*` and therefore
 * cannot see a file that lives at the package root.
 */
const require = createRequire(import.meta.url);
const garhPreset = require('@garh/ui/tailwind-preset') as Config;

const config: Config = {
  presets: [garhPreset],

  content: [
    './index.html',
    './src/**/*.{ts,tsx}',
    // REQUIRED — see the note above.
    '../../packages/ui/src/**/*.{ts,tsx}',
  ],

  theme: {
    extend: {
      /**
       * Canvas stacking order. The preset stops at `rail`/`topbar`/`popover`/
       * `dialog`/`toast`, because those are the only layers a shared primitive
       * knows about. The drawing surface needs its own scale underneath them,
       * and the 2D and 3D layers (Phases 4–5) must agree on it rather than each
       * inventing a z-index.
       */
      zIndex: {
        canvas: '0',
        'canvas-overlay': '5',
        'canvas-handles': '10',
      },
    },
  },

  plugins: [],
};

export default config;
