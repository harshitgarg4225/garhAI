# Phase 0 verification — what was traced, what was fixed, what is still unproven

*Written 2026-08-04, at the end of a whole-repo audit and repair pass.*

Phase 0's Definition of Done is one sentence:

> `docker compose up` → login → create empty project; CI green; a cross-tenant
> access attempt test proves 404/403.

**Phase 0's DoD is NOT met.** Not because the code is missing — it is written, and
this pass fixed several things that would have broken the path — but because
**nothing in this repository has ever been executed**. Every machine it was
authored on had no Docker, no Node, no pnpm and no Python 3.11; only Python 3.9.6.

This document exists so that nobody has to guess which claims are observations and
which are readings. Its value is entirely in its honesty. If you catch it
over-claiming, that is a bug in the document, and a serious one.

---

## 1. What "verified" means here, exactly

Three tiers are used below, and nothing is ever promoted without doing the work:

| Tier | Meaning |
|---|---|
| **EXECUTED** | A command actually ran on the authoring machine and produced the stated output. Only Python 3.9.6 stdlib and GNU Make 3.81 were available, so this tier is small. |
| **TRACED** | A human read every file on the path and checked the contract by hand or with a purpose-written script (AST import resolution, byte-level schema comparison, transliterated algorithms). Strong evidence, not proof. |
| **UNVERIFIED** | Nobody has run it and nobody could. Stated plainly, with the command that would settle it. |

---

## 2. The `docker compose up → login → create empty project` trace

Followed step by step, file by file. Each row says what was checked and how.

### 2.1 `docker compose up`

| Step | Finding | Tier |
|---|---|---|
| Compose project name | `name: garh-ai` is set explicitly. Required: the checkout directory is `Garh AI`, and Compose cannot derive a legal project name from it — without this the stack fails before starting a container. | TRACED |
| Compose version floor | `env_file: { path, required: false }` needs Compose **v2.24+**. Stated in the README prerequisites. | TRACED |
| `postgres` / `redis` / `minio` | Pinned images, healthchecks with sane `start_period`, named volumes. `minio-init` is a one-shot `mc` container that creates the bucket and exits; the API waits on `service_completed_successfully`. | TRACED |
| `api` image build | `apps/api/Dockerfile` extracts pinned deps from `apps/api/pyproject.toml` with stdlib `tomllib` rather than `pip install .` (hatchling declares packages that may not all exist at build time). | TRACED |
| **`worker-render` boot** | **WAS BROKEN, NOW FIXED (before this pass).** `services/Dockerfile` pinned `ortools` and `ezdxf` inline and never installed `pillow`, while the **default** `PROVIDER_RENDER=mock` path imports `PIL` at module scope. Both the Dockerfile and `ci.yml` now extract from `services/pyproject.toml` too. | TRACED |
| JWT keys | If `JWT_PRIVATE_KEY` is empty, the API container mints a 2048-bit dev keypair with `openssl`, collapses the PEMs to one line with `awk 'BEGIN{ORS="\\n"}1"'`, and exports them — `config.py` un-escapes the literal `\n`. The full round trip (generate → collapse → un-escape → `openssl pkey`) was **EXECUTED** during the scaffold. | EXECUTED |
| Migrations on boot | `API_MIGRATE_ON_BOOT` defaults to `true`; the command runs `alembic upgrade head` from `working_dir: /app/apps/api`. `alembic.ini` deliberately has no `sqlalchemy.url`; `migrations/env.py` builds it from `DATABASE_URL`. | TRACED |
| `/healthz` | `routers/health.py` mounts at the **root**, no prefix. The compose healthcheck curls `http://localhost:8000/healthz`, and `curl` is installed explicitly in both images (the slim bases ship neither curl nor wget). | TRACED |
| **`web` image build** | **WAS BROKEN, NOW FIXED (before this pass).** `COPY pnpm-lock.yaml` is a hard error when the file is absent; it is now `COPY pnpm-lock.yaml*` with `--prefer-frozen-lockfile`. The `build` stage also ran `pnpm --filter @garh/model build`, and that package declares no `build` script (`ERR_PNPM_NO_SCRIPT`) — removed, because it is consumed as source. | TRACED |
| `web` dev server | `vite.config.ts` sets `server.host: true`, port 5173, and a separate HMR port from `VITE_HMR_PORT` (published separately, because the browser connects to it directly from the host). Aliases mirror `tsconfig.base.json` `paths`. | TRACED |
| **`e2e` in the web container** | **FIXED THIS PASS.** `./e2e:/app/e2e` shadowed the `node_modules` the image had installed for that workspace member. Added the `web-e2e-modules` named volume. Cosmetic — nothing in that container runs Playwright — but a half-installed workspace is a confusing thing to debug into. | TRACED |

### 2.2 login

| Step | Finding | Tier |
|---|---|---|
| `/api/v1/auth/*` is mounted | An earlier review reported this as a blocker: `routers.api_router()` does not include `auth.router`. **That report was correct about `api_router()` and wrong about the outcome** — `main.py` mounts `auth_router.router` separately at `cfg.api_prefix`, and `routers/auth.py` carries its own `/auth` prefix. The routes exist. Reported as a misread. | TRACED |
| The refresh cookie's path | Scoped to `{api_prefix}/auth`, so auth **must** stay mounted under `/api/v1` — it is, at `main.py`. | TRACED |
| A user exists to log in as | Fresh database → no firm. `POST /auth/signup` creates one. **WAS BROKEN, NOW FIXED (before this pass):** the web client had no `signup` method and `LoginPage` never called one, so a fresh install dead-ended; worse, the store forwarded `name`/`firmName` to `/auth/verify`, whose model is `extra="forbid"` and would have 422'd. `LoginPage` now has a third `signup` step. | TRACED |
| The OTP reaches the developer | `DEV_ECHO_OTP` returns the code as `devCode` and the login screen shows it. Double-gated: `APP_ENV in (dev,test)` **and** the variable not explicitly falsy. **FIXED THIS PASS:** it was undocumented in `.env.example` — the single thing that makes "no mail provider" workable was invisible. Documenting it also exposed a hole in `scripts/check_env_drift.py`, which only recognised `os.environ.get("LITERAL")` and not the better `os.environ.get(CONSTANT)` form; that is fixed too. | TRACED |
| CORS + credentials | `allow_origins` defaults to `http://localhost:5173`, `allow_credentials=True`, never `*`. The client sends `credentials: 'include'`. `localhost:5173` and `localhost:8000` differ in port only, and SameSite ignores ports, so the `Lax` refresh cookie is same-site and is sent. | TRACED |
| Verify → session | `SessionResponse` carries `accessToken`, `expiresIn`, `user`, `firm`. The zod `sessionSchema` requires exactly those; the extra `tokenType`/`expiresAt` are stripped, and `firmSchema`'s `logoUrl`/`settings` have defaults, so the absent keys parse. Checked field by field. | TRACED |
| Rate limits do not lock you out first | `auth_ip_rule` and `verify_ip_rule` are enforced in `AuthService`, not as route decorators. The four Redis Lua scripts were **transliterated to Python and property-tested (29 assertions)** during the auth pass: the sliding window admits exactly the limit, denials consume nothing, rotation has exactly one winner under concurrent replay. | TRACED |

### 2.3 create empty project

| Step | Finding | Tier |
|---|---|---|
| Request shape | `useProjectStore.create` sends `{name, units, cityPack}`. `ProjectCreate` requires only `name` and defaults the rest. `CamelModel` is `extra="forbid"`, so the check matters — the store deliberately does **not** forward the dialog's `clientName`/`plot`, which go to `PUT /plot` and `PUT /brief` as separate best-effort calls. | TRACED |
| Response shape | `ProjectOut` ⇄ `projectSchema` compared field by field, along with all 21 other zod↔pydantic pairs. Four mismatches were found by an earlier review and fixed before this pass: sheet `number`/`title` nulls, the share-link `scope` nesting, the comment-resolve route, and sign-out-everywhere. Re-verified as fixed here. | TRACED |
| It is firm-scoped | `ProjectRepository` extends `Repository`, whose only query builder is `_scoped_select()` — there is no unscoped path. Asserted statically by `make tenancy-audit` (**EXECUTED, passes**) and by `tests/test_no_unscoped_queries.py` over the AST. | EXECUTED / TRACED |
| The empty state teaches | `DashboardPage` renders an `EmptyState` whose `demoAction` prop is **required by the type system** — golden rule 8 encoded as a compile error rather than a review comment. | TRACED |

---

## 3. Everything that was actually EXECUTED

On Python 3.9.6 + GNU Make 3.81, in this pass:

```
make secret-audit    → ok — no secret names or non-VITE_ env reads
make tenancy-audit   → ok — no direct session access outside repositories
make license-check   → scanned 16 python distributions: 0 denied, 0 unknown
make env-audit       → 134 documented names, 96 settings fields, 24 direct reads; no drift
make audit           → extracts 16 pins (see §6b); then fails only on "pip-audit: not found"
python3 -m py_compile  over all 184 Python files → clean
python3 fixtures/rules/_tools/verify_fixtures.py        → 118 rules / 238 fixtures / 18 check types
python3 fixtures/model/_tools/generate_golden_states.py --check → OK (11 cases)
```

Plus purpose-written scripts, all clean:

* **Python internal-import resolution** re-run after this pass's edits across 160
  modules in `garh_api`,
  `garh_model`, `garh_rules` and `services`: every `from x import y` resolves to a
  real module and a real name. 0 unresolved.
* **TypeScript import/export resolution** across 115 files in `apps/web/src`,
  `packages/*/src` and `e2e`: every relative and aliased import resolves and every
  named import exists in the target (after accounting for `export type { … } from`
  re-exports). 0 unresolved.
* **The audit-action registry invariant** simulated over the AST: no action literal
  left in `auth.py`, all five §13 categories present, no duplicates, all
  well-formed.
* JSON validity of all rule packs, fixtures and schemas.

Carried over from earlier passes, still valid:

* `parseLengthMm` transliterated to Python reproduces all 67 golden unit pairs and
  all 16 must-fail inputs with 0 mismatches.
* The model core's 7 Python test modules — 293 assertions — pass under a pytest +
  hypothesis shim, at 5 different property seeds.
* `fixtures/rules/_tools/verify_fixtures.py` and
  `fixtures/model/_tools/generate_golden_states.py --check` are both green.
* The empty document's canonical JSON was hand-written and hashed by an independent
  path; it matches `doc_hash`.

---

## 3b. §13 security checklist, walked item by item

A later pass walked playbook §13 line by line rather than by finding. Every row was
checked by reading the code, not by trusting a comment. Two gaps were found and fixed
(marked **FIXED**); the rest were already correct, and saying so is the point — a
checklist with no passes recorded is indistinguishable from one nobody ran.

| §13 requirement | State | Evidence |
|---|---|---|
| OTP 10 min expiry, 5 attempts | ✅ | `otp_ttl_seconds=600`, `otp_max_attempts=5`; attempts counted **in the row**, and the increment is committed by `AuthService._persist_failure_record()` — without that, `session_scope`'s rollback-on-raise made the 5-attempt cap completely inert. |
| JWT RS256, refresh rotation, reuse detection, logout-all | ✅ | `JWT_ALGORITHM` pinned to `RS256` in *two* places so `JWT_ALGORITHM=HS256` cannot boot (symmetric would let the public key mint tokens). Rotation/reuse property-tested via Lua transliteration. |
| Tenancy: no table access outside repositories | ✅ | `make tenancy-audit` **EXECUTED, passes**. `Repository`'s only query builder is `_scoped_select()`; no unscoped path exists. Escape hatch is one named function that logs + writes an audit row. |
| Cross-tenant test in CI | ✅ | `apps/api/tests/test_cross_tenant.py`, 10 tests. CI fails if the *file* is missing, so "no test" cannot read as "passed". |
| Share links: 256-bit, hashed at rest, scoped, expiring, revocable | ✅ | `secrets.token_urlsafe(share_token_bytes)` with `share_token_bytes: int = Field(default=32, ge=32)` — the `ge=32` means config cannot weaken it. Only `sha256(token)` is stored. Resolution filters `revoked` **and** `expires_at`. |
| Viewer router imports no write deps | ✅ | `share.public_router`'s module imports contain no write path; `unwrap_snapshot` is imported *inside* the single handler needing it. `viewer_route_paths()` is exported so a test can assert the surface. |
| Pydantic strict + zod at every boundary | ✅ | Request base model is `extra="forbid"` + `StrictInt` lengths (a float mm cannot reach the model core); `schemas/auth.py` adds `strict=True`. Client side: every response zod-parsed. |
| Upload size + type limits, worker parse timeout + memory cap | ✅ **FIXED** | Size cap, content sniff, 10 s parse timeout and 512 MB memory cap all present. **The cap was split-brained**: the API read `MAX_DXF_UPLOAD_BYTES` and `services/drawings/handler.py` compared against a hard-coded 20 MB, so raising the limit made the API accept a file the worker refused. Now one shared variable, in `SHARED_ENV_NAMES`. |
| No secret in the client bundle | ✅ | `make secret-audit` **EXECUTED, passes** — `apps/web` may only read `import.meta.env.VITE_*`. |
| Rate limits on auth and ops | ✅ **FIXED** | Auth (4 rules, all fail-closed), ops (60/s/firm), solver jobs (10/hr), anonymous share comments (per IP). **`POST /projects/:id/brief/parse` had none** — the only route that spends money at a third party per request. Added `llm_per_firm_rule` (`RATE_LIMIT_LLM_PER_HOUR`, per firm, **fails closed**). |
| `audit_log` on auth / export / share / override / delete | 🟡 | 15 of 19 registry actions have a live write site. Four do not, because the route does not exist yet: `compliance.overridden`, `user.role_changed`, `user.removed`, `firm.settings_changed`. Now enumerated in `tests/test_audit_actions.PENDING_ACTIONS` with the phase that adds each, and the test fails **in both directions** — a new un-emitted action fails, and wiring one up without deleting its entry also fails. |
| CSP with no inline scripts | ✅ | API: `default-src 'none'; sandbox`. SPA: `apps/web/nginx.conf`, `script-src 'self'`, no `unsafe-inline`/`unsafe-eval`. |
| SameSite=Lax refresh cookie | ✅ | `HttpOnly`, `Secure`, `SameSite=Lax`, path-scoped to `{api_prefix}/auth`. `clear_refresh_cookie` mirrors every attribute (mismatched attributes leave the original cookie in place). |
| CORS allowlist | ✅ | Explicit list, never `*`, with `allow_credentials=True`; logs a warning when empty. |
| Signed download URLs ≤ 10 min | ✅ | `ttl = min(int(ttl_seconds or settings.s3_signed_url_ttl_seconds), 600)` — hard-capped in code, so config cannot exceed §13. HMAC key derived from the JWT private key (no second secret); verification uses `hmac.compare_digest`; bad signature → 404, honest expiry → 410. |
| Prompt-injection containment | ✅ | Four gates in `services/llm/copilot.py`; LLM output only ever becomes ops validated against `ops.schema.json` and dry-run folded. `cannotDo`/`needsClarification` are honoured *before* any gate, and ops arriving alongside them are dropped. `redaction.py` keeps PII out of the model summary. |
| Lockfiles + `pip-audit`/`pnpm audit` + licence scanner | 🟡 | Scanner correct and **EXECUTED** (rejects GPL/AGPL, clears LGPL/MPL/permissive). `make audit` **FIXED**: it used an inline `import tomllib` and died with a bare `ModuleNotFoundError` on Python < 3.11; now `scripts/pinned_deps.py`. `pnpm-lock.yaml` still absent — the one blocker nothing on this machine can clear. |
| Never GPL/AGPL; no RPLAN weights; no FLUX.1-dev | ✅ | All 62 declared dependencies documented in `DECISIONS.md` (checked mechanically: 0 missing). `services/render/licenses.py` refuses FLUX.1-dev, its filename variants, SUPIR, SVD and anything matching `rplan` — **denylist beats allowlist**, so an operator cannot configure their way past it. |

---

## 4. What is UNVERIFIED, and the command that settles it

Nothing below has run. Each row is a claim waiting for a machine.

| DoD item / claim | Command that settles it | Expect |
|---|---|---|
| The stack starts at all | `docker compose config && docker compose up` | Compose could not even be YAML-parsed here (no PyYAML); the folded scalars in `api.command` and `minio-init.command` were reviewed by hand. First real failure is most likely there. |
| Migrations are reversible and complete | `make migrate && docker compose exec api alembic downgrade base && alembic upgrade head`, then `alembic revision --autogenerate` and confirm an **empty** diff | The migration has never touched a live Postgres. An empty autogenerate diff is the real proof that `models.py` and `0001_initial_schema.py` agree — the current proof is a text comparison of 18 tables and 110 named constraints. |
| Login works end to end | `make up && make seed`, then open `http://localhost:5173` | The seeded demo firm's address, or any address via signup. |
| Create-empty-project works | Same session: "New project", enter a name | Watch for a 422 — `extra="forbid"` is strict by design. |
| Cross-tenant returns 404 | `cd apps/api && pytest tests/test_cross_tenant.py` | CI has a named step that fails if the test file is *missing*, so "no test" cannot read as "passed". |
| The Python suite passes | `cd apps/api && pytest -q` | ~24 modules. `testpaths` now includes `garh_model/tests` and `garh_rules/tests`; those ~300 assertions have only ever run under a hand-rolled shim, never real pytest with real hypothesis. Expect findings. |
| **The TS↔Python state hash agrees** | `pnpm --filter @garh/model test:golden` | **The single highest-risk unverified claim in the repo.** `fixtures/model/golden-states.json` was generated by Python. The TypeScript side has never folded it. Three normalisation divergences were found and fixed by reading (`storey.add` level, `facade.apply_kit` params, `material.assign` target); this command is what proves there is no fourth. |
| The JS suite passes | `pnpm -r --if-present test` | 13 test files. `vitest` has never run here. |
| Types check | `pnpm -r --if-present typecheck` | `composite`/`noEmit` (TS6304) and a stray project reference (TS6305) were both fixed by reading; `tsc` has still never run. |
| Lint passes | `pnpm exec eslint . && ruff check --config apps/api/pyproject.toml apps/api services` | Type-aware ESLint needs every `.ts` to belong to a discoverable project; the build-config files are now excluded from type-aware linting for exactly that reason. |
| Strict typing holds | `mypy --strict apps/api/garh_model services` | Never run. `--strict` over ~90 modules that were written without it is not a formality. |
| CI is green | Push a branch | Blocked at step one until `pnpm-lock.yaml` exists. |
| E2E smoke passes | `make up && make seed && pnpm --filter @garh/e2e test:smoke` | Playwright browsers must be installed first. |
| DXF opens in a real CAD tool | Phase 8 | `services/drawings/dxf.py` exposes `audit()`; there is nothing to audit yet. |
| Perf budgets (§14) | `pnpm --filter @garh/web test` (the <10 ms local-fold assertion), Playwright traces for the 16 ms frame budget | The fold assertion has never run on real hardware. |

---

## 5. The bootstrap sequence, in order

For the first person with a real toolchain. Do not skip step 1; six jobs depend on it.

```bash
# 0. Prerequisites: Docker Compose v2.24+, Node 20, pnpm 9.12.0, Python 3.11, make.

# 1. The lockfile. Nothing JS works until this is committed.
make lockfile
git add pnpm-lock.yaml && git commit -m "chore: commit pnpm lockfile"

# 2. Environment. Never overwrites an existing .env.
make env
make dev-keys          # optional: a stable JWT keypair instead of the per-boot one

# 3. Bring it up. First build pulls images and installs both dependency trees.
make up
make ps                # every service should reach (healthy)

# 4. Schema + demo data.
make migrate           # a no-op if API_MIGRATE_ON_BOOT already ran it
make seed

# 5. The DoD path, by hand.
open http://localhost:5173
#    → sign up (or sign in as the seeded firm)
#    → the OTP is echoed on screen; DEV_ECHO_OTP is on in dev
#    → "New project", give it a name
#    → you should land on the brief tab of an empty project

# 6. The gates, in CI's order.
make lint
make typecheck
make test
make golden
make verify            # everything above plus the four security guards
pnpm --filter @garh/e2e exec playwright install --with-deps
make e2e-smoke
```

**Expect failures at steps 6.** That is the point of running them. Fix forward and
update this document — specifically, promote rows out of §4 as they are settled,
and record anything the run finds that this trace missed. A row that moves from
UNVERIFIED to EXECUTED should say which command proved it and when.

---

## 6. Defects fixed during this audit pass

For the record, so a reader can tell what changed from what was merely reviewed.

| Area | Defect | Fix |
|---|---|---|
| §13 web | The SPA shipped **no CSP at all**, and the `prod` nginx image had **no SPA fallback** — `/login` and every share link 404'd on a hard load. | Added `apps/web/nginx.conf` (CSP with `script-src 'self'`, HSTS, `try_files … /index.html`) and wired it into the Dockerfile. |
| §13 input | No ceiling on request-body size anywhere. Starlette buffers the whole body before Pydantic sees it, so an unauthenticated POST was unbounded memory pressure. | `MAX_REQUEST_BODY_BYTES` + `main.BodySizeLimitMiddleware`, checking `Content-Length` **and** the streamed byte count. |
| §13 audit | `auth.signup`, `auth.logout` and `auth.refresh_reuse_detected` were declared privately in `auth.py`, so a reviewer grepping `AUDIT_ACTIONS` for the refresh-reuse action would conclude it was never written. | Moved into the registry; added `tests/test_audit_actions.py`, which fails if any §13 category loses its action or if `auth.py` grows a private literal again. |
| Catalogue | `GET /catalog/*` fell back to `<repo>/catalog`, a directory that has never existed, while the authored and tested JSON lives in `fixtures/catalog`. The files were decorative. | Router fallback now finds the real directory; compose sets `GARH_CATALOG_DIR` explicitly; the seed's mismatch warning delegates instead of re-deriving the rule. |
| Lint | Type-aware ESLint would fail on `apps/web/{vite,tailwind}.config.ts`: their only home is `tsconfig.node.json`, which TypeScript's ProjectService never discovers. | `**/*.config.{ts,mts,cts}` is linted without type information; `tsc -p tsconfig.node.json` still typechecks them. |
| Env | `DEV_ECHO_OTP` and `TRUSTED_PROXY_HOPS` were undocumented; the env-drift script could not see reads made through a constant. | Both documented; `check_env_drift.py` now resolves `os.environ.get(CONSTANT)`. |
| Layout | `fixtures/{plans,sheets,copilot-commands}/` were named by §1 and absent. | Created with READMEs naming the phase that fills them and the exact shape expected. No invented data. |
| Assets | `index.html` linked `/favicon.svg` and `/apple-touch-icon.png`; `apps/web/public/` did not exist, so every page load 404'd twice. | Both added, plus `apps/web/README.md` explaining what may live in `public/`. |
| CI | A missing lockfile failed six jobs with `ERR_PNPM_NO_LOCKFILE`. | One preflight step, first job, with the fix in the message. `make lockfile` is the command. |

### 6b. Found by the §13 walkthrough (a later pass)

These four came from walking the security checklist top to bottom rather than from a
review's finding list, which is why none of them appear above.

| Area | Defect | Fix |
|---|---|---|
| §13 rate limits | **`POST /projects/:id/brief/parse` had no rate limit.** It is the only route in the API that spends money at a third party on every request, so an authenticated loop was a billing incident, not a load problem. | `llm_per_firm_rule` + `RATE_LIMIT_LLM_PER_HOUR` (per firm, default 60), enforced **before** the provider is resolved so a limited request costs nothing. Deliberately **fails closed** — the only product limit that does — because an *uncounted* call to a metered API is worse than a parse the architect retries. Pinned by `tests/test_rate_limits.py`. |
| §2 metering | **`credit_events(kind='llm')` was never written.** The kind was in `CREDIT_EVENT_KINDS` *and* in the table's CHECK constraint, and solver/render/export all metered — so the meter under-reported precisely the spend that leaves the building. | Recorded in `/brief/parse` after the provider returns (a failed call is not billed), with provider + model in `meta` so a free `mock` row is distinguishable during reconciliation. |
| §13 uploads | **The DXF size cap was split-brained.** The API enforced `MAX_DXF_UPLOAD_BYTES`; `services/drawings/handler.py` compared against a hard-coded `20 * 1024 * 1024` and ignored the variable. Raising the limit made the API accept a file the worker then rejected — surfacing as a failed job rather than an immediate 413. | `max_dxf_upload_bytes` added to `WorkerSettings` and to `SHARED_ENV_NAMES` (so `assert_shared_env_names_match` catches a future rename); the handler reads `ctx.settings`. It still re-checks after the blob fetch, because the envelope's `sizeBytes` is a claim and not a measurement. |
| §13 dependencies | **`make audit` crashed instead of auditing** on any interpreter below 3.11: it used an inline `import tomllib`, and `PY ?= python3`. A security target that dies with a bare `ModuleNotFoundError` reads as a broken repo and invites deleting it. | `scripts/pinned_deps.py` — prefers `tomllib`, falls back to a narrow line parser that **errors rather than returning a short list** (an under-count would quietly audit fewer packages). Verified on Python 3.9.6: extracts 16 runtime pins, cross-checked against an independent count of both manifests. |
| §13 audit trail | Four registry actions had **no write site anywhere**, so the registry read as a promise the code did not keep. | Enumerated in `tests/test_audit_actions.PENDING_ACTIONS` with the phase that adds each, and the new test fails in **both** directions. Its `_constant_names_by_action()` reads the mapping from the AST rather than deriving it from the action string — the mechanical rule would have reported `auth.refresh_reuse_detected` (constant `ACTION_AUTH_REFRESH_REUSE`, the single most security-relevant row in the table) as un-emitted, i.e. cried wolf on the one action that must never be missing. |
