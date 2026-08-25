// ESLint 9 flat config for the whole workspace.
// Run from the repo root: `pnpm lint` / `make lint-js`.
//
// Type-aware linting is on (projectService), which is what lets the
// no-floating-promises / no-misused-promises rules actually work — those catch
// the "optimistic op dispatched but never awaited, rollback never runs" class
// of bug in the model store.

import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import prettier from 'eslint-config-prettier';
import globals from 'globals';

export default tseslint.config(
  // ─── Never lint generated or vendored trees ────────────────────────────────
  {
    ignores: [
      '**/node_modules/**',
      '**/dist/**',
      '**/build/**',
      '**/.vite/**',
      '**/coverage/**',
      '**/playwright-report/**',
      '**/test-results/**',
      '**/*.tsbuildinfo',
      // Python side is Ruff's job.
      'apps/api/**',
      'services/**',
      // Golden artefacts and rule packs are data, not code.
      'fixtures/**',
      'rulepacks/**',
    ],
  },

  js.configs.recommended,
  ...tseslint.configs.recommendedTypeChecked,
  ...tseslint.configs.stylisticTypeChecked,

  // ─── Baseline for all TypeScript in the workspace ──────────────────────────
  {
    files: ['**/*.{ts,tsx,mts,cts}'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      parserOptions: {
        // Every .ts/.tsx file linted with TYPE information must belong to a
        // tsconfig that TypeScript's ProjectService can find by itself — and it
        // only ever looks for files literally named `tsconfig.json` while walking
        // up from the source file. A file whose only home is a differently-named
        // config (apps/web/tsconfig.node.json) is therefore "not found by the
        // project service" and every type-aware rule errors before linting
        // starts. The build-config files are excluded from type-aware linting
        // further down for exactly that reason; everything else resolves:
        //   apps/web/src/**            → apps/web/tsconfig.json
        //   packages/{model,ui}/src/** → that package's tsconfig.json
        //   packages/ui/vitest.config.ts → packages/ui/tsconfig.json (in include)
        //   e2e/**                     → e2e/tsconfig.json (include **/*.ts)
        projectService: true,
        // `import.meta.dirname` requires Node >= 20.11; `engines.node` pins that.
        tsconfigRootDir: import.meta.dirname,
      },
      globals: { ...globals.browser, ...globals.node },
    },
    rules: {
      // Unused code is dead weight in a bundle we hold to <1.5MB gz (§14).
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' },
      ],

      // Ops cross the network; a dropped rejection means a silently lost edit.
      '@typescript-eslint/no-floating-promises': 'error',
      '@typescript-eslint/no-misused-promises': 'error',
      '@typescript-eslint/await-thenable': 'error',
      '@typescript-eslint/require-await': 'error',

      // `any` erases the integer-mm types the whole model core depends on.
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-unsafe-assignment': 'error',
      '@typescript-eslint/no-unsafe-member-access': 'error',
      '@typescript-eslint/no-unsafe-call': 'error',
      '@typescript-eslint/no-unsafe-return': 'error',
      '@typescript-eslint/no-unsafe-argument': 'error',

      '@typescript-eslint/consistent-type-imports': [
        'error',
        { prefer: 'type-imports', fixStyle: 'inline-type-imports' },
      ],
      '@typescript-eslint/no-non-null-assertion': 'error',
      '@typescript-eslint/explicit-module-boundary-types': 'off',

      eqeqeq: ['error', 'always', { null: 'ignore' }],
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      'no-debugger': 'error',
      'prefer-const': 'error',
      'no-var': 'error',
      'object-shorthand': ['error', 'properties'],

      // §13: secrets never reach the client bundle. `process.env` in browser
      // code is either dead (Vite does not define it) or a leak; the audited
      // channel is `import.meta.env.VITE_*`. Enforced hard in apps/web below.
      'no-restricted-globals': [
        'error',
        { name: 'event', message: 'Use the explicit event parameter instead of the global.' },
      ],
    },
  },

  // ─── Geometry is integer millimetres, everywhere ───────────────────────────
  // Golden rule 6 / SKILL.md locked decision: no float lengths. Math.round is
  // the only sanctioned float→mm boundary; the rest of these silently produce
  // sub-millimetre drift that breaks dimension-chain sum assertions (§7.5).
  {
    files: ['packages/model/**/*.ts'],
    rules: {
      'no-restricted-properties': [
        'error',
        {
          object: 'Math',
          property: 'random',
          message:
            'Model core must be deterministic — seed a PRNG and pass it in (see §5.5 diversity seeds).',
        },
        {
          object: 'Math',
          property: 'floor',
          message:
            'Rounding a length? Use the shared mm helpers so every call site rounds identically (units.ts, golden-tested against units.py).',
        },
        {
          object: 'Math',
          property: 'ceil',
          message:
            'Rounding a length? Use the shared mm helpers so every call site rounds identically (units.ts, golden-tested against units.py).',
        },
      ],
      'no-restricted-syntax': [
        'error',
        {
          selector: 'Literal[raw=/^[0-9]+\\.[0-9]+$/]',
          message:
            'Float literal in the model core. Geometry is integer millimetres — express this as mm (e.g. 2750 not 2.75).',
        },
      ],
    },
  },

  // ─── Frontend ──────────────────────────────────────────────────────────────
  {
    files: ['apps/web/**/*.{ts,tsx}'],
    plugins: { 'react-hooks': reactHooks, 'react-refresh': reactRefresh },
    languageOptions: { globals: globals.browser },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],

      // The VITE_-prefix audit (§13, `make secret-audit`) is the CI gate; this
      // is the same rule enforced at author time so it fails in the editor.
      'no-restricted-properties': [
        'error',
        {
          object: 'process',
          property: 'env',
          message:
            'Client bundle: use import.meta.env.VITE_* only. Non-VITE_ vars are server/worker-only secrets (§13).',
        },
      ],
    },
  },

  // ─── Tests: loosen the type-safety rules that fight fixtures/mocks ─────────
  {
    files: [
      '**/*.{test,spec}.{ts,tsx}',
      '**/__tests__/**/*.{ts,tsx}',
      'e2e/**/*.{ts,tsx}',
      '**/*.fixture.ts',
    ],
    rules: {
      '@typescript-eslint/no-non-null-assertion': 'off',
      '@typescript-eslint/no-unsafe-assignment': 'off',
      '@typescript-eslint/no-unsafe-member-access': 'off',
      '@typescript-eslint/no-unsafe-argument': 'off',
      'no-console': 'off',
    },
  },

  // ─── Config files run in Node before any build step ───────────────────────
  {
    files: ['**/*.config.{ts,js,mjs}', '**/vite.config.ts', '**/vitest.config.ts'],
    languageOptions: { globals: globals.node },
    rules: {
      'no-console': 'off',
      '@typescript-eslint/no-unsafe-call': 'off',
    },
  },

  // ─── Build-config TypeScript is linted WITHOUT type information ────────────
  // `apps/web/{vite,tailwind}.config.ts` live only in `apps/web/tsconfig.node.json`,
  // which TypeScript's ProjectService never discovers (it looks for `tsconfig.json`
  // by name only). Left type-aware, `pnpm exec eslint .` fails on those two files
  // with "was not found by the project service" before a single rule runs —
  // and `apps/web`'s own `typecheck` script already passes `-p tsconfig.node.json`,
  // so the type coverage is not lost, only the type-aware *lint* rules are.
  //
  // Chosen over `allowDefaultProject`, which would silently typecheck these files
  // against synthetic compiler options rather than the ones they actually build with.
  {
    files: ['**/*.config.{ts,mts,cts}'],
    ...tseslint.configs.disableTypeChecked,
  },

  // Plain JS (this file included) has no type information to work from.
  {
    files: ['**/*.{js,mjs,cjs}'],
    ...tseslint.configs.disableTypeChecked,
  },

  // Must stay last: turns off every rule Prettier already owns.
  prettier,
);
