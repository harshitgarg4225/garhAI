# Ops runbook

How to run Garh AI in production without guessing. Everything here was executed
against a live stack before being written down; if you change a procedure,
re-run it before editing this file.

## The provider switches

The product runs fully mocked by default — zero keys, zero GPUs, that is a
locked decision. Each real provider is a config flip on the relevant Railway
service, no image rebuild needed (the SDKs are already in the images):

| Capability                                     | Service           | Env vars                                                                                                                                                                                 |
| ---------------------------------------------- | ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Real copilot / brief parsing (Claude)          | api               | `PROVIDER_LLM=anthropic`, `ANTHROPIC_API_KEY`; model override via `ANTHROPIC_MODEL` (default `claude-opus-5`)                                                                            |
| Real AI renders, no GPU (Stability hosted API) | worker-render     | `PROVIDER_RENDER=stability`, `STABILITY_API_KEY`; tune `STABILITY_TIMEOUT_SECONDS`                                                                                                       |
| Real AI renders, own GPU (diffusers)           | worker-render     | `PROVIDER_RENDER=diffusers` on a CUDA host with the `ml` extra installed                                                                                                                 |
| OTP sign-in mail                               | api               | `SMTP_HOST` + `SMTP_FROM` (both required), optional `SMTP_USER`/`SMTP_PASSWORD`/`SMTP_PORT` (587)/`SMTP_STARTTLS` (true). Unset ⇒ dev echoes codes, prod fails loudly naming these vars. |
| Error tracking (Sentry)                        | api + each worker | `SENTRY_DSN`, optional `SENTRY_TRACES_SAMPLE_RATE` (0.1), `APP_VERSION`/`GIT_SHA` for release tagging                                                                                    |
| Error tracking (web)                           | web (build-time)  | `VITE_SENTRY_DSN` — needs a redeploy to take effect                                                                                                                                      |

All of these default OFF. The api and workers log a single INFO line at boot for
each provider they actually enabled; absence of that line means the flip did not
take.

## Services and their start commands

Eight Railway services, one config file each under `deploy/railway/` (attach it per
service under Settings → Config-as-code). `apps/api/tests/test_deploy_config.py`
reads this table, those files and `docker-compose.yml` and fails on any drift, so
what is written here is what runs.

| Service                      | Config file                           | Start command                                                                                                         |
| ---------------------------- | ------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `api`                        | `deploy/railway/api.json`             | `python -m garh_api.migrate --seed && exec uvicorn garh_api.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 4` |
| `web`                        | `deploy/railway/web.json`             | image `CMD` — nginx, the `prod-railway` stage                                                                         |
| `worker-solver`              | `deploy/railway/worker-solver.json`   | `python -m services.solver.worker`                                                                                    |
| `worker-render`              | `deploy/railway/worker-render.json`   | `python -m services.render.worker`                                                                                    |
| `worker-drawings`            | `deploy/railway/worker-drawings.json` | `python -m services.drawings.worker`                                                                                  |
| `backup`                     | `deploy/railway/backup.json`          | `/app/scripts/backup_db.sh backup-s3` — cron `30 20 * * *` (02:00 IST)                                                |
| `postgres`, `redis`, `minio` | managed / marketplace                 | —                                                                                                                     |

`python -m garh_api.migrate` is the one boot step that touches the schema: it takes a
Postgres advisory lock before `alembic upgrade head` (the lock lives in
`migrations/env.py`, so a bare `alembic upgrade head` is guarded too), and `--seed`
creates the demo firm once and writes nothing on every boot after — an audit row
included. The api can therefore run with more than one replica; raise `numReplicas`
in `api.json` when the load says so. Compose runs the same command when
`API_MIGRATE_ON_BOOT=true`.

## Backups

A backup that has never been restored is a hope, not a backup. Three commands, one
image, and a rehearsal that checks the data rather than the schema:

```bash
# nightly — the scheduled backup service (deploy/railway/backup.json, image
# deploy/backup/Dockerfile) runs exactly this against the S3_* bucket:
scripts/backup_db.sh backup-s3          # pg_dump + <stem>.counts.json → backups/, then retention

# to disk instead (a laptop, a one-off before a risky migration):
DATABASE_URL=$DATABASE_URL scripts/backup_db.sh backup [out-dir]

# after every schema migration, and monthly regardless:
scripts/restore_rehearsal.sh --from-s3  # newest dump in the bucket → scratch db → verify → drop
scripts/restore_rehearsal.sh path/to/garh-<stamp>.dump
```

What `backup-s3` writes: the custom-format dump and, beside it, a **manifest** of
`count(*)` per table taken at dump time. Retention is two-sided on purpose:
dumps older than `BACKUP_RETENTION_DAYS` (14) are deleted, but never below
`BACKUP_KEEP_MIN` (7) dumps however old — a cron that was down for a month must not
wipe every backup on its first run back. `scripts/backup_s3.py` is the S3 half (put,
get, list, newest, prune) and signs with the API's own `garh_api.storage`, so the
backup image needs no AWS CLI and no extra credentials beyond `S3_*`.

What the rehearsal proves, and fails on (`scripts/verify_restore.py`):

1. **every table's row count** in the restored database equals the manifest — a
   missing table or a count off by one is a FAIL, not a warning;
2. **the newest snapshot-bearing design of up to three projects refolds to the hash
   production recorded**: the ops up to the snapshot's `atIdx` are folded from an
   empty document through the real model engine and the state hash must equal the
   `stateHash` the API wrote into the envelope, and the envelope's sha256 must equal
   `design_versions.snapshot_hash`. A restore that lost or reordered one op fails
   here (`test_backup.py` alters one op and proves it).

The image is `deploy/backup/Dockerfile`: PGDG's `postgresql-client-17` (bookworm's
`pg_dump` 15 refuses a 16/17 server — `PG_MAJOR` is the one knob), the api's Python
deps, the four scripts, non-root. It has not been built on Railway yet: creating the
service is an owner action (go-live checklist below). Railway's managed Postgres
backups, plan permitting, complement this; they do not replace an owned, rehearsed
dump.

**Executed 2026-09-16 (this checkout, local Postgres 16 + the moto object store,
source = the shared `garh` database, scratch = `garh_test_l_restore`):**

```text
=== scripts/backup_db.sh backup-s3   (2026-09-16T11:51:33Z)
dumped /tmp/tmp.RVEsEcssff/garh-20260916T115133Z.dump (716K)
{"put": "backups/garh-20260916T115133Z.dump", "bytes": 732586, "bucket": "garh-test-l"}
{"put": "backups/garh-20260916T115133Z.counts.json", "bytes": 654, "bucket": "garh-test-l"}
{"prefix": "backups/", "kept": ["backups/garh-20260916T115133Z.dump"], "deleted": [], "dryRun": false, "keepDays": 14, "keepMin": 7}
backup-s3 OK — backups/garh-20260916T115133Z.dump
=== scripts/restore_rehearsal.sh --from-s3
rehearsal: dump /tmp/tmp.4whqEAY2BW/garh-20260916T115133Z.dump
rehearsal: manifest /tmp/tmp.4whqEAY2BW/garh-20260916T115133Z.counts.json
rehearsal: created garh_test_l_restore
rehearsal: pg_restore OK
restore verification — garh_test_l_restore (row counts vs manifest)
  ok   alembic_version          expected      1  restored      1
  ok   audit_log                expected    247  restored    247
  ok   briefs                   expected     25  restored     25
  ok   compliance_reports       expected      8  restored      8
  ok   credit_events            expected     25  restored     25
  ok   design_versions          expected      8  restored      8
  ok   firms                    expected     16  restored     16
  ok   flags                    expected      6  restored      6
  ok   ops                      expected   1795  restored   1795
  ok   otp_codes                expected     39  restored     39
  ok   plots                    expected     25  restored     25
  ok   projects                 expected     25  restored     25
  ok   share_links              expected      9  restored      9
  ok   sheets                   expected     70  restored     70
  ok   solver_jobs              expected     19  restored     19
  ok   users                    expected     16  restored     16
  (13 more tables, all 0 expected / 0 restored)
  ok   project 13ecd061-35dd-4a6d-9158-a362dc41be4c: 70 ops → c3f7f3d41a3b621e (recorded c3f7f3d41a3b621e, envelope ok)
  ok   project 212361dc-68f5-4bb5-a425-d7abed6e8ed3: 70 ops → c3f7f3d41a3b621e (recorded c3f7f3d41a3b621e, envelope ok)
  ok   project 21c58077-fd15-4d6b-b3ba-b5d96ee81c29: 70 ops → c3f7f3d41a3b621e (recorded c3f7f3d41a3b621e, envelope ok)
  RESULT OK
rehearsal OK — … restores completely and its newest design refolds to the recorded hash
exit=0
```

The same loop on the deployed stack needs the backup service created and one
`railway run scripts/restore_rehearsal.sh --from-s3` from it; until then the ledger
line for production reads UNVERIFIED.

## Load smoke

Two modes of one script. The read-only one is safe against any stack; the
product-path one WRITES (firms, projects, sheet jobs, objects) and must only ever
point at a scratch stack.

```bash
# read-only: /healthz under 20 concurrent clients
python scripts/load_smoke.py --base-url https://<api-domain> --clients 20 --seconds 15

# the product path: sign up → project from a ready-made plan → compliance →
# generate the sheet set (a real drawings job) → download a sheet; p50/p95 per step
python scripts/load_smoke.py --journey --base-url http://127.0.0.1:8114 --clients 5 --iterations 6
```

The journey needs `DEV_ECHO_OTP` on the target (the code comes back in the
response), `TRUSTED_PROXY_HOPS=1` (each client sends its own `X-Forwarded-For`, so
each gets its own per-IP auth budget), and a drawings worker on the same Redis.
Each client is its own firm, so `--iterations` is bounded by
`RATE_LIMIT_EXPORT_JOBS_PER_HOUR` (40).

**Executed 2026-09-17 (this checkout; api on :8114 with ONE uvicorn worker, the
drawings worker at concurrency 2, mock providers, the moto object store, a fresh
scratch database; 4 shared CPUs):**

```text
journey    5 clients × 6 iterations, template blr-30x40-g1-3bhk, 40.2s wall
signIn               5 ok     0 err   p50=  1122.5  p95=  1214.9  max=  1214.9  mean=   977.9 ms
createProject       30 ok     0 err   p50=  1259.6  p95=  2015.5  max=  2450.7  mean=  1251.3 ms
readCompliance      30 ok     0 err   p50=   400.0  p95=   920.8  max=   932.9  mean=   485.3 ms
generateSheets      30 ok     0 err   p50=  4060.4  p95=  6593.1  max=  7262.3  mean=  4309.2 ms
downloadSheet       30 ok     0 err   p50=   226.2  p95=   486.3  max=   614.4  mean=   248.0 ms

read-only smoke: 8,655 requests (862 req/s, 20 clients, 10 s), 0 errors,
p50 18.6 ms / p95 28.0 ms / p99 37.7 ms on /healthz
```

What the numbers say: creating a project from a ready-made plan is ~1.3 s at p50
(70 ops folded and snapshotted server-side, plus the compliance report frozen with
it); a nine-sheet set is drawn and stored in ~4 s at p50 / 6.6 s at p95 with two
drawings slots serving five architects at once — queue wait is inside that number,
by design. The first run of the journey found a real defect in the script itself
(it expected a JSON link where the listing carries a signed `/downloads/<token>`
that 307s to the store), which is exactly why the download is a step of its own.
The 2026-08-26 baseline (1,280 req/s, p95 17 ms on `/healthz`, 1 worker) still
stands as the read-only reference. Re-run both before and after infra changes; an
order-of-magnitude p95 jump on any step, or any error, is a finding.

## Going live: the variables the owner must set

Everything below is an **owner action** — an account, a key, a domain. No commit
can do any of it, and until each row is done the product runs on the mock that
row names. `GET /admin/ops` (`/platform/ops` in the app) shows the live state of
every one of them, so this table is checked by looking at that page, not by
memory.

| #   | What                    | Where                                                | Set                                                                                            | Until it is done                                                                             |
| --- | ----------------------- | ---------------------------------------------------- | ---------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| 1   | Error tracking, api     | `api`                                                | `SENTRY_DSN` (+ optional `SENTRY_TRACES_SAMPLE_RATE`, `APP_VERSION`/`GIT_SHA`)                 | an exception in production is a log line nobody is paged for; `/healthz` reads `sentry: off` |
| 2   | Error tracking, workers | `worker-solver/render/drawings`                      | the same `SENTRY_DSN` on **each** worker service                                               | a worker crash-loops unseen; the ops page names the workers still `off`                      |
| 3   | Production mode         | `api`                                                | `APP_ENV=production`                                                                           | `/docs` is open and the readiness validator never runs (see the rollback note below)         |
| 4   | Payments                | `api`                                                | `PROVIDER_BILLING=razorpay`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` (rehearse first — below) | no money moves; the mock checkout serves every invoice                                       |
| 5   | Tax invoices            | `api`                                                | `BILLING_SUPPLIER_LEGAL_NAME`, `_GSTIN`, `_ADDRESS` (`_STATE_CODE` optional)                   | `POST /billing/invoices` answers 503 `billing_unavailable`                                   |
| 6   | Sign-in mail            | `api`                                                | `BREVO_API_KEY` + `SMTP_FROM` (Railway Hobby blocks SMTP), or `SMTP_HOST` + `SMTP_FROM`        | dev echoes codes; in production every `POST /auth/otp` 503s naming these vars                |
| 7   | Custom domain           | `web` (+ `APP_URL`, `CORS_ALLOW_ORIGINS` on the api) | the domain in Railway, then both vars to `https://<domain>`                                    | share links, invite links and signed downloads carry the generated Railway hostname          |
| 8   | Scheduled backups       | new `backup` service                                 | `deploy/railway/backup.json`, with `DATABASE_URL` + `S3_*` referenced from the other services  | there is no backup at all (§ Backups above)                                                  |
| 9   | Real copilot            | `api`                                                | `PROVIDER_LLM=anthropic`, `ANTHROPIC_API_KEY`                                                  | the copilot answers from fixtures — fine for a demo, not for a trial                         |
| 10  | Real renders            | `worker-render`                                      | `PROVIDER_RENDER=stability`, `STABILITY_API_KEY`                                               | renders are the mock compositor's tinted viewport                                            |
| 11  | The owner's own access  | `api`                                                | `PLATFORM_OWNER_EMAILS` (comma-separated sign-in addresses)                                    | nobody can open `/platform/ops` or change the platform fee — empty means nobody, by design   |
| 12  | Behind the edge         | `api`                                                | `TRUSTED_PROXY_HOPS=1`                                                                         | every browser shares one per-IP auth bucket and the fourth trial architect is throttled      |

Two rows deserve their own sentence.

**Row 3 has a rollback plan, and needs one.** `APP_ENV=production` turns on the
readiness validator in `config.py`: the api then refuses to boot while
`JWT_PRIVATE_KEY`, `DATABASE_URL`, `REDIS_URL`, the S3 credentials or `APP_URL`
are still on local defaults (the MinIO defaults count as defaults). Set it, watch
the deploy, and if it refuses read the boot log — it names the variable — set that
variable, or set `APP_ENV=staging` to get the same validation with `/docs` still
open. Refusing to boot is the safety net working; a half-configured deploy never
serves a request.

**Row 4 is rehearsed before it is switched**, and the rehearsal is written out in
"Going live with Razorpay" below. Test-mode keys first, a real order settled
through Razorpay's own checkout, a verify that flips the invoice to `paid`, and
the same verify with one character changed that must be refused. Only then live
keys.

**What is NOT on this list, deliberately.** The rule-pack values stay
`confidence: "seed"` until architects empanelled per city review them — that is a
product gate, not a variable. Data residency is a placement decision: the api,
workers and Postgres run in `us-west2` today while spec §15 and
`docs/deployment.md` claim India residency; either move the project to the nearest
Railway region or amend the claim before a customer reads it (`deploy/railway/README.md`).

## Is anything actually being watched?

`GET /api/v1/admin/ops` — or **Operations** beside Platform fee in the app
(`/platform/ops`, the same `PLATFORM_OWNER_EMAILS` allowlist) — answers in one
document, and re-reads itself every 30 s while the tab is open:

- **queues** — pending, delayed, processing and dead-lettered per work queue;
- **workers** — one row per live process from the heartbeat it writes on every
  sweep (`services/common/heartbeat.py`): its counters, its p50/p95, its
  providers, its own Sentry state, and how long ago it last spoke. A worker that
  is not running has no row, and the page names it under "no heartbeat from" —
  absence is the alarm, because an absent worker answers no probe at all;
- **jobs** — counts by status and enqueue-to-finish p50/p95/max per kind over 24
  hours, across every firm;
- **providers, Sentry, the schema head** — what is in force, and whether
  `alembic_version` matches the code's migration head.

The page leads with the findings an operator must act on (Sentry off, a worker
missing or stale, dead letters, a schema behind the code, no mail channel), each
with its next step. Nothing on it is a secret: names, counts, timestamps and
revision ids only, and `test_platform_ops.py` asserts the DSN, the connection
strings and every job id stay out of the body.

`GET /healthz` (unauthenticated, what the orchestrator polls) carries the same
`observability.sentry` flag, so "is error tracking on?" is answerable without a
token — the question that had been silently answered "no" on the deployed stack
for a month.

## Accessibility

```bash
pnpm --filter @garh/e2e exec playwright test --grep @a11y
```

`e2e/tests/accessibility.spec.ts` runs axe-core (WCAG 2.0/2.1 A + AA +
best-practice) over login, the dashboard, all four settings sections, billing,
the brief/compliance/sheets/renders/plan tabs and the tour, in a real browser.
**Serious and critical fail the run**; moderate findings are printed. If
`axe-core` is not installed the spec fails rather than skipping — a green
accessibility check that never ran is worse than no check.

**Executed 2026-09-20 (this checkout, twelve screens + the tour): 0
serious/critical.** The first run of that pass found two, both now fixed: the
audit trail's `<dl>` wrapped its pairs in `<span>` (invalid, and it loses the
term/description pairing), and the assumption chip's label sat at 3.42:1
contrast from `opacity-70` on 11px text — on every Brief page since the chip was
written, and invisible to jsdom, which computes no styles. The moderate findings
that remain are `landmark-one-main`/`region` on the §12 canvas panel grid and
`heading-order` where a card title follows a page title;
`docs/trust-operations-verification.md` records why they are not chased.

## Rate limits that will page you first

Auth endpoints fail closed (per-IP hourly, per-address 60 s resend cooldown +
hourly cap); solver jobs are capped per firm per hour; LLM routes per user per
hour. All knobs are `RATE_LIMIT_*` env vars on the api service — see
`.env.example` for the full inventory with defaults.

## Known operational sharp edges

- The api pytest suite's `clean_db` fixture TRUNCATES every table in whatever
  `DATABASE_URL` points at. Never point tests at a live database.
- Redis db 0 holds live queues and rate-limit state; never `FLUSHDB` it.
- `python -m garh_api.seed` is idempotent and safe to re-run on deploy.
- Workers probe their external tools (rsvg-convert, qpdf) at boot; a missing
  tool fails the boot loudly rather than the hundredth job quietly.

## Changing the platform fee (the owner's markup)

Every metered charge (an LLM call, a render, a solver run, an export) is recorded twice
on `credit_events`: `cost_micros`, what the provider charged us, and `charged_micros`,
what the architect's budget was debited — cost plus the platform fee in force at that
moment, whose basis points sit beside it in `markup_bps`. Budgets are spent in
`charged_micros`; reconciliation against a provider bill uses `cost_micros`.

The fee is a percentage of cost. It boots from `BILLING_MARKUP_PERCENT` (default 5) and
the owner changes it at runtime, no redeploy:

```bash
# who may: sign-in emails on PLATFORM_OWNER_EMAILS (comma-separated; empty = nobody)
curl -X PUT "$APP_URL/api/v1/admin/billing/markup" \
  -H "authorization: Bearer $TOKEN" -H "content-type: application/json" \
  -d '{"percent": "7.5"}'
# anyone signed in may read it — the usage card shows it to every architect
curl "$APP_URL/api/v1/admin/billing/markup" -H "authorization: Bearer $TOKEN"
```

Applies to the next charge and every one after; rows already written keep the fee they
were charged at (`test_markup.py` holds that as a negative control). 0–100, at most two
decimals. The value lives in `platform_settings` under `billing.markup_bps`, with the
email of the owner who set it beside it under `billing.markup_set_by`.

**From the app, no curl:** sign in with an address on `PLATFORM_OWNER_EMAILS`, and the
dashboard header shows a **Platform fee** link (`/platform/fee`) beside **Billing**. The
link appears only when `GET /admin/billing/markup` answers `canSet: true` for the caller;
it is a courtesy, not the gate — the page renders read-only for anyone else, and the
`PUT` answers 403 on its own. The page shows the fee in force, who set it and when (or
that the boot default is still in force), a field that refuses anything outside 0–100 or
finer than two decimals before the round trip, and a confirm that states the rule above:
the change applies from the next metered charge, earlier rows keep theirs. After a change
the page re-reads `/billing/usage`, so the percentage the usage card names is the one the
next generation, render, copilot call or export is debited at.

## Going live with Razorpay (the billing provider switch)

`PROVIDER_BILLING` is the switch. There is no feature flag: `billing_live` exists in
`DEFAULT_FLAGS` and is read by nothing. Under `mock` (the default, CI, the demo seed) no
money moves, but the security half is real — orders are signed with HMAC-SHA256 over
`order_id|payment_id` and verified with a constant-time compare, so a tampered signature
is refused under the mock exactly as it would be live.

**What the switch needs, precisely:**

| Variable                  | Read by                                                          | Required when                                                                                                     |
| ------------------------- | ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `PROVIDER_BILLING`        | `Settings.provider_billing`                                      | always (`mock` \| `razorpay`)                                                                                     |
| `RAZORPAY_KEY_ID`         | `Settings.razorpay_key_id`; handed to the checkout as `keyId`    | `razorpay` — the API refuses to boot without it                                                                   |
| `RAZORPAY_KEY_SECRET`     | `Settings.razorpay_key_secret`; signs/verifies                   | `razorpay` — same boot refusal                                                                                    |
| `RAZORPAY_WEBHOOK_SECRET` | `razorpay_provider.verify_webhook_signature` only (fails closed) | only once a webhook route exists — **none does today**; do not register a webhook URL in the dashboard until then |
| `BILLING_SUPPLIER_*`      | `billing/gst.py` at invoice time                                 | to issue any tax invoice (503 `billing_unavailable` otherwise), mock or live                                      |

**What changes when it flips.** `POST /billing/invoices/{id}/checkout` opens a real order
(`POST /v1/orders`, receipt = our invoice number, firm/invoice ids in `notes`) and answers
the order id, amount in paise and `keyId`. `POST /billing/payments/verify` checks the
widget's signature under the key secret, then `GET /v1/payments/{id}` for the server-side
truth and a capture if the payment is merely authorised. `POST /billing/payments/mock`
answers 404 — the pretend-payment path does not exist on a deployment that moves money.

**What is NOT wired, stated plainly.** The web's **Pay** button completes the journey
only under the mock. Under `razorpay` it opens the order and tells the admin to settle it
through the gateway's own checkout: Razorpay's `checkout.js` is not loaded by the web
app, so the browser has nothing to hand `keyId`/`orderId` to. That is the piece to build
before the first live rupee, and it is a client change only — the verify route is ready.
A payment completed after the browser closes is not settled either (no webhook route);
an admin re-opens the invoice and pays again, and the idempotent checkout reuses the
same order.

**The rehearsal before the first customer** (never done on this deployment — the adapter is
proven only against a strict `httpx.MockTransport` double in `test_billing_core.py`):

1. Test-mode keys (`rzp_test_…`) in `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET`,
   `PROVIDER_BILLING=razorpay`, `BILLING_SUPPLIER_*` set; confirm `/healthz` and that the
   API refused to boot when one key was blanked (that refusal is the negative control).
2. From Billing: GST details → Issue this period's invoice → Pay. Take `orderId` and
   `keyId` from the checkout response and complete the order in Razorpay's hosted
   test checkout (or `checkout.js` on any page) with a test card/UPI.
3. `POST /billing/payments/verify` with the three values the widget returned; expect
   the invoice `paid` and `billing_payments.signature_verified = true`; then re-run
   with one character of the signature changed and expect 400 `payment_not_verified`.
4. Only then swap in live keys. Keep the webhook secret empty until a webhook route
   exists.
