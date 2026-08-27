# Security checklist (playbook §13)

"Security is not a phase." Each item belongs to the phase that introduces the
surface, and each phase's DoD includes the relevant rows. This page is the live
tracker.

**Status legend:** ✅ implemented **and** something keeps it that way · 🟡 implemented,
nothing enforces it yet · ⬜ not started · 🔒 enforced by tooling

> **Last walked: 2026-08-04, during the pre-Phase-1 repo audit.** Everything below
> was verified by reading the code, not by running it — the authoring machine has no
> Docker, no Node and no Python 3.11. `docs/phase-0-verification.md` records exactly
> which claims are unexecuted and the command that settles each one. Read every ✅ as
> "the code says so"; the first real `pytest` run may demote some.

---

## Automated guards (run on every commit and every PR)

| Guard                | Command                                             | Enforces                                                                          |
| -------------------- | --------------------------------------------------- | --------------------------------------------------------------------------------- |
| 🔒 Secret audit      | `make secret-audit`                                 | no non-`VITE_` secret name in `apps/web/` source or bundle                        |
| 🔒 Tenancy audit     | `make tenancy-audit`                                | no direct table access outside the repository layer                               |
| 🔒 Tenancy test      | `pytest apps/api/tests/test_no_unscoped_queries.py` | the same rule over the AST, plus the positive invariants                          |
| 🔒 Cross-tenant test | `pytest apps/api/tests/test_cross_tenant.py`        | another firm's project is a 404, not a 403 with a hint                            |
| 🔒 Env audit         | `make env-audit`                                    | `.env.example` ⇔ both settings classes; a documented variable nothing reads fails |
| 🔒 Licence scan      | `make license-check`                                | Apache/MIT/BSD/MPL only — fails on GPL/AGPL **and** unknown                       |
| 🔒 Vulnerabilities   | `make audit`                                        | `pnpm audit` + `pip-audit` against both `pyproject.toml` manifests                |
| 🔒 Private keys      | pre-commit `detect-private-key`                     | no key material in a tracked file                                                 |

The guards live in the `Makefile`, not in the workflow, so the command that gates a
merge is the command you run locally.

---

## AuthN — email OTP + JWT RS256

| Item                                                            | Status | Where                                                                                                                                                                                            |
| --------------------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| OTP 10-minute expiry                                            | ✅     | `config.otp_ttl_seconds=600`; `repositories/otp.py`; `tests/test_auth_otp_policy.py`                                                                                                             |
| OTP 5-attempt limit, then burn the code                         | ✅     | `config.otp_max_attempts=5`. The counter is committed **outside** the request transaction (`AuthService._persist_failure_record`) — a rollback on the 401 path had made the cap completely inert |
| Expired / exhausted / wrong / never-issued fail **identically** | ✅     | all `400 otp_invalid`; telling them apart leaks whether an address has a live challenge                                                                                                          |
| JWT RS256; symmetric algorithms refused at boot                 | ✅     | `security.JWT_ALGORITHM`, `ConfigError` on anything else                                                                                                                                         |
| Refresh rotation, one-use, family-scoped                        | ✅     | `security.py` + Redis; the rotation Lua was property-tested for exactly-one-winner under a concurrent replay                                                                                     |
| Reuse detection kills the family                                | ✅     | audit action `auth.refresh_reuse_detected`                                                                                                                                                       |
| Logout-all                                                      | ✅     | `POST /auth/logout-all` bumps a per-user generation counter — and the client now calls it (`{everywhere:true}` posted to `/auth/logout` used to be silently discarded)                           |
| Signing key never leaves the API                                | ✅     | RS256 chosen for exactly this                                                                                                                                                                    |
| The dev OTP echo cannot escape dev                              | ✅     | `DEV_ECHO_OTP` is double-gated on `APP_ENV in (dev,test)`; documented in `.env.example`                                                                                                          |

**Known caveat, not a gap:** refresh session state lives in Redis, not Postgres
(§2 defines no `refresh_tokens` table). Flushing Redis resets the generation
counters, un-doing a past `logout-all` for access tokens still inside their 15
minutes. Run Redis with AOF; a `users.token_generation` column would remove it.

## AuthZ — tenancy

| Item                                         | Status | Where                                                                                                                                     |
| -------------------------------------------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `firm_id` on every tenant-owned row, indexed | ✅     | 14 tenant tables; asserted in `test_no_unscoped_queries.py`                                                                               |
| Every query takes a `TenantCtx`              | ✅     | `tenancy.Repository._scoped_select()` is the only query builder; there is no unscoped path and no public session                          |
| Handlers cannot touch tables                 | 🔒     | `make tenancy-audit` + the AST test                                                                                                       |
| The one escape hatch is loud                 | ✅     | `system_unscoped_session(task=, reason=)` — WARNING log plus an `audit_log` row under `SYSTEM_FIRM_ID`. Routers may not even _mention_ it |
| Cross-tenant fetch → 404                     | 🔒     | `tests/test_cross_tenant.py`; CI fails if the file is missing, not only if it fails                                                       |

## Share links

| Item                                                          | Status | Where                                                                                                                             |
| ------------------------------------------------------------- | ------ | --------------------------------------------------------------------------------------------------------------------------------- |
| 256-bit random token                                          | ✅     | `secrets.token_urlsafe(share_token_bytes)`; the field enforces `>= 32`                                                            |
| Stored **hashed**                                             | ✅     | only `sha256(token)` is persisted; plaintext is returned once, at creation                                                        |
| Scoped: project, sections, `canComment`                       | ✅     | `SHARE_SECTIONS = (plan, three_d, renders, sheets, compliance)`; `ctx.require_scope(section)`                                     |
| Expiry + revocation                                           | ✅     | `expires_at`, `revoked`, both filtered in `ShareTokenResolver.resolve`                                                            |
| Viewer is a separate read-only router importing no write deps | 🔒     | `share.public_router`; `test_viewer_surface_imports_no_write_path` asserts the write helpers are absent from module-scope imports |
| Viewer responses carry no download links                      | ✅     | by construction in the viewer serialisers                                                                                         |

## Input validation

| Item                                                       | Status | Where                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ---------------------------------------------------------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Pydantic strict at every server boundary                   | ✅     | `CamelModel` is `extra="forbid"`; `AuthModel` adds `strict=True`                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| zod at every client boundary                               | ✅     | every response in `apps/web/src/lib/api.ts` is parsed; a shape drift becomes a `malformed_response` `AppError` with a request id, never a silent `undefined`                                                                                                                                                                                                                                                                                                                                                                      |
| Integer mm enforced at the boundary                        | ✅     | `StrictInt` server-side, `OP_FIELD_NOT_INT_MM` in the op validator — a float length is rejected, never rounded                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Hard ceiling on any request body                           | ✅     | `MAX_REQUEST_BODY_BYTES` (8 MB) via `main.BodySizeLimitMiddleware`, checked against `Content-Length` **and** the streamed byte count so `Transfer-Encoding: chunked` cannot slip past                                                                                                                                                                                                                                                                                                                                             |
| DXF ≤20MB, images ≤10MB                                    | 🟡     | limits configured and advertised by `GET /meta`; **no upload route exists yet.** Phase 2 adds it and must list itself in `main.LARGE_BODY_PATH_SUFFIXES` and enforce its own per-format cap. The DXF cap is one shared variable (`MAX_DXF_UPLOAD_BYTES`, `[both]`, in `SHARED_ENV_NAMES`): the API rejects at the edge and the drawings worker re-checks after fetching the blob. It used to be hard-coded in the worker, so raising the limit made the API accept a file the worker then refused — a failed job instead of a 413 |
| Uploads type-sniffed, not trusted by extension             | ⬜     | Phase 2, with the upload route                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| DXF parsed **in a worker** with a 10s timeout + memory cap | 🟡     | `DXF_PARSE_TIMEOUT_SECONDS=10`, `DXF_PARSE_MEMORY_LIMIT_MB=512`, the size cap and the content sniff all live in `services/drawings/handler.py` and are enforced now; the ezdxf parse itself is an honest Phase 2 stub. The size cap is checked twice — against the envelope's `sizeBytes` before the fetch (cheap refusal) and against the real byte count after it, because `sizeBytes` is the uploader's claim                                                                                                                  |
| SVG output sanitised — no scripts, no `foreignObject`      | ⬜     | Phase 8, with the sheet engine                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |

## Secrets

| Item                                       | Status | Where                                                                                                  |
| ------------------------------------------ | ------ | ------------------------------------------------------------------------------------------------------ |
| Env only, never in the bundle              | 🔒     | `make secret-audit`                                                                                    |
| `VITE_` prefix audit in CI                 | 🔒     | `lint` job; `eslint.config.js` also bans `process.env` inside `apps/web` so it fails in the editor too |
| Exactly one module reads env on the client | ✅     | `apps/web/src/lib/env.ts`, zod-validated at startup                                                    |
| Provider API keys in worker env only       | ✅     | marked `[worker]` in `.env.example`                                                                    |
| No secrets in git                          | ✅     | `.env`, `.keys/`, `*.pem` gitignored + `detect-private-key`                                            |

## Rate limits + audit log

| Item                                                                           | Status                           | Where                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ------------------------------------------------------------------------------ | -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 60 ops/s per firm                                                              | ✅                               | `routers/ops.py` → `enforce_rate_limit`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 10 solver jobs/hr per firm                                                     | ✅                               | `Depends(rate_limit_solver_jobs)` on `POST /solve`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Per-IP limit on the auth endpoints                                             | ✅                               | `auth_ip_rule` / `verify_ip_rule` inside `AuthService`, plus a per-email resend rule                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Per-firm limit on the LLM routes                                               | ✅                               | `llm_per_firm_rule` (`RATE_LIMIT_LLM_PER_HOUR`, default 60) on `POST /projects/:id/brief/parse`, enforced _before_ the provider is resolved. §13 does not name this limit — it predates the route — but this is the only endpoint that spends money at a third party per request, so "no limit" was a billing incident waiting to happen. The copilot joins the rule in Phase 6                                                                                                                                                                                                                                       |
| Per-IP limit on the anonymous share-comment write                              | ✅                               | `share.py` `_share_comment_rule` — the one write on the viewer surface                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Auth limits fail **closed**, product limits fail **open**                      | ✅ with one deliberate exception | `ratelimit.py` — a Redis outage must not become an authentication bypass, and must not stop an architect drawing. **`llm_per_firm_rule` fails closed**, against the product-limit convention: an _uncounted_ call to a metered API is worse than a brief parse the architect retries. `tests/test_rate_limits.py` pins both policies, so the contrast cannot be "tidied up"                                                                                                                                                                                                                                           |
| LLM spend is metered                                                           | ✅                               | `credit_events(kind='llm')` written by `/brief/parse` after the provider returns, so a failed call is not billed; provider + model in `meta` so a free `mock` row is distinguishable. §2 requires "render/solver/LLM metering from day one" and the `llm` kind was declared but never written until this was wired                                                                                                                                                                                                                                                                                                    |
| `audit_log` on auth, exports, share creation, reg-profile overrides, deletions | 🟡                               | All five §13 categories are present in `AUDIT_ACTIONS`, and 15 of 19 actions have a live write site. **Four do not**, because the route that would emit them does not exist yet: `compliance.overridden` (Phase 2/4), `user.role_changed`, `user.removed`, `firm.settings_changed` (Phase 9). They are enumerated in `tests/test_audit_actions.PENDING_ACTIONS` with the phase for each, and that test fails **in both directions** — a new un-emitted action fails, and so does a stale entry once the route lands. It also still fails if a category loses its action or if `auth.py` grows a private literal again |

## Web

| Item                                         | Status | Where                                                                                                                                                                 |
| -------------------------------------------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| HSTS                                         | ✅     | `SecurityHeadersMiddleware`, production only (HSTS on `localhost` is a trap you cannot easily undo); also `apps/web/nginx.conf`                                       |
| CSP with no inline scripts                   | ✅     | API: `default-src 'none'; sandbox`. SPA: `apps/web/nginx.conf` ships `script-src 'self'` with no nonce — possible only because `index.html` contains no inline script |
| `SameSite=Lax` on the refresh cookie         | ✅     | `security.set_refresh_cookie`: HttpOnly + Secure outside dev + `Path=/api/v1/auth`, so it is not attached to any other request                                        |
| CORS allowlist, never `*`                    | ✅     | `CORS_ALLOW_ORIGINS` with `allow_credentials=True`, which makes `*` impossible anyway                                                                                 |
| `X-Frame-Options` / `frame-ancestors 'none'` | ✅     | both surfaces                                                                                                                                                         |
| Signed download URLs ≤10 min                 | ✅     | `sign_download_token` hard-caps the TTL at 600s whatever the caller asks for                                                                                          |
| SPA deep links resolve                       | ✅     | `nginx.conf` `try_files … /index.html`; without it every share link and every page refresh 404s                                                                       |

## LLM — prompt-injection containment

| Item                                                                   | Status | Where                                                                                                                                                                                         |
| ---------------------------------------------------------------------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Model output only ever becomes **validated ops** — never executed text | ✅     | `POST /brief/parse` writes through `dispatch_ops` like any other mutation; `services/llm/copilot.py` dry-run-folds every proposal before a human sees a diff                                  |
| Provider output is schema-validated inside the LLM layer               | ✅     | `LlmProvider.complete_json` guarantees a schema-valid `result.data`; both the mock and the Anthropic provider validate                                                                        |
| Injection attempts resolve to an honest refusal                        | ✅     | the shipped mock corpus includes an injection case that must produce `cannotDo`                                                                                                               |
| Model summaries exclude PII                                            | ✅     | `services/llm/redaction.py`                                                                                                                                                                   |
| A copilot endpoint exists                                              | ⬜     | **Phase 6.** `CopilotService` is written and fixture-tested, but no route mounts it, so `DiffPreview.tsx` has no server yet. Left unwired and recorded in DECISIONS.md rather than half-built |
| Brief text sent to an LLM is disclosed in the privacy policy           | ⬜     | DPDP (spec §15)                                                                                                                                                                               |
| Per-tenant isolation of fine-tune corpora, consent default **OFF**     | ⬜     | no corpus is collected today, which is the only reason this is not urgent                                                                                                                     |

The architecture _is_ the containment: a prompt injection can at worst produce an op
that fails validation. There is no path from model output to geometry, to SQL, or to
executed text.

## Dependencies

| Item                                                 | Status | Where                                                                                                                                                                                                                                                              |
| ---------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Lockfiles committed                                  | ⬜     | **`pnpm-lock.yaml` still does not exist** — generating it needs Node, which the authoring machine has not got. CI's first job now fails with an explicit message rather than six obscure `ERR_PNPM_NO_LOCKFILE`s; `make lockfile` is the one command that fixes it |
| `pnpm audit` / `pip-audit` in CI                     | ✅     | `supply-chain` job; `make audit` extracts pins from both manifests with `tomllib`                                                                                                                                                                                  |
| Licence scanner blocking GPL/AGPL/unknown            | ✅     | `make license-check` — **0 denied, 0 unknown** across the 16 distributions installed on the authoring machine                                                                                                                                                      |
| Weight-licence guard (never FLUX.1-dev, never RPLAN) | ✅     | `services/render/licenses.py`: denylist beats allowlist, marker matching beats exact matching, refusals are permanent `LicenseError`s logged at ERROR with a banner                                                                                                |

---

## Data policy (spec §15)

| Item                                                                 | Status | Note                                                                                                           |
| -------------------------------------------------------------------- | ------ | -------------------------------------------------------------------------------------------------------------- |
| Architect-of-record required per project                             | 🟡     | `projects.architect_of_record` exists and is validated as a firm member; not yet _required_ in order to export |
| Advisory-not-approval disclaimer **at export**, not buried in ToS    | ⬜     | Phase 8                                                                                                        |
| DPDP: briefs are sensitive PII — consent, retention limits, deletion | ⬜     | family composition, budget, religious inference via pooja/Vastu                                                |
| Designs owned by the firm; training consent default **OFF**          | ⬜     | per-tenant, contractual                                                                                        |
| Inference GPUs in Mumbai; LLM processing location disclosed          | ⬜     | `S3_REGION=ap-south-1` set                                                                                     |

---

## Reviewing this list

Walk it at the end of every phase and at the Phase 9 DoD, which requires every item
✅. When you tick something, link the test or the code that keeps it ticked — a
checklist entry with nothing enforcing it drifts back to ⬜ without anyone noticing.

That is exactly what happened to this page between the scaffold and this audit: a
dozen rows read ⬜ long after the code had been written. Under-claiming is the other
failure mode of a manual checklist, and it is just as misleading as over-claiming —
it hides which controls actually still need building.
