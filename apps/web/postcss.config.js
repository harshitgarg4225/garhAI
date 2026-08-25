/**
 * PostCSS pipeline. Two plugins, in this order, and nothing else:
 * Tailwind compiles the utility layer, autoprefixer targets whatever
 * `browserslist` resolves to (Vite's `build.target: es2022` is the real floor).
 *
 * ESM because both this package and the repo root declare `"type": "module"`.
 */
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
