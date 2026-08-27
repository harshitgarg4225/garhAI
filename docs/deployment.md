# Deployment

Playbook §18. The beta target is **a single VM running the compose stack**. That is a
deliberate choice, not a shortcut: at 50 beta firms it's the right amount of
infrastructure, and because the workers are stateless and coordinate only through
Redis and Postgres, moving to k8s later is a deployment change rather than a
rewrite.

> **Nothing here has been executed.** The scaffold has never been deployed — this is
> the intended shape, to be verified when a target environment exists.

---

## Images

| Image                 | Stages                                     | Notes                                                |
| --------------------- | ------------------------------------------ | ---------------------------------------------------- |
| `apps/api/Dockerfile` | `base` → `dev` → `prod`                    | `prod` runs as non-root uid 10001, 4 uvicorn workers |
| `services/Dockerfile` | `base` → `dev` → `prod`                    | one image, three entrypoints, non-root               |
| `apps/web/Dockerfile` | `base` → `deps` → `dev` / `build` → `prod` | `prod` is static assets on nginx                     |

Build context is the **repo root** for all three — the API and workers import
`garh_model` and read `rulepacks/`, and the web build resolves workspace packages.

Python dependencies are installed by extracting the pinned lists out of
`apps/api/pyproject.toml` with stdlib `tomllib`, rather than `pip install .`. See
`DECISIONS.md` for why (hatchling needs the full package tree, which isn't complete
yet), and note the consequence: **imports rely on `PYTHONPATH`**, which is also what
makes the bind-mounted dev source live without a reinstall.

---

## Differences from local

| Concern    | Local                            | Deployed                                           |
| ---------- | -------------------------------- | -------------------------------------------------- |
| Web        | Vite dev server + HMR            | static bundle on nginx (`prod` stage)              |
| API        | 1 worker, `--reload`             | 4 uvicorn workers, no reload                       |
| Migrations | run by the api container on boot | **separate step before rollout**                   |
| Secrets    | generated / mock defaults        | real, from the host's secret store                 |
| `APP_ENV`  | `dev`                            | `staging` or `prod` — enables fail-fast validation |
| TLS        | none                             | terminated at the reverse proxy                    |

### Migrations must not run on boot in production

Locally, `API_MIGRATE_ON_BOOT=true` is a convenience that keeps the quickstart one
command. With multiple replicas it becomes a race — N containers running
`alembic upgrade head` against the same database at once.

```bash
API_MIGRATE_ON_BOOT=false
docker compose run --rm api alembic upgrade head    # then roll out
```

### Fail-fast is the deploy safety net

With `APP_ENV` set to anything but `dev`, `config.py` refuses to boot while
`JWT_PRIVATE_KEY`, `DATABASE_URL`, `REDIS_URL`, the S3 credentials or `APP_URL` are
still on local defaults, and requires `ANTHROPIC_API_KEY` when
`PROVIDER_LLM=anthropic`. A half-configured deploy therefore never serves a request.

Generate production JWT keys **out of band** — never reuse a dev keypair:

```bash
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 -out jwt-private.pem
openssl rsa -in jwt-private.pem -pubout -out jwt-public.pem
awk 'BEGIN{ORS="\\n"}1' jwt-private.pem     # single-line form for the secret store
```

---

## Reverse proxy

Terminate TLS at the proxy and serve web + API from one origin so the refresh cookie
stays same-site. The §13 headers belong here, not in the app:

- HTTPS only, HSTS with a long max-age
- CSP with **no inline scripts**
- `SameSite=Lax` on the refresh cookie, `Secure` on
- CORS from the `CORS_ALLOW_ORIGINS` allowlist, never `*`

`/api/*` proxies to the api service; everything else serves the static bundle with
SPA fallback. Long-lived SSE connections (`/api/v1/**/events`) need buffering
disabled and a generous read timeout, or job progress silently stalls.

---

## Scaling shape

| Component       | Scale by                                                                   |
| --------------- | -------------------------------------------------------------------------- |
| api             | replicas behind the proxy — stateless                                      |
| worker-solver   | replicas; CP-SAT is CPU-bound, `SOLVER_NUM_SEARCH_WORKERS` threads per job |
| worker-render   | one per GPU; `RENDER_DEVICE=cuda`, L4 in Mumbai (`asia-south1` / `g6`)     |
| worker-drawings | replicas — CPU-bound, memory-hungry on large sheet sets                    |
| postgres        | vertical first; the op log is append-only and snapshots bound replay cost  |
| redis           | single instance is fine at beta; queue depth is the metric to watch        |

GPU render is the one component that cannot scale on the same box. Keeping inference
in Mumbai is also what keeps the India data-residency claim honest (spec §15), so
it's a placement constraint, not just a latency one.

---

## Backups

- **Nightly `pg_dump`** to object storage, retained per the data-retention policy.
- **S3 versioning is enabled on the bucket** by the `minio-init` container, so an
  overwritten render or export is recoverable.
- The op log is the source of truth for designs, so a database restore restores
  designs exactly — snapshots are a cache and can be rebuilt by replaying ops.
- **Test the restore path.** An untested backup is a hypothesis.

---

## Observability

Already wired into the scaffold:

- **structlog JSON** with request ids (`LOG_LEVEL`, `LOG_FORMAT`)
- **`/healthz` per service**, used by the compose healthchecks
- **Sentry-compatible error hook** (`SENTRY_DSN`, blank = off)

Still to add:

- **worker queue-depth metric** — the single most useful number for this
  architecture. Rising depth on `garh:queue:solver` is the earliest signal that
  solver capacity is short, and it precedes any user-visible symptom.
- job duration histograms per queue, to watch the §14 budgets in production rather
  than only in CI
- `credit_events` roll-ups, so real COGS is measurable before pricing goes live
  (spec D11)

---

## Release checklist

1. CI green on `main` (`ci-green`).
2. `make license-check` clean — no GPL/AGPL, no unknown licences.
3. Golden files unchanged, or changed deliberately with a note.
4. `docs/security-checklist.md` reviewed for anything the release touches.
5. Migrations applied as a separate step.
6. `APP_ENV` correct, so fail-fast validation is active.
7. Backup taken before the rollout.
8. `/healthz` green on api and all three workers; queue depth draining.
