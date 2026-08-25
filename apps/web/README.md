# `@garh/web`

Vite 5 + React 18 + TypeScript strict. The app shell, the Zustand stores and the
typed API client. UI primitives come from `@garh/ui`; the model core (integer-mm
geometry, the 32 ops, `fold`) from `@garh/model`. Both are consumed as
**TypeScript source** — neither has a build step, Vite bundles them through the
`resolve.alias` entries in `vite.config.ts`.

```bash
pnpm --filter @garh/web dev        # http://localhost:5173
pnpm --filter @garh/web typecheck  # tsconfig.json AND tsconfig.node.json
pnpm --filter @garh/web test       # vitest
pnpm --filter @garh/web build      # typecheck, then bundle to dist/
```

## Layout

| Path | Owns |
|---|---|
| `src/lib/` | env, errors (`AppError`), tokens, `HttpClient`, zod schemas, the `api` client, SSE, units, keymap, share helpers |
| `src/stores/` | `useSessionStore` `useProjectStore` `useModelStore` `useSelectionStore` `useJobsStore` `useUiStore` — **`model` is the only writer of the document** |
| `src/components/` | app shell, project layout, inspector, compliance strip, dialogs |
| `src/pages/` | login, dashboard, project shell + the six project tabs |
| `public/` | see below |
| `nginx.conf` | the `prod` image's server config: SPA fallback + §13 headers |

## Two tsconfigs, on purpose

`tsconfig.json` covers `src/` (DOM + `vite/client` types). `tsconfig.node.json`
covers `vite.config.ts`, `tailwind.config.ts` and `postcss.config.js`, which run
in Node before a bundle exists and may use `node:` builtins the app config
deliberately cannot see. The `typecheck` script runs both — a config file that
only one of them covers is a config file nothing typechecks.

Consequence worth knowing: TypeScript's ProjectService (which type-aware ESLint
uses) only auto-discovers files literally named `tsconfig.json`, so it never
finds `tsconfig.node.json`. `eslint.config.js` therefore lints `**/*.config.ts`
**without** type information. Type coverage is not lost — `tsc -p tsconfig.node.json`
still checks those files — only the type-aware lint rules are.

## `public/`

Vite copies this directory to the bundle root verbatim: no hashing, no
transform. Only files referenced by absolute path from `index.html` (or fetched
by the browser unprompted) belong here; anything TypeScript imports should live
under `src/` so Vite fingerprints it.

| File | Referenced by | Notes |
|---|---|---|
| `favicon.svg` | `<link rel="icon">` | Hand-drawn on the same 32-unit grid as `packages/ui/src/icons.tsx`. Colours are literal hex, not `--garh-*` — a favicon document gets no CSS custom properties from the page. A `prefers-color-scheme` block keeps it visible in dark browser chrome. |
| `apple-touch-icon.png` | `<link rel="apple-touch-icon">` | 180×180 opaque RGB; iOS ignores alpha and rounds the corners itself. |

Both were missing while `index.html` linked them, so every page load produced
two 404s — noise that teaches a developer to ignore 404s.

Deliberately absent: **`robots.txt`** (`index.html` already carries
`<meta name="robots">` and `nginx.conf` sends `X-Robots-Tag`; a third copy of one
statement is a third thing to desynchronise) and a **web app manifest** (Garh AI
is not installable in MVP, and a manifest with no service worker advertises a
capability that does not exist).

## Security notes that live in code, not here

* Only `import.meta.env.VITE_*` may be read, and only in `src/lib/env.ts`
  (`make secret-audit` is the gate; `eslint.config.js` bans `process.env` in this
  package so it also fails in the editor).
* `index.html` has **no inline script**, which is what lets `nginx.conf` ship
  `script-src 'self'` with no nonce and no `'unsafe-inline'`.
* Every API response is zod-parsed before a store sees it; a shape drift becomes
  a `malformed_response` `AppError` with a request id, never a silent `undefined`.
