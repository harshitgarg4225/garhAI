# Trust and operations (J14) — verification ledger

Same convention as every other ledger here: **EXECUTED** names the run that
proves a claim, **TRACED** means read-verified only, **UNVERIFIED** names the
exact command that would settle it. Written 2026-09-20 against this branch.

The reader's finding for J14 was that "operations are half-real": Sentry off on
the deployed stack with nothing saying so, no scheduled backup, a "load test"
that hit `/healthz`, migrations racing on boot against the doc that forbids them,
an onboarding tour that was a store with no UI, and DPDP/audit surfaces with no
client. Each row below is one of those.

## 1. Observability that cannot silently be off

- **EXECUTED** — `GET /api/v1/admin/ops` returns queue depths per queue
  (pending/delayed/processing/dead), a row per live worker from its heartbeat,
  24-hour job counts by status and enqueue-to-finish p50/p95/max per kind, the
  LLM/render/billing/mail providers by name, `sentry: on|off`, and
  `alembic_version` against the code's migration heads with the verdict in
  words. 16 tests in `apps/api/tests/test_platform_ops.py`, run here:
  `pytest tests/test_platform_ops.py` → 16 passed.
- **EXECUTED** — the gate: anonymous 401, a firm admin 403, an empty
  `PLATFORM_OWNER_EMAILS` 403, the owner 200. Same file.
- **EXECUTED** — the body carries no secret: the test sets a DSN and asserts it
  (and the database/Redis URLs, and every job/project/firm id) never appears.
- **EXECUTED** — a Redis outage names itself in `observability.redis` instead of
  500ing the page, and the empty worker list is still reported as "missing", so
  empty cannot read as idle.
- **EXECUTED** — every worker writes `garh:worker:<name>:<instance>` on each
  sweep with an expiry of three sweeps; a clean stop deletes it.
  `services/common/tests/test_heartbeat.py` → 9 passed, including the negative
  control that a payload written without an expiry would let a dead worker read
  as alive.
- **EXECUTED** — `/healthz` carries `observability.sentry`, so "is error
  tracking on?" is answerable by the probe every monitor already polls.
- **EXECUTED (browser)** — `/platform/ops` renders the alarms first, then the
  queues, workers, jobs, providers and schema head; 19 vitest cases across
  `features/platform/*` and `lib/api.ops.test.ts`, the last of which reads the
  router's own field names so a server rename fails the test instead of the page.
- **UNVERIFIED** — the page against the DEPLOYED stack with real workers:
  `GET https://<api-domain>/api/v1/admin/ops` signed in as an owner. Needs the
  `PLATFORM_OWNER_EMAILS` entry (go-live checklist row 11).

## 2. Scheduled backup and a restore rehearsal that checks the data

- **EXECUTED 2026-09-16** — `scripts/backup_db.sh backup-s3` against the local
  Postgres 16 and the moto object store: a 716 KB dump plus a per-table row-count
  manifest uploaded under `backups/`, then the two-sided retention pass. Output
  recorded verbatim in `docs/ops-runbook.md`.
- **EXECUTED 2026-09-16** — `scripts/restore_rehearsal.sh --from-s3`: the newest
  dump pulled from the bucket, restored into a scratch database, all 29 tables'
  row counts equal to the manifest (1,795 ops, 25 projects, 16 firms, 247 audit
  rows), and the newest snapshot-bearing design of three projects refolded from
  its op log through the real model engine to `c3f7f3d41a3b621e` — the hash the
  `design_versions` row recorded. `RESULT OK`, exit 0.
- **EXECUTED** — the rehearsal can fail: `tests/test_backup.py` alters one op's
  payload under a snapshot and requires the refold to disagree, moves one table's
  count and requires the manifest check to fail, and blanks a `snapshot_hash` and
  requires the envelope check to fail. 11 passed.
- **TRACED** — the `backup` service image (`deploy/backup/Dockerfile`, PGDG
  `postgresql-client-17` because bookworm's `pg_dump` 15 refuses a 16/17 server)
  and its cron entry (`deploy/railway/backup.json`, `30 20 * * *` = 02:00 IST).
  `test_backup.py` reads the Dockerfile and the JSON rather than trusting them.
- **UNVERIFIED** — the image BUILT and the cron fired on Railway. Creating that
  service is an owner action (go-live checklist row 8); settle with
  `railway run --service backup /app/scripts/restore_rehearsal.sh --from-s3`.

## 3. A load test on the product path

- **EXECUTED 2026-09-17** — `scripts/load_smoke.py --journey --clients 5
--iterations 6` against a private api (one uvicorn worker, drawings worker at
  concurrency 2, mock providers, a scratch database): 30/30 on every step, with
  p50/p95 per step recorded in the runbook. Sheets (a real drawings job, nine
  sheets) at 4.1 s p50 / 6.6 s p95; project-from-template 1.3 s p50; compliance
  0.4 s p50; the signed sheet download 0.23 s p50.
- **EXECUTED** — the read-only smoke on the same stack: 8,655 requests, 862
  req/s, 0 errors, p95 28 ms.
- The first journey run failed 30/30 on the download step — the script expected a
  JSON link where the listing carries a signed `/downloads/<token>` that 307s to
  the object store. That is why the download is a step of its own.
- **UNVERIFIED** — the journey against the deployed stack. It WRITES (firms,
  projects, jobs, objects), so it needs a scratch deployment, not production.

## 4. Migrations on boot, made safe

- **EXECUTED** — `migrations/env.py` takes `pg_advisory_lock` before
  `run_migrations`, so a bare `alembic upgrade head` is guarded too.
  `tests/test_migrate.py` proves it with real processes: a real
  `alembic upgrade head` subprocess sits blocked while a third connection holds
  the key and applies nothing, then completes when it is released; two processes
  upgrading an empty scratch database at once both exit 0 and leave one row at
  head. 25 passed with `tests/test_seed.py`.
- **EXECUTED** — `python -m garh_api.migrate --seed` run twice leaves the audit
  log unchanged: the boot seed does one indexed lookup and writes nothing once
  the demo firm exists. The ordinary `python -m garh_api.seed` still writes its
  `seed.completed` row, which is the negative control that the flag is what makes
  the difference.
- **EXECUTED** — `tests/test_deploy_config.py` reads `deploy/railway/*.json`, the
  runbook's service table and `docker-compose.yml` and fails on any drift, on a
  bare `alembic upgrade head` anywhere, on a healthcheck that is not `/healthz`,
  and on a Dockerfile whose last stage (the one Railway builds) is not the
  intended one. Each checker is also run against a deliberately broken copy. 8
  passed.
- **UNVERIFIED** — the api running with `numReplicas > 1` on Railway. The lock
  makes it safe; nobody has raised the number yet.

## 5. The onboarding tour

- **EXECUTED (browser)** — 30 checks against a private stack: the tour starts
  itself on a first visit, walks plot → brief → generate → plan → compliance →
  sheets with ← → , navigates to each step's tab, highlights the element it
  names, traps focus, finishes and persists, does not start again, re-runs from
  the top bar's lightbulb, skips on Escape, and leaves the app usable.
- That run found what jsdom could not: every tab is a lazy chunk, so step one's
  anchor mounts after the card and the 250 ms poll left the first thing a new
  user sees with no highlight. `useTourAnchor` now watches the DOM.
- **EXECUTED** — 18 vitest cases across `features/tour/*`.

## 6. The web surfaces for the API-complete security features

- **EXECUTED (browser, 19 checks)** — `/settings/privacy`: the audit trail (7
  rows on a fresh firm, actors named, actions described, filtered by the server's
  own vocabulary), the DPDP §11 export saved as a real file whose contents were
  read back, that export appearing in the trail afterwards, and the §12 erasure
  gated by the typed address plus a confirm — ending in the server's last-admin
  refusal with its next step.
- **EXECUTED** — 19 vitest cases (`features/privacy/*`, `lib/api.privacy.test.ts`),
  including the member's refusal, the 403 branch, and a drift guard that reads
  the erasure body and response field names out of `routers/privacy.py`.
- **Already present before this work:** 2FA enrol/activate/disable with recovery
  codes, and signed-in devices with revoke, live in `/settings/account` (J01's
  merged work). The scorecard's "no web surface" for those is stale.
- **UNVERIFIED** — a MEMBER's view of `/settings/privacy` in a browser. An
  invited colleague only becomes a member by following the token in the invite
  email, which this stack has no mailer to deliver and which `POST /auth/otp`
  deliberately will not echo for a pending address. Covered in vitest instead.

## 7. Accessibility

- **EXECUTED 2026-09-20** — axe-core (WCAG 2.0/2.1 A + AA + best-practice) over
  twelve screens in a real browser: login, dashboard, all four settings sections,
  billing, the brief/compliance/sheets/renders/plan tabs, and the tour open over
  the shell. Two serious violations found and fixed:
  - `definition-list` / `dlitem` on the new audit trail — the `<dl>` wrapped each
    pair in a `<span>`, which is invalid and loses the term/description pairing
    for a screen reader. Now a `<div>`.
  - `color-contrast` 3.42:1 and 3.48:1 on the assumption chip's label
    (`packages/ui/src/Chip.tsx`) — `opacity-70` over 11px uppercase text, on
    every Brief page since the chip was written. The opacity is gone and the
    label carries its weight instead.
- Moderate findings are printed, not failed: `landmark-one-main` and `region`
  fire on the §12 canvas panel grid, and `heading-order` on the settings sections
  where a card title follows the page title. Chasing them would mean
  restructuring the layout for a machine rather than for a person; they are
  recorded here so the decision is visible rather than implied.
- **The check is kept** as `e2e/tests/accessibility.spec.ts` (`@a11y`), which
  **fails** rather than skips when `axe-core` is absent — a green accessibility
  check that never ran is the repo's cardinal sin.
- **UNVERIFIED** — `@a11y` running in CI. It needs `axe-core` (MPL-2.0) in
  `pnpm-lock.yaml`; adding it from this worktree would have rewritten the shared
  lockfile mid-wave, so the dependency and the CI job are left to the
  coordinator: `pnpm add -D axe-core --filter @garh/web` then a `--grep @a11y`
  step beside the smoke job.

## 8. The go-live checklist

- **TRACED** — `docs/ops-runbook.md` "Going live: the variables the owner must
  set" is one table of twelve rows: what, where, the variable, and what stays
  broken until it is done. Every row is an owner action (an account, a key, a
  domain); no commit can do any of them. Two rows carry their own paragraph —
  `APP_ENV=production` because its rollback matters, and the Razorpay switch
  because it is rehearsed before it is flipped.
- The same page now documents the ops endpoint, so the checklist is verified by
  looking at `/platform/ops` rather than from memory.
