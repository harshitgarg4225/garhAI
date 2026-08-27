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

## Backups

A backup that has never been restored is a hope, not a backup.

```bash
# nightly (scheduled job or any host that reaches the DB):
DATABASE_URL=$DATABASE_URL scripts/backup_db.sh backup /backups

# after every schema migration, and monthly regardless:
DATABASE_URL=$DATABASE_URL scripts/backup_db.sh rehearse
```

`rehearse` restores the newest dump into a scratch database and fails unless
the core tables (`firms`, `users`, `projects`, `ops`) came back. Keep dumps off
the app containers' ephemeral disks. Railway's managed Postgres backups (plan
permitting) complement this; they do not replace an owned, rehearsed dump.

## Load smoke

```bash
python scripts/load_smoke.py --base-url https://<api-domain> --clients 20 --seconds 15
```

Read-only by design (cannot trip auth rate limits or write anything). Baseline
on 2026-08-26, single local uvicorn worker: 1,280 req/s, 0 errors,
p50 13 ms / p95 17 ms / p99 25 ms on `/healthz`. Re-run before and after infra
changes; an order-of-magnitude p95 jump or any `/healthz` error is a finding.

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
