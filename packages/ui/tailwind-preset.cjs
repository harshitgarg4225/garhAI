/**
 * Garh AI Tailwind preset — the semantic scale every app extends.
 *
 * Usage (apps/web/tailwind.config.cjs, owned by the web-shell agent):
 *
 *   module.exports = {
 *     presets: [require('@garh/ui/tailwind-preset')],
 *     content: [
 *       './index.html',
 *       './src/** /*.{ts,tsx}',
 *       '../../packages/ui/src/** /*.{ts,tsx}',   // <- REQUIRED, see note below
 *     ],
 *   };
 *
 * The preset deliberately does NOT declare `content`. Tailwind resolves content
 * globs relative to the config file that owns them, and a preset shipped from
 * another package cannot know where that file will live. The consuming app must
 * add the `packages/ui/src` glob itself or every class this package emits gets
 * tree-shaken out of the stylesheet.
 *
 * CommonJS (`.cjs`) because Tailwind 3.4 loads configs through `require`, and
 * the workspace root sets `"type": "module"`.
 */

/** rgb() with Tailwind's alpha slot, so `bg-brand/10` works. */
function v(name) {
  return `rgb(var(${name}) / <alpha-value>)`;
}

/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        canvas: v('--garh-canvas'),
        scrim: v('--garh-scrim'),
        surface: {
          DEFAULT: v('--garh-surface'),
          muted: v('--garh-surface-muted'),
          sunken: v('--garh-surface-sunken'),
        },
        line: {
          DEFAULT: v('--garh-line'),
          strong: v('--garh-line-strong'),
        },
        ink: {
          DEFAULT: v('--garh-ink'),
          muted: v('--garh-ink-muted'),
          subtle: v('--garh-ink-subtle'),
          inverse: v('--garh-ink-inverse'),
        },
        brand: {
          DEFAULT: v('--garh-brand'),
          strong: v('--garh-brand-strong'),
          soft: v('--garh-brand-soft'),
          fg: v('--garh-brand-fg'),
          ink: v('--garh-brand-ink'),
        },
        pass: {
          DEFAULT: v('--garh-pass'),
          soft: v('--garh-pass-soft'),
          line: v('--garh-pass-line'),
          ink: v('--garh-pass-ink'),
        },
        warn: {
          DEFAULT: v('--garh-warn'),
          soft: v('--garh-warn-soft'),
          line: v('--garh-warn-line'),
          ink: v('--garh-warn-ink'),
        },
        fail: {
          DEFAULT: v('--garh-fail'),
          soft: v('--garh-fail-soft'),
          line: v('--garh-fail-line'),
          ink: v('--garh-fail-ink'),
        },
        info: {
          DEFAULT: v('--garh-info'),
          soft: v('--garh-info-soft'),
          line: v('--garh-info-line'),
          ink: v('--garh-info-ink'),
        },
        neutral: {
          soft: v('--garh-neutral-soft'),
          line: v('--garh-neutral-line'),
          ink: v('--garh-neutral-ink'),
        },
        focus: v('--garh-focus'),
      },
      borderRadius: {
        // A quiet, drafting-adjacent radius scale. Nothing rounder than 12px
        // except pills — this is a tool, not a consumer app.
        sm: '4px',
        DEFAULT: '6px',
        md: '8px',
        lg: '12px',
        xl: '16px',
      },
      boxShadow: {
        sm: 'var(--garh-shadow-sm)',
        DEFAULT: 'var(--garh-shadow-sm)',
        md: 'var(--garh-shadow-md)',
        lg: 'var(--garh-shadow-lg)',
      },
      fontFamily: {
        sans: [
          'Inter var',
          'Inter',
          'system-ui',
          '-apple-system',
          'Segoe UI',
          'Roboto',
          'Noto Sans',
          'Noto Sans Devanagari',
          'sans-serif',
        ],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      fontSize: {
        // 11px exists for drawing-adjacent chrome (dim labels, sheet numbers).
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      spacing: {
        rail: '3.25rem', // left tool rail (§12)
        inspector: '20rem', // right inspector (§12)
        topbar: '3.5rem',
        strip: '2.75rem', // bottom compliance chip strip
      },
      zIndex: {
        rail: '20',
        topbar: '30',
        popover: '40',
        dialog: '50',
        toast: '60',
      },
      keyframes: {
        'garh-fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'garh-pop-in': {
          from: { opacity: '0', transform: 'translateY(4px) scale(0.98)' },
          to: { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        'garh-slide-in-right': {
          from: { opacity: '0', transform: 'translateX(12px)' },
          to: { opacity: '1', transform: 'translateX(0)' },
        },
        'garh-shimmer': {
          '100%': { transform: 'translateX(100%)' },
        },
        // Indeterminate progress: a sweep that never pretends to be a
        // percentage. §15 forbids a fake bar that creeps to 90% and waits.
        'garh-indeterminate': {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(400%)' },
        },
        'garh-spin': {
          to: { transform: 'rotate(360deg)' },
        },
      },
      animation: {
        'fade-in': 'garh-fade-in 120ms ease-out',
        'pop-in': 'garh-pop-in 140ms cubic-bezier(0.16, 1, 0.3, 1)',
        'slide-in-right': 'garh-slide-in-right 180ms cubic-bezier(0.16, 1, 0.3, 1)',
        shimmer: 'garh-shimmer 1.6s infinite',
        indeterminate: 'garh-indeterminate 1.5s ease-in-out infinite',
        spin: 'garh-spin 0.8s linear infinite',
      },
    },
  },
  plugins: [],
};
